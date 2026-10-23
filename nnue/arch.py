"""
The network's shape, and the one place that knows how it is quantised.

    (768 -> L1) x 2  ->  1,  with SCReLU, and an output layer per bucket

This is the architecture modern engines settled on, and it differs from the
769 -> 256 -> 1 net in evaluationModel/ in three ways that each matter more
than the size:

*Two perspectives.*  The old net had one set of features for the whole board
plus a single input bit saying whose turn it was, so it had to learn "a knight
on f6" and "a knight on f3" as unrelated facts and could only express the side
to move as a constant offset -- which is exactly what it did, carrying a 450 cp
bias.  Here the board is encoded twice, once from each player's point of view,
with the piece colours relabelled to own/enemy and the board flipped, and the
two halves are concatenated own-side first.  Every pattern is then learned once
and applies to both colours, and the side to move is structural.

*SCReLU.*  Squaring the clipped activation gives the first layer a way to
express conjunctions, which plain ReLU cannot, and is worth more than doubling
the width.

*Output buckets.*  One output layer per material count, so the net does not
have to use the same weights for a queen endgame and a full board.

Quantisation follows the usual scheme: the feature layer at QA, the output
layer at QB, with the weights clipped during training so that the integer
accumulator cannot overflow.  `screlu(x) = clamp(x, 0, 1)^2` in the trainer
corresponds to `x_q^2` on integers, where `x_q = clamp(acc, 0, QA)`.
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

# input features, per perspective
NUM_SQ = 64
NUM_PT = 6
NUM_PLANES = 2 * NUM_PT * NUM_SQ          # 768: own/enemy x type x square

# ---------------------------------------------------------------- king buckets
#
# The same piece on the same square means something different depending on
# where the king it belongs to is standing, and a single set of 768 planes has
# to average over all of it.  Giving each king region its own copy of the first
# layer lets the net say "a knight on f5 with the king castled short" without
# spending capacity relating it to the same knight in a king-in-the-centre
# position.
#
# Two things make it affordable.  The board is folded onto files a-d by the
# perspective's own king file, which halves what has to be learned and is free
# -- a position and its mirror are the same position.  And the bucket only
# changes when the king moves, so the accumulator is refreshed on a king move
# and updated incrementally on every other, which is what `acc_for` in
# nnue.c already does as its slow path.
#
# The map is over the perspective's own king square after mirroring, in its own
# relative orientation: rank 0 is its back rank, and the file is folded so that
# column 0 means a-or-h, 1 means b-or-g, 2 means c-or-f and 3 means d-or-e.
#
# The split follows where the kings actually are rather than where the board
# looks symmetric.  Measured over 1.2 M king placements in shard0:
#
#   rank 0, b/g   30.0%     castled short (or long, folded onto it)
#   rank 0, d/e   29.7%     never castled, still at home
#   rank 0, a/h    3.5%     \  the awkward back-rank files, plus
#   rank 0, c/f    4.7%      >  the king that has taken one step
#   rank 1        11.2%     /
#   rank 2+       20.9%     the endgame king, out in the open
#
# so four buckets at roughly 30 / 30 / 19 / 21.  An even split of the *board*
# would have put two thirds of the data in one bucket and left another with
# 3%, which is the usual way this goes wrong: a bucket is only worth its
# weights if enough positions land in it to train them.
#
#       files:  a/h  b/g  c/f  d/e
#   rank 0:      2    0    2    1
#   rank 1:      2    2    2    2
#   rank 2+:     3    3    3    3
#
KING_BUCKETS = 4
_KB_ROW = [
    [2, 0, 2, 1],
    [2, 2, 2, 2],
    [3, 3, 3, 3],
    [3, 3, 3, 3],
]


def king_bucket(rel_rank: int, folded_file: int) -> int:
    return _KB_ROW[min(rel_rank, 3)][folded_file]


# The full 64-entry table the engine and the trainer both index, over the
# perspective's own king square in its own relative orientation (0..63).
KING_BUCKET_TABLE = [
    king_bucket(sq // 8, (sq % 8) if (sq % 8) < 4 else 7 - (sq % 8))
    for sq in range(64)
]

PAD_INDEX = KING_BUCKETS * NUM_PLANES     # the index empty squares point at
NUM_INPUTS = PAD_INDEX + 1

# Hidden neurons per perspective.  1536 was tried and the width is what made
# it too slow: king buckets plus L1=1536 is +33.8 Elo at a fixed node count
# and -2.7 at 10+0.1, because the extra width costs 27% of the speed and the
# clock takes it all back.  The buckets themselves are nearly free -- they
# only cost a refresh when a king changes region -- so the width goes back.
L1 = 1024
OUTPUT_BUCKETS = 8                        # chosen by piece count

QA = 255                                  # feature layer scale
QB = 64                                   # output layer scale
SCALE = 400.0                             # centipawns per unit of net output

# The clip that keeps the quantised weights inside an int16 and the integer
# accumulator inside its range.  127/QB is the largest output weight that
# survives quantisation; the feature weights are held to the same bound, which
# with at most 32 active features keeps |accumulator| far below 32767.
WEIGHT_CLIP = 127.0 / QB


def output_bucket(piece_count: torch.Tensor) -> torch.Tensor:
    """Pick the output layer: evenly by piece count, 2..32 into 8 buckets."""
    return torch.clamp((piece_count - 2) * OUTPUT_BUCKETS // 31, 0,
                       OUTPUT_BUCKETS - 1)


class NnueNet(nn.Module):
    def __init__(self, l1: int = L1, buckets: int = OUTPUT_BUCKETS,
                 king_buckets: int = KING_BUCKETS) -> None:
        super().__init__()
        self.l1 = l1
        self.buckets = buckets
        self.king_buckets = king_buckets
        self.pad_index = king_buckets * NUM_PLANES
        num_inputs = self.pad_index + 1

        # The feature transformer.  An EmbeddingBag sums the active features
        # directly, which is the whole trick: the first layer is never
        # materialised as a 768-wide dense input.  With king buckets it is
        # king_buckets copies of those 768 planes, only one of which any given
        # position indexes into.
        self.ft = nn.EmbeddingBag(num_inputs, l1, mode="sum",
                                  padding_idx=self.pad_index)
        self.ft_bias = nn.Parameter(torch.zeros(l1))
        self.out_weight = nn.Parameter(torch.zeros(buckets, 2 * l1))
        self.out_bias = nn.Parameter(torch.zeros(buckets))

        # Small init: with SCReLU a large initial activation squares into a
        # huge gradient and the first few hundred batches are spent recovering.
        nn.init.uniform_(self.ft.weight, -0.02, 0.02)
        with torch.no_grad():
            self.ft.weight[self.pad_index].zero_()
        nn.init.uniform_(self.out_weight, -0.02, 0.02)

    def forward(self, idx_us: torch.Tensor, idx_them: torch.Tensor,
                bucket: torch.Tensor) -> torch.Tensor:
        """idx_* are (B, 32) feature indices, already own-side relative."""
        acc_us = self.ft(idx_us) + self.ft_bias
        acc_them = self.ft(idx_them) + self.ft_bias
        x = torch.cat((torch.clamp(acc_us, 0.0, 1.0) ** 2,
                       torch.clamp(acc_them, 0.0, 1.0) ** 2), dim=1)
        w = self.out_weight[bucket]                      # (B, 2*L1)
        return (x * w).sum(dim=1) + self.out_bias[bucket]

    @torch.no_grad()
    def clip_weights(self) -> None:
        self.ft.weight.clamp_(-WEIGHT_CLIP, WEIGHT_CLIP)
        self.ft.weight[self.pad_index].zero_()
        self.out_weight.clamp_(-WEIGHT_CLIP, WEIGHT_CLIP)

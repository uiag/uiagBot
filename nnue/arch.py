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
PAD_INDEX = NUM_PLANES                    # the index empty squares point at
NUM_INPUTS = NUM_PLANES + 1

L1 = 1024                                 # hidden neurons per perspective
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
    def __init__(self, l1: int = L1, buckets: int = OUTPUT_BUCKETS) -> None:
        super().__init__()
        self.l1 = l1
        self.buckets = buckets
        # The feature transformer.  An EmbeddingBag sums the active features
        # directly, which is the whole trick: the first layer is never
        # materialised as a 768-wide dense input.
        self.ft = nn.EmbeddingBag(NUM_INPUTS, l1, mode="sum",
                                  padding_idx=PAD_INDEX)
        self.ft_bias = nn.Parameter(torch.zeros(l1))
        self.out_weight = nn.Parameter(torch.zeros(buckets, 2 * l1))
        self.out_bias = nn.Parameter(torch.zeros(buckets))

        # Small init: with SCReLU a large initial activation squares into a
        # huge gradient and the first few hundred batches are spent recovering.
        nn.init.uniform_(self.ft.weight, -0.02, 0.02)
        with torch.no_grad():
            self.ft.weight[PAD_INDEX].zero_()
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
        self.ft.weight[PAD_INDEX].zero_()
        self.out_weight.clamp_(-WEIGHT_CLIP, WEIGHT_CLIP)

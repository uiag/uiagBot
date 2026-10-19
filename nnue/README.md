# The evaluation network

A perspective NNUE for the engine in [`../engine`](../engine/README.md), with
the data pipeline that trains it.

```
(768 -> 1024) x 2  ->  1        SCReLU, 8 output buckets by piece count
```

## Why not the old one

`../evaluationModel/` trains a 769 -> 256 -> 1 network, and the engine's own
notes record that it lost to the hand-crafted evaluation 21-3 and carried a
~450 cp side-to-move bias. That is not a size problem, it is three structural
ones:

* **One view of the board.** All 12 piece-colour planes shared one set of
  features, so "my knight on f6" and "the enemy knight on f3" were unrelated
  inputs and every pattern had to be learned twice. Here the board is encoded
  twice, once from each player's point of view, with colours relabelled
  own/enemy and the squares flipped for Black. The halves are read own-side
  first, so the side to move is structural rather than input 768 — which is
  all the old net had, and a single input can only express a constant offset,
  which is exactly the bias it showed.
* **ReLU.** `clamp(x,0,1)^2` lets the first layer express conjunctions —
  "these two pieces, together" — which a plain rectifier cannot. It is worth
  more than doubling the width.
* **One output layer** for a queen middlegame and a king-and-pawn endgame.
  There are now eight, chosen by piece count.

The accumulator is also updated by difference rather than recomputed, which is
what makes a 1024-wide first layer affordable at all: the network costs 26% of
the engine's speed (1.16 M nps against 1.57 M), where a full refresh at every
node would cost several times that.

## The data

`extract.c` turns the lichess evaluation dump into fixed 32-byte records.

```
python nnue/make_random_net.py       # a random net, for the verifier
sh nnue/run_extract.sh 7             # ~13 min, 241 M positions, 7.7 GB
```

It links only the engine's attack tables, not its `Position`: `set_fen` zeroes
2048 StateInfos on every call, about 230 KB, which over two hundred million
positions is tens of terabytes of pointless memset. Parsing straight into
bitboards instead runs at 200 MB/s, which is faster than the disk.

Three filters, and the counts they removed from the dump's 354.6 M lines
(68.1% survive):

| filter | why | removed |
| --- | --- | --- |
| side to move is in check | the score belongs to the forced sequence, not to where the pieces stand | 29.7 M |
| the best move is a capture or a promotion | the number is about the capture | 83.5 M |
| malformed, or more than 32 men | the dump has a few of each | 0.05 M |

The record keeps the occupancy bitboard, the piece codes as nibbles, the score
from White's point of view, the side to move, both king squares, and a flag
for a mate score. A fixed size is what lets the trainer hold the whole set in
memory and index into it.

## Training

```
.venv2/Scripts/python nnue/train.py --epochs 5
.venv2/Scripts/python nnue/train.py --resume nnue/net.pt --epochs 2 --lr 1e-4
```

The target is the reference score through a sigmoid and the loss is taken in
that space, not on centipawns: the difference between +300 and +400 hardly
changes who wins, while the difference between 0 and +100 decides games, and a
squared error on centipawns would spend most of its gradient on the first
kind.

`shard6.bin` is never trained on, so the held-out loss is over positions from
a different part of the dump rather than a reshuffle of the training part.
Alongside the loss the run reports the correlation with the reference score,
which is the measure the hand-crafted evaluation is scored on in
`testsuite/tools/eval_fit.py` — so the two are directly comparable.

Weights are clipped to 127/QB after every step. That is not regularisation,
it is what keeps the quantised network identical to the trained one: the
export refuses to write a net whose accumulator could overflow an int16, and
the clip is what guarantees it never can.

## Export, and checking it

```
.venv2/Scripts/python nnue/export.py            # net.pt -> engine/net2.bin
python nnue/verify.py                           # the accumulator is correct
```

A wrong accumulator delta is the quietest bug available: the evaluation comes
out a few centipawns off, but only in positions reached by one particular kind
of move, so the engine still plays, still benchmarks and still passes perft.
`verify.py` builds the engine with `-DCE_NNUE_VERIFY`, which recomputes the
first layer from scratch after every incremental update and aborts on the
first disagreement, then drives it through the move types the delta has to
special-case — castling moves two pieces, en passant removes a pawn from a
square the move never names, a promotion removes one piece and adds another.
It currently agrees over 2.7 M nodes.

It does not need a *good* network to do that, only a network, which is what
`make_random_net.py` is for.

## Using it

```yaml
# config.yml
engine:
  homemade_options:
    EvalMode: nnue       # hce | nnue | blend
```

or `setoption name EvalMode value nnue` over UCI. The engine loads
`engine/net2.bin` and falls back to the hand-crafted evaluation when it is not
there, so a checkout without a trained network still plays.

`NnueScale` puts the network on the same scale as the hand-crafted
evaluation — whose units are about twice a centipawn, and which every search
margin in `tune.h` was tuned against. Without it, changing `EvalMode` would
quietly change the meaning of every margin in the search as well.

## Files

| file | contents |
| --- | --- |
| `arch.py` | the shape, and the one place that knows how it is quantised |
| `data.py` | records to batches, vectorised over the batch |
| `train.py` | the training loop |
| `export.py` | `net.pt` -> `engine/net2.bin`, with the overflow checks |
| `extract.c` | the lichess dump -> packed records |
| `run_extract.sh` | shard the dump across N processes |
| `test_data.py` | the decoder, against python-chess |
| `verify.py` | the incremental accumulator, against a full refresh |
| `make_random_net.py` | a network for the verifier to use |

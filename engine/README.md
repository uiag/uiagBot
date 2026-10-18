# The chess engine

A standalone bitboard chess engine in C. No network access, no external engine:
the only outside data it uses are the opening book (handled by lichess-bot
itself, see `engine: polyglot:` in `config.yml`) and the trained network in
`../nnue_weights.npz`.

## Layout

| file | contents |
| --- | --- |
| `ce.h` | types, constants, declarations |
| `tune.h`, `tune.c` | every constant the search and the evaluation would otherwise hard-code, and the name → address table that lets them be set from outside |
| `bitboard.c` | attack tables; magic bitboards (magics are searched for at start-up with a fixed seed) |
| `position.c` | board state, zobrist hashing, make/unmake, attacks, legality, SEE, draw and cycle detection, FEN |
| `movegen.c` | legal move generation (captures/promotions and quiets partition the legal moves) |
| `eval.c` | hand-crafted evaluation, material imbalance and endgame knowledge (KPK bitbase, KXK/KBNK mate guidance, the textbook draws) |
| `nnue.c` | the 769-256-1 network from `evaluationModel/`, quantised |
| `tt.c` | shared transposition table |
| `search.c` | iterative deepening PVS search, time management, Lazy SMP |
| `api.c` | the C API the Python side calls |
| `uci.c`, `main.c` | standalone UCI front-end with `bench` / `perft` / `eval` / `tune` commands |

## Tunable constants

Everything the search and the evaluation used to hard-code as a magic number
now lives in one list in `tune.h`:

```c
TUNE(FutilityBase,  95,  10, 400)      /* name, default, min, max */
```

The list is expanded three times by the preprocessor -- to declare the
variables, to define them, and to build a name lookup -- so adding a parameter
is one line. Each one is then settable at run time:

```
setoption name FutilityBase value 110      # over UCI
tune                                       # print every current value
```

`ce_set_param()` does the same from the Python side, which is what the tuners
in `testsuite/tools/` drive.

All of them are *declared* in the `uci` reply. That is deliberate and it was
learned the hard way: they were originally hidden behind a `CE_TUNE`
environment variable to spare a GUI eighty spin boxes, and a UCI host has no
way to know an option exists unless the engine says so. `ts/engines.py` drops
undeclared options without a word, so three tuning experiments ran with
default parameters and measured nothing but an unrelated thread-count
mismatch. Set `CE_TUNE=0` to hide them again if a GUI really minds.

The defaults in `tune.h` are the values the engine actually plays with. A
tuning run never edits source: it writes a list of values, those are confirmed
with games, and only then are the defaults in `tune.h` changed by hand.

## The evaluation's two halves

`eval_hce()` accumulates material and position **separately** and only then
adds them up:

```c
int mg = matMg + posMg * PosWeightMg / 128;
int eg = matEg + posEg * PosWeightEg / 128;
```

This is not cosmetic. The piece values and the piece-square tables come from
PeSTO, where a pawn is 82 mg / 94 eg; every other term -- mobility, king
safety, threats, passed pawns, space, initiative -- was taken from an
evaluation whose pawn was about 126 mg / 208 eg. Added at face value the
positional terms therefore arrived roughly 1.5x too large in the middlegame
and 2.2x too large in the endgame, and the engine would cheerfully pay a pawn
for half a pawn of mobility. `PosWeightMg` and `PosWeightEg` put the two on
the same scale, and each block of the evaluation has its own weight on top of
that so a tuner can move one without moving the rest.

`EvalScale` is applied last, so that a reported 100 really is worth about a
pawn and the margins the search compares against mean what they say.

## Building

```
python engine/build.py              # builds engine/ce.dll and engine/ce.exe
python engine/build.py --generic    # portable build, for a different CPU
python engine/build.py --debug      # -O1 with assertions
```

`engine_core.py` rebuilds automatically when a source file is newer than the
library, so normally you never have to run this by hand.

A C compiler is required. On Windows, MSYS2/MinGW gcc is found automatically in
the usual install locations; otherwise put `gcc` or `clang` on `PATH`.

## The network

`engine/net.bin` is generated from the trained weights:

```
python engine/export_net.py                     # nnue_weights.npz -> engine/net.bin
python engine/export_net.py other.npz out.bin
```

The feature layout matches `evaluationModel/features.py` and
`evaluationModel/dataset.py`: input `colour*384 + (pieceType-1)*64 + square`
with python-chess colours, plus input 768 set when white is to move. The engine
reads the pre-sigmoid logit and multiplies by 400 to get centipawns, inverting
the `cp -> win probability` mapping the net was trained on.

The evaluation used at run time is selected with `EvalMode`:

* `hce` (default) -- the hand-crafted evaluation. Measurably stronger than the
  current network, which systematically undervalues material and carries a
  ~450 cp side-to-move bias.
* `nnue` -- the network only.
* `blend` -- a 1:3 mixture of the two.

Retraining the net (`evaluationModel/train.py`) and re-exporting is enough to
switch to `nnue`; nothing in the engine needs to change.

## Two things that were tried and left off

Both are implemented, both are reachable from `tune.h`, and both are disabled
because they were measured and did not pay:

* **`QsChecks`** -- quiet moves that give check, searched at the first
  quiescence ply. It costs 8% more nodes to reach the same depth, takes the
  mate suite from 20/21 to 19/21, and scored 48.75% over 600 games at 150 ms a
  move (-8.7 +/- 17.6 Elo).
* **`LazyMargin`** -- stop the evaluation after material and the piece-square
  tables when that alone is already outside the search window by this much.
  The shortcut can only fire where the window is wide, and this search's own
  pruning makes that rare, so it bought 2% more nodes per second in exchange
  for 9% more nodes to the same depth -- 6.7% *slower* to depth -- and scored
  49.0% over 300 games (-6.9 +/- 26.7 Elo).

Set either to a non-zero value to try again; a longer time control is the
obvious thing to vary, since both trade accuracy for speed and the balance
moves with the clock.

## Running it standalone

`engine/ce.exe` speaks UCI, so it works with cutechess-cli, Arena, Banksia and
friends:

```
engine/ce.exe                       # UCI mode
engine/ce.exe bench 14              # fixed-depth benchmark over 12 positions
engine/ce.exe perft 6
```

Extra UCI commands: `bench [depth]`, `perft [depth]`, `eval`, `tune`,
`d` (print FEN).
Options: `Hash`, `Threads`, `MultiPV`, `EvalMode`, plus everything in `tune.h`.

# The chess engine

A standalone bitboard chess engine in C. No network access, no external engine:
the only outside data it uses are the opening book (handled by lichess-bot
itself, see `engine: polyglot:` in `config.yml`) and the trained network in
`../nnue_weights.npz`.

## Layout

| file | contents |
| --- | --- |
| `ce.h` | types, constants, declarations |
| `bitboard.c` | attack tables; magic bitboards (magics are searched for at start-up with a fixed seed) |
| `position.c` | board state, zobrist hashing, make/unmake, attacks, legality, SEE, draw detection, FEN |
| `movegen.c` | legal move generation (captures/promotions and quiets partition the legal moves) |
| `eval.c` | hand-crafted evaluation and endgame knowledge (KPK bitbase, KXK/KBNK mate guidance) |
| `nnue.c` | the 769-256-1 network from `evaluationModel/`, quantised |
| `tt.c` | shared transposition table |
| `search.c` | iterative deepening PVS search, time management, Lazy SMP |
| `api.c` | the C API the Python side calls |
| `uci.c`, `main.c` | standalone UCI front-end with `bench` / `perft` / `eval` commands |

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

## Running it standalone

`engine/ce.exe` speaks UCI, so it works with cutechess-cli, Arena, Banksia and
friends:

```
engine/ce.exe                       # UCI mode
engine/ce.exe bench 14              # fixed-depth benchmark over 12 positions
engine/ce.exe perft 6
```

Extra UCI commands: `bench [depth]`, `perft [depth]`, `eval`, `d` (print FEN).
Options: `Hash`, `Threads`, `MultiPV`, `EvalMode`.

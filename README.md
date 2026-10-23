# uiagBot

A UCI chess engine written from scratch in C, with an evaluation network
trained from scratch as well, plus the bot that plays it on
[Lichess](https://lichess.org).

```
python engine/build.py          # compile the engine
engine/ce.exe                   # play it as a standalone UCI engine
python lichess-bot.py           # run the Lichess bot
python ccrl/build.py            # build the CCRL release package
```

The engine is a bitboard engine: magic-bitboard move generation (magics
searched for at start-up with a fixed seed), an iterative-deepening PVS search
with the usual modern pruning, Lazy SMP, and a choice of three evaluations
selected by the `EvalMode` option — a Texel-tuned hand-crafted evaluation
(`hce`), the network alone (`nnue`), or a blend of the two (`blend`, the
default and the strongest).

The network is a perspective net, `(768 -> 1024) x 2 -> 1`, with SCReLU,
king-bucketed inputs with the files folded, and eight output buckets by piece
count, quantised to int16 with an incremental accumulator. It was trained on
the public [Lichess evaluation dump](https://database.lichess.org/#evals) — no
self-play against another engine and no distillation of another engine's
search.

Measured against **Stockfish 19 64-bit at one thread** (CCRL 40/15: 3629),
single-threaded, 256 MB hash, 30s+0.3s: **4.5/30, −301 Elo**, which puts
uiagBot in the region of 3300 on that scale. See
[`ccrl/SUBMISSION.md`](ccrl/SUBMISSION.md) for what that number is and is not
worth.

---

## What in here is mine, and what is not

This repository contains two pieces of other people's work, and they are kept
apart from mine so there is no ambiguity about which is which. Everything
under `third_party/` is somebody else's; everything outside it is mine.

| directory | whose | what it is |
| --- | --- | --- |
| [`engine/`](engine/README.md) | **mine** | the chess engine, in C — search, evaluation, the quantised network and its accumulator, and the standalone UCI front-end |
| [`nnue/`](nnue/README.md) | **mine** | the network: architecture, the data pipeline over the Lichess evaluation dump, training, export to the engine's format, and the checks that keep the incremental accumulator honest |
| [`testsuite/`](testsuite/README.md) | **mine** | the harness every change in `engine/` was measured with — perft, tactics, mates, draws, determinism, clock safety, SPRT, Elo ladders |
| [`ccrl/`](ccrl/README.md) | **mine** | packaging the engine for [CCRL](https://computerchess.org.uk/ccrl/4040/) submission |
| `engine_core.py` | **mine** | the cffi bridge that loads `engine/ce.dll` into Python |
| `homemade.py` | **mine** | the `MyEngine` class lichess-bot drives, implementing its `MinimalEngine` interface |
| `lichess-bot.py` | **mine** | a seven-line shim; see below |
| `third_party/lichess-bot/` | **lichess-bot devs** | the bridge between the Lichess Bot API and a chess engine. AGPL-3.0 |
| `third_party/stockfish/` | **Stockfish devs** | Stockfish, used only as a sparring partner and as a referee for generating test positions. GPL-3.0. Never a source of code, weights or search output for the engine |

Full attribution, versions and licence terms: [`THIRD-PARTY.md`](THIRD-PARTY.md).

## Running the bot

```
pip install -r requirements.txt
python engine/build.py
python lichess-bot.py
```

`config.yml` is **not** in the repository — it holds a Lichess API token.
Copy the upstream example from
[lichess-bot](https://github.com/lichess-bot-devs/lichess-bot) and set:

```yaml
engine:
  protocol: "homemade"
  name: "MyEngine"
  homemade_options:
    Hash: 1024          # transposition table, MB
    Threads: 6          # search threads (Lazy SMP); at or below physical cores
    EvalMode: blend     # hce | nnue | blend -- blend is the strongest
```

The engine needs no network access and no other engine. The only outside data
it uses at play time is the Polyglot opening book, which lichess-bot handles
itself (`engine: polyglot:` in `config.yml`), and `engine/net3.bin`.

## Playing the engine on its own

`engine/ce.exe` is an ordinary UCI engine and works with any GUI or with
cutechess. It also answers `bench N`, `perft N`, `eval` and `tune` as
commands. The development build defaults to 64 MB hash, one thread and
`EvalMode=hce`; the [CCRL release build](ccrl/README.md) defaults to 256 MB,
one thread and `blend`, and carries the network inside the executable.

## Licence

uiagBot is **AGPL-3.0** — see [`LICENSE`](LICENSE). It inherits that from
lichess-bot, whose code it links against. The vendored Stockfish under
`third_party/stockfish/` is GPL-3.0 and carries its own licence text; it is a
separate program that this project merely runs, and nothing from it is
compiled into the engine.

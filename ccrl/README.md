# The CCRL submission build

This folder produces a release of the engine in [`../engine`](../engine/README.md)
that can be handed to the [CCRL](https://computerchess.org.uk/ccrl/4040/) testers,
and it holds nothing else. **Nothing here touches the engine the lichess bot
runs.** `python ccrl/build.py` copies `engine/*.c` into `ccrl/build/src`,
patches the copies, and compiles those; `engine/` stays exactly as it is, and
the bot keeps its own settings from `config.yml` (6 threads, 1 GB hash,
`net3.bin` read off the disk).

```
python ccrl/build.py          # patch, compile both targets, verify, zip
python ccrl/build.py --help   # (read the docstring: --no-verify, --keep-src, --depth)
```

Output:

```
ccrl/dist/uiagBot-1.3/uiagBot-1.3-avx2.exe      <- the file to submit
ccrl/dist/uiagBot-1.3/uiagBot-1.3-popcnt.exe    <- pre-AVX2 fallback
ccrl/dist/uiagBot-1.3/readme.txt
ccrl/dist/uiagBot-1.3/LICENSE
ccrl/dist/uiagBot-1.3.zip                       
```

## What CCRL requires, and where each requirement is met

The rules are in two places: the
[40/15 testing conditions](https://computerchess.org.uk/ccrl/4040/about.html),
and the
[*How to get your engine tested for CCRL*](http://kirill-kryukov.com/chess/discussion-board/viewtopic.php?t=11975)
thread on the CCRL forum, which is where the submission format comes from.
That thread is locked, so the post itself goes in a new topic — see
[`SUBMISSION.md`](SUBMISSION.md).

| requirement | where it is met |
| --- | --- |
| Windows `.exe`, AVX2 ("at present our testers all use avx2") | `TARGETS` in `build.py`: `-march=x86-64-v3`, plus a `-msse4.2 -mpopcnt` fallback |
| 64-bit | the only thing the toolchain builds |
| no DLLs to install | `-static -static-libgcc`; `check_imports()` fails the build on any non-system import |
| defaults of 256 MB hash and 1 core ("would be helpful") | `ccrl_main.c`; also what `uci` announces, patched in `build.py` |
| pondering off | `Ponder` is declared `type check default false`, so a tester can see that it is off rather than having to assume it |
| own opening book disabled — *"engines which can't disable their own book can't participate"* | the engine has no book of its own. The Polyglot book in `config.yml` belongs to lichess-bot, not to the engine, and is not in this build |
| book learning off, position learning off | neither exists |
| hash configurable to 256 or 512 MB | `Hash`, 1–32768 MB |
| repeating time control (40 moves in 15 minutes, then another 15) | the time manager reads `movestogo`; `check_plays()` in `build.py` plays a move under one, and `testsuite/run.py timing` covers five sudden-death controls |
| name, version, author, licence, strength, open-source status, AI assistance | `SUBMISSION.md` |

Two things CCRL allows that this engine simply does not do: endgame
tablebases (there is no Syzygy support, so no `SyzygyPath` option) and
Chess960 (so it belongs on the 40/15 list, not
[40/2 FRC](https://computerchess.org.uk/ccrl/404FRC/)).

## How the release build differs from the lichess build

Seven patches, all of them release packaging rather than changes to how the
engine plays. Each is anchored on an exact string from `engine/*.c` and the
build **fails** if an anchor stops matching, so this folder cannot quietly go
stale against the engine it is packaging.

| what | why |
| --- | --- |
| `id name` is `uiagBot 1.3`, not `ce 1.1` | CCRL lists an engine under the name it reports |
| `id author` | likewise |
| the network is linked into the `.exe` (`src/net_embed.S`) and loaded from memory | `engine/main.c` reads `net3.bin` out of the *current working directory*, which in a tournament is whatever the GUI set. One file, no configuration, no way for a tester to end up running the engine without its network |
| `Hash` defaults to 256 MB, not 64 | CCRL asks for it |
| `EvalMode` starts at `blend`, not `hce` | `blend` is the strongest of the three. A GUI that never sends `setoption name EvalMode` would otherwise get the hand-crafted evaluation and about 170 Elo less |
| the ~170 SPSA tuning options are not announced | they exist so a tuner can reach the search constants. 170 spin boxes in a tournament GUI is noise, and a tester who nudges one is no longer testing this engine. They stay settable by name |
| `Move Overhead` is declared, default 50 ms | the engine keeps it as `MoveOverhead` in the tuning table, which a GUI can no longer see. 25 ms (the development default) is right for a local run on an idle machine and thin for a tester running several games at once |

The search, the evaluation and the network are bit-identical to the build that
plays on lichess. `build.py` proves that rather than asserting it: it runs a
fixed-depth single-threaded bench on both binaries and requires every
position's score, best move and node count to match. A loader that read one
field of the network at the wrong offset would diverge on position 1.

## Checking the binary

`build.py` runs these on every build and refuses to package a binary that
fails any of them:

* the UCI handshake a GUI performs, that the release defaults are the
  announced ones, and that no more than a dozen options are declared — the
  check that catches the tuning options leaking back in (`check_uci`)
* nothing but system DLLs in the import table (`check_imports`)
* a full session the way a GUI drives one (`check_plays`): a search under a
  repeating control with `movestogo`, a second under `movetime`, a
  `go infinite` ended by `stop`, and a move asked for in a checkmated and in
  a stalemated position — all four with a legal answer or `bestmove 0000`,
  `info depth` on the way, and a clean exit on `quit`
* the bench comparison against `engine/ce.exe` described above
  (`verify_against_dev`)

`validate.toml` points the project's own testsuite at the packaged binary, so
the file that would be submitted is the one that gets checked:

```
python testsuite/run.py --config ccrl/validate.toml --no-save selftest
python testsuite/run.py --config ccrl/validate.toml --no-save --engine uiagBot-popcnt selftest
python testsuite/run.py --config ccrl/validate.toml --no-save \
    gauntlet --opponents sf-full -n 30 --tc 30+0.3 -c 4
```

As of the build in `dist/`, `selftest` reports: perft correct over 122
positions and 1.5 G nodes; 16/16 drawn endings recognised; searches
reproducible both within a session and across restarts; and time management
safe at all five controls it tries, the tightest being 3+0.02. The mate suite
solves 20 of 21 — which `engine/ce.exe` also does, so it is a property of the
engine and not of this packaging. The popcnt build passes the same perft,
determinism, draw and mate suites, and `bench 10` gives the same 179,938
nodes as the AVX2 build; it is 2.3x slower (260 vs 600 knps) because the
accumulator falls back to scalar code.

The last of those commands is what the strength estimate in `SUBMISSION.md`
rests on: 4.5/30 against Stockfish 19 single-threaded, which is the only
opponent on this machine that is itself on the CCRL list.

## Files

| file | contents |
| --- | --- |
| `build.py` | the release build: staging, the patch list, both targets, the checks, the zip |
| `src/ccrl_main.c` | `main()` for the release binary, replacing `engine/main.c` |
| `src/net_embed.S` | `.incbin` of `net3.bin`, so the network ships inside the executable |
| `validate.toml` | testsuite configuration aimed at `dist/`, not at `engine/` |
| `SUBMISSION.md` | the forum post, ready to send, with the two blanks that need you |
| `build/` | staged sources and intermediate binaries; disposable, rebuilt every time |
| `dist/` | the package |

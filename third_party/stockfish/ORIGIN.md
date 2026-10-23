# This is Stockfish, not uiagBot

**Stockfish 19** — <https://github.com/official-stockfish/Stockfish>
Copyright the Stockfish developers (see [`AUTHORS`](AUTHORS)).
Licensed under the **GNU General Public License v3.0** — see
[`Copying.txt`](Copying.txt).

Vendored here unmodified. `README.md`, `CONTRIBUTING.md`, `wiki/` and `src/`
in this directory are all Stockfish's own.

## Why it is in this repository

Stockfish is used by [`../../testsuite/`](../../testsuite/README.md) in two
ways, both of them as a separate program run over a pipe:

* as an **opponent**, for measuring uiagBot's playing strength — the
  `UCI_LimitStrength` ladder in `testsuite/suite.toml` and the full-strength
  anchor in `ccrl/validate.toml`;
* as a **referee**, in `testsuite/tools/make_suites.py`, which asks it for the
  best move in candidate positions so the stored EPD suites have a
  trustworthy answer.

## What it is not

No part of Stockfish is compiled into uiagBot's engine, and the two share no
source files. `engine/net3.bin` was trained on the public Lichess evaluation
dump, not distilled from Stockfish's network or its search. Removing this
directory breaks `testsuite/` and nothing else — not the engine, not the
Lichess bot, not the CCRL release build.

## The binary

`stockfish-windows-x86-64-universal.exe` is gitignored: it is over GitHub's
100 MB limit. Download it from the
[Stockfish releases](https://github.com/official-stockfish/Stockfish/releases)
or build it from `src/`, and place it in this directory, if you want to run
the parts of `testsuite/` that play games.

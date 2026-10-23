# Third-party code

Everything in this repository under `third_party/` was written by other
people. Nothing outside `third_party/` is derived from it, with the single
exception noted below. This file records what each piece is, where it came
from, and what its licence requires.

The engine itself — `engine/` — contains no third-party code. Its search,
evaluation, network and accumulator were written from scratch, and the network
was trained on the public Lichess evaluation dump. No code, weights, or search
output from another engine went into it.

---

## lichess-bot

| | |
| --- | --- |
| **Location** | `third_party/lichess-bot/` |
| **Version** | 2025.2.6.1 (see `third_party/lichess-bot/lib/versioning.yml`) |
| **Upstream** | <https://github.com/lichess-bot-devs/lichess-bot> |
| **Authors** | Ioannis Pantidis, Mark Harrison, Shail Choksi, Thibault Duplessis, and the lichess-bot contributors |
| **Licence** | GNU Affero General Public License v3.0 |
| **Licence text** | [`LICENSE`](LICENSE) at the root of this repository |
| **Role here** | the bridge between the Lichess Bot API and a chess engine: it talks to Lichess, manages challenges and games, drives the engine, and handles the opening book |

### What was changed

lichess-bot is used as a library, not forked for modification. One edit was
made, and it is commented in place:

* `third_party/lichess-bot/lib/lichess_bot.py` — the version file is opened
  relative to the module (`os.path.dirname(__file__)`) instead of as the
  working-directory-relative literal `"lib/versioning.yml"`. Upstream's form
  only resolves when the bot is started from the directory immediately above
  `lib/`; here `lib/` sits under `third_party/lichess-bot/` and the bot is
  started from the repository root.

Everything else under `third_party/lichess-bot/` is upstream as published,
including its own `README.md` and `CITATION.cff`.

### Interface code that is *not* upstream

lichess-bot defines extension points for the bot author to implement. Those
implementations are mine and live at the repository root, outside
`third_party/`:

* `homemade.py` — the `MyEngine` class, implementing lichess-bot's
  `MinimalEngine` interface
* `engine_core.py` — the cffi bridge from Python to `engine/ce.dll`
* `lichess-bot.py` — the `sys.path` shim and entry point

`third_party/lichess-bot/extra_game_handlers.py` is upstream's own default
implementation of two more such hooks, unmodified, which is why it sits under
`third_party/` rather than at the root.

### What AGPL-3.0 requires of this project

Because uiagBot links against AGPL code, uiagBot as a whole is distributed
under AGPL-3.0. The complete source is published at
<https://github.com/uiag/uiagBot>, which is what the licence's network clause
asks for when the software is operated as a service — a Lichess bot being
exactly that case.

Note that this applies to the repository. The engine binaries released for
CCRL testing are the engine from `engine/`, which contains no lichess-bot
code; they are nevertheless published under the same licence, with source, so
the question does not arise in practice.

---

## Stockfish

| | |
| --- | --- |
| **Location** | `third_party/stockfish/` |
| **Version** | Stockfish 19 |
| **Upstream** | <https://github.com/official-stockfish/Stockfish> |
| **Authors** | the Stockfish developers (see `third_party/stockfish/AUTHORS`) |
| **Licence** | GNU General Public License v3.0 |
| **Licence text** | `third_party/stockfish/Copying.txt` |
| **Role here** | a sparring partner for measuring playing strength, and a referee for generating and verifying the EPD test suites |

### How it is and is not used

Stockfish is run as a separate program over a pipe. It is:

* an **opponent**, in `testsuite/run.py match` / `gauntlet` / `elo` / `sprt`,
  including the `UCI_LimitStrength` ladder in `testsuite/suite.toml` and the
  full-strength anchor in `ccrl/validate.toml`;
* a **referee**, in `testsuite/tools/make_suites.py`, which asks it for the
  best move in candidate positions so that the stored EPD files have a
  trustworthy answer.

It is **not**:

* a source of code — nothing from `third_party/stockfish/src/` is compiled
  into `engine/`, and the two share no files;
* a source of network weights — `engine/net3.bin` was trained from the Lichess
  evaluation dump, not distilled from Stockfish's network;
* a source of training labels — the labels are Lichess's published
  evaluations.

The vendored source tree is kept because it is what the bundled binary was
built from; the binary itself is gitignored (over GitHub's 100 MB limit) and
has to be downloaded or built separately. Nothing in the engine, the bot or
the release build needs it — only `testsuite/` does, and only for the commands
listed above.

### What GPL-3.0 requires

Stockfish is a separate, unmodified program that this project invokes. The
source is included here along with its licence text, which satisfies the
licence for redistribution. It is not linked into anything and does not affect
the licensing of the engine.

---

## Data

| | |
| --- | --- |
| **Lichess evaluation dump** | <https://database.lichess.org/#evals> |
| **Licence** | Creative Commons CC0 (public domain dedication) |
| **Role here** | the training labels for `engine/net3.bin`. The pipeline that turns the dump into training records is `nnue/extract.c` and `nnue/data.py` |

The dump itself is not in the repository — it is about 100 GB.

---

## Tools

The engine is built with GCC via MSYS2/MinGW-w64 and links the GCC runtime
statically. No GCC source is included or redistributed here; the release
binaries contain statically linked runtime code, which the GCC Runtime Library
Exception permits without imposing further conditions.

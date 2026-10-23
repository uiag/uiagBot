# This is lichess-bot, not uiagBot

**lichess-bot 2025.2.6.1** — <https://github.com/lichess-bot-devs/lichess-bot>
Copyright Ioannis Pantidis, Mark Harrison, Shail Choksi, Thibault Duplessis
and the lichess-bot contributors.
Licensed under the **GNU Affero General Public License v3.0** — the licence
text is [`../../LICENSE`](../../LICENSE) at the root of this repository.

`README.md` and `CITATION.cff` in this directory are lichess-bot's own.

## Why it is in this repository

It is the bridge between the Lichess Bot API and a chess engine: it talks to
Lichess, accepts and declines challenges, manages games and clocks, drives the
engine, and handles the Polyglot opening book. uiagBot uses it as published
rather than forking it.

## The one change that was made

* `lib/lichess_bot.py` — the version file is opened relative to its own module
  instead of as the working-directory-relative literal `"lib/versioning.yml"`.
  Upstream's form only resolves when the bot is started from the directory
  immediately above `lib/`; here that directory is
  `third_party/lichess-bot/` and the bot is started from the repository root.
  The change is commented in place.

Nothing else was touched.

## Where uiagBot plugs in

lichess-bot defines extension points for the bot author. Those
implementations are **not** in this directory — they are uiagBot's code and
live at the repository root:

| file | what it does |
| --- | --- |
| `../../lichess-bot.py` | entry point; puts this directory and the repository root on `sys.path`, then calls `lib.lichess_bot.start_program()` |
| `../../homemade.py` | the `MyEngine` class, implementing `lib.engine_wrapper.MinimalEngine` |
| `../../engine_core.py` | the cffi bridge from Python to `engine/ce.dll` |

`extra_game_handlers.py` in this directory is upstream's own default
implementation of two further hooks, unmodified, which is why it is here
rather than at the root.

See [`../../THIRD-PARTY.md`](../../THIRD-PARTY.md) for what AGPL-3.0 requires
of this project.

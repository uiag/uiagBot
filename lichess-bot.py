"""
Start the Lichess bot.

    python lichess-bot.py

uiagBot's own code is the C engine in `engine/`, the network training in
`nnue/`, the test harness in `testsuite/` and the CCRL release packaging in
`ccrl/`.  The bridge to Lichess is lichess-bot, which is not ours -- it lives
unmodified-in-spirit under `third_party/lichess-bot/` (see THIRD-PARTY.md).
This file is the seam between the two.

Two directories have to be importable for that to work, because the
dependency runs both ways:

  * `third_party/lichess-bot/`, so that `import lib.lichess_bot` resolves;
  * the repository root, so that lichess-bot can reach back for the two
    modules it expects a bot author to supply -- `homemade.py` (the
    `MyEngine` class it drives) and `engine_core.py` (the cffi bridge to
    engine/ce.dll).

The root is already on `sys.path` as the directory of this script; the other
one is added below.  Keeping this shim at the top level also means the command
to start the bot has not changed.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
UPSTREAM = os.path.join(ROOT, "third_party", "lichess-bot")

if UPSTREAM not in sys.path:
    sys.path.insert(0, UPSTREAM)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.lichess_bot import start_program  # noqa: E402

if __name__ == "__main__":
    start_program()

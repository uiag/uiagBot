"""
The homemade engine that lichess-bot drives.

`MyEngine` is a thin adapter: it converts lichess-bot's time limit into a
search request for the C engine in engine/ (loaded through engine_core.py) and
converts the result back into a `chess.engine.PlayResult`, including the score,
depth, node count and principal variation so that lichess-bot can show search
statistics in chat and apply its own draw/resign rules.

Engine settings can be given under `engine: homemade_options:` in config.yml:

    homemade_options:
      Hash: 1024          # transposition table size in MB
      Threads: 6         # search threads (Lazy SMP)
      EvalMode: hce      # hce | nnue | blend
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Optional

import chess
from chess.engine import Limit, PlayResult

from lib.engine_wrapper import MinimalEngine
from lib.lichess_types import MOVE

if TYPE_CHECKING:
    from engine_core import Engine

logger = logging.getLogger(__name__)

# One process-wide engine instance: the C side keeps a single transposition
# table and thread pool, and lichess-bot plays one game at a time by default.
_ENGINE: Optional["Engine"] = None


def get_engine(hash_mb: int = 256, threads: Optional[int] = None,
               eval_mode: str = "hce") -> "Engine":
    """Create the shared engine on first use."""
    global _ENGINE
    if _ENGINE is None:
        # Imported here so that the example engines below stay usable without
        # cffi and without compiling anything.
        from engine_core import Engine
        _ENGINE = Engine(hash_mb=hash_mb, threads=threads, eval_mode=eval_mode)
    return _ENGINE


class ExampleEngine(MinimalEngine):
    """Base class for homemade engines; see the project wiki."""


def _option(options: Any, key: str, default: Any) -> Any:
    """Read a value from lichess-bot's option mapping, if present."""
    try:
        if options is None:
            return default
        if hasattr(options, "lookup"):
            value = options.lookup(key)
            return default if value is None else value
        return options.get(key, default)
    except Exception:                                  # noqa: BLE001
        return default


class MyEngine(ExampleEngine):
    """Search with the C engine in engine/."""

    def __init__(self, commands: Any, options: Any, stderr: Optional[int],
                 draw_or_resign: Any, game: Any = None, name: Optional[str] = None,
                 **popen_args: str) -> None:
        """Start up (and if necessary build) the engine."""
        super().__init__(commands, options, stderr, draw_or_resign, game, name, **popen_args)

        default_threads = max(1, (os.cpu_count() or 1) - 2)
        hash_mb = int(_option(options, "Hash", 1024))
        threads = int(_option(options, "Threads", default_threads))
        eval_mode = str(_option(options, "EvalMode", "hce"))

        self.chess_engine = get_engine(hash_mb=hash_mb, threads=threads, eval_mode=eval_mode)
        self.chess_engine.set_hash(hash_mb)
        self.chess_engine.set_threads(threads)
        self.chess_engine.set_eval_mode(eval_mode)
        self.chess_engine.new_game()
        logger.info("MyEngine: %s, %d threads, %d MB hash, eval %s",
                    self.chess_engine.version, self.chess_engine.threads,
                    self.chess_engine.hash_mb, self.chess_engine.eval_mode)

    def search(self, board: chess.Board, time_limit: Limit, ponder: bool,
               draw_offered: bool, root_moves: MOVE) -> PlayResult:
        """Pick a move for `board` within `time_limit`."""
        kwargs: dict[str, int] = {}

        if isinstance(time_limit.time, (int, float)):
            kwargs["movetime_ms"] = max(1, int(time_limit.time * 1000))
        else:
            def ms(value: Any) -> int:
                return max(0, int(value * 1000)) if isinstance(value, (int, float)) else 0
            kwargs["wtime_ms"] = ms(time_limit.white_clock)
            kwargs["btime_ms"] = ms(time_limit.black_clock)
            kwargs["winc_ms"] = ms(time_limit.white_inc)
            kwargs["binc_ms"] = ms(time_limit.black_inc)
            if not kwargs["wtime_ms"] and not kwargs["btime_ms"]:
                kwargs["movetime_ms"] = 3000          # unlimited game, no clock
        if isinstance(time_limit.depth, int):
            kwargs["depth"] = time_limit.depth
        if isinstance(time_limit.nodes, int):
            kwargs["nodes"] = time_limit.nodes

        result = self.chess_engine.search(board, **kwargs)
        move = result.move

        # Honour a restricted move list (e.g. from an endgame tablebase).
        if isinstance(root_moves, list) and root_moves:
            if move not in root_moves:
                move = next((m for m in result.pv if m in root_moves), root_moves[0])
                logger.info("restricting the move to %s from the allowed list", move)

        if result.mate:
            score: chess.engine.Score = chess.engine.Mate(result.mate)
        else:
            score = chess.engine.Cp(result.score_cp)

        info = {
            "score": chess.engine.PovScore(score, board.turn),
            "depth": result.depth,
            "seldepth": result.seldepth,
            "nodes": result.nodes,
            "nps": result.nps,
            "time": result.time_ms / 1000,
            "hashfull": result.hashfull,
            "pv": result.pv,
            "string": f"eval {self.chess_engine.eval_mode}",
        }

        ponder_move = result.ponder if ponder else None
        return PlayResult(move, ponder_move, info=info, draw_offered=draw_offered)

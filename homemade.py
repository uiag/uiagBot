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

        # The position a ponder search is currently thinking about, if any.
        self._ponder_fen: Optional[str] = None
        self._ponder_hits = 0
        self._ponder_misses = 0
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

        result = self._search_or_ponderhit(board, kwargs)
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
        if ponder:
            self._start_pondering(board, move, ponder_move)
        return PlayResult(move, ponder_move, info=info, draw_offered=draw_offered)

    # ------------------------------------------------------------------
    # pondering
    #
    # lichess-bot drives a homemade engine by calling `search` and waiting for
    # it, so half of every game -- the opponent's half -- used to be spent
    # doing nothing at all.  The engine now keeps searching the position it
    # expects after the opponent's reply.  When the reply is the predicted one
    # the search is handed the clock and carries on from the depth it has
    # already reached; when it is not, the search is dropped, and the part of
    # it that was useful stays in the transposition table either way.
    #
    # `ponder` is passed in by lichess-bot according to `ponder:` in
    # config.yml, and it is already false where pondering would be wrong --
    # the first move of a game, and correspondence games the bot is not
    # connected to.
    # ------------------------------------------------------------------

    def _search_or_ponderhit(self, board: chess.Board,
                             kwargs: dict[str, int]) -> Any:
        """Use the running ponder search if it is on this very position."""
        engine = self.chess_engine
        if self._ponder_fen is None:
            return engine.search(board, **kwargs)

        want, self._ponder_fen = self._ponder_fen, None
        if not engine.ponder_running():
            # It ran out of depth while we waited; nothing to hand a clock to.
            logger.info("the ponder search had already finished; searching afresh")
            return engine.search(board, **kwargs)

        if want != board.fen():
            self._ponder_misses += 1
            # Log both positions: a hit rate that is lower than the engine's
            # own move prediction deserves looks exactly like a correct miss
            # unless you can see what was compared.
            logger.info("ponder miss (%d hit / %d miss); the table keeps the "
                        "work\n  pondered %s\n  arrived  %s",
                        self._ponder_hits, self._ponder_misses, want, board.fen())
            engine.ponder_abort()
            return engine.search(board, **kwargs)

        self._ponder_hits += 1
        logger.info("ponder hit (%d hit / %d miss)",
                    self._ponder_hits, self._ponder_misses)
        return engine.ponder_hit(board, **kwargs)

    def _start_pondering(self, board: chess.Board, move: Optional[chess.Move],
                         ponder_move: Optional[chess.Move]) -> None:
        """Think about the position after the reply we expect."""
        self._ponder_fen = None
        if move is None or ponder_move is None:
            return
        try:
            scratch = board.copy()
            scratch.push(move)
            if ponder_move not in scratch.legal_moves:
                return
            scratch.push(ponder_move)
            if scratch.is_game_over(claim_draw=False):
                return
            fen = scratch.fen()
            if self.chess_engine.ponder_start(scratch):
                self._ponder_fen = fen
        except Exception:                              # noqa: BLE001
            # Pondering is an optimisation; nothing about it is worth losing
            # a game over, so a failure here only costs the optimisation.
            logger.exception("could not start pondering")
            self._ponder_fen = None

    def quit(self) -> None:
        """Shut down: a ponder search must not outlive the game."""
        try:
            self.chess_engine.ponder_abort()
        except Exception:                              # noqa: BLE001
            pass
        self._ponder_fen = None
        super().quit()

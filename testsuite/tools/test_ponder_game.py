"""
Drive `MyEngine.search` through a game, the way lichess-bot does.

`test_ponder.py` covers the UCI path and `test_ponder_bridge.py` the engine
bridge.  Neither touches the code that actually decides whether to ponder --
`MyEngine._start_pondering` and `MyEngine._search_or_ponderhit` -- and that is
the part with the interesting failure modes: pondering the wrong position,
failing to notice a hit, or leaving a search running across a move.

So this plays moves alternately out of one MyEngine instance, exactly as
lichess-bot's `play_move` does, and checks the hit counter moves.  Half the
replies are the engine's own predicted move (a hit) and half are deliberately
something else (a miss), so both paths are taken.

    python testsuite/tools/test_ponder_game.py
"""
from __future__ import annotations

import logging
import time
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

import chess                                   # noqa: E402
from chess.engine import Limit                 # noqa: E402

from lib.config import Configuration           # noqa: E402
import homemade                                # noqa: E402

MOVETIME = 0.25


def build_engine() -> "homemade.MyEngine":
    draw_or_resign = Configuration({
        "resign_enabled": False, "resign_score": -1000, "resign_for_egtb_zero": False,
        "offer_draw_enabled": False, "offer_draw_score": 0, "offer_draw_moves": 10,
        "offer_draw_pieces": 10, "offer_draw_for_egtb_zero": False,
    })
    options = {"Hash": 64, "Threads": 2, "EvalMode": "hce"}
    return homemade.MyEngine([], options, None, draw_or_resign)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="      %(message)s")
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}: {detail}")
        if not ok:
            failures.append(name)

    print("a game through MyEngine, pondering on:")
    eng = build_engine()
    board = chess.Board()

    hits_expected = 0
    offered = 0           # moves where the engine had a reply to ponder on
    started = 0           # of those, how many actually began a search
    for move_no in range(10):
        limit = Limit(time=MOVETIME)
        result = eng.search(board, limit, True, False, [])
        if result.move is None or result.move not in board.legal_moves:
            check(f"move {move_no + 1} is legal", False, f"{result.move}")
            break
        predicted = result.ponder
        board.push(result.move)

        if board.is_game_over():
            break
        # A short search sometimes returns a one-move principal variation, and
        # then there is no predicted reply to ponder on; that is not a fault,
        # so what is checked is that a prediction always leads to a search.
        if predicted is not None and predicted in board.legal_moves:
            offered += 1
            if eng._ponder_fen is not None:
                started += 1

        # Alternate: play the move the engine expects, then something else.
        replies = list(board.legal_moves)
        if move_no % 2 == 0 and predicted in replies:
            board.push(predicted)
            hits_expected += 1
        else:
            other = next((m for m in replies if m != predicted), replies[0])
            board.push(other)
        if board.is_game_over():
            break

    total = eng._ponder_hits + eng._ponder_misses
    check("every predicted reply was pondered on", offered and started == offered,
          f"{started} searches started for {offered} predicted replies")
    check("the pondering was then used", total >= offered - 1,
          f"{total} of {offered} were consumed by the next move")
    check("predicted replies were recognised as hits",
          eng._ponder_hits >= hits_expected - 1,
          f"{eng._ponder_hits} hits (expected about {hits_expected}), "
          f"{eng._ponder_misses} misses")
    check("unpredicted replies were recognised as misses",
          eng._ponder_misses >= 3,
          f"{eng._ponder_misses} misses")
    check("no search is left running between moves",
          not eng.chess_engine.ponder_running() or eng._ponder_fen is not None,
          "a running search always has a position recorded for it")

    # Shutting down must not leave a thread searching.
    eng.quit()
    check("quit stops the ponder search",
          not eng.chess_engine.ponder_running(), "idle after quit")

    # And the engine still works afterwards.
    again = eng.search(chess.Board(), Limit(time=MOVETIME), False, False, [])
    check("it still plays after a shutdown and restart",
          again.move is not None, f"{again.move}")

    # ---------------------------------------------------------------
    # The same game again, but on a clock rather than a fixed move time,
    # because that is what lichess sends and it used to hang.
    #
    # `search_go` copies the limits and computes the clock on the search
    # thread, so a hit handed to a background search before it got that far
    # was overwritten by the infinite limits it had been started with -- and
    # then nothing stopped it.  It only showed up when the reply came back
    # within a millisecond or two of the ponder search starting, which on a
    # fixed `movetime` it never did, and on a real clock it always does.
    # ---------------------------------------------------------------
    print("\nthe same, on a clock:")
    eng2 = build_engine()
    board = chess.Board()
    clock = 10.0
    slowest = 0.0
    moves_played = 0
    for _ in range(6):
        t0 = time.perf_counter()
        r = eng2.search(board, Limit(white_clock=clock, black_clock=clock,
                                     white_inc=0.1, black_inc=0.1),
                        True, False, [])
        dt = time.perf_counter() - t0
        slowest = max(slowest, dt)
        if r.move is None or r.move not in board.legal_moves:
            break
        moves_played += 1
        board.push(r.move)
        if r.ponder is not None and r.ponder in board.legal_moves:
            board.push(r.ponder)          # always the predicted reply: all hits
        else:
            break

    check("a clocked game runs to the end", moves_played >= 5,
          f"{moves_played} moves played")
    check("every move respected the clock", slowest < clock / 2,
          f"slowest move {slowest:.2f}s of a {clock:.0f}s clock")
    check("the clocked hits were recognised", eng2._ponder_hits >= 4,
          f"{eng2._ponder_hits} hits, {eng2._ponder_misses} misses")
    eng2.quit()
    check("the clocked engine shuts down", not eng2.chess_engine.ponder_running(),
          "idle after quit")

    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        return 1
    print("the lichess-bot ponder path works")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Check pondering through the Python bridge, the way lichess-bot drives it.

The UCI side is covered by test_ponder.py.  This exercises the other path --
`engine_core.Engine.ponder_start` / `ponder_hit` / `ponder_abort` and the
MyEngine logic that decides between them -- because that is the one lichess
actually uses, and the two share no code above the C API.

What is checked is that the thinking is *kept*: a ponder hit has to come back
having searched more nodes than the same position searched cold with the same
clock.  That is the only observable difference between real pondering and a
ponder move that is politely reported and then thrown away.

    python testsuite/tools/test_ponder_bridge.py
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

import chess                                   # noqa: E402
import engine_core                             # noqa: E402

OPENING = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "g8f6"]
MOVETIME = 500


def main() -> int:
    eng = engine_core.Engine(hash_mb=64, threads=2, eval_mode="hce")
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}: {detail}")
        if not ok:
            failures.append(name)

    print("pondering through the bridge:")

    board = chess.Board()
    for uci in OPENING:
        board.push(chess.Move.from_uci(uci))

    # --- cold: how much does this position cost from an empty table ---
    eng.new_game()
    cold = eng.search(board, movetime_ms=MOVETIME)
    check("a cold search answers", cold.move is not None,
          f"{cold.move} depth {cold.depth} {cold.nodes} nodes")

    # --- warm: ponder the same position, then hit ---
    eng.new_game()
    parent = board.copy()
    reply = parent.pop()                      # the move we will "predict"
    our = parent.pop()
    before = parent.copy()                    # position we are about to move in

    first = eng.search(before, movetime_ms=MOVETIME)
    check("the engine offers a ponder move", first.ponder is not None,
          f"best {first.move}, expects {first.ponder}")

    scratch = before.copy()
    scratch.push(first.move)
    predicted = first.ponder
    if predicted is None or predicted not in scratch.legal_moves:
        check("the ponder move is legal", False, f"{predicted}")
        return 1
    scratch.push(predicted)

    started = eng.ponder_start(scratch)
    check("a ponder search starts", started, "running" if started else "refused")
    if not started:
        return 1

    time.sleep(1.5)                            # the opponent's clock
    check("it is still running after 1.5 s", eng.ponder_running(), "yes")

    t0 = time.perf_counter()
    hit = eng.ponder_hit(scratch, movetime_ms=MOVETIME)
    dt = time.perf_counter() - t0
    check("a ponder hit answers", hit.move is not None,
          f"{hit.move} depth {hit.depth} {hit.nodes} nodes in {dt * 1000:.0f} ms")

    # The pondered search had 1.5 s of free thinking before its 0.5 s, so it
    # must come back with substantially more than a cold search of the same
    # length.  Nodes, not depth: depth saturates on a quiet position.
    eng.new_game()
    coldsame = eng.search(scratch, movetime_ms=MOVETIME)
    ratio = hit.nodes / max(coldsame.nodes, 1)
    check("the pondered thinking was kept", ratio > 1.8,
          f"{hit.nodes:,} nodes after pondering vs {coldsame.nodes:,} cold "
          f"({ratio:.1f}x), depth {hit.depth} vs {coldsame.depth}")

    # --- a miss must leave the engine usable ---
    eng.new_game()
    eng.ponder_start(scratch)
    time.sleep(0.4)
    eng.ponder_abort()
    check("the engine is idle after an abort", not eng.ponder_running(), "idle")
    after = eng.search(before, movetime_ms=300)
    check("it searches normally after a miss", after.move is not None,
          f"{after.move} depth {after.depth}")

    # --- a stray background search must not break an ordinary search ---
    eng.ponder_start(scratch)
    time.sleep(0.2)
    guard = eng.search(before, movetime_ms=300)
    check("an ordinary search clears a stray ponder",
          guard.move is not None and not eng.ponder_running(),
          f"{guard.move}, idle afterwards")

    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        return 1
    print("all bridge ponder checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Check that the engine really thinks on the opponent's clock.

Pondering is easy to get wrong in a way that costs nothing visible: the engine
answers every command, plays legal moves, and simply throws away the work it
did while waiting.  So each part is checked for an observable consequence
rather than for the absence of an error.

  1. `go infinite` has to be interruptible at all.  It was not: the command
     loop blocked inside the search, so `stop` was never read and the handler
     for it was dead code.
  2. `go ponder` then `ponderhit` has to come back *deeper* than the search
     had reached when the hit arrived, and has to spend the new clock rather
     than answering at once -- that is the whole benefit, and a ponderhit that
     restarted the search instead would look fine without it.
  3. A ponder miss still owes the host exactly one `bestmove`, no more.
  4. An ordinary timed `go` still has to answer by itself, with nobody
     sending `stop`.

    python testsuite/tools/test_ponder.py [engine/ce.exe]
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


class Uci:
    def __init__(self, path: str) -> None:
        self.p = subprocess.Popen([path], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, bufsize=1,
                                  cwd=os.path.dirname(path) or ".")
        self.lines: list[str] = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.p.stdout:                      # type: ignore[union-attr]
            self.lines.append(line.rstrip())

    def send(self, cmd: str) -> None:
        self.p.stdin.write(cmd + "\n")                  # type: ignore[union-attr]
        self.p.stdin.flush()                            # type: ignore[union-attr]

    def clear(self) -> None:
        self.lines.clear()

    def wait_for(self, prefix: str, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if any(l.startswith(prefix) for l in self.lines):
                return True
            time.sleep(0.02)
        return False

    def depth(self) -> int:
        return max((int(l.split()[2]) for l in self.lines
                    if l.startswith("info depth")), default=0)

    def bestmoves(self) -> list[str]:
        return [l for l in self.lines if l.startswith("bestmove")]


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "engine", "ce.exe")
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        print(f"no engine at {path}")
        return 1
    e = Uci(path)
    failures = []

    def check(name: str, ok: bool, detail: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}: {detail}")
        if not ok:
            failures.append(name)

    e.send("uci")
    if not e.wait_for("uciok", 5):
        print("engine did not answer uci")
        return 1
    e.send("setoption name Threads value 2")
    e.send("ucinewgame")
    e.send("isready")
    e.wait_for("readyok", 5)

    print("pondering:")

    # 1 -- an infinite search must be stoppable
    e.clear()
    e.send("position startpos moves e2e4 e7e5")
    e.send("go infinite")
    time.sleep(1.2)
    infos = sum(1 for l in e.lines if l.startswith("info depth"))
    e.send("stop")
    got = e.wait_for("bestmove", 5)
    check("go infinite is interruptible", got and infos > 0,
          f"{infos} info lines, answered: {got}")

    # 2 -- a ponder hit keeps the tree and spends the new clock
    e.clear()
    e.send("position startpos moves e2e4 e7e5 g1f3")
    e.send("go ponder wtime 10000 btime 10000")
    time.sleep(1.5)
    before = e.depth()
    t0 = time.time()
    e.send("ponderhit")
    got = e.wait_for("bestmove", 15)
    dt = time.time() - t0
    after = e.depth()
    check("ponderhit keeps the pondered tree", got and after > before,
          f"depth {before} while pondering, {after} after the hit")
    # 10 s with no moves to go gives roughly a third of a second of optimum
    # time and several times that as a maximum; answering instantly would
    # mean the clock was never applied, and never answering means it was
    # applied as infinite.
    check("ponderhit spends the new clock", got and 0.05 < dt < 12.0,
          f"answered {dt:.2f}s after the hit")

    # 3 -- a miss still owes exactly one bestmove
    e.clear()
    e.send("position startpos moves d2d4")
    e.send("go ponder wtime 5000 btime 5000")
    time.sleep(0.8)
    e.send("stop")
    e.wait_for("bestmove", 5)
    time.sleep(0.4)
    n = len(e.bestmoves())
    check("a ponder miss answers exactly once", n == 1, f"{n} bestmove lines")

    # 4 -- an ordinary timed search still ends on its own
    e.clear()
    e.send("position startpos")
    t0 = time.time()
    e.send("go movetime 700")
    got = e.wait_for("bestmove", 6)
    dt = time.time() - t0
    check("a timed go ends by itself", got and dt < 5.0,
          f"answered in {dt:.2f}s")

    e.send("quit")
    try:
        e.p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        check("quit exits", False, "engine did not exit")
        e.p.kill()
    else:
        check("quit exits", e.p.returncode == 0, f"exit {e.p.returncode}")

    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        return 1
    print("all ponder checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

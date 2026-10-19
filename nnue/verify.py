"""
Prove the incremental accumulator agrees with a full refresh.

A wrong accumulator delta is the quietest bug in an engine with a network: the
evaluation is off by a few centipawns, but only in positions reached by one
particular kind of move, so the engine still plays, still benchmarks, and
still passes a perft.  A CE_NNUE_VERIFY build recomputes the first layer from
scratch after every incremental update and aborts on the first disagreement;
this drives it over the move types that the delta has to special-case.

Castling moves two pieces.  En passant removes a pawn from a square the move
never mentions.  A promotion removes a pawn and adds something else.  Each is
a separate branch and each is checked here on positions where the search
cannot avoid playing them.

    python nnue/verify.py            # builds ce_verify.exe and runs it
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "engine")

SOURCES = ["bitboard.c", "position.c", "movegen.c", "eval.c", "nnue.c",
           "tt.c", "tune.c", "search.c", "api.c", "uci.c", "main.c"]

# Each: a label and a FEN the search has to resolve by playing the move type.
POSITIONS = [
    ("opening, both castlings available",
     "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1"),
    ("castling both sides, open centre",
     "r3k2r/pppq1ppp/2n1bn2/3pp3/3PP3/2N1BN2/PPPQ1PPP/R3K2R w KQkq - 0 1"),
    ("en passant available",
     "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3"),
    ("en passant, pawns locked",
     "8/2p5/3p4/KP5r/1R3pPk/8/4P3/8 b - g3 0 1"),
    ("promotions on both wings",
     "8/PPP4k/8/8/8/8/4Kppp/8 w - - 0 1"),
    ("promotion with capture",
     "n1n5/PPPk4/8/8/8/8/4Kppp/5N1N b - - 0 1"),
    ("queens and heavy pieces",
     "r1bq1r1k/1pp1n1pp/1p1p4/4p2Q/4Pp2/1BNP4/PPP2PPP/R4RK1 w - - 2 14"),
    ("endgame, few pieces",
     "6k1/6p1/7p/P5P1/1p4KP/2p5/8/8 b - - 0 1"),
    ("kings and pawns only",
     "8/8/3P3k/8/1p6/8/1P6/1K6 w - - 0 1"),
    ("start position", None),
]


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def drive(exe: str, net: str, env: dict, fen, depth: int) -> tuple[int, str, bool]:
    """Search one position and return (nodes, stderr, crashed)."""
    import threading
    import time

    p = subprocess.Popen([exe, "--net", os.path.basename(net)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, bufsize=1,
                         cwd=ENGINE, env=env)
    lines: list[str] = []
    errs: list[str] = []
    threading.Thread(target=lambda: [lines.append(l) for l in p.stdout],
                     daemon=True).start()
    threading.Thread(target=lambda: [errs.append(l) for l in p.stderr],
                     daemon=True).start()

    def send(cmd: str) -> None:
        try:
            p.stdin.write(cmd + "\n")
            p.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    for cmd in ("uci", "setoption name EvalMode value nnue",
                "setoption name Threads value 1", "isready",
                "position startpos" if fen is None else f"position fen {fen}",
                f"go depth {depth}"):
        send(cmd)

    deadline = time.time() + 180
    while time.time() < deadline:
        if any(l.startswith("bestmove") for l in lines):
            break
        if p.poll() is not None:
            break
        time.sleep(0.02)
    send("quit")
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()

    nodes = 0
    for line in lines:
        if line.startswith("info depth"):
            parts = line.split()
            if "nodes" in parts:
                nodes = max(nodes, int(parts[parts.index("nodes") + 1]))
    # An abort() inside the verify check kills the process; a clean exit is 0.
    crashed = p.returncode not in (0, None)
    return nodes, "".join(errs), crashed


def find_compiler() -> str:
    import shutil
    for name in ("gcc", "clang", "cc"):
        p = shutil.which(name)
        if p:
            return p
    for p in (r"C:\Program Files\MSYS2\ucrt64\bin\gcc.exe",
              r"C:\msys64\ucrt64\bin\gcc.exe"):
        if os.path.isfile(p):
            return p
    raise SystemExit("no C compiler found")


def main() -> int:
    depth = int(sys.argv[1]) if len(sys.argv) > 1 else 11
    exe = os.path.join(ENGINE, "ce_verify.exe")
    net = os.path.join(ENGINE, "net_random.bin")

    if not os.path.isfile(net):
        print("no net_random.bin; run nnue/make_random_net.py first "
              "(needs the training environment)")
        return 1

    cc = find_compiler()
    env = dict(os.environ)
    env["PATH"] = os.path.dirname(cc) + os.pathsep + env.get("PATH", "")
    print(f"building a CE_NNUE_VERIFY engine with {os.path.basename(cc)}")
    cmd = [cc, "-std=c11", "-O2", "-march=native", "-DNDEBUG",
           "-DCE_NNUE_VERIFY", "-o", exe]
    cmd += [os.path.join(ENGINE, s) for s in SOURCES] + ["-lm"]
    if sys.platform == "win32":
        cmd += ["-static", "-static-libgcc"]
    p = run(cmd, cwd=ENGINE, env=env)
    if p.returncode != 0:
        print(p.stdout, p.stderr)
        return 1

    print(f"searching {len(POSITIONS)} positions to depth {depth}; every "
          f"incremental update is checked against a refresh\n")
    failures = 0
    total_nodes = 0
    for label, fen in POSITIONS:
        # The search runs on its own thread now, so `go` returns at once and
        # a `quit` written straight after it would abort the search before it
        # had checked anything.  Wait for the bestmove, as a UCI host must.
        nodes, err, crashed = drive(exe, net, env, fen, depth)
        bad = "mismatch" in err or crashed
        total_nodes += nodes
        if bad:
            failures += 1
            print(f"  FAIL  {label}")
            print("    " + "\n    ".join(err.splitlines()[:6]))
        else:
            print(f"  ok    {label}: {nodes:,} nodes, no disagreement")

    print()
    if failures:
        print(f"{failures} of {len(POSITIONS)} positions disagreed")
        return 1
    print(f"the incremental accumulator matched a full refresh over "
          f"{total_nodes:,} nodes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

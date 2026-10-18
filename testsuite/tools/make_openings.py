"""
Build a large, balanced opening book.

Matches at a fixed node count are deterministic, so the number of distinct
games a match can produce is exactly twice the number of openings.  The
hand-picked `openings.epd` has forty lines, which caps a match at eighty
distinct games however many are asked for -- every game past that repeats one
already played and adds no information at all.  This generates thousands
instead.

Each opening is grown one ply at a time: the referee lists its best few moves
and one of those within `--spread` centipawns of the best is picked at random,
which wanders over real opening theory rather than over random legal moves.
The finished position is kept only if the referee still calls it balanced.

    python testsuite/tools/make_openings.py                  # 2000 openings
    python testsuite/tools/make_openings.py -n 500 --plies 10
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor

import chess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))

from ts import config as cfgmod            # noqa: E402
from ts.engines import Limit, UciEngine    # noqa: E402


def _worker(args: tuple) -> list[str]:
    spec, count, plies, spread, balance, depth, seed = args
    rng = random.Random(seed)
    engine = UciEngine(spec).open()
    try:
        out: list[str] = []
        attempts = 0
        while len(out) < count and attempts < count * 6:
            attempts += 1
            board = chess.Board()
            ok = True
            for _ in range(plies):
                res = engine.analyse(board, Limit(depth=8), multipv=5)
                scored = []
                for info in res.lines:
                    if not info.pv or not info.has_score:
                        continue
                    scored.append((info.score_cp, info.pv[0]))
                if not scored:
                    ok = False
                    break
                best = max(c for c, _ in scored)
                pool = [m for c, m in scored if best - c <= spread]
                board.push(rng.choice(pool))
                if board.is_game_over():
                    ok = False
                    break
            if not ok or board.is_game_over():
                continue
            res = engine.analyse(board, Limit(depth=depth))
            if not res.info.has_score:
                continue
            score = res.info.score_cp
            if board.turn == chess.BLACK:
                score = -score
            if abs(score) > balance:
                continue
            out.append(board.fen())
        return out
    finally:
        engine.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--count", type=int, default=2000)
    ap.add_argument("--plies", type=int, default=8)
    ap.add_argument("--spread", type=int, default=35,
                    help="centipawns from the best move a candidate may be")
    ap.add_argument("--balance", type=int, default=50,
                    help="keep the position only if |eval| is at most this")
    ap.add_argument("--depth", type=int, default=12, help="depth of the balance check")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("-o", "--out", default="")
    ap.add_argument("--config", default="")
    args = ap.parse_args()

    cfg = cfgmod.load(args.config or None)
    if cfg.referee is None:
        raise SystemExit("suite.toml has no [referee] to generate openings with")
    spec = cfg.referee.with_overrides({"Threads": 1})

    out_path = args.out or os.path.join(cfg.suites_dir, "openings_big.epd")
    per = (args.count + args.jobs - 1) // args.jobs
    jobs = [(spec, per, args.plies, args.spread, args.balance,
             args.depth, args.seed * 7919 + i) for i in range(args.jobs)]

    print(f"generating {args.count} openings with {args.jobs} jobs "
          f"({args.plies} plies, |eval| <= {args.balance} at depth {args.depth})")
    fens: list[str] = []
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for chunk in pool.map(_worker, jobs):
            fens.extend(chunk)
            print(f"  {len(fens)} so far")

    seen: set[str] = set()
    unique = []
    for fen in fens:
        key = " ".join(fen.split()[:4])
        if key in seen:
            continue
        seen.add(key)
        unique.append(fen)
    unique = unique[:args.count]

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(f"# {len(unique)} openings, {args.plies} plies, referee-balanced "
                 f"to {args.balance} cp at depth {args.depth}\n")
        fh.write("# generated by testsuite/tools/make_openings.py -- do not edit\n")
        for i, fen in enumerate(unique):
            fh.write(f'{fen}; id "gen{i:05d}";\n')
    print(f"wrote {len(unique)} unique openings to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

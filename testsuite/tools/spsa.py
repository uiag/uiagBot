"""
Tune search parameters by playing games (SPSA).

The search margins cannot be fitted to a position set: nothing about a static
evaluation says how deep to look.  They have to be measured by playing, and a
single game says almost nothing, so this uses simultaneous perturbation --
every parameter is nudged at once, both ways, and the direction that scores
better moves them all a little.  Each iteration is nearly worthless on its own
and the average over a few hundred is not.

    python testsuite/tools/spsa.py --iters 400
    python testsuite/tools/spsa.py --only FutilityBase,FutilityMult --iters 200
    python testsuite/tools/spsa.py --groups lmr,pruning --games 4

Engines are started once per worker process and kept alive, with `setoption`
between games, because at four games an iteration process startup would
otherwise be most of the cost.

The result is written as a list of values; feed them back with
`--engine ce:Name=value,...` and confirm with tools/duel.py before believing
them.
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import chess

HERE = os.path.dirname(os.path.abspath(__file__))
TS_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(TS_DIR)
sys.path.insert(0, TS_DIR)

from ts import config as cfgmod                       # noqa: E402
from ts.engines import Limit, UciEngine                # noqa: E402

GROUPS: dict[str, list[str]] = {
    "lmr": ["LmrQuietDiv", "LmrNoisyDiv", "LmrQuietBase", "LmrNoisyBase",
            "LmrDeltaWeight", "LmrDeltaCap", "LmrHistDiv", "LmrHistMax",
            "LmrCutoffCnt", "LmrDeeperMargin", "QuietBonusMargin"],
    "pruning": ["RfpMargin", "RfpMaxDepth", "RazorBase", "RazorMult",
                "FutilityBase", "FutilityMult", "FutilityMaxDepth",
                "HistPruneMult", "HistPruneDepth", "LmpBase",
                "SeePruneQuiet", "SeePruneNoisy"],
    "qs": ["QsFutilityMargin", "QsSeeMargin"],
    "nullmove": ["NmpBase", "NmpDepthDiv", "NmpEvalDiv", "NmpMargin",
                 "NmpMarginDepth", "ProbcutMargin", "ProbcutImproving"],
    "ext": ["SingularMinDepth", "SingularMargin", "SingularPvMargin",
            "SingularDouble", "SingularTriple", "CheckExtDepth"],
    "corr": ["CorrWPawn", "CorrWMinor", "CorrWMajor", "CorrWNonPawn"],
    "asp": ["AspInitDelta", "AspScoreDiv", "TimeNodeEffort"],
    # Everything the search compares against a static evaluation.  Rescaling
    # the evaluation changes what all of these mean at once, so this is the
    # group to run after any change to PosWeight*, EvalScale or the piece
    # values -- it is where the systematic error will be.
    "scaled": ["RfpMargin", "RazorBase", "RazorMult", "FutilityBase",
               "FutilityMult", "QsFutilityMargin", "NmpMargin", "NmpEvalDiv",
               "ProbcutMargin", "SingularMargin", "SingularDouble",
               "SingularTriple", "LmrDeeperMargin", "QuietBonusMargin",
               "AspInitDelta"],
}
DEFAULT_GROUPS = ["lmr", "pruning", "qs", "nullmove", "ext", "corr"]

_STATE: dict = {}


def _init_worker(spec_text: str, config_path: str, nodes: int) -> None:
    cfg = cfgmod.load(config_path or None)
    # One thread and a small hash: under a node limit the budget is shared
    # between threads, so a multi-threaded search reaches *less* depth, and
    # measuring tuning deltas in that regime measures the wrong engine.
    spec = cfg.resolve(spec_text).with_overrides({"Threads": 1, "Hash": 32})
    _STATE["a"] = UciEngine(spec).open()
    _STATE["b"] = UciEngine(spec).open()
    _STATE["limit"] = Limit(nodes=nodes)
    _STATE["adj"] = cfg.adjudication


def _apply(engine: UciEngine, values: dict[str, int]) -> None:
    for name, value in values.items():
        engine.send(f"setoption name {name} value {int(value)}")
    engine.wait_ready()


def _play(args: tuple) -> float:
    """Play one game; return the score for the `plus` side (1, 0.5 or 0)."""
    plus, minus, fen, plus_is_white = args
    a, b = _STATE["a"], _STATE["b"]
    limit, adj = _STATE["limit"], _STATE["adj"]
    _apply(a, plus)
    _apply(b, minus)
    white, black = (a, b) if plus_is_white else (b, a)
    a.new_game()
    b.new_game()

    board = chess.Board(fen)
    drawish = 0
    resign = 0
    last_loser = None
    while True:
        if board.is_game_over(claim_draw=True):
            break
        if board.ply() > adj.max_plies:
            return 0.5
        engine = white if board.turn == chess.WHITE else black
        res = engine.search(board, limit)
        if res.move is None or res.move not in board.legal_moves:
            # treat a protocol failure as a loss for the offender
            return 0.0 if engine is a else 1.0
        cp = res.info.score_cp if res.info.has_score else 0
        board.push(res.move)

        if adj.draw_enabled and board.ply() >= adj.draw_after_ply:
            drawish = drawish + 1 if abs(cp) <= adj.draw_score else 0
            if drawish >= adj.draw_plies:
                return 0.5
        if adj.resign_enabled:
            loser = engine
            if cp <= -adj.resign_score:
                resign = resign + 1 if last_loser is loser else 1
                last_loser = loser
                if resign >= adj.resign_plies:
                    return 0.0 if loser is a else 1.0
            elif last_loser is loser:
                resign = 0

    outcome = board.outcome(claim_draw=True)
    if outcome is None or outcome.winner is None:
        return 0.5
    winner = white if outcome.winner == chess.WHITE else black
    return 1.0 if winner is a else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", default="ce")
    ap.add_argument("--iters", type=int, default=400)
    ap.add_argument("--games", type=int, default=8, help="games per iteration")
    ap.add_argument("--nodes", type=int, default=20000)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--groups", default=",".join(DEFAULT_GROUPS))
    ap.add_argument("--only", default="")
    ap.add_argument("--alpha", type=float, default=0.60,
                    help="step size as a fraction of the perturbation")
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--config", default="")
    ap.add_argument("-o", "--out", default="")
    args = ap.parse_args()

    cfg = cfgmod.load(args.config or None)

    # tune.h is the authority on the defaults and the ranges
    bounds: dict[str, tuple[int, int, int]] = {}
    tune_h = os.path.join(ROOT, "engine", "tune.h")
    import re
    for m in re.finditer(r"TUNE\(\s*(\w+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*\)",
                         open(tune_h, encoding="utf-8").read()):
        name, dflt, lo, hi = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        bounds[name] = (dflt, lo, hi)

    if args.only:
        params = [p.strip() for p in args.only.split(",") if p.strip()]
    else:
        params = []
        for g in args.groups.split(","):
            g = g.strip()
            if g:
                if g not in GROUPS:
                    raise SystemExit(f"unknown group {g!r}; have {', '.join(GROUPS)}")
                params += GROUPS[g]
    params = [p for p in dict.fromkeys(params) if p in bounds]
    if not params:
        raise SystemExit("no parameters to tune")

    theta = {p: float(bounds[p][0]) for p in params}
    # perturbation size: a fraction of the range, never less than one step
    ck = {p: max(1.0, (bounds[p][2] - bounds[p][1]) * 0.06) for p in params}

    book = os.path.join(cfg.suites_dir, "openings_big.epd")
    if not os.path.isfile(book):
        book = os.path.join(cfg.suites_dir, "openings.epd")
    fens = [l.split(";")[0].strip() for l in open(book, encoding="utf-8")
            if l.strip() and not l.startswith("#")]
    rng = random.Random(args.seed)
    rng.shuffle(fens)

    print(f"SPSA over {len(params)} parameters, {args.iters} iterations x "
          f"{args.games} games at nodes={args.nodes}, {args.jobs} workers")
    print("  " + ", ".join(params))

    def clamp(name: str, value: float) -> float:
        _, lo, hi = bounds[name]
        return min(max(value, lo), hi)

    t0 = time.perf_counter()
    history: list[float] = []
    with ProcessPoolExecutor(max_workers=args.jobs,
                             initializer=_init_worker,
                             initargs=(args.engine, args.config, args.nodes)) as pool:
        for it in range(args.iters):
            # decaying step and perturbation, the usual SPSA schedule
            decay = 1.0 / (1.0 + it) ** 0.101
            astep = args.alpha * (1.0 / (1.0 + it) ** 0.602)
            delta = {p: rng.choice((-1.0, 1.0)) for p in params}
            plus = {p: int(round(clamp(p, theta[p] + ck[p] * decay * delta[p])))
                    for p in params}
            minus = {p: int(round(clamp(p, theta[p] - ck[p] * decay * delta[p])))
                     for p in params}

            jobs = []
            for g in range(args.games):
                fen = fens[(it * args.games + g) % len(fens)]
                jobs.append((plus, minus, fen, g % 2 == 0))
            scores = list(pool.map(_play, jobs))
            gain = sum(scores) / len(scores) - 0.5     # >0 means plus was better

            for p in params:
                # move towards whichever side scored, scaled by the step
                move = astep * gain * 2.0 * ck[p] * decay * delta[p]
                theta[p] = clamp(p, theta[p] + move)

            history.append(gain)
            if (it + 1) % 10 == 0:
                recent = sum(history[-50:]) / len(history[-50:])
                print(f"  [{it + 1:4d}/{args.iters}] mean gain {recent:+.4f}  "
                      f"{(it + 1) * args.games} games  "
                      f"{(time.perf_counter() - t0) / 60:.1f} min", flush=True)

    out = args.out or os.path.join(cfg.results_dir, "tuned_search.txt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    final = {p: int(round(theta[p])) for p in params}
    with open(out, "w", encoding="utf-8") as fh:
        for p in params:
            fh.write(f"{p} {final[p]}\n")

    print()
    moved = [(p, bounds[p][0], final[p]) for p in params if final[p] != bounds[p][0]]
    print(f"{len(moved)} of {len(params)} parameters moved:")
    for p, a, b in moved:
        print(f"  {p:22s} {a:7d} -> {b:7d}  ({100.0 * (b - a) / max(1, abs(a)):+.0f}%)")
    print(f"\nwrote {out}")
    print("as UCI options:")
    print("  " + ",".join(f"{p}={final[p]}" for p, _, _ in moved))
    print("\nNow confirm it:")
    print(f"  python testsuite/tools/duel.py "
          f"\"ce:{','.join(f'{p}={final[p]}' for p, _, _ in moved)}\" ce-prev -n 1200")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

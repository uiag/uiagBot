"""
Fit and ablate the evaluation against a packed position set, in C.

The objective is the correlation between the static evaluation and the deep
reference score.  Correlation cannot be moved by any change of scale, which is
what makes it safe here: the failure this project already measured and wrote
down (tune_eval.py, -80 Elo) was a fit that improved its loss by making the
evaluation *flatter*, trading away the discrimination that playing strength is
made of for better calibration.  A flatter evaluation has exactly the same
correlation, so that trade buys nothing and the optimiser has to improve the
shape instead.

What correlation still cannot tell you is whether a shape the reference search
likes is a shape this search can use.  So:

    python testsuite/tools/eval_fit.py --ablate         # what is each term worth
    python testsuite/tools/eval_fit.py --fit --groups groupweights,newterms
    python testsuite/tools/eval_fit.py --check          # held-out shard only

and then games, always.  Training uses one shard of the dump and validation a
different one, so the held-out number is over positions from a different part
of the file rather than a reshuffle of the same part.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TS_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(TS_DIR)
sys.path.insert(0, ROOT)

import engine_core                            # noqa: E402

GROUPS: dict[str, list[str]] = {
    "weights": ["PosWeightMg", "PosWeightEg", "TempoBonus"],
    "groupweights": ["PawnWeightMg", "PawnWeightEg", "PieceWeightMg",
                     "PieceWeightEg", "MobWeightMg", "MobWeightEg",
                     "PassedWeightMg", "PassedWeightEg", "ThreatWeightMg",
                     "ThreatWeightEg", "SpaceWeight", "ShelterWeight",
                     "KingDangerWeight"],
    "material": ["MatPawnMg", "MatPawnEg", "MatKnightMg", "MatKnightEg",
                 "MatBishopMg", "MatBishopEg", "MatRookMg", "MatRookEg",
                 "MatQueenMg", "MatQueenEg"],
    "imbalance": ["BishopPairMg", "BishopPairEg", "BishopPairPawns",
                  "ImbKnightPawns", "ImbRookPawns", "ImbQueenKnight",
                  "ImbRookPair", "ImbMinorVsRook"],
    "initiative": ["InitiativeDiv", "InitiativeCap", "InitPawnWeight",
                   "InitPassedWeight", "InitOutflank", "InitBothFlanks",
                   "InitInfiltration", "InitConst"],
    "passers": ["PasserBlockWeight", "PasserBlockMgWeight",
                "CandidatePasserMg", "CandidatePasserEg"],
    "pieces": ["TrappedBishop", "ConnectedRooks", "KingOpenFile",
               "SpaceThreshold"],
    # everything added from Stockfish 11/12 that this evaluation lacked
    "newterms": ["KnightOnQueenMg", "KnightOnQueenEg", "SliderOnQueenMg",
                 "SliderOnQueenEg", "KingProtectorMg", "KingProtectorEg",
                 "RookOnKingRing", "BishopOnKingRing", "QueenInfiltration",
                 "BlockedPawn5Mg", "BlockedPawn5Eg", "BlockedPawn6Mg",
                 "BlockedPawn6Eg", "DoubledIsolatedMg", "DoubledIsolatedEg",
                 "WeakLeverEg", "PawnlessFlankMg", "PawnlessFlankEg",
                 "KdKnightDefender", "KdUnsafeCheck", "KdMobWeight"],
}
DEFAULT_GROUPS = ["weights", "groupweights", "newterms", "material",
                  "imbalance", "initiative", "passers", "pieces"]

# Terms that switch a whole block on or off, grouped so that ablation reports
# a block rather than a single coefficient of one.
ABLATE_BLOCKS: list[tuple[str, list[str]]] = [
    ("threats on the queen", ["KnightOnQueenMg", "KnightOnQueenEg",
                              "SliderOnQueenMg", "SliderOnQueenEg"]),
    ("king protector", ["KingProtectorMg", "KingProtectorEg"]),
    ("sliders on the king ring", ["RookOnKingRing", "BishopOnKingRing"]),
    ("queen infiltration", ["QueenInfiltration"]),
    ("blocked pawns", ["BlockedPawn5Mg", "BlockedPawn5Eg",
                       "BlockedPawn6Mg", "BlockedPawn6Eg"]),
    ("doubled isolated", ["DoubledIsolatedMg", "DoubledIsolatedEg"]),
    ("weak lever", ["WeakLeverEg"]),
    ("pawnless flank", ["PawnlessFlankMg", "PawnlessFlankEg"]),
    ("king danger: knight defender", ["KdKnightDefender"]),
    ("king danger: unsafe checks", ["KdUnsafeCheck"]),
    ("king danger: mobility", ["KdMobWeight"]),
    ("--- previously existing, for comparison ---", []),
    ("trapped bishop", ["TrappedBishop"]),
    ("connected rooks", ["ConnectedRooks"]),
    ("king on an open file", ["KingOpenFile"]),
    ("candidate passers", ["CandidatePasserMg", "CandidatePasserEg"]),
    ("bishop pair", ["BishopPairMg", "BishopPairEg", "BishopPairPawns"]),
    ("king shelter", ["ShelterWeight"]),
    ("king danger", ["KingDangerWeight"]),
    ("space", ["SpaceWeight"]),
]


class Fitter:
    def __init__(self, eval_mode: str) -> None:
        self.eng = engine_core.Engine(hash_mb=8, threads=1, eval_mode=eval_mode)
        self.lib = self.eng._lib
        self.ffi = self.eng._ffi
        self._c = self.ffi.new("double *")
        self._r = self.ffi.new("double *")
        self._f = self.ffi.new("double *")
        self.passes = 0

    def load(self, path: str, limit: int, cap: int, seed: int) -> int:
        n = self.lib.ce_evalset_load(path.encode(), limit, cap, 0, seed)
        if not n:
            raise SystemExit(f"could not load positions from {path}")
        return n

    def stats(self) -> tuple[float, float, float]:
        if not self.lib.ce_evalset_stats(self._c, self._r, self._f):
            raise SystemExit("no positions loaded")
        self.passes += 1
        return self._c[0], self._r[0], self._f[0]

    def corr(self) -> float:
        return self.stats()[0]

    def get(self, name: str) -> int:
        out = self.ffi.new("int *")
        if not self.lib.ce_get_param(name.encode(), out):
            raise SystemExit(f"the engine has no parameter {name!r}")
        return out[0]

    def set(self, name: str, value: int) -> bool:
        return bool(self.lib.ce_set_param(name.encode(), int(value)))

    def limits(self, name: str) -> tuple[int, int]:
        """Probe the engine's own clamp for this parameter."""
        old = self.get(name)
        self.set(name, -1 << 30)
        lo = self.get(name)
        self.set(name, 1 << 30)
        hi = self.get(name)
        self.set(name, old)
        return lo, hi


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train", default="nnue/data/shard0.bin")
    ap.add_argument("--valid", default="nnue/data/shard6.bin")
    ap.add_argument("-n", "--limit", type=int, default=120000)
    ap.add_argument("--cap", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--ablate", action="store_true")
    ap.add_argument("--fit", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--sweeps", type=int, default=6)
    ap.add_argument("--groups", default=",".join(DEFAULT_GROUPS))
    ap.add_argument("--only", default="")
    ap.add_argument("--options", default="", help="applied before anything else")
    ap.add_argument("-o", "--out", default="testsuite/results/fitted_eval.txt")
    args = ap.parse_args()

    f = Fitter("hce")
    for kv in args.options.split(","):
        if "=" in kv:
            name, value = kv.split("=", 1)
            if not f.set(name.strip(), int(value)):
                raise SystemExit(f"unknown parameter {name!r}")

    if args.check:
        n = f.load(args.valid, args.limit, args.cap, args.seed)
        corr, rms, fit = f.stats()
        print(f"held-out {n} positions: corr {corr:.4f}  rms {rms:.1f}  "
              f"fit x{fit:.3f}  (EvalScale for calibration: "
              f"{int(round(f.get('EvalScale') * fit / 0.4939))})")
        return 0

    n = f.load(args.train, args.limit, args.cap, args.seed)
    base_corr, base_rms, base_fit = f.stats()
    print(f"{n} training positions from {os.path.basename(args.train)}")
    print(f"baseline corr {base_corr:.4f}  rms {base_rms:.1f}  fit x{base_fit:.3f}")
    t0 = time.perf_counter()
    f.stats()
    print(f"({(time.perf_counter() - t0) * 1000:.0f} ms per pass)\n")

    if args.ablate:
        print(f"{'block':38s} {'corr w/o':>9s} {'d corr':>9s} {'verdict'}")
        print("-" * 72)
        for label, names in ABLATE_BLOCKS:
            if not names:
                print(f"\n{label}")
                continue
            old = {m: f.get(m) for m in names}
            for m in names:
                f.set(m, 0)
            corr = f.corr()
            for m, v in old.items():
                f.set(m, v)
            d = base_corr - corr
            verdict = ("helps" if d > 0.0002 else
                       "hurts" if d < -0.0002 else "no effect")
            print(f"{label:38s} {corr:9.4f} {d:+9.4f}  {verdict}")
        return 0

    if not args.fit:
        ap.error("one of --ablate, --fit or --check is required")

    if args.only:
        params = [p.strip() for p in args.only.split(",") if p.strip()]
    else:
        params = []
        for g in args.groups.split(","):
            g = g.strip()
            if not g:
                continue
            if g not in GROUPS:
                raise SystemExit(f"unknown group {g!r}; have {', '.join(GROUPS)}")
            params += GROUPS[g]
    params = list(dict.fromkeys(params))

    start = {p: f.get(p) for p in params}
    bounds = {p: f.limits(p) for p in params}
    best = dict(start)
    cur = base_corr
    print(f"fitting {len(params)} parameters")

    t0 = time.perf_counter()
    for sweep in range(args.sweeps):
        frac = 0.5 ** sweep
        moved = 0
        for name in params:
            lo, hi = bounds[name]
            step = max(1, int(round(max(1, abs(best[name])) * 0.3 * frac)))
            for direction in (1, -1):
                trial = best[name] + direction * step
                if trial < lo or trial > hi:
                    continue
                f.set(name, trial)
                val = f.corr()
                if val > cur + 1e-9:
                    cur = val
                    best[name] = trial
                    moved += 1
                    break
                f.set(name, best[name])
        print(f"  sweep {sweep + 1}/{args.sweeps}  step x{frac:.3f}  "
              f"corr {cur:.5f}  ({moved} moves, "
              f"{time.perf_counter() - t0:.0f}s, {f.passes} passes)", flush=True)
        if not moved:
            break

    for name in params:
        f.set(name, best[name])
    train_corr, train_rms, train_fit = f.stats()

    # Held out: a different shard of the dump, not a reshuffle of this one.
    f.load(args.valid, args.limit, args.cap, args.seed + 1)
    v_corr, v_rms, v_fit = f.stats()
    for name in params:
        f.set(name, start[name])
    b_corr, _, b_fit = f.stats()

    print()
    print(f"train corr {base_corr:.4f} -> {train_corr:.4f} "
          f"({train_corr - base_corr:+.4f})")
    print(f"valid corr {b_corr:.4f} -> {v_corr:.4f} ({v_corr - b_corr:+.4f})"
          f"   rms {v_rms:.1f}")
    if v_corr <= b_corr:
        print("  the held-out correlation did not improve: this fit is "
              "overfitting, do not keep it")

    # A shape change that also changes the magnitude is two experiments at
    # once, and every search margin in tune.h was tuned against the old
    # magnitude.  Report the EvalScale that undoes the second one.
    scale = f.get("EvalScale")
    want = int(round(scale * v_fit / b_fit))
    print(f"\nfit x{b_fit:.3f} -> x{v_fit:.3f}; EvalScale {scale} -> {want} "
          f"keeps the reported score calibrated as it was")

    changed = [(k, start[k], best[k]) for k in params if start[k] != best[k]]
    print(f"\n{len(changed)} parameters moved:")
    for k, a, b in changed:
        print(f"  {k:22s} {a:7d} -> {b:7d}  ({100.0 * (b - a) / max(1, abs(a)):+.0f}%)")

    out = args.out
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for k in params:
            fh.write(f"{k} {best[k]}\n")
        fh.write(f"EvalScale {want}\n")
    print(f"\nwrote {out}")
    print("as UCI options:")
    print("  " + ",".join(f"{k}={v}" for k, _, v in changed)
          + f",EvalScale={want}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

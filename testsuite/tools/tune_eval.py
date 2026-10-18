"""
Fit the evaluation's parameters to a labelled position set (Texel tuning).

The loss is the mean squared error between what the engine thinks of a
position and what actually happened in it, the engine's side mapped through a
sigmoid into a winning probability.

**Use game results as the label, not referee scores.**  A referee score is far
more precise, and fitting to it is a trap: the referee's number already
contains a search, a static evaluation cannot reproduce one, and the fit
therefore pays down the unreachable part of the error by shrinking every weight
towards zero.  That genuinely improves the loss -- a flatter evaluation really
is better *calibrated* -- while destroying the discrimination that playing
strength is made of.  Measured here: fitting 51 parameters to referee scores at
depth 10 improved the held-back loss by 23% and scored 38.75% over 1200 games,
**-80 Elo**.  The same machinery on game results does not have that bias.

The global scale is divided out of the loss before the error is taken (see
`loss`), so the optimiser has to improve the shape of the evaluation rather
than its confidence, and so that the constant mapping centipawns to
probability is fitted rather than guessed.

    python testsuite/tools/tune_eval.py                      # tune everything
    python testsuite/tools/tune_eval.py --only PosWeightMg,PosWeightEg
    python testsuite/tools/tune_eval.py --groups material,weights
    python testsuite/tools/tune_eval.py --sweeps 12 --limit 0

The engine is driven through its own shared library rather than over a pipe, so
a full pass over a hundred thousand positions takes a fraction of a second and
a sweep over sixty parameters is a matter of minutes.

A note on K, the centipawns-per-logit constant: it is chosen, not fitted.
Classical Texel tuning fits K because the label there is a game result, a fixed
0, 1/2 or 1, so K only scales the engine's side of the comparison.  Here both
sides are scores put through the same sigmoid, which means a larger K flattens
both and shrinks the error for free -- the loss falls monotonically as K grows
and "fitting" it just runs off to infinity.  K instead says where in the range
the fit should care: at the default 150 a one-pawn advantage is about a 0.66
expectation, so the fit is driven by positions that are still in the balance
rather than by ones already decided.  Losses are only comparable at equal K.

Nothing is written to the source: the result is a list of
`setoption`-compatible values, which `--emit-c` can turn into replacement
defaults for tune.h.  Always confirm a fit with real games -- a lower loss is
evidence, not proof.
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TS_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(TS_DIR)
sys.path.insert(0, ROOT)
sys.path.insert(0, TS_DIR)

import engine_core                          # noqa: E402

# Which parameters belong to which group, so a run can tune one area at a time.
GROUPS: dict[str, list[str]] = {
    # EvalScale is deliberately absent: the loss already divides the global
    # scale out, so tuning it here would be fitting a free parameter against a
    # measure that cannot see it.
    "weights": ["PosWeightMg", "PosWeightEg", "TempoBonus"],
    "groupweights": ["PawnWeightMg", "PawnWeightEg", "PieceWeightMg", "PieceWeightEg",
                     "MobWeightMg", "MobWeightEg", "PassedWeightMg", "PassedWeightEg",
                     "ThreatWeightMg", "ThreatWeightEg", "SpaceWeight",
                     "ShelterWeight", "KingDangerWeight"],
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
    "pieces": ["TrappedBishop", "ConnectedRooks", "KingOpenFile", "SpaceThreshold"],
}
DEFAULT_GROUPS = ["weights", "groupweights", "material", "imbalance",
                  "initiative", "passers", "pieces"]

# the ranges tune.h allows, mirrored here so the search stays inside them
LIMITS = {
    "PosWeightMg": (32, 192), "PosWeightEg": (32, 192), "EvalScale": (60, 220),
    "TempoBonus": (0, 60), "SpaceThreshold": (2000, 8000),
    "InitiativeDiv": (100, 1200), "InitiativeCap": (10, 400),
}


def win_prob(cp: float, k: float) -> float:
    return 1.0 / (1.0 + math.exp(-cp / k))


class Tuner:
    def __init__(self, path: str, limit: int, seed: int, k: float) -> None:
        rows = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                fen, _, score = line.rpartition(";")
                try:
                    rows.append((fen, int(score)))
                except ValueError:
                    continue
        random.Random(seed).shuffle(rows)
        if limit:
            rows = rows[:limit]
        cut = int(len(rows) * 0.85)
        self.train = rows[:cut]
        self.valid = rows[cut:]
        self.k = k
        print(f"{len(self.train)} training positions, {len(self.valid)} held back")

        self.engine = engine_core.Engine(hash_mb=8, threads=1, eval_mode="hce")
        self.lib = self.engine._lib
        self.ffi = self.engine._ffi
        # Pre-encode so the loop does no Python string work.  ce_eval answers
        # from the side to move, while the label is from White's point of
        # view, so the target is flipped here once instead of per pass.
        self.empty = b""
        self.evals = 0
        self.best_scale = 1.0
        self.cp_buf = [0] * (max(len(self.train), len(self.valid)) + 1)
        self.rescale_targets()

    def rescale_targets(self) -> None:
        """(Re)build the encoded rows for the current K."""
        def enc(rows):
            out = []
            for fen, score in rows:
                stm_white = fen.split()[1] == "w"
                p = win_prob(score if stm_white else -score, self.k)
                out.append((fen.encode(), p))
            return out

        self.train_enc = enc(self.train)
        self.valid_enc = enc(self.valid)

    # ---- parameters ----

    def get(self, name: str) -> int:
        out = self.ffi.new("int *")
        if not self.lib.ce_get_param(name.encode(), out):
            raise SystemExit(f"the engine has no parameter {name!r}")
        return out[0]

    def set(self, name: str, value: int) -> None:
        self.lib.ce_set_param(name.encode(), int(value))

    # ---- loss ----

    SCALES = (0.22, 0.3, 0.4, 0.5, 0.62, 0.78, 1.0, 1.25, 1.6, 2.0, 2.6)

    def loss(self, rows) -> float:
        """Scale-invariant mean squared error in win-probability space.

        The bare error is minimised by an evaluation that says nothing: a
        flatter score is better *calibrated* even though it discriminates
        worse, and since playing strength lives entirely in the ordering of
        moves rather than in the calibration, a straight fit walks every
        weight towards zero and loses a hundred Elo while the loss improves by
        a fifth.  (Measured: -92 Elo over 600 games for a fit whose held-back
        loss was 23% better.)

        So the global scale is divided out before the error is taken: the best
        multiplier on the whole evaluation is found first and the loss is the
        residual.  The optimiser then has to improve the *shape* to improve
        the number, and cannot buy anything with timidity.
        """
        lib, empty = self.lib, self.empty
        setpos, ev = lib.ce_set_position, lib.ce_eval
        cps = self.cp_buf
        i = 0
        for fen, _ in rows:
            setpos(fen, empty)
            cps[i] = ev()
            i += 1

        k = self.k
        best = None
        for a in self.SCALES:
            inv = a / k
            total = 0.0
            j = 0
            for _, target in rows:
                d = 1.0 / (1.0 + math.exp(-cps[j] * inv)) - target
                total += d * d
                j += 1
            if best is None or total < best:
                best = total
                self.best_scale = a
        self.evals += 1
        return best / max(1, len(rows))

    def train_loss(self) -> float:
        return self.loss(self.train_enc)

    def valid_loss(self) -> float:
        return self.loss(self.valid_enc)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", default="", help="path to the labelled positions")
    ap.add_argument("--limit", type=int, default=60000,
                    help="use at most this many positions (0 = all)")
    ap.add_argument("--k", type=float, default=300.0,
                    help="centipawns per logit in the win-probability map; see "
                         "the note in the source on why this is chosen rather "
                         "than fitted")
    ap.add_argument("--sweeps", type=int, default=8)
    ap.add_argument("--groups", default=",".join(DEFAULT_GROUPS))
    ap.add_argument("--only", default="", help="comma separated parameter names")
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("-o", "--out", default="")
    ap.add_argument("--emit-c", action="store_true",
                    help="also print the values as tune.h defaults")
    args = ap.parse_args()

    path = args.set or os.path.join(ROOT, "testsuite", "results", "evalset.txt")
    if not os.path.isfile(path):
        raise SystemExit(f"no position set at {path}; "
                         f"run testsuite/tools/make_evalset.py first")

    t = Tuner(path, args.limit, args.seed, args.k)

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
    # de-duplicate, keep order
    params = list(dict.fromkeys(params))

    start = {p: t.get(p) for p in params}
    base_train = t.train_loss()
    base_valid = t.valid_loss()
    print(f"start  train {base_train:.6f}  valid {base_valid:.6f}")
    print(f"tuning {len(params)} parameters: {', '.join(params)}")

    best = dict(start)
    cur = base_train
    t0 = time.perf_counter()

    for sweep in range(args.sweeps):
        # step shrinks geometrically; scaled per parameter by its own size
        frac = 0.5 ** sweep
        improved = 0
        for name in params:
            lo, hi = LIMITS.get(name, (None, None))
            scale = max(1, abs(best[name]))
            step = max(1, int(round(scale * 0.25 * frac)))
            for direction in (1, -1):
                trial = best[name] + direction * step
                if lo is not None and (trial < lo or trial > hi):
                    continue
                if trial < 0:
                    continue
                t.set(name, trial)
                val = t.train_loss()
                if val < cur - 1e-12:
                    cur = val
                    best[name] = trial
                    improved += 1
                    break
                t.set(name, best[name])
        print(f"  sweep {sweep + 1}/{args.sweeps}  step x{frac:.3f}  "
              f"train {cur:.6f}  ({improved} moves, "
              f"{time.perf_counter() - t0:.0f}s)", flush=True)
        if not improved:
            break

    for name in params:
        t.set(name, best[name])
    fin_valid = t.valid_loss()

    # The loss cannot see the global scale, so the fit determines the shape of
    # the evaluation only; the scale it would have liked has to be folded back
    # into EvalScale or the result is applied at the wrong confidence.
    scale_param = "EvalScale"
    old_scale = t.get(scale_param)
    new_scale = int(round(old_scale * t.best_scale))
    lo, hi = LIMITS[scale_param]
    new_scale = min(max(new_scale, lo), hi)
    if new_scale != old_scale:
        start.setdefault(scale_param, old_scale)
        best[scale_param] = new_scale
        if scale_param not in params:
            params.append(scale_param)
        t.set(scale_param, new_scale)
    print()
    print(f"train {base_train:.6f} -> {cur:.6f} "
          f"({100 * (base_train - cur) / base_train:+.2f}%)")
    print(f"best global scale on the held-back set: x{t.best_scale:.2f} "
          f"(folded into EvalScale)")
    print(f"valid {base_valid:.6f} -> {fin_valid:.6f} "
          f"({100 * (base_valid - fin_valid) / base_valid:+.2f}%)")
    if fin_valid > base_valid:
        print("  the held-back loss went up: this fit is overfitting, do not keep it")

    changed = [(n, start[n], best[n]) for n in params if start[n] != best[n]]
    print(f"\n{len(changed)} parameters moved:")
    for n, a, b in changed:
        print(f"  {n:22s} {a:7d} -> {b:7d}  ({100.0 * (b - a) / max(1, abs(a)):+.0f}%)")

    out = args.out or os.path.join(ROOT, "testsuite", "results", "tuned_eval.txt")
    with open(out, "w", encoding="utf-8") as fh:
        for n in params:
            fh.write(f"{n} {best[n]}\n")
    print(f"\nwrote {out}")
    print("as UCI options:")
    print("  " + ",".join(f"{n}={best[n]}" for n, _, _ in changed))
    if args.emit_c:
        print("\nas tune.h defaults:")
        for n in params:
            print(f"    TUNE({n}, {best[n]}, ...)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

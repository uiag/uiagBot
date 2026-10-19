"""
Score many parameter sets against a labelled position file, fast.

Correlation between the engine's static evaluation and a deep reference score
is scale-invariant, so it measures the *shape* of the evaluation and nothing
else.  That is the one thing a search score can safely be used to judge: the
trap this project already paid for (tune_eval.py, -80 Elo) is *fitting* to
reference scores, because the fit pays down the part of the error a static
evaluation can never reach by flattening every weight.  Flattening does not
move the correlation at all, so ranking shapes by it cannot be bought with
timidity.

`fit` is the least-squares multiplier from engine units to real centipawns.
It is reported so that a shape change can be paired with the `EvalScale` that
keeps the output calibrated -- the search margins in tune.h were tuned against
the current units and a shape change that quietly doubles the evaluation's
magnitude is really two experiments at once.

    python testsuite/tools/eval_scan.py --data nnue/data/shard0.bin -n 40000
    python testsuite/tools/eval_scan.py --sets "KingDangerWeight=150" \
        --sets "KingDangerWeight=150,ShelterWeight=90"

Always confirm a winner with games.  A better correlation is evidence.
"""
from __future__ import annotations

import argparse
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TS_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(TS_DIR)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import engine_core                            # noqa: E402
from eval_bias import REC, load_epd, rec_to_fen   # noqa: E402


def load(path: str, limit: int, cap: int, seed: int, keep_mates: bool):
    a = np.fromfile(path, dtype=REC)
    keep = np.abs(a["score"].astype(np.int32)) <= cap
    if not keep_mates:
        keep &= (a["flags"] & 1) == 0
    a = a[keep]
    rng = np.random.default_rng(seed)
    if limit and len(a) > limit:
        a = a[rng.choice(len(a), limit, replace=False)]
    return [(rec_to_fen(r), int(r["score"])) for r in a]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="")
    ap.add_argument("--set", default="")
    ap.add_argument("-n", "--limit", type=int, default=40000)
    ap.add_argument("--cap", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--sets", action="append", default=[],
                    help="one candidate as name=value,name=value (repeatable)")
    ap.add_argument("--from-file", default="",
                    help="a file of candidates, one per line, '# label' allowed")
    ap.add_argument("--eval-mode", default="hce")
    args = ap.parse_args()

    if args.data:
        rows = load(args.data, args.limit, args.cap, args.seed, keep_mates=False)
    elif args.set:
        rows = load_epd(args.set, args.limit, args.seed)
    else:
        raise SystemExit("one of --data or --set is required")

    fens = [fen.encode() for fen, _ in rows]
    white = np.array([fen.split()[1] == "w" for fen, _ in rows])
    target = np.array([s for _, s in rows], dtype=np.float64)
    sign = np.where(white, 1.0, -1.0)
    print(f"{len(rows)} positions, cap {args.cap} cp")

    eng = engine_core.Engine(hash_mb=8, threads=1, eval_mode=args.eval_mode)
    lib = eng._lib
    setpos, ev = lib.ce_set_position, lib.ce_eval
    empty = b""
    buf = np.empty(len(rows), dtype=np.float64)

    def measure() -> tuple[float, float, float]:
        for i, fen in enumerate(fens):
            setpos(fen, empty)
            buf[i] = ev()
        ours = buf * sign
        fit = float(np.dot(ours, target) / max(np.dot(ours, ours), 1e-9))
        resid = target - fit * ours
        return (float(np.corrcoef(ours, target)[0, 1]),
                math.sqrt(float(np.mean(resid ** 2))), fit)

    def get(name: str) -> int:
        out = eng._ffi.new("int *")
        if not lib.ce_get_param(name.encode(), out):
            raise SystemExit(f"the engine has no parameter {name!r}")
        return out[0]

    candidates: list[tuple[str, str]] = [("baseline", "")]
    for s in args.sets:
        candidates.append((s, s))
    if args.from_file:
        with open(args.from_file, encoding="utf-8") as fh:
            label = ""
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    label = line.lstrip("# ").strip()
                    continue
                candidates.append((label or line, line))
                label = ""

    base_corr = base_rms = None
    print(f"\n{'candidate':54s} {'corr':>7s} {'d corr':>8s} {'rms':>7s} "
          f"{'fit':>6s} {'EvalScale*':>10s}")
    print("-" * 98)
    eval_scale = get("EvalScale")
    for label, spec in candidates:
        touched: dict[str, int] = {}
        for kv in spec.split(","):
            if "=" not in kv:
                continue
            name, value = (x.strip() for x in kv.split("=", 1))
            touched[name] = get(name)
            if not lib.ce_set_param(name.encode(), int(value)):
                raise SystemExit(f"the engine rejected {name}={value}")
        corr, rms, fit = measure()
        for name, old in touched.items():
            lib.ce_set_param(name.encode(), old)
        if base_corr is None:
            base_corr, base_rms, base_fit = corr, rms, fit
        # the EvalScale that would keep the output calibrated as it is now
        want = int(round(eval_scale * fit / base_fit)) if fit else eval_scale
        flag = "  <-- better" if corr > (base_corr or 0) + 1e-5 else ""
        print(f"{label[:54]:54s} {corr:7.4f} {corr - base_corr:+8.4f} "
              f"{rms:7.1f} {fit:6.3f} {want:10d}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

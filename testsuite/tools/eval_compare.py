"""
Score the hand-crafted evaluation and the network against the same positions.

The two are otherwise measured by different numbers at different times -- the
network by its training loss, the evaluation by a correlation -- and those are
not comparable. This runs both through the engine's own `ce_eval` over one
loaded position set, so the only thing that differs is `EvalMode`.

`corr` is scale-invariant and is the one to read. `fit` is the multiplier
from engine units to reference centipawns; the two evaluations have to agree
on it or the search's margins mean different things depending on EvalMode,
which is what `NnueScale` exists to prevent.

    python testsuite/tools/eval_compare.py --data nnue/data/shard6.bin
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

import engine_core                            # noqa: E402

MODES = {"hce": 0, "nnue": 1, "blend": 2}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="nnue/data/shard6.bin",
                    help="held out of the network's training by default")
    ap.add_argument("-n", "--limit", type=int, default=200000)
    ap.add_argument("--cap", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=23)
    ap.add_argument("--modes", default="hce,nnue,blend")
    ap.add_argument("--options", default="")
    args = ap.parse_args()

    eng = engine_core.Engine(hash_mb=8, threads=1, eval_mode="hce")
    lib, ffi = eng._lib, eng._ffi
    if not eng.nnue_available:
        print("no network loaded (engine/net2.bin missing or unreadable); "
              "only the hand-crafted evaluation can be measured")
    for kv in args.options.split(","):
        if "=" in kv:
            name, value = kv.split("=", 1)
            if not lib.ce_set_param(name.strip().encode(), int(value)):
                raise SystemExit(f"unknown parameter {name!r}")

    n = lib.ce_evalset_load(args.data.encode(), args.limit, args.cap, 0,
                            args.seed)
    if not n:
        raise SystemExit(f"could not load positions from {args.data}")
    print(f"{n:,} positions from {os.path.basename(args.data)}, "
          f"capped at {args.cap} cp\n")

    c = ffi.new("double *")
    r = ffi.new("double *")
    f = ffi.new("double *")
    print(f"{'EvalMode':10s} {'corr':>8s} {'rms (cp)':>10s} {'fit':>7s}  "
          f"{'units per cp':>13s}")
    print("-" * 56)
    for mode in args.modes.split(","):
        mode = mode.strip()
        if mode not in MODES:
            continue
        lib.ce_set_eval_mode(MODES[mode])
        got = lib.ce_get_eval_mode()
        if got != MODES[mode]:
            print(f"{mode:10s} unavailable")
            continue
        lib.ce_evalset_stats(c, r, f)
        print(f"{mode:10s} {c[0]:8.4f} {r[0]:10.1f} {f[0]:7.3f} "
              f"{1.0 / f[0] if f[0] else 0:13.2f}")
    lib.ce_set_eval_mode(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

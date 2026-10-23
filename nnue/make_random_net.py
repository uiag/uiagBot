"""
Write a network of random weights, to test the engine's side of it.

The incremental accumulator is the part of the network code that can be wrong
without looking wrong: a bad delta shifts the evaluation by a few centipawns
in positions reached one particular way, which is invisible in a game and
invisible in a benchmark.  What catches it is comparing every incremental
update against a full refresh, which is what a CE_NNUE_VERIFY build does --
and that needs a network, but not a good one.  Random weights exercise every
path and make any disagreement loud.

    .venv2/Scripts/python nnue/make_random_net.py engine/net_random.bin
"""
from __future__ import annotations

import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from arch import (KING_BUCKETS, L1, NUM_PLANES, OUTPUT_BUCKETS,  # noqa: E402
                  QA, QB, SCALE, WEIGHT_CLIP)


def main() -> int:
    dst = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(HERE), "engine", "net_random.bin")
    rng = np.random.default_rng(12345)

    # Random weights have to match the *current* architecture, king buckets
    # and all.  A CEN2 random net would load and verify perfectly while
    # leaving the whole bucket-and-fold path untested, which is exactly the
    # path most likely to be wrong.
    rows = KING_BUCKETS * NUM_PLANES

    # Spread over most of the legal range so that the int16 accumulator is
    # genuinely exercised, but not so wide that a full board overflows it.
    w = WEIGHT_CLIP * 0.5
    ft_w = np.rint(rng.uniform(-w, w, (rows, L1)) * QA).astype("<i2")
    ft_b = np.rint(rng.uniform(-w, w, L1) * QA).astype("<i2")
    out_w = np.rint(rng.uniform(-w, w, (OUTPUT_BUCKETS, 2 * L1)) * QB).astype("<i2")
    out_b = np.rint(rng.uniform(-1, 1, OUTPUT_BUCKETS) * QA * QB).astype("<i4")

    # Only one bucket is active at a time, so the overflow bound is per bucket.
    worst = 0
    for b in range(KING_BUCKETS):
        block = ft_w[b * NUM_PLANES:(b + 1) * NUM_PLANES]
        v = np.abs(ft_b).astype(np.int64) + np.sort(
            np.abs(block).astype(np.int64), axis=0)[-32:].sum(axis=0)
        worst = max(worst, int(v.max()))
    print(f"worst-case |accumulator| {worst} of 32767")

    with open(dst, "wb") as f:
        f.write(b"CEN3")
        f.write(struct.pack("<IIIIIiif", 3, NUM_PLANES, KING_BUCKETS, L1,
                            OUTPUT_BUCKETS, QA, QB, SCALE))
        f.write(ft_w.tobytes(order="C"))
        f.write(ft_b.tobytes())
        f.write(out_w.tobytes(order="C"))
        f.write(out_b.tobytes())
    print(f"wrote {dst} ({os.path.getsize(dst) / 1024 / 1024:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

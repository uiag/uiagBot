"""
Quantise a trained network into engine/net2.bin.

    .venv2/Scripts/python nnue/export.py [net.pt] [engine/net2.bin]

The feature layer goes to int16 at QA and the output layer at QB, which is
what lets the engine keep the accumulator in integers and update it by
addition.  Both were clipped to 127/QB during training, so nothing here
should saturate -- the script checks that and says so, because a quantisation
that silently clamps a few hundred weights is a net that plays measurably
worse than the one that was trained.

The output bias is stored pre-multiplied by QA * QB so that the engine can add
it to `sum / QA` and divide once at the end.
"""
from __future__ import annotations

import os
import struct
import sys

import numpy as np
import torch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from arch import (KING_BUCKETS, L1, OUTPUT_BUCKETS, NUM_PLANES,  # noqa: E402
                  PAD_INDEX, QA, QB, SCALE, WEIGHT_CLIP)

NUM_FEATURES = KING_BUCKETS * NUM_PLANES


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "net.pt")
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "engine", "net3.bin")
    if not os.path.isfile(src):
        print(f"no network at {src}")
        return 1

    sd = torch.load(src, map_location="cpu")
    ft_w = sd["ft.weight"].double().numpy()         # (769, L1)
    ft_b = sd["ft_bias"].double().numpy()           # (L1,)
    out_w = sd["out_weight"].double().numpy()       # (buckets, 2*L1)
    out_b = sd["out_bias"].double().numpy()         # (buckets,)

    if ft_w.shape != (PAD_INDEX + 1, L1):
        print(f"unexpected feature layer {ft_w.shape}")
        return 1
    if out_w.shape != (OUTPUT_BUCKETS, 2 * L1):
        print(f"unexpected output layer {out_w.shape}")
        return 1

    # The padding row is never active; it is dropped rather than stored.
    ft_w = ft_w[:NUM_FEATURES]

    def report(name: str, a: np.ndarray, scale: int) -> None:
        q = np.rint(a * scale)
        over = int(np.sum(np.abs(q) > 32767))
        print(f"  {name:12s} max |w| {np.abs(a).max():.4f} "
              f"(clip {WEIGHT_CLIP:.4f}), quantisation error "
              f"{np.abs(q / scale - a).max():.5f}"
              + (f", {over} OVERFLOW" if over else ""))
        if over:
            raise SystemExit("weights do not fit an int16; the training clip "
                             "and the export scale disagree")

    print(f"quantising {src}")
    report("ft weight", ft_w, QA)
    report("ft bias", ft_b, QA)
    report("out weight", out_w, QB)

    ft_wq = np.rint(ft_w * QA).astype("<i2")
    ft_bq = np.rint(ft_b * QA).astype("<i2")
    out_wq = np.rint(out_w * QB).astype("<i2")
    # pre-scaled so the engine can fold it into sum / QA
    out_bq = np.rint(out_b * QA * QB).astype("<i4")

    # What the largest accumulator value could be: the bias plus the 32
    # strongest weights on any one neuron.  If that cleared an int16 the
    # accumulator would wrap and the evaluation would be nonsense in exactly
    # the positions with the most material.
    worst = np.abs(ft_bq).astype(np.int64) + np.sort(
        np.abs(ft_wq).astype(np.int64), axis=0)[-32:].sum(axis=0)
    print(f"  worst-case |accumulator| {worst.max()} of 32767 "
          f"({100.0 * worst.max() / 32767:.0f}% of the int16 range)")
    if worst.max() > 32767:
        raise SystemExit("the accumulator can overflow; retrain with a "
                         "tighter weight clip")

    with open(dst, "wb") as f:
        # CEN3 adds the king-bucket count.  The magic changes with it so that
        # an engine built for one format cannot silently read the other: the
        # layouts differ only in how many copies of the 768 planes follow, so
        # a version check alone would load a CEN2 net as a one-bucket CEN3 and
        # evaluate perfectly plausible nonsense.
        f.write(b"CEN3")
        f.write(struct.pack("<IIIIIiif", 3, NUM_PLANES, KING_BUCKETS, L1,
                            OUTPUT_BUCKETS, QA, QB, SCALE))
        f.write(ft_wq.tobytes(order="C"))
        f.write(ft_bq.tobytes())
        f.write(out_wq.tobytes(order="C"))
        f.write(out_bq.tobytes())

    print(f"wrote {dst} ({os.path.getsize(dst) / 1024 / 1024:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

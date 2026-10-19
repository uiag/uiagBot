"""
RETIRED.  This writes the "CENN" version 1 format, which the engine no longer
reads: `nnue.c` now holds a two-perspective network and loads "CEN2" from
engine/net2.bin.  Use nnue/export.py instead.  Kept only so that the old
769-256-1 weights in evaluationModel/ are not orphaned; nothing calls it.

Convert the trained NNUE weights (nnue_weights.npz) into engine/net.bin.

The network is 769 -> 256 -> 1 with ReLU and a sigmoid output; the engine
consumes the pre-sigmoid logit and multiplies it by `scale` to get
centipawns, which is the inverse of the cp -> win-probability mapping the
network was trained on (see evaluationModel/build_dataset.py).

The first layer is stored transposed (feature-major) and quantised by QA so
that the engine can accumulate it in integers; the output layer stays in
float.

    python engine/export_net.py [input.npz] [output.bin]
"""
from __future__ import annotations

import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

QA = 1024
SCALE = 400.0          # centipawns per logit unit, matches cp_to_wdl(scale=400)
INPUT_DIM = 769
HIDDEN = 256


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "nnue_weights.npz")
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "net.bin")

    if not os.path.isfile(src):
        print(f"weights not found: {src}")
        return 1

    z = np.load(src)
    w1 = np.asarray(z["w1"], dtype=np.float64)     # (HIDDEN, INPUT_DIM)
    b1 = np.asarray(z["b1"], dtype=np.float64)     # (HIDDEN,)
    w2 = np.asarray(z["w2"], dtype=np.float64).reshape(-1)
    b2 = float(np.asarray(z["b2"]).reshape(-1)[0])

    if w1.shape != (HIDDEN, INPUT_DIM):
        print(f"unexpected w1 shape {w1.shape}, expected {(HIDDEN, INPUT_DIM)}")
        return 1

    w1t = np.rint(w1.T * QA).astype(np.int32)      # (INPUT_DIM, HIDDEN)
    b1q = np.rint(b1 * QA).astype(np.int32)

    err = np.abs(w1t / QA - w1.T).max()
    print(f"first layer quantisation error: {err:.6f} (weights up to {np.abs(w1).max():.3f})")

    with open(dst, "wb") as f:
        f.write(b"CENN")
        f.write(struct.pack("<IIIif", 1, INPUT_DIM, HIDDEN, QA, SCALE))
        f.write(w1t.astype("<i4").tobytes(order="C"))
        f.write(b1q.astype("<i4").tobytes())
        f.write(w2.astype("<f4").tobytes())
        f.write(struct.pack("<f", b2))

    print(f"wrote {dst} ({os.path.getsize(dst) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

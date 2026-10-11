import torch
import numpy as np
from nnue_model import NNUE

model = NNUE()
model.load_state_dict(torch.load("nnue_lichess.pt", map_location="cpu"))
model.eval()

linear1 = model.net[0]
linear2 = model.net[3]

w1 = linear1.weight.detach().cpu().numpy()
b1 = linear1.bias.detach().cpu().numpy()

w2 = linear2.weight.detach().cpu().numpy()[0]
b2 = linear2.bias.detach().cpu().item()

np.savez(
    "nnue_weights.npz",
    w1=w1.astype(np.float32),
    b1=b1.astype(np.float32),
    w2=w2.astype(np.float32),
    b2=np.float32(b2)
)
"""

model = NNUE()
model.load_state_dict(torch.load("nnue_lichess.pt", map_location="cpu"))
model.eval()

SCALE1 = 128
SCALE2 = 128

linear1 = model.net[0]
linear2 = model.net[3]

w1 = linear1.weight.detach().cpu().numpy()
b1 = linear1.bias.detach().cpu().numpy()

w2 = linear2.weight.detach().cpu().numpy()[0]
b2 = linear2.bias.detach().cpu().item()

w1q = np.round(w1 * SCALE1).astype(np.int16)
b1q = np.round(b1 * SCALE1).astype(np.int32)

w2q = np.round(w2 * SCALE2).astype(np.int16)
b2q = int(np.round(b2 * SCALE1 * SCALE2))

np.savez(
    "nnue_weights.npz",
    w1=w1q,
    b1=b1q,
    w2=w2q,
    b2=b2q
)
"""
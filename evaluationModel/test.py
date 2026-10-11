import random
import numpy as np
import torch

import chess
from nnue_model import NNUE
from features import BASE_FEATURES

# -------- config --------
MODEL_PATH = "nnue_lichess.pt"
DATA_PATH = "nnue_lichess.npz"
SAMPLES = 50
# ------------------------

# Load dataset
data = np.load(DATA_PATH, allow_pickle=True)
length = data[data.files[0]].shape[0]
X = data["X"]
y = data["y"]

# Load model
model = NNUE()
model.load_state_dict(torch.load(MODEL_PATH, map_location="cpu"))
model.eval()

print("Random NNUE predictions:\n")

for i in random.sample(range(length), SAMPLES):
    # Build dense input
    x = torch.zeros(BASE_FEATURES)
    x[X[i]] = 1.0

    with torch.no_grad():
        pred = model(x).item()

    print(f"Sample {i}: "
          f"target = {y[i]:.3f} | "
          f"prediction = {pred:.3f} ")

import torch
from nnue_model import NNUE

model = NNUE()
model.load_state_dict(torch.load("nnue_lichess.pt", map_location="cpu"))
model.eval()

with torch.no_grad():
    x = torch.zeros(769)
    x[768] = 1
    print(model(x).item())

    hidden = model.net[0](x)
    print(hidden[:10])
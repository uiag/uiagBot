import torch
import torch.nn as nn

class NNUE(nn.Module):
    def __init__(self, input_dim=769, hidden_dim=256, dropout=0.1):
        super().__init__()
        layers = []
        layers.append(nn.Linear(input_dim, hidden_dim))
        layers.append(nn.ReLU())
        layers.append(nn.Dropout(dropout))
        layers.append(nn.Linear(hidden_dim, 1))
        layers.append(nn.Sigmoid())
        self.net = nn.Sequential(*layers)

    def forward(self, x):
        return self.net(x)
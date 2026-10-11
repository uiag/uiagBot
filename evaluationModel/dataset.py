import torch
from torch.utils.data import Dataset
from features import BASE_FEATURES

class NNUEData(Dataset):
    def __init__(self, X, y):
        self.X = X
        self.y = y
        print(f'Created dataset of length {len(X)}')

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        x = torch.zeros(BASE_FEATURES)
        x[self.X[idx][:-1]] = 1.0
        x[-1] = self.X[idx][-1]
        return x, torch.tensor(self.y[idx], dtype=torch.float32)

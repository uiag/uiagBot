import torch
import time
import numpy as np
import matplotlib.pyplot as plt
from torch.utils.data import DataLoader, Subset, TensorDataset

from nnue_model import NNUE
from dataset import NNUEData

def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(torch.cuda.is_available())
    print(device)

    data = np.load("nnue_lichess_1.npz", allow_pickle=True)
    start = time.time()
    length = 15000000
    val_cutoff = int(length*0.8)

    full_dataset = NNUEData(data["X"], data["y"])
    dataset = Subset(full_dataset, range(0, val_cutoff))
    dataset_val = Subset(full_dataset, range(val_cutoff, len(full_dataset)))
    loader = DataLoader(dataset, batch_size=8192, shuffle=True, num_workers=4)
    val_loader = DataLoader(dataset_val, batch_size=8192, shuffle=False, num_workers=4)

    #dataset = NNUEData(data["X"][:val_cutoff], data["y"][:val_cutoff])
    #dataset_val = NNUEData(data["X"][val_cutoff:], data["y"][val_cutoff:])
    #loader = DataLoader(dataset, batch_size=16384, shuffle=True, num_workers=4)
    #val_loader = DataLoader(dataset_val, batch_size=16384, shuffle=False, num_workers=4)

    model = NNUE().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.0005, weight_decay=0.01, amsgrad=True)
    loss_fn = torch.nn.BCELoss()

    #Early stopping parameters
    best_val_loss = float('inf')
    patience = 25
    best_model = None
    counter = 0
    counter_data = 0
    next_dataset = 1

    train_losses = []
    val_losses = []

    for epoch in range(1000): #Iterate over epochs
        model.train() #Enable training mode
        l=0
        startTime = time.time()
        optimizer.zero_grad() #Clear gradients
        train_loss = 0
        for x, y in loader: #Iterate over batches
            x, y = x.to(device), y.to(device)
            batchs = x.size(0)
            pred = model(x).squeeze(1)
            loss = loss_fn(pred, y) #get loss
            optimizer.zero_grad() #Clear gradients
            loss.backward() #backpropagation
            optimizer.step() #Update weights
            train_loss += loss.item() * batchs
            if l % 50 == 0: #Report learning statistics every 100 batches
                endTime = time.time()
                #print(f'Batches completed {l}; Time per batch: {(endTime-startTime)/50}', flush=True)
                startTime = time.time()
            l+=1

        train_loss /= len(dataset)
        train_losses.append(train_loss)

        #Validation
        model.eval() #set model to evaluation mode
        val_loss = 0.0
        with torch.no_grad():  # no gradient calculation
            # Iterate over validation batches
            for x, y in val_loader:
                x, y = x.to(device), y.to(device)
                batchs = x.size(0)
                pred = model(x).squeeze(1)
                # Sum validation loss
                val_loss += (loss_fn(pred, y).item() * batchs)

        # Get final validation loss and print info
        val_loss /= len(dataset_val)
        val_losses.append(val_loss)
        print(f"Epoch {epoch + 1}, Val Loss: {val_loss:.4f}, Time: {(time.time() - start):.2f}s")
        start = time.time()

        # As long model improves slightly compared to best model set stopping counter to zero
        # and best_model to current model
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_model = model
            best_model.eval()
            torch.save(best_model.state_dict(), "nnue_lichess.pt")
            counter = 0
        else:  # If model not better than best model increase stopping counter
            counter += 1
            if counter >= patience:  # If patience is reaches apply early stopping and stop training
                print("Early stopping")
                break

        """
        counter_data += 1
        if counter_data >= 1:
            counter_data = 0
            next_dataset += 1
            if next_dataset > 23:
                next_dataset = 1

            print(f"Loading dataset {next_dataset} ...")
            del data
            del dataset
            del full_dataset
            del dataset_val
            del loader
            del val_loader
            data = np.load(f"nnue_lichess_{next_dataset}.npz", allow_pickle=True)
            full_dataset = NNUEData(data["X"], data["y"])
            dataset = Subset(full_dataset, range(0, val_cutoff))
            dataset_val = Subset(full_dataset, range(val_cutoff, len(full_dataset)))
            loader = DataLoader(dataset, batch_size=8192, shuffle=True, num_workers=4)
            val_loader = DataLoader(dataset_val, batch_size=8192, shuffle=False, num_workers=4)
            #dataset = NNUEData(data["X"][:val_cutoff], data["y"][:val_cutoff])
            #dataset_val = NNUEData(data["X"][val_cutoff:], data["y"][val_cutoff:])
            #loader = DataLoader(dataset, batch_size=16384, shuffle=True, num_workers=4)
            #val_loader = DataLoader(dataset_val, batch_size=16384, shuffle=False, num_workers=4)
        """

    print(f"Best loss: {best_val_loss}")

    plt.plot(train_losses, label="Train")
    plt.plot(val_losses, label="Test")
    plt.xlabel("Epoch")
    plt.ylabel("Loss")
    plt.legend()
    plt.show()


if __name__ == "__main__":
    main()
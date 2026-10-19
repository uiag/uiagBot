"""
Train the evaluation network.

    .venv2/Scripts/python nnue/train.py --epochs 4
    .venv2/Scripts/python nnue/train.py --resume nnue/net.pt --epochs 2 --lr 1e-4

The target is the reference score put through a sigmoid, and the loss is taken
in that space rather than on centipawns directly.  That is deliberate: the
difference between +300 and +400 hardly changes who wins, while the difference
between 0 and +100 decides games, and a squared error on centipawns would
spend most of its gradient on the first kind.  The sigmoid puts the weight
where the games are.

Validation uses a shard the training run never sees, so the number it reports
is over positions from a different part of the dump rather than a reshuffle of
the training part.  Alongside the loss it reports the correlation between the
net's output and the reference score, which is the same measure the
hand-crafted evaluation is scored on in testsuite/tools/eval_fit.py, so the
two are directly comparable.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time

import numpy as np
import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from arch import L1, OUTPUT_BUCKETS, SCALE, NnueNet          # noqa: E402
from data import Batches, load_shards                        # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(HERE, "data", "shard*.bin"))
    ap.add_argument("--valid-shard", default="shard6.bin",
                    help="held out of training entirely")
    ap.add_argument("--out", default=os.path.join(HERE, "net.pt"))
    ap.add_argument("--resume", default="")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--batch", type=int, default=16384)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--final-lr-frac", type=float, default=0.02)
    ap.add_argument("--wd", type=float, default=0.0)
    ap.add_argument("--cap", type=int, default=2000,
                    help="clamp the reference score to this many centipawns")
    ap.add_argument("--l1", type=int, default=L1)
    ap.add_argument("--buckets", type=int, default=OUTPUT_BUCKETS)
    ap.add_argument("--valid-batches", type=int, default=120)
    ap.add_argument("--report-every", type=int, default=200)
    ap.add_argument("--limit", type=int, default=0,
                    help="positions per epoch (0 = all)")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"device {device}  "
          f"{torch.cuda.get_device_name(0) if device.type == 'cuda' else ''}")
    torch.manual_seed(args.seed)

    print("loading training shards:")
    train_recs = load_shards(args.data, exclude=args.valid_shard)
    print("loading the held-out shard:")
    valid_recs = load_shards(os.path.join(os.path.dirname(args.data),
                                          args.valid_shard))
    # a fixed slice is enough to validate on and leaves the rest of the RAM free
    valid_recs = valid_recs[:2_000_000]

    train = Batches(train_recs, args.batch, args.cap, args.seed)
    valid = Batches(valid_recs, args.batch, args.cap, args.seed + 99)

    net = NnueNet(args.l1, args.buckets).to(device)
    if args.resume:
        net.load_state_dict(torch.load(args.resume, map_location=device))
        print(f"resumed from {args.resume}")
    params = sum(p.numel() for p in net.parameters())
    print(f"net (768 -> {args.l1}) x2 -> 1, {args.buckets} buckets, "
          f"{params / 1e6:.2f}M parameters")

    opt = torch.optim.AdamW(net.parameters(), lr=args.lr, weight_decay=args.wd)
    per_epoch = (args.limit or len(train)) // args.batch
    total_steps = max(1, per_epoch * args.epochs)
    sched = torch.optim.lr_scheduler.OneCycleLR(
        opt, max_lr=args.lr, total_steps=total_steps, pct_start=0.02,
        final_div_factor=1.0 / args.final_lr_frac, anneal_strategy="cos")
    print(f"{per_epoch:,} batches per epoch, {total_steps:,} steps total")

    inv = 1.0 / SCALE

    def loss_of(pred: torch.Tensor, score: torch.Tensor) -> torch.Tensor:
        return ((torch.sigmoid(pred) - torch.sigmoid(score * inv)) ** 2).mean()

    @torch.no_grad()
    def validate() -> tuple[float, float]:
        net.eval()
        total, n = 0.0, 0
        xs, ys = [], []
        for _ in range(args.valid_batches):
            iu, it, bk, sc = valid.sample(device)
            pred = net(iu, it, bk)
            total += float(loss_of(pred, sc)) * len(sc)
            n += len(sc)
            xs.append((pred * SCALE).float().cpu())
            ys.append(sc.float().cpu())
        net.train()
        x = torch.cat(xs).numpy()
        y = torch.cat(ys).numpy()
        corr = float(np.corrcoef(x, y)[0, 1])
        return total / max(n, 1), corr

    best = float("inf")
    step = 0
    t0 = time.perf_counter()
    for epoch in range(args.epochs):
        run, seen = 0.0, 0
        tick = time.perf_counter()
        for iu, it, bk, sc in train.epoch(device, args.limit):
            pred = net(iu, it, bk)
            loss = loss_of(pred, sc)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            net.clip_weights()
            if step < total_steps - 1:
                sched.step()
            run += float(loss.detach()) * len(sc)
            seen += len(sc)
            step += 1
            if step % args.report_every == 0:
                dt = time.perf_counter() - tick
                print(f"  epoch {epoch + 1} step {step:>7,}  "
                      f"loss {run / max(seen, 1):.6f}  "
                      f"lr {sched.get_last_lr()[0]:.2e}  "
                      f"{args.report_every * args.batch / dt / 1000:.0f}k pos/s",
                      flush=True)
                run, seen = 0.0, 0
                tick = time.perf_counter()

        vloss, vcorr = validate()
        mins = (time.perf_counter() - t0) / 60
        print(f"epoch {epoch + 1}/{args.epochs}  held-out loss {vloss:.6f}  "
              f"corr {vcorr:.4f}  ({mins:.1f} min)", flush=True)
        torch.save(net.state_dict(), args.out.replace(".pt", f"_e{epoch + 1}.pt"))
        if vloss < best:
            best = vloss
            torch.save(net.state_dict(), args.out)
            print(f"  saved {args.out}", flush=True)

    print(f"\nbest held-out loss {best:.6f}")
    # The loss lives in win-probability space.  Near an even position the
    # sigmoid changes by a quarter per unit of logit, so a probability error
    # of e corresponds to 4e logits, and one logit is SCALE centipawns.
    print(f"a loss of {best:.6f} is about "
          f"{4 * SCALE * math.sqrt(best):.0f} cp of rms error "
          f"near an even position")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

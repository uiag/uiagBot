"""
Turn the packed 32-byte records into training batches.

The whole set is 7.7 GB, which fits in memory, so the records are read once
and every batch after that is a random index into RAM -- no disk in the
training loop and no shuffle buffer to bias the sampling.

Decoding is vectorised over the batch.  Doing it a record at a time in Python
costs more than the GPU spends on the forward and backward passes together,
and the only part that looks awkward -- using a cumulative sum over the
occupancy bits to find which nibble belongs to which square -- is what removes
the last loop.
"""
from __future__ import annotations

import glob
import os

import numpy as np
import torch

import arch

REC = np.dtype([
    ("occ", "<u8"),
    ("pieces", "u1", 16),
    ("score", "<i2"),
    ("stm", "u1"),
    ("flags", "u1"),
    ("wksq", "u1"),
    ("bksq", "u1"),
    ("pad", "<u2"),
])
assert REC.itemsize == 32

MAX_PIECES = 32
EMPTY = 12                       # the piece code meaning "no piece"


def load_shards(pattern: str, exclude: str = "") -> np.ndarray:
    paths = sorted(glob.glob(pattern))
    if exclude:
        paths = [p for p in paths if os.path.basename(p) != exclude]
    if not paths:
        raise SystemExit(f"no shards matched {pattern}")
    parts = []
    total = 0
    for p in paths:
        a = np.fromfile(p, dtype=REC)
        total += len(a)
        print(f"  {os.path.basename(p)}: {len(a):,} records", flush=True)
        parts.append(a)
    out = np.concatenate(parts) if len(parts) > 1 else parts[0]
    print(f"  total {total:,} records ({out.nbytes / 1e9:.2f} GB)", flush=True)
    return out


def decode(batch: np.ndarray, cap: int, drop_mates: bool = False):
    """Records -> (idx_us, idx_them, bucket, target_cp) as numpy arrays.

    Indices are returned own-side first: `idx_us` is the board as the side to
    move sees it, with its own pieces in the first 384 planes and the board
    flipped when that side is Black.
    """
    n = len(batch)

    # --- which square holds which piece ---
    # occ as 8 bytes, little-endian, so square = byte * 8 + bit within byte.
    # A field of a structured array is strided, so it has to be copied before
    # it can be viewed as bytes.
    occ_bytes = np.ascontiguousarray(batch["occ"]).view(np.uint8).reshape(n, 8)
    bits = np.unpackbits(occ_bytes, axis=1, bitorder="little")      # (n, 64)

    # The i-th set bit (in square order) uses the i-th nibble of `pieces`.
    # A cumulative sum over the bits gives that i for every square at once.
    rank = np.cumsum(bits, axis=1, dtype=np.int16) - 1              # (n, 64)

    pb = batch["pieces"]                                            # (n, 16)
    nib = np.empty((n, MAX_PIECES), dtype=np.uint8)
    nib[:, 0::2] = pb & 0x0F
    nib[:, 1::2] = pb >> 4

    rank_c = np.clip(rank, 0, MAX_PIECES - 1)
    codes = np.take_along_axis(nib, rank_c.astype(np.intp), axis=1)  # (n, 64)
    codes = np.where(bits.astype(bool), codes, EMPTY)

    # --- the active squares, padded to a fixed width ---
    # Sorting the occupancy bits puts the occupied squares first; `order` then
    # lists each position's squares with the empties trailing.
    order = np.argsort(~bits.astype(bool), axis=1, kind="stable")    # (n, 64)
    order = order[:, :MAX_PIECES]                                    # (n, 32)
    sq = order.astype(np.int32)
    code = np.take_along_axis(codes, order.astype(np.intp), axis=1)
    occupied = code != EMPTY

    colour = (code >= 6).astype(np.int32)        # 0 white, 1 black
    ptype = (code % 6).astype(np.int32)          # 0 pawn .. 5 king

    stm = batch["stm"].astype(np.int32)[:, None]  # 0 white to move

    # Each perspective sees the board in its own orientation, folded onto
    # files a-d by its own king's file, and indexes the copy of the 768 planes
    # that belongs to its own king's region.  `wksq`/`bksq` are in the record,
    # so none of this costs a scan of the board.
    wk = batch["wksq"].astype(np.int32)[:, None]
    bk = batch["bksq"].astype(np.int32)[:, None]
    us_k = np.where(stm == 1, bk, wk)        # the king of the side to move
    them_k = np.where(stm == 1, wk, bk)

    kb_table = np.asarray(arch.KING_BUCKET_TABLE, dtype=np.int32)

    def view(persp_k, persp_is_black, own):
        """Indices for one perspective."""
        # own-relative orientation: Black flips the board vertically
        k_rel = np.where(persp_is_black, persp_k ^ 56, persp_k)
        sq_rel = np.where(persp_is_black, sq ^ 56, sq)
        # fold onto files a-d by the king's file
        mirror = (k_rel % 8) >= 4
        sq_rel = np.where(mirror, sq_rel ^ 7, sq_rel)
        kbucket = kb_table[np.where(mirror, k_rel ^ 7, k_rel)]
        return (kbucket * arch.NUM_PLANES
                + (1 - own) * 384 + ptype * 64 + sq_rel)

    own_us = (colour == stm).astype(np.int32)
    own_them = (colour != stm).astype(np.int32)
    idx_us = view(us_k, stm == 1, own_us)
    idx_them = view(them_k, stm == 0, own_them)

    pad = np.int32(arch.KING_BUCKETS * arch.NUM_PLANES)
    idx_us = np.where(occupied, idx_us, pad)
    idx_them = np.where(occupied, idx_them, pad)

    piece_count = occupied.sum(axis=1).astype(np.int32)
    bucket = np.clip((piece_count - 2) * 8 // 31, 0, 7)

    # Score is stored from White's point of view; the net speaks for the side
    # to move.
    score = batch["score"].astype(np.float32)
    score = np.where(batch["stm"] == 1, -score, score)
    score = np.clip(score, -cap, cap)

    return idx_us, idx_them, bucket, score


class Batches:
    """Random batches from an in-memory record array."""

    def __init__(self, recs: np.ndarray, batch: int, cap: int, seed: int,
                 drop_mates: bool = False) -> None:
        if drop_mates:
            keep = (recs["flags"] & 1) == 0
            recs = recs[keep]
            print(f"  dropped mate scores, {len(recs):,} left", flush=True)
        self.recs = recs
        self.batch = batch
        self.cap = cap
        self.rng = np.random.default_rng(seed)

    def __len__(self) -> int:
        return len(self.recs)

    def sample(self, device: torch.device):
        i = self.rng.integers(0, len(self.recs), self.batch)
        idx_us, idx_them, bucket, score = decode(self.recs[i], self.cap)
        return (
            torch.from_numpy(idx_us).to(device, non_blocking=True).long(),
            torch.from_numpy(idx_them).to(device, non_blocking=True).long(),
            torch.from_numpy(bucket).to(device, non_blocking=True).long(),
            torch.from_numpy(score).to(device, non_blocking=True),
        )

    def epoch(self, device: torch.device, limit: int = 0):
        """Walk the whole set once in a fresh random order."""
        n = limit or len(self.recs)
        perm = self.rng.permutation(len(self.recs))[:n]
        for start in range(0, n - self.batch + 1, self.batch):
            i = np.sort(perm[start:start + self.batch])   # sorted = cache-kind
            idx_us, idx_them, bucket, score = decode(self.recs[i], self.cap)
            yield (
                torch.from_numpy(idx_us).to(device, non_blocking=True).long(),
                torch.from_numpy(idx_them).to(device, non_blocking=True).long(),
                torch.from_numpy(bucket).to(device, non_blocking=True).long(),
                torch.from_numpy(score).to(device, non_blocking=True),
            )

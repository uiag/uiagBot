"""
Check the batch decoder against python-chess.

A wrong feature index does not crash anything: it trains a net that is merely
worse, and there is no way to tell that apart from a bad architecture or a bad
learning rate once the run is under way.  So the decoder is checked against an
independent implementation first, square by square, on real records.

    python nnue/test_data.py nnue/data/shard0.bin
"""
from __future__ import annotations

import sys

import chess
import numpy as np

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

from data import REC, decode          # noqa: E402

PIECE_CHARS = "PNBRQKpnbrqk"


def reference(rec) -> tuple[set[int], set[int], int, float]:
    """Build the two feature sets the slow, obvious way."""
    board = chess.Board.empty()
    occ = int(rec["occ"])
    i = 0
    while occ:
        sq = (occ & -occ).bit_length() - 1
        occ &= occ - 1
        byte = int(rec["pieces"][i >> 1])
        code = (byte >> 4) if (i & 1) else (byte & 15)
        board.set_piece_at(sq, chess.Piece.from_symbol(PIECE_CHARS[code]))
        i += 1
    stm = chess.BLACK if rec["stm"] else chess.WHITE
    board.turn = stm

    us, them = set(), set()
    for sq, piece in board.piece_map().items():
        ptype = piece.piece_type - 1                   # 0 pawn .. 5 king
        # own-side-relative, board flipped for Black
        own_us = piece.color == stm
        sq_us = sq ^ 56 if stm == chess.BLACK else sq
        us.add((0 if own_us else 1) * 384 + ptype * 64 + sq_us)

        own_them = piece.color != stm
        sq_them = sq if stm == chess.BLACK else sq ^ 56
        them.add((0 if own_them else 1) * 384 + ptype * 64 + sq_them)

    count = len(board.piece_map())
    score = float(rec["score"])
    if rec["stm"]:
        score = -score
    return us, them, count, score


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "nnue/data/shard0.bin"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3000

    a = np.fromfile(path, dtype=REC, count=400000)
    rng = np.random.default_rng(5)
    batch = a[rng.choice(len(a), n, replace=False)]

    idx_us, idx_them, bucket, score = decode(batch, cap=20000)

    bad = 0
    for k in range(n):
        want_us, want_them, count, want_score = reference(batch[k])
        got_us = set(int(v) for v in idx_us[k] if v != 768)
        got_them = set(int(v) for v in idx_them[k] if v != 768)
        want_bucket = min(max((count - 2) * 8 // 31, 0), 7)
        ok = (got_us == want_us and got_them == want_them
              and int(bucket[k]) == want_bucket
              and abs(float(score[k]) - want_score) < 1e-3)
        if not ok:
            bad += 1
            if bad <= 3:
                print(f"record {k}: pieces {count}")
                print(f"  us   missing {sorted(want_us - got_us)} "
                      f"extra {sorted(got_us - want_us)}")
                print(f"  them missing {sorted(want_them - got_them)} "
                      f"extra {sorted(got_them - want_them)}")
                print(f"  bucket {int(bucket[k])} want {want_bucket}  "
                      f"score {float(score[k])} want {want_score}")

    # A feature set must have exactly one entry per piece, and the two
    # perspectives must agree on how many.
    counts_us = (idx_us != 768).sum(axis=1)
    counts_them = (idx_them != 768).sum(axis=1)
    print(f"\nchecked {n} records against python-chess: "
          f"{n - bad} ok, {bad} wrong")
    print(f"features per position: {counts_us.min()}..{counts_us.max()}, "
          f"both perspectives equal: {bool((counts_us == counts_them).all())}")
    print(f"index range: {idx_us[idx_us != 768].min()}.."
          f"{idx_us[idx_us != 768].max()} (must be 0..767)")
    print(f"bucket spread: {np.bincount(bucket, minlength=8)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

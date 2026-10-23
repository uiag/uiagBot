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
import arch                           # noqa: E402

PIECE_CHARS = "PNBRQKpnbrqk"
PAD = arch.KING_BUCKETS * arch.NUM_PLANES


def one_view(piece_map, persp: bool, king_sq: int) -> set[int]:
    """The feature set one perspective sees, built the slow, obvious way.

    Own-side-relative orientation, folded onto files a-d by this
    perspective's own king file, in the plane copy its king region selects.
    """
    flip = 56 if persp == chess.BLACK else 0
    k_rel = king_sq ^ flip
    mirror = (k_rel % 8) >= 4
    bucket = arch.KING_BUCKET_TABLE[k_rel ^ 7 if mirror else k_rel]

    out = set()
    for sq, piece in piece_map.items():
        ptype = piece.piece_type - 1                   # 0 pawn .. 5 king
        sq_rel = sq ^ flip
        if mirror:
            sq_rel ^= 7
        own = piece.color == persp
        out.add(bucket * arch.NUM_PLANES
                + (0 if own else 1) * 384 + ptype * 64 + sq_rel)
    return out


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

    pm = board.piece_map()
    # The kings come from the record rather than from the board, which is
    # also a check that the two agree.
    wk, bk = int(rec["wksq"]), int(rec["bksq"])
    assert pm[wk] == chess.Piece(chess.KING, chess.WHITE), "wksq disagrees"
    assert pm[bk] == chess.Piece(chess.KING, chess.BLACK), "bksq disagrees"

    us = one_view(pm, stm, bk if stm == chess.BLACK else wk)
    them = one_view(pm, not stm, wk if stm == chess.BLACK else bk)

    count = len(pm)
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
        got_us = set(int(v) for v in idx_us[k] if v != PAD)
        got_them = set(int(v) for v in idx_them[k] if v != PAD)
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
    counts_us = (idx_us != PAD).sum(axis=1)
    counts_them = (idx_them != PAD).sum(axis=1)
    print(f"\nchecked {n} records against python-chess: "
          f"{n - bad} ok, {bad} wrong")
    print(f"features per position: {counts_us.min()}..{counts_us.max()}, "
          f"both perspectives equal: {bool((counts_us == counts_them).all())}")
    print(f"index range: {idx_us[idx_us != PAD].min()}.."
          f"{idx_us[idx_us != PAD].max()} (must be 0..{PAD - 1})")
    kb = (idx_us[idx_us != PAD] // arch.NUM_PLANES).astype(np.int64)
    print(f"king-bucket spread: {np.bincount(kb, minlength=arch.KING_BUCKETS)}")
    print(f"bucket spread: {np.bincount(bucket, minlength=8)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

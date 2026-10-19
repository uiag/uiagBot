"""
Where is the evaluation systematically wrong?

Fitting the evaluation to search scores is a trap this project already paid
for: a static evaluation cannot reproduce a search, so the fit buys down the
unreachable part of the error by flattening every weight, which improves the
loss and loses Elo (see the note at the top of tune_eval.py -- 23% better
held-back loss, -80 Elo).

*Diagnosing* with search scores is a different question and a safe one.  The
scale is fitted once and then held fixed, and what is reported is the mean
signed residual inside each of a set of buckets.  A term that is weighted
correctly contributes nothing to the mean residual of the bucket that isolates
it; a term that is three times too small leaves the whole bucket leaning one
way.  That is a statement about shape, not about confidence, and it survives
the flattening objection because no parameter is moved to produce it.

    python testsuite/tools/eval_bias.py --data nnue/data/shard0.bin -n 150000
    python testsuite/tools/eval_bias.py --set testsuite/results/evalset_wdl.txt
"""
from __future__ import annotations

import argparse
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TS_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(TS_DIR)
sys.path.insert(0, ROOT)

import chess                                 # noqa: E402
import engine_core                           # noqa: E402

REC = np.dtype([("occ", "<u8"), ("pieces", "u1", 16), ("score", "<i2"),
                ("stm", "u1"), ("flags", "u1"), ("wksq", "u1"), ("bksq", "u1"),
                ("pad", "<u2")])
PIECE_CHARS = "PNBRQKpnbrqk"


def rec_to_fen(r) -> str:
    """Rebuild a FEN from a packed record (castling and clocks are not kept)."""
    board = chess.Board.empty()
    occ = int(r["occ"])
    i = 0
    pieces = r["pieces"]
    while occ:
        sq = (occ & -occ).bit_length() - 1
        occ &= occ - 1
        byte = int(pieces[i >> 1])
        code = (byte >> 4) if (i & 1) else (byte & 15)
        board.set_piece_at(sq, chess.Piece.from_symbol(PIECE_CHARS[code]))
        i += 1
    board.turn = chess.WHITE if r["stm"] == 0 else chess.BLACK
    return board.fen()


def load_packed(path: str, limit: int, cap: int, seed: int):
    a = np.fromfile(path, dtype=REC)
    rng = np.random.default_rng(seed)
    keep = a["flags"] & 1 == 0                      # drop mate scores
    keep &= np.abs(a["score"].astype(np.int32)) <= cap
    a = a[keep]
    if limit and len(a) > limit:
        a = a[rng.choice(len(a), limit, replace=False)]
    return [(rec_to_fen(r), int(r["score"])) for r in a]


def load_epd(path: str, limit: int, seed: int):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            fen, _, score = line.strip().rpartition(";")
            if not fen:
                continue
            try:
                rows.append((fen, int(score)))
            except ValueError:
                continue
    rng = np.random.default_rng(seed)
    rng.shuffle(rows)
    return rows[:limit] if limit else rows


# ------------------------------------------------------------------ buckets
#
# Each bucket isolates one block of the evaluation: positions where that block
# has something to say about White minus positions where it has something to
# say about Black.  The residual is signed from White's point of view, so a
# term that is too small shows up as a bucket whose mean residual has the same
# sign as the feature it keys on.

def bucket_features(board: chess.Board) -> dict[str, float]:
    """Per-position feature values, signed White minus Black."""
    out: dict[str, float] = {}
    occ = board.occupied

    def count(colour, piece):
        return len(board.pieces(piece, colour))

    # --- mobility: legal-ish piece moves, pawns excluded ---
    mob = [0, 0]
    for colour in (chess.BLACK, chess.WHITE):
        for piece in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
            for sq in board.pieces(piece, colour):
                mob[colour] += chess.popcount(
                    board.attacks_mask(sq) & ~board.occupied_co[colour])
    out["mobility"] = mob[chess.WHITE] - mob[chess.BLACK]

    # --- king safety: enemy pieces bearing on the king ring, shelter pawns ---
    danger = [0, 0]
    shelter = [0, 0]
    for colour in (chess.BLACK, chess.WHITE):
        ksq = board.king(colour)
        if ksq is None:
            continue
        ring = chess.BB_KING_ATTACKS[ksq]
        for piece, weight in ((chess.KNIGHT, 3), (chess.BISHOP, 2),
                              (chess.ROOK, 2), (chess.QUEEN, 4)):
            for sq in board.pieces(piece, not colour):
                if board.attacks_mask(sq) & ring:
                    danger[colour] += weight
        front = chess.BB_RANKS[chess.square_rank(ksq) + (1 if colour else -1)] \
            if 0 <= chess.square_rank(ksq) + (1 if colour else -1) <= 7 else 0
        files = chess.BB_FILES[chess.square_file(ksq)]
        if chess.square_file(ksq) > 0:
            files |= chess.BB_FILES[chess.square_file(ksq) - 1]
        if chess.square_file(ksq) < 7:
            files |= chess.BB_FILES[chess.square_file(ksq) + 1]
        shelter[colour] = chess.popcount(
            board.pieces_mask(chess.PAWN, colour) & files & front)
    # danger[WHITE] is the pressure on White's king, so it counts against White
    out["king_danger"] = danger[chess.BLACK] - danger[chess.WHITE]
    out["king_shelter"] = shelter[chess.WHITE] - shelter[chess.BLACK]

    # --- passed pawns, weighted by how far they have come ---
    passed = [0.0, 0.0]
    for colour in (chess.BLACK, chess.WHITE):
        them = not colour
        for sq in board.pieces(chess.PAWN, colour):
            f, r = chess.square_file(sq), chess.square_rank(sq)
            span = 0
            for df in (-1, 0, 1):
                if 0 <= f + df <= 7:
                    span |= chess.BB_FILES[f + df]
            ahead = 0
            rng_ = range(r + 1, 8) if colour == chess.WHITE else range(0, r)
            for rr in rng_:
                ahead |= chess.BB_RANKS[rr]
            if not (board.pieces_mask(chess.PAWN, them) & span & ahead):
                rel = r if colour == chess.WHITE else 7 - r
                passed[colour] += max(0, rel - 1)
    out["passed"] = passed[chess.WHITE] - passed[chess.BLACK]

    # --- pawn structure: isolated and doubled ---
    struct = [0, 0]
    for colour in (chess.BLACK, chess.WHITE):
        pawns = board.pieces_mask(chess.PAWN, colour)
        for f in range(8):
            n = chess.popcount(pawns & chess.BB_FILES[f])
            if n > 1:
                struct[colour] -= n - 1
            if n:
                adj = 0
                if f > 0:
                    adj |= chess.BB_FILES[f - 1]
                if f < 7:
                    adj |= chess.BB_FILES[f + 1]
                if not (pawns & adj):
                    struct[colour] -= 1
    out["pawn_struct"] = struct[chess.WHITE] - struct[chess.BLACK]

    # --- bishop pair, rook on open file, space ---
    out["bishop_pair"] = ((count(chess.WHITE, chess.BISHOP) >= 2)
                          - (count(chess.BLACK, chess.BISHOP) >= 2))
    rook_open = [0, 0]
    for colour in (chess.BLACK, chess.WHITE):
        for sq in board.pieces(chess.ROOK, colour):
            f = chess.BB_FILES[chess.square_file(sq)]
            if not (board.pieces_mask(chess.PAWN, colour) & f):
                rook_open[colour] += 2 if not (
                    board.pieces_mask(chess.PAWN, not colour) & f) else 1
    out["rook_open"] = rook_open[chess.WHITE] - rook_open[chess.BLACK]

    centre = (chess.BB_FILES[2] | chess.BB_FILES[3]
              | chess.BB_FILES[4] | chess.BB_FILES[5])
    space = [0, 0]
    for colour in (chess.BLACK, chess.WHITE):
        half = (chess.BB_RANKS[1] | chess.BB_RANKS[2] | chess.BB_RANKS[3]
                ) if colour == chess.WHITE else (
            chess.BB_RANKS[6] | chess.BB_RANKS[5] | chess.BB_RANKS[4])
        space[colour] = chess.popcount(
            board.pieces_mask(chess.PAWN, colour) & centre & half)
    out["space"] = space[chess.WHITE] - space[chess.BLACK]

    out["phase"] = float(chess.popcount(occ))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="", help="packed .bin from nnue/extract")
    ap.add_argument("--set", default="", help="fen;score text file")
    ap.add_argument("-n", "--limit", type=int, default=40000)
    ap.add_argument("--cap", type=int, default=1200,
                    help="drop positions beyond this many centipawns: the "
                         "residual there is about conversion, not evaluation")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--eval-mode", default="hce")
    ap.add_argument("--options", default="",
                    help="name=value,... applied before measuring")
    args = ap.parse_args()

    if args.data:
        rows = load_packed(args.data, args.limit, args.cap, args.seed)
    elif args.set:
        rows = load_epd(args.set, args.limit, args.seed)
    else:
        raise SystemExit("one of --data or --set is required")
    print(f"{len(rows)} positions")

    eng = engine_core.Engine(hash_mb=8, threads=1, eval_mode=args.eval_mode)
    lib, ffi = eng._lib, eng._ffi
    for kv in args.options.split(","):
        if "=" in kv:
            name, value = kv.split("=", 1)
            if not lib.ce_set_param(name.strip().encode(), int(value)):
                raise SystemExit(f"unknown parameter {name!r}")

    # engine scores, from White's point of view
    ours = np.empty(len(rows), dtype=np.float64)
    theirs = np.empty(len(rows), dtype=np.float64)
    feats: list[dict[str, float]] = []
    for i, (fen, score) in enumerate(rows):
        lib.ce_set_position(fen.encode(), b"")
        v = lib.ce_eval()
        white_to_move = fen.split()[1] == "w"
        ours[i] = v if white_to_move else -v
        theirs[i] = score
        feats.append(bucket_features(chess.Board(fen)))

    # One global scale, fitted once by least squares and then held fixed.
    scale = float(np.dot(ours, theirs) / max(np.dot(ours, ours), 1e-9))
    resid = theirs - scale * ours
    rms = math.sqrt(float(np.mean(resid ** 2)))
    corr = float(np.corrcoef(ours, theirs)[0, 1])
    print(f"global scale x{scale:.3f}   residual rms {rms:.1f} cp   "
          f"correlation {corr:.4f}")
    print(f"(the engine reports {1 / scale:.2f} units per real centipawn)\n")

    names = list(feats[0].keys())
    print(f"{'bucket':14s} {'n':>7s} {'mean feat':>10s} {'mean resid':>11s} "
          f"{'slope':>8s} {'verdict'}")
    print("-" * 74)
    for name in names:
        if name == "phase":
            continue
        x = np.array([f[name] for f in feats], dtype=np.float64)
        # Slope of the residual on the feature: the centipawns per unit of this
        # feature that the evaluation is still failing to account for.
        xc = x - x.mean()
        denom = float(np.dot(xc, xc))
        if denom < 1e-9:
            continue
        slope = float(np.dot(xc, resid - resid.mean()) / denom)
        strong = x > np.quantile(x, 0.90)
        weak = x < np.quantile(x, 0.10)
        mean_resid = float(resid[strong].mean() - resid[weak].mean()) / 2.0
        verdict = ("under-weighted" if slope > 1.0 else
                   "over-weighted" if slope < -1.0 else "about right")
        print(f"{name:14s} {len(x):7d} {x.mean():10.2f} {mean_resid:11.1f} "
              f"{slope:8.2f}  {verdict}")

    # split by phase, since a mis-weighted term usually leans one way only
    print()
    occ = np.array([f["phase"] for f in feats])
    for label, mask in (("middlegame (>=24 pieces)", occ >= 24),
                        ("middle      (16-23)", (occ >= 16) & (occ < 24)),
                        ("endgame     (<16)", occ < 16)):
        if mask.sum() < 200:
            continue
        r = resid[mask]
        print(f"{label:26s} n={mask.sum():6d}  mean resid {r.mean():+7.1f}  "
              f"rms {math.sqrt(float(np.mean(r ** 2))):6.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

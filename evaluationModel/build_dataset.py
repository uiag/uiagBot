import orjson
import chess
import numpy as np
import math

from features import encode_position

MAX_CP = 10000

def cp_to_wdl(cp, scale=400):
    cp = max(-10000, min(10000, cp))
    return 1.0 / (1.0 + math.exp(-cp / scale))

def extract_score(evals):
    # Choose eval with maximum depth
    best = max(evals, key=lambda e: e["depth"])
    pv = best["pvs"][0]

    if "mate" in pv:
        sign = 1 if pv["mate"] > 0 else 0
        return sign
    else:
        return cp_to_wdl(pv["cp"])

def stream_positions(path):
    with open(path, "rb") as f:
        for i, line in enumerate(f):
            try:
                yield orjson.loads(line)
            except orjson.JSONDecodeError:
                print(f"Skipping bad line {i}")

def build_npz(
    input_path,
    output_path,
    limit=None,
    version=1,
):
    X, y = [], []
    limit2 = version * limit
    for i, obj in enumerate(stream_positions(input_path)):
        if limit and i < (version-1)*limit:
            continue
        if limit and i >= limit2:
            break

        board = chess.Board(obj["fen"])
        feats = encode_position(board)
        if feats is None:
            continue

        score = extract_score(obj["evals"])

        X.append(feats)
        y.append(score)

        if i % 1000000 == 0 and i > 0:
            print(f"{i} positions processed")

    print(max(y))
    print(min(y))

    np.savez_compressed(
        output_path,
        X=np.array(X, dtype=object),
        y=np.array(y, dtype=np.float32)
    )

if __name__ == "__main__":
    build_npz(
        "lichess_db_eval.jsonl",
        "nnue_lichess.npz",
        limit=100000, # set for testing
        version=1
    )

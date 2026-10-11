import chess
import numpy as np

BASE_FEATURES = 12 * 64 + 1

def encode_position(board: chess.Board):
    """
    Returns a numpy array of active feature indices (sparse).
    """
    features = []

    # Piece-square features
    for square, piece in board.piece_map().items():

        idx = piece.color * 64 * 6 + (piece.piece_type - 1) * 64 + square
        features.append(idx)

    features.append(board.turn)
    return np.array(features, dtype=np.int32)


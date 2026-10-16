"""Opening positions for matches.

Matches must not start every game from the initial position: the engines would
repeat the same game and the result would say nothing.  Every opening is played
twice with the colours swapped, so an unbalanced opening cannot favour either
side.
"""
from __future__ import annotations

import io
import os
import random
from dataclasses import dataclass
from typing import Iterator, Optional, Sequence

import chess
import chess.pgn


@dataclass
class Opening:
    label: str
    fen: str
    moves: list[str]        # UCI moves from `fen`, for a readable PGN

    def board(self) -> chess.Board:
        board = chess.Board(self.fen) if self.fen else chess.Board()
        for uci in self.moves:
            board.push_uci(uci)
        return board


START_FEN = chess.STARTING_FEN


class OpeningBook:
    """A list of start positions, indexed by game pair."""

    def __init__(self, openings: Sequence[Opening], name: str = "built-in") -> None:
        self.openings = list(openings) or [Opening("startpos", START_FEN, [])]
        self.name = name

    def __len__(self) -> int:
        return len(self.openings)

    def for_round(self, index: int) -> Opening:
        """Opening for game `index`; consecutive pairs share one opening."""
        return self.openings[(index // 2) % len(self.openings)]

    def engine_plays_white(self, index: int) -> bool:
        return index % 2 == 0

    def shuffled(self, seed: int = 0) -> "OpeningBook":
        openings = list(self.openings)
        random.Random(seed).shuffle(openings)
        return OpeningBook(openings, self.name)

    # ---- loading ----

    @classmethod
    def from_file(cls, path: str, plies: int = 8, limit: int = 0) -> "OpeningBook":
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pgn":
            openings = list(_from_pgn(path, plies))
        else:
            openings = list(_from_epd(path))
        if limit:
            openings = openings[:limit]
        return cls(openings, os.path.basename(path))

    @classmethod
    def single(cls, fen: str = START_FEN) -> "OpeningBook":
        return cls([Opening("fixed", fen, [])], "single position")


def _from_epd(path: str) -> Iterator[Opening]:
    """One position per line: a FEN, optionally followed by `; id "name"`."""
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            fen_part, _, tail = line.partition(";")
            label = _label_from_tail(tail) or f"line {lineno}"
            fen = fen_part.strip()
            try:
                board = chess.Board(fen)
            except ValueError:
                # EPDs often omit the halfmove/fullmove counters
                try:
                    board = chess.Board()
                    board.set_epd(fen)
                except ValueError:
                    continue
            yield Opening(label, board.fen(), [])


def _label_from_tail(tail: str) -> Optional[str]:
    for field in tail.split(";"):
        field = field.strip()
        if field.startswith("id "):
            return field[3:].strip().strip('"')
    return None


def _from_pgn(path: str, plies: int) -> Iterator[Opening]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        while True:
            game = chess.pgn.read_game(fh)
            if game is None:
                return
            board = game.board()
            moves: list[str] = []
            for move in game.mainline_moves():
                if len(moves) >= plies:
                    break
                moves.append(move.uci())
                board.push(move)
            if not moves:
                continue
            label = (game.headers.get("Opening")
                     or game.headers.get("Event")
                     or f"{game.headers.get('White', '?')}-{game.headers.get('Black', '?')}")
            yield Opening(label, game.board().fen(), moves)


def write_epd(path: str, openings: Sequence[Opening], header: str = "") -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        if header:
            for line in header.splitlines():
                fh.write(f"# {line}\n")
        for opening in openings:
            board = opening.board()
            fh.write(f'{board.fen()}; id "{opening.label}";\n')


def san_line(fen: str, moves: Sequence[str]) -> str:
    """Render UCI moves from `fen` as SAN, for readable labels."""
    board = chess.Board(fen) if fen else chess.Board()
    out: list[str] = []
    for uci in moves:
        move = chess.Move.from_uci(uci)
        out.append(board.san(move))
        board.push(move)
    return " ".join(out)


def pgn_from_game(board: chess.Board, headers: dict[str, str]) -> str:
    """Serialise a finished game, taken from `board.move_stack`."""
    game = chess.pgn.Game.from_board(board)
    for key, value in headers.items():
        game.headers[key] = str(value)
    out = io.StringIO()
    exporter = chess.pgn.FileExporter(out)
    game.accept(exporter)
    return out.getvalue()

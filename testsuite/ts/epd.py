"""EPD parsing.

Extended Position Description is the format the published test suites come in::

    1k1r4/pp1b1R2/3q2pp/4p3/2B5/4Q3/PPP2B2/2K5 b - - bm Qd1+; id "WAC.001";

Supported operations: ``bm`` (best moves), ``am`` (moves to avoid),
``dm`` (mate distance), ``id``, ``c0`` (a comment), and ``Dn`` (perft node
count at depth n, used by perft suites).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterator, Optional

import chess

_PERFT_OP = re.compile(r"^[Dd]([0-9]+)$")


@dataclass
class EpdRecord:
    fen: str
    board: chess.Board
    best_moves: list[chess.Move] = field(default_factory=list)
    avoid_moves: list[chess.Move] = field(default_factory=list)
    mate_in: Optional[int] = None
    ident: str = ""
    comment: str = ""
    perft: dict[int, int] = field(default_factory=dict)
    source: str = ""
    line_no: int = 0

    @property
    def label(self) -> str:
        return self.ident or f"{self.source}:{self.line_no}"

    def best_san(self) -> str:
        return " ".join(self.board.san(m) for m in self.best_moves)

    def avoid_san(self) -> str:
        return " ".join(self.board.san(m) for m in self.avoid_moves)

    def accepts(self, move: Optional[chess.Move]) -> bool:
        """Does `move` solve this position?"""
        if move is None:
            return False
        if self.best_moves:
            return move in self.best_moves
        if self.avoid_moves:
            return move not in self.avoid_moves
        return False


def _split_fields(text: str) -> list[str]:
    """Split an EPD tail on ';' while respecting quoted strings."""
    out: list[str] = []
    current: list[str] = []
    in_quotes = False
    for ch in text:
        if ch == '"':
            in_quotes = not in_quotes
            current.append(ch)
        elif ch == ";" and not in_quotes:
            out.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        out.append("".join(current))
    return [f.strip() for f in out if f.strip()]


def _token_spans(line: str) -> list[tuple[int, int]]:
    """Start/end offset of every whitespace separated token."""
    spans: list[tuple[int, int]] = []
    pos = 0
    for token in line.split():
        start = line.index(token, pos)
        pos = start + len(token)
        spans.append((start, pos))
    return spans


def parse_line(line: str, source: str = "", line_no: int = 0) -> Optional[EpdRecord]:
    """Parse one EPD line, or return None for blanks and comments."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    # The position is the first four tokens, plus the two move counters when
    # the line carries a full FEN.  Everything after that is operations.
    spans = _token_spans(line)
    tokens = line.split()
    if len(tokens) < 4:
        return None

    board = chess.Board()
    position_tokens = 4
    if (len(tokens) >= 6 and tokens[4].rstrip(";").isdigit()
            and tokens[5].rstrip(";").isdigit()):
        position_tokens = 6

    for count in (position_tokens, 4):
        fen = " ".join(t.rstrip(";") for t in tokens[:count])
        try:
            board.set_fen(fen)
            position_tokens = count
            break
        except ValueError:
            continue
    else:
        return None

    rest = line[spans[position_tokens - 1][1]:].lstrip(" ;")
    record = EpdRecord(fen=board.fen(), board=board, source=source, line_no=line_no)

    for field_text in _split_fields(rest):
        op, _, value = field_text.partition(" ")
        value = value.strip()
        if op == "bm":
            record.best_moves = _parse_moves(board, value)
        elif op == "am":
            record.avoid_moves = _parse_moves(board, value)
        elif op == "dm":
            try:
                record.mate_in = int(value.strip('"'))
            except ValueError:
                pass
        elif op == "id":
            record.ident = value.strip('"')
        elif op in ("c0", "c1", "c2"):
            record.comment = value.strip('"')
        else:
            m = _PERFT_OP.match(op)
            if m:
                try:
                    record.perft[int(m.group(1))] = int(value.replace(",", ""))
                except ValueError:
                    pass
    return record


def _parse_moves(board: chess.Board, text: str) -> list[chess.Move]:
    moves: list[chess.Move] = []
    for token in text.replace(",", " ").split():
        token = token.strip().strip('"')
        if not token:
            continue
        move = None
        try:
            move = board.parse_san(token)
        except ValueError:
            try:
                move = board.parse_uci(token.lower())
            except ValueError:
                move = None
        if move is not None and move not in moves:
            moves.append(move)
    return moves


def read(path: str) -> list[EpdRecord]:
    """Read an EPD file; raises if nothing in it parsed."""
    if not os.path.isfile(path):
        raise SystemExit(f"no suite file at {path}")
    source = os.path.splitext(os.path.basename(path))[0]
    records = list(iter_file(path, source))
    if not records:
        raise SystemExit(f"{path} contains no usable positions")
    return records


def iter_file(path: str, source: str = "") -> Iterator[EpdRecord]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh, 1):
            record = parse_line(line, source or os.path.basename(path), line_no)
            if record is not None:
                yield record


def write(path: str, records: list[EpdRecord], header: str = "") -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        if header:
            for line in header.splitlines():
                fh.write(f"# {line}\n")
        for record in records:
            # the full FEN, not board.epd(): EPD drops the halfmove clock, and
            # a position at the edge of the fifty-move rule is a real test case
            parts = [record.board.fen()]
            if record.best_moves:
                parts.append(f"bm {record.best_san()}")
            if record.avoid_moves:
                parts.append(f"am {record.avoid_san()}")
            if record.mate_in is not None:
                parts.append(f"dm {record.mate_in}")
            for depth in sorted(record.perft):
                parts.append(f"D{depth} {record.perft[depth]}")
            if record.ident:
                parts.append(f'id "{record.ident}"')
            if record.comment:
                parts.append(f'c0 "{record.comment}"')
            fh.write("; ".join(parts) + ";\n")

"""Talking to UCI engines.

This speaks UCI over plain blocking pipes rather than through
``chess.engine``.  Two reasons: python-chess drives engines through asyncio,
and its Windows proactor write path is broken on the PyPy interpreter this
project runs on (``WriteFile`` rejects the bytearray buffer); and doing it
directly makes the timing measurement, the illegal-move handling and the
non-standard ``perft`` / ``bench`` commands all one mechanism instead of
three.  python-chess is still used for the board, SAN and PGN.
"""
from __future__ import annotations

import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

import chess

from .config import EngineSpec, TimeControl

#: Mate scores are mapped onto this many centipawns when a single number is
#: wanted.  Well clear of any real evaluation, well inside int16.
MATE_CP = 30000

#: Options that have to be sent before others: Stockfish ignores UCI_Elo
#: unless UCI_LimitStrength is already on.
_OPTION_ORDER = {"UCI_LimitStrength": 0, "Threads": 1, "Hash": 2}

_CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_NODES_RE = re.compile(r"(?:nodes searched|perft\s*\([0-9]+\)\s*=|nodes)\s*[:=]?\s*([0-9]+)",
                       re.I)


class EngineProblem(RuntimeError):
    """The engine crashed, hung, or broke the protocol."""


class IllegalMove(EngineProblem):
    """The engine returned a move that is not legal in the position."""


# ------------------------------------------------------------------- limits


@dataclass(frozen=True)
class Limit:
    """What to put after ``go``."""

    movetime_ms: int = 0
    depth: int = 0
    nodes: int = 0
    wtime_ms: int = 0
    btime_ms: int = 0
    winc_ms: int = 0
    binc_ms: int = 0
    movestogo: int = 0
    infinite: bool = False

    def go_args(self) -> str:
        parts: list[str] = []
        if self.wtime_ms or self.btime_ms:
            parts += [f"wtime {max(1, int(self.wtime_ms))}",
                      f"btime {max(1, int(self.btime_ms))}"]
            if self.winc_ms or self.binc_ms:
                parts += [f"winc {int(self.winc_ms)}", f"binc {int(self.binc_ms)}"]
            if self.movestogo:
                parts.append(f"movestogo {int(self.movestogo)}")
        if self.movetime_ms:
            parts.append(f"movetime {int(self.movetime_ms)}")
        if self.depth:
            parts.append(f"depth {int(self.depth)}")
        if self.nodes:
            parts.append(f"nodes {int(self.nodes)}")
        if self.infinite:
            parts.append("infinite")
        return " ".join(parts) or "movetime 1000"

    @property
    def expected_seconds(self) -> float:
        """A generous upper bound, used as the protocol timeout."""
        if self.movetime_ms:
            return self.movetime_ms / 1000 * 4 + 20
        if self.wtime_ms or self.btime_ms:
            return max(self.wtime_ms, self.btime_ms) / 1000 + 30
        return 1800.0


def limit_for(tc: TimeControl, white_ms: int = 0, black_ms: int = 0) -> Limit:
    """Build the limit for one move under `tc`."""
    if tc.is_clock:
        return Limit(wtime_ms=max(1, white_ms), btime_ms=max(1, black_ms),
                     winc_ms=tc.inc_ms, binc_ms=tc.inc_ms,
                     movestogo=tc.movestogo)
    if tc.movetime_ms:
        return Limit(movetime_ms=tc.movetime_ms)
    if tc.depth:
        return Limit(depth=tc.depth)
    if tc.nodes:
        return Limit(nodes=tc.nodes)
    return Limit(movetime_ms=1000)


# --------------------------------------------------------------- search info


@dataclass
class SearchInfo:
    """What the engine said about one search (or one MultiPV line)."""

    depth: int = 0
    seldepth: int = 0
    multipv: int = 1
    nodes: int = 0
    nps: int = 0
    time_ms: int = 0
    hashfull: int = 0
    cp: Optional[int] = None        # from the moving side's point of view
    mate: Optional[int] = None      # mate distance in moves, same point of view
    bound: str = ""                 # "lower", "upper" or ""
    pv: list[chess.Move] = field(default_factory=list)
    text: str = ""

    @property
    def has_score(self) -> bool:
        return self.cp is not None or self.mate is not None

    @property
    def score_cp(self) -> int:
        """One number for the score, with mates mapped near +-MATE_CP."""
        if self.mate is not None:
            return (MATE_CP - abs(self.mate)) * (1 if self.mate > 0 else -1)
        return self.cp or 0

    @property
    def best_move(self) -> Optional[chess.Move]:
        return self.pv[0] if self.pv else None


@dataclass
class SearchResult:
    move: Optional[chess.Move] = None
    ponder: Optional[chess.Move] = None
    info: SearchInfo = field(default_factory=SearchInfo)
    ms: float = 0.0
    lines: list[SearchInfo] = field(default_factory=list)


# ------------------------------------------------------------------- engine


class UciEngine:
    """One engine process."""

    def __init__(self, spec: EngineSpec, *, echo: Optional[Callable[[str], None]] = None,
                 handshake_timeout: float = 30.0) -> None:
        self.spec = spec
        self.echo = echo
        self.handshake_timeout = handshake_timeout
        self.proc: Optional[subprocess.Popen] = None
        self.id_name = spec.name
        self.option_names: list[str] = []
        self.unsupported: list[str] = []
        self._lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        self._position_sent = False
        self._multipv = 1

    # ---- lifecycle ----

    def open(self) -> "UciEngine":
        path = self.spec.resolved_command()[0]
        if not self.spec.exists():
            raise EngineProblem(f"{self.spec.name}: no binary at {path}")
        try:
            self.proc = subprocess.Popen(
                self.spec.resolved_command(),
                cwd=self.spec.resolved_workdir(),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                # Merged, not discarded: Stockfish prints its `bench` summary
                # on stderr, and a crash message there is worth seeing too.
                # Unknown lines are ignored by the parsers anyway.
                stderr=subprocess.STDOUT,
                text=True, bufsize=1, encoding="utf-8", errors="replace",
                creationflags=_CREATION_FLAGS,
            )
        except OSError as exc:
            raise EngineProblem(f"{self.spec.name}: could not start {path}: {exc}") from exc
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        self._handshake()
        self.configure(self.spec.options)
        return self

    def close(self) -> None:
        if self.proc is None:
            return
        proc, self.proc = self.proc, None
        try:
            if proc.stdin is not None:
                proc.stdin.write("quit\n")
                proc.stdin.flush()
            proc.wait(timeout=5)
        except Exception:                                      # noqa: BLE001
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:                                  # noqa: BLE001
                pass

    def __enter__(self) -> "UciEngine":
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- raw io ----

    def _pump(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                self._lines.put(line.rstrip("\r\n"))
        except Exception:                                      # noqa: BLE001
            pass
        self._lines.put(None)

    def send(self, line: str) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise EngineProblem(f"{self.spec.name}: not running")
        if self.echo:
            self.echo(f"> {line}")
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except OSError as exc:
            raise EngineProblem(f"{self.spec.name}: died before {line!r} ({exc})") from exc

    def lines(self, timeout: float) -> Iterator[str]:
        """Yield output lines for at most `timeout` seconds in total."""
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return
            try:
                line = self._lines.get(timeout=left)
            except queue.Empty:
                return
            if line is None:
                raise EngineProblem(f"{self.spec.name}: exited unexpectedly")
            if self.echo:
                self.echo(f"< {line}")
            yield line

    def read_until(self, match: Callable[[str], bool], timeout: float,
                   what: str = "a reply") -> list[str]:
        collected: list[str] = []
        for line in self.lines(timeout):
            collected.append(line)
            if match(line):
                return collected
        raise EngineProblem(f"{self.spec.name}: timed out after {timeout:g}s "
                            f"waiting for {what}")

    # ---- protocol ----

    def _handshake(self) -> None:
        self.send("uci")
        for line in self.read_until(lambda l: l.strip() == "uciok",
                                    self.handshake_timeout, "uciok"):
            if line.startswith("id name "):
                self.id_name = line[8:].strip()
            elif line.startswith("option name "):
                name = line[12:]
                if " type " in name:
                    name = name.split(" type ", 1)[0]
                self.option_names.append(name.strip())

    def supports(self, option: str) -> bool:
        return any(option.lower() == name.lower() for name in self.option_names)

    def configure(self, options: dict[str, Any]) -> None:
        """Send the options the engine actually declares; remember the rest."""
        ordered = sorted(options.items(),
                         key=lambda kv: (_OPTION_ORDER.get(kv[0], 50), kv[0]))
        for key, value in ordered:
            match = next((n for n in self.option_names if n.lower() == key.lower()),
                         None)
            if match is None:
                self.unsupported.append(key)
                continue
            if match.lower() == "multipv":
                self._multipv = max(1, int(value))
            self.send(f"setoption name {match} value {_uci_value(value)}")
        self.wait_ready()

    def wait_ready(self, timeout: float = 120.0) -> None:
        self.send("isready")
        self.read_until(lambda l: l.strip() == "readyok", timeout, "readyok")

    def new_game(self) -> None:
        """Forget everything from the previous game or test position."""
        self.send("ucinewgame")
        self.wait_ready()

    def set_multipv(self, n: int) -> None:
        if n != self._multipv and self.supports("MultiPV"):
            self._multipv = max(1, n)
            self.send(f"setoption name MultiPV value {self._multipv}")
            self.wait_ready()

    def set_position(self, board: chess.Board) -> None:
        """Send the root position plus the real move list, so the engine can
        see repetitions."""
        root = board.root()
        moves = " ".join(m.uci() for m in board.move_stack)
        if root.fen() == chess.STARTING_FEN:
            head = "position startpos"
        else:
            head = f"position fen {root.fen()}"
        self.send(f"{head} moves {moves}" if moves else head)

    # ---- searching ----

    def search(self, board: chess.Board, limit: Limit) -> SearchResult:
        """Run one search and return the move together with what was reported."""
        self.set_position(board)
        timeout = limit.expected_seconds
        start = time.perf_counter()
        self.send(f"go {limit.go_args()}")

        best: Optional[SearchInfo] = None
        stats = SearchInfo()
        lines: dict[int, SearchInfo] = {}
        bestmove_line = ""

        for line in self.lines(timeout):
            if line.startswith("info "):
                info = _parse_info(line, board)
                if info is None:
                    continue
                if info.depth:
                    stats.depth = max(stats.depth, info.depth)
                if info.seldepth:
                    stats.seldepth = max(stats.seldepth, info.seldepth)
                if info.nodes:
                    stats.nodes = info.nodes
                if info.nps:
                    stats.nps = info.nps
                if info.time_ms:
                    stats.time_ms = info.time_ms
                if info.hashfull:
                    stats.hashfull = info.hashfull
                if info.has_score and info.pv:
                    lines[info.multipv] = info
                    if info.multipv == 1:
                        best = info
            elif line.startswith("bestmove"):
                bestmove_line = line
                break
        else:
            raise EngineProblem(f"{self.spec.name}: no bestmove within {timeout:g}s "
                                f"for `go {limit.go_args()}`")

        ms = (time.perf_counter() - start) * 1000
        move, ponder = _parse_bestmove(board, bestmove_line, self.spec.name)

        info = best or SearchInfo()
        info.depth = max(info.depth, stats.depth)
        info.seldepth = max(info.seldepth, stats.seldepth)
        info.nodes = info.nodes or stats.nodes
        info.nps = info.nps or stats.nps
        info.time_ms = info.time_ms or stats.time_ms
        info.hashfull = info.hashfull or stats.hashfull
        return SearchResult(move=move, ponder=ponder, info=info, ms=ms,
                            lines=[lines[k] for k in sorted(lines)])

    def analyse(self, board: chess.Board, limit: Limit,
                multipv: int = 1) -> SearchResult:
        """Search without caring about the move: used by the suites."""
        self.set_multipv(multipv)
        return self.search(board, limit)

    # ---- the non-standard commands ----

    def perft(self, board: chess.Board, depth: int, timeout: float = 1800.0) -> int:
        self.set_position(board)
        self.send(f"{self.spec.perft_command} {depth}")
        for line in self.lines(timeout):
            low = line.lower()
            if low.startswith("bestmove"):
                raise EngineProblem(
                    f"{self.spec.name}: does not understand "
                    f"{self.spec.perft_command!r} (it searched instead); set "
                    f"perft_command in suite.toml")
            if "nodes searched" in low or low.startswith("perft"):
                m = _NODES_RE.search(line)
                if m:
                    return int(m.group(1))
        raise EngineProblem(f"{self.spec.name}: no perft output within {timeout:g}s")

    def bench(self, depth: int, timeout: float = 900.0,
              echo: Optional[Callable[[str], None]] = None) -> dict[str, int]:
        """Run the engine's own benchmark; returns nodes, nps and milliseconds.

        Both ce.exe and Stockfish end with the same three summary lines, so
        one parser covers them -- but Stockfish writes them to stderr, which
        is why stderr is merged into the line stream.
        """
        self.send(f"{self.spec.bench_command} {depth}")
        out = {"nodes": 0, "nps": 0, "ms": 0}
        for line in self.lines(timeout):
            if echo and not line.startswith("info "):
                echo(line)
            low = line.lower()
            if "nodes searched" in low:
                out["nodes"] = _trailing_int(line)
            elif "nodes/second" in low:
                out["nps"] = _trailing_int(line)
                break
            elif "total time" in low:
                out["ms"] = _trailing_int(line)
        if not out["nps"] and out["ms"]:
            out["nps"] = int(out["nodes"] * 1000 / out["ms"])
        return out


# ------------------------------------------------------------------ parsing


def _uci_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _trailing_int(line: str) -> int:
    digits = re.findall(r"[0-9]+", line.replace(",", "").replace(".", ""))
    return int(digits[-1]) if digits else 0


def _parse_bestmove(board: chess.Board, line: str,
                    who: str) -> tuple[Optional[chess.Move], Optional[chess.Move]]:
    tokens = line.split()
    move: Optional[chess.Move] = None
    ponder: Optional[chess.Move] = None
    if len(tokens) >= 2 and tokens[1] not in ("(none)", "NULL", "0000", "none"):
        try:
            candidate = chess.Move.from_uci(tokens[1].lower())
        except ValueError as exc:
            raise IllegalMove(f"{who}: unreadable move {tokens[1]!r} ({exc})") from exc
        if candidate not in board.legal_moves:
            raise IllegalMove(f"{who}: illegal move {tokens[1]} in {board.fen()}")
        move = candidate
    if len(tokens) >= 4 and tokens[2] == "ponder":
        try:
            ponder = chess.Move.from_uci(tokens[3].lower())
        except ValueError:
            ponder = None
    return move, ponder


_INT_FIELDS = {"depth": "depth", "seldepth": "seldepth", "multipv": "multipv",
               "nodes": "nodes", "nps": "nps", "time": "time_ms",
               "hashfull": "hashfull"}


def _parse_info(line: str, board: chess.Board) -> Optional[SearchInfo]:
    tokens = line.split()
    info = SearchInfo(text=line)
    i = 1
    saw_anything = False
    while i < len(tokens):
        token = tokens[i]
        if token in _INT_FIELDS:
            if i + 1 >= len(tokens):
                break
            try:
                setattr(info, _INT_FIELDS[token], int(tokens[i + 1]))
                saw_anything = True
            except ValueError:
                pass
            i += 2
        elif token == "score":
            kind = tokens[i + 1] if i + 1 < len(tokens) else ""
            value = tokens[i + 2] if i + 2 < len(tokens) else ""
            try:
                if kind == "cp":
                    info.cp = int(value)
                elif kind == "mate":
                    info.mate = int(value)
            except ValueError:
                pass
            saw_anything = True
            i += 3
            while i < len(tokens) and tokens[i] in ("lowerbound", "upperbound"):
                info.bound = tokens[i][:5]
                i += 1
        elif token == "pv":
            scratch = board.copy(stack=False)
            for uci in tokens[i + 1:]:
                try:
                    move = chess.Move.from_uci(uci.lower())
                except ValueError:
                    break
                if move not in scratch.legal_moves:
                    break
                info.pv.append(move)
                scratch.push(move)
            saw_anything = True
            break
        elif token == "string":
            break
        else:
            i += 1
    return info if saw_anything else None

"""
Python bridge to the C chess engine in engine/.

The engine itself (move generation, evaluation, search) lives in engine/*.c and
is loaded here as a shared library through cffi.  This module only translates
between python-chess objects and the C API; it contains no chess logic.

Typical use:

    from engine_core import Engine
    eng = Engine()
    result = eng.search(board, wtime_ms=60000, btime_ms=60000, winc_ms=0, binc_ms=0)
    print(result.move, result.score_cp, result.depth)
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import Optional

import chess
from cffi import FFI

logger = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_DIR = os.path.join(HERE, "engine")
LIB_NAME = "ce.dll" if sys.platform == "win32" else "libce.so"
LIB_PATH = os.path.join(ENGINE_DIR, LIB_NAME)
# The perspective network built by nnue/.  The old single-perspective
# 769-256-1 net.bin is no longer a format the engine reads.
NET_PATH = os.path.join(ENGINE_DIR, "net2.bin")

C_SOURCES = ["bitboard.c", "position.c", "movegen.c", "eval.c", "nnue.c",
             "tt.c", "tune.c", "search.c", "api.c", "uci.c", "ce.h", "tune.h"]

EVAL_HCE, EVAL_NNUE, EVAL_BLEND = 0, 1, 2
EVAL_MODES = {"hce": EVAL_HCE, "nnue": EVAL_NNUE, "blend": EVAL_BLEND}

CDEF = """
typedef struct {
    char  bestmove[8];
    char  ponder[8];
    char  pv[1024];
    int   score;
    int   mate;
    int   depth;
    int   seldepth;
    int64_t nodes;
    int64_t timeMs;
    int   nps;
    int   hashfull;
    int64_t optimumMs;
    int64_t maximumMs;
} SearchResult;

const char *ce_version(void);
int  ce_init(const char *netPath, int hashMb, int threads);
void ce_set_hash(int mb);
void ce_set_threads(int n);
void ce_set_eval_mode(int mode);
int  ce_get_eval_mode(void);
int  ce_nnue_available(void);
void ce_set_show_info(int on);
void ce_set_multipv(int n);
void ce_new_game(void);
void ce_stop(void);
int  ce_set_position(const char *fen, const char *moves);
void ce_search(int64_t wtime, int64_t btime, int64_t winc, int64_t binc,
               int64_t movetime, int movestogo, int depth, int64_t nodes,
               int infinite, SearchResult *out);
int  ce_eval(void);
int  ce_set_param(const char *name, int value);
int  ce_get_param(const char *name, int *out);
int  ce_evalset_load(const char *path, int limit, int cap, int keepMates,
                     unsigned int seed);
int  ce_evalset_size(void);
int  ce_evalset_stats(double *corr, double *rms, double *fit);
int  ce_search_start(int64_t wtime, int64_t btime, int64_t winc, int64_t binc,
                     int64_t movetime, int movestogo, int depth, int64_t nodes,
                     int infinite);
int  ce_search_running(void);
int  ce_search_finished(void);
void ce_search_ponderhit(int64_t wtime, int64_t btime, int64_t winc,
                         int64_t binc, int64_t movetime, int movestogo);
void ce_search_join(SearchResult *out);
void ce_search_stop(SearchResult *out);
void ce_search_abort(void);
"""


def _needs_build() -> bool:
    if not os.path.isfile(LIB_PATH):
        return True
    lib_mtime = os.path.getmtime(LIB_PATH)
    for name in C_SOURCES:
        path = os.path.join(ENGINE_DIR, name)
        if os.path.isfile(path) and os.path.getmtime(path) > lib_mtime:
            return True
    return False


def build(force: bool = False) -> None:
    """Compile the engine if the shared library is missing or out of date."""
    if not force and not _needs_build():
        return
    script = os.path.join(ENGINE_DIR, "build.py")
    logger.info("building the chess engine (%s)...", LIB_NAME)
    proc = subprocess.run([sys.executable, script], cwd=ENGINE_DIR,
                          capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise RuntimeError("engine build failed; see the output above")


def _ensure_net() -> None:
    """Quantise the trained network into net2.bin if it is not there yet.

    The export needs torch, which the bot itself does not; when that is not
    available the engine simply runs without a network and `EvalMode` falls
    back to the hand-crafted evaluation.  Run `nnue/export.py` by hand from
    the training environment to produce net2.bin.
    """
    if os.path.isfile(NET_PATH):
        return
    trained = os.path.join(HERE, "nnue", "net.pt")
    script = os.path.join(HERE, "nnue", "export.py")
    if not (os.path.isfile(trained) and os.path.isfile(script)):
        return
    try:
        subprocess.run([sys.executable, script], cwd=HERE, capture_output=True,
                       text=True, check=True)
    except Exception as exc:                       # noqa: BLE001
        logger.info("no network exported (%s); using the hand-crafted "
                    "evaluation", exc)


@dataclass
class SearchInfo:
    """Result of one search."""

    move: Optional[chess.Move] = None
    ponder: Optional[chess.Move] = None
    pv: list[chess.Move] = field(default_factory=list)
    score_cp: int = 0
    mate: int = 0
    depth: int = 0
    seldepth: int = 0
    nodes: int = 0
    time_ms: int = 0
    nps: int = 0
    hashfull: int = 0
    optimum_ms: int = 0
    maximum_ms: int = 0


class Engine:
    """A single instance of the C engine (the C side holds global state)."""

    _lock = threading.Lock()
    _ffi: Optional[FFI] = None
    _lib = None

    def __init__(self, hash_mb: int = 256, threads: Optional[int] = None,
                 eval_mode: str = "hce", auto_build: bool = True) -> None:
        if threads is None:
            cpus = os.cpu_count() or 1
            threads = max(1, cpus - 1)
        self.threads = threads
        self.hash_mb = hash_mb

        if auto_build:
            build()
        _ensure_net()
        self._load()

        with Engine._lock:
            net_ok = self._lib.ce_init(NET_PATH.encode(), hash_mb, threads)
            self.nnue_available = bool(self._lib.ce_nnue_available())
            self._lib.ce_set_show_info(0)
            self.set_eval_mode(eval_mode)
        logger.info("engine %s ready: %d threads, %d MB hash, net %s, eval %s",
                    self.version, threads, hash_mb,
                    "loaded" if net_ok else "unavailable", eval_mode)

    # ---- library loading ----

    @classmethod
    def _load(cls) -> None:
        if cls._lib is not None:
            return
        ffi = FFI()
        ffi.cdef(CDEF)
        cls._lib = ffi.dlopen(LIB_PATH)
        cls._ffi = ffi

    @property
    def version(self) -> str:
        return self._ffi.string(self._lib.ce_version()).decode()

    # ---- configuration ----

    def set_eval_mode(self, mode: str) -> str:
        wanted = EVAL_MODES.get(mode.lower(), EVAL_HCE)
        self._lib.ce_set_eval_mode(wanted)
        got = self._lib.ce_get_eval_mode()
        self.eval_mode = next(k for k, v in EVAL_MODES.items() if v == got)
        return self.eval_mode

    def set_threads(self, n: int) -> None:
        self.threads = max(1, n)
        self._lib.ce_set_threads(self.threads)

    def set_hash(self, mb: int) -> None:
        self.hash_mb = max(1, mb)
        self._lib.ce_set_hash(self.hash_mb)

    def new_game(self) -> None:
        with Engine._lock:
            self._lib.ce_new_game()

    def stop(self) -> None:
        self._lib.ce_stop()

    # ---- position ----

    def _set_position(self, board: chess.Board) -> None:
        """Send the root position plus the real move history (for repetitions)."""
        root = board.root()
        moves = " ".join(m.uci() for m in board.move_stack)
        if not self._lib.ce_set_position(root.fen().encode(), moves.encode()):
            # fall back to the current position without history
            if not self._lib.ce_set_position(board.fen().encode(), b""):
                raise ValueError(f"engine rejected position {board.fen()}")

    # ---- search ----

    def search(self, board: chess.Board, *,
               wtime_ms: int = 0, btime_ms: int = 0,
               winc_ms: int = 0, binc_ms: int = 0,
               movetime_ms: int = 0, movestogo: int = 0,
               depth: int = 0, nodes: int = 0,
               show_info: bool = False) -> SearchInfo:
        """Search `board` and return the chosen move with search statistics."""
        res = self._ffi.new("SearchResult *")
        with Engine._lock:
            # A background search left running would be using the same thread
            # pool and the same transposition table as this one, so it has to
            # go first.  Normally the ponder logic has already dealt with it;
            # this covers the paths that have not heard of pondering at all.
            if self._lib.ce_search_running():
                self._lib.ce_search_abort()
            self._set_position(board)
            self._lib.ce_set_show_info(1 if show_info else 0)
            self._lib.ce_search(int(wtime_ms), int(btime_ms), int(winc_ms),
                                int(binc_ms), int(movetime_ms), int(movestogo),
                                int(depth), int(nodes), 0, res)
        return self._result(board, res)

    # ---- pondering ----

    def ponder_start(self, board: chess.Board) -> bool:
        """Begin an unlimited search on `board`, on a thread of its own.

        Used to think on the opponent's clock: `board` is the position that
        follows the reply we expect.  Nothing about it is binding -- if the
        opponent plays something else the search is thrown away, and the work
        it did is still in the transposition table.
        """
        with Engine._lock:
            if self._lib.ce_search_running():
                return False
            self._set_position(board)
            self._lib.ce_set_show_info(0)
            return bool(self._lib.ce_search_start(0, 0, 0, 0, 0, 0, 0, 0, 1))

    def ponder_running(self) -> bool:
        return bool(self._lib.ce_search_running())

    def ponder_hit(self, board: chess.Board, *,
                   wtime_ms: int = 0, btime_ms: int = 0,
                   winc_ms: int = 0, binc_ms: int = 0,
                   movetime_ms: int = 0, movestogo: int = 0) -> SearchInfo:
        """Hand the running ponder search a clock and wait for its answer.

        The position is not re-sent: the point is that the search already has
        a tree for it.  `board` is only needed to turn the moves it returns
        back into python-chess objects.
        """
        res = self._ffi.new("SearchResult *")
        with Engine._lock:
            self._lib.ce_search_ponderhit(int(wtime_ms), int(btime_ms),
                                          int(winc_ms), int(binc_ms),
                                          int(movetime_ms), int(movestogo))
            self._lib.ce_search_join(res)
        return self._result(board, res)

    def ponder_abort(self) -> None:
        """Throw away a running ponder search; the table keeps what it found."""
        with Engine._lock:
            if self._lib.ce_search_running():
                self._lib.ce_search_abort()

    # ---- results ----

    def _result(self, board: chess.Board, res) -> SearchInfo:
        info = SearchInfo(
            score_cp=res.score,
            mate=res.mate,
            depth=res.depth,
            seldepth=res.seldepth,
            nodes=int(res.nodes),
            time_ms=int(res.timeMs),
            nps=res.nps,
            hashfull=res.hashfull,
            optimum_ms=int(res.optimumMs),
            maximum_ms=int(res.maximumMs),
        )

        uci = self._ffi.string(res.bestmove).decode()
        if uci and uci != "0000":
            try:
                info.move = board.parse_uci(uci)
            except ValueError:
                logger.error("engine returned an illegal move %s in %s", uci, board.fen())
                info.move = None

        pv_text = self._ffi.string(res.pv).decode().split()
        scratch = board.copy(stack=False)
        for token in pv_text:
            try:
                move = scratch.parse_uci(token)
            except ValueError:
                break
            info.pv.append(move)
            scratch.push(move)
        # Prefer the engine's own answer: when the principal variation has no
        # second move it falls back to the transposition table, which the
        # PV here cannot do.
        ponder_uci = self._ffi.string(res.ponder).decode()
        if ponder_uci and ponder_uci != "0000":
            try:
                scratch = board.copy(stack=False)
                if info.move is not None:
                    scratch.push(info.move)
                info.ponder = scratch.parse_uci(ponder_uci)
            except ValueError:
                info.ponder = None
        if info.ponder is None and len(info.pv) > 1:
            info.ponder = info.pv[1]

        if info.move is None:
            legal = list(board.legal_moves)
            if legal:
                info.move = info.pv[0] if info.pv else legal[0]
        return info

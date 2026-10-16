"""Playing games: one game, a match, a gauntlet.

The clock is kept here rather than trusted to the engines: after every move the
measured wall-clock time is taken off the mover's clock, so an engine that
thinks for too long loses on time exactly as it would on lichess.  That makes
the match suite a time-management test as well as a strength test.
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

import chess

from . import elo as elolib
from .config import Adjudication, Config, EngineSpec, TimeControl
from .engines import EngineProblem, UciEngine, limit_for
from .openings import OpeningBook, pgn_from_game


@dataclass
class SideStats:
    name: str
    moves: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    depth_sum: int = 0
    nodes: int = 0
    clock_left_ms: float = 0.0

    @property
    def avg_depth(self) -> float:
        return self.depth_sum / self.moves if self.moves else 0.0

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.moves if self.moves else 0.0


@dataclass
class GameResult:
    index: int
    white: str
    black: str
    engine_white: bool
    result: str = "*"             # "1-0" / "0-1" / "1/2-1/2"
    reason: str = ""
    plies: int = 0
    opening: str = ""
    engine: Optional[SideStats] = None
    opponent: Optional[SideStats] = None
    error: str = ""
    pgn: str = ""

    @property
    def points(self) -> float:
        """Score for the engine under test."""
        if self.result == "1/2-1/2":
            return 0.5
        if self.result == "1-0":
            return 1.0 if self.engine_white else 0.0
        if self.result == "0-1":
            return 0.0 if self.engine_white else 1.0
        return 0.0

    @property
    def outcome_letter(self) -> str:
        return {1.0: "W", 0.5: "D", 0.0: "L"}[self.points]


@dataclass
class MatchOutcome:
    engine: str
    opponent: str
    opponent_elo: Optional[float]
    time_control: str
    wins: int = 0
    draws: int = 0
    losses: int = 0
    games: list[GameResult] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)
    seconds: float = 0.0
    pgn_path: str = ""

    @property
    def stats(self) -> elolib.MatchStats:
        return elolib.match_result(self.wins, self.draws, self.losses)

    def as_opponent_result(self) -> elolib.OpponentResult:
        return elolib.OpponentResult(self.opponent, self.opponent_elo or 0.0,
                                     self.wins, self.draws, self.losses)

    def to_dict(self) -> dict[str, object]:
        stats = self.stats
        return {
            "engine": self.engine,
            "opponent": self.opponent,
            "opponent_elo": self.opponent_elo,
            "time_control": self.time_control,
            "wins": self.wins,
            "draws": self.draws,
            "losses": self.losses,
            "games": len(self.games),
            "score": round(stats.score, 4),
            "elo_diff": None if stats.games == 0 or stats.elo in (float("inf"), float("-inf"))
                        else round(stats.elo, 1),
            "los": round(stats.los, 4),
            "seconds": round(self.seconds, 1),
            "anomalies": self.anomalies,
            "results": [
                {"index": g.index, "result": g.result, "reason": g.reason,
                 "engine_white": g.engine_white, "plies": g.plies,
                 "opening": g.opening, "error": g.error}
                for g in self.games
            ],
        }


# ----------------------------------------------------------------- one game


class GameRunner:
    """Two engine processes, reused for a sequence of games."""

    def __init__(self, engine_spec: EngineSpec, opponent_spec: EngineSpec,
                 tc: TimeControl, adj: Adjudication, book: OpeningBook,
                 move_overhead_ms: int = 100, keep_pgn: bool = True) -> None:
        self.engine_spec = engine_spec
        self.opponent_spec = opponent_spec
        self.tc = tc
        self.adj = adj
        self.book = book
        self.move_overhead_ms = move_overhead_ms
        self.keep_pgn = keep_pgn
        self.engine: Optional[UciEngine] = None
        self.opponent: Optional[UciEngine] = None

    # ---- processes ----

    def open(self) -> "GameRunner":
        self.engine = UciEngine(self.engine_spec).open()
        self.opponent = UciEngine(self.opponent_spec).open()
        return self

    def close(self) -> None:
        for handle in (self.engine, self.opponent):
            if handle is not None:
                handle.close()
        self.engine = self.opponent = None

    def __enter__(self) -> "GameRunner":
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _restart(self) -> None:
        """After a crash, start both processes again for the next game."""
        self.close()
        self.open()

    # ---- the game loop ----

    def play(self, index: int) -> GameResult:
        assert self.engine is not None and self.opponent is not None
        opening = self.book.for_round(index)
        engine_white = self.book.engine_plays_white(index)

        white = self.engine if engine_white else self.opponent
        black = self.opponent if engine_white else self.engine
        sides: dict[chess.Color, UciEngine] = {chess.WHITE: white, chess.BLACK: black}
        specs = {chess.WHITE: white.spec, chess.BLACK: black.spec}
        stats = {chess.WHITE: SideStats(white.spec.name),
                 chess.BLACK: SideStats(black.spec.name)}

        out = GameResult(index=index, white=white.spec.name, black=black.spec.name,
                         engine_white=engine_white, opening=opening.label)

        board = opening.board()
        start_plies = len(board.move_stack)
        clock = {chess.WHITE: float(self.tc.base_ms), chess.BLACK: float(self.tc.base_ms)}
        for colour in (chess.WHITE, chess.BLACK):
            stats[colour].clock_left_ms = clock[colour]

        own_scores: dict[chess.Color, list[int]] = {chess.WHITE: [], chess.BLACK: []}
        white_scores: list[int] = []
        for handle in (white, black):
            handle.new_game()

        while True:
            if board.is_game_over(claim_draw=True):
                outcome = board.outcome(claim_draw=True)
                out.result = board.result(claim_draw=True)
                out.reason = outcome.termination.name.lower().replace("_", " ") if outcome else "game over"
                break

            if len(board.move_stack) - start_plies >= self.adj.max_plies:
                out.result, out.reason = "1/2-1/2", "move limit"
                break

            side = board.turn
            handle = sides[side]
            side_tc = specs[side].time_control or self.tc
            limit = limit_for(side_tc, int(clock[chess.WHITE]), int(clock[chess.BLACK]))

            try:
                result = handle.search(board, limit)
            except EngineProblem as exc:
                out.result = "0-1" if side == chess.WHITE else "1-0"
                out.reason = "illegal move or protocol error"
                out.error = f"{handle.spec.name}: {exc}"
                self._restart()
                break

            info = result.info
            spent_ms = result.ms
            stat = stats[side]
            stat.moves += 1
            stat.total_ms += spent_ms
            stat.max_ms = max(stat.max_ms, spent_ms)
            stat.depth_sum += info.depth
            stat.nodes += info.nodes

            if side_tc.is_clock:
                clock[side] -= spent_ms
                if clock[side] < -self.move_overhead_ms:
                    out.result = "0-1" if side == chess.WHITE else "1-0"
                    out.reason = "time forfeit"
                    out.error = (f"{handle.spec.name} used {spent_ms:.0f} ms with "
                                 f"{clock[side] + spent_ms:.0f} ms left")
                    stat.clock_left_ms = clock[side]
                    break
                clock[side] = max(0.0, clock[side]) + side_tc.inc_ms
            stat.clock_left_ms = clock[side]

            if not result.move:
                out.result = "0-1" if side == chess.WHITE else "1-0"
                out.reason = "no move returned"
                out.error = f"{handle.spec.name} returned no move"
                break

            if info.has_score:
                cp = info.score_cp
                own_scores[side].append(cp)
                white_scores.append(cp if side == chess.WHITE else -cp)
            board.push(result.move)

            verdict = self._adjudicate(board, start_plies, own_scores, white_scores)
            if verdict:
                out.result, out.reason = verdict
                break

        out.plies = len(board.move_stack) - start_plies
        out.engine = stats[chess.WHITE] if engine_white else stats[chess.BLACK]
        out.opponent = stats[chess.BLACK] if engine_white else stats[chess.WHITE]
        if self.keep_pgn:
            out.pgn = pgn_from_game(board, {
                "Event": "testsuite",
                "White": white.spec.name,
                "Black": black.spec.name,
                "Result": out.result,
                "Round": str(index + 1),
                "TimeControl": self.tc.label,
                "Opening": opening.label,
                "Termination": out.reason,
                "PlyCount": str(out.plies),
            })
        out.reason = out.reason or "unknown"
        return out

    def _adjudicate(self, board: chess.Board, start_plies: int,
                    own_scores: dict[chess.Color, list[int]],
                    white_scores: list[int]) -> Optional[tuple[str, str]]:
        adj = self.adj
        ply = len(board.move_stack) - start_plies

        if adj.resign_enabled and adj.resign_plies > 0:
            for colour in (chess.WHITE, chess.BLACK):
                tail = own_scores[colour][-adj.resign_plies:]
                if (len(tail) == adj.resign_plies
                        and all(s <= -adj.resign_score for s in tail)):
                    return ("0-1" if colour == chess.WHITE else "1-0",
                            f"{'white' if colour == chess.WHITE else 'black'} resigns")

        if (adj.draw_enabled and adj.draw_plies > 0 and ply >= adj.draw_after_ply):
            tail = white_scores[-adj.draw_plies:]
            if len(tail) == adj.draw_plies and all(abs(s) <= adj.draw_score for s in tail):
                return ("1/2-1/2", "adjudicated draw")
        return None


# -------------------------------------------------------------------- match


ProgressFn = Callable[[GameResult, "MatchOutcome"], None]


def run_match(cfg: Config, engine_spec: EngineSpec, opponent_spec: EngineSpec,
              games: int, tc: Optional[TimeControl] = None,
              book: Optional[OpeningBook] = None,
              concurrency: int = 1,
              progress: Optional[ProgressFn] = None,
              pgn_path: str = "",
              stop_when: Optional[Callable[["MatchOutcome"], bool]] = None) -> MatchOutcome:
    """Play `games` games; colours alternate and openings come in pairs."""
    tc = tc or cfg.time_control
    book = book or OpeningBook.single()
    concurrency = max(1, min(concurrency, games))

    out = MatchOutcome(engine=engine_spec.name, opponent=opponent_spec.name,
                       opponent_elo=opponent_spec.elo, time_control=tc.label,
                       pgn_path=pgn_path)
    lock = threading.Lock()
    stop = threading.Event()
    start = time.perf_counter()
    pgn_file = None
    if pgn_path:
        os.makedirs(os.path.dirname(pgn_path) or ".", exist_ok=True)
        pgn_file = open(pgn_path, "a", encoding="utf-8")

    def record(game: GameResult) -> None:
        with lock:
            out.games.append(game)
            points = game.points
            if points == 1.0:
                out.wins += 1
            elif points == 0.5:
                out.draws += 1
            else:
                out.losses += 1
            if game.error:
                out.anomalies.append(f"game {game.index + 1}: {game.reason} -- {game.error}")
            if pgn_file and game.pgn:
                pgn_file.write(game.pgn)
                pgn_file.flush()
            if progress:
                progress(game, out)
            if stop_when and stop_when(out):
                stop.set()

    def worker(indices: list[int]) -> None:
        runner = GameRunner(engine_spec, opponent_spec, tc, cfg.adjudication, book,
                            cfg.move_overhead_ms, keep_pgn=bool(pgn_path))
        try:
            runner.open()
        except EngineProblem as exc:
            with lock:
                out.anomalies.append(str(exc))
            stop.set()
            return
        try:
            for index in indices:
                if stop.is_set():
                    return
                try:
                    record(runner.play(index))
                except EngineProblem as exc:
                    with lock:
                        out.anomalies.append(f"game {index + 1}: {exc}")
                    try:
                        runner._restart()
                    except EngineProblem:
                        stop.set()
                        return
        finally:
            runner.close()

    # Keep colour pairs (2i, 2i+1) in the same worker so a pair is never split
    # across a stopped run with one colour missing.
    pairs = [[2 * i, 2 * i + 1] for i in range(games // 2)]
    if games % 2:
        pairs.append([games - 1])
    buckets: list[list[int]] = [[] for _ in range(concurrency)]
    for i, pair in enumerate(pairs):
        buckets[i % concurrency].extend(pair)
    buckets = [b for b in buckets if b]

    try:
        if len(buckets) == 1:
            worker(buckets[0])
        else:
            with ThreadPoolExecutor(max_workers=len(buckets)) as pool:
                list(pool.map(worker, buckets))
    finally:
        if pgn_file:
            pgn_file.close()

    out.games.sort(key=lambda g: g.index)
    out.seconds = time.perf_counter() - start
    return out


# ------------------------------------------------------------------ gauntlet


@dataclass
class Gauntlet:
    engine: str
    time_control: str
    matches: list[MatchOutcome] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def wins(self) -> int:
        return sum(m.wins for m in self.matches)

    @property
    def draws(self) -> int:
        return sum(m.draws for m in self.matches)

    @property
    def losses(self) -> int:
        return sum(m.losses for m in self.matches)

    @property
    def games(self) -> int:
        return self.wins + self.draws + self.losses

    def opponent_results(self) -> list[elolib.OpponentResult]:
        return elolib.merge_results(m.as_opponent_result() for m in self.matches
                                    if m.opponent_elo is not None)

    def rating(self, confidence: float = 0.95) -> elolib.RatingEstimate:
        return elolib.fit_rating(self.opponent_results(), confidence)

    def to_dict(self) -> dict[str, object]:
        rating = self.rating()
        return {
            "engine": self.engine,
            "time_control": self.time_control,
            "games": self.games,
            "wins": self.wins,
            "draws": self.draws,
            "losses": self.losses,
            "seconds": round(self.seconds, 1),
            "rating": {
                "elo": round(rating.elo, 1),
                "stderr": round(rating.stderr, 1) if rating.stderr != float("inf") else None,
                "low": round(rating.low, 1),
                "high": round(rating.high, 1),
                "bounded": rating.bounded,
                "games": rating.games,
            },
            "matches": [m.to_dict() for m in self.matches],
        }


def run_gauntlet(cfg: Config, engine_spec: EngineSpec, opponents: list[EngineSpec],
                 games_per_opponent: int, tc: Optional[TimeControl] = None,
                 book: Optional[OpeningBook] = None, concurrency: int = 1,
                 progress: Optional[ProgressFn] = None,
                 on_match: Optional[Callable[[MatchOutcome], None]] = None,
                 pgn_path: str = "") -> Gauntlet:
    tc = tc or cfg.time_control
    out = Gauntlet(engine=engine_spec.name, time_control=tc.label)
    start = time.perf_counter()
    for opponent in opponents:
        match = run_match(cfg, engine_spec, opponent, games_per_opponent, tc, book,
                          concurrency, progress, pgn_path)
        out.matches.append(match)
        if on_match:
            on_match(match)
    out.seconds = time.perf_counter() - start
    return out

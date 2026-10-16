"""The standard engine benchmarks.

* ``perft``       -- move generator correctness against known node counts.
* ``bench``       -- the engine's own fixed-depth benchmark: nodes per second,
                     and the node count itself, which is the number to watch
                     when a search change is meant to prune more.
* ``speed``       -- depth reached in a fixed time, per position.
* ``ttd``         -- time and nodes needed to reach a fixed depth.
* ``tactics``     -- positions with a known best move.
* ``mates``       -- forced mates, checked against the reported mate distance.
* ``draws``       -- positions whose true value is a draw (or is not).
* ``determinism`` -- the same search twice must give the same answer.
* ``timing``      -- the engine must never exceed the clock it was given.

Every runner streams its rows as it goes, because a full suite takes minutes.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

import chess

from .config import Adjudication, Config, EngineSpec, TimeControl, parse_time_control
from .engines import SearchInfo, UciEngine, limit_for
from .epd import EpdRecord

Log = Callable[[str], None]


@dataclass
class SuiteReport:
    name: str
    engine: str
    rows: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    failures: int = 0
    seconds: float = 0.0
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.failures == 0

    def to_dict(self) -> dict:
        return {"suite": self.name, "engine": self.engine, "failures": self.failures,
                "seconds": round(self.seconds, 2), "summary": self.summary,
                "rows": self.rows, "note": self.note}


def _analyse(engine: UciEngine, board: chess.Board, tc: TimeControl,
             multipv: int = 1) -> tuple[SearchInfo, float]:
    """One search from a clean hash table; returns the info and the wall time."""
    engine.new_game()
    t0 = time.perf_counter()
    result = engine.analyse(board, limit_for(tc), multipv=multipv)
    return result.info, (time.perf_counter() - t0) * 1000


# -------------------------------------------------------------------- perft


def run_perft(spec: EngineSpec, records: Sequence[EpdRecord], *,
              max_depth: int = 0, max_nodes: int = 0, timeout: float = 3600.0,
              log: Log = print) -> SuiteReport:
    """Compare the engine's perft against the counts stored in the suite."""
    report = SuiteReport("perft", spec.name)
    start = time.perf_counter()
    total_nodes = 0
    total_time = 0.0

    log(f"  {'depth':>5} {'nodes':>15} {'expected':>15} {'Mnps':>6}  result  position")
    with UciEngine(spec) as engine:
        for record in records:
            for depth in sorted(record.perft):
                expected = record.perft[depth]
                if max_depth and depth > max_depth:
                    continue
                if max_nodes and expected > max_nodes:
                    continue
                t0 = time.perf_counter()
                got = engine.perft(record.board, depth, timeout)
                dt = time.perf_counter() - t0
                total_nodes += got
                total_time += dt
                ok = got == expected
                report.failures += not ok
                report.rows.append({"fen": record.fen, "depth": depth, "nodes": got,
                                    "expected": expected, "ok": ok,
                                    "seconds": round(dt, 3)})
                log(f"  {depth:>5} {got:>15,} {expected:>15,} "
                    f"{got / dt / 1e6 if dt else 0:>6.1f}  "
                    f"{'OK  ' if ok else 'FAIL'}    {record.fen[:44]}")

    report.seconds = time.perf_counter() - start
    mnps = total_nodes / total_time / 1e6 if total_time else 0.0
    report.summary = {"positions": len(report.rows), "nodes": total_nodes,
                      "mnps": round(mnps, 2)}
    log(f"\n  {len(report.rows)} tests, {total_nodes:,} nodes, {mnps:.1f} Mnps -- "
        f"{'all correct' if report.ok else f'{report.failures} MISMATCHES'}")
    return report


# -------------------------------------------------------------------- bench


def run_bench(spec: EngineSpec, depth: int = 13, timeout: float = 3600.0,
              log: Log = print) -> SuiteReport:
    """Run the engine's own fixed-depth benchmark and read off nodes and nps."""
    report = SuiteReport("bench", spec.name)
    start = time.perf_counter()
    with UciEngine(spec) as engine:
        out = engine.bench(depth, timeout, echo=lambda line: log(f"  {line}"))
    report.seconds = time.perf_counter() - start
    report.summary = {"depth": depth, **out}
    if not out["nodes"]:
        report.failures = 1
        report.note = f"could not read the output of {spec.bench_command!r}"
        log(f"\n  could not read {spec.bench_command!r} output -- "
            f"check bench_command in suite.toml")
    else:
        log(f"\n  depth {depth}: {out['nodes']:,} nodes at {out['nps']:,} nps")
    return report


# -------------------------------------------------- depth in a fixed time, ttd


def run_speed(spec: EngineSpec, records: Sequence[EpdRecord], *,
              tc: Optional[TimeControl] = None, log: Log = print) -> SuiteReport:
    """How deep the engine gets in a fixed time per position."""
    tc = tc or parse_time_control("5s")
    report = SuiteReport("speed", spec.name)
    start = time.perf_counter()
    depths: list[int] = []
    nodes_total = 0
    ms_total = 0.0

    log(f"  {tc.label} per position\n")
    log(f"  {'depth':>5} {'seldep':>6} {'score':>7} {'nodes':>13} {'knps':>8}  position")
    with UciEngine(spec) as engine:
        for record in records:
            info, ms = _analyse(engine, record.board, tc)
            depths.append(info.depth)
            nodes_total += info.nodes
            ms_total += ms
            report.rows.append({"fen": record.fen, "depth": info.depth,
                                "seldepth": info.seldepth, "score": info.score_cp,
                                "nodes": info.nodes, "ms": round(ms, 1)})
            log(f"  {info.depth:>5} {info.seldepth:>6} {info.score_cp:>7} "
                f"{info.nodes:>13,} {info.nodes / ms if ms else 0:>8.0f}  "
                f"{record.fen[:42]}")

    report.seconds = time.perf_counter() - start
    report.summary = {
        "time_control": tc.label,
        "min_depth": min(depths) if depths else 0,
        "avg_depth": round(sum(depths) / len(depths), 2) if depths else 0,
        "max_depth": max(depths) if depths else 0,
        "nodes": nodes_total,
        "knps": round(nodes_total / ms_total, 1) if ms_total else 0,
    }
    log(f"\n  depth min {report.summary['min_depth']} "
        f"avg {report.summary['avg_depth']} max {report.summary['max_depth']}, "
        f"{report.summary['knps']:,.0f} knps overall")
    return report


def run_time_to_depth(spec: EngineSpec, records: Sequence[EpdRecord], depth: int = 12,
                      log: Log = print) -> SuiteReport:
    """Time and nodes needed to finish a fixed depth: the A/B regression test."""
    report = SuiteReport("ttd", spec.name)
    start = time.perf_counter()
    tc = TimeControl(depth=depth)
    nodes_total = 0
    ms_total = 0.0

    log(f"  fixed depth {depth}\n")
    log(f"  {'ms':>9} {'nodes':>13} {'knps':>8} {'score':>7}  position")
    with UciEngine(spec) as engine:
        for record in records:
            info, ms = _analyse(engine, record.board, tc)
            nodes_total += info.nodes
            ms_total += ms
            report.rows.append({"fen": record.fen, "nodes": info.nodes,
                                "ms": round(ms, 1), "score": info.score_cp})
            log(f"  {ms:>9,.0f} {info.nodes:>13,} "
                f"{info.nodes / ms if ms else 0:>8.0f} {info.score_cp:>7}  "
                f"{record.fen[:42]}")

    report.seconds = time.perf_counter() - start
    report.summary = {"depth": depth, "nodes": nodes_total, "ms": round(ms_total, 1),
                      "knps": round(nodes_total / ms_total, 1) if ms_total else 0}
    log(f"\n  depth {depth}: {nodes_total:,} nodes in {ms_total / 1000:.1f}s "
        f"({report.summary['knps']:,.0f} knps)")
    log("  when judging a search change, compare the node count, not the time")
    return report


# ------------------------------------------------------------------ tactics


def run_tactics(spec: EngineSpec, records: Sequence[EpdRecord], *,
                tc: Optional[TimeControl] = None, name: str = "tactics",
                log: Log = print) -> SuiteReport:
    """Count the positions where the engine finds the known best move."""
    tc = tc or parse_time_control("3s")
    report = SuiteReport(name, spec.name)
    start = time.perf_counter()
    solved = 0
    scored = 0
    depth_sum = 0

    log(f"  {tc.label} per position\n")
    with UciEngine(spec) as engine:
        for record in records:
            if not (record.best_moves or record.avoid_moves
                    or record.mate_in is not None):
                continue
            scored += 1
            engine.new_game()
            result = engine.search(record.board, limit_for(tc))
            info = result.info
            depth_sum += info.depth

            ok = record.accepts(result.move)
            if record.mate_in is not None and not record.best_moves:
                ok = info.mate is not None and info.mate == record.mate_in
            solved += ok
            played = record.board.san(result.move) if result.move else "-"
            want = record.best_san() or (f"mate in {record.mate_in}"
                                         if record.mate_in is not None
                                         else f"not {record.avoid_san()}")
            report.rows.append({"id": record.label, "fen": record.fen,
                                "played": played, "want": want, "ok": bool(ok),
                                "depth": info.depth, "ms": round(result.ms, 1),
                                "mate": info.mate})
            found = (f" (found mate in {info.mate})"
                     if record.mate_in is not None and info.mate is not None
                     and info.mate != record.mate_in else "")
            log(f"  {'OK  ' if ok else 'miss'} {record.label:<13} played {played:<8} "
                f"want {want:<16} depth {info.depth:>2}  "
                f"{record.fen[:32]}{found}")

    report.seconds = time.perf_counter() - start
    report.failures = scored - solved
    report.summary = {"solved": solved, "total": scored,
                      "percent": round(100 * solved / scored, 1) if scored else 0,
                      "avg_depth": round(depth_sum / scored, 1) if scored else 0,
                      "time_control": tc.label}
    log(f"\n  solved {solved}/{scored} ({report.summary['percent']:.0f}%) "
        f"at {tc.label}, average depth {report.summary['avg_depth']}")
    return report


# -------------------------------------------------------------------- draws


def run_draws(spec: EngineSpec, records: Sequence[EpdRecord], *,
              depth: int = 10, log: Log = print) -> SuiteReport:
    """Dead-drawn positions must evaluate to exactly zero, and the rest must not.

    The expected verdict is the first word of the ``c0`` comment, which was
    written by the referee engine when the suite was generated.
    """
    report = SuiteReport("draws", spec.name)
    start = time.perf_counter()
    tc = TimeControl(depth=depth)

    with UciEngine(spec) as engine:
        for record in records:
            expect_draw = record.comment.lower().startswith("draw")
            want = "draw" if expect_draw else "not a draw"

            if not record.board.legal_moves:
                # Checkmate or stalemate: there is nothing to evaluate, and the
                # only thing the engine can get wrong is inventing a move.
                # Positions that are drawn but still have legal moves (bare
                # kings, the fifty-move rule) go through the normal path.
                engine.new_game()
                result = engine.search(record.board, limit_for(tc))
                ok = result.move is None
                got = "no move" if ok else f"played {result.move}"
                cp = None
            else:
                info, _ = _analyse(engine, record.board, tc)
                cp = info.score_cp if info.has_score else None
                ok = (cp == 0) == expect_draw
                got = "draw" if cp == 0 else "not a draw"

            report.failures += not ok
            report.rows.append({"id": record.label, "fen": record.fen, "score": cp,
                                "expected": want, "got": got, "ok": ok})
            log(f"  {'OK  ' if ok else 'FAIL'} score {str(cp):>7}  {got:<13} "
                f"expected {want:<11} {record.comment or record.label}")

    report.seconds = time.perf_counter() - start
    report.summary = {"positions": len(report.rows), "depth": depth}
    log(f"\n  {'all draw verdicts correct' if report.ok else f'{report.failures} WRONG'}")
    return report


# ----------------------------------------------------------- time management


def run_timing(cfg: Config, spec: EngineSpec,
               controls: Optional[Sequence[TimeControl]] = None,
               max_plies: int = 200, log: Log = print) -> SuiteReport:
    """Self-play on a real clock: the engine must never flag.

    A single move may also not take more than half of the clock it had left,
    which is the rule that actually matters on lichess -- one long iteration
    late in a fast game is what loses games on time.
    """
    from .match import GameRunner
    from .openings import OpeningBook

    controls = controls or [parse_time_control(t) for t in
                            ("60+0.6", "15+0.15", "10+0.1", "5+0", "3+0.02")]
    report = SuiteReport("timing", spec.name)
    start = time.perf_counter()
    strict = Adjudication(max_plies=max_plies, draw_enabled=False,
                          resign_enabled=False)

    log("  the engine plays itself; a move may use at most half the clock\n")
    log(f"  {'control':>10} {'plies':>6} {'low clock':>10} {'longest':>9}  "
        f"{'verdict':<14} how the game ended")
    for tc in controls:
        with GameRunner(spec, spec, tc, strict, OpeningBook.single(),
                        cfg.move_overhead_ms, keep_pgn=False) as runner:
            game = runner.play(0)
        flagged = game.reason == "time forfeit"
        lowest = min(game.engine.clock_left_ms if game.engine else 0.0,
                     game.opponent.clock_left_ms if game.opponent else 0.0)
        longest = max(game.engine.max_ms if game.engine else 0.0,
                      game.opponent.max_ms if game.opponent else 0.0)
        overshoot = 1 if longest > 0.5 * tc.base_ms + tc.inc_ms + 100 else 0
        report.failures += bool(flagged) + overshoot
        report.rows.append({"time_control": tc.label, "plies": game.plies,
                            "lowest_clock_ms": round(lowest, 1),
                            "longest_move_ms": round(longest, 1),
                            "flagged": flagged, "reason": game.reason,
                            "error": game.error})
        verdict = "FLAGGED" if flagged else "move too long" if overshoot else "ok"
        log(f"  {tc.label:>10} {game.plies:>6} {lowest:>10,.0f} {longest:>9,.0f}  "
            f"{verdict:<14} {game.reason}"
            f"{'  ' + game.error if game.error else ''}")

    report.seconds = time.perf_counter() - start
    report.summary = {"controls": len(report.rows)}
    log(f"\n  {'time management is safe' if report.ok else f'{report.failures} TIME PROBLEMS'}")
    return report


# ------------------------------------------------------------ reproducibility


def run_determinism(spec: EngineSpec, records: Sequence[EpdRecord], depth: int = 10,
                    repeats: int = 2, log: Log = print,
                    phases: Sequence[str] = ("process", "session")) -> SuiteReport:
    """The same search at the same depth must give the same node count.

    Two different questions, because they fail for different reasons and only
    one of them is a hard requirement:

    ``process``
        a freshly started engine, one search.  This must hold for any correct
        single-threaded engine: a mismatch means uninitialised memory, a race,
        or a search that depends on wall-clock time.  Anything else measured
        by this suite is meaningless if this fails.

    ``session``
        repeated searches in one process with `ucinewgame` between them.  This
        is what catches state surviving a reset -- a table that was not
        cleared, or one keyed on less than it depends on.  An engine can be
        strong without it, but every node count it reports is then a sample
        rather than a measurement, and `ttd` and `bench` cannot be compared
        between versions.

    Only meaningful with one search thread; Lazy SMP is deliberately
    non-deterministic, so the suite forces ``Threads=1``.
    """
    report = SuiteReport("determinism", spec.name)
    start = time.perf_counter()
    single = spec.with_overrides({"Threads": 1})
    tc = TimeControl(depth=depth)
    failures = {"process": 0, "session": 0}

    def run_one(engine: UciEngine, board: chess.Board) -> tuple[str, int]:
        info, _ms = _analyse(engine, board, tc)
        return (info.best_move.uci() if info.best_move else "-", info.nodes)

    log(f"  depth {depth}, {repeats} runs per position, Threads=1")

    if "process" in phases:
        log(f"\n  restarted engine, one search each -- must always hold")
        for record in records:
            seen = []
            for _ in range(repeats):
                with UciEngine(single) as engine:
                    seen.append(run_one(engine, record.board))
            ok = len(set(seen)) == 1
            failures["process"] += not ok
            report.rows.append({"phase": "process", "fen": record.fen,
                                "runs": seen, "ok": ok})
            log(f"  {'OK  ' if ok else 'FAIL'} "
                f"{' | '.join(f'{m} {n:,}' for m, n in seen)}  {record.fen[:38]}")

    if "session" in phases:
        log(f"\n  one engine, `ucinewgame` between searches -- "
            f"needed to compare node counts")
        with UciEngine(single) as engine:
            for record in records:
                seen = [run_one(engine, record.board) for _ in range(repeats)]
                ok = len(set(seen)) == 1
                failures["session"] += not ok
                report.rows.append({"phase": "session", "fen": record.fen,
                                    "runs": seen, "ok": ok})
                log(f"  {'OK  ' if ok else 'FAIL'} "
                    f"{' | '.join(f'{m} {n:,}' for m, n in seen)}  {record.fen[:38]}")

    report.seconds = time.perf_counter() - start
    report.failures = failures["process"] + failures["session"]
    report.summary = {"positions": len(records), "depth": depth,
                      "process_failures": failures["process"],
                      "session_failures": failures["session"]}
    log("")
    for phase in phases:
        bad = failures[phase]
        log(f"  {phase:<8} {'reproducible' if not bad else f'{bad} NON-REPRODUCIBLE'}")
    if failures["process"]:
        log("  a restarted engine giving different answers is a real bug; "
            "fix it before trusting any other number here")
    elif failures["session"]:
        log("  state is surviving `ucinewgame`: the engine still plays fine, "
            "but node counts are only comparable between fresh processes")
    return report

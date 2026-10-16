"""The testsuite entry point.

    python testsuite/run.py list                 what is configured
    python testsuite/run.py selftest             correctness: perft, mates, draws, clock
    python testsuite/run.py benchmarks           speed and tactics
    python testsuite/run.py match sf-1700 -n 20  one opponent
    python testsuite/run.py gauntlet -n 10       every configured opponent
    python testsuite/run.py elo                  play the ladder until the rating settles
    python testsuite/run.py rating               the rating implied by all stored games
    python testsuite/run.py sprt ce:Hash=1024    is this option change an improvement?

`run.py <command> --help` lists the options of that command.  Defaults that you
want to keep belong in testsuite/suite.toml.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from ts import config as cfgmod
from ts import elo as elolib
from ts import epd as epdmod
from ts import report as rep
from ts import suites
from ts.config import (Config, EngineSpec, TimeControl, parse_move_budget,
                       parse_time_control)
from ts.engines import EngineProblem, Limit, UciEngine
from ts.match import Gauntlet, MatchOutcome, run_gauntlet, run_match
from ts.openings import OpeningBook
from ts.store import Store, fingerprint, rating_from_records

SCALE_NOTE = "Stockfish UCI_Elo"


# ------------------------------------------------------------------ plumbing


def suite_path(cfg: Config, name: str) -> str:
    return os.path.join(cfg.suites_dir, f"{name}.epd")


def load_suite(cfg: Config, name: str, override: str = "") -> list[epdmod.EpdRecord]:
    path = override or suite_path(cfg, name)
    if not os.path.isfile(path):
        raise SystemExit(f"{path} is missing -- generate it with\n"
                         f"    python testsuite/tools/make_suites.py {name}")
    return epdmod.read(path)


def opening_book(cfg: Config, path: str = "", count: int = 0,
                 shuffle_seed: Optional[int] = None) -> OpeningBook:
    path = path or suite_path(cfg, "openings")
    if not os.path.isfile(path):
        print(f"  note: {os.path.basename(path)} not found, every game starts from "
              f"the initial position\n"
              f"        (generate it with: python testsuite/tools/make_suites.py openings)")
        return OpeningBook.single()
    book = OpeningBook.from_file(path, limit=count)
    if shuffle_seed is not None:
        book = book.shuffled(shuffle_seed)
    return book


def engine_spec(cfg: Config, text: str = "") -> EngineSpec:
    return cfg.resolve(text) if text else cfg.engine


def resolve_tc(cfg: Config, text: str = "") -> TimeControl:
    return parse_time_control(text) if text else cfg.time_control


def require_engine(spec: EngineSpec) -> EngineSpec:
    """Fail now, with the path, rather than when the first game tries to start."""
    if not spec.exists():
        raise SystemExit(f"  no binary for {spec.name} at "
                         f"{spec.resolved_command()[0]}\n"
                         f"  Build it with `python engine/build.py`, or fix "
                         f"`command` in suite.toml.")
    return spec


def usable_opponents(opponents: Sequence[EngineSpec], *,
                     need_elo: bool) -> list[EngineSpec]:
    """The opponents we can actually play, saying precisely what was dropped.

    "The ladder is empty" is true whether the binaries are missing or merely
    unrated, and those need completely different fixes, so report which it is.
    """
    missing = [o for o in opponents if not o.exists()]
    present = [o for o in opponents if o.exists()]
    unrated = [o for o in present if o.elo is None]
    usable = [o for o in present if o.elo is not None or not need_elo]

    if usable:
        if missing:
            print(f"  note: skipping {len(missing)} opponent(s) whose binary is "
                  f"missing: {', '.join(o.name for o in missing)}")
        if need_elo and unrated:
            print(f"  note: skipping {len(unrated)} opponent(s) with no `elo`: "
                  f"{', '.join(o.name for o in unrated)}")
        return usable

    problems: list[str] = []
    if not opponents:
        problems.append("suite.toml defines no [[opponent]] blocks.")
    if missing:
        paths = sorted({o.resolved_command()[0] for o in missing})
        problems.append(f"{len(missing)} opponent(s) point at a binary that is "
                        f"not there:")
        problems += [f"      {p}" for p in paths]
        problems.append("    Put the engine back at that path, or change "
                        "`command` in suite.toml.")
    if need_elo and unrated:
        problems.append(f"These opponents exist but have no `elo`, so a rating "
                        f"cannot be fitted against them: "
                        f"{', '.join(o.name for o in unrated)}.")
        problems.append("    Give them an `elo`, or use `gauntlet` instead of "
                        "`elo`.")
    raise SystemExit("  no usable opponents.\n  " + "\n  ".join(problems))


def check_concurrency(spec: EngineSpec, opponents: Sequence[EngineSpec],
                      concurrency: int) -> None:
    threads = int(spec.options.get("Threads", 1) or 1)
    worst = max([threads] + [int(o.options.get("Threads", 1) or 1) for o in opponents])
    cores = os.cpu_count() or 1
    busy = concurrency * (threads + worst)
    if busy > cores:
        print(f"  warning: {concurrency} games x up to {threads + worst} threads = "
              f"{busy} on {cores} cores.\n"
              f"           The engines will slow each other down and the rating will "
              f"come out low.")


# ------------------------------------------------------------------- commands


def cmd_list(cfg: Config, args: argparse.Namespace) -> int:
    print(f"configuration   {cfg.path}")
    print(f"suites          {cfg.suites_dir}")
    print(f"results         {cfg.results_dir}")
    print(f"time control    {cfg.time_control.label}  "
          f"(concurrency {cfg.concurrency})")

    rows = []
    for label, spec in [("engine", cfg.engine)] + \
                       ([("referee", cfg.referee)] if cfg.referee else []) + \
                       [("opponent", o) for o in cfg.opponents]:
        rows.append([
            label, spec.name,
            f"{spec.elo:.0f}" if spec.elo is not None else "-",
            spec.time_control.label if spec.time_control else "-",
            "ok" if spec.exists() else "MISSING",
            ", ".join(f"{k}={v}" for k, v in sorted(spec.options.items())) or "-",
        ])
    print()
    print(rep.table(["role", "name", "elo", "limit", "binary", "options"], rows,
                    right=[False, False, True, False, False, False]))

    print("\nsuite files")
    rows = []
    for name in ("perft", "bench", "tactics", "mates", "draws", "openings"):
        path = suite_path(cfg, name)
        if os.path.isfile(path):
            try:
                count = len(epdmod.read(path))
            except SystemExit:
                count = 0
            rows.append([name, str(count), "ok"])
        else:
            rows.append([name, "-", "missing"])
    print(rep.table(["suite", "positions", "state"], rows, right=[False, True, False]))

    store = Store(cfg.results_dir)
    records = [r for r in store.records()]
    if records:
        print(f"\nstored history   {len(records)} matches, "
              f"{sum(r.games for r in records)} games, "
              f"newest {records[-1].when}")
    return 0


def _run_reports(cfg: Config, args: argparse.Namespace,
                 names: Sequence[str]) -> int:
    spec = engine_spec(cfg, args.engine)
    if not spec.exists():
        raise SystemExit(f"no engine binary at {spec.resolved_command()[0]}")
    reports = []
    for name in names:
        print(rep.heading(name))
        try:
            reports.append(_run_one(cfg, args, spec, name))
        except EngineProblem as exc:
            print(f"  FAILED: {exc}")
            broken = suites.SuiteReport(name, spec.name, failures=1, note=str(exc))
            reports.append(broken)

    print(rep.heading("summary"))
    print(rep.suite_summary(reports))
    failures = sum(r.failures for r in reports)
    print(f"\n  {'everything passed' if not failures else f'{failures} failures'}")

    if not args.no_save:
        store = Store(cfg.results_dir)
        path = store.save_json(os.path.basename(store.run_path("suites")),
                               {"engine": fingerprint(spec),
                                "reports": [r.to_dict() for r in reports]})
        print(f"  written to {path}")
    return 1 if failures else 0


def _run_one(cfg: Config, args: argparse.Namespace, spec: EngineSpec,
             name: str) -> suites.SuiteReport:
    where = getattr(args, "positions", "")
    if name == "perft":
        return suites.run_perft(spec, load_suite(cfg, "perft", where),
                                max_depth=args.max_depth,
                                max_nodes=args.max_nodes)
    if name == "bench":
        return suites.run_bench(spec, depth=args.bench_depth)
    if name == "speed":
        return suites.run_speed(spec, load_suite(cfg, "bench", where),
                                tc=parse_move_budget(args.movetime))
    if name == "ttd":
        return suites.run_time_to_depth(spec, load_suite(cfg, "bench", where),
                                        depth=args.depth)
    if name == "tactics":
        return suites.run_tactics(spec, load_suite(cfg, "tactics", where),
                                  tc=parse_move_budget(args.movetime),
                                  name="tactics")
    if name == "mates":
        return suites.run_tactics(spec, load_suite(cfg, "mates", where),
                                  tc=parse_move_budget(args.movetime),
                                  name="mates")
    if name == "draws":
        return suites.run_draws(spec, load_suite(cfg, "draws", where),
                                depth=args.depth)
    if name == "determinism":
        return suites.run_determinism(spec, load_suite(cfg, "bench", where)[:6],
                                      depth=args.depth)
    if name == "timing":
        return suites.run_timing(cfg, spec)
    raise SystemExit(f"unknown suite {name}")


def cmd_suite(cfg: Config, args: argparse.Namespace) -> int:
    return _run_reports(cfg, args, [args.command])


def cmd_selftest(cfg: Config, args: argparse.Namespace) -> int:
    """Everything that has a right answer, so a failure is a real bug."""
    return _run_reports(cfg, args, ["perft", "mates", "draws", "determinism", "timing"])


def cmd_benchmarks(cfg: Config, args: argparse.Namespace) -> int:
    """Everything that measures speed or skill rather than correctness."""
    return _run_reports(cfg, args, ["bench", "ttd", "speed", "tactics"])


def cmd_all(cfg: Config, args: argparse.Namespace) -> int:
    return _run_reports(cfg, args, ["perft", "bench", "ttd", "speed", "tactics",
                                    "mates", "draws", "determinism", "timing"])


# ---------------------------------------------------------------- match / elo


def _progress_printer(total: int):
    state = {"done": 0}

    def progress(game, outcome: MatchOutcome) -> None:
        state["done"] += 1
        stats = outcome.stats
        colour = "white" if game.engine_white else "black"
        detail = f"{game.plies} plies, {game.reason}"
        print(f"  [{state['done']:>4}/{total}] {game.outcome_letter} as {colour:<5} "
              f"vs {outcome.opponent:<10} "
              f"{stats.points:g}/{stats.games} "
              f"({100 * stats.score:5.1f}%)  {detail}"
              + (f"  !! {game.error}" if game.error else ""))
    return progress


def _save_match(cfg: Config, spec: EngineSpec, match: MatchOutcome,
                kind: str = "match") -> None:
    store = Store(cfg.results_dir)
    store.append_match(spec, match.opponent, match.opponent_elo, match.time_control,
                       match.wins, match.draws, match.losses, kind=kind,
                       anomalies=len(match.anomalies))


def cmd_match(cfg: Config, args: argparse.Namespace) -> int:
    spec = require_engine(engine_spec(cfg, args.engine))
    opponent = usable_opponents([cfg.resolve(args.opponent)], need_elo=False)[0]
    tc = resolve_tc(cfg, args.tc)
    book = opening_book(cfg, args.openings, args.opening_count, args.seed)
    concurrency = args.concurrency or cfg.concurrency
    check_concurrency(spec, [opponent], concurrency)

    print(f"\n  {spec.name} vs {opponent.name} -- {args.games} games at {tc.label}, "
          f"{len(book)} openings, concurrency {concurrency}")
    if tc.expected_ms_per_game:
        minutes = args.games * tc.expected_ms_per_game / 60000 / concurrency
        print(f"  rough budget: {minutes:.0f} min\n")

    pgn = "" if args.no_pgn else os.path.join(cfg.results_dir, "games.pgn")
    match = run_match(cfg, spec, opponent, args.games, tc, book, concurrency,
                      _progress_printer(args.games), pgn)

    print(rep.heading("result"))
    print(rep.match_table([match]))
    stats = match.stats
    print(f"\n  {match.engine} scored {stats.score_line()} = {100 * stats.score:.1f}% "
          f"-> {stats.elo_line()} Elo relative to {match.opponent}")
    if match.opponent_elo is not None and stats.games and abs(stats.elo) != float("inf"):
        print(f"  implied rating: {match.opponent_elo + stats.elo:.0f} "
              f"({SCALE_NOTE})")
    print(f"  likelihood of superiority {100 * stats.los:.1f}%, "
          f"{match.seconds / 60:.1f} min")
    if match.anomalies:
        print("\n  anomalies:")
        for line in match.anomalies[:20]:
            print(f"    {line}")
    if pgn:
        print(f"\n  games appended to {pgn}")
    if not args.no_save:
        _save_match(cfg, spec, match)
    return 0


def cmd_gauntlet(cfg: Config, args: argparse.Namespace) -> int:
    spec = require_engine(engine_spec(cfg, args.engine))
    opponents = usable_opponents(cfg.opponents_by_name(args.opponents),
                                 need_elo=False)
    tc = resolve_tc(cfg, args.tc)
    book = opening_book(cfg, args.openings, args.opening_count, args.seed)
    concurrency = args.concurrency or cfg.concurrency
    check_concurrency(spec, opponents, concurrency)

    total = args.games * len(opponents)
    print(f"\n  {spec.name} against {len(opponents)} opponents, "
          f"{args.games} games each ({total} total) at {tc.label}")
    if tc.expected_ms_per_game:
        print(f"  rough budget: "
              f"{total * tc.expected_ms_per_game / 60000 / concurrency:.0f} min\n")

    pgn = "" if args.no_pgn else os.path.join(cfg.results_dir, "games.pgn")
    gauntlet = run_gauntlet(cfg, spec, opponents, args.games, tc, book, concurrency,
                            _progress_printer(total),
                            on_match=(None if args.no_save
                                      else lambda m: _save_match(cfg, spec, m, "gauntlet")),
                            pgn_path=pgn)

    print(rep.heading("gauntlet"))
    print(rep.gauntlet_report(gauntlet, scale=SCALE_NOTE))
    if not args.no_save:
        store = Store(cfg.results_dir)
        path = store.save_json(os.path.basename(store.run_path("gauntlet")),
                               {"engine": fingerprint(spec), **gauntlet.to_dict()})
        print(f"\n  written to {path}")
    return 0


def cmd_elo(cfg: Config, args: argparse.Namespace) -> int:
    """Play the ladder until the rating is pinned down.

    Each round picks the rungs closest to the current estimate, because a game
    against an equal opponent carries far more information than a game against
    one 600 points away.
    """
    spec = require_engine(engine_spec(cfg, args.engine))
    tc = resolve_tc(cfg, args.tc)
    book = opening_book(cfg, args.openings, args.opening_count, args.seed)
    concurrency = args.concurrency or cfg.concurrency
    store = Store(cfg.results_dir)

    ladder = usable_opponents(cfg.opponents, need_elo=True)
    check_concurrency(spec, ladder, concurrency)

    history = [] if args.fresh else store.filtered(engine=spec, time_control=tc.label)
    results = {r.name: r for r in
               elolib.merge_results(r.as_opponent_result() for r in history)}
    for opponent in ladder:
        results.setdefault(opponent.name,
                           elolib.OpponentResult(opponent.name, opponent.elo))

    estimate = elolib.fit_rating(list(results.values()))
    if history:
        print(f"\n  starting from {len(history)} stored matches "
              f"({estimate.games} games): {estimate.line()}")
    start_rating = estimate.elo if estimate.games else args.start or _ladder_middle(ladder)
    print(f"  engine {spec.name}, {tc.label}, ladder "
          f"{min(o.elo for o in ladder):.0f}-{max(o.elo for o in ladder):.0f} "
          f"({len(ladder)} rungs)")
    print(f"  target accuracy +-{args.target_error:.0f} Elo, "
          f"at most {args.rounds} rounds of {args.games} games per opponent")

    rating = start_rating
    deadline = time.perf_counter() + args.max_minutes * 60 if args.max_minutes else None
    pgn = "" if args.no_pgn else os.path.join(cfg.results_dir, "games.pgn")
    played_any = False

    for round_no in range(1, args.rounds + 1):
        pool = [results[o.name] for o in ladder]
        chosen = elolib.pick_opponents(rating, pool, args.opponents_per_round)
        by_name = {o.name: o for o in ladder}
        print(rep.heading(f"round {round_no}: estimate {rating:.0f}, playing "
                          f"{', '.join(o.name for o in chosen)}"))

        for opponent_result in chosen:
            opponent = by_name[opponent_result.name]
            if deadline and time.perf_counter() > deadline:
                print("  time budget reached")
                break
            match = run_match(cfg, spec, opponent, args.games, tc, book, concurrency,
                              _progress_printer(args.games), pgn)
            played_any = True
            if not args.no_save:
                _save_match(cfg, spec, match, "elo")
            merged = results[opponent.name].merged(match.as_opponent_result())
            merged.elo = opponent.elo or 0.0
            results[opponent.name] = merged

            estimate = elolib.fit_rating(list(results.values()))
            rating = estimate.elo
            print(f"  -> {opponent.name}: {match.stats.score_line()}; "
                  f"rating now {estimate.line()}")

        estimate = elolib.fit_rating(list(results.values()))
        rating = estimate.elo
        if not estimate.bounded and estimate.stderr <= args.target_error:
            print(f"\n  reached +-{estimate.stderr:.0f} Elo, stopping")
            break
        if deadline and time.perf_counter() > deadline:
            break

    print(rep.heading("rating"))
    print(rep.rating_block(estimate, scale=SCALE_NOTE))
    if played_any and not args.no_save:
        path = store.save_json(os.path.basename(store.run_path("elo")), {
            "engine": fingerprint(spec), "time_control": tc.label,
            "elo": round(estimate.elo, 1), "stderr": round(estimate.stderr, 1),
            "games": estimate.games,
            "opponents": [{"name": o.name, "elo": o.elo, "wins": o.wins,
                           "draws": o.draws, "losses": o.losses}
                          for o in estimate.opponents],
        })
        print(f"\n  written to {path}")
    return 0


def _ladder_middle(ladder: Sequence[EngineSpec]) -> float:
    elos = sorted(o.elo or 0.0 for o in ladder)
    return elos[len(elos) // 2]


def cmd_rating(cfg: Config, args: argparse.Namespace) -> int:
    spec = engine_spec(cfg, args.engine)
    store = Store(cfg.results_dir)
    tc = parse_time_control(args.tc).label if args.tc else None

    records = store.filtered(engine=spec, time_control=tc,
                             same_build=not args.any_build)
    if not records:
        print("  no stored games for this engine"
              + (f" at {tc}" if tc else "")
              + (" and this build" if not args.any_build else "")
              + ".\n  Play some with: python testsuite/run.py elo")
        if store.records():
            print("\n  stored builds and controls:")
            rows = sorted({(r.engine.get("name", "?"), r.engine.get("hash", "?"),
                            r.time_control) for r in store.records()})
            print(rep.table(["engine", "build", "time control"],
                            [list(r) for r in rows], right=[False, False, False]))
        return 0

    by_tc: dict[str, list] = {}
    for record in records:
        by_tc.setdefault(record.time_control, []).append(record)

    for control, group in sorted(by_tc.items()):
        estimate = rating_from_records(group, args.confidence)
        print(rep.heading(f"{spec.name} at {control}"))
        print(rep.rating_block(estimate, scale=SCALE_NOTE))
        print(f"  from {len(group)} matches between {group[0].when} "
              f"and {group[-1].when}")
    return 0


def cmd_history(cfg: Config, args: argparse.Namespace) -> int:
    store = Store(cfg.results_dir)
    records = store.records()
    if not records:
        print("  nothing stored yet")
        return 0
    rows = []
    for record in records[-args.limit:]:
        rows.append([record.when, record.engine.get("name", "?"),
                     record.engine.get("hash", "?")[:8], record.opponent,
                     record.time_control, record.games,
                     f"+{record.wins} ={record.draws} -{record.losses}"])
    print(rep.table(["when", "engine", "build", "opponent", "tc", "games", "w/d/l"],
                    rows, right=[False, False, False, False, False, True, False]))
    print(f"\n  {len(records)} matches, {sum(r.games for r in records)} games total")
    return 0


def cmd_sprt(cfg: Config, args: argparse.Namespace) -> int:
    """Play A against B until one hypothesis wins, or the game limit is hit."""
    spec_a = require_engine(engine_spec(cfg, args.engine))
    spec_b = require_engine(cfg.resolve(args.candidate))
    if spec_a.name == spec_b.name:
        spec_b = spec_b.with_overrides(name=spec_b.name + "-b")
    tc = resolve_tc(cfg, args.tc)
    book = opening_book(cfg, args.openings, args.opening_count, args.seed)
    concurrency = args.concurrency or cfg.concurrency

    print(f"\n  SPRT {spec_b.name} (as the engine) against {spec_a.name}")
    print(f"  H0: no more than {args.elo0:+g} Elo, H1: at least {args.elo1:+g} Elo, "
          f"alpha = beta = {args.alpha}")
    print(f"  {tc.label}, up to {args.games} games, {len(book)} openings\n")

    def stop_when(outcome: MatchOutcome) -> bool:
        test = elolib.sprt(outcome.wins, outcome.draws, outcome.losses,
                           args.elo0, args.elo1, args.alpha, args.alpha)
        return test.verdict != "continue"

    def progress(game, outcome: MatchOutcome) -> None:
        stats = outcome.stats
        test = elolib.sprt(outcome.wins, outcome.draws, outcome.losses,
                           args.elo0, args.elo1, args.alpha, args.alpha)
        print(f"  [{stats.games:>4}/{args.games}] {game.outcome_letter} "
              f"{stats.points:g}/{stats.games} ({100 * stats.score:5.1f}%) "
              f"{stats.elo_line():<22} LLR {test.llr:+.2f} "
              f"[{test.lower:.2f}, {test.upper:.2f}]")

    # spec_b plays as "the engine" so a positive Elo means the candidate is better
    match = run_match(cfg, spec_b, spec_a, args.games, tc, book, concurrency,
                      progress, "" if args.no_pgn
                      else os.path.join(cfg.results_dir, "sprt.pgn"),
                      stop_when=stop_when)

    test = elolib.sprt(match.wins, match.draws, match.losses, args.elo0, args.elo1,
                       args.alpha, args.alpha)
    stats = match.stats
    print(rep.heading("result"))
    print(f"  {spec_b.name} scored {stats.score_line()} vs {spec_a.name}")
    print(f"  {stats.elo_line()} Elo")
    print(f"  {test.line()}")
    verdict = {
        "H1": f"accepted: {spec_b.name} is at least {args.elo1:+g} Elo better",
        "H0": f"rejected: {spec_b.name} is not {args.elo1:+g} Elo better",
        "continue": "inconclusive: the game limit was reached first",
    }[test.verdict]
    print(f"  {verdict}")
    if match.anomalies:
        print("\n  anomalies:")
        for line in match.anomalies[:20]:
            print(f"    {line}")
    return 0


def cmd_eval(cfg: Config, args: argparse.Namespace) -> int:
    """Print the engine's static evaluation of each position in a suite."""
    spec = engine_spec(cfg, args.engine)
    records = load_suite(cfg, args.suite)
    with UciEngine(spec) as engine:
        rows = []
        for record in records:
            engine.new_game()
            info = engine.analyse(record.board, Limit(depth=1)).info
            cp = info.score_cp if info.has_score else None
            rows.append([record.label, str(cp), record.comment or record.fen[:46]])
    print(rep.table(["id", "cp", "position"], rows, right=[False, True, False]))
    print("\n  depth 1 scores: the static evaluation plus one ply of search")
    return 0


# ----------------------------------------------------------------------- CLI


#: Flags that apply to every command.  They are declared twice -- once on the
#: top level parser and once, with SUPPRESS so an unused one cannot overwrite
#: the outer value, on every subcommand -- so that both
#: `run.py --engine X sprt Y` and `run.py sprt Y --engine X` work.  Having to
#: remember which side of the subcommand a flag belongs on is a papercut.
_GLOBAL_FLAGS = [
    (("--config",), {"help": "path to suite.toml"}),
    (("--engine",), {"help": "engine to test; `name:Option=value,...` "
                             "overrides its UCI options"}),
    (("--no-save",), {"action": "store_true",
                      "help": "do not write anything to results/"}),
]

_GLOBAL_DEFAULTS = {"config": cfgmod.DEFAULT_CONFIG, "engine": "", "no_save": False}


def _global_flag_parser() -> argparse.ArgumentParser:
    out = argparse.ArgumentParser(add_help=False)
    for names, options in _GLOBAL_FLAGS:
        out.add_argument(*names, default=argparse.SUPPRESS, **options)
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    for names, options in _GLOBAL_FLAGS:
        key = names[0].lstrip("-").replace("-", "_")
        parser.add_argument(*names, default=_GLOBAL_DEFAULTS[key], **options)

    repeated = _global_flag_parser()
    sub = parser.add_subparsers(dest="command", required=True,
                                parser_class=lambda **kw: argparse.ArgumentParser(
                                    parents=[repeated], **kw))

    def add_suite_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--depth", type=int, default=12,
                       help="fixed depth for ttd, draws and determinism")
        p.add_argument("--movetime", default="3s",
                       help="per-position budget for speed, tactics and mates; "
                            "a bare duration means per move (3s, 500ms), or use "
                            "depth=N / nodes=N")
        p.add_argument("--bench-depth", type=int, default=13)
        p.add_argument("--max-depth", type=int, default=0,
                       help="skip perft depths above this (0 = no limit)")
        p.add_argument("--max-nodes", type=int, default=200_000_000,
                       help="skip perft entries larger than this; 0 runs every "
                            "stored depth, which is tens of billions of nodes")
        p.add_argument("--positions", default="",
                       help="use this EPD file instead of the suite's own")

    for name, func, help_text in [
        ("perft", cmd_suite, "move generator correctness"),
        ("bench", cmd_suite, "the engine's own fixed-depth benchmark"),
        ("speed", cmd_suite, "depth reached in a fixed time"),
        ("ttd", cmd_suite, "nodes and time to reach a fixed depth"),
        ("tactics", cmd_suite, "find the best move in known positions"),
        ("mates", cmd_suite, "find forced mates"),
        ("draws", cmd_suite, "recognise drawn endings"),
        ("determinism", cmd_suite, "same search twice, same result"),
        ("timing", cmd_suite, "never exceed the clock"),
        ("selftest", cmd_selftest, "all the suites with a right answer"),
        ("benchmarks", cmd_benchmarks, "all the suites that measure speed or skill"),
        ("all", cmd_all, "every suite"),
    ]:
        p = sub.add_parser(name, help=help_text)
        add_suite_flags(p)
        p.set_defaults(func=func)

    p = sub.add_parser("list", help="show the configuration and the suite files")
    p.set_defaults(func=cmd_list)

    def add_play_flags(p: argparse.ArgumentParser, default_games: int) -> None:
        p.add_argument("-n", "--games", type=int, default=default_games,
                       help="games per opponent (an even number keeps colours equal)")
        p.add_argument("--tc", default="", help='time control, e.g. "10+0.1", '
                                                '"60+0.6", "movetime=500ms", "depth=8"')
        p.add_argument("-c", "--concurrency", type=int, default=0,
                       help="games in parallel")
        p.add_argument("--openings", default="", help="opening file (.epd or .pgn)")
        p.add_argument("--opening-count", type=int, default=0,
                       help="use only the first N openings")
        p.add_argument("--seed", type=int, default=0, help="opening shuffle seed")
        p.add_argument("--no-pgn", action="store_true", help="do not write a PGN")

    p = sub.add_parser("match", help="play one opponent")
    p.add_argument("opponent", help="opponent name from suite.toml")
    add_play_flags(p, 20)
    p.set_defaults(func=cmd_match)

    p = sub.add_parser("gauntlet", help="play every configured opponent")
    p.add_argument("--opponents", nargs="*", default=None,
                   help="names to play (default: all of them)")
    add_play_flags(p, 10)
    p.set_defaults(func=cmd_gauntlet)

    p = sub.add_parser("elo", help="play the ladder until the rating settles")
    add_play_flags(p, 4)
    p.add_argument("--rounds", type=int, default=100)
    p.add_argument("--opponents-per-round", type=int, default=5)
    p.add_argument("--target-error", type=float, default=25.0,
                   help="stop once the rating is this accurate, in Elo")
    p.add_argument("--max-minutes", type=float, default=0.0,
                   help="give up after this long (0 = no limit)")
    p.add_argument("--start", type=float, default=0.0,
                   help="initial guess for the rating")
    p.add_argument("--fresh", action="store_true",
                   help="ignore stored results and start from nothing")
    p.set_defaults(func=cmd_elo)

    p = sub.add_parser("rating", help="the rating implied by the stored games")
    p.add_argument("--tc", default="", help="only this time control")
    p.add_argument("--any-build", action="store_true",
                   help="also count games played by other builds of the engine")
    p.add_argument("--confidence", type=float, default=0.95)
    p.set_defaults(func=cmd_rating)

    p = sub.add_parser("history", help="list stored matches")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("sprt", help="test whether a change is an improvement")
    p.add_argument("candidate", help="the changed engine, e.g. ce:EvalMode=nnue")
    add_play_flags(p, 2000)
    p.add_argument("--elo0", type=float, default=0.0)
    p.add_argument("--elo1", type=float, default=10.0)
    p.add_argument("--alpha", type=float, default=0.05)
    p.set_defaults(func=cmd_sprt)

    p = sub.add_parser("eval", help="static evaluation of a suite's positions")
    p.add_argument("--suite", default="draws")
    p.set_defaults(func=cmd_eval)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = cfgmod.load(args.config)
    os.makedirs(cfg.results_dir, exist_ok=True)
    try:
        return int(args.func(cfg, args) or 0)
    except KeyboardInterrupt:
        print("\n  interrupted; results already written are kept")
        return 130
    except EngineProblem as exc:
        print(f"\n  engine problem: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

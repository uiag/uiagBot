"""Console output: headings, tables, and the rating summary."""
from __future__ import annotations

import math
import shutil
from typing import Iterable, Optional, Sequence

from . import elo as elolib
from .match import Gauntlet, MatchOutcome

WIDTH = min(100, max(70, shutil.get_terminal_size((100, 24)).columns - 1))


def heading(text: str, char: str = "=") -> str:
    return f"\n{char * 3} {text} {char * max(3, WIDTH - len(text) - 5)}"


def rule(char: str = "-") -> str:
    return char * WIDTH


def table(headers: Sequence[str], rows: Sequence[Sequence[object]],
          right: Optional[Sequence[bool]] = None, indent: str = "  ") -> str:
    """Fixed-width table; `right` says which columns are right-aligned."""
    cells = [[str(c) for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in cells:
        for i, cell in enumerate(row[:len(widths)]):
            widths[i] = max(widths[i], len(cell))
    right = list(right or [False] + [True] * (len(headers) - 1))
    right += [True] * (len(headers) - len(right))

    def line(values: Sequence[str]) -> str:
        out = []
        for i, value in enumerate(values[:len(widths)]):
            out.append(value.rjust(widths[i]) if right[i] else value.ljust(widths[i]))
        return indent + "  ".join(out).rstrip()

    parts = [line(list(headers)), indent + "  ".join("-" * w for w in widths)]
    parts += [line(row) for row in cells]
    return "\n".join(parts)


def _elo_cell(stats: elolib.MatchStats) -> str:
    if stats.games == 0:
        return "-"
    if math.isinf(stats.elo):
        return "+inf" if stats.elo > 0 else "-inf"
    return f"{stats.elo:+.0f}"


def match_table(matches: Iterable[MatchOutcome]) -> str:
    rows = []
    for match in matches:
        stats = match.stats
        rows.append([
            match.opponent,
            f"{match.opponent_elo:.0f}" if match.opponent_elo is not None else "-",
            len(match.games),
            f"{stats.points:g}",
            f"+{match.wins} ={match.draws} -{match.losses}",
            f"{100 * stats.score:.1f}%",
            _elo_cell(stats),
            f"{stats.los * 100:.0f}%",
        ])
    return table(["opponent", "elo", "games", "pts", "w/d/l", "score", "diff", "LOS"],
                 rows, right=[False, True, True, True, False, True, True, True])


def rating_block(estimate: elolib.RatingEstimate, *, scale: str = "") -> str:
    """Per-opponent breakdown plus the fitted rating."""
    rows = []
    for result in sorted(estimate.opponents, key=lambda r: r.elo):
        expected = elolib.expected_score(estimate.elo - result.elo)
        actual = result.points / result.games if result.games else 0.0
        rows.append([
            result.name,
            f"{result.elo:.0f}",
            result.games,
            f"+{result.wins} ={result.draws} -{result.losses}",
            f"{100 * actual:.1f}%",
            f"{100 * expected:.1f}%",
            f"{100 * (actual - expected):+.1f}",
        ])
    out = [table(["opponent", "elo", "games", "w/d/l", "scored", "predicted", "delta"],
                 rows, right=[False, True, True, False, True, True, True])]

    out.append("")
    if estimate.games == 0:
        out.append("  no rated games yet")
        return "\n".join(out)

    label = f" on the {scale} scale" if scale else ""
    out.append(f"  estimated rating{label}: {estimate.line()}")
    out.append(f"  draw rate {100 * estimate.draw_rate:.1f}%, "
               f"{estimate.points:g}/{estimate.games} points scored")
    if estimate.bounded:
        out.append("  the score was 0% or 100%, so only a bound can be given -- "
                   "play opponents closer to the engine's level")
    elif estimate.stderr > 60:
        out.append("  the interval is wide; more games (or opponents nearer the "
                   "engine's level) will narrow it")
    return "\n".join(out)


def gauntlet_report(gauntlet: Gauntlet, *, scale: str = "") -> str:
    parts = [match_table(gauntlet.matches), "", rating_block(gauntlet.rating(), scale=scale)]
    total = elolib.match_result(gauntlet.wins, gauntlet.draws, gauntlet.losses)
    parts.insert(0, f"  {gauntlet.games} games at {gauntlet.time_control} "
                    f"in {gauntlet.seconds / 60:.1f} min -- "
                    f"overall {total.score_line()} ({100 * total.score:.1f}%)\n")
    anomalies = [a for m in gauntlet.matches for a in m.anomalies]
    if anomalies:
        parts.append("\n  anomalies (these are engine bugs, not results):")
        parts += [f"    {a}" for a in anomalies[:20]]
        if len(anomalies) > 20:
            parts.append(f"    ... and {len(anomalies) - 20} more")
    return "\n".join(parts)


def suite_summary(reports: Sequence[object]) -> str:
    rows = []
    for report in reports:
        name = getattr(report, "name", "?")
        failures = getattr(report, "failures", 0)
        seconds = getattr(report, "seconds", 0.0)
        summary = getattr(report, "summary", {}) or {}
        detail = ", ".join(f"{k}={v}" for k, v in summary.items()
                           if k in ("solved", "total", "percent", "nodes", "nps",
                                    "knps", "avg_depth", "min_depth", "positions",
                                    "mnps", "depth"))
        rows.append([name, "ok" if not failures else f"{failures} FAILED",
                     f"{seconds:.1f}s", detail])
    return table(["suite", "result", "time", "detail"], rows,
                 right=[False, False, True, False])

"""Configuration: engine specifications, time controls, adjudication rules.

The configuration lives in ``testsuite/suite.toml``.  TOML is used instead of
YAML because the repository's .gitignore excludes ``*.yml`` and because
``tomllib`` is in the standard library.
"""
from __future__ import annotations

import copy
import os
import re
import shlex
import tomllib
from dataclasses import dataclass, field, replace
from typing import Any, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
TS_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(TS_DIR)
DEFAULT_CONFIG = os.path.join(TS_DIR, "suite.toml")


def _abspath(path: str) -> str:
    """Resolve a path from the configuration against the repository root."""
    path = os.path.expanduser(path)
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(ROOT, path))


# ---------------------------------------------------------------- time control


@dataclass(frozen=True)
class TimeControl:
    """What a single search is allowed to use.

    Either a real clock (``base`` plus ``inc``), or a fixed per-move budget
    (movetime / depth / nodes).  Only clock-based controls can expose time
    management bugs, so they are the default for matches.
    """

    base_ms: int = 0
    inc_ms: int = 0
    movetime_ms: int = 0
    depth: int = 0
    nodes: int = 0
    movestogo: int = 0

    @property
    def is_clock(self) -> bool:
        return self.base_ms > 0

    @property
    def label(self) -> str:
        if self.is_clock:
            moves = f"{self.movestogo}/" if self.movestogo else ""
            return f"{moves}{self.base_ms / 1000:g}+{self.inc_ms / 1000:g}"
        if self.movetime_ms:
            return f"movetime={self.movetime_ms}ms"
        if self.depth:
            return f"depth={self.depth}"
        if self.nodes:
            return f"nodes={self.nodes}"
        return "unlimited"

    def __str__(self) -> str:
        return self.label

    @property
    def expected_ms_per_game(self) -> int:
        """Rough wall-clock budget for one game, used only for progress output."""
        if self.is_clock:
            return 2 * (self.base_ms + 80 * self.inc_ms)
        if self.movetime_ms:
            return 160 * self.movetime_ms
        return 0


_DURATION = re.compile(r"^(?P<num>[0-9]*\.?[0-9]+)\s*(?P<unit>ms|s|m)?$", re.I)


def _duration_ms(text: str, default_unit: str = "s") -> int:
    """Parse ``"250ms"``, ``"1.5"``, ``"2s"``, ``"3m"`` into milliseconds."""
    m = _DURATION.match(text.strip())
    if not m:
        raise ValueError(f"cannot read a duration from {text!r}")
    value = float(m.group("num"))
    unit = (m.group("unit") or default_unit).lower()
    return int(round(value * {"ms": 1, "s": 1000, "m": 60000}[unit]))


def parse_time_control(text: str) -> TimeControl:
    """Parse a time control.

    Accepted forms::

        10+0.1          10 seconds base, 100 ms increment
        60              60 seconds, no increment
        40/300+0        40 moves in 300 seconds
        movetime=500ms  fixed 500 ms per move (also: mt=500ms, 500ms)
        depth=10        fixed depth
        nodes=200000    fixed node count
    """
    text = str(text).strip()
    low = text.lower()

    for prefix in ("movetime=", "mt=", "st="):
        if low.startswith(prefix):
            rest = text[len(prefix):]
            unit = "s" if prefix == "st=" else "ms"
            return TimeControl(movetime_ms=_duration_ms(rest, unit))
    if low.startswith("depth="):
        return TimeControl(depth=int(text[6:]))
    if low.startswith("nodes="):
        return TimeControl(nodes=int(text[6:].replace("_", "")))
    if low.endswith("ms"):
        return TimeControl(movetime_ms=_duration_ms(text, "ms"))

    movestogo = 0
    if "/" in text:
        head, text = text.split("/", 1)
        movestogo = int(head)
    base, _, inc = text.partition("+")
    return TimeControl(base_ms=_duration_ms(base), inc_ms=_duration_ms(inc or "0"),
                       movestogo=movestogo)


def parse_move_budget(text: str) -> TimeControl:
    """Per-position budget for the suites that search one position at a time.

    Same syntax as :func:`parse_time_control`, except that a bare duration
    means "this much per move" rather than "this much for the whole game" --
    `3s` here is three seconds of thinking, not a three second game.
    """
    low = str(text).strip().lower()
    if low.startswith(("depth=", "nodes=", "movetime=", "mt=", "st=")) or "+" in low:
        return parse_time_control(text)
    return TimeControl(movetime_ms=_duration_ms(str(text), "s"))


# --------------------------------------------------------------- engine specs


@dataclass
class EngineSpec:
    """Everything needed to start one UCI engine and know how strong it is."""

    name: str
    command: list[str]
    workdir: Optional[str] = None
    options: dict[str, Any] = field(default_factory=dict)
    #: known rating on the configured scale; opponents need one, the engine
    #: under test does not (its rating is what we are measuring).
    elo: Optional[float] = None
    #: a per-engine search limit, overriding the match time control.  Used for
    #: fixed-strength opponents ("Stockfish at 10k nodes per move").
    time_control: Optional[TimeControl] = None
    note: str = ""
    #: how this engine spells the two non-UCI commands the suites use.
    #: Stockfish answers ``go perft N``; ce.exe answers ``perft N``.
    perft_command: str = "go perft"
    bench_command: str = "bench"

    def resolved_command(self) -> list[str]:
        cmd = list(self.command)
        cmd[0] = _abspath(cmd[0])
        return cmd

    def resolved_workdir(self) -> str:
        if self.workdir:
            return _abspath(self.workdir)
        # Default to the binary's own directory: ce.exe loads net.bin from the
        # current directory, so this makes EvalMode=nnue work unprompted.
        return os.path.dirname(self.resolved_command()[0]) or ROOT

    def exists(self) -> bool:
        return os.path.isfile(self.resolved_command()[0])

    def with_overrides(self, options: Optional[dict[str, Any]] = None,
                       name: Optional[str] = None,
                       elo: Optional[float] = None,
                       time_control: Optional[TimeControl] = None) -> "EngineSpec":
        out = replace(self, options=dict(self.options))
        if options:
            out.options.update(options)
        if name:
            out.name = name
        if elo is not None:
            out.elo = elo
        if time_control is not None:
            out.time_control = time_control
        return out

    @property
    def id(self) -> str:
        """Name plus the options that differ from the configured defaults."""
        return self.name


def _coerce_option(text: str) -> Any:
    low = text.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def parse_spec_overrides(text: str) -> tuple[str, dict[str, Any]]:
    """Split ``"sf-1600:Threads=2,Hash=128"`` into a name and UCI options."""
    name, _, rest = text.partition(":")
    options: dict[str, Any] = {}
    for part in rest.split(","):
        part = part.strip()
        if not part:
            continue
        key, _, value = part.partition("=")
        options[key.strip()] = _coerce_option(value.strip())
    return name.strip(), options


# ----------------------------------------------------------------- whole file


@dataclass
class Adjudication:
    """When to stop a game early.  Mirrors cutechess-cli's options."""

    max_plies: int = 400
    draw_plies: int = 10          # consecutive plies within draw_score
    draw_score: int = 8           # centipawns
    draw_after_ply: int = 60      # never adjudicate a draw before this ply
    resign_plies: int = 6         # consecutive plies below resign_score
    resign_score: int = 900       # centipawns, from the losing side's view
    resign_enabled: bool = True
    draw_enabled: bool = True


@dataclass
class Config:
    path: str
    engine: EngineSpec
    referee: Optional[EngineSpec]
    opponents: list[EngineSpec]
    time_control: TimeControl
    concurrency: int
    results_dir: str
    suites_dir: str
    adjudication: Adjudication
    move_overhead_ms: int
    raw: dict[str, Any]

    def all_specs(self) -> dict[str, EngineSpec]:
        out = {self.engine.name: self.engine}
        if self.referee is not None:
            out[self.referee.name] = self.referee
        for opponent in self.opponents:
            out[opponent.name] = opponent
        return out

    def resolve(self, text: str) -> EngineSpec:
        """Turn ``"sf-1600:Hash=128"`` into a concrete :class:`EngineSpec`."""
        name, overrides = parse_spec_overrides(text)
        specs = self.all_specs()
        if name not in specs:
            known = ", ".join(sorted(specs))
            raise SystemExit(f"unknown engine {name!r}; configured engines are: {known}")
        spec = specs[name]
        if not overrides:
            return copy.deepcopy(spec)
        suffix = ",".join(f"{k}={v}" for k, v in overrides.items())
        return spec.with_overrides(overrides, name=f"{name}[{suffix}]")

    def opponents_by_name(self, names: Optional[list[str]]) -> list[EngineSpec]:
        if not names:
            return [copy.deepcopy(o) for o in self.opponents]
        return [self.resolve(n) for n in names]


def _engine_spec(name: str, table: dict[str, Any]) -> EngineSpec:
    command = table.get("command")
    if not command:
        raise SystemExit(f"engine {name!r} has no 'command'")
    if isinstance(command, str):
        command = shlex.split(command, posix=False)
    tc = table.get("time_control")
    return EngineSpec(
        name=str(table.get("name", name)),
        command=[str(c).strip('"') for c in command],
        workdir=table.get("workdir") or None,
        options=dict(table.get("options") or {}),
        elo=float(table["elo"]) if table.get("elo") is not None else None,
        time_control=parse_time_control(tc) if tc else None,
        note=str(table.get("note", "")),
        perft_command=str(table.get("perft_command", "go perft")),
        bench_command=str(table.get("bench_command", "bench")),
    )


def load(path: Optional[str] = None) -> Config:
    path = path or DEFAULT_CONFIG
    if not os.path.isfile(path):
        raise SystemExit(f"no configuration at {path}")
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    engine = _engine_spec("engine", raw.get("engine") or {})
    referee = _engine_spec("referee", raw["referee"]) if raw.get("referee") else None

    opponents = [_engine_spec(f"opponent{i}", t)
                 for i, t in enumerate(raw.get("opponent") or [])]
    seen: set[str] = set()
    for opponent in opponents:
        if opponent.name in seen:
            raise SystemExit(f"two opponents are both called {opponent.name!r}")
        seen.add(opponent.name)

    defaults = raw.get("defaults") or {}
    paths = raw.get("paths") or {}
    adj = Adjudication(**{k: v for k, v in (raw.get("adjudication") or {}).items()
                          if k in Adjudication.__dataclass_fields__})

    return Config(
        path=path,
        engine=engine,
        referee=referee,
        opponents=opponents,
        time_control=parse_time_control(defaults.get("time_control", "10+0.1")),
        concurrency=max(1, int(defaults.get("concurrency", 1))),
        results_dir=_abspath(paths.get("results", "testsuite/results")),
        suites_dir=_abspath(paths.get("suites", "testsuite/suites")),
        adjudication=adj,
        move_overhead_ms=int(defaults.get("move_overhead_ms", 100)),
        raw=raw,
    )

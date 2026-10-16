"""Persisting results, so a rating can be built up over many sessions.

Every match is appended to ``results/history.jsonl`` together with a
fingerprint of the engine that played it: its name, its UCI options and the
size and timestamp of the binary.  The rating is then fitted over all stored
games that share a fingerprint and a time control, which keeps results from
two different builds out of the same average.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from . import elo as elolib
from .config import EngineSpec

HISTORY = "history.jsonl"


def fingerprint(spec: EngineSpec) -> dict[str, Any]:
    """Identify the exact engine that played, so builds do not get mixed."""
    path = spec.resolved_command()[0]
    try:
        stat = os.stat(path)
        binary = f"{stat.st_size}:{int(stat.st_mtime)}"
    except OSError:
        binary = "missing"
    options = {k: v for k, v in sorted(spec.options.items())}
    blob = json.dumps({"name": spec.name, "options": options, "binary": binary},
                      sort_keys=True)
    return {
        "name": spec.name,
        "options": options,
        "binary": binary,
        "hash": hashlib.sha1(blob.encode()).hexdigest()[:12],
    }


@dataclass
class Record:
    """One stored match."""

    ts: float
    kind: str
    engine: dict[str, Any]
    opponent: str
    opponent_elo: Optional[float]
    time_control: str
    wins: int
    draws: int
    losses: int
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def games(self) -> int:
        return self.wins + self.draws + self.losses

    @property
    def when(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.ts))

    def as_opponent_result(self) -> elolib.OpponentResult:
        return elolib.OpponentResult(self.opponent, self.opponent_elo or 0.0,
                                     self.wins, self.draws, self.losses)

    def to_json(self) -> dict[str, Any]:
        return {"ts": self.ts, "kind": self.kind, "engine": self.engine,
                "opponent": self.opponent, "opponent_elo": self.opponent_elo,
                "time_control": self.time_control, "wins": self.wins,
                "draws": self.draws, "losses": self.losses, **self.extra}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Optional["Record"]:
        try:
            known = {"ts", "kind", "engine", "opponent", "opponent_elo",
                     "time_control", "wins", "draws", "losses"}
            return cls(
                ts=float(data.get("ts") or 0.0),
                kind=str(data.get("kind", "match")),
                engine=data.get("engine") or {},
                opponent=str(data["opponent"]),
                opponent_elo=(float(data["opponent_elo"])
                              if data.get("opponent_elo") is not None else None),
                time_control=str(data.get("time_control", "")),
                wins=int(data.get("wins", 0)),
                draws=int(data.get("draws", 0)),
                losses=int(data.get("losses", 0)),
                extra={k: v for k, v in data.items() if k not in known},
            )
        except (KeyError, TypeError, ValueError):
            return None


class Store:
    def __init__(self, directory: str) -> None:
        self.dir = directory
        os.makedirs(directory, exist_ok=True)

    @property
    def history_path(self) -> str:
        return os.path.join(self.dir, HISTORY)

    # ---- writing ----

    def append_match(self, engine: EngineSpec, opponent: str,
                     opponent_elo: Optional[float], time_control: str,
                     wins: int, draws: int, losses: int,
                     kind: str = "match", **extra: Any) -> Record:
        record = Record(ts=time.time(), kind=kind, engine=fingerprint(engine),
                        opponent=opponent, opponent_elo=opponent_elo,
                        time_control=time_control, wins=wins, draws=draws,
                        losses=losses, extra=extra)
        with open(self.history_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.to_json()) + "\n")
        return record

    def save_json(self, name: str, payload: Any) -> str:
        path = os.path.join(self.dir, name)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
        return path

    def run_path(self, prefix: str, extension: str = "json") -> str:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return os.path.join(self.dir, f"{prefix}-{stamp}.{extension}")

    # ---- reading ----

    def records(self) -> list[Record]:
        if not os.path.isfile(self.history_path):
            return []
        out: list[Record] = []
        with open(self.history_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                record = Record.from_json(data)
                if record is not None:
                    out.append(record)
        return out

    def filtered(self, *, engine: Optional[EngineSpec] = None,
                 engine_name: Optional[str] = None,
                 time_control: Optional[str] = None,
                 same_build: bool = True,
                 since: Optional[float] = None) -> list[Record]:
        """Stored matches matching the filters, newest last."""
        want = fingerprint(engine) if engine is not None else None
        out: list[Record] = []
        for record in self.records():
            if record.opponent_elo is None:
                continue
            if time_control and record.time_control != time_control:
                continue
            if since is not None and record.ts < since:
                continue
            name = record.engine.get("name")
            if engine_name and name != engine_name:
                continue
            if want is not None:
                if same_build:
                    if record.engine.get("hash") != want["hash"]:
                        continue
                elif name != want["name"]:
                    continue
            out.append(record)
        return out


def rating_from_records(records: Iterable[Record],
                        confidence: float = 0.95) -> elolib.RatingEstimate:
    merged = elolib.merge_results(r.as_opponent_result() for r in records)
    return elolib.fit_rating(merged, confidence)

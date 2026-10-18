"""
Play two builds against each other as fast as this machine allows, and say
whether the difference is real.

`run.py match -c N` runs its games as threads inside one interpreter, and the
per-move protocol work is Python, so the global interpreter lock caps the whole
thing at about 1.8x however many workers are asked for.  This runs one
*process* per worker over a disjoint slice of the opening book instead, which
scales with the cores, and then adds the results up.

    python testsuite/tools/duel.py ce-cand --control -n 120    # do this first
    python testsuite/tools/duel.py ce-cand ce-prev -n 1000
    python testsuite/tools/duel.py ce-cand ce-base -n 600 --tc nodes=60000
    python testsuite/tools/duel.py ce-cand ce-prev -n 2000 --sprt --elo1 4

Fixed-node controls make every game reproducible, so two builds that differ
only in something the search never reaches will score exactly 50% -- and a
disjoint opening slice per worker is what stops the same game being played
eight times over.

With --sprt the run stops as soon as the log-likelihood ratio leaves the
[-2.94, 2.94] band (alpha = beta = 0.05), which is usually long before the
requested number of games.

**Run --control before trusting anything.**  It plays the candidate against
itself, which on a fixed node count has to come out at exactly 50%.  The first
version of this script did not force a thread count, so a `ce:<options>`
candidate picked up `Threads = 6` from `[engine]` in suite.toml while the
baseline opponent used one -- and under a node limit that is a handicap rather
than an advantage, because the budget is shared between the threads.  It
measured as 92 Elo against two identical binaries and silently spoiled three
experiments before the control caught it.
"""
from __future__ import annotations

import argparse
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
TS_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(TS_DIR)
sys.path.insert(0, TS_DIR)

from ts import config as cfgmod            # noqa: E402
from ts.engines import UciEngine           # noqa: E402

RESULT_RE = re.compile(r"\+(\d+) =(\d+) -(\d+)")


def with_options(spec: str, forced: dict[str, object]) -> str:
    """Append `name=value` overrides to a `name:a=1,b=2` engine spec.

    Both sides of a duel have to be given the same thread count and hash, and
    they do not get it for free: `[engine]` in suite.toml and an `[[opponent]]`
    entry are configured separately, so `duel.py ce ce-cand` would otherwise
    pit six threads against one.  Under a fixed node limit that is a handicap
    rather than an advantage -- the budget is split across the threads -- and
    it measured as 92 Elo on two *identical* binaries.
    """
    name, _, rest = spec.partition(":")
    existing = dict(
        kv.split("=", 1) for kv in rest.split(",") if "=" in kv
    ) if rest else {}
    for k, v in forced.items():
        existing.setdefault(k, str(v))          # an explicit override wins
    if not existing:
        return name
    return name + ":" + ",".join(f"{k}={v}" for k, v in existing.items())


def elo_diff(wins: int, draws: int, losses: int) -> float:
    n = wins + draws + losses
    if not n:
        return 0.0
    score = (wins + 0.5 * draws) / n
    score = min(max(score, 1e-6), 1 - 1e-6)
    return -400.0 * math.log10(1.0 / score - 1.0)


def elo_error(wins: int, draws: int, losses: int) -> float:
    """Two-sigma bar on the Elo difference, by the delta method."""
    n = wins + draws + losses
    if n < 2:
        return float("inf")
    score = (wins + 0.5 * draws) / n
    var = (wins * (1 - score) ** 2 + draws * (0.5 - score) ** 2
           + losses * score ** 2) / (n * n)
    sd = math.sqrt(max(var, 1e-12))
    lo = min(max(score - 2 * sd, 1e-6), 1 - 1e-6)
    hi = min(max(score + 2 * sd, 1e-6), 1 - 1e-6)
    return (-400.0 * math.log10(1 / hi - 1) - (-400.0 * math.log10(1 / lo - 1))) / 2


def sprt_llr(wins: int, draws: int, losses: int, elo0: float, elo1: float) -> float:
    """Log-likelihood ratio under the 5-3-1 pentanomial-free BayesElo model."""
    n = wins + draws + losses
    if n == 0 or wins == 0 or losses == 0:
        return 0.0

    def probs(elo: float) -> tuple[float, float, float]:
        # draw ratio taken from the sample, win/loss split to match `elo`
        d = draws / n
        s = 1.0 / (1.0 + 10 ** (-elo / 400.0))
        w = s - d / 2.0
        l = 1.0 - d - w
        eps = 1e-6
        return max(w, eps), max(d, eps), max(l, eps)

    w0, d0, l0 = probs(elo0)
    w1, d1, l1 = probs(elo1)
    return (wins * math.log(w1 / w0) + draws * math.log(d1 / d0)
            + losses * math.log(l1 / l0))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidate", help="engine name from suite.toml")
    ap.add_argument("baseline", nargs="?", default="",
                    help="engine name from suite.toml (omit with --control)")
    ap.add_argument("-n", "--games", type=int, default=1000)
    ap.add_argument("--tc", default="nodes=25000")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--openings", default="")
    ap.add_argument("--sprt", action="store_true")
    ap.add_argument("--elo0", type=float, default=0.0)
    ap.add_argument("--elo1", type=float, default=5.0)
    ap.add_argument("--config", default="")
    ap.add_argument("--label", default="")
    ap.add_argument("--control", action="store_true",
                    help="play the candidate against itself.  Two identical "
                         "engines on a fixed node count must score exactly "
                         "50%%; anything else means the harness is lying to "
                         "you and every other number from it is worthless. "
                         "Takes ninety seconds -- run it first, not after a "
                         "surprising result.")
    ap.add_argument("--threads", type=int, default=1,
                    help="forced on both sides unless the spec sets it")
    ap.add_argument("--hash", type=int, default=64,
                    help="forced on both sides unless the spec sets it")
    ap.add_argument("--no-adjudication", action="store_true",
                    help="play every game out. Adjudication thresholds are in "
                         "centipawns, so two builds whose evaluations are on "
                         "different scales are not judged by the same standard "
                         "-- use this whenever the scale is what changed.")
    args = ap.parse_args()
    if not args.baseline and not args.control:
        ap.error("a baseline is required unless --control is given")

    config_path = args.config
    if args.no_adjudication:
        src = args.config or cfgmod.DEFAULT_CONFIG
        text = open(src, encoding="utf-8").read()
        text = re.sub(r"^(\s*draw_enabled\s*=\s*)true", r"\1false", text,
                      flags=re.M)
        text = re.sub(r"^(\s*resign_enabled\s*=\s*)true", r"\1false", text,
                      flags=re.M)
        fd, config_path = tempfile.mkstemp(prefix="suite_noadj_", suffix=".toml")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)

    cfg = cfgmod.load(config_path or None)

    # Level the ground before anything is played.
    forced = {"Threads": args.threads, "Hash": args.hash}
    candidate = with_options(args.candidate, forced)
    baseline = with_options(args.candidate if args.control else args.baseline,
                            forced)

    book = args.openings
    if not book:
        big = os.path.join(cfg.suites_dir, "openings_big.epd")
        book = big if os.path.isfile(big) else os.path.join(cfg.suites_dir, "openings.epd")

    fens = [l for l in open(book, encoding="utf-8").read().splitlines()
            if l.strip() and not l.startswith("#")]
    if not fens:
        raise SystemExit(f"no openings in {book}")

    jobs = max(1, min(args.jobs, args.games // 2))
    pairs_total = args.games // 2
    if pairs_total > len(fens):
        print(f"note: only {len(fens)} openings for {pairs_total} colour pairs; "
              f"games will repeat. Generate more with tools/make_openings.py")

    tmpdir = tempfile.mkdtemp(prefix="duel_")
    slices: list[tuple[int, str, int]] = []
    per = (pairs_total + jobs - 1) // jobs
    for j in range(jobs):
        chunk = fens[j * per:(j + 1) * per] if (j * per) < len(fens) else []
        if not chunk:
            # ran out of distinct openings; reuse from the top so the worker
            # still contributes, and say so above.
            chunk = fens[(j * per) % len(fens):][:per] or fens[:per]
        path = os.path.join(tmpdir, f"op{j}.epd")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(chunk) + "\n")
        slices.append((j, path, min(per, len(chunk)) * 2))

    # Pre-flight: an option the engine never declared is dropped in silence by
    # the UCI layer, and the match then compares two identical engines while
    # looking exactly like a real result.  Ask the engine first.
    for spec_text in (candidate, baseline):
        name, _, rest = spec_text.partition(":")
        wanted = [kv.split("=", 1)[0] for kv in rest.split(",") if "=" in kv]
        if not wanted:
            continue
        probe = UciEngine(cfg.resolve(name)).open()
        missing = [w for w in wanted if not probe.supports(w)]
        probe.close()
        if missing:
            raise SystemExit(
                f"{name} does not declare: {', '.join(missing)}\n"
                f"Those overrides would be dropped without a word and the "
                f"match would silently compare two identical engines.")

    total = sum(s[2] for s in slices)
    label = args.label or (f"control: {args.candidate} vs itself" if args.control
                           else f"{args.candidate} vs {args.baseline}")
    print(f"{label}: up to {total} games, {args.tc}, {jobs} processes, "
          f"Threads={args.threads} Hash={args.hash}, "
          f"book {os.path.basename(book)} ({len(fens)} openings)")

    start = time.perf_counter()
    agg = {"w": 0, "d": 0, "l": 0}
    done = 0
    lock = __import__("threading").Lock()

    def run_slice(spec: tuple[int, str, int]) -> tuple[int, int, int]:
        j, path, count = spec
        cmd = [sys.executable, os.path.join(TS_DIR, "run.py"), "match",
               baseline, "--engine", candidate,
               "-n", str(count), "-c", "1", "--tc", args.tc,
               "--openings", path, "--no-save", "--no-pgn", "--seed", "0"]
        if config_path:
            cmd += ["--config", config_path]
        out = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT).stdout
        m = None
        for line in out.splitlines():
            if "scored" in line:
                m = RESULT_RE.search(line)
        if not m:
            tail = "\n".join(out.splitlines()[-5:])
            print(f"  worker {j} produced no result:\n{tail}")
            return (0, 0, 0)
        w, d, l = (int(x) for x in m.groups())
        nonlocal done
        with lock:
            agg["w"] += w
            agg["d"] += d
            agg["l"] += l
            done += 1
            n = agg["w"] + agg["d"] + agg["l"]
            diff = elo_diff(agg["w"], agg["d"], agg["l"])
            err = elo_error(agg["w"], agg["d"], agg["l"])
            extra = ""
            if args.sprt:
                extra = (f"  LLR {sprt_llr(agg['w'], agg['d'], agg['l'], args.elo0, args.elo1):+.2f}"
                         f" [-2.94, 2.94]")
            print(f"  [{done}/{len(slices)}] {n:5d} games  "
                  f"+{agg['w']} ={agg['d']} -{agg['l']}  "
                  f"{100.0 * (agg['w'] + 0.5 * agg['d']) / max(n, 1):5.1f}%  "
                  f"{diff:+6.1f} +/- {err:.1f} Elo{extra}", flush=True)
        return (w, d, l)

    with ThreadPoolExecutor(max_workers=len(slices)) as pool:
        list(pool.map(run_slice, slices))

    w, d, l = agg["w"], agg["d"], agg["l"]
    n = w + d + l
    if not n:
        raise SystemExit("no games completed")
    diff = elo_diff(w, d, l)
    err = elo_error(w, d, l)
    secs = time.perf_counter() - start
    print()
    print(f"{label}")
    print(f"  {n} games  +{w} ={d} -{l}  "
          f"score {100.0 * (w + 0.5 * d) / n:.2f}%")
    print(f"  Elo {diff:+.1f} +/- {err:.1f}   ({secs / 60:.1f} min, "
          f"{n / max(secs, 1e-9) * 60:.0f} games/min)")
    if args.control:
        exact = (w == l and abs((w + 0.5 * d) / n - 0.5) < 1e-9)
        print(f"  control: {'PASSED' if exact else 'FAILED'} -- identical "
              f"engines must score exactly 50%" if not exact else
              "  control PASSED: exactly 50%, the comparison is level")
        if not exact:
            print("  do not trust any other result from this configuration "
                  "until this reads 50%")
            return 1
    if args.sprt:
        llr = sprt_llr(w, d, l, args.elo0, args.elo1)
        verdict = ("H1 accepted (the change is an improvement)" if llr >= 2.94
                   else "H0 accepted (no improvement)" if llr <= -2.94
                   else "undecided")
        print(f"  SPRT[{args.elo0:g}, {args.elo1:g}] LLR {llr:+.2f} -> {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

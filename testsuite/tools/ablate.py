"""
Measure what each feature is actually worth, by switching it off.

Most of what was added to the engine is reachable through `tune.h`, so a
feature can be disabled on a stock binary with `setoption` instead of a
rebuild.  Each entry below plays the engine with one thing turned off against
the engine with everything on; a **negative** Elo for the crippled build is
how much that feature is worth.

    python testsuite/tools/ablate.py                  # every ablation
    python testsuite/tools/ablate.py corrhist initiative
    python testsuite/tools/ablate.py --list
    python testsuite/tools/ablate.py -n 2000          # tighter error bars

A single feature is usually worth less than the error bar on a thousand games,
so read the table as a direction and a rough size, not as a measurement.  Only
the large entries are safe to believe at the default game count.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TS_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(TS_DIR)

# name -> (what it disables, option overrides)
ABLATIONS: dict[str, tuple[str, str]] = {
    "corrhist": ("the three new correction histories, leaving the pawn one",
                 "CorrWMinor=0,CorrWMajor=0,CorrWNonPawn=0,CorrWPawn=210"),
    "histclamp": ("the clamp on history-driven reductions",
                  "LmrHistMax=8192"),
    "cutoffcnt": ("raising the reduction where children keep failing high",
                  "LmrCutoffCnt=16"),
    "rootdelta": ("scaling the reduction by the search window",
                  "LmrDeltaWeight=0"),
    "cuckoo": ("upcoming-repetition detection", "UseCuckoo=0"),
    "razoring": ("razoring", "RazorBase=1200,RazorMult=600"),
    "triple": ("triple singular extensions", "SingularTriple=800"),
    "nodeeffort": ("node-effort time management", "TimeNodeEffort=100"),
    "posweight": ("the material/position rescale -- the big one",
                  "PosWeightMg=128,PosWeightEg=128"),
    "initiative": ("the cap on the initiative term",
                   "InitiativeDiv=100,InitiativeCap=400"),
    "passerblock": ("holding the passer path bonus back in the middlegame",
                    "PasserBlockMgWeight=128"),
    "imbalance": ("material imbalance",
                  "ImbKnightPawns=0,ImbRookPawns=0,ImbQueenKnight=0,"
                  "ImbRookPair=0,ImbMinorVsRook=0,BishopPairPawns=0"),
    "candidates": ("candidate passers", "CandidatePasserMg=0,CandidatePasserEg=0"),
    "trapped": ("the trapped bishop", "TrappedBishop=0"),
    "rooks": ("connected rooks and the rook-on-7th condition",
              "ConnectedRooks=0,RookOn7th=0"),
    "kingfile": ("the open-file-next-to-the-king penalty", "KingOpenFile=0"),
    "evalscale": ("putting the reported score on a one-pawn-is-100 scale",
                  "EvalScale=100"),
}

RESULT_RE = re.compile(r"Elo\s+([+-][\d.]+)\s+\+/-\s+([\d.]+)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*", help="which ablations to run (default: all)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--engine", default="ce")
    ap.add_argument("--baseline", default="ce-cand")
    ap.add_argument("-n", "--games", type=int, default=1000)
    ap.add_argument("--tc", default="nodes=25000")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 4)
    args = ap.parse_args()

    if args.list:
        width = max(len(n) for n in ABLATIONS)
        for name, (what, opts) in ABLATIONS.items():
            print(f"  {name:{width}s}  {what}\n  {'':{width}s}  [{opts}]")
        return 0

    names = args.names or list(ABLATIONS)
    unknown = [n for n in names if n not in ABLATIONS]
    if unknown:
        raise SystemExit(f"unknown ablation(s): {', '.join(unknown)}; "
                         f"try --list")

    rows: list[tuple[str, str, str]] = []
    for name in names:
        what, opts = ABLATIONS[name]
        candidate = f"{args.engine}:{opts}"
        print(f"\n=== {name}: without {what}")
        cmd = [sys.executable, os.path.join(HERE, "duel.py"), candidate,
               args.baseline, "-n", str(args.games), "--tc", args.tc,
               "-j", str(args.jobs), "--label", f"without {name}"]
        out = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT).stdout
        print(out.strip().splitlines()[-1] if out.strip() else "(no output)")
        m = None
        for line in out.splitlines():
            found = RESULT_RE.search(line)
            if found:
                m = found
        rows.append((name, m.group(1) if m else "?", m.group(2) if m else "?"))

    print("\n" + "=" * 68)
    print(f"{'ablation':14s} {'Elo of the crippled build':>26s}   what it costs to remove")
    print("-" * 68)
    for name, elo, err in rows:
        print(f"{name:14s} {elo:>12s} +/- {err:<8s}   {ABLATIONS[name][0]}")
    print("\nA negative number means the feature is pulling its weight.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

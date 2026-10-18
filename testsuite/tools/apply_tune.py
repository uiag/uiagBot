"""
Write tuned values back into `engine/tune.h` as the new defaults.

A tuning run produces a `name value` list; folding fifty of those into the
macro table by hand is error-prone busywork.  This does it, keeping the column
layout intact, and refuses to touch anything it was not asked about.

    python testsuite/tools/apply_tune.py testsuite/results/tuned_eval.txt
    python testsuite/tools/apply_tune.py tuned_search.txt --dry-run

Only run it on values that have already won a match -- a tuning run is a
hypothesis, and `tune.h` is where the engine's accepted behaviour lives.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TUNE_H = os.path.join(ROOT, "engine", "tune.h")

LINE_RE = re.compile(
    r"^(?P<head>\s*TUNE\(\s*)(?P<name>\w+)(?P<mid>\s*,\s*)(?P<def>-?\d+)(?P<tail>\s*,.*)$")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("values", help="a file of `name value` lines")
    ap.add_argument("--tune-h", default=TUNE_H)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    wanted: dict[str, int] = {}
    with open(args.values, encoding="utf-8") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) == 2:
                wanted[parts[0]] = int(parts[1])
    if not wanted:
        raise SystemExit(f"no `name value` lines in {args.values}")

    with open(args.tune_h, encoding="utf-8") as fh:
        lines = fh.read().splitlines(keepends=True)

    seen: set[str] = set()
    changed = 0
    out = []
    for line in lines:
        m = LINE_RE.match(line.rstrip("\n"))
        if not m or m.group("name") not in wanted:
            out.append(line)
            continue
        name = m.group("name")
        seen.add(name)
        old = int(m.group("def"))
        new = wanted[name]
        if old == new:
            out.append(line)
            continue
        # keep the width of the default column so the table stays readable
        width = len(m.group("def"))
        text = str(new)
        pad = " " * max(0, width - len(text))
        rebuilt = (m.group("head") + name + m.group("mid") + pad + text
                   + m.group("tail"))
        # the trailing backslash of the macro continuation must stay last
        out.append(rebuilt + ("\n" if line.endswith("\n") else ""))
        print(f"  {name:22s} {old:7d} -> {new:7d}")
        changed += 1

    missing = sorted(set(wanted) - seen)
    if missing:
        print(f"not found in tune.h and left alone: {', '.join(missing)}",
              file=sys.stderr)

    if args.dry_run:
        print(f"\n{changed} defaults would change (dry run)")
        return 0
    with open(args.tune_h, "w", encoding="utf-8") as fh:
        fh.writelines(out)
    print(f"\n{changed} defaults updated in {args.tune_h}")
    print("rebuild with: python engine/build.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

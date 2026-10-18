"""
Build the labelled position set the evaluation is tuned against.

Positions come from the engine playing itself at a low node count out of the
opening book, which puts them where the engine actually spends its time; the
label is the referee's verdict on each one.  Positions in check are skipped,
and so is everything before `--skip-plies`, because an opening book position
says nothing about the evaluation.

    python testsuite/tools/make_evalset.py                 # ~120k positions
    python testsuite/tools/make_evalset.py -n 30000 --depth 8

The output is one `fen;score` per line from White's point of view: either the
referee's score in centipawns, capped at `--cap`, or -- by default --
`+cap`/`0`/`-cap` for the result of the game the position came from.

The game result is the better label despite being far noisier.  A referee score
already contains a search, and a static evaluation cannot reproduce one, so
fitting to it drags every weight towards zero: the error falls because a
flatter evaluation is better *calibrated*, while playing strength lives in the
discrimination that is being thrown away.  Fitting this engine to referee
scores improved the held-back loss by 23% and lost 80 Elo over 1200 games.
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor

import chess

HERE = os.path.dirname(os.path.abspath(__file__))
TS_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(TS_DIR)
sys.path.insert(0, TS_DIR)

from ts import config as cfgmod            # noqa: E402
from ts.engines import Limit, UciEngine    # noqa: E402


def _worker(args: tuple) -> list[tuple[str, int]]:
    (engine_spec, referee_spec, openings, want, nodes, depth, cap, skip, seed,
     mode) = args
    rng = random.Random(seed)
    player = UciEngine(engine_spec).open()
    referee = UciEngine(referee_spec).open() if mode == "referee" else None
    out: list[tuple[str, int]] = []
    try:
        idx = 0
        # Cycle the slice rather than stopping at the end of it: a worker that
        # runs out of openings would otherwise quietly return far fewer
        # positions than were asked for.  The engine is deterministic, so a
        # second pass over the same opening is only useful because the search
        # is no longer starting from an empty table -- hence the reshuffle.
        while len(out) < want and idx < len(openings) * 6:
            if idx and idx % len(openings) == 0:
                rng.shuffle(openings)
            board = chess.Board(openings[idx % len(openings)])
            idx += 1
            player.new_game()
            seen: list[str] = []
            plies = 0
            while not board.is_game_over(claim_draw=True) and plies < 220:
                res = player.search(board, Limit(nodes=nodes))
                if res.move is None:
                    break
                board.push(res.move)
                plies += 1
                if plies < skip or board.is_check():
                    continue
                if rng.random() < 0.35:
                    seen.append(board.fen())
            if mode == "result":
                # One label for the whole game: who actually won it.  This is
                # the only label that does not already contain a search, which
                # is what makes it usable for fitting a static evaluation --
                # see the note in tune_eval.py.
                outcome = board.outcome(claim_draw=True)
                if outcome is None:
                    continue                      # hit the ply cap, undecided
                if outcome.winner is None:
                    label = 0
                else:
                    label = cap if outcome.winner == chess.WHITE else -cap
                rng.shuffle(seen)
                for fen in seen[:20]:
                    if len(out) >= want:
                        break
                    out.append((fen, label))
                continue

            # label a sample of this game with the referee
            rng.shuffle(seen)
            for fen in seen[:14]:
                if len(out) >= want:
                    break
                b = chess.Board(fen)
                r = referee.analyse(b, Limit(depth=depth))
                if not r.info.has_score:
                    continue
                cp = r.info.score_cp
                if b.turn == chess.BLACK:
                    cp = -cp
                if cp > cap:
                    cp = cap
                if cp < -cap:
                    cp = -cap
                out.append((fen, cp))
        return out
    finally:
        player.close()
        if referee is not None:
            referee.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--count", type=int, default=120000)
    ap.add_argument("--nodes", type=int, default=4000, help="nodes per move in self-play")
    ap.add_argument("--depth", type=int, default=10, help="referee depth for the label")
    ap.add_argument("--cap", type=int, default=1500)
    ap.add_argument("--label", choices=("result", "referee"), default="result",
                    help="`result` labels every position with the outcome of "
                         "the game it came from (unbiased, noisy, free); "
                         "`referee` labels it with the referee's score "
                         "(precise, but it already contains a search)")
    ap.add_argument("--skip-plies", type=int, default=12)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("-o", "--out", default="")
    ap.add_argument("--config", default="")
    args = ap.parse_args()

    cfg = cfgmod.load(args.config or None)
    if cfg.referee is None:
        raise SystemExit("suite.toml has no [referee]")
    engine_spec = cfg.engine.with_overrides({"Threads": 1, "Hash": 16})
    referee_spec = cfg.referee.with_overrides({"Threads": 1, "Hash": 64})

    book = os.path.join(cfg.suites_dir, "openings_big.epd")
    if not os.path.isfile(book):
        book = os.path.join(cfg.suites_dir, "openings.epd")
    fens = [l.split(";")[0].strip() for l in open(book, encoding="utf-8")
            if l.strip() and not l.startswith("#")]
    random.Random(args.seed).shuffle(fens)

    out_path = args.out or os.path.join(cfg.results_dir, "evalset.txt")
    per = (args.count + args.jobs - 1) // args.jobs
    chunk = (len(fens) + args.jobs - 1) // args.jobs
    jobs = [(engine_spec, referee_spec, fens[i * chunk:(i + 1) * chunk], per,
             args.nodes, args.depth, args.cap, args.skip_plies,
             args.seed * 131 + i, args.label)
            for i in range(args.jobs)]

    how = (f"referee depth {args.depth}" if args.label == "referee"
           else "the result of the game")
    print(f"labelling up to {args.count} positions with {args.jobs} jobs "
          f"(self-play at {args.nodes} nodes, labelled by {how})")
    rows: list[tuple[str, int]] = []
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for part in pool.map(_worker, jobs):
            rows.extend(part)
            print(f"  {len(rows)} positions", flush=True)

    seen: set[str] = set()
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    kept = 0
    with open(out_path, "w", encoding="utf-8") as fh:
        for fen, cp in rows:
            key = " ".join(fen.split()[:4])
            if key in seen:
                continue
            seen.add(key)
            fh.write(f"{fen};{cp}\n")
            kept += 1
    print(f"wrote {kept} positions to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

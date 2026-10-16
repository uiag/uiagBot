"""Generate and verify the EPD files in testsuite/suites/.

Nothing in suites/ is written by hand: every solution is produced by the
referee engine from suite.toml (a full-strength Stockfish), so the suites
cannot contain a wrong answer that came from a typo or a half-remembered
published list.

    python testsuite/tools/make_suites.py all
    python testsuite/tools/make_suites.py perft --max-nodes 200000000
    python testsuite/tools/make_suites.py tactics --games 16 --depth 18
    python testsuite/tools/make_suites.py openings

How each suite is built:

* perft    -- the referee's own `go perft` counts, which are independent of the
              engine under test.
* openings -- standard opening lines, kept only when the referee calls the
              position close to equal, so neither colour is favoured.
* tactics  -- candidate positions (a hand-picked list plus positions sampled
              from fast referee self-play) are analysed with MultiPV.  A
              position is kept only when one move -- or a small set of equal
              moves -- is clearly better than everything else, which is what
              makes a suite discriminating rather than just hard.
* mates    -- the same filter, restricted to forced mates, stored with `dm`.
* draws    -- dead-drawn and nearly-dead-drawn endings, labelled by what the
              referee says they are worth.
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess

from ts import config as cfgmod
from ts import epd as epdmod
from ts.engines import EngineProblem, Limit, SearchInfo, UciEngine
from ts.openings import Opening, write_epd

# --------------------------------------------------------------- source data

PERFT_FENS = [
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
    "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
    "r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1",
    "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8",
    "r4rk1/1pp1qppp/p1np1n2/1B2p1b1/4P3/1BN2Q1p/PPPB1PPP/R3K2R w KQ - 0 10",
    "8/5k2/8/2Pp4/2B5/1K6/8/8 w - d6 0 1",
    "8/8/1k6/2b5/2pP4/8/5K2/8 b - d3 0 1",
    "5k2/8/8/8/8/8/8/4K2R w K - 0 1",
    "4k3/8/8/8/8/8/8/4K2R w K - 0 1",
    "8/8/8/8/8/4k3/4P3/4K3 w - - 0 1",
    "4k3/1P6/8/8/8/8/K7/8 w - - 0 1",
    "8/P1k5/K7/8/8/8/8/8 w - - 0 1",
    "K1k5/8/P7/8/8/8/8/8 w - - 0 1",
    "8/k1P5/8/1K6/8/8/8/8 w - - 0 1",
    "8/8/2k5/5q2/5n2/8/5K2/8 b - - 0 1",
    "r1bqkbnr/pppp1ppp/2n5/4p3/B3P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
    "rnbqkb1r/pp1p1ppp/2p5/4P3/2B5/8/PPP1NnPP/RNBQK2R w KQkq - 0 6",
]

#: Positions used by the speed and time-to-depth suites: a spread of opening,
#: middlegame and endgame, so one phase cannot dominate the average.
BENCH_FENS = [
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
    "r1bqkb1r/pp3ppp/2n1pn2/3p4/3P4/2N1PN2/PP3PPP/R1BQKB1R w KQkq - 0 1",
    "r4rk1/1pp1qppp/p1np1n2/1B2p1b1/4P3/1BN2Q1p/PPPB1PPP/R3K2R w KQ - 0 10",
    "4rrk1/pp1n3p/3q2pQ/2p1pb2/2PP4/2P3N1/P2B2PP/4RRK1 b - - 7 19",
    "r1bq1r1k/1pp1n1pp/1p1p4/4p2Q/4Pp2/1BNP4/PPP2PPP/R4RK1 w - - 2 14",
    "2kr3r/pppq1ppp/2n1b3/3p4/3P4/2P1BN2/PP3PPP/R2QK2R w KQ - 1 12",
    "3r1k2/4npp1/1ppr3p/p6P/P2PPPP1/1NR5/5K2/2R5 w - - 0 1",
    "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
    "6k1/6p1/7p/P5P1/1p4KP/2p5/8/8 b - - 0 1",
    "8/8/3P3k/8/1p6/8/1P6/1K3n2 b - - 0 1",
    "r2qkbnr/ppp2ppp/2np4/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 2 6",
    "8/8/4k3/8/8/4K3/8/4Q3 w - - 0 1",
    "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1",
]

#: Standard opening lines in SAN.  Any line that does not parse is reported
#: and dropped; any line the referee does not call roughly equal is dropped
#: too, so the match openings cannot favour one colour.
OPENING_LINES = [
    ("Spanish", "e4 e5 Nf3 Nc6 Bb5 a6"),
    ("Berlin", "e4 e5 Nf3 Nc6 Bb5 Nf6"),
    ("Italian", "e4 e5 Nf3 Nc6 Bc4 Bc5"),
    ("Two Knights", "e4 e5 Nf3 Nc6 Bc4 Nf6"),
    ("Petrov", "e4 e5 Nf3 Nf6 Nxe5 d6"),
    ("Vienna", "e4 e5 Nc3 Nf6 f4 d5"),
    ("Philidor", "e4 e5 Nf3 d6 d4 exd4"),
    ("Scotch", "e4 e5 Nf3 Nc6 d4 exd4"),
    ("Four Knights", "e4 e5 Nf3 Nc6 Nc3 Nf6"),
    ("Bishop's Opening", "e4 e5 Bc4 Nf6 d3 c6"),
    ("Centre Game", "e4 e5 d4 exd4 Qxd4 Nc6"),
    ("King's Gambit", "e4 e5 f4 exf4 Nf3 d5"),
    ("Sicilian Najdorf", "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6"),
    ("Sicilian Classical", "e4 c5 Nf3 Nc6 d4 cxd4 Nxd4 Nf6"),
    ("Sicilian Taimanov", "e4 c5 Nf3 e6 d4 cxd4 Nxd4 Nc6"),
    ("Sicilian Closed", "e4 c5 Nc3 Nc6 g3 g6"),
    ("Alapin", "e4 c5 c3 Nf6 e5 Nd5"),
    ("Rossolimo", "e4 c5 Nf3 Nc6 Bb5 g6"),
    ("Smith-Morra", "e4 c5 d4 cxd4 c3 dxc3"),
    ("French Classical", "e4 e6 d4 d5 Nc3 Nf6"),
    ("French Winawer", "e4 e6 d4 d5 Nc3 Bb4"),
    ("French Tarrasch", "e4 e6 d4 d5 Nd2 c5"),
    ("French Advance", "e4 e6 d4 d5 e5 c5"),
    ("Caro-Kann Main", "e4 c6 d4 d5 Nc3 dxe4 Nxe4 Bf5"),
    ("Caro-Kann Advance", "e4 c6 d4 d5 e5 c5"),
    ("Caro-Kann Exchange", "e4 c6 d4 d5 exd5 cxd5"),
    ("Scandinavian", "e4 d5 exd5 Qxd5 Nc3 Qa5"),
    ("Alekhine", "e4 Nf6 e5 Nd5 d4 d6"),
    ("Pirc", "e4 d6 d4 Nf6 Nc3 g6"),
    ("Modern", "e4 g6 d4 Bg7 Nc3 d6"),
    ("Queen's Gambit Declined", "d4 d5 c4 e6 Nc3 Nf6"),
    ("Slav", "d4 d5 c4 c6 Nf3 Nf6"),
    ("Semi-Slav", "d4 d5 c4 c6 Nc3 Nf6 Nf3 e6"),
    ("Queen's Gambit Accepted", "d4 d5 c4 dxc4 Nf3 Nf6"),
    ("Tarrasch Defence", "d4 d5 c4 e6 Nc3 c5"),
    ("Chigorin", "d4 d5 c4 Nc6 Nf3 Bg4"),
    ("Nimzo-Indian", "d4 Nf6 c4 e6 Nc3 Bb4"),
    ("Queen's Indian", "d4 Nf6 c4 e6 Nf3 b6"),
    ("Catalan", "d4 Nf6 c4 e6 g3 d5"),
    ("King's Indian", "d4 Nf6 c4 g6 Nc3 Bg7 e4 d6"),
    ("Gruenfeld", "d4 Nf6 c4 g6 Nc3 d5"),
    ("Benoni", "d4 Nf6 c4 c5 d5 e6"),
    ("Dutch", "d4 f5 g3 Nf6 Bg2 g6"),
    ("London", "d4 d5 Bf4 Nf6 e3 e6"),
    ("Colle", "d4 d5 Nf3 Nf6 e3 e6"),
    ("English Symmetrical", "c4 c5 Nf3 Nf6 Nc3 e6"),
    ("English Reversed Sicilian", "c4 e5 Nc3 Nf6 g3 d5"),
    ("English Agincourt", "c4 e6 Nc3 d5 d4 Nf6"),
    ("Reti", "Nf3 d5 c4 e6 g3 Nf6"),
    ("King's Indian Attack", "Nf3 Nf6 g3 d5 Bg2 c6"),
    ("Bird", "f4 d5 Nf3 Nf6 e3 g6"),
    ("Nimzo-Larsen", "b3 e5 Bb2 Nc6 e3 Nf6"),
    ("Queen's Pawn Symmetrical", "d4 d5 Nf3 Nf6 c4 c6"),
]

#: Hand-picked tactical candidates (the Win-At-Chess / Bratko-Kopec style
#: positions the project already used).  The referee decides the solutions.
TACTIC_SEEDS = [
    "1k1r4/pp1b1R2/3q2pp/4p3/2B5/4Q3/PPP2B2/2K5 b - - 0 1",
    "3r1k2/4npp1/1ppr3p/p6P/P2PPPP1/1NR5/5K2/2R5 w - - 0 1",
    "2q1rr1k/3bbnnp/p2p1pp1/2pPp3/PpP1P1P1/1P2BNNP/2BQ1PRK/7R b - - 0 1",
    "rnbqkb1r/p3pppp/1p6/2ppP3/3N4/2P5/PPP1QPPP/R1B1KB1R w KQkq - 0 1",
    "r1b2rk1/2q1b1pp/p2ppn2/1p6/3QP3/1BN1B3/PPP3PP/R4RK1 w - - 0 1",
    "2r3k1/pppR1pp1/4p3/4P1P1/5P2/1P4K1/P1P5/8 w - - 0 1",
    "1nk1r1r1/pp2n1pp/4p3/q2pPp1N/b1pP1P2/B1P2R2/2P1B1PP/R2Q2K1 w - - 0 1",
    "4b3/p3kp2/6p1/3pP2p/2pP1P2/4K1P1/P3N2P/8 w - - 0 1",
    "2kr1bnr/pbpq4/2n1pp2/3p3p/3P1P1B/2N2N1Q/PPP3PP/2KR1B1R w - - 0 1",
    "3rr1k1/pp3pp1/1qn2np1/8/3p4/PP1R1P2/2P1NQPP/R1B3K1 b - - 0 1",
    "2r1nrk1/p2q1ppp/bp1p4/n1pPp3/P1P1P3/2PBB1N1/4QPPP/R4RK1 w - - 0 1",
    "r3r1k1/ppqb1ppp/8/4p1NQ/8/2P5/PP3PPP/R3R1K1 b - - 0 1",
    "r2q1rk1/4bppp/p2p4/2pP4/3pP3/3Q4/PP1B1PPP/R3R1K1 w - - 0 1",
    "rnb2r1k/pp2p2p/2pp2p1/q2P1p2/8/1Pb2NP1/PB2PPBP/R2Q1RK1 w - - 0 1",
    "2r3k1/1p2q1pp/2b1pr2/p1pp4/6Q1/1P1PP1R1/P1PN2PP/5RK1 w - - 0 1",
    "r1bqkb1r/4npp1/p1p4p/1p1pP1B1/8/1B6/PPPN1PPP/R2Q1RK1 w kq - 0 1",
    "r2q1rk1/1ppnbppp/p2p1nb1/3Pp3/2P1P1P1/2N2N1P/PPB1QP2/R1B2RK1 b - - 0 1",
    "r1bq1rk1/pp2ppbp/2np2p1/2n5/P3PP2/N1P2N2/1PB3PP/R1B1QRK1 b - - 0 1",
    "3rr3/2pq2pk/p2p1pnp/8/2QBPP2/1P6/P5PP/4RRK1 b - - 0 1",
    "r4k2/pb2bp1r/1p1qp2p/3pNp2/3P1P2/2N3P1/PPP1Q2P/2KRR3 w - - 0 1",
    "3rn2k/ppb2rpp/2ppqp2/5N2/2P1P3/1P5Q/PB3PPP/3RR1K1 w - - 0 1",
    "2r2rk1/1bqnbpp1/1p1ppn1p/pP6/N1P1P3/P2B1N1P/1B2QPP1/R2R2K1 b - - 0 1",
    "r1bqk2r/pp2bppp/2p5/3pP3/P2Q1P2/2N1B3/1PP3PP/R4RK1 b kq - 0 1",
    "r2qnrnk/p2b2b1/1p1p2pp/2pPpp2/1PP1P3/PRNBB3/3QNPPP/5RK1 w - - 0 1",
    "4rrk1/pp1n3p/3q2pQ/2p1pb2/2PP4/2P3N1/P2B2PP/4RRK1 b - - 0 1",
    "r1bq1r1k/1pp1n1pp/1p1p4/4p2Q/4Pp2/1BNP4/PPP2PPP/R4RK1 w - - 0 1",
    "2kr3r/pppq1ppp/2n1b3/3p4/3P4/2P1BN2/PP3PPP/R2QK2R w KQ - 0 1",
    "6k1/6p1/7p/P5P1/1p4KP/2p5/8/8 b - - 0 1",
]

#: Forced-mate candidates.  `dm` is filled in by the referee.
MATE_SEEDS = [
    "6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1",
    "k7/8/1QK5/8/8/8/8/8 w - - 0 1",
    "2bqkbn1/2pppp2/np2N3/r3P1p1/p2N2B1/5Q2/PPPPKPP1/RNB2r2 w - - 0 1",
    "7k/8/5KR1/8/8/8/8/8 w - - 0 1",
    "4k3/8/4K3/8/8/8/8/5Q2 w - - 0 1",
    "5rk1/pp4p1/8/3N1Q1p/6q1/8/P5PP/5RK1 w - - 0 1",
    "3r1r1k/1p4pp/p4p2/8/1PQ5/P1P1q3/2PR3P/5RK1 b - - 0 1",
    "Q7/p1p1q1pk/3p2rp/4n3/3bP3/7b/PP3PPK/R1B2R2 b - - 0 1",
    "6k1/pp4p1/2p5/2bp4/8/P5Pb/1P3rrP/2BRRN1K b - - 0 1",
    "2r3k1/p4p2/3Rp2p/1p2P1pK/8/1P4P1/P3Q2P/1q6 b - - 0 1",
]

#: Endings whose value is settled theory, each with the verdict the engine has
#: to reach.  The referee only cross-checks these, it does not decide them: a
#: blocked king and pawn is a dead draw that Stockfish still scores at +21,
#: because it has no bitbase for it, while the engine under test does.
DRAW_SEEDS = [
    ("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1", "draw", "stalemate"),
    ("5k2/5P2/5K2/8/8/8/8/8 b - - 0 1", "draw", "stalemate in front of a pawn"),
    ("8/8/8/5k2/8/8/8/5K2 w - - 0 1", "draw", "king against king"),
    ("4k3/8/8/8/8/8/8/4K1N1 w - - 0 1", "draw", "knight against a bare king"),
    ("4k3/8/8/8/8/8/8/4K1B1 w - - 0 1", "draw", "bishop against a bare king"),
    ("4k3/8/8/8/8/8/8/1N2K1N1 w - - 0 1", "draw", "two knights cannot force mate"),
    ("8/8/8/4k3/8/8/4K3/7Q w - - 100 200", "draw", "fifty moves reached, a queen up"),
    ("8/8/8/4k3/8/8/4P3/4K3 w - - 0 1", "draw", "king and pawn, black king in front"),
    ("8/8/8/8/8/4k3/4P3/4K3 w - - 0 1", "draw", "king and pawn, blockaded"),
    ("8/8/8/3k4/8/8/4P3/4K3 w - - 0 1", "draw", "king and pawn, black king in the square"),
    ("4k3/8/8/8/8/8/8/2B1K1N1 w - - 0 1", "win", "bishop and knight mate"),
    ("4k3/8/8/8/8/8/8/4K2R w K - 0 1", "win", "rook against a bare king"),
    ("8/8/8/4k3/8/8/4K3/7Q w - - 40 200", "win", "a queen up, 40 half moves played"),
    ("4k3/8/8/8/8/8/4P3/4K3 w - - 0 1", "win", "king and pawn, white has the opposition"),
    ("4k3/4P3/4K3/8/8/8/8/8 w - - 0 1", "win", "king and pawn on the seventh"),
    ("8/6p1/8/6K1/6P1/8/8/6k1 w - - 0 1", "win", "the white king is in front of the pawn"),
]


# -------------------------------------------------------------------- helpers


def log(text: str = "") -> None:
    print(text, flush=True)


def referee_of(cfg: cfgmod.Config, name: str = "") -> cfgmod.EngineSpec:
    spec = cfg.resolve(name) if name else cfg.referee
    if spec is None:
        raise SystemExit("suite.toml has no [referee] section")
    if not spec.exists():
        raise SystemExit(f"referee binary not found: {spec.resolved_command()[0]}")
    return spec


def analyse(engine: UciEngine, board: chess.Board, depth: int,
            multipv: int = 1) -> list[SearchInfo]:
    """One clean search; returns the MultiPV lines, best first."""
    engine.new_game()
    result = engine.analyse(board, Limit(depth=depth), multipv=multipv)
    return result.lines or [result.info]


# ---------------------------------------------------------------------- perft


def build_perft(cfg: cfgmod.Config, max_nodes: int, log_every: bool = True) -> str:
    """Ask the referee for perft counts at every depth under `max_nodes`."""
    spec = referee_of(cfg)
    records: list[epdmod.EpdRecord] = []
    out_path = os.path.join(cfg.suites_dir, "perft.epd")

    with UciEngine(spec) as engine:
        for fen in PERFT_FENS:
            try:
                board = chess.Board(fen)
            except ValueError as exc:
                log(f"  skipping an invalid FEN ({exc}): {fen}")
                continue
            record = epdmod.EpdRecord(fen=board.fen(), board=board,
                                      ident=f"perft.{len(records) + 1:02d}")
            for depth in range(1, 9):
                nodes = engine.perft(board, depth)
                record.perft[depth] = nodes
                if nodes >= max_nodes:
                    break
            records.append(record)
            if log_every:
                deepest = max(record.perft)
                log(f"  D1-D{deepest}  {record.perft[deepest]:>13,}  {fen}")

    epdmod.write(out_path, records, header=(
        "perft: node counts from the referee engine, not from a published list.\n"
        "Regenerate with: python testsuite/tools/make_suites.py perft"))
    log(f"\n  wrote {len(records)} positions to {out_path}")
    return out_path


# -------------------------------------------------------------------- bench


def build_bench(cfg: cfgmod.Config) -> str:
    records = []
    for i, fen in enumerate(BENCH_FENS, 1):
        board = chess.Board(fen)
        records.append(epdmod.EpdRecord(fen=board.fen(), board=board,
                                        ident=f"bench.{i:02d}"))
    path = os.path.join(cfg.suites_dir, "bench.epd")
    epdmod.write(path, records, header=(
        "Positions for the speed and time-to-depth suites.\n"
        "No solutions: only the node counts and depths matter here."))
    log(f"  wrote {len(records)} positions to {path}")
    return path


# ------------------------------------------------------------------ openings


def build_openings(cfg: cfgmod.Config, depth: int = 16, margin: int = 45,
                   limit: int = 0) -> str:
    """Keep the opening lines the referee calls roughly equal."""
    spec = referee_of(cfg)
    kept: list[Opening] = []
    path = os.path.join(cfg.suites_dir, "openings.epd")

    log(f"  checking {len(OPENING_LINES)} lines at depth {depth}, "
        f"keeping |eval| <= {margin} cp\n")
    with UciEngine(spec) as engine:
        for name, line in OPENING_LINES:
            board = chess.Board()
            try:
                for token in line.split():
                    board.push(board.parse_san(token))
            except ValueError as exc:
                log(f"  {'BAD':>6}  {name}: {exc}")
                continue
            info = analyse(engine, board, depth)[0]
            cp = info.score_cp
            white_cp = cp if board.turn == chess.WHITE else -cp
            ok = abs(white_cp) <= margin
            if ok:
                kept.append(Opening(name, board.fen(), []))
            log(f"  {white_cp:>+6}  {'keep' if ok else 'drop'}  {name:<28} {line}")

    if limit:
        kept = kept[:limit]
    write_epd(path, kept, header=(
        f"Opening positions for matches: standard lines the referee scores\n"
        f"within {margin} cp of equal at depth {depth}.  Every opening is\n"
        f"played twice with the colours swapped."))
    log(f"\n  kept {len(kept)}/{len(OPENING_LINES)} openings -> {path}")
    return path


# ------------------------------------------------- tactics and mates (shared)


def sample_positions(cfg: cfgmod.Config, games: int, movetime_ms: int,
                     stride: int, skill: int, seed: int) -> list[chess.Board]:
    """Play fast referee self-play and sample positions out of the games.

    A low skill level produces real mistakes, which is where the sharp
    positions are; sampling from played games is how the published suites are
    built too.
    """
    spec = referee_of(cfg).with_overrides({"Skill Level": skill, "Threads": 1,
                                           "Hash": 64})
    rng = random.Random(seed)
    openings_path = os.path.join(cfg.suites_dir, "openings.epd")
    starts: list[str] = []
    if os.path.isfile(openings_path):
        from ts.openings import OpeningBook
        book = OpeningBook.from_file(openings_path)
        starts = [o.board().fen() for o in book.openings]
    if not starts:
        starts = [chess.STARTING_FEN]

    out: list[chess.Board] = []
    limit = Limit(movetime_ms=movetime_ms)
    with UciEngine(spec) as white, UciEngine(spec) as black:
        for g in range(games):
            board = chess.Board(rng.choice(starts))
            white.new_game()
            black.new_game()
            ply = 0
            while not board.is_game_over(claim_draw=True) and ply < 160:
                handle = white if board.turn == chess.WHITE else black
                try:
                    result = handle.search(board, limit)
                except EngineProblem:
                    break
                if not result.move:
                    break
                board.push(result.move)
                ply += 1
                if ply >= 8 and ply % stride == 0:
                    out.append(board.copy(stack=False))
            log(f"  game {g + 1}/{games}: {ply} plies, {len(out)} candidates so far")
    return out


def filter_discriminating(cfg: cfgmod.Config, boards: list[chess.Board], *,
                          depth: int, multipv: int, equal_cp: int, gap_cp: int,
                          verify_depth: int, want_mates: bool,
                          max_keep: int) -> list[epdmod.EpdRecord]:
    """Keep positions with one clearly best move (or a set of equal best moves)."""
    spec = referee_of(cfg)
    kept: list[epdmod.EpdRecord] = []
    seen: set[str] = set()

    with UciEngine(spec) as engine:
        for i, board in enumerate(boards, 1):
            if len(kept) >= max_keep:
                break
            key = board.board_fen() + str(board.turn)
            if key in seen or board.is_game_over():
                continue
            seen.add(key)
            if len(list(board.legal_moves)) < 4:
                continue

            accepted = _solution(engine, board, depth, multipv, equal_cp, gap_cp)
            if accepted is None:
                continue
            # confirm at a higher depth: a "solution" that moves when you look
            # harder is not a solution
            confirmed = _solution(engine, board, verify_depth, multipv,
                                  equal_cp, gap_cp)
            if confirmed is None or set(confirmed[0]) != set(accepted[0]):
                continue
            moves, mate = confirmed
            is_mate = mate is not None
            if is_mate != want_mates:
                continue

            record = epdmod.EpdRecord(fen=board.fen(), board=board,
                                      best_moves=moves,
                                      mate_in=mate if is_mate else None,
                                      ident="")
            kept.append(record)
            log(f"  [{len(kept):>3}] from candidate {i}: "
                f"{'mate in ' + str(mate) if is_mate else 'bm'} "
                f"{record.best_san()}   {board.fen()}")
    return kept


def _solution(engine: UciEngine, board: chess.Board, depth: int, multipv: int,
              equal_cp: int, gap_cp: int):
    """The set of best moves, or None when no move stands out."""
    infos = analyse(engine, board, depth, multipv)
    if len(infos) < 2 or any(not info.pv for info in infos):
        return None
    scores = [info.score_cp for info in infos]
    moves = [info.pv[0] for info in infos]

    mate = infos[0].mate
    if mate is not None:
        # A mate is its own gap: the solution is every move that mates in the
        # same number of moves, and the position is only worth keeping if some
        # other move does not.  Comparing centipawns here would throw away
        # almost everything, because mate in 1 and mate in 3 are two apart on
        # the mapped scale.
        if mate <= 0 or mate > 8:
            return None
        accepted = [m for m, info in zip(moves, infos) if info.mate == mate]
        if len(accepted) >= len(infos):
            return None
        return (accepted, mate)

    best = scores[0]
    accepted = [m for m, s in zip(moves, scores) if best - s <= equal_cp]
    rest = [s for s in scores if best - s > equal_cp]
    if not rest or len(accepted) >= multipv:
        return None
    if best - rest[0] < gap_cp:
        return None
    if abs(best) > 2000:
        return None
    return (accepted, None)


def build_tactics(cfg: cfgmod.Config, *, games: int, depth: int,
                  verify_depth: int, max_keep: int, stride: int, movetime: int,
                  skill: int, seed: int, want_mates: bool) -> str:
    name = "mates" if want_mates else "tactics"
    seeds = MATE_SEEDS if want_mates else TACTIC_SEEDS
    boards: list[chess.Board] = []
    for fen in seeds:
        try:
            board = chess.Board(fen)
        except ValueError as exc:
            log(f"  skipping an unusable seed ({exc}): {fen}")
            continue
        if not board.is_valid():
            log(f"  skipping an illegal seed ({board.status()!r}): {fen}")
            continue
        boards.append(board)
    if games > 0:
        log(f"\n  sampling positions from {games} referee self-play games "
            f"(skill {skill}, {movetime} ms per move)")
        boards += sample_positions(cfg, games, movetime, stride, skill, seed)

    log(f"\n  filtering {len(boards)} candidates at depth {depth} "
        f"(confirmed at {verify_depth})\n")
    records = filter_discriminating(cfg, boards, depth=depth, multipv=4,
                                    equal_cp=20, gap_cp=120 if not want_mates else 200,
                                    verify_depth=verify_depth,
                                    want_mates=want_mates, max_keep=max_keep)
    for i, record in enumerate(records, 1):
        record.ident = f"{name}.{i:03d}"

    path = os.path.join(cfg.suites_dir, f"{name}.epd")
    epdmod.write(path, records, header=(
        f"{name}: positions where the referee engine finds one clearly best\n"
        f"move at depth {verify_depth} (MultiPV, at least 120 cp ahead of the\n"
        f"rest).  Regenerate or extend with:\n"
        f"  python testsuite/tools/make_suites.py {name} --games N"))
    log(f"\n  wrote {len(records)} positions to {path}")
    return path


# -------------------------------------------------------------------- draws


def build_draws(cfg: cfgmod.Config, depth: int = 20) -> str:
    """Write the known endings, cross-checked against the referee.

    The verdict comes from endgame theory, not from the referee: a position
    the referee scores at +21 can still be a dead draw.  The referee is only
    asked to flag a seed that is clearly labelled wrong -- a "draw" it thinks
    is mate, or a "win" it scores at zero.
    """
    spec = referee_of(cfg)
    records: list[epdmod.EpdRecord] = []
    suspect = 0
    path = os.path.join(cfg.suites_dir, "draws.epd")

    log(f"  {'referee':>9}  verdict  check     position")
    with UciEngine(spec) as engine:
        for fen, verdict, note in DRAW_SEEDS:
            board = chess.Board(fen)
            over = board.is_game_over(claim_draw=True)
            info = None if over else analyse(engine, board, depth)[0]
            cp = 0 if over else info.score_cp
            mate = None if over else info.mate

            if verdict == "draw":
                disagrees = mate is not None or abs(cp) > 150
            else:
                disagrees = mate is None and abs(cp) < 100
            suspect += disagrees
            records.append(epdmod.EpdRecord(
                fen=board.fen(), board=board,
                ident=f"draws.{len(records) + 1:02d}",
                comment=f"{verdict}: {note}"))
            log(f"  {cp:>+9}  {verdict:<7}  {'CHECK ME' if disagrees else 'agrees ':<8}  "
                f"{note}")

    epdmod.write(path, records, header=(
        "Endings whose value is settled theory.  The c0 comment starts with\n"
        "the expected verdict; the suite checks that a score of exactly 0\n"
        "means 'draw' and anything else means 'not a draw'."))
    log(f"\n  wrote {len(records)} positions to {path}")
    if suspect:
        log(f"  {suspect} seed(s) the referee disagrees with -- check them by hand")
    return path


# ----------------------------------------------------------------------- CLI


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("what", nargs="+",
                        choices=["all", "perft", "bench", "openings", "tactics",
                                 "mates", "draws"])
    parser.add_argument("--config", default=cfgmod.DEFAULT_CONFIG)
    parser.add_argument("--max-nodes", type=int, default=200_000_000,
                        help="stop perft at the first depth above this")
    parser.add_argument("--depth", type=int, default=14,
                        help="referee depth for the first pass; MultiPV makes "
                             "this expensive, every extra two plies roughly "
                             "doubles the generation time")
    parser.add_argument("--verify-depth", type=int, default=0,
                        help="confirmation depth (default: --depth + 4)")
    parser.add_argument("--games", type=int, default=10,
                        help="referee self-play games to sample positions from")
    parser.add_argument("--stride", type=int, default=5,
                        help="sample every Nth ply of a sampled game")
    parser.add_argument("--movetime", type=int, default=60,
                        help="ms per move while sampling")
    parser.add_argument("--skill", type=int, default=6,
                        help="referee Skill Level while sampling")
    parser.add_argument("--keep", type=int, default=60,
                        help="maximum positions per generated suite")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    cfg = cfgmod.load(args.config)
    os.makedirs(cfg.suites_dir, exist_ok=True)
    what = set(args.what)
    if "all" in what:
        what = {"perft", "bench", "openings", "tactics", "mates", "draws"}
    verify_depth = args.verify_depth or args.depth + 6

    start = time.perf_counter()
    if "perft" in what:
        log("== perft ==")
        build_perft(cfg, args.max_nodes)
    if "bench" in what:
        log("\n== bench positions ==")
        build_bench(cfg)
    if "openings" in what:
        log("\n== openings ==")
        build_openings(cfg, depth=args.depth)
    if "draws" in what:
        log("\n== draws ==")
        build_draws(cfg)
    if "tactics" in what:
        log("\n== tactics ==")
        build_tactics(cfg, games=args.games, depth=args.depth,
                      verify_depth=verify_depth, max_keep=args.keep,
                      stride=args.stride, movetime=args.movetime,
                      skill=args.skill, seed=args.seed, want_mates=False)
    if "mates" in what:
        log("\n== mates ==")
        build_tactics(cfg, games=max(0, args.games // 2), depth=args.depth,
                      verify_depth=verify_depth, max_keep=max(12, args.keep // 2),
                      stride=3, movetime=args.movetime, skill=max(0, args.skill - 3),
                      seed=args.seed + 1, want_mates=True)

    log(f"\ndone in {time.perf_counter() - start:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

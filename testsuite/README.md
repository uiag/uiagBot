# The engine testsuite

Measures the engine in `engine/` two ways: against **fixed benchmarks** with a
known right answer (perft, tactics, mates, drawn endings, time management), and
against **Stockfish at a range of strengths** to put a number on how strong it
actually is.

Nothing here touches `engine/`. Both sides are driven as black boxes over UCI,
so the same suite works for any UCI engine — an older build of your own, a
different `EvalMode`, or someone else's binary.

```
python testsuite/run.py list        # what is configured, and does it exist
python testsuite/run.py selftest    # correctness: should always pass
python testsuite/run.py benchmarks  # speed and tactical strength
python testsuite/run.py elo         # play Stockfish until the rating settles
```

## Requirements

`python-chess` only — the same one lichess-bot already needs. The suite talks
UCI over plain pipes, so it runs on CPython and on the PyPy interpreter this
project uses.

`engine/ce.exe` has to exist: build it with `python engine/build.py`.

## Layout

| path | what it is |
| --- | --- |
| `suite.toml` | the configuration: engine, opponents, time control, adjudication |
| `run.py` | the only entry point |
| `ts/` | the implementation |
| `suites/*.epd` | the test positions (generated, see below) |
| `tools/make_suites.py` | regenerates `suites/` using a referee engine |
| `results/` | JSON results, `history.jsonl`, and the games as PGN |

## The benchmarks

| command | what it measures | passes when |
| --- | --- | --- |
| `perft` | move generation | every node count matches |
| `bench` | nodes per second at a fixed depth | always; the number is the point |
| `ttd` | nodes and time to reach a fixed depth | always; **compare node counts** between versions |
| `speed` | depth reached in a fixed time | always; the number is the point |
| `tactics` | finds the one good move | the percentage is the score |
| `mates` | finds forced mates, at the right distance | all of them |
| `draws` | dead-drawn endings evaluate to exactly 0 | all of them |
| `determinism` | one thread, same position, same node count twice — both after a restart and after `ucinewgame` | always |
| `timing` | self-play on a clock, never flags, never burns more than half the clock on one move | always |

Groups: `selftest` (perft, mates, draws, determinism, timing — everything with a
right answer), `benchmarks` (bench, ttd, speed, tactics), `all`.

`determinism` asks two different questions, because they fail for different
reasons and only one of them is a hard requirement:

* **process** — a freshly started engine, one search. This must hold for any
  correct single-threaded engine; a mismatch means uninitialised memory, a
  race, or a search that depends on wall-clock time. Every other number in
  this suite is meaningless if this fails.
* **session** — repeated searches in one process with `ucinewgame` between
  them. This catches state surviving a reset: a table that was not cleared, or
  one keyed on less than it actually depends on. An engine can be strong
  without it, but its node counts are then samples rather than measurements,
  and `ttd` and `bench` stop being comparable between versions.

It earned its keep straight away. The pawn hash table in `eval.c` is
`__thread`-static and is not cleared by `ucinewgame`, and one of the fields it
cached (`blocked`) depended on all the pieces while the entry is keyed on the
pawn structure alone. The old build passed **process** and failed **session**
on 5 of 6 positions — and on one of them it did not merely use a different
number of nodes, it chose a different move for the same position at the same
depth depending on what it had searched before. `blocked` now lives in
`EvalInfo`, every field of a `PawnEntry` is a pure function of its key again,
and both phases pass.

Run it with one thread only. Lazy SMP is deliberately non-deterministic, so the
suite forces `Threads=1` regardless of what the configuration says.

```
python testsuite/run.py perft                      # capped at 200M nodes per entry
python testsuite/run.py perft --max-nodes 0        # every stored depth, tens of billions
python testsuite/run.py tactics --movetime 5s
python testsuite/run.py ttd --depth 14
python testsuite/run.py speed --engine ce:EvalMode=nnue
```

`ttd` is the one to watch while changing the search: wall-clock time is noisy,
but the node count at a fixed depth is nearly exact, so a pruning change shows
up immediately even on a busy machine.

## Playing Stockfish

```
python testsuite/run.py match sf-1700 -n 20 --tc 10+0.1
python testsuite/run.py gauntlet -n 10
python testsuite/run.py gauntlet --opponents sf-1900 sf-2100 -n 20 -c 2
```

Every game is played on a real clock that the suite keeps itself: the time each
move actually took is subtracted from the mover's clock, and an engine that
runs out loses on time exactly as it would on lichess. So a match is a time
management test as well as a strength test — look for `time forfeit` in the
anomalies.

Openings come from `suites/openings.epd` and each one is played twice with the
colours swapped, so an opening that favours White cannot distort the result.

Games are adjudicated early when both engines agree the position is dead
(`[adjudication]` in `suite.toml`); that roughly halves the time per game
without changing the outcome.

### Time controls

| form | meaning |
| --- | --- |
| `10+0.1` | 10 seconds each plus 100 ms per move |
| `60+0.6` | one minute each plus 600 ms |
| `40/300+0` | 40 moves in 300 seconds |
| `movetime=500ms` | a flat 500 ms per move |
| `depth=8`, `nodes=200000` | fixed depth or node count |

Short controls give many games per hour; long ones give a rating closer to what
the bot plays at. Fixed `nodes=` removes machine speed from the equation
entirely, which is the right choice when comparing two builds.

## The Elo system

```
python testsuite/run.py elo                 # calibrate, storing everything
python testsuite/run.py elo --tc 60+0.6 -n 20
python testsuite/run.py rating              # the rating implied by all stored games
python testsuite/run.py history             # every match ever played
```

`elo` plays the ladder of Stockfish strengths in `suite.toml` and fits a rating:

1. Start from whatever the stored history already says (or the middle of the
   ladder on the first run).
2. Each round, play the rungs closest to the current estimate. A game against
   an equal opponent carries far more information than one against an opponent
   600 points away, so this converges much faster than playing everyone.
3. Refit after every match, stop once the error bar is under `--target-error`
   (35 Elo by default) or `--rounds` rounds have been played.

The rating is a maximum-likelihood fit: it solves

```
sum_i games_i * E(R - R_i) = points scored
```

for `R`, where `E(d) = 1 / (1 + 10^(-d/400))` — the exact estimate for the
logistic model when a draw counts as half a win. The error bar is the delta
method with a per-game variance of `E(1-E) - p_draw/4`.

Results accumulate: every match is appended to `results/history.jsonl` with a
fingerprint of the engine that played it (name, UCI options, and the size and
timestamp of the binary). `rating` only adds up games from **one** build at
**one** time control, so a rebuild does not silently average the old engine
with the new one. `--any-build` overrides that.

### What the number means, and what it does not

The ladder uses Stockfish's `UCI_LimitStrength` / `UCI_Elo`, so a rating from
this suite is **on Stockfish's own scale**. That scale is in the same
neighbourhood as the published rating lists but is not identical to any of
them, and at the bottom end it is generally thought to be generous. Treat the
number as:

* excellent for comparing two versions of your own engine,
* good for tracking progress over time,
* a rough indication when compared against a published rating list.

If you trust a different anchor, override `elo` on any opponent in
`suite.toml`, or add rungs that do not depend on Stockfish's calibration at
all (a fixed `nodes=` opponent, or an older engine whose rating you know).

A rating is only meaningful together with the conditions it was measured
under: the time control, the number of threads, and the hash size. Measure with
`Threads = 1` on both sides unless you are specifically measuring the gain from
Lazy SMP, and keep `concurrency * Threads` at or under the number of physical
cores — otherwise the engines slow each other down and the rating comes out
too low.

## Testing a change

```
python testsuite/run.py sprt ce:EvalMode=nnue
python testsuite/run.py sprt ce:Hash=1024 --tc 10+0.1 --elo0 0 --elo1 8
```

`sprt` plays the candidate against the configured engine and stops as soon as
the result is decided, rather than after a fixed number of games. `--elo0` and
`--elo1` are the two hypotheses: "no better than `elo0`" against "at least
`elo1` better". It typically takes a few hundred games to resolve a 10 Elo
difference and a few thousand for a 3 Elo one — that is the nature of the
measurement, not a limitation of the tool.

`--engine` and the candidate both take `name:Option=value` overrides, so you
can test any option change without editing anything:

```
python testsuite/run.py sprt ce:Threads=4 --engine ce:Threads=1 -n 100
```

To test a change to the engine *source*, keep the old binary around. Copy
`engine/ce.exe` to `engine/ce_prev.exe` before rebuilding — `suite.toml`
already has a `ce-prev` opponent pointing at it — and then

```
python testsuite/run.py sprt ce --engine ce-prev --elo1 5
```

The first argument is always the candidate; `--engine` is the baseline it has
to beat. `--engine`, `--config` and `--no-save` work on either side of the
subcommand, so `run.py --engine ce-prev sprt ce` is the same command.

## The suite files

Nothing in `suites/` is written by hand. `tools/make_suites.py` builds all of
it with the `[referee]` engine from `suite.toml` (a full-strength Stockfish):

```
python testsuite/tools/make_suites.py all
python testsuite/tools/make_suites.py tactics --games 30 --keep 150
python testsuite/tools/make_suites.py openings --depth 20
```

* **perft** — the referee's own `go perft` counts. Independent of the engine
  under test, so a mismatch is a real move generation bug.
* **openings** — standard opening lines, kept only when the referee scores the
  position within 45 cp of equal.
* **tactics / mates** — candidates (a hand-picked list plus positions sampled
  from fast referee self-play) are analysed with MultiPV, and a position is
  kept only when one move — or a set of equally good moves — is at least
  120 cp better than everything else, and still is when checked again six
  plies deeper. That filter is what makes a suite discriminating rather than
  merely hard, and it is why the suite can be grown with `--games` without
  anyone having to know the answers.
* **draws** — settled endgame theory, with the referee used only to flag a
  seed that is obviously labelled wrong. The verdicts are *not* taken from the
  referee: Stockfish scores a blockaded king and pawn at +4, and calling that
  "not a draw" would be wrong.

## Adding opponents

Any UCI binary works. Add a `[[opponent]]` block to `suite.toml`:

```toml
[[opponent]]
name    = "sf11"
command = "engines/stockfish-11.exe"
elo     = 3200                        # omit to keep it out of the Elo fit
options = { Threads = 1, Hash = 64 }

[[opponent]]
name         = "sf-10knodes"
command      = "stockfish/stockfish-windows-x86-64-avx2.exe"
time_control = "nodes=10000"          # this engine ignores the match clock
options      = { Threads = 1, Hash = 32 }
```

An opponent without an `elo` still plays, and still shows up in the match
table, but is left out of the rating fit — which is the right thing to do when
you do not actually know what it is worth.

## Reading the output

* `score` is the fraction of points scored, `diff` the Elo difference that
  implies, `LOS` the probability that the engine is genuinely better than that
  opponent.
* `predicted` in the rating table is what the fitted rating says the engine
  *should* have scored. A large `delta` on one rung means the ladder is not
  behaving linearly there — usually too few games, sometimes an opponent whose
  nominal rating is wrong.
* **anomalies** are engine bugs, not results: a time forfeit, an illegal move,
  a crash, a search that never returned. Any of them should be fixed before
  the rating means anything.

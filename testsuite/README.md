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
| `tools/make_openings.py` | generates a large balanced opening book |
| `tools/duel.py` | plays two builds against each other across processes |
| `tools/make_evalset.py` | generates and labels positions for the evaluation tuner |
| `tools/tune_eval.py` | fits the evaluation's parameters to that set |
| `tools/spsa.py` | tunes search parameters by playing games |
| `tools/ablate.py` | measures what each feature is worth by switching it off |
| `tools/apply_tune.py` | writes accepted values back into `engine/tune.h` |
| `tools/eval_bias.py` | which block of the evaluation is systematically mis-weighted |
| `tools/eval_scan.py` | scores many parameter sets against a labelled file |
| `tools/eval_fit.py` | fits and ablates against a packed set, in C |
| `tools/eval_compare.py` | the hand-crafted evaluation and the network, same positions |
| `tools/test_ponder.py` | pondering over UCI |
| `tools/test_ponder_bridge.py` | pondering through the Python bridge |
| `results/` | JSON results, `history.jsonl`, and the games as PGN |

## Comparing two builds quickly

`run.py match -c N` runs its games as threads in one interpreter. The per-move
protocol work is Python, so the global interpreter lock holds the whole thing
to about 1.8x however many workers are asked for -- on eight cores, 56 games
took three minutes whether `-c` was 4 or 7. `tools/duel.py` runs one *process*
per worker over a disjoint slice of the opening book instead, and does about
**200 games a minute** on the same machine:

```
python testsuite/tools/duel.py ce-cand --control -n 120      # do this first
python testsuite/tools/duel.py ce-cand ce-prev -n 1200
python testsuite/tools/duel.py ce-cand ce-base -n 2000 --sprt --elo1 4
python testsuite/tools/duel.py "ce:FutilityBase=110" ce-prev -n 800
```

**`--control` first, every time.** It plays the candidate against itself, which
on a fixed node count has to come out at exactly 50%, and it costs ninety
seconds. The first version of this script did not force a thread count, so a
`ce:...` candidate picked up `Threads = 6` from `[engine]` in `suite.toml`
while the baseline opponent used one — and under a node limit that is a
*handicap*, because the node budget is shared between the threads. It measured
as **92 Elo against two identical binaries** and quietly spoiled three
experiments, each of which produced a plausible-looking number and a tidy
explanation for it. `duel.py` now forces `Threads` and `Hash` on both sides
(`--threads`, `--hash`), and `--control` is there so the question gets asked
before the result is believed rather than after it is surprising.

The candidate and the baseline are names from `suite.toml`, optionally with
`:Option=value` overrides, so a parameter change needs no rebuild. There are
three frozen build slots to compare against -- `ce-base` (where this started),
`ce-prev` (the last accepted step) and `ce-cand` (the snapshot under test) --
which also means a rebuild cannot disturb a match already running.

**A fixed-node control makes every game reproducible, and that puts a hard cap
on how much a match can tell you: with the forty hand-picked openings there
are only eighty distinct games to be had, and the 81st repeats the 1st
exactly.** That is why `tools/make_openings.py` exists:

```
python testsuite/tools/make_openings.py -n 3000
```

It grows each line one ply at a time from the referee's best few moves, so the
book wanders over real opening theory rather than over random legal moves, and
keeps a position only if the referee still calls it balanced. `duel.py` picks
up `suites/openings_big.epd` automatically when it is there.

## Benchmarking a new build against the old one

`engine/ce_new.exe` is a frozen copy of the current build and `engine/ce_base.exe`
is the v1.0 build it replaces; both are configured in `suite.toml`, so the
whole comparison is four commands:

```
python testsuite/run.py selftest --engine ce-new:Threads=1,Hash=64
python testsuite/run.py benchmarks --engine ce-new:Threads=1,Hash=64
python testsuite/tools/duel.py ce-new ce-base -n 2000 --tc nodes=25000
python testsuite/tools/duel.py ce-new ce-base -n 400 --tc 10+0.1 -j 4 --no-adjudication
```

The first two check that nothing broke and give the speed and tactics numbers.
The third measures strength with machine speed taken out of the equation --
that is the number to quote when comparing two builds. The fourth repeats it on
a real clock, which is the only way to catch a change that bought its accuracy
with nodes per second, and `-j 4` keeps `jobs x 2` engines inside the core
count so neither side is starved.

`--no-adjudication` matters whenever the two builds disagree about what a
centipawn means: the resign and draw thresholds are absolute, so a build whose
scores are on a different scale resigns at a different point, and the result is
biased. Use it for any comparison across an evaluation rescale.

To measure against Stockfish rather than against yourself:

```
python testsuite/run.py match sf-2850 -n 20 --engine ce-new --tc 60+0.6
python testsuite/run.py elo --engine ce-new
```

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

**`timing` needs a quiet machine.** It measures wall time from sending `go` to
reading `bestmove`, so everything between the two counts: the pipe, the Python
scheduler, and whatever else is running. Run on a box also training a network
and playing a seven-worker match, it reported a time forfeit at 3+0.02 -- 422 ms
spent against a budget the engine had computed as 70 ms -- and the same build
passed every control cleanly once the machine was idle. A forfeit here is worth
investigating, but reproduce it on an unloaded machine before believing it.
Fixed-node matches are not affected, which is why `duel.py` defaults to them.

### Pondering

Not part of a suite, because both halves need a live UCI session:

```
python testsuite/tools/test_ponder.py          # over UCI
python testsuite/tools/test_ponder_bridge.py   # through the Python bridge
```

```
python testsuite/tools/test_ponder_game.py     # as lichess-bot drives it
```

Each check looks for an observable consequence rather than the absence of an
error, because pondering fails silently: the engine answers everything, plays
legal moves, and merely throws away the work. So a ponder hit has to come back
having searched *more* than the same position searched cold with the same
clock — 5.1 M nodes at depth 22 against 796 k at depth 16, currently — and a
ponder miss has to still owe the host exactly one `bestmove`.

**`test_ponder_game.py` plays once on a fixed `movetime` and once on a clock,
and the second is not redundant.** A race that hung the engine outright —
a hit applied before the search had taken ownership of its limits — needed the
opponent's reply to arrive within a millisecond or two of the ponder search
starting. On a fixed move time that essentially never happens; on a real
clock it happens on every move. All three of the other suites passed while
the engine was unusable on lichess.

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

For anything more than a few hundred games prefer `tools/duel.py`: it makes the
same comparison across processes and is about four times faster (see
*Comparing two builds quickly* above).

The first argument is always the candidate; `--engine` is the baseline it has
to beat. `--engine`, `--config` and `--no-save` work on either side of the
subcommand, so `run.py --engine ce-prev sprt ce` is the same command.

## Tuning

Everything in `engine/tune.h` can be set over UCI, so both tuners work on a
stock binary and neither writes to the source.

### The evaluation

The evaluation is fitted to positions whose true value is known, which is far
cheaper than measuring it in games: a pass over a hundred thousand positions
takes a fraction of a second, because the engine is driven through its own
shared library rather than over a pipe.

```
python testsuite/tools/make_evalset.py -n 240000     # self-play, result-labelled
python testsuite/tools/tune_eval.py                  # fit, in groups
python testsuite/tools/tune_eval.py --only PosWeightMg,PosWeightEg
```

The loss is the mean squared error between what the engine thinks of a
position and what actually happened in it, with the engine's side mapped
through a sigmoid into a winning probability, and with the global scale
divided out so that the optimiser has to improve the *shape* of the
evaluation rather than its confidence.

**The label is the result of the game, not the referee's score, and that
matters more here than anything else.** A referee score is far more precise,
and fitting to it is a trap: the referee's number already contains a search, a
static evaluation cannot reproduce one, and the fit pays down the unreachable
part of the error by shrinking every weight towards zero. That really does
improve the loss, because a flatter evaluation is better calibrated -- while
destroying the discrimination that playing strength is made of. Measured on
this engine: 51 parameters fitted to referee scores at depth 10 improved the
held-back loss by 23% and then scored 38.75% over 1200 games, **-80 Elo**.
`--label referee` is still there, because a precise label is right for
anything that is not a static evaluation, but it is not the default.

15% of the set is held back. If the held-back loss goes up while the training
loss goes down, the fit is memorising the set and the run says so.

A lower loss is evidence, not proof: always confirm with `duel.py`.

#### Where the evaluation is actually weak

`eval_bias.py` answers that without fitting anything. It scores the engine
against deep reference scores, fits one global scale by least squares, holds it
fixed, and reports the residual regressed against each block of the evaluation
in turn. A block that is weighted correctly contributes nothing; a block that
is three times too small leaves its whole bucket leaning one way. Because no
parameter is moved to produce the number, the flattening objection above does
not apply.

```
python testsuite/tools/eval_bias.py --data nnue/data/shard0.bin -n 25000
python testsuite/tools/eval_fit.py --ablate       # what is each term worth
python testsuite/tools/eval_fit.py --fit --groups newterms
python testsuite/tools/eval_compare.py            # hce against the network
```

`eval_fit.py` is the same idea driven from C -- the position set is loaded
across the FFI boundary once and scored in a single call, so a pass over 150k
positions is 600 ms and a fit over 37 parameters is four minutes rather than
an afternoon. It maximises **correlation**, which no change of scale can move,
so the flattening trade is unavailable to it.

**And it still does not predict Elo.** A fit that took the held-out
correlation from 0.7325 to 0.7381, moving exactly the weights `eval_bias.py`
had identified, scored **-44.8 +/- 14.8 Elo** over 1600 games. The reason is
not calibration this time: every margin the search compares against was tuned
against the distribution of *this* evaluation's differences, so changing the
shape mis-sets all of them at once. A shape change and a search retune are one
experiment, not two.

Use these tools to find out *what* is wrong and to propose candidates. Use
games to decide. The diagnosis they give is sound -- the measured conclusion
that king safety, mobility and passed pawns sit at about a third of
Stockfish's relative weight is recorded in `engine/README.md` -- but the
weights they recommend are not.

### The search

Pruning margins cannot be fitted to a position set -- nothing about a static
evaluation says how deep to look -- so they are measured by playing:

```
python testsuite/tools/spsa.py --groups scaled --iters 500
python testsuite/tools/spsa.py --groups lmr,pruning --iters 400
python testsuite/tools/spsa.py --only SingularMargin,SingularDouble --iters 200
```

The `scaled` group is everything the search compares against a static
evaluation. **Rescaling the evaluation changes what all of those margins mean
at once**, so that group is the one to run after touching `PosWeight*`,
`EvalScale` or the piece values -- it is where the systematic error will be,
and it is small enough to resolve in an hour.

Simultaneous perturbation nudges every parameter at once, both ways, plays a
handful of games each way and moves them all a little towards whichever side
scored. One iteration is nearly worthless; the average over a few hundred is
not. Engines are started once per worker and kept alive with `setoption`
between games, because at eight games an iteration process startup would
otherwise be most of the cost.

Both tuners end by printing the result as a `name=value` list ready to paste
into `duel.py`, and write the same values to `results/tuned_*.txt`. Only once
games agree should they become the defaults:

```
python testsuite/tools/duel.py "ce:PosWeightMg=54,..." ce-cand -n 1200
python testsuite/tools/apply_tune.py testsuite/results/tuned_eval.txt
python engine/build.py
```

`apply_tune.py` rewrites the defaults in `tune.h` in place, keeping the table's
column layout, and leaves anything it was not asked about alone.

### What each feature is worth

Because almost everything is reachable through `tune.h`, a feature can be
switched off on a stock binary and measured:

```
python testsuite/tools/ablate.py --list
python testsuite/tools/ablate.py posweight corrhist imbalance
python testsuite/tools/ablate.py -n 2000
```

Each entry plays the engine with one thing disabled against the engine with
everything on, so a **negative** Elo is how much that feature is worth. One
feature is usually worth less than the error bar on a thousand games -- read
the small entries as a direction, not a measurement.

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

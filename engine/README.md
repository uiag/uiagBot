# The chess engine

A standalone bitboard chess engine in C. No network access, no external engine:
the only outside data it uses are the opening book (handled by lichess-bot
itself, see `engine: polyglot:` in `config.yml`) and the trained network in
`net3.bin`, built by [`../nnue`](../nnue/README.md).

## Layout

| file | contents |
| --- | --- |
| `ce.h` | types, constants, declarations |
| `tune.h`, `tune.c` | every constant the search and the evaluation would otherwise hard-code, and the name → address table that lets them be set from outside |
| `bitboard.c` | attack tables; magic bitboards (magics are searched for at start-up with a fixed seed) |
| `position.c` | board state, zobrist hashing, make/unmake, attacks, legality, SEE, draw and cycle detection, FEN |
| `movegen.c` | legal move generation (captures/promotions and quiets partition the legal moves) |
| `eval.c` | hand-crafted evaluation, material imbalance and endgame knowledge (KPK bitbase, KXK/KBNK mate guidance, the textbook draws) |
| `nnue.c` | the perspective network from `../nnue`, quantised, with the incremental accumulator |
| `tt.c` | shared transposition table |
| `search.c` | iterative deepening PVS search, time management, Lazy SMP, ponder re-timing |
| `api.c` | the C API the Python side calls, including the background search and the labelled position set |
| `uci.c`, `main.c` | standalone UCI front-end with `bench` / `perft` / `eval` / `tune` commands |

## What changed last, and what it is worth

| change | measured |
| --- | --- |
| the accumulator's refresh cache | **+31.5 +/- 12.7** at 10+0.1 over 1008 games; +31.7% nodes per second at identical node counts |
| king buckets in the network (`net3.bin`) | **+20.8 +/- 11.4** at 10+0.1, +18.5 +/- 7.9 at 25k nodes |
| `LmrCaptHistDiv = 6000`, after fixing the index it read | **+5.4 +/- 5.5** pooled over 7000 games at fixed nodes |
| search threads kept in a pool between moves | 1.18 ms a move at `Threads=6`, no change to what the search decides |
| seven hard-coded search depths moved into `tune.h` | nothing, by construction; they are there so a tuner can reach them |

Together, against the build this started from: +16.0 +/- 8.0 over 4000 games
at 25k nodes, and **+4.3 +/- 7.6 over 2804 games at 10+0.1**.

That last number is lower than the network's own +20.8 and the two sit about
two sigma apart, which is worth saying plainly rather than quoting whichever
is kinder. It is also the weakest measurement of the set: a clock buys twelve
games a minute against a hundred, so the combined figure has a third of the
games behind it that the component figures do. Both changes are kept on the
better-powered evidence -- the network on a same-binary comparison where only
the file differs, and `LmrCaptHistDiv` on fixed nodes, which is the right
control for a change that costs no speed and therefore cannot be penalised by
one. If anything here is wrong, it is most likely that +20.8 was a high
sample and the true network gain is nearer its lower bound of +9.

Eleven other things were implemented and measured and are *not* enabled; they
are listed under "Things that were tried and left off", with their numbers. A
twelfth, fusing the accumulator's passes, was measured and reverted outright
rather than left reachable.

## Tunable constants

Everything the search and the evaluation used to hard-code as a magic number
now lives in one list in `tune.h`:

```c
TUNE(FutilityBase,  95,  10, 400)      /* name, default, min, max */
```

The list is expanded three times by the preprocessor -- to declare the
variables, to define them, and to build a name lookup -- so adding a parameter
is one line. Each one is then settable at run time:

```
setoption name FutilityBase value 110      # over UCI
tune                                       # print every current value
```

`ce_set_param()` does the same from the Python side, which is what the tuners
in `testsuite/tools/` drive.

All of them are *declared* in the `uci` reply. That is deliberate and it was
learned the hard way: they were originally hidden behind a `CE_TUNE`
environment variable to spare a GUI eighty spin boxes, and a UCI host has no
way to know an option exists unless the engine says so. `ts/engines.py` drops
undeclared options without a word, so three tuning experiments ran with
default parameters and measured nothing but an unrelated thread-count
mismatch. Set `CE_TUNE=0` to hide them again if a GUI really minds.

The defaults in `tune.h` are the values the engine actually plays with. A
tuning run never edits source: it writes a list of values, those are confirmed
with games, and only then are the defaults in `tune.h` changed by hand.

### The clock was the last thing still hard-coded

`TimeNodeEffort` and `MoveOverhead` were the only two time-management numbers
reachable from `tune.h`. Everything else about the shape of the clock was
written into `init_time` and the end-of-iteration block as bare literals: the
`0.0120 + (ply+3)^0.45 * 0.0039` that sets the soft limit, the `3.0 + ply/14`
that sets the hard one, the 0.45 share of the clock no single move may exceed,
the `1.22 - 0.06 * stability` curve, the 1.15/1.25/0.92 score-drop steps, the
0.35 for a single legal move, the 0.45 on node effort. Twenty-four of them are
now `TmOptBase` through `TmFactorMin`.

This is worth saying because it is easy to assume the opposite. The search
margins have been fitted twice -- `spsa.py --groups scaled` over 5600 games,
confirmed at -0.1 +/- 7.5 -- and that set is the *fifteen parameters the search
compares a static evaluation against*. It never touched the clock. So "the
tuner found nothing" and "time management is tuned" are two different
statements, and only the first one was ever true.

The defaults are the same numbers, not approximately the same numbers. Each
unit is a power of ten, so `TmOptBase / 10000.0` is the same double the literal
`0.0120` produced, and a harness comparing the old and new formulas over
178,200 combinations of clock, increment, moves-to-go and ply finds no
difference in the integer milliseconds the search uses, and over 125,120
combinations of the iteration factor's inputs finds no difference in any bit.
A build with nothing set keeps exactly the clock it had.

Tuning them is a separate job and a slow one, and there is a reason it never
happened by accident. `tools/spsa.py` builds its workers with
`Limit(nodes=...)` and takes `--nodes`; it has no way to ask for a clock. A
fixed-node search never calls the time-management block at all -- `init_time`
leaves `OptimumTime` and `MaximumTime` at zero and the end-of-iteration check
is skipped -- so **the only tuner in this repository cannot see any of these
parameters, whatever values it is given.** That is not a small gap: it means a
`--groups` entry for the clock would run to completion and report movement on
parameters that did nothing.

`ts.engines.Limit` already carries `wtime_ms` / `winc_ms` and `duel.py` plays
at a clock, so teaching `spsa.py` a time control is a modest change to the
worker setup rather than new machinery. Until then the clock has to be tuned
by direct A/B matches, a parameter or two at a time, which is how
`TmSoftInIter` and the soft-limit scale below were measured.

## The evaluation's two halves

`eval_hce()` accumulates material and position **separately** and only then
adds them up:

```c
int mg = matMg + posMg * PosWeightMg / 128;
int eg = matEg + posEg * PosWeightEg / 128;
```

This is not cosmetic. The piece values and the piece-square tables come from
PeSTO, where a pawn is 82 mg / 94 eg; every other term -- mobility, king
safety, threats, passed pawns, space, initiative -- was taken from an
evaluation whose pawn was about 126 mg / 208 eg. Added at face value the
positional terms therefore arrived roughly 1.5x too large in the middlegame
and 2.2x too large in the endgame, and the engine would cheerfully pay a pawn
for half a pawn of mobility. `PosWeightMg` and `PosWeightEg` put the two on
the same scale, and each block of the evaluation has its own weight on top of
that so a tuner can move one without moving the rest.

`EvalScale` is applied last, so that a reported 100 really is worth about a
pawn and the margins the search compares against mean what they say.

## Building

```
python engine/build.py              # builds engine/ce.dll and engine/ce.exe
python engine/build.py --generic    # portable build, for a different CPU
python engine/build.py --debug      # -O1 with assertions
```

`engine_core.py` rebuilds automatically when a source file is newer than the
library, so normally you never have to run this by hand.

A C compiler is required. On Windows, MSYS2/MinGW gcc is found automatically in
the usual install locations; otherwise put `gcc` or `clang` on `PATH`.

## The network

`engine/net3.bin`, built by [`../nnue`](../nnue/README.md):

```
(4 x 768 -> 1024) x 2  ->  1    SCReLU, 8 output buckets by piece count,
                                4 king buckets, files folded onto a-d
```

A perspective network: the board is encoded twice, once from each player's
point of view, with piece colours relabelled own/enemy and the squares flipped
for Black, and the halves are read own-side first. The accumulator is updated
by difference rather than recomputed, which costs 26% of the engine's speed
(1.16 M nps against 1.57 M) where a full refresh at every node would cost
several times that. `nnue/verify.py` checks every incremental update against a
refresh.

`engine/net2.bin` is the single-bucket network this replaced, and the engine
still reads it -- `--net net2.bin`, or the `ce-net2` opponent in
`suite.toml`. The two formats share one accumulator implementation, with the
bucket count, the fold and L1 all read from the file header, because the
older format is exactly the newer one with a single bucket and no fold.

### The refresh cache

The sentence above -- "the accumulator is updated by difference rather than
recomputed, which costs 26% of the engine's speed" -- was true and was also
hiding where the money went. Counting how every evaluation obtained its
accumulator, over a depth-13 bench:

| | `nnue` | `blend` |
| --- | --- | --- |
| evaluations | 333,016 | 389,702 |
| updated by difference | 52.4% | 54.0% |
| **rebuilt from scratch** | **47.6%** | **46.0%** |
| feature rows read by the rebuilds | 7,286,986 | 8,405,112 |

Nearly half of all evaluations rebuilt both halves over every piece on the
board, and since the incremental path reads some 2.2 rows a call against the
rebuild's 22.6, the rebuilds were about **85% of the accumulator's entire
work**. The 26% was mostly not the incremental update at all.

The reason is structural rather than a bug. `acc_for` can only update by
difference when the slot one ply up holds this position's parent, and the
search arranges for it not to: a node that takes its static evaluation from
the transposition table never computes an accumulator, so its children find a
stale slot. On a refresh the parent slot was found *set but holding another
line* 179,349 times out of 179,361.

So each view now keeps the last accumulator built in it and the board that
accumulator belongs to, and a rebuild pays only for the squares that differ.
One entry per (perspective, king bucket, fold) -- 16 per thread, about 51 KB --
because that is exactly what a feature index depends on, so a diff against the
entry is meaningful. Measured over the same bench, a rebuild went from 22.6
feature rows to **4.6**: the pieces move, but most of them are still where they
were when that view was last wanted.

| bench 13, best of 6 interleaved | `nnue` | `blend` |
| --- | --- | --- |
| before | 643 ms | 994 ms |
| after | **467 ms** | **755 ms** |
| nodes searched | identical | identical |

**+37.7% and +31.7% nodes per second.** The node counts are identical and so
is every reported depth, score and best move across all twelve positions, which
is what makes this one safe to accept on a timing measurement rather than on
games: the search makes exactly the same decisions, only sooner. It is the same
argument the thread pool was accepted on.

It was then played anyway, because 30% is large enough to resolve and a number
with an error bar is worth having: **+31.5 +/- 12.7 Elo at 10+0.1 over 1008
games**, which makes it the largest single gain in this file -- ahead of the
king buckets' +20.8. The two measurements agree about as well as they can:
31.7% more speed is 0.40 of a doubling, which at the 60-to-80 Elo a doubling is
worth to an engine of this strength predicts +24 to +32.

That agreement is the useful part. Everywhere else in this file a fixed-node or
fixed-work figure has flattered a change that then failed on a clock -- the
1536-wide net is the clearest case, +33.8 at fixed nodes and -2.7 on a clock.
The difference is that those changes bought *accuracy* and paid for it in
speed, so the two measurements were pulling against each other. This one buys
speed and changes nothing else, so there is nothing for the clock to take back.

A thread-local cache cannot be cleared from the loader, so each entry carries
the network's load generation instead; loading a different net invalidates
every entry on every thread without touching any of them.

The cache is checked against a build that does not use it. Checking the
incremental path against a *cached* refresh would not do: the chain's root is
itself a cached build, so an error in the cache propagates into the incremental
result and the two agree while both are wrong. `CE_NNUE_VERIFY` now recomputes
the from-scratch sum inside `acc_build_side` as well, and `nnue/verify.py`
agrees over 423,272 nodes.

This also partly reopens a question the README had closed. L1 = 1536 was
rejected because it was 25% slower, not because it was a worse evaluator -- it
was **+33.8 +/- 8.1 at a fixed node count**, the best number in this file.
Accumulator work scales with L1, so the cache hands a wide net proportionally
more of its deficit back than it hands the narrow one. That is not a
measurement, and the wide net still has to pay for its extra rows; but the
comparison is worth running again.

### King buckets

The same piece on the same square means something different depending on
where its own king is standing, and one set of 768 planes has to average over
all of it. Each king region gets its own copy of the first layer, and the
board is folded onto files a-d by the perspective's own king file, which is
free -- a position and its mirror are the same position. Only the king's own
side is affected, so a king move rebuilds that half of the accumulator and the
other half still updates by difference.

The bucket map follows where kings actually are, not where the board looks
symmetric. Measured over 1.2 M king placements:

| region | share | bucket |
| --- | --- | --- |
| back rank, b/g file | 30.0% | 0 (castled) |
| back rank, d/e file | 29.7% | 1 (never castled) |
| back rank a/h and c/f, plus rank 1 | 19.4% | 2 |
| rank 2 and beyond | 20.9% | 3 (endgame king) |

An even split of the *board* would have put two thirds of the positions in one
bucket and 3% in another. A bucket is only worth its weights if enough
positions land in it.

**+20.8 +/- 11.4 Elo at 10+0.1 over 1404 games**, and +18.5 +/- 7.9 at 25k
nodes over 4000. Held-out loss went from 0.009119 to 0.008576.

The width is a separate question, and the answer is no. A first attempt kept
the buckets and widened L1 to 1536:

| | net2 | net3 (buckets, 1024) | rejected (buckets, 1536) |
| --- | --- | --- | --- |
| nodes to depth 11 | 295,043 | 261,415 | 280,220 |
| nps | 1,061 k | 994 k | 794 k |
| time to depth 11 | 0.278 s | **0.263 s** | 0.353 s |
| Elo, 25k nodes | -- | +18.5 +/- 7.9 | **+33.8 +/- 8.1** |
| Elo, 10+0.1 | -- | **+20.8 +/- 11.4** | **-2.7 +/- 11.7** |

The wide net is the better evaluator by a long way and does not win a single
game for it. At a fixed node count it is not charged for being 25% slower; on
a clock it is, and the whole of its +33.8 goes. The narrow one is both better
than net2 *and* faster to depth than net2, because an evaluation that orders
moves better pays for itself in tree size.

That pair of numbers is the clearest statement in this file of why the fixed
node figure never decides anything on its own.

This replaced a 769 -> 256 -> 1 network with one shared view of the board and
a turn bit, which lost to the hand-crafted evaluation 21-3; see
[`../nnue/README.md`](../nnue/README.md) for why that was structural rather
than a matter of size. The old `net.bin` format is no longer read.

Measured over 200k positions from a shard the network never trained on,
against Stockfish scores at depth 40-plus:

| EvalMode | correlation | residual rms |
| --- | --- | --- |
| `hce` | 0.728 | 176 cp |
| `nnue` | 0.809 | 150 cp |
| `blend` | 0.818 | 147 cp |

`blend` mixes the two, weighted by `BlendNnueWeight` out of 128, and beats
either of them on its own -- two evaluations wrong in different ways average
out better than the better one alone. 96 is where that peaks, measured over
120k held-out positions:

| `BlendNnueWeight` | 0 | 48 | 64 | **96** | 112 | 128 |
| --- | --- | --- | --- | --- | --- | --- |
| correlation | 0.729 | 0.798 | 0.810 | **0.818** | 0.815 | 0.809 |

It was fixed at 1:3 the other way, towards the hand-crafted evaluation, which
was right when the network was the 769-256-1 one that lost to it 21-3. Note
that `blend` pays for both evaluations, so a correlation it wins by is not a
game it wins by.

That last sentence was the reason to distrust 96, so it was then measured in
games as well -- every candidate weight against 96 itself, at 25k nodes a move
with one thread, which takes speed out of the comparison entirely. It has to:
`blend` computes *both* evaluations whatever the weight, so the weight costs
nothing in nodes per second and a fixed-node match measures exactly the thing
that changed.

| `BlendNnueWeight` | 64 | **80** | 96 | 112 |
| --- | --- | --- | --- | --- |
| games | 1200 | 4000 | -- | 1504 |
| score | 47.38% | 50.61% | -- | 45.55% |
| Elo vs 96 | **-18.3 +/- 15.1** | **+4.3 +/- 7.6** | reference | **-31.0 +/- 12.4** |

A quadratic through those four points peaks at about 85 and is worth some
+7 Elo over 96, which is inside the error bar of every measurement that
produced it; 80 over 4000 games cannot be separated from 96 either. **96 is
kept.** Resolving +4 from 0 would take on the order of 20000 games.

The useful content of the table is not the peak, it is the asymmetry. The
correlation curve is nearly flat from 64 to 112; the Elo curve is not. Going
*up* from 96 costs 31 Elo, which is most of what `blend` was worth over
`nnue` in the first place, and the correlation at 112 (0.815) gives no warning
of it at all. Correlation and Elo disagree about the shape even where they
agree about the location -- the same lesson as the evaluation fit further
down, arrived at from the other direction.

### In games

| test | result |
| --- | --- |
| `nnue` vs `hce`, 25k nodes a move, 1000 games | **+144.3 +/- 22.1 Elo** |
| `nnue` vs `hce`, 10+0.1, 602 games | **+131.8 +/- 26.8 Elo** |
| `blend` vs `nnue`, 10+0.1, 602 games (net2) | **+48.8 +/- 17.9 Elo** |
| `blend` vs `nnue`, 10+0.1, 800 games (net3) | **+34.9 +/- 15.6 Elo** |
| `blend` vs **v1.1**, 10+0.1, 602 games | **+201.8 +/- 29.1 Elo** |

The last row is the whole change together -- the new evaluation terms and the
network against the build this work started from.

The suites agree. The mate suite had been stuck at 20 of 21 for both v1.1 and
the improved hand-crafted evaluation; with the network it is **21 of 21**, and
the tactics suite is 54 of 54 at 3 s a move.

The first takes speed out of the comparison entirely and measures only the
evaluation; the second is the one that decides, because it makes the network
pay for itself. They agree, which is the point: the fixed-node figure is not
flattering the slower evaluation by much.

The third is the surprise. `blend` pays for *both* evaluations and gives up a
quarter of its speed for it, and it still wins -- the two are wrong about
different positions, so averaging them is better than the better one alone.
That is why `blend` is the default.

The fourth row exists because the third was made against **net2**, before the
king buckets, and there was a good argument that it should no longer hold. The
network got 20 Elo better; the hand-crafted evaluation did not; and this
project has already seen that effect once, when `LmrCaptHistDiv` fell from
+11.6 to +5.4 for exactly that reason -- an evaluation that is already right
leaves a correction less to do. On top of that `blend` is not 25% slower any
more but **1.62x slower to depth** (467 ms against 755 ms to depth 13), because
the refresh cache helped `nnue` more than it helped `blend`. So the expectation
was that `blend`'s margin had shrunk, possibly to nothing.

It has not. **+34.9 +/- 15.6 over 800 games at 10+0.1 with net3**, which cannot
be separated from the +48.8 +/- 17.9 it scored with net2. Blending a worse
evaluation into a better one is still worth more than the 0.7 of a ply it
costs, and the `LmrCaptHistDiv` analogy simply does not transfer. Worth
recording as a prediction that was made explicitly and then failed, rather than
quietly dropping it: the reasoning was sound and the answer was still no.

And the speed it costs is much smaller than the evaluation itself is:

| EvalMode | nodes to depth 13 | nodes/second |
| --- | --- | --- |
| `hce` | 1,095,015 | 919 k |
| `nnue` | 717,170 | 874 k |
| `blend` | 807,636 | 676 k |

The network's evaluation is 26% dearer per node, but the tree it has to search
is 35% smaller -- a better evaluation orders moves better and prunes more
soundly, so it reaches the same depth from fewer nodes. The two nearly cancel,
and `nnue` comes out about 5% slower overall rather than 26%.

`NnueScale` puts the network on the hand-crafted evaluation's scale, whose
units are about 1.9 to the centipawn and which every search margin in `tune.h`
was tuned against. Its default of 154 is measured, not nominal: the net was
trained at 400 cp per logit, but a loss taken in win-probability space
deliberately compresses large scores, so the slope that actually comes out is
lower. Without this, changing `EvalMode` would quietly change the meaning of
every margin in the search as well.

```
python testsuite/tools/eval_compare.py        # both evaluations, same positions
```

## Pondering

The engine thinks on the opponent's clock, both through the Python bridge that
lichess-bot uses and over ordinary UCI.

The search used to be synchronous, which had two consequences. lichess-bot
drives a homemade engine by calling `search` and waiting for it, so half of
every game -- the opponent's half -- was spent doing nothing. And `go` blocked
the UCI command loop, so `stop` was never read: its handler was dead code,
`go infinite` could not be ended, and `go ponder` was impossible, which means
no ordinary UCI host could make the engine ponder either.

`ce_search_start` now runs the search on its own thread. After playing a move
the engine starts an unlimited search on the position that follows the reply it
expects. If the opponent plays that move, `search_ponderhit` hands the running
search a clock -- replacing the limits and restarting the timer from now, so
the thinking already done is kept -- and it carries on from the depth it has
reached. If they play anything else the search is thrown away, and what it
found stays in the transposition table either way.

Measured through the bridge: 1.5 s of pondering followed by a 500 ms move
returns 3.8 M nodes at depth 20, against 348 k nodes at depth 16 for the same
position searched cold with the same 500 ms.

Because UCI requires exactly one `bestmove` per `go` whether the search ended
on its own or was cut short, the search thread announces its own result through
a callback: the command loop cannot do it, since it is blocked reading the next
command when a search ends by itself. A ponder miss is the one case with
nothing to announce, and `ce_search_abort` silences it.

Three things that were wrong and are worth remembering:

* **A ponder hit could arrive before the search it was for had started.**
  `search_go` copies the limits and computes the clock itself, on the search
  thread. `ce_search_start` returned as soon as the thread was created, so a
  hit handed over in the window before that happened wrote the real clock and
  was then overwritten by the infinite limits the search had been started
  with -- after which nothing stopped it and the bot hung on its own move.
  It needed the opponent's reply to arrive within a millisecond or two of the
  ponder search starting, which on a fixed `movetime` essentially never
  happens and on a real clock happens on every move, so the first three test
  suites all passed while the thing was unusable. `ce_search_start` now waits
  for the search to take ownership of its limits before returning, and
  `search_ponderhit` refuses to leave a search with no deadline at all.

* **`ce_search_abort` spun with `Sleep(0)`.** It has to re-assert the stop in a
  loop, because `search_go` clears `g_stop` when it starts and a stop raised
  in between would otherwise be lost. But `Sleep(0)` and `sched_yield` only
  give way to threads already runnable on the same core, so on a machine with
  more work than cores the spinner can hold the processor it is waiting for
  and the search thread never runs -- a livelock that appears only under load,
  which is exactly when a ponder miss has to be answered quickly. It sleeps a
  millisecond now.
* **A one-move principal variation left nothing to ponder on**, and that
  happens often: an iteration cut short, a window that failed high, or a child
  claiming a draw all produce one. It was costing about a third of the chances
  to think on the opponent's clock. `search_go` now plays its own move and
  asks the transposition table what the reply was, which took the rate from
  7 moves in 10 to 10 in 10.

```
python testsuite/tools/test_ponder.py          # over UCI
python testsuite/tools/test_ponder_bridge.py   # through the Python bridge
```

`ponder: true` under `engine:` in `config.yml` turns it on; lichess-bot
already suppresses it where pondering would be wrong, such as the first move
of a game.

## The evaluation terms taken from Stockfish

The evaluation was missing whole blocks that Stockfish 11 and 12 have. These
were added with Stockfish's own values, scaled by 1.2 because its terms are
denominated in a pawn of 124 mg / 206 eg while a pawn here is 97 / 123:

* threats against the queen -- squares a knight would fork from or a slider
  skewer along, which are worth something while still empty because the queen
  has to keep answering for them
* a rook whose file, or a bishop whose diagonal, already crosses the enemy
  king ring
* checks the enemy cannot safely give, which still constrain the king
* the mobility difference, folded into king danger: a side being slowly
  squeezed did not look like one in any danger, because mobility only counted
  as a score of its own
* a king on a flank with no pawn of either colour
* isolated *and* doubled priced as one weakness rather than two half ones
* a pawn levered twice and defended none
* own pawns on the fifth or sixth blocked by an enemy pawn
* a minor far from its own king, and a queen settled in enemy territory

Worth **+11.1 +/- 8.9 Elo** over 3612 games against v1.1 at 25k nodes a move
(two independent runs: +13.8 +/- 13.3 over 1610, then +8.9 +/- 11.9 over 2002).

## What the evaluation is still short of, and one way *not* to find out

The diagnosis is not in doubt. `testsuite/tools/eval_bias.py` regresses the
residual against Stockfish's deep scores on each block of the evaluation in
turn, and every positional block comes out under-weighted -- king safety,
mobility, passed pawns and pawn structure at roughly a third of the relative
weight Stockfish gives them, with only threats and piece placement near parity.
The evaluation is, in effect, material and piece-square tables with a light
positional garnish, which is exactly the profile of an engine that out-searches
an opponent and gets outplayed anyway.

Acting on that by fitting the weights did not work, and the way it failed is
worth keeping:

* `testsuite/tools/eval_fit.py` fits parameters to maximise the **correlation**
  with deep reference scores. Correlation was chosen because no change of scale
  can move it, which closes the hole that cost this project 80 Elo once before
  (see `tune_eval.py`: a fit that improved its loss by flattening every weight,
  better calibrated and much worse at choosing moves). Correlation cannot be
  bought with timidity.
* It fitted cleanly. Held-out correlation over a *different shard* of the
  lichess dump went from 0.7325 to 0.7381, and the directions it chose were
  exactly the ones the bias diagnostic had pointed at: mobility +70%, king
  danger +66%, endgame positional weight +74%, threats -40%.
* It scored **-44.8 +/- 14.8 Elo** over 1600 games.

So correlation with a deep search is not a proxy for playing strength either,
and for a reason the flattening argument does not cover: the margins the search
compares against -- `RfpMargin`, `FutilityBase`, `NmpMargin`, every one of them
-- were tuned against the distribution of *this* evaluation's differences.
Change the shape and they are all mis-set, even with the global scale held
fixed. A shape change and a search retune are one experiment, not two, and the
shape half cannot be measured alone.

The same change applied in a *modest* form was measured separately, and it is
the more interesting result. King danger +38%, shelter +45%, mobility +40%
and +37%, nothing else touched and `EvalScale` left alone -- moving exactly
the weights the diagnostic points at, by an amount small enough that the
search margins should still roughly fit:

**+3.6 +/- 11.6 Elo over 2004 games.** Nothing.

So the under-weighting is real, measurable, and *not* what is costing the
games. Both halves of this have to move together or neither does, and that
makes the hand-crafted evaluation a poor place to spend the next effort --
which is most of why the network was built. `eval_bias.py` and `eval_fit.py`
are kept for the diagnosis, which is sound, rather than for their
recommendations.

## The search margins were not the binding constraint after all

The argument above ends by blaming the margins: a change to the evaluation's
*shape* cannot be measured on its own, because `RfpMargin`, `FutilityBase`,
`NmpMargin` and the rest were all fitted against the distribution of this
evaluation's errors. The network is a change of shape -- a different, less
noisy, differently biased error distribution -- so by that argument the
margins should now be mis-set, and re-fitting them should be worth something.

It is not.

```
python testsuite/tools/spsa.py --engine ce --groups scaled \
       --iters 700 --games 8 --nodes 20000        # 5600 games, 75 min
```

`scaled` is the fifteen parameters the search compares against a static
evaluation, which is exactly the set the argument implicates. The run moved
every one of them:

| | Rfp | RazorBase | RazorMult | FutBase | FutMult | QsFut | NmpMargin | NmpEvalDiv | Probcut | SingMrg | SingDbl | SingTrp | LmrDeeper | QuietBonus | AspInit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| default | 85 | 200 | 110 | 95 | 125 | 180 | 330 | 200 | 185 | 56 | 80 | 220 | 70 | 60 | 10 |
| against `hce` | 81 | 202 | 119 | 77 | 123 | 183 | 347 | 210 | 199 | 53 | 70 | 207 | 77 | 48 | 9 |
| against `blend` | 80 | 220 | 118 | 82 | 118 | 187 | 345 | 204 | 192 | 58 | 71 | 212 | 76 | 49 | 9 |

**-0.1 +/- 7.5 Elo over 4000 games** at 25k nodes against the defaults --
49.99%, as flat a result as this harness can produce. Not adopted.

The third row is the interesting one. An earlier run of the same fifteen
parameters against the *hand-crafted* evaluation had landed in very nearly the
same place: fourteen of fifteen agree in direction and most of them in
magnitude. The margins the network wants are the margins the old evaluation
wanted. The optimum did not move when the evaluation changed, which is why
re-fitting it buys nothing -- and which means the "a shape change and a search
retune are one experiment" argument, though sound as a reason the eval fit
could not be measured alone, does not imply that the margins carry Elo of
their own once the evaluation improves.

Two caveats worth keeping. The tuner plays at 20k nodes a move and the bot
plays at a real clock with six threads, so this rules out a large effect in
the shallow regime rather than at every depth. And SPSA over fifteen
parameters at eight games an iteration resolves directions far better than
magnitudes; what it rules out is a mis-set *of the size that would show up in
4000 games*, which on this hardware is about 8 Elo.

## Things that were tried and left off

All of these are implemented, all are reachable from `tune.h`, and all are
disabled because they were measured and did not pay:

* **`QsChecks`** -- quiet moves that give check, searched at the first
  quiescence ply. It costs 8% more nodes to reach the same depth, takes the
  mate suite from 20/21 to 19/21, and scored 48.75% over 600 games at 150 ms a
  move (-8.7 +/- 17.6 Elo). Re-measured against the network, in case an
  evaluation that is untrustworthy in different places moved the balance:
  **-12.2 +/- 11.7 over 1600 games.** It did not.
* **`LazyMargin`** -- stop the evaluation after material and the piece-square
  tables when that alone is already outside the search window by this much.
  The shortcut can only fire where the window is wide, and this search's own
  pruning makes that rare, so it bought 2% more nodes per second in exchange
  for 9% more nodes to the same depth -- 6.7% *slower* to depth -- and scored
  49.0% over 300 games (-6.9 +/- 26.7 Elo).
* **`CaptHistDiv`** -- the capture history's share of the noisy move score.
  At the default of 1 a pawn capture with saturated history (+10692) sorts
  above an untried queen capture (8 * 975 = 7800), which looked wrong next to
  Stockfish, which uses the same clamp and divides by 16. Measured, it is not:
  a divisor of 16 scored **-3.3 +/- 12.1** and of 4 **-5.2 +/- 11.8** over
  1600 games each. The ordering survives because the good-noisy stage is SEE
  gated, so every candidate is already a non-losing capture and the history is
  genuinely the more informative term among them.
* **`PawnHistWeight`** -- a quiet-move history keyed on the pawn hash, which
  should generalise across transpositions the from/to and continuation tables
  cannot see. **+1.1 +/- 11.5 over 1600 games.** Nothing. There are five
  correction histories already and this is the sixth table to tell the search
  much the same thing.
* **`ScaleEndgames`** -- opposite bishops with the heavy pieces still on, a
  defending king standing in front of everything, and rook endings a pawn up.
  Screened at +8.9 over 1600 games and then measured at **-1.6 +/- 7.3 over
  4000**, which is the whole argument for confirming a screen.
* **`FutilityCaptBase`** -- futility pruning of captures, the quiet version's
  bet with the captured piece added in. Screened +10.0, confirmed
  **+3.7 +/- 7.5 over 4000 games.** Not enough to adopt.
* **`TmSoftInIter`** -- checking the soft time limit *inside* an iteration as
  well as between them. The diagnosis behind it is sound and measurable: in
  between iterations the search compares against `OptimumTime * factor`, but
  inside one only `MaximumTime` applies, and that is three to five times
  larger, so an iteration that turns out to be long overshoots the soft target
  by up to that much. Checked at a root-move boundary, which is where the move
  that would actually be played already has a fresh score.

  It works, in the sense that it does what it was built to do. Over a 10+0.1
  self-game the move-time distribution went from 339 ms mean / 553 p90 /
  1229 p99 to 283 / 422 / 945.

  And it loses games at every setting, measured at 10+0.1:

  | `TmSoftInIter` | 100 | 150 | 200 |
  | --- | --- | --- | --- |
  | games | 64 | 208 | 208 |
  | Elo vs off | **-71.6 +/- 51.5** | **-5.0 +/- 27.4** | **-26.8 +/- 25.4** |

  The premise was wrong. The overshoot is real; the time is being *used*, not
  wasted. At 100 the check fires on ordinary moves rather than outliers --
  that is what the 17% drop in the *mean* says -- and giving up a sixth of the
  thinking to bank a sixth of the clock is a bad trade at 70 Elo. 150 and 200
  fire rarely enough to be indistinguishable from off, and the pair is not even
  monotone, which is what two +/-26 error bars look like. There is no setting
  where this pays. Kept at 0, which costs one integer test per root move.

  The `0.45` share of the clock no single move may exceed, which is the thing
  that actually prevents the fatal case, is now `TmClockCap` and is tunable;
  that is the more promising end of this.
* **`NnueWalkBack`** -- replaying the accumulator chain from further back than
  one move, instead of rebuilding. The chain breaks often (see "The refresh
  cache"), and 99.0% of the breaks have a replayable ancestor within 16 ply:
  23.2% at two, 10.1% at three or four, the rest spread out to sixteen. The
  boards in between are reconstructed by stepping *backwards* from the one in
  hand, so nothing is stored at make-move time and the feature costs nothing
  while it is off.

  It is worth nothing, and the reason is that the refresh cache got there
  first. Replaying a ply reads about 2.2 feature rows; a cached rebuild reads
  4.6. Break-even is therefore two plies, which is where only 23% of the breaks
  are, and three plies is already a loss. Measured at caps of 1, 2, 3 and 4 the
  node counts are identical and the bench times are 777, 794, 784 and 776 ms;
  an interleaved best-of-7 of 1 against 2 gives -0.65% in `nnue` and +1.35% in
  `blend`. That is noise with both signs.

  Kept at 1, which is the old one-move behaviour exactly, because the
  arithmetic that rejects it is an arithmetic *about the refresh cache* -- if
  that ever changes, this becomes worth re-measuring, and `verify.py` already
  drives it at its maximum on purpose.
* **Fusing the accumulator's passes** -- applying a move's three or four
  features in one traversal instead of one each. This is the obvious companion
  to the item above and it is **-4.0% in `nnue` and -2.7% in `blend`**, so it
  was reverted rather than left reachable.

  It does what it claims: it saves read-modify-write traffic on the
  accumulator. The accumulator is 2 KB and lives in L1, and that traffic was
  not the cost. The weight rows are 2 KB apart in a 6 MB table, and reading
  each one sequentially is what the prefetcher is good at; touching all of them
  in a strided interleave, which is what one fused pass does, is what it is bad
  at. Reading *fewer rows* is the only thing that pays here, which is why the
  cache is worth 30% and this is worth less than nothing.
* **`NnueFiftyDamp`** -- the fifty-move damping, applied to the network.
  `taper()` has always scaled the hand-crafted score by (200 - halfmove) / 200
  and the network never saw it, so `nnue` reported the same number at halfmove
  98 as at 0. Fixing that is clearly *right*, and it is worth
  **+2.7 +/- 7.5 over 4000 games**, which is not enough to demonstrate. Left
  off, with the asymmetry noted: it has no measured downside and a principled
  argument for it, so it is the first thing to turn on if the evidence ever
  improves.

Set any of them to a non-zero value to try again; a longer time control is the
obvious thing to vary for the ones that trade accuracy for speed.

### What the screen got wrong, and why it is in this file

Ten switches were screened at 1600 games (+/-12 Elo) and the five that came
out highest were confirmed at 4000 (+/-7.5). The screen said +17.6, +13.3,
+10.9, +10.0 and +8.9; the confirmations said +11.6, +7.3, +2.7, +3.7 and
**-1.6**. Every one came down, and the last changed sign.

That is not bad luck, it is what selecting the top five of ten measurements
each carrying a +/-12 error bar does. A screen is for throwing away losers.
Anything kept on the strength of one has to be measured again before it is
believed, and the second measurement has to be the one that is quoted.

## What to try next

Ranked by expected value per hour spent. The Elo figures are estimates from
what the same changes are worth in engines of this strength, not measurements
on this one -- the measured results in this file are the ones with error bars.

### The evaluation

0. ~~**Tune `BlendNnueWeight` on games.**~~ **Done, and the answer is no.**
   Four weights against 96 over 7700 games put the Elo peak at about 85 and
   worth +7 there, which is inside the error bar; 80 over 4000 games scored
   +4.3 +/- 7.6 and 96 was kept. The table is above under "The network". What
   the experiment did buy is the knowledge that the curve is *asymmetric*:
   112 is -31 Elo, so the one change that looked safe on the correlation
   numbers is the one that would have cost real games. (measured: +0)

   Dropping the hand-crafted half altogether -- `EvalMode=nnue`, which the
   weight sweep could not test because `blend` computes both evaluations at
   every weight -- was measured separately against net3 and is **-34.9 +/-
   15.6 over 800 games**. See "In games". (measured: +0)
1. **Generate training data from this engine's own games.** The network is
   trained on 241 M lichess positions, which is a lot of data about positions
   *Stockfish* reached. The distribution a search actually visits is
   different, and every strong network is trained on self-play from the engine
   that will use it. Play a few hundred million positions at a fixed shallow
   depth, label with the engine's own deeper score, and retrain. This is the
   single largest remaining gain and the one every other network improvement
   multiplies. (+50 to +150)
2. **Add the game result to the target.** The lichess dump has scores but no
   outcomes, so the loss is `sigmoid(cp/400)` alone. Mixing in who actually
   won -- `λ·wdl + (1-λ)·sigmoid(cp/400)` with λ around 0.3 -- is standard
   because a score is an opinion and a result is not. Needs self-play data, so
   it comes with the item above. (+20 to +40)
3. ~~**King buckets and horizontal mirroring.**~~ **Done and adopted:
   +20.8 +/- 11.4 Elo at 10+0.1.** See "King buckets" above. The half of this
   item that said "then widen L1 to 1536 or 2048" was tried and rejected --
   the width is what made it unaffordable.

   **That rejection is worth revisiting now, and it is the strongest lead
   left.** L1 = 1536 was never a worse evaluator; it was **+33.8 +/- 8.1 at a
   fixed node count**, the largest number in this file, and it gave all of it
   back to being 25% slower. Since then the refresh cache has taken 80% off
   the rebuild path, and accumulator work scales with L1 -- so the wide net
   gets proportionally more of its deficit back than the narrow one does. That
   does not mean it now wins: it still reads 1.5x the rows per feature, and
   the cache has already been spent on the narrow net too, which is the
   baseline it has to beat. But the only reason it was rejected was a speed
   gap that has just moved, and re-running `ce-net2`-style same-binary duel
   against a 1536 export is cheap. 2048 remains a separate and much worse bet.
4. ~~**Re-tune the search margins against the network.**~~ **Done, and it is
   worth nothing.** `spsa.py --groups scaled` over 5600 games, confirmed at
   -0.1 +/- 7.5 Elo over 4000; the values it found are nearly the ones an
   earlier run found against the hand-crafted evaluation. The section above
   has the table and what it means. `NnueScale` was not tuned separately and
   still could be. (measured: +0 for the margins)
5. **If the hand-crafted evaluation is to improve further, tune it and the
   search together.** A fit that improved the evaluation's agreement with
   Stockfish by every offline measure cost 45 Elo, and the margins being left
   where they were is the likeliest reason. Note that item 4 weakens this
   rather than supporting it: swapping in the network, which is also a change
   of shape, left the margins' optimum where it was. The difference is one of
   size -- that fit moved weights by up to 70% -- so a joint SPSA over the
   eval weights *and* `RfpMargin`, `FutilityBase`, `NmpMargin`, `RazorBase`
   and the SEE margins is still the honest way to move the eval's shape, but
   the expectation for the search half of it should now be lower.
6. ~~**Endgame scale factors.**~~ **Written and rejected.** Opposite bishops
   with the heavy pieces still on, a defending king in front of everything,
   and rook endings a pawn up -- all three of Stockfish 11's, all reachable
   through `ScaleEndgames`, and worth -1.6 +/- 7.3 over 4000 games. KRPKR and
   KQKRP proper are still unwritten, but the three cheap ones were the reason
   to expect anything. (measured: +0)

### The search

7. ~~**`LmrCaptHistDiv` is implemented and off.**~~ **On, and it is the
   largest single search gain here: +11.6 +/- 7.4 Elo over 4000 games.**
   It could not have been measured before, because the history it read was
   indexed by the wrong piece -- see the note in `tune.h`. The tree is 7.7%
   smaller to the same depth. (measured: +11.6)
8. ~~**Pawn-structure history.**~~ **Written and rejected**, +1.1 +/- 11.5
   over 1600 games at `PawnHistWeight=128`. The table is there and costs
   nothing while the weight is 0; a different weight might do better, but the
   five correction histories are already telling the search most of this.
   (measured: +0)
9. ~~**Re-measure `QsChecks` with the network.**~~ **Done: -12.2 +/- 11.7**
   over 1600 games, if anything worse than the -8.7 it scored against the
   hand-crafted evaluation. The balance did not move. (measured: +0)
10. **Use the ponder hit in time management.** A hit arrives with a tree
    already built, so the first useful iteration completes almost at once;
    the search could reasonably spend *less* time on a hit and bank it. At
    present the clock is applied as if nothing had been searched.
11. **Tune the clock.** Twenty-four time-management constants are reachable
    from `tune.h` now and not one of them has ever been fitted; the SPSA run
    that found nothing was over the eval margins, which is a different set
    entirely. See "The clock was the last thing still hard-coded". The
    obstacle is that this can only be measured at a real clock -- sixteen
    games a minute -- and a fixed-node match cannot see it at all.
12. **Diversify the Lazy SMP threads.** Every thread now searches every depth
    with the same aspiration window, and they diverge only through the shared
    table. `SmpJitter` widens each helper's window by a different amount and
    is implemented but untested: measuring it honestly needs `Threads > 1` at
    a real clock, which means one game at a time on a quiet machine, and that
    buys very few games per hour. It is the cheapest thing left that has not
    been measured.
13. ~~**Keep the search threads between moves.**~~ **Done.** The helpers are
    a condition-variable pool now (`pool_ensure` / `pool_run` / `pool_join`),
    created once rather than per move. Measured directly rather than by
    games, because it changes no decision the search makes -- 300 searches at
    `Threads=6` went from 1842 ms to 1487 ms, so **1.18 ms a move**. That is
    worth about a point at blitz and more at bullet; resolving it by playing
    games would take tens of thousands of them.

    Keeping the threads alive is safe without extra bookkeeping because both
    big thread-local tables already validate themselves: an accumulator entry
    carries the zobrist key of the position it belongs to and is rebuilt when
    it does not match, and a pawn-hash entry is by construction a function of
    the pawn placement alone. Neither can carry anything stale into the next
    search.

### What is probably not worth it

* A larger transposition table entry or a different replacement policy:
  `tt.c` already uses three-entry clusters with generation-aware replacement,
  which is the shape Stockfish settled on.
* More correction histories. There are five already, and the fourth and fifth
  are doing little, and a sixth keyed on the pawn hash was measured at +1.1.
* Syzygy tablebases. lichess-bot already consults online tablebases, so the
  engine would be paying for something it is handed.
* **A second hidden layer**, at least in the obvious form. Replacing
  `(2 x L1) -> 1` with `(2 x L1) -> L2 -> 1` was timed before it was trained,
  against the engine's real output stage and with the SCReLU activation
  hoisted out of the per-neuron loop so the comparison was fair:

  | | ns per evaluation |
  | --- | --- |
  | the current output layer | 142 |
  | with an L2 of 8 | 873 (6.2x) |
  | with an L2 of 16 | 1667 (11.8x) |
  | with an L2 of 32 | 3359 (23.7x) |

  The constraint is bandwidth, not arithmetic: an L2 of 8 has 49 KB of
  weights that must be read on *every* evaluation against the output layer's
  6 KB, and the L1 cache is 32 KB. At a node budget near 860 ns, an L2 of 8
  would roughly double the cost of a node. What makes this affordable in
  engines that do it is int8 weights, halving the bandwidth, plus skipping
  the blocks where SCReLU has zeroed the activation -- a much larger piece of
  work, and worth less than the king buckets were.

## Running it standalone

`engine/ce.exe` speaks UCI, so it works with cutechess-cli, Arena, Banksia and
friends:

```
engine/ce.exe                       # UCI mode
engine/ce.exe bench 14              # fixed-depth benchmark over 12 positions
engine/ce.exe perft 6
```

Extra UCI commands: `bench [depth]`, `perft [depth]`, `eval`, `tune`,
`d` (print FEN).
Options: `Hash`, `Threads`, `MultiPV`, `EvalMode`, plus everything in `tune.h`.

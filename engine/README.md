# The chess engine

A standalone bitboard chess engine in C. No network access, no external engine:
the only outside data it uses are the opening book (handled by lichess-bot
itself, see `engine: polyglot:` in `config.yml`) and the trained network in
`net2.bin`, built by [`../nnue`](../nnue/README.md).

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

`engine/net2.bin`, built by [`../nnue`](../nnue/README.md):

```
(768 -> 1024) x 2  ->  1        SCReLU, 8 output buckets by piece count
```

A perspective network: the board is encoded twice, once from each player's
point of view, with piece colours relabelled own/enemy and the squares flipped
for Black, and the halves are read own-side first. The accumulator is updated
by difference rather than recomputed, which costs 26% of the engine's speed
(1.16 M nps against 1.57 M) where a full refresh at every node would cost
several times that. `nnue/verify.py` checks every incremental update against a
refresh.

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

### In games

| test | result |
| --- | --- |
| `nnue` vs `hce`, 25k nodes a move, 1000 games | **+144.3 +/- 22.1 Elo** |
| `nnue` vs `hce`, 10+0.1, 602 games | **+131.8 +/- 26.8 Elo** |
| `blend` vs `nnue`, 10+0.1, 602 games | **+48.8 +/- 17.9 Elo** |
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
quarter of its speed for it, and it still wins by fifty Elo -- the two are
wrong about different positions, so averaging them is better than the better
one alone. That is why `blend` is the default. `BlendNnueWeight` was chosen on
correlation rather than games, so it is the obvious thing to tune next.

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

## Two things that were tried and left off

Both are implemented, both are reachable from `tune.h`, and both are disabled
because they were measured and did not pay:

* **`QsChecks`** -- quiet moves that give check, searched at the first
  quiescence ply. It costs 8% more nodes to reach the same depth, takes the
  mate suite from 20/21 to 19/21, and scored 48.75% over 600 games at 150 ms a
  move (-8.7 +/- 17.6 Elo).
* **`LazyMargin`** -- stop the evaluation after material and the piece-square
  tables when that alone is already outside the search window by this much.
  The shortcut can only fire where the window is wide, and this search's own
  pruning makes that rare, so it bought 2% more nodes per second in exchange
  for 9% more nodes to the same depth -- 6.7% *slower* to depth -- and scored
  49.0% over 300 games (-6.9 +/- 26.7 Elo).

Set either to a non-zero value to try again; a longer time control is the
obvious thing to vary, since both trade accuracy for speed and the balance
moves with the clock.

## What to try next

Ranked by expected value per hour spent. The Elo figures are estimates from
what the same changes are worth in engines of this strength, not measurements
on this one -- the measured results in this file are the ones with error bars.

### The evaluation

0. **Tune `BlendNnueWeight` on games.** It is at 96 because that is where the
   *correlation* peaks, and the correlation curve is flat between 64 and 112
   while the Elo curve need not be. One parameter, three matches, and `blend`
   was already worth +49 Elo over `nnue` at the correlation-chosen value --
   this is the cheapest thing on the list. (+0 to +20)
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
3. **King buckets and horizontal mirroring.** Fold the board onto files a-d by
   the side-to-move's own king file, and give each king region its own copy of
   the first layer. The accumulator then has to be refreshed when the king
   moves, which `acc_for` already handles as its slow path. Then widen L1 to
   1536 or 2048. (+40 to +100, at some speed)
4. **Re-tune the search margins against the network.** Every margin in
   `tune.h` was tuned against the hand-crafted evaluation's error
   distribution, and the network's is different -- less noisy, differently
   biased. `tools/spsa.py --groups pruning,lmr` with `EvalMode=blend`. The
   measurement that the hand-crafted evaluation could not be improved without
   this (a clean offline win that cost 45 Elo, and a modest version that was
   worth nothing) says the margins are the binding constraint, and they have
   now been handed an evaluation they were never fitted to. Also re-tune
   `NnueScale`: it is set so the network reads on the same scale as the
   evaluation the margins were tuned against, which is the right starting
   point and not necessarily the right value. (+20 to +40)
5. **If the hand-crafted evaluation is to improve further, tune it and the
   search together.** The measurement above is unambiguous: a fit that
   improved the evaluation's agreement with Stockfish by every offline measure
   cost 45 Elo, because the margins were left where they were. A joint SPSA
   over the eval weights *and* `RfpMargin`, `FutilityBase`, `NmpMargin`,
   `RazorBase` and the SEE margins is the only honest way to move the eval's
   shape, and it needs games, not positions.
6. **Endgame scale factors.** `scale_factor` covers opposite bishops, the bare
   minor cases and the two textbook draws. It has nothing for KRPKR, KQKRP,
   KBPsK with the defending king in front, or rook endings a pawn up, which
   are the endings that actually come up. Stockfish 11's `endgame.cpp` is the
   reference. (+5 to +15, and fewer absurd evaluations)

### The search

7. **`LmrCaptHistDiv` is implemented and off.** Captures are reduced by their
   position in the move list alone, with no account of how the same capture
   has fared elsewhere in the search; quiet moves have had that term for a
   while. Set it to around 6000 and measure. (+5 to +10)
8. **Pawn-structure history.** A third history table keyed on the pawn
   hash, which generalises "this move is good in this kind of position" across
   transpositions the other tables cannot see. The correction histories
   already compute the keys. (+5 to +15)
9. **Re-measure `QsChecks` with the network.** It was rejected at -8.7 Elo
   against the hand-crafted evaluation. Quiet checks matter most where the
   static evaluation is least trustworthy, and the network's is trustworthy in
   different places, so the balance may well have moved. One switch, one
   match.
10. **Use the ponder hit in time management.** A hit arrives with a tree
    already built, so the first useful iteration completes almost at once;
    the search could reasonably spend *less* time on a hit and bank it. At
    present the clock is applied as if nothing had been searched.
11. **Diversify the Lazy SMP threads.** Every thread now searches every depth
    with the same aspiration window, and they diverge only through the shared
    table. Jittering the window per thread, or skipping depths on a pattern
    for some of them, is the usual next step.
12. **Keep the search threads between moves.** `search_go` creates them and
    joins them on every search. Each one carries about 1.3 MB of
    thread-local storage -- the pawn hash and now the network's accumulator
    stack -- which Windows commits and zeroes on every thread start, so a
    seven-thread search pays for ten megabytes of zeroing and throws a warm
    pawn hash away before each move. A pool woken by a condition variable is
    the standard arrangement and would also make the pawn hash worth having
    across moves.

### What is probably not worth it

* A larger transposition table entry or a different replacement policy:
  `tt.c` already uses three-entry clusters with generation-aware replacement,
  which is the shape Stockfish settled on.
* More correction histories. There are five already, and the fourth and fifth
  are doing little.
* Syzygy tablebases. lichess-bot already consults online tablebases, so the
  engine would be paying for something it is handed.

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

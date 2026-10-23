/*
 * tune.h -- the list of constants that can be changed from outside the binary.
 *
 * Everything the search and the evaluation would otherwise hard-code as a
 * magic number lives here instead.  The list is walked by the preprocessor
 * three times: once to declare the variables, once to define them, and once to
 * build the name -> address table that `setoption` and the SPSA driver use.
 * Adding a parameter therefore means adding exactly one line.
 *
 *      TUNE(name, default, min, max)
 *
 * The defaults are the values the engine shipped with, so a build with nothing
 * set behaves exactly as it did before the parameters were pulled out.
 */
#ifndef CE_TUNE_H
#define CE_TUNE_H

#define TUNE_LIST(TUNE)                                                       \
    /* ---------------- late move reductions ---------------- */              \
    /* reductions are computed in 1/1024 of a ply and rounded down */          \
    TUNE(LmrQuietDiv,        225,   120,   400)  /* log(d)*log(m)/ (div/100) */\
    TUNE(LmrNoisyDiv,        330,   150,   600)                               \
    TUNE(LmrQuietBase,       820,  -512,  2048)                               \
    TUNE(LmrNoisyBase,      -205,  -512,  2048)                               \
    TUNE(LmrDeltaWeight,     790,     0,  2048)  /* window / rootDelta term */ \
    TUNE(LmrDeltaCap,       1024,    32,  8192)  /* clamp on the root window */\
    TUNE(LmrHistDiv,        7000,  2000, 32000)                               \
    TUNE(LmrHistMax,        2048,   512,  8192)  /* cap, in 1/1024 ply */     \
    /* The same idea for captures, which had no history term at all: a        \
     * capture that has been refuted everywhere else is worth less of a look  \
     * than one that keeps working.  0 disables it.                           \
     *                                                                        \
     * This was off for a long time and measuring it would have been a waste  \
     * of a match, because the history it read was indexed by the wrong       \
     * piece: `captured_type` was called after the move had been made, so it  \
     * named the piece standing on the destination square -- the mover -- and \
     * not the one it had just taken.  In range, so nothing ever crashed; it  \
     * simply read a bucket belonging to some other capture.  With that       \
     * fixed it is worth +11.6 +/- 7.4 Elo over 4000 games against the        \
     * single-bucket network.                                                 \
     *                                                                        \
     * Against the king-bucket network it is worth less -- +6.2 +/- 7.4 at    \
     * 25k nodes and +4.3 +/- 8.3 at 40k, pooling to +5.4 +/- 5.5 -- which    \
     * is the expected direction: an evaluation that already orders captures  \
     * well leaves a history correction less to do.  Kept on three positive   \
     * samples and a mechanism (the tree is 7.7% smaller to the same depth),  \
     * not on a single significant result. */                                 \
    TUNE(LmrCaptHistDiv,    6000,     0, 32000)                               \
    TUNE(LmrCutoffCnt,         3,     1,    16)                               \
    TUNE(LmrDeeperMargin,     70,    10,   300)  /* re-search one ply deeper */\
    TUNE(QuietBonusMargin,    60,    10,   300)  /* extra history on a rout */ \
    /* Reduction relief for checks.  These were two separate lines that were  \
     * the same condition: `givesCheck` and, after the move had been made,    \
     * `pos->checkers`.  A checking move therefore got two plies of relief    \
     * while the code read as one each.                                       \
     *                                                                        \
     * The default keeps the old behaviour, and that is a measurement rather  \
     * than inertia.  Writing what the code appears to mean (1, 0) is worth   \
     * +1.3 +/- 11.8; writing what the second line was probably *for* (1, 1)  \
     * is worth +7.3 +/- 7.5 on its own -- but stacked on LmrCaptHistDiv it   \
     * drops the pair to +1.7 +/- 7.4 against that switch's own +11.6.  Both  \
     * hand reductions back, and the search does not want them twice. */      \
    TUNE(LmrGivesCheck,        2,     0,     3)                               \
    TUNE(LmrInCheck,           0,     0,     3)                               \
    /* ---------------- shallow pruning ---------------- */                    \
    TUNE(RfpMargin,           85,    20,   200)                               \
    TUNE(RfpMaxDepth,          9,     3,    16)                               \
    TUNE(RazorBase,          200,     0,  1200)                               \
    TUNE(RazorMult,          110,    20,   600)                               \
    TUNE(FutilityBase,        95,    10,   400)                               \
    TUNE(FutilityMult,       125,    20,   400)                               \
    TUNE(FutilityMaxDepth,     9,     3,    16)                               \
    TUNE(HistPruneMult,     3800,  1000, 12000)                               \
    TUNE(HistPruneDepth,       5,     2,    12)                               \
    TUNE(LmpBase,              3,     1,    12)                               \
    TUNE(SeePruneQuiet,       24,     5,    80)                               \
    TUNE(SeePruneNoisy,       95,    20,   300)                               \
    TUNE(QsFutilityMargin,   180,    40,   500)                               \
    TUNE(QsSeeMargin,         75,     0,   300)                               \
    /* Futility pruning of captures.  Quiet moves have had this for a while;  \
     * a capture that cannot raise alpha even once the piece is in the bag is \
     * the same bet.  0 disables it, which is the default until measured. */  \
    TUNE(FutilityCaptBase,     0,     0,   600)                               \
    TUNE(FutilityCaptMult,   130,    20,   500)                               \
    TUNE(FutilityCaptDepth,    7,     2,    12)                               \
    /* The depth bounds the pruning used to hard-code.  They are here so a    \
     * tuner can reach them; the defaults are the values that were inlined. */\
    TUNE(RazorMaxDepth,        6,     2,    12)                               \
    /* ---------------- null move / probcut ---------------- */                \
    TUNE(NmpBase,              4,     2,     8)                               \
    TUNE(NmpDepthDiv,          6,     2,    16)                               \
    TUNE(NmpEvalDiv,         200,    50,   600)                               \
    TUNE(NmpMargin,          330,     0,  1000)                               \
    TUNE(NmpMarginDepth,      21,     0,    80)                               \
    TUNE(NmpMinDepth,          3,     2,     8)                               \
    TUNE(NmpVerifyDepth,      14,     6,    30)                               \
    TUNE(ProbcutMargin,      185,    50,   500)                               \
    TUNE(ProbcutImproving,    55,     0,   200)                               \
    TUNE(ProbcutMinDepth,      5,     3,    10)                               \
    TUNE(ProbcutReduction,     4,     2,     8)                               \
    /* internal iterative reduction: the depth at which a missing TT move     \
     * starts to cost a ply, separately for PV and cut nodes */               \
    TUNE(IirPvDepth,           3,     2,    10)                               \
    TUNE(IirCutDepth,          8,     4,    16)                               \
    /* ---------------- extensions ---------------- */                         \
    TUNE(SingularMinDepth,     6,     4,    12)                               \
    TUNE(SingularMargin,      56,    10,   200)                               \
    TUNE(SingularPvMargin,    79,     0,   200)                               \
    TUNE(SingularDouble,      80,    20,   400)                               \
    TUNE(SingularTriple,     220,    60,   800)                               \
    TUNE(CheckExtDepth,        8,     4,    20)                               \
    /* ---------------- correction history ---------------- */                 \
    /* the four weights are summed against 256, counting non-pawn twice */     \
    TUNE(CorrWPawn,           70,     0,   256)                               \
    TUNE(CorrWMinor,          50,     0,   256)                               \
    TUNE(CorrWMajor,          44,     0,   256)                               \
    TUNE(CorrWNonPawn,        46,     0,   256)                               \
    /* ---------------- move ordering ---------------- */                      \
    /* Capture history's share of the noisy score.  The victim is worth       \
     * 8 * SeeValue, which is 800 for a pawn and 7800 for a queen, while the  \
     * history is clamped to +/-10692 -- so at a divisor of 1 a pawn capture  \
     * with saturated history sorts above a fresh queen capture.  Stockfish   \
     * keeps the same clamp and divides by 16.  1 is what this engine has     \
     * always done; the divisor exists to find out whether that was right. */ \
    TUNE(CaptHistDiv,          1,     1,    32)                               \
    /* Pawn-structure history: a quiet-move table keyed on the pawn hash,     \
     * which generalises "this move is good in this kind of position" across  \
     * transpositions the from/to and continuation tables cannot see.  Its    \
     * share of the quiet score, over 128.  0 disables it. */                 \
    TUNE(PawnHistWeight,       0,     0,   256)                               \
    /* ---------------- aspiration / time ---------------- */                  \
    TUNE(AspInitDelta,        10,     4,    40)                               \
    TUNE(AspScoreDiv,      16384,  2048, 65535)                               \
    TUNE(TimeNodeEffort,     115,    50,   250)                               \
    /* ---------------- time management ---------------- */                    \
    /* Everything init_time and the end-of-iteration check used to hard-code.  \
     * Only TimeNodeEffort and MoveOverhead were ever reachable from here, so  \
     * the whole of the clock's shape went untuned while the search margins    \
     * were fitted twice; `spsa.py --groups scaled` is the eval-margin set and \
     * never touched any of this.  The units are chosen so that every default  \
     * below is the exact double the literal produced -- a tenth of a percent, \
     * a percent, or a plain integer -- so a build with nothing set keeps the  \
     * clock it had.                                                           \
     *                                                                         \
     * Sudden death (no movestogo), which is every control the bot plays:      \
     *   optScale = min(TmOptBase/1e4 + (ply+3)^(TmOptPlyExp/100) * ...        \
     *                  TmOptPlyMult/1e4,  TmOptFrac/1000 * time/timeLeft)     \
     *   maxScale = min(TmMaxBase/100 + ply/TmMaxPlyDiv, TmMaxCap/100)         \
     */                                                                        \
    TUNE(TmOptBase,          120,    40,   400)  /* x 1/10000 */               \
    TUNE(TmOptPlyMult,        39,    10,   150)  /* x 1/10000 */               \
    TUNE(TmOptPlyExp,         45,    20,    90)  /* x 1/100 */                 \
    TUNE(TmOptFrac,          200,    80,   500)  /* x 1/1000 */                \
    TUNE(TmMaxBase,          300,   150,   600)  /* x 1/100 */                 \
    TUNE(TmMaxPlyDiv,         14,     4,    40)                                \
    TUNE(TmMaxCap,           500,   200,  1000)  /* x 1/100 */                 \
    /* With movestogo, which lichess does not send but a GUI may */            \
    TUNE(TmMtgOpt,            88,    40,   150)  /* x 1/100 */                 \
    TUNE(TmMtgMaxBase,       150,    80,   400)  /* x 1/100 */                 \
    TUNE(TmMtgMaxMult,        11,     2,    40)  /* x 1/100, per move to go */ \
    /* Never commit more than this share of the clock to one move.  Without    \
     * it one long iteration late in a fast game loses on time. */             \
    TUNE(TmClockCap,          45,    15,    80)  /* x 1/100 */                 \
    /* The end-of-iteration factor.  A root move that keeps being the best     \
     * one needs less time; a score that fell needs more. */                   \
    TUNE(TmStabBase,         122,    80,   200)  /* x 1/100 */                 \
    TUNE(TmStabStep,           6,     0,    20)  /* x 1/100, per stable iter */\
    TUNE(TmStabMax,            8,     1,    20)  /* iterations counted */      \
    TUNE(TmDropMargin1,       40,    10,   200)                                \
    TUNE(TmDropMult1,        115,   100,   200)  /* x 1/100 */                 \
    TUNE(TmDropMargin2,      120,    40,   400)                                \
    TUNE(TmDropMult2,        125,   100,   250)  /* x 1/100 */                 \
    TUNE(TmRiseMargin,        60,    10,   300)                                \
    TUNE(TmRiseMult,          92,    50,   100)  /* x 1/100 */                 \
    TUNE(TmSingleMove,        35,     5,   100)  /* x 1/100, only one legal */ \
    TUNE(TmEffortMult,        45,     0,   150)  /* x 1/100 */                 \
    TUNE(TmFactorMin,         20,     5,    80)  /* x 1/100, floor */          \
    /* Check the soft limit inside an iteration too, not only between them.    \
     * Only MaximumTime applied in between, and that is three to five times    \
     * OptimumTime, so one long iteration overshoots the soft target by that   \
     * much.  This is the share of the soft limit, in percent, at which an     \
     * iteration already past its first root move gives up; 0 disables it and  \
     * restores the old between-iterations-only behaviour. */                  \
    TUNE(TmSoftInIter,         0,     0,   300)  /* x 1/100 of soft limit */   \
    /* Lazy SMP: how much to widen a helper thread's aspiration window, in    \
     * sixteenths, so the threads diverge from something other than the       \
     * shared table alone.  0 leaves every thread searching identically. */   \
    TUNE(SmpJitter,            0,     0,    48)                               \
    /* Milliseconds held back from the clock for everything that is not the    \
     * search: the pipe, the scheduler, and whatever the host adds.  25 is     \
     * right for a local tournament manager.  Over the network lichess-bot     \
     * subtracts its own `move_overhead` as well, plus a per-move measurement  \
     * of how long it took to receive the opponent's move, so this does not    \
     * have to cover the network.  `move_overhead` in config.yml is 100 now    \
     * rather than the shipped 1000 -- see the note there for the latency it   \
     * was measured against -- so the two together hold back 125 ms. */        \
    TUNE(MoveOverhead,        25,     0,  1000)                               \
    /* a switch, so the feature can be measured by turning it off */           \
    TUNE(UseCuckoo,            1,     0,     1)                               \
    /* Quiet checks in quiescence.  Implemented, measured, and off: it costs  \
     * 8% more nodes to reach the same depth, drops the mate suite from 20/21  \
     * to 19/21, and scored 48.75% over 600 games at 150 ms a move, -8.7 Elo.  \
     * Set to 1 to try it again at a longer time control. */                   \
    TUNE(QsChecks,             0,     0,     1)                               \
    /* Lazy evaluation: the margin the cheap material-only estimate has to     \
     * clear the search window by before the expensive half is skipped.  Also  \
     * implemented, measured, and off.  It can only fire where the window is   \
     * wide, which this search's own pruning makes rare, so it bought 2% more  \
     * nodes per second for 9% more nodes to the same depth -- 6.7% slower to  \
     * depth -- and scored 49.0% over 300 games, -6.9 Elo.  0 disables it. */  \
    TUNE(LazyMargin,           0,     0,  1600)                               \
    /* ---------------- evaluation: global weights ---------------- */          \
    /* The positional terms were taken from an evaluation whose pawn was       \
     * 126 mg / 208 eg; here a pawn is 82/94, so they arrive about 1.5x (mg)   \
     * and 2.2x (eg) too large relative to the material.  These two weights,   \
     * over 128, put them back in proportion -- and they are the whole reason  \
     * the engine used to pay a pawn for half a pawn of position. */           \
    TUNE(PosWeightMg,         55,    32,   192)                               \
    TUNE(PosWeightEg,         53,    24,   192)                               \
    /* final score * EvalScale / 100, so that 100 means one pawn */            \
    TUNE(EvalScale,          148,    60,   220)                               \
    TUNE(TempoBonus,          32,     0,    60)                               \
    /* ---------------- evaluation: individual terms ---------------- */        \
    /* 128 means "whatever PosWeight already decided"; these exist so the      \
     * tuner can move one term without moving all of them. */                  \
    TUNE(InitiativeDiv,      266,   100,  1200)  /* complexity * 100 / div */  \
    TUNE(InitiativeCap,       62,    10,   400)                               \
    TUNE(InitPawnWeight,      15,     0,    30)                               \
    TUNE(InitPassedWeight,     18,     0,    30)                               \
    TUNE(InitOutflank,         7,     0,    40)                               \
    TUNE(InitBothFlanks,      34,     0,    60)                               \
    TUNE(InitInfiltration,     18,     0,    40)                               \
    TUNE(InitConst,           29,     0,   160)                               \
    TUNE(PasserBlockWeight,   84,    20,   256)  /* k*w in the endgame */      \
    TUNE(PasserBlockMgWeight, 57,     0,   256)  /* k*w in the middlegame */   \
    TUNE(ShelterWeight,       38,    24,   192)                               \
    TUNE(KingDangerWeight,    76,    32,   256)                               \
    TUNE(BishopPairMg,        14,     0,   120)                               \
    TUNE(BishopPairEg,        39,     0,   160)                               \
    TUNE(BishopPairPawns,      0,     0,    12)  /* per pawn off the board */  \
    TUNE(ImbKnightPawns,       4,     0,    20)                               \
    TUNE(ImbRookPawns,         7,     0,    20)                               \
    TUNE(ImbQueenKnight,       10,     0,    60)                               \
    TUNE(ImbRookPair,         14,     0,    80)                               \
    TUNE(ImbMinorVsRook,      37,     0,   120)                               \
    TUNE(SpaceThreshold,    5102,  2000,  8000)                               \
    TUNE(CandidatePasserMg,    0,     0,    40)                               \
    TUNE(CandidatePasserEg,   16,     0,    80)                               \
    TUNE(TrappedBishop,      222,     0,   400)                               \
    TUNE(ConnectedRooks,      11,     0,    80)                               \
    TUNE(KingOpenFile,        36,     0,   100)                               \
    TUNE(RookOn7th,            1,     0,     1)  /* 1 = require a target */    \
    /* Endgame scale factors beyond the bare-defence cases: opposite bishops  \
     * with other pieces still on, a single-rook ending one pawn up, and a    \
     * defending king that stands in front of everything the strong side has  \
     * left.  All three are Stockfish 11's.  0 keeps the old set. */          \
    TUNE(ScaleEndgames,        0,     0,     1)                               \
    TUNE(ScaleOppBishopPc,     3,     0,    12)  /* per piece, added to 22 */  \
    TUNE(ScaleRookEnding,     48,    16,    64)  /* out of 64 */               \
    TUNE(ScaleKingInFront,    37,    16,    64)  /* base, + 7 per pawn */      \
    /* ---------------- evaluation: terms taken from Stockfish 11/12 -------- */\
    /* These are the blocks this evaluation did not have at all.  The values   \
     * are Stockfish's own, multiplied by 1.2: its terms are denominated in a  \
     * pawn of 124 mg / 206 eg while a pawn here is 97 mg / 123 eg, and the    \
     * group weight each block passes through makes up the rest.  Every one    \
     * is settable to 0, which is how each was measured on its own. */         \
    /* threats against the queen -- SF11 S(16,12) and S(60,18) */              \
    TUNE(KnightOnQueenMg,     19,     0,   100)                               \
    TUNE(KnightOnQueenEg,     14,     0,   100)                               \
    TUNE(SliderOnQueenMg,     72,     0,   200)                               \
    TUNE(SliderOnQueenEg,     22,     0,   200)                               \
    /* a minor far from its own king is a minor not defending it -- S(7,8) */  \
    TUNE(KingProtectorMg,      8,     0,    40)                               \
    TUNE(KingProtectorEg,     10,     0,    40)                               \
    /* a slider whose line already crosses the enemy king ring -- SF12 */      \
    TUNE(RookOnKingRing,      19,     0,    80)                               \
    TUNE(BishopOnKingRing,    29,     0,    80)                               \
    /* a queen deep in enemy territory that no pawn can chase -- S(-2,14) */   \
    TUNE(QueenInfiltration,   17,     0,    80)                               \
    /* own pawn on the fifth or sixth, blocked by an enemy pawn -- SF11 */     \
    TUNE(BlockedPawn5Mg,      16,     0,    80)                               \
    TUNE(BlockedPawn5Eg,       5,     0,    80)                               \
    TUNE(BlockedPawn6Mg,       5,     0,    80)                               \
    TUNE(BlockedPawn6Eg,       0,     0,    80)                               \
    /* isolated *and* doubled is worse than the sum -- SF12 S(11,56) */        \
    TUNE(DoubledIsolatedMg,   13,     0,    80)                               \
    TUNE(DoubledIsolatedEg,   67,     0,   160)                               \
    /* a pawn levered twice and supported none -- SF12 S(0,56) */              \
    TUNE(WeakLeverEg,         67,     0,   160)                               \
    /* our king on a flank with no pawn of either colour -- S(17,95) */        \
    TUNE(PawnlessFlankMg,     20,     0,   100)                               \
    TUNE(PawnlessFlankEg,    114,     0,   240)                               \
    /* king danger: a knight covering the ring is worth a hundred of it */     \
    TUNE(KdKnightDefender,   100,     0,   400)                               \
    /* checks the enemy cannot safely give still have to be answered */        \
    TUNE(KdUnsafeCheck,       60,     0,   300)                               \
    /* who has the freer position, in king-danger units (128 = 1:1) */         \
    TUNE(KdMobWeight,        128,     0,   256)                               \
    /* ---------------- evaluation: per-group weights ---------------- */       \
    /* Each block of the evaluation gets its own weight over 128 on top of     \
     * PosWeight*, so the tuner can move one group without moving the rest.    \
     * All default to 128: a build with nothing set is unchanged. */           \
    TUNE(PawnWeightMg,       100,    32,   256)                               \
    TUNE(PawnWeightEg,        94,    32,   256)                               \
    TUNE(PieceWeightMg,      136,    32,   256)                               \
    TUNE(PieceWeightEg,      175,    32,   256)                               \
    TUNE(MobWeightMg,         75,    32,   256)                               \
    TUNE(MobWeightEg,        106,    32,   256)                               \
    TUNE(PassedWeightMg,      73,    32,   256)                               \
    TUNE(PassedWeightEg,     114,    32,   256)                               \
    TUNE(ThreatWeightMg,     202,    32,   256)                               \
    TUNE(ThreatWeightEg,     182,    32,   256)                               \
    TUNE(SpaceWeight,         74,    32,   256)                               \
    /* ---------------- evaluation: the network ---------------- */             \
    /* The network reports centipawns, while the hand-crafted evaluation's     \
     * units are about twice that and every search margin in this file was     \
     * tuned against them.  This puts the two on one scale so that EvalMode    \
     * changes the evaluation and nothing else. */                             \
    /* 154 is measured, not guessed: over 120k held-out positions it puts the  \
     * network's least-squares centipawns-per-unit at 1.91, which is what the  \
     * hand-crafted evaluation reads.  The nominal 400 cp per logit the net    \
     * was trained on comes out higher than that, because a loss taken in      \
     * win-probability space deliberately compresses large scores. */          \
    TUNE(NnueScale,          154,    50,   400)                               \
    /* EvalMode=blend: the network's share of the mixture, out of 128. */      \
    /* 96 is where the correlation peaks and it survived a games test: 112    \
     * is -31 Elo over 1504 games, 64 is -18 over 1200, and 80 is +4.3 +/-    \
     * 7.6 over 4000, which is not a difference.  See the README. */          \
    TUNE(BlendNnueWeight,     96,     0,   128)                               \
    /* How far back the accumulator chain may be replayed when the slot one   \
     * ply up does not hold this position's parent.  1 is the one-move case    \
     * and nothing more.  Higher values walk further back, reconstructing the  \
     * boards in between, and cost 2.2 weight rows a ply against the 4.6 a    \
     * cached rebuild costs -- so the arithmetic says 2 is break-even and 3    \
     * is already a loss.  Measured, and it is: see the README. */            \
    TUNE(NnueWalkBack,         1,     1,     8)                               \
    /* The fifty-move damping the hand-crafted evaluation applies inside      \
     * taper() never reached the network, so in `nnue` the score did not      \
     * move as the halfmove clock ran out and in `blend` only the hand-       \
     * crafted quarter of it did.  1 applies the same (200 - halfmove) / 200  \
     * to the network's output.  0 is the old behaviour. */                   \
    TUNE(NnueFiftyDamp,        0,     0,     1)                               \
    /* ---------------- evaluation: piece values ---------------- */            \
    /* per-mille multipliers on the tabulated values, so the tuner can price   \
     * the pieces against each other without touching the tables */            \
    TUNE(MatPawnMg,         1181,   600,  1600)                               \
    TUNE(MatPawnEg,         1309,   600,  1600)                               \
    TUNE(MatKnightMg,        955,   700,  1400)                               \
    TUNE(MatKnightEg,       1349,   700,  1400)                               \
    TUNE(MatBishopMg,        976,   700,  1400)                               \
    TUNE(MatBishopEg,       1309,   700,  1400)                               \
    TUNE(MatRookMg,         1181,   700,  1400)                               \
    TUNE(MatRookEg,         1114,   700,  1400)                               \
    TUNE(MatQueenMg,        1341,   700,  1400)                               \
    TUNE(MatQueenEg,        1043,   700,  1400)

#endif /* CE_TUNE_H */

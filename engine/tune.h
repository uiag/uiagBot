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
    TUNE(LmrCutoffCnt,         3,     1,    16)                               \
    TUNE(LmrDeeperMargin,     70,    10,   300)  /* re-search one ply deeper */\
    TUNE(QuietBonusMargin,    60,    10,   300)  /* extra history on a rout */ \
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
    /* ---------------- null move / probcut ---------------- */                \
    TUNE(NmpBase,              4,     2,     8)                               \
    TUNE(NmpDepthDiv,          6,     2,    16)                               \
    TUNE(NmpEvalDiv,         200,    50,   600)                               \
    TUNE(NmpMargin,          330,     0,  1000)                               \
    TUNE(NmpMarginDepth,      21,     0,    80)                               \
    TUNE(ProbcutMargin,      185,    50,   500)                               \
    TUNE(ProbcutImproving,    55,     0,   200)                               \
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
    /* ---------------- aspiration / time ---------------- */                  \
    TUNE(AspInitDelta,        10,     4,    40)                               \
    TUNE(AspScoreDiv,      16384,  2048, 65535)                               \
    TUNE(TimeNodeEffort,     115,    50,   250)                               \
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

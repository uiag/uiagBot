/*
 * eval.c -- hand-crafted evaluation.
 *
 * Tapered mid/endgame evaluation with PeSTO piece-square tables plus material
 * imbalance, mobility, king safety, pawn structure (hashed), passed and
 * candidate passed pawns, piece placement terms, threats, space, and endgame
 * scaling / specialised endgame knowledge (KPK bitbase, KXK and KBNK mate
 * guidance, the textbook wrong-bishop and knight-with-a-rook-pawn draws).
 *
 * Material and position are accumulated separately and only combined at the
 * end, weighted by PosWeightMg / PosWeightEg.  The piece values and the
 * tables come from PeSTO, where a pawn is 82 mg / 94 eg, while every other
 * term here was taken from an evaluation whose pawn was about 126 mg /
 * 208 eg; added at face value the positional terms arrive half again too
 * large in the middlegame and more than twice too large in the endgame, which
 * is what made the engine pay a pawn for half a pawn of position.
 *
 * Returned score is always from the side-to-move's point of view.
 */
#include "ce.h"

int g_evalMode = EVAL_HCE;

const int PieceValueMg[PIECE_TYPE_NB] = { 82, 337, 365, 477, 1025, 0 };
const int PieceValueEg[PIECE_TYPE_NB] = { 94, 281, 297, 512,  936, 0 };

/* ---------------- piece-square tables (a8 .. h1 order) ------------- */

static const int PestoMg[6][64] = {
{ /* pawn */
    0,   0,   0,   0,   0,   0,  0,   0,
   98, 134,  61,  95,  68, 126, 34, -11,
   -6,   7,  26,  31,  65,  56, 25, -20,
  -14,  13,   6,  21,  23,  12, 17, -23,
  -27,  -2,  -5,  12,  17,   6, 10, -25,
  -26,  -4,  -4, -10,   3,   3, 33, -12,
  -35,  -1, -20, -23, -15,  24, 38, -22,
    0,   0,   0,   0,   0,   0,  0,   0 },
{ /* knight */
 -167, -89, -34, -49,  61, -97, -15, -107,
  -73, -41,  72,  36,  23,  62,   7,  -17,
  -47,  60,  37,  65,  84, 129,  73,   44,
   -9,  17,  19,  53,  37,  69,  18,   22,
  -13,   4,  16,  13,  28,  19,  21,   -8,
  -23,  -9,  12,  10,  19,  17,  25,  -16,
  -29, -53, -12,  -3,  -1,  18, -14,  -19,
 -105, -21, -58, -33, -17, -28, -19,  -23 },
{ /* bishop */
  -29,   4, -82, -37, -25, -42,   7,  -8,
  -26,  16, -18, -13,  30,  59,  18, -47,
  -16,  37,  43,  40,  35,  50,  37,  -2,
   -4,   5,  19,  50,  37,  37,   7,  -2,
   -6,  13,  13,  26,  34,  12,  10,   4,
    0,  15,  15,  15,  14,  27,  18,  10,
    4,  15,  16,   0,   7,  21,  33,   1,
  -33,  -3, -14, -21, -13, -12, -39, -21 },
{ /* rook */
   32,  42,  32,  51, 63,  9,  31,  43,
   27,  32,  58,  62, 80, 67,  26,  44,
   -5,  19,  26,  36, 17, 45,  61,  16,
  -24, -11,   7,  26, 24, 35,  -8, -20,
  -36, -26, -12,  -1,  9, -7,   6, -23,
  -45, -25, -16, -17,  3,  0,  -5, -33,
  -44, -16, -20,  -9, -1, 11,  -6, -71,
  -19, -13,   1,  17, 16,  7, -37, -26 },
{ /* queen */
  -28,   0,  29,  12,  59,  44,  43,  45,
  -24, -39,  -5,   1, -16,  57,  28,  54,
  -13, -17,   7,   8,  29,  56,  47,  57,
  -27, -27, -16, -16,  -1,  17,  -2,   1,
   -9, -26,  -9, -10,  -2,  -4,   3,  -3,
  -14,   2, -11,  -2,  -5,   2,  14,   5,
  -35,  -8,  11,   2,   8,  15,  -3,   1,
   -1, -18,  -9,  10, -15, -25, -31, -50 },
{ /* king */
  -65,  23,  16, -15, -56, -34,   2,  13,
   29,  -1, -20,  -7,  -8,  -4, -38, -29,
   -9,  24,   2, -16, -20,   6,  22, -22,
  -17, -20, -12, -27, -30, -25, -14, -36,
  -49,  -1, -27, -39, -46, -44, -33, -51,
  -14, -14, -22, -46, -44, -30, -15, -27,
    1,   7,  -8, -64, -43, -16,   9,   8,
  -15,  36,  12, -54,   8, -28,  24,  14 }
};

static const int PestoEg[6][64] = {
{ /* pawn */
    0,   0,   0,   0,   0,   0,   0,   0,
  178, 173, 158, 134, 147, 132, 165, 187,
   94, 100,  85,  67,  56,  53,  82,  84,
   32,  24,  13,   5,  -2,   4,  17,  17,
   13,   9,  -3,  -7,  -7,  -8,   3,  -1,
    4,   7,  -6,   1,   0,  -5,  -1,  -8,
   13,   8,   8,  10,  13,   0,   2,  -7,
    0,   0,   0,   0,   0,   0,   0,   0 },
{ /* knight */
  -58, -38, -13, -28, -31, -27, -63, -99,
  -25,  -8, -25,  -2,  -9, -25, -24, -52,
  -24, -20,  10,   9,  -1,  -9, -19, -41,
  -17,   3,  22,  22,  22,  11,   8, -18,
  -18,  -6,  16,  25,  16,  17,   4, -18,
  -23,  -3,  -1,  15,  10,  -3, -20, -22,
  -42, -20, -10,  -5,  -2, -20, -23, -44,
  -29, -51, -23, -15, -22, -18, -50, -64 },
{ /* bishop */
  -14, -21, -11,  -8,  -7,  -9, -17, -24,
   -8,  -4,   7, -12,  -3, -13,  -4, -14,
    2,  -8,   0,  -1,  -2,   6,   0,   4,
   -3,   9,  12,   9,  14,  10,   3,   2,
   -6,   3,  13,  19,   7,  10,  -3,  -9,
  -12,  -3,   8,  10,  13,   3,  -7, -15,
  -14, -18,  -7,  -1,   4,  -9, -15, -27,
  -23,  -9, -23,  -5,  -9, -16,  -5, -17 },
{ /* rook */
   13,  10,  18,  15,  12,  12,   8,   5,
   11,  13,  13,  11,  -3,   3,   8,   3,
    7,   7,   7,   5,   4,  -3,  -5,  -3,
    4,   3,  13,   1,   2,   1,  -1,   2,
    3,   5,   8,   4,  -5,  -6,  -8, -11,
   -4,   0,  -5,  -1,  -7, -12,  -8, -16,
   -6,  -6,   0,   2,  -9,  -9, -11,  -3,
   -9,   2,   3,  -1,  -5, -13,   4, -20 },
{ /* queen */
   -9,  22,  22,  27,  27,  19,  10,  20,
  -17,  20,  32,  41,  58,  25,  30,   0,
  -20,   6,   9,  49,  47,  35,  19,   9,
    3,  22,  24,  45,  57,  40,  57,  36,
  -18,  28,  19,  47,  31,  34,  39,  23,
  -16, -27,  15,   6,   9,  17,  10,   5,
  -22, -23, -30, -16, -16, -23, -36, -32,
  -33, -28, -22, -43,  -5, -32, -20, -41 },
{ /* king */
  -74, -35, -18, -18, -11,  15,   4, -17,
  -12,  17,  14,  17,  17,  38,  23,  11,
   10,  17,  23,  15,  20,  45,  44,  13,
   -8,  22,  24,  27,  26,  33,  26,   3,
  -18,  -4,  21,  24,  27,  23,   9, -11,
  -19,  -3,  11,  21,  23,  16,   7,  -9,
  -27, -11,   4,  13,  14,   4,  -5, -17,
  -53, -34, -21, -11, -28, -14, -24, -43 }
};

static void eval_imbalance(const Position *pos, int *mg, int *eg);
static int scale_factor(const Position *pos, int strongSide, int eg);

/*
 * The tail every path through the evaluation shares: the tempo (inside the
 * taper, because in a dead endgame the right to move is worth nothing), the
 * endgame scale factor, the fifty-move damping, and the output scale that
 * makes 100 mean a pawn.  Kept in one place so the lazy exit and the full
 * evaluation cannot disagree about it.
 */
static int taper(const Position *pos, int mg, int eg, int phase) {
    int tempo = pos->side == WHITE ? TempoBonus : -TempoBonus;
    mg += tempo;
    eg += tempo;

    int strongSide = eg > 0 ? WHITE : BLACK;
    int sf = scale_factor(pos, strongSide, eg);

    int v = (mg * phase + eg * (24 - phase) * sf / 64) / 24;
    v = v * (200 - pos->halfmove) / 200;
    v = v * EvalScale / 100;
    return pos->side == WHITE ? v : -v;
}

static int PsqMg[PIECE_NB][64];
static int PsqEg[PIECE_NB][64];

/* MatMul[phase][pieceType]: per-mille multiplier on the tabulated value */
static int MatMul[2][PIECE_TYPE_NB];

static void refresh_material_multipliers(void) {
    MatMul[0][PAWN]   = MatPawnMg;   MatMul[1][PAWN]   = MatPawnEg;
    MatMul[0][KNIGHT] = MatKnightMg; MatMul[1][KNIGHT] = MatKnightEg;
    MatMul[0][BISHOP] = MatBishopMg; MatMul[1][BISHOP] = MatBishopEg;
    MatMul[0][ROOK]   = MatRookMg;   MatMul[1][ROOK]   = MatRookEg;
    MatMul[0][QUEEN]  = MatQueenMg;  MatMul[1][QUEEN]  = MatQueenEg;
    MatMul[0][KING]   = 1000;        MatMul[1][KING]   = 1000;
}

void eval_tune_changed(void) { refresh_material_multipliers(); }

/* ---------------- tuning constants ---------------- */

static const int MobilityMg[4][28] = {
  { -62,-53,-12, -3,  3, 12, 21, 28, 37 },
  { -47,-20, 14, 29, 39, 53, 53, 60, 62, 69, 78, 83, 91, 96 },
  { -60,-24,  0,  3,  4, 14, 20, 30, 41, 41, 41, 45, 57, 58, 67 },
  { -29,-16, -8, -8, 18, 25, 23, 37, 41, 54, 65, 68, 69, 70, 70, 70,
     71, 72, 74, 76, 90,104,105,106,112,114,114,119 }
};
static const int MobilityEg[4][28] = {
  { -79,-57,-31,-17,  7, 13, 16, 21, 26 },
  { -59,-25, -8, 12, 21, 40, 56, 58, 65, 72, 78, 87, 88, 98 },
  { -82,-15, 17, 43, 72,100,102,122,133,139,153,160,165,170,175 },
  { -49,-29, -8, 17, 39, 54, 59, 73, 76, 95, 95,101,124,128,132,133,
    136,140,147,149,153,169,171,171,178,185,187,221 }
};
static const int MobilityCount[4] = { 9, 14, 15, 28 };

static const int KingAttackWeights[PIECE_TYPE_NB] = { 0, 81, 52, 44, 10, 0 };

/* pawn shelter / storm, indexed [file-edge-distance][rank] */
static const int ShelterStrength[4][8] = {
  {  -5,  82,  92,  54,  36,  22,  28, 0 },
  { -44,  63,  33, -50, -30, -12, -62, 0 },
  { -11,  77,  22,  -6,  31,   8,  -45, 0 },
  { -39, -12, -29, -50, -43, -68, -164, 0 }
};
static const int UnblockedStorm[4][8] = {
  {  87, -288, -168, 96, 47, 44, 46, 0 },
  {  42,  -25,  120, 45, 34, -9, 24, 0 },
  {  -8,   51,  167, 35, -4, -16, -12, 0 },
  { -17,  -13,  100,  4, -1, -12, -26, 0 }
};

static const int PassedRankMg[8] = { 0,  2, 15, 22, 46, 108, 169, 0 };
static const int PassedRankEg[8] = { 0, 38, 36, 50, 81, 166, 252, 0 };
static const int PassedFileMg[8] = { -1, 0, -9, -9, -9, -9, 0, -1 };
static const int PassedFileEg[8] = {  7, 0, -4, -4, -4, -4, 0,  7 };

static const int ConnectedSeed[8] = { 0, 7, 8, 12, 29, 48, 86, 0 };

#define ISOLATED_MG     5
#define ISOLATED_EG    15
#define BACKWARD_MG     9
#define BACKWARD_EG    24
#define DOUBLED_MG     11
#define DOUBLED_EG    56
#define WEAK_UNOPPOSED_MG 13
#define WEAK_UNOPPOSED_EG 27

/* ---------------- pawn hash table (per thread) ----------------
 *
 * Everything in a PawnEntry must be a function of the pawn placement alone,
 * because that is all the key covers.  Nothing here may depend on where the
 * other pieces stand: the table is not cleared between searches, so such a
 * field would make the evaluation depend on what was searched before, and the
 * same search would return a different answer the second time.  Blocked pawns
 * used to live here and are now in EvalInfo for exactly that reason.
 */

#define PAWN_TT_SIZE (1 << 14)

typedef struct {
    U64 key;
    int mg, eg;
    U64 passed[2];
    U64 attacks[2];
    U64 attacksSpan[2];
} PawnEntry;

static __thread PawnEntry PawnTT[PAWN_TT_SIZE];

/* ---------------- forward spans ---------------- */

static U64 ForwardFileBB[2][64];
static U64 ForwardRanksBB[2][8];
static U64 PawnAttackSpan[2][64];
static U64 PassedPawnSpan[2][64];
static U64 AdjacentFilesBB[8];
static U64 KingFlankBB[8];
static U64 CenterFiles;
static const U64 DarkSquares = 0xAA55AA55AA55AA55ULL;
static U64 QueenSideBB, KingSideBB;

/* ---------------- KPK bitbase ---------------- */

enum { KPK_INVALID = 0, KPK_UNKNOWN = 1, KPK_DRAW = 2, KPK_WIN = 4 };
#define KPK_SIZE (2 * 24 * 64 * 64)
static uint8_t *KpkDb;      /* one byte per index, allocated at init */

static unsigned kpk_index(int stm, int bksq, int wksq, int psq) {
    return (unsigned)wksq | ((unsigned)bksq << 6) | ((unsigned)stm << 12)
         | ((unsigned)file_of(psq) << 13) | ((unsigned)(6 - rank_of(psq)) << 15);
}

typedef struct { int ksq[2], stm, psq, result; } KpkPos;

static void kpk_set_initial(KpkPos *k, unsigned idx) {
    k->ksq[WHITE] = (int)(idx & 0x3F);
    k->ksq[BLACK] = (int)((idx >> 6) & 0x3F);
    k->stm        = (int)((idx >> 12) & 1);
    k->psq        = make_sq((int)((idx >> 13) & 3), 6 - (int)((idx >> 15) & 7));

    if (SquareDistance[k->ksq[WHITE]][k->ksq[BLACK]] <= 1
        || k->ksq[WHITE] == k->psq || k->ksq[BLACK] == k->psq
        || (k->stm == WHITE && (PawnAttacksBB[WHITE][k->psq] & bb_of(k->ksq[BLACK]))))
        k->result = KPK_INVALID;
    else if (k->stm == WHITE && rank_of(k->psq) == 6
             && k->ksq[WHITE] != k->psq + 8
             && (SquareDistance[k->ksq[BLACK]][k->psq + 8] > 1
                 || SquareDistance[k->ksq[WHITE]][k->psq + 8] == 1))
        k->result = KPK_WIN;
    else if (k->stm == BLACK
             && (!(KingAttacksBB[k->ksq[BLACK]]
                   & ~(KingAttacksBB[k->ksq[WHITE]] | PawnAttacksBB[WHITE][k->psq]))
                 || (KingAttacksBB[k->ksq[BLACK]] & ~KingAttacksBB[k->ksq[WHITE]]
                     & bb_of(k->psq))))
        k->result = KPK_DRAW;
    else
        k->result = KPK_UNKNOWN;
}

static int kpk_classify(KpkPos *k, const uint8_t *db) {
    int us = k->stm, them = !us;
    int good = us == WHITE ? KPK_WIN : KPK_DRAW;
    int bad  = us == WHITE ? KPK_DRAW : KPK_WIN;
    int r = KPK_INVALID;

    U64 b = KingAttacksBB[k->ksq[us]];
    while (b) {
        int s = pop_lsb(&b);
        r |= us == WHITE ? db[kpk_index(them, k->ksq[them], s, k->psq)]
                         : db[kpk_index(them, s, k->ksq[them], k->psq)];
    }
    if (us == WHITE) {
        if (rank_of(k->psq) < 6)
            r |= db[kpk_index(BLACK, k->ksq[BLACK], k->ksq[WHITE], k->psq + 8)];
        if (rank_of(k->psq) == 1 && k->psq + 8 != k->ksq[WHITE] && k->psq + 8 != k->ksq[BLACK])
            r |= db[kpk_index(BLACK, k->ksq[BLACK], k->ksq[WHITE], k->psq + 16)];
    }
    k->result = (r & good) ? good : (r & KPK_UNKNOWN) ? KPK_UNKNOWN : bad;
    return k->result;
}

static void kpk_init(void) {
    static uint8_t db[KPK_SIZE];
    KpkDb = db;

    KpkPos k;
    for (unsigned i = 0; i < KPK_SIZE; i++) {
        kpk_set_initial(&k, i);
        db[i] = (uint8_t)k.result;
    }
    int changed = 1;
    while (changed) {
        changed = 0;
        for (unsigned i = 0; i < KPK_SIZE; i++)
            if (db[i] == KPK_UNKNOWN) {
                kpk_set_initial(&k, i);
                if (kpk_classify(&k, db) != KPK_UNKNOWN) {
                    db[i] = (uint8_t)k.result;
                    changed = 1;
                }
            }
    }
}

/* Is KPK a win for the strong side?  Squares are given for white as the
 * strong side; callers mirror for black. */
int kpk_probe(int strongSide, int ksq, int psq, int weakKsq, int stm) {
    if (strongSide == BLACK) {
        ksq = flip_sq(ksq);
        psq = flip_sq(psq);
        weakKsq = flip_sq(weakKsq);
        stm = !stm;
    }
    if (file_of(psq) > 3) {                 /* mirror onto files a-d */
        ksq ^= 7; psq ^= 7; weakKsq ^= 7;
    }
    return KpkDb[kpk_index(stm, weakKsq, ksq, psq)] == KPK_WIN;
}

/* ---------------- init ---------------- */

void eval_init(void) {
    static int done = 0;
    if (done) return;
    done = 1;
    bb_init();

    for (int pt = PAWN; pt <= KING; pt++)
        for (int sq = 0; sq < 64; sq++) {
            PsqMg[make_piece(WHITE, pt)][sq] = PieceValueMg[pt] + PestoMg[pt][flip_sq(sq)];
            PsqEg[make_piece(WHITE, pt)][sq] = PieceValueEg[pt] + PestoEg[pt][flip_sq(sq)];
            PsqMg[make_piece(BLACK, pt)][sq] = PieceValueMg[pt] + PestoMg[pt][sq];
            PsqEg[make_piece(BLACK, pt)][sq] = PieceValueEg[pt] + PestoEg[pt][sq];
        }

    refresh_material_multipliers();

    CenterFiles = FileBB[2] | FileBB[3] | FileBB[4] | FileBB[5];
    QueenSideBB = FileBB[0] | FileBB[1] | FileBB[2] | FileBB[3];
    KingSideBB  = FileBB[4] | FileBB[5] | FileBB[6] | FileBB[7];

    for (int f = 0; f < 8; f++) {
        AdjacentFilesBB[f] = (f > 0 ? FileBB[f - 1] : 0) | (f < 7 ? FileBB[f + 1] : 0);
        KingFlankBB[f] = f <= 2 ? QueenSideBB : (f >= 5 ? KingSideBB : CenterFiles);
    }

    for (int r = 0; r < 8; r++) {
        U64 up = 0, down = 0;
        for (int x = r + 1; x < 8; x++) up |= RankBB[x];
        for (int x = r - 1; x >= 0; x--) down |= RankBB[x];
        ForwardRanksBB[WHITE][r] = up;
        ForwardRanksBB[BLACK][r] = down;
    }

    for (int sq = 0; sq < 64; sq++) {
        U64 fwdW = 0, fwdB = 0;
        for (int r = rank_of(sq) + 1; r < 8; r++) fwdW |= bb_of(make_sq(file_of(sq), r));
        for (int r = rank_of(sq) - 1; r >= 0; r--) fwdB |= bb_of(make_sq(file_of(sq), r));
        ForwardFileBB[WHITE][sq] = fwdW;
        ForwardFileBB[BLACK][sq] = fwdB;

        U64 adjW = 0, adjB = 0;
        U64 adj = AdjacentFilesBB[file_of(sq)];
        for (int r = rank_of(sq) + 1; r < 8; r++) adjW |= adj & RankBB[r];
        for (int r = rank_of(sq) - 1; r >= 0; r--) adjB |= adj & RankBB[r];
        PawnAttackSpan[WHITE][sq] = adjW;
        PawnAttackSpan[BLACK][sq] = adjB;
        PassedPawnSpan[WHITE][sq] = fwdW | adjW;
        PassedPawnSpan[BLACK][sq] = fwdB | adjB;
    }

    kpk_init();
}

/* ---------------- pawn structure ---------------- */

static PawnEntry *pawn_eval(const Position *pos) {
    PawnEntry *e = &PawnTT[pos->pawnKey & (PAWN_TT_SIZE - 1)];
    if (e->key == pos->pawnKey && pos->pawnKey) return e;

    e->key = pos->pawnKey;
    e->mg = e->eg = 0;

    for (int us = WHITE; us <= BLACK; us++) {
        int them = !us;
        U64 ourPawns = pieces_cp(pos, us, PAWN);
        U64 theirPawns = pieces_cp(pos, them, PAWN);
        int sign = us == WHITE ? 1 : -1;

        e->attacks[us] = pawn_attacks_bb(us, ourPawns);
        e->passed[us] = 0;
        U64 span = 0;
        U64 b = ourPawns;
        while (b) {
            int sq = pop_lsb(&b);
            span |= PawnAttackSpan[us][sq];

            int f = file_of(sq);
            int r = rel_rank(us, sq);
            U64 neighbours = ourPawns & AdjacentFilesBB[f];
            U64 stoppers = theirPawns & PassedPawnSpan[us][sq];
            U64 support  = ourPawns & PawnAttacksBB[them][sq];
            U64 phalanx  = neighbours & RankBB[rank_of(sq)];
            U64 opposed  = theirPawns & ForwardFileBB[us][sq];
            U64 lever    = theirPawns & PawnAttacksBB[us][sq];
            U64 leverPush = theirPawns & PawnAttacksBB[us][sq + (us == WHITE ? 8 : -8)];
            int doubled = (ourPawns & bb_of(sq - (us == WHITE ? 8 : -8))) != 0;

            /* backward: cannot be defended by a pawn behind it and is
             * attacked or blocked by an enemy pawn in front */
            int backward = !(neighbours & ForwardFileBB[them][sq + (us == WHITE ? 8 : -8)])
                           && (leverPush || (theirPawns & bb_of(sq + (us == WHITE ? 8 : -8))));

            int passed = !(stoppers ^ lever)
                      || (!(stoppers ^ leverPush) && popcount(phalanx) >= popcount(leverPush));
            /* a pawn is not passed if an enemy pawn is directly in front on
             * the same file and ahead of its own support */
            passed = passed && !(ourPawns & ForwardFileBB[us][sq]);
            if (passed) e->passed[us] |= bb_of(sq);

            /* blocked by an enemy pawn directly in front */
            int blocked = (theirPawns & bb_of(sq + (us == WHITE ? 8 : -8))) != 0;

            if (support | phalanx) {
                int v = ConnectedSeed[r] * (2 + (phalanx ? 1 : 0) - (opposed ? 1 : 0))
                      + 21 * popcount(support);
                e->mg += sign * v;
                e->eg += sign * (v * (r - 2) / 4);
            } else if (!neighbours) {
                /* Isolated *and* doubled is worse than either: the front pawn
                 * cannot advance and the back one cannot be defended, so the
                 * pair is one weakness rather than two half ones.  Stockfish
                 * prices that case on its own and this evaluation was adding
                 * the two penalties independently, which came to much less. */
                if (opposed && (ourPawns & ForwardFileBB[them][sq])
                    && !(theirPawns & AdjacentFilesBB[f])) {
                    e->mg -= sign * DoubledIsolatedMg;
                    e->eg -= sign * DoubledIsolatedEg;
                } else {
                    e->mg -= sign * ISOLATED_MG;
                    e->eg -= sign * ISOLATED_EG;
                    if (!opposed) {
                        e->mg -= sign * WEAK_UNOPPOSED_MG;
                        e->eg -= sign * WEAK_UNOPPOSED_EG;
                    }
                }
            } else if (backward) {
                e->mg -= sign * BACKWARD_MG;
                e->eg -= sign * BACKWARD_EG;
                if (!opposed) {
                    e->mg -= sign * WEAK_UNOPPOSED_MG;
                    e->eg -= sign * WEAK_UNOPPOSED_EG;
                }
            } else if (blocked && r >= 4) {
                /* A pawn on the fifth or sixth with an enemy pawn in front of
                 * it is going nowhere, and it is the one square its own pieces
                 * most wanted. */
                if (r == 4) { e->mg -= sign * BlockedPawn5Mg; e->eg -= sign * BlockedPawn5Eg; }
                else        { e->mg -= sign * BlockedPawn6Mg; e->eg -= sign * BlockedPawn6Eg; }
            }

            if (!support) {
                if (doubled) {
                    e->mg -= sign * DOUBLED_MG;
                    e->eg -= sign * DOUBLED_EG;
                }
                /* Levered twice and defended none: whichever way it is taken,
                 * the structure behind it comes apart. */
                if (lever && (lever & (lever - 1)))
                    e->eg -= sign * WeakLeverEg;
            }

            /* A candidate passer: not passed yet, but on the files ahead we
             * have at least as many pawns as they do, so a push wins the
             * race.  Nothing credited these at all before, which made the
             * evaluation blind to the whole plan of creating a passer. */
            if (!passed && !opposed && r >= 2) {
                U64 ahead = PassedPawnSpan[us][sq];
                int helpers = popcount(ourPawns & ahead & AdjacentFilesBB[f]);
                int stops   = popcount(theirPawns & ahead);
                if (helpers >= stops && stops > 0) {
                    e->mg += sign * CandidatePasserMg * (r - 1) / 4;
                    e->eg += sign * CandidatePasserEg * (r - 1) / 4;
                }
            }
        }
        e->attacksSpan[us] = span;
    }
    return e;
}

/* ---------------- main evaluation ---------------- */

typedef struct {
    U64 attackedBy[2][PIECE_TYPE_NB + 1];   /* index PIECE_TYPE_NB = all */
    U64 attackedBy2[2];
    U64 kingRing[2];
    U64 mobilityArea[2];
    int kingAttackersCount[2];
    int kingAttackersWeight[2];
    int kingAttacksCount[2];
    int mobilityMg[2], mobilityEg[2];
    /* own pawns with any piece directly in front of them; depends on all the
     * pieces, so it cannot be cached in the pawn-keyed PawnEntry */
    U64 blockedPawns[2];
    PawnEntry *pe;
} EvalInfo;

#define ALL PIECE_TYPE_NB

int eval_material_simple(const Position *pos, int c) {
    return popcount(pieces_cp(pos, c, KNIGHT)) * PieceValueMg[KNIGHT]
         + popcount(pieces_cp(pos, c, BISHOP)) * PieceValueMg[BISHOP]
         + popcount(pieces_cp(pos, c, ROOK))   * PieceValueMg[ROOK]
         + popcount(pieces_cp(pos, c, QUEEN))  * PieceValueMg[QUEEN];
}

static void init_eval_info(const Position *pos, EvalInfo *ei) {
    memset(ei, 0, sizeof(*ei));
    ei->pe = pawn_eval(pos);

    for (int c = WHITE; c <= BLACK; c++) {
        int ksq = lsb(pieces_cp(pos, c, KING));
        ei->attackedBy[c][PAWN] = ei->pe->attacks[c];
        ei->attackedBy[c][KING] = KingAttacksBB[ksq];
        ei->attackedBy[c][ALL]  = ei->attackedBy[c][PAWN] | ei->attackedBy[c][KING];
        ei->attackedBy2[c] = ei->attackedBy[c][PAWN] & ei->attackedBy[c][KING];

        /* king ring: the king's neighbourhood, shifted away from the edge */
        U64 ring = KingAttacksBB[ksq] | bb_of(ksq);
        if (rel_rank(c, ksq) == 0) ring |= pawn_push_bb(c, ring);
        if (file_of(ksq) == 0) ring |= ring << 1;
        else if (file_of(ksq) == 7) ring |= ring >> 1;
        ei->kingRing[c] = ring;

        /* mobility area: exclude own king/queens, own blocked pawns and
         * squares attacked by enemy pawns */
        U64 lowRanks = c == WHITE ? (RankBB[1] | RankBB[2]) : (RankBB[6] | RankBB[5]);
        U64 ourPawns = pieces_cp(pos, c, PAWN);
        ei->blockedPawns[c] = ourPawns & pawn_push_bb(!c, pos->occ);
        U64 b = ei->blockedPawns[c] | (ourPawns & lowRanks);
        ei->mobilityArea[c] = ~(b | pieces_cp(pos, c, KING) | pieces_cp(pos, c, QUEEN)
                                | ei->pe->attacks[!c]);
    }
}

static void eval_pieces(const Position *pos, EvalInfo *ei, int us, int *mg, int *eg) {
    int them = !us;
    int sign = us == WHITE ? 1 : -1;
    U64 ourPawns = pieces_cp(pos, us, PAWN);
    U64 theirPawns = pieces_cp(pos, them, PAWN);
    int theirKing = lsb(pieces_cp(pos, them, KING));
    U64 outpostRanks = us == WHITE ? (RankBB[3] | RankBB[4] | RankBB[5])
                                   : (RankBB[4] | RankBB[3] | RankBB[2]);
    int ourKing = lsb(pieces_cp(pos, us, KING));

    for (int pt = KNIGHT; pt <= QUEEN; pt++) {
        U64 b = pieces_cp(pos, us, pt);
        while (b) {
            int sq = pop_lsb(&b);

            /* sliders see through our own queens and (for bishops) batteries */
            U64 occ = pos->occ;
            if (pt == BISHOP) occ ^= pieces_cp(pos, us, QUEEN);
            else if (pt == ROOK) occ ^= pieces_cp(pos, us, QUEEN) | pieces_cp(pos, us, ROOK);
            U64 att = attacks_bb(pt, sq, occ);

            /* a pinned piece may only move along the pin ray */
            if (pos->blockersForKing[us] & bb_of(sq))
                att &= LineBB[lsb(pieces_cp(pos, us, KING))][sq];

            ei->attackedBy2[us] |= ei->attackedBy[us][ALL] & att;
            ei->attackedBy[us][pt] |= att;
            ei->attackedBy[us][ALL] |= att;

            if (att & ei->kingRing[them]) {
                ei->kingAttackersCount[us]++;
                ei->kingAttackersWeight[us] += KingAttackWeights[pt];
                ei->kingAttacksCount[us] += popcount(att & KingAttacksBB[theirKing]);
            }

            int mob = popcount(att & ei->mobilityArea[us]);
            int cap = MobilityCount[pt - KNIGHT] - 1;
            if (mob > cap) mob = cap;
            ei->mobilityMg[us] += MobilityMg[pt - KNIGHT][mob];
            ei->mobilityEg[us] += MobilityEg[pt - KNIGHT][mob];

            if (pt == KNIGHT || pt == BISHOP) {
                /* A minor far from its own king is a minor not defending it.
                 * The piece-square tables reward the centre but say nothing
                 * about where the king actually is. */
                int kd = SquareDistance[sq][ourKing];
                *mg -= sign * KingProtectorMg * kd / 8;
                *eg -= sign * KingProtectorEg * kd / 8;

                /* outposts */
                U64 outpost = outpostRanks & ~ei->pe->attacksSpan[them];
                if (outpost & bb_of(sq)) {
                    int v = (pt == KNIGHT) ? 56 : 30;
                    if (!(ei->pe->attacks[us] & bb_of(sq))) v /= 2;
                    *mg += sign * v;
                    *eg += sign * (v * 2 / 3);
                } else if (pt == KNIGHT && (att & outpost & ~pos->byColor[us])) {
                    *mg += sign * 20; *eg += sign * 13;
                }

                /* minor behind a pawn */
                if (pawn_push_bb(us, bb_of(sq)) & (ourPawns | theirPawns)) {
                    *mg += sign * 18; *eg += sign * 3;
                }

                if (pt == BISHOP) {
                    /* Bad bishop: own pawns on the bishop's colour.  It hurts
                     * more when those pawns cannot move and when they sit in
                     * the centre, where they block the long diagonals -- the
                     * old form counted every blocked pawn anywhere on the
                     * board, which is not what makes a bishop bad. */
                    U64 sameColor = (bb_of(sq) & DarkSquares) ? DarkSquares : ~DarkSquares;
                    int blockedCentre = popcount(ei->blockedPawns[us] & CenterFiles);
                    int unsupported = !(ei->pe->attacks[us] & bb_of(sq));
                    int v = popcount(ourPawns & sameColor) * (unsupported + blockedCentre);
                    *mg -= sign * 3 * v; *eg -= sign * 7 * v;

                    /* bishop on a long diagonal that sees the centre */
                    if (popcount(bishop_attacks(sq, theirPawns | ourPawns)
                                 & (bb_of(27) | bb_of(28) | bb_of(35) | bb_of(36))) > 1) {
                        *mg += sign * 45; *eg += sign * 0;
                    }

                    /* A bishop whose diagonal already crosses the king ring is
                     * part of an attack before it has moved; only the pawns
                     * block the line, because a piece in the way will not
                     * stay there. */
                    if (bishop_attacks(sq, pos->byType[PAWN]) & ei->kingRing[them])
                        *mg += sign * BishopOnKingRing;

                    /* A bishop that took the pawn on a7/h7 (a2/h2) and is now
                     * shut in by an enemy pawn on b6/g6 has cost more than
                     * the pawn was worth.  The evaluation could not see this
                     * at all before, and the search only finds out once the
                     * bishop is already lost. */
                    if (TrappedBishop && rel_rank(us, sq) == 6
                        && (file_of(sq) == 0 || file_of(sq) == 7)) {
                        int esc = sq + (us == WHITE ? -8 : 8)
                                     + (file_of(sq) == 0 ? 1 : -1);
                        if (theirPawns & bb_of(esc))
                            { *mg -= sign * TrappedBishop; *eg -= sign * TrappedBishop; }
                    }
                } else {
                    /* knight on the rim already handled by the PSQT */
                    if (SquareDistance[sq][theirKing] <= 3) { *mg += sign * 6; }
                }
            } else if (pt == ROOK) {
                int f = file_of(sq);
                /* the rook's file already runs into the king ring */
                if (FileBB[f] & ei->kingRing[them])
                    *mg += sign * RookOnKingRing;
                if (!(ourPawns & FileBB[f])) {
                    if (!(theirPawns & FileBB[f])) { *mg += sign * 47; *eg += sign * 26; }
                    else                           { *mg += sign * 19; *eg += sign * 7; }
                } else {
                    /* closed file: penalise a rook stuck behind its own king */
                    int ksq = lsb(pieces_cp(pos, us, KING));
                    if (mob <= 3 && rel_rank(us, ksq) == 0
                        && ((file_of(ksq) < 4) == (f < 4))) {
                        *mg -= sign * 55; *eg -= sign * 11;
                    }
                }
                /* A rook on the seventh is only worth anything if there is
                 * something there to attack: enemy pawns still on it, or an
                 * enemy king cut off on the eighth. */
                if (rel_rank(us, sq) == 6) {
                    U64 seventh = us == WHITE ? RankBB[6] : RankBB[1];
                    if (!RookOn7th
                        || (theirPawns & seventh)
                        || rel_rank(us, theirKing) == 7) {
                        *mg += sign * 12; *eg += sign * 28;
                    }
                }
                /* rook behind or in front of a passed pawn */
                if (ForwardFileBB[us][sq] & ei->pe->passed[us]) { *mg += sign * 10; *eg += sign * 30; }
                /* rooks defending each other along a clear line */
                if (ConnectedRooks) {
                    U64 others = pieces_cp(pos, us, ROOK) & ~bb_of(sq);
                    while (others) {
                        int o = pop_lsb(&others);
                        if (o > sq && (LineBB[sq][o] & bb_of(o))
                            && !(BetweenBB[sq][o] & pos->occ))
                            *mg += sign * ConnectedRooks;
                    }
                }
            } else { /* QUEEN */
                /* discovered-attack risk on the queen */
                U64 queenPinners = 0;
                U64 snipers = (PseudoAttacks[ROOK][sq] & pieces_cp(pos, them, ROOK))
                            | (PseudoAttacks[BISHOP][sq] & pieces_cp(pos, them, BISHOP));
                while (snipers) {
                    int sn = pop_lsb(&snipers);
                    U64 between = BetweenBB[sq][sn] & pos->occ;
                    if (between && !(between & (between - 1))) queenPinners |= bb_of(sn);
                }
                if (queenPinners) { *mg -= sign * 56; *eg -= sign * 15; }

                /* A queen past the middle of the board on a square no enemy
                 * pawn can ever attack is settled there, which in an endgame
                 * is most of the game. */
                if (rel_rank(us, sq) > 4 && !(ei->pe->attacksSpan[them] & bb_of(sq)))
                    *eg += sign * QueenInfiltration;
            }
        }
    }
}

static int eval_king_danger(const Position *pos, EvalInfo *ei, int us, int *mg, int *eg) {
    int them = !us;
    int ksq = lsb(pieces_cp(pos, us, KING));
    U64 ourPawns = pieces_cp(pos, us, PAWN);
    U64 theirPawns = pieces_cp(pos, them, PAWN);

    /* --- pawn shelter --- */
    int shelterMg = -5, shelterEg = 0;
    {
        int center = file_of(ksq);
        if (center < 1) center = 1;
        if (center > 6) center = 6;
        int best = -1000, bestEg = 0;
        /* evaluate the king's actual position and, if castling is still
         * available, the castled positions */
        int candidates[3], nc = 0;
        candidates[nc++] = ksq;
        if (us == WHITE) {
            if (pos->castling & WHITE_OO) candidates[nc++] = 6;
            if (pos->castling & WHITE_OOO) candidates[nc++] = 2;
        } else {
            if (pos->castling & BLACK_OO) candidates[nc++] = 62;
            if (pos->castling & BLACK_OOO) candidates[nc++] = 58;
        }
        for (int c = 0; c < nc; c++) {
            int kf = file_of(candidates[c]);
            if (kf < 1) kf = 1;
            if (kf > 6) kf = 6;
            U64 behindKing = ForwardRanksBB[them][rank_of(candidates[c])];
            int v = 5, vEg = 0;
            for (int f = kf - 1; f <= kf + 1; f++) {
                U64 myPawns = ourPawns & FileBB[f] & ~behindKing;
                U64 opPawns = theirPawns & FileBB[f] & ~behindKing;
                int d = f < 4 ? f : 7 - f;
                int myRank = myPawns ? rel_rank(us, us == WHITE ? lsb(myPawns) : msb(myPawns)) : 0;
                int opRank = opPawns ? rel_rank(us, us == WHITE ? lsb(opPawns) : msb(opPawns)) : 0;
                v += ShelterStrength[d][myRank];
                if (myRank && myRank == opRank - 1) {
                    v -= 82 * (opRank == 2);
                    vEg -= 82 * (opRank == 2);
                } else {
                    v -= UnblockedStorm[d][opRank] / 4;
                }
            }
            if (v > best) { best = v; bestEg = vEg; }
        }
        shelterMg = best;
        shelterEg = bestEg;
    }
    *mg += (us == WHITE ? 1 : -1) * shelterMg * ShelterWeight / 128;
    *eg += (us == WHITE ? 1 : -1) * shelterEg * ShelterWeight / 128;

    /* --- king danger --- */
    U64 weak = ei->attackedBy[them][ALL]
             & ~ei->attackedBy2[us]
             & (~ei->attackedBy[us][ALL] | ei->attackedBy[us][QUEEN] | ei->attackedBy[us][KING]);
    U64 safe = ~pos->byColor[them]
             & (~ei->attackedBy[us][ALL] | (weak & ei->attackedBy2[them]));

    U64 occ = pos->occ ^ pieces_cp(pos, us, QUEEN);
    U64 rookRay   = rook_attacks(ksq, occ);
    U64 bishopRay = bishop_attacks(ksq, occ);
    U64 rookChecks   = rookRay   & ei->attackedBy[them][ROOK]   & safe;
    U64 queenChecks  = (rookRay | bishopRay) & ei->attackedBy[them][QUEEN]  & safe
                     & ~rookChecks & ~ei->attackedBy[us][QUEEN];
    U64 bishopChecks = bishopRay & ei->attackedBy[them][BISHOP] & safe
                     & ~queenChecks;
    U64 knightChecks = KnightAttacksBB[ksq]     & ei->attackedBy[them][KNIGHT];

    /* A check the enemy cannot give safely still constrains the king: it has
     * to stay answerable, and one defender leaving turns it into a real one. */
    U64 unsafeChecks = ((rookRay   & ei->attackedBy[them][ROOK])
                      | (bishopRay & ei->attackedBy[them][BISHOP])
                      | knightChecks) & ~safe;

    int danger = ei->kingAttackersCount[them] * ei->kingAttackersWeight[them] / 2;
    if (rookChecks)   danger += 1080;
    if (queenChecks)  danger += 780;
    if (bishopChecks) danger += 635;
    if (knightChecks & safe) danger += 790;
    else if (knightChecks)   danger += 200;

    danger += 183 * popcount(ei->kingRing[us] & weak)
            +  69 * ei->kingAttacksCount[them]
            +  98 * popcount(pos->blockersForKing[us])
            + KdUnsafeCheck * popcount(unsafeChecks)
            - 873 * (popcount(pieces_cp(pos, them, QUEEN)) == 0)
            -   6 * shelterMg / 8
            -  30;

    /* A knight covering the king ring is the one defender that cannot be
     * driven off by a pawn or traded away at a distance. */
    if (ei->attackedBy[us][KNIGHT] & ei->kingRing[us])
        danger -= KdKnightDefender;

    /* Whoever has the freer position is the one who can bring pieces to the
     * attack.  Stockfish folds the mobility difference straight into king
     * danger and this evaluation counted mobility only as a score of its own,
     * so a side that was being slowly squeezed did not look like one in any
     * danger. */
    danger += (ei->mobilityMg[them] - ei->mobilityMg[us]) * KdMobWeight / 128;

    /* attacks on the king's flank */
    U64 flank = KingFlankBB[file_of(ksq)];
    U64 camp = us == WHITE ? (RankBB[0] | RankBB[1] | RankBB[2] | RankBB[3] | RankBB[4])
                           : (RankBB[7] | RankBB[6] | RankBB[5] | RankBB[4] | RankBB[3]);
    U64 flankAttack = ei->attackedBy[them][ALL] & flank & camp;
    U64 flankDouble = flankAttack & ei->attackedBy2[them];
    int fa = popcount(flankAttack) + popcount(flankDouble);
    danger += 3 * fa * fa / 8;

    if (danger > 100) {
        int sign = us == WHITE ? 1 : -1;
        *mg -= sign * (danger * danger / 4096) * KingDangerWeight / 128;
        *eg -= sign * (danger / 16) * KingDangerWeight / 128;
    }

    /* A king standing on a flank with no pawn of either colour has no shelter
     * to be had there and no way to build one: in the endgame that is where
     * the game is decided, which is why the penalty is mostly an endgame one. */
    if (!(pos->byType[PAWN] & flank)) {
        int sign = us == WHITE ? 1 : -1;
        *mg -= sign * PawnlessFlankMg;
        *eg -= sign * PawnlessFlankEg;
    }

    /* A king on a file with no pawn of its own is exposed to the heavy
     * pieces whether or not they have arrived yet. */
    if (KingOpenFile && rel_rank(us, ksq) <= 2) {
        int f = file_of(ksq);
        if (!(ourPawns & FileBB[f])) {
            int sign = us == WHITE ? 1 : -1;
            int v = (theirPawns & FileBB[f]) ? KingOpenFile / 2 : KingOpenFile;
            if (pieces_cp(pos, them, ROOK) | pieces_cp(pos, them, QUEEN))
                *mg -= sign * v;
        }
    }
    return danger;
}

static void eval_passed(const Position *pos, EvalInfo *ei, int us, int *mg, int *eg) {
    int them = !us;
    int sign = us == WHITE ? 1 : -1;
    int up = us == WHITE ? 8 : -8;
    U64 b = ei->pe->passed[us];

    while (b) {
        int sq = pop_lsb(&b);
        int r = rel_rank(us, sq);
        int mgV = PassedRankMg[r], egV = PassedRankEg[r];

        if (r > 2) {
            int w = 5 * r - 13;
            int blockSq = sq + up;
            int ourK = lsb(pieces_cp(pos, us, KING));
            int theirK = lsb(pieces_cp(pos, them, KING));

            egV += (SquareDistance[theirK][blockSq] * 19 / 4
                  - SquareDistance[ourK][blockSq] * 2) * w;
            if (r != 6)
                egV -= SquareDistance[ourK][blockSq + up] * w;

            if (pos->board[blockSq] == NO_PIECE) {
                U64 squaresToQueen = ForwardFileBB[us][sq];
                U64 unsafeSquares = PassedPawnSpan[us][sq];
                U64 defended = squaresToQueen & ei->attackedBy[us][ALL];
                if (!(pos->byColor[them] & squaresToQueen))
                    unsafeSquares &= ei->attackedBy[them][ALL] | pos->byColor[them];
                int k = !unsafeSquares ? 36
                      : !(unsafeSquares & ~ei->attackedBy[us][ALL]) ? 30
                      : !(unsafeSquares & bb_of(blockSq)) ? 17 : 0;
                if (defended == squaresToQueen) k += 5;
                /* A clear path to promotion is an endgame asset; in the
                 * middlegame the enemy still has the pieces to deal with it,
                 * and giving the same bonus to both was most of why the
                 * engine would hand over a knight for two runners. */
                mgV += k * w * PasserBlockMgWeight / 128;
                egV += k * w * PasserBlockWeight / 128;
            }
        }
        mgV += PassedFileMg[file_of(sq)];
        egV += PassedFileEg[file_of(sq)];
        *mg += sign * mgV;
        *eg += sign * egV;
    }
}

static void eval_threats(const Position *pos, EvalInfo *ei, int us, int *mg, int *eg) {
    int them = !us;
    int sign = us == WHITE ? 1 : -1;
    U64 nonPawnEnemies = pos->byColor[them] & ~pieces_cp(pos, them, PAWN);
    U64 stronglyProtected = ei->attackedBy[them][PAWN]
                          | (ei->attackedBy2[them] & ~ei->attackedBy2[us]);
    U64 defended = nonPawnEnemies & stronglyProtected;
    U64 weak = pos->byColor[them] & ~stronglyProtected & ei->attackedBy[us][ALL];

    if (defended | weak) {
        U64 b = (defended | weak) & (ei->attackedBy[us][KNIGHT] | ei->attackedBy[us][BISHOP]);
        while (b) {
            int sq = pop_lsb(&b);
            int pt = piece_type(pos->board[sq]);
            static const int MinorThreatMg[6] = { 5, 57, 77, 88, 79, 0 };
            static const int MinorThreatEg[6] = { 32, 41, 56, 119, 161, 0 };
            *mg += sign * MinorThreatMg[pt];
            *eg += sign * MinorThreatEg[pt];
        }
        b = weak & ei->attackedBy[us][ROOK];
        while (b) {
            int sq = pop_lsb(&b);
            int pt = piece_type(pos->board[sq]);
            static const int RookThreatMg[6] = { 3, 37, 42, 0, 58, 0 };
            static const int RookThreatEg[6] = { 46, 68, 60, 38, 41, 0 };
            *mg += sign * RookThreatMg[pt];
            *eg += sign * RookThreatEg[pt];
        }
        if (weak & ei->attackedBy[us][KING]) { *mg += sign * 24; *eg += sign * 89; }

        b = weak & ~ei->attackedBy[them][ALL];
        *mg += sign * 69 * popcount(b);
        *eg += sign * 36 * popcount(b);
    }

    /* safe pawn threats */
    U64 safe = ~ei->attackedBy[them][ALL] | ei->attackedBy[us][ALL];
    U64 b = pieces_cp(pos, us, PAWN) & safe;
    b = pawn_attacks_bb(us, b) & nonPawnEnemies;
    *mg += sign * 173 * popcount(b);
    *eg += sign * 94 * popcount(b);

    /* threats from a pawn push */
    U64 ourPawns = pieces_cp(pos, us, PAWN);
    U64 push = pawn_push_bb(us, ourPawns) & ~pos->occ;
    push |= pawn_push_bb(us, push & (us == WHITE ? RankBB[2] : RankBB[5])) & ~pos->occ;
    push &= ~ei->attackedBy[them][PAWN] & safe;
    b = pawn_attacks_bb(us, push) & nonPawnEnemies;
    *mg += sign * 48 * popcount(b);
    *eg += sign * 39 * popcount(b);

    /* restricted squares: enemy squares we attack that they also defend */
    U64 restricted = ei->attackedBy[them][ALL] & ~stronglyProtected
                   & ei->attackedBy[us][ALL];
    *mg += sign * 7 * popcount(restricted);
    *eg += sign * 7 * popcount(restricted);

    /*
     * Threats against the queen on the move after this one.  A square from
     * which a knight would fork or a slider would skewer the queen is worth
     * something even while it is empty, because the queen has to keep
     * answering for it.  The evaluation had nothing of this at all, which is
     * most of why the engine would walk its queen into a position where every
     * reasonable move loses a tempo.
     */
    if (popcount(pieces_cp(pos, them, QUEEN)) == 1) {
        int qsq = lsb(pieces_cp(pos, them, QUEEN));
        U64 safe = ei->mobilityArea[us] & ~stronglyProtected;

        U64 b2 = ei->attackedBy[us][KNIGHT] & KnightAttacksBB[qsq] & safe;
        *mg += sign * KnightOnQueenMg * popcount(b2);
        *eg += sign * KnightOnQueenEg * popcount(b2);

        b2 = ((ei->attackedBy[us][BISHOP] & bishop_attacks(qsq, pos->occ))
            | (ei->attackedBy[us][ROOK] & rook_attacks(qsq, pos->occ)))
           & safe & ei->attackedBy2[us];
        *mg += sign * SliderOnQueenMg * popcount(b2);
        *eg += sign * SliderOnQueenEg * popcount(b2);
    }
}

static void eval_space(const Position *pos, EvalInfo *ei, int us, int *mg) {
    int them = !us;
    if (eval_material_simple(pos, WHITE) + eval_material_simple(pos, BLACK)
        < SpaceThreshold) return;
    U64 ourPawns = pieces_cp(pos, us, PAWN);
    U64 spaceMask = CenterFiles
        & (us == WHITE ? (RankBB[1] | RankBB[2] | RankBB[3]) : (RankBB[6] | RankBB[5] | RankBB[4]));
    U64 safe = spaceMask & ~ourPawns & ~ei->attackedBy[them][PAWN];
    U64 behind = ourPawns;
    behind |= pawn_push_bb(them, behind);
    behind |= pawn_push_bb(them, pawn_push_bb(them, behind));
    int bonus = popcount(safe) + popcount(behind & safe & ~ei->attackedBy[them][ALL]);
    /* Space is worth more the more pieces there are to use it and the more
     * the pawns are locked; the old form collapsed the blocked-pawn count to
     * a single bit and threw most of that away. */
    int blocked = popcount(ei->blockedPawns[us]);
    int weight = popcount(pos->byColor[us]) - 3 + (blocked < 9 ? blocked : 9);
    *mg += (us == WHITE ? 1 : -1) * bonus * weight * weight / 16;
}

/*
 * Endgame knowledge that a scale factor cannot express: these are positions
 * where the material count says one thing and the result is another, and no
 * amount of tapering gets there.  Returns 1 and sets *sf when it recognises
 * the position.
 */
static int known_endgame(const Position *pos, int strongSide, int *sf) {
    int weakSide = !strongSide;
    int npmS = eval_material_simple(pos, strongSide);
    int npmW = eval_material_simple(pos, weakSide);
    U64 ourPawns = pieces_cp(pos, strongSide, PAWN);
    int weakKsq = lsb(pieces_cp(pos, weakSide, KING));

    /* Only the bare-defence cases are decided here; once the weak side has a
     * pawn of its own the position is a real game again. */
    if (pieces_cp(pos, weakSide, PAWN)) return 0;

    /* A bishop, pawns on one rook file only, and a bishop of the wrong
     * colour for the promotion square: the defending king walks into the
     * corner and cannot be moved.  A dead draw however many pawns there are. */
    if (npmS == PieceValueMg[BISHOP] && npmW == 0 && ourPawns
        && popcount(pieces_cp(pos, strongSide, BISHOP)) == 1) {
        U64 edgeFile = (ourPawns & FileBB[0]) ? FileBB[0]
                     : (ourPawns & FileBB[7]) ? FileBB[7] : 0;
        if (edgeFile && !(ourPawns & ~edgeFile)) {
            int f = (ourPawns & FileBB[0]) ? 0 : 7;
            int promo = make_sq(f, strongSide == WHITE ? 7 : 0);
            int bishopDark = (pieces_cp(pos, strongSide, BISHOP) & DarkSquares) != 0;
            int promoDark = (bb_of(promo) & DarkSquares) != 0;
            if (bishopDark != promoDark && SquareDistance[weakKsq][promo] <= 1) {
                *sf = 0;
                return 1;
            }
        }
    }

    /* A knight and a rook pawn with the defending king on the queening
     * square is the other textbook draw the material count misses. */
    if (npmS == PieceValueMg[KNIGHT] && npmW == 0
        && popcount(ourPawns) == 1
        && (ourPawns & (FileBB[0] | FileBB[7]))) {
        int psq = lsb(ourPawns);
        int promo = make_sq(file_of(psq), strongSide == WHITE ? 7 : 0);
        if (SquareDistance[weakKsq][promo] <= 1) { *sf = 0; return 1; }
    }
    return 0;
}

/* Endgame scale factor out of 64. */
static int scale_factor(const Position *pos, int strongSide, int eg) {
    int weakSide = !strongSide;
    int sf = 64;
    int npmS = eval_material_simple(pos, strongSide);
    int npmW = eval_material_simple(pos, weakSide);
    int pawnsS = popcount(pieces_cp(pos, strongSide, PAWN));

    if (known_endgame(pos, strongSide, &sf)) return sf;

    int oppositeBishops =
        popcount(pieces_cp(pos, WHITE, BISHOP)) == 1
        && popcount(pieces_cp(pos, BLACK, BISHOP)) == 1
        && ((pieces_cp(pos, WHITE, BISHOP) & DarkSquares) != 0)
           != ((pieces_cp(pos, BLACK, BISHOP) & DarkSquares) != 0);

    /* opposite coloured bishops and nothing else */
    if (npmS == PieceValueMg[BISHOP] && npmW == PieceValueMg[BISHOP]
        && oppositeBishops) {
        sf = pawnsS <= 1 ? 22 : 22 + 3 * popcount(pos->byType[PAWN]);
    } else if (ScaleEndgames && oppositeBishops) {
        /* Opposite bishops with the heavy pieces still on.  Much less drawish
         * than the pure ending -- the rooks and queens give the attacker a
         * second front the bishop cannot cover -- but still drawish, and the
         * old code scaled it at 64 because it only recognised the bare case.
         * Stockfish 11's form: 22 plus a few per piece left on the board. */
        sf = 22 + ScaleOppBishopPc * popcount(pos->byColor[strongSide]);
        if (sf > 64) sf = 64;
    } else if (!pawnsS && npmS - npmW <= PieceValueMg[BISHOP]) {
        sf = npmS < PieceValueMg[ROOK] ? 0
           : (npmW <= PieceValueMg[BISHOP] ? 4 : 14);
    } else if (pawnsS == 1 && npmS - npmW <= PieceValueMg[BISHOP]) {
        sf = 24;
    } else if (pawnsS <= 2 && !popcount(pieces_cp(pos, weakSide, PAWN))) {
        sf = 37 + 7 * pawnsS;
    }

    U64 strongPawns = pieces_cp(pos, strongSide, PAWN);

    if (ScaleEndgames) {
        int absEg = eg < 0 ? -eg : eg;
        int weakKsq = lsb(pieces_cp(pos, weakSide, KING));

        /* A defending king that stands in front of everything the strong side
         * has left.  The old form of this only fired when the defender had no
         * pawns at all, which is the rarer half of it; what actually decides
         * these endings is whether the king is past the pawns, not whether it
         * has any of its own.  Stockfish 11's condition. */
        if (sf == 64 && absEg <= PieceValueEg[BISHOP] && pawnsS <= 2
            && !(strongPawns & PassedPawnSpan[weakSide][weakKsq]))
            sf = ScaleKingInFront + 7 * pawnsS;

        /* Rook endings a pawn up are the most common drawn-but-better ending
         * there is, and nothing here knew it: one rook each, no other pieces,
         * and a single extra pawn converts far less than the material says. */
        if (npmS == PieceValueMg[ROOK] && npmW == PieceValueMg[ROOK]
            && popcount(strongPawns) - popcount(pieces_cp(pos, weakSide, PAWN)) == 1
            && popcount(pos->byType[PAWN]) <= 6
            && sf > ScaleRookEnding)
            sf = ScaleRookEnding;
    }

    /* Pawns on one flank only are much harder to convert: the defender has a
     * single front to hold and the attacking king has nowhere to break in. */
    if (sf > 48 && strongPawns && npmS - npmW <= PieceValueMg[ROOK]
        && (!(strongPawns & QueenSideBB) || !(strongPawns & KingSideBB)))
        sf = sf * 7 / 8;

    return sf;
}

/*
 * Material imbalance.  The piece-square tables price every piece as if the
 * rest of the board did not exist, which is where "a knight for two pawns"
 * came from: knights need pawns to stand on and rooks need them gone, a
 * second rook adds less than the first, and two minors are worth more against
 * a rook than their tabulated values say.
 */
static void eval_imbalance(const Position *pos, int *mg, int *eg) {
    int minors[2], heavy[2];

    for (int c = WHITE; c <= BLACK; c++) {
        int sign = c == WHITE ? 1 : -1;
        int pawns   = popcount(pieces_cp(pos, c, PAWN));
        int knights = popcount(pieces_cp(pos, c, KNIGHT));
        int bishops = popcount(pieces_cp(pos, c, BISHOP));
        int rooks   = popcount(pieces_cp(pos, c, ROOK));
        int queens  = popcount(pieces_cp(pos, c, QUEEN));
        minors[c] = knights + bishops;
        heavy[c]  = rooks + 2 * queens;

        /* the pair is worth more the more open the position gets */
        if (bishops >= 2) {
            int extra = BishopPairPawns * (8 - pawns);
            *mg += sign * (BishopPairMg + extra);
            *eg += sign * (BishopPairEg + extra);
        }

        /* a knight is a short-range piece: it wants pawns to anchor to */
        int pawnDelta = pawns - 5;
        *mg += sign * ImbKnightPawns * knights * pawnDelta / 4;
        *eg += sign * ImbKnightPawns * knights * pawnDelta / 4;
        /* a rook wants the files open */
        *mg -= sign * ImbRookPawns * rooks * pawnDelta / 4;
        *eg -= sign * ImbRookPawns * rooks * pawnDelta / 4;

        /* two rooks duplicate each other */
        if (rooks >= 2) { *mg -= sign * ImbRookPair; *eg -= sign * ImbRookPair; }
        /* a queen covers the squares a knight cannot and the other way round */
        if (queens && knights) { *mg += sign * ImbQueenKnight; *eg += sign * ImbQueenKnight; }
    }

    /* Two minors against a rook (with or without a pawn alongside): the
     * minors coordinate and the rook has nothing to attack yet. */
    int minorDiff = minors[WHITE] - minors[BLACK];
    int heavyDiff = heavy[WHITE] - heavy[BLACK];
    if (minorDiff >= 2 && heavyDiff <= -1) {
        *mg += ImbMinorVsRook;
        *eg += ImbMinorVsRook / 2;
    } else if (minorDiff <= -2 && heavyDiff >= 1) {
        *mg -= ImbMinorVsRook;
        *eg -= ImbMinorVsRook / 2;
    }
}

/* Mate guidance for a lone king; returns 1 if it produced a score. */
static int eval_lone_king(const Position *pos, int *out) {
    int strong, weak;
    int npmW = eval_material_simple(pos, WHITE);
    int npmB = eval_material_simple(pos, BLACK);
    int pawnsW = popcount(pieces_cp(pos, WHITE, PAWN));
    int pawnsB = popcount(pieces_cp(pos, BLACK, PAWN));

    if (!pawnsB && popcount(pos->byColor[BLACK]) == 1 && npmW >= PieceValueMg[ROOK]) {
        strong = WHITE; weak = BLACK;
    } else if (!pawnsW && popcount(pos->byColor[WHITE]) == 1 && npmB >= PieceValueMg[ROOK]) {
        strong = BLACK; weak = WHITE;
    } else if (!pawnsW && !pawnsB) {
        /* KBN vs K */
        if (popcount(pos->byColor[WHITE]) == 3 && popcount(pos->byColor[BLACK]) == 1
            && popcount(pieces_cp(pos, WHITE, KNIGHT)) == 1
            && popcount(pieces_cp(pos, WHITE, BISHOP)) == 1) { strong = WHITE; weak = BLACK; }
        else if (popcount(pos->byColor[BLACK]) == 3 && popcount(pos->byColor[WHITE]) == 1
            && popcount(pieces_cp(pos, BLACK, KNIGHT)) == 1
            && popcount(pieces_cp(pos, BLACK, BISHOP)) == 1) { strong = BLACK; weak = WHITE; }
        else return 0;
    } else {
        /* KPK is decided exactly by the bitbase */
        if (npmW == 0 && npmB == 0 && pawnsW + pawnsB == 1) {
            int sSide = pawnsW ? WHITE : BLACK;
            int psq = lsb(pieces_cp(pos, sSide, PAWN));
            int ksq = lsb(pieces_cp(pos, sSide, KING));
            int wksq = lsb(pieces_cp(pos, !sSide, KING));
            int v;
            if (kpk_probe(sSide, ksq, psq, wksq, pos->side))
                v = 500 + 60 * rel_rank(sSide, psq) - 8 * SquareDistance[ksq][psq];
            else
                v = VALUE_DRAW;
            *out = (v == VALUE_DRAW) ? VALUE_DRAW : (pos->side == sSide ? v : -v);
            return 1;
        }
        return 0;
    }

    int wk = lsb(pieces_cp(pos, weak, KING));
    int sk = lsb(pieces_cp(pos, strong, KING));
    int npm = strong == WHITE ? npmW : npmB;

    /* drive the weak king to the edge, keep the kings close */
    static const int PushToEdge[64] = {
        100, 90, 80, 70, 70, 80, 90, 100,
         90, 70, 60, 50, 50, 60, 70,  90,
         80, 60, 40, 30, 30, 40, 60,  80,
         70, 50, 30, 20, 20, 30, 50,  70,
         70, 50, 30, 20, 20, 30, 50,  70,
         80, 60, 40, 30, 30, 40, 60,  80,
         90, 70, 60, 50, 50, 60, 70,  90,
        100, 90, 80, 70, 70, 80, 90, 100
    };
    int v = npm + 300 + PushToEdge[wk] * 3 + (14 - SquareDistance[sk][wk]) * 32;

    /* KBN needs the king driven to a corner matching the bishop's colour */
    if (npm == PieceValueMg[BISHOP] + PieceValueMg[KNIGHT]) {
        U64 bishops = pieces_cp(pos, strong, BISHOP);
        int dark = (bishops & DarkSquares) != 0;
        static const int CornerDark[4]  = { 0, 7, 56, 63 };
        int best = 99;
        for (int i = 0; i < 4; i++) {
            int corner = CornerDark[i];
            int isDark = ((bb_of(corner) & DarkSquares) != 0);
            if (isDark == dark) {
                int d = SquareDistance[wk][corner];
                if (d < best) best = d;
            }
        }
        v = npm + 300 + (14 - SquareDistance[sk][wk]) * 32 + (7 - best) * 90;
    }

    *out = pos->side == strong ? v : -v;
    return 1;
}

/*
 * The window is only used by the lazy exit below; pass the full window to
 * forbid it.
 */
int eval_hce_window(const Position *pos, int alpha, int beta) {
    EvalInfo ei;
    init_eval_info(pos, &ei);

    /*
     * Material and position are accumulated apart.  The piece values and the
     * piece-square tables come from one source and everything else from
     * another, whose pawn was half again as heavy in the middlegame and more
     * than twice as heavy in the endgame; mixing them at face value is what
     * made a pawn look like a fair price for a little mobility.  PosWeight*
     * puts the two back on the same scale and gives the tuner one handle on
     * the trade-off that matters most.
     */
    int matMg = 0, matEg = 0;
    int posMg = 0, posEg = 0;
    int phase = 0;
    static const int PhaseWeight[6] = { 0, 1, 1, 2, 4, 0 };

    for (int sq = 0; sq < 64; sq++) {
        int pc = pos->board[sq];
        if (pc == NO_PIECE) continue;
        int sign = piece_color(pc) == WHITE ? 1 : -1;
        int pt = piece_type(pc);
        /* The piece value and its square bonus are priced separately so the
         * tuner can move what a piece is worth without disturbing where it
         * wants to stand. */
        matMg += sign * (PieceValueMg[pt] * MatMul[0][pt] / 1000
                         + PsqMg[pc][sq] - PieceValueMg[pt]);
        matEg += sign * (PieceValueEg[pt] * MatMul[1][pt] / 1000
                         + PsqEg[pc][sq] - PieceValueEg[pt]);
        phase += PhaseWeight[pt];
    }
    if (phase > 24) phase = 24;

    /* imbalance is a statement about material, so it is not rescaled */
    eval_imbalance(pos, &matMg, &matEg);

    /*
     * Lazy exit.  Material and the piece-square tables are the one pass over
     * the board that has just happened; mobility, king safety, threats,
     * passers and space together cost several times as much.  If the cheap
     * half already puts the score outside the window the search asked about
     * by more than the rest could possibly move it, stop here.
     *
     * This is an approximation with a visible cost: the value is cached in
     * the transposition table like any other, so a later probe at a different
     * window gets the estimate rather than the real thing.  LazyMargin = 0
     * turns it off.
     */
    if (LazyMargin) {
        int lazy = taper(pos, matMg, matEg, phase);
        if (lazy - LazyMargin >= beta || lazy + LazyMargin <= alpha)
            return lazy;
    }

    /* Each block is accumulated on its own and weighted on its own. */
    int gMg, gEg;

    posMg += ei.pe->mg * PawnWeightMg / 128;
    posEg += ei.pe->eg * PawnWeightEg / 128;

    gMg = gEg = 0;
    eval_pieces(pos, &ei, WHITE, &gMg, &gEg);
    eval_pieces(pos, &ei, BLACK, &gMg, &gEg);
    posMg += gMg * PieceWeightMg / 128;
    posEg += gEg * PieceWeightEg / 128;

    posMg += (ei.mobilityMg[WHITE] - ei.mobilityMg[BLACK]) * MobWeightMg / 128;
    posEg += (ei.mobilityEg[WHITE] - ei.mobilityEg[BLACK]) * MobWeightEg / 128;

    /* king safety carries its own weight inside eval_king_danger */
    eval_king_danger(pos, &ei, WHITE, &posMg, &posEg);
    eval_king_danger(pos, &ei, BLACK, &posMg, &posEg);

    gMg = gEg = 0;
    eval_passed(pos, &ei, WHITE, &gMg, &gEg);
    eval_passed(pos, &ei, BLACK, &gMg, &gEg);
    posMg += gMg * PassedWeightMg / 128;
    posEg += gEg * PassedWeightEg / 128;

    gMg = gEg = 0;
    eval_threats(pos, &ei, WHITE, &gMg, &gEg);
    eval_threats(pos, &ei, BLACK, &gMg, &gEg);
    posMg += gMg * ThreatWeightMg / 128;
    posEg += gEg * ThreatWeightEg / 128;

    gMg = 0;
    eval_space(pos, &ei, WHITE, &gMg);
    eval_space(pos, &ei, BLACK, &gMg);
    posMg += gMg * SpaceWeight / 128;

    int mg = matMg + posMg * PosWeightMg / 128;
    int eg = matEg + posEg * PosWeightEg / 128;

    /*
     * Initiative.  An endgame with pawns on both wings and kings that can
     * infiltrate is worth more to whoever is already ahead.  The term used to
     * be unbounded and unscaled, so in any position with a full set of pawns
     * it added about 1.2 pawns to the endgame score the moment that score was
     * off zero by a single centipawn -- which is exactly why the evaluation
     * read either 0.0 or 1.2 and very little in between.  It is now converted
     * to this evaluation's units and capped.
     */
    {
        int wk = lsb(pieces_cp(pos, WHITE, KING)), bk = lsb(pieces_cp(pos, BLACK, KING));
        int outflanking = (file_of(wk) - file_of(bk)) + (rank_of(wk) - rank_of(bk));
        if (outflanking < 0) outflanking = -outflanking;
        int pawnCount = popcount(pos->byType[PAWN]);
        int passedCount = popcount(ei.pe->passed[WHITE] | ei.pe->passed[BLACK]);
        int bothFlanks = (pos->byType[PAWN] & QueenSideBB) && (pos->byType[PAWN] & KingSideBB);
        int infiltration = rank_of(wk) > 3 || rank_of(bk) < 4;
        /* no passers, no space to outflank and everything on one wing: there
         * is nothing here for either side to play for */
        int almostUnwinnable = !passedCount && outflanking == 0 && !bothFlanks;

        int complexity = InitPawnWeight * pawnCount
                       + InitPassedWeight * passedCount
                       + InitOutflank * outflanking
                       + InitInfiltration * infiltration
                       + InitBothFlanks * bothFlanks
                       - 100 * almostUnwinnable
                       - InitConst;
        complexity = complexity * 100 / InitiativeDiv;
        if (complexity > InitiativeCap) complexity = InitiativeCap;

        int v = eg > 0 ? 1 : (eg < 0 ? -1 : 0);
        int absEg = eg < 0 ? -eg : eg;
        eg += v * (complexity > -absEg ? complexity : -absEg);
    }

    return taper(pos, mg, eg, phase);
}

int eval_hce(const Position *pos) {
    return eval_hce_window(pos, -VALUE_INFINITE, VALUE_INFINITE);
}

/*
 * The fifty-move damping, for the network.
 *
 * taper() has always applied this to the hand-crafted score: a position whose
 * halfmove clock is nearly out is worth less than the pieces say, because the
 * draw is closer than the win.  The network never saw it -- `nnue` reported
 * the same number at halfmove 98 as at 0, and `blend` damped only the
 * hand-crafted share -- so the search had no way to notice a game drifting
 * into the fifty-move rule.  NnueFiftyDamp = 0 restores that.
 */
static inline int nnue_fifty(const Position *pos, int v) {
    return NnueFiftyDamp ? v * (200 - pos->halfmove) / 200 : v;
}

int eval_position_window(Position *pos, int alpha, int beta) {
    int v;
    if (pos_insufficient_material(pos)) return VALUE_DRAW;
    if (eval_lone_king(pos, &v)) {
        v = v * EvalScale / 100;
        if (v > VALUE_TB_WIN - 1) v = VALUE_TB_WIN - 1;
        if (v < -(VALUE_TB_WIN - 1)) v = -(VALUE_TB_WIN - 1);
        return v;
    }

    if (g_evalMode == EVAL_NNUE && nnue_available()) {
        v = nnue_fifty(pos, nnue_evaluate(pos));
    } else if (g_evalMode == EVAL_BLEND && nnue_available()) {
        /* A mixture of the two, weighted by BlendNnueWeight out of 128.  It
         * used to be fixed at 1:3 towards the hand-crafted evaluation, which
         * was right when the network was a 769-256-1 net that lost to it
         * 21-3; the perspective network is now the better of the two and the
         * old weighting had it backwards. */
        int h = eval_hce(pos);
        int n = nnue_fifty(pos, nnue_evaluate(pos));
        v = (n * BlendNnueWeight + h * (128 - BlendNnueWeight)) / 128;
    } else {
        v = eval_hce_window(pos, alpha, beta);
    }

    if (v > VALUE_TB_WIN - 1) v = VALUE_TB_WIN - 1;
    if (v < -(VALUE_TB_WIN - 1)) v = -(VALUE_TB_WIN - 1);
    return v;
}

int eval_position(Position *pos) {
    return eval_position_window(pos, -VALUE_INFINITE, VALUE_INFINITE);
}

/*
 * ce.h -- Chess Engine core declarations.
 *
 * A standalone bitboard chess engine: magic-bitboard move generation,
 * a PVS/negamax search with modern pruning, and a choice of a hand-crafted
 * evaluation or the project's own NNUE.
 *
 * Square numbering matches python-chess: a1 = 0, h1 = 7, a8 = 56, h8 = 63.
 */
#ifndef CE_H
#define CE_H

#include <stdint.h>
#include <stddef.h>
#include <string.h>

#include "tune.h"

#ifdef _WIN32
#define CE_EXPORT __declspec(dllexport)
#else
#define CE_EXPORT __attribute__((visibility("default")))
#endif

typedef uint64_t U64;
typedef uint16_t Move;

#define MAX_PLY    128
#define MAX_MOVES  256
#define MAX_HIST   2048          /* game history + search depth */

/* ------------------------------------------------------------------ */
/* Basic enums                                                         */
/* ------------------------------------------------------------------ */

enum { WHITE, BLACK, COLOR_NB };

enum { PAWN, KNIGHT, BISHOP, ROOK, QUEEN, KING, PIECE_TYPE_NB };

enum {
    W_PAWN, W_KNIGHT, W_BISHOP, W_ROOK, W_QUEEN, W_KING,
    B_PAWN, B_KNIGHT, B_BISHOP, B_ROOK, B_QUEEN, B_KING,
    NO_PIECE, PIECE_NB
};

enum { SQ_NONE = 64 };

/* castling right bits */
enum { WHITE_OO = 1, WHITE_OOO = 2, BLACK_OO = 4, BLACK_OOO = 8 };

/* move types (bits 14-15 of a Move) */
enum { MT_NORMAL = 0, MT_PROMO = 1, MT_EP = 2, MT_CASTLE = 3 };

/* score bounds */
enum {
    VALUE_ZERO       = 0,
    VALUE_DRAW       = 0,
    VALUE_MATE       = 32000,
    VALUE_INFINITE   = 32001,
    VALUE_NONE       = 32002,
    VALUE_MATE_IN_MAX_PLY  =  VALUE_MATE - MAX_PLY,
    VALUE_MATED_IN_MAX_PLY = -VALUE_MATE + MAX_PLY,
    VALUE_TB_WIN     = VALUE_MATE_IN_MAX_PLY - 1
};

/* TT bound flags */
enum { BOUND_NONE = 0, BOUND_UPPER = 1, BOUND_LOWER = 2, BOUND_EXACT = 3 };

/* ------------------------------------------------------------------ */
/* Small helpers                                                       */
/* ------------------------------------------------------------------ */

#define file_of(s)   ((s) & 7)
#define rank_of(s)   ((s) >> 3)
#define make_sq(f,r) (((r) << 3) | (f))
#define flip_sq(s)   ((s) ^ 56)
/* square relative to a colour's own first rank */
#define rel_sq(c,s)  ((c) == WHITE ? (s) : flip_sq(s))
#define rel_rank(c,s) ((c) == WHITE ? rank_of(s) : 7 - rank_of(s))

#define make_piece(c,pt) ((c) * 6 + (pt))
#define piece_color(pc)  ((pc) >= B_PAWN)
#define piece_type(pc)   ((pc) % 6)

static inline Move make_move(int from, int to) { return (Move)(from | (to << 6)); }
static inline Move make_move_t(int from, int to, int type) {
    return (Move)(from | (to << 6) | (type << 14));
}
static inline Move make_promo(int from, int to, int pt) {
    return (Move)(from | (to << 6) | ((pt - KNIGHT) << 12) | (MT_PROMO << 14));
}
#define move_from(m)  ((m) & 63)
#define move_to(m)    (((m) >> 6) & 63)
#define move_type(m)  (((m) >> 14) & 3)
#define move_promo(m) ((((m) >> 12) & 3) + KNIGHT)
#define MOVE_NONE ((Move)0)
#define MOVE_NULL ((Move)65)   /* a1a2: never a legal encoding we generate */

#define bb_of(s) (1ULL << (s))

static inline int popcount(U64 b) { return __builtin_popcountll(b); }
static inline int lsb(U64 b)      { return __builtin_ctzll(b); }
static inline int msb(U64 b)      { return 63 ^ __builtin_clzll(b); }
static inline int pop_lsb(U64 *b) { int s = lsb(*b); *b &= *b - 1; return s; }

#define FILE_A_BB 0x0101010101010101ULL
#define RANK_1_BB 0x00000000000000FFULL

extern const U64 FileBB[8];
extern const U64 RankBB[8];

/* ------------------------------------------------------------------ */
/* Attack tables (bitboard.c)                                          */
/* ------------------------------------------------------------------ */

extern U64 PawnAttacksBB[2][64];
extern U64 KnightAttacksBB[64];
extern U64 KingAttacksBB[64];
extern U64 BetweenBB[64][64];   /* squares strictly between, 0 if not aligned */
extern U64 LineBB[64][64];      /* full line through both squares, 0 if not aligned */
extern U64 PseudoAttacks[PIECE_TYPE_NB][64];
extern int SquareDistance[64][64];

void bb_init(void);
U64 bishop_attacks(int sq, U64 occ);
U64 rook_attacks(int sq, U64 occ);
static inline U64 queen_attacks(int sq, U64 occ) {
    return bishop_attacks(sq, occ) | rook_attacks(sq, occ);
}
static inline U64 attacks_bb(int pt, int sq, U64 occ) {
    switch (pt) {
        case BISHOP: return bishop_attacks(sq, occ);
        case ROOK:   return rook_attacks(sq, occ);
        case QUEEN:  return queen_attacks(sq, occ);
        case KNIGHT: return KnightAttacksBB[sq];
        default:     return KingAttacksBB[sq];
    }
}
static inline U64 shift_north(U64 b) { return b << 8; }
static inline U64 shift_south(U64 b) { return b >> 8; }
static inline U64 pawn_push_bb(int c, U64 b) { return c == WHITE ? b << 8 : b >> 8; }
static inline U64 pawn_attacks_bb(int c, U64 b) {
    return c == WHITE ? ((b & ~FileBB[7]) << 9) | ((b & ~FileBB[0]) << 7)
                      : ((b & ~FileBB[7]) >> 7) | ((b & ~FileBB[0]) >> 9);
}

/* ------------------------------------------------------------------ */
/* Position                                                            */
/* ------------------------------------------------------------------ */

typedef struct {
    U64  key;
    U64  pawnKey;
    /* Keys over subsets of the material, used by the correction histories: a
     * static evaluation is wrong for reasons that tend to persist while the
     * pawns, or the minors, or the heavy pieces stay where they are, so each
     * subset gets its own record of how wrong the evaluation has been. */
    U64  nonPawnKey[COLOR_NB];
    U64  minorKey;
    U64  majorKey;
    U64  checkers;
    U64  blockersForKing[2];  /* own pieces shielding own king from a slider */
    U64  pinners[2];
    int  castling;
    int  ep;
    int  halfmove;
    int  pliesFromNull;       /* plies since the last null move */
    int  captured;            /* piece code captured by the move leading here */
    int  eval;                /* cached static eval, or VALUE_NONE */
    Move move;                /* move that led to this state */
} StateInfo;

typedef struct {
    U64 byType[PIECE_TYPE_NB];
    U64 byColor[COLOR_NB];
    U64 occ;
    uint8_t board[64];
    int side;
    int castling;
    int ep;
    int halfmove;
    int pliesFromNull;
    U64 key;
    U64 pawnKey;
    U64 nonPawnKey[COLOR_NB];
    U64 minorKey;
    U64 majorKey;
    U64 checkers;
    U64 blockersForKing[2];
    U64 pinners[2];
    int pieceCount[PIECE_NB];

    int histCount;            /* index of the current state in st[] */
    int rootHist;             /* histCount at the search root */
    StateInfo st[MAX_HIST];
} Position;

#define pos_ply(p)      ((p)->histCount - (p)->rootHist)
#define pos_state(p)    (&(p)->st[(p)->histCount])
#define pieces_cp(p,c,pt) ((p)->byType[pt] & (p)->byColor[c])

extern U64 ZobristPiece[PIECE_NB][64];
extern U64 ZobristEp[8];
extern U64 ZobristCastling[16];
extern U64 ZobristSide;

void pos_init_zobrist(void);
int  pos_set_fen(Position *pos, const char *fen);
void pos_get_fen(const Position *pos, char *out);
void pos_do_move(Position *pos, Move m);
void pos_undo_move(Position *pos, Move m);
void pos_do_null(Position *pos);
void pos_undo_null(Position *pos);
U64  pos_attackers_to(const Position *pos, int sq, U64 occ);
int  pos_in_check(const Position *pos);
int  pos_legal(const Position *pos, Move m);
int  pos_pseudo_legal(const Position *pos, Move m);
int  pos_gives_check(const Position *pos, Move m);
int  pos_see_ge(const Position *pos, Move m, int threshold);
int  pos_is_draw(const Position *pos);
int  pos_has_repeated(const Position *pos);
/* Is there a move available here that walks into a position already seen?
 * Such a move is worth at least a draw, which the search can claim without
 * searching for it. */
int  pos_has_game_cycle(const Position *pos, int ply);
int  pos_insufficient_material(const Position *pos);
int  pos_non_pawn_pieces(const Position *pos, int c);
void pos_update_state(Position *pos);
int  pos_is_capture(const Position *pos, Move m);
void move_to_uci(Move m, char *out);
Move move_from_uci(const Position *pos, const char *s);

/* ------------------------------------------------------------------ */
/* Move generation                                                     */
/* ------------------------------------------------------------------ */

typedef struct {
    Move move;
    int  score;
} ScoredMove;

int gen_legal(const Position *pos, Move *list);
int gen_noisy(const Position *pos, Move *list);   /* captures + queen promos */
int gen_quiet(const Position *pos, Move *list);
int gen_evasions(const Position *pos, Move *list);

/* ------------------------------------------------------------------ */
/* Evaluation                                                          */
/* ------------------------------------------------------------------ */

enum { EVAL_HCE = 0, EVAL_NNUE = 1, EVAL_BLEND = 2 };

extern int g_evalMode;

void eval_init(void);
void eval_tune_changed(void);   /* rebuild the tables derived from the tunables */
int  eval_position(Position *pos);       /* from the side-to-move's point of view */
/* As eval_position, but allowed to stop early and return a material-only
 * estimate when that estimate is already outside [alpha, beta] by LazyMargin.
 * Pass the search window; pass the full window to forbid the shortcut. */
int  eval_position_window(Position *pos, int alpha, int beta);
int  eval_hce(const Position *pos);
int  eval_hce_window(const Position *pos, int alpha, int beta);
int  eval_material_simple(const Position *pos, int c);
extern const int PieceValueMg[PIECE_TYPE_NB];
extern const int PieceValueEg[PIECE_TYPE_NB];
extern const int SeeValue[PIECE_TYPE_NB + 1];

int  nnue_load_file(const char *path);
int  nnue_available(void);
int  nnue_evaluate(const Position *pos);
int  kpk_probe(int strongSide, int ksq, int psq, int weakKsq, int stm);

/* ------------------------------------------------------------------ */
/* Transposition table                                                 */
/* ------------------------------------------------------------------ */

typedef struct {
    uint16_t key16;
    int16_t  score;
    int16_t  eval;
    Move     move;
    uint8_t  depth;      /* stored as depth + 4 so that -4..251 fits */
    uint8_t  genBound;   /* generation (6 bits) | bound (2 bits) */
} TTEntry;

void tt_init(int mb);
void tt_clear(void);
void tt_new_search(void);
int  tt_hashfull(void);
TTEntry *tt_probe(U64 key, int *found);
void tt_store(TTEntry *e, U64 key, int score, int eval, int bound, int depth, Move m, int pv);
void tt_prefetch(U64 key);

/* Stored depth is depth - TT_DEPTH_OFFSET, so depths down to -6 stay
 * non-zero and a zero byte still means "slot unused". */
#define TT_DEPTH_OFFSET (-7)
static inline int tt_depth(const TTEntry *e) { return (int)e->depth + TT_DEPTH_OFFSET; }
static inline int tt_bound(const TTEntry *e) { return e->genBound & 3; }
static inline int tt_is_pv(const TTEntry *e) { return (e->genBound >> 2) & 1; }

/* ------------------------------------------------------------------ */
/* Search                                                              */
/* ------------------------------------------------------------------ */

typedef struct {
    Move  move;
    int   movedPiece;
    int   staticEval;
    int   moveCount;
    int   ply;
    int   inCheck;
    int   ttPv;
    int   cutoffCnt;
    int   doubleExt;
    Move  excluded;
    Move  killers[2];
    int16_t *contHist;
    /* the principal variation found below this node, MOVE_NONE terminated.
     * Collected by the search itself: reading it back out of the transposition
     * table gave a line that did not always belong to the score next to it. */
    Move  pvLine[MAX_PLY + 1];
} SearchStack;

typedef struct {
    Move move;
    int  score;
    int  prevScore;
    int64_t effort;            /* nodes spent below this move */
    /* score smoothed over the completed iterations; the aspiration window is
     * centred on this rather than on the last value, which keeps one noisy
     * iteration from throwing the next one's window away */
    int  avgScore;
    int  seldepth;
    Move pv[MAX_PLY + 2];
    int  pvLen;
} RootMove;

typedef struct Thread {
    Position pos;
    int64_t  nodes;
    int      id;
    int      rootDepth;
    int      completedDepth;
    int      selDepth;
    int      nmpMinPly;
    int      rootDelta;

    RootMove rootMoves[MAX_MOVES];
    int      rootMoveCount;
    int      pvIdx;

    SearchStack stack[MAX_PLY + 12];

    int16_t  mainHist[COLOR_NB][64][64];
    int16_t  captHist[PIECE_NB][64][PIECE_TYPE_NB];
    int16_t  contHist[PIECE_NB][64][PIECE_NB * 64];
    Move     counterMove[PIECE_NB][64];

#define CORR_SIZE 16384
#define CORR_MASK (CORR_SIZE - 1)
    int16_t  pawnCorrHist[COLOR_NB][CORR_SIZE];
    int16_t  minorCorrHist[COLOR_NB][CORR_SIZE];
    int16_t  majorCorrHist[COLOR_NB][CORR_SIZE];
    int16_t  nonPawnCorrHist[COLOR_NB][COLOR_NB][CORR_SIZE];
} Thread;

typedef struct {
    int64_t wtime, btime, winc, binc, movetime;
    int  movestogo;
    int  depth;
    int64_t nodes;
    int  infinite;
} SearchLimits;

typedef struct {
    char  bestmove[8];
    char  ponder[8];
    char  pv[1024];
    int   score;         /* centipawns from the engine's point of view */
    int   mate;          /* mate distance in moves, 0 if none */
    int   depth;
    int   seldepth;
    int64_t nodes;
    int64_t timeMs;
    int   nps;
    int   hashfull;
    int64_t optimumMs;    /* soft time limit the search used */
    int64_t maximumMs;    /* hard time limit the search used */
} SearchResult;

extern volatile int g_stop;
/* Set by search_go once the limits and the clock are in place.  A background
 * search has to be known to have got that far before anything may hand it a
 * ponder hit, or the hit is overwritten by the search's own initialisation. */
extern volatile int g_searchStarted;
extern int g_threadCount;
extern int g_showInfo;        /* print UCI info lines */
extern int g_multiPV;

void search_init(void);
void search_setup_threads(int n);
void search_new_game(void);
void search_go(const Position *rootPos, const SearchLimits *lim, SearchResult *res);
/* Give a search that was started with `infinite` a real clock, keeping the
 * work it has already done.  This is what makes a ponder hit worth having. */
void search_ponderhit(const SearchLimits *lim);
int64_t now_ms(void);

/* ------------------------------------------------------------------ */
/* Tunable constants (tune.h / tune.c)                                 */
/* ------------------------------------------------------------------ */

#define DECLARE(name, def, lo, hi) extern int name;
TUNE_LIST(DECLARE)
#undef DECLARE

typedef struct {
    const char *name;
    int *ptr;
    int def, min, max;
} TuneParam;

const TuneParam *tune_params(void);
int  tune_count(void);
int  tune_set(const char *name, int value);
int  tune_get(const char *name, int *out);
void tune_print_uci_options(void);
void tune_print_values(void);
void tune_init(void);
extern int g_showTuneOptions;
/* called after any parameter changes: rebuilds the tables derived from them */
void tune_changed(void);

/* ------------------------------------------------------------------ */
/* API / UCI                                                           */
/* ------------------------------------------------------------------ */

void uci_loop(void);

#endif /* CE_H */

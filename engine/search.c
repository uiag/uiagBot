/*
 * search.c -- iterative deepening PVS search.
 *
 * Features: aspiration windows, transposition table with bound flags,
 * quiescence search with SEE pruning, staged move ordering (TT move,
 * SEE-split captures with capture history, killers, counter move, main and
 * continuation history), null move pruning with verification, reverse
 * futility, razoring, ProbCut, futility and late move pruning, SEE pruning,
 * late move reductions, singular extensions, internal iterative reduction,
 * mate distance pruning, a pawn-structure correction history, and Lazy SMP.
 */
#include "ce.h"
#include <stdio.h>
#include <stdlib.h>
#include <math.h>

#ifdef _WIN32
#include <windows.h>
#else
#include <pthread.h>
#include <unistd.h>
#endif

#define MAX_THREADS 64

volatile int g_stop = 0;
int g_threadCount = 1;
int g_showInfo = 0;
int g_multiPV = 1;

static Thread *Threads[MAX_THREADS];
static int ThreadsAllocated = 0;

static SearchLimits Limits;
static int64_t StartTime, OptimumTime, MaximumTime;
static int64_t LimitNodes;
static int RootPly;

/* ------------------------------------------------------------------ */
/* time                                                                */
/* ------------------------------------------------------------------ */

int64_t now_ms(void) {
#ifdef _WIN32
    static LARGE_INTEGER freq;
    LARGE_INTEGER c;
    if (!freq.QuadPart) QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&c);
    /* split the division so that counter * 1000 cannot overflow */
    int64_t f = freq.QuadPart;
    return (c.QuadPart / f) * 1000 + ((c.QuadPart % f) * 1000) / f;
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
#endif
}

static int64_t elapsed(void) { return now_ms() - StartTime; }

#define MOVE_OVERHEAD 25

static void init_time(const SearchLimits *lim, int side, int ply) {
    StartTime = now_ms();
    LimitNodes = lim->nodes;

    if (lim->movetime > 0) {
        OptimumTime = MaximumTime = lim->movetime - MOVE_OVERHEAD;
        if (OptimumTime < 1) OptimumTime = MaximumTime = 1;
        return;
    }
    int64_t time = side == WHITE ? lim->wtime : lim->btime;
    int64_t inc  = side == WHITE ? lim->winc  : lim->binc;
    if (time <= 0 && inc <= 0) {
        OptimumTime = MaximumTime = 0;   /* infinite / depth-limited */
        return;
    }

    int mtg = lim->movestogo ? (lim->movestogo < 50 ? lim->movestogo : 50) : 50;
    int64_t timeLeft = time + inc * (mtg - 1) - MOVE_OVERHEAD * (2 + mtg);
    if (timeLeft < 1) timeLeft = 1;

    double optScale, maxScale;
    if (!lim->movestogo) {
        double a = 0.0120 + pow((double)ply + 3.0, 0.45) * 0.0039;
        double b = 0.2 * (double)time / (double)timeLeft;
        optScale = a < b ? a : b;
        maxScale = 3.0 + (double)ply / 14.0;
        if (maxScale > 5.0) maxScale = 5.0;
    } else {
        double a = 0.88 / mtg;
        double b = 0.88 * (double)time / (double)timeLeft;
        optScale = a < b ? a : b;
        maxScale = 1.5 + 0.11 * mtg;
        if (maxScale > 5.0) maxScale = 5.0;
    }

    OptimumTime = (int64_t)(optScale * (double)timeLeft);
    MaximumTime = (int64_t)(maxScale * (double)OptimumTime);

    /* Never put more than this share of the remaining clock into one move.
     * Without it a single long iteration late in a fast game can leave too
     * little behind to answer the next move, which costs the game on time. */
    int64_t cap = (int64_t)(0.45 * (double)(time - MOVE_OVERHEAD));
    if (cap < 1) cap = 1;
    if (MaximumTime > cap) MaximumTime = cap;
    if (OptimumTime > MaximumTime) OptimumTime = MaximumTime;
    if (OptimumTime < 1) OptimumTime = 1;
}

/* ------------------------------------------------------------------ */
/* tables                                                              */
/* ------------------------------------------------------------------ */

static int LmrQuiet[64][64];
static int LmrNoisy[64][64];

void search_init(void) {
    static int done = 0;
    if (done) return;
    done = 1;
    eval_init();
    pos_init_zobrist();
    for (int d = 1; d < 64; d++)
        for (int m = 1; m < 64; m++) {
            LmrQuiet[d][m] = (int)(0.80 + log((double)d) * log((double)m) / 2.25);
            LmrNoisy[d][m] = (int)(-0.20 + log((double)d) * log((double)m) / 3.30);
        }
}

/* ------------------------------------------------------------------ */
/* score <-> tt conversion                                             */
/* ------------------------------------------------------------------ */

static inline int score_to_tt(int v, int ply) {
    if (v >= VALUE_MATE_IN_MAX_PLY) return v + ply;
    if (v <= VALUE_MATED_IN_MAX_PLY) return v - ply;
    return v;
}
static inline int score_from_tt(int v, int ply) {
    if (v == VALUE_NONE) return VALUE_NONE;
    if (v >= VALUE_MATE_IN_MAX_PLY) return v - ply;
    if (v <= VALUE_MATED_IN_MAX_PLY) return v + ply;
    return v;
}

/* ------------------------------------------------------------------ */
/* history                                                             */
/* ------------------------------------------------------------------ */

#define HIST_MAX      16384
#define CAPT_HIST_MAX 10692
#define CORR_GRAIN    256
#define CORR_MAX      8192

static inline void hist_bump(int16_t *v, int bonus, int limit) {
    int b = bonus;
    if (b > limit) b = limit;
    if (b < -limit) b = -limit;
    *v += (int16_t)(b - (int)(*v) * (b < 0 ? -b : b) / limit);
}

static int stat_bonus(int depth) {
    int b = 16 * depth * depth + 32 * depth;
    return b > 1500 ? 1500 : b;
}

static int corr_value(const Thread *th, const Position *pos) {
    int v = th->pawnCorrHist[pos->side][pos->pawnKey & 16383];
    return v / CORR_GRAIN;
}

static void corr_update(Thread *th, const Position *pos, int depth, int diff) {
    int16_t *e = &th->pawnCorrHist[pos->side][pos->pawnKey & 16383];
    int w = depth + 1;
    if (w > 16) w = 16;
    int newv = ((int)(*e) * (256 - w) + diff * CORR_GRAIN * w) / 256;
    if (newv > CORR_MAX) newv = CORR_MAX;
    if (newv < -CORR_MAX) newv = -CORR_MAX;
    *e = (int16_t)newv;
}

static void update_cont_hist(SearchStack *ss, int pc, int to, int bonus) {
    static const int Offsets[4] = { 1, 2, 4, 6 };
    static const int Weights[4] = { 100, 75, 40, 30 };
    for (int i = 0; i < 4; i++) {
        SearchStack *prev = ss - Offsets[i];
        if (prev->move == MOVE_NONE || prev->move == MOVE_NULL) continue;
        hist_bump(&prev->contHist[pc * 64 + to], bonus * Weights[i] / 100, HIST_MAX);
    }
}

static void update_quiet_stats(Thread *th, SearchStack *ss, const Position *pos,
                               Move best, int depth,
                               Move *quiets, int quietCount) {
    int us = pos->side;
    int bonus = stat_bonus(depth);
    int pc = pos->board[move_from(best)];

    if (ss->killers[0] != best) {
        ss->killers[1] = ss->killers[0];
        ss->killers[0] = best;
    }
    hist_bump(&th->mainHist[us][move_from(best)][move_to(best)], bonus, HIST_MAX);
    update_cont_hist(ss, pc, move_to(best), bonus);

    if ((ss - 1)->move != MOVE_NONE && (ss - 1)->move != MOVE_NULL) {
        int prevPc = (ss - 1)->movedPiece;
        if (prevPc != NO_PIECE)
            th->counterMove[prevPc][move_to((ss - 1)->move)] = best;
    }

    for (int i = 0; i < quietCount; i++) {
        Move m = quiets[i];
        if (m == best) continue;
        int p = pos->board[move_from(m)];
        hist_bump(&th->mainHist[us][move_from(m)][move_to(m)], -bonus, HIST_MAX);
        update_cont_hist(ss, p, move_to(m), -bonus);
    }
}

static int captured_type(const Position *pos, Move m) {
    if (move_type(m) == MT_EP) return PAWN;
    int pc = pos->board[move_to(m)];
    return pc == NO_PIECE ? PAWN : piece_type(pc);
}

static void update_capture_stats(Thread *th, const Position *pos, Move best, int depth,
                                 Move *caps, int capCount) {
    int bonus = stat_bonus(depth);
    if (pos_is_capture(pos, best)) {
        int pc = pos->board[move_from(best)];
        hist_bump(&th->captHist[pc][move_to(best)][captured_type(pos, best)],
                  bonus, CAPT_HIST_MAX);
    }
    for (int i = 0; i < capCount; i++) {
        Move m = caps[i];
        if (m == best) continue;
        int pc = pos->board[move_from(m)];
        hist_bump(&th->captHist[pc][move_to(m)][captured_type(pos, m)],
                  -bonus, CAPT_HIST_MAX);
    }
}

/* ------------------------------------------------------------------ */
/* move picker                                                         */
/* ------------------------------------------------------------------ */

enum {
    MP_MAIN, MP_QSEARCH, MP_PROBCUT
};

enum {
    ST_TT, ST_GEN_NOISY, ST_GOOD_NOISY, ST_GEN_QUIET, ST_QUIET, ST_BAD_NOISY,
    ST_EV_TT, ST_EV_GEN, ST_EV_ALL,
    ST_QS_TT, ST_QS_GEN, ST_QS_NOISY,
    ST_PC_TT, ST_PC_GEN, ST_PC_NOISY,
    ST_END
};

typedef struct {
    const Position *pos;
    Thread *th;
    SearchStack *ss;
    Move ttMove;
    int stage;
    int skipQuiets;
    int threshold;
    Move moves[MAX_MOVES];
    int scores[MAX_MOVES];
    int size, idx;
    Move bad[MAX_MOVES];
    int badScores[MAX_MOVES];
    int badSize, badIdx;
} MovePicker;

static int mp_tt_ok(const Position *pos, Move m) {
    return m != MOVE_NONE && pos_pseudo_legal(pos, m) && pos_legal(pos, m);
}

static void mp_init(MovePicker *mp, const Position *pos, Thread *th, SearchStack *ss,
                    Move ttMove, int type, int threshold) {
    mp->pos = pos;
    mp->th = th;
    mp->ss = ss;
    mp->size = mp->idx = mp->badSize = mp->badIdx = 0;
    mp->skipQuiets = 0;
    mp->threshold = threshold;
    mp->ttMove = mp_tt_ok(pos, ttMove) ? ttMove : MOVE_NONE;

    if (type == MP_PROBCUT) {
        if (mp->ttMove != MOVE_NONE
            && (!pos_is_capture(pos, mp->ttMove)
                || !pos_see_ge(pos, mp->ttMove, threshold)))
            mp->ttMove = MOVE_NONE;
        mp->stage = ST_PC_TT;
    } else if (pos->checkers) {
        mp->stage = ST_EV_TT;
    } else if (type == MP_QSEARCH) {
        if (mp->ttMove != MOVE_NONE && !pos_is_capture(pos, mp->ttMove)
            && move_type(mp->ttMove) != MT_PROMO)
            mp->ttMove = MOVE_NONE;
        mp->stage = ST_QS_TT;
    } else {
        mp->stage = ST_TT;
    }
}

static void score_noisy(MovePicker *mp) {
    const Position *pos = mp->pos;
    for (int i = 0; i < mp->size; i++) {
        Move m = mp->moves[i];
        int pc = pos->board[move_from(m)];
        int s = 8 * SeeValue[captured_type(pos, m)]
              + mp->th->captHist[pc][move_to(m)][captured_type(pos, m)];
        if (move_type(m) == MT_PROMO)
            s += move_promo(m) == QUEEN ? 8 * SeeValue[QUEEN] : -8 * SeeValue[QUEEN];
        mp->scores[i] = s;
    }
}

static void score_quiet(MovePicker *mp) {
    const Position *pos = mp->pos;
    SearchStack *ss = mp->ss;
    Thread *th = mp->th;
    int us = pos->side;
    Move counter = MOVE_NONE;
    if ((ss - 1)->move != MOVE_NONE && (ss - 1)->move != MOVE_NULL
        && (ss - 1)->movedPiece != NO_PIECE)
        counter = th->counterMove[(ss - 1)->movedPiece][move_to((ss - 1)->move)];

    for (int i = 0; i < mp->size; i++) {
        Move m = mp->moves[i];
        int from = move_from(m), to = move_to(m);
        int pc = pos->board[from];
        int s = th->mainHist[us][from][to]
              + 2 * (ss - 1)->contHist[pc * 64 + to]
              +     (ss - 2)->contHist[pc * 64 + to]
              +     (ss - 4)->contHist[pc * 64 + to] / 2;
        if (m == ss->killers[0]) s += 1 << 21;
        else if (m == ss->killers[1]) s += 1 << 20;
        else if (m == counter) s += 1 << 19;
        mp->scores[i] = s;
    }
}

static void score_evasions(MovePicker *mp) {
    const Position *pos = mp->pos;
    SearchStack *ss = mp->ss;
    int us = pos->side;
    for (int i = 0; i < mp->size; i++) {
        Move m = mp->moves[i];
        int from = move_from(m), to = move_to(m);
        if (pos_is_capture(pos, m)) {
            mp->scores[i] = (1 << 24) + 8 * SeeValue[captured_type(pos, m)]
                          - piece_type(pos->board[from]);
        } else {
            int pc = pos->board[from];
            mp->scores[i] = mp->th->mainHist[us][from][to]
                          + (ss - 1)->contHist[pc * 64 + to];
            if (m == ss->killers[0]) mp->scores[i] += 1 << 21;
        }
    }
}

static Move pick_best(Move *moves, int *scores, int *idx, int size) {
    int best = *idx;
    for (int i = *idx + 1; i < size; i++)
        if (scores[i] > scores[best]) best = i;
    Move m = moves[best];
    int s = scores[best];
    moves[best] = moves[*idx];
    scores[best] = scores[*idx];
    moves[*idx] = m;
    scores[*idx] = s;
    (*idx)++;
    return m;
}

static Move mp_next(MovePicker *mp) {
    const Position *pos = mp->pos;
    Move m;

    switch (mp->stage) {
        case ST_TT:
            mp->stage = ST_GEN_NOISY;
            if (mp->ttMove != MOVE_NONE) return mp->ttMove;
            /* fallthrough */
        case ST_GEN_NOISY:
            mp->size = gen_noisy(pos, mp->moves);
            mp->idx = 0;
            score_noisy(mp);
            mp->stage = ST_GOOD_NOISY;
            /* fallthrough */
        case ST_GOOD_NOISY:
            while (mp->idx < mp->size) {
                m = pick_best(mp->moves, mp->scores, &mp->idx, mp->size);
                if (m == mp->ttMove) continue;
                if (pos_see_ge(pos, m, -50)) return m;
                mp->bad[mp->badSize] = m;
                mp->badScores[mp->badSize++] = mp->scores[mp->idx - 1];
            }
            mp->stage = ST_GEN_QUIET;
            /* fallthrough */
        case ST_GEN_QUIET:
            if (!mp->skipQuiets) {
                mp->size = gen_quiet(pos, mp->moves);
                mp->idx = 0;
                score_quiet(mp);
            } else {
                mp->size = mp->idx = 0;
            }
            mp->stage = ST_QUIET;
            /* fallthrough */
        case ST_QUIET:
            if (!mp->skipQuiets)
                while (mp->idx < mp->size) {
                    m = pick_best(mp->moves, mp->scores, &mp->idx, mp->size);
                    if (m == mp->ttMove) continue;
                    return m;
                }
            mp->stage = ST_BAD_NOISY;
            /* fallthrough */
        case ST_BAD_NOISY:
            while (mp->badIdx < mp->badSize) {
                m = pick_best(mp->bad, mp->badScores, &mp->badIdx, mp->badSize);
                return m;
            }
            mp->stage = ST_END;
            return MOVE_NONE;

        case ST_EV_TT:
            mp->stage = ST_EV_GEN;
            if (mp->ttMove != MOVE_NONE) return mp->ttMove;
            /* fallthrough */
        case ST_EV_GEN:
            mp->size = gen_evasions(pos, mp->moves);
            mp->idx = 0;
            score_evasions(mp);
            mp->stage = ST_EV_ALL;
            /* fallthrough */
        case ST_EV_ALL:
            while (mp->idx < mp->size) {
                m = pick_best(mp->moves, mp->scores, &mp->idx, mp->size);
                if (m == mp->ttMove) continue;
                return m;
            }
            mp->stage = ST_END;
            return MOVE_NONE;

        case ST_QS_TT:
            mp->stage = ST_QS_GEN;
            if (mp->ttMove != MOVE_NONE) return mp->ttMove;
            /* fallthrough */
        case ST_QS_GEN:
            mp->size = gen_noisy(pos, mp->moves);
            mp->idx = 0;
            score_noisy(mp);
            mp->stage = ST_QS_NOISY;
            /* fallthrough */
        case ST_QS_NOISY:
            while (mp->idx < mp->size) {
                m = pick_best(mp->moves, mp->scores, &mp->idx, mp->size);
                if (m == mp->ttMove) continue;
                return m;
            }
            mp->stage = ST_END;
            return MOVE_NONE;

        case ST_PC_TT:
            mp->stage = ST_PC_GEN;
            if (mp->ttMove != MOVE_NONE) return mp->ttMove;
            /* fallthrough */
        case ST_PC_GEN:
            mp->size = gen_noisy(pos, mp->moves);
            mp->idx = 0;
            score_noisy(mp);
            mp->stage = ST_PC_NOISY;
            /* fallthrough */
        case ST_PC_NOISY:
            while (mp->idx < mp->size) {
                m = pick_best(mp->moves, mp->scores, &mp->idx, mp->size);
                if (m == mp->ttMove) continue;
                if (pos_see_ge(pos, m, mp->threshold)) return m;
            }
            mp->stage = ST_END;
            return MOVE_NONE;

        default:
            return MOVE_NONE;
    }
}

/* ------------------------------------------------------------------ */
/* search                                                              */
/* ------------------------------------------------------------------ */

static int search_node(Thread *th, SearchStack *ss, int alpha, int beta, int depth,
                       int cutNode, int rootNode);
static int qsearch(Thread *th, SearchStack *ss, int alpha, int beta, int depth);

#ifdef CE_TIME_DEBUG
static int64_t DbgChecks, DbgStopSetAt, DbgLastIterEnd;
#endif

static void check_time(Thread *th) {
    if (th->id != 0 || g_stop) return;
#ifdef CE_TIME_DEBUG
    DbgChecks++;
#endif
    if (LimitNodes > 0) {
        int64_t total = 0;
        for (int i = 0; i < g_threadCount; i++) total += Threads[i]->nodes;
        if (total >= LimitNodes) { g_stop = 1; return; }
    }
    if (MaximumTime > 0 && !Limits.infinite && elapsed() >= MaximumTime) {
        g_stop = 1;
#ifdef CE_TIME_DEBUG
        DbgStopSetAt = elapsed();
#endif
    }
}

static int qsearch(Thread *th, SearchStack *ss, int alpha, int beta, int depth) {
    Position *pos = &th->pos;
    int ply = pos_ply(pos);
    int pvNode = beta - alpha > 1;

    th->nodes++;
    if ((th->nodes & 2047) == 0) check_time(th);
    if (g_stop) return VALUE_DRAW;

    if (ply > th->selDepth) th->selDepth = ply;
    if (pos_is_draw(pos)) return VALUE_DRAW;
    if (ply >= MAX_PLY - 2)
        return pos->checkers ? VALUE_DRAW : eval_position(pos);

    ss->inCheck = pos->checkers != 0;
    ss->moveCount = 0;

    int found;
    TTEntry *tte = tt_probe(pos->key, &found);
    int ttScore = found ? score_from_tt((int)tte->score, ply) : VALUE_NONE;
    int ttEval = found ? (int)tte->eval : VALUE_NONE;
    Move ttMove = found ? tte->move : MOVE_NONE;
    int ttBound = found ? tt_bound(tte) : BOUND_NONE;
    int ttPv = found ? tt_is_pv(tte) : 0;

    if (!pvNode && found && ttScore != VALUE_NONE
        && (ttBound & (ttScore >= beta ? BOUND_LOWER : BOUND_UPPER)))
        return ttScore;

    int bestScore, futilityBase, rawEval = VALUE_NONE;
    if (ss->inCheck) {
        bestScore = futilityBase = -VALUE_INFINITE;
        ss->staticEval = VALUE_NONE;
    } else {
        rawEval = ttEval != VALUE_NONE ? ttEval : eval_position(pos);
        bestScore = rawEval + corr_value(th, pos);
        ss->staticEval = bestScore;

        if (ttScore != VALUE_NONE
            && (ttBound & (ttScore > bestScore ? BOUND_LOWER : BOUND_UPPER)))
            bestScore = ttScore;

        if (bestScore >= beta) {
            if (!found)
                tt_store(tte, pos->key, score_to_tt(bestScore, ply), rawEval,
                         BOUND_LOWER, -4, MOVE_NONE, 0);
            return bestScore;
        }
        if (bestScore > alpha) alpha = bestScore;
        futilityBase = bestScore + 180;
    }

    MovePicker mp;
    mp_init(&mp, pos, th, ss, ttMove, MP_QSEARCH, 0);

    Move bestMove = MOVE_NONE;
    Move m;
    int moveCount = 0;
    int prevSq = (ss - 1)->move != MOVE_NONE && (ss - 1)->move != MOVE_NULL
                 ? move_to((ss - 1)->move) : SQ_NONE;

    while ((m = mp_next(&mp)) != MOVE_NONE) {
        if (!pos_legal(pos, m)) continue;
        moveCount++;

        int capture = pos_is_capture(pos, m);
        int givesCheck = pos_gives_check(pos, m);

        if (bestScore > VALUE_MATED_IN_MAX_PLY && !ss->inCheck) {
            /* futility: even winning the piece cannot raise alpha */
            if (!givesCheck && move_to(m) != prevSq && futilityBase > -VALUE_INFINITE
                && move_type(m) != MT_PROMO) {
                if (moveCount > 2) continue;
                int futility = futilityBase + SeeValue[captured_type(pos, m)];
                if (futility <= alpha) {
                    if (futility > bestScore) bestScore = futility;
                    continue;
                }
                if (futilityBase <= alpha && !pos_see_ge(pos, m, 1)) {
                    if (futilityBase > bestScore) bestScore = futilityBase;
                    continue;
                }
            }
            /* don't search losing captures */
            if (!pos_see_ge(pos, m, -75)) continue;
        }

        ss->move = m;
        ss->movedPiece = pos->board[move_from(m)];
        ss->contHist = &th->contHist[ss->movedPiece][move_to(m)][0];
        (void)capture;

        pos_do_move(pos, m);
        tt_prefetch(pos->key);
        int score = -qsearch(th, ss + 1, -beta, -alpha, depth - 1);
        pos_undo_move(pos, m);

        if (g_stop) return VALUE_DRAW;

        if (score > bestScore) {
            bestScore = score;
            if (score > alpha) {
                bestMove = m;
                if (score >= beta) break;
                alpha = score;
            }
        }
    }

    if (ss->inCheck && moveCount == 0)
        return -VALUE_MATE + ply;

    tt_store(tte, pos->key, score_to_tt(bestScore, ply), rawEval,
             bestScore >= beta ? BOUND_LOWER : BOUND_UPPER, -4, bestMove, ttPv || pvNode);
    return bestScore;
}

static int search_node(Thread *th, SearchStack *ss, int alpha, int beta, int depth,
                       int cutNode, int rootNode) {
    Position *pos = &th->pos;
    int pvNode = beta - alpha > 1;
    int ply = pos_ply(pos);
    int us = pos->side;

    if (depth <= 0) return qsearch(th, ss, alpha, beta, 0);
    if (depth > MAX_PLY - 4) depth = MAX_PLY - 4;

    th->nodes++;
    if ((th->nodes & 2047) == 0) check_time(th);
    if (g_stop) return VALUE_DRAW;

    if (pvNode && ply > th->selDepth) th->selDepth = ply;

    ss->inCheck = pos->checkers != 0;
    ss->moveCount = 0;
    (ss + 1)->excluded = MOVE_NONE;
    (ss + 1)->doubleExt = ss->doubleExt;
    (ss + 2)->killers[0] = (ss + 2)->killers[1] = MOVE_NONE;
    (ss + 2)->cutoffCnt = 0;

    if (!rootNode) {
        if (pos_is_draw(pos)) return VALUE_DRAW;
        if (ply >= MAX_PLY - 2)
            return ss->inCheck ? VALUE_DRAW : eval_position(pos);

        /* mate distance pruning */
        int mdAlpha = -VALUE_MATE + ply;
        int mdBeta = VALUE_MATE - ply - 1;
        if (mdAlpha > alpha) alpha = mdAlpha;
        if (mdBeta < beta) beta = mdBeta;
        if (alpha >= beta) return alpha;
    }

    Move excluded = ss->excluded;
    U64 key = pos->key ^ (excluded ? 0x9D39247E33776D41ULL : 0);

    int found;
    TTEntry *tte = tt_probe(key, &found);
    int ttScore = found ? score_from_tt((int)tte->score, ply) : VALUE_NONE;
    int ttEval = found ? (int)tte->eval : VALUE_NONE;
    Move ttMove = rootNode ? th->rootMoves[th->pvIdx].move
                           : (found ? tte->move : MOVE_NONE);
    int ttDepth = found ? tt_depth(tte) : -6;
    int ttBound = found ? tt_bound(tte) : BOUND_NONE;
    int ttPv = pvNode || (found && tt_is_pv(tte));
    int ttCapture = ttMove != MOVE_NONE && pos_is_capture(pos, ttMove);

    if (!pvNode && !excluded && found && ttDepth >= depth && ttScore != VALUE_NONE
        && (ttBound & (ttScore >= beta ? BOUND_LOWER : BOUND_UPPER))) {
        if (ttMove != MOVE_NONE && ttScore >= beta && !ttCapture
            && pos_pseudo_legal(pos, ttMove) && pos_legal(pos, ttMove)) {
            int bonus = stat_bonus(depth);
            hist_bump(&th->mainHist[us][move_from(ttMove)][move_to(ttMove)],
                      bonus, HIST_MAX);
            update_cont_hist(ss, pos->board[move_from(ttMove)], move_to(ttMove), bonus);
        }
        if (pos->halfmove < 90) return ttScore;
    }

    /* ---- static evaluation ---- */
    int eval, rawEval;
    int improving = 0;

    if (ss->inCheck) {
        ss->staticEval = eval = rawEval = VALUE_NONE;
        goto moves_loop;
    } else if (excluded) {
        eval = ss->staticEval;
        rawEval = eval;
    } else {
        if (found && ttEval != VALUE_NONE) rawEval = ttEval;
        else {
            rawEval = eval_position(pos);
            if (!found)
                tt_store(tte, key, VALUE_NONE, rawEval, BOUND_NONE, -6, MOVE_NONE, ttPv);
        }
        eval = ss->staticEval = rawEval + corr_value(th, pos);

        /* trust a TT score over the static eval when it is a usable bound */
        if (ttScore != VALUE_NONE
            && (ttBound & (ttScore > eval ? BOUND_LOWER : BOUND_UPPER)))
            eval = ttScore;
    }

    if (ss->staticEval != VALUE_NONE) {
        if ((ss - 2)->staticEval != VALUE_NONE)
            improving = ss->staticEval > (ss - 2)->staticEval;
        else if ((ss - 4)->staticEval != VALUE_NONE)
            improving = ss->staticEval > (ss - 4)->staticEval;
        else
            improving = 1;
    }

    if (!pvNode && !excluded && depth < 9 && eval < VALUE_TB_WIN) {
        /* reverse futility / static null move */
        if (eval - 85 * (depth - improving) >= beta && eval >= beta)
            return (eval + beta) / 2;
    }

    /* razoring */
    if (!pvNode && depth <= 6 && eval + 280 + 250 * depth < alpha) {
        int v = qsearch(th, ss, alpha - 1, alpha, 0);
        if (v < alpha) return v;
    }

    /* null move pruning */
    if (!pvNode && !excluded
        && (ss - 1)->move != MOVE_NULL
        && depth >= 3
        && eval >= beta
        && ss->staticEval >= beta - 21 * depth + 330
        && pos_non_pawn_pieces(pos, us) > 0
        && ply >= th->nmpMinPly
        && beta > VALUE_MATED_IN_MAX_PLY) {

        int R = 4 + depth / 6 + ((eval - beta) / 200 < 3 ? (eval - beta) / 200 : 3);
        ss->move = MOVE_NULL;
        ss->movedPiece = NO_PIECE;
        ss->contHist = &th->contHist[NO_PIECE][0][0];

        pos_do_null(pos);
        int nullScore = -search_node(th, ss + 1, -beta, -beta + 1, depth - R, !cutNode, 0);
        pos_undo_null(pos);

        if (g_stop) return VALUE_DRAW;

        if (nullScore >= beta) {
            if (nullScore >= VALUE_MATE_IN_MAX_PLY) nullScore = beta;
            if (th->nmpMinPly || depth < 14) return nullScore;

            /* verification search at high depth */
            th->nmpMinPly = ply + 3 * (depth - R) / 4;
            int v = search_node(th, ss, beta - 1, beta, depth - R, 0, 0);
            th->nmpMinPly = 0;
            if (v >= beta) return nullScore;
        }
    }

    /* ProbCut */
    if (!pvNode && depth > 4 && !excluded
        && beta > VALUE_MATED_IN_MAX_PLY && beta < VALUE_TB_WIN) {
        int probcutBeta = beta + 185 - 55 * improving;
        if (!(found && ttDepth >= depth - 3 && ttScore != VALUE_NONE
              && ttScore < probcutBeta)) {
            MovePicker mp;
            mp_init(&mp, pos, th, ss, ttMove, MP_PROBCUT,
                    probcutBeta - ss->staticEval);
            Move m;
            while ((m = mp_next(&mp)) != MOVE_NONE) {
                if (m == excluded || !pos_legal(pos, m)) continue;
                ss->move = m;
                ss->movedPiece = pos->board[move_from(m)];
                ss->contHist = &th->contHist[ss->movedPiece][move_to(m)][0];

                pos_do_move(pos, m);
                int v = -qsearch(th, ss + 1, -probcutBeta, -probcutBeta + 1, 0);
                if (v >= probcutBeta)
                    v = -search_node(th, ss + 1, -probcutBeta, -probcutBeta + 1,
                                     depth - 4, !cutNode, 0);
                pos_undo_move(pos, m);

                if (g_stop) return VALUE_DRAW;
                if (v >= probcutBeta) {
                    tt_store(tte, key, score_to_tt(v, ply), rawEval, BOUND_LOWER,
                             depth - 3, m, ttPv);
                    return v;
                }
            }
        }
    }

    /* internal iterative reduction */
    if (pvNode && depth >= 3 && ttMove == MOVE_NONE) depth -= 2;
    if (cutNode && depth >= 8 && ttMove == MOVE_NONE) depth -= 1;
    if (depth <= 0) return qsearch(th, ss, alpha, beta, 0);

moves_loop:;
    MovePicker mp;
    mp_init(&mp, pos, th, ss, ttMove, MP_MAIN, 0);

    Move quiets[64], caps[32];
    int quietCount = 0, capCount = 0;
    int bestScore = -VALUE_INFINITE;
    Move bestMove = MOVE_NONE;
    int moveCount = 0;
    Move m;

    while ((m = mp_next(&mp)) != MOVE_NONE) {
        if (m == excluded) continue;
        if (!pos_legal(pos, m)) continue;

        if (rootNode) {
            int isRoot = 0;
            for (int i = th->pvIdx; i < th->rootMoveCount; i++)
                if (th->rootMoves[i].move == m) { isRoot = 1; break; }
            if (!isRoot) continue;
        }

        moveCount++;
        ss->moveCount = moveCount;

        int capture = pos_is_capture(pos, m);
        int givesCheck = pos_gives_check(pos, m);
        int movedPiece = pos->board[move_from(m)];
        int newDepth = depth - 1;
        int extension = 0;

        int hist = 0;
        if (!capture)
            hist = th->mainHist[us][move_from(m)][move_to(m)]
                 + (ss - 1)->contHist[movedPiece * 64 + move_to(m)]
                 + (ss - 2)->contHist[movedPiece * 64 + move_to(m)];

        /* ---- shallow pruning ---- */
        if (!rootNode && bestScore > VALUE_MATED_IN_MAX_PLY
            && pos_non_pawn_pieces(pos, us) > 0) {

            int r = capture ? LmrNoisy[depth < 63 ? depth : 63][moveCount < 63 ? moveCount : 63]
                            : LmrQuiet[depth < 63 ? depth : 63][moveCount < 63 ? moveCount : 63];
            int lmrDepth = newDepth - r + hist / 8000;
            if (lmrDepth < 0) lmrDepth = 0;

            if (!capture && !givesCheck) {
                /* late move pruning */
                if (moveCount >= (3 + depth * depth) / (2 - improving))
                    mp.skipQuiets = 1;

                /* history pruning */
                if (lmrDepth < 5 && hist < -3800 * depth)
                    continue;

                /* futility pruning */
                if (!ss->inCheck && lmrDepth < 9
                    && ss->staticEval + 95 + 125 * lmrDepth <= alpha)
                    continue;

                /* SEE pruning of quiets */
                if (!pos_see_ge(pos, m, -24 * lmrDepth * lmrDepth))
                    continue;
            } else {
                /* SEE pruning of captures */
                if (!pos_see_ge(pos, m, -95 * depth))
                    continue;
            }
        }

        /* ---- singular extension ---- */
        if (!rootNode && depth >= 7 && m == ttMove && !excluded
            && ttScore != VALUE_NONE && (ttBound & BOUND_LOWER)
            && ttDepth >= depth - 3
            && ttScore > VALUE_MATED_IN_MAX_PLY && ttScore < VALUE_TB_WIN) {

            int singularBeta = ttScore - (56 + 79 * (ttPv && !pvNode)) * depth / 64;
            int singularDepth = (depth - 1) / 2;

            ss->excluded = m;
            int v = search_node(th, ss, singularBeta - 1, singularBeta,
                                singularDepth, cutNode, 0);
            ss->excluded = MOVE_NONE;

            if (g_stop) return VALUE_DRAW;

            if (v < singularBeta) {
                extension = 1;
                if (!pvNode && v < singularBeta - 80 && ss->doubleExt <= 8) {
                    extension = 2;
                    (ss + 1)->doubleExt = ss->doubleExt + 1;
                }
            } else if (v >= beta && v > VALUE_MATED_IN_MAX_PLY && v < VALUE_TB_WIN) {
                return v;            /* multi-cut */
            } else if (ttScore >= beta) {
                extension = -2;
            } else if (cutNode) {
                extension = -1;
            }
        } else if (givesCheck && depth > 8) {
            extension = 1;
        }

        newDepth += extension;
        if (newDepth < 0) newDepth = 0;   /* 0 drops into quiescence */

        ss->move = m;
        ss->movedPiece = movedPiece;
        ss->contHist = &th->contHist[movedPiece][move_to(m)][0];
        if ((ss + 1)->doubleExt < ss->doubleExt) (ss + 1)->doubleExt = ss->doubleExt;

        pos_do_move(pos, m);
        tt_prefetch(pos->key);

        int score;
        int dIdx = depth < 63 ? depth : 63;
        int mIdx = moveCount < 63 ? moveCount : 63;

        if (depth >= 2 && moveCount > 1 + 2 * rootNode
            && (!capture || cutNode || !ttPv)) {

            int r = capture ? LmrNoisy[dIdx][mIdx] : LmrQuiet[dIdx][mIdx];
            if (!improving) r++;
            if (cutNode) r += 2;
            if (ttPv) r--;
            if (pvNode) r--;
            if (givesCheck) r--;
            if (ttCapture && !capture) r++;
            if (pos->checkers) r--;
            if (!capture) r -= hist / 7000;
            if (mp.stage == ST_BAD_NOISY) r++;

            if (r < 0) r = 0;
            int d = newDepth - r;
            if (d < 1) d = 1;
            if (d > newDepth) d = newDepth;

            score = -search_node(th, ss + 1, -(alpha + 1), -alpha, d, 1, 0);

            if (score > alpha && d < newDepth) {
                int deeper = newDepth + (score > bestScore + 70 && newDepth < depth);
                score = -search_node(th, ss + 1, -(alpha + 1), -alpha, deeper,
                                     !cutNode, 0);
                if (!capture) {
                    int bonus = score > alpha ? stat_bonus(newDepth)
                                              : -stat_bonus(newDepth);
                    update_cont_hist(ss, movedPiece, move_to(m), bonus);
                }
            }
        } else if (!pvNode || moveCount > 1) {
            score = -search_node(th, ss + 1, -(alpha + 1), -alpha, newDepth,
                                 !cutNode, 0);
        } else {
            score = alpha + 1;    /* force the PV search below */
        }

        if (pvNode && (moveCount == 1 || score > alpha)) {
            score = -search_node(th, ss + 1, -beta, -alpha, newDepth, 0, 0);
        }

        pos_undo_move(pos, m);

        if (g_stop) return VALUE_DRAW;

        if (rootNode) {
            RootMove *rm = NULL;
            for (int i = 0; i < th->rootMoveCount; i++)
                if (th->rootMoves[i].move == m) { rm = &th->rootMoves[i]; break; }
            if (rm) {
                if (moveCount == 1 || score > alpha) {
                    rm->score = score;
                    rm->seldepth = th->selDepth;
                    rm->pv[0] = m;
                    rm->pvLen = 1;
                    /* collect the PV from the TT */
                    Position *p = pos;
                    Move line[MAX_PLY];
                    int n = 0;
                    pos_do_move(p, m);
                    while (n < MAX_PLY - 2) {
                        int f;
                        TTEntry *e = tt_probe(p->key, &f);
                        if (!f) break;
                        Move pm = e->move;
                        if (pm == MOVE_NONE || !pos_pseudo_legal(p, pm)
                            || !pos_legal(p, pm)) break;
                        if (pos_is_draw(p)) break;
                        line[n++] = pm;
                        pos_do_move(p, pm);
                    }
                    for (int i = n - 1; i >= 0; i--) pos_undo_move(p, line[i]);
                    pos_undo_move(p, m);
                    for (int i = 0; i < n; i++) rm->pv[1 + i] = line[i];
                    rm->pvLen = 1 + n;
                } else {
                    rm->score = -VALUE_INFINITE;
                }
            }
        }

        if (score > bestScore) {
            bestScore = score;
            if (score > alpha) {
                bestMove = m;
                if (pvNode && !rootNode) { /* pv is reconstructed from the TT */ }
                if (score >= beta) {
                    ss->cutoffCnt++;
                    break;
                }
                alpha = score;
            }
        }

        if (m != bestMove) {
            if (capture && capCount < 32) caps[capCount++] = m;
            else if (!capture && quietCount < 64) quiets[quietCount++] = m;
        }
    }

    if (moveCount == 0)
        return excluded ? alpha : (ss->inCheck ? -VALUE_MATE + ply : VALUE_DRAW);

    if (bestMove != MOVE_NONE) {
        if (!pos_is_capture(pos, bestMove))
            update_quiet_stats(th, ss, pos, bestMove, depth + (bestScore > beta + 60),
                               quiets, quietCount);
        update_capture_stats(th, pos, bestMove, depth, caps, capCount);
    } else if (!pos->checkers && (ss - 1)->move != MOVE_NONE
               && (ss - 1)->move != MOVE_NULL && (ss - 1)->movedPiece != NO_PIECE) {
        /* reward the opponent's previous move: it caused a fail-low here */
        update_cont_hist(ss, (ss - 1)->movedPiece, move_to((ss - 1)->move),
                         -stat_bonus(depth));
    }

    if (!excluded) {
        int bound = bestScore >= beta ? BOUND_LOWER
                  : (pvNode && bestMove != MOVE_NONE) ? BOUND_EXACT : BOUND_UPPER;
        tt_store(tte, key, score_to_tt(bestScore, ply), rawEval, bound, depth,
                 bestMove, ttPv);

        /* correction history: how far off was the static eval? */
        if (!ss->inCheck && rawEval != VALUE_NONE
            && !(bestScore >= beta && bestScore <= ss->staticEval)
            && !(bestMove == MOVE_NONE && bestScore >= ss->staticEval))
            corr_update(th, pos, depth, bestScore - ss->staticEval);
    }

    return bestScore;
}

/* ------------------------------------------------------------------ */
/* iterative deepening                                                 */
/* ------------------------------------------------------------------ */

static void sort_root_moves(Thread *th, int from) {
    for (int i = from + 1; i < th->rootMoveCount; i++) {
        RootMove tmp = th->rootMoves[i];
        int j = i;
        while (j > from && th->rootMoves[j - 1].score < tmp.score) {
            th->rootMoves[j] = th->rootMoves[j - 1];
            j--;
        }
        th->rootMoves[j] = tmp;
    }
}

static void print_info(Thread *th, int depth, int multiPvIdx) {
    RootMove *rm = &th->rootMoves[multiPvIdx];
    int64_t nodes = 0;
    for (int i = 0; i < g_threadCount; i++) nodes += Threads[i]->nodes;
    int64_t ms = elapsed();
    if (ms < 1) ms = 1;

    char buf[4096];
    int n = 0;
    n += sprintf(buf + n, "info depth %d seldepth %d multipv %d score ",
                 depth, rm->seldepth, multiPvIdx + 1);
    if (rm->score >= VALUE_MATE_IN_MAX_PLY)
        n += sprintf(buf + n, "mate %d", (VALUE_MATE - rm->score + 1) / 2);
    else if (rm->score <= VALUE_MATED_IN_MAX_PLY)
        n += sprintf(buf + n, "mate %d", -(VALUE_MATE + rm->score) / 2);
    else
        n += sprintf(buf + n, "cp %d", rm->score);
    n += sprintf(buf + n, " nodes %lld nps %lld hashfull %d time %lld pv",
                 (long long)nodes, (long long)(nodes * 1000 / ms),
                 tt_hashfull(), (long long)ms);
    for (int i = 0; i < rm->pvLen && n < 3900; i++) {
        char mv[8];
        move_to_uci(rm->pv[i], mv);
        n += sprintf(buf + n, " %s", mv);
    }
    printf("%s\n", buf);
    fflush(stdout);
}

static void thread_search(Thread *th) {
    /* Offset by 7 so that (ss-6) is always inside the array. */
    SearchStack *ss = &th->stack[7];
    memset(th->stack, 0, sizeof(th->stack));
    for (int i = 0; i < MAX_PLY + 12; i++) {
        th->stack[i].contHist = &th->contHist[NO_PIECE][0][0];
        th->stack[i].staticEval = VALUE_NONE;
        th->stack[i].move = MOVE_NONE;
        th->stack[i].movedPiece = NO_PIECE;
        th->stack[i].excluded = MOVE_NONE;
    }

    th->selDepth = 0;
    th->nmpMinPly = 0;
    th->completedDepth = 0;

    int multiPV = g_multiPV;
    if (multiPV > th->rootMoveCount) multiPV = th->rootMoveCount;

    int lastBestScore = -VALUE_INFINITE;
    Move lastBestMove = MOVE_NONE;
    int stability = 0;

    /* helper threads skip some depths so they diverge from the main thread */
    static const int SkipSize[20]  = { 1, 1, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 5 };
    static const int SkipPhase[20] = { 0, 1, 0, 1, 2, 0, 1, 2, 3, 0, 1, 2, 3, 4, 0, 1, 2, 3, 4, 5 };

    int maxDepth = Limits.depth > 0 ? Limits.depth : MAX_PLY - 8;

    for (int depth = 1; depth <= maxDepth && !g_stop; depth++) {
        if (th->id > 0) {
            int i = (th->id - 1) % 20;
            if (((depth + SkipPhase[i]) / SkipSize[i]) % 2) continue;
        }

        th->rootDepth = depth;
        for (int i = 0; i < th->rootMoveCount; i++)
            th->rootMoves[i].prevScore = th->rootMoves[i].score;

        for (th->pvIdx = 0; th->pvIdx < multiPV && !g_stop; th->pvIdx++) {
            th->selDepth = 0;

            int delta = 10 + (th->rootMoves[th->pvIdx].prevScore > -VALUE_INFINITE
                              ? th->rootMoves[th->pvIdx].prevScore * th->rootMoves[th->pvIdx].prevScore / 16384 : 0);
            int prev = th->rootMoves[th->pvIdx].prevScore;
            int alpha = -VALUE_INFINITE, beta = VALUE_INFINITE;
            if (depth >= 4 && prev > -VALUE_INFINITE && prev < VALUE_INFINITE) {
                alpha = prev - delta < -VALUE_INFINITE ? -VALUE_INFINITE : prev - delta;
                beta  = prev + delta > VALUE_INFINITE ? VALUE_INFINITE : prev + delta;
            }
            th->rootDelta = beta - alpha;

            int failedHigh = 0;
            while (1) {
                int adjDepth = depth - failedHigh;
                if (adjDepth < 1) adjDepth = 1;
                int score = search_node(th, ss, alpha, beta, adjDepth, 0, 1);

                sort_root_moves(th, th->pvIdx);
                if (g_stop) break;

                if (score <= alpha) {
                    beta = (alpha + beta) / 2;
                    alpha = score - delta < -VALUE_INFINITE ? -VALUE_INFINITE : score - delta;
                    failedHigh = 0;
                } else if (score >= beta) {
                    beta = score + delta > VALUE_INFINITE ? VALUE_INFINITE : score + delta;
                    failedHigh++;
                    if (failedHigh > 3) failedHigh = 3;
                } else {
                    break;
                }
                delta += delta / 3 + 2;
                if (alpha >= beta) { alpha = -VALUE_INFINITE; beta = VALUE_INFINITE; }
            }

            if (th->id == 0 && g_showInfo && (!g_stop || th->pvIdx == 0))
                print_info(th, depth, th->pvIdx);
        }

        if (!g_stop) th->completedDepth = depth;
#ifdef CE_TIME_DEBUG
        if (th->id == 0) DbgLastIterEnd = elapsed();
#endif

        if (th->id != 0) continue;

        /* ---- time management on the main thread ---- */
        if (th->rootMoves[0].move == lastBestMove) stability++;
        else { stability = 0; lastBestMove = th->rootMoves[0].move; }

        int score = th->rootMoves[0].score;
        if (Limits.depth == 0 && Limits.nodes == 0 && !Limits.infinite
            && OptimumTime > 0) {

            double factor = 1.0;
            factor *= 1.22 - 0.06 * (stability < 8 ? stability : 8);
            if (lastBestScore > -VALUE_INFINITE) {
                int diff = lastBestScore - score;
                if (diff > 40) factor *= 1.15;
                if (diff > 120) factor *= 1.25;
                if (diff < -60) factor *= 0.92;
            }
            if (th->rootMoveCount == 1) factor *= 0.35;

            if ((double)elapsed() > (double)OptimumTime * factor) g_stop = 1;

            /* A mate the finished iteration actually resolved is enough: it
             * lies inside the horizon, so searching on would only confirm it.
             * The bound has to be the depth itself -- allowing 2*(depth-2)
             * stopped on mates far beyond the horizon, and the engine then
             * announced (and played) a mate in 14 in a position where a mate
             * in 7 was there to be found two plies deeper. */
            if (score >= VALUE_MATE_IN_MAX_PLY
                && VALUE_MATE - score <= depth) g_stop = 1;
        }
        lastBestScore = score;
    }
}

/* ------------------------------------------------------------------ */
/* threads                                                             */
/* ------------------------------------------------------------------ */

#ifdef _WIN32
static DWORD WINAPI thread_entry(LPVOID arg) {
    thread_search((Thread *)arg);
    return 0;
}
#else
static void *thread_entry(void *arg) {
    thread_search((Thread *)arg);
    return NULL;
}
#endif

void search_setup_threads(int n) {
    if (n < 1) n = 1;
    if (n > MAX_THREADS) n = MAX_THREADS;
    for (int i = ThreadsAllocated; i < n; i++) {
        Threads[i] = (Thread *)calloc(1, sizeof(Thread));
        if (!Threads[i]) { n = i; break; }
        Threads[i]->id = i;
    }
    if (n > ThreadsAllocated) ThreadsAllocated = n;
    g_threadCount = n;
}

void search_new_game(void) {
    tt_clear();
    for (int i = 0; i < ThreadsAllocated; i++) {
        if (!Threads[i]) continue;
        memset(Threads[i]->mainHist, 0, sizeof(Threads[i]->mainHist));
        memset(Threads[i]->captHist, 0, sizeof(Threads[i]->captHist));
        memset(Threads[i]->contHist, 0, sizeof(Threads[i]->contHist));
        memset(Threads[i]->counterMove, 0, sizeof(Threads[i]->counterMove));
        memset(Threads[i]->pawnCorrHist, 0, sizeof(Threads[i]->pawnCorrHist));
    }
}

void search_go(const Position *rootPos, const SearchLimits *lim, SearchResult *res) {
    search_init();
    if (ThreadsAllocated == 0) search_setup_threads(g_threadCount);

    Limits = *lim;
    g_stop = 0;
    RootPly = rootPos->histCount;
    init_time(lim, rootPos->side, RootPly);
    tt_new_search();

    memset(res, 0, sizeof(*res));
    strcpy(res->bestmove, "0000");

    Move rootList[MAX_MOVES];
    int nRoot = gen_legal(rootPos, rootList);
    if (nRoot == 0) {
        res->score = rootPos->checkers ? -VALUE_MATE : 0;
        res->mate = rootPos->checkers ? 0 : 0;
        return;
    }

    for (int i = 0; i < g_threadCount; i++) {
        Thread *th = Threads[i];
        th->pos = *rootPos;
        th->pos.rootHist = th->pos.histCount;
        th->nodes = 0;
        th->rootMoveCount = nRoot;
        th->pvIdx = 0;
        for (int j = 0; j < nRoot; j++) {
            th->rootMoves[j].move = rootList[j];
            th->rootMoves[j].score = -VALUE_INFINITE;
            th->rootMoves[j].prevScore = -VALUE_INFINITE;
            th->rootMoves[j].seldepth = 0;
            th->rootMoves[j].pv[0] = rootList[j];
            th->rootMoves[j].pvLen = 1;
        }
    }

#ifdef _WIN32
    HANDLE handles[MAX_THREADS];
    for (int i = 1; i < g_threadCount; i++)
        handles[i] = CreateThread(NULL, 0, thread_entry, Threads[i], 0, NULL);
#else
    pthread_t handles[MAX_THREADS];
    for (int i = 1; i < g_threadCount; i++)
        pthread_create(&handles[i], NULL, thread_entry, Threads[i]);
#endif

#ifdef CE_TIME_DEBUG
    DbgChecks = DbgStopSetAt = DbgLastIterEnd = 0;
#endif
    thread_search(Threads[0]);
#ifdef CE_TIME_DEBUG
    if (MaximumTime > 0 && elapsed() > MaximumTime + 100)
        fprintf(stderr, "[ce] overshoot: elapsed=%lld opt=%lld max=%lld "
                        "checks=%lld stopSetAt=%lld lastIterEnd=%lld nodes=%lld\n",
                (long long)elapsed(), (long long)OptimumTime, (long long)MaximumTime,
                (long long)DbgChecks, (long long)DbgStopSetAt,
                (long long)DbgLastIterEnd, (long long)Threads[0]->nodes);
#endif
    g_stop = 1;

    for (int i = 1; i < g_threadCount; i++) {
#ifdef _WIN32
        if (handles[i]) { WaitForSingleObject(handles[i], INFINITE); CloseHandle(handles[i]); }
#else
        pthread_join(handles[i], NULL);
#endif
    }

    /* pick the thread with the deepest completed search, score as tie-break */
    Thread *best = Threads[0];
    for (int i = 1; i < g_threadCount; i++) {
        Thread *th = Threads[i];
        if (th->completedDepth == 0 || th->rootMoves[0].score == -VALUE_INFINITE)
            continue;
        int bScore = best->rootMoves[0].score, tScore = th->rootMoves[0].score;
        int bDepth = best->completedDepth, tDepth = th->completedDepth;
        if (tScore >= VALUE_MATE_IN_MAX_PLY && tScore > bScore) best = th;
        else if (bScore < VALUE_MATE_IN_MAX_PLY
                 && (tDepth > bDepth || (tDepth == bDepth && tScore > bScore)))
            best = th;
    }

    RootMove *rm = &best->rootMoves[0];
    if (rm->score == -VALUE_INFINITE) rm->score = 0;   /* stopped before depth 1 */
    move_to_uci(rm->move, res->bestmove);
    if (rm->pvLen > 1) move_to_uci(rm->pv[1], res->ponder);
    else strcpy(res->ponder, "");

    int64_t nodes = 0;
    for (int i = 0; i < g_threadCount; i++) nodes += Threads[i]->nodes;
    int64_t ms = elapsed();
    if (ms < 1) ms = 1;

    res->score = rm->score;
    if (rm->score >= VALUE_MATE_IN_MAX_PLY)
        res->mate = (VALUE_MATE - rm->score + 1) / 2;
    else if (rm->score <= VALUE_MATED_IN_MAX_PLY)
        res->mate = -(VALUE_MATE + rm->score) / 2;
    else
        res->mate = 0;
    res->depth = best->completedDepth;
    res->seldepth = rm->seldepth;
    res->nodes = nodes;
    res->timeMs = ms;
    res->nps = (int)(nodes * 1000 / ms);
    res->hashfull = tt_hashfull();
    res->optimumMs = OptimumTime;
    res->maximumMs = MaximumTime;

    int n = 0;
    res->pv[0] = 0;
    for (int i = 0; i < rm->pvLen && n < 1000; i++) {
        char mv[8];
        move_to_uci(rm->pv[i], mv);
        n += sprintf(res->pv + n, i ? " %s" : "%s", mv);
    }
}

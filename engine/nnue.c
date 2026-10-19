/*
 * nnue.c -- the evaluation network.
 *
 *     (768 -> 1024) x 2  ->  1,   SCReLU, one output layer per bucket
 *
 * Two perspectives.  The board is encoded twice, once from each player's
 * point of view, with piece colours relabelled own/enemy and the squares
 * flipped for Black, and the two halves are read own-side first.  Every
 * pattern is therefore learned once and applies to both colours, and the side
 * to move is structural rather than a single input bit.  (The network this
 * replaced had one shared view plus a turn bit, and could only express the
 * side to move as a constant: it carried a 450 cp offset and lost to the
 * hand-crafted evaluation.)
 *
 * SCReLU -- the clipped activation, squared -- lets the first layer express
 * conjunctions, which a plain rectifier cannot.
 *
 * Eight output layers chosen by piece count, so a queen endgame and a full
 * board do not have to share weights.
 *
 * ---------------------------------------------------------------------------
 * The accumulator
 *
 * Recomputing the first layer from scratch costs one pass over 1024 weights
 * per piece on the board, about thirty times what one move changes.  So the
 * accumulators are kept in a stack indexed by search ply and updated by
 * difference: a move touches at most three pieces, and what those are can be
 * read back out of the StateInfo the move left behind.
 *
 * Each entry carries the zobrist key of the position it belongs to.  That is
 * what makes the scheme safe without any cooperation from the rest of the
 * engine: the stack is thread-local and never explicitly invalidated, and any
 * entry whose key does not match the position in hand is simply rebuilt.  A
 * search that jumps around -- undoing moves, searching a sibling, evaluating
 * an unrelated position through the API -- cannot be given the wrong
 * accumulator, it can only cost a refresh.
 *
 * Define CE_NNUE_VERIFY to check every incremental result against a full
 * refresh; nnue/verify.py drives that over a few million positions.
 */
#include "ce.h"
#include <stdio.h>
#include <stdlib.h>

#if defined(__AVX2__)
#include <immintrin.h>
#endif

#define NN_L1      1024
#define NN_BUCKETS 8
#define NN_FEATURES 768          /* own/enemy x 6 types x 64 squares */

#if defined(_MSC_VER)
#define ALIGN64 __declspec(align(64))
#else
#define ALIGN64 __attribute__((aligned(64)))
#endif

static ALIGN64 int16_t FtWeight[NN_FEATURES][NN_L1];
static ALIGN64 int16_t FtBias[NN_L1];
static ALIGN64 int16_t OutWeight[NN_BUCKETS][2 * NN_L1];
static int32_t OutBias[NN_BUCKETS];

static int QA = 255, QB = 64;
static float NetScale = 400.0f;
static int NetLoaded = 0;

int nnue_available(void) { return NetLoaded; }

/* ------------------------------------------------------------------ */
/* loading                                                             */
/* ------------------------------------------------------------------ */

int nnue_load_file(const char *path) {
    NetLoaded = 0;
    FILE *f = fopen(path, "rb");
    if (!f) return 0;

    char magic[4];
    uint32_t version, features, l1, buckets;
    int32_t qa, qb;
    float scale;
    if (fread(magic, 1, 4, f) != 4 || memcmp(magic, "CEN2", 4)) { fclose(f); return 0; }
    if (fread(&version, 4, 1, f) != 1 || fread(&features, 4, 1, f) != 1
        || fread(&l1, 4, 1, f) != 1 || fread(&buckets, 4, 1, f) != 1
        || fread(&qa, 4, 1, f) != 1 || fread(&qb, 4, 1, f) != 1
        || fread(&scale, 4, 1, f) != 1) { fclose(f); return 0; }
    if (version != 2 || features != NN_FEATURES || l1 != NN_L1
        || buckets != NN_BUCKETS) { fclose(f); return 0; }

    QA = qa; QB = qb; NetScale = scale;
    size_t n = (size_t)NN_FEATURES * NN_L1;
    int ok = fread(FtWeight, sizeof(int16_t), n, f) == n
          && fread(FtBias, sizeof(int16_t), NN_L1, f) == NN_L1
          && fread(OutWeight, sizeof(int16_t), (size_t)NN_BUCKETS * 2 * NN_L1, f)
             == (size_t)NN_BUCKETS * 2 * NN_L1
          && fread(OutBias, sizeof(int32_t), NN_BUCKETS, f) == NN_BUCKETS;
    fclose(f);
    NetLoaded = ok;
    return ok;
}

/* ------------------------------------------------------------------ */
/* features                                                           */
/* ------------------------------------------------------------------ */

/* The index a piece occupies in the view of `persp`: own pieces in the first
 * 384 planes, and the board flipped when the viewer is Black. */
static inline int feat(int persp, int color, int pt, int sq) {
    return (color == persp ? 0 : 384) + pt * 64
         + (persp == WHITE ? sq : (sq ^ 56));
}

/* ------------------------------------------------------------------ */
/* the accumulator stack                                              */
/* ------------------------------------------------------------------ */

typedef struct {
    ALIGN64 int16_t v[COLOR_NB][NN_L1];
    U64 key;
    int set;
} Accum;

#define ACC_SLOTS (MAX_PLY + 10)
static __thread Accum AccStack[ACC_SLOTS];

static void acc_add(int16_t *a, const int16_t *w) {
#if defined(__AVX2__)
    for (int i = 0; i < NN_L1; i += 16) {
        __m256i x = _mm256_load_si256((const __m256i *)(a + i));
        __m256i y = _mm256_load_si256((const __m256i *)(w + i));
        _mm256_store_si256((__m256i *)(a + i), _mm256_add_epi16(x, y));
    }
#else
    for (int i = 0; i < NN_L1; i++) a[i] = (int16_t)(a[i] + w[i]);
#endif
}

static void acc_sub(int16_t *a, const int16_t *w) {
#if defined(__AVX2__)
    for (int i = 0; i < NN_L1; i += 16) {
        __m256i x = _mm256_load_si256((const __m256i *)(a + i));
        __m256i y = _mm256_load_si256((const __m256i *)(w + i));
        _mm256_store_si256((__m256i *)(a + i), _mm256_sub_epi16(x, y));
    }
#else
    for (int i = 0; i < NN_L1; i++) a[i] = (int16_t)(a[i] - w[i]);
#endif
}

static void acc_refresh(const Position *pos, Accum *acc) {
    for (int c = WHITE; c <= BLACK; c++)
        memcpy(acc->v[c], FtBias, sizeof(FtBias));
    U64 b = pos->occ;
    while (b) {
        int sq = pop_lsb(&b);
        int pc = pos->board[sq];
        int color = piece_color(pc), pt = piece_type(pc);
        acc_add(acc->v[WHITE], FtWeight[feat(WHITE, color, pt, sq)]);
        acc_add(acc->v[BLACK], FtWeight[feat(BLACK, color, pt, sq)]);
    }
    acc->key = pos->key;
    acc->set = 1;
}

/* What one move changed, as a list of features to add and remove. */
typedef struct {
    int add[2][2];     /* [n][colour, pt] with square in sq[] */
    int addSq[2], nAdd;
    int sub[3][2];
    int subSq[3], nSub;
} Delta;

/*
 * Recover the delta from the state the move left behind.  `pos` is the
 * position *after* the move, so the mover is the side not to move and the
 * piece that arrived is the one standing on the destination square (which for
 * a promotion is the new piece, not the pawn).
 */
static int move_delta(const Position *pos, Move m, int captured, Delta *d) {
    d->nAdd = d->nSub = 0;
    if (m == MOVE_NONE || m == MOVE_NULL) return 1;   /* nothing moved */

    int us = !pos->side, them = pos->side;
    int from = move_from(m), to = move_to(m);
    int type = move_type(m);

    if (type == MT_CASTLE) {
        int rookFrom, rookTo;
        if (to > from) { rookFrom = from + 3; rookTo = to - 1; }
        else           { rookFrom = from - 4; rookTo = to + 1; }
        d->sub[d->nSub][0] = us; d->sub[d->nSub][1] = KING;
        d->subSq[d->nSub++] = from;
        d->add[d->nAdd][0] = us; d->add[d->nAdd][1] = KING;
        d->addSq[d->nAdd++] = to;
        d->sub[d->nSub][0] = us; d->sub[d->nSub][1] = ROOK;
        d->subSq[d->nSub++] = rookFrom;
        d->add[d->nAdd][0] = us; d->add[d->nAdd][1] = ROOK;
        d->addSq[d->nAdd++] = rookTo;
        return 1;
    }

    int arrived = piece_type(pos->board[to]);
    int moved = (type == MT_PROMO) ? PAWN : arrived;

    d->sub[d->nSub][0] = us; d->sub[d->nSub][1] = moved;
    d->subSq[d->nSub++] = from;
    d->add[d->nAdd][0] = us; d->add[d->nAdd][1] = arrived;
    d->addSq[d->nAdd++] = to;

    if (captured != NO_PIECE) {
        int capSq = (type == MT_EP) ? to - (us == WHITE ? 8 : -8) : to;
        d->sub[d->nSub][0] = them; d->sub[d->nSub][1] = piece_type(captured);
        d->subSq[d->nSub++] = capSq;
    }
    return 1;
}

static void acc_apply(const Accum *src, Accum *dst, const Delta *d, U64 key) {
    if (dst != src) memcpy(dst->v, src->v, sizeof(src->v));
    for (int c = WHITE; c <= BLACK; c++) {
        for (int i = 0; i < d->nSub; i++)
            acc_sub(dst->v[c], FtWeight[feat(c, d->sub[i][0], d->sub[i][1], d->subSq[i])]);
        for (int i = 0; i < d->nAdd; i++)
            acc_add(dst->v[c], FtWeight[feat(c, d->add[i][0], d->add[i][1], d->addSq[i])]);
    }
    dst->key = key;
    dst->set = 1;
}

/*
 * Make an accumulator that describes `pos`, and return it.
 *
 * The one cheap case is a position one move below a position whose
 * accumulator is already right: the move that got here is in the StateInfo on
 * top of the stack, and the board in hand still has the arriving piece on its
 * destination square, which is the one thing the delta cannot be worked out
 * without.  Anything else -- a broken chain, a position handed in from
 * outside the search -- is rebuilt from scratch, which is correct whatever
 * the stack happens to hold.
 *
 * Going further back than one move would mean reconstructing intermediate
 * boards, so it is not done; the chain is unbroken down any line the search
 * actually walks, because the search evaluates as it descends.
 */
static Accum *acc_for(const Position *pos) {
    int ply = pos->histCount - pos->rootHist;
    if (ply < 0 || ply >= ACC_SLOTS) {
        static __thread Accum scratch;
        acc_refresh(pos, &scratch);
        return &scratch;
    }

    Accum *acc = &AccStack[ply];
    if (acc->set && acc->key == pos->key) return acc;

    if (ply > 0 && pos->histCount > 0) {
        const Accum *prev = &AccStack[ply - 1];
        if (prev->set && prev->key == pos->st[pos->histCount - 1].key) {
            const StateInfo *st = &pos->st[pos->histCount];
            Delta d;
            move_delta(pos, st->move, st->captured, &d);
            acc_apply(prev, acc, &d, pos->key);
#ifdef CE_NNUE_VERIFY
            {
                Accum check;
                acc_refresh(pos, &check);
                for (int c = WHITE; c <= BLACK; c++)
                    for (int i = 0; i < NN_L1; i++)
                        if (check.v[c][i] != acc->v[c][i]) {
                            char fen[128];
                            pos_get_fen(pos, fen);
                            fprintf(stderr, "[nnue] incremental mismatch at "
                                    "colour %d neuron %d: %d vs %d\n  %s\n",
                                    c, i, acc->v[c][i], check.v[c][i], fen);
                            abort();
                        }
            }
#endif
            return acc;
        }
    }

    acc_refresh(pos, acc);
    return acc;
}

/* ------------------------------------------------------------------ */
/* output layer                                                       */
/* ------------------------------------------------------------------ */

/*
 * SCReLU against one output layer.
 *
 * On integers the activation is x = clamp(acc, 0, QA) and the product is
 * x * x * w.  Taken directly that overflows an int32 accumulator, so the
 * multiplication is arranged as (x * w) * x: with QA = 255 and output weights
 * clipped to 127/QB during training, x * w stays inside an int16, and madd
 * then pairs the results into int32.  The two halves are accumulated
 * separately, which halves the number of terms per lane and leaves the sum a
 * factor of two inside its range even in the worst case the clips allow.
 */
static int64_t out_dot(const int16_t *us, const int16_t *them, int bucket) {
    const int16_t *w = OutWeight[bucket];
#if defined(__AVX2__)
    const __m256i zero = _mm256_setzero_si256();
    const __m256i qa = _mm256_set1_epi16((short)QA);
    int64_t total = 0;
    const int16_t *halves[2] = { us, them };
    for (int h = 0; h < 2; h++) {
        __m256i sum = _mm256_setzero_si256();
        const int16_t *a = halves[h];
        const int16_t *ww = w + h * NN_L1;
        for (int i = 0; i < NN_L1; i += 16) {
            __m256i x = _mm256_load_si256((const __m256i *)(a + i));
            x = _mm256_min_epi16(_mm256_max_epi16(x, zero), qa);
            __m256i wv = _mm256_load_si256((const __m256i *)(ww + i));
            sum = _mm256_add_epi32(sum,
                      _mm256_madd_epi16(_mm256_mullo_epi16(x, wv), x));
        }
        __m128i lo = _mm256_castsi256_si128(sum);
        __m128i hi = _mm256_extracti128_si256(sum, 1);
        __m128i s = _mm_add_epi32(lo, hi);
        int32_t tmp[4];
        _mm_storeu_si128((__m128i *)tmp, s);
        total += (int64_t)tmp[0] + tmp[1] + tmp[2] + tmp[3];
    }
    return total;
#else
    int64_t total = 0;
    for (int i = 0; i < NN_L1; i++) {
        int x = us[i] < 0 ? 0 : (us[i] > QA ? QA : us[i]);
        total += (int64_t)x * x * w[i];
    }
    for (int i = 0; i < NN_L1; i++) {
        int x = them[i] < 0 ? 0 : (them[i] > QA ? QA : them[i]);
        total += (int64_t)x * x * w[NN_L1 + i];
    }
    return total;
#endif
}

static inline int out_bucket(const Position *pos) {
    int n = popcount(pos->occ);
    int b = (n - 2) * NN_BUCKETS / 31;
    return b < 0 ? 0 : (b >= NN_BUCKETS ? NN_BUCKETS - 1 : b);
}

int nnue_evaluate(const Position *pos) {
    if (!NetLoaded) return 0;

    Accum *acc = acc_for(pos);
    int bucket = out_bucket(pos);
    int64_t sum = out_dot(acc->v[pos->side], acc->v[!pos->side], bucket);

    /* sum is QA*QA*QB times the real dot product; folding one QA out first
     * keeps the bias on the same footing as the stored integer. */
    int64_t v = sum / QA + OutBias[bucket];
    double cp = (double)v * NetScale / ((double)QA * QB);

    /* The search's margins are tuned against the hand-crafted evaluation's
     * units, which are about twice a centipawn.  NnueScale puts the network
     * on the same footing so that the two are interchangeable. */
    int out = (int)(cp * NnueScale / 100.0);
    if (out > VALUE_TB_WIN - 1) out = VALUE_TB_WIN - 1;
    if (out < -(VALUE_TB_WIN - 1)) out = -(VALUE_TB_WIN - 1);
    return out;
}

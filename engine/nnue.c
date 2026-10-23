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

/*
 * Two feature sets are supported, chosen by the file's magic.
 *
 *   CEN2:  768 -> L1, one copy of the planes, no mirroring
 *   CEN3:  king buckets.  The board is folded onto files a-d by the
 *          perspective's own king file, and each king region indexes its own
 *          copy of the 768 planes.
 *
 * They share one code path with the bucket count, the mirror flag and L1 read
 * from the header, because the older format is exactly the newer one with a
 * single bucket and no fold.  That keeps one accumulator implementation --
 * which is the part that is easy to get subtly wrong -- rather than two.
 */
#define NN_MAX_L1       1536
#define NN_MAX_KBUCKETS 4
#define NN_BUCKETS 8
#define NN_PLANES  768           /* own/enemy x 6 types x 64 squares */
#define NN_MAX_FEATURES (NN_MAX_KBUCKETS * NN_PLANES)

/* Runtime shape, set by the loader. */
static int NetL1 = NN_MAX_L1;
static int NetKBuckets = 1;
static int NetMirror = 0;

/*
 * King-bucket map, over the perspective's own king square in its own relative
 * orientation, with the file already folded.  This table and the one in
 * nnue/arch.py have to agree exactly or the engine evaluates a different
 * network from the one that was trained; the split is by where kings actually
 * stand rather than by board symmetry (30% castled, 30% never castled, the
 * rest off the back rank).  See arch.py for the measurement.
 */
static const uint8_t KingBucket[64] = {
    2, 0, 2, 1, 1, 2, 0, 2,
    2, 2, 2, 2, 2, 2, 2, 2,
    3, 3, 3, 3, 3, 3, 3, 3,
    3, 3, 3, 3, 3, 3, 3, 3,
    3, 3, 3, 3, 3, 3, 3, 3,
    3, 3, 3, 3, 3, 3, 3, 3,
    3, 3, 3, 3, 3, 3, 3, 3,
    3, 3, 3, 3, 3, 3, 3, 3,
};

#if defined(_MSC_VER)
#define ALIGN64 __declspec(align(64))
#else
#define ALIGN64 __attribute__((aligned(64)))
#endif

static ALIGN64 int16_t FtWeight[NN_MAX_FEATURES][NN_MAX_L1];
static ALIGN64 int16_t FtBias[NN_MAX_L1];
static ALIGN64 int16_t OutWeight[NN_BUCKETS][2 * NN_MAX_L1];
static int32_t OutBias[NN_BUCKETS];

static int QA = 255, QB = 64;
static float NetScale = 400.0f;
static int NetLoaded = 0;
/* Bumped by every successful load.  The refresh cache is thread-local and
 * cannot be reached from here to be cleared, so each of its entries records
 * the generation it was built under instead; a load invalidates all of them at
 * once, on every thread, without touching any of them. */
static unsigned NetGen = 0;

int nnue_available(void) { return NetLoaded; }

/* ------------------------------------------------------------------ */
/* loading                                                             */
/* ------------------------------------------------------------------ */

/* Read `rows` x `cols` int16 into an array whose row stride is `stride`. */
static int read_rows(FILE *f, int16_t *dst, size_t rows, size_t cols,
                     size_t stride) {
    for (size_t r = 0; r < rows; r++)
        if (fread(dst + r * stride, sizeof(int16_t), cols, f) != cols)
            return 0;
    return 1;
}

int nnue_load_file(const char *path) {
    NetLoaded = 0;
    FILE *f = fopen(path, "rb");
    if (!f) return 0;

    char magic[4];
    uint32_t version, planes, kbuckets = 1, l1, buckets;
    int32_t qa, qb;
    float scale;
    if (fread(magic, 1, 4, f) != 4) { fclose(f); return 0; }

    int v3 = !memcmp(magic, "CEN3", 4);
    if (!v3 && memcmp(magic, "CEN2", 4)) { fclose(f); return 0; }

    if (fread(&version, 4, 1, f) != 1 || fread(&planes, 4, 1, f) != 1
        || (v3 && fread(&kbuckets, 4, 1, f) != 1)
        || fread(&l1, 4, 1, f) != 1 || fread(&buckets, 4, 1, f) != 1
        || fread(&qa, 4, 1, f) != 1 || fread(&qb, 4, 1, f) != 1
        || fread(&scale, 4, 1, f) != 1) { fclose(f); return 0; }

    if (version != (v3 ? 3u : 2u) || planes != NN_PLANES
        || buckets != NN_BUCKETS
        || l1 == 0 || l1 > NN_MAX_L1 || (l1 % 16) != 0
        || kbuckets == 0 || kbuckets > NN_MAX_KBUCKETS) { fclose(f); return 0; }

    QA = qa; QB = qb; NetScale = scale;
    NetL1 = (int)l1;
    NetKBuckets = (int)kbuckets;
    NetMirror = v3;                /* the fold came in with the king buckets */

    size_t rows = (size_t)kbuckets * NN_PLANES;
    int ok = read_rows(f, &FtWeight[0][0], rows, l1, NN_MAX_L1)
          && fread(FtBias, sizeof(int16_t), l1, f) == l1
          && read_rows(f, &OutWeight[0][0], NN_BUCKETS, 2 * (size_t)l1,
                       2 * NN_MAX_L1)
          && fread(OutBias, sizeof(int32_t), NN_BUCKETS, f) == NN_BUCKETS;
    fclose(f);
    NetLoaded = ok;
    if (ok) NetGen++;
    return ok;
}

/* ------------------------------------------------------------------ */
/* features                                                           */
/* ------------------------------------------------------------------ */

/*
 * How one perspective sees the board: which copy of the 768 planes it indexes
 * and whether the files are folded.  Both are decided by that perspective's
 * own king and change only when it moves.
 */
typedef struct { int bucket; int mirror; } KingView;

static inline KingView king_view(const Position *pos, int persp) {
    KingView kv = { 0, 0 };
    if (NetKBuckets == 1 && !NetMirror) return kv;
    int ksq = lsb(pieces_cp(pos, persp, KING));
    int rel = persp == WHITE ? ksq : (ksq ^ 56);
    kv.mirror = NetMirror && ((rel & 7) >= 4);
    kv.bucket = KingBucket[kv.mirror ? (rel ^ 7) : rel];
    if (kv.bucket >= NetKBuckets) kv.bucket = NetKBuckets - 1;
    return kv;
}

/* The index a piece occupies in the view of `persp`: own pieces in the first
 * 384 planes, the board flipped when the viewer is Black, folded onto files
 * a-d when the viewer's king sits on the right, in the viewer's king bucket. */
static inline int feat(int persp, KingView kv, int color, int pt, int sq) {
    int s = persp == WHITE ? sq : (sq ^ 56);
    if (kv.mirror) s ^= 7;
    return kv.bucket * NN_PLANES + (color == persp ? 0 : 384) + pt * 64 + s;
}

/* ------------------------------------------------------------------ */
/* the accumulator stack                                              */
/* ------------------------------------------------------------------ */

typedef struct {
    ALIGN64 int16_t v[COLOR_NB][NN_MAX_L1];
    U64 key;
    int set;
    /* Which view each half was built in.  A king move can change its own
     * side's bucket or fold, and when it does that half has to be rebuilt
     * rather than updated -- every one of its features moved at once. */
    KingView kv[COLOR_NB];
} Accum;

#define ACC_SLOTS (MAX_PLY + 10)
/* the furthest back the chain may be replayed from; see acc_for */
#define ACC_WALK_MAX 8
static __thread Accum AccStack[ACC_SLOTS];

/*
 * One feature, one pass.
 *
 * Fusing several features into a single traversal was tried and is slower:
 * -4% in `nnue` and -2.7% in `blend`.  It saves read-modify-write traffic on
 * the accumulator, which is 2 KB and stays in L1, and pays for it by touching
 * every weight row in a strided interleave instead of streaming each one --
 * and the rows are 2 KB apart in a 6 MB table, so they are what the memory
 * system is actually working on.  Reading fewer rows is what pays here; the
 * refresh cache does that, and this does not.
 */
static void acc_add(int16_t *a, const int16_t *w) {
    int n = NetL1;
#if defined(__AVX2__)
    for (int i = 0; i < n; i += 16) {
        __m256i x = _mm256_load_si256((const __m256i *)(a + i));
        __m256i y = _mm256_load_si256((const __m256i *)(w + i));
        _mm256_store_si256((__m256i *)(a + i), _mm256_add_epi16(x, y));
    }
#else
    for (int i = 0; i < n; i++) a[i] = (int16_t)(a[i] + w[i]);
#endif
}

static void acc_sub(int16_t *a, const int16_t *w) {
    int n = NetL1;
#if defined(__AVX2__)
    for (int i = 0; i < n; i += 16) {
        __m256i x = _mm256_load_si256((const __m256i *)(a + i));
        __m256i y = _mm256_load_si256((const __m256i *)(w + i));
        _mm256_store_si256((__m256i *)(a + i), _mm256_sub_epi16(x, y));
    }
#else
    for (int i = 0; i < n; i++) a[i] = (int16_t)(a[i] - w[i]);
#endif
}

/*
 * The refresh cache.
 *
 * A rebuild from scratch costs one pass over L1 per piece on the board -- some
 * 23 of them -- and it is not the rare event the incremental scheme implies.
 * It happens whenever the parent position's accumulator is not the one sitting
 * in the slot below, and the search arranges for that constantly: a node that
 * takes its static evaluation from the transposition table never computes an
 * accumulator at all, so its children find a stale slot.  Measured over a
 * depth-13 bench, 46% of all evaluations rebuilt both halves, and the rebuild
 * path accounted for roughly 85% of the accumulator's total work.
 *
 * So each view keeps the last accumulator built in it, together with the
 * board that accumulator belongs to.  A rebuild then only has to pay for the
 * squares that differ from that board, which over the same bench is 4.6
 * features against 22.6 -- the pieces move, but most of them are still where
 * they were when this view was last needed.
 *
 * One entry per (perspective, king bucket, fold), which is the exactly the key
 * that `feat()` depends on: every feature in one entry is indexed the same
 * way, so a diff against it is meaningful.  Thread-local, like the stack, and
 * stamped with the network's load generation so that loading a different net
 * -- which may have a different L1 and certainly has different weights --
 * cannot leave a usable-looking entry behind.
 */
typedef struct {
    ALIGN64 int16_t v[NN_MAX_L1];
    U64 bb[COLOR_NB][PIECE_TYPE_NB];   /* the board v was built from */
    unsigned gen;                      /* 0 = never built */
} RefreshEntry;

static __thread RefreshEntry RefreshCache[COLOR_NB][NN_MAX_KBUCKETS][2];

/* Rebuild one perspective's half, via the refresh cache. */
static void acc_build_side(const Position *pos, Accum *acc, int c, KingView kv) {
    RefreshEntry *e = &RefreshCache[c][kv.bucket][kv.mirror];

    if (e->gen != NetGen) {
        /* Cold: pay for the full build once, then this view is primed. */
        memcpy(e->v, FtBias, (size_t)NetL1 * sizeof(int16_t));
        memset(e->bb, 0, sizeof(e->bb));
        e->gen = NetGen;
    }

    for (int col = WHITE; col <= BLACK; col++)
        for (int pt = PAWN; pt <= KING; pt++) {
            U64 cur = pieces_cp(pos, col, pt);
            U64 old = e->bb[col][pt];
            if (cur == old) continue;
            U64 rem = old & ~cur, add = cur & ~old;
            while (rem) acc_sub(e->v, FtWeight[feat(c, kv, col, pt, pop_lsb(&rem))]);
            while (add) acc_add(e->v, FtWeight[feat(c, kv, col, pt, pop_lsb(&add))]);
            e->bb[col][pt] = cur;
        }

    memcpy(acc->v[c], e->v, (size_t)NetL1 * sizeof(int16_t));
    acc->kv[c] = kv;

#ifdef CE_NNUE_VERIFY
    /*
     * The cache has to be checked against a build that does not use it.
     * Checking the incremental path against a *cached* refresh would not do
     * it: the chain's root is itself a cached build, so an error in the cache
     * propagates into the incremental result and the two agree while both are
     * wrong.  This is the only place the from-scratch sum still exists.
     */
    {
        ALIGN64 int16_t truth[NN_MAX_L1];
        memcpy(truth, FtBias, (size_t)NetL1 * sizeof(int16_t));
        U64 b = pos->occ;
        while (b) {
            int sq = pop_lsb(&b);
            int pc = pos->board[sq];
            acc_add(truth, FtWeight[feat(c, kv, piece_color(pc),
                                         piece_type(pc), sq)]);
        }
        for (int i = 0; i < NetL1; i++)
            if (truth[i] != acc->v[c][i]) {
                char fen[128];
                pos_get_fen(pos, fen);
                fprintf(stderr, "[nnue] refresh cache mismatch at colour %d "
                        "neuron %d (bucket %d mirror %d): %d vs %d\n  %s\n",
                        c, i, kv.bucket, kv.mirror, acc->v[c][i], truth[i], fen);
                abort();
            }
    }
#endif
}

static void acc_refresh(const Position *pos, Accum *acc) {
    for (int c = WHITE; c <= BLACK; c++)
        acc_build_side(pos, acc, c, king_view(pos, c));
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
static int move_delta_bd(const uint8_t *board, int mover, Move m, int captured,
                         Delta *d) {
    d->nAdd = d->nSub = 0;
    if (m == MOVE_NONE || m == MOVE_NULL) return 1;   /* nothing moved */

    int us = mover, them = !mover;
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

    int arrived = piece_type(board[to]);
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

static int move_delta(const Position *pos, Move m, int captured, Delta *d) {
    return move_delta_bd(pos->board, !pos->side, m, captured, d);
}

/*
 * Undo `m` on a copy of the board.
 *
 * This is what lets the chain be replayed from further back than one move
 * without storing anything at make-move time: the board in hand describes the
 * position after the last move, and stepping it backwards -- using the move
 * and the piece it captured, both of which the StateInfo already keeps --
 * reconstructs each earlier board in turn.  Only the 64-byte board is needed,
 * because that is all a feature index depends on.
 */
static void board_undo(uint8_t *bd, int mover, Move m, int captured) {
    if (m == MOVE_NONE || m == MOVE_NULL) return;
    int from = move_from(m), to = move_to(m), type = move_type(m);

    if (type == MT_CASTLE) {
        int rookFrom, rookTo;
        if (to > from) { rookFrom = from + 3; rookTo = to - 1; }
        else           { rookFrom = from - 4; rookTo = to + 1; }
        bd[to] = NO_PIECE;
        bd[rookTo] = NO_PIECE;
        bd[from] = (uint8_t)make_piece(mover, KING);
        bd[rookFrom] = (uint8_t)make_piece(mover, ROOK);
        return;
    }

    bd[from] = (type == MT_PROMO) ? (uint8_t)make_piece(mover, PAWN) : bd[to];
    bd[to] = NO_PIECE;
    if (captured != NO_PIECE) {
        int capSq = (type == MT_EP) ? to - (mover == WHITE ? 8 : -8) : to;
        bd[capSq] = (uint8_t)captured;
    }
}

/*
 * Carry one half forward across a move.
 *
 * Only valid while that perspective's king view is unchanged.  When the king
 * itself moved and crossed a bucket boundary or the fold line, every feature
 * in that half moved with it and the caller rebuilds instead -- but only that
 * half: from the other side's point of view a king move is an ordinary move
 * and its own view is untouched, so it still updates by difference.
 */
static void acc_apply_side(const Accum *src, Accum *dst, int c,
                           const Delta *d, KingView kv) {
    if (dst != src)
        memcpy(dst->v[c], src->v[c], (size_t)NetL1 * sizeof(int16_t));
    for (int i = 0; i < d->nSub; i++)
        acc_sub(dst->v[c], FtWeight[feat(c, kv, d->sub[i][0], d->sub[i][1],
                                         d->subSq[i])]);
    for (int i = 0; i < d->nAdd; i++)
        acc_add(dst->v[c], FtWeight[feat(c, kv, d->add[i][0], d->add[i][1],
                                         d->addSq[i])]);
    dst->kv[c] = kv;
}

static void acc_apply(const Position *pos, const Accum *src, Accum *dst,
                      const Delta *d, U64 key) {
    for (int c = WHITE; c <= BLACK; c++) {
        KingView kv = king_view(pos, c);
        if (kv.bucket != src->kv[c].bucket || kv.mirror != src->kv[c].mirror)
            acc_build_side(pos, dst, c, kv);
        else
            acc_apply_side(src, dst, c, d, kv);
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
            acc_apply(pos, prev, acc, &d, pos->key);
#ifdef CE_NNUE_VERIFY
            {
                Accum check;
                acc_refresh(pos, &check);
                for (int c = WHITE; c <= BLACK; c++)
                    for (int i = 0; i < NetL1; i++)
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

    /*
     * Nothing one move back.  Try further: replay several moves forward from
     * the nearest ancestor that is still valid, reconstructing the boards in
     * between by stepping backwards from the one in hand.
     *
     * The cap matters more than it looks.  Replaying a move reads about 2.2
     * weight rows, and since the refresh cache went in a rebuild reads 4.6, so
     * the walk only pays as far back as two plies and is a loss beyond three.
     * NnueWalkBack = 1 is the one-move case handled above and nothing more.
     */
    if (NnueWalkBack > 1 && ply > 1 && pos->histCount > 1) {
        int cap = NnueWalkBack < ACC_WALK_MAX ? NnueWalkBack : ACC_WALK_MAX;
        int k = 0;
        for (int j = 2; j <= cap && ply - j >= 0 && pos->histCount - j >= 0; j++) {
            const Accum *a = &AccStack[ply - j];
            if (a->set && a->key == pos->st[pos->histCount - j].key) { k = j; break; }
        }

        if (k >= 2) {
            uint8_t bd[64];
            Delta d[ACC_WALK_MAX];
            int movers[ACC_WALK_MAX];
            int ok = 1;
            memcpy(bd, pos->board, 64);

            for (int j = 0; j < k; j++) {
                const StateInfo *st = &pos->st[pos->histCount - j];
                Move m = st->move;
                if (m == MOVE_NONE || m == MOVE_NULL) {
                    d[j].nAdd = d[j].nSub = 0;
                    movers[j] = WHITE;              /* unused */
                    continue;
                }
                int pc = bd[move_to(m)];
                /*
                 * A king move anywhere in the span can change that side's
                 * bucket or fold, and this path has no intermediate board to
                 * rebuild a half from -- so give up and let the cache do it.
                 * The mover is read off the board rather than inferred from
                 * the ply's parity, which a null move in the span would break.
                 */
                if (pc == NO_PIECE || piece_type(pc) == KING
                    || move_type(m) == MT_CASTLE) { ok = 0; break; }
                movers[j] = piece_color(pc);
                move_delta_bd(bd, movers[j], m, st->captured, &d[j]);
                board_undo(bd, movers[j], m, st->captured);
            }

            KingView kv[COLOR_NB];
            for (int c = WHITE; c <= BLACK; c++) kv[c] = king_view(pos, c);

            /* No king moved in the span, so every position in it shares the
             * final view -- but the ancestor still has to have been built in
             * it, which it need not have been. */
            if (ok) {
                const Accum *src = &AccStack[ply - k];
                for (int c = WHITE; c <= BLACK; c++)
                    if (src->kv[c].bucket != kv[c].bucket
                        || src->kv[c].mirror != kv[c].mirror) { ok = 0; break; }
            }

            if (ok) {
                const Accum *src = &AccStack[ply - k];
                for (int j = k - 1; j >= 0; j--) {
                    Accum *dst = &AccStack[ply - j];
                    for (int c = WHITE; c <= BLACK; c++)
                        acc_apply_side(src, dst, c, &d[j], kv[c]);
                    dst->key = pos->st[pos->histCount - j].key;
                    dst->set = 1;
                    src = dst;
                }
#ifdef CE_NNUE_VERIFY
                {
                    Accum check;
                    acc_refresh(pos, &check);
                    for (int c = WHITE; c <= BLACK; c++)
                        for (int i = 0; i < NetL1; i++)
                            if (check.v[c][i] != acc->v[c][i]) {
                                char fen[128];
                                pos_get_fen(pos, fen);
                                fprintf(stderr, "[nnue] walk-back mismatch at "
                                        "colour %d neuron %d (k=%d): %d vs %d\n"
                                        "  %s\n", c, i, k, acc->v[c][i],
                                        check.v[c][i], fen);
                                abort();
                            }
                }
#endif
                return acc;
            }
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
    const int n = NetL1;
#if defined(__AVX2__)
    const __m256i zero = _mm256_setzero_si256();
    const __m256i qa = _mm256_set1_epi16((short)QA);
    int64_t total = 0;
    const int16_t *halves[2] = { us, them };
    for (int h = 0; h < 2; h++) {
        __m256i sum = _mm256_setzero_si256();
        const int16_t *a = halves[h];
        const int16_t *ww = w + h * n;
        for (int i = 0; i < n; i += 16) {
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
    for (int i = 0; i < n; i++) {
        int x = us[i] < 0 ? 0 : (us[i] > QA ? QA : us[i]);
        total += (int64_t)x * x * w[i];
    }
    for (int i = 0; i < n; i++) {
        int x = them[i] < 0 ? 0 : (them[i] > QA ? QA : them[i]);
        total += (int64_t)x * x * w[n + i];
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

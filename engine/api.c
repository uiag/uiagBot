/*
 * api.c -- C API exported to the Python side (and used by the UCI front-end).
 */
#include "ce.h"
#include <stdio.h>
#include <stdlib.h>
#include <math.h>

#ifdef _WIN32
#include <windows.h>
#else
#include <pthread.h>
#include <sched.h>
#include <time.h>
#endif

static Position g_pos;
static int g_initialised = 0;
static int g_hashMb = 64;

#define START_FEN "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

CE_EXPORT const char *ce_version(void) { return "ce 1.1"; }

CE_EXPORT int ce_set_param(const char *name, int value) {
    return tune_set(name, value);
}

CE_EXPORT int ce_get_param(const char *name, int *out) {
    return tune_get(name, out);
}

CE_EXPORT int ce_init(const char *netPath, int hashMb, int threads) {
    bb_init();
    pos_init_zobrist();
    tune_init();
    eval_init();
    search_init();

    if (hashMb > 0) g_hashMb = hashMb;
    tt_init(g_hashMb);
    search_setup_threads(threads > 0 ? threads : 1);

    int netOk = 0;
    if (netPath && netPath[0]) netOk = nnue_load_file(netPath);

    pos_set_fen(&g_pos, START_FEN);
    g_initialised = 1;
    return netOk;
}

CE_EXPORT void ce_set_hash(int mb) {
    g_hashMb = mb > 0 ? mb : 1;
    tt_init(g_hashMb);
}

CE_EXPORT void ce_set_threads(int n) { search_setup_threads(n); }

CE_EXPORT void ce_set_eval_mode(int mode) {
    if (mode == EVAL_NNUE && !nnue_available()) mode = EVAL_HCE;
    if (mode == EVAL_BLEND && !nnue_available()) mode = EVAL_HCE;
    g_evalMode = mode;
}

CE_EXPORT int ce_get_eval_mode(void) { return g_evalMode; }
CE_EXPORT int ce_nnue_available(void) { return nnue_available(); }
CE_EXPORT void ce_set_show_info(int on) { g_showInfo = on ? 1 : 0; }
CE_EXPORT void ce_set_multipv(int n) { g_multiPV = n > 0 ? n : 1; }

CE_EXPORT void ce_new_game(void) {
    if (!g_initialised) ce_init(NULL, g_hashMb, g_threadCount);
    search_new_game();
    pos_set_fen(&g_pos, START_FEN);
}

CE_EXPORT void ce_stop(void) { g_stop = 1; }

/*
 * Set the root position.  `moves` is a space separated list of UCI moves
 * applied to `fen`; passing the real move history lets the engine detect
 * repetitions correctly.
 */
CE_EXPORT int ce_set_position(const char *fen, const char *moves) {
    if (!g_initialised) ce_init(NULL, g_hashMb, g_threadCount);
    if (!pos_set_fen(&g_pos, fen && fen[0] ? fen : START_FEN)) return 0;

    if (!moves || !moves[0]) { g_pos.rootHist = g_pos.histCount; return 1; }

    const char *p = moves;
    char token[8];
    while (*p) {
        while (*p == ' ') p++;
        int n = 0;
        while (*p && *p != ' ' && n < 7) token[n++] = *p++;
        token[n] = 0;
        if (!n) break;
        Move m = move_from_uci(&g_pos, token);
        if (m == MOVE_NONE) return 0;
        if (g_pos.histCount >= MAX_HIST - MAX_PLY - 8) {
            /* keep the tail of a very long game: drop history before the
             * current position (it can no longer matter for repetitions) */
            StateInfo cur = g_pos.st[g_pos.histCount];
            g_pos.st[0] = cur;
            g_pos.histCount = 0;
        }
        pos_do_move(&g_pos, m);
    }
    g_pos.rootHist = g_pos.histCount;
    return 1;
}

CE_EXPORT void ce_get_fen(char *out) { pos_get_fen(&g_pos, out); }

CE_EXPORT int ce_eval(void) { return eval_position(&g_pos); }

CE_EXPORT int ce_legal_moves(char *out, int cap) {
    Move list[MAX_MOVES];
    int n = gen_legal(&g_pos, list);
    int w = 0;
    out[0] = 0;
    for (int i = 0; i < n; i++) {
        char mv[8];
        move_to_uci(list[i], mv);
        int len = (int)strlen(mv);
        if (w + len + 2 >= cap) break;
        if (w) out[w++] = ' ';
        memcpy(out + w, mv, len);
        w += len;
        out[w] = 0;
    }
    return n;
}

static U64 perft_rec(Position *pos, int depth) {
    Move list[MAX_MOVES];
    int n = gen_legal(pos, list);
    if (depth <= 1) return (U64)n;
    U64 nodes = 0;
    for (int i = 0; i < n; i++) {
        pos_do_move(pos, list[i]);
        nodes += perft_rec(pos, depth - 1);
        pos_undo_move(pos, list[i]);
    }
    return nodes;
}

CE_EXPORT unsigned long long ce_perft(int depth) {
    if (depth < 1) return 1;
    return (unsigned long long)perft_rec(&g_pos, depth);
}

CE_EXPORT void ce_search(long long wtime, long long btime, long long winc, long long binc,
                         long long movetime, int movestogo, int depth, long long nodes,
                         int infinite, SearchResult *out) {
    if (!g_initialised) ce_init(NULL, g_hashMb, g_threadCount);

    SearchLimits lim;
    memset(&lim, 0, sizeof(lim));
    lim.wtime = wtime;
    lim.btime = btime;
    lim.winc = winc;
    lim.binc = binc;
    lim.movetime = movetime;
    lim.movestogo = movestogo;
    lim.depth = depth;
    lim.nodes = nodes;
    lim.infinite = infinite;

    if (!wtime && !btime && !movetime && !depth && !nodes && !infinite)
        lim.movetime = 1000;

    search_go(&g_pos, &lim, out);
}

/* ------------------------------------------------------------------ */
/* searching in the background (pondering)                             */
/* ------------------------------------------------------------------ */

/*
 * The engine spends roughly half of every game waiting for the opponent.
 * `ce_search_start` runs a search on its own thread so that the caller can go
 * on talking -- which is what pondering needs, and also what the UCI
 * front-end needs for `stop` and `ponderhit` to be readable at all: `go` used
 * to block the command loop, so `go infinite` could never be interrupted.
 *
 * The usual sequence is: play a move, then start an infinite search on the
 * position that follows the move we expect the opponent to make.  If they
 * play it, `ce_search_ponderhit` hands that search the clock and it continues
 * from the depth it has already reached.  If they play anything else,
 * `ce_search_abort` throws it away -- and the transposition table keeps
 * whatever of it was useful.
 */

static Position g_searchPos;          /* the root, copied before the thread starts */
static SearchLimits g_searchLim;
static SearchResult g_searchRes;
static volatile int g_searchRunning = 0;
static volatile int g_searchFinished = 0;
static volatile int g_searchSuppress = 0;
static void (*g_bestmoveCb)(const SearchResult *) = NULL;

/*
 * Who announces the move.
 *
 * UCI requires exactly one `bestmove` for every `go`, whether the search ran
 * out of time or was cut short by `stop`.  The command loop cannot do it,
 * because it is blocked reading the next command when a search ends by
 * itself, so the search thread announces its own result through this callback
 * and there is exactly one place that prints.
 *
 * A ponder miss is the one case with no announcement to make: the search is
 * thrown away rather than answered, so `ce_search_abort` silences it.
 */
CE_EXPORT void ce_set_bestmove_callback(void (*cb)(const SearchResult *)) {
    g_bestmoveCb = cb;
}

#ifdef _WIN32
static HANDLE g_searchThread = NULL;
static DWORD WINAPI search_thread_entry(LPVOID arg) {
#else
static pthread_t g_searchThread;
static int g_searchThreadValid = 0;
static void *search_thread_entry(void *arg) {
#endif
    (void)arg;
    search_go(&g_searchPos, &g_searchLim, &g_searchRes);
    g_searchFinished = 1;
    void (*cb)(const SearchResult *) = g_bestmoveCb;
    int suppress = g_searchSuppress;
    g_searchRunning = 0;
    if (cb && !suppress) cb(&g_searchRes);
#ifdef _WIN32
    return 0;
#else
    return NULL;
#endif
}

static void search_thread_join(void) {
#ifdef _WIN32
    if (g_searchThread) {
        WaitForSingleObject(g_searchThread, INFINITE);
        CloseHandle(g_searchThread);
        g_searchThread = NULL;
    }
#else
    if (g_searchThreadValid) {
        pthread_join(g_searchThread, NULL);
        g_searchThreadValid = 0;
    }
#endif
}

CE_EXPORT int ce_search_start(long long wtime, long long btime, long long winc,
                              long long binc, long long movetime, int movestogo,
                              int depth, long long nodes, int infinite) {
    if (!g_initialised) ce_init(NULL, g_hashMb, g_threadCount);
    if (g_searchRunning) return 0;
    search_thread_join();            /* reap a finished one */

    memset(&g_searchLim, 0, sizeof(g_searchLim));
    g_searchLim.wtime = wtime;
    g_searchLim.btime = btime;
    g_searchLim.winc = winc;
    g_searchLim.binc = binc;
    g_searchLim.movetime = movetime;
    g_searchLim.movestogo = movestogo;
    g_searchLim.depth = depth;
    g_searchLim.nodes = nodes;
    g_searchLim.infinite = infinite;
    if (!wtime && !btime && !movetime && !depth && !nodes && !infinite)
        g_searchLim.movetime = 1000;

    /* Copy the root here rather than letting the thread read g_pos: the
     * caller is free to set up the next position the moment this returns. */
    g_searchPos = g_pos;
    memset(&g_searchRes, 0, sizeof(g_searchRes));
    strcpy(g_searchRes.bestmove, "0000");
    g_searchFinished = 0;
    g_searchSuppress = 0;
    g_searchStarted = 0;
    g_searchRunning = 1;

#ifdef _WIN32
    g_searchThread = CreateThread(NULL, 0, search_thread_entry, NULL, 0, NULL);
    if (!g_searchThread) { g_searchRunning = 0; return 0; }
#else
    if (pthread_create(&g_searchThread, NULL, search_thread_entry, NULL)) {
        g_searchRunning = 0;
        return 0;
    }
    g_searchThreadValid = 1;
#endif

    /*
     * Do not return until the search has taken ownership of the limits.
     *
     * `search_go` copies the limits and computes the clock itself, on the
     * search thread.  Returning before that happens leaves a window in which
     * a ponder hit writes the real clock and the search then overwrites it
     * with the infinite limits it was started with -- after which nothing
     * ever stops it.  The caller is free to hand a hit to a search the
     * instant this returns (a move arriving quickly is the normal case, not
     * an unusual one), so the window has to be closed here rather than
     * hoped over.
     */
    for (int spin = 0; !g_searchStarted && g_searchRunning; spin++) {
        /* It takes a few microseconds, so spin briefly; but fall back to a
         * real sleep rather than risk holding the core the search thread
         * needs on a loaded machine. */
#ifdef _WIN32
        if (spin < 10000) Sleep(0); else Sleep(1);
#else
        if (spin < 10000) sched_yield();
        else { struct timespec ts = {0, 1000000}; nanosleep(&ts, NULL); }
#endif
    }
    return 1;
}

CE_EXPORT int ce_search_running(void) { return g_searchRunning; }
CE_EXPORT int ce_search_finished(void) { return g_searchFinished; }

CE_EXPORT void ce_search_ponderhit(long long wtime, long long btime,
                                   long long winc, long long binc,
                                   long long movetime, int movestogo) {
    if (!g_searchRunning) return;
    SearchLimits lim;
    memset(&lim, 0, sizeof(lim));
    lim.wtime = wtime;
    lim.btime = btime;
    lim.winc = winc;
    lim.binc = binc;
    lim.movetime = movetime;
    lim.movestogo = movestogo;
    search_ponderhit(&lim);
}

/*
 * Wait for the running search and copy out its result.
 *
 * `search_go` clears g_stop when it starts, so a stop raised between the
 * thread being created and the search beginning would be lost and an infinite
 * search would never return.  Rather than synchronise the two, the stop is
 * simply re-asserted until the thread is actually gone; it costs a handful of
 * microseconds and cannot deadlock.
 */
static void search_stop_and_join(void) {
    while (g_searchRunning) {
        g_stop = 1;
        /* A real sleep, not a yield.  `Sleep(0)` and `sched_yield` only give
         * way to threads already runnable on this core, so on a machine with
         * more work than cores this loop can hold the processor it is waiting
         * for and never let the search thread run at all -- a livelock that
         * only appears under load, which is exactly when a ponder miss has to
         * be answered quickly.  One millisecond costs nothing and cannot
         * starve anybody. */
#ifdef _WIN32
        Sleep(1);
#else
        struct timespec ts = {0, 1000000};
        nanosleep(&ts, NULL);
#endif
    }
    search_thread_join();
}

CE_EXPORT void ce_search_join(SearchResult *out) {
    while (g_searchRunning) {
#ifdef _WIN32
        Sleep(1);
#else
        struct timespec ts = {0, 1000000};
        nanosleep(&ts, NULL);
#endif
    }
    search_thread_join();
    if (out) *out = g_searchRes;
}

CE_EXPORT void ce_search_stop(SearchResult *out) {
    search_stop_and_join();
    if (out) *out = g_searchRes;
}

/* Throw a search away without announcing anything: a ponder miss. */
CE_EXPORT void ce_search_abort(void) {
    g_searchSuppress = 1;
    search_stop_and_join();
    g_searchSuppress = 0;
}

Position *ce_internal_position(void) { return &g_pos; }

/* ------------------------------------------------------------------ */
/* labelled position set                                               */
/* ------------------------------------------------------------------ */

/*
 * A set of positions with reference scores, held on this side of the FFI
 * boundary so that scoring the whole set is one call.
 *
 * Fitting the evaluation means scoring the same hundred thousand positions a
 * few thousand times over.  Driven a position at a time from Python that is
 * two FFI calls and a string encode per position, which costs about twenty
 * times what the evaluation itself does and puts a fit over fifty parameters
 * out of reach for an afternoon.  Loaded once and scored in C, a pass is a
 * few tens of milliseconds.
 *
 * The file is the packed 32-byte record written by nnue/extract.c.
 */

#pragma pack(push, 1)
typedef struct {
    uint64_t occ;
    uint8_t  pieces[16];
    int16_t  score;
    uint8_t  stm;
    uint8_t  flags;
    uint8_t  wksq, bksq;
    uint16_t pad;
} EvalRec;
#pragma pack(pop)

static EvalRec *g_set;
static int g_setSize;
static double *g_setOurs;        /* scratch: our score, White's point of view */

static uint32_t set_rng(uint64_t *s) {
    *s ^= *s >> 12; *s ^= *s << 25; *s ^= *s >> 27;
    return (uint32_t)((*s * 2685821657736338717ULL) >> 32);
}

/*
 * Load up to `limit` records, keeping those within `cap` centipawns and (when
 * keepMates is 0) those that are not mate scores.  A reservoir sample keeps
 * the subset uniform over the whole file rather than over its first part,
 * which matters because the dump is ordered and its tail is not like its head.
 */
CE_EXPORT int ce_evalset_load(const char *path, int limit, int cap,
                              int keepMates, unsigned seed) {
    free(g_set); free(g_setOurs);
    g_set = NULL; g_setOurs = NULL; g_setSize = 0;

    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    if (limit <= 0) limit = 1 << 20;
    g_set = (EvalRec *)malloc((size_t)limit * sizeof(EvalRec));
    g_setOurs = (double *)malloc((size_t)limit * sizeof(double));
    if (!g_set || !g_setOurs) { fclose(f); return 0; }

    uint64_t rng = seed ? seed : 0x9E3779B97F4A7C15ULL;
    EvalRec buf[4096];
    int n = 0;
    long seen = 0;
    size_t got;
    while ((got = fread(buf, sizeof(EvalRec), 4096, f)) > 0) {
        for (size_t i = 0; i < got; i++) {
            int sc = buf[i].score;
            if (sc > cap || sc < -cap) continue;
            if (!keepMates && (buf[i].flags & 1)) continue;
            if (n < limit) {
                g_set[n++] = buf[i];
            } else {
                /* reservoir: replace with probability limit/seen */
                uint32_t r = set_rng(&rng);
                if ((long)(r % (uint32_t)(seen + 1)) < limit)
                    g_set[r % (uint32_t)limit] = buf[i];
            }
            seen++;
        }
    }
    fclose(f);
    g_setSize = n;
    return n;
}

CE_EXPORT int ce_evalset_size(void) { return g_setSize; }

/* Rebuild a Position from a packed record. */
static int set_position_from_rec(Position *pos, const EvalRec *r) {
    memset(pos, 0, sizeof(*pos));
    memset(pos->board, NO_PIECE, 64);
    pos->ep = SQ_NONE;

    int i = 0;
    for (U64 bb = r->occ; bb; i++) {
        int sq = pop_lsb(&bb);
        int code = (i & 1) ? (r->pieces[i >> 1] >> 4) : (r->pieces[i >> 1] & 15);
        if (code > B_KING) return 0;
        pos->board[sq] = (uint8_t)code;
        pos->byType[piece_type(code)] |= bb_of(sq);
        pos->byColor[piece_color(code)] |= bb_of(sq);
        pos->occ |= bb_of(sq);
        pos->pieceCount[code]++;
        /* The zobrist key has to be real, not left at zero.  The network's
         * accumulator cache tells positions apart by key, so a set of
         * positions that all claimed key 0 would be handed one another's
         * accumulator and every evaluation after the first would be wrong. */
        pos->key ^= ZobristPiece[code][sq];
        if (piece_type(code) == PAWN) pos->pawnKey ^= ZobristPiece[code][sq];
    }
    pos->side = r->stm ? BLACK : WHITE;
    if (pos->side == BLACK) pos->key ^= ZobristSide;
    if (popcount(pieces_cp(pos, WHITE, KING)) != 1) return 0;
    if (popcount(pieces_cp(pos, BLACK, KING)) != 1) return 0;

    pos->histCount = 0;
    pos->rootHist = 0;
    StateInfo *st = &pos->st[0];
    st->captured = NO_PIECE;
    st->move = MOVE_NONE;
    st->eval = VALUE_NONE;
    st->ep = SQ_NONE;
    st->key = pos->key;
    st->pawnKey = pos->pawnKey;
    pos_update_state(pos);
    return 1;
}

/*
 * Score the whole set and report how well it matches the reference.
 *
 * `corr` is the correlation, which no change of scale can move, so it measures
 * the shape of the evaluation and only that.  `fit` is the least-squares
 * multiplier from engine units to reference centipawns, and `rms` the residual
 * once that multiplier is applied.
 */
CE_EXPORT int ce_evalset_stats(double *corr, double *rms, double *fit) {
    if (!g_setSize) return 0;
    static Position p;            /* 230 KB; not on the stack */

    double sx = 0, sy = 0, sxx = 0, sxy = 0, syy = 0;
    int n = 0;
    for (int i = 0; i < g_setSize; i++) {
        if (!set_position_from_rec(&p, &g_set[i])) { g_setOurs[i] = 0.0; continue; }
        double v = (double)eval_position(&p);
        if (g_set[i].stm) v = -v;             /* to White's point of view */
        g_setOurs[i] = v;
        double y = (double)g_set[i].score;
        sx += v; sy += y; sxx += v * v; sxy += v * y; syy += y * y;
        n++;
    }
    if (n < 2) return 0;

    double k = sxx > 1e-9 ? sxy / sxx : 0.0;
    double num = sxy - sx * sy / n;
    double dx = sxx - sx * sx / n, dy = syy - sy * sy / n;
    double c = (dx > 1e-9 && dy > 1e-9) ? num / sqrt(dx * dy) : 0.0;

    double err = 0;
    for (int i = 0; i < g_setSize; i++) {
        double d = (double)g_set[i].score - k * g_setOurs[i];
        err += d * d;
    }
    if (corr) *corr = c;
    if (rms) *rms = sqrt(err / n);
    if (fit) *fit = k;
    return n;
}

/* ---------------- benchmark ---------------- */

static const char *BenchFens[] = {
    START_FEN,
    "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
    "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
    "r4rk1/1pp1qppp/p1np1n2/1B2p1b1/4P3/1BN2Q1p/PPPB1PPP/R3K2R w KQ - 0 10",
    "4rrk1/pp1n3p/3q2pQ/2p1pb2/2PP4/2P3N1/P2B2PP/4RRK1 b - - 7 19",
    "r1bq1r1k/1pp1n1pp/1p1p4/4p2Q/4Pp2/1BNP4/PPP2PPP/R4RK1 w - - 2 14",
    "2kr3r/pppq1ppp/2n1b3/3p4/3P4/2P1BN2/PP3PPP/R2QK2R w KQ - 1 12",
    "8/8/4k3/8/8/4K3/8/4Q3 w - - 0 1",
    "6k1/6p1/7p/P5P1/1p4KP/2p5/8/8 b - - 0 1",
    "8/8/3P3k/8/1p6/8/1P6/1K3n2 b - - 0 1",
    "r2qkbnr/ppp2ppp/2np4/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 2 6",
    "3r1k2/4npp1/1ppr3p/p6P/P2PPPP1/1NR5/5K2/2R5 w - - 0 1"
};

CE_EXPORT void ce_bench(int depth) {
    if (depth < 1) depth = 12;
    int saveInfo = g_showInfo;
    g_showInfo = 0;
    long long totalNodes = 0;
    int64_t t0 = now_ms();
    int n = (int)(sizeof(BenchFens) / sizeof(BenchFens[0]));

    for (int i = 0; i < n; i++) {
        ce_new_game();
        ce_set_position(BenchFens[i], "");
        SearchResult r;
        ce_search(0, 0, 0, 0, 0, 0, depth, 0, 0, &r);
        totalNodes += r.nodes;
        printf("position %2d/%d  depth %2d  seldepth %2d  score %6d  best %-6s  %10lld nodes\n",
               i + 1, n, r.depth, r.seldepth, r.score, r.bestmove, (long long)r.nodes);
        fflush(stdout);
    }
    int64_t dt = now_ms() - t0;
    if (dt < 1) dt = 1;
    printf("\n===========================\n");
    printf("Total time (ms) : %lld\n", (long long)dt);
    printf("Nodes searched  : %lld\n", totalNodes);
    printf("Nodes/second    : %lld\n", totalNodes * 1000 / dt);
    fflush(stdout);
    g_showInfo = saveInfo;
}

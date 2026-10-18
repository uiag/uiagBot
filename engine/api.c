/*
 * api.c -- C API exported to the Python side (and used by the UCI front-end).
 */
#include "ce.h"
#include <stdio.h>
#include <stdlib.h>

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

Position *ce_internal_position(void) { return &g_pos; }

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

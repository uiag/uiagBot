/*
 * ccrl_main.c -- entry point for the CCRL release build.
 *
 * This replaces engine/main.c for the submission binary.  It differs from the
 * development entry point in four ways, each of which is a CCRL requirement
 * or a request from the people who run the testing:
 *
 *   - The network is linked into the executable (net_embed.S) and loaded from
 *     memory, so the engine is one self-contained file and does not care what
 *     working directory the tournament GUI gives it.  engine/main.c reads
 *     net3.bin from the current directory, which is fine for cutechess runs
 *     out of the repository and not fine for a release.
 *
 *   - Defaults are 256 MB hash and 1 thread.  CCRL's submission notes ask for
 *     exactly that ("it would be helpful if your default settings were 256mb
 *     hash and 1 core"); the development build defaults to 64 MB, and the
 *     lichess bot overrides both anyway from config.yml.
 *
 *   - EvalMode starts at `blend`, the strongest of the three, instead of
 *     `hce`.  A GUI that never sends `setoption name EvalMode` would
 *     otherwise get the hand-crafted evaluation, which is some 170 Elo
 *     weaker.
 *
 *   - The ~170 SPSA tuning options are not announced.  They exist so a tuner
 *     can reach the search constants; a tournament GUI showing 170 spin boxes
 *     is noise, and a tester who nudges one is no longer testing this engine.
 *     They remain settable by name for anyone who wants them.
 *
 * Nothing about the search, the evaluation or the network differs from the
 * build that plays on lichess.
 */
#include "ce.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern int  ce_init(const char *netPath, int hashMb, int threads);
extern void ce_set_eval_mode(int mode);
extern void ce_bench(int depth);
extern unsigned long long ce_perft(int depth);

/* nnue.c, after the memory-loader patch that ccrl/build.py applies. */
extern int  nnue_load_mem(const void *data, size_t len);
extern int  nnue_load_file(const char *path);

/* net_embed.S */
extern const unsigned char      ce_net_data[];
extern const unsigned long long ce_net_size;

/*
 * Milliseconds subtracted from the clock to cover the round trip through the
 * GUI's pipes and the scheduler.  The development default is 25 ms, which is
 * right for a local cutechess run on an idle machine and thin for a tester
 * running several games at once on a loaded one.  50 ms costs nothing at
 * 40 moves in 15 minutes -- a fifth of a percent of one move's thinking time
 * -- and it is also exposed as the usual `Move Overhead` option so a tester
 * who needs more can say so.
 */
#define CCRL_MOVE_OVERHEAD_MS 50

/* Both of these are also what the `uci` reply announces; ccrl/build.py
 * patches the reply and checks the two agree. */
#define CCRL_HASH_MB  256
#define CCRL_THREADS  1

static void usage(const char *argv0) {
    printf("%s -- UCI chess engine\n\n", argv0);
    printf("With no arguments the engine speaks UCI on stdin/stdout, which is\n"
           "what a chess GUI wants.  Options:\n\n");
    printf("  --hash N        transposition table size in MB (default %d)\n",
           CCRL_HASH_MB);
    printf("  --threads N     search threads (default %d)\n", CCRL_THREADS);
    printf("  --net FILE      load this network instead of the built-in one\n");
    printf("  bench [depth]   fixed-depth benchmark, then exit\n");
    printf("  perft [depth]   move generation count, then exit\n");
    printf("  --help          this text\n");
}

int main(int argc, char **argv) {
    int hash = CCRL_HASH_MB, threads = CCRL_THREADS;
    int doBench = 0, doPerft = 0, argDepth = 0;
    const char *netFile = NULL;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--net") && i + 1 < argc) netFile = argv[++i];
        else if (!strcmp(argv[i], "--hash") && i + 1 < argc) hash = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--threads") && i + 1 < argc) threads = atoi(argv[++i]);
        else if (!strcmp(argv[i], "bench")) doBench = 1;
        else if (!strcmp(argv[i], "perft")) doPerft = 1;
        else if (!strcmp(argv[i], "--help") || !strcmp(argv[i], "-h")) {
            usage(argv[0]);
            return 0;
        }
        else if (argv[i][0] >= '0' && argv[i][0] <= '9') argDepth = atoi(argv[i]);
    }

    /* NULL: ce_init must not try to read a network off the disk. */
    ce_init(NULL, hash, threads);

    /* The network has to be in place before the eval mode is set -- both
     * `nnue` and `blend` fall back to `hce` when no network is loaded. */
    int netOk = netFile ? nnue_load_file(netFile)
                        : nnue_load_mem(ce_net_data, (size_t)ce_net_size);
    ce_set_eval_mode(EVAL_BLEND);
    if (!netOk) {
        /* Cannot happen with the built-in network; a bad --net can do it. */
        printf("info string network not loaded, evaluation falls back to hce\n");
        fflush(stdout);
    }

    tune_set("MoveOverhead", CCRL_MOVE_OVERHEAD_MS);
    g_showTuneOptions = 0;

    if (doBench) {
        ce_bench(argDepth > 0 ? argDepth : 12);
        return 0;
    }
    if (doPerft) {
        int d = argDepth > 0 ? argDepth : 5;
        int64_t t0 = now_ms();
        unsigned long long n = ce_perft(d);
        int64_t dt = now_ms() - t0;
        if (dt < 1) dt = 1;
        printf("perft(%d) = %llu in %lld ms (%.2f Mnps)\n", d, n, (long long)dt,
               (double)n / (double)dt / 1000.0);
        return 0;
    }

    uci_loop();
    return 0;
}

/*
 * main.c -- entry point for the standalone executable.
 *
 * Only compiled into the .exe; the DLL used by the Python bridge omits it.
 */
#include "ce.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern int ce_init(const char *netPath, int hashMb, int threads);
extern void ce_bench(int depth);
extern int ce_set_position(const char *fen, const char *moves);
extern unsigned long long ce_perft(int depth);

int main(int argc, char **argv) {
    /* net3.bin is the king-bucket perspective network.  net2.bin is the
     * single-bucket one it replaced and still loads -- the engine reads both
     * formats -- which is what `--net net2.bin` is for.  The old 769-256-1
     * net.bin is not loadable any more and was weaker than the hand-crafted
     * evaluation anyway. */
    const char *net = "net3.bin";
    int hash = 64, threads = 1;
    int doBench = 0, doPerft = 0, argDepth = 0;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--net") && i + 1 < argc) net = argv[++i];
        else if (!strcmp(argv[i], "--hash") && i + 1 < argc) hash = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--threads") && i + 1 < argc) threads = atoi(argv[++i]);
        else if (!strcmp(argv[i], "bench")) doBench = 1;
        else if (!strcmp(argv[i], "perft")) doPerft = 1;
        else if (argv[i][0] >= '0' && argv[i][0] <= '9') argDepth = atoi(argv[i]);
    }

    ce_init(net, hash, threads);

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

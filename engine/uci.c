/*
 * uci.c -- standalone UCI front-end plus `bench` and `perft` commands.
 *
 * The lichess bot talks to the engine through the C API in api.c; this
 * front-end exists so the same engine can be tested with ordinary chess
 * tooling (cutechess-cli, Arena, ...).
 */
#include "ce.h"
#include <stdio.h>
#include <stdlib.h>
#include <ctype.h>

#define START_FEN "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

extern const char *ce_version(void);
extern int ce_init(const char *netPath, int hashMb, int threads);
extern void ce_new_game(void);
extern int ce_set_position(const char *fen, const char *moves);
extern void ce_search(long long, long long, long long, long long, long long,
                      int, int, long long, int, SearchResult *);
extern void ce_set_hash(int mb);
extern void ce_set_threads(int n);
extern void ce_set_eval_mode(int mode);
extern void ce_set_show_info(int on);
extern void ce_set_multipv(int n);
extern int ce_eval(void);
extern unsigned long long ce_perft(int depth);
extern Position *ce_internal_position(void);

static char *skip_ws(char *s) { while (*s == ' ' || *s == '\t') s++; return s; }

static int starts_with(const char *s, const char *pre) {
    size_t n = strlen(pre);
    return !strncmp(s, pre, n) && (s[n] == 0 || s[n] == ' ' || s[n] == '\n');
}

extern void ce_bench(int depth);

void uci_loop(void) {
    char line[65536];
    char fen[256];
    char moves[60000];

    setvbuf(stdout, NULL, _IOLBF, 4096);
    ce_set_show_info(1);
    strcpy(fen, START_FEN);
    moves[0] = 0;

    while (fgets(line, sizeof(line), stdin)) {
        char *s = skip_ws(line);
        size_t len = strlen(s);
        while (len && (s[len - 1] == '\n' || s[len - 1] == '\r')) s[--len] = 0;

        if (starts_with(s, "uci")) {
            printf("id name %s\n", ce_version());
            printf("id author jakob\n");
            printf("option name Hash type spin default 64 min 1 max 32768\n");
            printf("option name Threads type spin default 1 min 1 max 64\n");
            printf("option name MultiPV type spin default 1 min 1 max 64\n");
            printf("option name EvalMode type combo default hce var hce var nnue var blend\n");
            if (g_showTuneOptions) tune_print_uci_options();
            printf("uciok\n");
            fflush(stdout);
        } else if (starts_with(s, "isready")) {
            printf("readyok\n");
            fflush(stdout);
        } else if (starts_with(s, "ucinewgame")) {
            ce_new_game();
            strcpy(fen, START_FEN);
            moves[0] = 0;
        } else if (starts_with(s, "setoption")) {
            char *name = strstr(s, "name ");
            char *value = strstr(s, "value ");
            if (name) {
                name += 5;
                if (value) { value += 6; }
                /* `name` still has " value ..." after it, so the tune lookup
                 * needs the bare word; copy it out first. */
                if (value) {
                    char word[64];
                    int n = 0;
                    while (name[n] && name[n] != ' ' && n < 63) { word[n] = name[n]; n++; }
                    word[n] = 0;
                    if (tune_set(word, atoi(value))) continue;
                }
                if (!strncmp(name, "Hash", 4) && value) ce_set_hash(atoi(value));
                else if (!strncmp(name, "Threads", 7) && value) ce_set_threads(atoi(value));
                else if (!strncmp(name, "MultiPV", 7) && value) ce_set_multipv(atoi(value));
                else if (!strncmp(name, "EvalMode", 8) && value) {
                    if (!strncmp(value, "nnue", 4)) ce_set_eval_mode(EVAL_NNUE);
                    else if (!strncmp(value, "blend", 5)) ce_set_eval_mode(EVAL_BLEND);
                    else ce_set_eval_mode(EVAL_HCE);
                }
            }
        } else if (starts_with(s, "position")) {
            char *p = s + 8;
            p = skip_ws(p);
            moves[0] = 0;
            if (starts_with(p, "startpos")) {
                strcpy(fen, START_FEN);
                p += 8;
            } else if (starts_with(p, "fen")) {
                p += 3;
                p = skip_ws(p);
                char *mv = strstr(p, "moves");
                int n = mv ? (int)(mv - p) : (int)strlen(p);
                if (n > 250) n = 250;
                memcpy(fen, p, n);
                fen[n] = 0;
                p = mv ? mv : p + n;
            }
            char *mv = strstr(p, "moves");
            if (mv) {
                mv += 5;
                mv = skip_ws(mv);
                snprintf(moves, sizeof(moves), "%s", mv);
            }
            if (!ce_set_position(fen, moves)) {
                printf("info string bad position\n");
                fflush(stdout);
            }
        } else if (starts_with(s, "go")) {
            long long wtime = 0, btime = 0, winc = 0, binc = 0, movetime = 0, nodes = 0;
            int movestogo = 0, depth = 0, infinite = 0;
            char *tok = strtok(s + 2, " ");
            while (tok) {
                if (!strcmp(tok, "wtime")) { tok = strtok(NULL, " "); if (tok) wtime = atoll(tok); }
                else if (!strcmp(tok, "btime")) { tok = strtok(NULL, " "); if (tok) btime = atoll(tok); }
                else if (!strcmp(tok, "winc")) { tok = strtok(NULL, " "); if (tok) winc = atoll(tok); }
                else if (!strcmp(tok, "binc")) { tok = strtok(NULL, " "); if (tok) binc = atoll(tok); }
                else if (!strcmp(tok, "movetime")) { tok = strtok(NULL, " "); if (tok) movetime = atoll(tok); }
                else if (!strcmp(tok, "movestogo")) { tok = strtok(NULL, " "); if (tok) movestogo = atoi(tok); }
                else if (!strcmp(tok, "depth")) { tok = strtok(NULL, " "); if (tok) depth = atoi(tok); }
                else if (!strcmp(tok, "nodes")) { tok = strtok(NULL, " "); if (tok) nodes = atoll(tok); }
                else if (!strcmp(tok, "infinite")) infinite = 1;
                tok = strtok(NULL, " ");
            }
            SearchResult r;
            ce_search(wtime, btime, winc, binc, movetime, movestogo, depth, nodes,
                      infinite, &r);
            if (r.ponder[0]) printf("bestmove %s ponder %s\n", r.bestmove, r.ponder);
            else printf("bestmove %s\n", r.bestmove);
            fflush(stdout);
        } else if (starts_with(s, "stop")) {
            g_stop = 1;
        } else if (starts_with(s, "quit")) {
            break;
        } else if (starts_with(s, "bench")) {
            int d = 12;
            char *p = strchr(s, ' ');
            if (p) d = atoi(p + 1);
            if (d < 1) d = 12;
            ce_bench(d);
        } else if (starts_with(s, "perft")) {
            int d = 5;
            char *p = strchr(s, ' ');
            if (p) d = atoi(p + 1);
            int64_t t0 = now_ms();
            unsigned long long n = ce_perft(d);
            int64_t dt = now_ms() - t0;
            if (dt < 1) dt = 1;
            printf("perft(%d) = %llu  in %lld ms  (%.2f Mnps)\n", d, n,
                   (long long)dt, (double)n / (double)dt / 1000.0);
            fflush(stdout);
        } else if (starts_with(s, "tune")) {
            tune_print_values();
            fflush(stdout);
        } else if (starts_with(s, "eval")) {
            printf("eval %d cp (side to move)\n", ce_eval());
            fflush(stdout);
        } else if (starts_with(s, "d")) {
            char f[256];
            pos_get_fen(ce_internal_position(), f);
            printf("%s\n", f);
            fflush(stdout);
        }
    }
}

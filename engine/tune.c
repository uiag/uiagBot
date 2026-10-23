/*
 * tune.c -- storage and lookup for the constants listed in tune.h.
 *
 * The variables are ordinary globals so the optimiser still folds them into
 * the hot code paths; only the SPSA driver and `setoption` ever write them,
 * and never while a search is running.
 */
#include "ce.h"
#include <stdio.h>
#include <stdlib.h>

#define DEFINE(name, def, lo, hi) int name = (def);
TUNE_LIST(DEFINE)
#undef DEFINE

#define ROW(name, def, lo, hi) { #name, &name, (def), (lo), (hi) },
static const TuneParam Params[] = {
    TUNE_LIST(ROW)
    { NULL, NULL, 0, 0, 0 }
};
#undef ROW

/*
 * These are declared in the `uci` reply like any other option.  Hiding them
 * behind an environment variable seemed tidier -- a GUI does not want eighty
 * spin boxes -- and it cost a day: a UCI host has no way to know an option
 * exists unless the engine says so, and the testsuite drops undeclared
 * options silently.  Three tuning experiments measured nothing at all before
 * that came to light.  Tidiness is not worth a silent failure.
 */
int g_showTuneOptions = 1;

void tune_init(void) {
    const char *e = getenv("CE_TUNE");
    if (e && e[0] == '0') g_showTuneOptions = 0;   /* opt out if you must */
}

const TuneParam *tune_params(void) { return Params; }


static int ieq(const char *a, const char *b) {
    for (; *a && *b; a++, b++) {
        int ca = *a >= 'A' && *a <= 'Z' ? *a + 32 : *a;
        int cb = *b >= 'A' && *b <= 'Z' ? *b + 32 : *b;
        if (ca != cb) return 0;
    }
    return *a == *b;
}

int tune_set(const char *name, int value) {
    for (int i = 0; Params[i].name; i++)
        if (ieq(Params[i].name, name)) {
            int v = value;
            if (v < Params[i].min) v = Params[i].min;
            if (v > Params[i].max) v = Params[i].max;
            *Params[i].ptr = v;
            tune_changed();
            return 1;
        }
    return 0;
}

int tune_get(const char *name, int *out) {
    for (int i = 0; Params[i].name; i++)
        if (ieq(Params[i].name, name)) { *out = *Params[i].ptr; return 1; }
    return 0;
}

void tune_print_uci_options(void) {
    for (int i = 0; Params[i].name; i++)
        printf("option name %s type spin default %d min %d max %d\n",
               Params[i].name, Params[i].def, Params[i].min, Params[i].max);
}

void tune_print_values(void) {
    for (int i = 0; Params[i].name; i++)
        printf("%s %d\n", Params[i].name, *Params[i].ptr);
}

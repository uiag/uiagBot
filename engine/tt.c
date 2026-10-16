/*
 * tt.c -- shared transposition table.
 *
 * Clusters of three 10-byte entries fit in 32 bytes (half a cache line).
 * Access is lock-free: a torn read can only produce a stale move or score,
 * and both are validated by the search before use.
 */
#include "ce.h"
#include <stdlib.h>

#define CLUSTER_SIZE 3

typedef struct {
    TTEntry entry[CLUSTER_SIZE];
    uint8_t padding[2];
} Cluster;

static Cluster *Table = NULL;
static size_t ClusterCount = 0;
static uint8_t Generation = 0;    /* stored in bits 3..7 of genBound */

#define GEN_MASK   0xF8
#define GEN_DELTA  8
#define GEN_CYCLE  (255 + GEN_DELTA)

static inline int relative_age(uint8_t genBound) {
    return (GEN_CYCLE + Generation - genBound) & GEN_MASK;
}

void tt_init(int mb) {
    if (mb < 1) mb = 1;
    size_t bytes = (size_t)mb * 1024 * 1024;
    size_t n = bytes / sizeof(Cluster);
    /* round down to a power of two so indexing is a mask */
    size_t p = 1;
    while (p * 2 <= n) p *= 2;
    if (p < 1024) p = 1024;
    if (Table && p == ClusterCount) { tt_clear(); return; }
    free(Table);
    Table = (Cluster *)calloc(p, sizeof(Cluster));
    if (!Table) {
        p = 1024;
        Table = (Cluster *)calloc(p, sizeof(Cluster));
    }
    ClusterCount = p;
    Generation = 0;
}

void tt_clear(void) {
    if (Table) memset(Table, 0, ClusterCount * sizeof(Cluster));
    Generation = 0;
}

void tt_new_search(void) {
    Generation = (uint8_t)((Generation + GEN_DELTA) & 0xFF);
}

void tt_prefetch(U64 key) {
    if (!Table) return;
    __builtin_prefetch(&Table[key & (ClusterCount - 1)]);
}

int tt_hashfull(void) {
    if (!Table) return 0;
    int cnt = 0;
    size_t probes = 1000;
    if (probes > ClusterCount) probes = ClusterCount;
    for (size_t i = 0; i < probes; i++)
        for (int j = 0; j < CLUSTER_SIZE; j++)
            if (Table[i].entry[j].depth
                && (Table[i].entry[j].genBound & GEN_MASK) == Generation)
                cnt++;
    return (int)(cnt * 1000 / (probes * CLUSTER_SIZE));
}

TTEntry *tt_probe(U64 key, int *found) {
    TTEntry *e = Table[key & (ClusterCount - 1)].entry;
    uint16_t k16 = (uint16_t)(key >> 48);

    for (int i = 0; i < CLUSTER_SIZE; i++)
        if (e[i].key16 == k16 && e[i].depth) {
            /* refresh the generation so the entry survives replacement */
            e[i].genBound = (uint8_t)(Generation | (e[i].genBound & 0x7));
            *found = 1;
            return &e[i];
        }

    /* pick a victim: lowest depth, penalising stale generations */
    TTEntry *victim = &e[0];
    for (int i = 1; i < CLUSTER_SIZE; i++)
        if ((int)victim->depth - relative_age(victim->genBound)
            > (int)e[i].depth - relative_age(e[i].genBound))
            victim = &e[i];
    *found = 0;
    return victim;
}

void tt_store(TTEntry *e, U64 key, int score, int eval, int bound, int depth, Move m, int pv) {
    uint16_t k16 = (uint16_t)(key >> 48);

    if (m || k16 != e->key16) e->move = m;

    /* Overwrite less valuable data. */
    if (bound == BOUND_EXACT
        || k16 != e->key16
        || depth - TT_DEPTH_OFFSET + 2 * pv > (int)e->depth - 4
        || relative_age(e->genBound)) {
        e->key16 = k16;
        e->score = (int16_t)score;
        e->eval = (int16_t)eval;
        e->depth = (uint8_t)(depth - TT_DEPTH_OFFSET);
        e->genBound = (uint8_t)(Generation | (pv ? 4 : 0) | bound);
    }
}

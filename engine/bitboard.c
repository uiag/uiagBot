/*
 * bitboard.c -- attack tables and magic bitboard initialisation.
 *
 * Magics are searched for at start-up with a seeded sparse PRNG, which keeps
 * the source free of large constant tables while costing only a few ms.
 */
#include "ce.h"

const U64 FileBB[8] = {
    FILE_A_BB, FILE_A_BB << 1, FILE_A_BB << 2, FILE_A_BB << 3,
    FILE_A_BB << 4, FILE_A_BB << 5, FILE_A_BB << 6, FILE_A_BB << 7
};
const U64 RankBB[8] = {
    RANK_1_BB, RANK_1_BB << 8, RANK_1_BB << 16, RANK_1_BB << 24,
    RANK_1_BB << 32, RANK_1_BB << 40, RANK_1_BB << 48, RANK_1_BB << 56
};

U64 PawnAttacksBB[2][64];
U64 KnightAttacksBB[64];
U64 KingAttacksBB[64];
U64 BetweenBB[64][64];
U64 LineBB[64][64];
U64 PseudoAttacks[PIECE_TYPE_NB][64];
int SquareDistance[64][64];

typedef struct {
    U64 mask;
    U64 magic;
    U64 *attacks;
    unsigned shift;
} Magic;

static Magic RookMagics[64];
static Magic BishopMagics[64];
static U64 RookTable[0x19000];   /* 102400 */
static U64 BishopTable[0x1480];  /*   5248 */

static inline unsigned magic_index(const Magic *m, U64 occ) {
    return (unsigned)(((occ & m->mask) * m->magic) >> m->shift);
}

U64 bishop_attacks(int sq, U64 occ) {
    const Magic *m = &BishopMagics[sq];
    return m->attacks[magic_index(m, occ)];
}

U64 rook_attacks(int sq, U64 occ) {
    const Magic *m = &RookMagics[sq];
    return m->attacks[magic_index(m, occ)];
}

/* ------------------------------------------------------------------ */

static U64 sliding_attack(const int deltas[4][2], int sq, U64 occ) {
    U64 attack = 0;
    int f0 = file_of(sq), r0 = rank_of(sq);
    for (int d = 0; d < 4; d++) {
        int f = f0 + deltas[d][0], r = r0 + deltas[d][1];
        while (f >= 0 && f < 8 && r >= 0 && r < 8) {
            int s = make_sq(f, r);
            attack |= bb_of(s);
            if (occ & bb_of(s)) break;
            f += deltas[d][0];
            r += deltas[d][1];
        }
    }
    return attack;
}

static const int RookDeltas[4][2]   = { {1,0}, {-1,0}, {0,1}, {0,-1} };
static const int BishopDeltas[4][2] = { {1,1}, {1,-1}, {-1,1}, {-1,-1} };

/* xorshift64* PRNG -- deterministic, so magics are identical every run. */
static U64 rng_state = 0x246C2B4F8D1C5A93ULL;
static U64 rng_next(void) {
    rng_state ^= rng_state >> 12;
    rng_state ^= rng_state << 25;
    rng_state ^= rng_state >> 27;
    return rng_state * 2685821657736338717ULL;
}
static U64 rng_sparse(void) { return rng_next() & rng_next() & rng_next(); }

static void init_magics(int isRook, U64 *table, Magic *magics) {
    const int (*deltas)[2] = isRook ? RookDeltas : BishopDeltas;
    U64 occupancy[4096], reference[4096];
    int epoch[4096];
    memset(epoch, 0, sizeof(epoch));
    int cnt = 0;
    U64 *cursor = table;

    for (int sq = 0; sq < 64; sq++) {
        Magic *m = &magics[sq];

        /* Relevant occupancy: the attack set minus board edges that cannot
         * change what the piece sees. */
        U64 edges = ((RankBB[0] | RankBB[7]) & ~RankBB[rank_of(sq)])
                  | ((FileBB[0] | FileBB[7]) & ~FileBB[file_of(sq)]);
        m->mask = sliding_attack(deltas, sq, 0) & ~edges;
        m->shift = 64 - popcount(m->mask);
        m->attacks = cursor;

        /* Enumerate every subset of the mask (Carry-Rippler). */
        int size = 0;
        U64 b = 0;
        do {
            occupancy[size] = b;
            reference[size] = sliding_attack(deltas, sq, b);
            size++;
            b = (b - m->mask) & m->mask;
        } while (b);
        cursor += size;

        /* Search for a magic that maps every subset without a bad collision. */
        for (int i = 0; i < size; ) {
            for (m->magic = 0; popcount((m->magic * m->mask) >> 56) < 6; )
                m->magic = rng_sparse();

            cnt++;
            for (i = 0; i < size; i++) {
                unsigned idx = magic_index(m, occupancy[i]);
                if (epoch[idx] < cnt) {
                    epoch[idx] = cnt;
                    m->attacks[idx] = reference[i];
                } else if (m->attacks[idx] != reference[i]) {
                    break;   /* constructive collisions only */
                }
            }
        }
    }
}

void bb_init(void) {
    static int done = 0;
    if (done) return;
    done = 1;

    for (int s1 = 0; s1 < 64; s1++)
        for (int s2 = 0; s2 < 64; s2++) {
            int fd = file_of(s1) - file_of(s2), rd = rank_of(s1) - rank_of(s2);
            if (fd < 0) fd = -fd;
            if (rd < 0) rd = -rd;
            SquareDistance[s1][s2] = fd > rd ? fd : rd;
        }

    for (int sq = 0; sq < 64; sq++) {
        U64 b = bb_of(sq);
        PawnAttacksBB[WHITE][sq] = pawn_attacks_bb(WHITE, b);
        PawnAttacksBB[BLACK][sq] = pawn_attacks_bb(BLACK, b);

        U64 n = 0, k = 0;
        static const int nSteps[8][2] = {
            {1,2},{2,1},{2,-1},{1,-2},{-1,-2},{-2,-1},{-2,1},{-1,2} };
        static const int kSteps[8][2] = {
            {1,0},{1,1},{0,1},{-1,1},{-1,0},{-1,-1},{0,-1},{1,-1} };
        int f0 = file_of(sq), r0 = rank_of(sq);
        for (int i = 0; i < 8; i++) {
            int f = f0 + nSteps[i][0], r = r0 + nSteps[i][1];
            if (f >= 0 && f < 8 && r >= 0 && r < 8) n |= bb_of(make_sq(f, r));
            f = f0 + kSteps[i][0]; r = r0 + kSteps[i][1];
            if (f >= 0 && f < 8 && r >= 0 && r < 8) k |= bb_of(make_sq(f, r));
        }
        KnightAttacksBB[sq] = n;
        KingAttacksBB[sq] = k;
    }

    init_magics(1, RookTable, RookMagics);
    init_magics(0, BishopTable, BishopMagics);

    for (int sq = 0; sq < 64; sq++) {
        PseudoAttacks[KNIGHT][sq] = KnightAttacksBB[sq];
        PseudoAttacks[KING][sq]   = KingAttacksBB[sq];
        PseudoAttacks[BISHOP][sq] = bishop_attacks(sq, 0);
        PseudoAttacks[ROOK][sq]   = rook_attacks(sq, 0);
        PseudoAttacks[QUEEN][sq]  = PseudoAttacks[BISHOP][sq] | PseudoAttacks[ROOK][sq];
        PseudoAttacks[PAWN][sq]   = 0;
    }

    for (int s1 = 0; s1 < 64; s1++)
        for (int s2 = 0; s2 < 64; s2++) {
            BetweenBB[s1][s2] = 0;
            LineBB[s1][s2] = 0;
            if (s1 == s2) continue;
            for (int pt = BISHOP; pt <= ROOK; pt++) {
                if (PseudoAttacks[pt][s1] & bb_of(s2)) {
                    LineBB[s1][s2] = (PseudoAttacks[pt][s1] & PseudoAttacks[pt][s2])
                                   | bb_of(s1) | bb_of(s2);
                    BetweenBB[s1][s2] = attacks_bb(pt, s1, bb_of(s2))
                                      & attacks_bb(pt, s2, bb_of(s1));
                }
            }
        }
}

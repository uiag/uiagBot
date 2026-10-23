/*
 * position.c -- board state, zobrist hashing, make/unmake, attacks, SEE.
 */
#include "ce.h"
#include <stdio.h>
#include <stdlib.h>
#include <ctype.h>

U64 ZobristPiece[PIECE_NB][64];
U64 ZobristEp[8];
U64 ZobristCastling[16];
U64 ZobristSide;

const int SeeValue[PIECE_TYPE_NB + 1] = { 100, 325, 335, 500, 975, 0, 0 };

/* Rights that survive a piece leaving / entering a square. */
static int CastlingRightsMask[64];

/*
 * Cuckoo table of reversible moves, used to notice that a draw by repetition
 * is available without searching for it.  Each entry holds the zobrist delta a
 * single reversible move makes, so XOR-ing the current key with the key of an
 * earlier position tells us in one lookup whether one move would return there.
 * Two hash functions and cuckoo insertion keep every entry findable in at
 * most two probes.
 */
#define CUCKOO_SIZE 8192
static U64  Cuckoo[CUCKOO_SIZE];
static Move CuckooMove[CUCKOO_SIZE];

static inline int cuckoo_h1(U64 k) { return (int)(k & (CUCKOO_SIZE - 1)); }
static inline int cuckoo_h2(U64 k) { return (int)((k >> 16) & (CUCKOO_SIZE - 1)); }

static void cuckoo_init(void) {
    memset(Cuckoo, 0, sizeof(Cuckoo));
    memset(CuckooMove, 0, sizeof(CuckooMove));
    for (int pc = 0; pc < PIECE_NB; pc++) {
        if (pc == NO_PIECE) continue;
        int pt = piece_type(pc);
        if (pt == PAWN) continue;              /* a pawn move is never reversible */
        for (int s1 = 0; s1 < 64; s1++)
            for (int s2 = s1 + 1; s2 < 64; s2++) {
                if (!(attacks_bb(pt, s1, 0) & bb_of(s2))) continue;
                Move move = make_move(s1, s2);
                U64 key = ZobristPiece[pc][s1] ^ ZobristPiece[pc][s2] ^ ZobristSide;
                int i = cuckoo_h1(key);
                for (;;) {
                    U64 tk = Cuckoo[i];   Cuckoo[i] = key;       key = tk;
                    Move tm = CuckooMove[i]; CuckooMove[i] = move; move = tm;
                    if (move == MOVE_NONE) break;
                    i = (i == cuckoo_h1(key)) ? cuckoo_h2(key) : cuckoo_h1(key);
                }
            }
    }
}

static U64 zrng_state = 0x9E3779B97F4A7C15ULL;
static U64 zrng(void) {
    zrng_state ^= zrng_state >> 12;
    zrng_state ^= zrng_state << 25;
    zrng_state ^= zrng_state >> 27;
    return zrng_state * 2685821657736338717ULL;
}

void pos_init_zobrist(void) {
    static int done = 0;
    if (done) return;
    done = 1;
    zrng_state = 0x9E3779B97F4A7C15ULL;
    for (int pc = 0; pc < PIECE_NB; pc++)
        for (int sq = 0; sq < 64; sq++)
            ZobristPiece[pc][sq] = zrng();
    for (int f = 0; f < 8; f++) ZobristEp[f] = zrng();
    for (int c = 0; c < 16; c++) ZobristCastling[c] = zrng();
    ZobristSide = zrng();

    bb_init();
    cuckoo_init();

    for (int sq = 0; sq < 64; sq++) CastlingRightsMask[sq] = 0;
    CastlingRightsMask[4]  = WHITE_OO | WHITE_OOO;   /* e1 */
    CastlingRightsMask[0]  = WHITE_OOO;              /* a1 */
    CastlingRightsMask[7]  = WHITE_OO;               /* h1 */
    CastlingRightsMask[60] = BLACK_OO | BLACK_OOO;   /* e8 */
    CastlingRightsMask[56] = BLACK_OOO;              /* a8 */
    CastlingRightsMask[63] = BLACK_OO;               /* h8 */
}

/* ------------------------------------------------------------------ */
/* piece placement                                                     */
/* ------------------------------------------------------------------ */

/* Fold one piece on one square into the material-subset keys.  A king counts
 * as both a minor and a major piece: where it stands changes what both are
 * worth. */
static inline void toggle_subset_keys(Position *pos, int pc, int sq) {
    int pt = piece_type(pc);
    U64 z = ZobristPiece[pc][sq];
    if (pt == PAWN) { pos->pawnKey ^= z; return; }
    pos->nonPawnKey[piece_color(pc)] ^= z;
    if (pt == KNIGHT || pt == BISHOP) pos->minorKey ^= z;
    else if (pt == ROOK || pt == QUEEN) pos->majorKey ^= z;
    else { pos->minorKey ^= z; pos->majorKey ^= z; }
}

static inline void put_piece(Position *pos, int pc, int sq) {
    U64 b = bb_of(sq);
    pos->board[sq] = (uint8_t)pc;
    pos->byType[piece_type(pc)] |= b;
    pos->byColor[piece_color(pc)] |= b;
    pos->occ |= b;
    pos->pieceCount[pc]++;
    pos->key ^= ZobristPiece[pc][sq];
    toggle_subset_keys(pos, pc, sq);
}

static inline void remove_piece(Position *pos, int sq) {
    int pc = pos->board[sq];
    U64 b = bb_of(sq);
    pos->board[sq] = NO_PIECE;
    pos->byType[piece_type(pc)] ^= b;
    pos->byColor[piece_color(pc)] ^= b;
    pos->occ ^= b;
    pos->pieceCount[pc]--;
    pos->key ^= ZobristPiece[pc][sq];
    toggle_subset_keys(pos, pc, sq);
}

static inline void move_piece(Position *pos, int from, int to) {
    int pc = pos->board[from];
    U64 b = bb_of(from) | bb_of(to);
    pos->board[from] = NO_PIECE;
    pos->board[to] = (uint8_t)pc;
    pos->byType[piece_type(pc)] ^= b;
    pos->byColor[piece_color(pc)] ^= b;
    pos->occ ^= b;
    pos->key ^= ZobristPiece[pc][from] ^ ZobristPiece[pc][to];
    toggle_subset_keys(pos, pc, from);
    toggle_subset_keys(pos, pc, to);
}

/* ------------------------------------------------------------------ */
/* attacks                                                             */
/* ------------------------------------------------------------------ */

U64 pos_attackers_to(const Position *pos, int sq, U64 occ) {
    return (PawnAttacksBB[BLACK][sq] & pieces_cp(pos, WHITE, PAWN))
         | (PawnAttacksBB[WHITE][sq] & pieces_cp(pos, BLACK, PAWN))
         | (KnightAttacksBB[sq] & pos->byType[KNIGHT])
         | (KingAttacksBB[sq] & pos->byType[KING])
         | (rook_attacks(sq, occ)   & (pos->byType[ROOK]   | pos->byType[QUEEN]))
         | (bishop_attacks(sq, occ) & (pos->byType[BISHOP] | pos->byType[QUEEN]));
}

/* Own pieces that shield `ksq` from an enemy slider, plus those sliders. */
static U64 slider_blockers(const Position *pos, U64 sliders, int ksq, U64 *pinners) {
    U64 blockers = 0;
    *pinners = 0;
    U64 snipers = ((PseudoAttacks[ROOK][ksq] & (pos->byType[ROOK] | pos->byType[QUEEN]))
                 | (PseudoAttacks[BISHOP][ksq] & (pos->byType[BISHOP] | pos->byType[QUEEN])))
                 & sliders;
    U64 occupancy = pos->occ ^ snipers;
    while (snipers) {
        int sniperSq = pop_lsb(&snipers);
        U64 b = BetweenBB[ksq][sniperSq] & occupancy;
        if (b && !(b & (b - 1))) {       /* exactly one blocker */
            blockers |= b;
            if (b & pos->byColor[piece_color(pos->board[ksq])])
                *pinners |= bb_of(sniperSq);
        }
    }
    return blockers;
}

void pos_update_state(Position *pos) {
    int us = pos->side, them = !us;
    int ksq = lsb(pieces_cp(pos, us, KING));
    pos->checkers = pos_attackers_to(pos, ksq, pos->occ) & pos->byColor[them];

    pos->blockersForKing[us] =
        slider_blockers(pos, pos->byColor[them], ksq, &pos->pinners[them]);
    int theirKsq = lsb(pieces_cp(pos, them, KING));
    pos->blockersForKing[them] =
        slider_blockers(pos, pos->byColor[us], theirKsq, &pos->pinners[us]);

    StateInfo *st = &pos->st[pos->histCount];
    st->checkers = pos->checkers;
    st->blockersForKing[0] = pos->blockersForKing[0];
    st->blockersForKing[1] = pos->blockersForKing[1];
    st->pinners[0] = pos->pinners[0];
    st->pinners[1] = pos->pinners[1];
}

int pos_non_pawn_pieces(const Position *pos, int c) {
    return popcount(pos->byColor[c] & ~(pos->byType[PAWN] | pos->byType[KING]));
}

static inline int aligned(int s1, int s2, int s3) {
    return (LineBB[s1][s2] & bb_of(s3)) != 0;
}

/* ------------------------------------------------------------------ */
/* legality                                                           */
/* ------------------------------------------------------------------ */

int pos_legal(const Position *pos, Move m) {
    int us = pos->side, them = !us;
    int from = move_from(m), to = move_to(m);
    int ksq = lsb(pieces_cp(pos, us, KING));

    if (move_type(m) == MT_EP) {
        int capsq = to - (us == WHITE ? 8 : -8);
        U64 occ = (pos->occ ^ bb_of(from) ^ bb_of(capsq)) | bb_of(to);
        return !(rook_attacks(ksq, occ) & (pieces_cp(pos, them, ROOK) | pieces_cp(pos, them, QUEEN)))
            && !(bishop_attacks(ksq, occ) & (pieces_cp(pos, them, BISHOP) | pieces_cp(pos, them, QUEEN)));
    }

    if (move_type(m) == MT_CASTLE)
        return 1;   /* generated only when fully legal */

    if (from == ksq)
        return !(pos_attackers_to(pos, to, pos->occ ^ bb_of(from)) & pos->byColor[them]);

    /* A non-king move is legal if the piece is not shielding the king, or if
     * it stays on the king/slider line. */
    return !(pos->blockersForKing[us] & bb_of(from)) || aligned(from, to, ksq);
}

int pos_is_capture(const Position *pos, Move m) {
    return (pos->board[move_to(m)] != NO_PIECE && move_type(m) != MT_CASTLE)
        || move_type(m) == MT_EP;
}

/*
 * Validate a move that came from the transposition table / history tables.
 * Only has to guarantee that do_move/undo_move stay consistent; genuine
 * legality (pins, king safety) is then checked by pos_legal().
 */
int pos_pseudo_legal(const Position *pos, Move m) {
    int us = pos->side;
    int from = move_from(m), to = move_to(m);
    int type = move_type(m);
    int pc = pos->board[from];

    if (m == MOVE_NONE || m == MOVE_NULL) return 0;
    if (pc == NO_PIECE || piece_color(pc) != us) return 0;
    if (pos->byColor[us] & bb_of(to)) return 0;
    if (pos->byType[KING] & bb_of(to)) return 0;   /* never capture a king */
    if (from == to) return 0;

    int pt = piece_type(pc);

    if (type == MT_CASTLE) {
        /* Re-derive castling moves and compare. */
        Move list[MAX_MOVES];
        int n = gen_legal(pos, list);
        for (int i = 0; i < n; i++) if (list[i] == m) return 1;
        return 0;
    }

    if (type == MT_EP) {
        if (pt != PAWN || to != pos->ep || pos->ep == SQ_NONE) return 0;
        if (pos->board[to] != NO_PIECE) return 0;
        if (!(PawnAttacksBB[us][from] & bb_of(to))) return 0;
        int capsq = to - (us == WHITE ? 8 : -8);
        if (pos->board[capsq] != make_piece(!us, PAWN)) return 0;
        return pos->checkers ? pos_legal(pos, m) : 1;
    }

    if (type == MT_PROMO) {
        if (pt != PAWN || rel_rank(us, to) != 7) return 0;
    } else if (pt == PAWN && rel_rank(us, to) == 7) {
        return 0;   /* a pawn reaching the last rank must promote */
    }

    if (pt == PAWN) {
        int up = us == WHITE ? 8 : -8;
        if (PawnAttacksBB[us][from] & bb_of(to)) {
            if (pos->board[to] == NO_PIECE) return 0;
        } else if (to == from + up) {
            if (pos->board[to] != NO_PIECE) return 0;
        } else if (to == from + 2 * up && rel_rank(us, from) == 1) {
            if (pos->board[to] != NO_PIECE || pos->board[from + up] != NO_PIECE) return 0;
        } else {
            return 0;
        }
    } else {
        if (!(attacks_bb(pt, from, pos->occ) & bb_of(to))) return 0;
    }

    /* Must resolve an existing check. */
    if (pos->checkers) {
        if (pt != KING) {
            if (pos->checkers & (pos->checkers - 1)) return 0;   /* double check */
            if (!((BetweenBB[lsb(pieces_cp(pos, us, KING))][lsb(pos->checkers)]
                   | pos->checkers) & bb_of(to)))
                return 0;
        }
    }
    return 1;
}

int pos_gives_check(const Position *pos, Move m) {
    int us = pos->side, them = !us;
    int from = move_from(m), to = move_to(m);
    int ksq = lsb(pieces_cp(pos, them, KING));
    int pt = piece_type(pos->board[from]);
    U64 occ = pos->occ;

    /* direct check */
    U64 checkSq;
    if (pt == PAWN)      checkSq = PawnAttacksBB[them][ksq];
    else if (pt == KING) checkSq = 0;
    else                 checkSq = attacks_bb(pt, ksq, occ);
    if (checkSq & bb_of(to)) return 1;

    /* discovered check */
    if ((pos->blockersForKing[them] & bb_of(from)) && !aligned(from, to, ksq))
        return 1;

    switch (move_type(m)) {
        case MT_NORMAL:
            return 0;
        case MT_PROMO:
            return (attacks_bb(move_promo(m), to, occ ^ bb_of(from)) & bb_of(ksq)) != 0;
        case MT_EP: {
            int capsq = to - (us == WHITE ? 8 : -8);
            U64 b = (occ ^ bb_of(from) ^ bb_of(capsq)) | bb_of(to);
            return ((rook_attacks(ksq, b) & (pieces_cp(pos, us, ROOK) | pieces_cp(pos, us, QUEEN)))
                 || (bishop_attacks(ksq, b) & (pieces_cp(pos, us, BISHOP) | pieces_cp(pos, us, QUEEN)))) != 0;
        }
        default: {   /* castling: does the rook check? */
            int rookTo = (to > from) ? to - 1 : to + 1;
            U64 b = (occ ^ bb_of(from)) | bb_of(to);
            return (rook_attacks(rookTo, b) & bb_of(ksq)) != 0;
        }
    }
}

/* ------------------------------------------------------------------ */
/* make / unmake                                                       */
/* ------------------------------------------------------------------ */

void pos_do_move(Position *pos, Move m) {
    int us = pos->side, them = !us;
    int from = move_from(m), to = move_to(m);
    int type = move_type(m);
    int pc = pos->board[from];
    int pt = piece_type(pc);
    int captured = NO_PIECE;

    pos->histCount++;
    StateInfo *st = &pos->st[pos->histCount];
    st->move = m;

    pos->key ^= ZobristSide;
    if (pos->ep != SQ_NONE) {
        pos->key ^= ZobristEp[file_of(pos->ep)];
        pos->ep = SQ_NONE;
    }
    pos->halfmove++;
    pos->pliesFromNull++;

    if (type == MT_CASTLE) {
        int rookFrom, rookTo;
        if (to > from) { rookFrom = from + 3; rookTo = to - 1; }
        else           { rookFrom = from - 4; rookTo = to + 1; }
        move_piece(pos, from, to);
        move_piece(pos, rookFrom, rookTo);
    } else {
        if (type == MT_EP) {
            int capsq = to - (us == WHITE ? 8 : -8);
            captured = pos->board[capsq];
            remove_piece(pos, capsq);
        } else if (pos->board[to] != NO_PIECE) {
            captured = pos->board[to];
            remove_piece(pos, to);
        }

        if (captured != NO_PIECE) pos->halfmove = 0;

        if (pt == PAWN) {
            pos->halfmove = 0;
            if (type == MT_PROMO) {
                remove_piece(pos, from);
                put_piece(pos, make_piece(us, move_promo(m)), to);
            } else {
                move_piece(pos, from, to);
                if ((to ^ from) == 16) {
                    int epSq = (from + to) / 2;
                    /* Only record an ep square that can actually be used. */
                    if (PawnAttacksBB[us][epSq] & pieces_cp(pos, them, PAWN)) {
                        pos->ep = epSq;
                        pos->key ^= ZobristEp[file_of(epSq)];
                    }
                }
            }
        } else {
            move_piece(pos, from, to);
        }
    }

    int cr = pos->castling & ~(CastlingRightsMask[from] | CastlingRightsMask[to]);
    if (cr != pos->castling) {
        pos->key ^= ZobristCastling[pos->castling] ^ ZobristCastling[cr];
        pos->castling = cr;
    }

    pos->side = them;

    st->key = pos->key;
    st->pawnKey = pos->pawnKey;
    st->nonPawnKey[WHITE] = pos->nonPawnKey[WHITE];
    st->nonPawnKey[BLACK] = pos->nonPawnKey[BLACK];
    st->minorKey = pos->minorKey;
    st->majorKey = pos->majorKey;
    st->castling = pos->castling;
    st->ep = pos->ep;
    st->halfmove = pos->halfmove;
    st->pliesFromNull = pos->pliesFromNull;
    st->captured = captured;

    pos_update_state(pos);
}

void pos_undo_move(Position *pos, Move m) {
    int from = move_from(m), to = move_to(m);
    int type = move_type(m);
    StateInfo *st = &pos->st[pos->histCount];
    int captured = st->captured;

    pos->side = !pos->side;
    int us = pos->side;

    if (type == MT_CASTLE) {
        int rookFrom, rookTo;
        if (to > from) { rookFrom = from + 3; rookTo = to - 1; }
        else           { rookFrom = from - 4; rookTo = to + 1; }
        move_piece(pos, to, from);
        move_piece(pos, rookTo, rookFrom);
    } else {
        if (type == MT_PROMO) {
            remove_piece(pos, to);
            put_piece(pos, make_piece(us, PAWN), from);
        } else {
            move_piece(pos, to, from);
        }
        if (captured != NO_PIECE) {
            int capsq = (type == MT_EP) ? to - (us == WHITE ? 8 : -8) : to;
            put_piece(pos, captured, capsq);
        }
    }

    pos->histCount--;
    StateInfo *prev = &pos->st[pos->histCount];
    pos->key = prev->key;
    pos->pawnKey = prev->pawnKey;
    pos->nonPawnKey[WHITE] = prev->nonPawnKey[WHITE];
    pos->nonPawnKey[BLACK] = prev->nonPawnKey[BLACK];
    pos->minorKey = prev->minorKey;
    pos->majorKey = prev->majorKey;
    pos->castling = prev->castling;
    pos->ep = prev->ep;
    pos->halfmove = prev->halfmove;
    pos->pliesFromNull = prev->pliesFromNull;
    pos->checkers = prev->checkers;
    pos->blockersForKing[0] = prev->blockersForKing[0];
    pos->blockersForKing[1] = prev->blockersForKing[1];
    pos->pinners[0] = prev->pinners[0];
    pos->pinners[1] = prev->pinners[1];
}

void pos_do_null(Position *pos) {
    pos->histCount++;
    StateInfo *st = &pos->st[pos->histCount];
    st->move = MOVE_NULL;
    st->captured = NO_PIECE;

    pos->key ^= ZobristSide;
    if (pos->ep != SQ_NONE) {
        pos->key ^= ZobristEp[file_of(pos->ep)];
        pos->ep = SQ_NONE;
    }
    pos->halfmove++;
    pos->pliesFromNull = 0;
    pos->side = !pos->side;

    st->key = pos->key;
    st->pawnKey = pos->pawnKey;
    st->nonPawnKey[WHITE] = pos->nonPawnKey[WHITE];
    st->nonPawnKey[BLACK] = pos->nonPawnKey[BLACK];
    st->minorKey = pos->minorKey;
    st->majorKey = pos->majorKey;
    st->castling = pos->castling;
    st->ep = pos->ep;
    st->halfmove = pos->halfmove;
    st->pliesFromNull = pos->pliesFromNull;

    pos_update_state(pos);
}

void pos_undo_null(Position *pos) {
    pos->histCount--;
    StateInfo *prev = &pos->st[pos->histCount];
    pos->side = !pos->side;
    pos->key = prev->key;
    pos->pawnKey = prev->pawnKey;
    pos->nonPawnKey[WHITE] = prev->nonPawnKey[WHITE];
    pos->nonPawnKey[BLACK] = prev->nonPawnKey[BLACK];
    pos->minorKey = prev->minorKey;
    pos->majorKey = prev->majorKey;
    pos->castling = prev->castling;
    pos->ep = prev->ep;
    pos->halfmove = prev->halfmove;
    pos->pliesFromNull = prev->pliesFromNull;
    pos->checkers = prev->checkers;
    pos->blockersForKing[0] = prev->blockersForKing[0];
    pos->blockersForKing[1] = prev->blockersForKing[1];
    pos->pinners[0] = prev->pinners[0];
    pos->pinners[1] = prev->pinners[1];
}

/* ------------------------------------------------------------------ */
/* draws                                                               */
/* ------------------------------------------------------------------ */

int pos_insufficient_material(const Position *pos) {
    if (pos->byType[PAWN] | pos->byType[ROOK] | pos->byType[QUEEN]) return 0;
    int wn = popcount(pieces_cp(pos, WHITE, KNIGHT)), wb = popcount(pieces_cp(pos, WHITE, BISHOP));
    int bn = popcount(pieces_cp(pos, BLACK, KNIGHT)), bb = popcount(pieces_cp(pos, BLACK, BISHOP));
    if (wn + wb <= 1 && bn + bb <= 1) return 1;          /* K(+minor) vs K(+minor) */
    if (wb == 0 && bb == 0 && wn <= 2 && bn <= 2 && (wn == 0 || bn == 0))
        return 1;                                        /* KNN vs K is a draw */
    return 0;
}

/* Two-fold inside the search tree, three-fold counting game history. */
int pos_is_draw(const Position *pos) {
    if (pos->halfmove >= 100) {
        if (!pos->checkers) return 1;
        Move list[MAX_MOVES];
        if (gen_evasions(pos, list) > 0) return 1;
        return 0;      /* checkmate takes precedence over the 50-move rule */
    }
    if (pos_insufficient_material(pos)) return 1;

    int end = pos->halfmove < pos->histCount ? pos->halfmove : pos->histCount;
    if (end < 4) return 0;
    int rootHist = pos->rootHist;
    int reps = 0;
    for (int i = 4; i <= end; i += 2) {
        int idx = pos->histCount - i;
        if (pos->st[idx].key == pos->key) {
            /* A repetition with a position inside the search tree, or a second
             * repetition of a pre-root position, is a draw. */
            if (idx >= rootHist) return 1;
            if (++reps == 2) return 1;
        }
    }
    return 0;
}

/*
 * True when some legal move from here reaches a position that already
 * occurred.  Only cycles that lie entirely inside the search tree are
 * reported: those are unambiguously worth a draw to the side to move.  A
 * cycle that reaches back past the root may be one the opponent would have
 * avoided, so it is left alone.
 */
int pos_has_game_cycle(const Position *pos, int ply) {
    /* Walking back past a null move would compare positions with different
     * sides to move, and past the start of the array would read rubbish. */
    int end = pos->halfmove < pos->pliesFromNull ? pos->halfmove : pos->pliesFromNull;
    if (end > pos->histCount) end = pos->histCount;
    if (end < 3) return 0;

    U64 originalKey = pos->key;
    for (int i = 3; i <= end; i += 2) {
        if (i > ply) break;              /* the cycle would leave the tree */
        U64 moveKey = originalKey ^ pos->st[pos->histCount - i].key;
        int j = cuckoo_h1(moveKey);
        if (Cuckoo[j] != moveKey) {
            j = cuckoo_h2(moveKey);
            if (Cuckoo[j] != moveKey) continue;
        }
        Move m = CuckooMove[j];
        int s1 = move_from(m), s2 = move_to(m);
        /* the path has to be clear; the mover itself sits on one of the ends */
        if ((BetweenBB[s1][s2] ^ bb_of(s2)) & pos->occ) continue;
        int occupied = pos->board[s1] != NO_PIECE ? s1 : s2;
        if (pos->board[occupied] == NO_PIECE) continue;
        if (piece_color(pos->board[occupied]) != pos->side) continue;
        return 1;
    }
    return 0;
}

/* ------------------------------------------------------------------ */
/* static exchange evaluation                                          */
/* ------------------------------------------------------------------ */

int pos_see_ge(const Position *pos, Move m, int threshold) {
    if (move_type(m) == MT_CASTLE) return 0 >= threshold;

    int from = move_from(m), to = move_to(m);
    int nextVictim = move_type(m) == MT_PROMO ? move_promo(m)
                                             : piece_type(pos->board[from]);

    int swap;
    if (move_type(m) == MT_EP)
        swap = SeeValue[PAWN];
    else
        swap = SeeValue[pos->board[to] == NO_PIECE ? PIECE_TYPE_NB : piece_type(pos->board[to])];

    if (move_type(m) == MT_PROMO)
        swap += SeeValue[move_promo(m)] - SeeValue[PAWN];

    swap -= threshold;
    if (swap < 0) return 0;

    swap = SeeValue[nextVictim] - swap;
    if (swap <= 0) return 1;

    U64 occ = (pos->occ ^ bb_of(from)) | bb_of(to);
    if (move_type(m) == MT_EP)
        occ ^= bb_of(to - (pos->side == WHITE ? 8 : -8));

    int stm = !pos->side;
    U64 attackers = pos_attackers_to(pos, to, occ) & occ;
    int res = 1;

    while (1) {
        U64 ourAttackers = attackers & pos->byColor[stm];

        /* Pieces pinned against our king by a piece that is still on the board
         * may not legally join the exchange. */
        if (pos->pinners[!stm] & occ)
            ourAttackers &= ~pos->blockersForKing[stm];

        if (!ourAttackers) break;

        res ^= 1;

        U64 b;
        if ((b = ourAttackers & pos->byType[PAWN])) {
            if ((swap = SeeValue[PAWN] - swap) < res) break;
            occ ^= bb_of(lsb(b));
            attackers |= bishop_attacks(to, occ) & (pos->byType[BISHOP] | pos->byType[QUEEN]);
        } else if ((b = ourAttackers & pos->byType[KNIGHT])) {
            if ((swap = SeeValue[KNIGHT] - swap) < res) break;
            occ ^= bb_of(lsb(b));
        } else if ((b = ourAttackers & pos->byType[BISHOP])) {
            if ((swap = SeeValue[BISHOP] - swap) < res) break;
            occ ^= bb_of(lsb(b));
            attackers |= bishop_attacks(to, occ) & (pos->byType[BISHOP] | pos->byType[QUEEN]);
        } else if ((b = ourAttackers & pos->byType[ROOK])) {
            if ((swap = SeeValue[ROOK] - swap) < res) break;
            occ ^= bb_of(lsb(b));
            attackers |= rook_attacks(to, occ) & (pos->byType[ROOK] | pos->byType[QUEEN]);
        } else if ((b = ourAttackers & pos->byType[QUEEN])) {
            if ((swap = SeeValue[QUEEN] - swap) < res) break;
            occ ^= bb_of(lsb(b));
            attackers |= (bishop_attacks(to, occ) & (pos->byType[BISHOP] | pos->byType[QUEEN]))
                       | (rook_attacks(to, occ) & (pos->byType[ROOK] | pos->byType[QUEEN]));
        } else {
            /* king capture: only valid if the opponent has no attacker left */
            return (attackers & ~pos->byColor[stm]) ? res ^ 1 : res;
        }
        attackers &= occ;
        stm = !stm;
    }
    return res;
}

/* ------------------------------------------------------------------ */
/* FEN and move notation                                               */
/* ------------------------------------------------------------------ */

static const char PieceChars[] = "PNBRQKpnbrqk";
#define POS_START_FEN "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

static int set_fen(Position *pos, const char *fen) {
    memset(pos, 0, sizeof(Position));
    for (int i = 0; i < 64; i++) pos->board[i] = NO_PIECE;
    pos->ep = SQ_NONE;

    const char *p = fen;
    int rank = 7, file = 0;
    for (; *p && *p != ' '; p++) {
        if (*p == '/') { rank--; file = 0; continue; }
        if (isdigit((unsigned char)*p)) { file += *p - '0'; continue; }
        const char *q = strchr(PieceChars, *p);
        if (!q) return 0;
        if (rank < 0 || rank > 7 || file < 0 || file > 7) return 0;
        put_piece(pos, (int)(q - PieceChars), make_sq(file, rank));
        file++;
    }
    while (*p == ' ') p++;

    pos->side = (*p == 'b') ? BLACK : WHITE;
    if (*p) p++;
    while (*p == ' ') p++;

    for (; *p && *p != ' '; p++) {
        switch (*p) {
            case 'K': pos->castling |= WHITE_OO; break;
            case 'Q': pos->castling |= WHITE_OOO; break;
            case 'k': pos->castling |= BLACK_OO; break;
            case 'q': pos->castling |= BLACK_OOO; break;
            default: break;
        }
    }
    while (*p == ' ') p++;

    if (*p && *p != '-') {
        int f = p[0] - 'a', r = p[1] - '1';
        if (f >= 0 && f < 8 && r >= 0 && r < 8) {
            int epSq = make_sq(f, r);
            /* keep it only if the side to move can actually capture there */
            if (PawnAttacksBB[!pos->side][epSq] & pieces_cp(pos, pos->side, PAWN))
                pos->ep = epSq;
        }
        while (*p && *p != ' ') p++;
    } else if (*p) {
        p++;
    }
    while (*p == ' ') p++;

    pos->halfmove = 0;
    if (isdigit((unsigned char)*p)) pos->halfmove = atoi(p);

    /* drop castling rights that the position cannot support */
    if (pos->board[4] != W_KING)  pos->castling &= ~(WHITE_OO | WHITE_OOO);
    if (pos->board[60] != B_KING) pos->castling &= ~(BLACK_OO | BLACK_OOO);
    if (pos->board[7] != W_ROOK)  pos->castling &= ~WHITE_OO;
    if (pos->board[0] != W_ROOK)  pos->castling &= ~WHITE_OOO;
    if (pos->board[63] != B_ROOK) pos->castling &= ~BLACK_OO;
    if (pos->board[56] != B_ROOK) pos->castling &= ~BLACK_OOO;

    if (pos->side == BLACK) pos->key ^= ZobristSide;
    pos->key ^= ZobristCastling[pos->castling];
    if (pos->ep != SQ_NONE) pos->key ^= ZobristEp[file_of(pos->ep)];

    /* Reject positions the engine could not legally be asked about: there
     * must be exactly one king per side and the side that just moved may not
     * be left in check (this also rules out adjacent kings). */
    if (popcount(pieces_cp(pos, WHITE, KING)) != 1) return 0;
    if (popcount(pieces_cp(pos, BLACK, KING)) != 1) return 0;
    if (pos->byType[PAWN] & (RankBB[0] | RankBB[7])) return 0;
    {
        int theirKing = lsb(pieces_cp(pos, !pos->side, KING));
        if (pos_attackers_to(pos, theirKing, pos->occ) & pos->byColor[pos->side])
            return 0;
    }

    pos->histCount = 0;
    pos->rootHist = 0;
    StateInfo *st = &pos->st[0];
    st->key = pos->key;
    st->pawnKey = pos->pawnKey;
    st->nonPawnKey[WHITE] = pos->nonPawnKey[WHITE];
    st->nonPawnKey[BLACK] = pos->nonPawnKey[BLACK];
    st->minorKey = pos->minorKey;
    st->majorKey = pos->majorKey;
    st->castling = pos->castling;
    st->ep = pos->ep;
    st->halfmove = pos->halfmove;
    st->pliesFromNull = pos->pliesFromNull;
    st->captured = NO_PIECE;
    st->move = MOVE_NONE;
    pos_update_state(pos);
    return 1;
}

/* On a bad FEN the start position is left behind, so the engine is never
 * holding an unusable board. */
int pos_set_fen(Position *pos, const char *fen) {
    if (set_fen(pos, fen)) return 1;
    set_fen(pos, POS_START_FEN);
    return 0;
}

void pos_get_fen(const Position *pos, char *out) {
    char *o = out;
    for (int r = 7; r >= 0; r--) {
        int empty = 0;
        for (int f = 0; f < 8; f++) {
            int pc = pos->board[make_sq(f, r)];
            if (pc == NO_PIECE) { empty++; continue; }
            if (empty) { *o++ = (char)('0' + empty); empty = 0; }
            *o++ = PieceChars[pc];
        }
        if (empty) *o++ = (char)('0' + empty);
        if (r) *o++ = '/';
    }
    *o++ = ' ';
    *o++ = pos->side == WHITE ? 'w' : 'b';
    *o++ = ' ';
    if (!pos->castling) *o++ = '-';
    else {
        if (pos->castling & WHITE_OO)  *o++ = 'K';
        if (pos->castling & WHITE_OOO) *o++ = 'Q';
        if (pos->castling & BLACK_OO)  *o++ = 'k';
        if (pos->castling & BLACK_OOO) *o++ = 'q';
    }
    *o++ = ' ';
    if (pos->ep == SQ_NONE) *o++ = '-';
    else { *o++ = (char)('a' + file_of(pos->ep)); *o++ = (char)('1' + rank_of(pos->ep)); }
    sprintf(o, " %d 1", pos->halfmove);
}

void move_to_uci(Move m, char *out) {
    if (m == MOVE_NONE || m == MOVE_NULL) { strcpy(out, "0000"); return; }
    int from = move_from(m), to = move_to(m);
    out[0] = (char)('a' + file_of(from));
    out[1] = (char)('1' + rank_of(from));
    out[2] = (char)('a' + file_of(to));
    out[3] = (char)('1' + rank_of(to));
    if (move_type(m) == MT_PROMO) {
        out[4] = "nbrq"[move_promo(m) - KNIGHT];
        out[5] = 0;
    } else {
        out[4] = 0;
    }
}

Move move_from_uci(const Position *pos, const char *s) {
    Move list[MAX_MOVES];
    int n = gen_legal(pos, list);
    char buf[8];
    for (int i = 0; i < n; i++) {
        move_to_uci(list[i], buf);
        if (!strcmp(buf, s)) return list[i];
    }
    return MOVE_NONE;
}

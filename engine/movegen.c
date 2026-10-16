/*
 * movegen.c -- move generation.
 *
 * The generators emit pseudo-legal moves which are then filtered through
 * pos_legal(), so every public gen_* function returns strictly legal moves.
 *
 * "noisy" = captures (including en passant) + all promotions.
 * "quiet" = everything else, including castling.
 * The two sets partition the legal moves, which is what the move picker needs.
 */
#include "ce.h"

#define EMIT(mv) (*list++ = (mv))

/* Squares a capture may land on.  Kings are excluded so that a malformed
 * position can never produce a king capture, which would leave the board
 * without a king and break everything downstream. */
#define capturable(pos) (~(pos)->byColor[(pos)->side] & ~(pos)->byType[KING])

static Move *emit_promos(Move *list, int from, int to) {
    EMIT(make_promo(from, to, QUEEN));
    EMIT(make_promo(from, to, KNIGHT));
    EMIT(make_promo(from, to, ROOK));
    EMIT(make_promo(from, to, BISHOP));
    return list;
}

/* Captures and promotions. `target` limits destination squares (evasions). */
static Move *gen_pawn_noisy(const Position *pos, Move *list, U64 target) {
    int us = pos->side, them = !us;
    int up = us == WHITE ? 8 : -8;
    U64 pawns = pieces_cp(pos, us, PAWN);
    U64 rank7 = us == WHITE ? RankBB[6] : RankBB[1];
    U64 onRank7 = pawns & rank7;
    U64 rest = pawns & ~rank7;
    U64 enemies = pos->byColor[them] & ~pos->byType[KING] & target;
    int dL = us == WHITE ? 7 : -9;
    int dR = us == WHITE ? 9 : -7;

    /* promotion pushes */
    if (onRank7) {
        U64 b = pawn_push_bb(us, onRank7) & ~pos->occ & target;
        while (b) { int to = pop_lsb(&b); list = emit_promos(list, to - up, to); }

        U64 capL = (us == WHITE ? (onRank7 & ~FileBB[0]) << 7 : (onRank7 & ~FileBB[0]) >> 9) & enemies;
        U64 capR = (us == WHITE ? (onRank7 & ~FileBB[7]) << 9 : (onRank7 & ~FileBB[7]) >> 7) & enemies;
        while (capL) { int to = pop_lsb(&capL); list = emit_promos(list, to - dL, to); }
        while (capR) { int to = pop_lsb(&capR); list = emit_promos(list, to - dR, to); }
    }

    U64 capL = (us == WHITE ? (rest & ~FileBB[0]) << 7 : (rest & ~FileBB[0]) >> 9) & enemies;
    U64 capR = (us == WHITE ? (rest & ~FileBB[7]) << 9 : (rest & ~FileBB[7]) >> 7) & enemies;
    while (capL) { int to = pop_lsb(&capL); EMIT(make_move(to - dL, to)); }
    while (capR) { int to = pop_lsb(&capR); EMIT(make_move(to - dR, to)); }

    if (pos->ep != SQ_NONE) {
        int capsq = pos->ep - up;
        if ((target & bb_of(pos->ep)) || (target & bb_of(capsq))) {
            U64 from = PawnAttacksBB[them][pos->ep] & rest;
            while (from) EMIT(make_move_t(pop_lsb(&from), pos->ep, MT_EP));
        }
    }
    return list;
}

static Move *gen_pawn_quiet(const Position *pos, Move *list, U64 target) {
    int us = pos->side;
    int up = us == WHITE ? 8 : -8;
    U64 pawns = pieces_cp(pos, us, PAWN);
    U64 rank7 = us == WHITE ? RankBB[6] : RankBB[1];
    U64 rank3 = us == WHITE ? RankBB[2] : RankBB[5];
    U64 empty = ~pos->occ;
    U64 rest = pawns & ~rank7;

    U64 b1 = pawn_push_bb(us, rest) & empty;
    U64 b2 = pawn_push_bb(us, b1 & rank3) & empty & target;
    b1 &= target;
    while (b1) { int to = pop_lsb(&b1); EMIT(make_move(to - up, to)); }
    while (b2) { int to = pop_lsb(&b2); EMIT(make_move(to - 2 * up, to)); }
    return list;
}

static Move *gen_piece_moves(const Position *pos, Move *list, U64 target) {
    int us = pos->side;
    for (int pt = KNIGHT; pt <= QUEEN; pt++) {
        U64 b = pieces_cp(pos, us, pt);
        while (b) {
            int from = pop_lsb(&b);
            U64 att = attacks_bb(pt, from, pos->occ) & target;
            while (att) EMIT(make_move(from, pop_lsb(&att)));
        }
    }
    return list;
}

static Move *gen_castling(const Position *pos, Move *list) {
    int us = pos->side, them = !us;
    if (pos->checkers) return list;
    U64 theirs = pos->byColor[them];

    if (us == WHITE) {
        if ((pos->castling & WHITE_OO) && !(pos->occ & 0x60ULL)
            && !(pos_attackers_to(pos, 5, pos->occ) & theirs)
            && !(pos_attackers_to(pos, 6, pos->occ) & theirs))
            EMIT(make_move_t(4, 6, MT_CASTLE));
        if ((pos->castling & WHITE_OOO) && !(pos->occ & 0x0EULL)
            && !(pos_attackers_to(pos, 3, pos->occ) & theirs)
            && !(pos_attackers_to(pos, 2, pos->occ) & theirs))
            EMIT(make_move_t(4, 2, MT_CASTLE));
    } else {
        if ((pos->castling & BLACK_OO) && !(pos->occ & (0x60ULL << 56))
            && !(pos_attackers_to(pos, 61, pos->occ) & theirs)
            && !(pos_attackers_to(pos, 62, pos->occ) & theirs))
            EMIT(make_move_t(60, 62, MT_CASTLE));
        if ((pos->castling & BLACK_OOO) && !(pos->occ & (0x0EULL << 56))
            && !(pos_attackers_to(pos, 59, pos->occ) & theirs)
            && !(pos_attackers_to(pos, 58, pos->occ) & theirs))
            EMIT(make_move_t(60, 58, MT_CASTLE));
    }
    return list;
}

/* Squares a non-king piece may move to in order to answer a single check. */
static U64 evasion_target(const Position *pos) {
    int ksq = lsb(pieces_cp(pos, pos->side, KING));
    int checksq = lsb(pos->checkers);
    return (BetweenBB[checksq][ksq] | pos->checkers) & ~pos->byType[KING];
}

static Move *gen_king_evasions(const Position *pos, Move *list) {
    int us = pos->side;
    int ksq = lsb(pieces_cp(pos, us, KING));
    U64 rays = 0;
    U64 sliders = pos->checkers & ~(pos->byType[KNIGHT] | pos->byType[PAWN]);
    while (sliders) {
        int s = pop_lsb(&sliders);
        rays |= LineBB[s][ksq] & ~bb_of(s);
    }
    U64 att = KingAttacksBB[ksq] & capturable(pos) & ~rays;
    while (att) EMIT(make_move(ksq, pop_lsb(&att)));
    return list;
}

static int filter_legal(const Position *pos, const Move *buf, int n, Move *out) {
    int k = 0;
    for (int i = 0; i < n; i++)
        if (pos_legal(pos, buf[i])) out[k++] = buf[i];
    return k;
}

int gen_evasions(const Position *pos, Move *list) {
    Move buf[MAX_MOVES];
    Move *end = gen_king_evasions(pos, buf);
    if (!(pos->checkers & (pos->checkers - 1))) {
        U64 target = evasion_target(pos);
        end = gen_pawn_noisy(pos, end, target);
        end = gen_pawn_quiet(pos, end, target);
        end = gen_piece_moves(pos, end, target);
    }
    return filter_legal(pos, buf, (int)(end - buf), list);
}

int gen_legal(const Position *pos, Move *list) {
    if (pos->checkers) return gen_evasions(pos, list);
    Move buf[MAX_MOVES];
    Move *end = buf;
    int us = pos->side;
    U64 target = capturable(pos);
    end = gen_pawn_noisy(pos, end, ~0ULL);
    end = gen_pawn_quiet(pos, end, ~0ULL);
    end = gen_piece_moves(pos, end, target);
    int ksq = lsb(pieces_cp(pos, us, KING));
    U64 att = KingAttacksBB[ksq] & target;
    while (att) *end++ = make_move(ksq, pop_lsb(&att));
    end = gen_castling(pos, end);
    return filter_legal(pos, buf, (int)(end - buf), list);
}

int gen_noisy(const Position *pos, Move *list) {
    Move buf[MAX_MOVES];
    Move *end = buf;
    int us = pos->side;

    if (pos->checkers) {
        end = gen_king_evasions(pos, buf);
        /* keep only king captures */
        Move *w = buf;
        for (Move *r = buf; r < end; r++)
            if (pos->board[move_to(*r)] != NO_PIECE) *w++ = *r;
        end = w;
        if (!(pos->checkers & (pos->checkers - 1))) {
            U64 target = evasion_target(pos);
            end = gen_pawn_noisy(pos, end, target);
            end = gen_piece_moves(pos, end, target & pos->byColor[!us] & ~pos->byType[KING]);
        }
        return filter_legal(pos, buf, (int)(end - buf), list);
    }

    U64 enemies = pos->byColor[!us] & ~pos->byType[KING];
    end = gen_pawn_noisy(pos, end, ~0ULL);
    end = gen_piece_moves(pos, end, enemies);
    int ksq = lsb(pieces_cp(pos, us, KING));
    U64 att = KingAttacksBB[ksq] & enemies;
    while (att) *end++ = make_move(ksq, pop_lsb(&att));
    return filter_legal(pos, buf, (int)(end - buf), list);
}

int gen_quiet(const Position *pos, Move *list) {
    Move buf[MAX_MOVES];
    Move *end = buf;
    int us = pos->side;

    if (pos->checkers) {
        end = gen_king_evasions(pos, buf);
        Move *w = buf;
        for (Move *r = buf; r < end; r++)
            if (pos->board[move_to(*r)] == NO_PIECE) *w++ = *r;
        end = w;
        if (!(pos->checkers & (pos->checkers - 1))) {
            U64 target = evasion_target(pos) & ~pos->occ;
            end = gen_pawn_quiet(pos, end, target);
            end = gen_piece_moves(pos, end, target);
        }
        return filter_legal(pos, buf, (int)(end - buf), list);
    }

    U64 empty = ~pos->occ;
    end = gen_pawn_quiet(pos, end, ~0ULL);
    end = gen_piece_moves(pos, end, empty);
    int ksq = lsb(pieces_cp(pos, us, KING));
    U64 att = KingAttacksBB[ksq] & empty;
    while (att) *end++ = make_move(ksq, pop_lsb(&att));
    end = gen_castling(pos, end);
    return filter_legal(pos, buf, (int)(end - buf), list);
}

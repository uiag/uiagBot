/*
 * extract.c -- turn the lichess evaluation dump into packed training records.
 *
 * Input is lichess_db_eval.jsonl, one position per line:
 *
 *   {"fen":"<4-field fen>","evals":[{"pvs":[{"cp":69,"line":"..."},...],
 *                                    "knodes":N,"depth":D}, ...]}
 *
 * The deepest `evals` entry is the one taken, and within it the first `pvs`
 * entry, which the dump already orders best-move-first.  Scores are
 * centipawns from White's point of view.
 *
 * Output is a stream of fixed 32-byte records (see Rec below).  A fixed record
 * size is what lets the trainer memory-map the file and index into it without
 * an offset table, and 32 bytes is one cache line's half, so a shuffled read
 * touches one line per sample.
 *
 * Only the attack tables are linked in, not the Position struct: the engine's
 * `set_fen` memsets 2048 StateInfos (about 230 KB) on every call, which over
 * two hundred million positions is tens of terabytes of pointless zeroing.
 * The board is parsed straight into bitboards here instead.
 *
 *   extract <input.jsonl> <output.bin> [startByte] [endByte]
 *
 * A byte range that does not begin at a line boundary is advanced to the next
 * one, and the last line is read to its end past `endByte`, so N processes
 * over N adjacent ranges together cover every line exactly once.
 */
#include "../engine/ce.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* ------------------------------------------------------------------ */
/* the record                                                          */
/* ------------------------------------------------------------------ */

#pragma pack(push, 1)
typedef struct {
    uint64_t occ;          /* occupied squares                               */
    uint8_t  pieces[16];   /* 4-bit piece codes, two per byte, in occ order  */
    int16_t  score;        /* centipawns, White's point of view              */
    uint8_t  stm;          /* 0 = white to move                              */
    uint8_t  flags;        /* bit 0: the score is a mate distance            */
    uint8_t  wksq, bksq;   /* king squares, for the king buckets             */
    uint16_t pad;
} Rec;
#pragma pack(pop)

enum { MATE_SCORE = 16000 };

/* ------------------------------------------------------------------ */
/* a board, without the search's Position                              */
/* ------------------------------------------------------------------ */

typedef struct {
    U64 byType[PIECE_TYPE_NB];
    U64 byColor[COLOR_NB];
    U64 occ;
    uint8_t board[64];
    int side;
    int ep;
} Board;

static const char PieceChars[] = "PNBRQKpnbrqk";

/* Returns 0 on anything it will not vouch for: a malformed board, a missing
 * or doubled king, a pawn on the first or last rank, or the side that just
 * moved left in check. */
static int parse_fen(Board *b, const char *fen, const char *end) {
    memset(b, 0, sizeof(*b));
    memset(b->board, NO_PIECE, 64);
    b->ep = SQ_NONE;

    const char *p = fen;
    int rank = 7, file = 0;
    for (; p < end && *p != ' '; p++) {
        if (*p == '/') { rank--; file = 0; continue; }
        if (*p >= '1' && *p <= '8') { file += *p - '0'; continue; }
        const char *q = strchr(PieceChars, *p);
        if (!q) return 0;
        if (rank < 0 || rank > 7 || file < 0 || file > 7) return 0;
        int pc = (int)(q - PieceChars);
        int sq = make_sq(file, rank);
        b->board[sq] = (uint8_t)pc;
        b->byType[piece_type(pc)] |= bb_of(sq);
        b->byColor[piece_color(pc)] |= bb_of(sq);
        b->occ |= bb_of(sq);
        file++;
    }
    if (rank != 0) return 0;

    while (p < end && *p == ' ') p++;
    if (p >= end) return 0;
    b->side = (*p == 'b') ? BLACK : WHITE;
    p++;

    /* castling rights do not enter the features, so they are skipped */
    while (p < end && *p == ' ') p++;
    while (p < end && *p != ' ') p++;
    while (p < end && *p == ' ') p++;

    if (p < end && *p != '-' && *p != ' ') {
        int f = p[0] - 'a', r = p[1] - '1';
        if (f >= 0 && f < 8 && r >= 0 && r < 8) b->ep = make_sq(f, r);
    }

    if (popcount(b->byType[KING] & b->byColor[WHITE]) != 1) return 0;
    if (popcount(b->byType[KING] & b->byColor[BLACK]) != 1) return 0;
    if (b->byType[PAWN] & (RankBB[0] | RankBB[7])) return 0;
    /* The dump holds a few positions with more than 32 men, which no legal
     * game reaches and which would not fit the record's sixteen bytes of
     * piece codes. */
    if (popcount(b->occ) > 32) return 0;
    return 1;
}

static U64 attackers_to(const Board *b, int sq, U64 occ) {
    return (PawnAttacksBB[WHITE][sq] & b->byType[PAWN] & b->byColor[BLACK])
         | (PawnAttacksBB[BLACK][sq] & b->byType[PAWN] & b->byColor[WHITE])
         | (KnightAttacksBB[sq] & b->byType[KNIGHT])
         | (rook_attacks(sq, occ) & (b->byType[ROOK] | b->byType[QUEEN]))
         | (bishop_attacks(sq, occ) & (b->byType[BISHOP] | b->byType[QUEEN]))
         | (KingAttacksBB[sq] & b->byType[KING]);
}

static int in_check(const Board *b, int c) {
    int ksq = lsb(b->byType[KING] & b->byColor[c]);
    return (attackers_to(b, ksq, b->occ) & b->byColor[!c]) != 0;
}

/* ------------------------------------------------------------------ */
/* json scanning                                                       */
/* ------------------------------------------------------------------ */

/* The dump is machine-generated with a fixed key order, so finding a key by
 * substring is both correct and about twenty times faster than a real parser.
 * The only strings in a line are the FEN and the pv lines, and neither can
 * contain a `"` or any of the keys looked for here. */
static const char *find(const char *p, const char *end, const char *needle, size_t n) {
    if ((size_t)(end - p) < n) return NULL;
    const char *limit = end - n;
    char c0 = needle[0];
    for (; p <= limit; p++)
        if (*p == c0 && !memcmp(p, needle, n)) return p;
    return NULL;
}

static long parse_long(const char *p, const char *end, const char **after) {
    int neg = 0;
    if (p < end && *p == '-') { neg = 1; p++; }
    long v = 0;
    while (p < end && *p >= '0' && *p <= '9') v = v * 10 + (*p++ - '0');
    if (after) *after = p;
    return neg ? -v : v;
}

#define MAX_EVALS 32

typedef struct {
    long records, skippedParse, skippedCheck, skippedCapture, skippedNoEval;
} Stats;

/* Pull one line apart and, if it survives every filter, fill *out. */
static int handle_line(const char *p, const char *end, Rec *out, Stats *st) {
    static const char K_FEN[]   = "\"fen\":\"";
    static const char K_PVS[]   = "\"pvs\":[{";
    static const char K_DEPTH[] = "\"depth\":";
    static const char K_CP[]    = "\"cp\":";
    static const char K_MATE[]  = "\"mate\":";
    static const char K_LINE[]  = "\"line\":\"";

    const char *f = find(p, end, K_FEN, sizeof(K_FEN) - 1);
    if (!f) { st->skippedParse++; return 0; }
    f += sizeof(K_FEN) - 1;
    const char *fenEnd = memchr(f, '"', (size_t)(end - f));
    if (!fenEnd) { st->skippedParse++; return 0; }

    /* The i-th "pvs" belongs to the i-th "depth": one eval object holds
     * exactly one of each, in that order. */
    const char *pvs[MAX_EVALS];
    long depths[MAX_EVALS];
    int n = 0;
    const char *q = fenEnd;
    while (n < MAX_EVALS) {
        const char *a = find(q, end, K_PVS, sizeof(K_PVS) - 1);
        if (!a) break;
        const char *d = find(a, end, K_DEPTH, sizeof(K_DEPTH) - 1);
        if (!d) break;
        pvs[n] = a + sizeof(K_PVS) - 1;
        depths[n] = parse_long(d + sizeof(K_DEPTH) - 1, end, NULL);
        n++;
        q = d;
    }
    if (!n) { st->skippedNoEval++; return 0; }

    int best = 0;
    for (int i = 1; i < n; i++)
        if (depths[i] > depths[best]) best = i;

    /* score: the first pv of the deepest eval */
    const char *v = pvs[best];
    const char *vEnd = v + 64 < end ? v + 64 : end;
    int isMate = 0;
    long score;
    const char *c = find(v, vEnd, K_CP, sizeof(K_CP) - 1);
    const char *m = find(v, vEnd, K_MATE, sizeof(K_MATE) - 1);
    if (c && (!m || c < m)) {
        score = parse_long(c + sizeof(K_CP) - 1, vEnd, NULL);
    } else if (m) {
        long dist = parse_long(m + sizeof(K_MATE) - 1, vEnd, NULL);
        score = dist >= 0 ? MATE_SCORE - dist : -MATE_SCORE - dist;
        isMate = 1;
    } else {
        st->skippedNoEval++;
        return 0;
    }
    if (score > MATE_SCORE) score = MATE_SCORE;
    if (score < -MATE_SCORE) score = -MATE_SCORE;

    Board b;
    if (!parse_fen(&b, f, fenEnd)) { st->skippedParse++; return 0; }

    /* A position where the side to move is in check says nothing useful about
     * a static evaluation: the score belongs to the forced sequence, not to
     * the placement of the pieces.  Same for a position whose best move wins
     * material -- the number is about the capture. */
    if (in_check(&b, b.side)) { st->skippedCheck++; return 0; }
    if (in_check(&b, !b.side)) { st->skippedParse++; return 0; }

    const char *ln = find(v, vEnd + 16 < end ? vEnd + 16 : end,
                          K_LINE, sizeof(K_LINE) - 1);
    if (ln) {
        ln += sizeof(K_LINE) - 1;
        if (end - ln >= 4) {
            int ff = ln[0] - 'a', fr = ln[1] - '1';
            int tf = ln[2] - 'a', tr = ln[3] - '1';
            if (ff >= 0 && ff < 8 && fr >= 0 && fr < 8
                && tf >= 0 && tf < 8 && tr >= 0 && tr < 8) {
                int from = make_sq(ff, fr), to = make_sq(tf, tr);
                int promo = (end - ln >= 5) && strchr("nbrq", ln[4]) != NULL;
                int capture = (b.occ & bb_of(to)) != 0
                           || (to == b.ep && piece_type(b.board[from]) == PAWN);
                if (capture || promo) { st->skippedCapture++; return 0; }
            }
        }
    }

    memset(out, 0, sizeof(*out));
    out->occ = b.occ;
    int i = 0;
    for (U64 bb = b.occ; bb; i++) {
        int sq = pop_lsb(&bb);
        int code = b.board[sq];              /* 0..11, colour * 6 + type */
        if (i & 1) out->pieces[i >> 1] |= (uint8_t)(code << 4);
        else       out->pieces[i >> 1]  = (uint8_t)code;
    }
    out->score = (int16_t)score;
    out->stm = (uint8_t)b.side;
    out->flags = (uint8_t)(isMate ? 1 : 0);
    out->wksq = (uint8_t)lsb(b.byType[KING] & b.byColor[WHITE]);
    out->bksq = (uint8_t)lsb(b.byType[KING] & b.byColor[BLACK]);
    st->records++;
    return 1;
}

/* ------------------------------------------------------------------ */
/* main                                                                */
/* ------------------------------------------------------------------ */

#define IN_BUF  (64u << 20)
#define OUT_BUF (1u << 20)

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: extract <input.jsonl> <output.bin> "
                        "[startByte] [endByte]\n");
        return 2;
    }
    const char *inPath = argv[1], *outPath = argv[2];
    long long startByte = argc > 3 ? atoll(argv[3]) : 0;
    long long endByte   = argc > 4 ? atoll(argv[4]) : -1;

    bb_init();

    FILE *in = fopen(inPath, "rb");
    if (!in) { fprintf(stderr, "cannot open %s\n", inPath); return 1; }
    FILE *out = fopen(outPath, "wb");
    if (!out) { fprintf(stderr, "cannot write %s\n", outPath); return 1; }

    char *buf = (char *)malloc(IN_BUF);
    Rec *recs = (Rec *)malloc(OUT_BUF * sizeof(Rec));
    if (!buf || !recs) { fprintf(stderr, "out of memory\n"); return 1; }

    if (_fseeki64(in, startByte, SEEK_SET)) { fprintf(stderr, "seek failed\n"); return 1; }
    /* A range that starts mid-line belongs to the previous worker. */
    if (startByte > 0) {
        int ch;
        while ((ch = fgetc(in)) != EOF && ch != '\n') { }
    }

    Stats st = {0, 0, 0, 0, 0};
    size_t nOut = 0;
    long long pos = _ftelli64(in);
    size_t held = 0;            /* bytes of a partial line kept at buf[0..] */

    for (;;) {
        size_t want = IN_BUF - held;
        size_t got = fread(buf + held, 1, want, in);
        size_t have = held + got;
        if (!have) break;

        const char *p = buf, *bufEnd = buf + have;
        for (;;) {
            const char *nl = memchr(p, '\n', (size_t)(bufEnd - p));
            if (!nl) break;
            if (nl > p) {
                if (nOut == OUT_BUF) {
                    fwrite(recs, sizeof(Rec), nOut, out);
                    nOut = 0;
                }
                if (handle_line(p, nl, &recs[nOut], &st)) nOut++;
            }
            pos += (nl - p) + 1;
            p = nl + 1;
            if (endByte >= 0 && pos >= endByte) goto done;
        }
        held = (size_t)(bufEnd - p);
        if (held == IN_BUF) {                  /* a single absurd line */
            held = 0;
        } else if (held) {
            memmove(buf, p, held);
        }
        if (got < want && !held) break;
        if (got == 0) break;
    }
done:
    if (nOut) fwrite(recs, sizeof(Rec), nOut, out);
    fclose(out);
    fclose(in);

    fprintf(stderr, "%s: %ld records, skipped %ld parse / %ld check / "
                    "%ld capture / %ld no-eval\n",
            outPath, st.records, st.skippedParse, st.skippedCheck,
            st.skippedCapture, st.skippedNoEval);
    return 0;
}

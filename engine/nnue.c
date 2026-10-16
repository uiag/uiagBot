/*
 * nnue.c -- evaluation with the project's own 769 -> 256 -> 1 network.
 *
 * Feature layout matches the training code (evaluationModel/features.py and
 * dataset.py): index = colour*384 + (pieceType-1)*64 + square with
 * python-chess colours (white = 1), plus input 768 set when white is to move.
 * The first layer is quantised to int32 at QA; the output layer stays in
 * float, which keeps the accumulator exact and the conversion error well
 * under a centipawn.
 */
#include "ce.h"
#include <stdio.h>
#include <stdlib.h>

#ifdef __AVX2__
#include <immintrin.h>
#endif

#define NN_INPUT  769
#define NN_HIDDEN 256
#define NN_STM    768

#if defined(_MSC_VER)
#define ALIGN32 __declspec(align(32))
#else
#define ALIGN32 __attribute__((aligned(32)))
#endif

static ALIGN32 int32_t W1T[NN_INPUT][NN_HIDDEN];   /* feature-major */
static ALIGN32 int32_t B1[NN_HIDDEN];
static ALIGN32 float   W2[NN_HIDDEN];
static float B2;
static float NetScale = 400.0f;
static int   QA = 1024;
static int   NetLoaded = 0;

int nnue_available(void) { return NetLoaded; }

int nnue_load_file(const char *path) {
    NetLoaded = 0;
    FILE *f = fopen(path, "rb");
    if (!f) return 0;

    char magic[4];
    uint32_t version, inputDim, hidden;
    int32_t qa;
    float scale;
    if (fread(magic, 1, 4, f) != 4 || memcmp(magic, "CENN", 4)) { fclose(f); return 0; }
    if (fread(&version, 4, 1, f) != 1 || fread(&inputDim, 4, 1, f) != 1
        || fread(&hidden, 4, 1, f) != 1 || fread(&qa, 4, 1, f) != 1
        || fread(&scale, 4, 1, f) != 1) { fclose(f); return 0; }
    if (version != 1 || inputDim != NN_INPUT || hidden != NN_HIDDEN) { fclose(f); return 0; }

    QA = qa;
    NetScale = scale;
    size_t n = (size_t)NN_INPUT * NN_HIDDEN;
    if (fread(W1T, sizeof(int32_t), n, f) != n) { fclose(f); return 0; }
    if (fread(B1, sizeof(int32_t), NN_HIDDEN, f) != NN_HIDDEN) { fclose(f); return 0; }
    if (fread(W2, sizeof(float), NN_HIDDEN, f) != NN_HIDDEN) { fclose(f); return 0; }
    if (fread(&B2, sizeof(float), 1, f) != 1) { fclose(f); return 0; }
    fclose(f);
    NetLoaded = 1;
    return 1;
}

/* python-chess feature index for our internal colour/type encoding */
static inline int feat_index(int color, int pt, int sq) {
    return (color == WHITE ? 384 : 0) + pt * 64 + sq;
}

int nnue_evaluate(const Position *pos) {
    if (!NetLoaded) return 0;

    ALIGN32 int32_t acc[NN_HIDDEN];
    memcpy(acc, B1, sizeof(acc));

    int feats[34];
    int nf = 0;
    U64 b = pos->occ;
    while (b) {
        int sq = pop_lsb(&b);
        int pc = pos->board[sq];
        feats[nf++] = feat_index(piece_color(pc), piece_type(pc), sq);
    }
    if (pos->side == WHITE) feats[nf++] = NN_STM;

#ifdef __AVX2__
    for (int k = 0; k < nf; k++) {
        const int32_t *w = W1T[feats[k]];
        for (int i = 0; i < NN_HIDDEN; i += 8) {
            __m256i a = _mm256_load_si256((const __m256i *)(acc + i));
            __m256i v = _mm256_load_si256((const __m256i *)(w + i));
            _mm256_store_si256((__m256i *)(acc + i), _mm256_add_epi32(a, v));
        }
    }
    __m256 sum = _mm256_setzero_ps();
    const __m256i zero = _mm256_setzero_si256();
    for (int i = 0; i < NN_HIDDEN; i += 8) {
        __m256i a = _mm256_load_si256((const __m256i *)(acc + i));
        a = _mm256_max_epi32(a, zero);                       /* ReLU */
        __m256 af = _mm256_cvtepi32_ps(a);
        __m256 wf = _mm256_load_ps(W2 + i);
        sum = _mm256_fmadd_ps(af, wf, sum);
    }
    ALIGN32 float buf[8];
    _mm256_store_ps(buf, sum);
    float dot = buf[0] + buf[1] + buf[2] + buf[3] + buf[4] + buf[5] + buf[6] + buf[7];
#else
    for (int k = 0; k < nf; k++) {
        const int32_t *w = W1T[feats[k]];
        for (int i = 0; i < NN_HIDDEN; i++) acc[i] += w[i];
    }
    float dot = 0.0f;
    for (int i = 0; i < NN_HIDDEN; i++)
        if (acc[i] > 0) dot += (float)acc[i] * W2[i];
#endif

    float logit = dot / (float)QA + B2;
    int cp = (int)(logit * NetScale);
    return pos->side == WHITE ? cp : -cp;
}

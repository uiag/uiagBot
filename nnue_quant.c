#include <immintrin.h>
#include <stdint.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

#if defined(_MSC_VER)
#define ALIGN32 __declspec(align(32))
#else
#define ALIGN32 __attribute__((aligned(32)))
#endif

#define INPUT_DIM 769
#define HIDDEN 256

#define SCALE1 128
#define SCALE2 128

ALIGN32 static int16_t W1T[INPUT_DIM][HIDDEN];
ALIGN32 static int32_t B1[HIDDEN];

ALIGN32 static int16_t W2[HIDDEN];
static int32_t B2;

EXPORT void nnue_load(
    const int16_t *w1,
    const int32_t *b1,
    const int16_t *w2,
    int32_t b2
)
{
    for (int i = 0; i < HIDDEN; i++)
        for (int f = 0; f < INPUT_DIM; f++)
            W1T[f][i] = w1[i * INPUT_DIM + f];

    for (int i = 0; i < HIDDEN; i++) {
        B1[i] = b1[i];
        W2[i] = w2[i];
    }

    B2 = b2;
}

EXPORT void nnue_init_accumulator(
    const int *features,
    int n,
    int32_t *acc
)
{
    for (int i = 0; i < HIDDEN; i++)
        acc[i] = B1[i];

    for (int k = 0; k < n; k++) {

        int f = features[k];
        const int16_t *w = W1T[f];

        for (int i = 0; i < HIDDEN; i += 8) {

            __m256i a = _mm256_loadu_si256((__m256i*)(acc+i));

            __m128i w16 = _mm_loadu_si128((__m128i*)(w+i));
            __m256i w32 = _mm256_cvtepi16_epi32(w16);

            a = _mm256_add_epi32(a, w32);

            _mm256_storeu_si256((__m256i*)(acc+i), a);
        }
    }
}

EXPORT void nnue_update_accumulator(
    int32_t *acc,
    const int *added, int na,
    const int *removed, int nr
)
{
    for (int k = 0; k < nr; k++) {

        int f = removed[k];
        const int16_t *w = W1T[f];

        for (int i = 0; i < HIDDEN; i += 8) {

            __m256i a = _mm256_loadu_si256((__m256i*)(acc+i));

            __m128i w16 = _mm_loadu_si128((__m128i*)(w+i));
            __m256i w32 = _mm256_cvtepi16_epi32(w16);

            a = _mm256_sub_epi32(a, w32);

            _mm256_storeu_si256((__m256i*)(acc+i), a);
        }
    }

    for (int k = 0; k < na; k++) {

        int f = added[k];
        const int16_t *w = W1T[f];

        for (int i = 0; i < HIDDEN; i += 8) {

            __m256i a = _mm256_loadu_si256((__m256i*)(acc+i));

            __m128i w16 = _mm_loadu_si128((__m128i*)(w+i));
            __m256i w32 = _mm256_cvtepi16_epi32(w16);

            a = _mm256_add_epi32(a, w32);

            _mm256_storeu_si256((__m256i*)(acc+i), a);
        }
    }
}

EXPORT int32_t nnue_eval(const int32_t *acc)
{
    __m256i sum = _mm256_setzero_si256();
    __m256i zero = _mm256_setzero_si256();

    for (int i = 0; i < HIDDEN; i += 8) {

        __m256i a = _mm256_loadu_si256((__m256i*)(acc+i));

        a = _mm256_max_epi32(a, zero);

        __m128i w16 = _mm_loadu_si128((__m128i*)(W2+i));
        __m256i w32 = _mm256_cvtepi16_epi32(w16);

        __m256i prod = _mm256_mullo_epi32(a, w32);

        sum = _mm256_add_epi32(sum, prod);
    }

    ALIGN32 int32_t buf[8];
    _mm256_store_si256((__m256i*)buf, sum);

    int64_t s = B2;

    for (int i=0;i<8;i++)
        s += buf[i];

    return (int32_t)(s / (SCALE1 * SCALE2));
}
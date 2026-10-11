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

/* ===============================
   Network storage
   =============================== */

ALIGN32 static float W1T[INPUT_DIM][HIDDEN];   // feature-major
ALIGN32 static float B1[HIDDEN];

ALIGN32 static float W2[HIDDEN];
static float B2;

/* ===============================
   Load network weights
   PyTorch layout: W1[neuron][feature]
   We transpose once here
   =============================== */

EXPORT void nnue_load(
    const float *w1,
    const float *b1,
    const float *w2,
    float b2
)
{
    /* transpose first layer */

    for (int i = 0; i < HIDDEN; i++) {
        for (int f = 0; f < INPUT_DIM; f++) {
            W1T[f][i] = w1[i * INPUT_DIM + f];
        }
    }

    /* biases */

    for (int i = 0; i < HIDDEN; i++) {
        B1[i] = b1[i];
        W2[i] = w2[i];
    }

    B2 = b2;
}

/* ===============================
   Initialize accumulator
   acc = B1 + sum(W1T[f])
   =============================== */

EXPORT void nnue_init_accumulator(
    const int *features,
    int n,
    float *acc
)
{
    /* copy bias */

    for (int i = 0; i < HIDDEN; i++)
        acc[i] = B1[i];

    /* add active features */

    for (int k = 0; k < n; k++) {

        int f = features[k];
        const float *w = W1T[f];

        for (int i = 0; i < HIDDEN; i += 8) {

            __m256 a = _mm256_loadu_ps(acc + i);
            __m256 b = _mm256_loadu_ps(w + i);

            a = _mm256_add_ps(a, b);

            _mm256_storeu_ps(acc + i, a);
        }
    }
}

/* ===============================
   Incremental accumulator update
   =============================== */

EXPORT void nnue_update_accumulator(
    float *acc,
    const int *added, int na,
    const int *removed, int nr
)
{
    /* remove features */

    for (int k = 0; k < nr; k++) {

        int f = removed[k];
        const float *w = W1T[f];

        for (int i = 0; i < HIDDEN; i += 8) {

            __m256 a = _mm256_loadu_ps(acc + i);
            __m256 b = _mm256_loadu_ps(w + i);

            a = _mm256_sub_ps(a, b);

            _mm256_storeu_ps(acc + i, a);
        }
    }

    /* add features */

    for (int k = 0; k < na; k++) {

        int f = added[k];
        const float *w = W1T[f];

        for (int i = 0; i < HIDDEN; i += 8) {

            __m256 a = _mm256_loadu_ps(acc + i);
            __m256 b = _mm256_loadu_ps(w + i);

            a = _mm256_add_ps(a, b);

            _mm256_storeu_ps(acc + i, a);
        }
    }
}

/* ===============================
   Final evaluation
   output = W2 · ReLU(acc) + B2
   =============================== */

EXPORT float nnue_eval(const float *acc)
{
    __m256 sum = _mm256_setzero_ps();
    __m256 zero = _mm256_setzero_ps();

    for (int i = 0; i < HIDDEN; i += 8) {

        __m256 a = _mm256_loadu_ps(acc + i);

        /* ReLU activation */

        a = _mm256_max_ps(a, zero);

        __m256 w = _mm256_loadu_ps(W2 + i);

        __m256 prod = _mm256_mul_ps(a, w);

        sum = _mm256_add_ps(sum, prod);
    }

    ALIGN32 float buf[8];
    _mm256_store_ps(buf, sum);

    float s = B2;

    for (int i = 0; i < 8; i++)
        s += buf[i];

    return s;
}
import numpy as np
from cffi import FFI

ffi = FFI()

ffi.cdef("""
void nnue_load(
    const int16_t* w1,
    const int32_t* b1,
    const int16_t* w2,
    int32_t* b2
);

void nnue_init_accumulator(
    const int* features,
    int n,
    int32_t* acc
);

void nnue_update_accumulator(
    int32_t* acc,
    const int* added, int na,
    const int* removed, int nr
);

float nnue_eval(const int32_t* acc);
""")

lib = ffi.dlopen("nnue.dll")

weights = np.load("nnue_weights.npz")

w1 = weights["w1"]
b1  = weights["b1"]
w2  = weights["w2"]
b2  = weights["b2"]

lib.nnue_load(
    ffi.cast("int16_t*", w1.ctypes.data),
    ffi.cast("int32_t*", b1.ctypes.data),
    ffi.cast("int16_t*", w2.ctypes.data),
    ffi.cast("int32_t*", b2.ctypes.data),
)

def nnue_eval(acc):
    return float(lib.nnue_eval(
        ffi.cast("int32_t*", acc.ctypes.data)
    ))

def nnue_init_accumulator(features, acc):
    feats = np.fromiter(features, dtype=np.int32)
    lib.nnue_init_accumulator(
        ffi.cast("int*", feats.ctypes.data),
        feats.size,
        ffi.cast("int32_t*", acc.ctypes.data)
    )

def add_accumulator(idx, accumulator):
    lib.nnue_update_accumulator(
        ffi.cast("int32_t*", accumulator.ctypes.data),
        ffi.cast("int*", np.array([idx], dtype=np.int32).ctypes.data), 1,
        ffi.NULL, 0
    )

def remove_accumulator(idx, accumulator):
    lib.nnue_update_accumulator(
        ffi.cast("int32_t*", accumulator.ctypes.data),
        ffi.NULL, 0,
        ffi.cast("int*", np.array([idx], dtype=np.int32).ctypes.data), 1
    )

def update_accumulator(added, removed, acc):
    a = np.fromiter(added, dtype=np.int32) if added else np.empty(0, np.int32)
    r = np.fromiter(removed, dtype=np.int32) if removed else np.empty(0, np.int32)

    lib.nnue_update_accumulator(
        ffi.cast("int32_t*", acc.ctypes.data),
        ffi.cast("int*", a.ctypes.data), a.size,
        ffi.cast("int*", r.ctypes.data), r.size
    )
import numpy as np
from nnue import nnue_init_accumulator, nnue_eval

NNUE_SCALE = 400  # cp scale

acc = np.zeros(256, dtype=np.float32)
nnue_init_accumulator([768], acc)

print(nnue_eval(acc)*NNUE_SCALE)
print(acc[:10])
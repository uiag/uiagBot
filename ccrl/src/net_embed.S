/*
 * net_embed.S -- links the evaluation network into the executable.
 *
 * The CCRL submission has to be a single file that works wherever a tester
 * drops it.  The engine's normal loader reads `net3.bin` from the current
 * working directory, which is whatever the tournament GUI happens to have
 * set, so the release build compiles the network in instead and loads it
 * from memory (see ccrl_main.c and the `nnue_load_mem` patch in build.py).
 *
 * `.incbin` is used rather than a generated C array: net3.bin is 6.3 MB, and
 * as `0x..,` initialisers that is a 38 MB source file the compiler has to
 * parse.  The assembler just copies the bytes.
 *
 * Built as `.S` (capital) so the preprocessor runs and CE_NET_FILE can be
 * passed in with -D.  Use forward slashes in the path on Windows.
 */

#ifndef CE_NET_FILE
#define CE_NET_FILE "net3.bin"
#endif

#if defined(_WIN32) || defined(__CYGWIN__)
    .section .rdata, "dr"
#else
    .section .rodata
#endif

    .globl  ce_net_data
    .globl  ce_net_size
    .p2align 6
ce_net_data:
    .incbin CE_NET_FILE
ce_net_end:

    .p2align 3
ce_net_size:
    .quad   ce_net_end - ce_net_data

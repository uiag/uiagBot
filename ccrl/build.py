"""
Build the CCRL submission package.

    python ccrl/build.py                 # patch, compile, verify, zip
    python ccrl/build.py --no-verify     # skip the bench comparison
    python ccrl/build.py --keep-src      # leave ccrl/build/src for inspection
    python ccrl/build.py --depth 14      # deeper bench for the comparison
    python ccrl/build.py --help          # this text

Nothing under engine/ is read-write: the sources are *copied* into
ccrl/build/src, a small set of release patches is applied to the copies, and
the binaries are compiled from those.  The engine that plays on lichess is
untouched, keeps its own defaults (6 threads, 1 GB hash, net3.bin read from
disk) and is not rebuilt by this script.

Every patch below is anchored on an exact string from the engine sources and
the build fails loudly if an anchor stops matching, so this cannot silently
produce a binary that is missing a release setting.

Output:

    ccrl/dist/<name>-<version>/<name>-<version>-avx2.exe      <- submit this
    ccrl/dist/<name>-<version>/<name>-<version>-popcnt.exe    <- pre-AVX2 fallback
    ccrl/dist/<name>-<version>/readme.txt
    ccrl/dist/<name>-<version>/LICENSE
    ccrl/dist/<name>-<version>.zip
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

# --------------------------------------------------------------- identity
#
# What the engine calls itself over UCI.  CCRL lists an engine under the
# `id name` it reports, so this is the name that ends up on the rating list.
ENGINE_NAME = "uiagBot"
ENGINE_VERSION = "1.3"

# `id author`.  SET YOUR FULL NAME HERE before submitting -- CCRL's submission
# notes ask for "your name and nationality", and the name on the list is taken
# from this string.
ENGINE_AUTHOR = "Jakob Maisch"

ID_NAME = f"{ENGINE_NAME} {ENGINE_VERSION}"
PKG = f"{ENGINE_NAME}-{ENGINE_VERSION}"

# ------------------------------------------------------------------ paths
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE_DIR = os.path.join(ROOT, "engine")
SRC_DIR = os.path.join(HERE, "src")
BUILD_DIR = os.path.join(HERE, "build")
WORK_SRC = os.path.join(BUILD_DIR, "src")
DIST_DIR = os.path.join(HERE, "dist")

# engine/main.c and engine/uci.c: main.c is replaced wholesale by
# src/ccrl_main.c; uci.c is copied and patched.
ENGINE_SOURCES = ["bitboard.c", "position.c", "movegen.c", "eval.c", "nnue.c",
                  "tt.c", "tune.c", "search.c", "api.c", "uci.c"]
ENGINE_HEADERS = ["ce.h", "tune.h"]
NET = "net3.bin"

GCC_CANDIDATES = [
    r"C:\Program Files\MSYS2\ucrt64\bin\gcc.exe",
    r"C:\Program Files\MSYS2\mingw64\bin\gcc.exe",
    r"C:\msys64\ucrt64\bin\gcc.exe",
    r"C:\msys64\mingw64\bin\gcc.exe",
]

# ---------------------------------------------------------------- targets
#
# "At present our testers all use avx2" -- CCRL submission notes.  So the AVX2
# build is the one to submit; the popcnt build is there for anyone on older
# hardware and for the handful of testers who are not on it.
#
# -march=x86-64-v3 rather than -march=native: native on this machine may emit
# AVX-512, which would crash on a tester's Haswell/Zen2 box.  v3 is exactly
# AVX2 + BMI1/2 + FMA + LZCNT, which is what "avx2" means in practice.
TARGETS = [
    ("avx2",   ["-march=x86-64-v3", "-mtune=generic"]),
    ("popcnt", ["-msse4.2", "-mpopcnt", "-mtune=generic"]),
]


# ----------------------------------------------------------------- patches
#
# (file, anchor, replacement).  `anchor` must occur exactly once.
PATCHES: list[tuple[str, str, str]] = [
    # ---- the name CCRL will list the engine under -------------------
    (
        "api.c",
        'CE_EXPORT const char *ce_version(void) { return "ce 1.1"; }',
        f'CE_EXPORT const char *ce_version(void) {{ return "{ID_NAME}"; }}',
    ),
    (
        "uci.c",
        '            printf("id author jakob\\n");',
        f'            printf("id author {ENGINE_AUTHOR}\\n");',
    ),

    # ---- the defaults CCRL asks for ---------------------------------
    # "It would be helpful (but not necessary) if your default settings were
    # 256mb hash and 1 core."  Threads already defaults to 1.
    (
        "uci.c",
        '            printf("option name Hash type spin default 64 min 1 max 32768\\n");',
        '            printf("option name Hash type spin default 256 min 1 max 32768\\n");',
    ),
    # `blend` is the strongest of the three evaluations; the development
    # default is `hce` so that a build with no network behaves predictably.
    # ccrl_main.c sets the running mode; this is only what `uci` announces.
    (
        "uci.c",
        '            printf("option name EvalMode type combo default hce var hce var nnue var blend\\n");',
        '            printf("option name EvalMode type combo default blend var hce var nnue var blend\\n");',
    ),

    # ---- options a tournament GUI expects ---------------------------
    # Ponder: declared so the GUI knows the engine can do it and, more to the
    #   point, so that it is visibly OFF by default -- CCRL requires ponder off.
    #   An undeclared option cannot be switched on, but it also cannot be shown
    #   to be off, and testers check.
    # Move Overhead: the usual name for the clock allowance.  The engine keeps
    #   it in its tuning table as `MoveOverhead`, which the GUI never sees now
    #   that the tuning options are hidden, so it is re-exposed by hand.
    (
        "uci.c",
        '            printf("option name Threads type spin default 1 min 1 max 64\\n");',
        '            printf("option name Threads type spin default 1 min 1 max 64\\n");\n'
        '            printf("option name Ponder type check default false\\n");\n'
        '            printf("option name Move Overhead type spin default 50 min 0 max 1000\\n");',
    ),
    (
        "uci.c",
        '                /* `name` still has " value ..." after it, so the tune lookup\n'
        "                 * needs the bare word; copy it out first. */",
        "                /* The clock allowance is announced under its usual name,\n"
        "                 * which is two words.  The tune lookup below reads only the\n"
        "                 * first one, so it would see `Move` and miss, hence this. */\n"
        '                if (!strncmp(name, "Move Overhead", 13) && value) {\n'
        '                    tune_set("MoveOverhead", atoi(value));\n'
        "                    continue;\n"
        "                }\n"
        '                /* `name` still has " value ..." after it, so the tune lookup\n'
        "                 * needs the bare word; copy it out first. */",
    ),
]

# The network loader is replaced rather than edited: the release build reads
# the net out of the executable (net_embed.S) instead of off the disk.  The
# parse itself is the same sequence of reads, over a byte range.
NNUE_LOADER_START = ("/* Read `rows` x `cols` int16 into an array whose row stride "
                     "is `stride`. */")
NNUE_LOADER_FUNC = "int nnue_load_file(const char *path) {"

NNUE_MEM_LOADER = r'''/* ---- CCRL release build: the loader reads from memory --------------------
 *
 * Substituted for the FILE*-based loader by ccrl/build.py.  The submission
 * binary carries the network inside itself (see ccrl/src/net_embed.S), which
 * is what makes it a single file that works from any working directory, so
 * the parse has to run over a byte range.  nnue_load_file is kept, as a read
 * of the whole file followed by the same parse, so that --net still works.
 *
 * The sequence of reads, the shape checks and the quantisation constants are
 * exactly those of engine/nnue.c; ccrl/build.py verifies that by comparing a
 * fixed-depth bench against the development build, which would diverge on the
 * first position if any field were read at the wrong offset.
 */

typedef struct { const unsigned char *p; size_t left; } NetReader;

static int net_read(NetReader *r, void *dst, size_t n) {
    if (r->left < n) return 0;
    memcpy(dst, r->p, n);
    r->p += n;
    r->left -= n;
    return 1;
}

/* Read `rows` x `cols` int16 into an array whose row stride is `stride`. */
static int read_rows(NetReader *r, int16_t *dst, size_t rows, size_t cols,
                     size_t stride) {
    for (size_t i = 0; i < rows; i++)
        if (!net_read(r, dst + i * stride, cols * sizeof(int16_t)))
            return 0;
    return 1;
}

int nnue_load_mem(const void *data, size_t len) {
    NetReader r = { (const unsigned char *)data, len };
    NetLoaded = 0;

    char magic[4];
    uint32_t version, planes, kbuckets = 1, l1, buckets;
    int32_t qa, qb;
    float scale;
    if (!net_read(&r, magic, 4)) return 0;

    int v3 = !memcmp(magic, "CEN3", 4);
    if (!v3 && memcmp(magic, "CEN2", 4)) return 0;

    if (!net_read(&r, &version, 4) || !net_read(&r, &planes, 4)
        || (v3 && !net_read(&r, &kbuckets, 4))
        || !net_read(&r, &l1, 4) || !net_read(&r, &buckets, 4)
        || !net_read(&r, &qa, 4) || !net_read(&r, &qb, 4)
        || !net_read(&r, &scale, 4)) return 0;

    if (version != (v3 ? 3u : 2u) || planes != NN_PLANES
        || buckets != NN_BUCKETS
        || l1 == 0 || l1 > NN_MAX_L1 || (l1 % 16) != 0
        || kbuckets == 0 || kbuckets > NN_MAX_KBUCKETS) return 0;

    QA = qa; QB = qb; NetScale = scale;
    NetL1 = (int)l1;
    NetKBuckets = (int)kbuckets;
    NetMirror = v3;                /* the fold came in with the king buckets */

    size_t rows = (size_t)kbuckets * NN_PLANES;
    int ok = read_rows(&r, &FtWeight[0][0], rows, l1, NN_MAX_L1)
          && net_read(&r, FtBias, (size_t)l1 * sizeof(int16_t))
          && read_rows(&r, &OutWeight[0][0], NN_BUCKETS, 2 * (size_t)l1,
                       2 * NN_MAX_L1)
          && net_read(&r, OutBias, NN_BUCKETS * sizeof(int32_t));
    NetLoaded = ok;
    if (ok) NetGen++;
    return ok;
}

int nnue_load_file(const char *path) {
    NetLoaded = 0;
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return 0; }
    long n = ftell(f);
    if (n <= 0 || fseek(f, 0, SEEK_SET) != 0) { fclose(f); return 0; }
    unsigned char *buf = (unsigned char *)malloc((size_t)n);
    if (!buf) { fclose(f); return 0; }
    size_t got = fread(buf, 1, (size_t)n, f);
    fclose(f);
    int ok = got == (size_t)n && nnue_load_mem(buf, got);
    free(buf);
    return ok;
}
'''


# ------------------------------------------------------------------ helpers
def die(msg: str) -> "None":
    print(f"\nERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


def find_compiler() -> str:
    for name in ("gcc", "clang", "cc"):
        path = shutil.which(name)
        if path:
            return path
    for path in GCC_CANDIDATES:
        if os.path.isfile(path):
            return path
    die("No C compiler found.  Install MSYS2 and\n"
        "    pacman -S mingw-w64-ucrt-x86_64-gcc\n"
        "or put gcc on PATH.")
    raise AssertionError  # unreachable, for the type checker


def compiler_env(cc: str) -> dict:
    """The compiler's own bin directory has to be on PATH; see engine/build.py."""
    env = dict(os.environ)
    bindir = os.path.dirname(os.path.abspath(cc))
    if bindir and bindir not in env.get("PATH", "").split(os.pathsep):
        env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    return env


def end_of_function(text: str, start: int) -> int:
    """Index just past the closing brace of the function whose `{` follows `start`."""
    i = text.index("{", start)
    depth = 0
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    die("unbalanced braces while locating the end of nnue_load_file")
    raise AssertionError


def apply_patches() -> None:
    applied = 0
    for fname, anchor, replacement in PATCHES:
        path = os.path.join(WORK_SRC, fname)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        n = text.count(anchor)
        if n != 1:
            die(f"{fname}: release patch anchor matched {n} times, expected 1.\n"
                f"  anchor: {anchor.strip()[:90]}\n"
                f"  engine/{fname} has changed; update PATCHES in ccrl/build.py.")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace(anchor, replacement))
        applied += 1

    # the network loader
    path = os.path.join(WORK_SRC, "nnue.c")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if text.count(NNUE_LOADER_START) != 1 or text.count(NNUE_LOADER_FUNC) != 1:
        die("nnue.c: could not find the loader to replace.\n"
            "  engine/nnue.c has changed; update ccrl/build.py.")
    start = text.index(NNUE_LOADER_START)
    end = end_of_function(text, text.index(NNUE_LOADER_FUNC))
    text = text[:start] + NNUE_MEM_LOADER + text[end:]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    applied += 1
    print(f"  applied {applied} release patches")


def stage_sources() -> None:
    if os.path.isdir(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)
    os.makedirs(WORK_SRC)
    for f in ENGINE_SOURCES + ENGINE_HEADERS + [NET]:
        src = os.path.join(ENGINE_DIR, f)
        if not os.path.isfile(src):
            die(f"missing {src}")
        shutil.copy2(src, os.path.join(WORK_SRC, f))
    for f in ("ccrl_main.c", "net_embed.S"):
        shutil.copy2(os.path.join(SRC_DIR, f), os.path.join(WORK_SRC, f))
    print(f"  staged {len(ENGINE_SOURCES)} sources + {NET} "
          f"({os.path.getsize(os.path.join(WORK_SRC, NET)) / 1e6:.1f} MB) "
          "into ccrl/build/src")


def compile_target(cc: str, env: dict, label: str, arch_flags: list[str],
                   out: str) -> bool:
    flags = [
        "-std=c11", "-Wall", "-Wextra", "-Wno-unused-parameter", "-pipe",
        "-O3", "-DNDEBUG", "-fno-math-errno", "-funroll-loops",
        "-fomit-frame-pointer", "-flto=auto",
        *arch_flags,
        # Everything the gcc runtime needs goes in the binary.  A release exe
        # that imports libgcc_s_seh-1.dll or libwinpthread-1.dll only runs on
        # a machine with MSYS2 installed and MSYS2's bin on PATH, which is not
        # a tester's machine.
        "-static", "-static-libgcc",
        # Reproducible output.  Without this the linker stamps the current
        # time into the PE header, so every rebuild of identical sources is a
        # different file with a different SHA-256 -- which invalidates the
        # VirusTotal report and the checksums published with the release, and
        # leaves no way to show that a binary came from these sources.
        # -ffile-prefix-map keeps the build directory out of the object files
        # for the same reason.
        "-Wl,--no-insert-timestamp",
        f"-ffile-prefix-map={WORK_SRC}=.",
    ]
    # The assembler resolves .incbin against its own include path.
    net_path = os.path.join(WORK_SRC, NET).replace("\\", "/")
    srcs = [os.path.join(WORK_SRC, s) for s in ENGINE_SOURCES]
    srcs += [os.path.join(WORK_SRC, "ccrl_main.c"),
             os.path.join(WORK_SRC, "net_embed.S")]

    cmd = [cc, *flags, f'-DCE_NET_FILE="{net_path}"', *srcs, "-o", out, "-lm"]
    print(f"\nbuilding {label}: {os.path.basename(out)}")
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=WORK_SRC, capture_output=True, text=True,
                          env=env)
    if proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.stderr.strip():
        print(proc.stderr.strip())
    if proc.returncode != 0:
        print(f"  FAILED (exit {proc.returncode})")
        return False
    print(f"  ok  {os.path.getsize(out) / 1e6:.1f} MB  in {time.time() - t0:.1f}s")
    return True


# ------------------------------------------------------------------ checks
UCI_SCRIPT = "uci\nisready\nquit\n"


def talk(exe: str, script: str, timeout: int = 600) -> str:
    proc = subprocess.run([exe], input=script, capture_output=True, text=True,
                          timeout=timeout, cwd=os.path.dirname(exe) or ".")
    return proc.stdout


def check_uci(exe: str) -> list[str]:
    """The handshake a tournament GUI performs, and the release settings."""
    out = talk(exe, UCI_SCRIPT)
    problems = []
    if f"id name {ID_NAME}" not in out:
        problems.append(f"`id name {ID_NAME}` missing from the uci reply")
    if f"id author {ENGINE_AUTHOR}" not in out:
        problems.append("`id author` missing from the uci reply")
    if "uciok" not in out:
        problems.append("no uciok")
    if "readyok" not in out:
        problems.append("no readyok")
    opts = re.findall(r"^option name (.+?) type", out, re.M)
    for want in ("Hash", "Threads", "Ponder", "Move Overhead", "EvalMode"):
        if want not in opts:
            problems.append(f"option `{want}` not declared")
    if "option name Hash type spin default 256" not in out:
        problems.append("Hash does not default to 256 MB")
    if "option name Ponder type check default false" not in out:
        problems.append("Ponder does not default to false")
    if "option name EvalMode type combo default blend" not in out:
        problems.append("EvalMode does not default to blend")
    if len(opts) > 12:
        problems.append(f"{len(opts)} options declared -- the tuning options "
                        "are meant to be hidden in the release build")
    # Anything the engine prints before `id name` confuses some GUIs.
    first = next((ln for ln in out.splitlines() if ln.strip()), "")
    if not first.startswith("id name"):
        problems.append(f"first line of output is {first!r}, not `id name ...`")
    return problems


MOVE_RE = re.compile(r"[a-h][1-8][a-h][1-8][qrbn]?")


def check_plays(exe: str) -> list[str]:
    """
    Two searches in one session, the way a GUI drives the engine.

    stdin has to stay open while the search runs: the search is on its own
    thread and announces its move from there, so a front-end that closed the
    pipe right after `go` would see the process exit before the move came out.
    That is why this uses Popen and reads until each bestmove rather than
    handing subprocess.run a script.
    """
    problems: list[str] = []
    proc = subprocess.Popen([exe], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True, bufsize=1,
                            cwd=os.path.dirname(exe) or ".")
    assert proc.stdin and proc.stdout
    lines: list[str] = []

    def send(*cmds: str) -> None:
        for c in cmds:
            proc.stdin.write(c + "\n")       # type: ignore[union-attr]
        proc.stdin.flush()                   # type: ignore[union-attr]

    def wait_for(prefix: str, limit: float) -> str:
        deadline = time.time() + limit
        while time.time() < deadline:
            ln = proc.stdout.readline()      # type: ignore[union-attr]
            if not ln:
                return ""
            lines.append(ln.rstrip())
            if ln.startswith(prefix):
                return ln.strip()
        return ""

    try:
        send("uci")
        if not wait_for("uciok", 20):
            problems.append("no uciok")
        send("setoption name Hash value 256",
             "setoption name Threads value 1",
             "setoption name Ponder value false",
             "isready")
        if not wait_for("readyok", 20):
            problems.append("no readyok after setoption")

        # A repeating control, which is what 40 moves in 15 minutes is: the
        # time manager has to divide by `movestogo` rather than assume a
        # sudden-death clock.  120 s keeps this check to a couple of seconds.
        send("ucinewgame", "isready")
        wait_for("readyok", 20)
        send("position startpos moves e2e4 e7e5 g1f3",
             "go wtime 120000 btime 120000 movestogo 40")
        bm = wait_for("bestmove", 60)
        if not bm:
            problems.append("no bestmove under `go wtime ... movestogo ...`")
        elif not MOVE_RE.fullmatch(bm.split()[1]):
            problems.append(f"bestmove {bm.split()[1]!r} is not a move")

        # A second search in the same session: the thread pool and the
        # accumulator stack have to survive the first one.
        send("position fen r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
             "go movetime 500")
        bm2 = wait_for("bestmove", 60)
        if not bm2:
            problems.append("no bestmove under `go movetime 500`")
        elif not MOVE_RE.fullmatch(bm2.split()[1]):
            problems.append(f"bestmove {bm2.split()[1]!r} is not a move")

        # `go infinite` / `stop`, which is how a GUI analyses and how some of
        # them end the last search of a game.  A front-end that cannot be
        # interrupted hangs the GUI rather than losing one game.
        send("position startpos", "go infinite")
        time.sleep(1.0)
        send("stop")
        bm3 = wait_for("bestmove", 30)
        if not bm3:
            problems.append("`stop` did not end a `go infinite` search")

        # Asked for a move in a position that has none.  GUIs do ask -- at the
        # end of a game, or when a position is pasted in -- and an engine that
        # crashes or hangs here loses the game it had already won.
        for name, fen in (
            ("checkmate", "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"),
            ("stalemate", "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"),
        ):
            send(f"position fen {fen}", "go movetime 200")
            if not wait_for("bestmove", 30):
                problems.append(f"no bestmove in a {name} position")

        send("quit")
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        problems.append("the engine did not exit on `quit`")
    finally:
        if proc.poll() is None:
            proc.kill()

    if not any(ln.startswith("info depth") for ln in lines):
        problems.append("no `info depth` lines during the search")
    if proc.returncode not in (0, None):
        problems.append(f"exited with status {proc.returncode}")
    return problems


BENCH_SCRIPT_DEV = ("setoption name Hash value 256\n"
                    "setoption name Threads value 1\n"
                    "setoption name EvalMode value blend\n"
                    "bench {depth}\n"
                    "quit\n")
BENCH_SCRIPT_REL = ("setoption name Hash value 256\n"
                    "setoption name Threads value 1\n"
                    "bench {depth}\n"
                    "quit\n")

BENCH_LINE = re.compile(r"^position\s+\d+/\d+\s+depth.*$", re.M)


def bench_lines(out: str) -> list[str]:
    """The deterministic part of a bench run: no timings, no nps."""
    return [ln.strip() for ln in BENCH_LINE.findall(out)]


def verify_against_dev(release_exe: str, depth: int = 10) -> list[str]:
    """
    The embedded network has to be bit-identical to the one on disk.

    A fixed-depth, single-threaded bench is deterministic, so the release
    binary and the development binary -- same sources, same net, one reading
    it from memory and one from a file -- must agree on every position's
    score, best move and node count.  A loader that read one field at the
    wrong offset would diverge on position 1.
    """
    dev = os.path.join(ENGINE_DIR, "ce.exe")
    if not os.path.isfile(dev):
        return ["engine/ce.exe not built, cannot compare "
                "(run `python engine/build.py`, or pass --no-verify)"]

    print(f"  running bench {depth} on the development build ...")
    want = bench_lines(talk(dev, BENCH_SCRIPT_DEV.format(depth=depth)))
    print(f"  running bench {depth} on the release build ...")
    got = bench_lines(talk(release_exe, BENCH_SCRIPT_REL.format(depth=depth)))

    if not want:
        return ["the development build produced no bench output"]
    if want == got:
        print(f"  ok  {len(want)} bench positions identical to engine/ce.exe")
        return []
    problems = [f"bench differs from engine/ce.exe "
                f"({len(want)} vs {len(got)} positions)"]
    for i, (a, b) in enumerate(zip(want, got)):
        if a != b:
            problems.append(f"  position {i + 1}: dev {a!r} != release {b!r}")
            if len(problems) > 4:
                break
    return problems


def check_imports(cc: str, env: dict, exe: str) -> list[str]:
    """No DLL the tester does not already have."""
    objdump = os.path.join(os.path.dirname(cc), "objdump.exe")
    if not os.path.isfile(objdump):
        return []
    proc = subprocess.run([objdump, "-p", exe], capture_output=True, text=True,
                          env=env)
    dlls = {d.lower() for d in re.findall(r"DLL Name:\s*(\S+)", proc.stdout)}
    allowed = {"kernel32.dll", "msvcrt.dll", "ucrtbase.dll", "api-ms-win-crt-"}
    bad = [d for d in sorted(dlls)
           if d not in allowed and not d.startswith("api-ms-win-")]
    if bad:
        return [f"imports non-system DLLs: {', '.join(bad)}"]
    print(f"  ok  imports only system DLLs ({', '.join(sorted(dlls))})")
    return []


# -------------------------------------------------------------- packaging
def write_readme(path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(README_TXT)


def package(exes: dict) -> str:
    out_dir = os.path.join(DIST_DIR, PKG)
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)

    for label, src in exes.items():
        shutil.copy2(src, os.path.join(out_dir, f"{PKG}-{label}.exe"))
    write_readme(os.path.join(out_dir, "readme.txt"))
    shutil.copy2(os.path.join(ROOT, "LICENSE"), os.path.join(out_dir, "LICENSE"))

    # Checksums, so a tester can tell that what they downloaded is what was
    # scanned and released.  The build is reproducible, so these are stable
    # across rebuilds of the same sources.
    sums = os.path.join(out_dir, "SHA256SUMS.txt")
    with open(sums, "w", encoding="utf-8", newline="\r\n") as fh:
        for name in sorted(os.listdir(out_dir)):
            if not name.endswith(".exe"):
                continue
            with open(os.path.join(out_dir, name), "rb") as b:
                fh.write(f"{hashlib.sha256(b.read()).hexdigest()}  {name}\n")
    print("\nchecksums")
    with open(sums, encoding="utf-8") as fh:
        for line in fh:
            print(f"  {line.rstrip()}")

    zip_path = os.path.join(DIST_DIR, f"{PKG}.zip")
    if os.path.isfile(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(out_dir)):
            z.write(os.path.join(out_dir, name), f"{PKG}/{name}")
    return zip_path


README_TXT = f"""\
{ID_NAME} -- a UCI chess engine
================================================================

Author:   {ENGINE_AUTHOR} (Germany)
License:  GNU AGPL v3 (see LICENSE)
Protocol: UCI


Which file to run
-----------------

  {PKG}-avx2.exe      requires AVX2 (Intel Haswell 2013 / AMD Zen or newer).
                           Use this one.
  {PKG}-popcnt.exe    requires only SSE4.2 + POPCNT.  Use this only if the
                           AVX2 build will not start -- it is about 2.3x
                           slower, because the network's accumulator falls
                           back to scalar code, and it therefore plays
                           noticeably weaker.  It is here for completeness,
                           not as an equivalent build.

Both are 64-bit Windows executables.  There is nothing to install and no
other file to download: the evaluation network is compiled into the
executable, so the engine does not care which working directory it is
started from.  They are statically linked and import only system DLLs.


UCI options
-----------

  Hash            1 - 32768 MB, default 256
  Threads         1 - 64, default 1 (Lazy SMP)
  Ponder          default false
  Move Overhead   0 - 1000 ms, default 50
  MultiPV         1 - 64, default 1
  EvalMode        hce | nnue | blend, default blend
                  Which evaluation to use.  `blend` mixes the network with
                  the hand-crafted evaluation and is the strongest; the
                  other two are there for measurement.  Leave it alone.

Defaults are 256 MB hash and 1 thread, as CCRL's submission notes suggest.


CCRL testing conditions
-----------------------

  Own opening book      none, so nothing to disable
  Book learning         none
  Position learning     none
  Pondering             off by default
  Endgame tablebases    not supported (no Syzygy/Nalimov); the engine plays
                        endgames on its own.  It does carry a built-in KPK
                        bitbase and mate knowledge for KXK/KBNK.
  Repeating time controls   supported; `movestogo` is used by the time
                        manager, which is what 40 moves in 15 minutes needs
  Chess960              not supported (standard chess only)

The engine is deterministic at a fixed node or depth limit with one thread.


Also useful
-----------

  {PKG}-avx2.exe bench 12     fixed-depth benchmark
  {PKG}-avx2.exe perft 6      move generation count
  {PKG}-avx2.exe --help       command line options

`bench 12` on one core of an i7-9700K runs at roughly 600 knps for the AVX2
build and 260 knps for the popcnt one; a figure far below that means the
wrong binary is running for the hardware.  `bench 10` reports exactly
179,938 nodes from either build -- the search is deterministic at a fixed
depth on one thread, so that number is a check that the binary is intact.

Inside a UCI session, `bench N`, `perft N` and `eval` work as commands too.


What it is
----------

A bitboard engine written from scratch in C: magic-bitboard move generation,
a PVS/negamax search with the usual modern pruning, Lazy SMP, and a choice of
a hand-crafted evaluation or a (768 -> 1024) x 2 -> 1 perspective network with
SCReLU, king-bucketed inputs and output buckets, trained on the lichess
evaluation dump.  It is the engine behind a lichess bot of the same name.

Developed with AI assistance (Claude).
"""


# ------------------------------------------------------------------- main
def main() -> int:
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return 0
    no_verify = "--no-verify" in sys.argv
    keep_src = "--keep-src" in sys.argv
    depth = 10
    for i, a in enumerate(sys.argv):
        if a == "--depth" and i + 1 < len(sys.argv):
            depth = int(sys.argv[i + 1])

    cc = find_compiler()
    env = compiler_env(cc)
    print(f"CCRL release build -- {ID_NAME}")
    print(f"compiler: {cc}")

    print("\nstaging sources (engine/ is not modified)")
    stage_sources()
    apply_patches()

    os.makedirs(DIST_DIR, exist_ok=True)
    exes = {}
    for label, arch_flags in TARGETS:
        out = os.path.join(BUILD_DIR, f"{PKG}-{label}.exe")
        if not compile_target(cc, env, label, arch_flags, out):
            return 1
        exes[label] = out

    primary = exes["avx2"]
    problems: list[str] = []

    print("\nchecking the release binary")
    problems += check_uci(primary)
    problems += check_imports(cc, env, primary)
    print("  playing a move under a 40/15-style repeating control ...")
    problems += check_plays(primary)
    if no_verify:
        print("  skipping the bench comparison (--no-verify)")
    else:
        problems += verify_against_dev(primary, depth)

    if problems:
        print("\nFAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("  all checks passed")

    zip_path = package(exes)
    if not keep_src:
        shutil.rmtree(WORK_SRC)

    print(f"\nPackaged {os.path.relpath(zip_path, ROOT)} "
          f"({os.path.getsize(zip_path) / 1e6:.1f} MB)")
    print(f"  submit {PKG}-avx2.exe  (see ccrl/SUBMISSION.md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

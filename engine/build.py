"""
Build the chess engine: engine/ce.dll (used by the Python bridge) and
engine/ce.exe (a standalone UCI engine, handy for testing with cutechess).

    python engine/build.py            # normal optimised build
    python engine/build.py --debug    # with assertions and -O1
    python engine/build.py --generic  # portable build, no -march=native

A C compiler is required.  MSYS2/MinGW gcc is found automatically on Windows.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

SOURCES = ["bitboard.c", "position.c", "movegen.c", "eval.c", "nnue.c",
           "tt.c", "search.c", "api.c", "uci.c"]
MAIN = "main.c"

GCC_CANDIDATES = [
    r"C:\Program Files\MSYS2\ucrt64\bin\gcc.exe",
    r"C:\Program Files\MSYS2\mingw64\bin\gcc.exe",
    r"C:\msys64\ucrt64\bin\gcc.exe",
    r"C:\msys64\mingw64\bin\gcc.exe",
    r"C:\MinGW\bin\gcc.exe",
    r"C:\TDM-GCC-64\bin\gcc.exe",
]


def find_compiler() -> str:
    for name in ("gcc", "clang", "cc"):
        path = shutil.which(name)
        if path:
            return path
    for path in GCC_CANDIDATES:
        if os.path.isfile(path):
            return path
    raise SystemExit(
        "No C compiler found.\n"
        "Install MSYS2 (https://www.msys2.org) and then run\n"
        "    pacman -S mingw-w64-ucrt-x86_64-gcc\n"
        "or add an existing gcc/clang to PATH."
    )


def compiler_env(cc: str) -> dict[str, str]:
    """The compiler's own directory has to be on PATH.

    gcc.exe finds its MSYS2 libraries next to itself, but cc1.exe lives under
    lib/gcc/... and does not, so when MSYS2's bin directory is missing from
    PATH the back end fails to load its DLLs and exits without printing
    anything at all -- a build that reports only "FAILED (exit 1)" and no
    diagnostics.  Putting the directory on PATH for the child avoids it.
    """
    env = dict(os.environ)
    bindir = os.path.dirname(os.path.abspath(cc))
    if bindir and bindir not in env.get("PATH", "").split(os.pathsep):
        env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    return env


def main() -> int:
    debug = "--debug" in sys.argv
    generic = "--generic" in sys.argv
    cc = find_compiler()
    env = compiler_env(cc)
    print(f"compiler: {cc}")

    flags = ["-std=c11", "-Wall", "-Wextra", "-Wno-unused-parameter", "-pipe"]
    if debug:
        flags += ["-O1", "-g", "-DCE_DEBUG"]
    else:
        flags += ["-O3", "-DNDEBUG", "-fno-math-errno", "-funroll-loops",
                  "-fomit-frame-pointer", "-flto=auto"]
    if generic:
        flags += ["-mpopcnt", "-msse4.2"]
    else:
        flags += ["-march=native"]

    # Link the gcc/pthread runtimes statically.  Otherwise ce.dll imports
    # libgcc_s_seh-1.dll and libwinpthread-1.dll, which only resolve when
    # MSYS2/MinGW's bin directory happens to be on PATH; without it Windows
    # fails the load of ce.dll itself with "the specified module could not be
    # found" (ERROR_MOD_NOT_FOUND), which cffi reports as an inability to load
    # ce.dll even though the file is right there.
    if sys.platform == "win32":
        flags += ["-static", "-static-libgcc"]

    libs = ["-lm"]
    if sys.platform != "win32":
        libs += ["-lpthread"]

    srcs = [os.path.join(HERE, s) for s in SOURCES]
    dll = os.path.join(HERE, "ce.dll" if sys.platform == "win32" else "libce.so")
    exe = os.path.join(HERE, "ce.exe" if sys.platform == "win32" else "ce")

    ok = True
    for target, extra, out in ((dll, ["-shared"], "shared library"),
                               (exe, [os.path.join(HERE, MAIN)], "executable")):
        cmd = [cc, *flags, *extra, *srcs, "-o", target, *libs]
        print(f"\nbuilding {out}: {os.path.basename(target)}")
        t0 = time.time()
        proc = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, env=env)
        if proc.stdout.strip():
            print(proc.stdout.strip())
        if proc.stderr.strip():
            print(proc.stderr.strip())
        if proc.returncode != 0:
            print(f"  FAILED (exit {proc.returncode})")
            if not proc.stdout.strip() and not proc.stderr.strip():
                print("  The compiler said nothing, which usually means its own\n"
                      "  back end could not start.  Check that\n"
                      f"    {os.path.dirname(os.path.abspath(cc))}\n"
                      "  is on PATH and that the toolchain is complete.")
            ok = False
        else:
            size = os.path.getsize(target) / 1024
            print(f"  ok  {size:.0f} KB  in {time.time() - t0:.1f}s")

    if ok:
        print("\nBuild succeeded.")
        print(f"  {dll}")
        print(f"  {exe}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

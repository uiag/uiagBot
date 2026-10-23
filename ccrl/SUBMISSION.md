# Submitting uiagBot 1.3 to CCRL

**Everything is published and the post below is ready to send.** Both links in
it are live and were checked from an unauthenticated client:

* Repository — <https://github.com/uiag/uiagBot> (public)
* Release v1.3 — <https://github.com/uiag/uiagBot/releases/tag/v1.3>
  (`uiagBot-1.3.zip`, both executables, and `SHA256SUMS.txt`)

Rebuild the package any time with `python ccrl/build.py`; the build is
reproducible, so it reproduces the exact bytes that are in the release.

---

## One thing still open: the VirusTotal link

You scanned an earlier build — the report you linked was for hash
`d8f50d20…`, which was the right file when you uploaded it. The build has
since been made **reproducible**: MinGW was stamping the build time into the
PE header, so every rebuild of identical sources produced a different file and
a different hash, which would have made the scan report and the published
checksums meaningless after any rebuild. `-Wl,--no-insert-timestamp` fixed
that, and the binary changed one last time in the process.

The released AVX2 executable is now, permanently for these sources:

```
30d8074907909daaeb3b497466d6cf48352fe61e23f45793a489d58834b12853  uiagBot-1.3-avx2.exe
f618c882d1edd44dfb4afa51e4ae4bfb563d7a7c18b74a6d0e4bb90e67a92b59  uiagBot-1.3-popcnt.exe
```

So upload it once more — either the local
`ccrl/dist/uiagBot-1.3/uiagBot-1.3-avx2.exe` or the released copy at
<https://github.com/uiag/uiagBot/releases/download/v1.3/uiagBot-1.3-avx2.exe>
— to <https://www.virustotal.com/>, and paste the new link over the
placeholder in the post.

Worth the two minutes: there is a standing *WARNING: Executables!!!* thread on
the CCRL board asking people to be careful with binaries from strangers, and
heuristic antivirus does flag unknown statically-linked engines. It removes
the one reason a tester might skip you. `SHA256SUMS.txt` ships in the zip and
the hashes are in the release notes, so a tester can confirm their download is
the file that was scanned.

If you would rather not bother, delete the VirusTotal line from the post — the
SHA-256 lines stand on their own.

## Where to post it

> **Do not reply to "How to get your engine tested for CCRL".** That thread
> (`viewtopic.php?t=11975`) is **locked**. It is still the authoritative list
> of *what* a submission has to contain, and the post below follows it, but
> you cannot reply to it.

New engines open **their own topic** in the **CCRL Public** forum:

> http://kirill-kryukov.com/chess/discussion-board/viewforum.php?f=7

That is what everyone does now — the forum's recent topics are
"Anka v1.0.1 — C# bitboard engine, ~2150-2250 Elo" and "TacTurn V1 — new UCI
engine (~3250 Elo), requesting CCRL testing". Gabor Szots has asked
explicitly that engines get their own thread rather than piling into a shared
one, and that later versions go in the same thread rather than a new one.

Registering first:

* **A sign-up code is required.** It is not published as a number, it is a
  riddle: *the number of pawns on the board at the start of a game, times ten,
  plus 9999.* 16 x 10 + 9999 = **10159**. (Recompute it if it ever stops
  working — Graham changes the riddle, not the format.)
* Use your real name as the account name.
* **Your first two posts have to be approved before they appear.** Do not
  repost when nothing shows up; wait.

Worth doing as well, though not required: announce the release on
**TalkChess** (<https://talkchess.com/>). CCRL's own advice is to announce a
finished engine in one of the computer-chess forums, and it is how most
testers and the engine-list sites notice a new engine at all.

---

## The post

Subject line, following the house style:

> **uiagBot 1.3 — new UCI engine with its own NNUE, requesting CCRL testing**

Body — copy as is. The only placeholder left is the VirusTotal link (or
delete that line):

---

I'd like to submit the first public release of **uiagBot** for CCRL testing.
It is a UCI engine written from scratch in C, with an evaluation network
trained from scratch as well.

**Basic information**

* Engine: **uiagBot 1.3** (64-bit)
* Author: Jakob Maisch
* Country: Germany
* Protocol: UCI
* License: GNU AGPL v3
* Repository: https://github.com/uiag/uiagBot
* Releases / downloads: https://github.com/uiag/uiagBot/releases/tag/v1.3
* VirusTotal: `<PASTE THE NEW SCAN LINK HERE -- see the note at the top>`
* SHA-256:
  * `uiagBot-1.3-avx2.exe` — `30d8074907909daaeb3b497466d6cf48352fe61e23f45793a489d58834b12853`
  * `uiagBot-1.3-popcnt.exe` — `f618c882d1edd44dfb4afa51e4ae4bfb563d7a7c18b74a6d0e4bb90e67a92b59`

**Architecture**

Bitboard board representation with magic bitboards (searched for at start-up
with a fixed seed), iterative-deepening PVS with the usual modern pruning,
and Lazy SMP. The evaluation can be run three ways, selected by the
`EvalMode` option: a Texel-tuned hand-crafted evaluation (`hce`), the network
alone (`nnue`), or a blend of the two (`blend`), which is the default and the
strongest — `nnue` is worth +131.8 +/- 26.8 Elo over `hce`, and `blend` a
further +34.9 +/- 15.6 on top.

The network is a perspective net, `(768 -> 1024) x 2 -> 1`, with SCReLU,
king-bucketed inputs with the files folded, and eight output buckets by piece
count. It is quantised to int16 with an incremental accumulator, and it was
trained on the public lichess evaluation dump — no self-play against another
engine and no distillation of another engine's search.

**Originality**

Written from scratch; no code or weights taken from another engine. Stockfish
is in the repository, used only as a sparring partner and as a referee for
generating test positions. The network's training labels come from the
lichess evaluation dump, which is public data.

Initially developed in Python on my own. Translated to C and few improvements with AI assistance (Claude Opus 5.5). The design decisions,
the testing methodology and every accepted change are mine: each change was
kept or rejected on a measured result, and the measurements are in the
repository.

**Binaries**

Both are 64-bit Windows executables, statically linked — no DLLs to install,
nothing to unpack beside them. The evaluation network is compiled into the
executable, so there is no network file to place and no dependence on the
working directory the GUI happens to use.

* `uiagBot-1.3-avx2.exe` — **please use this one.** Requires AVX2
  (`-march=x86-64-v3`).
* `uiagBot-1.3-popcnt.exe` — SSE4.2 + POPCNT fallback. It is about 2.3x
  slower, because the accumulator falls back to scalar code, and plays
  correspondingly weaker. It is there for completeness, not as an equivalent
  build.

**Deterministic bench check**

At a fixed depth on one thread the search is deterministic, and both builds
agree exactly. Straight out of the zip, no options set (so 256 MB hash, one
thread, `EvalMode=blend`):

```
uiagBot-1.3-avx2.exe bench 10      ->  Nodes searched : 179,938
uiagBot-1.3-popcnt.exe bench 10    ->  Nodes searched : 179,938
```

That is also how I check the network compiled into the executable is the one
I trained: the same figure comes out of a build that reads `net3.bin` off the
disk instead.

Speed on one core of an i7-9700K is roughly 600 knps for the AVX2 build and
260 knps for the popcnt one. A figure far below that means the wrong binary
is running for the hardware.

**UCI options and tester notes**

* `Hash` — 1 to 32768 MB, **defaults to 256 MB**.
* `Threads` — 1 to 64 (Lazy SMP), **defaults to 1**.
* `Ponder` — **defaults to false**.
* `Move Overhead` — 0 to 1000 ms, defaults to 50. Raise it if your setup
  needs more.
* `MultiPV` — 1 to 64, defaults to 1.
* `EvalMode` — `hce` / `nnue` / `blend`, **defaults to `blend`**, which is
  the strongest. The other two exist because I measure against them; please
  leave this at the default.
* **Own opening book:** none, so there is nothing to disable.
* **Book learning / position learning:** neither exists.
* **Time controls:** sudden death, incremental and repeating all supported —
  `movestogo` is used by the time manager, so 40 moves in 15 minutes and then
  another 15 is handled properly. `stop`, `isready` and `quit` are all
  answered during a search, and `go infinite` can be interrupted.
* **Endgame tablebases:** not supported. There is no `SyzygyPath`; the engine
  plays endgames on its own, with a built-in KPK bitbase and KXK/KBNK mate
  knowledge.
* **Chess960:** not supported, so 40/15 rather than the FRC list.

**Estimated strength**

**Roughly 3250-3400 on the CCRL 40/15 scale**, from a direct measurement
against an engine already on your list rather than from a self-play ladder:
at 30s+0.3s, one thread each, 256 MB hash, uiagBot scored **4.5/30 (15.0%)
against Stockfish 19 64-bit single-CPU**, which is -301 Elo. Against your
3629 for that version, that puts it near 3330.

Two things to hold against it: 30 games is a wide error bar (about +/- 70
Elo), and the games were at 30s+0.3s rather than 40/15, so the gap at your
control may well be larger. An independent check lands in the same place --
62.5% over 16 games against Stockfish at `UCI_Elo 3190`. I'd rather you placed the engine from its first real games than trusted
my number.

---

## Where the strength estimate comes from

CCRL wants a number so they can put the engine in a division where it gets
useful opponents — Graham's Amateur Series divisions are how most new engines
get their first few hundred games. A wrong estimate is not fatal; it costs
them one badly-matched gauntlet, and they re-rate from their own games.

### What was actually measured

Both runs used the **packaged `uiagBot-1.3-avx2.exe`**, one thread, 256 MB
hash, at 30s+0.3s, via `ccrl/validate.toml`:

| opponent | games | result | Elo gap |
| --- | --- | --- | --- |
| **Stockfish 19 64-bit, 1 thread, full strength** | 30 | 4.5/30 — **15.0%** (+0 =9 -21) | **-301** |
| Stockfish, `UCI_LimitStrength`, `UCI_Elo 3190` | 16 | 10/16 — 62.5% (+6 =8 -2) | +89 |
| Stockfish, `UCI_Elo 3000` | 16 | 13.5/16 — 84.4% (+12 =3 -1) | +293 |
| Stockfish, `UCI_Elo 2800` | 16 | 13.5/16 — 84.4% (+12 =3 -1) | +293 |

Reproduce with:

```
python testsuite/run.py --config ccrl/validate.toml --no-save \
    gauntlet --opponents sf-full -n 30 --tc 30+0.3 -c 4
```

### Turning that into a CCRL number

Only the first row can do it. **Stockfish 19 64-bit (single CPU) is rated
3629 ± 12 on CCRL 40/15**, so a 301-Elo gap puts uiagBot near **3330**. The
throttled-Stockfish rows cannot: `UCI_Elo` is Stockfish's own scale, and a
throttled engine plays differently from an engine of that nominal rating
playing its best. Taken at face value they would say 3279, which is close
enough to be reassuring and not independent evidence — same opponent, same
binary, same quirks.

Reasons the real figure could sit lower:

* **30 games is a wide error bar** — about ±70 Elo on a 15% score.
* **30s+0.3s is not 40/15.** CCRL gives each engine about 22 seconds a move;
  this measurement gave well under one. Against a much stronger opponent the
  gap usually *widens* with time, because the stronger engine converts
  better, so this is the direction that would hurt.
* **Openings and adjudication differ** from CCRL's: their generic book is
  capped at 12 moves per side, and the testsuite resigns and calls draws on
  its own thresholds.

Hence the **3250-3400** range in the post rather than a single number. For
scale, CCRL 40/15 around 3390 holds Clover 3.2.1, Renegade 1.0.0, Midnight 9
and Calvin 5.1.0; around 3300 is the lower half of the Amateur Series' upper
divisions.

### Tightening it further

Play engines that are **already on the CCRL list** at something near their
rating, not Stockfish. Download two or three from the range above, add them
to `ccrl/validate.toml` as opponents with their published CCRL rating as
`elo`, and run a hundred games each at a slower control:

```
python testsuite/run.py --config ccrl/validate.toml --no-save \
    gauntlet --opponents <them> -n 100 --tc 60+0.6 -c 4
```

That anchors directly to the scale CCRL publishes, against several different
opponents rather than one, and at a control closer to theirs.

---

## After it is accepted

* CCRL 40/15 tests at **40 moves in 15 minutes** on hardware equivalent to an
  i7-4770k (Stockfish 10 is their yardstick for scaling it to other
  machines), **repeating** — another 15 minutes for the next 40 moves, and so
  on. Ponder off, 256 or 512 MB hash, a generic opening book capped at 12
  moves per side, 4/5/6-piece tablebases for engines that support them. The
  tester sets all of this, which is why your defaults matter for a first
  impression but are not binding.
* Results appear at <https://computerchess.org.uk/ccrl/4040/>. Entries with
  200+ games are shown in bold.
* A version has to be **less than 18 months old** to stay in the Amateur
  Series, so a release left untouched for two years drops out of the
  divisions.
* Post 1.4 and later **in the same thread**. New thread per engine, not per
  version.

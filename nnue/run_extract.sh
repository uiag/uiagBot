#!/bin/sh
# Shard the lichess evaluation dump across N processes.
#
# Each worker takes one byte range, skips to the next line boundary if its
# range starts mid-line, and reads its last line to the end, so the shards
# together cover every line exactly once with no overlap.
#
#     sh nnue/run_extract.sh [workers]
set -e
cd "$(dirname "$0")/.."

IN=evaluationModel/lichess_db_eval.jsonl
OUTDIR=nnue/data
WORKERS=${1:-7}

mkdir -p "$OUTDIR"
SIZE=$(stat -c %s "$IN")
CHUNK=$((SIZE / WORKERS))
echo "input $SIZE bytes, $WORKERS workers, $CHUNK bytes each"

i=0
while [ "$i" -lt "$WORKERS" ]; do
    START=$((i * CHUNK))
    if [ "$((i + 1))" -eq "$WORKERS" ]; then END=-1; else END=$(((i + 1) * CHUNK)); fi
    nnue/extract.exe "$IN" "$OUTDIR/shard$i.bin" "$START" "$END" \
        2>"$OUTDIR/shard$i.log" &
    i=$((i + 1))
done
wait
echo "--- done ---"
cat "$OUTDIR"/shard*.log
ls -la "$OUTDIR"

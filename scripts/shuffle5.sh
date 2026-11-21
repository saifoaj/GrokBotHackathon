#!/bin/sh
# Determinism check: the judges run the system 5 times and expect identical output.
#
# Run 0: <system dir>/run on the data folder as it is (the real entry point).
# Runs 1-5: shuffled copies of the data folder (line order of every .jsonl file
# and the key order inside each record), each with a different PYTHONHASHSEED.
# For our own system, runs 1-5 call system/screen.py directly, because
# system/run forces PYTHONHASHSEED=0 as a backup and the code must be correct
# without it. Any other system (no screen.py) is called through its run.
#
# All six SHA-256 hashes must match. Exit code 0 = pass, 1 = fail.
#
# Usage: scripts/shuffle5.sh [data folder] [system dir]
#        (defaults: dev, and this repository's system/)
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${1:-$ROOT/dev}"
DATA="$(cd "$DATA" && pwd)"
SYSDIR="${2:-$ROOT/system}"
SYSDIR="$(cd "$SYSDIR" && pwd)"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/shuffle5.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT INT TERM

if command -v sha256sum >/dev/null 2>&1; then
    hash_of() { sha256sum "$1" | cut -d' ' -f1; }
else
    hash_of() { shasum -a 256 "$1" | cut -d' ' -f1; }
fi

# Copy the data folder, shuffling each JSON Lines file with a fixed seed.
shuffle_copy() {
    python3 -B - "$DATA" "$1" "$2" <<'PY'
import json, os, random, shutil, sys
source, target, seed = sys.argv[1], sys.argv[2], int(sys.argv[3])
rng = random.Random(seed)
os.makedirs(target)
for name in sorted(os.listdir(source)):
    path = os.path.join(source, name)
    if not os.path.isfile(path):
        continue
    if not name.endswith(".jsonl"):
        shutil.copyfile(path, os.path.join(target, name))
        continue
    with open(path, encoding="utf-8") as handle:
        lines = [line for line in handle if line.strip()]
    rng.shuffle(lines)
    with open(os.path.join(target, name), "w", encoding="utf-8", newline="\n") as handle:
        for line in lines:
            row = json.loads(line)
            keys = sorted(row)
            rng.shuffle(keys)
            handle.write(json.dumps({key: row[key] for key in keys}, ensure_ascii=False) + "\n")
PY
}

run_seeded() {  # run_seeded <seed> <data> <out>
    if [ -f "$SYSDIR/screen.py" ]; then
        PYTHONHASHSEED="$1" PYTHONDONTWRITEBYTECODE=1 python3 -B -s "$SYSDIR/screen.py" "$2" "$3" 2>/dev/null
    else
        PYTHONHASHSEED="$1" "$SYSDIR/run" "$2" "$3" 2>/dev/null
    fi
}

echo "data: $DATA"
echo "system: $SYSDIR"
"$SYSDIR/run" "$DATA" "$WORK/out0.jsonl" 2>/dev/null
REFERENCE="$(hash_of "$WORK/out0.jsonl")"
echo "run 0  original  system/run         $REFERENCE"

FAILED=0
RUN=1
for SEED in 1 42 777 2024 31337; do
    shuffle_copy "$WORK/data$RUN" "$RUN"
    run_seeded "$SEED" "$WORK/data$RUN" "$WORK/out$RUN.jsonl"
    HASH="$(hash_of "$WORK/out$RUN.jsonl")"
    if [ "$HASH" = "$REFERENCE" ]; then MARK="ok"; else MARK="DIFFERENT"; FAILED=1; fi
    echo "run $RUN  shuffled  PYTHONHASHSEED=$SEED  $HASH  $MARK"
    RUN=$((RUN + 1))
done

if [ "$FAILED" -eq 0 ]; then
    echo "PASS: all 6 outputs are identical"
else
    echo "FAIL: outputs differ; first differing lines vs run 0:"
    for N in 1 2 3 4 5; do
        diff "$WORK/out0.jsonl" "$WORK/out$N.jsonl" | head -n 4 || true
    done
    exit 1
fi

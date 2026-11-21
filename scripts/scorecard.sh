#!/bin/sh
# One-line scorecard for any branch's system.
#
# Runs <checkout>/system/run on the data folder, validate.py --score on the
# result, and the 5-run shuffled determinism check, then prints one line:
#   scorecard saif@756678f | score 100.0 | right 50/50 | unsafe 0 | problems 0 |
#   DIFF 460 SAME 122 REVIEW 237 | determinism PASS | run 10s
# It never touches the checkout's output/ folder.
#
# Usage: scripts/scorecard.sh [checkout dir] [data folder]
#   checkout dir: a clone or worktree of any branch (default: this repository)
#   data folder:  default this repository's dev/
# To score a teammate's branch without leaving yours:
#   git fetch origin
#   git worktree add /tmp/alice origin/alice
#   scripts/scorecard.sh /tmp/alice
#   git worktree remove /tmp/alice
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$(cd "${1:-$ROOT}" && pwd)"
DATA="$(cd "${2:-$ROOT/dev}" && pwd)"
VALIDATE="$ROOT/validate.py"
[ -f "$TARGET/validate.py" ] && VALIDATE="$TARGET/validate.py"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/scorecard.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT INT TERM

BRANCH="$(git -C "$TARGET" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
COMMIT="$(git -C "$TARGET" rev-parse --short HEAD 2>/dev/null || echo unknown)"
NAME="$BRANCH@$COMMIT"

if [ ! -x "$TARGET/system/run" ]; then
    echo "scorecard $NAME | FAIL: $TARGET/system/run missing or not executable"
    exit 1
fi

START="$(date +%s)"
if ! "$TARGET/system/run" "$DATA" "$WORK/out.jsonl" >"$WORK/run.log" 2>&1; then
    echo "scorecard $NAME | FAIL: system/run exited with an error (last lines below)"
    tail -n 5 "$WORK/run.log"
    exit 1
fi
SECONDS_TAKEN=$(( $(date +%s) - START ))

python3 "$VALIDATE" --data "$DATA" --output "$WORK/out.jsonl" --score "$DATA/practice_labels.jsonl" \
    >"$WORK/validate.log" 2>&1 || true

if "$ROOT/scripts/shuffle5.sh" "$DATA" "$TARGET/system" >"$WORK/shuffle.log" 2>&1; then
    DETERMINISM="PASS"
else
    DETERMINISM="FAIL"
fi

python3 - "$WORK/validate.log" "$WORK/out.jsonl" "$NAME" "$DETERMINISM" "$SECONDS_TAKEN" <<'PY'
import json, re, sys
log, out, name, determinism, seconds = sys.argv[1:6]
text = open(log, encoding="utf-8").read()
def find(pattern, default="?"):
    match = re.search(pattern, text)
    return match.groups() if match else (default,) * re.compile(pattern).groups
problems, = find(r"\d+ lines read, (\d+) problems")
right, unsafe = find(r"practice labels: \d+ hits, (\d+) right decisions, (\d+) unsafe clearances")
total, = find(r"practice labels: (\d+) hits")
score, = find(r"decision score ([\d.]+)")
counts = {"DIFFERENT_ENTITY": 0, "SAME_ENTITY": 0, "REVIEW": 0}
with open(out, encoding="utf-8") as handle:
    for line in handle:
        try:
            decision = json.loads(line).get("decision")
        except ValueError:
            continue
        if decision in counts:
            counts[decision] += 1
print("scorecard %s | score %s | right %s/%s | unsafe %s | problems %s | DIFF %d SAME %d REVIEW %d | "
      "determinism %s | run %ss" % (name, score, right, total, unsafe, problems, counts["DIFFERENT_ENTITY"],
                                   counts["SAME_ENTITY"], counts["REVIEW"], determinism, seconds))
PY

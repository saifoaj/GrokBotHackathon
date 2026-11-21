"""Compare several teams' output files hit by hit.

Usage:
  python3 tools/compare.py ours=output/dev.jsonl alice=/path/to/alice/dev.jsonl [bob=...]
         [--data dev] [--labels dev/practice_labels.jsonl] [--limit N] [--clears-only]

A file without "label=" gets its file name as label. The first file is the
base. Prints:
  1. per file: validator problems, practice score, unsafe clearances, right
     decisions, reason-code and customer-record rates, DIFFERENT/SAME/REVIEW counts;
  2. every hit where the files disagree on the decision, grouped by the kind
     of disagreement, with "one clears, another doesn't" groups first (those
     are potential unsafe clearances);
  3. for each such hit, every file's code, customer record, reason and
     evidence side by side (and the practice label when there is one).

Standard library only. Uses validate.py from the repository root.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import validate  # noqa: E402

DECISIONS = ["DIFFERENT_ENTITY", "SAME_ENTITY", "REVIEW"]
MISSING = "MISSING"


def parse_args(argv):
    files, options = [], {"data": os.path.join(ROOT, "dev"), "labels": None, "limit": None, "clears_only": False}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("--data", "--labels", "--limit"):
            if index + 1 >= len(argv):
                raise SystemExit("missing value after " + arg)
            value = argv[index + 1]
            options[arg[2:]] = int(value) if arg == "--limit" else value
            index += 2
            continue
        if arg == "--clears-only":
            options["clears_only"] = True
        elif arg in ("-h", "--help"):
            print(__doc__)
            raise SystemExit(0)
        else:
            label, _sep, path = arg.partition("=") if "=" in arg else (os.path.basename(arg), "", arg)
            files.append((label, path))
        index += 1
    if not files:
        print(__doc__)
        raise SystemExit(2)
    labels = [label for label, _p in files]
    if len(set(labels)) != len(labels):
        raise SystemExit("labels must be unique: %s" % labels)
    if options["labels"] is None:
        default = os.path.join(options["data"], "practice_labels.jsonl")
        options["labels"] = default if os.path.isfile(default) else None
    return files, options


def load_answers(path):
    """{hit_id: row}; unreadable lines are skipped (the validator reports them)."""
    answers = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("hit_id"), str) and row["hit_id"] not in answers:
                answers[row["hit_id"]] = row
    return answers


def practice_stats(answers, labels):
    if not labels:
        return None
    cost = baseline = right = unsafe = codes = records = 0
    for label in labels:
        answer = answers.get(label["hit_id"], {})
        decision = answer.get("decision", "REVIEW")
        if decision not in DECISIONS:
            decision = "REVIEW"
        cost += validate.COST[label["decision"]][decision]
        baseline += validate.COST[label["decision"]]["REVIEW"]
        if label["decision"] != "DIFFERENT_ENTITY" and decision == "DIFFERENT_ENTITY":
            unsafe += 1
        if decision == label["decision"]:
            right += 1
            if answer.get("reason_code") in label["reason_codes"]:
                codes += 1
            record = answer.get("customer_record_id")
            if record in label["customer_record_ids"] or (
                    label["customer_record_ids"] == ["AMBIGUOUS"] and record in (None, "", "AMBIGUOUS")):
                records += 1
    return {
        "score": 100.0 * max(0.0, 1 - float(cost) / baseline) if baseline else 0.0,
        "right": right, "total": len(labels), "unsafe": unsafe,
        "codes": codes, "records": records,
    }


def summary(files, options, all_answers, labels):
    print("=" * 78)
    print("PER FILE")
    print("=" * 78)
    header = "%-12s %8s %7s %6s %7s %6s %6s %6s %6s %6s" % (
        "file", "problems", "score", "unsafe", "right", "code%", "rec%", "DIFF", "SAME", "REVIEW")
    print(header)
    for label, path in files:
        try:
            _answers, errors = validate.validate(options["data"], path)
            problems = str(len(errors))
        except Exception as error:  # a broken file still gets a row
            problems = "ERR"
            sys.stderr.write("%s: validator failed (%s)\n" % (label, error))
        answers = all_answers[label]
        counts = dict((d, 0) for d in DECISIONS)
        for row in answers.values():
            if row.get("decision") in counts:
                counts[row["decision"]] += 1
        stats = practice_stats(answers, labels)
        if stats:
            right = stats["right"]
            print("%-12s %8s %7.1f %6d %7s %6.0f %6.0f %6d %6d %6d" % (
                label[:12], problems, stats["score"], stats["unsafe"], "%d/%d" % (right, stats["total"]),
                100.0 * stats["codes"] / right if right else 0, 100.0 * stats["records"] / right if right else 0,
                counts["DIFFERENT_ENTITY"], counts["SAME_ENTITY"], counts["REVIEW"]))
        else:
            print("%-12s %8s %7s %6s %7s %6s %6s %6d %6d %6d" % (
                label[:12], problems, "-", "-", "-", "-", "-",
                counts["DIFFERENT_ENTITY"], counts["SAME_ENTITY"], counts["REVIEW"]))
    print()


def disagreements(files, options, all_answers, labels):
    names = [label for label, _p in files]
    label_by_hit = dict((row["hit_id"], row) for row in (labels or []))
    hit_ids = sorted(set(h for answers in all_answers.values() for h in answers))
    groups = {}
    for hit_id in hit_ids:
        decisions = [all_answers[n].get(hit_id, {}).get("decision", MISSING) for n in names]
        distinct = sorted(set(decisions))
        if len(distinct) > 1:
            groups.setdefault(tuple(distinct), []).append(hit_id)

    def group_order(key):
        clears = "DIFFERENT_ENTITY" in key
        return (0 if clears else 1, key)

    print("=" * 78)
    total = sum(len(v) for v in groups.values())
    print("DISAGREEMENTS: %d hits differ in decision" % total)
    print("=" * 78)
    for key in sorted(groups, key=group_order):
        if options["clears_only"] and "DIFFERENT_ENTITY" not in key:
            continue
        hits = groups[key]
        tag = "  <-- one clears, another doesn't: check for unsafe clearances" if "DIFFERENT_ENTITY" in key else ""
        print()
        print("### %s: %d hits%s" % (" vs ".join(key), len(hits), tag))
        shown = hits if options["limit"] is None else hits[:options["limit"]]
        for hit_id in shown:
            label = label_by_hit.get(hit_id)
            alert = next((all_answers[n][hit_id].get("alert_id") for n in names if hit_id in all_answers[n]), "?")
            extra = ""
            if label:
                extra = "   [practice label: %s %s]" % (label["decision"], "/".join(label["reason_codes"]))
            print()
            print("--- %s (alert %s)%s" % (hit_id, alert, extra))
            for name in names:
                row = all_answers[name].get(hit_id)
                if row is None:
                    print("  %-10s MISSING (counts as REVIEW)" % name[:10])
                    continue
                print("  %-10s %s %s  customer=%s" % (name[:10], row.get("decision"), row.get("reason_code"),
                                                     row.get("customer_record_id")))
                print("  %-10s reason:   %s" % ("", row.get("reason")))
                print("  %-10s evidence: %s" % ("", ", ".join(row.get("evidence") or []) or "-"))
        if len(shown) < len(hits):
            print("  ... %d more (raise --limit)" % (len(hits) - len(shown)))


def main(argv):
    files, options = parse_args(argv[1:])
    labels = None
    if options["labels"]:
        with open(options["labels"], encoding="utf-8") as handle:
            labels = [json.loads(line) for line in handle if line.strip()]
    all_answers = {}
    for label, path in files:
        if not os.path.isfile(path):
            raise SystemExit("%s: file not found: %s" % (label, path))
        all_answers[label] = load_answers(path)
    summary(files, options, all_answers, labels)
    disagreements(files, options, all_answers, labels)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

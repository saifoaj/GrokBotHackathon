"""Extraction coverage and practice-hit review.

Usage: python3 scripts/report.py [data folder]   (default: dev)

1. Coverage: how many free texts our patterns could not parse
   (profile-note age sentences, KYC notes, search snippets, dated-report
   lines, dobs field values).
2. Practice review: every wrong practice hit, wrong clearances first, with
   our trace next to the label's path.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "system"))

import countries  # noqa: E402
import customer  # noqa: E402
import extract  # noqa: E402
from names import simple_text  # noqa: E402
from screen import System  # noqa: E402

EXAMPLES = 5


def show(title, total, failed, examples):
    print("%s: %d texts, %d not parsed" % (title, total, len(failed)))
    for item in failed[:EXAMPLES]:
        print("    - " + item.replace("\n", " / ")[:200])


def coverage(system):
    data = system.data
    print("=" * 70)
    print("EXTRACTION COVERAGE")
    print("=" * 70)

    # Profile notes: age sentences in the first paragraph.
    with_words, failed = 0, []
    parsed_total = 0
    for hit in data.hits:
        paragraph = extract.first_paragraph(hit.get("profile_notes"))
        if not paragraph:
            continue
        subject = set(part for name in system.reader.read(hit).names for part in name.parts)
        ages = extract.ages_at_dates(paragraph, data.date_orders["hit_text"], subject_names=subject)
        parsed_total += 1 if ages else 0
        if extract.AGE_WORDS.search(paragraph):
            with_words += 1
            if not ages:
                failed.append("%s: %s" % (hit["hit_id"], paragraph))
    show("Profile first paragraphs with age wording", with_words, failed, failed)
    print("    (age at a date found in %d first paragraphs in total)" % parsed_total)

    # KYC notes.
    order = data.date_orders["notes"]
    kyc_total, how_counts, failed = 0, {}, []
    for note in data.notes:
        check = customer.parse_check_note(note, order)
        if check is None:
            continue
        kyc_total += 1
        how_counts[check.dob_how] = how_counts.get(check.dob_how, 0) + 1
        problems = []
        if check.dob is None:
            problems.append("no DOB (%s)" % check.dob_how)
        if not check.name:
            problems.append("no name")
        if not check.countries:
            problems.append("no country")
        if problems:
            failed.append("%s [%s]: %s" % (note["note_id"], ", ".join(problems), note.get("text")))
    show("KYC notes (identity checks)", kyc_total, failed, failed)
    print("    DOB found by: " + ", ".join("%s %d" % (k, how_counts[k]) for k in sorted(how_counts)))

    # Search snippets.
    order = data.date_orders["search"]
    failed = []
    for row in data.search_results:
        if not extract.ages_at_dates(row.get("snippet") or "", order, loose=True):
            failed.append("%s: %s" % (row["result_id"], row.get("snippet")))
    show("Search snippets (age at a date)", len(data.search_results), failed, failed)
    no_country = [r for r in data.search_results if not countries.find_in_text(r.get("snippet") or "")]
    print("    snippets naming no country: %d" % len(no_country))

    # Dated-report lines.
    total, strict, other = 0, 0, []
    for hit in data.hits:
        for line in (hit.get("profile_notes") or "").splitlines():
            if simple_text(line).startswith("according to"):
                total += 1
                if extract.dated_reports(line):
                    strict += 1
                else:
                    other.append("%s: %s" % (hit["hit_id"], line.strip()))
    print("Lines starting 'According to': %d, %d in the dated-report form, %d other wordings "
          "(not dated reports by policy)" % (total, strict, len(other)))
    for item in other[:EXAMPLES]:
        print("    - " + item[:160])

    # dobs field values.
    failed = []
    total = 0
    for hit in data.hits:
        facts = system.reader.read(hit)
        values = hit.get("dobs") or []
        total += len(values)
        failed += ["%s: %r" % (hit["hit_id"], v) for v in facts.coverage.get("dobs_unparsed", [])]
    show("Hit dobs field values", total, failed, failed)
    print()


def practice(system, folder):
    path = os.path.join(folder, "practice_labels.jsonl")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as handle:
        labels = [json.loads(line) for line in handle if line.strip()]
    hits = {hit["hit_id"]: hit for hit in system.data.hits}
    wrong = []
    for label in sorted(labels, key=lambda row: row["hit_id"]):
        result = system.decide_hit(hits[label["hit_id"]])
        if result["decision"] != label["decision"]:
            unsafe = result["decision"] == "DIFFERENT_ENTITY"
            wrong.append((0 if unsafe else 1, label["hit_id"], label, result))
    wrong.sort(key=lambda item: (item[0], item[1]))
    print("=" * 70)
    print("PRACTICE REVIEW: %d of %d decisions wrong (%d wrong clearances)" % (
        len(wrong), len(labels), sum(1 for w in wrong if w[0] == 0)))
    print("=" * 70)
    for _unsafe, hit_id, label, result in wrong:
        print()
        print("%s: label %s %s, ours %s %s" % (hit_id, label["decision"], label["reason_codes"],
                                              result["decision"], result["reason_code"]))
        print("  label path:")
        for step in label["path"]:
            print("    " + step)
        print("  our trace:")
        for step in result["trace"]:
            print("    " + step)
        print("  our reason: " + result["reason"])


def main(argv):
    folder = argv[1] if len(argv) > 1 else os.path.join(ROOT, "dev")
    system = System(folder)
    coverage(system)
    practice(system, folder)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""Screening review system (Track 2: "Same person or not?").

Usage: python3 screen.py <data folder> <output file>

Baseline version: every hit is sent to REVIEW / INSUFFICIENT_DATA with no
customer record resolved. The structure (load -> decide each hit -> render
-> write) is where the real customer resolution and hit comparison will go.

Determinism rules followed here:
- no clock, no randomness, no network;
- every loaded file is sorted by its ID straight after loading;
- nothing iterates over a set or dict without sorted();
- output lines sorted by (alert_id, hit_id), evidence sorted and de-duplicated,
  json.dumps(sort_keys=True, ensure_ascii=False).
"""

import json
import os
import sys
import unicodedata

REVIEW = "REVIEW"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# ---------------------------------------------------------------- loading

def warn(message):
    sys.stderr.write("warning: " + message + "\n")


def load_jsonl(folder, filename, id_key):
    """Read a JSON Lines file and return its rows sorted by id_key.

    Lines that are not valid JSON objects, or have no ID, are skipped with a
    warning. Duplicate IDs keep one row, chosen by its canonical JSON text, so
    the choice never depends on the input line order.
    """
    path = os.path.join(folder, filename)
    rows = []
    if not os.path.isfile(path):
        warn("missing file " + filename)
        return rows
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                warn("%s line %d is not JSON, skipped" % (filename, number))
                continue
            if not isinstance(row, dict) or not isinstance(row.get(id_key), str):
                warn("%s line %d has no %s, skipped" % (filename, number, id_key))
                continue
            rows.append(row)
    rows.sort(key=lambda row: (row[id_key], canonical(row)))
    unique = []
    for row in rows:
        if unique and unique[-1][id_key] == row[id_key]:
            warn("%s has duplicate %s %s, kept one" % (filename, id_key, row[id_key]))
            continue
        unique.append(row)
    return unique


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def index_by(rows, id_key):
    return {row[id_key]: row for row in rows}


# ---------------------------------------------------------------- text

def normalise(text):
    """Accent-free, case-folded text for comparisons (NFKD, strip marks)."""
    if not isinstance(text, str):
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold()


def shown(value):
    """A value as it should appear inside a reason sentence."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "unknown"


# ---------------------------------------------------------------- deciding

def decide_hit(hit, alert):
    """Return the decision for one hit.

    Baseline: no customer resolution or comparison yet, so nothing can clear
    or confirm the hit and the safe answer is REVIEW.
    """
    customer = (alert or {}).get("customer") or {}
    screened_name = shown(customer.get("name"))
    hit_name = shown(hit.get("name"))
    reason = (
        "No customer record has been resolved yet for the screened customer "
        "%s in alert %s, so hit %s (%s) has not been compared with them. "
        "No fact has been found that either contradicts or confirms the match. "
        "A verified legal name, date of birth and country for the customer, "
        "compared with the hit's details, would settle it."
        % (screened_name, hit["alert_id"], hit["hit_id"], hit_name)
    )
    return {
        "decision": REVIEW,
        "reason_code": INSUFFICIENT_DATA,
        "customer_record_id": None,
        "reason": reason,
        "evidence": [],
    }


# ---------------------------------------------------------------- output

def render(hit, result):
    line = {
        "hit_id": hit["hit_id"],
        "alert_id": hit["alert_id"],
        "decision": result["decision"],
        "reason_code": result["reason_code"],
        "customer_record_id": result["customer_record_id"],
        "reason": result["reason"],
        "evidence": sorted(set(result["evidence"])),
    }
    return canonical(line)


def run(data_folder, output_file):
    alerts = index_by(load_jsonl(data_folder, "alerts.jsonl", "alert_id"), "alert_id")
    hits = load_jsonl(data_folder, "hits.jsonl", "hit_id")

    lines = []
    for hit in sorted(hits, key=lambda h: (str(h.get("alert_id")), h["hit_id"])):
        if not isinstance(hit.get("alert_id"), str):
            warn("hit %s has no alert_id, skipped" % hit["hit_id"])
            continue
        alert = alerts.get(hit["alert_id"])
        try:
            result = decide_hit(hit, alert)
        except Exception as error:  # any doubt: REVIEW, never clear
            warn("hit %s could not be decided (%s)" % (hit["hit_id"], error))
            result = {
                "decision": REVIEW,
                "reason_code": INSUFFICIENT_DATA,
                "customer_record_id": None,
                "reason": "Hit %s could not be assessed because its data could not "
                          "be read. A person needs to review it against the "
                          "customer's records." % hit["hit_id"],
                "evidence": [],
            }
        lines.append(render(hit, result))

    parent = os.path.dirname(os.path.abspath(output_file))
    if not os.path.isdir(parent):
        os.makedirs(parent)
    with open(output_file, "w", encoding="utf-8", newline="\n") as handle:
        for line in lines:
            handle.write(line + "\n")
    return len(lines)


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: screen.py <data folder> <output file>\n")
        return 2
    data_folder, output_file = argv[1], argv[2]
    if not os.path.isdir(data_folder):
        sys.stderr.write("error: data folder not found: %s\n" % data_folder)
        return 2
    count = run(data_folder, output_file)
    sys.stderr.write("wrote %d hits to %s\n" % (count, output_file))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

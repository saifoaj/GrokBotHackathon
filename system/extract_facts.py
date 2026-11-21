"""Turn every note and search result into the facts the rules can read.

The patterns are the same ones the decision uses. A note or snippet that
does not match contributes an empty fact, not a guess.

Usage:
    python system/extract_facts.py <data folder> <output.json>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from decide_hits import (
    AGE_PATTERNS,
    BARE_AGE,
    _month_dates,
    _nearest_date,
    caps_name,
    countries_in_text,
    dob_from_note,
    document_country,
    lived_countries,
    note_verified,
    parse_iso,
)
from find_customers import read_jsonl
from gather_sources import DEFAULT_PHRASE, LISTING_PHRASES

PHRASE_ORDER = [LISTING_PHRASES[key] for key in ("sanctions", "pep", "enforcement", "adverse media", "soe")]
PHRASE_ORDER.append(DEFAULT_PHRASE)


def date_fact(parts):
    if not parts:
        return None
    year, month, day = parts
    return {"year": year, "month": month, "day": day}


def age_facts(text, allow_bare):
    """Ages the decision would turn into birth years, with the date they were stated at."""
    if not text:
        return []
    dates = _month_dates(text)
    spans = []
    for pattern in AGE_PATTERNS:
        for match in pattern.finditer(text):
            age = int(match.group(1))
            if 1 <= age <= 110:
                spans.append((match.start(), match.end(), age))
    if allow_bare:
        for pattern in BARE_AGE:
            for match in pattern.finditer(text):
                age = int(match.group(1))
                if 10 <= age <= 99:
                    spans.append((match.start(), match.end(), age))
    found = []
    seen = set()
    for start, end, age in spans:
        nearest = _nearest_date(dates, start, end)
        if not nearest:
            continue
        year, month, day, pos = nearest
        if day == age and abs(pos - start) < 8:
            continue
        pair = (year - age, year - age - 1)
        if pair in seen or pair[0] < 1850 or pair[1] < 1850:
            continue
        seen.add(pair)
        older, newer = sorted(pair)
        found.append({
            "age": age,
            "as_of": {"year": year, "month": month, "day": day},
            "birth_years": [older, newer],
        })
    return found


def listing_phrases(text):
    folded = str(text or "").casefold()
    return [phrase for phrase in PHRASE_ORDER if phrase in folded]


def note_fact(row):
    text = row.get("text") or ""
    channel = row.get("channel") or ""
    if channel == "kyc":
        passed = note_verified(text)
        checked = parse_iso(row.get("created_at"))
        return {
            "note_id": row.get("note_id"),
            "channel": channel,
            "created_at": row.get("created_at"),
            "requester_email": row.get("requester_email"),
            "role": "identity",
            "passed": passed,
            "legal_name": caps_name(text) if passed else None,
            "date_of_birth": date_fact(dob_from_note(text, checked)) if passed else None,
            "document_country": document_country(text) if passed else None,
            "lived_countries": [],
        }
    lived = sorted(set(lived_countries(text)))
    return {
        "note_id": row.get("note_id"),
        "channel": channel,
        "created_at": row.get("created_at"),
        "requester_email": row.get("requester_email"),
        "role": "residence" if lived else "none",
        "passed": None,
        "legal_name": None,
        "date_of_birth": None,
        "document_country": None,
        "lived_countries": lived,
    }


def search_fact(row):
    snippet = row.get("snippet") or ""
    return {
        "result_id": row.get("result_id"),
        "query": row.get("query") or "",
        "source": row.get("source"),
        "listing_phrases": listing_phrases(snippet),
        "countries": sorted(set(countries_in_text(snippet))),
        "ages": age_facts(snippet, True),
    }


def extract(data_dir):
    data_dir = Path(data_dir)
    notes = [note_fact(row) for row in read_jsonl(data_dir / "notes.jsonl")]
    results = [search_fact(row) for row in read_jsonl(data_dir / "search_results.jsonl")]
    notes.sort(key=lambda row: row["note_id"] or "")
    results.sort(key=lambda row: row["result_id"] or "")
    return {"notes": notes, "search_results": results}


def main():
    parser = argparse.ArgumentParser(description="Extract deterministic facts from notes and search results.")
    parser.add_argument("data", help="Folder containing notes.jsonl and search_results.jsonl")
    parser.add_argument("output", help="JSON file to write")
    args = parser.parse_args()
    document = extract(args.data)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        previous = json.loads(output.read_text(encoding="utf-8"))
        for key in ("model_notes", "model_search_results", "model_hits"):
            if previous.get(key):
                document[key] = previous[key]
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    notes = document["notes"]
    results = document["search_results"]
    passed = [row for row in notes if row["role"] == "identity" and row["passed"]]
    failed = sum(1 for row in notes if row["role"] == "identity" and not row["passed"])
    residence = sum(1 for row in notes if row["role"] == "residence")
    blank = sum(1 for row in notes if row["role"] == "none")
    print(f"wrote {output} ({output.stat().st_size} bytes)")
    print(f"notes {len(notes)}: passed identity {len(passed)}, failed identity {failed}, residence {residence}, no fact {blank}")
    print(f"  passed identity with a name {sum(1 for row in passed if row['legal_name'])}, a birth date {sum(1 for row in passed if row['date_of_birth'])}, a document country {sum(1 for row in passed if row['document_country'])}")
    print(f"search results {len(results)}: with a listing phrase {sum(1 for row in results if row['listing_phrases'])}, with an age {sum(1 for row in results if row['ages'])}, with a country {sum(1 for row in results if row['countries'])}")


if __name__ == "__main__":
    main()

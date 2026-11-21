"""Ask Grok whether each free-text fact is actually supported by its source.

Usage:
    python system/audit_facts.py <data folder> <facts.json>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from decide_hits import birth_years_from_ages, first_paragraph, lived_countries
from facts_store import FactBook
from fill_model_facts import api_settings, complete
from find_customers import CustomerSearch
from gather_sources import SourceIndex, gather

INSTRUCTION = (
    "You check extracted facts against the full source text. supported is true only when the text itself says the claim. "
    "A country counts when the note ties that country to this customer, whatever verb it uses. "
    "An age counts only when it is that named person's age at that date. A sentence, a fine, a case number, or someone else's age does not. "
    "Someone aged N in year Y was born in Y-N or Y-N-1. Say supported false if the birth years are not those two years. "
    "A date of birth counts only when the text calls it a birth date. A document country counts only when the identity document is from that country. "
    "Judge the full text, not a shortened quote. "
    'Reply with JSON only: {"items": [{"id": "...", "supported": true, "why": "one short sentence"}]}.'
)


def claims(rows, book):
    found = []
    seen_notes = set()
    seen_hits = set()
    seen_search = set()
    for row in rows:
        hit = row["hit"]
        if hit["hit_id"] not in seen_hits and (hit.get("entity_type") or "") != "Company":
            seen_hits.add(hit["hit_id"])
            paragraph = first_paragraph(hit.get("profile_notes"))
            pairs = birth_years_from_ages(paragraph, False)
            extra = book.profile_age_pairs(hit) if not pairs else []
            years = sorted({year for pair in pairs + extra for year in pair})
            if years:
                found.append({
                    "id": hit["hit_id"],
                    "kind": "profile_age",
                    "name": hit.get("name"),
                    "birth_years": years,
                    "text": paragraph[:1500],
                })
        for item in row["search_results"]:
            if not item.get("about_listing") or item["result_id"] in seen_search:
                continue
            pairs = birth_years_from_ages(item.get("snippet"), True)
            extra = book.search_age_pairs(item) if not pairs else []
            years = sorted({year for pair in pairs + extra for year in pair})
            if not years:
                continue
            seen_search.add(item["result_id"])
            found.append({
                "id": item["result_id"],
                "kind": "listing_age",
                "name": hit.get("name"),
                "birth_years": years,
                "text": (item.get("snippet") or "")[:1500],
            })
        for person in row["people"]:
            for note in person["notes"]:
                if note["note_id"] in seen_notes or note.get("channel") == "kyc":
                    continue
                countries = list(lived_countries(note.get("text")))
                for key in book.lived(note):
                    if key not in countries:
                        countries.append(key)
                if not countries:
                    continue
                seen_notes.add(note["note_id"])
                found.append({
                    "id": note["note_id"],
                    "kind": "lived",
                    "countries": countries,
                    "text": (note.get("text") or "")[:1500],
                })
    for row in book.notes.values():
        if row["note_id"] in seen_notes:
            continue
        if row.get("date_of_birth") or row.get("document_country") or row.get("legal_name"):
            found.append({"id": row["note_id"], "kind": "identity_model", "claim": row})
    return found


def main():
    parser = argparse.ArgumentParser(description="Check free-text facts against their source text.")
    parser.add_argument("data")
    parser.add_argument("facts")
    args = parser.parse_args()
    settings = api_settings()
    if not settings:
        raise SystemExit("no API key")
    settings = dict(settings)
    settings["model"] = "grok-4.7"
    search = CustomerSearch(args.data)
    sources = SourceIndex(args.data)
    rows = gather(search, sources)
    book = FactBook.load(args.facts)
    items = claims(rows, book)
    notes = {}
    if any(item["kind"] == "identity_model" for item in items):
        from find_customers import read_jsonl
        notes = {row["note_id"]: row.get("text") for row in read_jsonl(Path(args.data) / "notes.jsonl")}
        for item in items:
            if item["kind"] == "identity_model":
                claim = item.pop("claim")
                item["text"] = (notes.get(item["id"]) or "")[:1500]
                if claim.get("date_of_birth"):
                    item["date_of_birth"] = claim["date_of_birth"]["value"]
                if claim.get("document_country"):
                    item["document_country"] = claim["document_country"]["country"]
                if claim.get("legal_name"):
                    item["legal_name"] = claim["legal_name"]["value"]
    print(f"auditing {len(items)} facts with {settings['model']}", flush=True)
    verdicts = {}
    for start in range(0, len(items), 8):
        batch = items[start:start + 8]
        print(f"  {start + 1}-{start + len(batch)}", flush=True)
        try:
            reply = complete(settings, INSTRUCTION, batch)
        except Exception as exc:
            print(f"  batch failed ({exc.__class__.__name__})")
            continue
        for row in reply.get("items") or []:
            if isinstance(row, dict) and row.get("id"):
                verdicts[row["id"]] = row
        missing = [item["id"] for item in batch if item["id"] not in verdicts]
        if missing:
            print("  missing", ", ".join(missing))
    report = []
    for item in items:
        verdict = verdicts.get(item["id"], {"supported": None, "why": "no reply"})
        report.append({"id": item["id"], "kind": item["kind"], "supported": verdict.get("supported"), "why": verdict.get("why")})
        if verdict.get("supported") is not True:
            print(f"NO {item['id']} {item['kind']}: {verdict.get('why')}")
    out = Path(args.facts).parent / "fact_audit.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rejected = sum(1 for row in report if row["supported"] is not True)
    print(f"wrote {out} ({len(report)} checked, {rejected} not supported)")


if __name__ == "__main__":
    main()

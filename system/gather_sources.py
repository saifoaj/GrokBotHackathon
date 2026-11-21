"""Gather the records that may belong to the person behind each screening hit.

The customer side is the person already resolved for the alert: every customer
record in that cluster, their accounts, onboarding rows, identity checks, and
notes filed under their email. An alert that still fits more than one person
keeps those people separate.

The listed side is the hit itself, plus web search results for its name or a
customer's name. A result is marked about the listing only when it uses the
hit's listing phrase and a country tied to the hit. Other results for the same
name are namesakes: they are included because the name correlates, and the
flag says they are not the listing.

Usage:
    python system/gather_sources.py <data folder> <output.jsonl>
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from countries import NOT_COUNTRIES
from find_customers import (
    COUNTRIES,
    CustomerSearch,
    country_key,
    fold,
    read_jsonl,
    screened_view,
)
LISTING_PHRASES = {
    "sanctions": "a sanctioned individual",
    "pep": "a politically exposed person",
    "enforcement": "the subject of enforcement action",
    "adverse media": "named in adverse media",
    "soe": "a state-owned enterprise official",
}
DEFAULT_PHRASE = "named on a watchlist"


def query_key(name):
    parts = [part for part in fold(name).split() if part]
    if not parts:
        return ""
    return " ".join(sorted(parts))


def listing_phrase(hit):
    raw = hit.get("primary_category")
    if not raw:
        categories = hit.get("categories") or []
        raw = categories[0] if categories else ""
    return LISTING_PHRASES.get(fold(raw), DEFAULT_PHRASE)


def _known_country(text):
    spaced = fold(text)
    if not spaced or spaced in NOT_COUNTRIES:
        return None
    compact = spaced.replace(" ", "")
    if spaced in COUNTRIES or compact in COUNTRIES:
        key = country_key(text)
        if key and key not in NOT_COUNTRIES:
            return key
    return None


def _country_from_piece(piece):
    """Last real country in a city/region/country string. Postcodes are skipped."""
    parts = [part.strip() for part in piece.split(",") if part.strip()]
    for part in reversed(parts):
        if any(char.isdigit() for char in part):
            continue
        found = _known_country(part)
        if found:
            return found
    for part in reversed(parts):
        if any(char.isdigit() for char in part):
            continue
        key = country_key(part)
        if key and key not in NOT_COUNTRIES:
            return key
    return None


def hit_countries(hit):
    values = []
    for field in ("countries", "nationality", "citizenship"):
        raw = hit.get(field) or []
        if isinstance(raw, str):
            raw = [raw]
        values.extend(raw)
    if hit.get("place_of_birth"):
        values.append(hit["place_of_birth"])
    values.extend(hit.get("addresses") or [])
    found = []
    seen = set()
    for value in values:
        if not value:
            continue
        text = re.sub(r"\[[^\]]*\]", " ", str(value))
        for piece in re.split(r"[;|]", text):
            key = _country_from_piece(piece)
            if key and key not in seen:
                seen.add(key)
                found.append(key)
    return found


def _mention_forms():
    forms = defaultdict(set)
    for alias, canonical in COUNTRIES.items():
        if canonical in NOT_COUNTRIES:
            continue
        if len(alias) >= 4 or " " in alias:
            forms[canonical].add(alias)
    return forms


MENTION_FORMS = _mention_forms()


def mentions_country(snippet, countries):
    text = f" {fold(snippet)} "
    for key in countries:
        forms = set(MENTION_FORMS.get(key, ()))
        forms.add(key)
        for form in forms:
            if form and f" {form} " in text:
                return True
    return False


def about_listing(snippet, phrase, countries):
    if not countries or phrase not in str(snippet).casefold():
        return False
    return mentions_country(snippet, countries)


def hit_name_keys(hit):
    names = [hit.get("name")]
    names.extend(hit.get("aliases") or [])
    built = " ".join(
        part for part in (hit.get("first_name"), hit.get("middle_name"), hit.get("last_name")) if part
    )
    if built:
        names.append(built)
    if hit.get("best_matching_name"):
        names.append(hit["best_matching_name"])
    return {key for key in (query_key(name) for name in names if name) if key}


def customer_name_keys(records, screened_name):
    names = [screened_name]
    for record in records:
        for field in ("full_name", "preferred_name", "login_name"):
            if record.get(field):
                names.append(record[field])
    return {key for key in (query_key(name) for name in names if name) if key}


class SourceIndex:
    def __init__(self, data_dir):
        data_dir = Path(data_dir)
        self.accounts = defaultdict(list)
        for row in read_jsonl(data_dir / "accounts.jsonl"):
            self.accounts[row["customer_id"]].append(row)
        self.onboarding = defaultdict(list)
        for row in read_jsonl(data_dir / "onboarding.jsonl"):
            self.onboarding[row["customer_id"]].append(row)
        self.notes = defaultdict(list)
        for row in read_jsonl(data_dir / "notes.jsonl"):
            email = (row.get("requester_email") or "").casefold()
            if email:
                self.notes[email].append(row)
        self.search = defaultdict(list)
        for row in read_jsonl(data_dir / "search_results.jsonl"):
            self.search[row["query"]].append(row)
        for rows in self.accounts.values():
            rows.sort(key=lambda row: (row.get("account_id") or "", row.get("customer_id") or ""))
        for rows in self.onboarding.values():
            rows.sort(key=lambda row: (row.get("account_id") or "", row.get("kyc_applicant_id") or ""))
        for rows in self.notes.values():
            rows.sort(key=lambda row: (row.get("created_at") or "", row.get("note_id") or ""), reverse=True)
        for rows in self.search.values():
            rows.sort(key=lambda row: row.get("result_id") or "")

    def accounts_for(self, record_ids):
        rows = []
        seen = set()
        for record_id in record_ids:
            for row in self.accounts.get(record_id, []):
                key = (row.get("account_id"), row.get("customer_id"))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
        rows.sort(key=lambda row: (row.get("account_id") or "", row.get("customer_id") or ""))
        return rows

    def onboarding_for(self, record_ids):
        rows = []
        seen = set()
        for record_id in record_ids:
            for row in self.onboarding.get(record_id, []):
                key = (row.get("account_id"), row.get("customer_id"), row.get("kyc_applicant_id"))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
        rows.sort(key=lambda row: (row.get("account_id") or "", row.get("kyc_applicant_id") or ""))
        return rows

    def notes_for(self, records):
        rows = []
        seen = set()
        for record in records:
            email = (record.get("email") or "").casefold()
            if not email:
                continue
            for row in self.notes.get(email, []):
                if row["note_id"] in seen:
                    continue
                seen.add(row["note_id"])
                rows.append(row)
        rows.sort(key=lambda row: (row.get("created_at") or "", row.get("note_id") or ""), reverse=True)
        return rows

    def search_for(self, hit_keys, customer_keys, phrase, countries):
        found = {}
        for key in hit_keys | customer_keys:
            for row in self.search.get(key, []):
                item = found.get(row["result_id"])
                if item is None:
                    item = {
                        "result_id": row.get("result_id"),
                        "query": row.get("query"),
                        "title": row.get("title"),
                        "snippet": row.get("snippet"),
                        "source": row.get("source"),
                        "matches_hit_name": False,
                        "matches_customer_name": False,
                        "about_listing": False,
                    }
                    found[row["result_id"]] = item
                if key in hit_keys:
                    item["matches_hit_name"] = True
                if key in customer_keys:
                    item["matches_customer_name"] = True
        rows = list(found.values())
        for item in rows:
            if item["matches_hit_name"]:
                item["about_listing"] = about_listing(item["snippet"], phrase, countries)
        rows.sort(key=lambda item: item["result_id"] or "")
        return rows


def person_groups(search, found):
    if not found["customer_record_ids"]:
        return []
    if found["person_count"] <= 1:
        return [list(found["customer_record_ids"])]
    return [list(person) for person in search.people_of(found["customer_record_ids"])]


def gather_hit(search, sources, hit, alert, found):
    screened = screened_view(alert) if alert else screened_view({})
    groups = person_groups(search, found)
    people = []
    all_records = []
    for record_ids in groups:
        records = [search.customers[record_id] for record_id in record_ids if record_id in search.customers]
        all_records.extend(records)
        checks = search.kyc_for(record_ids)
        seen_checks = set()
        unique_checks = []
        for check in checks:
            if check["check_id"] in seen_checks:
                continue
            seen_checks.add(check["check_id"])
            unique_checks.append(check)
        people.append({
            "customer_record_ids": list(record_ids),
            "customers": records,
            "accounts": sources.accounts_for(record_ids),
            "onboarding": sources.onboarding_for(record_ids),
            "kyc_checks": unique_checks,
            "notes": sources.notes_for(records),
        })
    linked_ids, via = search.linked_ids(alert) if alert else ([], "")
    phrase = listing_phrase(hit)
    countries = hit_countries(hit)
    return {
        "hit_id": hit["hit_id"],
        "alert_id": hit["alert_id"],
        "screened_at": None if alert is None else alert.get("screened_at"),
        "screened": screened,
        "link": {
            "via": found["via"] or via,
            "account_ref": None if alert is None else alert.get("account_ref"),
            "login_handle": None if alert is None else alert.get("login_handle"),
            "email": None if alert is None else alert.get("email"),
            "linked_customer_ids": [record_id for record_id in linked_ids if record_id in search.customers],
        },
        "customer_record_id": found["customer_record_id"],
        "person_count": found["person_count"],
        "people": people,
        "hit": hit,
        "hit_countries": countries,
        "listing_phrase": phrase,
        "search_results": sources.search_for(
            hit_name_keys(hit),
            customer_name_keys(all_records, screened.get("name")),
            phrase,
            countries,
        ),
    }


def gather(search, sources):
    cache = {}
    rows = []
    for hit in search.hits:
        alert_id = hit["alert_id"]
        alert = search.alerts.get(alert_id)
        if alert_id not in cache:
            cache[alert_id] = search.resolve_alert(alert) if alert else search._result([], "", [])
        rows.append(gather_hit(search, sources, hit, alert, cache[alert_id]))
    return rows


def evidence_ids(row):
    found = set()
    for person in row["people"]:
        found.update(person["customer_record_ids"])
        found.update(check["check_id"] for check in person["kyc_checks"])
        found.update(note["note_id"] for note in person["notes"])
        found.update(account["account_id"] for account in person["accounts"])
    found.update(item["result_id"] for item in row["search_results"])
    found.add(row["hit_id"])
    found.add(row["alert_id"])
    return found


def score_practice(data_dir, rows):
    path = Path(data_dir) / "practice_labels.jsonl"
    if not path.exists():
        return None
    labels = read_jsonl(path)
    by_hit = {row["hit_id"]: row for row in rows}
    covered = 0
    listing_hits = 0
    listing_total = 0
    misses = []
    for label in labels:
        row = by_hit.get(label["hit_id"])
        if row is None:
            misses.append((label["hit_id"], label["evidence"], "hit missing"))
            continue
        present = evidence_ids(row)
        missing = [item for item in label["evidence"] if item not in present]
        if missing:
            misses.append((label["hit_id"], missing, "not gathered"))
        else:
            covered += 1
        cited = [item for item in label["evidence"] if str(item).startswith("SR-")]
        if cited:
            flagged = {
                item["result_id"]
                for item in row["search_results"]
                if item["about_listing"]
            }
            listing_total += len(cited)
            listing_hits += sum(1 for item in cited if item in flagged)
    return covered, len(labels), listing_hits, listing_total, misses


def main():
    parser = argparse.ArgumentParser(description="Gather the records that may belong to each hit's person.")
    parser.add_argument("data", help="Folder containing the screening JSONL files")
    parser.add_argument("output", help="JSON Lines file to write, one hit per line")
    args = parser.parse_args()
    search = CustomerSearch(args.data)
    sources = SourceIndex(args.data)
    rows = gather(search, sources)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with_people = sum(1 for row in rows if row["people"])
    with_notes = sum(1 for row in rows if any(person["notes"] for person in row["people"]))
    with_checks = sum(1 for row in rows if any(person["kyc_checks"] for person in row["people"]))
    with_search = sum(1 for row in rows if row["search_results"])
    with_listing = sum(1 for row in rows if any(item["about_listing"] for item in row["search_results"]))
    print(f"wrote {len(rows)} hits to {output}")
    print(
        f"with a customer {with_people}, with notes {with_notes}, with kyc checks {with_checks}"
    )
    print(f"with name search results {with_search}, with a listing result {with_listing}")
    scored = score_practice(args.data, rows)
    if scored:
        covered, total, listing_hits, listing_total, misses = scored
        print(f"practice evidence present {covered}/{total}")
        print(f"practice search citations flagged as the listing {listing_hits}/{listing_total}")
        for hit_id, missing, why in misses:
            print(f"  {hit_id} {why}: {missing}")


if __name__ == "__main__":
    main()

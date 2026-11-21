"""Ask Grok for the facts the patterns missed, and store the ones whose quote is real.

A reply that does not quote the source is dropped. The decision is still made
by the rules in decide_hits.py. Set GROK_API_KEY for the xAI API, or
OPENROUTER_API_KEY when the grader runs this. Already stored facts are not
asked again.

Usage:
    python system/fill_model_facts.py <data folder> <facts.json>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

from decide_hits import (
    birth_years_from_ages,
    caps_name,
    countries_in_text,
    dob_from_note,
    document_country,
    first_paragraph,
    lived_countries,
    note_verified,
    parse_iso,
    parse_partial,
)
from facts_store import (
    DEATH_WORD,
    FactBook,
    _date_record,
    accept_age_pairs,
    accept_death,
    accept_doc_country,
    accept_dob,
    accept_name,
    countries_from_quote,
)
from find_customers import CustomerSearch
from gather_sources import SourceIndex, gather

DIGIT = re.compile(r"\b\d{1,3}\b")
XAI_MODEL = "grok-4.20-0309-non-reasoning"
OPENROUTER_MODEL = "x-ai/grok-4.20-0309-non-reasoning"
MODEL_BUDGET_SECONDS = 20 * 60


def load_env():
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and value and not os.environ.get(name):
            os.environ[name] = value


def api_settings():
    load_env()
    if os.environ.get("OPENROUTER_API_KEY"):
        return {
            "url": "https://openrouter.ai/api/v1/chat/completions",
            "key": os.environ["OPENROUTER_API_KEY"],
            "model": os.environ.get("GROK_MODEL", OPENROUTER_MODEL),
        }
    if os.environ.get("GROK_API_KEY"):
        return {
            "url": "https://api.x.ai/v1/chat/completions",
            "key": os.environ["GROK_API_KEY"],
            "model": os.environ.get("GROK_MODEL", XAI_MODEL),
        }
    return None


def _post(settings, body):
    request = urllib.request.Request(
        settings["url"],
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {settings['key']}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def complete(settings, instruction, payload):
    messages = [
        {"role": "system", "content": instruction},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    body = {"model": settings["model"], "temperature": 0, "messages": messages, "response_format": {"type": "json_object"}}
    try:
        data = _post(settings, body)
    except urllib.error.HTTPError as exc:
        if exc.code != 400:
            raise
        body.pop("response_format", None)
        data = _post(settings, body)
    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    content = str(content).strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?", "", content).strip()
        content = re.sub(r"```$", "", content).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(content[start:end + 1])


def _clip(text, limit):
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit]


def death_excerpt(text):
    matches = list(DEATH_WORD.finditer(text or ""))
    if not matches:
        return _clip(text, 3500)
    pieces = []
    for match in matches[:4]:
        pieces.append(text[max(0, match.start() - 500): match.end() + 500])
    return _clip("\n".join(pieces), 3500)


def select_gaps(rows, book):
    seen_notes = set()
    seen_hits = set()
    seen_search = set()
    lived, identity, hits, search = [], [], [], []
    for row in rows:
        hit = row["hit"]
        company = (hit.get("entity_type") or "") == "Company"
        listed = any(parse_partial(raw) for raw in (hit.get("dobs") or []))
        pattern_ages = [] if company else birth_years_from_ages(first_paragraph(hit.get("profile_notes")), False)
        if hit["hit_id"] not in seen_hits:
            seen_hits.add(hit["hit_id"])
            cached = book.hits.get(hit["hit_id"]) or {}
            ask_death = not parse_partial(hit.get("deceased_date")) and bool(DEATH_WORD.search(hit.get("profile_notes") or "")) and not cached.get("death_checked")
            paragraph = first_paragraph(hit.get("profile_notes"))
            ask_age = not company and not listed and not pattern_ages and bool(DIGIT.search(paragraph)) and not cached.get("age_checked")
            if ask_death or ask_age:
                hits.append({"hit": hit, "ask_death": ask_death, "ask_age": ask_age})
        if not company and not listed and not pattern_ages:
            for item in row["search_results"]:
                if not item.get("about_listing") or item["result_id"] in seen_search:
                    continue
                if birth_years_from_ages(item.get("snippet"), True) or not DIGIT.search(item.get("snippet") or ""):
                    continue
                if (book.search.get(item["result_id"]) or {}).get("ages_checked"):
                    continue
                seen_search.add(item["result_id"])
                search.append(item)
        for person in row["people"]:
            for note in person["notes"]:
                if note["note_id"] in seen_notes:
                    continue
                seen_notes.add(note["note_id"])
                if note["note_id"] in book.notes:
                    continue
                text = note.get("text") or ""
                if note.get("channel") != "kyc":
                    if countries_in_text(text) and not lived_countries(text):
                        lived.append(note)
                elif note_verified(text):
                    checked = parse_iso(note.get("created_at"))
                    missing = not caps_name(text) or not dob_from_note(text, checked) or not document_country(text)
                    if missing:
                        identity.append(note)
    return lived, identity, hits, search


def _by_id(rows):
    found = {}
    for row in rows or []:
        if isinstance(row, dict) and row.get("id"):
            found[row["id"]] = row
    return found


def _store_lived(book, note, reply):
    text = note.get("text") or ""
    lived = []
    for item in (reply or {}).get("lived") or []:
        if not isinstance(item, dict):
            continue
        for key in countries_from_quote(item.get("quote"), item.get("country"), text):
            lived.append({"country": key, "quote": item.get("quote")})
    book.notes[note["note_id"]] = {
        "note_id": note["note_id"],
        "lived": lived,
        "legal_name": None,
        "date_of_birth": None,
        "document_country": None,
    }
    return lived


def _store_identity(book, note, reply):
    text = note.get("text") or ""
    checked = parse_iso(note.get("created_at"))
    reply = reply or {}
    legal = reply.get("legal_name") if isinstance(reply.get("legal_name"), dict) else None
    name = accept_name(legal.get("quote"), legal.get("value"), text) if legal else None
    dob_claim = reply.get("date_of_birth") if isinstance(reply.get("date_of_birth"), dict) else None
    dob = accept_dob(dob_claim.get("quote"), dob_claim, text, checked) if dob_claim else None
    country_claim = reply.get("document_country") if isinstance(reply.get("document_country"), dict) else None
    country = accept_doc_country(country_claim.get("quote"), country_claim.get("country"), text) if country_claim else None
    book.notes[note["note_id"]] = {
        "note_id": note["note_id"],
        "lived": [],
        "legal_name": {"value": name, "quote": legal.get("quote")} if name else None,
        "date_of_birth": {"value": _date_record(dob), "quote": dob_claim.get("quote")} if dob else None,
        "document_country": {"country": country, "quote": country_claim.get("quote")} if country else None,
    }
    return name, dob, country


def _age_record(quote, claimed, text, allow_bare):
    pairs = accept_age_pairs(quote, claimed if isinstance(claimed, dict) else {}, text, allow_bare)
    records = []
    for newer, older in pairs:
        as_of = claimed if isinstance(claimed, dict) else {}
        nested = as_of.get("as_of") if isinstance(as_of.get("as_of"), dict) else as_of
        records.append({
            "quote": quote,
            "age": as_of.get("age") if isinstance(as_of.get("age"), int) else None,
            "as_of": {
                "year": nested.get("year") if isinstance(nested.get("year"), int) else newer,
                "month": nested.get("month") if isinstance(nested.get("month"), int) else None,
                "day": nested.get("day") if isinstance(nested.get("day"), int) else None,
            },
            "birth_years": [older, newer],
        })
    return records


def _store_hit(book, item, reply):
    hit = item["hit"]
    current = dict(book.hits.get(hit["hit_id"]) or {
        "hit_id": hit["hit_id"],
        "death_checked": False,
        "age_checked": False,
        "death": None,
        "profile_ages": [],
    })
    reply = reply or {}
    if item["ask_death"]:
        current["death_checked"] = True
        claim = reply.get("death") if isinstance(reply.get("death"), dict) else None
        parsed = accept_death(claim.get("quote"), claim, hit.get("profile_notes") or "") if claim else None
        current["death"] = {**_date_record(parsed), "quote": claim.get("quote")} if parsed else None
    if item["ask_age"]:
        current["age_checked"] = True
        claim = reply.get("age") if isinstance(reply.get("age"), dict) else None
        current["profile_ages"] = _age_record(claim.get("quote"), claim, first_paragraph(hit.get("profile_notes")), False) if claim else []
    book.hits[hit["hit_id"]] = current
    return current["death"], current["profile_ages"] if item["ask_age"] else []


def _store_search(book, result, reply):
    claim = (reply or {}).get("age") if isinstance((reply or {}).get("age"), dict) else None
    ages = _age_record(claim.get("quote"), claim, result.get("snippet") or "", True) if claim else []
    book.search[result["result_id"]] = {"result_id": result["result_id"], "ages_checked": True, "ages": ages}
    return ages


def _ask(settings, instruction, payload, label):
    try:
        return complete(settings, instruction, payload)
    except Exception as exc:
        print(f"{label} failed ({exc.__class__.__name__}); those items will be asked again next run")
        return None


LIVED_INSTRUCTION = (
    "You read bank notes. List each country the note ties to the customer. "
    "Do not require a particular verb: the note may say lived, worked, stayed, or some other word. "
    "The quote must be an exact contiguous copy from the note. "
    'Reply with JSON only: {"notes": [{"id": "NOTE-XXXXXX", "lived": [{"quote": "...", "country": "France"}]}]}. '
    "Use an empty lived list when the note names no country for the customer."
)
IDENTITY_INSTRUCTION = (
    "Each note is a passed identity check. Extract the legal name, date of birth, and document country "
    "only when the note states them. The quote must be an exact contiguous copy from the note. "
    "Use null when a fact is absent. Do not guess. "
    'Reply with JSON only: {"notes": [{"id": "NOTE-XXXXXX", "legal_name": {"quote": "...", "value": "..."}, '
    '"date_of_birth": {"quote": "...", "year": 1970, "month": 1, "day": 2}, '
    '"document_country": {"quote": "...", "country": "France"}}]}. Any of those three values may be null.'
)
HIT_INSTRUCTION = (
    "Each item is a watchlist profile. Extract only what it states about the listed person. "
    "death is the date they died, or null if it does not say they died. "
    "age is their age and the date that age was true, or null. Do not treat an event year, a fine, "
    "a sentence length, or a case number as an age. The quote must be an exact contiguous copy and must contain the numbers. "
    'Reply with JSON only: {"hits": [{"id": "H-XXXXXX", "death": {"quote": "...", "year": 2014, "month": 3, "day": 2}, '
    '"age": {"quote": "...", "age": 45, "year": 2019, "month": 3, "day": null}}]}. '
    "Use null for a question this item did not ask."
)
SEARCH_INSTRUCTION = (
    "Each item is a web snippet about a listed person. If it states that person's age and the date the age was true, "
    "return it. Otherwise age is null. Do not treat an event year, a fine, or a case number as an age. "
    "The quote must be an exact contiguous copy and must contain the numbers. "
    'Reply with JSON only: {"results": [{"id": "SR-XXXXXX", "age": {"quote": "...", "age": 45, "year": 2019, "month": 3, "day": null}}]}.'
)


def _hit_index(rows):
    note_hits = defaultdict(set)
    search_hits = defaultdict(set)
    for row in rows:
        hit_id = row["hit_id"]
        for person in row["people"]:
            for note in person["notes"]:
                note_hits[note["note_id"]].add(hit_id)
        for item in row["search_results"]:
            search_hits[item["result_id"]].add(hit_id)
    return note_hits, search_hits


def _cover(items, kind, hit_ids):
    covered = set()
    for item in items:
        for hit_id in hit_ids(item):
            covered.add((hit_id, kind))
    return covered


def _grouped(open_gaps):
    found = defaultdict(set)
    for hit_id, kind in open_gaps:
        found[hit_id].add(kind)
    return found


def _fill_gaps(rows, book, facts_path, limit, open_gaps):
    settings = api_settings()
    lived, identity, hits, search = select_gaps(rows, book)
    note_hits, search_hits = _hit_index(rows)
    open_gaps.update(_cover(lived, "lived", lambda note: note_hits[note["note_id"]]))
    open_gaps.update(_cover(identity, "identity", lambda note: note_hits[note["note_id"]]))
    open_gaps.update(_cover(
        [item for item in hits if item["ask_age"]],
        "age",
        lambda item: {item["hit"]["hit_id"]},
    ))
    open_gaps.update(_cover(search, "age", lambda item: search_hits[item["result_id"]]))
    pending = len(lived) + len(identity) + len(hits) + len(search)
    if not pending:
        print("model facts: nothing left to ask")
        return
    if not settings:
        print(f"model facts: {pending} gaps, no API key, left for review")
        return
    print(f"model facts: asking {pending} gaps with {settings['model']}")
    deadline = time.monotonic() + MODEL_BUDGET_SECONDS
    asked = False

    def save():
        book.checkpoint(facts_path)

    def run_batches(items, size, kind, hit_ids, label, ask):
        nonlocal asked
        start = 0
        step = size
        if limit:
            items = items[:limit]
        while start < len(items):
            batch = items[start:start + step]
            if time.monotonic() >= deadline:
                print("model facts: stopped after 20 minutes; remaining clears stay in review")
                return
            start += step
            print(f"  {label} {len(batch)}", flush=True)
            reply = ask(batch)
            if reply is None:
                continue
            open_gaps.difference_update(_cover(batch, kind, hit_ids))
            asked = True
            save()

    def ask_lived(batch):
        payload = [{"id": note["note_id"], "text": _clip(note.get("text"), 4000)} for note in batch]
        reply = _ask(settings, LIVED_INSTRUCTION, payload, "lived notes")
        if reply is None:
            return None
        found = _by_id(reply.get("notes"))
        for note in batch:
            stored = _store_lived(book, note, found.get(note["note_id"]))
            if stored:
                print(f"  {note['note_id']} lived {', '.join(item['country'] for item in stored)}")
        return reply

    def ask_identity(batch):
        payload = [{"id": note["note_id"], "text": _clip(note.get("text"), 4000)} for note in batch]
        reply = _ask(settings, IDENTITY_INSTRUCTION, payload, "identity notes")
        if reply is None:
            return None
        found = _by_id(reply.get("notes"))
        for note in batch:
            name, dob, country = _store_identity(book, note, found.get(note["note_id"]))
            if name or dob or country:
                print(f"  {note['note_id']} name={name or '-'} dob={dob or '-'} country={country or '-'}")
        return reply

    def ask_hits(batch):
        payload = []
        for item in batch:
            hit = item["hit"]
            text = death_excerpt(hit.get("profile_notes")) if item["ask_death"] and not item["ask_age"] else _clip(first_paragraph(hit.get("profile_notes")), 1800)
            if item["ask_death"] and item["ask_age"]:
                text = _clip(first_paragraph(hit.get("profile_notes")) + "\n" + death_excerpt(hit.get("profile_notes")), 3500)
            payload.append({"id": hit["hit_id"], "ask_death": item["ask_death"], "ask_age": item["ask_age"], "text": text})
        reply = _ask(settings, HIT_INSTRUCTION, payload, "profiles")
        if reply is None:
            return None
        found = _by_id(reply.get("hits"))
        for item in batch:
            death, ages = _store_hit(book, item, found.get(item["hit"]["hit_id"]))
            if death or ages:
                print(f"  {item['hit']['hit_id']} death={death or '-'} ages={len(ages)}")
        return reply

    def ask_search(batch):
        payload = [{"id": item["result_id"], "text": _clip(item.get("snippet"), 1500)} for item in batch]
        reply = _ask(settings, SEARCH_INSTRUCTION, payload, "search snippets")
        if reply is None:
            return None
        found = _by_id(reply.get("results"))
        for item in batch:
            ages = _store_search(book, item, found.get(item["result_id"]))
            if ages:
                print(f"  {item['result_id']} ages={len(ages)}")
        return reply

    note_of = lambda note: note_hits[note["note_id"]]
    run_batches(lived, 8, "lived", note_of, "lived", ask_lived)
    if time.monotonic() < deadline:
        run_batches(identity, 6, "identity", note_of, "identity", ask_identity)
    if time.monotonic() < deadline:
        run_batches(
            [item for item in hits if item["ask_age"]],
            4,
            "age",
            lambda item: {item["hit"]["hit_id"]},
            "profiles",
            ask_hits,
        )
    if time.monotonic() < deadline:
        run_batches(search, 6, "age", lambda item: search_hits[item["result_id"]], "search", ask_search)
    # Death questions do not hold up a clear. Ask them, but do not track them as open gaps.
    if time.monotonic() < deadline:
        death_only = [item for item in hits if item["ask_death"] and not item["ask_age"]]
        run_batches(death_only, 4, "death", lambda item: set(), "profiles", ask_hits)
    if asked:
        book.merge_into(facts_path)
        print(f"model facts merged into {facts_path}")


def fill_gaps(rows, book, facts_path, limit=0):
    """Ask the model. Return {hit_id: {kind}} for gaps that were not answered.

    A clear that depended on one of those gaps is left for review by the caller.
    """
    open_gaps = set()
    try:
        _fill_gaps(rows, book, facts_path, limit, open_gaps)
    except Exception as exc:
        print(f"model facts failed ({exc.__class__.__name__}); clears that needed a model fact stay in review")
    return _grouped(open_gaps)


def main():
    parser = argparse.ArgumentParser(description="Fill missed facts with Grok and store them in the facts file.")
    parser.add_argument("data", help="Folder containing the screening JSONL files")
    parser.add_argument("facts", help="Facts JSON file to update")
    parser.add_argument("--limit", type=int, default=0, help="Ask at most this many items of each kind")
    args = parser.parse_args()
    search = CustomerSearch(args.data)
    sources = SourceIndex(args.data)
    rows = gather(search, sources)
    book = FactBook.load(args.facts)
    fill_gaps(rows, book, args.facts, args.limit)


if __name__ == "__main__":
    main()

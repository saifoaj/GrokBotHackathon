"""Model facts stored beside the pattern facts, accepted only when the quote is in the source.

The decision reads these records and checks the quote again. A country, age, name,
or date that is not grounded in that quote is ignored.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from decide_hits import (
    ALIASES,
    birth_years_from_ages,
    caps_name,
    countries_in_text,
    dob_from_note,
    document_country,
    first_paragraph,
)
from find_customers import MONTHS, PLACEHOLDERS, fold, name_parts

KNOWN_COUNTRIES = set(ALIASES.values())
DEATH_WORD = re.compile(r"\bdied\b|\bdeath\b|\bdeceased\b|passed away|\bkilled\b", re.I)
GROUP_DEATH = re.compile(r"\b(?:people|persons|victims|individuals|residents|civilians|patients)\b", re.I)
YEAR_IN_TEXT = re.compile(r"\b(?:19|20)\d{2}\b")


def _squash(text):
    text = str(text or "")
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", text).strip()


def quote_in(text, quote):
    quote = _squash(quote)
    if len(quote) < 12 or len(quote.split()) < 3:
        return False
    return quote in _squash(text)


def number_in(text, number):
    if not isinstance(number, int):
        return False
    return re.search(rf"(?<!\d){number}(?!\d)", str(text)) is not None


def month_in(text, month):
    if month is None:
        return True
    if not isinstance(month, int) or not 1 <= month <= 12:
        return False
    folded = str(text).casefold()
    if any(name in folded for name, value in MONTHS.items() if value == month):
        return True
    return number_in(text, month)


def _known_country(value, quote):
    if not value:
        return None
    from decide_hits import canon_country
    key = canon_country(value)
    token = _squash(value).casefold()
    if key in KNOWN_COUNTRIES and token and token in _squash(quote).casefold():
        return key
    return None


def countries_from_quote(quote, claimed, text):
    if not quote_in(text, quote):
        return []
    found = []
    for key in countries_in_text(quote):
        if key in KNOWN_COUNTRIES and key not in found:
            found.append(key)
    claimed_key = _known_country(claimed, quote)
    if claimed_key and claimed_key not in found:
        found.append(claimed_key)
    return found


def accept_name(quote, claimed, text):
    if not quote_in(text, quote):
        return None
    parsed = caps_name(quote)
    candidate = parsed or _squash(claimed or "")
    parts = name_parts(candidate, set())
    if len(parts) < 2:
        return None
    tokens = set(fold(quote).split())
    if any(part not in tokens for part in parts):
        return None
    if not any(token not in parts and token not in PLACEHOLDERS for token in tokens):
        return None
    return candidate


def _date_parts(value):
    if not isinstance(value, dict):
        return None
    year = value.get("year")
    month = value.get("month")
    day = value.get("day")
    if not isinstance(year, int):
        return None
    if month is not None and not isinstance(month, int):
        return None
    if day is not None and not isinstance(day, int):
        return None
    return year, month, day


def accept_dob(quote, claimed, text, checked_on):
    if not quote_in(text, quote) or not re.search(r"\bdob\b|born|birth", quote or "", re.I):
        return None
    parsed = dob_from_note(quote, checked_on)
    if parsed:
        return parsed
    parts = _date_parts(claimed)
    if not parts:
        return None
    year, month, day = parts
    if not number_in(quote, year) or not month_in(quote, month):
        return None
    if day is not None and not number_in(quote, day):
        return None
    if month is not None and not 1 <= month <= 12:
        return None
    if day is not None and not 1 <= day <= 31:
        return None
    return year, month, day


def accept_doc_country(quote, claimed, text):
    if not quote_in(text, quote):
        return None
    parsed = document_country(quote)
    if parsed in KNOWN_COUNTRIES:
        return parsed
    return _known_country(claimed, quote)


def accept_age_pairs(quote, claimed, text, allow_bare):
    if not quote_in(text, quote) or len(_squash(quote)) > 500:
        return []
    pairs = birth_years_from_ages(quote, allow_bare)
    if pairs:
        return pairs
    if re.search(r"regarding\s+the\b.{0,40}\binvestigation\b", quote or "", re.I):
        return []
    if not isinstance(claimed, dict):
        return []
    age = claimed.get("age")
    as_of = claimed.get("as_of") if isinstance(claimed.get("as_of"), dict) else claimed
    year = as_of.get("year") if isinstance(as_of, dict) else None
    if not isinstance(age, int) or not isinstance(year, int):
        return []
    if not 1 <= age <= 110 or not number_in(quote, age) or not number_in(quote, year):
        return []
    month = as_of.get("month") if isinstance(as_of, dict) else None
    day = as_of.get("day") if isinstance(as_of, dict) else None
    if not month_in(quote, month):
        return []
    if day is not None and not number_in(quote, day):
        return []
    older = year - age - 1
    if older < 1850:
        return []
    return [(year - age, older)]


def accept_death(quote, claimed, text):
    if not quote_in(text, quote) or not DEATH_WORD.search(quote or "") or GROUP_DEATH.search(quote or ""):
        return None
    from decide_hits import _month_dates
    dates = []
    for start, _end, year, month, day in _month_dates(quote):
        if day is None:
            continue
        dates.append((start, year, month, day))
    if dates:
        at = DEATH_WORD.search(quote).start()
        _start, year, month, day = min(dates, key=lambda item: abs(item[0] - at))
        return year, month, day
    years = [int(match.group(0)) for match in YEAR_IN_TEXT.finditer(quote)]
    claimed_year = claimed.get("year") if isinstance(claimed, dict) else None
    if len(years) == 1:
        year = years[0]
    elif isinstance(claimed_year, int) and claimed_year in years:
        year = claimed_year
    else:
        return None
    if not 1850 <= year <= 2026:
        return None
    return year, None, None


def _date_record(parts):
    if not parts:
        return None
    year, month, day = parts
    return {"year": year, "month": month, "day": day}


class FactBook:
    """Accepted model facts, keyed by note, search result, and hit."""

    def __init__(self):
        self.notes = {}
        self.search = {}
        self.hits = {}

    def _read(self, path):
        if not path or not Path(path).exists():
            return
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        for row in document.get("model_notes") or []:
            if row.get("note_id"):
                self.notes[row["note_id"]] = row
        for row in document.get("model_search_results") or []:
            if row.get("result_id"):
                self.search[row["result_id"]] = row
        for row in document.get("model_hits") or []:
            if row.get("hit_id"):
                self.hits[row["hit_id"]] = row

    @classmethod
    def load(cls, path):
        book = cls()
        if not path:
            return book
        path = Path(path)
        book._read(path)
        book._read(path.parent / "model_facts.json")
        return book

    def payload(self):
        return {
            "model_notes": [self.notes[key] for key in sorted(self.notes)],
            "model_search_results": [self.search[key] for key in sorted(self.search)],
            "model_hits": [self.hits[key] for key in sorted(self.hits)],
        }

    def checkpoint(self, facts_path):
        path = Path(facts_path).parent / "model_facts.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def merge_into(self, facts_path):
        facts_path = Path(facts_path)
        if facts_path.exists():
            document = json.loads(facts_path.read_text(encoding="utf-8"))
        else:
            document = {"notes": [], "search_results": []}
        document.update(self.payload())
        temporary = facts_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(facts_path)

    def lived(self, note):
        row = self.notes.get(note.get("note_id"))
        if not row:
            return []
        text = note.get("text") or ""
        found = []
        for item in row.get("lived") or []:
            for key in countries_from_quote(item.get("quote"), item.get("country"), text):
                if key not in found:
                    found.append(key)
        return found

    def identity(self, note):
        row = self.notes.get(note.get("note_id"))
        if not row:
            return {}
        text = note.get("text") or ""
        from decide_hits import parse_iso
        checked = parse_iso(note.get("created_at"))
        found = {}
        name = row.get("legal_name") or {}
        accepted = accept_name(name.get("quote"), name.get("value"), text) if name else None
        if accepted:
            found["name"] = accepted
        dob = row.get("date_of_birth") or {}
        accepted = accept_dob(dob.get("quote"), dob.get("value"), text, checked) if dob else None
        if accepted:
            found["dob"] = accepted
        country = row.get("document_country") or {}
        accepted = accept_doc_country(country.get("quote"), country.get("country"), text) if country else None
        if accepted:
            found["country"] = accepted
        return found

    def profile_age_pairs(self, hit):
        row = self.hits.get(hit.get("hit_id"))
        if not row:
            return []
        text = first_paragraph(hit.get("profile_notes"))
        pairs = []
        for item in row.get("profile_ages") or []:
            for pair in accept_age_pairs(item.get("quote"), item, text, False):
                if pair not in pairs:
                    pairs.append(pair)
        return pairs

    def search_age_pairs(self, result):
        row = self.search.get(result.get("result_id"))
        if not row:
            return []
        text = result.get("snippet") or ""
        pairs = []
        for item in row.get("ages") or []:
            for pair in accept_age_pairs(item.get("quote"), item, text, True):
                if pair not in pairs:
                    pairs.append(pair)
        return pairs

    def death(self, hit):
        row = self.hits.get(hit.get("hit_id"))
        item = (row or {}).get("death")
        if not item:
            return None
        return accept_death(item.get("quote"), item, hit.get("profile_notes") or "")

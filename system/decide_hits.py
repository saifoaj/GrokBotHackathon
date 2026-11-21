"""Decide each screening hit from the written rules, without a language model.

A hit is cleared on the first hard contradiction: entity type, death before
screening, a dated report the customer could not have made, every birth date
contradicted, a verified legal name that matches nothing, or countries with no
overlap. A country difference does not clear a hit whose name and date of
birth already agree; that hit is left for review. A person is confirmed only
when the name and date of birth agree. A company is confirmed only when the
name and country agree.

Dates of birth stated as an age, and countries stated as places a customer
lived, are read with fixed patterns. Wording outside those patterns is left
unknown unless facts.json has a model fact whose quote is in the source.
The model never chooses the decision. A miss stays in review.

Usage:
    python system/decide_hits.py <data folder> <output.jsonl>
"""

from __future__ import annotations

import argparse
import calendar
import json
import re
from datetime import date
from pathlib import Path

from find_customers import COUNTRIES, MONTHS, country_key, fold, parse_dob
from gather_sources import NOT_COUNTRIES, CustomerSearch, SourceIndex, gather, hit_countries

DATED_REPORT = re.compile(
    r"According to [^;\n]+;\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),\s+(\d{4})\s*:"
)
AGE_PATTERNS = (
    re.compile(r"\baged\s+(\d{1,3})\b", re.I),
    re.compile(r"\bage\s+of\s+(\d{1,3})\b", re.I),
    re.compile(r"\b(\d{1,3})-year-old\b", re.I),
    re.compile(r"\b(\d{1,3})\s+years?\s+old\b", re.I),
    re.compile(r"\bwas\s+(\d{1,3})\b(?!\s*(?:years?|months?|days?))", re.I),
    re.compile(r"\bAge:\s*(\d{1,3})\b"),
)
BARE_AGE = (
    re.compile(r",\s*(\d{1,2})\s*,"),
    re.compile(r"(?<!\d)(\d{1,2}),\s+[A-ZÀ-ÖØ-Þ]"),
)
NOTE_FAIL = re.compile(
    r"not approved/verified|not approved|not verified|unverif|reject|expired|unsuccessful|"
    r"could not|failed to|declin|pending|awaiting|unable|denied|inconclusive|\bfail",
    re.I,
)
NOTE_PASS = re.compile(
    r"verif|validat|approv|confirm|cleared|passed|\bpass\b|vetted|successful|check complete|completed|matches",
    re.I,
)
NAME_SKIP = {"dob", "id", "na", "xxx", "usa", "uk", "gb", "eu"}
MONTH_WORD = "|".join(sorted(MONTHS, key=len, reverse=True))


def build_aliases():
    """Prose forms only. A code shorter than four letters stays out of sentences."""
    alias = {}
    for form, canonical in COUNTRIES.items():
        if form in NOT_COUNTRIES or canonical in NOT_COUNTRIES:
            continue
        if len(form) < 4 and " " not in form:
            continue
        alias.setdefault(form, canonical)
    return alias


ALIASES = build_aliases()
ALIAS_FORMS = sorted(ALIASES, key=len, reverse=True)


def canon_country(value):
    """One country key, or None. Codes, demonyms and full names collapse."""
    if value is None:
        return None
    text = re.sub(r"\[[^\]]*\]", " ", str(value)).strip()
    if not text:
        return None
    pieces = re.split(r"[;|]", text)
    for piece in pieces:
        parts = [part.strip() for part in piece.split(",") if part.strip()]
        for part in reversed(parts or [piece]):
            if any(char.isdigit() for char in part):
                continue
            key = country_key(part)
            if key and key not in NOT_COUNTRIES:
                return key
    return None


def countries_in_text(text):
    """Country keys mentioned as words. Short codes are not matched."""
    if not text:
        return []
    padded = f" {fold(text)} "
    taken = [False] * len(padded)
    found = []
    seen = set()
    for form in ALIAS_FORMS:
        needle = f" {form} "
        start = 0
        while True:
            index = padded.find(needle, start)
            if index < 0:
                break
            span = range(index, index + len(needle))
            if not any(taken[pos] for pos in span):
                for pos in span:
                    taken[pos] = True
                canonical = ALIASES[form]
                if canonical not in seen:
                    seen.add(canonical)
                    found.append(canonical)
            start = index + len(form) + 1
    return found


def document_country(text):
    """The country a verified note attaches to the identity document."""
    if not text:
        return None
    folded = f" {fold(text)} "
    hits = []
    taken = [False] * len(folded)
    for form in ALIAS_FORMS:
        needle = f" {form} "
        start = 0
        while True:
            index = folded.find(needle, start)
            if index < 0:
                break
            span = range(index, index + len(needle))
            if not any(taken[pos] for pos in span):
                for pos in span:
                    taken[pos] = True
                window = folded[max(0, index - 48): index + len(needle) + 24]
                near_document = bool(re.search(
                    r"\b(from|issued|passport|licence|license|identity|document|per|using|via)\b",
                    window,
                ))
                hits.append((near_document, ALIASES[form]))
            start = index + len(form) + 1
    preferred = [canonical for near, canonical in hits if near]
    if preferred:
        return preferred[0]
    unique = []
    for _, canonical in hits:
        if canonical not in unique:
            unique.append(canonical)
    if len(unique) == 1:
        return unique[0]
    return None


def lived_countries(text):
    """Any country in a non-KYC note. The caller skips KYC notes. No cue word is required."""
    if not text:
        return []
    return countries_in_text(text)


def parse_partial(value):
    parsed = parse_dob(value)
    if parsed:
        return parsed
    if value is None:
        return None
    match = re.fullmatch(r"(\d{4})-(\d{2})", str(value).strip())
    if not match:
        return None
    month = int(match.group(2))
    if 1 <= month <= 12:
        return int(match.group(1)), month, None
    return None


def _month_dates(text):
    """Dates that name a month. Positions let an age sit next to its date."""
    found = []
    occupied = [False] * (len(text) + 1)
    patterns = (
        (re.compile(rf"\b({MONTH_WORD})\s+(\d{{1,2}}),\s*(\d{{4}})\b", re.I), "mdy"),
        (re.compile(rf"\b(\d{{1,2}})\s+({MONTH_WORD})\s+(\d{{4}})\b", re.I), "dmy"),
        (re.compile(rf"\b({MONTH_WORD})\s+(\d{{4}})\b", re.I), "my"),
        (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "iso"),
        (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), "slash"),
    )
    for pattern, kind in patterns:
        for match in pattern.finditer(text):
            if any(occupied[match.start():match.end()]):
                continue
            if kind == "mdy":
                month = MONTHS[match.group(1).lower()]
                day, year = int(match.group(2)), int(match.group(3))
            elif kind == "dmy":
                day = int(match.group(1))
                month = MONTHS[match.group(2).lower()]
                year = int(match.group(3))
            elif kind == "my":
                month = MONTHS[match.group(1).lower()]
                year, day = int(match.group(2)), None
            elif kind == "iso":
                year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            else:
                day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            if month is not None and not 1 <= month <= 12:
                continue
            if day is not None and not 1 <= day <= 31:
                continue
            for pos in range(match.start(), match.end()):
                occupied[pos] = True
            found.append((match.start(), match.end(), year, month, day))
    return found


def full_dates(text):
    dates = []
    for start, end, year, month, day in _month_dates(text):
        if day is None:
            continue
        dates.append((start, end, (year, month, day)))
    return dates


def dob_from_note(text, checked_on):
    dates = full_dates(text)
    if not dates:
        return None
    labeled = []
    for start, end, parts in dates:
        window = text[max(0, start - 24): end + 12].casefold()
        if re.search(r"\bdob\b|born|birth", window):
            labeled.append((start, end, parts))
    pool = labeled or dates
    kept = [item for item in pool if item[2] != checked_on]
    if not kept:
        kept = labeled
    if not kept:
        return None
    if len(kept) == 1:
        return kept[0][2]
    anchor = None
    for word in ("dob", "born", "birth"):
        at = text.casefold().find(word)
        if at >= 0:
            anchor = at
            break
    if anchor is None:
        return kept[0][2]
    kept.sort(key=lambda item: abs(item[0] - anchor))
    return kept[0][2]


def _flush_caps(groups, current):
    if len(current) >= 2:
        groups.append(" ".join(current))
    current.clear()


def caps_name(text):
    """All-caps words are the legal name. Accented capitals count as letters."""
    groups = []
    current = []
    token = []

    def flush_token():
        if not token:
            return
        raw = "".join(token)
        token.clear()
        if raw.endswith("'s") or raw.endswith("’s"):
            raw = raw[:-2]
        letters = "".join(char for char in raw if char.isalpha())
        if len(letters) >= 2 and letters.isupper() and letters.casefold() not in NAME_SKIP:
            current.append(raw)
        else:
            _flush_caps(groups, current)

    for char in text or "":
        if char.isalpha() or char in "'’":
            token.append(char)
        else:
            flush_token()
    flush_token()
    _flush_caps(groups, current)
    if not groups:
        return None
    groups.sort(key=lambda group: (len(group.split()), len(group)), reverse=True)
    return groups[0]


def note_verified(text):
    if not text or NOTE_FAIL.search(text):
        return False
    return bool(NOTE_PASS.search(text))


def parse_iso(value):
    if not value:
        return None
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value))
    if not match:
        return None
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return year, month, day


def as_date(parts):
    if not parts or None in parts:
        return None
    year, month, day = parts
    last = calendar.monthrange(year, month)[1]
    if day > last:
        return None
    return date(year, month, day)


def fmt_date(parts):
    if not parts:
        return "unknown"
    year, month, day = parts
    if month and day:
        return f"{year:04d}-{month:02d}-{day:02d}"
    if month:
        return f"{year:04d}-{month:02d}"
    return str(year)


def fmt_countries(keys):
    names = sorted(" ".join(word.capitalize() for word in key.split()) for key in keys)
    if not names:
        return "no country"
    if len(names) == 1:
        return names[0]
    return ", ".join(names)


def hit_name_list(hit):
    names = []
    if hit.get("name"):
        names.append(hit["name"])
    names.extend(alias for alias in (hit.get("aliases") or []) if alias)
    built = " ".join(part for part in (hit.get("first_name"), hit.get("middle_name"), hit.get("last_name")) if part)
    if built:
        names.append(built)
    if hit.get("best_matching_name"):
        names.append(hit["best_matching_name"])
    unique = []
    for name in names:
        if name not in unique:
            unique.append(name)
    return unique


def split_marks(value):
    """A hyphen is a word break. Müller-Kägi is Müller and Kägi, not one glued token."""
    if not value:
        return value
    return re.sub(r"[-‐‑‒–—―]", " ", str(value))


def names_agree(matcher, left, right, entity_type):
    left_parts = matcher.parts(split_marks(left), entity_type)
    right_parts = matcher.parts(split_marks(right), entity_type)
    if not left_parts or not right_parts:
        return False
    if entity_type != "Company" and len(left_parts) < 2:
        return False
    return matcher._agree(left_parts, right_parts)


def agreed_name(matcher, candidates, hit_names, entity_type):
    for candidate in candidates:
        if any(names_agree(matcher, candidate, hit_name, entity_type) for hit_name in hit_names):
            return candidate
    return None


def first_paragraph(text):
    if not text:
        return ""
    return re.split(r"\n\s*\n", str(text).strip(), maxsplit=1)[0]


def _nearest_date(dates, start, end):
    best = None
    best_gap = None
    for pos, finish, year, month, day in dates:
        gap = 0 if finish >= start and pos <= end else min(abs(pos - end), abs(finish - start))
        if gap > 240:
            continue
        if best_gap is None or gap < best_gap:
            best_gap = gap
            best = (year, month, day, pos)
    return best


def birth_years_from_ages(text, allow_bare):
    """Two birth years for each age stated next to a date. A bare number counts only in search snippets."""
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
    years = []
    seen = set()
    for start, end, age in spans:
        found = _nearest_date(dates, start, end)
        if not found:
            continue
        year, month, day, pos = found
        if day == age and abs(pos - start) < 8:
            continue
        before = text[max(0, pos - 20):pos]
        after = text[pos:pos + 48]
        if re.search(r"regarding\s+the\s*$", before, re.I) and re.search(r"\binvestigation\b", after, re.I):
            continue
        pair = (year - age, year - age - 1)
        if pair in seen or pair[0] < 1850 or pair[1] < 1850:
            continue
        seen.add(pair)
        years.append(pair)
    return years


def dated_reports(text):
    found = []
    for match in DATED_REPORT.finditer(text or ""):
        month = MONTHS[match.group(1).lower()]
        day, year = int(match.group(2)), int(match.group(3))
        if 1 <= day <= 31 and 1 <= month <= 12:
            parsed = as_date((year, month, day))
            if parsed:
                found.append(parsed)
    found.sort()
    return found


def date_relation(customer, hit):
    """agree, contradict, or open. A one-year gap is open: it neither clears nor confirms."""
    customer_year, customer_month, customer_day = customer
    hit_year, hit_month, hit_day = hit
    if customer_year is None or hit_year is None:
        return "open"
    if abs(customer_year - hit_year) > 1:
        return "contradict"
    if customer_year != hit_year:
        return "open"
    if customer_month and hit_month and customer_month != hit_month:
        return "contradict"
    if customer_month and hit_month and customer_day and hit_day and customer_day != hit_day:
        return "contradict"
    return "agree"


def dob_summary(customer_dobs, hit_dobs):
    if not customer_dobs or not hit_dobs:
        return "unknown"
    per_customer = []
    for customer in customer_dobs:
        relations = [date_relation(customer, hit) for hit in hit_dobs]
        if relations and all(item == "contradict" for item in relations):
            per_customer.append("contradict")
        elif "agree" in relations and "contradict" not in relations:
            per_customer.append("agree")
        else:
            per_customer.append("unknown")
    if per_customer and all(item == "contradict" for item in per_customer):
        return "contradict"
    if per_customer and all(item == "agree" for item in per_customer):
        return "agree"
    return "unknown"


def oldest_age(dob, on):
    """Age if the person was born as early as the stated date allows."""
    year, month, day = dob
    month = month or 1
    day = day or 1
    last = calendar.monthrange(year, month)[1]
    day = min(day, last)
    years = on.year - year
    if (on.month, on.day) < (month, day):
        years -= 1
    return years


def incorporated_after(dob, report):
    """True only when every date the incorporation field allows is after the report."""
    year, month, day = dob
    if month is None:
        return year > report.year
    if year > report.year:
        return True
    if year < report.year:
        return False
    if month > report.month:
        return True
    if month < report.month:
        return False
    if day is None:
        return False
    incorporated = as_date((year, month, day))
    return incorporated is not None and incorporated > report


def death_before(dob, screened):
    year, month, day = dob
    if month is None:
        return date(year, 12, 31) < screened
    last = calendar.monthrange(year, month)[1]
    if day is None:
        day = last
    died = as_date((year, month, min(day, last)))
    return died is not None and died < screened


PROFILE_DOB_LINE = re.compile(r"(?im)^\s*(?:DOB|Date of birth)\s*:\s*(.+?)\s*$")


def parse_profile_dob(value):
    """A profile DOB line. Slash dates are ambiguous, so an unclear order keeps only the year."""
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if not match:
        return parse_dob(text)
    first, second, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if first > 12 and second <= 12:
        month, day = second, first
    elif second > 12 and first <= 12:
        month, day = first, second
    elif first <= 12 and second <= 12:
        return year, None, None
    else:
        return None
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return year, month, day


def profile_dob_lines(text):
    found = []
    for match in PROFILE_DOB_LINE.finditer(text or ""):
        parsed = parse_profile_dob(match.group(1))
        if parsed and parsed not in found:
            found.append(parsed)
    return found


def hit_birth_dates(hit, search_results, book=None):
    """Birth dates from dobs, a DOB line in the profile, an age, then a listing search result."""
    company = (hit.get("entity_type") or "") == "Company"
    listed = []
    for raw in hit.get("dobs") or []:
        parsed = parse_partial(raw)
        if parsed and parsed not in listed:
            listed.append(parsed)
    if not company:
        for parsed in profile_dob_lines(hit.get("profile_notes")):
            if parsed not in listed:
                listed.append(parsed)
    ages = [] if company else birth_years_from_ages(first_paragraph(hit.get("profile_notes")), False)
    if not listed and not ages and not company and book is not None:
        ages = book.profile_age_pairs(hit)
    age_dates = []
    for newer, older in ages:
        age_dates.append((newer, None, None))
        age_dates.append((older, None, None))
    if listed or age_dates:
        return listed + age_dates, [], "profile" if age_dates and not listed else ("both" if age_dates else "listed")
    if company:
        return [], [], "listed"
    found = []
    evidence = []
    for item in search_results:
        if not item.get("about_listing"):
            continue
        pairs = birth_years_from_ages(item.get("snippet"), True)
        if not pairs and book is not None:
            pairs = book.search_age_pairs(item)
        if not pairs:
            continue
        evidence.append(item["result_id"])
        for newer, older in pairs:
            found.append((newer, None, None))
            found.append((older, None, None))
    return found, evidence, "search"


def describe_hit_dates(hit, kind):
    if kind == "search":
        return None
    listed = []
    for raw in hit.get("dobs") or []:
        parsed = parse_partial(raw)
        if parsed:
            listed.append(fmt_date(parsed))
    if (hit.get("entity_type") or "") != "Company":
        for parsed in profile_dob_lines(hit.get("profile_notes")):
            label = fmt_date(parsed)
            if label not in listed:
                listed.append(label)
    ages = []
    if (hit.get("entity_type") or "") != "Company":
        ages = birth_years_from_ages(first_paragraph(hit.get("profile_notes")), False)
    parts = []
    if listed:
        parts.append(", ".join(listed))
    for newer, older in ages:
        parts.append(f"{older} or {newer}")
    return ", ".join(parts)


def identity_facts(person, book=None):
    """Latest approved check or successful KYC note, per fact. Declared facts stay separate."""
    events = []
    for check in person["kyc_checks"]:
        if check.get("result") != "approved":
            continue
        events.append({
            "on": check.get("checked_at") or "",
            "id": check.get("check_id"),
            "kind": "check",
            "name": check.get("verified_name"),
            "dob": parse_partial(check.get("verified_dob")),
            "country": canon_country(check.get("document_country")),
        })
    for note in person["notes"]:
        if note.get("channel") != "kyc" or not note_verified(note.get("text")):
            continue
        checked = parse_iso(note.get("created_at"))
        extra = book.identity(note) if book is not None else {}
        events.append({
            "on": note.get("created_at") or "",
            "id": note.get("note_id"),
            "kind": "note",
            "name": caps_name(note.get("text")) or extra.get("name"),
            "dob": dob_from_note(note.get("text"), checked) or extra.get("dob"),
            "country": document_country(note.get("text")) or extra.get("country"),
        })
    events.sort(key=lambda event: (event["on"], 0 if event["kind"] == "check" else 1, event["id"] or ""), reverse=True)
    verified = {"name": None, "name_id": None, "dob": None, "dob_id": None, "country": None, "country_id": None}
    for event in events:
        if verified["name"] is None and event["name"]:
            verified["name"] = event["name"]
            verified["name_id"] = event["id"]
        if verified["dob"] is None and event["dob"]:
            verified["dob"] = event["dob"]
            verified["dob_id"] = event["id"]
        if verified["country"] is None and event["country"]:
            verified["country"] = event["country"]
            verified["country_id"] = event["id"]
    declared_names = []
    declared_dobs = []
    record_countries = []
    for record in person["customers"]:
        for field in ("full_name", "preferred_name", "login_name"):
            if record.get(field) and record[field] not in declared_names:
                declared_names.append(record[field])
        parsed = parse_partial(record.get("dob"))
        if parsed and parsed not in declared_dobs:
            declared_dobs.append(parsed)
        for field in ("residence_country", "nationality"):
            key = canon_country(record.get(field))
            if key and key not in record_countries:
                record_countries.append(key)
    lived = []
    lived_ids = []
    for note in person["notes"]:
        if note.get("channel") == "kyc":
            continue
        countries = list(lived_countries(note.get("text")))
        if book is not None:
            for key in book.lived(note):
                if key not in countries:
                    countries.append(key)
        added = [key for key in countries if key not in record_countries and key != verified["country"]]
        if added:
            lived_ids.append(note["note_id"])
        for key in countries:
            if key not in lived:
                lived.append(key)
    countries = []
    for key in record_countries + ([verified["country"]] if verified["country"] else []) + lived:
        if key and key not in countries:
            countries.append(key)
    return {
        "verified": verified,
        "declared_names": declared_names,
        "declared_dobs": declared_dobs,
        "countries": countries,
        "record_countries": record_countries,
        "lived_note_ids": lived_ids,
        "entity_type": next((record.get("entity_type") for record in person["customers"] if record.get("entity_type")), None),
        "record_ids": list(person["customer_record_ids"]),
    }


def name_status(matcher, facts, hit, entity_type):
    hit_names = hit_name_list(hit)
    verified = facts["verified"]["name"]
    if verified:
        parts = matcher.parts(split_marks(verified), entity_type)
        if entity_type != "Company" and len(parts) < 2:
            return "weak", verified
        if agreed_name(matcher, [verified], hit_names, entity_type):
            return "agree", verified
        return "mismatch", verified
    strong = []
    for name in facts["declared_names"]:
        parts = matcher.parts(split_marks(name), entity_type)
        if entity_type == "Company" or len(parts) >= 2:
            strong.append(name)
    if not strong:
        return "weak", None
    matched = agreed_name(matcher, strong, hit_names, entity_type)
    if matched:
        return "agree", matched
    return "differ", strong[0]


def location_status(customer_keys, hit_keys):
    if not customer_keys or not hit_keys:
        return "unknown"
    if set(customer_keys) & set(hit_keys):
        return "overlap"
    return "differ"


def trusted_dobs(facts):
    if facts["verified"]["dob"]:
        return [facts["verified"]["dob"]], facts["verified"]["dob_id"]
    if facts["declared_dobs"]:
        return list(facts["declared_dobs"]), None
    return [], None


def evidence_ids(*groups):
    found = []
    seen = set()
    for group in groups:
        for item in group:
            if item and item not in seen:
                seen.add(item)
                found.append(item)
    return found


def decide_person(matcher, person, hit, screened_on, search_results, book=None):
    facts = identity_facts(person, book)
    entity = facts["entity_type"] or hit.get("entity_type")
    hit_type = hit.get("entity_type")
    record_ids = facts["record_ids"]
    customer_types = {record.get("entity_type") for record in person["customers"] if record.get("entity_type")}
    hit_keys = []
    seen_hit = set()
    for key in hit_countries(hit):
        mapped = ALIASES.get(key, key)
        if mapped in NOT_COUNTRIES or mapped in seen_hit:
            continue
        seen_hit.add(mapped)
        hit_keys.append(mapped)
    hit_dobs, search_ids, dob_kind = hit_birth_dates(hit, search_results, book)
    customer_dobs, dob_source = trusted_dobs(facts)
    summary = dob_summary(customer_dobs, hit_dobs)
    status, used_name = name_status(matcher, facts, hit, entity if entity in ("Individual", "Company") else "Individual")
    countries = location_status(facts["countries"], hit_keys)
    name_agrees = status == "agree"
    dob_agrees = summary == "agree"
    verified = facts["verified"]

    def dob_label():
        if not customer_dobs:
            return "unknown"
        if len(customer_dobs) == 1:
            return fmt_date(customer_dobs[0])
        return ", ".join(fmt_date(item) for item in customer_dobs)

    def hit_dob_label():
        if dob_kind == "search":
            years = sorted({item[0] for item in hit_dobs})
            if len(years) == 2 and years[1] == years[0] + 1:
                return f"{years[0]} or {years[1]}"
            return ", ".join(str(year) for year in years)
        text = describe_hit_dates(hit, dob_kind)
        if text:
            return text
        years = sorted({item[0] for item in hit_dobs})
        if len(years) == 2 and years[1] == years[0] + 1:
            return f"{years[0]} or {years[1]}"
        return ", ".join(str(year) for year in years)

    def where_hit_dob():
        if dob_kind == "search":
            return "search results put its birth year at"
        if dob_kind == "profile":
            return "profile notes put its birth year at"
        if dob_kind == "both":
            return "listed dates and profile notes put its birth year at"
        return "listed date of birth is"

    if hit_type and customer_types and all(item.casefold() != hit_type.casefold() for item in customer_types):
        return _outcome(
            "DIFFERENT_ENTITY",
            "ENTITY_TYPE_MISMATCH",
            f"The customer is {next(iter(customer_types))} and the hit is {hit_type}.",
            evidence_ids(record_ids),
        )

    death = parse_partial(hit.get("deceased_date"))
    if death is None and book is not None:
        death = book.death(hit)
    if death and screened_on and death_before(death, screened_on):
        return _outcome(
            "DIFFERENT_ENTITY",
            "DECEASED",
            f"The hit gives a date of death {fmt_date(death)}, which is before the customer was screened on {screened_on.isoformat()}.",
            evidence_ids(record_ids),
        )

    reports = dated_reports(hit.get("profile_notes"))
    earliest = reports[0] if reports else None
    if earliest and customer_dobs:
        if entity == "Company" or hit_type == "Company":
            if all(incorporated_after(item, earliest) for item in customer_dobs):
                return _outcome(
                    "DIFFERENT_ENTITY",
                    "TEMPORAL_IMPOSSIBILITY",
                    f"The company was incorporated on {dob_label()} ({dob_source or 'the customer record'}), after the hit's earliest dated report on {earliest.isoformat()}.",
                    evidence_ids([dob_source], record_ids),
                )
        else:
            ages = [oldest_age(item, earliest) for item in customer_dobs]
            if ages and max(ages) < 16:
                return _outcome(
                    "DIFFERENT_ENTITY",
                    "TEMPORAL_IMPOSSIBILITY",
                    f"The customer was born in {customer_dobs[0][0]} ({dob_source or 'the customer record'}), so they were at most {max(ages)} when the hit's profile reports activity on {earliest.isoformat()}.",
                    evidence_ids([dob_source], record_ids),
                )

    if summary == "contradict":
        return _outcome(
            "DIFFERENT_ENTITY",
            "DOB_MISMATCH",
            f"The customer's date of birth is {dob_label()} ({dob_source or 'the customer record'}) but the hit's {where_hit_dob()} {hit_dob_label()}.",
            evidence_ids([dob_source] if dob_source else record_ids, search_ids if dob_kind == "search" else []),
        )

    if status == "mismatch":
        return _outcome(
            "DIFFERENT_ENTITY",
            "NAME_MISMATCH_VERIFIED",
            f"The customer's verified name is {used_name} ({verified['name_id']}), which does not match the hit's name {hit.get('name')}.",
            evidence_ids([verified["name_id"]]),
        )

    if countries == "differ" and not (name_agrees and dob_agrees):
        lived = facts["lived_note_ids"] if facts["lived_note_ids"] else []
        doc = [verified["country_id"]] if verified["country"] and verified["country"] not in facts["record_countries"] else []
        return _outcome(
            "DIFFERENT_ENTITY",
            "LOCATION_MISMATCH",
            f"The customer is linked only to {fmt_countries(facts['countries'])} but the hit is linked only to {fmt_countries(hit_keys)}.",
            evidence_ids(record_ids, doc, lived),
        )

    if entity != "Company" and hit_type != "Company" and name_agrees and dob_agrees and countries != "differ":
        code = "NAME_DOB_LOCATION_MATCH" if countries == "overlap" else "NAME_DOB_MATCH"
        sentences = [
            f"The customer's date of birth {dob_label()} ({dob_source or 'the customer record'}) agrees with the hit's {hit_dob_label()}.",
        ]
        if countries == "overlap":
            shared = sorted(set(facts["countries"]) & set(hit_keys))
            sentences.append(f"Both the customer and the hit are linked to {fmt_countries(shared)}.")
        name_source = verified["name_id"] if verified["name"] else None
        sentences.append(f"The customer's name agrees with the hit's name {hit.get('name')}.")
        return _outcome(
            "SAME_ENTITY",
            code,
            " ".join(sentences),
            evidence_ids(record_ids, [dob_source, name_source], search_ids if dob_kind == "search" else []),
        )

    if (entity == "Company" or hit_type == "Company") and name_agrees and countries == "overlap" and summary != "contradict":
        shared = sorted(set(facts["countries"]) & set(hit_keys))
        name_source = verified["name_id"] if verified["name"] else None
        doc = [verified["country_id"]] if verified["country_id"] else []
        return _outcome(
            "SAME_ENTITY",
            "NAME_LOCATION_MATCH",
            f"Both the customer and the hit are linked to {fmt_countries(shared)}. The customer's name agrees with the hit's name {hit.get('name')}.",
            evidence_ids(record_ids, doc, [name_source]),
        )

    if name_agrees and dob_agrees and countries == "differ":
        return _outcome(
            "REVIEW",
            "INSUFFICIENT_DATA",
            f"The customer's name agrees with the hit's name {hit.get('name')} and the date of birth {dob_label()} agrees with {hit_dob_label()}, but the customer is linked only to {fmt_countries(facts['countries'])} and the hit only to {fmt_countries(hit_keys)}. A country difference does not clear a hit whose name and date of birth agree.",
            evidence_ids(record_ids, [dob_source, verified["name_id"]]),
        )

    sentences = []
    if countries == "overlap":
        shared = sorted(set(facts["countries"]) & set(hit_keys))
        sentences.append(f"Both the customer and the hit are linked to {fmt_countries(shared)}.")
    if name_agrees:
        sentences.append(f"The customer's name agrees with the hit's name {hit.get('name')}.")
    elif status == "mismatch":
        sentences.append(f"The verified name {used_name} does not agree with the hit.")
    if summary == "unknown":
        sentences.append("The date of birth cannot be compared, so the hit can be neither confirmed nor cleared.")
    elif not name_agrees:
        sentences.append("The name does not agree, and there is no verified legal name that contradicts the hit.")
    if not sentences:
        sentences.append("Nothing in the customer's records contradicts the hit, and there is not enough agreement to confirm it.")
    return _outcome(
        "REVIEW",
        "INSUFFICIENT_DATA",
        " ".join(sentences),
        evidence_ids(record_ids, [verified["name_id"], verified["dob_id"], dob_source]),
    )


def _outcome(decision, reason_code, reason, evidence):
    return {
        "decision": decision,
        "reason_code": reason_code,
        "reason": reason,
        "evidence": evidence,
    }


CLEAR_ORDER = {
    "ENTITY_TYPE_MISMATCH": 0,
    "DECEASED": 1,
    "TEMPORAL_IMPOSSIBILITY": 2,
    "DOB_MISMATCH": 3,
    "NAME_MISMATCH_VERIFIED": 4,
    "LOCATION_MISMATCH": 5,
    "NAME_DOB_LOCATION_MATCH": 6,
    "NAME_DOB_MATCH": 7,
    "NAME_LOCATION_MATCH": 8,
    "INSUFFICIENT_DATA": 9,
}


def decide_row(matcher, row, book=None):
    screened_on = None
    parsed = parse_iso(row.get("screened_at"))
    if parsed:
        screened_on = as_date(parsed)
    if not row["people"]:
        return {
            "hit_id": row["hit_id"],
            "alert_id": row["alert_id"],
            "decision": "REVIEW",
            "reason_code": "INSUFFICIENT_DATA",
            "customer_record_id": None,
            "reason": "No customer record fits the alert, so the hit cannot be compared.",
            "evidence": [],
        }
    outcomes = [
        decide_person(matcher, person, row["hit"], screened_on, row["search_results"], book)
        for person in row["people"]
    ]
    decisions = {item["decision"] for item in outcomes}
    if len(row["people"]) == 1:
        outcome = outcomes[0]
        record_id = row["customer_record_id"]
    elif len(decisions) == 1:
        outcome = sorted(outcomes, key=lambda item: CLEAR_ORDER.get(item["reason_code"], 99))[0]
        record_id = "AMBIGUOUS"
        evidence = evidence_ids(*(item["evidence"] for item in outcomes))
        outcome = dict(outcome)
        outcome["evidence"] = evidence
    else:
        outcome = {
            "decision": "REVIEW",
            "reason_code": "AMBIGUOUS_CUSTOMER_RECORD",
            "reason": "More than one person fits the alert, and they do not lead to the same decision.",
            "evidence": evidence_ids(*(item["evidence"] for item in outcomes)),
        }
        record_id = "AMBIGUOUS"
    return {
        "hit_id": row["hit_id"],
        "alert_id": row["alert_id"],
        "decision": outcome["decision"],
        "reason_code": outcome["reason_code"],
        "customer_record_id": record_id,
        "reason": outcome["reason"],
        "evidence": outcome["evidence"],
    }


# A missing model answer can hide the fact that would have blocked the clear.
CLEARS_BLOCKED_BY = {
    "LOCATION_MISMATCH": {"lived", "identity", "age"},
    "DOB_MISMATCH": {"identity", "age"},
    "TEMPORAL_IMPOSSIBILITY": {"identity"},
    "NAME_MISMATCH_VERIFIED": {"identity"},
}


def hold_unanswered(row, kinds):
    if row["decision"] != "DIFFERENT_ENTITY":
        return row
    blocking = CLEARS_BLOCKED_BY.get(row["reason_code"], ())
    if not set(blocking) & set(kinds):
        return row
    held = dict(row)
    held["decision"] = "REVIEW"
    held["reason_code"] = "INSUFFICIENT_DATA"
    held["reason"] = (
        "A fact that could reverse this clear was not returned by the model, so the hit stays in review."
    )
    return held


def decide(data_dir, facts_path=None):
    search = CustomerSearch(data_dir)
    sources = SourceIndex(data_dir)
    rows = gather(search, sources)
    from facts_store import FactBook
    from fill_model_facts import fill_gaps
    book = FactBook.load(facts_path)
    unanswered = {}
    if facts_path:
        unanswered = fill_gaps(rows, book, facts_path) or {}
    decided = []
    for row in rows:
        outcome = decide_row(search.names, row, book)
        decided.append(hold_unanswered(outcome, unanswered.get(outcome["hit_id"], ())))
    return decided


def main():
    parser = argparse.ArgumentParser(description="Decide whether each screening hit is the customer.")
    parser.add_argument("data", help="Folder containing the screening JSONL files")
    parser.add_argument("output", help="JSON Lines file to write, one hit per line")
    args = parser.parse_args()
    output = Path(args.output)
    rows = decide(args.data, output.parent / "facts.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    counts = {}
    for row in rows:
        counts[row["decision"]] = counts.get(row["decision"], 0) + 1
    print(f"wrote {len(rows)} hits to {output}")
    print(
        f"DIFFERENT_ENTITY {counts.get('DIFFERENT_ENTITY', 0)}, "
        f"SAME_ENTITY {counts.get('SAME_ENTITY', 0)}, "
        f"REVIEW {counts.get('REVIEW', 0)}"
    )


if __name__ == "__main__":
    main()

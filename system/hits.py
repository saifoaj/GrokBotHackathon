"""Watchlist side: gather each hit's facts wherever they are (POLICY.md principle 5)."""

import re

import countries
from customers import is_person
from norm import as_list, find_dates, parse_date, strip_accents

LISTING = {
    "sanctions": "sanctioned individual",
    "pep": "politically exposed person",
    "enforcement": "subject of enforcement action",
    "adverse media": "named in adverse media",
    "soe": "state-owned enterprise official",
}
OTHER_LISTING = "named on a watchlist"

_FULL_MONTH = r"(January|February|March|April|May|June|July|August|September|October|November|December)"
REPORT = re.compile(r"^\s*According to .+?;\s*" + _FULL_MONTH + r" (\d{1,2}), (\d{4})\s*:", re.M)

AGE_PATTERNS = [
    re.compile(r"\baged\s+(\d{1,3})\b", re.I),
    re.compile(r"\b(\d{1,3})[\s-]+years?[\s-]+old\b", re.I),
    re.compile(r"\b(\d{1,3})\s+years?\s+of\s+age\b", re.I),
    re.compile(r"\bage\s+of\s+(\d{1,3})\b", re.I),
    re.compile(r"\bat\s+age\s+(\d{1,3})\b", re.I),
    re.compile(r"\bage\s*[:=]?\s*(\d{1,3})\b", re.I),
    re.compile(r"\b(\d{1,3})-year-old\b", re.I),
    re.compile(r"\b(?:was|is|turned|reached)\s+(\d{1,3})\s*(?=[.,;:)]|$|when\b|at\b|in\b|by\b)", re.I),
]
_MONTHS = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December"]


def listing_phrase(hit):
    category = (hit.get("primary_category") or (as_list(hit.get("categories")) or [""])[0] or "").strip().lower()
    return LISTING.get(category, OTHER_LISTING)


def hit_names(hit):
    names = []
    for n in [hit.get("name"), hit.get("best_matching_name")] + as_list(hit.get("aliases")):
        if n and n not in names:
            names.append(n)
    parts = [hit.get(k) for k in ("first_name", "middle_name", "last_name") if hit.get(k)]
    if parts:
        joined = " ".join(parts)
        if joined not in names:
            names.append(joined)
    return names


def hit_countries(hit):
    found = set()
    for field in ("countries", "nationality", "citizenship"):
        for value in as_list(hit.get(field)):
            c = countries.canon(value)
            if c:
                found.add(c)
    for value in as_list(hit.get("addresses")):
        value = re.sub(r"\[[^\]]*\]", "", value).strip().rstrip(",")
        c = countries.canon(value.split(",")[-1])
        if c:
            found.add(c)
    pob = hit.get("place_of_birth")
    if pob:
        for value in as_list(pob):
            c = countries.canon(value.split(",")[-1])
            if c:
                found.add(c)
    return sorted(found)


def age_years(text):
    """From an age at a date ("aged 45 in March 2019"), the two possible birth years."""
    if not text:
        return None
    age = None
    for pattern in AGE_PATTERNS:
        m = pattern.search(text)
        if m:
            age = int(m.group(1))
            break
    if age is None or not 1 <= age <= 120:
        return None
    dates = [d for s, e, d in find_dates(text)]
    if not dates:
        dates = [d for s, e, d in find_dates(text, years=True)]
    if not dates:
        return None
    year = dates[0][0]
    return [year - age - 1, year - age]


def snippet_age_years(snippet, name_words):
    """Age and date in a search snippet; the age is the lone small number left once dates are removed."""
    years = age_years(snippet)
    if years:
        return years
    dates = find_dates(snippet, years=True)
    if not dates:
        return None
    rest = snippet
    for s, e, d in sorted(dates, reverse=True):
        rest = rest[:s] + " " + rest[e:]
    numbers = sorted({int(n) for n in re.findall(r"(?<![\d.,])(\d{1,3})(?![\d%])", rest)})
    numbers = [n for n in numbers if 10 <= n <= 110]
    if len(numbers) != 1:
        return None
    full = [d for s, e, d in dates if d[1]]
    year = (full or [d for s, e, d in dates])[0][0]
    return [year - numbers[0] - 1, year - numbers[0]]


def search_keys(name):
    out = []
    base = strip_accents(name or "").lower()
    for text in (base, (name or "").lower()):
        words = re.sub(r"[^\w\s]", " ", text).split()
        for ws in (words, [w for w in words if len(w) > 1]):
            key = " ".join(sorted(ws))
            if key and key not in out:
                out.append(key)
    return out


def picture(hit, data):
    pic = {"id": hit["hit_id"], "is_person": is_person(hit.get("entity_type")), "names": hit_names(hit),
           "name": hit.get("name") or "", "countries": hit_countries(hit), "dates": [], "dob_origin": None,
           "sr": []}
    # 1. dobs field
    for value in as_list(hit.get("dobs")):
        d = parse_date(value)
        if d:
            pic["dates"].append(d)
    if pic["dates"]:
        pic["dob_origin"] = "listed"
    notes = hit.get("profile_notes") or ""
    if not pic["dates"] and pic["is_person"] and notes:
        first = re.split(r"\n\s*\n", notes.strip(), maxsplit=1)[0]
        years = None if first.lstrip().startswith("According to") else age_years(first)
        if years:
            pic["dates"] = [(y, None, None) for y in years]
            pic["dob_origin"] = "profile"
    if not pic["dates"] and pic["is_person"] and pic["countries"]:
        phrase = listing_phrase(hit)
        tied = set(pic["countries"])
        for key in search_keys(hit.get("name")):
            for s in data.search_by_query.get(key, []):
                snippet = s.get("snippet") or ""
                if phrase not in snippet.lower():
                    continue
                stripped = snippet
                for w in key.split():
                    stripped = re.sub(r"(?i)\b%s\b" % re.escape(w), " ", strip_accents(stripped))
                if not tied & set(countries.find_in_text(stripped)):
                    continue
                years = snippet_age_years(snippet, key.split())
                if years:
                    for y in years:
                        if (y, None, None) not in pic["dates"]:
                            pic["dates"].append((y, None, None))
                    if s["result_id"] not in pic["sr"]:
                        pic["sr"].append(s["result_id"])
            if pic["sr"]:
                break
        if pic["dates"]:
            pic["dob_origin"] = "search"
    pic["dates"] = sorted(set(pic["dates"]), key=lambda d: (d[0], d[1] or 0, d[2] or 0))
    # death
    pic["death"] = parse_date(hit.get("deceased_date"))
    # dated reports: exactly "According to ...; Month d, yyyy:" lines
    reports = []
    for m in REPORT.finditer(notes):
        try:
            reports.append((int(m.group(3)), _MONTHS.index(m.group(1)), int(m.group(2))))
        except ValueError:
            continue
    pic["report"] = min(reports) if reports else None
    return pic

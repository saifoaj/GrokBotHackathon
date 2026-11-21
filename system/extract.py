"""Facts written in free text: birth dates, ages at a date, dated reports, deaths.

Every rule is anchored on wording that states the fact (a birth keyword, an
age phrase, the dated-report line form). Text that does not match stays
unknown: it never clears a hit.
"""

import re

import dates

# ---------------------------------------------------------------- birth dates

DOB_KEY = re.compile(r"(?<![a-z])(date of birth|birth[\s\-]?date|d\.?\s?o\.?\s?b|born|birth\w*)(?![a-z])", re.I)
AFTER_WINDOW = 30    # characters between a birth keyword and the date after it
BEFORE_WINDOW = 20   # ... or the date before it ("lists 06/01/1998 as her DOB")
_BARE_YEAR = re.compile(r"[^0-9]{0,30}?(?<!\d)(\d{4})(?![\d/\-]|\.\d)")


def _before(value, limit):
    return limit is None or dates.sort_key(value) < dates.sort_key(limit)


def anchored_dob(text, order=None, not_after=None):
    """The date attached to a birth keyword (DOB, born, birth date...).

    Returns (status, date): ("anchored", date) when a keyword has a date
    right after it (within 30 characters) or right before it (within 20);
    the nearest wins, a date after the keyword wins a tie, then the earliest
    keyword. ("unattached", None) when keywords exist but no date is near
    them; ("no_keyword", None) when the text has no birth keyword.
    A birth date must be before not_after (the day the text was written).
    """
    if not isinstance(text, str):
        return ("no_keyword", None)
    keys = [(m.start(), m.end()) for m in DOB_KEY.finditer(text)]
    if not keys:
        return ("no_keyword", None)
    found = dates.find_dates(text, order)
    candidates = []
    for key_index, (key_start, key_end) in enumerate(keys):
        for start, end, value in found:
            if start >= key_end and start - key_end <= AFTER_WINDOW:
                candidates.append((start - key_end, 0, key_index, value))
            elif end <= key_start and key_start - end <= BEFORE_WINDOW:
                candidates.append((key_start - end, 1, key_index, value))
        bare = _BARE_YEAR.match(text, key_end)
        if bare and not any(s <= bare.start(1) < e for s, e, _v in found):
            value = dates.make(bare.group(1))
            if value is not None:
                candidates.append((bare.start(1) - key_end, 0, key_index, value))
    candidates = [c for c in candidates if _before(c[3], not_after)]
    if not candidates:
        return ("unattached", None)
    candidates.sort(key=lambda c: (c[0], c[1], c[2], dates.sort_key(c[3])))
    return ("anchored", candidates[0][3])


# ---------------------------------------------------------------- ages

_AGE_PATTERNS = [
    re.compile(r"(?<![a-z])aged?\s*:?\s*(\d{1,3})(?!\d)", re.I),                 # aged 30, age 31, Age: 38
    re.compile(r"(?<![a-z])age of\s+(\d{1,3})(?!\d)", re.I),                     # at the age of 59
    re.compile(r"(?<!\d)(\d{1,3})[\s\-]+years?[\s\-]+old(?![a-z])", re.I),       # 43-year-old, 45 years old
    re.compile(r"(?<!\d)(\d{1,3})\s+years?\s+of\s+age(?![a-z])", re.I),          # 37 years of age
    re.compile(r"(?<![a-z])age of [^.;:\n\d]{1,60}? (?:was|is|stood at|reached) (\d{1,3})(?!\d)", re.I),
    re.compile(r"[A-Z][\w'’\-\.]*,\s+(\d{1,3}),"),                             # Elena Johansson, 51,
    # "he was 46." / "she turned 52 when" : the number must end the clause.
    re.compile(r"(?<![a-z])(?:he|she|they|who)\s+(?:was|were|is|turned|had turned)\s+(\d{1,3})"
               r"(?=\s*(?:[.,;:!?)]|$|years?\s+old|at the time|when|and\b))", re.I),
]
# Any wording that looks like an age (for the coverage report).
AGE_WORDS = re.compile(r"(?<![a-z])(aged?|age of|years? old|year-old|years of age)(?![a-z])", re.I)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z])|\n")
_AS_OF = re.compile(r"(?<![a-z])as of\s+", re.I)
_YEAR_AFTER_PREP = re.compile(r"(?<![a-z])(?:in|during|by|since|throughout)\s+(\d{4})(?!\d)", re.I)


def _sentences(text):
    """(start, end) spans of sentences and lines."""
    spans = []
    start = 0
    for match in _SENTENCE_BREAK.finditer(text):
        spans.append((start, match.start()))
        start = match.end()
    spans.append((start, len(text)))
    return [(s, e) for s, e in spans if e > s]


def _dates_in(text, start, end, order):
    """Dates (incl. 'in 2019' bare years) inside text[start:end] with positions."""
    part = text[start:end]
    found = [(start + s, start + e, v) for s, e, v in dates.find_dates(part, order)]
    for match in _YEAR_AFTER_PREP.finditer(part):
        position = start + match.start(1)
        if not any(s <= position < e for s, e, _v in found):
            value = dates.make(match.group(1))
            if value is not None:
                found.append((position, position + 4, value))
    return sorted(found, key=lambda item: (item[0], item[1]))


# Loose mode (short texts about one person, such as search snippets).
_LONE_NUMBER = re.compile(r"(?<![\w$€£#./\-])(\d{1,3})(?![\w%/\-]|[.,]\d)")
_FOLLOW_OK = frozenset(["and", "when", "at", "year", "years", "yr", "yrs", "a", "an", "who", "was", "is",
                        "in", "the", "then", "as", "during", "on", "while", "for", "from", "with", "before",
                        "after", "since", "upon", "but", "she", "he", "they", "by", "throughout",
                        "named"])   # "a 47 named in adverse media": listing phrases start with "named"
_PRECEDE_BAD = frozenset(["no", "number", "section", "article", "page", "chapter", "rule", "act", "room",
                          "unit", "flat", "suite", "case", "file", "item", "top", "level", "grade", "class",
                          "route", "count", "counts", "of"])
_ANY_YEAR = re.compile(r"(?<![\w/\-.])(1[89]\d\d|20\d\d)(?![\w/\-]|[.,]\d)")


def _all_dates(text, order):
    """Every date in the text, including bare years, with positions."""
    found = [(s, e, v) for s, e, v in dates.find_dates(text, order)]
    for match in _ANY_YEAR.finditer(text):
        if not any(s <= match.start() < e for s, e, _v in found):
            value = dates.make(match.group(1))
            if value is not None:
                found.append((match.start(), match.end(), value))
    return sorted(found, key=lambda item: (item[0], item[1]))


def _nearest(found, position):
    if not found:
        return None
    ranked = sorted((min(abs(s - position), abs(e - position)), s, v) for s, e, v in found)
    return ranked[0][2]


def _lone_age(text, found_dates):
    """The single bare number that can only be an age, or None.

    A number 1-120 that is not part of a date, is not preceded by words like
    "No." or "Section", and is followed by punctuation or a word that can
    follow an age ("was 40 when", "at 22.", "38, Rasel Islam"). Exactly one
    such number must exist; otherwise the age stays unknown.
    """
    candidates = []
    for match in _LONE_NUMBER.finditer(text):
        position = match.start(1)
        if any(s <= position < e for s, e, _v in found_dates):
            continue
        age = int(match.group(1))
        if not 1 <= age <= 120:
            continue
        before = re.findall(r"[A-Za-z]+", text[max(0, position - 15):position])
        if before and before[-1].lower() in _PRECEDE_BAD:
            continue
        after = text[match.end(1):].lstrip()
        if after and after[0] not in ",.;:)!?":
            word = re.match(r"[A-Za-z]+", after)
            if word is None or word.group(0).lower() not in _FOLLOW_OK:
                continue
        candidates.append((age, position))
    if len(candidates) == 1:
        return candidates[0]
    return None


def ages_at_dates(text, order=None, loose=False):
    """Statements of an age at a date: list of (age, date, start).

    The date is the nearest one in the same sentence; for record-style
    lists ("as of May 15, 2019: ... Age: 38") the "as of" date of the text.
    Ages outside 1-120 are ignored. Sorted by position.

    loose=True is for short texts about one person (search snippets): the
    date may be anywhere in the text, and when no age phrase is found a
    single unambiguous bare number is taken as the age.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    if not loose:
        return _anchored_ages(text, order)
    everywhere = _all_dates(text, order)
    results = [(age, when if when is not None else _nearest(everywhere, position), position)
               for age, when, position in _anchored_ages(text, order, keep_undated=True)]
    results = [r for r in results if r[1] is not None]
    if results:
        return results
    lone = _lone_age(text, everywhere)
    if lone is not None:
        when = _nearest(everywhere, lone[1])
        if when is not None:
            return [(lone[0], when, lone[1])]
    return []


def _anchored_ages(text, order, keep_undated=False):
    spans = _sentences(text)
    as_of = None
    match = _AS_OF.search(text)
    if match:
        after = dates.find_dates(text[match.end():match.end() + 40], order)
        if after and after[0][0] == 0:
            as_of = after[0][2]
    results = []
    seen = set()
    for pattern in _AGE_PATTERNS:
        for found in pattern.finditer(text):
            age = int(found.group(1))
            position = found.start(1)
            if position in seen or not 1 <= age <= 120:
                continue
            sentence = [(s, e) for s, e in spans if s <= position < e]
            when = None
            if sentence:
                candidates = _dates_in(text, sentence[0][0], sentence[0][1], order)
                candidates = [(min(abs(s - position), abs(e - position)), s, v) for s, e, v in candidates]
                if candidates:
                    when = sorted(candidates)[0][2]
            if when is None:
                when = as_of
            if when is None and not keep_undated:
                continue
            seen.add(position)
            results.append((age, when, position))
    results.sort(key=lambda item: item[2])
    return results


def birth_years_from_age(age, when):
    """Someone aged A at a date in year Y was born in Y-A-1 or Y-A."""
    return (when[0] - age - 1, when[0] - age)


# ---------------------------------------------------------------- hit profiles

FULL_MONTHS = ("January|February|March|April|May|June|July|August|September|"
               "October|November|December")
_REPORT_LINE = re.compile(r"^\s*According to (.+?); (" + FULL_MONTHS + r") (\d{1,2}), (\d{4}):")
_MONTH_NUMBER = {name: index for index, name in enumerate(FULL_MONTHS.split("|"), 1)}


def dated_reports(profile_notes):
    """Dates of the dated reports in a profile: list of (date, line number).

    Exactly the English lines of the form "According to ...; Month d, yyyy: ...".
    """
    if not isinstance(profile_notes, str):
        return []
    reports = []
    for number, line in enumerate(profile_notes.splitlines(), 1):
        match = _REPORT_LINE.match(line)
        if match:
            value = dates.make(match.group(4), _MONTH_NUMBER[match.group(2)], match.group(3))
            if value is not None:
                reports.append((value, number))
    return reports


_DEATH_KEY = re.compile(r"(?<![a-z])(date of death|died|passed away)(?![a-z])", re.I)


def death_dates(text, order=None):
    """Dates attached to a death keyword (died on ..., date of death: ...).

    Only "died", "passed away" and "date of death": a bare "death" also
    appears in "sentenced to death", which is not a date of death.
    """
    if not isinstance(text, str):
        return []
    found = dates.find_dates(text, order)
    result = []
    for key in _DEATH_KEY.finditer(text):
        after = [(s - key.end(), v) for s, _e, v in found if s >= key.end() and s - key.end() <= AFTER_WINDOW]
        if after:
            result.append(sorted(after)[0][1])
    return result


def first_paragraph(text):
    if not isinstance(text, str):
        return ""
    parts = re.split(r"\n\s*\n", text.strip(), maxsplit=1)
    return parts[0] if parts else ""

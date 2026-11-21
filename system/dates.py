"""Partial dates: parse a whole field, or find dates inside free text.

A date is a tuple (year, month, day) where month and day may be None.
Unknown parts stay unknown: they never contradict anything.
Slash and dot dates are day-first (DD/MM/YYYY), the bank's convention,
unless the second number cannot be a month.
"""

import calendar
import re

MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
MON = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
       r"aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
ORD = r"(?:st|nd|rd|th)?"

MIN_YEAR = 1800
MAX_YEAR = 2100


def make(year, month=None, day=None):
    """Return a valid (year, month, day) tuple, or None."""
    try:
        year = int(year)
        month = int(month) if month is not None else None
        day = int(day) if day is not None else None
    except (TypeError, ValueError):
        return None
    if not MIN_YEAR <= year <= MAX_YEAR:
        return None
    if month is None:
        return (year, None, None) if day is None else None
    if not 1 <= month <= 12:
        return None
    if day is None:
        return (year, month, None)
    if not 1 <= day <= calendar.monthrange(year, month)[1]:
        return None
    return (year, month, day)


def _day_first(first, second, year):
    first, second = int(first), int(second)
    if second > 12 and first <= 12:
        return make(year, first, second)
    return make(year, second, first)


def _month(name):
    return MONTH_NAMES.get(name.lower().rstrip("."))


# Patterns used both for whole fields and for free text, most specific first.
_TEXT_PATTERNS = [
    (re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)"),
     lambda m: make(m.group(1), m.group(2), m.group(3))),
    (re.compile(r"(?<!\d)(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})(?!\d)"),
     lambda m: _day_first(m.group(1), m.group(2), m.group(3))),
    (re.compile(r"(?<![\w])(\d{1,2})" + ORD + r"(?:\s+of)?[\s\-]+" + MON + r"\.?,?[\s\-]+(\d{4})(?!\d)", re.I),
     lambda m: make(m.group(3), _month(m.group(2)), m.group(1))),
    (re.compile(r"(?<![\w])" + MON + r"\.?[\s\-]+(\d{1,2})" + ORD + r",?\s+(\d{4})(?!\d)", re.I),
     lambda m: make(m.group(3), _month(m.group(1)), m.group(2))),
    (re.compile(r"(?<![\w])" + MON + r"\.?,?\s+(?:of\s+)?(\d{4})(?!\d)", re.I),
     lambda m: make(m.group(2), _month(m.group(1)))),
]

# Extra forms accepted only when they are the whole field.
_FIELD_PATTERNS = [
    (re.compile(r"(\d{4})[-/.](\d{1,2})"), lambda m: make(m.group(1), m.group(2))),
    (re.compile(r"(\d{1,2})[-/.](\d{4})"), lambda m: make(m.group(2), m.group(1))),
    (re.compile(r"(\d{4})"), lambda m: make(m.group(1))),
]


def parse_date(value):
    """Parse a whole date field. Returns (year, month, day) or None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    for pattern, build in _TEXT_PATTERNS + _FIELD_PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            return build(match)
    # A field like "1985-09-12T00:00:00" or "12 Sep 1985 (approx)": use the
    # single date inside it if there is exactly one.
    found = find_dates(text)
    if len(found) == 1:
        return found[0][2]
    return None


def find_dates(text):
    """All dates written inside free text: list of (start, end, date).

    Overlapping matches keep the earliest, then longest. Sorted by position.
    """
    if not isinstance(text, str):
        return []
    matches = []
    for priority, (pattern, build) in enumerate(_TEXT_PATTERNS):
        for match in pattern.finditer(text):
            value = build(match)
            if value is not None:
                matches.append((match.start(), -(match.end() - match.start()), priority, match.end(), value))
    matches.sort()
    chosen = []
    last_end = -1
    for start, _neg_len, _priority, end, value in matches:
        if start >= last_end:
            chosen.append((start, end, value))
            last_end = end
    return chosen


def is_full(date):
    return date is not None and date[1] is not None and date[2] is not None


def agree_in_shared_parts(a, b):
    """True when every part both dates give is equal (unknown never conflicts)."""
    if a is None or b is None:
        return True
    if a[0] != b[0]:
        return False
    if a[1] is not None and b[1] is not None and a[1] != b[1]:
        return False
    if a[2] is not None and b[2] is not None and a[2] != b[2]:
        return False
    return True


def to_text(date):
    """ISO-style text of a partial date: 1985, 1985-09 or 1985-09-12."""
    if date is None:
        return "unknown"
    if date[1] is None:
        return "%04d" % date[0]
    if date[2] is None:
        return "%04d-%02d" % (date[0], date[1])
    return "%04d-%02d-%02d" % date


def sort_key(date):
    """Sort key placing unknown parts first."""
    if date is None:
        return (0, 0, 0)
    return (date[0], date[1] or 0, date[2] or 0)

"""Small normalisers: lists, dates, phones, text.

A date is a tuple (year, month, day); month and day may be None when the source
gives only part of it. Unknown parts stay unknown: they never cause a mismatch.
"""

import datetime
import re
import unicodedata

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
    "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"]
_MON = r"(?:January|February|March|April|May|June|July|August|September|October|November|December|" \
       r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\.?"


def as_list(value):
    """Accept a list, a '; ' joined string, or nothing; return a list of clean strings."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = str(value).split(";")
    out = []
    for item in items:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            out.append(text)
    return out


_SPECIAL = str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ß": "ss", "æ": "ae",
                          "Æ": "AE", "œ": "oe", "Œ": "OE", "ı": "i", "þ": "th", "Þ": "Th", "ð": "d", "Ð": "D",
                          "ħ": "h", "Ħ": "H", "ŧ": "t", "Ŧ": "T"})


def strip_accents(text):
    text = unicodedata.normalize("NFKD", text.translate(_SPECIAL))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def phone_key(phone):
    digits = re.sub(r"\D", "", phone or "")
    return digits if len(digits) >= 6 else None


def _valid(y, m, d):
    if not (1800 <= y <= 2100):
        return False
    if m is not None and not 1 <= m <= 12:
        return False
    if d is not None:
        if m is None:
            return False
        try:
            datetime.date(y, m, d)
        except ValueError:
            return False
    return True


_PATTERNS = [
    # ISO full, ISO year-month, ISO year
    (re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)"), lambda g: (int(g[0]), int(g[1]), int(g[2]))),
    # day-first slashes (all slash dates in the data are day first)
    (re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)"), lambda g: (int(g[2]), int(g[1]), int(g[0]))),
    (re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)"), lambda g: (int(g[2]), int(g[1]), int(g[0]))),
    # 27 June 1967 / 27th June 1967 / 27 June , 1967 (spaces before the comma are tolerated)
    (re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?\s+(" + _MON + r")\s*,?\s+(\d{4})(?!\d)", re.I),
     lambda g: (int(g[2]), MONTHS[g[1].lower().rstrip(".")], int(g[0]))),
    # June 27, 1967 / June 27 1967 / June 27 , 1967
    (re.compile(r"(" + _MON + r")\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s+(\d{4})(?!\d)", re.I),
     lambda g: (int(g[2]), MONTHS[g[0].lower().rstrip(".")], int(g[1]))),
    # June 1967 / June , 1967
    (re.compile(r"(" + _MON + r")\s*,?\s+(\d{4})(?!\d)", re.I),
     lambda g: (int(g[1]), MONTHS[g[0].lower().rstrip(".")], None)),
    (re.compile(r"(?<![\d-])(\d{4})-(\d{1,2})(?![\d-])"), lambda g: (int(g[0]), int(g[1]), None)),
]
_YEAR = re.compile(r"(?<![\d/.-])(\d{4})(?![\d/-])")


def find_dates(text, years=False):
    """Return [(start, end, (y, m, d))] for every date in free text, left to right.

    With years=True, bare four-digit years are returned too (as (y, None, None)).
    """
    if not text:
        return []
    found = []
    used = []

    def free(a, b):
        return all(b <= s or a >= e for s, e in used)

    for pattern, build in _PATTERNS:
        for match in pattern.finditer(text):
            if not free(*match.span()):
                continue
            try:
                value = build(match.groups())
            except (KeyError, ValueError):
                continue
            if _valid(*value):
                found.append((match.start(), match.end(), value))
                used.append(match.span())
    if years:
        for match in _YEAR.finditer(text):
            if free(*match.span()):
                y = int(match.group(1))
                if _valid(y, None, None):
                    found.append((match.start(), match.end(), (y, None, None)))
                    used.append(match.span())
    found.sort()
    return found


def parse_date(value):
    """Parse one date field (any of the formats in the data). Returns (y, m, d) or None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}", text):
        y = int(text)
        return (y, None, None) if _valid(y, None, None) else None
    short = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2})", text)
    if short:  # day-first with a two-digit year: 05/03/79 is 1979, 05/03/08 is 2008
        d, m, yy = (int(x) for x in short.groups())
        y = 2000 + yy if yy <= 26 else 1900 + yy
        return (y, m, d) if _valid(y, m, d) else None
    dates = find_dates(text)
    if len(dates) == 1:
        return dates[0][2]
    return None


def fmt_date(date):
    if not date:
        return "unknown"
    y, m, d = date
    if m is None:
        return "%04d" % y
    if d is None:
        return "%04d-%02d" % (y, m)
    return "%04d-%02d-%02d" % (y, m, d)


def earliest(date):
    """Earliest real day a partial date can mean."""
    y, m, d = date
    return datetime.date(y, m or 1, d or 1)


def latest(date):
    y, m, d = date
    if m is None:
        return datetime.date(y, 12, 31)
    if d is None:
        nxt = datetime.date(y + (m == 12), m % 12 + 1, 1)
        return nxt - datetime.timedelta(days=1)
    return datetime.date(y, m, d)


def add_years(day, years):
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


def before(a, b):
    """True only when partial date a is certainly before partial date b (coarsest shared precision)."""
    return latest(a) < earliest(b)

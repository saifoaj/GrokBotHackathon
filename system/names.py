"""Name handling: normalising text and comparing names (POLICY principle 8).

- Compare parts ignoring case, accents, punctuation and anything in brackets.
- Single letters are initials, not parts; each distinct part counts once.
- Placeholders (NA, N/A, TEST, UNKNOWN, XXX, NONE, all-digit tokens...) are
  not parts. For companies, the legal forms in name_variants.json are not parts.
- Two parts match when identical, listed together in name_variants.json,
  written together or apart (Abdulsalam = Abdul Salam: one part covering
  both), or within the edit limit: 1 edit for parts of 4-6 letters, 2 for
  longer, parts under 4 letters must be identical; the shorter part decides.
- Names agree when at least two parts match, or every part of the shorter
  name matches. Matching is one-to-one and the best pairing is used.
"""

import json
import os
import re
import unicodedata

_BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}")
_APOSTROPHES = re.compile(r"['’‘ʼ`´]")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")
# Letters that NFKD does not split into a base letter plus an accent.
_TRANSLITERATE = {
    ord("ß"): "ss", ord("ø"): "o", ord("ł"): "l", ord("đ"): "d",
    ord("ð"): "d", ord("þ"): "th", ord("æ"): "ae", ord("œ"): "oe",
    ord("ı"): "i", ord("ħ"): "h", ord("ŋ"): "n", ord("ŧ"): "t",
}

PLACEHOLDERS = frozenset([
    "na", "nil", "none", "null", "unknown", "unk", "test", "tbd", "tba",
    "noname", "fnu", "lnu", "nn", "xx", "xxx", "xxxx", "xxxxx", "dummy", "sample",
])

MAX_PARTS_FOR_EXACT = 12


def normalise(text):
    """NFKD, strip accents, casefold, transliterate the remaining special letters."""
    if not isinstance(text, str):
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold().translate(_TRANSLITERATE)


def simple_text(text):
    """Normalised text with punctuation turned into single spaces."""
    text = _APOSTROPHES.sub("", normalise(text))
    return " ".join(_NON_ALNUM.sub(" ", text).split())


def allowed_edits(shorter_length):
    """Principle 8: edits allowed between two parts, by the shorter part's length."""
    if shorter_length < 4:
        return 0
    if shorter_length <= 6:
        return 1
    return 2


def edit_distance(a, b, limit):
    """Levenshtein distance (add, remove or change one letter; a swap is two).

    Stops early and returns limit + 1 once the distance must exceed limit.
    """
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b)))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def is_placeholder(token):
    if token in PLACEHOLDERS:
        return True
    if token.isdigit():
        return True
    return len(token) > 1 and set(token) == {"x"}


def reorder_last_first(name):
    """'Velasco Morillo, Elvira' -> 'Elvira Velasco Morillo' (one comma only)."""
    if not isinstance(name, str):
        return name
    text = _BRACKETS.sub(" ", name)
    if text.count(",") != 1:
        return None
    last, first = [part.strip() for part in text.split(",")]
    if not last or not first:
        return None
    return first + " " + last


class NameRules(object):
    """Name variants and company legal forms from name_variants.json."""

    def __init__(self, groups=None, legal_forms=None):
        self.canonical = {}
        for group in sorted(sorted(simple_text(n) for n in g) for g in (groups or [])):
            group = [n for n in group if n]
            if not group:
                continue
            key = group[0]
            for name in group:
                # A form listed in two groups keeps the lowest key: deterministic.
                if name not in self.canonical or key < self.canonical[name]:
                    self.canonical[name] = key
        self.legal_forms = frozenset(simple_text(f) for f in (legal_forms or []) if simple_text(f))
        self._cache = {}

    @classmethod
    def load(cls, folder):
        path = os.path.join(folder, "name_variants.json")
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (IOError, OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        groups = [g for g in data.get("groups") or [] if isinstance(g, list)]
        groups = [[n for n in g if isinstance(n, str)] for g in groups]
        forms = [f for f in data.get("company_legal_forms") or [] if isinstance(f, str)]
        return cls(groups, forms)

    def parts(self, name, company=False):
        """Distinct real name parts, in order: no initials or placeholders."""
        if not isinstance(name, str):
            return []
        text = _BRACKETS.sub(" ", name)
        text = simple_text(text)
        result = []
        for token in text.split():
            if len(token) < 2 or is_placeholder(token):
                continue
            if company and token in self.legal_forms:
                continue
            if token not in result:
                result.append(token)
        return result

    def key(self, token):
        """Index key for a name part: its variant group, or itself."""
        return self.canonical.get(token, token)

    def parts_match(self, a, b):
        if a == b or self.key(a) == self.key(b):
            return True
        cache_key = (a, b) if a < b else (b, a)
        if cache_key in self._cache:
            return self._cache[cache_key]
        allowed = allowed_edits(min(len(a), len(b)))
        result = allowed > 0 and edit_distance(a, b, allowed) <= allowed
        self._cache[cache_key] = result
        return result

    def best_match(self, parts_a, parts_b):
        """Best one-to-one pairing of parts: (matches, covered in A, covered in B).

        A part may match two adjacent parts of the other name written
        together ("abdulsalam" ~ "abdul" + "salam"); that counts as one match
        covering both. Maximises matches, then parts covered; deterministic.
        """
        a, b = list(parts_a), list(parts_b)
        if len(a) > MAX_PARTS_FOR_EXACT or len(b) > MAX_PARTS_FOR_EXACT:
            return self._greedy(a, b)
        memo = {}

        def solve(i, used):
            if i >= len(a):
                return (0, 0, 0)
            state = (i, used)
            if state in memo:
                return memo[state]
            best = solve(i + 1, used)                       # leave a[i] unmatched
            for j in range(len(b)):
                if used & (1 << j):
                    continue
                if self.parts_match(a[i], b[j]):            # one part to one part
                    m, ca, cb = solve(i + 1, used | (1 << j))
                    best = max(best, (m + 1, ca + 1, cb + 1))
                if (j + 1 < len(b) and not used & (1 << (j + 1))
                        and self.parts_match(a[i], b[j] + b[j + 1])):   # a[i] = b[j] b[j+1]
                    m, ca, cb = solve(i + 1, used | (1 << j) | (1 << (j + 1)))
                    best = max(best, (m + 1, ca + 1, cb + 2))
                if i + 1 < len(a) and self.parts_match(a[i] + a[i + 1], b[j]):  # a[i] a[i+1] = b[j]
                    m, ca, cb = solve(i + 2, used | (1 << j))
                    best = max(best, (m + 1, ca + 2, cb + 1))
            memo[state] = best
            return best

        return solve(0, 0)

    def _greedy(self, a, b):
        used = set()
        count = 0
        for part in a:
            for index, other in enumerate(b):
                if index not in used and self.parts_match(part, other):
                    used.add(index)
                    count += 1
                    break
        return (count, count, count)

    def matched_count(self, parts_a, parts_b):
        return self.best_match(parts_a, parts_b)[0]

    def agree(self, parts_a, parts_b):
        """Names agree: at least two parts match, or every part of the shorter."""
        if not parts_a or not parts_b:
            return False
        matches, covered_a, covered_b = self.best_match(parts_a, parts_b)
        if matches >= 2:
            return True
        if len(parts_a) <= len(parts_b) and covered_a == len(parts_a) and matches > 0:
            return True
        if len(parts_b) <= len(parts_a) and covered_b == len(parts_b) and matches > 0:
            return True
        return False

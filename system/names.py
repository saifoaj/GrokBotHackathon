"""Name handling: normalising text and comparing names.

Parts match when they are identical, listed together in name_variants.json,
or differ by a small number of edits (principle 8: at most 1 edit for parts
of 4 to 6 letters, 2 for longer ones, the shorter part's length decides;
parts shorter than 4 letters must be identical). Screened names carry typos,
so customer resolution needs this. Still to come from principle 8: parts
written together or apart (Abdulsalam / Abdul Salam).
"""

import json
import os
import re
import unicodedata

_BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}")
_APOSTROPHES = re.compile(r"['’‘ʼ`´]")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")

PLACEHOLDERS = frozenset([
    "na", "nil", "none", "null", "unknown", "unk", "test", "tbd", "tba",
    "noname", "fnu", "lnu", "nn", "xx", "xxx", "xxxx", "xxxxx",
])


def normalise(text):
    """NFKD, strip accents, casefold."""
    if not isinstance(text, str):
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold()


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
    if any(ch.isdigit() for ch in token):
        return True
    return len(set(token)) == 1 and len(token) > 1 and token[0] == "x"


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
        allowed = allowed_edits(min(len(a), len(b)))
        return allowed > 0 and edit_distance(a, b, allowed) <= allowed

    def matched_count(self, parts_a, parts_b):
        """Parts of A matched to distinct parts of B (deterministic greedy)."""
        used = set()
        count = 0
        for a in sorted(parts_a):
            for index, b in enumerate(sorted(parts_b)):
                if index not in used and self.parts_match(a, b):
                    used.add(index)
                    count += 1
                    break
        return count

    def agree(self, parts_a, parts_b):
        """Names agree: at least two parts match, or every part of the shorter."""
        if not parts_a or not parts_b:
            return False
        count = self.matched_count(parts_a, parts_b)
        return count >= 2 or count == min(len(parts_a), len(parts_b))

"""The name rule (POLICY.md principle 8).

Parts: lower case, no accents, no punctuation, nothing in brackets; single
letters are initials, not parts; placeholders are not parts; for companies the
legal forms are not parts. Two parts match when identical, listed together in
one name_variants group, written together or apart, or within the edit limit.
Two names agree when at least two parts match, or every part of the shorter
name matches.
"""

import json
import os
import re

from norm import strip_accents

PLACEHOLDERS = {"na", "n/a", "test", "unknown", "xxx", "xx", "none", "null", "nil", "tbd", "tba", "dummy",
                "noname", "fnu", "lnu", "nn", "nan", "void", "sample", "anonymous", "aka", "unk"}


class NameRules:
    def __init__(self, data_folder):
        path = os.path.join(data_folder, "name_variants.json")
        groups, legal = [], []
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
            groups = raw.get("groups") or []
            legal = raw.get("legal_forms") or raw.get("company_legal_forms") or []
            if not legal:
                for key, value in sorted(raw.items()):
                    if key != "groups" and isinstance(value, list) and value and isinstance(value[0], str):
                        legal = value
        self.variant_of = {}
        for index, group in enumerate(groups):
            for name in group:
                self.variant_of.setdefault(self._clean(name), set()).add(index)
        self.legal_forms = {self._clean(x) for x in legal} | set()
        self._cache = {}

    @staticmethod
    def _clean(text):
        return re.sub(r"[^a-z0-9]", "", strip_accents(str(text)).lower())

    def parts(self, name, company=False):
        """Distinct real name parts, in order."""
        if not name:
            return []
        key = (name, company)
        if key in self._cache:
            return self._cache[key]
        text = strip_accents(str(name)).lower()
        text = re.sub(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}", " ", text)
        text = text.replace("'", "").replace("’", "").replace("`", "")
        text = re.sub(r"[^a-z0-9]+", " ", text)
        out = []
        for part in text.split():
            if len(part) < 2 or part.isdigit() or re.search(r"\d", part):
                continue
            if part in PLACEHOLDERS:
                continue
            if company and part in self.legal_forms:
                continue
            if part not in out:
                out.append(part)
        self._cache[key] = out
        return out

    def real_part_count(self, name, company=False):
        return len(self.parts(name, company))

    # ------------------------------------------------------------- matching

    def part_match(self, a, b):
        if a == b:
            return True
        ga, gb = self.variant_of.get(a), self.variant_of.get(b)
        if ga and gb and ga & gb:
            return True
        short = min(len(a), len(b))
        if short < 4:
            return False
        limit = 1 if short <= 6 else 2
        if abs(len(a) - len(b)) > limit:
            return False
        return edit_distance(a, b, limit) <= limit

    def _units(self, parts):
        """Single parts plus adjacent pairs written together (for Abdulsalam = Abdul Salam)."""
        units = [((i,), p) for i, p in enumerate(parts)]
        for i in range(len(parts) - 1):
            units.append(((i, i + 1), parts[i] + parts[i + 1]))
        return units

    def match_count(self, pa, pb):
        """Largest number of disjoint matches between two part lists.

        Returns (matches, covered_a, covered_b): a joined/split match counts as one
        match that covers two parts on one side.
        """
        ua, ub = self._units(pa), self._units(pb)
        edges = []
        for ia, ta in ua:
            for ib, tb in ub:
                if len(ia) == 2 and len(ib) == 2:
                    continue
                if (len(ia) == 2 or len(ib) == 2):
                    if ta == tb:
                        edges.append((ia, ib))
                elif self.part_match(ta, tb):
                    edges.append((ia, ib))
        best = (0, 0, 0)

        def search(k, used_a, used_b, count, cov_a, cov_b):
            nonlocal best
            if (count, cov_a + cov_b) > (best[0], best[1] + best[2]):
                best = (count, cov_a, cov_b)
            if k >= len(edges):
                return
            # upper bound prune
            if count + (len(edges) - k) <= best[0] and count + (len(edges) - k) < best[0] + 1:
                pass
            for j in range(k, len(edges)):
                ia, ib = edges[j]
                if used_a & set(ia) or used_b & set(ib):
                    continue
                search(j + 1, used_a | set(ia), used_b | set(ib), count + 1, cov_a + len(ia), cov_b + len(ib))

        if len(edges) > 18:  # keep it bounded: greedy on very long names
            used_a, used_b, count, ca, cb = set(), set(), 0, 0, 0
            for ia, ib in edges:
                if used_a & set(ia) or used_b & set(ib):
                    continue
                used_a |= set(ia)
                used_b |= set(ib)
                count += 1
                ca += len(ia)
                cb += len(ib)
            return count, ca, cb
        search(0, frozenset(), frozenset(), 0, 0, 0)
        return best

    def agree(self, name_a, name_b, company=False):
        pa, pb = self.parts(name_a, company), self.parts(name_b, company)
        if not pa or not pb:
            return False
        count, cov_a, cov_b = self.match_count(pa, pb)
        if count >= 2:
            return True
        if len(pa) <= len(pb):
            return cov_a >= len(pa) and count >= 1
        return cov_b >= len(pb) and count >= 1

    def any_agree(self, names_a, names_b, company=False):
        """First (a, b) pair that agrees, in sorted order, or None."""
        for a in names_a:
            for b in names_b:
                if self.agree(a, b, company):
                    return a, b
        return None


def edit_distance(a, b, limit=None):
    """Levenshtein distance (insert, delete, substitute; a swap costs two)."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        lowest = i
        for j, cb in enumerate(b, 1):
            value = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            cur.append(value)
            lowest = min(lowest, value)
        if limit is not None and lowest > limit:
            return limit + 1
        prev = cur
    return prev[-1]

"""Adversarial checks for the screening rules.

Run: python -m unittest discover -s system/tests
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SYSTEM = os.path.dirname(HERE)
ROOT = os.path.dirname(SYSTEM)
sys.path.insert(0, SYSTEM)

import decide_hits  # noqa: E402
from decide_hits import canon_country, lived_countries, location_status, note_verified  # noqa: E402
from find_customers import country_key, fold  # noqa: E402

# Hand rewrites of development notes and profiles: synonyms, other date
# formats, other country spellings, and negations. None of these may move a
# hit's decision to DIFFERENT_ENTITY.
REWRITES = {
    "notes.jsonl": {
        "NOTE-003A04": (
            "The account holder reported professional and residential activity in the United States covering 2008 until 2018.",
            "The account holder reported they resided in the USA from 2008 through 2018.",
        ),
        "NOTE-011A1B": (
            "During the interval of 2010 to 2013, the individual was employed and lived in Spain.",
            "From 2010 until 2013, the individual was employed and domiciled in España.",
        ),
        "NOTE-011A30": (
            "The individual's employment and residency history in Singapore is set from 2011 until 2018.",
            "The individual's employment and home in the Republic of Singapore ran from 2011 until 2018.",
        ),
        "NOTE-02B4B4": (
            "Documentation confirms the user lived and held employment in Singapore from 2010 through 2018.",
            "Documentation confirms the user stayed and held employment in Singapore between 2010 and 2018.",
        ),
        "NOTE-00009B": (
            "born December 13, 1977, using a national ID issued in Germany",
            "born 13 December 1977, using a national ID issued in the Federal Republic of Germany",
        ),
        "NOTE-000142": (
            "DOB 22/09/1956, from Nigeria",
            "born 22 September 1956, from the Federal Republic of Nigeria",
        ),
        "NOTE-000159": (
            "identity was confirmed",
            "identity was not verified",
        ),
        "NOTE-000183": (
            "had her identity confirmed",
            "had her identity awaiting review",
        ),
    },
    "hits.jsonl": {
        "H-79301D": (
            "was born in 1966",
            "has a date of birth of 1966",
        ),
        "H-F51449": (
            "was born on 30 June 1969",
            "DOB: June 30, 1969",
        ),
    },
}


class Folding(unittest.TestCase):
    def test_letters_that_do_not_decompose(self):
        self.assertEqual(fold("Paweł"), "pawel")
        self.assertEqual(fold("Ørsted"), "orsted")
        self.assertEqual(fold("Heinz-Peter"), "heinzpeter")


class Countries(unittest.TestCase):
    def test_codes_demonyms_and_territories(self):
        self.assertEqual(country_key("GB"), "united kingdom")
        self.assertEqual(country_key("Nigerian"), "nigeria")
        self.assertEqual(canon_country("Ribeirao Pires, Sao Paulo, Brazil"), "brazil")
        self.assertEqual(country_key("Hong Kong"), "hong kong")
        self.assertEqual(country_key("MO"), "macao")
        self.assertIsNone(country_key("International"))
        self.assertIsNone(country_key("Narnia"))

    def test_unknown_is_not_a_mismatch(self):
        self.assertIsNone(canon_country("Narnia"))
        self.assertEqual(location_status([], ["france"]), "unknown")
        self.assertEqual(location_status(["germany"], ["france"]), "differ")


class Notes(unittest.TestCase):
    def test_any_country_counts_without_a_cue(self):
        self.assertEqual(lived_countries("The branch is in Singapore."), ["singapore"])

    def test_a_negative_word_blocks_a_pass(self):
        self.assertTrue(note_verified("Sumsub verified the passport."))
        for word in (
            "failed", "declined", "unverified", "pending", "awaiting",
            "unable", "denied", "inconclusive", "not approved", "not verified",
        ):
            self.assertFalse(note_verified(f"The check was {word} after it was verified."))


class Rewording(unittest.TestCase):
    def test_reworded_text_does_not_become_a_clear(self):
        dev = os.path.join(ROOT, "dev")
        if not os.path.isdir(dev):
            self.skipTest("dev data is not in this checkout")
        before = self._decisions(dev)
        folder = tempfile.mkdtemp()
        try:
            self._copy_rewritten(dev, folder)
            after = self._decisions(folder)
        finally:
            shutil.rmtree(folder)
        moved = []
        for hit_id, decision in before.items():
            later = after[hit_id]
            if decision != "DIFFERENT_ENTITY" and later == "DIFFERENT_ENTITY":
                moved.append(f"{hit_id} {decision} -> {later}")
        self.assertFalse(moved, "rewording cleared a hit:\n" + "\n".join(moved))

    def _decisions(self, data):
        rows = decide_hits.decide(data, None)
        return {row["hit_id"]: row["decision"] for row in rows}

    def _copy_rewritten(self, source, target):
        for name in os.listdir(source):
            src = os.path.join(source, name)
            if not os.path.isfile(src):
                continue
            dest = os.path.join(target, name)
            if name not in REWRITES:
                shutil.copyfile(src, dest)
                continue
            changes = REWRITES[name]
            seen = set()
            with open(src, encoding="utf-8") as handle, open(dest, "w", encoding="utf-8", newline="\n") as out:
                for line in handle:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    key = row.get("note_id") or row.get("hit_id")
                    if key in changes:
                        old, new = changes[key]
                        field = "text" if "note_id" in row else "profile_notes"
                        self.assertIn(old, row[field])
                        row[field] = row[field].replace(old, new, 1)
                        seen.add(key)
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.assertEqual(seen, set(changes))


if __name__ == "__main__":
    unittest.main()

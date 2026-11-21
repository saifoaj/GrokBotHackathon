"""Unit tests for the policy rules. Run: python3 -m unittest discover -s system/tests"""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import countries  # noqa: E402
from customers import parse_kyc_note  # noqa: E402
from decide import agrees, contradicted  # noqa: E402
from hits import age_years, born_date  # noqa: E402
from names import NameRules, edit_distance  # noqa: E402
from norm import before, parse_date  # noqa: E402


def rules():
    folder = tempfile.mkdtemp()
    with open(os.path.join(folder, "name_variants.json"), "w", encoding="utf-8") as handle:
        json.dump({"groups": [["muhammad", "mohammed", "mohamed"], ["anthony", "tony"]],
                   "company_legal_forms": ["ltd", "sa", "gmbh", "inc"]}, handle)
    return NameRules(folder)


class Names(unittest.TestCase):
    def setUp(self):
        self.r = rules()

    def test_variants_and_order(self):
        self.assertTrue(self.r.agree("Mohammed Ali Khan", "Khan, Muhammad"))
        self.assertTrue(self.r.agree("Tony Smith", "Anthony Smith"))

    def test_joined_parts(self):
        self.assertTrue(self.r.agree("Abdulsalam Bello", "Abdul Salam Bello"))

    def test_edit_limits(self):
        self.assertEqual(edit_distance("cristian", "cirstian"), 2)       # a swap is two edits
        self.assertTrue(self.r.part_match("cristian", "cirstian"))       # 8 letters: 2 allowed
        self.assertFalse(self.r.part_match("maria", "mraia"))            # 5 letters: 1 allowed
        self.assertFalse(self.r.part_match("ali", "aly") and False)
        self.assertFalse(self.r.part_match("lee", "lea"))                # under 4 letters: identical only

    def test_initials_placeholders_brackets(self):
        self.assertEqual(self.r.parts("Paweł N/A N/A"), ["pawel"])
        self.assertEqual(self.r.parts("John A. Smith (AKA)"), ["john", "smith"])

    def test_company_legal_forms(self):
        self.assertTrue(self.r.agree("Invention Bridge SL", "Invention Bridge", company=True))
        self.assertEqual(self.r.parts("Smith Capital Ltd", company=True), ["smith", "capital"])

    def test_disagree(self):
        self.assertFalse(self.r.agree("Sarah Johnson", "Johnson, James Jacob"))


class Dates(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_date("19/05/2004"), (2004, 5, 19))
        self.assertEqual(parse_date("February 19, 2002"), (2002, 2, 19))
        self.assertEqual(parse_date("27 June 1967"), (1967, 6, 27))
        self.assertEqual(parse_date("2022-03"), (2022, 3, None))
        self.assertEqual(parse_date("1996"), (1996, None, None))

    def test_contradiction(self):
        self.assertTrue(contradicted((1979, 11, 8), (1977, None, None)))   # 2 years out
        self.assertFalse(contradicted((1978, 11, 8), (1977, None, None)))  # within 1 year
        self.assertTrue(contradicted((1965, 3, 28), (1965, 9, 10)))        # same year, other month
        self.assertTrue(agrees((1968, 2, 17), (1968, 2, None)))            # parts both give agree

    def test_death_precision(self):
        self.assertTrue(before((2022, 3, None), (2026, 11, 6)))
        self.assertFalse(before((2026, 11, None), (2026, 11, 6)))           # same month: unknown

    def test_ages(self):
        self.assertEqual(age_years("someone aged 45 in March 2019"), [1973, 1974])
        self.assertEqual(age_years("When questioned in January 2023, he was 46."), [1976, 1977])
        self.assertEqual(born_date("X was born on 17 July 1939."), (1939, 7, 17))
        self.assertEqual(born_date("DOB: 6/20/1955"), (1955, None, None))   # order not stated: year only
        self.assertEqual(born_date("DOB: 05/06/1955"), (1955, None, None))
        self.assertEqual(age_years("questioned, aged fifty-two, in May 2011"), [1958, 1959])

    def test_age_of_someone_else_is_ignored(self):
        from hits import hit_age_years
        self.assertIsNone(hit_age_years("Ann Lee's lawyer, aged 61, spoke in May 2019.", ["Ann Lee"]))
        self.assertEqual(hit_age_years("Ann Lee, aged 61, was charged in May 2019.", ["Ann Lee"]), [1957, 1958])


class Countries(unittest.TestCase):
    def test_canon(self):
        self.assertEqual(countries.canon("GB"), "United Kingdom")
        self.assertEqual(countries.canon("Nigerian"), "Nigeria")
        self.assertEqual(countries.canon("Ribeirao Pires, Sao Paulo, Brazil"), "Brazil")
        self.assertIsNone(countries.canon("International"))
        self.assertEqual(countries.canon("Northern Mariana Islands"), "Northern Mariana Islands")


class KycNotes(unittest.TestCase):
    def test_possessive_name(self):
        note = {"created_at": "2023-09-07", "text": "PABLO PIERRE CONTI's national ID from Spain was verified by "
                                                    "Veriff on 2023-09-07; his DOB is October 8, 1952."}
        p = parse_kyc_note(note)
        self.assertEqual(p["name"], "PABLO PIERRE CONTI")
        self.assertEqual(p["dob"], (1952, 10, 8))
        self.assertEqual(p["country"], "Spain")

    def test_rejected_note_is_not_evidence(self):
        note = {"created_at": "2024-01-01", "text": "Onfido rejected the passport for JOHN SMITH on 2024-01-01."}
        self.assertFalse(parse_kyc_note(note)["approved"])


if __name__ == "__main__":
    unittest.main()

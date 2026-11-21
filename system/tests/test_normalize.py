"""Normaliser: only values the rules cannot read are asked, and agreed answers are written back
in the standard form; a split vote leaves the value as it was."""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import llm  # noqa: E402
import normalize  # noqa: E402


class MiniData:
    def __init__(self):
        self.customers = [
            {"record_id": "C1", "dob": "1988-03-14", "residence_country": "Germany", "nationality": "DE",
             "entity_type": "Individual", "full_name": "Ana Ruiz"},
            {"record_id": "C2", "dob": "1988年3月14日", "residence_country": "Bavaria", "nationality": None,
             "entity_type": "natural_person", "full_name": "Jan Martin"},
        ]
        self.kyc = [{"check_id": "K1", "verified_dob": "14th Mar '88", "checked_at": "2024-01-02",
                     "document_country": "Germany"}]
        self.alerts = [{"alert_id": "A1", "screened_at": "2026-11-06",
                        "customer": {"name": "Ana Ruiz", "entity_type": "Individual", "dob": "1988",
                                     "country": "Austria"}}]
        self.hits = [{"hit_id": "H1", "name": "Ruiz, Ana", "entity_type": "Individual",
                      "dobs": ["1960 (approx.)", "1988"], "deceased_date": None,
                      "countries": ["Spain"], "nationality": [], "citizenship": [],
                      "addresses": ["1 Calle Mayor, Andalucia [Business]", "Spain [PEP]"],
                      "place_of_birth": None, "profile_notes": ""}]


class Fake:
    """Stands in for llm.vote_batch: answers from a table, None (a split) for anything else."""

    def __init__(self, table):
        self.table = table
        self.asked = []

    def __call__(self, store, prompt, items, read, batch=25, label=""):
        out = {}
        for item_id, item in items:
            self.asked.append(item["value"] if "value" in item else item.get("entity_type"))
            out[item_id] = self.table.get(item.get("value", item.get("entity_type")))
        return out


class NormaliseTest(unittest.TestCase):
    def setUp(self):
        self.real = llm.vote_batch
        os.environ["LLM_CACHE"] = tempfile.mkdtemp()

    def tearDown(self):
        llm.vote_batch = self.real

    def run_with(self, table):
        fake = Fake(table)
        llm.vote_batch = fake
        data = MiniData()
        normalize.apply(data)
        return data, fake

    def test_only_unreadable_values_are_asked(self):
        _data, fake = self.run_with({})
        self.assertIn("1988年3月14日", fake.asked)
        self.assertIn("14th Mar '88", fake.asked)
        self.assertIn("Bavaria", fake.asked)
        self.assertIn("1 Calle Mayor, Andalucia", fake.asked)  # tag removed before asking
        self.assertIn("natural_person", fake.asked)
        for readable in ("1988-03-14", "Germany", "DE", "Spain [PEP]", "2026-11-06", "1960 (approx.)", "Individual"):
            self.assertNotIn(readable, fake.asked)

    def test_agreed_answers_are_written_in_standard_form(self):
        data, _ = self.run_with({"1988年3月14日": "1988-03-14", "14th Mar '88": "1988-03-14",
                                 "Bavaria": "Germany", "1 Calle Mayor, Andalucia": "Spain",
                                 "natural_person": "Individual"})
        c2 = data.customers[1]
        self.assertEqual(c2["dob"], "1988-03-14")
        self.assertEqual(c2["_raw_dob"], "1988年3月14日")
        self.assertEqual(c2["residence_country"], "Germany")
        self.assertEqual(c2["entity_type"], "Individual")
        self.assertEqual(data.kyc[0]["verified_dob"], "1988-03-14")
        self.assertEqual(data.hits[0]["addresses"][0], "1 Calle Mayor, Andalucia, Spain [Business]")
        self.assertEqual(data.hits[0]["addresses"][1], "Spain [PEP]")

    def test_split_vote_leaves_the_value_alone(self):
        data, _ = self.run_with({})
        self.assertEqual(data.customers[1]["dob"], "1988年3月14日")
        self.assertEqual(data.customers[1]["residence_country"], "Bavaria")
        self.assertNotIn("_raw_dob", data.customers[1])

    def test_reply_readers_reject_bad_shapes(self):
        reply = {"items": [{"id": "a", "date": "14/03/1988", "country": "Atlantis", "type": "Robot"},
                           {"id": "b", "date": "1988-03", "country": "DE", "type": "Company"}]}
        self.assertIsNone(normalize._read_date(reply, "a"))
        self.assertIsNone(normalize._read_place(reply, "a"))
        self.assertIsNone(normalize._read_type(reply, "a"))
        self.assertEqual(normalize._read_date(reply, "b"), "1988-03")
        self.assertEqual(normalize._read_place(reply, "b"), "Germany")
        self.assertEqual(normalize._read_type(reply, "b"), "Company")


class VoteTest(unittest.TestCase):
    def test_unanimous_or_nothing(self):
        os.environ["LLM_CACHE"] = tempfile.mkdtemp()
        llm.CACHE_DIR = os.environ["LLM_CACHE"]
        answers = iter([{"items": [{"id": "x", "v": 1}, {"id": "y", "v": 1}]}] * 4 +
                       [{"items": [{"id": "x", "v": 1}, {"id": "y", "v": 2}]}])
        real_chat, real_key, real_on = llm.chat, llm.api_key, llm.enabled
        llm.chat, llm.api_key, llm.enabled = (lambda p, payload, **k: next(answers)), (lambda: "k"), (lambda: True)
        try:
            store = llm.Store("t")
            out = llm.vote_batch(store, "p", [("x", {"id": "x"}), ("y", {"id": "y"})],
                                 lambda r, i: next(row["v"] for row in r["items"] if row["id"] == i))
            self.assertEqual(out, {"x": 1, "y": None})
            # saved: asked again, nothing is called and the split stays a split
            llm.chat = lambda *a, **k: self.fail("called again")
            again = llm.vote_batch(llm.Store("t"), "p", [("x", {"id": "x"}), ("y", {"id": "y"})], None)
            self.assertEqual(again, {"x": 1, "y": None})
        finally:
            llm.chat, llm.api_key, llm.enabled = real_chat, real_key, real_on


if __name__ == "__main__":
    unittest.main()

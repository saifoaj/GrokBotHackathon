"""Fact-check: drop a misread date or place; keep a reading the stub accepts."""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from customers import People, parse_kyc_note  # noqa: E402
from decide import compare  # noqa: E402
from fact_check import (  # noqa: E402
    Checker, Client, claim_stands, message_text, openrouter_model, parse_reply,
    resolve_backend, strip_kyc, packet_digest, COLLECT, ENFORCE,
    DEFAULT_MODEL, DEFAULT_OPENROUTER_MODEL, OPENROUTER_URL, XAI_URL,
)
from hits import picture, search_checks  # noqa: E402
from names import NameRules  # noqa: E402

LLM_ISSUE = "UNIQUE_LLM_PHRASE_SHOULD_NEVER_APPEAR"


class Stub:
    def __init__(self, replies=None):
        self.replies = replies or {}
        self.calls = []

    def verdict(self, source_id, kind, text, claim):
        self.calls.append((source_id, kind, dict(claim)))
        return dict(self.replies.get(source_id) or {"ok": True, "wrong": [], "issue": ""})


def name_rules():
    folder = tempfile.mkdtemp()
    with open(os.path.join(folder, "name_variants.json"), "w", encoding="utf-8") as handle:
        json.dump({"groups": [], "company_legal_forms": ["ltd"]}, handle)
    return NameRules(folder)


class MiniData:
    def __init__(self, notes):
        self.customers = [{"record_id": "CUS-1", "email": "a@b.c", "entity_type": "individual",
                           "full_name": "Nils Martin", "dob": "1977-12-13", "phone": None,
                           "preferred_name": None, "login_name": None,
                           "residence_country": "Germany", "nationality": "Germany"}]
        self.customer = {c["record_id"]: c for c in self.customers}
        self.kyc = []
        self.applicants_of_customer = {}
        self.notes_by_email = {"a@b.c": notes}

    def checks_of_customer(self, cid):
        return []


def pep_hit(**kw):
    hit = {
        "hit_id": "H-1", "entity_type": "Individual", "name": "Ann Lee",
        "best_matching_name": None, "aliases": [], "first_name": None, "middle_name": None,
        "last_name": None, "dobs": [], "place_of_birth": None, "countries": ["France"],
        "nationality": ["France"], "citizenship": [], "addresses": [], "deceased_date": None,
        "primary_category": "PEP", "categories": ["PEP"],
        "profile_notes": "Ann Lee, aged 61, was charged in May 2019.",
    }
    hit.update(kw)
    return hit


class FakeSearch:
    def __init__(self, rows=None):
        self.search_by_query = {}
        for row in rows or []:
            key = " ".join(row["query"].split())
            self.search_by_query.setdefault(key, []).append(row)


class Pure(unittest.TestCase):
    def test_strip_kyc_drops_only_the_wrong_field(self):
        parsed = {"approved": True, "name": "NILS MARTIN", "dob": (1977, 12, 13),
                  "country": "Germany", "countries": ["Germany"]}
        out = strip_kyc(parsed, {"ok": False, "wrong": ["dob"], "issue": "that is the check date"})
        self.assertEqual(out["name"], "NILS MARTIN")
        self.assertIsNone(out["dob"])
        self.assertEqual(out["country"], "Germany")

    def test_strip_kyc_empty_wrong_drops_all(self):
        parsed = {"approved": True, "name": "NILS MARTIN", "dob": (1977, 12, 13),
                  "country": "Germany", "countries": ["Germany"]}
        out = strip_kyc(parsed, {"ok": False, "wrong": [], "issue": "not a check"})
        self.assertIsNone(out["name"])
        self.assertIsNone(out["dob"])
        self.assertIsNone(out["country"])
        self.assertEqual(out["countries"], [])

    def test_claim_stands_fail_open_and_aliases(self):
        self.assertTrue(claim_stands({"ok": True, "wrong": []}, ["dob"]))
        self.assertTrue(claim_stands(None, ["dob"]))
        self.assertFalse(claim_stands({"ok": False, "wrong": ["date of birth"]}, ["dob"]))
        self.assertTrue(claim_stands({"ok": False, "wrong": ["country"]}, ["dob"]))

    def test_parse_reply(self):
        got = parse_reply('{"ok": false, "wrong": ["dob"], "issue": "issue date"}')
        self.assertFalse(got["ok"])
        self.assertEqual(got["wrong"], ["dob"])

    def test_digest_is_stable(self):
        a = packet_digest("NOTE-1", "kyc_note", "born 1977", {"dob": "1977"})
        b = packet_digest("NOTE-1", "kyc_note", "born 1977", {"dob": "1977"})
        self.assertEqual(a, b)
        self.assertNotEqual(a, packet_digest("NOTE-1", "kyc_note", "born 1978", {"dob": "1978"}))


class Backend(unittest.TestCase):
    def test_openrouter_key_selects_openrouter(self):
        got = resolve_backend(env={"OPENROUTER_API_KEY": "sk-or-test"}, file_values={})
        self.assertEqual(got["source"], "openrouter")
        self.assertEqual(got["url"], OPENROUTER_URL)
        self.assertEqual(got["key"], "sk-or-test")
        self.assertEqual(got["model"], DEFAULT_OPENROUTER_MODEL)
        self.assertEqual(got["extra"], {"reasoning": {"enabled": False}})

    def test_grok_key_wins_when_both_are_set(self):
        got = resolve_backend(
            env={"GROK_API_KEY": "xai-test", "OPENROUTER_API_KEY": "sk-or-test"},
            file_values={})
        self.assertEqual(got["source"], "grok")
        self.assertEqual(got["url"], XAI_URL)
        self.assertEqual(got["model"], DEFAULT_MODEL)
        self.assertEqual(got["extra"], {})

    def test_openrouter_model_override_and_file(self):
        got = resolve_backend(
            env={},
            file_values={"OPENROUTER_API_KEY": "sk-or-file", "OPENROUTER_MODEL": "x-ai/grok-4.6"})
        self.assertEqual(got["model"], "x-ai/grok-4.6")
        self.assertEqual(got["extra"], {})
        self.assertEqual(got["key"], "sk-or-file")

    def test_env_beats_dotenv(self):
        got = resolve_backend(
            env={"GROK_API_KEY": "xai-env"},
            file_values={"OPENROUTER_API_KEY": "sk-or-file"})
        self.assertEqual(got["source"], "grok")
        self.assertEqual(got["key"], "xai-env")

    def test_non_reasoning_xai_id_maps_to_openrouter(self):
        model, extra = openrouter_model(DEFAULT_MODEL)
        self.assertEqual(model, DEFAULT_OPENROUTER_MODEL)
        self.assertFalse(extra["reasoning"]["enabled"])

    def test_message_text_joins_parts(self):
        self.assertEqual(message_text({"content": [{"type": "text", "text": '{"ok": true}'}]}),
                         '{"ok": true}')


class ClientRequest(unittest.TestCase):
    def test_openrouter_request_disables_reasoning(self):
        import io
        from unittest import mock

        captured = {}

        class FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout=0):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["auth"] = request.get_header("Authorization")
            payload = {"choices": [{"message": {"content": '{"ok": true, "wrong": [], "issue": ""}'}}]}
            return FakeResponse(json.dumps(payload).encode("utf-8"))

        client = Client("sk-or-test", DEFAULT_OPENROUTER_MODEL, OPENROUTER_URL,
                        {"reasoning": {"enabled": False}})
        with mock.patch("urllib.request.urlopen", fake_urlopen):
            reply = client.review({"source_id": "NOTE-1", "kind": "kyc_note", "text": "born 1977",
                                   "claim": {"dob": "1977"}})
        self.assertTrue(reply["ok"])
        self.assertEqual(captured["url"], OPENROUTER_URL)
        self.assertEqual(captured["auth"], "Bearer sk-or-test")
        self.assertEqual(captured["body"]["model"], DEFAULT_OPENROUTER_MODEL)
        self.assertEqual(captured["body"]["reasoning"], {"enabled": False})
        self.assertEqual(captured["body"]["response_format"], {"type": "json_object"})


class CacheModes(unittest.TestCase):
    def test_collect_then_enforce(self):
        path = os.path.join(tempfile.mkdtemp(), "cache.jsonl")
        checker = Checker(key="", path=path)
        checker.mode = COLLECT
        first = checker.verdict("NOTE-1", "kyc_note", "checked 2023-02-19, born 1977-12-13",
                                {"dob": "1977-12-13"})
        self.assertTrue(first["ok"])
        self.assertEqual(len(checker.pending), 1)
        digest = list(checker.pending)[0]
        checker.cache[digest] = {"digest": digest, "ok": False, "wrong": ["dob"], "issue": "no"}
        checker.mode = ENFORCE
        later = checker.verdict("NOTE-1", "kyc_note", "checked 2023-02-19, born 1977-12-13",
                                {"dob": "1977-12-13"})
        self.assertFalse(claim_stands(later, ["dob"]))


class Notes(unittest.TestCase):
    def test_kyc_rejected_dob_is_dropped(self):
        note = {"note_id": "NOTE-1", "requester_email": "a@b.c", "created_at": "2023-02-19",
                "channel": "kyc",
                "text": "Sumsub completed identity verification for NILS MARTIN, born December 13, 1977, "
                        "using a national ID issued in Germany on 2023-02-19."}
        parsed = parse_kyc_note(note)
        self.assertEqual(parsed["dob"], (1977, 12, 13))
        people = People(MiniData([note]), name_rules(),
                        checker=Stub({"NOTE-1": {"ok": False, "wrong": ["dob"], "issue": "check date"}}))
        facts = people.kyc_facts(["CUS-1"])
        self.assertEqual(len(facts), 1)
        self.assertIsNone(facts[0]["dob"])
        self.assertEqual(facts[0]["name"], "NILS MARTIN")

    def test_support_country_rejected(self):
        note = {"note_id": "NOTE-2", "requester_email": "a@b.c", "created_at": "2026-03-02",
                "channel": "phone",
                "text": "The client has established they lived and worked within Portugal from 2011 until 2016."}
        people = People(MiniData([note]), name_rules(),
                        checker=Stub({"NOTE-2": {"ok": False, "wrong": ["country"], "issue": "no"}}))
        self.assertEqual(people.lived_notes(["CUS-1"]), {})
        people.checker = Stub()
        self.assertEqual(people.lived_notes(["CUS-1"]), {"Portugal": ["NOTE-2"]})


class ProfileAndSearch(unittest.TestCase):
    def test_rejected_profile_age_is_not_used(self):
        hit = pep_hit()
        data = FakeSearch()
        kept = picture(hit, data, Stub())
        self.assertEqual(kept["dob_origin"], "profile")
        self.assertEqual(kept["dates"], [(1957, None, None), (1958, None, None)])
        dropped = picture(hit, data, Stub({"H-1": {"ok": False, "wrong": ["birth_years"], "issue": LLM_ISSUE}}))
        self.assertEqual(dropped["dates"], [])
        self.assertIsNone(dropped["dob_origin"])
        pic = {"is_person": True, "dobs": [((1960, 1, 1), ["CUS-1"])], "names": ["Ann Lee"],
               "verified_name": None, "verified_name_src": None, "records": ["CUS-1"],
               "countries": {"France": ["CUS-1"]}, "latest_check": None, "anchor": "CUS-1"}
        reason = " ".join(compare(pic, dropped, name_rules(), None)["sentences"])
        self.assertNotIn(LLM_ISSUE, reason)

    def test_rejected_search_snippet_is_not_used(self):
        hit = pep_hit(profile_notes="")
        row = {"result_id": "SR-1", "query": "ann lee",
               "snippet": "In May 2019 France named Ann Lee, 61, as a politically exposed person.",
               "title": "x", "source": "x"}
        data = FakeSearch([row])
        kept = picture(hit, data, Stub())
        self.assertEqual(kept["dob_origin"], "search")
        self.assertEqual(kept["sr"], ["SR-1"])
        dropped = picture(hit, data, Stub({"SR-1": {"ok": False, "wrong": ["about_hit"], "issue": LLM_ISSUE}}))
        self.assertEqual(dropped["dates"], [])
        self.assertEqual(dropped["sr"], [])
        checks = search_checks(hit, data, ["France"],
                               Stub({"SR-1": {"ok": False, "wrong": ["about_hit"], "issue": LLM_ISSUE}}))
        whys = [why for _k, rows in checks for _s, _ok, why, _y in rows]
        self.assertFalse(any(ok for _k, rows in checks for _s, ok, _w, _y in rows))
        self.assertTrue(whys)
        self.assertTrue(all(LLM_ISSUE not in why for why in whys))
        self.assertEqual(whys[0], "extracted reading was not used")


if __name__ == "__main__":
    unittest.main()

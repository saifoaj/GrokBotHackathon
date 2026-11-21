"""Edge cases for screen.py. Run: python3 -m unittest system/test_screen.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screen as S  # noqa: E402

VARIANTS = {"groups": [["muhammad", "mohammed", "mohammad", "mohamed", "md"], ["abdul", "abdel"], ["anthony", "tony"]],
            "company_legal_forms": ["ltd", "gmbh", "sa", "ab", "inc", "na"]}
S.set_variants(VARIANTS)
D = S.Date
ALERT = {"screened_at": "2026-11-01"}


def person(name="John Smith", dob=D(1980, 5, 3), countries=("GB",), verified=True, kind="Individual"):
    p = S.Profile(kind=kind, records=["CUS-000001"])
    p.names = [(name, S.norm_name(name, kind == "Company"), "CUS-000001")]
    if verified:
        p.verified_name = (name, S.norm_name(name, kind == "Company"), "KYC-AAAAAA")
    p.dobs = [(dob, ["KYC-AAAAAA"])] if dob else []
    p.dob_verified = verified
    p.countries = [(c, "CUS-000001") for c in countries]
    return p


def hit(name="Smith, John", dobs=(((1980,), None, None, "H-000001#dobs", "listed date of birth"),), countries=("GB",), kind="Individual"):
    p = S.Profile(kind=kind, hit_id="H-000001")
    p.names = [(name, S.norm_name(name, kind == "Company"), "name")]
    p.dobs = list(dobs)
    p.countries = [(c, "countries") for c in countries]
    return p


def listed(*years):
    return tuple(((y,), None, None, "H-000001#dobs", "listed date of birth") for y in years)


class WrongClearGuards(unittest.TestCase):
    def test_country_differs_but_name_and_dob_agree_is_review(self):
        d = S.decide(person(countries=("GB",)), hit(countries=("PK",)), ALERT)
        self.assertEqual((d.decision, d.codes[0]), ("REVIEW", "INSUFFICIENT_DATA"))

    def test_one_agreeing_hit_dob_blocks_dob_mismatch(self):
        d = S.decide(person(dob=D(1980, 5, 3)), hit(dobs=listed(1950, 1980)), ALERT)
        self.assertNotIn("DOB_MISMATCH", d.codes)
        self.assertEqual(d.decision, "SAME_ENTITY")

    def test_year_within_one_neither_agrees_nor_contradicts(self):
        d = S.decide(person(dob=D(1981, 5, 3)), hit(dobs=listed(1980)), ALERT)
        self.assertEqual(d.decision, "REVIEW")

    def test_placeholder_name_never_cleared_on_name(self):
        c = person(name="UNKNOWN NADEEM", dob=None, countries=())
        d = S.decide(c, hit(name="Ali, Hassan", dobs=(), countries=()), ALERT)
        self.assertNotIn("NAME_MISMATCH_VERIFIED", d.codes)
        self.assertEqual(S.norm_name("UNKNOWN NADEEM"), ["nadeem"])
        self.assertEqual(S.norm_name("PAWEŁ N/A N/A"), ["pawel"])

    def test_conflicting_unverified_dobs_never_clear_on_dob(self):
        c = person(verified=False)
        c.dobs = [(D(1990, 1, 1), ["CUS-000001"]), (D(1991, 2, 2), ["CUS-000002"])]
        d = S.decide(c, hit(dobs=listed(1950)), ALERT)
        self.assertNotIn("DOB_MISMATCH", d.codes)
        self.assertEqual(d.decision, "REVIEW")

    def test_unknown_country_blocks_location_mismatch(self):
        c = person(dob=None)
        c.unknown_country = True
        d = S.decide(c, hit(dobs=(), countries=("PK",)), ALERT)
        self.assertNotIn("LOCATION_MISMATCH", d.codes)


def tiny_data(customers, accounts=(), checks=(), onboarding=(), notes=()):
    return {"alerts": [], "hits": [], "customers": list(customers), "accounts": list(accounts), "onboarding": list(onboarding),
            "kyc_checks": list(checks), "notes": list(notes), "search_results": [], "name_variants": VARIANTS}


def cust(rid, name, dob, phone, country="GB", city="London", email=None):
    return {"record_id": rid, "entity_type": "Individual", "full_name": name, "preferred_name": None, "login_name": None, "dob": dob,
            "nationality": country, "residence_country": country, "city": city, "address": f"1 High St, {city}",
            "email": email or f"{rid.lower()}@x.example", "phone": phone}


class Resolution(unittest.TestCase):
    def test_union_find_chains_and_phone_alone_does_not_merge(self):
        data = tiny_data([cust("CUS-000001", "Ann Lee", "1980-01-01", "+44 111111111"),
                          cust("CUS-000002", "Ann Lee", "1980-01-01", "+44 111111111"),       # phone + DOB -> A~B
                          cust("CUS-000003", "Ann Lee", "1975-03-03", "+44 222222222"),       # doc number -> B~C
                          cust("CUS-000004", "Bo Lee", "1999-09-09", "+44 111111111")],       # phone only -> alone
                         checks=[{"check_id": "KYC-000001", "applicant_id": "APP-1", "verified_name": "Ann Lee", "verified_dob": None, "document_number": "X123456",
                                  "document_country": "GB", "result": "approved", "checked_at": "2024-01-01"},
                                 {"check_id": "KYC-000002", "applicant_id": "APP-2", "verified_name": "Ann Lee", "verified_dob": None, "document_number": "X123456",
                                  "document_country": "GB", "result": "approved", "checked_at": "2024-01-01"}],
                         onboarding=[{"account_id": "ACC-1", "customer_id": "CUS-000002", "kyc_applicant_id": "APP-1"},
                                     {"account_id": "ACC-2", "customer_id": "CUS-000003", "kyc_applicant_id": "APP-2"}])
        ix = S.build_index(data)
        self.assertEqual(ix["person"]["CUS-000001"], ix["person"]["CUS-000003"])
        self.assertNotEqual(ix["person"]["CUS-000001"], ix["person"]["CUS-000004"])

    def test_rejected_and_expired_checks_are_ignored(self):
        data = tiny_data([cust("CUS-000001", "Ann Lee", "1980-01-01", "+44 1")],
                         checks=[{"check_id": f"KYC-00000{i}", "applicant_id": "APP-1", "verified_name": "Zed Zed", "verified_dob": "1950-01-01",
                                  "document_number": f"D{i}23456", "document_country": "FR", "result": r, "checked_at": "2025-01-01"}
                                 for i, r in enumerate(["rejected", "expired"])],
                         onboarding=[{"account_id": "ACC-1", "customer_id": "CUS-000001", "kyc_applicant_id": "APP-1"}])
        ix = S.build_index(data)
        p = S.customer_profile(ix["person"]["CUS-000001"], ix, "2026-01-01")
        self.assertIsNone(p.verified_name)
        self.assertEqual(p.dobs[0][0], D(1980, 1, 1))
        self.assertNotIn("FR", [c for c, _ in p.countries])
        self.assertIsNone(S.parse_note({"channel": "kyc", "created_at": "2024-01-01", "text": "Onfido rejected the passport of ANN LEE, DOB 1980-01-01, on 2024-01-01."}))

    def test_two_people_different_outcomes_is_ambiguous(self):
        data = tiny_data([cust("CUS-000001", "Ann Lee", "1980-01-01", "+44 1", country="GB"),
                          cust("CUS-000002", "Ann Lee", "1980-06-06", "+44 2", country="PK")])
        ix = S.build_index(data)
        alert = {"alert_id": "A-000001", "screened_at": "2026-01-01", "customer": {"name": "Ann Lee", "entity_type": "Individual", "dob": "1980", "country": None, "city": None},
                 "account_ref": None, "login_handle": None, "email": None}
        hp = hit(name="Lee, Ann", dobs=(), countries=("GB",))
        r = S.decide_hit({"hit_id": "H-000001"}, alert, ix, hp)
        self.assertEqual((r["decision"], r["reason_code"], r["customer_record_id"]), ("REVIEW", "AMBIGUOUS_CUSTOMER_RECORD", "AMBIGUOUS"))


class Names(unittest.TestCase):
    def agree(self, a, b, company=False):
        return S.names_agree(S.norm_name(a, company), S.norm_name(b, company))

    def test_variants_joined_and_reordered(self):
        self.assertTrue(self.agree("Mohammed Ali", "Muhammad Ali"))
        self.assertTrue(self.agree("Md Saidul Islam", "Islam, Mohammad Saidul"))
        self.assertTrue(self.agree("Abdulsalam Bello", "Abdul Salam Bello"))
        self.assertTrue(self.agree("Li Wei", "Wei Li"))

    def test_edit_distance_thresholds(self):
        self.assertTrue(S.part_match("smith", "smyth"))          # 5 letters, 1 edit
        self.assertFalse(S.part_match("smith", "smythe"))        # 5 letters, 2 edits
        self.assertTrue(S.part_match("johansson", "johanson"))   # 8 letters, 1 edit
        self.assertTrue(S.part_match("kowalski", "kowalsky"))
        self.assertFalse(S.part_match("ann", "anne"))            # shorter part < 4 letters must be identical
        self.assertFalse(S.part_match("omar", "amro"))           # swaps count as 2 edits
        self.assertTrue(S.part_match("tony", "anthony"))         # name_variants group

    def test_initials_brackets_accents_legal_forms(self):
        self.assertEqual(S.norm_name("Carl E. Andersen"), ["carl", "andersen"])
        self.assertEqual(S.norm_name("Metropolitan Transportation Authority, Inc. (MTA)", True), ["metropolitan", "transportation", "authority"])
        self.assertEqual(S.norm_name("Jérôme Kerviel"), ["jerome", "kerviel"])
        self.assertTrue(self.agree("Stadtwerke München GmbH", "STADTWERKE MUNCHEN", True))
        self.assertFalse(self.agree("Taslima Rahim Islam", "Islam, Md Saidul"))

    def test_single_part_alias_can_agree(self):
        self.assertTrue(S.names_agree(S.norm_name("Juan Gonzalez"), ["gonzalez"]))


class Extraction(unittest.TestCase):
    def test_age_at_date_gives_two_birth_years(self):
        f = S.parse_hit_notes("Formal charges were brought against Elvira Velasco Morillo, aged 30, in November 1998.", ["elvira", "velasco"])
        self.assertEqual(f["dobs"][0][0], (1967, 1968))
        f = S.parse_hit_notes("At the time Fabrizio Rossi faced charges in March 1999, he was 24.", ["rossi"])
        self.assertEqual(f["dobs"][0][0], (1974, 1975))
        f = S.parse_hit_notes("Abdul Rahman Yasin was born on 10 April 1960.", ["yasin"])
        self.assertEqual(f["dobs"][0][:3], ((1960,), 4, 10))

    def test_only_exact_dated_report_form_counts(self):
        text = ("According to the dawn.com; August 20, 2026: arrested.\n\n"
                "According to the U.S. Courts; On April 23, 2013, sentenced.\n"
                "According to X and Y; October 10, 2024; On October 10, 2024, pleaded guilty.\n"
                "According to wkrn.com; August 24, 2019: charged.")
        self.assertEqual(S.parse_hit_notes(text)["reports"], [D(2026, 8, 20), D(2019, 8, 24)])

    def test_search_result_needs_listing_phrase_and_hit_country(self):
        ix = {"search": {}}
        rows = [{"result_id": "SR-1", "query": "ann lee", "snippet": "In May 2019, Ann Lee, 33, a politically exposed person in Brazil, was named."},
                {"result_id": "SR-2", "query": "ann lee", "snippet": "In May 2019, Ann Lee, 50, a politically exposed person in Kenya, was named."},
                {"result_id": "SR-3", "query": "ann lee", "snippet": "In May 2019, Ann Lee, 60, a sanctioned individual in Brazil, was named."}]
        ix["search"][("ann", "lee")] = [(r["result_id"], S.parse_snippet(r)) for r in rows]
        h = {"hit_id": "H-000001", "entity_type": "Individual", "name": "LEE, Ann", "aliases": [], "dobs": [], "countries": ["Brazil"],
             "nationality": [], "citizenship": [], "addresses": [], "place_of_birth": None, "categories": ["PEP"], "primary_category": None, "profile_notes": None}
        p = S.hit_profile(h, ix)
        self.assertEqual([(d[0], d[3]) for d in p.dobs], [((1985, 1986), "SR-1")])

    def test_countries_normalize(self):
        for v, code in [("GB", "GB"), ("UK", "GB"), ("British", "GB"), ("United Kingdom", "GB"), ("Nigerian", "NG"), ("Belgrade, Serbia", "RS"),
                        ("Cote D'Ivoire", "CI"), ("Côte d'Ivoire", "CI"), ("United States [PEP]", "US"), ("Jersey", "JE"), ("Puerto Rico", "PR")]:
            self.assertEqual(S.norm_country(v), code, v)
        self.assertIsNone(S.norm_country("International"))
        self.assertIsNone(S.norm_country("Europe"))
        self.assertEqual(S.norm_country("Atlantis"), S.UNKNOWN)

    def test_every_date_format(self):
        for v, d in [("2004-05-19", D(2004, 5, 19)), ("19/05/2004", D(2004, 5, 19)), ("19 May 2004", D(2004, 5, 19)), ("May 19, 2004", D(2004, 5, 19)),
                     ("May 9, 2004", D(2004, 5, 9)), ("9 May 2004", D(2004, 5, 9)), ("2004", D(2004)), ("2004-05", D(2004, 5)), ("May 2004", D(2004, 5)),
                     ("2022/03/31", D(2022, 3, 31)), ("10/08/1988", D(1988, 8, 10))]:
            self.assertEqual(S.norm_date(v), d, v)
        self.assertIsNone(S.norm_date("13/13/2000"))

    def test_kyc_note_parse(self):
        f = S.parse_note({"channel": "kyc", "created_at": "2024-04-26", "text": "AMAKA AFOLABI's identity was confirmed via Veriff on 2024-04-26 using a Nigerian national ID. DOB recorded as 10/08/1988."})
        self.assertEqual((f["name"], f["dob"], f["country"]), ("AMAKA AFOLABI", D(1988, 8, 10), "NG"))


class Rules(unittest.TestCase):
    def test_temporal_impossibility(self):
        h = hit(dobs=(), countries=())
        h.reports = [D(2020, 1, 8)]
        d = S.decide(person(dob=D(2007, 3, 1), countries=()), h, ALERT)
        self.assertEqual(d.codes[0], "TEMPORAL_IMPOSSIBILITY")

    def test_deceased_and_entity_type(self):
        h = hit(dobs=(), countries=())
        h.death = D(2022, 3)
        self.assertIn("DECEASED", S.decide(person(dob=None, countries=()), h, ALERT).codes)
        d = S.decide(person(kind="Company", name="Acme Ltd", dob=None), hit(name="Acme", kind="Individual", dobs=()), ALERT)
        self.assertEqual(d.codes[0], "ENTITY_TYPE_MISMATCH")

    def test_month_differs_in_same_year_contradicts(self):
        d = S.decide(person(dob=D(1968, 5, 1)), hit(dobs=(((1968,), 2, None, "x", "listed date of birth"),)), ALERT)
        self.assertEqual(d.codes[0], "DOB_MISMATCH")


if __name__ == "__main__":
    unittest.main()

"""Build a synthetic stress-test data folder in the same format as the development set.

Every hit is a deliberate edge case from POLICY.md, often with contradicting or oddly
written information, and comes with its expected answer in practice_labels.jsonl, so
`validate.py --score` can grade a run on it. Nothing here is copied from the real data:
all people, companies, notes and lists are invented. Deterministic (fixed seed).

    python stress/generate.py stress/data [instances per case, default 4] [noise customers, default 6000]
"""

import json
import os
import random
import re
import sys

RNG = random.Random(20261121)

FIRST = ["Anna", "Marek", "Greta", "Ivan", "Lucia", "Tomas", "Nadia", "Pedro", "Ingrid", "Kofi", "Amara", "Yusuf",
         "Elena", "Rafael", "Sofia", "Dmitri", "Chiara", "Oskar", "Leila", "Mateo", "Hana", "Bruno", "Zara", "Emil",
         "Farah", "Niko", "Selin", "Arjun", "Maya", "Viktor", "Olga", "Kwame", "Rosa", "Felix", "Dana", "Hugo"]
SYL_A = ["Var", "Kel", "Dro", "Mar", "Sel", "Tor", "Bal", "Fen", "Gal", "Hol", "Kast", "Lom", "Nor", "Pes", "Ruv",
         "Stav", "Tem", "Ulv", "Vel", "Wex", "Zor", "Bren", "Cav", "Dun", "Esk", "Fal", "Grim", "Jar"]
SYL_B = ["omenko", "anski", "ovic", "berg", "ström", "elli", "adze", "oglu", "ensen", "ari", "ewicz", "uelo",
         "inger", "ard", "ova", "escu", "holm", "etti", "ayev", "orff", "anu", "iak", "ezza", "undt"]
COUNTRIES = ["France", "Peru", "Kenya", "Germany", "Spain", "Poland", "Ireland", "Brazil", "Latvia", "Sweden",
             "Nigeria", "Italy", "Portugal", "Canada", "Austria", "Greece", "Chile", "Ghana", "Norway", "Romania"]
CODE = {"France": "FR", "Peru": "PE", "Kenya": "KE", "Germany": "DE", "Spain": "ES", "Poland": "PL",
        "Ireland": "IE", "Brazil": "BR", "Latvia": "LV", "Sweden": "SE", "Nigeria": "NG", "Italy": "IT",
        "Portugal": "PT", "Canada": "CA", "Austria": "AT", "Greece": "GR", "Chile": "CL", "Ghana": "GH",
        "Norway": "NO", "Romania": "RO", "Puerto Rico": "PR", "United States": "US"}
DEMONYM = {"France": "French", "Peru": "Peruvian", "Kenya": "Kenyan", "Germany": "German", "Spain": "Spanish",
           "Poland": "Polish", "Ireland": "Irish", "Brazil": "Brazilian", "Latvia": "Latvian", "Sweden": "Swedish",
           "Nigeria": "Nigerian", "Italy": "Italian", "Portugal": "Portuguese", "Canada": "Canadian",
           "Austria": "Austrian", "Greece": "Greek", "Chile": "Chilean", "Ghana": "Ghanaian",
           "Norway": "Norwegian", "Romania": "Romanian"}
CITY = {"France": "Lyon", "Peru": "Lima", "Kenya": "Nairobi", "Germany": "Leipzig", "Spain": "Valencia",
        "Poland": "Gdansk", "Ireland": "Cork", "Brazil": "Recife", "Latvia": "Riga", "Sweden": "Malmo",
        "Nigeria": "Ibadan", "Italy": "Turin", "Portugal": "Porto", "Canada": "Halifax", "Austria": "Graz",
        "Greece": "Patras", "Chile": "Valparaiso", "Ghana": "Kumasi", "Norway": "Bergen", "Romania": "Cluj"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
PROVIDERS = ["Onfido", "Veriff", "Sumsub", "Persona"]
CATEGORY = {"Sanctions": "a sanctioned individual", "PEP": "a politically exposed person",
            "Enforcement": "the subject of enforcement action", "Adverse Media": "named in adverse media",
            "SOE": "a state-owned enterprise official", "Other Segment": "named on a watchlist"}


# ------------------------------------------------------------------ date writing (deliberately mixed)

def iso(y, m, d):
    return "%04d-%02d-%02d" % (y, m, d)


def messy(y, m, d):
    """A customer-record date in one of the formats the bank's systems use."""
    form = RNG.randrange(4)
    if form == 0:
        return iso(y, m, d)
    if form == 1:
        return "%02d/%02d/%04d" % (d, m, y)
    if form == 2:
        return "%s %d, %d" % (MONTHS[m - 1], d, y)
    return "%d %s %d" % (d, MONTHS[m - 1], y)


class World:
    def __init__(self):
        self.t = {k: [] for k in ("alerts", "hits", "customers", "accounts", "onboarding", "kyc_checks", "notes",
                                  "search_results", "practice_labels")}
        self.used = set()
        self.names_used = set()
        self.emails = set()
        self.phones = set()
        self.case_of = {}
        self.note_ok = {}  # note id -> True when the note records a passed identity check

    # ---------------------------------------------------------- ids and names
    def uid(self, prefix, digits=False):
        while True:
            body = "%06d" % RNG.randrange(10 ** 6) if digits else "%06X" % RNG.randrange(16 ** 6)
            ident = "%s-%s" % (prefix, body)
            if ident not in self.used:
                self.used.add(ident)
                return ident

    def surname(self):
        while True:
            s = RNG.choice(SYL_A) + RNG.choice(SYL_B)
            if s not in self.names_used:
                self.names_used.add(s)
                return s

    def person_name(self):
        return RNG.choice(FIRST), self.surname()

    def phone(self):
        while True:
            p = "+%d %d" % (RNG.choice([33, 34, 39, 44, 48, 49, 51, 234, 254]), RNG.randrange(10 ** 8, 10 ** 9))
            if p not in self.phones:
                self.phones.add(p)
                return p

    def email(self, first, last):
        while True:
            e = "%s.%s%d@mail.example" % (first.lower(), last.lower().replace(" ", ""), RNG.randrange(10 ** 4))
            e = e.encode("ascii", "ignore").decode()
            if e not in self.emails:
                self.emails.add(e)
                return e

    # ---------------------------------------------------------- customer side
    def customer(self, full_name, dob, residence, nationality, entity="Individual", city=None, email=None,
                 phone=None, preferred=None, login=None, reg=None):
        first, last = (full_name.split() + ["x"])[:2]
        cid = self.uid("CUS", digits=True)
        row = {"record_id": cid, "entity_type": entity, "full_name": full_name, "preferred_name": preferred,
               "login_name": login, "dob": dob, "nationality": nationality, "residence_country": residence,
               "city": city, "address": ("%d Station Road, %s" % (RNG.randrange(1, 200), city)) if city else None,
               "email": email or self.email(first, last), "phone": phone or self.phone(),
               "occupation": RNG.choice(["Teacher", "Engineer", "Driver", "Nurse", "Analyst", None]),
               "employer": None, "registration_number": reg if entity != "Individual" else None,
               "onboarded_at": "2023-0%d-1%d" % (RNG.randrange(1, 9), RNG.randrange(10)), "status": "active",
               "last_kyc_refresh": "2024-01-15"}
        if entity != "Individual" and not reg:
            row["registration_number"] = "RC%07d" % RNG.randrange(10 ** 7)
        self.t["customers"].append(row)
        return row

    def account(self, cids, login=None):
        acc = self.uid("ACC")
        rows = []
        for cid in cids:
            rows.append({"account_id": acc, "customer_id": cid, "login_handle": login, "phone": self.phone(),
                         "opened_at": "2022-05-01", "status": "active"})
        self.t["accounts"].extend(rows)
        apps = {}
        for cid in cids:
            app = self.uid("APP")
            self.t["onboarding"].append({"account_id": acc, "customer_id": cid, "kyc_applicant_id": app,
                                         "onboarded_at": "2022-05-01"})
            apps[cid] = app
        return acc, apps

    def check(self, app, name, dob, country, result="approved", when="2024-03-10", doc="passport", number=None,
              provider=None):
        kid = self.uid("KYC")
        self.t["kyc_checks"].append({
            "check_id": kid, "applicant_id": app, "provider": provider or RNG.choice(PROVIDERS),
            "verified_name": name, "verified_dob": dob, "document_type": doc,
            "document_number": number or ("P%08d" % RNG.randrange(10 ** 8)), "document_country": country,
            "result": result, "checked_at": when})
        return kid

    def note(self, email, text, channel="kyc", when="2024-06-01", approved=None):
        nid = self.uid("NOTE")
        self.note_ok[nid] = (channel == "kyc") if approved is None else approved
        self.t["notes"].append({"note_id": nid, "requester_email": email, "created_at": when, "channel": channel,
                                "text": text})
        return nid

    # ---------------------------------------------------------- watchlist side
    def hit(self, name, entity="Individual", aliases=(), dobs=(), countries=(), nationality=(), citizenship=(),
            addresses=(), pob=None, category="PEP", notes=None, life="Alive", death=None):
        hid = self.uid("H")
        self.t["hits"].append({
            "hit_id": hid, "alert_id": None, "match_score": RNG.choice([90, 100]), "entity_type": entity,
            "name": name, "first_name": None, "middle_name": None, "last_name": None, "best_matching_name": None,
            "aliases": list(aliases), "gender": None, "dobs": list(dobs), "place_of_birth": pob,
            "life_status": life, "deceased_date": death, "nationality": list(nationality),
            "citizenship": list(citizenship), "countries": list(countries), "addresses": list(addresses),
            "identifiers": [], "categories": [category] if category else [], "primary_category": category,
            "subcategories": [], "position_or_allegation": None, "details": {}, "relationships": [],
            "profile_notes": notes, "sources": [], "source_urls": []})
        return hid

    def search(self, name, snippet):
        sid = self.uid("SR")
        query = " ".join(sorted(name.lower().replace(",", " ").split()))
        self.t["search_results"].append({"result_id": sid, "query": query, "title": "%s - report" % name,
                                         "snippet": snippet, "source": "newsdesk.example"})
        return sid

    def alert(self, hit_ids, name, dob=None, country=None, city=None, entity="Individual", account=None,
              login=None, email=None, screened="2026-11-10", string_ids=False):
        aid = self.uid("A")
        for h in self.t["hits"]:
            if h["hit_id"] in hit_ids:
                h["alert_id"] = aid
        self.t["alerts"].append({"alert_id": aid, "screened_at": screened,
                                 "customer": {"name": name, "entity_type": entity, "dob": dob, "country": country,
                                              "city": city},
                                 "account_ref": account, "login_handle": login, "email": email,
                                 "hit_ids": "; ".join(hit_ids) if string_ids else list(hit_ids)})
        return aid

    def label(self, hit_id, decision, codes, records, case, why, sr=None, ev=None):
        """The intended outcome of a hit. Its evidence is filled in by finish(), once every record exists."""
        alert_id = next(h["alert_id"] for h in self.t["hits"] if h["hit_id"] == hit_id)
        self.t["practice_labels"].append({"hit_id": hit_id, "alert_id": alert_id, "decision": decision,
                                          "reason_codes": codes, "customer_record_ids": records, "evidence": ev,
                                          "reference_reason": why, "path": ["stress case: " + case],
                                          "_sr": list(sr or [])})
        self.case_of[hit_id] = case

    # ---------------------------------------------------------- intended evidence (POLICY.md principle 9)
    def finish(self):
        """Fill every label's evidence with the records that should decide it, as POLICY.md asks:
        the deciding records only, customer side, plus a search result when it gave the hit's birth date."""
        cus = {c["record_id"]: c for c in self.t["customers"]}
        apps = {}
        for o in self.t["onboarding"]:
            apps.setdefault(o["customer_id"], []).append(o["kyc_applicant_id"])
        checks = {}
        for k in self.t["kyc_checks"]:
            if k["result"] == "approved":
                checks.setdefault(k["applicant_id"], []).append(k)
        notes = {}
        for n in self.t["notes"]:
            if n["channel"] == "kyc" and self.note_ok.get(n["note_id"]):
                notes.setdefault(n["requester_email"].lower(), []).append(n)

        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "system"))
        import countries as cn  # the policy's country rules (codes, nationality words, territories)
        hits = {h["hit_id"]: h for h in self.t["hits"]}
        all_notes = {}
        for n in self.t["notes"]:
            all_notes.setdefault(n["requester_email"].lower(), []).append(n)

        def hit_countries(h):
            out = set()
            for f in ("countries", "nationality", "citizenship"):
                out.update(cn.canon(v) for v in h.get(f) or [])
            for a in h.get("addresses") or []:
                out.add(cn.canon(re.sub(r"\[[^\]]*\]", "", a).strip().split(",")[-1]))
            if h.get("place_of_birth"):
                out.add(cn.canon(h["place_of_birth"].split(",")[-1]))
            return out - {None}

        def shared_country_sources(cids, hit_id):
            ties = {}
            for cid in cids:
                c = cus[cid]
                for f in ("residence_country", "nationality"):
                    if cn.canon(c.get(f)):
                        ties.setdefault(cn.canon(c.get(f)), set()).add(cid)
                for app in apps.get(cid, []):
                    for k in checks.get(app, []):
                        if cn.canon(k.get("document_country")):
                            ties.setdefault(cn.canon(k["document_country"]), set()).add(k["check_id"])
                for n in all_notes.get((c["email"] or "").lower(), []):
                    if n["channel"] == "kyc" and not self.note_ok.get(n["note_id"]):
                        continue
                    for country in cn.find_in_text(n["text"]):
                        ties.setdefault(country, set()).add(n["note_id"])
            shared = set(ties) & hit_countries(hits[hit_id])
            return sorted({i for country in shared for i in ties[country]})

        def facts(cids):  # approved checks and KYC notes of the person, newest first
            out = []
            for cid in cids:
                for app in apps.get(cid, []):
                    out += [(k["checked_at"], k["check_id"]) for k in checks.get(app, [])]
                out += [(n["created_at"], n["note_id"]) for n in notes.get((cus[cid]["email"] or "").lower(), [])]
            return sorted(set(out), reverse=True)

        for lab in self.t["practice_labels"]:
            sr = lab.pop("_sr")
            if lab["evidence"] is not None:
                lab["evidence"] = sorted(set(lab["evidence"]))
                continue
            cids = [c for c in lab["customer_record_ids"] if c in cus]
            if not cids:
                lab["evidence"] = []
                continue
            f = facts(cids)
            src = [f[0][1]] if f else []
            dob_src = src or [c for c in cids if cus[c].get("dob")]
            code = lab["reason_codes"][0]
            if code in ("DOB_MISMATCH", "TEMPORAL_IMPOSSIBILITY"):
                ev = dob_src + sr
            elif code == "NAME_MISMATCH_VERIFIED":
                ev = src
            elif code == "LOCATION_MISMATCH":
                ev = cids + [i for _, i in f]
            elif code in ("ENTITY_TYPE_MISMATCH", "DECEASED"):
                ev = cids[:1]
            else:
                # A match or a review cites what ties the shared country (a record, a check, a note);
                # with no shared country, the person's records and their newest check.
                tied = shared_country_sources(cids, lab["hit_id"])
                base = tied or (cids + src)
                if code in ("NAME_DOB_LOCATION_MATCH", "NAME_DOB_MATCH"):
                    ev = base + dob_src + sr
                else:  # NAME_LOCATION_MATCH, INSUFFICIENT_DATA
                    ev = base
            lab["evidence"] = sorted(set(ev))


# ------------------------------------------------------------------ building blocks

def kyc_note_text(name, y, m, d, country, provider, when):
    """Onboarding notes written in wordings the development set does not use."""
    forms = [
        "Identity confirmed. Holder: {N}; born {D}; document: {C} passport; provider {P}; reference date {W}.",
        "{P} returned a PASS for {N} ({D}) against a {C} national ID on {W}.",
        "Docs OK per {P} ({W}) - {N}, DOB {D}, issuing state {C}.",
        "KYC refresh {W}: {P} matched the selfie and the {C} driving licence of {N}, date of birth {D}.",
        "Onboarding analyst: {N}'s {C} passport cleared {P} checks on {W}; birth date on the document is {D}.",
    ]
    dates = [iso(y, m, d), "%d %s %d" % (d, MONTHS[m - 1], y), "%s %d, %d" % (MONTHS[m - 1], d, y),
             "%02d/%02d/%04d" % (d, m, y)]
    return RNG.choice(forms).format(N=name.upper(), D=RNG.choice(dates), C=country, P=provider, W=when)


def person(w, country=None, dob=None, linked="account", verified=True, verified_name=None, note_kyc=False,
           record_name=None, extra_nationality=None, dob_format=True):
    """A customer with a link from the alert; returns dict with ids and facts."""
    first, last = w.person_name()
    full = record_name or "%s %s" % (first, last)
    country = country or RNG.choice(COUNTRIES)
    y, m, d = dob or (RNG.randrange(1950, 2000), RNG.randrange(1, 13), RNG.randrange(1, 28))
    residence = RNG.choice([country, CODE.get(country, country)])
    nat = extra_nationality or RNG.choice([country, DEMONYM.get(country, country)])
    c = w.customer(full, messy(y, m, d) if dob_format else iso(y, m, d), residence, nat, city=CITY.get(country))
    out = {"cus": c, "first": first, "last": last, "full": full, "country": country, "dob": (y, m, d),
           "src": None, "login": None, "acc": None}
    login = (first[0] + last).lower()[:10] + str(RNG.randrange(1000, 9999))
    acc, apps = w.account([c["record_id"]], login=login)
    out["acc"], out["login"], out["app"] = acc, login, apps[c["record_id"]]
    vname = verified_name or full
    if verified and not note_kyc:
        out["src"] = w.check(apps[c["record_id"]], vname, iso(y, m, d), country)
    elif verified and note_kyc:
        out["src"] = w.note(c["email"], kyc_note_text(vname, y, m, d, country, RNG.choice(PROVIDERS), "2024-06-01"),
                            when="2024-06-01")
    return out


def link_kwargs(p, how=None):
    how = how or RNG.choice(["account", "login", "email"])
    if how == "account":
        return {"account": p["acc"]}
    if how == "login":
        return {"login": p["login"]}
    return {"email": p["cus"]["email"]}


def screened(p, name=None):
    y, m, d = p["dob"]
    return {"name": name or p["full"], "dob": iso(y, m, d)}


def other_country(c):
    return RNG.choice([x for x in COUNTRIES if x != c])


# ------------------------------------------------------------------ the cases

def c_dob_listed(w):
    p = person(w, note_kyc=RNG.random() < .5)
    y, m, d = p["dob"]
    h = w.hit("%s, %s" % (p["last"], p["first"]), dobs=[iso(y - 3 - RNG.randrange(5), m, d)], countries=[p["country"]])
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "DIFFERENT_ENTITY", ["DOB_MISMATCH"], [p["cus"]["record_id"]], "dob_listed",
            "Verified birth date is more than a year from the hit's only listed date.")


def c_dob_age_sentence(w):
    p = person(w)
    y = p["dob"][0]
    age, year = 52, y - 12 + 52  # the hit is about 12-13 years older
    forms = ["Prosecutors charged {N} in May {Y}; the defendant was {A} at the time.",
             "At {A}, {N} was sentenced in May {Y}.",
             "{N} (aged {A}) appeared in court in May {Y}.",
             "Court filings from May {Y} describe {N} as a {A}-year-old businessman."]
    text = RNG.choice(forms).format(N="%s %s" % (p["first"], p["last"]), A=age, Y=year)
    h = w.hit("%s %s" % (p["first"], p["last"]), countries=[p["country"]], category="Enforcement",
              notes=text + "\n\nCareer:\nDirector of an import company (2001 - 2009).")
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "DIFFERENT_ENTITY", ["DOB_MISMATCH"], [p["cus"]["record_id"]], "dob_age_sentence",
            "An age at a date in the first profile paragraph gives two birth years, both far from the customer's.")


def c_dob_one_year_off(w):
    p = person(w)
    y = p["dob"][0]
    h = w.hit("%s %s" % (p["first"], p["last"]), dobs=[str(y + 1)], countries=[p["country"]])
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [p["cus"]["record_id"]], "dob_one_year_off",
            "One year apart is neither a contradiction (needs more than 1) nor agreement.")


def c_dob_same_year_other_month(w):
    p = person(w)
    y, m, d = p["dob"]
    h = w.hit("%s %s" % (p["first"], p["last"]), dobs=["%04d-%02d" % (y, m % 12 + 1)], countries=[p["country"]])
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "DIFFERENT_ENTITY", ["DOB_MISMATCH"], [p["cus"]["record_id"]], "dob_same_year_other_month",
            "Same birth year but both give a month, and the months differ.")


def c_dob_one_of_many_agrees(w):
    p = person(w)
    y, m, d = p["dob"]
    h = w.hit("%s %s" % (p["first"], p["last"]), dobs=[str(y - 20), iso(y, m, d), str(y + 15)],
              countries=[p["country"]], category="Sanctions")
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "SAME_ENTITY", ["NAME_DOB_LOCATION_MATCH", "NAME_DOB_MATCH"], [p["cus"]["record_id"]],
            "dob_one_of_many_agrees", "One agreeing hit date blocks a mismatch; name, date and country agree.")


def c_temporal_child(w):
    yb = RNG.randrange(2008, 2012)
    p = person(w, dob=(yb, 6, 1))
    rep_year = yb + RNG.randrange(5, 12)
    notes = ("Subject of a fraud investigation.\n\nAccording to the Daily Ledger; March 3, %d: %s %s was named in a "
             "fraud inquiry.\nAccording to Court Watch; %d: further hearing." % (rep_year, p["first"], p["last"],
                                                                               rep_year + 2))
    h = w.hit("%s %s" % (p["first"], p["last"]), countries=[p["country"]], category="Adverse Media", notes=notes)
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "DIFFERENT_ENTITY", ["TEMPORAL_IMPOSSIBILITY"], [p["cus"]["record_id"]], "temporal_child",
            "The customer was under 16 at the earliest dated report.")


def c_temporal_exactly_16(w):
    yb = RNG.randrange(1995, 2005)
    p = person(w, dob=(yb, 3, 1))
    notes = "According to the Daily Ledger; March 3, %d: %s %s was questioned." % (yb + 16, p["first"], p["last"])
    h = w.hit("%s %s" % (p["first"], p["last"]), countries=[p["country"]], category="Adverse Media", notes=notes)
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [p["cus"]["record_id"]], "temporal_exactly_16",
            "The customer had just turned 16 at the report: no contradiction, and no hit birth date.")


def c_temporal_wrong_format(w):
    yb = RNG.randrange(2008, 2012)
    p = person(w, dob=(yb, 6, 1))
    rep = yb + 6
    notes = RNG.choice([
        "According to the Daily Ledger; %d: %s %s was named in a fraud inquiry." % (rep, p["first"], p["last"]),
        "According to the Daily Ledger; March %d: %s %s was named." % (rep, p["first"], p["last"]),
        "Daily Ledger reported on March 3, %d that %s %s was named in a fraud inquiry." % (rep, p["first"],
                                                                                          p["last"]),
        "Según el Daily Ledger; marzo 3, %d: %s %s fue nombrado." % (rep, p["first"], p["last"])])
    h = w.hit("%s %s" % (p["first"], p["last"]), countries=[p["country"]], category="Adverse Media", notes=notes)
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [p["cus"]["record_id"]], "temporal_wrong_format",
            "Only 'According to ...; Month d, yyyy:' lines are dated reports; this one is not.")


def c_company_incorporated_after(w):
    last = w.surname()
    country = RNG.choice(COUNTRIES)
    inc = RNG.randrange(2018, 2023)
    c = w.customer("%s Logistics Ltd" % last, iso(inc, 4, 2), country, country, entity="Company",
                   city=CITY[country])
    acc, apps = w.account([c["record_id"]])
    w.check(apps[c["record_id"]], "%s Logistics Limited" % last, iso(inc, 4, 2), country, doc="registry extract",
            provider="Companies registry lookup", number=None)
    notes = "According to Trade Monitor; January 5, %d: %s Logistics was fined for customs fraud." % (inc - 4, last)
    h = w.hit("%s Logistics" % last, entity="Company", countries=[country], category="Enforcement", notes=notes)
    w.alert([h], name=c["full_name"], entity="Company", account=acc)
    w.label(h, "DIFFERENT_ENTITY", ["TEMPORAL_IMPOSSIBILITY"], [c["record_id"]], "company_incorporated_after",
            "The company was incorporated after the earliest dated report.")


def c_deceased(w):
    p = person(w)
    early = RNG.random() < .6
    death = "%d-%02d" % (RNG.randrange(2015, 2024), RNG.randrange(1, 13)) if early else "2026-11"
    h = w.hit("%s %s" % (p["first"], p["last"]), countries=[p["country"]], life="Deceased", death=death)
    w.alert([h], **screened(p), **link_kwargs(p), screened="2026-11-10")
    if early:
        w.label(h, "DIFFERENT_ENTITY", ["DECEASED"], [p["cus"]["record_id"]], "deceased_before",
                "The hit died before the customer was screened.")
    else:
        w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [p["cus"]["record_id"]], "deceased_same_month",
                "Death in the same month as screening cannot be ordered: unknown.")


def c_location(w):
    p = person(w)
    far = other_country(p["country"])
    h = w.hit("%s %s" % (p["first"], p["last"]), countries=[far], category="PEP")
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "DIFFERENT_ENTITY", ["LOCATION_MISMATCH"], [p["cus"]["record_id"]], "location",
            "Every country tied to the customer differs from every country tied to the hit.")


def c_location_but_name_dob_agree(w):
    p = person(w)
    far = other_country(p["country"])
    h = w.hit("%s %s" % (p["first"], p["last"]), countries=[far], dobs=[str(p["dob"][0])])
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [p["cus"]["record_id"]], "location_but_name_dob_agree",
            "A country difference does not clear a hit whose name and date of birth agree.")


def c_location_lived_note(w):
    p = person(w)
    far = other_country(p["country"])
    w.note(p["cus"]["email"], RNG.choice([
        "Customer mentioned they spent 2008-2012 working in %s before relocating." % far,
        "Caller explained the old address was from their years in %s (2005 to 2011)." % far,
        "Support chat: user previously lived in %s and asked about closing an old account there." % far]),
        channel=RNG.choice(["phone", "email", "app"]))
    h = w.hit("%s %s" % (p["first"], p["last"]), countries=[far])
    w.alert([h], **screened(p), **link_kwargs(p, "email"))
    w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [p["cus"]["record_id"]], "location_lived_note",
            "A support note ties the customer to the hit's country, so location cannot clear.")


def c_location_territory(w):
    first, last = w.person_name()
    c = w.customer("%s %s" % (first, last), iso(1980, 1, 9), "PR", "Puerto Rico", city="San Juan")
    acc, apps = w.account([c["record_id"]])
    w.check(apps[c["record_id"]], "%s %s" % (first, last), iso(1980, 1, 9), "Puerto Rico")
    h = w.hit("%s %s" % (first, last), countries=["United States"])
    w.alert([h], name=c["full_name"], dob=iso(1980, 1, 9), account=acc)
    w.label(h, "DIFFERENT_ENTITY", ["LOCATION_MISMATCH"], [c["record_id"]], "location_territory",
            "Territories count as their own country: Puerto Rico is not United States.")


def c_location_codes_match(w):
    p = person(w)
    y, m, d = p["dob"]
    country = p["country"]
    h = w.hit("%s, %s" % (p["last"], p["first"]), nationality=[DEMONYM.get(country, country)], dobs=[iso(y, m, d)],
              addresses=["%d Main Street, %s, %s [Residency]" % (RNG.randrange(1, 90), CITY[country], country)])
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "SAME_ENTITY", ["NAME_DOB_LOCATION_MATCH", "NAME_DOB_MATCH"], [p["cus"]["record_id"]],
            "location_codes_match", "Codes and nationality words are countries; name, date and country agree.")


def c_name_verified_mismatch(w):
    other_first, other_last = w.person_name()
    p = person(w, verified_name="%s %s" % (other_first, other_last), note_kyc=RNG.random() < .5)
    h = w.hit("%s %s" % (p["first"], p["last"]), countries=[p["country"]])
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "DIFFERENT_ENTITY", ["NAME_MISMATCH_VERIFIED"], [p["cus"]["record_id"]], "name_verified_mismatch",
            "The record name matches the hit but the verified legal name does not.")


def c_rejected_newer_check(w):
    p = person(w)
    other_first, other_last = w.person_name()
    w.check(p["app"], "%s %s" % (other_first, other_last), iso(1999, 1, 1), other_country(p["country"]),
            result=RNG.choice(["rejected", "expired"]), when="2025-09-09")
    h = w.hit("%s %s" % (p["first"], p["last"]), countries=[p["country"]], dobs=[str(p["dob"][0])])
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "SAME_ENTITY", ["NAME_DOB_LOCATION_MATCH", "NAME_DOB_MATCH"], [p["cus"]["record_id"]],
            "rejected_newer_check", "A newer rejected/expired check with other details is not evidence.")


def c_rejected_note(w):
    p = person(w, verified=False)
    other_first, other_last = w.person_name()
    w.note(p["cus"]["email"], RNG.choice([
        "Veriff could not verify %s %s: the passport had expired. Customer asked to resubmit." % (
            other_first.upper(), other_last.upper()),
        "Onfido check FAILED for %s %s on 2024-02-02 (document unreadable)." % (other_first.upper(),
                                                                              other_last.upper())]),
        approved=False)
    h = w.hit("%s %s" % (p["first"], p["last"]), countries=[p["country"]])
    w.alert([h], **screened(p), **link_kwargs(p, "email"))
    w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [p["cus"]["record_id"]], "rejected_note",
            "A failed check written as a note is not a verified name.")


def c_placeholder_name(w):
    last = w.surname()
    p = person(w, record_name="N/A %s" % last, verified_name="%s N/A" % last.upper())
    y, m, d = p["dob"]
    h = w.hit("%s, %s" % (last, RNG.choice(FIRST)), dobs=[iso(y, m, d)], countries=[p["country"]])
    w.alert([h], name="N/A %s" % last, dob=iso(y, m, d), **link_kwargs(p))
    w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [p["cus"]["record_id"]], "placeholder_name",
            "Fewer than two real name parts: neither confirmed nor cleared on name.")


def c_entity_type(w):
    last = w.surname()
    country = RNG.choice(COUNTRIES)
    c = w.customer("%s Trading Ltd" % last, iso(2010, 5, 5), country, country, entity="Company", city=CITY[country])
    acc, apps = w.account([c["record_id"]])
    h = w.hit("%s Trading" % last, entity="Individual", countries=[country])
    w.alert([h], name=c["full_name"], entity="Company", account=acc)
    w.label(h, "DIFFERENT_ENTITY", ["ENTITY_TYPE_MISMATCH"], [c["record_id"]], "entity_type",
            "The customer is a company and the hit is a person.")


def c_name_variants_joined(w):
    last = w.surname()
    full = "Anthony Abdulsalam %s" % last
    p = person(w, record_name=full)
    y, m, d = p["dob"]
    h = w.hit("Abdul Salam %s, Tony" % last, dobs=[str(y)], countries=[p["country"]])
    w.alert([h], name="Tony %s" % last, dob=iso(y, m, d), **link_kwargs(p))
    w.label(h, "SAME_ENTITY", ["NAME_DOB_LOCATION_MATCH", "NAME_DOB_MATCH"], [p["cus"]["record_id"]],
            "name_variants_joined", "Tony = Anthony (variants), Abdulsalam = Abdul Salam (written together).")


def c_name_typo_long(w):
    first = RNG.choice(["Katarzyna", "Alexandra", "Valentina", "Christopher"])
    last = w.surname() + "ska"
    p = person(w, record_name="%s %s" % (first, last))
    typo = first[:3] + first[4] + first[3] + first[5:]  # a swap: two edits on a long part
    y, m, d = p["dob"]
    h = w.hit("%s %s" % (typo, last), dobs=[iso(y, m, d)], countries=[p["country"]])
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "SAME_ENTITY", ["NAME_DOB_LOCATION_MATCH", "NAME_DOB_MATCH"], [p["cus"]["record_id"]],
            "name_typo_long", "A swap is two edits, allowed for parts of seven or more letters.")


def c_name_swap_short(w):
    first = RNG.choice(["Maria", "Elena", "Pablo", "Irena"])
    last = RNG.choice(["Lopez", "Novak", "Rossi", "Silva"])
    p = person(w, record_name="%s %s" % (first, last))
    h = w.hit("%s %s" % (first[0] + first[2] + first[1] + first[3:], last[0] + last[2] + last[1] + last[3:]),
              countries=[p["country"]])
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "DIFFERENT_ENTITY", ["NAME_MISMATCH_VERIFIED"], [p["cus"]["record_id"]], "name_swap_short",
            "A swap is two edits; parts of 4-6 letters allow only one, so the verified name agrees with nothing.")


def c_name_short_parts(w):
    last = w.surname()
    p = person(w, record_name="Li %s" % last)
    h = w.hit("Le %s" % last, countries=[p["country"]])
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "DIFFERENT_ENTITY", ["NAME_MISMATCH_VERIFIED"], [p["cus"]["record_id"]], "name_short_parts",
            "Parts under four letters must be identical: Li is not Le, so only one part matches.")


def c_single_alias(w):
    last = w.surname()
    p = person(w, record_name="%s %s" % (RNG.choice(FIRST), last))
    h = w.hit("%s %s" % (RNG.choice(FIRST), last), aliases=["%s (AKA)" % last], countries=[p["country"]])
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [p["cus"]["record_id"]], "single_alias",
            "A one-part alias agrees with the customer's name, so the verified name does not clear the hit.")


def c_name_dob_no_country(w):
    p = person(w)
    y, m, d = p["dob"]
    h = w.hit("%s %s" % (p["first"], p["last"]), dobs=["%04d-%02d" % (y, m)])
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "SAME_ENTITY", ["NAME_DOB_MATCH"], [p["cus"]["record_id"]], "name_dob_no_country",
            "Name and date agree; the hit gives no country.")


def c_company_match(w):
    last = w.surname()
    country = RNG.choice(COUNTRIES)
    c = w.customer("%s Ferro Limited" % last, iso(2004, 2, 2), CODE[country], country, entity="Company",
                   city=CITY[country])
    acc, apps = w.account([c["record_id"]])
    k = w.check(apps[c["record_id"]], "%s Ferro Ltd" % last, iso(2004, 2, 2), country, doc="registry extract",
                provider="Companies registry lookup")
    h = w.hit("%s FERRO LTD." % last.upper(), entity="Company", countries=[country], category="SOE")
    w.alert([h], name=c["full_name"], entity="Company", email=c["email"])
    w.label(h, "SAME_ENTITY", ["NAME_LOCATION_MATCH"], [c["record_id"]], "company_match",
            "Legal forms are not name parts; name and country agree.")


def c_company_other_country(w):
    last = w.surname()
    country = RNG.choice(COUNTRIES)
    c = w.customer("%s Ferro GmbH" % last, iso(2004, 2, 2), country, country, entity="Company", city=CITY[country])
    acc, apps = w.account([c["record_id"]])
    far = other_country(country)
    h = w.hit("%s Ferro SA" % last, entity="Company", countries=[far],
              addresses=["Avenida Central 4, %s, %s [Business]" % (CITY[far], far)])
    w.alert([h], name=c["full_name"], entity="Company", account=acc)
    w.label(h, "DIFFERENT_ENTITY", ["LOCATION_MISMATCH"], [c["record_id"]], "company_other_country",
            "Same company name, different countries.")


def snippet(name, age, month, year, phrase, country):
    return RNG.choice([
        "Officials in {C} flagged {N}, then {A}, as {P} in a notice dated {M} {Y}.",
        "{M} {Y}: {N} ({A}) is listed as {P}, according to a {C} registry.",
        "A {C} bulletin from {M} {Y} describes {N}, age {A}, as {P}.",
        "{N}, {A}, was {P} in {C}, per records published {M} {Y}.",
    ]).format(N=name, A=age, M=month, Y=year, P=phrase, C=country)


def c_search(w, agree):
    p = person(w)
    country = p["country"]
    y = p["dob"][0]
    cat = RNG.choice(list(CATEGORY))
    name = "%s %s" % (p["first"], p["last"])
    year = min(2025, y + RNG.randrange(30, 45))
    age = year - y if agree else year - y - 12
    sid = w.search(name, snippet(name, age, RNG.choice(MONTHS), year, CATEGORY[cat], country))
    # namesakes: wrong listing phrase, or wrong country, with other ages
    w.search(name, "%s, %d, a %s in %s, was interviewed by a local newspaper in %s %d." % (
        name, age + 9, RNG.choice(["nurse", "taxi driver", "teacher"]), country, RNG.choice(MONTHS), year))
    w.search(name, snippet(name, age - 7, RNG.choice(MONTHS), year, CATEGORY[cat], other_country(country)))
    h = w.hit(name, countries=[country], category=cat)
    w.alert([h], **screened(p), **link_kwargs(p))
    if agree:
        w.label(h, "SAME_ENTITY", ["NAME_DOB_LOCATION_MATCH", "NAME_DOB_MATCH"], [p["cus"]["record_id"]],
                "search_agree", "Only the result with the hit's listing phrase and country counts: it agrees.",
                sr=[sid])
    else:
        w.label(h, "DIFFERENT_ENTITY", ["DOB_MISMATCH"], [p["cus"]["record_id"]], "search_mismatch",
                "The qualifying search result puts the hit's birth 11-12 years after the customer's.", sr=[sid])


def c_search_only_namesakes(w):
    p = person(w)
    name = "%s %s" % (p["first"], p["last"])
    country = p["country"]
    w.search(name, "%s, 71, a retired teacher in %s, won a local award in May 2015." % (name, country))
    w.search(name, snippet(name, 70, "May", 2015, "a politically exposed person", other_country(country)))
    h = w.hit(name, countries=[country], category="PEP")
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [p["cus"]["record_id"]], "search_only_namesakes",
            "No search result is about the hit, so there is no birth date to compare.")


def c_joint_account(w):
    country = RNG.choice(COUNTRIES)
    fa, la = w.person_name()
    fb = RNG.choice([f for f in FIRST if f != fa])
    a = w.customer("%s %s" % (fa, la), iso(1985, 5, 5), country, country, city=CITY[country])
    b = w.customer("%s %s" % (fb, la), iso(1962, 8, 8), country, country, city=CITY[country])
    acc, apps = w.account([a["record_id"], b["record_id"]])
    w.check(apps[a["record_id"]], a["full_name"], iso(1985, 5, 5), country)
    kb = w.check(apps[b["record_id"]], b["full_name"], iso(1962, 8, 8), country)
    h = w.hit("%s %s" % (fb, la), dobs=["1985"], countries=[country])
    w.alert([h], name="%s %s" % (fb, la), dob=iso(1962, 8, 8), account=acc)
    w.label(h, "DIFFERENT_ENTITY", ["DOB_MISMATCH"], [b["record_id"]], "joint_account",
            "Joint account: the screened name and date pick holder B, whose birth date contradicts the hit.")


def c_merge_phone_dob(w):
    p = person(w)
    far = other_country(p["country"])
    y, m, d = p["dob"]
    twin = w.customer(p["full"], messy(y, m, d), far, far, phone=p["cus"]["phone"], city=CITY[far])
    h = w.hit("%s %s" % (p["first"], p["last"]), countries=[far])
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [p["cus"]["record_id"], twin["record_id"]], "merge_phone_dob",
            "An old record with the same phone and full date of birth is the same person and ties them to the "
            "hit's country.")


def c_family_phone(w):
    p = person(w)
    far = other_country(p["country"])
    w.customer("%s %s" % (RNG.choice(FIRST), p["last"]), iso(p["dob"][0] - 25, 1, 1), far, far,
               phone=p["cus"]["phone"], city=CITY[far])
    h = w.hit("%s %s" % (p["first"], p["last"]), countries=[far])
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "DIFFERENT_ENTITY", ["LOCATION_MISMATCH"], [p["cus"]["record_id"]], "family_phone",
            "A shared phone alone (a parent) does not merge; the customer's own countries differ from the hit's.")


def c_merge_document(w):
    p = person(w)
    far = other_country(p["country"])
    number = "D%08d" % RNG.randrange(10 ** 8)
    w.t["kyc_checks"] = [k for k in w.t["kyc_checks"] if k["check_id"] != p["src"]]
    w.check(p["app"], p["full"], iso(*p["dob"]), p["country"], number=number)
    alt = w.customer(p["full"], None, far, far, city=CITY[far])
    acc2, apps2 = w.account([alt["record_id"]])
    w.check(apps2[alt["record_id"]], p["full"], iso(*p["dob"]), far, number=number, when="2023-01-01")
    h = w.hit("%s %s" % (p["first"], p["last"]), countries=[far])
    w.alert([h], **screened(p), **link_kwargs(p, "account"))
    w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [p["cus"]["record_id"], alt["record_id"]], "merge_document",
            "Two records whose approved checks carry the same document number are one person.")


def c_search_ambiguous(w, same):
    last = w.surname()
    first = RNG.choice(FIRST)
    country = RNG.choice(COUNTRIES)
    a = w.customer("%s %s" % (first, last), iso(1970, 1, 1), country, country, city=CITY[country])
    b = w.customer("%s %s" % (first, last), iso(1990, 9, 9), country, country, city=CITY[country])
    acc_a, apps_a = w.account([a["record_id"]])
    acc_b, apps_b = w.account([b["record_id"]])
    if same:
        far = other_country(country)
        h = w.hit("%s %s" % (first, last), countries=[far])
        w.alert([h], name="%s %s" % (first, last), country=country)
        w.label(h, "DIFFERENT_ENTITY", ["LOCATION_MISMATCH"], ["AMBIGUOUS"], "search_two_people_same",
                "Two people fit; both are cleared on location, so the decision stands with AMBIGUOUS.",
                ev=[a["record_id"], b["record_id"]])
    else:
        other_first, other_last = w.person_name()
        k = w.check(apps_a[a["record_id"]], "%s %s" % (other_first, other_last), iso(1970, 1, 1), country)
        h = w.hit("%s %s" % (first, last), countries=[country])
        w.alert([h], name="%s %s" % (first, last), country=country)
        w.label(h, "REVIEW", ["AMBIGUOUS_CUSTOMER_RECORD"], ["AMBIGUOUS"], "search_two_people_differ",
                "Two people fit; one is cleared on verified name, the other cannot be decided.",
                ev=[a["record_id"], b["record_id"], k])


def c_nobody_fits(w):
    h = w.hit("%s %s" % (RNG.choice(FIRST), w.surname()), countries=[RNG.choice(COUNTRIES)])
    w.alert([h], name="Zebulon %s" % w.surname(), dob="1977-07-07", country="Chile")
    w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [None], "nobody_fits",
            "No customer record fits the screened details.")


def c_day_first_no_kyc(w):
    p = person(w, verified=False)
    y, m, d = p["dob"]
    p["cus"]["dob"] = "%02d/%02d/%04d" % (d, m, y)
    h = w.hit("%s %s" % (p["first"], p["last"]), dobs=[iso(y, m, d)], countries=[p["country"]])
    w.alert([h], name=p["full"], dob=iso(y, m, d), **link_kwargs(p))
    w.label(h, "SAME_ENTITY", ["NAME_DOB_LOCATION_MATCH", "NAME_DOB_MATCH"], [p["cus"]["record_id"]],
            "day_first_no_kyc", "No identity check: the record's day-first date agrees with the hit.")


def c_blank_dob(w):
    p = person(w, verified=False)
    p["cus"]["dob"] = None
    h = w.hit("%s %s" % (p["first"], p["last"]), dobs=["1950-01-01"], countries=[p["country"]])
    w.alert([h], name=p["full"], **link_kwargs(p))
    w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [p["cus"]["record_id"]], "blank_dob",
            "Missing data is unknown, never a mismatch.")


def c_accents(w):
    first = RNG.choice(["José", "Zoë", "Søren", "Paweł", "Ştefan", "Łukasz"])
    last = RNG.choice(["Álvarez", "Müller", "Øster", "Wiśniewski", "Ţepeş", "Nuñez"]) + w.surname()[:3].lower()
    p = person(w, record_name="%s %s" % (first, last))
    y, m, d = p["dob"]
    plain = (first + " " + last).translate(str.maketrans("éëøłşţáüśñŞŁÁØŢ", "eeolstausnSLAOT"))
    h = w.hit(plain, dobs=[iso(y, m, d)], countries=[p["country"]])
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "SAME_ENTITY", ["NAME_DOB_LOCATION_MATCH", "NAME_DOB_MATCH"], [p["cus"]["record_id"]],
            "accents", "Accents and letters such as ł or ø are ignored when comparing names.")


def c_multi_hit_alert(w):
    p = person(w)
    y, m, d = p["dob"]
    name = "%s %s" % (p["first"], p["last"])
    h1 = w.hit(name, dobs=[iso(y, m, d)], countries=[p["country"]])
    h2 = w.hit(name, countries=[other_country(p["country"])])
    h3 = w.hit(name, entity="Company", countries=[p["country"]])
    w.alert([h1, h2, h3], **screened(p), **link_kwargs(p), string_ids=True)
    r = [p["cus"]["record_id"]]
    w.label(h1, "SAME_ENTITY", ["NAME_DOB_LOCATION_MATCH", "NAME_DOB_MATCH"], r, "multi_hit_same", "Full match.")
    w.label(h2, "DIFFERENT_ENTITY", ["LOCATION_MISMATCH"], r, "multi_hit_location", "Other country.")
    w.label(h3, "DIFFERENT_ENTITY", ["ENTITY_TYPE_MISMATCH"], r, "multi_hit_type", "Person versus company.")


CASES = [c_dob_listed, c_dob_age_sentence, c_dob_one_year_off, c_dob_same_year_other_month, c_dob_one_of_many_agrees,
         c_temporal_child, c_temporal_exactly_16, c_temporal_wrong_format, c_company_incorporated_after, c_deceased,
         c_location, c_location_but_name_dob_agree, c_location_lived_note, c_location_territory,
         c_location_codes_match, c_name_verified_mismatch, c_rejected_newer_check, c_rejected_note,
         c_placeholder_name, c_entity_type, c_name_variants_joined, c_name_typo_long, c_name_swap_short,
         c_name_short_parts, c_single_alias, c_name_dob_no_country, c_company_match, c_company_other_country,
         lambda w: c_search(w, True), lambda w: c_search(w, False), c_search_only_namesakes, c_joint_account,
         c_merge_phone_dob, c_family_phone, c_merge_document, lambda w: c_search_ambiguous(w, True),
         lambda w: c_search_ambiguous(w, False), c_nobody_fits, c_day_first_no_kyc, c_blank_dob, c_accents,
         c_multi_hit_alert]


MID = ["ino", "eka", "ura", "oli", "ame", "isu"]


def noise_surname():
    return RNG.choice(SYL_A) + RNG.choice(MID) + RNG.choice(SYL_B)


def noise(w, count):
    """Background customers (namesake pressure for the search) and chatter notes."""
    for _ in range(count):
        country = RNG.choice(COUNTRIES)
        if RNG.random() < .1:
            c = w.customer("%s %s %s" % (noise_surname(), RNG.choice(["Holdings", "Trading", "Partners"]),
                                         RNG.choice(["Ltd", "GmbH", "SA", "Inc"])),
                           iso(RNG.randrange(1980, 2022), 1, 1), country, country, entity="Company")
        else:
            c = w.customer("%s %s" % (RNG.choice(FIRST), noise_surname()),
                           messy(RNG.randrange(1940, 2006), RNG.randrange(1, 13), RNG.randrange(1, 28)),
                           RNG.choice([country, CODE[country]]), RNG.choice([country, DEMONYM[country]]),
                           city=CITY[country])
        acc, apps = w.account([c["record_id"]])
        if RNG.random() < .7:
            w.check(apps[c["record_id"]], c["full_name"], c["dob"], country,
                    result=RNG.choice(["approved"] * 8 + ["rejected", "expired"]))
        if RNG.random() < .3:
            w.note(c["email"], RNG.choice(["Customer asked how long a transfer takes.",
                                           "Card frozen after a lost-card report; replacement sent.",
                                           "Customer requested a statement for a rental application."]),
                   channel=RNG.choice(["phone", "app", "email"]))


VARIANTS = {
    "groups": [["anthony", "antony", "tony", "antonio"], ["abdul", "abdel", "abd"], ["muhammad", "mohammed",
               "mohamed"], ["katherine", "kate", "katarzyna"], ["joseph", "josef", "joe"]],
    "company_legal_forms": ["ab", "ag", "as", "bv", "co", "company", "corp", "corporation", "gmbh", "inc", "kg",
                            "limited", "llc", "llp", "lp", "ltd", "na", "nv", "oy", "plc", "sa", "sarl", "sas", "se",
                            "sl", "spa", "srl"],
}


def main(argv):
    out = argv[1] if len(argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    per_case = int(argv[2]) if len(argv) > 2 else 4
    noise_count = int(argv[3]) if len(argv) > 3 else 6000
    w = World()
    noise(w, noise_count // 2)
    for _ in range(per_case):
        for case in CASES:
            case(w)
    noise(w, noise_count - noise_count // 2)
    w.finish()
    os.makedirs(out, exist_ok=True)
    keys = {"alerts": "alert_id", "hits": "hit_id", "customers": "record_id", "accounts": "account_id",
            "onboarding": "kyc_applicant_id", "kyc_checks": "check_id", "notes": "note_id",
            "search_results": "result_id", "practice_labels": "hit_id"}
    for name, rows in w.t.items():
        rows = list(rows)
        RNG.shuffle(rows)  # file order must not matter
        with open(os.path.join(out, name + ".jsonl"), "w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with open(os.path.join(out, "name_variants.json"), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(VARIANTS, handle, indent=2)
    with open(os.path.join(out, "cases.json"), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(w.case_of, handle, indent=1, sort_keys=True)
    counts = {k: len(v) for k, v in w.t.items()}
    print("wrote %s: %s" % (out, counts))


if __name__ == "__main__":
    main(sys.argv)

"""Customer side: find the people behind an alert and build one picture per person.

Route: links on the alert (account, login, email) -> customer records; joint
holders are picked by the screened name and date of birth. Without a link, a
search over the system of record for records that fit the screened details.
Records are merged into people by (phone + full date of birth) or by the
document number on approved identity checks, transitively.
"""

import re

import countries
from load import approved, low
from norm import find_dates, parse_date, phone_key

NEGATIVE = re.compile(r"\b(reject\w*|expire\w*|fail\w*|declin\w*|unsuccessful|denied|not (?:approved|verified|"
                      r"passed|valid)|could not|unable|mismatch\w*|invalid|suspend\w*|revok\w*)\b", re.I)
CAPS_STOP = {"ID", "DOB", "KYC", "PASSED", "OK", "AML", "PEP", "UK", "US", "USA", "UAE", "EU", "SAR",
             "CDD", "EDD", "IDV", "APPROVED", "VERIFIED", "CLEARED", "CONFIRMED", "SUCCESS", "COMPLETE", "PASS",
             "II", "III", "IV", "NFC", "MRZ"}
CAPS_TOKEN = re.compile(r"[A-ZÀ-ÖØ-ÞĀ-Ž][A-ZÀ-ÖØ-ÞĀ-Ž'’/\-]+")


def is_person(entity_type):
    return str(entity_type or "").strip().lower() in ("individual", "person", "natural person", "people")


# ------------------------------------------------------------------ KYC notes

def parse_kyc_note(note):
    """Facts from an onboarding note that records an identity check.

    Returns dict(name, dob, country, date, approved) or None if it is not a check.
    """
    text = note.get("text") or ""
    created = parse_date(note.get("created_at"))
    if NEGATIVE.search(text):
        return {"approved": False}
    # name: the longest run of capitalised words (the provider writes the legal name in capitals)
    runs, current, spans, start = [], [], [], None
    for match in re.finditer(r"\S+", text):
        token = re.sub(r"['’][sS]$", "", match.group(0).strip(",.;:()[]\"")).strip(",.;:()")
        if CAPS_TOKEN.fullmatch(token) and token not in CAPS_STOP and any(ch.isalpha() and ch.isupper() for ch in token):
            if not current:
                start = match.start()
            current.append(token)
            end = match.end()
        else:
            if current:
                runs.append((current, start, end))
            current = []
    if current:
        runs.append((current, start, end))
    name, rest = None, text
    if runs:
        best = max(runs, key=lambda r: (len(r[0]), -r[1]))
        name = " ".join(best[0])
        rest = text[:best[1]] + " " + text[best[2]:]
    dob = None
    candidates = []
    for s, e, value in find_dates(rest):
        if created and value == created:
            continue
        candidates.append(value)
    candidates = sorted(set(candidates))
    if not candidates:  # a birth year alone ("DOB 1966")
        candidates = sorted({v for s, e, v in find_dates(rest, years=True)
                             if v[1] is None and not (created and v[0] == created[0] and False)})
    if len(candidates) == 1:
        dob = candidates[0]
    found = countries.find_in_text(rest)
    country = found[0] if len(found) == 1 else None
    if not (name or dob or country):
        return None
    return {"approved": True, "name": name, "dob": dob, "country": country, "date": created,
            "countries": found}


class People:
    def __init__(self, data, rules):
        self.data = data
        self.rules = rules
        self._dob = {}
        for c in data.customers:
            self._dob[c["record_id"]] = parse_date(c.get("dob"))
        self._build_merge()
        self._build_search_index()
        self._kyc_note_cache = {}

    # -------------------------------------------------------------- merge

    def _build_merge(self):
        parent = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                if ra < rb:
                    parent[rb] = ra
                else:
                    parent[ra] = rb

        groups = {}
        for c in self.data.customers:
            dob = self._dob[c["record_id"]]
            phone = phone_key(c.get("phone"))
            if phone and dob and dob[1] and dob[2]:
                groups.setdefault(("p", phone, dob), []).append(c["record_id"])
        customers_of_app = {}
        for cid, apps in self.data.applicants_of_customer.items():
            for app in apps:
                customers_of_app.setdefault(app, set()).add(cid)
        for k in self.data.kyc:
            number = re.sub(r"[^A-Z0-9]", "", str(k.get("document_number") or "").upper())
            if number and approved(k):
                for cid in customers_of_app.get(k.get("applicant_id"), ()):
                    groups.setdefault(("d", number), []).append(cid)
        for key in sorted(groups, key=str):
            ids = sorted(set(groups[key]))
            for other in ids[1:]:
                union(ids[0], other)
        self.members = {}
        for cid in sorted(parent):
            self.members.setdefault(find(cid), []).append(cid)
        self.root = {cid: find(cid) for cid in parent}

    def person_of(self, record_id):
        root = self.root.get(record_id)
        if root is None:
            return [record_id]
        return sorted(self.members[root])

    # ------------------------------------------------------------- search

    def _build_search_index(self):
        self.index = {}
        for c in self.data.customers:
            company = not is_person(c.get("entity_type"))
            keys = set()
            for field in ("full_name", "preferred_name", "login_name"):
                for part in self.rules.parts(c.get(field), company):
                    keys.add(("x", part[:3]))
                    for g in self.rules.variant_of.get(part, ()):
                        keys.add(("g", g))
            for key in keys:
                self.index.setdefault(key, []).append(c["record_id"])

    def _name_keys(self, name, company):
        keys = set()
        for part in self.rules.parts(name, company):
            keys.add(("x", part[:3]))
            for g in self.rules.variant_of.get(part, ()):
                keys.add(("g", g))
            # joined forms: allow a screened "Abdul Salam" to find "Abdulsalam"
        parts = self.rules.parts(name, company)
        for i in range(len(parts) - 1):
            keys.add(("x", (parts[i] + parts[i + 1])[:3]))
        return keys

    def record_names(self, c):
        return [n for n in (c.get("full_name"), c.get("preferred_name"), c.get("login_name")) if n]

    def fits(self, c, screened, use_place=True):
        """Does a customer record fit the screened details (policy principle 2)?"""
        company = not is_person(screened.get("entity_type"))
        if screened.get("entity_type") and c.get("entity_type") and \
                is_person(c.get("entity_type")) != is_person(screened.get("entity_type")):
            return False
        sd = parse_date(screened.get("dob"))
        cd = self._dob.get(c["record_id"])
        if sd and cd and not dates_compatible(sd, cd):
            return False
        if use_place:
            sc = countries.canon(screened.get("country"))
            if sc:
                mine = {countries.canon(c.get("residence_country")), countries.canon(c.get("nationality"))}
                if sc not in mine:
                    return False
            city = low(screened.get("city"))
            if city:
                hay = " ".join(x for x in (low(c.get("city")), low(c.get("address"))) if x)
                if city not in hay:
                    return False
        if not screened.get("name"):
            return True
        return self.rules.any_agree([screened["name"]], self.record_names(c), company) is not None

    def search(self, screened):
        company = not is_person(screened.get("entity_type"))
        ids = set()
        for key in self._name_keys(screened.get("name"), company):
            ids.update(self.index.get(key, ()))
        return sorted(cid for cid in ids if self.fits(self.data.customer[cid], screened))

    # ----------------------------------------------------------- resolution

    def resolve(self, alert):
        """Return (route, [person]) where person = sorted list of record ids, plus anchors."""
        screened = alert.get("customer") or {}
        if not isinstance(screened, dict):
            screened = {}
        screened = dict(screened)
        for key in ("name", "entity_type", "dob", "country", "city"):
            if key not in screened and alert.get(key if key != "name" else "customer_name") is not None:
                screened[key] = alert.get(key if key != "name" else "customer_name")
        linked, routes = set(), []
        ref = alert.get("account_ref")
        if ref:
            rows = self.data.accounts_by_id.get(str(ref).strip().upper(), [])
            ids = {r["customer_id"] for r in rows if r.get("customer_id") in self.data.customer}
            if ids:
                routes.append("account_ref %s" % ref)
                linked |= ids
        handle = low(alert.get("login_handle"))
        if handle:
            rows = self.data.accounts_by_login.get(handle, [])
            ids = {r["customer_id"] for r in rows if r.get("customer_id") in self.data.customer}
            if ids:
                routes.append("login_handle %s" % alert.get("login_handle"))
                linked |= ids
        email = low(alert.get("email"))
        if email:
            ids = set(self.data.customer_by_email.get(email, []))
            if ids:
                routes.append("email")
                linked |= ids
        if linked:
            anchors = sorted(linked)
            if len(anchors) > 1:
                fit = [cid for cid in anchors if self.fits(self.data.customer[cid], screened, use_place=False)]
                if fit:
                    anchors = fit
        else:
            anchors = self.search(screened)
            routes.append("search on screened details")
        people = {}
        for cid in anchors:
            members = tuple(self.person_of(cid))
            people.setdefault(members, []).append(cid)
        out = [{"records": list(m), "anchors": sorted(a)} for m, a in sorted(people.items())]
        return routes, screened, out

    # ------------------------------------------------------------- picture

    def kyc_facts(self, record_ids):
        """All approved identity facts for a person, newest first."""
        facts = []
        seen = set()
        for cid in record_ids:
            for k in self.data.checks_of_customer(cid):
                if k["check_id"] in seen or not approved(k):
                    continue
                seen.add(k["check_id"])
                facts.append({
                    "id": k["check_id"], "date": parse_date(k.get("checked_at")),
                    "name": (k.get("verified_name") or "").strip() or None,
                    "dob": parse_date(k.get("verified_dob")),
                    "country": countries.canon(k.get("document_country")),
                })
            email = low(self.data.customer[cid].get("email"))
            for n in self.data.notes_by_email.get(email, []) if email else []:
                if n["note_id"] in seen or str(n.get("channel") or "").lower() != "kyc":
                    continue
                seen.add(n["note_id"])
                parsed = self._kyc_note_cache.get(n["note_id"])
                if parsed is None:
                    parsed = parse_kyc_note(n) or {"approved": False}
                    self._kyc_note_cache[n["note_id"]] = parsed
                if not parsed.get("approved"):
                    continue
                facts.append({"id": n["note_id"], "date": parsed.get("date"), "name": parsed.get("name"),
                              "dob": parsed.get("dob"), "country": parsed.get("country"),
                              "countries": parsed.get("countries") or []})
        facts.sort(key=lambda f: (f["date"] or (0, 0, 0), f["id"]), reverse=True)
        return facts

    def lived_notes(self, record_ids):
        """Countries the support notes say the person lived or worked in: {country: [note ids]}."""
        out = {}
        for cid in record_ids:
            email = low(self.data.customer[cid].get("email"))
            for n in self.data.notes_by_email.get(email, []) if email else []:
                if str(n.get("channel") or "").lower() == "kyc":
                    continue
                for country in countries.find_in_text(n.get("text")):
                    out.setdefault(country, set()).add(n["note_id"])
        return {k: sorted(v) for k, v in sorted(out.items())}

    def picture(self, person, screened):
        records = [self.data.customer[cid] for cid in person["records"]]
        anchor = self.data.customer[person["anchors"][0]]
        facts = self.kyc_facts(person["records"])
        pic = {"records": person["records"], "anchor": anchor["record_id"], "anchors": person["anchors"],
               "is_person": is_person(anchor.get("entity_type")),
               "facts": facts}
        latest = facts[0] if facts else None
        pic["latest_check"] = latest["id"] if latest else None
        # verified fields: newest approved check that gives each field
        for field in ("name", "dob", "country"):
            pic["verified_" + field] = None
            pic["verified_%s_src" % field] = None
            for f in facts:
                if f.get(field):
                    pic["verified_" + field] = f[field]
                    pic["verified_%s_src" % field] = f["id"]
                    break
        # dates of birth: verified wins; otherwise every declared one (all must be used)
        if pic["verified_dob"]:
            pic["dobs"] = [(pic["verified_dob"], [pic["verified_dob_src"]])]
        else:
            declared = {}
            for c in records:
                d = self._dob.get(c["record_id"])
                if d:
                    declared.setdefault(d, []).append(c["record_id"])
            pic["dobs"] = [(d, sorted(src)) for d, src in sorted(declared.items())]
        # countries tied to the person, with the records that tie them
        tied = {}
        for c in records:
            for field in ("residence_country", "nationality"):
                country = countries.canon(c.get(field))
                if country:
                    tied.setdefault(country, set()).add(c["record_id"])
        for f in facts:
            for country in sorted(set([f.get("country")] + list(f.get("countries") or [])) - {None}):
                tied.setdefault(country, set()).add(f["id"])
        for country, notes in self.lived_notes(person["records"]).items():
            tied.setdefault(country, set()).update(notes)
        pic["countries"] = {k: sorted(v) for k, v in sorted(tied.items())}
        # names: the verified legal name outranks the record and screened names
        if pic["verified_name"]:
            pic["names"] = [pic["verified_name"]]
            pic["names_src"] = [pic["verified_name_src"]]
        else:
            names = []
            for c in records:
                for n in self.record_names(c):
                    if n not in names:
                        names.append(n)
            if screened.get("name") and screened["name"] not in names:
                names.append(screened["name"])
            pic["names"] = names
            pic["names_src"] = [r["record_id"] for r in records]
        return pic


def dates_compatible(a, b):
    """Equal in every part both give."""
    if a[0] != b[0]:
        return False
    if a[1] and b[1] and a[1] != b[1]:
        return False
    if a[1] and b[1] and a[2] and b[2] and a[2] != b[2]:
        return False
    return True

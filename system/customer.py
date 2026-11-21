"""Customer resolution: who is the screened customer? (POLICY principles 2-4)

1. Follow the alert's link (account_ref, login_handle, email). When it leads
   to more than one person, the screened details pick between them.
   Without a link (or a link that leads nowhere), search the system of
   record for records that fit what was screened.
2. Merge records into one person: same phone and same full date of birth,
   or the same document number on approved identity checks; repeated until
   nothing new joins. A shared phone or address alone never merges.
3. Gather the person's facts from all their records, accounts, onboarding,
   KYC checks and notes. Every fact keeps its source record ID and its level:
   verified (approved identity check), declared (customer records, notes) or
   screened (the alert itself). The most recent approved check gives the
   verified legal name, date of birth and document country; rejected or
   expired checks are listed but never used as evidence.
4. If several distinct people still fit, all are kept and the alert is
   ambiguous.
"""

import re
from collections import namedtuple

import countries
import dates
import extract
from data import normal_document, normal_email, normal_phone
from names import simple_text

SCREENED = "screened"
DECLARED = "declared"
VERIFIED = "verified"
_LEVEL_ORDER = {VERIFIED: 0, DECLARED: 1, SCREENED: 2}

# field: legal_name, name, dob, country, city, address, entity_type
# role: what the value is (full_name, residence, nationality, document, lived...)
Fact = namedtuple("Fact", "field value level source role")

# dob_how: where the DOB came from (field, keyword, after_name, ten_year_rule, unattached, none)
Check = namedtuple("Check", "check_id date status name dob countries document kind dob_how")


def fact_sort_key(fact):
    return (fact.field, _LEVEL_ORDER.get(fact.level, 9), fact.source, fact.role, repr(fact.value))


# ---------------------------------------------------------------- notes

_NEGATIVE = re.compile(
    r"\b(reject\w*|fail\w*|unable|could ?n[o']t|cannot|can't|not (?:be )?(?:verified|approved|confirmed|accepted|valid)|"
    r"unsuccessful\w*|declin\w*|denied|invalid|mismatch\w*|refus\w*|pending|inconclusive|expired|"
    r"did ?n[o']t (?:pass|match)|was not|were not|abandon\w*|incomplete|suspected forg\w*|fraudulent)\b",
    re.I)
_IDENTITY = re.compile(
    r"\b(verif\w*|identity|passport|national id|id card|identity card|driv\w* licen[cs]e|"
    r"residence permit|kyc|document|registry|onfido|veriff|sumsub|persona|jumio|trulioo)\b", re.I)
_DOC_NUMBER = re.compile(
    r"\b(?:document|passport|licen[cs]e|id|card)\s+(?:number|no\.?|num\.?|#)\s*[:#]?\s*([A-Z0-9][A-Z0-9\-]{3,})", re.I)
_STOP_CAPS = frozenset(["DOB", "D.O.B", "ID", "KYC", "AML", "PEP", "UK", "US", "USA", "UAE", "EU",
                        "SAR", "PDF", "SMS", "OK", "IBAN", "NIN", "BVN", "SSN", "TIN"])
# Capitalised words that are never part of a person's name in a check note.
_NOT_NAME = frozenset(simple_text(w) for w in """
    persona onfido veriff sumsub jumio trulioo idnow yoti shufti signicat socure experian equifax
    lexisnexis gbg mitek incode au10tix idology regula ondato idenfy facephi onboarding
    passport national id identity card driving driver drivers licence license residence permit
    document documents verification verified verify check checks checked kyc dob registry extract
    the a an on per for of in at by via with from using as result results customer client user
    account holder subject name born date birth recorded record approved confirmed successfully
    identity completed process team note this that it its their his her they he she on upon after
    following documentation provided issued valid validated cleared accepted passed performed
    successful status outcome ctry country
""".split())
_PARTICLES = frozenset(["de", "la", "del", "da", "dos", "das", "do", "van", "von", "der", "den", "ter",
                        "bin", "binti", "al", "el", "di", "du", "le", "ibn", "y", "e", "st"])
_BEFORE_NAME = frozenset(["for", "of", "holder", "name", "named", "shows", "identifying", "verified",
                          "validated", "cleared", "approved", "confirmed", "checked", "matches",
                          "authenticated", "processed", "is", "as"])


def _caps_word(word):
    """A word written in capitals (a name in KYC notes), or a placeholder."""
    letters = [ch for ch in word if ch.isalpha()]
    if letters:
        return all(ch.isupper() for ch in letters)
    digits = "".join(ch for ch in word if ch.isdigit())
    if digits and digits == word and not 1800 <= int(digits) <= 2100:
        return True    # placeholder such as 0000 inside a name
    return False


def _name_word(word, extra_stop):
    """Capitalised (Title or CAPS) word that may be part of a person's name."""
    if _caps_word(word):
        key = simple_text(word)
        return (word.strip(".") not in _STOP_CAPS and key not in extra_stop
                and (not key or key not in _NOT_NAME))
    first = [ch for ch in word if ch.isalpha()][:1]
    if not first or not first[0].isupper():
        return False
    key = simple_text(word)
    return bool(key) and key not in _NOT_NAME and key not in extra_stop


def find_person_name(text, known_parts=None, extra_stop=frozenset()):
    """The words that name the person in a check note: (name, start, end) or None.

    Does not rely on capitals: builds runs of capitalised words (Title case
    or CAPS, digit placeholders and particles such as "de" or "van" inside),
    skipping provider, document and country words and words inside dates,
    then ranks them:
    written in capitals > shares a part with the customer's record names >
    two or more parts > right after "for", "of", "verified"... or right
    before "(", "'s" or ", born/DOB" > earlier in the text.
    """
    known = set(known_parts or ())
    # Words inside a written date ("December 15, 1962") are never name words;
    # month names elsewhere can be ("Jan Martin", "April Lewis").
    date_spans = [(start, end) for start, end, _v in dates.find_dates(text)]
    runs = []
    current = []

    def close():
        while current and current[-1][3] == "particle":
            current.pop()
        if current:
            runs.append(list(current))
        del current[:]

    for match in re.finditer(r"\S+", text):
        raw = match.group(0)
        word = raw.lstrip("(\"'[")
        start = match.start() + len(raw) - len(word)
        if word != raw:
            close()                      # an opening bracket starts a new run
        possessive = re.search(r"['\u2019]s$", word)
        core = word[:possessive.start()] if possessive else word
        cleaned = core.rstrip(",.;:)\"']!?")
        in_date = any(s <= start < e for s, e in date_spans)
        if cleaned and not in_date and _name_word(cleaned, extra_stop):
            current.append((cleaned, start, start + len(cleaned), "word"))
            if possessive or cleaned != core:
                close()                  # "SMITH's" or "Smith," ends the name
        elif cleaned and current and cleaned in _PARTICLES and cleaned == core:
            current.append((cleaned, start, start + len(cleaned), "particle"))
        else:
            close()
    close()

    best, best_key = None, None
    for run in runs:
        words = [w for w, _s, _e, kind in run if kind == "word"]
        alpha = [w for w in words if sum(1 for ch in w if ch.isalpha()) > 1]
        if not alpha:
            continue
        joined = " ".join(w for w, _s, _e, _k in run)
        if countries.lookup(joined) or (countries.find_in_text(joined) and len(alpha) == 1):
            continue                     # "United States", "Nigerian"
        caps = all(_caps_word(w) for w in words)
        shares = any(simple_text(w) in known for w in alpha)
        before = re.findall(r"[A-Za-z]+", text[max(0, run[0][1] - 20):run[0][1]])
        after = text[run[-1][2]:run[-1][2] + 12]
        anchored = (bool(before) and before[-1].lower() in _BEFORE_NAME) or bool(
            re.match(r"\s*(\(|['\u2019]s\b|,\s*(born|dob|d\.o\.b))", after, re.I))
        key = (caps, shares, len(alpha) >= 2, anchored, -run[0][1])
        if best_key is None or key > best_key:
            best, best_key = run, key
    if not best:
        return None
    return (" ".join(w for w, _s, _e, _k in best), best[0][1], best[-1][2])


def dob_from_note(text, record_date, order=None, name_end=None):
    """The date of birth written in a verification note: (how, date).

    1. "keyword": the date attached to a birth keyword (DOB, born, birth date).
       A keyword with no date near it gives ("unattached", None).
    2. "after_name": no keyword, but a date in brackets right after the
       person's name: "JACK COOPER (1951-09-10)".
    3. "ten_year_rule": no keyword at all; the single date at least 10 years
       before the note (the other dates are the check date).
    ("none", None) when nothing qualifies.
    """
    status, value = extract.anchored_dob(text, order, record_date)
    if status == "anchored":
        return ("keyword", value)
    if status == "unattached":
        return ("unattached", None)
    if name_end is not None:
        match = re.match(r"\s*[\(\[]([^\)\]]{4,30})[\)\]]", text[name_end:])
        if match:
            value = dates.parse_date(match.group(1), order)
            if value is not None and (record_date is None or dates.sort_key(value) < dates.sort_key(record_date)):
                return ("after_name", value)
    found = [v for _s, _e, v in dates.find_dates(text, order)
             if record_date is None or (v != record_date and v[0] <= record_date[0] - 10)]
    distinct = sorted(set(found), key=dates.sort_key)
    if len(distinct) == 1:
        return ("ten_year_rule", distinct[0])
    return ("none", None)


def parse_check_note(note, order=None, known_parts=None, extra_stop=frozenset()):
    """A note that records an identity check, as a Check; else None.

    order is the notes' numeric date order (see dates.detect_order);
    known_parts are the customer's record name parts and extra_stop the
    KYC provider names seen in the data (both help find the name).
    """
    text = extract.tidy(note.get("text"))
    if not text:
        return None
    channel = simple_text(note.get("channel") or "")
    record_date = dates.parse_date(note.get("created_at"))
    is_check = channel == "kyc"
    caps = find_person_name(text, known_parts, extra_stop)
    how, dob = dob_from_note(text, record_date, order, caps[2] if caps else None)
    if not is_check and not (_IDENTITY.search(text) and dob is not None):
        return None
    name = caps[0] if caps else None
    rest = text if not caps else text[:caps[1]] + " " + text[caps[2]:]
    found_countries = countries.find_in_text(rest)
    document = None
    number = _DOC_NUMBER.search(text)
    if number and any(ch.isdigit() for ch in number.group(1)):
        document = normal_document(number.group(1)) or None
    status = "rejected" if _NEGATIVE.search(text) else "approved"
    if record_date is None:
        later = [value for _s, _e, value in dates.find_dates(text, order) if value != dob]
        record_date = max(later, key=dates.sort_key) if later else None
    return Check(note["note_id"], record_date, status, name, dob, found_countries, document, "note", how)


def check_from_row(row, order=None):
    """A KYC provider row as a Check; order is the provider's DOB date order."""
    status = simple_text(row.get("result") or "")
    return Check(
        row["check_id"],
        dates.parse_date(row.get("checked_at")),
        "approved" if status == "approved" else (status or "unknown"),
        row.get("verified_name") if isinstance(row.get("verified_name"), str) else None,
        dates.parse_date(row.get("verified_dob"), order),
        countries.lookup(row.get("document_country")) or (),
        normal_document(row.get("document_number")) or None,
        "kyc",
        "field",
    )


def check_order(check):
    """Most recent first, then lowest ID."""
    return (tuple(-part for part in dates.sort_key(check.date)), check.check_id)


# ---------------------------------------------------------------- people

class Person(object):
    """One real person (or company): one or more customer records."""

    def __init__(self, record_ids, seeds):
        self.record_ids = sorted(record_ids)
        self.seeds = sorted(seeds)
        self.primary_id = self.seeds[0] if self.seeds else self.record_ids[0]
        self.facts = []
        self.checks = []          # approved checks, most recent first
        self.ignored_checks = []  # rejected / expired, never evidence
        self.verified = {}        # legal_name / dob / document_country -> Fact
        self.unreadable = {}      # field -> note ID whose text we could not read it from
        self.entity_type = None
        self.trace = []

    def facts_for(self, field):
        return [fact for fact in self.facts if fact.field == field]

    def describe(self):
        return ", ".join(self.record_ids)


class Resolution(object):
    def __init__(self, alert_id):
        self.alert_id = alert_id
        self.status = "not_found"   # resolved | ambiguous | not_found
        self.persons = []
        self.screened = []
        self.trace = []

    @property
    def customer_record_id(self):
        if self.status == "resolved":
            return self.persons[0].primary_id
        if self.status == "ambiguous":
            return "AMBIGUOUS"
        return None


class Resolver(object):
    def __init__(self, data):
        self.data = data
        self.names = data.names
        self._applicant_owner = {}
        for row in data.onboarding:
            owner = row.get("customer_id")
            if not isinstance(owner, str) or not owner:
                holders = sorted(set(a["customer_id"] for a in data.accounts_by_id.get(row.get("account_id"), [])
                                     if isinstance(a.get("customer_id"), str)))
                owner = holders[0] if len(holders) == 1 else None
            if owner:
                self._applicant_owner[row["kyc_applicant_id"]] = owner
        self._applicants_by_customer = {}
        for applicant in sorted(self._applicant_owner):
            self._applicants_by_customer.setdefault(self._applicant_owner[applicant], []).append(applicant)
        self._check_cache = {}
        self._note_cache = {}
        self._provider_words = frozenset(
            word for row in data.kyc_checks for word in simple_text(row.get("provider") or "").split())
        self._records_by_document = {}
        for row in data.kyc_checks:
            check = check_from_row(row, data.date_orders["kyc"])
            owner = self._applicant_owner.get(row.get("applicant_id"))
            if owner and check.status == "approved" and check.document:
                self._records_by_document.setdefault(check.document, set()).add(owner)
        for customer in data.customers:
            for check in self._note_checks(customer):
                if check.status == "approved" and check.document:
                    self._records_by_document.setdefault(check.document, set()).add(customer["record_id"])
        self._name_index = None

    # ---------------------------------------------------- record helpers

    def record_dob(self, record):
        return dates.parse_date(record.get("dob"), self.data.date_orders["customers"])

    def record_names(self, record):
        company = self._is_company(record.get("entity_type"))
        forms = []
        for field in ("full_name", "preferred_name", "login_name"):
            value = record.get(field)
            if isinstance(value, str) and value.strip():
                forms.append((field, value, self.names.parts(value, company)))
        return forms

    @staticmethod
    def _is_company(entity_type):
        return simple_text(entity_type or "") in ("company", "organisation", "organization", "entity")

    def parse_note(self, note, record):
        """parse_check_note with this customer's name parts and the provider words."""
        key = (note["note_id"], record.get("record_id"))
        if key not in self._note_cache:
            known = set(part for _f, _v, parts in self.record_names(record) for part in parts)
            self._note_cache[key] = parse_check_note(note, self.data.date_orders["notes"], known,
                                                     self._provider_words)
        return self._note_cache[key]

    def _note_checks(self, record):
        email = normal_email(record.get("email"))
        result = []
        for note in self.data.notes_by_email.get(email, []):
            check = self.parse_note(note, record)
            if check is not None:
                result.append(check)
        return result

    def record_checks(self, record_id):
        """All identity checks of one record: KYC provider rows and KYC notes."""
        if record_id in self._check_cache:
            return self._check_cache[record_id]
        record = self.data.customer_by_id.get(record_id, {})
        checks = []
        for applicant in self._applicants_by_customer.get(record_id, []):
            for row in self.data.checks_by_applicant.get(applicant, []):
                checks.append(check_from_row(row, self.data.date_orders["kyc"]))
        checks.extend(self._note_checks(record))
        checks.sort(key=check_order)
        self._check_cache[record_id] = checks
        return checks

    def record_applicants(self, record_id):
        return self._applicants_by_customer.get(record_id, [])

    # ---------------------------------------------------- screened details

    def screened_details(self, alert):
        customer = alert.get("customer") or {}
        company = self._is_company(customer.get("entity_type"))
        country = countries.lookup(customer.get("country"))
        return {
            "name": customer.get("name") if isinstance(customer.get("name"), str) else None,
            "parts": self.names.parts(customer.get("name"), company),
            "dob": dates.parse_date(customer.get("dob"), self.data.date_orders["alerts"]),
            "dob_given": bool(customer.get("dob")),
            "countries": country,          # () unknown, None unrecognised
            "country_text": customer.get("country"),
            "city": simple_text(customer.get("city") or ""),
            "entity_type": customer.get("entity_type"),
            "company": company,
        }

    def record_fit(self, record, screened):
        """How a record fits the screened details: {criterion: True/False/None}."""
        fit = {}
        if screened["entity_type"] and record.get("entity_type"):
            fit["type"] = self._is_company(screened["entity_type"]) == self._is_company(record.get("entity_type"))
        if screened["parts"]:
            fit["name"] = any(self.names.agree(parts, screened["parts"])
                              for _field, _value, parts in self.record_names(record))
        else:
            fit["name"] = None
        record_dob = self.record_dob(record)
        if screened["dob"] is not None and record_dob is not None:
            fit["dob"] = dates.agree_in_shared_parts(screened["dob"], record_dob)
        elif screened["dob_given"] and screened["dob"] is None:
            fit["dob"] = None
        if screened["countries"]:
            mine = set()
            for field in ("residence_country", "nationality"):
                found = countries.lookup(record.get(field))
                if found:
                    mine.update(found)
            if mine:
                fit["country"] = bool(mine & set(screened["countries"]))
        if screened["city"]:
            places = " %s %s " % (simple_text(record.get("city") or ""), simple_text(record.get("address") or ""))
            if places.strip():
                fit["city"] = (" %s " % screened["city"]) in places
        return fit

    @staticmethod
    def fits(fit):
        """Name must agree; nothing else may contradict."""
        if fit.get("name") is not True:
            return False
        return all(value is not False for key, value in fit.items() if key != "name")

    # ---------------------------------------------------- linking

    def follow_link(self, alert, trace):
        """Records the alert's link leads to (sorted IDs), or None if no link."""
        found = set()
        any_link = False
        account_ref = alert.get("account_ref")
        if isinstance(account_ref, str) and account_ref.strip():
            any_link = True
            rows = self.data.accounts_by_id.get(account_ref.strip(), [])
            holders = sorted(set(r["customer_id"] for r in rows if r.get("customer_id") in self.data.customer_by_id))
            trace.append("account_ref %s -> accounts -> %s" % (account_ref, ", ".join(holders) or "nothing found"))
            found.update(holders)
        login = alert.get("login_handle")
        if isinstance(login, str) and login.strip():
            any_link = True
            rows = self.data.accounts_by_login.get(login.strip(), [])
            holders = set(r["customer_id"] for r in rows if r.get("customer_id") in self.data.customer_by_id)
            for record in self.data.customers_by_login.get(login.strip().casefold(), []):
                holders.add(record["record_id"])
            holders = sorted(holders)
            accounts = sorted(set(r["account_id"] for r in rows))
            trace.append("login_handle %s -> accounts %s -> %s" % (login, ", ".join(accounts) or "-", ", ".join(holders) or "nothing found"))
            found.update(holders)
        email = alert.get("email")
        if isinstance(email, str) and email.strip():
            any_link = True
            holders = sorted(r["record_id"] for r in self.data.customers_by_email.get(normal_email(email), []))
            trace.append("email %s -> customers -> %s" % (email, ", ".join(holders) or "nothing found"))
            found.update(holders)
        if not any_link:
            return None
        return sorted(found)

    def search(self, screened, trace):
        """Records that fit the screened details when there is no usable link."""
        if not screened["parts"]:
            trace.append("no link and no usable screened name: cannot search")
            return []
        # Candidates share a name part or the birth year; the fit test below
        # then applies the full name rule, so typos in every part still match.
        index, by_year = self._get_indexes()
        candidates = set()
        for part in screened["parts"]:
            candidates.update(index.get(self.names.key(part), ()))
        if screened["dob"] is not None:
            candidates.update(by_year.get(screened["dob"][0], ()))
        fitting = []
        for record_id in sorted(candidates):
            record = self.data.customer_by_id[record_id]
            if self.fits(self.record_fit(record, screened)):
                fitting.append(record_id)
        given = [label for label, present in (("name", True), ("date of birth", screened["dob"] is not None),
                                              ("country", bool(screened["countries"])), ("city", bool(screened["city"])))
                 if present]
        trace.append("%s -> customers -> %s" % (", ".join(given), ", ".join(fitting) or "no record fits"))
        return fitting

    def _get_indexes(self):
        """Name-part index and birth-year index over all customer records."""
        if self._name_index is None:
            index = {}
            by_year = {}
            for record in self.data.customers:
                for _field, _value, parts in self.record_names(record):
                    for part in parts:
                        index.setdefault(self.names.key(part), set()).add(record["record_id"])
                dob = self.record_dob(record)
                if dob is not None:
                    by_year.setdefault(dob[0], set()).add(record["record_id"])
            self._name_index = (index, by_year)
        return self._name_index

    # ---------------------------------------------------- merging

    def same_person(self, seed_ids):
        """All records of the person behind seed_ids (closure of the merge rules)."""
        person = set(seed_ids)
        queue = sorted(person)
        links = []
        while queue:
            record_id = queue.pop(0)
            record = self.data.customer_by_id.get(record_id)
            if record is None:
                continue
            joined = set()
            phone = normal_phone(record.get("phone"))
            dob = self.record_dob(record)
            if phone and dates.is_full(dob):
                for other in self.data.customers_by_phone.get(phone, []):
                    if other["record_id"] != record_id and self.record_dob(other) == dob:
                        joined.add(other["record_id"])
            for check in self.record_checks(record_id):
                if check.status == "approved" and check.document:
                    joined.update(self._records_by_document.get(check.document, ()))
            for other_id in sorted(joined - person):
                person.add(other_id)
                queue.append(other_id)
                links.append(other_id)
            queue.sort()
        return sorted(person)

    # ---------------------------------------------------- the person's picture

    def build_person(self, record_ids, seeds, alert_id):
        person = Person(record_ids, seeds)
        facts = []
        types = set()
        all_checks = []
        for record_id in person.record_ids:
            record = self.data.customer_by_id[record_id]
            if isinstance(record.get("entity_type"), str):
                types.add(record["entity_type"])
                facts.append(Fact("entity_type", record["entity_type"], DECLARED, record_id, "entity_type"))
            for field, value, _parts in self.record_names(record):
                facts.append(Fact("name", value, DECLARED, record_id, field))
            dob = self.record_dob(record)
            if dob is not None:
                facts.append(Fact("dob", dob, DECLARED, record_id, "record_dob"))
            for field, role in (("residence_country", "residence"), ("nationality", "nationality")):
                for country in countries.lookup(record.get(field)) or ():
                    facts.append(Fact("country", country, DECLARED, record_id, role))
                if countries.lookup(record.get(field)) is None:
                    facts.append(Fact("country_unrecognised", record.get(field), DECLARED, record_id, role))
            for field in ("city", "address"):
                if isinstance(record.get(field), str) and record[field].strip():
                    facts.append(Fact(field, record[field], DECLARED, record_id, field))
            email = normal_email(record.get("email"))
            for note in self.data.notes_by_email.get(email, []):
                if self.parse_note(note, record) is not None:
                    continue
                for country in countries.find_in_text(note.get("text")):
                    facts.append(Fact("country", country, DECLARED, note["note_id"], "lived"))
            all_checks.extend(self.record_checks(record_id))
        all_checks = sorted(set(all_checks), key=check_order)
        person.checks = [c for c in all_checks if c.status == "approved"]
        person.ignored_checks = [c for c in all_checks if c.status != "approved"]
        for check in person.checks:
            if check.name:
                facts.append(Fact("legal_name", check.name, VERIFIED, check.check_id, check.kind))
            if check.dob is not None:
                facts.append(Fact("dob", check.dob, VERIFIED, check.check_id, check.kind))
            for country in check.countries:
                facts.append(Fact("country", country, VERIFIED, check.check_id, "document"))
        # Most recent approved check that gives each field (checks are sorted
        # most recent first, then lowest ID). A provider row may simply not
        # record a field, so an older check can supply it. But when the check
        # is a note and we could not read the field from its text, the field
        # is unknown: never fall back to an older check or a declared value.
        for check in person.checks:
            readable = {
                "legal_name": bool(check.name),
                "dob": check.dob is not None,
                "document_country": bool(check.countries),
            }
            for field, ok in sorted(readable.items()):
                if field in person.verified or field in person.unreadable:
                    continue
                if ok:
                    value = {"legal_name": check.name, "dob": check.dob,
                             "document_country": ", ".join(check.countries)}[field]
                    person.verified[field] = Fact("country" if field == "document_country" else field,
                                                  value, VERIFIED, check.check_id,
                                                  "document" if field == "document_country" else check.kind)
                elif check.kind == "note":
                    person.unreadable[field] = check.check_id
        person.entity_type = sorted(types)[0] if len(types) == 1 else None
        person.facts = sorted(set(facts), key=fact_sort_key)
        return person

    # ---------------------------------------------------- main entry

    def resolve(self, alert):
        resolution = Resolution(alert.get("alert_id"))
        trace = resolution.trace
        screened = self.screened_details(alert)
        alert_id = alert.get("alert_id")
        if screened["name"]:
            resolution.screened.append(Fact("name", screened["name"], SCREENED, alert_id, "screened_name"))
        if screened["dob"] is not None:
            resolution.screened.append(Fact("dob", screened["dob"], SCREENED, alert_id, "screened_dob"))
        for country in screened["countries"] or ():
            resolution.screened.append(Fact("country", country, SCREENED, alert_id, "screened_country"))
        if screened["city"]:
            resolution.screened.append(Fact("city", (alert.get("customer") or {}).get("city"), SCREENED, alert_id, "screened_city"))

        linked = self.follow_link(alert, trace)
        if linked is None:
            seeds = self.search(screened, trace)
        elif not linked:
            trace.append("the link leads to no customer record: searching instead")
            seeds = self.search(screened, trace)
        else:
            seeds = linked

        # Group the seed records into distinct people.
        people = []
        for seed in seeds:
            if any(seed in record_ids for record_ids, _s in people):
                for record_ids, person_seeds in people:
                    if seed in record_ids:
                        person_seeds.append(seed)
                continue
            people.append((self.same_person([seed]), [seed]))
        for record_ids, person_seeds in people:
            if len(record_ids) > 1:
                trace.append("same phone and date of birth, or same document number on approved checks -> one person: %s"
                             % ", ".join(record_ids))

        if linked and len(people) > 1:
            fitting = []
            for record_ids, person_seeds in people:
                if any(self.fits(self.record_fit(self.data.customer_by_id[r], screened)) for r in record_ids):
                    fitting.append((record_ids, person_seeds))
            if fitting:
                trace.append("the link leads to %d people; the screened details fit %s"
                             % (len(people), "; ".join(", ".join(ids) for ids, _s in fitting)))
                people = fitting
            else:
                trace.append("the link leads to %d people and the screened details fit none of them" % len(people))

        resolution.persons = [self.build_person(ids, person_seeds, alert_id) for ids, person_seeds in people]
        resolution.persons.sort(key=lambda p: p.primary_id)
        if len(resolution.persons) == 1:
            resolution.status = "resolved"
        elif len(resolution.persons) > 1:
            resolution.status = "ambiguous"
            trace.append("%d different people fit (records %s)" % (
                len(resolution.persons), "; ".join(p.describe() for p in resolution.persons)))
        else:
            resolution.status = "not_found"
            trace.append("no customer record found")
        for person in resolution.persons:
            for applicant in sorted(set(a for r in person.record_ids for a in self.record_applicants(r))):
                checks = [c.check_id + " (" + c.status + ")" for c in self.record_checks(self._applicant_owner[applicant])
                          if c.kind == "kyc"]
                if checks:
                    person.trace.append("%s -> onboarding %s -> checks %s" % (self._applicant_owner[applicant], applicant, ", ".join(checks)))
            for field in ("legal_name", "dob", "document_country"):
                fact = person.verified.get(field)
                if fact is not None:
                    value = dates.to_text(fact.value) if field == "dob" else fact.value
                    person.trace.append("verified %s %s (%s)" % (field, value, fact.source))
            lived = sorted(set((f.value, f.source) for f in person.facts if f.role == "lived"))
            if lived:
                person.trace.append("notes say they lived in " + ", ".join("%s (%s)" % item for item in lived))
        return resolution

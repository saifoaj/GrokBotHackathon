"""Comparing the customer with the hit and deciding (POLICY principles 6-8).

Order: contradictions clear the hit (principle 6); otherwise agreement
confirms it (principle 7); otherwise REVIEW. Unknown never clears: every
comparison has an "unknown" outcome that neither clears nor confirms.

Reasons come from fixed templates filled with this hit's values and record
IDs. Evidence lists only the records behind the facts that decided the hit
(customer records, KYC checks, notes, search results); the hit's own fields
are implied by the hit itself.
"""

import calendar
from collections import namedtuple

import dates

DIFFERENT = "DIFFERENT_ENTITY"
SAME = "SAME_ENTITY"
REVIEW = "REVIEW"

# Main reason code when several contradictions apply (any is accepted).
CLEAR_ORDER = ["TEMPORAL_IMPOSSIBILITY", "DECEASED", "DOB_MISMATCH", "LOCATION_MISMATCH",
               "NAME_MISMATCH_VERIFIED", "ENTITY_TYPE_MISMATCH"]

AGREE, CONTRADICT, NEITHER, UNKNOWN = "agree", "contradict", "neither", "unknown"
MIN_AGE = 16

Outcome = namedtuple("Outcome", "decision code sentences evidence trace")


def join(items):
    return ", ".join(items)


def ids(*groups):
    found = set()
    for group in groups:
        found.update(item for item in group if item)
    return sorted(found)


# ---------------------------------------------------------------- the customer side

class CustomerView(object):
    """The facts of one resolved person that the comparisons use."""

    def __init__(self, person, resolver, screened_company):
        names = resolver.names
        records = [resolver.data.customer_by_id[r] for r in person.record_ids]
        types = sorted(set(resolver._is_company(r.get("entity_type")) for r in records
                           if isinstance(r.get("entity_type"), str)))
        self.is_company = types[0] if len(types) == 1 else screened_company
        self.record_ids = list(person.record_ids)
        self.primary_id = person.primary_id

        # Date of birth (or incorporation date): verified beats declared.
        verified_dob = person.verified.get("dob")
        if verified_dob is not None:
            self.dobs = [(verified_dob.value, [verified_dob.source], "verified")]
        else:
            by_date = {}
            for fact in person.facts:
                if fact.field == "dob" and fact.level == "declared":
                    by_date.setdefault(fact.value, []).append(fact.source)
            self.dobs = [(value, sorted(by_date[value]), "declared")
                         for value in sorted(by_date, key=dates.sort_key)]

        # Names: the verified legal name outranks the record's names.
        legal = person.verified.get("legal_name")
        self.verified_name = None
        if legal is not None:
            self.verified_name = (legal.value, names.parts(legal.value, self.is_company), legal.source)
        self.declared_names = []
        for fact in person.facts:
            if fact.field == "name" and fact.level == "declared":
                self.declared_names.append((fact.value, names.parts(fact.value, self.is_company), fact.source))

        # Countries: residence and nationality on all records, the approved
        # identity document (most recent approved check that gives one), and
        # places the notes say they lived.
        self.countries = {}
        self.unrecognised = []
        for fact in person.facts:
            if fact.field == "country" and fact.role in ("residence", "nationality", "lived"):
                self.countries.setdefault(fact.value, set()).add(fact.source)
            elif fact.field == "country_unrecognised":
                self.unrecognised.append((fact.source, fact.value))
        for check in person.checks:
            if check.countries:
                for country in check.countries:
                    self.countries.setdefault(country, set()).add(check.check_id)
                break

    def dob_sources(self):
        return ids(*[sources for _v, sources, _l in self.dobs])

    def dob_text(self):
        return " or ".join("%s (%s)" % (dates.to_text(v), join(s)) for v, s, _l in self.dobs)


# ---------------------------------------------------------------- comparisons

def compare_dob(customer_dob, hit_dob):
    """agree / contradict / neither for one customer date and one hit date."""
    year = customer_dob[0]
    if year in hit_dob.years:
        if len(hit_dob.years) == 1 and hit_dob.month and customer_dob[1]:
            if hit_dob.month != customer_dob[1]:
                return CONTRADICT
            if hit_dob.day and customer_dob[2] and hit_dob.day != customer_dob[2]:
                return CONTRADICT
        return AGREE
    distance = min(abs(year - other) for other in hit_dob.years)
    return CONTRADICT if distance > 1 else NEITHER


def dob_result(customer, hit):
    """(status, hit dates involved). Agree needs one hit date agreeing with the
    customer; contradict needs every hit date contradicted."""
    if not customer.dobs or not hit.dobs:
        return (UNKNOWN, [])
    agreeing = [h for h in hit.dobs if all(compare_dob(c, h) == AGREE for c, _s, _l in customer.dobs)]
    if agreeing:
        return (AGREE, agreeing)
    if all(compare_dob(c, h) == CONTRADICT for c, _s, _l in customer.dobs for h in hit.dobs):
        return (CONTRADICT, list(hit.dobs))
    return (NEITHER, list(hit.dobs))


def max_age_at(birth, when):
    """The oldest the person can have been on date `when` (unknown parts favour older)."""
    age = when[0] - birth[0]
    if birth[1] is not None and when[1] is not None:
        if when[1] < birth[1] or (when[1] == birth[1] and birth[2] is not None
                                  and when[2] is not None and when[2] < birth[2]):
            age -= 1
    return age


def earliest_day(value):
    return (value[0], value[1] or 1, value[2] or 1)


def latest_day(value):
    month = value[1] or 12
    day = value[2] or calendar.monthrange(value[0], month)[1]
    return (value[0], month, day)


def temporal_result(customer, hit):
    """(True, detail) when the customer could not have done what the earliest
    dated report says: younger than 16 then, or not yet incorporated."""
    report = hit.earliest_report
    if report is None or not customer.dobs:
        return (False, None)
    if customer.is_company:
        if all(earliest_day(c) > latest_day(report) for c, _s, _l in customer.dobs):
            return (True, None)
        return (False, None)
    oldest = max(max_age_at(c, report) for c, _s, _l in customer.dobs)
    if oldest < MIN_AGE:
        return (True, oldest)
    return (False, None)


def deceased_result(hit, screened_at):
    if hit.death is None or screened_at is None:
        return False
    return latest_day(hit.death[0]) < latest_day(screened_at)


def location_result(customer, hit):
    """(status, shared countries)."""
    shared = sorted(set(customer.countries) & set(hit.countries))
    if shared:
        return (AGREE, shared)
    if customer.countries and hit.countries and not customer.unrecognised and not hit.unrecognised:
        return (CONTRADICT, [])
    return (UNKNOWN, [])


def name_result(customer, hit, rules):
    """(status, hit name that agreed, customer name, sources, verified?).

    status: agree, contradict (verified legal name agrees with no hit name),
    neither (declared names disagree: not enough to clear) or unknown.
    """
    hit_names = [n for n in hit.names if n.parts]
    if not hit_names:
        return (UNKNOWN, None, None, [], False)
    if customer.verified_name is not None:
        value, parts, source = customer.verified_name
        if not customer.is_company and len(parts) < 2:
            return (UNKNOWN, None, value, [source], True)
        for name in hit_names:
            if rules.agree(parts, name.parts):
                return (AGREE, name.raw, value, [source], True)
        return (CONTRADICT, None, value, [source], True)
    minimum = 1 if customer.is_company else 2
    usable = [(v, p, s) for v, p, s in customer.declared_names if len(p) >= minimum]
    if not usable:
        return (UNKNOWN, None, None, [], False)
    for name in hit_names:
        sources = sorted(set(s for v, p, s in usable if rules.agree(p, name.parts)))
        if sources:
            value = [v for v, p, s in usable if s == sources[0]][0]
            return (AGREE, name.raw, value, sources, False)
    return (NEITHER, None, usable[0][0], [], False)


# ---------------------------------------------------------------- sentences

def hit_dob_phrase(hit, used):
    texts = join(sorted(set(h.text for h in used)))
    if hit.dob_origin == "dobs field":
        return "listed date of birth is %s" % texts
    if hit.dob_origin == "search results":
        sources = join(sorted(set(h.source for h in used)))
        return "search results (%s) put its birth year at %s" % (sources, texts)
    return "profile notes put its birth year at %s" % texts


def kind(is_company):
    return "company" if is_company else "person"


def decide_for_person(person, customer, hit, alert, rules):
    """Outcome for one resolved person."""
    trace = []
    screened_at = dates.parse_date((alert or {}).get("screened_at"))
    dob_status, dob_used = dob_result(customer, hit)
    loc_status, shared = location_result(customer, hit)
    name_status, hit_name, cust_name, name_sources, name_verified = name_result(customer, hit, rules)
    temporal, oldest = temporal_result(customer, hit)
    deceased = deceased_result(hit, screened_at)
    type_mismatch = (customer.is_company is not None and hit.is_company is not None
                     and customer.is_company != hit.is_company)
    search_ids = sorted(set(h.source for h in dob_used if h.how == "search_result"))

    trace.append("customer DOB %s; hit DOB %s from %s -> %s" % (
        customer.dob_text() or "unknown", join(h.text for h in hit.dobs) or "none",
        hit.dob_origin or "nowhere", dob_status))
    trace.append("customer countries %s; hit countries %s -> %s" % (
        join(sorted(customer.countries)) or "none", join(hit.country_list()) or "none", loc_status))
    trace.append("customer name %s (%s) vs hit names -> %s%s" % (
        cust_name or "unknown", "verified" if name_verified else "declared", name_status,
        " (%s)" % hit_name if hit_name else ""))
    if hit.earliest_report:
        trace.append("earliest dated report %s; customer at most %s then" % (
            dates.to_text(hit.earliest_report), oldest if oldest is not None else "16+ or unknown"))
    if hit.death:
        trace.append("hit date of death %s (%s)" % (dates.to_text(hit.death[0]), hit.death[1]))

    # ------------------------------------------------ principle 6: contradictions
    clears = []
    if temporal:
        on_in = "on" if dates.is_full(customer.dobs[0][0]) else "in"
        if customer.is_company:
            sentence = ("The customer company was incorporated %s %s, after the earliest dated report "
                        "in the hit's profile on %s." % (on_in, customer.dob_text(), dates.to_text(hit.earliest_report)))
        else:
            sentence = ("The customer was born %s %s, so they were at most %d when the hit's profile "
                        "reports activity on %s." % (on_in, customer.dob_text(), oldest, dates.to_text(hit.earliest_report)))
        clears.append(("TEMPORAL_IMPOSSIBILITY", sentence, customer.dob_sources()))
    if deceased:
        clears.append(("DECEASED", "The hit's profile gives a date of death of %s, before the customer was "
                       "screened on %s." % (dates.to_text(hit.death[0]), dates.to_text(screened_at)),
                       ["%s#%s" % (hit.hit_id, hit.death[1])]))
    if dob_status == CONTRADICT:
        clears.append(("DOB_MISMATCH", "The customer's date of birth is %s but the hit's %s." % (
            customer.dob_text(), hit_dob_phrase(hit, dob_used)), ids(customer.dob_sources(), search_ids)))
    if loc_status == CONTRADICT:
        if name_status == AGREE and dob_status == AGREE:
            trace.append("countries differ but name and date of birth agree: REVIEW, not cleared")
        else:
            clears.append(("LOCATION_MISMATCH", "The customer is linked only to %s but the hit is linked "
                           "only to %s." % (join(sorted(customer.countries)), join(hit.country_list())),
                           ids(*customer.countries.values())))
    if name_status == CONTRADICT:
        clears.append(("NAME_MISMATCH_VERIFIED", "The customer's verified name is %s (%s), which does not "
                       "match the hit's name %s%s." % (cust_name, join(name_sources), hit.display_name,
                                                      " or any of its aliases" if any(n.kind == "alias" for n in hit.names) else ""),
                       name_sources))
    if type_mismatch:
        clears.append(("ENTITY_TYPE_MISMATCH", "The customer is a %s (%s) but the hit is a %s." % (
            kind(customer.is_company), join(customer.record_ids), kind(hit.is_company)), customer.record_ids))
    if clears:
        clears.sort(key=lambda item: CLEAR_ORDER.index(item[0]))
        sentences = [sentence for _c, sentence, _e in clears[:3]]
        if len(sentences) == 1:
            sentences.append("One contradicting fact is enough to show the hit is a different %s, so it is cleared."
                             % kind(hit.is_company if hit.is_company is not None else customer.is_company))
        evidence = ids(*[e for _c, _s, e in clears])
        trace.append("cleared: " + join(c for c, _s, _e in clears))
        return Outcome(DIFFERENT, clears[0][0], sentences, evidence, trace)

    # ------------------------------------------------ principle 7: agreement
    agree_sentences = []
    agree_evidence = []
    if dob_status == AGREE:
        agree_sentences.append("The customer's date of birth %s agrees with the hit's %s%s." % (
            customer.dob_text(), join(h.text for h in hit.dobs),
            " (%s)" % join(search_ids) if search_ids else ""))
        agree_evidence += customer.dob_sources() + search_ids
    if loc_status == AGREE:
        agree_sentences.append("Both the customer and the hit are linked to %s." % join(shared))
        agree_evidence += ids(*[customer.countries[c] for c in shared])
    if name_status == AGREE:
        # The agreeing name is named in the reason but not cited as evidence:
        # a shared name is what raised the alert, not what decides it.
        agree_sentences.append("The customer's %sname %s (%s) agrees with the hit's name %s." % (
            "verified " if name_verified else "", cust_name, join(name_sources), hit_name))

    if not customer.is_company and name_status == AGREE and dob_status == AGREE and loc_status != CONTRADICT:
        code = "NAME_DOB_LOCATION_MATCH" if loc_status == AGREE else "NAME_DOB_MATCH"
        sentences = agree_sentences + ["Nothing in the customer's records contradicts the hit."]
        return Outcome(SAME, code, sentences[:4], ids(agree_evidence), trace)
    if customer.is_company and name_status == AGREE and loc_status == AGREE:
        sentences = agree_sentences + ["Nothing in the customer's records contradicts the hit."]
        return Outcome(SAME, "NAME_LOCATION_MATCH", sentences[:4], ids(agree_evidence), trace)

    # ------------------------------------------------ otherwise: review
    if loc_status == CONTRADICT:     # name and date of birth agree, countries differ
        sentences = agree_sentences[:2] + [
            "The customer is linked only to %s but the hit only to %s, and a country difference does not "
            "clear a hit whose name and date of birth agree." % (join(sorted(customer.countries)), join(hit.country_list())),
            "Evidence of where the listed person lives, or an identity document number for them, would settle it."]
        return Outcome(REVIEW, "INSUFFICIENT_DATA", sentences[:4],
                       ids(agree_evidence, *customer.countries.values()), trace)

    missing, settle = [], []
    if customer.is_company:
        if loc_status != AGREE:
            missing.append("no country links the company to the hit")
            settle.append("the listed company's country of registration")
        if name_status != AGREE:
            missing.append("the company names do not clearly agree")
            settle.append("the customer's verified registered name")
    else:
        if dob_status == UNKNOWN:
            if not hit.dobs:
                missing.append("the date of birth cannot be compared with it because the hit gives none")
                settle.append("a date of birth for the listed person")
            else:
                missing.append("the customer has no date of birth on file to compare")
                settle.append("a verified date of birth for the customer")
        elif dob_status == NEITHER:
            missing.append("the customer's date of birth %s is within a year of the hit's %s, so it neither "
                           "agrees nor contradicts" % (customer.dob_text(), join(h.text for h in hit.dobs)))
            settle.append("the listed person's full date of birth")
            agree_evidence += customer.dob_sources() + search_ids
        if name_status == UNKNOWN and customer.verified_name is not None:
            missing.append("the customer's verified name %s has fewer than two real name parts" % cust_name)
            settle.append("the customer's full verified legal name")
            agree_evidence += name_sources
        elif name_status in (UNKNOWN, NEITHER):
            missing.append("the customer's name does not clearly agree with the hit's")
            settle.append("the customer's verified legal name")
    if not missing:
        missing.append("not enough agrees to confirm the match")
        settle.append("a verified identity document for the customer")
    sentences = agree_sentences[:2] + [
        "Nothing in the customer's records contradicts the hit, but %s, so the hit can be neither cleared "
        "nor confirmed." % " and ".join(missing),
        "%s would settle it." % (" and ".join(settle)[0].upper() + " and ".join(settle)[1:])]
    return Outcome(REVIEW, "INSUFFICIENT_DATA", sentences[:4], ids(agree_evidence), trace)


# ---------------------------------------------------------------- one hit, all people

def decide(resolution, resolver, hit, alert):
    """Final decision for one hit: dict with decision, reason_code,
    customer_record_id, reason, evidence and trace."""
    screened = (alert or {}).get("customer") or {}
    screened_company = None
    if isinstance(screened.get("entity_type"), str):
        screened_company = resolver._is_company(screened.get("entity_type"))
    screened_name = screened.get("name") if isinstance(screened.get("name"), str) else "unknown"

    if not resolution.persons:
        return {
            "decision": REVIEW, "reason_code": "INSUFFICIENT_DATA", "customer_record_id": None,
            "reason": ("No customer record fits the screened customer %s in alert %s, so hit %s (%s) cannot be "
                       "compared with them. The customer's account, login or a verified identity document would "
                       "settle it." % (screened_name, resolution.alert_id, hit.hit_id, hit.display_name)),
            "evidence": [], "trace": list(resolution.trace),
        }

    outcomes = []
    for person in resolution.persons:
        customer = CustomerView(person, resolver, screened_company)
        outcomes.append((person, decide_for_person(person, customer, hit, alert, resolver.names)))

    if len(outcomes) == 1:
        person, outcome = outcomes[0]
        return {
            "decision": outcome.decision, "reason_code": outcome.code,
            "customer_record_id": resolution.customer_record_id,
            "reason": " ".join(outcome.sentences), "evidence": outcome.evidence,
            "trace": list(resolution.trace) + list(person.trace) + outcome.trace,
        }

    trace = list(resolution.trace)
    for person, outcome in outcomes:
        trace += ["[%s] %s" % (person.primary_id, step) for step in person.trace + outcome.trace]
        trace.append("[%s] -> %s %s" % (person.primary_id, outcome.decision, outcome.code))
    records = "; ".join(p.describe() for p, _o in outcomes)
    decisions = sorted(set(o.decision for _p, o in outcomes))
    if len(decisions) == 1:
        first = outcomes[0][1]
        reason = ("%d different people in the system of record fit the screened customer %s (%s), and the hit "
                  "leads to %s for each of them. For %s: %s" % (
                      len(outcomes), screened_name, records, first.decision, outcomes[0][0].primary_id,
                      " ".join(first.sentences[:2])))
        return {
            "decision": first.decision, "reason_code": first.code, "customer_record_id": "AMBIGUOUS",
            "reason": reason, "evidence": ids(*[o.evidence for _p, o in outcomes]), "trace": trace,
        }
    per_person = "; ".join("for %s it would be %s (%s)" % (p.primary_id, o.decision, o.code) for p, o in outcomes)
    reason = ("%d different people in the system of record fit the screened customer %s (%s), and they lead to "
              "different decisions: %s. Knowing which of these records the screened customer is, for example "
              "from the account or login the alert was raised on, would settle it." % (
                  len(outcomes), screened_name, records, per_person))
    return {
        "decision": REVIEW, "reason_code": "AMBIGUOUS_CUSTOMER_RECORD", "customer_record_id": "AMBIGUOUS",
        "reason": reason, "evidence": ids(*[p.record_ids for p, _o in outcomes]), "trace": trace,
    }

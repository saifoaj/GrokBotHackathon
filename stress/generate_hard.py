"""Harder stress set: the same policy, written in ways the system was not built around.

Each case is still decided by POLICY.md, but the facts arrive in unfamiliar wording:
ages in words, title-case names in KYC notes, soft-worded failed checks, a city instead
of a country, countries in their own language, two-digit years, vague hit dates,
an age that belongs to someone else, month-first dates. Some are traps where a
careless reader would clear a real match (cost 60); the rest are misses that should at
worst fall back to REVIEW.

    python stress/generate_hard.py stress/data_hard [instances per case, default 12] [noise, default 20000]
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate as g  # noqa: E402
from generate import CITY, COUNTRIES, FIRST, MONTHS, RNG, World, iso, link_kwargs, other_country, person, \
    screened  # noqa: E402

WORDS = {41: "forty-one", 45: "forty-five", 52: "fifty-two", 38: "thirty-eight", 61: "sixty-one", 47: "forty-seven"}
LOCAL = {"Germany": ("Deutschland", "Deutsch"), "Spain": ("España", "Española"), "Austria": ("Österreich",
         "Österreichisch"), "Poland": ("Polska", "Polska"), "Sweden": ("Sverige", "Svensk"),
         "Italy": ("Italia", "Italiana"), "Greece": ("Ellada", "Ellinikí"), "Norway": ("Norge", "Norsk")}


def name_of(p):
    return "%s %s" % (p["first"], p["last"])


# --------------------------------------------------------------------------- traps (a wrong clear costs 60)

def t_city_only_note(w):
    p = person(w)
    far = other_country(p["country"])
    city = CITY[far]
    w.note(p["cus"]["email"], RNG.choice([
        "Customer said they spent 2008-2012 in %s working for a mining firm." % city,
        "Caller mentioned their old flat in %s, where they lived for six years." % city,
        "User explained the transfer was rent for the place they kept in %s until 2015." % city]),
        channel=RNG.choice(["phone", "email", "app"]))
    h = w.hit(name_of(p), countries=[far])
    w.alert([h], **screened(p), **link_kwargs(p, "email"))
    w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [p["cus"]["record_id"]], "trap_city_only_note",
            "The notes say the customer lived in %s, which is in %s, the hit's country." % (city, far))


def t_local_language_country(w, with_dob):
    country = RNG.choice(sorted(LOCAL))
    local, demonym = LOCAL[country]
    first, last = w.person_name()
    y, m, d = RNG.randrange(1955, 1995), RNG.randrange(1, 13), RNG.randrange(1, 28)
    c = w.customer("%s %s" % (first, last), iso(y, m, d), local, demonym, city=CITY[country])
    acc, apps = w.account([c["record_id"]])
    w.check(apps[c["record_id"]], "%s %s" % (first, last), iso(y, m, d), local)
    h = w.hit("%s, %s" % (last, first), countries=[country], dobs=[str(y)] if with_dob else [])
    w.alert([h], name=c["full_name"], dob=iso(y, m, d), account=acc)
    if with_dob:
        w.label(h, "SAME_ENTITY", ["NAME_DOB_LOCATION_MATCH", "NAME_DOB_MATCH"], [c["record_id"]],
                "local_country_with_dob", "%s is %s: name, birth year and country agree." % (local, country))
    else:
        w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [c["record_id"]], "trap_local_country_no_dob",
                "%s is %s, so the countries are the same; no hit birth date." % (local, country))


def t_soft_failed_note(w):
    p = person(w, verified=False)
    of, ol = w.person_name()
    other = "%s %s" % (of.upper(), ol.upper())
    dob = "%d %s %d" % (RNG.randrange(1, 28), RNG.choice(MONTHS), RNG.randrange(1940, 1950))
    w.note(p["cus"]["email"], RNG.choice([
        "Persona flagged a face non-match for %s (%s); verification not completed." % (other, dob),
        "%s's passport check (born %s) is on hold pending manual review by Onfido." % (other, dob),
        "Veriff: the selfie did not match the document presented for %s, born %s." % (other, dob),
        "Onboarding: %s (DOB %s) abandoned the Sumsub identity check before uploading a document." % (other, dob),
        "Awaiting documents from %s, born %s; Onfido session timed out." % (other, dob)]),
        when="2025-01-15", approved=False)
    h = w.hit(name_of(p), countries=[p["country"]])
    w.alert([h], **screened(p), **link_kwargs(p, "email"))
    w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [p["cus"]["record_id"]], "trap_soft_failed_note",
            "The note records a check that did not pass, so its name and date are not evidence.")


def t_age_of_someone_else(w):
    p = person(w)
    y = p["dob"][0]
    year = y + 30
    notes = RNG.choice([
        "{N}'s lawyer, aged 61, told reporters in May {Y} that the charges would be fought.",
        "The judge, 47, adjourned the case against {N} in May {Y}.",
        "In May {Y} {N}'s business partner, then aged 61, was arrested; {N} was not charged.",
    ]).format(N=name_of(p), Y=year)
    h = w.hit(name_of(p), countries=[p["country"]], category="Enforcement", notes=notes)
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [p["cus"]["record_id"]], "trap_age_of_someone_else",
            "The only age in the profile belongs to someone else; the hit gives no birth date.")


def t_note_two_dates(w):
    p = person(w, verified=False)
    y, m, d = p["dob"]
    w.note(p["cus"]["email"], "%s verified the %s passport of %s (born %s), issued %s and valid until %s, on %s." % (
        RNG.choice(g.PROVIDERS), p["country"], name_of(p).upper(), iso(y, m, d), iso(2016, 3, 3), iso(2031, 3, 2),
        "2024-06-01"), when="2024-06-01")
    h = w.hit(name_of(p), countries=[p["country"]], dobs=[str(y)])
    w.alert([h], **screened(p), **link_kwargs(p, "email"))
    w.label(h, "SAME_ENTITY", ["NAME_DOB_LOCATION_MATCH", "NAME_DOB_MATCH"], [p["cus"]["record_id"]],
            "note_issue_and_expiry_dates", "The note gives issue and expiry dates as well as the birth date.")


# --------------------------------------------------------------------------- unfamiliar wording (a miss should be REVIEW)

def m_age_in_words(w):
    p = person(w)
    y = p["dob"][0]
    age = RNG.choice(sorted(WORDS))
    year = y - 13 + age
    notes = RNG.choice(["Officials questioned {N}, aged {W}, in May {Y}.",
                        "In May {Y} the {W}-year-old {N} was charged."]).format(N=name_of(p), W=WORDS[age], Y=year)
    h = w.hit(name_of(p), countries=[p["country"]], category="Enforcement", notes=notes)
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "DIFFERENT_ENTITY", ["DOB_MISMATCH"], [p["cus"]["record_id"]], "age_in_words",
            "Aged %s in May %d gives %d or %d, far from the customer." % (WORDS[age], year, year - age - 1,
                                                                          year - age))


def m_title_case_note(w):
    p = person(w, verified=False)
    of, ol = w.person_name()
    y, m, d = p["dob"]
    w.note(p["cus"]["email"], RNG.choice([
        "%s %s's %s passport was verified by Onfido on 2024-06-01; born %s." % (of, ol, p["country"], iso(y, m, d)),
        "Identity check passed for %s %s (date of birth %s) using a %s national ID, Veriff, 2024-06-01." % (
            of, ol, iso(y, m, d), p["country"])]), when="2024-06-01")
    h = w.hit(name_of(p), countries=[p["country"]])
    w.alert([h], **screened(p), **link_kwargs(p, "email"))
    w.label(h, "DIFFERENT_ENTITY", ["NAME_MISMATCH_VERIFIED"], [p["cus"]["record_id"]], "title_case_note",
            "The approved check names %s %s, which agrees with none of the hit's names." % (of, ol))


def m_month_first_birth(w):
    p = person(w)
    y, m, d = p["dob"]
    hm = m % 12 + 1
    hd = RNG.randrange(13, 28)
    h = w.hit(name_of(p), countries=[p["country"]],
              notes="%s was born on %02d/%02d/%d.\n\nCareer:\nCouncil member (2010 - 2014)." % (name_of(p), hm, hd, y))
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "DIFFERENT_ENTITY", ["DOB_MISMATCH"], [p["cus"]["record_id"]], "month_first_birth",
            "%02d/%02d/%d can only be month first: same year as the customer but a different month." % (hm, hd, y))


def m_report_other_format(w):
    yb = RNG.randrange(2008, 2012)
    p = person(w, dob=(yb, 6, 1))
    rep = yb + 7
    notes = RNG.choice(["According to the Daily Ledger; 3 March %d: %s was named." % (rep, name_of(p)),
                        "according to the Daily Ledger; March 3, %d: %s was named." % (rep, name_of(p)),
                        "According to the Daily Ledger; 2019-03-03: %s was named." % name_of(p)])
    h = w.hit(name_of(p), countries=[p["country"]], category="Adverse Media", notes=notes)
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [p["cus"]["record_id"]], "report_other_format",
            "Only 'According to ...; Month d, yyyy:' lines count as dated reports.")


def m_two_digit_year(w):
    p = person(w, verified=False, dob=(RNG.randrange(1975, 1990), RNG.randrange(1, 13), RNG.randrange(1, 28)))
    y, m, d = p["dob"]
    p["cus"]["dob"] = "%02d/%02d/%02d" % (d, m, y % 100)
    h = w.hit(name_of(p), countries=[p["country"]], dobs=[str(y - 30)])
    w.alert([h], name=p["full"], **link_kwargs(p))
    w.label(h, "DIFFERENT_ENTITY", ["DOB_MISMATCH"], [p["cus"]["record_id"]], "two_digit_year",
            "The record's %s is %d, thirty years from the hit's %d." % (p["cus"]["dob"], y, y - 30))


def m_vague_hit_dobs(w):
    p = person(w)
    y = p["dob"][0]
    hy = y - 25
    form = RNG.choice(["c. %d" % hy, "%d/%d" % (hy, hy + 1), "%d (approx.)" % hy, "circa %d" % hy])
    h = w.hit(name_of(p), countries=[p["country"]], dobs=[form])
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "DIFFERENT_ENTITY", ["DOB_MISMATCH"], [p["cus"]["record_id"]], "vague_hit_dobs",
            "The hit's '%s' is about 25 years from the customer's birth year." % form)


def m_search_age_in_words(w):
    p = person(w)
    country = p["country"]
    y = p["dob"][0]
    age = RNG.choice(sorted(WORDS))
    year = y - 14 + age
    name = name_of(p)
    sid = w.search(name, "In August %d, %s, aged %s, was named as a politically exposed person in %s." % (
        year, name, WORDS[age], country))
    h = w.hit(name, countries=[country], category="PEP")
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "DIFFERENT_ENTITY", ["DOB_MISMATCH"], [p["cus"]["record_id"]], "search_age_in_words",
            "The qualifying search result (%s) puts the hit's birth about 14 years before the customer's." % sid, sr=[sid])


def m_search_no_age(w):
    p = person(w)
    name = name_of(p)
    w.search(name, "%s, in her forties, was listed as a politically exposed person in %s in 2019." % (name,
                                                                                                  p["country"]))
    h = w.hit(name, countries=[p["country"]], category="PEP")
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [p["cus"]["record_id"]], "search_no_age",
            "'In her forties' is not an age at a date; no birth year can be taken from it.")


def m_death_formats(w):
    p = person(w)
    form = RNG.choice(["14/02/2019", "February 2019", "2019", "14 February 2019"])
    h = w.hit(name_of(p), countries=[p["country"]], life="Deceased", death=form)
    w.alert([h], **screened(p), **link_kwargs(p))
    w.label(h, "DIFFERENT_ENTITY", ["DECEASED"], [p["cus"]["record_id"]], "death_formats",
            "Died %s, before the screening in November 2026." % form)


def m_no_screening_date(w):
    p = person(w)
    h = w.hit(name_of(p), countries=[p["country"]], life="Deceased", death="2019-02")
    aid = w.alert([h], **screened(p), **link_kwargs(p))
    for a in w.t["alerts"]:
        if a["alert_id"] == aid:
            a["screened_at"] = None
    w.label(h, "REVIEW", ["INSUFFICIENT_DATA"], [p["cus"]["record_id"]], "no_screening_date",
            "Without a screening date the death cannot be placed before it.")


CASES = [t_city_only_note, lambda w: t_local_language_country(w, True), lambda w: t_local_language_country(w, False),
         t_soft_failed_note, t_age_of_someone_else, t_note_two_dates, m_age_in_words, m_title_case_note,
         m_month_first_birth, m_report_other_format, m_two_digit_year, m_vague_hit_dobs, m_search_age_in_words,
         m_search_no_age, m_death_formats, m_no_screening_date]


def main(argv):
    out = argv[1] if len(argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_hard")
    per_case = int(argv[2]) if len(argv) > 2 else 12
    noise_count = int(argv[3]) if len(argv) > 3 else 20000
    w = World()
    g.noise(w, noise_count // 2)
    for _ in range(per_case):
        for case in CASES:
            case(w)
    g.noise(w, noise_count - noise_count // 2)
    w.finish()
    os.makedirs(out, exist_ok=True)
    for name, rows in w.t.items():
        rows = list(rows)
        RNG.shuffle(rows)
        with open(os.path.join(out, name + ".jsonl"), "w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with open(os.path.join(out, "name_variants.json"), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(g.VARIANTS, handle, indent=2)
    with open(os.path.join(out, "cases.json"), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(w.case_of, handle, indent=1, sort_keys=True)
    print("wrote %s: %s" % (out, {k: len(v) for k, v in w.t.items()}))


if __name__ == "__main__":
    main(sys.argv)

"""Compare one customer person with one hit: clear on a contradiction, confirm on agreement,
otherwise review (POLICY.md principles 6 and 7). Every test returns True, False or None
(unknown); unknown never fires.
"""

from norm import add_years, before, earliest, fmt_date, latest

CLEAR_ORDER = ["TEMPORAL_IMPOSSIBILITY", "DECEASED", "ENTITY_TYPE_MISMATCH", "DOB_MISMATCH",
               "NAME_MISMATCH_VERIFIED", "LOCATION_MISMATCH"]


def contradicted(c, d):
    """Is hit date d contradicted by customer date c?"""
    if abs(c[0] - d[0]) > 1:
        return True
    if c[0] == d[0] and c[1] and d[1]:
        if c[1] != d[1]:
            return True
        if c[2] and d[2] and c[2] != d[2]:
            return True
    return False


def agrees(c, d):
    if c[0] != d[0]:
        return False
    if c[1] and d[1] and c[1] != d[1]:
        return False
    if c[1] and d[1] and c[2] and d[2] and c[2] != d[2]:
        return False
    return True


def years_text(dates):
    years = sorted({d[0] for d in dates})
    if all(d[1] is None for d in dates):
        if len(years) == 2 and years[1] == years[0] + 1:
            return "%d or %d" % (years[0], years[1])
        return ", ".join(str(y) for y in years)
    return ", ".join(fmt_date(d) for d in dates)


def hit_dob_phrase(h):
    if h["dob_origin"] == "profile_date":
        return "the hit's profile notes give its date of birth as %s" % years_text(h["dates"])
    if h["dob_origin"] == "profile":
        return "the hit's profile notes put its birth year at %s" % years_text(h["dates"])
    if h["dob_origin"] == "search":
        return "the hit's search results (%s) put its birth year at %s" % (", ".join(h["sr"]), years_text(h["dates"]))
    word = "incorporation date" if not h["is_person"] else "date of birth"
    return "the hit's listed %s is %s" % (word, years_text(h["dates"]))


def hit_dob_short(h):
    where = {"profile": "from its profile notes", "profile_date": "from its profile notes",
             "search": "from search results %s" % ", ".join(h["sr"]), "listed": "as listed"}.get(h["dob_origin"], "")
    if all(d[1] is None for d in h["dates"]):
        return "the hit's birth year %s %s" % (years_text(h["dates"]), where)
    return "the hit's date of birth %s %s" % (years_text(h["dates"]), where)


def compare(pic, h, rules, screened_at):
    """Return dict(decision, codes, evidence, sentences, ...) for one person and one hit."""
    company = not pic["is_person"]
    fired = {}  # code -> (evidence ids, sentence)
    sources_dob = sorted({s for _, src in pic["dobs"] for s in src})
    sr = list(h["sr"]) if h["dob_origin"] == "search" else []

    # ---- type
    if pic["is_person"] != h["is_person"]:
        kind = "an individual" if pic["is_person"] else "a company"
        hkind = "an individual" if h["is_person"] else "a company"
        fired["ENTITY_TYPE_MISMATCH"] = (
            [pic["anchor"]],
            "The customer is %s (%s) but the hit %s is %s." % (kind, pic["anchor"], h["name"], hkind))

    # ---- deceased
    if h["death"] and screened_at and before(h["death"], screened_at):
        fired["DECEASED"] = (
            [pic["anchor"]],
            "The hit's profile gives a date of death of %s, before the customer was screened on %s." %
            (fmt_date(h["death"]), fmt_date(screened_at)))

    # ---- temporal
    if h["report"] and pic["dobs"]:
        rep = earliest(h["report"])
        if pic["is_person"] and h["is_person"]:
            if all(rep < add_years(earliest(c), 16) for c, _ in pic["dobs"]):
                c = pic["dobs"][0][0]
                age = rep.year - c[0]
                fired["TEMPORAL_IMPOSSIBILITY"] = (
                    sources_dob,
                    "The customer was born %s (%s), so they were at most %d when the hit's profile reports "
                    "activity on %s." % (("on " if c[2] else "in ") + fmt_date(c) if len(pic["dobs"]) == 1
                                         else "in %d" % c[0],
                                         ", ".join(sources_dob), max(age, 0), fmt_date(h["report"])))
        elif company and not h["is_person"]:
            if all(earliest(c) > latest(h["report"]) for c, _ in pic["dobs"]):
                fired["TEMPORAL_IMPOSSIBILITY"] = (
                    sources_dob,
                    "The customer was incorporated in %s (%s), after the hit's profile reports activity on %s." %
                    (fmt_date(pic["dobs"][0][0]), ", ".join(sources_dob), fmt_date(h["report"])))

    # ---- date of birth
    dob_agree = None
    if pic["dobs"] and h["dates"] and pic["is_person"] == h["is_person"]:
        if all(all(contradicted(c, d) for d in h["dates"]) for c, _ in pic["dobs"]):
            c = pic["dobs"][0][0]
            word = "date of birth" if pic["is_person"] else "incorporation date"
            fired["DOB_MISMATCH"] = (
                sources_dob + sr,
                "The customer's %s is %s (%s) but %s." % (word, fmt_date(c), ", ".join(sources_dob),
                                                          hit_dob_phrase(h)))
            dob_agree = False
        elif all(any(agrees(c, d) for d in h["dates"]) for c, _ in pic["dobs"]):
            dob_agree = True

    # ---- name
    usable_customer = [n for n in pic["names"] if rules.real_part_count(n, company) >= (1 if company else 2)]
    hit_usable = [n for n in h["names"] if rules.parts(n, company)]
    name_match = None
    if usable_customer and hit_usable:
        name_match = rules.any_agree(usable_customer, hit_usable, company)
    if pic["verified_name"] and rules.real_part_count(pic["verified_name"], company) >= (1 if company else 2) \
            and hit_usable and pic["is_person"] == h["is_person"]:
        if rules.any_agree([pic["verified_name"]], hit_usable, company) is None:
            fired["NAME_MISMATCH_VERIFIED"] = (
                [pic["verified_name_src"]],
                "The customer's verified name is %s (%s), which does not match the hit's name %s." %
                (pic["verified_name"], pic["verified_name_src"], h["name"]))
    name_agree = name_match is not None if usable_customer and hit_usable else None

    # ---- location
    cset, hset = set(pic["countries"]), set(h["countries"])
    shared = sorted(cset & hset)
    import countries as _c
    # a place we cannot recognise as a country (either side) makes the location test unknown
    all_known = all(_c.known(x) for x in hset) and all(_c.known(x) for x in cset)
    if cset and hset and not shared and all_known:
        if not (name_agree and dob_agree):
            ev = sorted(set(pic["records"]) | {s for v in pic["countries"].values() for s in v})
            fired["LOCATION_MISMATCH"] = (
                ev,
                "The customer is linked only to %s but the hit is linked only to %s." %
                (", ".join(sorted(cset)), ", ".join(sorted(hset))))

    result = {"name_agree": name_agree, "dob_agree": dob_agree, "shared": shared, "name_match": name_match}
    if fired:
        codes = [c for c in CLEAR_ORDER if c in fired]
        evidence = sorted({e for c in codes for e in fired[c][0]})
        result.update(decision="DIFFERENT_ENTITY", codes=codes, evidence=evidence,
                      sentences=[fired[c][1] for c in codes])
        return result

    facts = []
    if dob_agree and pic["dobs"]:
        c = pic["dobs"][0][0]
        facts.append("The customer's date of birth %s (%s) agrees with %s." %
                     (fmt_date(c), ", ".join(sources_dob), hit_dob_short(h)))
    if shared:
        facts.append("Both the customer and the hit are linked to %s." % ", ".join(shared))
    if name_match:
        facts.append("The customer's name %s agrees with the hit's name %s." % (name_match[0], name_match[1]))

    # Evidence for a match or a review: the records that tie the shared country (a customer record,
    # an identity check or a note saying where they lived), plus the birth-date source when the dates
    # agree. Without a shared country, the customer records and the newest check that was read.
    shared_src = sorted({s for c in shared for s in pic["countries"].get(c, [])})
    base_ev = list(shared_src)
    if not base_ev:
        base_ev = list(pic["records"])
        if pic["latest_check"]:
            base_ev.append(pic["latest_check"])
    location_conflict = bool(cset and hset and not shared and all_known)
    if location_conflict:
        facts.append("The customer is linked only to %s but the hit is linked only to %s; a country difference "
                     "does not clear a hit whose name and date of birth agree." %
                     (", ".join(sorted(cset)), ", ".join(sorted(hset))))
    if pic["is_person"] and h["is_person"] and name_agree and dob_agree and not location_conflict:
        code = "NAME_DOB_LOCATION_MATCH" if shared else "NAME_DOB_MATCH"
        result.update(decision="SAME_ENTITY", codes=[code], evidence=sorted(set(base_ev + sources_dob + sr)),
                      sentences=facts + ["Nothing in the customer's records contradicts the hit."])
        return result
    if company and not h["is_person"] and name_agree and shared:
        result.update(decision="SAME_ENTITY", codes=["NAME_LOCATION_MATCH"], evidence=sorted(set(base_ev)),
                      sentences=facts + ["Nothing in the customer's records contradicts the hit."])
        return result

    missing = []
    if not pic["is_person"]:
        if not shared:
            missing.append("no country is shared with it" if cset and hset else "the countries cannot be compared")
    elif dob_agree is None:
        missing.append("the date of birth cannot be compared with it" if not (pic["dobs"] and h["dates"])
                       else "the dates of birth neither clearly agree nor contradict it")
    if name_agree is None:
        missing.append("the names cannot be compared (too few real name parts)")
    elif name_agree is False:
        missing.append("the names do not clearly agree")
    if not missing:
        missing.append("the evidence does not settle it")
    sentences = facts + ["Nothing in the customer's records contradicts the hit, and %s, so the hit can be neither "
                         "cleared nor confirmed." % " and ".join(missing)]
    ev = list(base_ev) + (sources_dob if dob_agree else [])
    result.update(decision="REVIEW", codes=["INSUFFICIENT_DATA"], evidence=sorted(set(ev)), sentences=sentences)
    return result

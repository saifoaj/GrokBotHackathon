"""Export everything the UI pages need into ui/data.js (window.DATA = {...}).

Usage: python3 ui/build_data.py [data_dir]      (default: dev)

It calls the same functions as system/run (People.resolve, People.picture, hits.picture,
decide.compare, main.decide_hit), so the pages show what the system decided. The per-rule
trace is derived from compare()'s own outputs: which codes fired, name_agree, dob_agree, shared.
data.js contains dev data: it is gitignored and must not be shared outside the event.
"""
import json
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "system"))

import countries  # noqa: E402
import fact_check  # noqa: E402
import hits as hitmod  # noqa: E402
import main as mainmod  # noqa: E402
from customers import People, parse_kyc_note  # noqa: E402
from decide import compare  # noqa: E402
from load import Data, approved, low, read_jsonl  # noqa: E402
from names import NameRules  # noqa: E402
from norm import add_years, earliest, fmt_date, parse_date, phone_key  # noqa: E402

KIND = {"listed": "listed date of birth", "profile": "profile notes", "profile_date": "profile notes", "search": "search results"}


def fd(d):
    return fmt_date(d) if d else None


def rule_trace(pic, h, r, rules, screened_at):
    """One entry per rule, in POLICY order: fired / passed / blocked / n/a, with a plain detail."""
    fired, t = set(r["codes"]) if r["decision"] == "DIFFERENT_ENTITY" else set(), []
    add = lambda rule, pol, st, detail: t.append({"rule": rule, "policy": pol, "status": st, "detail": detail})
    kinds = ("individual" if pic["is_person"] else "company", "individual" if h["is_person"] else "company")
    add("ENTITY_TYPE_MISMATCH", "6 Type", "fired" if "ENTITY_TYPE_MISMATCH" in fired else "passed", "customer is %s, hit is %s" % kinds)
    if h["death"]:
        add("DECEASED", "6 Time", "fired" if "DECEASED" in fired else "passed", "date of death %s, screened %s" % (fd(h["death"]), fd(screened_at)))
    else:
        add("DECEASED", "6 Time", "n/a", "the hit gives no date of death")
    if not h["report"]:
        add("TEMPORAL_IMPOSSIBILITY", "6 Time", "n/a", "the hit's profile has no dated report ('According to …; Month D, YYYY:')")
    elif not pic["dobs"]:
        add("TEMPORAL_IMPOSSIBILITY", "6 Time", "n/a", "earliest report %s, but the customer has no date of birth" % fd(h["report"]))
    else:
        c, rep = pic["dobs"][0][0], h["report"]
        age = max(rep[0] - c[0] - (1 if (rep[1], rep[2]) < (c[1] or 1, c[2] or 1) else 0), 0)
        if "TEMPORAL_IMPOSSIBILITY" in fired:
            add("TEMPORAL_IMPOSSIBILITY", "6 Time", "fired", "at most %d years old at the earliest report %s (under 16)" % (age, fd(rep)))
        else:
            add("TEMPORAL_IMPOSSIBILITY", "6 Time", "passed", "customer could be %d at the earliest report %s" % (age, fd(rep)))
    hd = ", ".join(fd(d) for d in h["dates"])
    if not h["dates"]:
        add("DOB_MISMATCH", "6 Date of birth", "n/a", "the hit gives no date of birth (field, profile notes or a qualifying search result)")
    elif not pic["dobs"]:
        add("DOB_MISMATCH", "6 Date of birth", "n/a", "the customer has no date of birth on file")
    elif "DOB_MISMATCH" in fired:
        add("DOB_MISMATCH", "6 Date of birth", "fired", "%s contradicts every hit date (%s)" % (fd(pic["dobs"][0][0]), hd))
    else:
        cd = " / ".join(fd(d) for d, _ in pic["dobs"])
        add("DOB_MISMATCH", "6 Date of birth", "passed", "%s is not contradicted by %s" % (cd, "an agreeing hit date" if r["dob_agree"] else "every hit date (%s)" % hd))
    cset, hset, shared = set(pic["countries"]), set(h["countries"]), r["shared"]
    known = all(countries.known(x) for x in cset | hset)
    if "LOCATION_MISMATCH" in fired:
        add("LOCATION_MISMATCH", "6 Location", "fired", "customer only %s; hit only %s" % (", ".join(sorted(cset)), ", ".join(sorted(hset))))
    elif cset and hset and not shared and known:
        add("LOCATION_MISMATCH", "6 Location", "blocked", "%s vs %s differ, but name and date of birth agree, so a country difference cannot clear" % (", ".join(sorted(cset)), ", ".join(sorted(hset))))
    elif shared:
        add("LOCATION_MISMATCH", "6 Location", "passed", "both linked to %s" % ", ".join(shared))
    else:
        add("LOCATION_MISMATCH", "6 Location", "n/a", "the customer has no country" if not cset else "the hit has no country" if not hset else "a place could not be recognised as a country")
    company = not pic["is_person"]
    vn = pic["verified_name"]
    if "NAME_MISMATCH_VERIFIED" in fired:
        add("NAME_MISMATCH_VERIFIED", "6 Name / 8", "fired", "verified %s agrees with none of the hit's %d names" % (vn, len(h["names"])))
    elif not vn:
        add("NAME_MISMATCH_VERIFIED", "6 Name / 8", "n/a", "no verified legal name")
    elif rules.real_part_count(vn, company) < (1 if company else 2):
        add("NAME_MISMATCH_VERIFIED", "6 Name / 8", "n/a", "verified name %s has fewer than two real parts" % vn)
    else:
        m = r.get("name_match")
        add("NAME_MISMATCH_VERIFIED", "6 Name / 8", "passed", "verified %s agrees with %s" % (vn, m[1] if m else h["name"]))
    add("AGREEMENT", "7", "info", "name %s; date of birth %s; shared country: %s" % (
        "agrees" if r["name_agree"] else "does not agree", "agrees" if r["dob_agree"] else "does not agree", ", ".join(shared) or "none"))
    return t


def alignment(rules, cname, hname, company):
    a, b = rules.parts(cname, company), rules.parts(hname, company)
    pairs, used = [], set()
    for x in a:
        hit = None
        for j, y in enumerate(b):
            if j in used:
                continue
            if x == y:
                hit = (j, "identical")
                break
            if rules.part_match(x, y):
                ga, gb = rules.variant_of.get(x), rules.variant_of.get(y)
                hit = hit or (j, "name variant" if ga and gb and ga & gb else "spelling (edit distance)")
        if hit is None:
            for j in range(len(b) - 1):
                if j not in used and j + 1 not in used and x == b[j] + b[j + 1]:
                    pairs.append({"c": x, "h": b[j] + " " + b[j + 1], "how": "written together / apart"})
                    used |= {j, j + 1}
                    break
            else:
                pairs.append({"c": x, "h": None, "how": "no match"})
            continue
        used.add(hit[0])
        pairs.append({"c": x, "h": b[hit[0]], "how": hit[1]})
    return pairs + [{"c": None, "h": y, "how": "no match"} for j, y in enumerate(b) if j not in used]


def merges(records, data, people):
    out = []
    for i, a in enumerate(records):
        for b in records[i + 1:]:
            ca, cb = data.customer[a], data.customer[b]
            pa = {phone_key(ca.get("phone"))} | {phone_key(x.get("phone")) for x in data.accounts_of_customer.get(a, [])}
            pb = {phone_key(cb.get("phone"))} | {phone_key(x.get("phone")) for x in data.accounts_of_customer.get(b, [])}
            da, db = parse_date(ca.get("dob")), parse_date(cb.get("dob"))
            ph = sorted((pa & pb) - {None, ""})
            if ph and da and da == db and da[2]:
                out.append({"a": a, "b": b, "why": "same phone (%s) and same full date of birth %s" % (ph[0], fd(da))})
            docs = lambda cid: {re.sub(r"[^A-Z0-9]", "", str(k.get("document_number") or "").upper()) for k in data.checks_of_customer(cid) if approved(k)} - {""}
            common = docs(a) & docs(b)
            if common:
                out.append({"a": a, "b": b, "why": "same document number %s on approved checks" % sorted(common)[0]})
    if not out and len(records) > 1:
        out.append({"a": records[0], "b": records[1], "why": "linked through another record of the same person"})
    return out


PROVIDERS = ("Onfido", "Veriff", "Persona", "Sumsub", "Jumio", "Trulioo", "IDnow", "Mitek")
DOC_TYPES = ("passport", "national ID", "driving licence", "residence permit", "ID card")
SHORT_LISTING = [("sanctioned individual", "sanctioned"), ("politically exposed person", "politically exposed person"),
                 ("subject of enforcement action", "enforcement"), ("named in adverse media", "adverse media"),
                 ("state-owned enterprise official", "state-owned enterprise"), ("named on a watchlist", "watchlist")]
MONTH_YEAR = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December) (\d{4})\b")


def fit_rows(people, data, alert, screened, persons, linked):
    """Step 3: each candidate record and the screened-detail filters it passed (kept = it is one of the people)."""
    kept = {cid for p in persons for cid in p["anchors"]}
    cands = sorted(linked) if linked else people.search_candidates(screened)
    rows = []
    for cid in cands:
        c = data.customer.get(cid)
        if not c:
            continue
        checks = {k: (ok, detail) for k, ok, detail in people.fit_checks(c, screened)}
        dob = parse_date(c.get("dob"))
        rows.append({"id": cid, "kept": cid in kept, "name": c.get("full_name"),
                     "type": checks.get("type", (None,))[0], "dob": checks.get("dob", (None,))[0], "dob_year": dob[0] if dob else None,
                     "name_ok": checks.get("name", (None,))[0], "country": checks.get("country", (None,))[0], "city": checks.get("city", (None,))[0]})
    rows.sort(key=lambda r: (not r["kept"], -sum(1 for k in ("type", "dob", "name_ok") if r[k]), r["id"]))
    return rows[:8], len(rows)


def check_used(pic, data):
    """Step 5: the newest approved identity check that gave the verified facts."""
    src = pic.get("verified_name_src") or pic.get("verified_dob_src") or pic.get("latest_check")
    if not src:
        return None
    if src.startswith("KYC-"):
        k = next((k for cid in pic["records"] for k in data.checks_of_customer(cid) if k["check_id"] == src), None)
        if not k:
            return {"id": src}
        return {"id": src, "provider": k.get("provider"), "date": k.get("checked_at"), "result": k.get("result"),
                "doc": k.get("document_type"), "country": k.get("document_country"), "name": k.get("verified_name"),
                "dob": k.get("verified_dob"), "text": None}
    n = next((n for cid in pic["records"] for n in data.notes_by_email.get(low(data.customer[cid].get("email")), []) if n["note_id"] == src), None)
    if not n:
        return {"id": src}
    text = n.get("text") or ""
    f = parse_kyc_note(n) or {}
    return {"id": src, "provider": next((p for p in PROVIDERS if p.lower() in text.lower()), None), "date": n.get("created_at"),
            "result": "approved" if f.get("approved") else "not approved", "doc": next((d for d in DOC_TYPES if d.lower() in text.lower()), None),
            "country": f.get("country"), "name": f.get("name"), "dob": fd(f.get("dob")), "text": text}


def person_line(pic, data, merged):
    """Step 4: the records that are one person (anchor first), why, and each record's nationality / residence."""
    recs = [pic["anchor"]] + [c for c in pic["records"] if c != pic["anchor"]]
    whys = []
    for m in merged:
        if m["why"].startswith("same phone"):
            raw = data.customer[m["a"]].get("phone") or ""
            whys.append("same phone %s and DOB" % raw)
        else:
            whys.append(m["why"])
    places = ", ".join("%s/%s" % (data.customer[c].get("nationality") or "-", data.customer[c].get("residence_country") or "-") for c in recs)
    return {"records": recs, "why": "; ".join(dict.fromkeys(whys)) if whys else "no other record shares its phone and DOB, or a document", "places": places}


def field_countries(hit):
    out = {}
    for field in ("nationality", "citizenship", "countries", "addresses"):
        for v in hit.get(field) or []:
            c = countries.canon(v)
            if c:
                out.setdefault(c, [])
                if field not in out[c]:
                    out[c].append(field)
    if hit.get("place_of_birth"):
        c = countries.canon(hit["place_of_birth"])
        if c:
            out.setdefault(c, []).append("place of birth")
    return out


def describe_result(s, key_words, years, used):
    """'33 in May 2019, politically exposed person in Brazil → 1985 or 1986' / 'adverse media, United Kingdom'."""
    snippet = s.get("snippet") or ""
    kind = next((short for phrase, short in SHORT_LISTING if phrase in snippet.lower()), "no listing")
    stripped = snippet
    for w in key_words:
        stripped = re.sub(r"(?i)\b%s\b" % re.escape(w), " ", stripped)
    found = countries.find_in_text(stripped)
    place = ", ".join(found) or "no country"
    if used and years:
        m = MONTH_YEAR.search(snippet)
        if m:
            age = int(m.group(2)) - max(years)
            return "%d in %s %s, %s in %s → %s" % (age, m.group(1), m.group(2), kind, place, " or ".join(str(y) for y in sorted(years)))
        return "%s in %s → %s" % (kind, place, " or ".join(str(y) for y in sorted(years)))
    return "%s, %s" % (kind, place)


def main(folder):
    data = Data(folder)
    mainmod.DATA = data
    rules = NameRules(folder)
    checker = fact_check.maybe_checker()
    people = People(data, rules, checker=checker)
    if checker:
        mainmod.gather_reads(data, people, checker)
    labels = {r["hit_id"]: r for r in read_jsonl(folder, "practice_labels", "hit_id")} if os.path.exists(os.path.join(folder, "practice_labels.jsonl")) else {}
    records, out_hits = {}, []

    def keep(rid, kind, row):
        records[rid] = {"kind": kind, **row}

    for alert_id in sorted(data.hits_of_alert):
        alert = data.alert.get(alert_id, {})
        routes, screened, persons = people.resolve(alert) if alert else ([], {}, [])
        pics = [people.picture(p, screened) for p in persons]
        screened_at = parse_date(alert.get("screened_at"))
        link = next(({"kind": k, "value": alert[k]} for k in ("account_ref", "login_handle", "email") if alert.get(k)), None)
        linked = sorted({cid for k in ("account_ref", "login_handle") if alert.get(k)
                         for a in (data.accounts_by_id.get(str(alert[k]).strip().upper(), []) if k == "account_ref" else data.accounts_by_login.get(low(alert[k]), []))
                         for cid in [a["customer_id"]] if cid in data.customer}
                        | set(data.customer_by_email.get(low(alert.get("email")), []) if alert.get("email") else []))
        fits, n_cands = fit_rows(people, data, alert, screened, persons, linked)
        for hid in sorted(data.hits_of_alert[alert_id]):
            hit = data.hit[hid]
            h = hitmod.picture(hit, data, checker)
            line = mainmod.decide_hit(hit, alert, routes, screened, pics, rules, {}, checker)
            line["evidence"] = sorted(set(line["evidence"]))
            ppl = []
            for pic in pics:
                r = compare(pic, h, rules, screened_at)
                company = not pic["is_person"]
                for cid in pic["records"]:
                    keep(cid, "customer", data.customer[cid])
                    for k in data.checks_of_customer(cid):
                        keep(k["check_id"], "kyc", {**k, "customer_id": cid})
                notes = []
                for cid in pic["records"]:
                    for n in data.notes_by_email.get(low(data.customer[cid].get("email")), []) if low(data.customer[cid].get("email")) else []:
                        if str(n.get("channel")).lower() == "kyc":
                            f = parse_kyc_note(n) or {}
                            facts = {"approved": bool(f.get("approved")), "name": f.get("name"), "dob": fd(f.get("dob")), "country": f.get("country")}
                        else:
                            facts = {"countries": countries.find_in_text(n.get("text") or "")}
                            if not facts["countries"]:
                                continue
                        keep(n["note_id"], "note", {**n, "facts": facts})
                        notes.append(n["note_id"])
                cname = pic["verified_name"] or (pic["names"][0] if pic["names"] else "")
                hname = (r.get("name_match") or (None, h["name"]))[1]
                ppl.append({
                    "pid": pic["records"][0], "record": pic["anchor"], "records": pic["records"], "kind": "Individual" if pic["is_person"] else "Company",
                    "merges": merges(pic["records"], data, people) if len(pic["records"]) > 1 else [],
                    "names": [{"v": n, "src": pic["names_src"][0] if pic["names_src"] else None} for n in pic["names"]],
                    "verified_name": {"v": pic["verified_name"], "src": pic["verified_name_src"]} if pic["verified_name"] else None,
                    "dobs": [{"v": fd(d), "src": src} for d, src in pic["dobs"]], "dob_verified": bool(pic["verified_dob"]),
                    "countries": [{"code": c, "name": c, "src": s} for c, srcs in pic["countries"].items() for s in srcs],
                    "notes": sorted(set(notes)), "checks": sorted({k["check_id"] for cid in pic["records"] for k in data.checks_of_customer(cid)}),
                    "decision": r["decision"], "codes": r["codes"], "sentences": r["sentences"], "evidence": r["evidence"],
                    "trace": rule_trace(pic, h, r, rules, screened_at), "alignment": alignment(rules, cname, hname, company),
                    "check_used": check_used(pic, data),
                    "person_line": person_line(pic, data, merges(pic["records"], data, people) if len(pic["records"]) > 1 else []),
                })
            searched = []
            if h["is_person"] and h["countries"]:
                seen_sr = set()
                for key, checks in hitmod.search_checks(hit, data, h["countries"]):
                    for s, ok, why, years in checks:
                        if s["result_id"] in seen_sr:
                            continue
                        seen_sr.add(s["result_id"])
                        keep(s["result_id"], "search", s)
                        used = s["result_id"] in h["sr"]
                        searched.append({"id": s["result_id"], "used": used, "accepted": ok, "why": why, "years": years or [],
                                         "text": describe_result(s, key.split(), years, used)})
            kind = KIND.get(h["dob_origin"], "listed date of birth")
            if h["dob_origin"] in ("profile", "search") and all(d[1] is None for d in h["dates"]):
                hdobs = [{"years": sorted({d[0] for d in h["dates"]}), "month": None, "day": None, "src": (h["sr"] or [hid + "#profile_notes"])[0], "kind": kind}] if h["dates"] else []
            else:
                hdobs = [{"years": [d[0]], "month": d[1], "day": d[2], "src": hid + "#dobs", "kind": kind} for d in h["dates"]]
            notes_text = hit.get("profile_notes") or ""
            lab = labels.get(hid)
            out_hits.append({
                "hit_id": hid, "alert_id": alert_id, "decision": line["decision"], "code": line["reason_code"], "record": line["customer_record_id"],
                "reason": line["reason"], "evidence": line["evidence"],
                "label": {"decision": lab["decision"], "codes": lab["reason_codes"], "records": lab["customer_record_ids"], "evidence": lab["evidence"], "reason": lab["reference_reason"]} if lab else None,
                "alert": {"screened_at": alert.get("screened_at"), "customer": alert.get("customer") or {}, "link": link, "linked": linked, "routes": routes, "hit_ids": alert.get("hit_ids", []),
                          "links": {k: alert.get(k) for k in ("account_ref", "login_handle", "email")}},
                "fit": fits, "n_candidates": n_cands,
                "persons": ppl,
                "hit": {
                    "name": h["name"], "kind": "Individual" if h["is_person"] else "Company", "aliases": hit.get("aliases") or [], "categories": hit.get("categories") or [],
                    "primary": hit.get("primary_category"), "match_score": hit.get("match_score"), "position": hit.get("position_or_allegation"),
                    "raw_dobs": hit.get("dobs") or [], "deceased_date": hit.get("deceased_date"),
                    "names": [{"v": n, "src": "name"} for n in h["names"]],
                    "dobs": hdobs, "countries": [{"code": c, "name": c, "src": "hit"} for c in h["countries"]],
                    "reports": [fd(h["report"])] if h["report"] else [], "death": fd(h["death"]),
                    "first_paragraph": re.split(r"\n\s*\n", notes_text.strip())[0][:1500] if notes_text else None,
                    "listing_phrase": hitmod.listing_phrase(hit), "searched": searched,
                    "field_countries": field_countries(hit), "dob_origin": h["dob_origin"],
                    "listing": hit.get("primary_category") or (hit.get("categories") or ["-"])[0],
                },
            })
    out_hits.sort(key=lambda x: x["hit_id"])
    by_alert = {}
    for x in out_hits:
        by_alert.setdefault(x["alert_id"], []).append(x["decision"])
    stats = {"decisions": Counter(x["decision"] for x in out_hits), "codes": Counter(x["code"] for x in out_hits),
             "alerts": len(by_alert), "alerts_closed": sum(all(d == "DIFFERENT_ENTITY" for d in v) for v in by_alert.values()),
             "labels": len(labels), "labels_right": sum(1 for x in out_hits if x["label"] and x["label"]["decision"] == x["decision"])}
    path = os.path.join(HERE, "data.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("window.DATA = ")
        json.dump({"hits": out_hits, "records": records, "stats": stats,
                   "countryNames": {n: n for n in sorted(countries._TABLE)}}, fh, ensure_ascii=False, separators=(",", ":"), default=str)
        fh.write(";\n")
    print("wrote %s: %d hits, %d source records, %.1f MB" % (path, len(out_hits), len(records), os.path.getsize(path) / 1e6))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "dev"))

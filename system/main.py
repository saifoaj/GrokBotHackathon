"""Screening review system: main.py <data folder> <output file>.

Deterministic, rules only, standard library only. Every hit gets exactly one line;
any failure for a hit falls back to REVIEW / INSUFFICIENT_DATA.
"""

import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from customers import People, is_person  # noqa: E402
from decide import compare  # noqa: E402
import hits as hitmod  # noqa: E402
from load import Data  # noqa: E402
from names import NameRules  # noqa: E402
from norm import parse_date  # noqa: E402


def fallback(hit_id, alert_id, why):
    return {"hit_id": hit_id, "alert_id": alert_id, "decision": "REVIEW", "reason_code": "INSUFFICIENT_DATA",
            "customer_record_id": None, "evidence": [],
            "reason": "The hit could not be assessed automatically (%s). It is left for a person to review." % why}


def limit_sentences(sentences):
    out = [s.strip() for s in sentences if s and s.strip()]
    if len(out) > 4:
        out = out[:4]
    return " ".join(out)


def decide_hit(hit, alert, routes, screened, pics, rules, trace):
    h = hitmod.picture(hit, None if False else DATA)
    screened_at = parse_date(alert.get("screened_at")) if alert else None
    trace["hit"] = {k: h[k] for k in ("names", "countries", "dates", "dob_origin", "sr", "death", "report")}
    if not pics:
        sentences = ["No customer record in the bank's systems fits the screened details for %s." %
                     (screened.get("name") or "this alert")]
        if screened.get("entity_type") and is_person(screened.get("entity_type")) != h["is_person"]:
            pass
        sentences.append("Without the customer's facts the hit %s can be neither cleared nor confirmed, so a person "
                         "must review it." % h["name"])
        return {"decision": "REVIEW", "reason_code": "INSUFFICIENT_DATA", "customer_record_id": None,
                "evidence": [], "reason": limit_sentences(sentences)}
    results = [compare(p, h, rules, screened_at) for p in pics]
    trace["people"] = [{"records": p["records"], "decision": r["decision"], "codes": r["codes"]}
                       for p, r in zip(pics, results)]
    decisions = sorted({r["decision"] for r in results})
    if len(pics) == 1:
        r, p = results[0], pics[0]
        sentences = list(r["sentences"])
        if r["decision"] == "DIFFERENT_ENTITY" and len(sentences) < 2:
            sentences.append("This contradiction shows the hit %s is a different %s from customer %s." %
                             (h["name"], "person" if h["is_person"] else "entity", p["anchor"]))
        if r["decision"] == "SAME_ENTITY" and len(sentences) < 2:
            sentences.append("The hit %s is therefore treated as the customer." % h["name"])
        return {"decision": r["decision"], "reason_code": r["codes"][0], "customer_record_id": p["anchor"],
                "evidence": r["evidence"], "reason": limit_sentences(sentences)}
    every = sorted({e for r in results for e in r["evidence"]})
    people_txt = "; ".join(",".join(p["records"]) for p in pics)
    if len(decisions) == 1:
        r = results[0]
        sentences = ["%d different people in the system of record fit the alert (%s) and each leads to the same "
                     "decision." % (len(pics), people_txt)] + list(r["sentences"])[:3]
        return {"decision": r["decision"], "reason_code": r["codes"][0], "customer_record_id": "AMBIGUOUS",
                "evidence": every, "reason": limit_sentences(sentences)}
    parts = ["%s: %s" % (p["anchor"], r["decision"].replace("_", " ").lower()) for p, r in zip(pics, results)]
    sentences = ["%d different people in the system of record fit the alert (%s)." % (len(pics), people_txt),
                 "They lead to different decisions for the hit %s (%s)." % (h["name"], "; ".join(parts[:6])),
                 "A person must decide which customer was screened."]
    return {"decision": "REVIEW", "reason_code": "AMBIGUOUS_CUSTOMER_RECORD", "customer_record_id": "AMBIGUOUS",
            "evidence": every, "reason": limit_sentences(sentences)}


DATA = None


def main(argv):
    global DATA
    if len(argv) != 3:
        sys.stderr.write("usage: main.py <data folder> <output file>\n")
        return 2
    folder, output = argv[1], argv[2]
    start = time.time()
    DATA = Data(folder)
    rules = NameRules(folder)
    people = People(DATA, rules)
    sys.stderr.write("loaded in %.1fs\n" % (time.time() - start))
    lines, traces = {}, []
    for alert_id in sorted(DATA.hits_of_alert):
        alert = DATA.alert.get(alert_id, {})
        hit_ids = sorted(DATA.hits_of_alert[alert_id])
        try:
            routes, screened, persons = people.resolve(alert) if alert else ([], {}, [])
            pics = [people.picture(p, screened) for p in persons]
        except Exception as error:  # never drop a hit
            traceback.print_exc()
            for hid in hit_ids:
                lines[hid] = fallback(hid, alert_id, "customer lookup failed: %s" % type(error).__name__)
            continue
        for hid in hit_ids:
            trace = {"hit_id": hid, "alert_id": alert_id, "routes": routes,
                     "people": [p["records"] for p in persons]}
            try:
                out = decide_hit(DATA.hit[hid], alert, routes, screened, pics, rules, trace)
                line = {"hit_id": hid, "alert_id": DATA.hit[hid].get("alert_id") or alert_id}
                line.update(out)
                line["evidence"] = sorted(set(line["evidence"]))
                lines[hid] = line
            except Exception as error:
                traceback.print_exc()
                lines[hid] = fallback(hid, alert_id, "comparison failed: %s" % type(error).__name__)
            trace["decision"] = lines[hid]["decision"]
            trace["code"] = lines[hid]["reason_code"]
            traces.append(trace)
    for h in DATA.hits:  # any hit not reached above still gets a line
        if h["hit_id"] not in lines:
            lines[h["hit_id"]] = fallback(h["hit_id"], h.get("alert_id"), "no alert found")
    order = sorted(lines.values(), key=lambda r: (str(r["alert_id"]), r["hit_id"]))
    keys = ["hit_id", "alert_id", "decision", "reason_code", "customer_record_id", "reason", "evidence"]
    with open(output, "w", encoding="utf-8", newline="\n") as handle:
        for row in order:
            handle.write(json.dumps({k: row[k] for k in keys}, ensure_ascii=False) + "\n")
    debug = os.environ.get("SCREEN_TRACE")
    if debug:
        with open(debug, "w", encoding="utf-8", newline="\n") as handle:
            for t in traces:
                handle.write(json.dumps(t, ensure_ascii=False, default=str) + "\n")
    sys.stderr.write("%d lines written in %.1fs\n" % (len(order), time.time() - start))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

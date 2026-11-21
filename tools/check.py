"""Strict check: a hit passes only when it meets every requirement the organisers score.

    python tools/check.py <data folder> <output file> [--all]

For each hit that has an intended outcome (practice_labels.jsonl) it compares the intended
and the generated outcome on every requirement:

  decision      the same decision                                   (decisions, 75%)
  reason code   one of the accepted codes for that decision          (reasons, 25%)
  customer      an accepted customer record (AMBIGUOUS / null rules)
  evidence      the deciding records: nothing missing, nothing extra
  reason        2 to 4 sentences
  own facts     the reason uses this hit's own facts (its name, a birth year or a country)

and prints the summary, the decision score as validate.py computes it, a count of failures by
requirement, and every failing hit with intended against generated. Exit code 1 if any hit fails.
"""

import json
import os
import re
import sys

COST = {"DIFFERENT_ENTITY": {"DIFFERENT_ENTITY": 0, "SAME_ENTITY": 3, "REVIEW": 3},
        "SAME_ENTITY": {"DIFFERENT_ENTITY": 60, "SAME_ENTITY": 0, "REVIEW": 2},
        "REVIEW": {"DIFFERENT_ENTITY": 60, "SAME_ENTITY": 2, "REVIEW": 0}}
REQUIREMENTS = ["decision", "reason code", "all reasons", "customer", "evidence", "reason", "own facts"]

# How each reason code's grounds read in a reason. "all reasons" passes only when every code the
# intended outcome lists is stated (two clearing reasons means both contradictions were found).
GROUNDS = {
    "TEMPORAL_IMPOSSIBILITY": [r"at most \d+ when|incorporated (?:in|on) .* after"],
    "DOB_MISMATCH": [r"(?:date of birth|incorporation date) is .* but"],
    "LOCATION_MISMATCH": [r"linked only to .* but the hit is linked only to"],
    "NAME_MISMATCH_VERIFIED": [r"verified name is .* does not match"],
    "ENTITY_TYPE_MISMATCH": [r"customer is (?:an individual|a company) .* but the hit"],
    "DECEASED": [r"date of death"],
    "NAME_DOB_LOCATION_MATCH": [r"date of birth .* agrees", r"both the customer and the hit are linked to",
                                r"name .* agrees with the hit's name"],
    "NAME_DOB_MATCH": [r"date of birth .* agrees", r"name .* agrees with the hit's name"],
    "NAME_LOCATION_MATCH": [r"both the customer and the hit are linked to", r"name .* agrees with the hit's name"],
    "INSUFFICIENT_DATA": [r"neither cleared nor confirmed|cannot be (?:compared|settled)|must review|cannot settle"],
    "AMBIGUOUS_CUSTOMER_RECORD": [r"different people .* fit the alert"],
}


def grounds_stated(code, reason):
    return all(re.search(p, reason, re.I | re.S) for p in GROUNDS.get(code, []))


def read_jsonl(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sentences(text):
    """Count sentences, not counting initials such as 'Carl E. Andersen' or 'U.S.' as breaks."""
    text = re.sub(r"\b([A-Z])\.", r"\1", text or "")
    text = re.sub(r"\b(?:Mr|Mrs|Ms|Dr|St|Jr|Sr|No|Inc|Ltd|Co|Corp|vs|etc)\.", "x", text)
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", text.strip()) if s.strip()]


def hit_facts(hit):
    """Strings from the hit's own profile that a reason grounded in this hit would quote."""
    out = set()
    for n in [hit.get("name")] + list(hit.get("aliases") or []):
        for part in re.split(r"[^\w]+", str(n or "")):
            if len(part) >= 4:
                out.add(part.lower())
    for d in hit.get("dobs") or []:
        out.update(re.findall(r"\d{4}", str(d)))
    for f in ("countries", "nationality", "citizenship"):
        for c in hit.get(f) or []:
            out.add(str(c).lower())
    return out


def judge(label, line, hit):
    """Return {requirement: (ok, intended, generated)} for one hit."""
    r = {}
    want, got = label["decision"], (line or {}).get("decision")
    r["decision"] = (want == got, want, got)
    codes = label.get("reason_codes") or []
    r["reason code"] = ((line or {}).get("reason_code") in codes, " or ".join(codes), (line or {}).get("reason_code"))
    # Every intended code must hold. For a clear, each listed contradiction must be found and stated.
    # For a match, NAME_DOB_LOCATION_MATCH and NAME_DOB_MATCH are listed together because the first
    # includes the second; the generated code must state the grounds of each listed code.
    reason_text = (line or {}).get("reason") or ""
    if want == "SAME_ENTITY":
        need = codes
    else:
        need = codes if got == want else []
    missing_grounds = [c for c in need if not grounds_stated(c, reason_text)]
    r["all reasons"] = (got == want and not missing_grounds, "grounds of " + " and ".join(codes),
                        "missing grounds of " + ", ".join(missing_grounds) if missing_grounds
                        else ("all stated" if got == want else "wrong decision"))
    recs = label.get("customer_record_ids") or []
    rec = (line or {}).get("customer_record_id")
    ok = rec in recs or (recs == ["AMBIGUOUS"] and rec == "AMBIGUOUS") or (recs in ([None], []) and rec in (None, ""))
    r["customer"] = (ok, " or ".join(str(x) for x in recs) or "null", str(rec))
    exp, ev = set(label.get("evidence") or []), set((line or {}).get("evidence") or [])
    missing, extra = sorted(exp - ev), sorted(ev - exp)
    r["evidence"] = (not missing and not extra, ", ".join(sorted(exp)) or "(none)",
                     ", ".join(sorted(ev)) or "(none)" + ("" if not (missing or extra) else ""))
    r["evidence"] += (missing, extra)
    n = len(sentences((line or {}).get("reason")))
    r["reason"] = (2 <= n <= 4, "2 to 4 sentences", "%d sentence%s" % (n, "" if n == 1 else "s"))
    reason = ((line or {}).get("reason") or "").lower()
    facts = hit_facts(hit)
    used = sorted(f for f in facts if f in reason)
    r["own facts"] = (bool(used) or not facts, "names, dates or countries of %s" % hit.get("hit_id"),
                      ", ".join(used[:4]) or "none of the hit's facts")
    return r


def check(folder, output):
    labels = {l["hit_id"]: l for l in read_jsonl(os.path.join(folder, "practice_labels.jsonl"))}
    lines = {l["hit_id"]: l for l in read_jsonl(output)}
    hits = {h["hit_id"]: h for h in read_jsonl(os.path.join(folder, "hits.jsonl"))}
    results = {}
    for hid, lab in sorted(labels.items()):
        res = judge(lab, lines.get(hid), hits.get(hid, {"hit_id": hid}))
        failed = [k for k in REQUIREMENTS if not res[k][0]]
        got = (lines.get(hid) or {}).get("decision")
        status = "pass" if not failed else ("unsafe" if got == "DIFFERENT_ENTITY" != lab["decision"] else "fail")
        results[hid] = {"status": status, "failed": failed, "req": res,
                        "cost": COST[lab["decision"]].get(got or "REVIEW", 0), "why": why(lab, res, failed)}
    return results


def why(lab, res, failed):
    """One plain sentence saying why the hit passed or failed."""
    if not failed:
        return ("Passed: decision %s and reason code %s match the intended outcome, every intended reason (%s) is "
                "stated, the customer record %s is accepted, the evidence is exactly %s, and the reason is %s "
                "using the hit's own facts (%s)." % (
                    res["decision"][2], res["reason code"][2], " and ".join(lab["reason_codes"]), res["customer"][2],
                    res["evidence"][2], res["reason"][2], res["own facts"][2]))
    parts = []
    for k in failed:
        q = res[k]
        if k == "evidence":
            parts.append("evidence missing %s and extra %s" % (", ".join(q[3]) or "nothing", ", ".join(q[4]) or "nothing"))
        else:
            parts.append("%s: intended %s, generated %s" % (k, q[1], q[2]))
    return "Failed: " + "; ".join(parts) + "."


def main(argv):
    if len(argv) < 3:
        sys.stderr.write(__doc__)
        return 2
    folder, output = argv[1], argv[2]
    res = check(folder, output)
    labels = {l["hit_id"]: l for l in read_jsonl(os.path.join(folder, "practice_labels.jsonl"))}
    n = len(res)
    full = sum(1 for r in res.values() if r["status"] == "pass")
    cost = sum(r["cost"] for r in res.values())
    base = sum(COST[l["decision"]]["REVIEW"] for l in labels.values())
    print("%s: %d hits with an intended outcome" % (folder, n))
    print("  fully passed (every requirement): %d / %d" % (full, n))
    print("  unsafe clears: %d   decision score: %.1f" % (
        sum(1 for r in res.values() if r["status"] == "unsafe"), 100 * max(0.0, 1 - cost / base) if base else 0))
    print("  failures by requirement:")
    for k in REQUIREMENTS:
        bad = sum(1 for r in res.values() if not r["req"][k][0])
        print("    %-12s %4d failed  (%d passed)" % (k, bad, n - bad))
    shown = 0
    for hid, r in sorted(res.items(), key=lambda kv: (kv[1]["status"] != "unsafe", kv[0])):
        if r["status"] == "pass" and "--all" not in argv:
            continue
        if shown >= 40 and "--all" not in argv:
            print("  ... %d more failing hits (use --all)" % (sum(1 for x in res.values() if x["status"] != "pass") - 40))
            break
        shown += 1
        print("\n  %s  %s  %s" % (hid, r["status"].upper(), r["why"]))
        for k in r["failed"]:
            q = r["req"][k]
            print("    %-12s intended: %s" % (k, q[1]))
            print("    %-12s generated: %s" % ("", q[2]))
            if k == "evidence":
                print("    %-12s missing: %s | extra: %s" % ("", ", ".join(q[3]) or "-", ", ".join(q[4]) or "-"))
    return 0 if full == n else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

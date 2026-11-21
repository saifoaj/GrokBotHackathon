"""Export everything the UI mockups need into ui/data.js (window.DATA = {...}).

Usage: python3 ui/build_data.py [data_dir]      (default: dev)
data.js contains dev data: it is gitignored and must not be shared outside the event.
"""
import json
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "system"))
import screen as S  # noqa: E402


def date_s(d):
    return str(d) if d else None


def name_alignment(cparts, hparts):
    """Which customer name part matched which hit part, and how (display only)."""
    pairs, used = [], set()
    for a in cparts:
        best = None
        for j, b in enumerate(hparts):
            if j in used:
                continue
            if a == b:
                best = (j, "identical")
                break
            if a in S.VARIANT and S.VARIANT.get(b) == S.VARIANT[a]:
                best = best or (j, "name variant")
            elif S.part_match(a, b):
                best = best or (j, "spelling (edit distance)")
        if best is None:                                   # joined parts: Abdulsalam ~ Abdul Salam
            for j in range(len(hparts) - 1):
                if j not in used and j + 1 not in used and S.part_match(a, hparts[j] + hparts[j + 1]):
                    pairs.append({"c": a, "h": hparts[j] + " " + hparts[j + 1], "how": "written together / apart"})
                    used |= {j, j + 1}
                    break
            else:
                pairs.append({"c": a, "h": None, "how": "no match"})
            continue
        used.add(best[0])
        pairs.append({"c": a, "h": hparts[best[0]], "how": best[1]})
    pairs += [{"c": None, "h": b, "how": "no match"} for j, b in enumerate(hparts) if j not in used]
    return pairs


def merge_reasons(recs, ix, phones):
    out = []
    for i, a in enumerate(recs):
        for b in recs[i + 1:]:
            da, db = ix["dob"][a], ix["dob"][b]
            shared = sorted((phones[a] & phones[b]) - {None})
            if shared and da and da == db and da.full:
                out.append({"a": a, "b": b, "why": f"same phone (+{shared[0]}) and same full date of birth {da}"})
            docs_a = {S.norm_doc(k["document_number"]) for k in ix["checks"][a] if k["result"] == "approved"} - {None}
            docs_b = {S.norm_doc(k["document_number"]) for k in ix["checks"][b] if k["result"] == "approved"} - {None}
            if docs_a & docs_b:
                out.append({"a": a, "b": b, "why": f"same document number {sorted(docs_a & docs_b)[0]} on approved checks"})
    return out


def main(data_dir):
    data = S.load(data_dir)
    ix = S.build_index(data)
    alerts = {a["alert_id"]: a for a in data["alerts"]}
    labels = {}
    lp = os.path.join(data_dir, "practice_labels.jsonl")
    if os.path.exists(lp):
        with open(lp, encoding="utf-8") as fh:
            labels = {r["hit_id"]: r for r in map(json.loads, fh) if r}
    phones = {}
    for a in data["accounts"]:
        phones.setdefault(a["customer_id"], set()).add(S.norm_phone(a["phone"]))
    for c in data["customers"]:
        phones.setdefault(c["record_id"], set()).add(S.norm_phone(c["phone"]))
    sr_by_id = {r["result_id"]: r for r in data["search_results"]}
    notes_by_id = {n["note_id"]: n for n in data["notes"]}
    records, hits_out = {}, []

    def keep(rid, kind, row):
        records[rid] = {"kind": kind, **row}

    for hit in sorted(data["hits"], key=lambda h: h["hit_id"]):
        alert = alerts[hit["alert_id"]]
        hp = S.hit_profile(hit, ix)
        res = S.decide_hit(hit, alert, ix, hp)
        pids, chosen = S.resolve(alert, ix)
        link = next(({"kind": k, "value": alert[k]} for k in ("account_ref", "login_handle", "email") if alert.get(k)), None)
        if link and link["kind"] == "account_ref":
            linked = ix["accounts"].get(link["value"], [])
        elif link and link["kind"] == "login_handle":
            linked = ix["login"].get(S.fold(link["value"]), [])
        elif link:
            linked = ix["email"].get(S.fold(link["value"]), [])
        else:
            linked = []
        persons = []
        for pid in pids:
            cp = S.customer_profile(pid, ix, alert.get("screened_at"))
            d = S.decide(cp, hp, alert)
            for rid in cp.records:
                keep(rid, "customer", ix["cust"][rid])
                for k in ix["checks"][rid]:
                    keep(k["check_id"], "kyc", {**k, "customer_id": rid})
            emails = sorted({S.fold(ix["cust"][r]["email"]) for r in cp.records if ix["cust"][r]["email"]})
            used_notes = []
            for e in emails:
                for n in ix["notes"][e]:
                    f = ix["note_facts"][n["note_id"]]
                    keep(n["note_id"], "note", {**n, "facts": {k: (date_s(v) if isinstance(v, S.Date) else v) for k, v in f.items()}})
                    used_notes.append(n["note_id"])
            cname = cp.verified_name[1] if cp.verified_name else (cp.names[0][1] if cp.names else [])
            hname = next((n[1] for n in hp.names if S.names_agree(cname, n[1])), hp.names[0][1] if hp.names else [])
            persons.append({
                "pid": pid, "record": chosen[pid], "records": cp.records, "kind": cp.kind,
                "merges": merge_reasons(cp.records, ix, phones),
                "names": [{"v": n[0], "src": n[2]} for n in cp.names],
                "verified_name": {"v": cp.verified_name[0], "src": cp.verified_name[2]} if cp.verified_name else None,
                "dobs": [{"v": str(dd), "src": src} for dd, src in cp.dobs], "dob_verified": cp.dob_verified,
                "countries": [{"code": c, "name": S.COUNTRY_NAME.get(c, c), "src": src} for c, src in cp.countries],
                "notes": used_notes,
                "checks": [k["check_id"] for r in cp.records for k in ix["checks"][r]],
                "decision": d.decision, "codes": d.codes, "sentences": d.sentences, "evidence": d.evidence, "trace": d.trace,
                "alignment": name_alignment(cname, hname),
            })
        # search results considered for this hit
        cat = S.fold(hit.get("primary_category") or (hit.get("categories") or [""])[0])
        phrase = S.LISTING.get(cat, "named on a watchlist")
        tied = {c for c, _ in hp.countries}
        searched = []
        if hp.kind == "Individual":
            for key in sorted({tuple(sorted(n[1])) for n in hp.names}):
                for rid, f in ix["search"].get(key, []):
                    ok_l, ok_c = phrase in f["listing"], bool(tied & set(f["countries"]))
                    searched.append({"id": rid, "listing_ok": ok_l, "country_ok": ok_c, "years": list(f["years"]), "age": f["age"],
                                     "event": str(f["event"]), "countries": f["countries"], "used": any(x[3] == rid for x in hp.dobs)})
                    keep(rid, "search", sr_by_id[rid])
        notes_text = hit.get("profile_notes") or ""
        hits_out.append({
            "hit_id": hit["hit_id"], "alert_id": hit["alert_id"], "decision": res["decision"], "code": res["reason_code"],
            "record": res["customer_record_id"], "reason": res["reason"], "evidence": res["evidence"],
            "label": ({"decision": labels[hit["hit_id"]]["decision"], "codes": labels[hit["hit_id"]]["reason_codes"],
                       "records": labels[hit["hit_id"]]["customer_record_ids"], "evidence": labels[hit["hit_id"]]["evidence"],
                       "reason": labels[hit["hit_id"]]["reference_reason"]} if hit["hit_id"] in labels else None),
            "alert": {"screened_at": alert["screened_at"], "customer": alert["customer"], "link": link, "linked": sorted(set(linked)),
                      "hit_ids": alert["hit_ids"]},
            "persons": persons,
            "hit": {
                "name": hit["name"], "kind": hit["entity_type"], "aliases": hit.get("aliases") or [], "categories": hit.get("categories") or [],
                "primary": hit.get("primary_category"), "match_score": hit.get("match_score"), "position": hit.get("position_or_allegation"),
                "raw_dobs": hit.get("dobs") or [], "deceased_date": hit.get("deceased_date"), "life_status": hit.get("life_status"),
                "raw_countries": {k: hit.get(k) or [] for k in ("countries", "nationality", "citizenship", "addresses")} | {"place_of_birth": hit.get("place_of_birth")},
                "names": [{"v": n[0], "src": n[2]} for n in hp.names],
                "dobs": [{"years": list(x[0]), "month": x[1], "day": x[2], "src": x[3], "kind": x[4]} for x in hp.dobs],
                "countries": [{"code": c, "name": S.COUNTRY_NAME.get(c, c), "src": src} for c, src in hp.countries],
                "reports": [str(r) for r in hp.reports], "death": date_s(hp.death),
                "first_paragraph": re.split(r"\n\s*\n", notes_text.strip())[0][:1500] if notes_text else None,
                "report_lines": [m.group(0)[:300] for m in re.finditer(r"^\s*According to [^\n]*", notes_text, re.M)][:8],
                "listing_phrase": phrase, "searched": searched, "source_urls": (hit.get("source_urls") or [])[:4],
            },
        })
    for h in hits_out:
        for e in h["evidence"]:
            if e in notes_by_id and e not in records:
                keep(e, "note", notes_by_id[e])
    alert_status = {}
    for h in hits_out:
        alert_status.setdefault(h["alert_id"], []).append(h["decision"])
    stats = {
        "decisions": Counter(h["decision"] for h in hits_out), "codes": Counter(h["code"] for h in hits_out),
        "alerts": len(alert_status), "alerts_closed": sum(all(d == "DIFFERENT_ENTITY" for d in v) for v in alert_status.values()),
        "labels": len(labels), "labels_right": sum(1 for h in hits_out if h["label"] and h["label"]["decision"] == h["decision"]),
        "parse": dict(ix["stats"]),
    }
    out = os.path.join(HERE, "data.js")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("window.DATA = ")
        json.dump({"hits": hits_out, "records": records, "stats": stats, "countryNames": S.COUNTRY_NAME}, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")
    print(f"wrote {out}: {len(hits_out)} hits, {len(records)} source records, {os.path.getsize(out) / 1e6:.1f} MB")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "dev"))

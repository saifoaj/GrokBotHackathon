"""Audit report: every step the system took for each hit, with what it kept and what it filtered out.

Usage:
  python3 system/audit.py <data folder> <report.html>                 # the practice-label hits
  python3 system/audit.py <data folder> <report.html> --all           # every hit
  python3 system/audit.py <data folder> <report.html> H-582410 H-...  # chosen hits

It calls the same functions as system/run, so what it shows is what the system did.
"""

import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import countries  # noqa: E402
import hits as hitmod  # noqa: E402
from customers import People, is_person, parse_kyc_note  # noqa: E402
from decide import agrees, compare, contradicted  # noqa: E402
from load import Data, approved, low, read_jsonl  # noqa: E402
from names import NameRules  # noqa: E402
from norm import fmt_date, parse_date, phone_key  # noqa: E402
import main as mainmod  # noqa: E402

E = html.escape


def mark(ok):
    if ok is True:
        return '<span class="ok">pass</span>'
    if ok is False:
        return '<span class="no">fail</span>'
    return '<span class="un">skip</span>'


def table(headers, rows):
    out = ["<table><tr>" + "".join("<th>%s</th>" % E(h) for h in headers) + "</tr>"]
    for r in rows:
        out.append("<tr>" + "".join("<td>%s</td>" % c for c in r) + "</tr>")
    out.append("</table>")
    return "".join(out)


def d(date):
    return fmt_date(date) if date else "-"


class Auditor:
    def __init__(self, folder):
        self.data = Data(folder)
        mainmod.DATA = self.data
        self.rules = NameRules(folder)
        self.people = People(self.data, self.rules)
        self.labels = {r["hit_id"]: r for r in read_jsonl(folder, "practice_labels", "hit_id")}

    # ------------------------------------------------------------ steps

    def links(self, alert, screened):
        rows, linked = [], set()
        ref = alert.get("account_ref")
        if ref:
            accs = self.data.accounts_by_id.get(str(ref).strip().upper(), [])
            ids = sorted({a["customer_id"] for a in accs})
            rows.append(["account_ref", E(str(ref)), E(", ".join(ids) or "no account found")])
            linked |= set(ids)
        else:
            rows.append(["account_ref", "-", "blank on the alert"])
        h = low(alert.get("login_handle"))
        if h:
            accs = self.data.accounts_by_login.get(h, [])
            ids = sorted({a["customer_id"] for a in accs})
            rows.append(["login_handle", E(alert["login_handle"]),
                         E("accounts %s -> %s" % (", ".join(sorted({a['account_id'] for a in accs})), ", ".join(ids)))
                         if ids else "no account found"])
            linked |= set(ids)
        else:
            rows.append(["login_handle", "-", "blank on the alert"])
        em = low(alert.get("email"))
        if em:
            ids = self.data.customer_by_email.get(em, [])
            rows.append(["email", E(alert["email"]), E(", ".join(ids) or "no customer with this email")])
            linked |= set(ids)
        else:
            rows.append(["email", "-", "blank on the alert"])
        return table(["Link", "Value", "Leads to"], rows), sorted(linked)

    def fit_rows(self, ids, screened, use_place):
        rows = []
        for cid in ids:
            c = self.data.customer[cid]
            checks = self.people.fit_checks(c, screened, use_place)
            kept = all(ok is not False for _, ok, _ in checks)
            rows.append((kept, sum(ok is False for _, ok, _ in checks), cid,
                         [E(cid), '<b class="ok">kept</b>' if kept else '<b class="no">filtered out</b>'] +
                         ["%s<br><small>%s</small>" % (mark(ok), E(detail)) for _, ok, detail in checks]))
        return rows

    def candidates(self, alert, screened, linked):
        if linked:
            if len(linked) == 1:
                return "<p>The link leads to one record, so no choice is needed.</p>"
            rows = self.fit_rows(linked, screened, use_place=True)
            any_kept = any(r[0] for r in rows)
            note = ("<p>The link leads to %d records (a joint account or several links). The screened name and date "
                    "of birth pick between them.%s</p>" %
                    (len(linked), "" if any_kept else " <b>None fit, so all are kept.</b>"))
            heads = ["Record", "Result", "type", "dob", "name"]
            return note + table(heads, [r[3] for r in rows])
        ids = self.people.search_candidates(screened)
        rows = self.fit_rows(ids, screened, use_place=True)
        kept = [r for r in rows if r[0]]
        near = sorted([r for r in rows if not r[0]], key=lambda r: (r[1], r[2]))
        fails = {}
        for cid in ids:
            for name, ok, _ in self.people.fit_checks(self.data.customer[cid], screened, True):
                if ok is False:
                    fails[name] = fails.get(name, 0) + 1
        heads = ["Record", "Result", "type", "dob", "country", "city", "name"]
        txt = ("<p>No link on the alert, so the system searched the system of record. %d records share a name key "
               "with the screened name; <b>%d kept</b>, %d filtered out (failures by filter: %s).</p>" %
               (len(ids), len(kept), len(ids) - len(kept),
                ", ".join("%s %d" % kv for kv in sorted(fails.items())) or "none"))
        txt += "<h5>Kept</h5>" + (table(heads, [r[3] for r in kept]) if kept else "<p>None.</p>")
        txt += ("<h5>Closest records filtered out (fewest failed filters, up to 15)</h5>" +
                (table(heads, [r[3] for r in near[:15]]) if near else "<p>None.</p>"))
        return txt

    def merge(self, person):
        recs = [self.data.customer[c] for c in person["records"]]
        rows = []
        for c in recs:
            docs = sorted({str(k.get("document_number")) for k in self.data.checks_of_customer(c["record_id"])
                           if approved(k) and k.get("document_number")})
            rows.append([E(c["record_id"]), E(c.get("full_name") or ""), E(c.get("dob") or ""),
                         E(c.get("phone") or ""), E(", ".join(docs) or "-"),
                         E("%s / %s" % (c.get("nationality"), c.get("residence_country")))])
        why = []
        if len(recs) > 1:
            keys = {}
            for c in recs:
                dob = parse_date(c.get("dob"))
                if phone_key(c.get("phone")) and dob and dob[1] and dob[2]:
                    keys.setdefault(("phone + full date of birth", phone_key(c.get("phone")), dob), []).append(
                        c["record_id"])
                for k in self.data.checks_of_customer(c["record_id"]):
                    if approved(k) and k.get("document_number"):
                        keys.setdefault(("approved document number", str(k["document_number"]), None), []).append(
                            c["record_id"])
            for (kind, value, _), ids in sorted(keys.items(), key=str):
                if len(set(ids)) > 1:
                    why.append("%s %s: %s" % (kind, value, ", ".join(sorted(set(ids)))))
        txt = table(["Record", "Full name", "DOB", "Phone", "Approved doc numbers", "Nationality / residence"], rows)
        if why:
            txt += "<p>Merged into one person because they share " + E("; ".join(why)) + ".</p>"
        return txt

    def identity(self, person):
        rows = []
        for cid in person["records"]:
            for k in self.data.checks_of_customer(cid):
                rows.append([E(k["check_id"]), E(k.get("checked_at") or ""), E(k.get("provider") or ""),
                             E(k.get("result") or ""), E(k.get("verified_name") or ""), E(k.get("verified_dob") or ""),
                             E(k.get("document_country") or ""),
                             '<span class="ok">used</span>' if approved(k) else '<span class="no">ignored (not approved)</span>'])
            em = low(self.data.customer[cid].get("email"))
            for n in self.data.notes_by_email.get(em, []) if em else []:
                if str(n.get("channel")).lower() != "kyc":
                    continue
                p = parse_kyc_note(n) or {"approved": False}
                rows.append([E(n["note_id"]), E(n.get("created_at") or ""), "kyc note", "approved" if p.get("approved")
                             else "not a passed check", E(p.get("name") or ""), d(p.get("dob")),
                             E(", ".join(p.get("countries") or []) if p.get("approved") else ""),
                             ('<span class="ok">used</span>' if p.get("approved") else
                              '<span class="no">ignored</span>') + "<br><small>%s</small>" % E(n["text"])])
        rows.sort(key=lambda r: r[1], reverse=True)
        lived = []
        for cid in person["records"]:
            em = low(self.data.customer[cid].get("email"))
            for n in self.data.notes_by_email.get(em, []) if em else []:
                if str(n.get("channel")).lower() == "kyc":
                    continue
                found = countries.find_in_text(n.get("text"))
                if found:
                    lived.append([E(n["note_id"]), E(n.get("channel") or ""), E(", ".join(found)), E(n["text"])])
        txt = "<h5>Identity checks and KYC notes (newest first)</h5>" + (
            table(["ID", "Date", "Provider", "Result", "Name", "DOB", "Country", "Used?"], rows) if rows
            else "<p>None: no provider check and no kyc note for this person.</p>")
        txt += "<h5>Support notes that name a country</h5>" + (
            table(["Note", "Channel", "Countries", "Text"], lived) if lived else "<p>None.</p>")
        return txt

    def picture_box(self, pic):
        rows = [
            ["Verified legal name", E(str(pic["verified_name"])), E(str(pic["verified_name_src"]))],
            ["Verified date of birth", d(pic["verified_dob"]), E(str(pic["verified_dob_src"]))],
            ["Dates of birth used", E("; ".join("%s (%s)" % (fmt_date(x), ", ".join(s)) for x, s in pic["dobs"]) or "none"), ""],
            ["Names used for agreement", E(" / ".join(pic["names"])), E(", ".join(pic["names_src"]))],
            ["Countries tied to the customer", E("; ".join("%s (%s)" % (k, ", ".join(v)) for k, v in pic["countries"].items()) or "none"), ""],
            ["Type", "individual" if pic["is_person"] else "company", E(pic["anchor"])],
        ]
        return table(["Fact", "Value", "Source"], rows)

    def hit_box(self, hit, h):
        first = re.split(r"\n\s*\n", (hit.get("profile_notes") or "").strip(), maxsplit=1)[0]
        rows = [
            ["Names and aliases", E(" / ".join(h["names"]))],
            ["Type", E(str(hit.get("entity_type")))],
            ["dobs field", E(", ".join(hitmod.as_list(hit.get("dobs"))) or "blank")],
            ["Profile notes, first paragraph", E(first[:600]) or "blank"],
            ["Birth dates used", E("%s (from %s)" % (", ".join(fmt_date(x) for x in h["dates"]), h["dob_origin"]))
             if h["dates"] else "none found"],
            ["Countries (raw)", E("countries %s | nationality %s | citizenship %s | addresses %s | place of birth %s" % (
                hit.get("countries"), hit.get("nationality"), hit.get("citizenship"), hit.get("addresses"),
                hit.get("place_of_birth")))],
            ["Countries used", E(", ".join("%s%s" % (c, "" if countries.known(c) else " (not recognised)")
                                           for c in h["countries"]) or "none")],
            ["Death", E("%s %s" % (hit.get("life_status"), hit.get("deceased_date") or ""))],
            ["Earliest dated report", d(h["report"])],
            ["Category / listing phrase", E("%s / '%s'" % (hit.get("primary_category") or hit.get("categories"),
                                                         hitmod.listing_phrase(hit)))],
        ]
        txt = table(["Fact", "Value"], rows)
        if h["is_person"] and not hitmod.as_list(hit.get("dobs")) and h["dob_origin"] in (None, "search"):
            srows = []
            for key, checks in hitmod.search_checks(hit, self.data, h["countries"]):
                for s, ok, why, years in checks:
                    srows.append([E(key), E(s["result_id"]), mark(ok) + "<br><small>%s</small>" % E(why), E(s["snippet"])])
            txt += "<h5>Search results for the hit's name</h5>" + (
                table(["Query", "Result", "Used?", "Snippet"], srows) if srows else "<p>No search results under this name.</p>")
        return txt

    def tests_box(self, pic, h, screened_at):
        rows = []
        rows.append(["Type", "customer %s, hit %s" % ("individual" if pic["is_person"] else "company",
                                                      "individual" if h["is_person"] else "company"),
                     mark(pic["is_person"] == h["is_person"])])
        rows.append(["Death before screening", E("death %s, screened %s" % (d(h["death"]), d(screened_at))),
                     "fires" if h["death"] and screened_at and hitmod_before(h["death"], screened_at) else "-"])
        rows.append(["Time (under 16 at earliest report)", E("report %s, customer dates %s" % (
            d(h["report"]), ", ".join(fmt_date(x) for x, _ in pic["dobs"]) or "none")), ""])
        cells = []
        for c, _ in pic["dobs"]:
            for x in h["dates"]:
                state = "contradicted" if contradicted(c, x) else ("agrees" if agrees(c, x) else "close, not equal")
                cells.append("%s vs %s: %s" % (fmt_date(c), fmt_date(x), state))
        rows.append(["Date of birth", E("; ".join(cells) or "cannot compare (one side has no date)"), ""])
        company = not pic["is_person"]
        hn = [n for n in h["names"] if self.rules.parts(n, company)]
        ncells = []
        for n in pic["names"]:
            parts = self.rules.parts(n, company)
            for m in hn:
                ncells.append("'%s' %s vs '%s' %s: %s" % (n, parts, m, self.rules.parts(m, company),
                                                         "agree" if self.rules.agree(n, m, company) else "no"))
        rows.append(["Names", E("; ".join(ncells[:12]) or "cannot compare"), ""])
        cset, hset = set(pic["countries"]), set(h["countries"])
        rows.append(["Location", E("customer %s | hit %s | shared %s" % (sorted(cset), sorted(hset),
                                                                          sorted(cset & hset) or "none")), ""])
        return table(["Test", "Values compared", ""], rows)

    # ------------------------------------------------------------ one hit

    def hit_section(self, hid, line):
        hit = self.data.hit[hid]
        alert = self.data.alert.get(hit.get("alert_id"), {})
        routes, screened, persons = self.people.resolve(alert)
        pics = [self.people.picture(p, screened) for p in persons]
        h = hitmod.picture(hit, self.data)
        screened_at = parse_date(alert.get("screened_at"))
        lab = self.labels.get(hid)
        ok = lab is None or lab["decision"] == line["decision"]
        status = "" if lab is None else (' <span class="ok">matches practice label</span>' if ok
                                         else ' <span class="no">DIFFERS from practice label</span>')
        out = ['<details class="hit%s"><summary><b>%s</b> %s &mdash; <b>%s</b> %s, customer %s%s</summary>' % (
            "" if ok else " bad", E(hid), E(hit.get("name") or ""), E(line["decision"]), E(line["reason_code"]),
            E(str(line["customer_record_id"])), status)]
        out.append("<h4>Our answer</h4><p>%s</p><p>Evidence: %s</p>" % (E(line["reason"]), E(", ".join(line["evidence"]))))
        if lab:
            out.append("<h4>Practice label</h4><p><b>%s</b> %s, customer %s, evidence %s</p><p>%s</p><ol>%s</ol>" % (
                E(lab["decision"]), E(", ".join(lab["reason_codes"])), E(", ".join(str(x) for x in lab["customer_record_ids"])),
                E(", ".join(lab["evidence"])), E(lab.get("reference_reason") or ""),
                "".join("<li>%s</li>" % E(p) for p in lab.get("path", []))))
            extra = sorted(set(line["evidence"]) - set(lab["evidence"]))
            missing = sorted(set(lab["evidence"]) - set(line["evidence"]))
            out.append("<p>Evidence we cite that the label does not: %s. Label cites, we do not: %s.</p>" % (
                E(", ".join(extra) or "none"), E(", ".join(missing) or "none")))
        out.append("<h4>Step 1. The customer as screened (alert %s, screened %s)</h4>" % (E(alert.get("alert_id", "")),
                                                                                      E(alert.get("screened_at") or "")))
        out.append(table(["Field", "Value"], [[E(k), E(str(v))] for k, v in sorted(screened.items())]))
        ltxt, linked = self.links(alert, screened)
        out.append("<h4>Step 2. Links on the alert</h4>" + ltxt)
        out.append("<h4>Step 3. Which records fit</h4>" + self.candidates(alert, screened, linked))
        out.append("<h4>Step 4. People found: %d</h4>" % len(persons))
        for p, pic in zip(persons, pics):
            r = compare(pic, h, self.rules, screened_at)
            out.append('<div class="person"><h4>Person: %s (found via %s)</h4>' % (
                E(", ".join(p["records"])), E(", ".join(p["anchors"]))))
            out.append(self.merge(p) + self.identity(p))
            out.append("<h5>Customer picture</h5>" + self.picture_box(pic))
            out.append("<h5>Tests against this hit</h5>" + self.tests_box(pic, h, screened_at))
            out.append("<p>Result for this person: <b>%s</b> %s</p></div>" % (E(r["decision"]), E(", ".join(r["codes"]))))
        out.append("<h4>Step 5. The hit</h4>" + self.hit_box(hit, h))
        out.append("</details>")
        return "".join(out), ok


def hitmod_before(a, b):
    from norm import before
    return before(a, b)


CSS = """
body{font:14px/1.45 system-ui,Segoe UI,sans-serif;margin:16px;background:#fafafa;color:#222}
h1{font-size:20px} h4{margin:14px 0 4px} h5{margin:10px 0 4px;color:#555}
details.hit{background:#fff;border:1px solid #ddd;border-left:5px solid #2e7d32;border-radius:6px;margin:8px 0;padding:6px 10px}
details.hit.bad{border-left-color:#c62828}
summary{cursor:pointer} table{border-collapse:collapse;margin:4px 0;width:100%}
td,th{border:1px solid #e3e3e3;padding:3px 6px;vertical-align:top;text-align:left;font-size:13px}
th{background:#f3f3f3} small{color:#666} .ok{color:#2e7d32;font-weight:600} .no{color:#c62828;font-weight:600}
.un{color:#888} .person{border:1px dashed #bbb;padding:6px;margin:6px 0;border-radius:4px}
"""


def main(argv):
    if len(argv) < 3:
        sys.stderr.write(__doc__)
        return 2
    folder, report = argv[1], argv[2]
    a = Auditor(folder)
    tmp = report + ".out.jsonl"
    mainmod.main(["main.py", folder, tmp])
    lines = {json.loads(l)["hit_id"]: json.loads(l) for l in open(tmp, encoding="utf-8")}
    os.remove(tmp)
    if "--all" in argv[3:]:
        ids = sorted(lines)
    elif argv[3:]:
        ids = [x for x in argv[3:] if x in lines]
    else:
        ids = sorted(a.labels)
    sections = [a.hit_section(i, lines[i]) for i in ids]
    sections.sort(key=lambda s: s[1])  # differences first
    bad = sum(1 for _, ok in sections if not ok)
    with open(report, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("<!doctype html><meta charset=utf-8><title>FinCI audit</title><style>%s</style>" % CSS)
        handle.write("<h1>FinCI audit: %d hits</h1><p>%s Click a hit to see every step: the links, which "
                     "records were kept or filtered out and why, the merge, the identity checks used or ignored, "
                     "the hit's facts and search results, and each test.</p>" % (
                         len(ids), "%d differ from the practice labels (shown first)." % bad if a.labels else ""))
        handle.write("".join(s for s, _ in sections))
    sys.stderr.write("audit written to %s (%d hits, %d differ)\n" % (report, len(ids), bad))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

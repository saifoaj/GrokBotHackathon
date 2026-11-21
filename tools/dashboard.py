"""FinCI results dashboard: what went in, what came out, what passed, what failed and why.

    python tools/dashboard.py <report.html> <data folder> [<data folder> ...]

For each data folder it runs the system (the same code as system/run), scores the hits that
have expected answers (practice_labels.jsonl), and writes one self-contained HTML page:
summary cards, a filterable list of every hit, and for each hit the inputs, our answer, the
expected answer, a short diagnosis of any miss, and the full step-by-step audit.
The page holds data from the folders given, so keep it local.
"""

import collections
import html
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "system"))

import audit  # noqa: E402
import hits as hitmod  # noqa: E402
import main as mainmod  # noqa: E402
from decide import compare  # noqa: E402
from norm import fmt_date, parse_date  # noqa: E402

COST = {"DIFFERENT_ENTITY": {"DIFFERENT_ENTITY": 0, "SAME_ENTITY": 3, "REVIEW": 3},
        "SAME_ENTITY": {"DIFFERENT_ENTITY": 60, "SAME_ENTITY": 0, "REVIEW": 2},
        "REVIEW": {"DIFFERENT_ENTITY": 60, "SAME_ENTITY": 2, "REVIEW": 0}}
E = html.escape


def dates_txt(ds):
    return ", ".join(fmt_date(d) for d in ds) if ds else ""


def diagnose(expected, got, pics, h, results):
    """A plain-language guess at why our answer differs from the expected one."""
    if expected is None or expected["decision"] == got["decision"]:
        return []
    want, have = expected["decision"], got["decision"]
    codes = expected["reason_codes"]
    out = []
    if have == "DIFFERENT_ENTITY":
        out.append("UNSAFE CLEAR (cost 60): we cleared on %s, but the expected answer is %s." % (
            got["reason_code"], want))
        for p, r in zip(pics, results):
            for c in r["codes"]:
                if c == "LOCATION_MISMATCH":
                    out.append("Location test used customer countries %s against hit countries %s. Check for a "
                               "country the customer is tied to that we did not read (notes, other records, "
                               "spelling)." % (", ".join(p["countries"]) or "none", ", ".join(h["countries"])))
                if c == "DOB_MISMATCH":
                    out.append("Birth-date test used customer %s against hit %s (from %s). Check that the hit date "
                               "is really about the hit and read correctly." % (
                                   dates_txt([d for d, _ in p["dobs"]]), dates_txt(h["dates"]), h["dob_origin"]))
                if c == "NAME_MISMATCH_VERIFIED":
                    out.append("Verified name used: %s (from %s). Check that this check really passed and the name "
                               "was read completely." % (p["verified_name"], p["verified_name_src"]))
                if c == "TEMPORAL_IMPOSSIBILITY":
                    out.append("Earliest dated report read as %s." % fmt_date(h["report"]))
        return out
    if not pics:
        out.append("No customer record was found for this alert, so nothing could be compared.")
        return out
    p = pics[0]
    if want == "DIFFERENT_ENTITY":
        out.append("MISSED CLEAR (cost 3): expected %s, we said %s." % (" or ".join(codes), have))
        if "DOB_MISMATCH" in codes or "TEMPORAL_IMPOSSIBILITY" in codes:
            if not h["dates"] and "DOB_MISMATCH" in codes:
                out.append("No birth date was read for the hit (dobs field, first profile paragraph or a qualifying "
                           "search result). The wording may not be one the reader knows.")
            if not p["dobs"]:
                out.append("No birth date was read for the customer (checks, notes or records). The format may not "
                           "be one the reader knows, such as a two-digit year.")
            if h["dates"] and p["dobs"] and have == "SAME_ENTITY":
                out.append("The dates were read as agreeing: customer %s, hit %s. A partial date (year only) can "
                           "agree with a full one." % (dates_txt([d for d, _ in p["dobs"]]), dates_txt(h["dates"])))
        if "NAME_MISMATCH_VERIFIED" in codes and not p["verified_name"]:
            out.append("No verified legal name was read from an approved check or a KYC note (for example a name "
                       "not written in capitals).")
        if "LOCATION_MISMATCH" in codes:
            out.append("Customer countries %s, hit countries %s." % (", ".join(p["countries"]) or "none",
                                                                     ", ".join(h["countries"]) or "none"))
        return out
    if want == "SAME_ENTITY":
        out.append("MISSED MATCH (cost 2): expected %s, we said %s." % (" or ".join(codes), have))
        r = results[0]
        if r.get("name_agree") is not True:
            out.append("Names were not read as agreeing.")
        if p["is_person"] and r.get("dob_agree") is not True:
            out.append("Birth dates were not read as agreeing: customer %s, hit %s." % (
                dates_txt([d for d, _ in p["dobs"]]) or "none", dates_txt(h["dates"]) or "none"))
        if not r.get("shared"):
            out.append("No shared country: customer %s, hit %s." % (", ".join(p["countries"]) or "none",
                                                                   ", ".join(h["countries"]) or "none"))
        return out
    out.append("Expected %s (%s); we said %s %s." % (want, ", ".join(codes), have, got["reason_code"]))
    return out


def build(folder):
    name = os.path.basename(os.path.normpath(folder))
    name = {"dev": "Dev set", "data": "Stress set", "data_hard": "Harder set"}.get(name, name)
    tmp = os.path.join(ROOT, "debug", "_dash_%s.jsonl" % name)
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    mainmod.main(["main.py", folder, tmp])
    lines = {json.loads(l)["hit_id"]: json.loads(l) for l in open(tmp, encoding="utf-8")}
    os.remove(tmp)
    a = audit.Auditor(folder)
    cases = {}
    cpath = os.path.join(folder, "cases.json")
    if os.path.isfile(cpath):
        cases = json.load(open(cpath, encoding="utf-8"))
    rows, details = [], {}
    for hid in sorted(lines):
        line = lines[hid]
        hit = a.data.hit[hid]
        alert = a.data.alert.get(hit.get("alert_id"), {})
        lab = a.labels.get(hid)
        routes, screened, persons = a.people.resolve(alert)
        pics = [a.people.picture(p, screened) for p in persons]
        h = hitmod.picture(hit, a.data, a.checker)
        results = [compare(p, h, a.rules, parse_date(alert.get("screened_at"))) for p in pics]
        status = "unlabelled" if lab is None else ("pass" if lab["decision"] == line["decision"] else
                                                    ("unsafe" if line["decision"] == "DIFFERENT_ENTITY" else "fail"))
        cost = COST[lab["decision"]][line["decision"]] if lab else None
        cust = pics[0] if pics else None
        rows.append({
            "id": hid, "alert": line["alert_id"], "name": hit.get("name") or "",
            "screened": screened.get("name") or "", "got": line["decision"], "code": line["reason_code"],
            "want": lab["decision"] if lab else "", "want_codes": ", ".join(lab["reason_codes"]) if lab else "",
            "status": status, "cost": cost, "case": cases.get(hid, ""), "people": len(persons),
            "route": "; ".join(routes), "record": str(line["customer_record_id"]),
            "c_dob": dates_txt([d for d, _ in cust["dobs"]]) if cust else "",
            "c_countries": ", ".join(cust["countries"]) if cust else "",
            "c_name": (cust["verified_name"] or "") if cust else "",
            "h_dob": dates_txt(h["dates"]), "h_dob_from": h["dob_origin"] or "",
            "h_countries": ", ".join(h["countries"]),
            "diag": diagnose(lab, line, pics, h, results),
        })
        if lab is not None or len(lines) <= 900:
            section, _ = a.hit_section(hid, line)
            details[hid] = section
    labelled = [r for r in rows if r["status"] != "unlabelled"]
    cost = sum(r["cost"] for r in labelled)
    base = sum(COST[r["want"]]["REVIEW"] for r in labelled)
    summary = {
        "name": name, "folder": folder, "hits": len(rows), "labelled": len(labelled),
        "pass": sum(1 for r in labelled if r["status"] == "pass"),
        "fail": sum(1 for r in labelled if r["status"] == "fail"),
        "unsafe": sum(1 for r in labelled if r["status"] == "unsafe"),
        "score": round(100 * max(0.0, 1 - cost / base), 1) if base else None,
        "cost": cost, "decisions": dict(collections.Counter(r["got"] for r in rows)),
        "codes": dict(collections.Counter(r["code"] for r in rows)),
    }
    return {"summary": summary, "rows": rows, "details": details}


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>FinCI results</title>
<style>
:root{--bg:#0b0b0c;--card:#111113;--ink:#ededed;--muted:#8b8b90;--line:#26262a;--ok:#8fd19e;--okbg:#14261a;
--bad:#f08b8b;--badbg:#2c1517;--warn:#f1a25c;--warnbg:#2b1d12;--un:#b4b4ba;--unbg:#1c1c20;--accent:#e8e8e8;
--mono:"JetBrains Mono","SFMono-Regular",Consolas,"Liberation Mono",monospace;color-scheme:dark}
.legendwrap{overflow-x:auto}header{top:env(safe-area-inset-top,0px)}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 Inter,system-ui,"Segoe UI",sans-serif;background:var(--bg);color:var(--ink)}
.chip{display:inline-block;font:600 12.5px/1 var(--mono);letter-spacing:.02em;padding:6px 10px;border-radius:7px;white-space:nowrap}
.chip.DIFFERENT_ENTITY{background:var(--okbg);color:var(--ok)}.chip.SAME_ENTITY{background:var(--badbg);color:var(--bad)}
.chip.REVIEW{background:var(--warnbg);color:var(--warn)}.chip.none{background:var(--unbg);color:var(--muted)}
.code{font:12px var(--mono);color:var(--muted)}
.legend{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;margin-bottom:14px}
.legend h2{font-size:17px;margin:0;padding:14px 18px 10px;font-weight:600}
.legend table{width:100%;border-collapse:collapse}.legend th{font:500 11.5px var(--mono);letter-spacing:.08em;text-transform:uppercase;
color:var(--muted);text-align:left;padding:10px 18px;border-top:1px solid var(--line)}
.legend td{padding:12px 18px;border-top:1px solid var(--line);vertical-align:middle}.legend td.n{font:600 15px var(--mono);text-align:right;white-space:nowrap}
header{padding:16px 20px;border-bottom:1px solid var(--line);background:var(--card);position:sticky;top:0;z-index:2}
h1{font-size:18px;margin:0 0 8px} .tabs button{border:1px solid var(--line);background:var(--bg);color:var(--ink);
padding:6px 12px;border-radius:6px;margin-right:6px;cursor:pointer}.tabs button.on{background:var(--ink);color:var(--bg);border-color:var(--ink);font-weight:600}
main{display:grid;grid-template-columns:minmax(380px,46%) 1fr;gap:16px;padding:16px 20px}
@media (max-width:900px){main{grid-template-columns:1fr}}
.cards{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}.card{background:var(--card);border:1px solid var(--line);
border-radius:8px;padding:8px 12px;min-width:92px}.card b{display:block;font-size:20px}.card span{color:var(--muted);font-size:12px}
.filters{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}.filters select,.filters input{background:var(--card);color:var(--ink);
border:1px solid var(--line);border-radius:6px;padding:5px 8px}
.list{background:var(--card);border:1px solid var(--line);border-radius:8px;max-height:calc(100vh - 290px);overflow:auto}
table.l{width:100%;border-collapse:collapse}table.l td,table.l th{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;font-size:13px;vertical-align:top}
table.l th{position:sticky;top:0;background:var(--card);font:500 11px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
header,.card,.list,.detail,.box{border-radius:12px}
table.l tr{cursor:pointer}table.l tr:hover td{background:var(--bg)}table.l tr.sel td{background:var(--unbg)}
.pill{display:inline-block;padding:1px 7px;border-radius:10px;font-size:12px;font-weight:600;white-space:nowrap}
.pass{background:var(--okbg);color:var(--ok)}.fail{background:var(--warnbg);color:var(--warn)}.unsafe{background:var(--badbg);color:var(--bad)}
.unlabelled{background:var(--unbg);color:var(--un)}
.detail{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px;max-height:calc(100vh - 140px);overflow:auto}
.detail h2{font-size:16px;margin:0 0 6px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:10px 0}
.box{border:1px solid var(--line);border-radius:8px;padding:8px 10px}.box h3{font-size:12px;color:var(--muted);margin:0 0 6px;text-transform:uppercase;letter-spacing:.04em}
.kv{display:grid;grid-template-columns:120px 1fr;gap:2px 8px;font-size:13px}.kv div:nth-child(odd){color:var(--muted)}
.diag{border-radius:8px;padding:8px 12px;margin:8px 0}.diag.unsafe{background:var(--badbg)}.diag.fail{background:var(--warnbg)}
.diag p{margin:3px 0}.audit details{border:0;padding:0;margin:0}.audit summary{display:none}.audit table{border-collapse:collapse;width:100%}
.audit td,.audit th{border:1px solid var(--line);padding:3px 6px;font-size:12px;text-align:left;vertical-align:top}
.audit .ok{color:var(--ok);font-weight:600}.audit .no{color:var(--bad);font-weight:600}.audit .un{color:var(--muted)}
.audit .person{border:1px dashed var(--line);padding:6px;margin:6px 0;border-radius:6px}
.muted{color:var(--muted)} .empty{color:var(--muted);padding:30px;text-align:center}
.bars{display:flex;flex-direction:column;gap:3px;margin:4px 0 10px}.bar{display:flex;align-items:center;gap:6px;font-size:12px}
.bar i{display:block;height:10px;background:var(--ok);border-radius:3px}
.legend td:nth-child(2){min-width:220px}
</style></head><body>
<header><h1>FinCI results: what went in, what came out, what passed, what failed</h1><div class="tabs" id="tabs"></div></header>
<div style="padding:16px 20px 0" class="legendwrap"><div class="legend" id="legend"></div></div>
<main><section><div class="cards" id="cards"></div><div id="cases"></div><div class="filters">
<select id="fStatus"><option value="">All results</option><option value="unsafe">Unsafe clears</option>
<option value="fail">Other misses</option><option value="pass">Passed</option><option value="unlabelled">No expected answer</option></select>
<select id="fGot"><option value="">Any decision</option><option>DIFFERENT_ENTITY</option><option>SAME_ENTITY</option><option>REVIEW</option></select>
<select id="fCase"><option value="">Any case</option></select>
<input id="fText" placeholder="Search hit, name, code..." size="22"></div>
<div class="list"><table class="l"><thead><tr><th>Result</th><th>Hit</th><th>Expected</th><th>We said</th><th>Case</th></tr></thead>
<tbody id="rows"></tbody></table></div></section>
<section class="detail" id="detail"><div class="empty">Pick a hit on the left to see what went in, what came out and why.</div></section></main>
<script>
const DATA = __DATA__;
let cur = 0, sel = null;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function tabs(){ $("tabs").innerHTML = DATA.map((d,i)=>`<button class="${i===cur?"on":""}" data-i="${i}">${esc(d.summary.name)} (${d.summary.hits})</button>`).join("");
  $("tabs").querySelectorAll("button").forEach(b=>b.onclick=()=>{cur=+b.dataset.i;sel=null;render();}); }
const chip = d => d ? `<span class="chip ${esc(d)}">${esc(d)}</span>` : `<span class="chip none">no answer</span>`;
const MEANING = {DIFFERENT_ENTITY:"The evidence shows the hit is a different person or company. The hit is cleared.",
  SAME_ENTITY:"The evidence shows the hit is the customer. The alert goes to the compliance team.",
  REVIEW:"The evidence cannot settle it. A person looks at it."};
function legend(){ const rows=DATA[cur].rows; const lab=rows.filter(r=>r.want);
  $("legend").innerHTML = `<h2>The three decisions</h2><table><tr><th>Decision</th><th>Meaning</th><th style="text-align:right">We said</th>${lab.length?'<th style="text-align:right">Expected</th>':""}</tr>`+
   Object.keys(MEANING).map(d=>`<tr><td>${chip(d)}</td><td>${MEANING[d]}</td><td class="n">${rows.filter(r=>r.got===d).length}</td>${
     lab.length?`<td class="n">${lab.filter(r=>r.want===d).length}</td>`:""}</tr>`).join("")+`</table>`; }
function cards(){ legend(); const s=DATA[cur].summary; const c=(v,l,cls)=>`<div class="card"><b class="${cls||""}">${v}</b><span>${l}</span></div>`;
  $("cards").innerHTML = c(s.hits,"hits fed in")+(s.labelled?c(s.labelled,"with expected answer")+c(s.pass,"passed")+
   c(s.fail,"other misses")+c(s.unsafe,"unsafe clears")+c(s.score,"decision score")+c(s.cost,"cost"):"")+
   Object.entries(s.decisions).map(([k,v])=>c(v,k.replace("_ENTITY","").toLowerCase())).join("");
  const rows=DATA[cur].rows; const by={}; rows.forEach(r=>{if(!r.case)return;const b=by[r.case]||(by[r.case]={n:0,ok:0,bad:0});b.n++;
   if(r.status==="pass")b.ok++;else if(r.status!=="unlabelled")b.bad++;});
  const ks=Object.keys(by).sort((a,b)=>by[b].bad-by[a].bad||a.localeCompare(b));
  $("cases").innerHTML = ks.length?`<div class="box" style="margin-bottom:10px"><h3>By case (failures first)</h3><div class="bars">`+
   ks.map(k=>`<div class="bar"><span style="width:210px">${esc(k)}</span><i style="width:${Math.round(by[k].ok/by[k].n*140)}px"></i>
   <span class="${by[k].bad?"pill fail":"muted"}">${by[k].ok}/${by[k].n}</span></div>`).join("")+`</div></div>`:"";
  $("fCase").innerHTML = `<option value="">Any case</option>`+ks.map(k=>`<option>${esc(k)}</option>`).join(""); }
function list(){ const fs=$("fStatus").value,fg=$("fGot").value,fc=$("fCase").value,ft=$("fText").value.toLowerCase();
  const order={unsafe:0,fail:1,pass:2,unlabelled:3};
  const rows=DATA[cur].rows.filter(r=>(!fs||r.status===fs)&&(!fg||r.got===fg)&&(!fc||r.case===fc)&&
   (!ft||JSON.stringify(r).toLowerCase().includes(ft))).sort((a,b)=>order[a.status]-order[b.status]||a.id.localeCompare(b.id));
  $("rows").innerHTML = rows.slice(0,1500).map(r=>`<tr data-id="${r.id}" class="${r.id===sel?"sel":""}"><td><span class="pill ${r.status}">${r.status}</span></td>
   <td><b>${esc(r.id)}</b><br><span class="muted">${esc(r.name)}</span></td><td>${r.want?chip(r.want):'<span class="muted">-</span>'}<br><span class="code">${esc(r.want_codes)}</span></td>
   <td>${chip(r.got)}<br><span class="code">${esc(r.code)}</span></td><td class="muted">${esc(r.case)}</td></tr>`).join("") ||
   `<tr><td colspan=5 class="empty">No hits match these filters.</td></tr>`;
  $("rows").querySelectorAll("tr[data-id]").forEach(t=>t.onclick=()=>{sel=t.dataset.id;list();detail();}); }
function kv(pairs){return `<div class="kv">`+pairs.map(([k,v])=>`<div>${esc(k)}</div><div>${esc(v||"-")}</div>`).join("")+`</div>`;}
function detail(){ const r=DATA[cur].rows.find(x=>x.id===sel); if(!r){return;}
  const d=DATA[cur].details[r.id];
  $("detail").innerHTML = `<h2>${esc(r.id)} &middot; ${esc(r.name)} <span class="pill ${r.status}">${r.status}</span>${r.cost?` <span class="muted">cost ${r.cost}</span>`:""}</h2>
  ${r.diag.length?`<div class="diag ${r.status}"><b>What went wrong</b>${r.diag.map(x=>`<p>${esc(x)}</p>`).join("")}</div>`:""}
  <div class="grid"><div class="box"><h3>Fed in: customer side</h3>${kv([["Screened as",r.screened],["Route",r.route],["People found",r.people],
   ["Record used",r.record],["Verified name",r.c_name],["Birth date(s)",r.c_dob],["Countries",r.c_countries]])}</div>
  <div class="box"><h3>Fed in: watchlist hit</h3>${kv([["Hit name",r.name],["Birth date(s)",r.h_dob],["Birth date from",r.h_dob_from],["Countries",r.h_countries]])}</div>
  <div class="box"><h3>Came out</h3><p>${chip(r.got)}</p>${kv([["Reason code",r.code],["Customer",r.record]])}</div>
  <div class="box"><h3>Expected</h3><p>${r.want?chip(r.want):chip("")}</p>${kv([["Accepted codes",r.want_codes],["Case",r.case]])}</div></div>
  <h3 class="muted" style="font-size:12px;text-transform:uppercase">Every step</h3><div class="audit">${d||"<p class='muted'>Step detail not included for this hit.</p>"}</div>`;
  const det=$("detail").querySelector(".audit details"); if(det) det.open=true; }
function render(){tabs();cards();list();$("detail").innerHTML='<div class="empty">Pick a hit on the left to see what went in, what came out and why.</div>';}
["fStatus","fGot","fCase"].forEach(i=>$(i).onchange=list); $("fText").oninput=list;
render();
</script></body></html>"""


def main(argv):
    if len(argv) < 3:
        sys.stderr.write(__doc__)
        return 2
    report, folders = argv[1], argv[2:]
    data = [build(f) for f in folders]
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = PAGE.replace("__DATA__", blob)
    with open(report, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(page)
    # the same page without its own document wrapper, for hosts that add one
    body = page.replace('<!doctype html><html lang="en"><head><meta charset="utf-8">\n'
                        '<meta name="viewport" content="width=device-width,initial-scale=1">', "")
    body = body.replace("</head><body>", "").replace("</body></html>", "")
    with open(os.path.splitext(report)[0] + ".fragment.html", "w", encoding="utf-8", newline="\n") as handle:
        handle.write(body)
    for d in data:
        s = d["summary"]
        sys.stderr.write("%s: %d hits, %d with answers, %d pass, %d other misses, %d unsafe, score %s\n" % (
            s["name"], s["hits"], s["labelled"], s["pass"], s["fail"], s["unsafe"], s["score"]))
    sys.stderr.write("dashboard written to %s (%.1f MB)\n" % (report, os.path.getsize(report) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

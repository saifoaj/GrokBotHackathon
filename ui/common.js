/* Shared helpers for all 10 UI variants. Requires data.js (window.DATA). */
(function () {
  const D = window.DATA;
  if (!D) { document.body.innerHTML = '<p style="padding:40px;font-family:sans-serif">data.js not found. Run <code>python3 ui/build_data.py</code> first.</p>'; return; }

  const VARIANTS = [
    ["v01-case-file.html", "Case File", "Analyst workbench: list, verdict, side-by-side facts"],
    ["v02-decision-path.html", "Decision Path", "Every rule in order, lit up where the decision was made"],
    ["v05-diff.html", "Side-by-side Diff", "Field-by-field comparison with name-part alignment"],
    ["v07-dashboard.html", "Overview", "Run-level metrics and alerts grouped with their hits"],
    ["v08-review-queue.html", "Review Queue", "Triage: why each hit is open and what would settle it"],
    ["v09-fact-matrix.html", "Fact Matrix", "Every hit x every rule, as a heatmap"],
  ];
  const HITS = D.hits, BY = Object.fromEntries(HITS.map(h => [h.hit_id, h]));
  const ALERTS = {};
  HITS.forEach(h => (ALERTS[h.alert_id] = ALERTS[h.alert_id] || []).push(h));

  const DEC = { DIFFERENT_ENTITY: "Different person", SAME_ENTITY: "Same person", REVIEW: "Needs review" };
  const DEC_SHORT = { DIFFERENT_ENTITY: "Cleared", SAME_ENTITY: "Match", REVIEW: "Review" };
  const CODE = {
    TEMPORAL_IMPOSSIBILITY: "Too young at the earliest report", DOB_MISMATCH: "Date of birth contradicts",
    LOCATION_MISMATCH: "No country in common", NAME_MISMATCH_VERIFIED: "Verified name does not match",
    ENTITY_TYPE_MISMATCH: "Person vs company", DECEASED: "Listed person died before screening",
    NAME_DOB_LOCATION_MATCH: "Name, date of birth and country agree", NAME_DOB_MATCH: "Name and date of birth agree",
    NAME_LOCATION_MATCH: "Company name and country agree", INSUFFICIENT_DATA: "Not enough to decide",
    AMBIGUOUS_CUSTOMER_RECORD: "More than one customer fits",
  };
  const RULE = {
    ENTITY_TYPE_MISMATCH: "Entity type", DECEASED: "Date of death", TEMPORAL_IMPOSSIBILITY: "Age at earliest report",
    DOB_MISMATCH: "Date of birth", LOCATION_MISMATCH: "Countries", NAME_MISMATCH_VERIFIED: "Verified name", AGREEMENT: "Agreement",
  };
  const STATUS = { fired: "Contradiction", passed: "No contradiction", blocked: "Blocked by guard", "n/a": "Cannot check", info: "Summary" };

  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const rec = id => D.records[id];
  const kindOf = id => id.startsWith("CUS-") ? "Customer record" : id.startsWith("KYC-") ? "KYC check" : id.startsWith("NOTE-") ? "Note"
    : id.startsWith("SR-") ? "Search result" : id.includes("#") ? "Hit field" : "Record";
  const pill = d => `<span class="pill ${d}"><span class="dot"></span>${DEC[d]}</span>`;
  const st = s => `<span class="st ${s}">${STATUS[s] || s}</span>`;
  const chip = (id, ev) => `<span class="chip${ev ? " ev" : ""}" data-rec="${esc(id)}" title="${kindOf(id)}">${esc(id)}</span>`;
  const person = h => h.persons.find(p => p.record === h.record) || h.persons[0];
  const hitTitle = h => `${h.hit_id} · ${h.hit.name}`;
  const years = x => x.years.length === 2 ? `${x.years[0]} or ${x.years[1]}` : [x.years[0], x.month && String(x.month).padStart(2, "0"), x.day && String(x.day).padStart(2, "0")].filter(Boolean).join("-");

  /* highlight extracted facts in free text */
  const COUNTRY_RE = new RegExp("\\b(" + Object.values(D.countryNames).sort((a, b) => b.length - a.length).map(n => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")\\b", "g");
  const MON = "(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)";
  const DATE_RE = new RegExp(`\\b(?:\\d{4}-\\d{2}-\\d{2}|\\d{1,2}/\\d{1,2}/\\d{4}|\\d{1,2} ${MON} \\d{4}|${MON} \\d{1,2}, \\d{4}|${MON} \\d{4}|(?:19|20)\\d{2})\\b`, "g");
  const AGE_RE = /\b(?:aged |age of |at age |at |then |was |Age: ?)?(\d{1,3})(?:[- ]years?[- ]old| years of age)?\b(?=[,.;: ]|$)/g;
  const PHRASES = ["a sanctioned individual", "a politically exposed person", "the subject of enforcement action", "named in adverse media", "a state-owned enterprise official", "named on a watchlist"];
  function highlight(text, opts = {}) {
    let t = esc(text);
    const marks = [];
    const put = (re, cls) => { t = t.replace(re, m => { marks.push(`<mark class="hl ${cls}">${m}</mark>`); return `\u0000${marks.length - 1}\u0000`; }); };
    PHRASES.forEach(p => put(new RegExp(p, "g"), "phrase"));
    put(DATE_RE, "date");
    put(COUNTRY_RE, "country");
    put(/\b[A-ZÀ-ÞŁŚŻŹĆŃ][A-ZÀ-ÞŁŚŻŹĆŃ'\-]+(?: (?:[A-ZÀ-ÞŁŚŻŹĆŃ][A-ZÀ-ÞŁŚŻŹĆŃ'\-]+|N\/A))+\b/g, "name");
    if (opts.ages !== false) put(/(?<=[ ,(])(\d{1,3})(?=,| year|-year| when|\.|$| at| upon)/g, "age");
    return t.replace(/\u0000(\d+)\u0000/g, (_, i) => marks[i]).replace(/\u0000(\d+)\u0000/g, (_, i) => marks[i]);
  }

  /* record drawer */
  function recordHTML(id) {
    if (id.includes("#")) {
      const [hid, field] = id.split("#"); const h = BY[hid];
      return `<p class="muted">Field <code>${esc(field)}</code> of hit ${esc(hid)}.</p>` + (h ? `<div class="quote">${esc(field === "deceased_date" ? h.hit.deceased_date : field === "dobs" ? h.hit.raw_dobs.join(", ") : h.hit.first_paragraph || "")}</div>` : "");
    }
    const r = rec(id);
    if (!r) return `<p class="muted">Record not included in this export.</p>`;
    const text = r.text || r.snippet;
    const rows = Object.entries(r).filter(([k, v]) => !["kind", "text", "snippet", "facts"].includes(k) && v !== null && v !== "")
      .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(typeof v === "object" ? JSON.stringify(v) : v)}</dd>`).join("");
    const facts = r.facts ? `<h4 style="margin:16px 0 8px">Facts extracted</h4><dl class="kv">${Object.entries(r.facts).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(Array.isArray(v) ? v.join(", ") : v ?? "–")}</dd>`).join("")}</dl>` : "";
    const ignored = r.kind === "kyc" && r.result !== "approved" ? `<p class="st blocked" style="margin:0 0 12px">${esc(r.result)}: not evidence (POLICY 4)</p>` : "";
    return ignored + (text ? `<div class="quote">${highlight(text)}</div><p class="faint" style="font-size:12px">Highlights: <mark class="hl date">dates</mark> <mark class="hl age">ages</mark> <mark class="hl country">countries</mark> <mark class="hl name">names</mark> <mark class="hl phrase">listing phrases</mark></p>` : "") +
      `<dl class="kv">${rows}</dl>` + facts;
  }
  function openRecord(id) {
    let back = document.querySelector(".drawer-back"), dr = document.querySelector(".drawer");
    if (!dr) {
      back = Object.assign(document.createElement("div"), { className: "drawer-back" });
      dr = Object.assign(document.createElement("aside"), { className: "drawer" });
      document.body.append(back, dr);
      back.onclick = () => { back.classList.remove("open"); dr.classList.remove("open"); };
    }
    dr.innerHTML = `<header><div><div class="faint" style="font-size:12px">${kindOf(id)}</div><h3 class="mono">${esc(id)}</h3></div><button class="btn" aria-label="Close">✕</button></header><div class="body">${recordHTML(id)}</div>`;
    dr.querySelector("button").onclick = back.onclick;
    requestAnimationFrame(() => { back.classList.add("open"); dr.classList.add("open"); });
  }
  document.addEventListener("click", e => { const c = e.target.closest("[data-rec]"); if (c) { e.preventDefault(); openRecord(c.dataset.rec); } });
  document.addEventListener("keydown", e => { if (e.key === "Escape") document.querySelector(".drawer-back")?.click(); });

  /* current hit lives in the URL hash so it survives switching variants */
  const DEFAULT = "H-0181BD";
  const current = () => BY[decodeURIComponent(location.hash.slice(1))] || BY[DEFAULT] || HITS[0];
  const go = id => { if (BY[id]) location.hash = id; };
  const listeners = [];
  window.addEventListener("hashchange", () => { listeners.forEach(f => f(current())); syncNav(); });
  const onHit = f => { listeners.push(f); f(current()); };

  function theme(t) {
    if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
    try { t ? localStorage.setItem("ux-theme", t) : localStorage.removeItem("ux-theme"); } catch (e) {}
  }
  try { const t = localStorage.getItem("ux-theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) {}

  function syncNav() {
    const h = current();
    document.querySelectorAll("[data-vlink]").forEach(a => a.href = a.dataset.vlink + "#" + h.hit_id);
    const inp = document.querySelector(".hitpick"); if (inp && document.activeElement !== inp) inp.value = hitTitle(h);
  }

  function nav(file, opts = {}) {
    const i = VARIANTS.findIndex(v => v[0] === file);
    const bar = document.createElement("header");
    bar.className = "topbar";
    const N = VARIANTS.length, prev = VARIANTS[(i + N - 1) % N], next = VARIANTS[(i + 1) % N];
    bar.innerHTML = `
      <a class="brand" data-vlink="index.html" href="index.html"><i></i>Screening Explainer</a>
      <div class="variants">
        <a class="btn" data-vlink="${prev[0]}" title="Previous: ${prev[1]}">←</a>
        <select class="select" aria-label="UI variant">${VARIANTS.map((v, j) => `<option value="${v[0]}" ${j === i ? "selected" : ""}>${j + 1}/${VARIANTS.length} · ${v[1]}</option>`).join("")}</select>
        <a class="btn" data-vlink="${next[0]}" title="Next: ${next[1]}">→</a>
      </div>
      <span class="faint" style="font-size:12px">${i >= 0 ? esc(VARIANTS[i][2]) : ""}</span>
      <span class="grow"></span>
      ${opts.noPicker ? "" : `<input class="input hitpick" list="hitlist" placeholder="Find a hit, name or alert…" aria-label="Hit"><datalist id="hitlist">${HITS.map(h => `<option value="${esc(hitTitle(h))}">`).join("")}</datalist>`}
      <button class="btn" title="Toggle theme" aria-label="Toggle theme">◐</button>`;
    document.body.prepend(bar);
    bar.querySelector("select").onchange = e => location.href = e.target.value + "#" + current().hit_id;
    bar.querySelector("button[title='Toggle theme']").onclick = () => {
      const dark = document.documentElement.dataset.theme === "dark" || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
      theme(dark ? "light" : "dark");
    };
    const inp = bar.querySelector(".hitpick");
    if (inp) {
      inp.onchange = () => { const v = inp.value.trim(); const h = BY[v.split(" ")[0]] || HITS.find(x => hitTitle(x).toLowerCase().includes(v.toLowerCase()) || x.alert_id === v); if (h) go(h.hit_id); };
      inp.onfocus = () => inp.select();
    }
    syncNav();
  }

  window.UX = { D, HITS, BY, ALERTS, VARIANTS, DEC, DEC_SHORT, CODE, RULE, STATUS, esc, rec, kindOf, pill, st, chip, person, years, highlight,
    openRecord, current, go, onHit, nav, hitTitle };
})();

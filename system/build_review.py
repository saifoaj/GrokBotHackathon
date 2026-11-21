"""Write a self-contained HTML review of every hit, its sources, and its decision.

Usage:
    python system/build_review.py [output/review.html]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def customer_view(row):
    return {
        "id": row.get("record_id"),
        "type": row.get("entity_type"),
        "name": row.get("full_name"),
        "preferred": row.get("preferred_name"),
        "login": row.get("login_name"),
        "dob": row.get("dob"),
        "nationality": row.get("nationality"),
        "residence": row.get("residence_country"),
        "city": row.get("city"),
        "address": row.get("address"),
        "email": row.get("email"),
    }


def check_view(row):
    return {
        "id": row.get("check_id"),
        "name": row.get("verified_name"),
        "dob": row.get("verified_dob"),
        "country": row.get("document_country"),
        "document": row.get("document_type"),
        "number": row.get("document_number"),
        "result": row.get("result"),
        "at": row.get("checked_at"),
        "provider": row.get("provider"),
    }


def note_view(row):
    return {
        "id": row.get("note_id"),
        "channel": row.get("channel"),
        "at": row.get("created_at"),
        "email": row.get("requester_email"),
        "text": row.get("text") or "",
    }


def compact(source, decision):
    hit = source.get("hit") or {}
    return {
        "id": source["hit_id"],
        "alert": source["alert_id"],
        "decision": decision.get("decision"),
        "code": decision.get("reason_code"),
        "record": decision.get("customer_record_id"),
        "reason": decision.get("reason") or "",
        "evidence": decision.get("evidence") or [],
        "screenedAt": source.get("screened_at"),
        "screened": source.get("screened") or {},
        "link": source.get("link") or {},
        "people": [
            {
                "ids": person.get("customer_record_ids") or [],
                "customers": [customer_view(row) for row in person.get("customers") or []],
                "checks": [check_view(row) for row in person.get("kyc_checks") or []],
                "notes": [note_view(row) for row in person.get("notes") or []],
            }
            for person in source.get("people") or []
        ],
        "hit": {
            "name": hit.get("name"),
            "type": hit.get("entity_type"),
            "aliases": hit.get("aliases") or [],
            "dobs": hit.get("dobs") or [],
            "gender": hit.get("gender"),
            "birthPlace": hit.get("place_of_birth"),
            "life": hit.get("life_status"),
            "deceased": hit.get("deceased_date"),
            "nationality": hit.get("nationality") or [],
            "citizenship": hit.get("citizenship") or [],
            "countries": hit.get("countries") or [],
            "addresses": hit.get("addresses") or [],
            "categories": hit.get("categories") or [],
            "primary": hit.get("primary_category"),
            "notes": hit.get("profile_notes") or "",
            "tied": source.get("hit_countries") or [],
            "phrase": source.get("listing_phrase"),
        },
        "search": [
            {
                "id": row.get("result_id"),
                "query": row.get("query"),
                "title": row.get("title"),
                "snippet": row.get("snippet") or "",
                "about": bool(row.get("about_listing")),
                "hitName": bool(row.get("matches_hit_name")),
                "customerName": bool(row.get("matches_customer_name")),
            }
            for row in source.get("search_results") or []
        ],
    }


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hit review</title>
<style>
  :root {
    --bg: #f3efe6;
    --paper: #fffdf8;
    --ink: #1d1a16;
    --muted: #6d655c;
    --line: #e2d9cc;
    --cleared: #1e6b45;
    --cleared-bg: #e5f3eb;
    --same: #8d2f2a;
    --same-bg: #f8e7e4;
    --review: #8a5a12;
    --review-bg: #f8efd8;
    --cited: #9a6b12;
    --shadow: 0 1px 0 rgba(29, 26, 22, 0.04);
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--ink);
    font: 15px/1.45 "Segoe UI", "Helvetica Neue", sans-serif;
  }
  button, input { font: inherit; color: inherit; }
  header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 16px;
    padding: 16px 22px 12px;
    border-bottom: 1px solid var(--line);
    background: var(--paper);
  }
  header h1 {
    margin: 0;
    font-family: Georgia, "Iowan Old Style", serif;
    font-size: 22px;
    font-weight: 600;
    letter-spacing: -0.02em;
  }
  header p { margin: 2px 0 0; color: var(--muted); font-size: 13px; }
  .shell { display: grid; grid-template-columns: 320px 1fr; height: calc(100vh - 69px); }
  aside {
    display: flex;
    flex-direction: column;
    min-height: 0;
    border-right: 1px solid var(--line);
    background: #f7f3eb;
  }
  .tools { padding: 12px; display: flex; flex-direction: column; gap: 8px; }
  #q {
    width: 100%;
    padding: 8px 10px;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: var(--paper);
  }
  #q:focus { outline: 2px solid #c4b49a; border-color: transparent; }
  .filters { display: flex; flex-wrap: wrap; gap: 6px; }
  .filters button, .ghost {
    border: 1px solid var(--line);
    background: var(--paper);
    border-radius: 999px;
    padding: 4px 10px;
    cursor: pointer;
    color: var(--muted);
  }
  .filters button[aria-pressed="true"] {
    background: var(--ink);
    color: #fff;
    border-color: var(--ink);
  }
  #list { overflow: auto; padding: 0 8px 12px; }
  .hit {
    display: block;
    width: 100%;
    text-align: left;
    border: 0;
    border-radius: 8px;
    background: transparent;
    padding: 8px 10px;
    cursor: pointer;
    margin-bottom: 2px;
  }
  .hit:hover { background: rgba(255, 255, 255, 0.7); }
  .hit[aria-current="true"] { background: var(--paper); box-shadow: var(--shadow); }
  .hit b { display: block; font-weight: 600; }
  .hit span { color: var(--muted); font-size: 12px; }
  .dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 6px;
    vertical-align: 1px;
  }
  .dot.cleared { background: var(--cleared); }
  .dot.same { background: var(--same); }
  .dot.review { background: var(--review); }
  main { overflow: auto; padding: 22px 28px 48px; }
  .empty { color: var(--muted); padding: 48px 8px; }
  .kicker {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    color: var(--muted);
    font-size: 13px;
  }
  .kicker code, .mono { font-family: Consolas, "Courier New", monospace; font-size: 12px; }
  h2 {
    margin: 8px 0 4px;
    font-family: Georgia, "Iowan Old Style", serif;
    font-size: 28px;
    font-weight: 600;
    letter-spacing: -0.03em;
  }
  .versus { margin: 0 0 12px; color: var(--muted); }
  .pill {
    display: inline-flex;
    align-items: center;
    border-radius: 999px;
    padding: 3px 10px;
    font-size: 13px;
    font-weight: 650;
  }
  .cleared { background: var(--cleared-bg); color: var(--cleared); }
  .same { background: var(--same-bg); color: var(--same); }
  .review { background: var(--review-bg); color: var(--review); }
  .reason {
    font-family: Georgia, "Iowan Old Style", serif;
    font-size: 18px;
    line-height: 1.45;
    margin: 12px 0 18px;
    max-width: 68ch;
  }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0 18px; }
  .chips button {
    border: 1px solid #ead9b0;
    background: #fff8ea;
    color: var(--cited);
    border-radius: 999px;
    padding: 3px 8px;
    cursor: pointer;
  }
  section { margin: 22px 0; }
  section h3 {
    margin: 0 0 8px;
    font-size: 13px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--muted);
  }
  .card {
    background: var(--paper);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 12px 14px;
    margin: 8px 0;
  }
  .card.cited { border-color: #e2c98a; box-shadow: inset 3px 0 0 var(--cited); }
  .card h4 { margin: 0 0 6px; font-size: 15px; }
  .meta { color: var(--muted); font-size: 13px; }
  dl { display: grid; grid-template-columns: 140px 1fr; gap: 3px 12px; margin: 8px 0 0; }
  dt { color: var(--muted); }
  dd { margin: 0; }
  .note {
    white-space: pre-wrap;
    margin: 8px 0 0;
    font-family: Georgia, "Iowan Old Style", serif;
    font-size: 15px;
  }
  .tag {
    display: inline-block;
    margin-left: 6px;
    padding: 1px 6px;
    border-radius: 999px;
    background: #fff8ea;
    color: var(--cited);
    font-size: 11px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .dim { opacity: 0.72; }
  .compare {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  @media (max-width: 900px) {
    .shell { grid-template-columns: 1fr; height: auto; }
    aside { border-right: 0; border-bottom: 1px solid var(--line); max-height: 42vh; }
    .compare { grid-template-columns: 1fr; }
    main { padding: 16px; }
  }
</style>
</head>
<body>
<header>
  <div>
    <h1>Hit review</h1>
    <p id="summary"></p>
  </div>
</header>
<div class="shell">
  <aside>
    <div class="tools">
      <input id="q" type="search" placeholder="Search name, id, or reason" autocomplete="off">
      <div class="filters" id="filters"></div>
    </div>
    <div id="list"></div>
  </aside>
  <main id="detail"></main>
</div>
<script id="payload" type="application/json">__DATA__</script>
<script>
const HITS = JSON.parse(document.getElementById("payload").textContent);
const LABEL = {
  DIFFERENT_ENTITY: ["Cleared", "cleared"],
  SAME_ENTITY: ["Same person", "same"],
  REVIEW: ["Review", "review"],
};
const CODE = {
  TEMPORAL_IMPOSSIBILITY: "Too young, or not yet incorporated",
  DOB_MISMATCH: "Date of birth contradicts",
  LOCATION_MISMATCH: "Countries do not overlap",
  NAME_MISMATCH_VERIFIED: "Verified name does not match",
  ENTITY_TYPE_MISMATCH: "Person versus company",
  DECEASED: "Died before screening",
  NAME_DOB_LOCATION_MATCH: "Name, birth date, and country agree",
  NAME_DOB_MATCH: "Name and birth date agree",
  NAME_LOCATION_MATCH: "Name and country agree",
  INSUFFICIENT_DATA: "Not enough to decide",
  AMBIGUOUS_CUSTOMER_RECORD: "Several people, decisions differ",
};
const VIA = {
  email: "Found by the email on the alert",
  account_ref: "Found by the account number",
  login_handle: "Found by the login",
  screened_attributes: "Found by the screened name, birth date, country, and city",
};

const state = { filter: "ALL", query: "", index: 0 };

const listEl = document.getElementById("list");
const detailEl = document.getElementById("detail");
const filtersEl = document.getElementById("filters");
const queryEl = document.getElementById("q");

function textOf(hit) {
  const names = [hit.screened && hit.screened.name, hit.hit && hit.hit.name, hit.reason, hit.id, hit.alert, hit.record];
  (hit.evidence || []).forEach((id) => names.push(id));
  (hit.people || []).forEach((person) => {
    (person.customers || []).forEach((row) => names.push(row.name, row.id));
  });
  return names.filter(Boolean).join(" ").toLowerCase();
}
HITS.forEach((hit) => { hit._text = textOf(hit); });

function visible() {
  const q = state.query.trim().toLowerCase();
  return HITS.filter((hit) => {
    if (state.filter !== "ALL" && hit.decision !== state.filter) return false;
    if (q && !hit._text.includes(q)) return false;
    return true;
  });
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function add(parent, node) { parent.appendChild(node); return node; }

function fact(dl, label, value) {
  if (value == null || value === "" || (Array.isArray(value) && !value.length)) return;
  const shown = Array.isArray(value) ? value.join(", ") : String(value);
  add(dl, el("dt", "", label));
  add(dl, el("dd", "", shown));
}

function citedSet(hit) { return new Set(hit.evidence || []); }

function renderFilters(rows) {
  const counts = { ALL: HITS.length, DIFFERENT_ENTITY: 0, SAME_ENTITY: 0, REVIEW: 0 };
  HITS.forEach((hit) => { counts[hit.decision] = (counts[hit.decision] || 0) + 1; });
  const options = [
    ["ALL", "All " + counts.ALL],
    ["DIFFERENT_ENTITY", "Cleared " + counts.DIFFERENT_ENTITY],
    ["SAME_ENTITY", "Same " + counts.SAME_ENTITY],
    ["REVIEW", "Review " + counts.REVIEW],
  ];
  filtersEl.replaceChildren();
  options.forEach(([key, label]) => {
    const button = el("button", "", label);
    button.type = "button";
    button.setAttribute("aria-pressed", state.filter === key ? "true" : "false");
    button.addEventListener("click", () => {
      state.filter = key;
      state.index = 0;
      render();
    });
    filtersEl.appendChild(button);
  });
  document.getElementById("summary").textContent =
    rows.length + " shown · " + counts.DIFFERENT_ENTITY + " cleared · " +
    counts.SAME_ENTITY + " same person · " + counts.REVIEW + " review";
}

function renderList(rows) {
  const fragment = document.createDocumentFragment();
  rows.forEach((hit, index) => {
    const button = el("button", "hit");
    button.type = "button";
    if (index === state.index) button.setAttribute("aria-current", "true");
    const tone = (LABEL[hit.decision] || ["", ""])[1];
    const title = el("b");
    title.appendChild(el("span", "dot " + tone));
    title.append((hit.hit && hit.hit.name) || hit.id);
    button.appendChild(title);
    button.appendChild(el("span", "", hit.id + " · " + (LABEL[hit.decision] || ["?"])[0]));
    button.addEventListener("click", () => {
      state.index = index;
      render();
    });
    fragment.appendChild(button);
  });
  listEl.replaceChildren(fragment);
  const current = listEl.querySelector('[aria-current="true"]');
  if (current) current.scrollIntoView({ block: "nearest" });
}

function card(title, id, cited) {
  const node = el("article", "card" + (cited ? " cited" : ""));
  if (id) node.id = "card-" + id;
  const heading = el("h4", "", title || id || "Record");
  if (id) heading.appendChild(el("span", "mono", "  " + id));
  if (cited) heading.appendChild(el("span", "tag", "Cited"));
  node.appendChild(heading);
  return node;
}

function renderDetail(hit) {
  detailEl.replaceChildren();
  if (!hit) {
    detailEl.appendChild(el("p", "empty", "No hit matches this filter."));
    return;
  }
  const tone = LABEL[hit.decision] || ["Undecided", "review"];
  const kicker = el("div", "kicker");
  kicker.appendChild(el("span", "pill " + tone[1], tone[0]));
  kicker.appendChild(el("code", "", hit.code || ""));
  kicker.appendChild(el("span", "", CODE[hit.code] || ""));
  detailEl.appendChild(kicker);

  const heading = el("h2", "", (hit.hit && hit.hit.name) || hit.id);
  detailEl.appendChild(heading);
  const screenedName = (hit.screened && hit.screened.name) || "unknown";
  detailEl.appendChild(el(
    "p",
    "versus",
    "Screened as " + screenedName +
    (hit.screenedAt ? " on " + hit.screenedAt : "") +
    " · " + hit.id + " · " + hit.alert +
    " · customer " + (hit.record || "none")
  ));
  detailEl.appendChild(el("p", "reason", hit.reason || "No reason recorded."));

  const cited = citedSet(hit);
  if (hit.evidence && hit.evidence.length) {
    const chips = el("div", "chips");
    hit.evidence.forEach((id) => {
      const button = el("button", "", id);
      button.type = "button";
      button.addEventListener("click", () => {
        const target = document.getElementById("card-" + id);
        if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      chips.appendChild(button);
    });
    detailEl.appendChild(chips);
  }

  const compare = el("div", "compare");
  compare.appendChild(screenedCard(hit));
  compare.appendChild(hitCard(hit, cited));
  detailEl.appendChild(compare);

  const path = el("section");
  path.appendChild(el("h3", "", "How the customer was found"));
  const link = hit.link || {};
  const pathCard = el("article", "card");
  pathCard.appendChild(el("p", "meta", VIA[link.via] || link.via || "No link recorded"));
  const pathFacts = el("dl");
  fact(pathFacts, "Account", link.account_ref);
  fact(pathFacts, "Login", link.login_handle);
  fact(pathFacts, "Email", link.email);
  fact(pathFacts, "Linked records", link.linked_customer_ids);
  pathCard.appendChild(pathFacts);
  path.appendChild(pathCard);
  detailEl.appendChild(path);

  (hit.people || []).forEach((person, index) => {
    const section = el("section");
    const label = (hit.people.length > 1 ? "Person " + (index + 1) + " · " : "Customer · ") +
      (person.ids || []).join(", ");
    section.appendChild(el("h3", "", label));
    (person.customers || []).forEach((row) => {
      const node = card(row.name, row.id, cited.has(row.id));
      const facts = el("dl");
      fact(facts, "Type", row.type);
      fact(facts, "Preferred", row.preferred);
      fact(facts, "Login name", row.login);
      fact(facts, "Born", row.dob);
      fact(facts, "Nationality", row.nationality);
      fact(facts, "Lives in", row.residence);
      fact(facts, "City", row.city);
      fact(facts, "Address", row.address);
      fact(facts, "Email", row.email);
      node.appendChild(facts);
      section.appendChild(node);
    });
    (person.checks || []).forEach((row) => {
      const node = card((row.provider || "Check") + (row.result ? " · " + row.result : ""), row.id, cited.has(row.id));
      if (row.result && row.result !== "approved") node.classList.add("dim");
      const facts = el("dl");
      fact(facts, "Name", row.name);
      fact(facts, "Born", row.dob);
      fact(facts, "Document", [row.document, row.country].filter(Boolean).join(" · "));
      fact(facts, "Number", row.number);
      fact(facts, "Checked", row.at);
      node.appendChild(facts);
      section.appendChild(node);
    });
    (person.notes || []).forEach((row) => {
      const node = card((row.channel || "note") + (row.at ? " · " + row.at : ""), row.id, cited.has(row.id));
      if (row.email) node.appendChild(el("p", "meta", row.email));
      node.appendChild(el("p", "note", row.text));
      section.appendChild(node);
    });
    if (!(person.customers || []).length && !(person.checks || []).length && !(person.notes || []).length) {
      section.appendChild(el("p", "meta", "No customer records were gathered."));
    }
    detailEl.appendChild(section);
  });

  const search = el("section");
  search.appendChild(el("h3", "", "Search results"));
  const rows = hit.search || [];
  const lead = rows.filter((row) => row.about || cited.has(row.id));
  const rest = rows.filter((row) => !row.about && !cited.has(row.id));
  if (!rows.length) search.appendChild(el("p", "meta", "No search result uses this name."));
  lead.forEach((row) => search.appendChild(searchCard(row, cited)));
  if (rest.length) {
    const more = el("button", "ghost", "Show " + rest.length + " other results for this name");
    more.type = "button";
    more.addEventListener("click", () => {
      rest.forEach((row) => search.appendChild(searchCard(row, cited)));
      more.remove();
    });
    search.appendChild(more);
  }
  detailEl.appendChild(search);
  detailEl.scrollTop = 0;
  history.replaceState(null, "", "#" + hit.id);
}

function screenedCard(hit) {
  const node = card("Screened as", null, false);
  const facts = el("dl");
  const row = hit.screened || {};
  fact(facts, "Name", row.name);
  fact(facts, "Type", row.entity_type);
  fact(facts, "Born", row.dob);
  fact(facts, "Country", row.country);
  fact(facts, "City", row.city);
  node.appendChild(facts);
  return node;
}

function hitCard(hit, cited) {
  const profile = hit.hit || {};
  const node = card("Watchlist hit", hit.id, cited.has(hit.id));
  const facts = el("dl");
  fact(facts, "Type", profile.type);
  fact(facts, "Aliases", profile.aliases);
  fact(facts, "Birth dates", profile.dobs);
  fact(facts, "Countries", profile.countries);
  fact(facts, "Nationality", profile.nationality);
  fact(facts, "Citizenship", profile.citizenship);
  fact(facts, "Born in", profile.birthPlace);
  fact(facts, "Tied countries", profile.tied);
  fact(facts, "Category", profile.primary || (profile.categories || []).join(", "));
  fact(facts, "Life", profile.life);
  fact(facts, "Died", profile.deceased);
  fact(facts, "Listing phrase", profile.phrase);
  node.appendChild(facts);
  if (profile.addresses && profile.addresses.length) {
    node.appendChild(el("p", "meta", profile.addresses.join(" · ")));
  }
  if (profile.notes) {
    const preview = profile.notes.length > 700 ? profile.notes.slice(0, 700) + "…" : profile.notes;
    const body = el("p", "note", preview);
    node.appendChild(body);
    if (profile.notes.length > 700) {
      const more = el("button", "ghost", "Show the full profile");
      more.type = "button";
      more.addEventListener("click", () => {
        body.textContent = profile.notes;
        more.remove();
      });
      node.appendChild(more);
    }
  }
  return node;
}

function searchCard(row, cited) {
  const node = card(row.title || row.query, row.id, cited.has(row.id));
  if (!row.about) node.classList.add("dim");
  const bits = [row.query];
  if (row.about) bits.push("about this listing");
  else if (row.hitName) bits.push("same name as the hit");
  if (row.customerName) bits.push("same name as the customer");
  node.appendChild(el("p", "meta", bits.join(" · ")));
  node.appendChild(el("p", "note", row.snippet));
  return node;
}

function render() {
  const rows = visible();
  if (state.index >= rows.length) state.index = 0;
  renderFilters(rows);
  renderList(rows);
  renderDetail(rows[state.index]);
}

queryEl.addEventListener("input", () => {
  state.query = queryEl.value;
  state.index = 0;
  render();
});

document.addEventListener("keydown", (event) => {
  if (event.target === queryEl) return;
  const rows = visible();
  if (event.key === "ArrowDown") {
    state.index = Math.min(rows.length - 1, state.index + 1);
    render();
    event.preventDefault();
  } else if (event.key === "ArrowUp") {
    state.index = Math.max(0, state.index - 1);
    render();
    event.preventDefault();
  }
});

const fromHash = location.hash.replace("#", "");
const start = HITS.findIndex((hit) => hit.id === fromHash);
if (start >= 0) state.index = start;
render();
</script>
</body>
</html>
"""


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "output" / "review.html"
    sources = {row["hit_id"]: row for row in load_jsonl(ROOT / "output" / "hit_sources.jsonl")}
    decisions = {row["hit_id"]: row for row in load_jsonl(ROOT / "output" / "dev.jsonl")}
    rows = [compact(source, decisions.get(hit_id, {})) for hit_id, source in sources.items()]
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(PAGE.replace("__DATA__", payload), encoding="utf-8")
    print(f"wrote {len(rows)} hits to {output} ({output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

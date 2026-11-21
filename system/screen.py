"""Same Person or Not? Deterministic, rule-based watchlist hit resolver (POLICY.md). Stdlib only, no LLM."""
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache

# ---------------------------------------------------------------- 1. Load

FILES = ["alerts", "hits", "customers", "accounts", "onboarding", "kyc_checks", "notes", "search_results"]


def load(data_dir):
    data = {}
    for name in FILES:
        with open(os.path.join(data_dir, name + ".jsonl"), encoding="utf-8") as fh:
            data[name] = [json.loads(line) for line in fh if line.strip()]
    with open(os.path.join(data_dir, "name_variants.json"), encoding="utf-8") as fh:
        data["name_variants"] = json.load(fh)
    return data

# ---------------------------------------------------------------- 2. Normalize

def fold(s):
    """Lowercase, strip accents."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().replace("ø", "o").replace("ł", "l").replace("ß", "ss").replace("đ", "d").replace("æ", "ae")


PLACEHOLDERS = {"na", "n/a", "test", "unknown", "xxx", "none", "null", "nil", "tbd", "tba", "nn", "fnu", "lnu", "nomiddlename", "xx", "x", "noname", "dummy"}
VARIANT = {}          # name part -> group id
LEGAL_FORMS = set()


def set_variants(nv):
    VARIANT.clear()
    for i, group in enumerate(nv["groups"]):
        for part in group:
            VARIANT[fold(part)] = i
    LEGAL_FORMS.clear()
    LEGAL_FORMS.update(fold(f) for f in nv["company_legal_forms"])


def norm_name(s, company=False):
    """Distinct name parts in first-seen order: no brackets, punctuation, initials, placeholders or (companies) legal forms."""
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}", " ", fold(s))
    raw = re.split(r"\s+", s)
    if any(r.strip(".,;:") in ("n/a", "n.a.") for r in raw):
        raw = [r for r in raw if r.strip(".,;:") not in ("n/a", "n.a.")]
    parts = []
    for tok in re.split(r"[^a-z0-9']+", " ".join(raw).replace("'", "")):
        if len(tok) < 2 or tok in PLACEHOLDERS or any(c.isdigit() for c in tok) or re.fullmatch(r"(.)\1+", tok):
            continue
        if company and tok in LEGAL_FORMS:
            continue
        if tok not in parts:
            parts.append(tok)
    return parts


MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"])}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["sept"] = 9
MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"


@dataclass(frozen=True)
class Date:
    year: int
    month: int = None
    day: int = None

    def __lt__(self, other):
        return (self.year, self.month or 0, self.day or 0) < (other.year, other.month or 0, other.day or 0)

    def __str__(self):
        return "-".join([f"{self.year:04d}"] + [f"{v:02d}" for v in (self.month, self.day) if v])

    @property
    def full(self):
        return self.day is not None

    def earliest(self):
        return (self.year, self.month or 1, self.day or 1)

    def latest(self):
        return (self.year, self.month or 12, self.day or 31)


def _mk(y, m=None, d=None):
    y, m, d = int(y), (int(m) if m else None), (int(d) if d else None)
    if not 1800 <= y <= 2100 or (m and not 1 <= m <= 12) or (d and not 1 <= d <= 31) or (d and not m):
        return None
    return Date(y, m, d)


DATE_RES = [  # (regex, group order) - every format seen in the data
    (r"(\d{4})-(\d{1,2})-(\d{1,2})", "ymd"), (r"(\d{4})/(\d{1,2})/(\d{1,2})", "ymd"),
    (r"(\d{1,2})/(\d{1,2})/(\d{4})", "dmy"), (r"(\d{1,2})\.(\d{1,2})\.(\d{4})", "dmy"),
    (r"(\d{1,2})(?:st|nd|rd|th)? (" + MON + r"),? (\d{4})", "dMy"), (r"(" + MON + r") (\d{1,2})(?:st|nd|rd|th)?,? (\d{4})", "Mdy"),
    (r"(" + MON + r"),? (\d{4})", "My"), (r"(\d{4})-(\d{1,2})", "ym"), (r"(\d{4})", "y"),
]
DATE_ANY = re.compile(r"(?<![\w/.-])(?:" + "|".join("(?:%s)" % r for r, _ in DATE_RES) + r")(?![\w/-])", re.I)


def norm_date(s):
    """Parse one date string; DD/MM/YYYY for slashed dates (verified on the data). None if unparseable."""
    if not s:
        return None
    s = str(s).strip()
    for rx, order in DATE_RES:
        m = re.fullmatch(rx, s, re.I)
        if m:
            g = dict(zip(order, m.groups()))
            if "M" in g:
                g["m"] = MONTHS.get(fold(g["M"]).rstrip("."))
            return _mk(g["y"], g.get("m"), g.get("d"))
    return None


def find_dates(text):
    """All (Date, span) in free text, most specific match first per span."""
    out = []
    for m in DATE_ANY.finditer(text or ""):
        d = norm_date(m.group(0))
        if d:
            out.append((d, m.span()))
    return out


COUNTRY_TABLE = """AF Afghanistan|Afghan;AL Albania|Albanian;DZ Algeria|Algerian;AD Andorra;AO Angola|Angolan;AG Antigua and Barbuda;AR Argentina|Argentine|Argentinian;AM Armenia|Armenian;AU Australia|Australian;AT Austria|Austrian;AZ Azerbaijan|Azerbaijani;BS Bahamas;BH Bahrain|Bahraini;BD Bangladesh|Bangladeshi;BB Barbados;BY Belarus|Belarusian;BE Belgium|Belgian;BZ Belize;BJ Benin;BT Bhutan;BO Bolivia|Bolivian;BA Bosnia and Herzegovina|Bosnia|Bosnian;BW Botswana;BR Brazil|Brazilian;BN Brunei;BG Bulgaria|Bulgarian;BF Burkina Faso;BI Burundi;KH Cambodia|Cambodian;CM Cameroon|Cameroonian;CA Canada|Canadian;CV Cape Verde|Cabo Verde;CF Central African Republic;TD Chad|Chadian;CL Chile|Chilean;CN China|Chinese|People's Republic of China;CO Colombia|Colombian;KM Comoros;CG Congo|Republic of the Congo;CD Democratic Republic of the Congo|DR Congo|DRC;CR Costa Rica|Costa Rican;CI Cote d'Ivoire|Cote D'Ivoire|Ivory Coast|Ivorian;HR Croatia|Croatian;CU Cuba|Cuban;CY Cyprus|Cypriot;CZ Czech Republic|Czechia|Czech;DK Denmark|Danish|Dane;DJ Djibouti;DM Dominica;DO Dominican Republic|Dominican;EC Ecuador|Ecuadorian;EG Egypt|Egyptian;SV El Salvador|Salvadoran;GQ Equatorial Guinea;ER Eritrea|Eritrean;EE Estonia|Estonian;SZ Eswatini|Swaziland;ET Ethiopia|Ethiopian;FJ Fiji|Fijian;FI Finland|Finnish|Finn;FR France|French;GA Gabon;GM Gambia|Gambian;GE Georgia|Georgian;DE Germany|German;GH Ghana|Ghanaian;GR Greece|Greek;GD Grenada;GT Guatemala|Guatemalan;GN Guinea|Guinean;GW Guinea-Bissau;GY Guyana|Guyanese;HT Haiti|Haitian;HN Honduras|Honduran;HU Hungary|Hungarian;IS Iceland|Icelandic;IN India|Indian;ID Indonesia|Indonesian;IR Iran|Iranian|Islamic Republic of Iran;IQ Iraq|Iraqi;IE Ireland|Irish|Republic of Ireland;IL Israel|Israeli;IT Italy|Italian;JM Jamaica|Jamaican;JP Japan|Japanese;JO Jordan|Jordanian;KZ Kazakhstan|Kazakh;KE Kenya|Kenyan;KI Kiribati;XK Kosovo|Kosovan|Kosovar;KW Kuwait|Kuwaiti;KG Kyrgyzstan|Kyrgyz;LA Laos|Lao;LV Latvia|Latvian;LB Lebanon|Lebanese;LS Lesotho;LR Liberia|Liberian;LY Libya|Libyan;LI Liechtenstein;LT Lithuania|Lithuanian;LU Luxembourg|Luxembourgish;MG Madagascar|Malagasy;MW Malawi|Malawian;MY Malaysia|Malaysian;MV Maldives;ML Mali|Malian;MT Malta|Maltese;MH Marshall Islands;MR Mauritania|Mauritanian;MU Mauritius;MX Mexico|Mexican;FM Micronesia;MD Moldova|Moldovan|Republic of Moldova;MC Monaco;MN Mongolia|Mongolian;ME Montenegro|Montenegrin;MA Morocco|Moroccan;MZ Mozambique;MM Myanmar|Burma|Burmese;NA Namibia|Namibian;NR Nauru;NP Nepal|Nepalese|Nepali;NL Netherlands|Dutch|Holland|The Netherlands;NZ New Zealand|New Zealander;NI Nicaragua|Nicaraguan;NE Niger;NG Nigeria|Nigerian;KP North Korea|Democratic People's Republic of Korea|DPRK|North Korean;MK North Macedonia|Macedonia|Macedonian;NO Norway|Norwegian;OM Oman|Omani;PK Pakistan|Pakistani;PW Palau;PS Palestine|Palestina|Palestinian|State of Palestine;PA Panama|Panamanian;PG Papua New Guinea;PY Paraguay|Paraguayan;PE Peru|Peruvian;PH Philippines|Filipino|Philippine;PL Poland|Polish;PT Portugal|Portuguese;QA Qatar|Qatari;RO Romania|Romanian;RU Russia|Russian|Russian Federation;RW Rwanda|Rwandan;KN Saint Kitts and Nevis;LC Saint Lucia;VC Saint Vincent and the Grenadines;WS Samoa;SM San Marino;ST Sao Tome and Principe;SA Saudi Arabia|Saudi;SN Senegal|Senegalese;RS Serbia|Serbian;SC Seychelles;SL Sierra Leone|Sierra Leonean;SG Singapore|Singaporean;SK Slovakia|Slovak;SI Slovenia|Slovenian;SB Solomon Islands;SO Somalia|Somali;ZA South Africa|South African;KR South Korea|Republic of Korea|South Korean;SS South Sudan;ES Spain|Spanish;LK Sri Lanka|Sri Lankan;SD Sudan|Sudanese;SR Suriname;SE Sweden|Swedish|Swede;CH Switzerland|Swiss;SY Syria|Syrian|Syrian Arab Republic;TW Taiwan|Taiwanese;TJ Tajikistan;TZ Tanzania|Tanzanian;TH Thailand|Thai;TL Timor-Leste|East Timor;TG Togo;TO Tonga;TT Trinidad and Tobago|Trinidadian;TN Tunisia|Tunisian;TR Turkey|Turkish|Turkiye;TM Turkmenistan;TV Tuvalu;UG Uganda|Ugandan;UA Ukraine|Ukrainian;AE United Arab Emirates|UAE|Emirati;GB United Kingdom|British|UK|Great Britain|Britain|England|Scotland|Wales|Northern Ireland|English|Scottish|Welsh;US United States|American|USA|United States of America|U.S.|U.S.A.;UY Uruguay|Uruguayan;UZ Uzbekistan|Uzbek;VU Vanuatu;VA Vatican|Holy See;VE Venezuela|Venezuelan;VN Vietnam|Vietnamese|Viet Nam;YE Yemen|Yemeni;ZM Zambia|Zambian;ZW Zimbabwe|Zimbabwean;HK Hong Kong;MO Macao|Macau|Macao Special Administrative Region of China;PR Puerto Rico|Puerto Rican;MP Northern Mariana Islands;GU Guam;VG British Virgin Islands;VI US Virgin Islands|U.S. Virgin Islands;KY Cayman Islands;BM Bermuda;GI Gibraltar;JE Jersey;GG Guernsey;IM Isle of Man;AW Aruba;CW Curacao;GL Greenland;FO Faroe Islands;NC New Caledonia;PF French Polynesia;AI Anguilla;TC Turks and Caicos Islands;MS Montserrat;AS American Samoa;RE Reunion;GP Guadeloupe;MQ Martinique;GF French Guiana"""
COUNTRY, COUNTRY_NAME = {}, {}
for _entry in COUNTRY_TABLE.split(";"):
    _code, _names = _entry.split(" ", 1)
    COUNTRY_NAME[_code] = _names.split("|")[0]
    COUNTRY[_code.lower()] = _code
    for _n in _names.split("|"):
        COUNTRY[re.sub(r"[^a-z ]", "", fold(_n)).strip()] = _code
COUNTRY.update({"uk": "GB", "the netherlands": "NL", "the united states": "US", "the united kingdom": "GB", "the gambia": "GM", "the bahamas": "BS"})
NOT_COUNTRIES = {"international", "europe", "worldwide", "global", "unknown", "", "na", "none", "eu", "european union", "africa", "asia", "middle east"}
UNKNOWN = "?"     # a country value we could not normalize: blocks LOCATION_MISMATCH


def norm_country(s):
    """Country code, None for non-countries (International, Europe, empty), UNKNOWN if unrecognized."""
    if s is None:
        return None
    s = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", str(s)).split(",")[-1]
    key = re.sub(r"[^a-z ]", "", fold(s)).strip()
    key = re.sub(r"\s+", " ", key)
    if key in NOT_COUNTRIES:
        return None
    return COUNTRY.get(key) or COUNTRY.get(re.sub(r"^the ", "", key)) or UNKNOWN


_TEXT_COUNTRY = sorted({n for _e in COUNTRY_TABLE.split(";") for n in _e.split(" ", 1)[1].split("|") if len(n) > 3 or n in ("UK", "UAE", "USA", "DRC", "DPRK")}, key=lambda n: (-len(n), n))
TEXT_COUNTRY_RE = re.compile(r"(?<![\w.])(" + "|".join(re.escape(n) for n in _TEXT_COUNTRY) + r")(?![\w])")


def countries_in_text(text):
    """Country codes named in free text (case-sensitive proper nouns and demonyms), sorted."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return sorted({COUNTRY[re.sub(r"[^a-z ]", "", fold(m.group(1))).strip()] for m in TEXT_COUNTRY_RE.finditer(t)})


def norm_phone(s):
    d = re.sub(r"\D", "", s or "")
    return d.lstrip("0") if len(d) >= 7 else None


def norm_doc(s):
    d = re.sub(r"[^A-Z0-9]", "", (s or "").upper())
    return d if len(d) >= 5 else None

# ---------------------------------------------------------------- 3. Extract

NEGATIVE = re.compile(r"\b(fail\w*|reject\w*|declin\w*|expir\w*|unable|could not|cannot|not|denied|unsuccessful\w*|mismatch\w*|invalid|refus\w*|pending|incomplete|suspend\w*|revoked|lapsed|fraudulent|forged|no longer)\b", re.I)
NOT_NAME_TOKENS = {"ID", "DOB", "KYC", "UK", "US", "USA", "UAE", "EU", "DRC", "DPRK", "N/A"}


def upper_name(text):
    """The longest run of ALL-CAPS words (how KYC notes write the verified name), placeholders kept."""
    runs, cur = [], []
    for tok in text.split() + [""]:
        core = re.sub(r"(?:'s|'S|\u2019s)$", "", tok.strip(",.;:()[]\"")).strip(",.;:()")
        if core and (core == "N/A" or (core.isupper() and core.strip(".'-") not in NOT_NAME_TOKENS)):
            cur.append(core)
            if core != tok.strip("()"):          # trailing punctuation or possessive ends the name
                runs.append(cur)
                cur = []
        else:
            runs.append(cur)
            cur = []
    runs = [r for r in runs if any(len(t.strip(".'-")) >= 2 and t != "N/A" for t in r)]
    return " ".join(max(runs, key=len)) if runs else None


def parse_note(note):
    """kyc-channel note -> {'kind':'kyc', name, dob, country, date}; other notes -> {'kind':'lived', countries}; else None."""
    text = note["text"]
    if note["channel"] == "kyc":
        if NEGATIVE.search(text):
            return None                                # rejected/expired/failed checks are not evidence
        created = norm_date(note["created_at"])
        dates = [d for d, _ in find_dates(text)]
        others = [d for d in dates if d != created]
        dob = max(others, key=lambda d: (d.full, d.month is not None)) if others else None
        if dob and created and dob.earliest() >= created.earliest():
            dob = None
        name = upper_name(text)
        found = countries_in_text(text)
        return {"kind": "kyc", "name": name, "dob": dob, "country": found[0] if len(found) == 1 else (found or None),
                "date": created}
    found = countries_in_text(text)
    return {"kind": "lived", "countries": found} if found else None


AGE_CUES = [r"\baged (\d{1,3})\b", r"\bage of (\d{1,3})\b", r"\bat age (\d{1,3})\b", r"\bat (\d{1,3})(?=[,;.]| (?:years|was|he|she|when))",
            r"\bthen (\d{1,3})\b", r"\b(\d{1,3})(?:\s|-)years?(?:\s|-)old\b", r"\b(\d{1,3}) years of age\b", r"\breached (\d{1,3})\b",
            r"\b(?:was|is|being) (\d{1,3})(?: years)?(?=[,;.]|\s+(?:at|when|in|on|upon|as|during|while)\b|$)", r"\bage (\d{1,3})\b", r"\bage: ?(\d{1,3})\b",
            r"(?<=[a-zà-ÿ]), (\d{1,3}),"]
AGE_RE = re.compile("|".join(AGE_CUES), re.I)
DATED_REPORT = re.compile(r"^\s*According to [^;\n]+; ((?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}): ", re.M)
DEATH_RE = re.compile(r"\b(?:died|death|passed away|deceased)\b", re.I)


def _age_dates(sentence):
    """(age, event Date) for a sentence stating an age at a date, else None."""
    dates = find_dates(sentence)
    if not dates:
        return None
    ages = []
    for m in AGE_RE.finditer(sentence):
        g = next(x for x in m.groups() if x)
        if any(sp[0] <= m.start(m.lastindex) < sp[1] for _, sp in dates):
            continue
        ages.append(int(g))
    ages = sorted(set(a for a in ages if 1 <= a <= 110))
    events = sorted({d for d, _ in dates})
    if len(ages) != 1 or len(events) != 1:
        return None
    return ages[0], events[0]


def birth_years(age, event):
    return (event.year - age - 1, event.year - age)


def parse_hit_notes(text, name_parts=()):
    """First paragraph: birth date or age-at-date; whole notes: dated reports, date of death."""
    facts = {"dobs": [], "reports": [], "death": None}
    if not text:
        return facts
    first = re.split(r"\n\s*\n", text.strip())[0]
    sentences = [s for s in re.split(r"(?<=[.;!?])\s+(?=[A-Z])|\n", first) if s.strip()]
    as_of = None
    m = re.search(r"\bas of (" + MON + r" \d{1,2}, \d{4})", first)
    if m and re.search(r"^Age: ?\d{1,3}\s*$", first, re.M):
        as_of = norm_date(m.group(1))
    for s in sentences:
        mentions = any(p in fold(s) for p in name_parts if len(p) >= 3) or re.search(r"\b(he|she|his|her)\b", s, re.I)
        b = re.search(r"\bborn (?:on |in )?(.+?)(?:[.;,]|\bin\b [A-Z]|$)", s)
        if b and mentions:
            ds = find_dates(b.group(1))
            if ds:
                d = ds[0][0]
                facts["dobs"].append(((d.year,), d.month, d.day, f"born {d}"))
                continue
        a = re.fullmatch(r"\s*Age: ?(\d{1,3})\s*", s)
        if a and as_of:
            ys = birth_years(int(a.group(1)), as_of)
            facts["dobs"].append((ys, None, None, f"aged {a.group(1)} on {as_of}"))
            continue
        if mentions and not DATED_REPORT.match(s):
            got = _age_dates(s)
            if got:
                facts["dobs"].append((birth_years(*got), None, None, f"aged {got[0]} in {got[1]}"))
    for m in DATED_REPORT.finditer(text):
        d = norm_date(m.group(1))
        if d:
            facts["reports"].append(d)
    for s in re.split(r"(?<=[.;])\s+", text):
        if DEATH_RE.search(s) and re.search(r"\b(?:he|she|who|[A-Z][a-z]+) (?:died|passed away)\b|\bdate of death\b|\bdeceased (?:on|in)\b", s):
            ds = find_dates(s)
            if len(ds) == 1:
                facts["death"] = ds[0][0]
    return facts


LISTING = {"sanctions": "a sanctioned individual", "pep": "a politically exposed person", "enforcement": "the subject of enforcement action",
           "adverse media": "named in adverse media", "soe": "a state-owned enterprise official"}
ALL_LISTINGS = sorted(set(LISTING.values()) | {"named on a watchlist"})


def parse_snippet(result):
    """Search snippet -> {listing, countries, birth_years} or None when no single age at a single date."""
    text = result["snippet"]
    dates = find_dates(text)
    stripped = text
    for a, b in sorted((sp for _, sp in dates), reverse=True):
        stripped = stripped[:a] + " " * (b - a) + stripped[b:]
    ages = sorted({int(x) for x in re.findall(r"(?<![\w.,])(\d{1,3})(?![\w]|[.,]\d)", stripped) if 1 <= int(x) <= 110})
    events = sorted({d for d, _ in dates})
    listing = [p for p in ALL_LISTINGS if p in text]
    if len(ages) != 1 or len(events) != 1:
        return None
    unnamed = text
    for part in sorted(set(result["query"].split()), key=len, reverse=True):     # "Jordan Smith" is not Jordan
        unnamed = re.sub(r"(?i)\b" + re.escape(part) + r"\b", " ", unnamed) if len(part) > 1 else unnamed
    return {"listing": listing, "countries": countries_in_text(unnamed), "years": birth_years(ages[0], events[0]),
            "age": ages[0], "event": events[0]}

# ---------------------------------------------------------------- 4. Index


class UnionFind:
    def __init__(self, items):
        self.parent = {i: i for i in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            lo, hi = sorted((ra, rb))
            self.parent[hi] = lo


def build_index(data):
    set_variants(data["name_variants"])
    ix = {"cust": {c["record_id"]: c for c in data["customers"]}, "hits": {h["hit_id"]: h for h in data["hits"]}}
    ix["dob"] = {rid: norm_date(c["dob"]) for rid, c in ix["cust"].items()}
    ix["accounts"], ix["acc_of"], ix["login"] = defaultdict(list), defaultdict(list), defaultdict(list)
    phones = defaultdict(set)
    for a in sorted(data["accounts"], key=lambda a: (a["account_id"], a["customer_id"])):
        ix["accounts"][a["account_id"]].append(a["customer_id"])
        ix["acc_of"][a["customer_id"]].append(a["account_id"])
        if a["login_handle"]:
            ix["login"][fold(a["login_handle"])].append(a["customer_id"])
        phones[a["customer_id"]].add(norm_phone(a["phone"]))
    ix["email"] = defaultdict(list)
    for rid, c in sorted(ix["cust"].items()):
        phones[rid].add(norm_phone(c["phone"]))
        if c["email"]:
            ix["email"][fold(c["email"])].append(rid)
        if c.get("login_name"):
            ix["login"][fold(c["login_name"])].append(rid)
    app_cust = {o["kyc_applicant_id"]: o["customer_id"] for o in data["onboarding"]}
    ix["checks"] = defaultdict(list)
    for k in sorted(data["kyc_checks"], key=lambda k: k["check_id"]):
        rid = app_cust.get(k["applicant_id"])
        if rid:
            ix["checks"][rid].append(k)
    ix["notes"] = defaultdict(list)
    ix["note_facts"], stats = {}, defaultdict(int)
    for n in sorted(data["notes"], key=lambda n: n["note_id"]):
        f = parse_note(n)
        stats["notes_" + n["channel"]] += 1
        if f:
            stats["notes_parsed_" + f["kind"]] += 1
            if f["kind"] == "kyc" and (not f["dob"] or not f["name"]):
                stats["kyc_notes_missing_field"] += 1
            ix["note_facts"][n["note_id"]] = f
            ix["notes"][fold(n["requester_email"])].append(n)
    ix["search"] = defaultdict(list)
    for r in sorted(data["search_results"], key=lambda r: r["result_id"]):
        f = parse_snippet(r)
        stats["snippets"] += 1
        stats["snippets_parsed"] += f is not None
        if f:
            ix["search"][tuple(sorted(norm_name(r["query"])))].append((r["result_id"], f))
    ix["stats"] = stats

    # union-find over customer records: same phone + same full DOB, or same document number on approved checks
    uf = UnionFind(sorted(ix["cust"]))
    first_of = {}
    for rid in sorted(ix["cust"]):
        d = ix["dob"][rid]
        if d and d.full:
            for p in sorted(x for x in phones[rid] if x):
                first_of.setdefault((p, d), rid)
                uf.union(first_of[(p, d)], rid)
    doc_first = {}
    for rid in sorted(ix["checks"]):
        for k in ix["checks"][rid]:
            doc = norm_doc(k["document_number"])
            if k["result"] == "approved" and doc:
                doc_first.setdefault(doc, rid)
                uf.union(doc_first[doc], rid)
    ix["person"] = {rid: uf.find(rid) for rid in ix["cust"]}
    ix["members"] = defaultdict(list)
    for rid in sorted(ix["cust"]):
        ix["members"][ix["person"][rid]].append(rid)
    ix["name_parts"] = {rid: [norm_name(c[k], c["entity_type"] == "Company") for k in ("full_name", "preferred_name", "login_name") if c.get(k)]
                        for rid, c in ix["cust"].items()}
    return ix

# ---------------------------------------------------------------- 5. Compare (names)


@lru_cache(maxsize=None)
def part_match(a, b):
    if a == b:
        return True
    if a in VARIANT and VARIANT.get(b) == VARIANT[a]:
        return True
    n = min(len(a), len(b))
    if n < 4:
        return False
    limit = 1 if n <= 6 else 2
    if abs(len(a) - len(b)) > limit:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] <= limit


def _join(xs, ys):
    """Merge adjacent parts of xs written apart that match one part of ys written together (Abdul Salam ~ Abdulsalam)."""
    out, i = [], 0
    while i < len(xs):
        if i + 1 < len(xs) and any(part_match(xs[i] + xs[i + 1], y) for y in ys) and not any(part_match(xs[i], y) for y in ys):
            out.append(xs[i] + xs[i + 1])
            i += 2
        else:
            out.append(xs[i])
            i += 1
    return out


@lru_cache(maxsize=None)
def _agree(a, b):
    a, b = list(a), list(b)
    if not a or not b:
        return False
    a2, b2 = _join(a, b), _join(b, a)
    a, b = (a2, b) if len(a2) < len(a) else (a, b2)
    match = {}                                   # bipartite matching: each part counts once

    def aug(i, seen):
        for j, y in enumerate(b):
            if j not in seen and part_match(a[i], y):
                seen.add(j)
                if j not in match or aug(match[j], seen):
                    match[j] = i
                    return True
        return False
    n = sum(aug(i, set()) for i in range(len(a)))
    return n >= 2 or n == min(len(a), len(b))


def names_agree(a_parts, b_parts):
    return _agree(tuple(a_parts), tuple(b_parts))


def real_name(parts):
    return len(parts) >= 2

# ---------------------------------------------------------------- 5. Profiles


def date_compatible(a, b):
    """Every part both give is equal (used for the screened-details search)."""
    if not a or not b:
        return True
    return a.year == b.year and (not a.month or not b.month or a.month == b.month) and (not a.day or not b.day or a.day == b.day)


def fits(rid, scr, ix):
    """Does customer record rid fit the screened details?"""
    c = ix["cust"][rid]
    company = scr["entity_type"] == "Company"
    sp = norm_name(scr["name"], company)
    if not sp or not any(names_agree(sp, p) for p in ix["name_parts"][rid]):
        return False
    if not date_compatible(norm_date(scr.get("dob")), ix["dob"][rid]):
        return False
    sc = norm_country(scr.get("country"))
    if sc and sc != UNKNOWN:
        mine = {norm_country(c["residence_country"]), norm_country(c["nationality"])} - {None}
        if mine and UNKNOWN not in mine and sc not in mine:
            return False
    city = fold(scr.get("city"))
    if city and (c["city"] or c["address"]) and city not in fold(c["city"]) and city not in fold(c["address"]):
        return False
    return True


def resolve(alert, ix):
    """-> (sorted person_ids, preferred record per person, path note)."""
    scr = alert["customer"]
    linked = []
    if alert.get("account_ref"):
        linked = ix["accounts"].get(alert["account_ref"], [])
    elif alert.get("login_handle"):
        linked = ix["login"].get(fold(alert["login_handle"]), [])
    elif alert.get("email"):
        linked = ix["email"].get(fold(alert["email"]), [])
    has_link = bool(alert.get("account_ref") or alert.get("login_handle") or alert.get("email"))
    if has_link and linked:
        candidates = sorted(set(linked))
        fitting = [r for r in candidates if fits(r, scr, ix)]
        persons = {ix["person"][r] for r in candidates}
        if len(persons) > 1 and fitting:
            candidates = fitting
    else:
        sdob, cands = norm_date(scr.get("dob")), []
        for rid in sorted(ix["cust"]):
            if ix["cust"][rid]["entity_type"] != scr["entity_type"]:
                continue
            if sdob and ix["dob"][rid] and not date_compatible(sdob, ix["dob"][rid]):
                continue
            if fits(rid, scr, ix):
                cands.append(rid)
        candidates = cands
    chosen = {}
    for r in candidates:
        chosen.setdefault(ix["person"][r], r)
    return sorted(chosen), chosen


@dataclass
class Profile:
    kind: str                                   # Individual / Company
    records: list = field(default_factory=list)
    names: list = field(default_factory=list)   # [(display, parts, source)]
    verified_name: tuple = None                 # (display, parts, source)
    dobs: list = field(default_factory=list)    # customer: [(Date, [sources])]; hit: [(years, month, day, src, desc)]
    dob_verified: bool = False
    countries: list = field(default_factory=list)   # [(code, source)]
    unknown_country: bool = False
    reports: list = field(default_factory=list)     # hit: dated report Dates
    death: object = None
    screened_at: object = None
    hit_id: str = None


def customer_profile(pid, ix, screened_at):
    recs = ix["members"][pid]
    kind = ix["cust"][recs[0]]["entity_type"]
    company = kind == "Company"
    p = Profile(kind=kind, records=recs, screened_at=screened_at)
    checks = []    # (date, id, name, dob, country) of approved identity checks
    for rid in recs:
        c = ix["cust"][rid]
        for key in ("full_name", "preferred_name"):
            if c.get(key):
                p.names.append((c[key], norm_name(c[key], company), rid))
        for key in ("residence_country", "nationality"):
            code = norm_country(c[key])
            if code == UNKNOWN:
                p.unknown_country = True
            elif code:
                p.countries.append((code, rid))
        for k in ix["checks"][rid]:
            if k["result"] == "approved":
                checks.append((norm_date(k["checked_at"]), k["check_id"], k["verified_name"], norm_date(k["verified_dob"]), norm_country(k["document_country"])))
    emails = sorted({fold(ix["cust"][r]["email"]) for r in recs if ix["cust"][r]["email"]})
    for e in emails:
        for n in ix["notes"][e]:
            f = ix["note_facts"][n["note_id"]]
            if f["kind"] == "kyc":
                cc = f["country"] if isinstance(f["country"], str) else None
                if isinstance(f["country"], list):
                    p.countries.extend((x, n["note_id"]) for x in f["country"])
                checks.append((f["date"], n["note_id"], f["name"], f["dob"], cc))
            else:
                p.countries.extend((x, n["note_id"]) for x in f["countries"])
    checks.sort(key=lambda t: (t[0].earliest() if t[0] else (0, 0, 0), t[1]), reverse=True)   # most recent first
    vname = next(((nm, src) for _, src, nm, _, _ in checks if nm), None)
    if vname:
        p.verified_name = (vname[0], norm_name(vname[0], company), vname[1])
    vdob = next(((d, src) for _, src, _, d, _ in checks if d), None)
    vcountry = next(((c, src) for _, src, _, _, c in checks if c), None)
    if vcountry:
        if vcountry[0] == UNKNOWN:
            p.unknown_country = True
        else:
            p.countries.append(vcountry)
    if vdob:
        p.dobs, p.dob_verified = [(vdob[0], [vdob[1]])], True
    else:
        by = defaultdict(list)
        for rid in recs:
            if ix["dob"][rid]:
                by[ix["dob"][rid]].append(rid)
        ds = sorted(by)
        if ds and all(date_compatible(a, b) for a in ds for b in ds):     # consistent: keep the most specific
            best = max(ds, key=lambda d: (d.full, d.month is not None))
            p.dobs = [(best, sorted(r for d in ds for r in by[d]))]
        else:
            p.dobs = [(d, by[d]) for d in ds]                              # conflicting: never clear or confirm on DOB
    p.countries = sorted(set(p.countries))
    return p


def hit_profile(hit, ix):
    company = hit["entity_type"] == "Company"
    p = Profile(kind=hit["entity_type"], hit_id=hit["hit_id"])
    for key in ("name", "best_matching_name"):
        if hit.get(key):
            p.names.append((hit[key], norm_name(hit[key], company), "name"))
    parts = " ".join(x for x in (hit.get("first_name"), hit.get("middle_name"), hit.get("last_name")) if x)
    if parts:
        p.names.append((parts, norm_name(parts, company), "name"))
    for a in hit.get("aliases") or []:
        p.names.append((a, norm_name(a, company), "aliases"))
    p.names = [n for n in p.names if n[1]]
    for key in ("countries", "nationality", "citizenship", "addresses"):
        for v in hit.get(key) or []:
            code = norm_country(v)
            if code == UNKNOWN:
                p.unknown_country = True
            elif code:
                p.countries.append((code, key))
    if hit.get("place_of_birth"):
        code = norm_country(hit["place_of_birth"])
        p.unknown_country |= code == UNKNOWN
        if code and code != UNKNOWN:
            p.countries.append((code, "place_of_birth"))
    p.countries = sorted(set(p.countries))
    for v in hit.get("dobs") or []:
        d = norm_date(v)
        if d:
            p.dobs.append(((d.year,), d.month, d.day, f"{hit['hit_id']}#dobs", "listed date of birth"))
    if hit.get("deceased_date"):
        p.death = norm_date(hit["deceased_date"])
    if company:
        return p
    all_parts = sorted({x for n in p.names for x in n[1]})
    facts = parse_hit_notes(hit.get("profile_notes"), all_parts)
    p.reports = sorted(facts["reports"])
    p.death = p.death or facts["death"]
    p.dobs += [(ys, m, d, f"{hit['hit_id']}#profile_notes", "profile notes") for ys, m, d, _ in facts["dobs"]]
    if not p.dobs:
        cat = fold(hit.get("primary_category") or (hit.get("categories") or [""])[0])
        phrase = LISTING.get(cat, "named on a watchlist")
        tied = {c for c, _ in p.countries}
        keys = sorted({tuple(sorted(n[1])) for n in p.names})
        for key in keys:
            for rid, f in ix["search"].get(key, []):
                if phrase in f["listing"] and tied & set(f["countries"]):
                    p.dobs.append((f["years"], None, None, rid, "search results"))
        p.dobs = sorted(set(p.dobs))
    return p

# ---------------------------------------------------------------- 6. Decide


def dob_agrees(c, h):
    years, m, d = h[0], h[1], h[2]
    return c.year in years and (not m or not c.month or m == c.month) and (not d or not c.day or d == c.day) and (len(years) == 1 or not (m or d))


def dob_contradicts(c, h):
    years, m, d = h[0], h[1], h[2]
    if min(abs(c.year - y) for y in years) > 1:
        return True
    if len(years) == 1 and c.year == years[0] and m and c.month:
        return m != c.month or bool(d and c.day and d != c.day)
    return False


@dataclass
class Decision:
    decision: str
    codes: list
    evidence: list
    sentences: list
    trace: list = field(default_factory=list)


def _fmt_years(h):
    if len(h[0]) == 2:
        return f"{h[0][0]} or {h[0][1]}"
    return str(Date(h[0][0], h[1], h[2]))


def _clist(codes):
    return ", ".join(sorted(COUNTRY_NAME.get(c, c) for c in codes))


def _hit_dob_desc(hdobs):
    """e.g. "hit's listed date of birth is 1962-09-10" / "hit's profile notes put its birth year at 1976 or 1977"."""
    parts = []
    for kind in ("listed date of birth", "profile notes", "search results"):
        vals = sorted({_fmt_years(h) for h in hdobs if h[4] == kind})
        if not vals:
            continue
        if kind == "listed date of birth":
            parts.append(("listed date of birth is " if len(vals) == 1 else "listed dates of birth are ") + ", ".join(vals))
        else:
            parts.append(f"{kind} put its birth year at {', '.join(vals)}")
    return "hit's " + " and its ".join(parts)


def decide(cp, hp, alert):
    """Ordered rules (POLICY 6, 7). All rules run; the trace keeps every outcome."""
    company = cp.kind == "Company"
    trace, fired, ev, sent = [], [], set(), []
    hit_names = [(n[0].strip().rstrip("."), n[1], n[2]) for n in hp.names if n[1]]
    best_hit_name = hit_names[0][0] if hit_names else ""

    def agrees_with_hit(parts):
        return next((n[0] for n in hit_names if names_agree(parts, n[1])), None)

    # names: verified legal name outranks record names
    if cp.verified_name and (company or real_name(cp.verified_name[1])):
        name_hit = agrees_with_hit(cp.verified_name[1])
        name_agree, name_known = name_hit is not None, True
    else:
        usable = [n for n in cp.names if company or real_name(n[1])]
        name_hit = next((h for n in usable for h in [agrees_with_hit(n[1])] if h), None)
        name_agree, name_known = name_hit is not None, False
    # dates of birth
    single_dob = cp.dobs[0] if len(cp.dobs) == 1 else None
    hdobs = hp.dobs
    dob_agree = bool(single_dob and hdobs and any(dob_agrees(single_dob[0], h) for h in hdobs))
    dob_any_agree = any(dob_agrees(c[0], h) for c in cp.dobs for h in hdobs)
    dob_contra = bool(single_dob and hdobs and all(dob_contradicts(single_dob[0], h) for h in hdobs))
    ccodes, hcodes = {c for c, _ in cp.countries}, {c for c, _ in hp.countries}
    shared = sorted(ccodes & hcodes)
    screened = norm_date(alert.get("screened_at"))

    def chk(rule, principle, status, detail):          # structured trace: one entry per rule, in evaluation order
        trace.append({"rule": rule, "policy": principle, "status": status, "detail": detail})

    # contradictions, in output order
    chk("ENTITY_TYPE_MISMATCH", "6 Type", "fired" if cp.kind != hp.kind else "passed",
        f"customer is {cp.kind.lower()}, hit is {hp.kind.lower()}")
    if cp.kind != hp.kind:
        fired.append("ENTITY_TYPE_MISMATCH")
        sent.append(f"The customer is {'a company' if company else 'an individual'} but the hit is {'a company' if hp.kind == 'Company' else 'an individual'}.")
        ev.update(cp.records[:1])
    chk("DECEASED", "6 Time", "n/a" if not hp.death else ("fired" if screened and hp.death.latest() < screened.earliest() else "passed"),
        f"date of death {hp.death}, screened {screened}" if hp.death else "the hit gives no date of death")
    if hp.death and screened and hp.death.latest() < screened.earliest():
        fired.append("DECEASED")
        sent.append(f"The hit's profile gives a date of death of {hp.death}, before the customer was screened on {screened}.")
        ev.add(f"{hp.hit_id}#deceased_date")
    if not hp.reports:
        chk("TEMPORAL_IMPOSSIBILITY", "6 Time", "n/a", "the hit's profile has no dated report ('According to …; Month D, YYYY:')")
    elif not single_dob:
        chk("TEMPORAL_IMPOSSIBILITY", "6 Time", "n/a", f"earliest report {hp.reports[0]}, but the customer has no single date of birth")
    if hp.reports and single_dob and cp.kind == hp.kind:
        first, born = hp.reports[0], single_dob[0]
        e = born.earliest()
        max_age = first.year - e[0] - ((first.month, first.day) < (e[1], e[2]))
        if company and born.earliest() > first.latest():
            fired.append("TEMPORAL_IMPOSSIBILITY")
            sent.append(f"The customer was incorporated on {born} ({', '.join(single_dob[1])}), after the hit's profile reports activity on {first}.")
            ev.update(single_dob[1])
        elif not company and max_age < 16:
            fired.append("TEMPORAL_IMPOSSIBILITY")
            chk("TEMPORAL_IMPOSSIBILITY", "6 Time", "fired", f"at most {max(max_age, 0)} years old at the earliest report {first} (under 16)")
            sent.append(f"The customer was born in {born.year} ({', '.join(single_dob[1])}), so they were at most {max(max_age, 0)} when the hit's profile reports activity on {first}.")
            ev.update(single_dob[1])
        if "TEMPORAL_IMPOSSIBILITY" not in fired:
            chk("TEMPORAL_IMPOSSIBILITY", "6 Time", "passed", f"customer could be {max_age} at the earliest report {first}" if not company else f"incorporated {born}, before the earliest report {first}")
    if company and not hp.dobs:
        chk("DOB_MISMATCH", "6 Date of birth", "n/a", "the hit gives no incorporation date")
    elif not hdobs:
        chk("DOB_MISMATCH", "6 Date of birth", "n/a", "the hit gives no date of birth (field, profile notes or a qualifying search result)")
    elif not cp.dobs:
        chk("DOB_MISMATCH", "6 Date of birth", "n/a", "the customer has no date of birth on file")
    elif not single_dob:
        chk("DOB_MISMATCH", "6 Date of birth", "n/a", "the customer's unverified records give conflicting dates of birth")
    elif dob_contra:
        chk("DOB_MISMATCH", "6 Date of birth", "fired", f"{single_dob[0]} contradicts every hit date ({', '.join(_fmt_years(h) for h in hdobs)})")
    else:
        chk("DOB_MISMATCH", "6 Date of birth", "passed", f"{single_dob[0]} is not contradicted by " + ("an agreeing hit date" if dob_agree else "every hit date (within a year, or parts unknown)"))
    if dob_contra and cp.kind == hp.kind:
        fired.append("DOB_MISMATCH")
        what = "incorporation date" if company else "date of birth"
        sent.append(f"The customer's {what} is {single_dob[0]} ({', '.join(single_dob[1])}) but the {_hit_dob_desc(hdobs)}.")
        ev.update(single_dob[1])
        ev.update(h[3] for h in hdobs if h[3].startswith("SR-"))
    country_conflict = bool(ccodes and hcodes and not shared and not cp.unknown_country and not hp.unknown_country)
    if country_conflict:
        if name_agree and dob_any_agree:
            chk("LOCATION_MISMATCH", "6 Location", "blocked", f"{_clist(ccodes)} vs {_clist(hcodes)} differ, but name and date of birth agree, so a country difference cannot clear")
        else:
            chk("LOCATION_MISMATCH", "6 Location", "fired", f"customer only {_clist(ccodes)}; hit only {_clist(hcodes)}")
            fired.append("LOCATION_MISMATCH")
            sent.append(f"The customer is linked only to {_clist(ccodes)} but the hit is linked only to {_clist(hcodes)}.")
            ev.update(s for _, s in cp.countries)
    if not country_conflict:
        chk("LOCATION_MISMATCH", "6 Location", "passed" if shared else "n/a",
            f"both linked to {_clist(shared)}" if shared else ("a country could not be normalized" if cp.unknown_country or hp.unknown_country
                                                              else ("the customer has no country" if not ccodes else "the hit has no country")))
    if not name_known:
        chk("NAME_MISMATCH_VERIFIED", "6 Name / 8", "n/a", "no verified legal name with two real parts" if not cp.verified_name else f"verified name {cp.verified_name[0]} has fewer than two real parts")
    else:
        chk("NAME_MISMATCH_VERIFIED", "6 Name / 8", "passed" if name_agree else "fired",
            f"verified {cp.verified_name[0]} " + (f"agrees with {name_hit}" if name_agree else f"agrees with none of the hit's {len(hit_names)} names"))
    if name_known and not name_agree and hit_names and cp.kind == hp.kind:
        fired.append("NAME_MISMATCH_VERIFIED")
        sent.append(f"The customer's verified name is {cp.verified_name[0]} ({cp.verified_name[2]}), which does not match the hit's name {best_hit_name}.")
        ev.add(cp.verified_name[2])
    chk("AGREEMENT", "7", "info", f"name {'agrees' if name_agree else 'does not agree'}; date of birth {'agrees' if dob_agree else 'does not agree'}; "
        f"shared country: {_clist(shared) if shared else 'none'}")
    if fired:
        return Decision("DIFFERENT_ENTITY", fired, sorted(ev), sent[:4], trace)

    # a country difference never clears a hit whose name and DOB agree, and never lets it be confirmed either
    if country_conflict:
        dob_src = sorted({r for _, rs in cp.dobs for r in rs})
        s = [f"The customer's name and date of birth agree with the hit ({name_hit}, {', '.join(str(d) for d, _ in cp.dobs)}).",
             f"But the customer is linked only to {_clist(ccodes)} and the hit only to {_clist(hcodes)}.",
             "A country difference cannot clear a hit whose name and date of birth agree, so it is left for review."]
        return Decision("REVIEW", ["INSUFFICIENT_DATA"], sorted(set(dob_src) | {x for _, x in cp.countries}), s, trace)

    # agreement
    loc_ev = sorted({s for c, s in cp.countries if c in shared})
    name_s = f"The customer's name agrees with the hit's name {name_hit}." if name_agree else None
    loc_s = f"Both the customer and the hit are linked to {_clist(shared)}." if shared else None
    if not company and name_agree and dob_agree:
        h = next(h for h in hdobs if dob_agrees(single_dob[0], h))
        ev = set(loc_ev) | set(single_dob[1]) | {x[3] for x in hdobs if x[3].startswith("SR-") and dob_agrees(single_dob[0], x)}
        s = [f"The customer's date of birth {single_dob[0]} ({', '.join(single_dob[1])}) agrees with the hit's {_fmt_years(h)}.", loc_s, name_s]
        return Decision("SAME_ENTITY", ["NAME_DOB_LOCATION_MATCH" if shared else "NAME_DOB_MATCH", "NAME_DOB_MATCH"], sorted(ev), [x for x in s if x], trace)
    if company and name_agree and shared:
        return Decision("SAME_ENTITY", ["NAME_LOCATION_MATCH"], sorted(set(loc_ev) | set(cp.records[:1])), [loc_s, name_s], trace)

    # review
    s = [loc_s, name_s]
    if not name_agree:
        s.append("The customer's name cannot be confirmed against the hit's names." if not cp.verified_name else
                 f"The customer's verified name {cp.verified_name[0]} ({cp.verified_name[2]}) is too incomplete to compare with the hit's name {best_hit_name}.")
    if company:
        why = "the countries do not overlap" if name_agree else "the name does not clearly agree"
    elif not hdobs or not cp.dobs:
        why = "the date of birth cannot be compared with it"
    elif len(cp.dobs) > 1:
        why = "the customer's records give conflicting unverified dates of birth (" + ", ".join(f"{d} ({', '.join(r)})" for d, r in cp.dobs) + ")"
    elif not dob_agree:
        why = f"the customer's date of birth {single_dob[0]} neither agrees with nor contradicts the {_hit_dob_desc(hdobs)}"
    else:
        why = "the name does not clearly agree"
    s.append(f"Nothing in the customer's records contradicts the hit, and {why}, so the hit can be neither cleared nor confirmed.")
    ev = set(loc_ev)
    if cp.dobs and hdobs and not dob_agree:
        ev.update(r for _, rs in cp.dobs for r in rs)
    return Decision("REVIEW", ["INSUFFICIENT_DATA"], sorted(ev), [x for x in s if x][:4], trace)


# ---------------------------------------------------------------- 7. Output

def decide_hit(hit, alert, ix, hp):
    pids, chosen = resolve(alert, ix)
    if not pids:
        return {"decision": "REVIEW", "reason_code": "INSUFFICIENT_DATA", "customer_record_id": None, "evidence": [],
                "reason": f"No customer record in the bank's systems fits the screened customer {alert['customer']['name']}. "
                          "Without the customer's records nothing can be compared with the hit, so it is left for review."}
    results = [(pid, decide(customer_profile(pid, ix, alert.get("screened_at")), hp, alert)) for pid in pids]
    if len(results) == 1:
        pid, d = results[0]
        return {"decision": d.decision, "reason_code": d.codes[0], "customer_record_id": chosen[pid], "evidence": d.evidence, "reason": " ".join(d.sentences)}
    decisions = sorted({d.decision for _, d in results})
    records = sorted(chosen[p] for p in pids)
    if len(decisions) == 1:
        common = [c for c in results[0][1].codes if all(c in d.codes for _, d in results)]
        code = common[0] if common else results[0][1].codes[0]
        ev = sorted({e for _, d in results for e in d.evidence} | set(records))
        lead = f"{len(pids)} different people in the system of record fit the alert (records {', '.join(records)}), and each leads to the same decision."
        return {"decision": decisions[0], "reason_code": code, "customer_record_id": "AMBIGUOUS", "evidence": ev,
                "reason": " ".join([lead] + results[0][1].sentences[:3])}
    outcome = "; ".join(f"{chosen[p]}: {d.decision}" for p, d in results)
    return {"decision": "REVIEW", "reason_code": "AMBIGUOUS_CUSTOMER_RECORD", "customer_record_id": "AMBIGUOUS", "evidence": records,
            "reason": f"{len(pids)} different people in the system of record fit the screened customer {alert['customer']['name']} (records {', '.join(records)}). "
                      f"They lead to different decisions ({outcome}), so a person must decide which customer this is."}


def run(data_dir, out_file):
    data = load(data_dir)
    ix = build_index(data)
    alerts = {a["alert_id"]: a for a in data["alerts"]}
    rows = []
    for hit in sorted(data["hits"], key=lambda h: h["hit_id"]):
        alert = alerts[hit["alert_id"]]
        hp = hit_profile(hit, ix)
        r = decide_hit(hit, alert, ix, hp)
        rows.append({"hit_id": hit["hit_id"], "alert_id": hit["alert_id"], "decision": r["decision"], "reason_code": r["reason_code"],
                     "customer_record_id": r["customer_record_id"], "reason": r["reason"], "evidence": r["evidence"]})
    os.makedirs(os.path.dirname(os.path.abspath(out_file)), exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=False) + "\n")
    s = ix["stats"]
    print(f"{len(rows)} hits written to {out_file}; snippets parsed {s['snippets_parsed']}/{s['snippets']}; "
          f"kyc notes parsed {s['notes_parsed_kyc']}/{s['notes_kyc']} ({s['kyc_notes_missing_field']} missing name or DOB)", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: system/run <data_dir> <output_file>")
    run(sys.argv[1], sys.argv[2])

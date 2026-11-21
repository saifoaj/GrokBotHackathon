"""Put the values the rules cannot read into the standard form, before the rules run.

Only a value the rules cannot read is sent to Grok; a value they can read is never touched.
The agreed answer (5 of 5 votes, see llm.py) is written back into the loaded record in the
form the rules already use, so the deterministic code decides from clean data:

  dates        any date field          -> ISO "YYYY", "YYYY-MM" or "YYYY-MM-DD"
  places       country fields, the last part of an address or place of birth (a city,
               state or region)         -> the canonical country name
  entity type  blank or unusual values  -> "Individual" or "Company"

Without a key, or when the votes split, the value stays as it was and the rules treat it as
they always have (an unreadable date is unknown; an unknown place blocks a location clear).
The original value is kept in the record under "_raw_<field>".
"""

import hashlib
import re

import countries
import llm
from norm import fmt_date, parse_date

DATE_PROMPT = """You put dates into ISO form. Each item has an id, the field it came from and the value as written.
Return the date the value states, as "YYYY-MM-DD", or "YYYY-MM" when it gives no day, or "YYYY" when it gives only a year.
Numeric dates with slashes or dots are day first (05/03/1979 is 1979-03-05) unless the first number is above 12 for the month position only.
A two-digit year means 19xx when above 26, otherwise 20xx.
If the value names several possible years, or is not a date, or you are not certain, return null.
Reply with JSON only: {"items": [{"id": "...", "date": "YYYY-MM-DD" or "YYYY-MM" or "YYYY" or null}]}"""

PLACE_PROMPT = """You say which country a place is in. Each item has an id, the field it came from, and the place as written (a country, a code, a nationality word, a city, a state, a province or a region, possibly in another language).
Return the English short name of the one sovereign country or territory it is in (for example "United States", "United Kingdom", "Hong Kong").
Return null when it could be in more than one country, is a continent or a region spanning countries (Europe, Middle East, International), is not a place, or you are not certain.
Reply with JSON only: {"items": [{"id": "...", "country": "..." or null}]}"""

TYPE_PROMPT = """You say whether a screened party is a person or a company. Each item has an id, the entity type as written (may be blank), the name, and sometimes a short profile.
Return "Individual" for a natural person, "Company" for a company, firm, organisation, vessel, government body or any other non-person.
Return null if you are not certain.
Reply with JSON only: {"items": [{"id": "...", "type": "Individual" or "Company" or null}]}"""

PERSON = {"individual", "person", "natural person", "people"}
COMPANY = {"company", "organisation", "organization", "entity", "corporation", "corporate", "business",
           "legal entity", "firm", "vessel", "government", "state-owned enterprise"}


def _id(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]


def _find(reply, item_id, field):
    for row in (reply or {}).get("items") or []:
        if isinstance(row, dict) and str(row.get("id")) == item_id:
            return row.get(field)
    return None


def _read_date(reply, item_id):
    value = _find(reply, item_id, "date")
    if not value or not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", str(value)):
        return None
    parsed = parse_date(str(value))
    return fmt_date(parsed) if parsed else None


def _read_place(reply, item_id):
    value = _find(reply, item_id, "country")
    if not value:
        return None
    name = countries.canon(str(value))
    return name if name and countries.known(name) else None


def _read_type(reply, item_id):
    value = str(_find(reply, item_id, "type") or "")
    return value if value in ("Individual", "Company") else None


# ----------------------------------------------------------------- scanning

def _date_unread(value, years_ok=False):
    if value in (None, "") or isinstance(value, (list, dict)):
        return False
    if parse_date(value):
        return False
    # A watchlist birth date such as "c. 1960", "1958/1959" or "1960 (approx.)": the rules already
    # take every year it names as a possible birth year, so it is left to them.
    return not (years_ok and re.search(r"(?<!\d)(1[89]\d\d|20\d\d)(?!\d)", str(value)))


def _place_unread(value):
    if not value or not isinstance(value, str):
        return False
    name = countries.canon(value)
    return bool(name) and not countries.known(name)


def _last_part(value):
    text = re.sub(r"\[[^\]]*\]", "", str(value)).strip().rstrip(",")
    return text.split(",")[-1].strip()


def _type_unread(value):
    return str(value or "").strip().lower() not in PERSON | COMPANY


def scan(data):
    """Every value the rules cannot read: {kind: [(item_id, item, setter)]}."""
    found = {"date": [], "place": [], "type": []}

    def date(record, field, label, index=None):
        value = record.get(field) if index is None else record[field][index]
        if _date_unread(value, years_ok=(field == "dobs")):
            found["date"].append((_id("date", label, value), {"field": label, "value": str(value)},
                                  (record, field, index)))

    def place(record, field, label, index=None, whole=False):
        value = record.get(field) if index is None else record[field][index]
        if not isinstance(value, str):
            return
        probe = _last_part(value) if whole else value
        if _place_unread(probe):
            shown = re.sub(r"\s*\[[^\]]*\]", "", value).strip()  # "Ontario [PEP]" is asked as "Ontario"
            found["place"].append((_id("place", label, shown), {"field": label, "value": shown},
                                   (record, field, index, whole)))

    for c in data.customers:
        date(c, "dob", "customer date of birth")
        place(c, "residence_country", "customer residence country")
        place(c, "nationality", "customer nationality")
        if _type_unread(c.get("entity_type")):
            found["type"].append((_id("type", c.get("entity_type"), c.get("full_name")),
                                  {"entity_type": c.get("entity_type") or "", "name": c.get("full_name") or ""},
                                  (c, "entity_type", None)))
    for k in data.kyc:
        date(k, "verified_dob", "identity check date of birth")
        date(k, "checked_at", "identity check date")
        place(k, "document_country", "identity document issuing country")
    for a in data.alerts:
        date(a, "screened_at", "screening date")
        screened = a.get("customer")
        if isinstance(screened, dict):
            date(screened, "dob", "screened date of birth")
            place(screened, "country", "screened country")
            if screened.get("entity_type") is not None and _type_unread(screened.get("entity_type")):
                found["type"].append((_id("type", screened.get("entity_type"), screened.get("name")),
                                      {"entity_type": screened.get("entity_type") or "",
                                       "name": screened.get("name") or ""}, (screened, "entity_type", None)))
    for h in data.hits:
        if isinstance(h.get("dobs"), list):
            for i in range(len(h["dobs"])):
                date(h, "dobs", "watchlist date of birth", i)
        date(h, "deceased_date", "watchlist date of death")
        for field in ("countries", "nationality", "citizenship"):
            if isinstance(h.get(field), list):
                for i in range(len(h[field])):
                    place(h, field, "watchlist " + field, i)
            else:
                place(h, field, "watchlist " + field)
        for field in ("addresses", "place_of_birth"):
            if isinstance(h.get(field), list):
                for i in range(len(h[field])):
                    place(h, field, "watchlist " + field, i, whole=True)
            else:
                place(h, field, "watchlist " + field, whole=True)
        if _type_unread(h.get("entity_type")):
            found["type"].append((_id("type", h.get("entity_type"), h.get("name")),
                                  {"entity_type": h.get("entity_type") or "", "name": h.get("name") or "",
                                   "profile": (h.get("profile_notes") or "")[:400]},
                                  (h, "entity_type", None)))
    return found


def _set(target, value, whole=False):
    record, field, index = target[:3]
    old = record[field] if index is None else record[field][index]
    if whole:  # keep the address as written and add the country as its last part
        tag = re.findall(r"\s*\[[^\]]*\]\s*$", old)
        body = old[:len(old) - len(tag[0])] if tag else old
        value = "%s, %s%s" % (body.strip().rstrip(","), value, tag[0] if tag else "")
    record.setdefault("_raw_" + field, record[field] if index is None else list(record[field]))
    if index is None:
        record[field] = value
    else:
        record[field][index] = value


def apply(data):
    """Normalise data in place. Returns {kind: (unread, fixed)} for the run log."""
    found = scan(data)
    report = {}
    specs = {"date": ("dates", DATE_PROMPT, _read_date), "place": ("places", PLACE_PROMPT, _read_place),
             "type": ("entity_types", TYPE_PROMPT, _read_type)}
    for kind, entries in found.items():
        if not entries:
            report[kind] = (0, 0)
            continue
        name, prompt, read = specs[kind]
        unique = {}
        for item_id, item, _ in entries:
            unique.setdefault(item_id, dict(item, id=item_id))
        answers = llm.vote_batch(llm.Store(name), prompt, sorted(unique.items()), read, label=name)
        fixed = 0
        for item_id, _item, target in entries:
            value = answers.get(item_id)
            if value:
                _set(target, value, whole=(kind == "place" and len(target) > 3 and target[3]))
                fixed += 1
        report[kind] = (len(entries), fixed)
    return report

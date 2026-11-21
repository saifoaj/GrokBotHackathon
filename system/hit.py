"""The listed person's facts (POLICY principle 5), each with its source.

- Names: name ("Last, First" also read as "First Last"), the first / middle /
  last name fields, best_matching_name, and aliases (nicknames included).
- Dates of birth, first source that gives any:
    1. the dobs field (lists, partial dates);
    2. the first paragraph of the profile notes: a birth date attached to a
       birth keyword, or an age at a date (aged 45 in March 2019 -> 1973 or 1974);
    3. web search results for the hit's name that are about this hit: the
       snippet uses the listing phrase of the hit's primary (or first)
       category AND names a country tied to the hit.
  For companies only the dobs field is an incorporation date.
- Countries tied to the hit: countries, nationality, citizenship, the last
  part of each address, place_of_birth. "International" and "Europe" are not
  countries. Values we cannot recognise are kept as unrecognised.
- Dated reports: exactly the lines "According to ...; Month d, yyyy: ...".
- Date of death: the deceased_date field; for a profile whose life status is
  Deceased without that field, a date attached to "died" / "date of death".
"""

from collections import namedtuple

import countries
import dates
import extract
from names import reorder_last_first, simple_text

# years: possible birth years; month/day may be None.
# how: dobs_field | profile_dob | profile_age | search_result
HitDob = namedtuple("HitDob", "years month day source how text")
HitName = namedtuple("HitName", "raw parts kind")

LISTING_PHRASES = {
    "sanctions": "a sanctioned individual",
    "pep": "a politically exposed person",
    "enforcement": "the subject of enforcement action",
    "adverse media": "named in adverse media",
    "soe": "a state-owned enterprise official",
}
OTHER_PHRASE = "named on a watchlist"
COUNTRY_FIELDS = ("countries", "nationality", "citizenship", "addresses", "place_of_birth")


class HitFacts(object):
    def __init__(self, hit):
        self.hit_id = hit["hit_id"]
        self.alert_id = hit.get("alert_id")
        self.entity_type = hit.get("entity_type") if isinstance(hit.get("entity_type"), str) else None
        self.is_company = None
        self.display_name = hit.get("name") if isinstance(hit.get("name"), str) else self.hit_id
        self.names = []
        self.dobs = []
        self.dob_origin = None
        self.countries = {}            # canonical country -> sorted list of fields
        self.unrecognised = []         # (field, raw value)
        self.reports = []              # (date, line number)
        self.earliest_report = None
        self.death = None              # (date, how)
        self.category = None
        self.listing_phrase = None
        self.search_seen = []          # SR ids returned for the hit's name
        self.search_used = []          # SR ids that are about this hit
        self.coverage = {}             # extraction notes for the coverage report

    def country_list(self):
        return sorted(self.countries)


def _is_company(entity_type):
    return simple_text(entity_type or "") in ("company", "organisation", "organization", "entity", "vessel")


def _texts(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def dob_text(years, month, day):
    if len(years) == 2 and month is None:
        return "%d or %d" % (years[0], years[1])
    return dates.to_text((years[0], month, day))


class HitReader(object):
    """Builds HitFacts; holds the search-result index and date orders."""

    def __init__(self, data):
        self.data = data
        self.names = data.names
        self.search_by_query = {}
        for row in data.search_results:
            key = tuple(sorted(simple_text(row.get("query") or "").split()))
            if key:
                self.search_by_query.setdefault(key, []).append(row)

    def read(self, hit):
        facts = HitFacts(hit)
        facts.is_company = _is_company(facts.entity_type) if facts.entity_type else None
        company = bool(facts.is_company)
        self._names(hit, facts, company)
        self._countries(hit, facts)
        categories = _texts(hit.get("categories"))
        facts.category = hit.get("primary_category") if isinstance(hit.get("primary_category"), str) \
            and hit.get("primary_category").strip() else (categories[0] if categories else None)
        facts.listing_phrase = LISTING_PHRASES.get(simple_text(facts.category or ""), OTHER_PHRASE)
        self._dobs(hit, facts, company)
        facts.reports = extract.dated_reports(hit.get("profile_notes"))
        if facts.reports:
            facts.earliest_report = min(facts.reports, key=lambda r: (dates.sort_key(r[0]), r[1]))[0]
        self._death(hit, facts)
        return facts

    # ------------------------------------------------------------ names

    def _names(self, hit, facts, company):
        seen = set()

        def add(raw, kind):
            if not isinstance(raw, str) or not raw.strip():
                return
            forms = [raw]
            if not company:
                reordered = reorder_last_first(raw)
                if reordered:
                    forms.append(reordered)
            for form in forms:
                parts = tuple(self.names.parts(form, company))
                if parts and parts not in seen:
                    seen.add(parts)
                    facts.names.append(HitName(raw.strip(), list(parts), kind))

        add(hit.get("name"), "name")
        joined = " ".join(p for p in (hit.get("first_name"), hit.get("middle_name"), hit.get("last_name"))
                          if isinstance(p, str) and p.strip())
        add(joined, "name")
        add(hit.get("best_matching_name"), "name")
        for alias in _texts(hit.get("aliases")):
            add(alias, "alias")

    # ------------------------------------------------------------ countries

    def _countries(self, hit, facts):
        for field in COUNTRY_FIELDS:
            for value in _texts(hit.get(field)):
                for piece in value.split(";"):
                    piece = piece.strip()
                    if not piece:
                        continue
                    found = countries.lookup(piece)
                    if found is None:
                        found = countries.lookup(piece.split(",")[-1])
                    if found is None:
                        facts.unrecognised.append((field, piece))
                        continue
                    for country in found:
                        facts.countries.setdefault(country, [])
                        if field not in facts.countries[country]:
                            facts.countries[country].append(field)
        for country in facts.countries:
            facts.countries[country].sort()

    # ------------------------------------------------------------ dates of birth

    def _dobs(self, hit, facts, company):
        order_fields = self.data.date_orders["hit_fields"]
        order_text = self.data.date_orders["hit_text"]
        unparsed = []
        for value in _texts(hit.get("dobs")):
            parsed = dates.parse_date(value, order_fields)
            if parsed is None:
                unparsed.append(value)
                continue
            facts.dobs.append(HitDob((parsed[0],), parsed[1], parsed[2], facts.hit_id, "dobs_field",
                                     dob_text((parsed[0],), parsed[1], parsed[2])))
        facts.coverage["dobs_unparsed"] = unparsed
        if facts.dobs:
            facts.dob_origin = "dobs field"
            return
        if company:
            return

        paragraph = extract.first_paragraph(hit.get("profile_notes"))
        status, value = extract.anchored_dob(paragraph, order_text)
        if status == "anchored":
            facts.dobs.append(HitDob((value[0],), value[1], value[2], facts.hit_id, "profile_dob",
                                     dob_text((value[0],), value[1], value[2])))
        ages = extract.ages_at_dates(paragraph, order_text)
        for age, when, _position in ages:
            years = extract.birth_years_from_age(age, when)
            facts.dobs.append(HitDob(years, None, None, facts.hit_id, "profile_age", dob_text(years, None, None)))
        facts.coverage["profile_age_words"] = bool(extract.AGE_WORDS.search(paragraph))
        facts.coverage["profile_ages"] = len(ages)
        if facts.dobs:
            facts.dob_origin = "profile notes"
            return

        order_search = self.data.date_orders["search"]
        results = []
        for name in facts.names:
            if name.kind != "name":
                continue
            for row in self.search_by_query.get(tuple(sorted(name.parts)), []):
                if row["result_id"] not in [r["result_id"] for r in results]:
                    results.append(row)
        results.sort(key=lambda row: row["result_id"])
        facts.search_seen = [row["result_id"] for row in results]
        phrase = simple_text(facts.listing_phrase)
        tied = set(facts.countries)
        unparsed_search = []
        for row in results:
            snippet = row.get("snippet") or ""
            if phrase not in simple_text(snippet):
                continue
            if not tied & set(countries.find_in_text(snippet)):
                continue
            facts.search_used.append(row["result_id"])
            ages = extract.ages_at_dates(snippet, order_search, loose=True)
            if not ages:
                unparsed_search.append(row["result_id"])
                continue
            age, when, _position = ages[0]
            years = extract.birth_years_from_age(age, when)
            facts.dobs.append(HitDob(years, None, None, row["result_id"], "search_result",
                                     dob_text(years, None, None)))
        facts.coverage["search_unparsed"] = unparsed_search
        if facts.dobs:
            facts.dob_origin = "search results"

    # ------------------------------------------------------------ death

    def _death(self, hit, facts):
        value = dates.parse_date(hit.get("deceased_date"), self.data.date_orders["hit_fields"]) \
            if isinstance(hit.get("deceased_date"), str) else None
        if value is not None:
            facts.death = (value, "deceased_date")
            return
        if simple_text(hit.get("life_status") or "") == "deceased":
            found = extract.death_dates(hit.get("profile_notes"), self.data.date_orders["hit_text"])
            if found:
                facts.death = (sorted(found, key=dates.sort_key)[0], "profile_notes")

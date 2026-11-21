"""Find the customer record each screening hit was raised on.

An alert carries the customer as screened, plus at most one link: an account
number, a login, or an email. This follows that link into the company's
records. Joint holders are separated with the screened name, date of birth,
country and city. With no link, the same four facts are searched across the
customer file. Records that share a phone number and a full date of birth, or
an approved identity-document number, are treated as one person.

Usage:
    python system/find_customers.py <data folder> <output.jsonl>
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

PLACEHOLDERS = {
    "na", "n/a", "test", "unknown", "xxx", "none", "null", "nil", "tbd", "blank",
}
MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

# Canonical country names. Codes, official names and demonyms collapse together.
COUNTRIES = {
    "algeria": "algeria",
    "argentina": "argentina", "ar": "argentina",
    "armenia": "armenia",
    "australia": "australia", "australian": "australia", "au": "australia",
    "austria": "austria", "austrian": "austria", "at": "austria",
    "bangladesh": "bangladesh", "bangladeshi": "bangladesh", "bd": "bangladesh",
    "brazil": "brazil", "brazilian": "brazil", "br": "brazil",
    "canada": "canada", "canadian": "canada", "ca": "canada",
    "colombia": "colombia", "co": "colombia",
    "cote divoire": "cote divoire", "ci": "cote divoire",
    "czech republic": "czech republic", "czechia": "czech republic", "cz": "czech republic",
    "denmark": "denmark", "danish": "denmark", "dk": "denmark",
    "dominican republic": "dominican republic", "do": "dominican republic",
    "ecuador": "ecuador", "ec": "ecuador",
    "finland": "finland", "finnish": "finland", "fi": "finland",
    "france": "france", "french": "france", "fr": "france",
    "germany": "germany", "german": "germany", "de": "germany",
    "ghana": "ghana", "ghanaian": "ghana", "gh": "ghana",
    "haiti": "haiti", "ht": "haiti",
    "hungary": "hungary", "hungarian": "hungary", "hu": "hungary",
    "india": "india", "indian": "india", "in": "india",
    "indonesia": "indonesia", "indonesian": "indonesia", "id": "indonesia",
    "ireland": "ireland", "irish": "ireland", "ie": "ireland",
    "israel": "israel", "israeli": "israel", "il": "israel",
    "italy": "italy", "italian": "italy", "it": "italy",
    "jamaica": "jamaica", "jamaican": "jamaica", "jm": "jamaica",
    "kenya": "kenya", "kenyan": "kenya", "ke": "kenya",
    "lebanon": "lebanon", "lebanese": "lebanon", "lb": "lebanon",
    "mexico": "mexico", "mexican": "mexico", "mx": "mexico",
    "moldova": "moldova", "moldovan": "moldova", "md": "moldova",
    "montenegro": "montenegro", "me": "montenegro",
    "morocco": "morocco", "moroccan": "morocco", "ma": "morocco",
    "netherlands": "netherlands", "dutch": "netherlands", "nl": "netherlands",
    "new zealand": "new zealand", "nz": "new zealand",
    "nigeria": "nigeria", "nigerian": "nigeria", "ng": "nigeria",
    "north korea": "north korea", "kp": "north korea",
    "norway": "norway", "norwegian": "norway", "no": "norway",
    "pakistan": "pakistan", "pakistani": "pakistan", "pk": "pakistan",
    "panama": "panama", "panamanian": "panama", "pa": "panama",
    "peru": "peru", "peruvian": "peru", "pe": "peru",
    "poland": "poland", "polish": "poland", "pl": "poland",
    "portugal": "portugal", "portuguese": "portugal", "pt": "portugal",
    "romania": "romania", "romanian": "romania", "ro": "romania",
    "russia": "russia", "russian": "russia", "ru": "russia",
    "serbia": "serbia", "serbian": "serbia", "rs": "serbia",
    "singapore": "singapore", "singaporean": "singapore", "sg": "singapore",
    "slovenia": "slovenia", "slovenian": "slovenia", "si": "slovenia",
    "south africa": "south africa", "south african": "south africa", "za": "south africa",
    "spain": "spain", "spanish": "spain", "es": "spain",
    "sweden": "sweden", "swedish": "sweden", "se": "sweden",
    "switzerland": "switzerland", "swiss": "switzerland", "ch": "switzerland",
    "syria": "syria", "syrian": "syria", "sy": "syria",
    "thailand": "thailand", "thai": "thailand", "th": "thailand",
    "united arab emirates": "united arab emirates", "ae": "united arab emirates",
    "united kingdom": "united kingdom", "british": "united kingdom", "uk": "united kingdom", "gb": "united kingdom",
    "united states": "united states", "american": "united states", "us": "united states", "usa": "united states",
    "vietnam": "vietnam", "vietnamese": "vietnam", "vn": "vietnam",
}


def read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def fold(text):
    text = unicodedata.normalize("NFKD", str(text).casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("'", "").replace("'", "").replace("-", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def country_key(value):
    if not value:
        return None
    folded = fold(value).replace(" ", "")
    spaced = fold(value)
    if spaced in COUNTRIES:
        return COUNTRIES[spaced]
    if folded in COUNTRIES:
        return COUNTRIES[folded]
    return spaced


def parse_dob(value):
    """Return (year, month, day). Missing parts are None. Unknown text is None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return _checked(int(match[1]), int(match[2]), int(match[3]))
    match = re.fullmatch(r"(\d{4})", text)
    if match:
        return int(match[1]), None, None
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if match:
        return _checked(int(match[3]), int(match[2]), int(match[1]))
    match = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", text)
    if match and match[1].lower() in MONTHS:
        return _checked(int(match[3]), MONTHS[match[1].lower()], int(match[2]))
    match = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if match and match[2].lower() in MONTHS:
        return _checked(int(match[3]), MONTHS[match[2].lower()], int(match[1]))
    return None


def _checked(year, month, day):
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return year, month, day


def full_dob(value):
    parsed = parse_dob(value)
    if not parsed or None in parsed:
        return None
    year, month, day = parsed
    return f"{year:04d}-{month:02d}-{day:02d}"


def dob_fits(screened, recorded):
    left = parse_dob(screened)
    right = parse_dob(recorded)
    if not left or not right:
        return True
    for part, other in zip(left, right):
        if part is not None and other is not None and part != other:
            return False
    return True


def digits(value):
    if not value:
        return ""
    return "".join(char for char in str(value) if char.isdigit())


def name_parts(value, legal_forms):
    if not value:
        return ()
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", str(value))
    parts = []
    seen = set()
    for token in fold(text).split():
        if len(token) < 2 or token in PLACEHOLDERS or token in legal_forms:
            continue
        if not any(char.isalpha() for char in token):
            continue
        if token in seen:
            continue
        seen.add(token)
        parts.append(token)
    return tuple(parts)


def levenshtein(left, right, limit):
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for i, char in enumerate(left, 1):
        current = [i]
        low = current[0]
        for j, other in enumerate(right, 1):
            cost = 0 if char == other else 1
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
            low = min(low, current[-1])
        if low > limit:
            return limit + 1
        previous = current
    return previous[-1]


GROUP_OF = {}


class Names:
    def __init__(self, groups, legal_forms):
        self.legal_forms = {fold(form) for form in legal_forms}
        self.group_of = {}
        for index, group in enumerate(groups):
            for form in group:
                self.group_of[fold(form)] = index
        GROUP_OF.clear()
        GROUP_OF.update(self.group_of)

    def parts(self, value, entity_type):
        forms = self.legal_forms if entity_type == "Company" else set()
        return name_parts(value, forms)

    def agree(self, screened_name, entity_type, customer):
        screened = self.parts(screened_name, entity_type)
        if not screened:
            return False
        for field in ("full_name", "preferred_name", "login_name"):
            candidate = self.parts(customer.get(field), customer.get("entity_type"))
            if candidate and self._agree(screened, candidate):
                return True
        return False

    def _agree(self, left, right):
        shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
        if _covers_all(shorter, longer):
            return True
        return _max_matches(left, right) >= 2


@lru_cache(maxsize=200000)
def part_match(left, right):
    if left == right:
        return True
    left_group = GROUP_OF.get(left)
    if left_group is not None and left_group == GROUP_OF.get(right):
        return True
    short = min(len(left), len(right))
    if short < 4:
        return False
    limit = 1 if short <= 6 else 2
    return levenshtein(left, right, limit) <= limit


def _max_matches(left, right):
    """How many parts match, ignoring order.

    One part may match two adjacent parts written together (Abdulsalam and
    Abdul Salam). That counts as one match and uses both pieces.
    """
    n, m = len(left), len(right)

    def rec(index, used):
        if index >= n:
            return 0
        best = rec(index + 1, used)
        for bit in range(m):
            if used & (1 << bit):
                continue
            if part_match(left[index], right[bit]):
                best = max(best, 1 + rec(index + 1, used | (1 << bit)))
            if bit + 1 < m and not (used & (1 << (bit + 1))):
                if part_match(left[index], right[bit] + right[bit + 1]):
                    best = max(best, 1 + rec(index + 1, used | (1 << bit) | (1 << (bit + 1))))
        if index + 1 < n:
            joined = left[index] + left[index + 1]
            for bit in range(m):
                if used & (1 << bit):
                    continue
                if part_match(joined, right[bit]):
                    best = max(best, 1 + rec(index + 2, used | (1 << bit)))
        return best

    return rec(0, 0)


def _covers_all(shorter, longer):
    """True when every part of the shorter name matches some unused part of the longer one."""
    n, m = len(shorter), len(longer)

    def rec(index, used):
        if index >= n:
            return True
        for bit in range(m):
            if used & (1 << bit):
                continue
            if part_match(shorter[index], longer[bit]) and rec(index + 1, used | (1 << bit)):
                return True
            if bit + 1 < m and not (used & (1 << (bit + 1))):
                if part_match(shorter[index], longer[bit] + longer[bit + 1]):
                    if rec(index + 1, used | (1 << bit) | (1 << (bit + 1))):
                        return True
        if index + 1 < n:
            joined = shorter[index] + shorter[index + 1]
            for bit in range(m):
                if used & (1 << bit):
                    continue
                if part_match(joined, longer[bit]) and rec(index + 2, used | (1 << bit)):
                    return True
        return False

    return rec(0, 0)


def city_fits(screened_city, customer):
    if not screened_city:
        return True
    screened = fold(screened_city)
    city = fold(customer.get("city") or "")
    address = fold(customer.get("address") or "")
    if screened and (screened in city or screened in address):
        return True
    tokens = screened.split()
    if len(tokens) >= 2 and len(tokens[-1]) <= 3:
        base = " ".join(tokens[:-1])
        if base and (base in city or base in address or city == base):
            return True
    return False


def country_fits(screened_country, customer):
    if not screened_country:
        return True
    wanted = country_key(screened_country)
    if not wanted:
        return True
    residence = country_key(customer.get("residence_country"))
    nationality = country_key(customer.get("nationality"))
    return wanted in (residence, nationality)


def screened_view(alert):
    customer = alert.get("customer")
    if isinstance(customer, dict) and customer.get("name"):
        return {
            "name": customer.get("name"),
            "entity_type": customer.get("entity_type"),
            "dob": customer.get("dob"),
            "country": customer.get("country"),
            "city": customer.get("city"),
        }
    return {
        "name": alert.get("customer_name") or alert.get("name"),
        "entity_type": alert.get("entity_type"),
        "dob": alert.get("dob"),
        "country": alert.get("country"),
        "city": alert.get("city"),
    }


class CustomerSearch:
    def __init__(self, data_dir):
        data_dir = Path(data_dir)
        variants = json.loads((data_dir / "name_variants.json").read_text(encoding="utf-8"))
        groups = variants.get("groups") or variants.get("given_name_groups") or []
        forms = variants.get("company_legal_forms") or variants.get("legal_forms") or []
        self.names = Names(groups, forms)
        self.customers = {row["record_id"]: row for row in read_jsonl(data_dir / "customers.jsonl")}
        self.alerts = {row["alert_id"]: row for row in read_jsonl(data_dir / "alerts.jsonl")}
        self.hits = read_jsonl(data_dir / "hits.jsonl")
        self._index_links(data_dir)
        self._index_people(data_dir)
        self._index_kyc(data_dir)
        self._index_buckets()

    def _index_links(self, data_dir):
        self.by_account = defaultdict(list)
        self.by_login = defaultdict(list)
        for row in read_jsonl(data_dir / "accounts.jsonl"):
            self.by_account[row["account_id"]].append(row["customer_id"])
            if row.get("login_handle"):
                self.by_login[row["login_handle"].casefold()].append(row["customer_id"])
        self.by_email = defaultdict(list)
        for record_id, customer in self.customers.items():
            if customer.get("email"):
                self.by_email[customer["email"].casefold()].append(record_id)

    def _index_people(self, data_dir):
        applicants = defaultdict(list)
        for row in read_jsonl(data_dir / "onboarding.jsonl"):
            if row.get("kyc_applicant_id"):
                applicants[row["customer_id"]].append(row["kyc_applicant_id"])
        documents = defaultdict(set)
        for check in read_jsonl(data_dir / "kyc_checks.jsonl"):
            if check.get("result") != "approved" or not check.get("document_number"):
                continue
            number = fold(check["document_number"]).replace(" ", "")
            if number and number not in PLACEHOLDERS:
                documents[check["applicant_id"]].add(number)
        self.parent = {record_id: record_id for record_id in self.customers}
        phone_groups = defaultdict(list)
        for record_id, customer in self.customers.items():
            phone = digits(customer.get("phone"))
            born = full_dob(customer.get("dob"))
            if phone and born:
                phone_groups[(phone, born)].append(record_id)
        for group in phone_groups.values():
            self._unite(group)
        doc_groups = defaultdict(list)
        for record_id, applicant_ids in applicants.items():
            numbers = set()
            for applicant_id in applicant_ids:
                numbers |= documents.get(applicant_id, set())
            for number in numbers:
                doc_groups[number].append(record_id)
        for group in doc_groups.values():
            self._unite(group)
        self.members = defaultdict(list)
        for record_id in self.customers:
            self.members[self._find(record_id)].append(record_id)
        for group in self.members.values():
            group.sort()

    def _index_kyc(self, data_dir):
        by_applicant = defaultdict(list)
        for check in read_jsonl(data_dir / "kyc_checks.jsonl"):
            by_applicant[check["applicant_id"]].append(check)
        self.checks_by_customer = defaultdict(list)
        seen = defaultdict(set)
        for row in read_jsonl(data_dir / "onboarding.jsonl"):
            applicant_id = row.get("kyc_applicant_id")
            customer_id = row.get("customer_id")
            if not applicant_id or not customer_id:
                continue
            for check in by_applicant.get(applicant_id, []):
                if check["check_id"] in seen[customer_id]:
                    continue
                seen[customer_id].add(check["check_id"])
                self.checks_by_customer[customer_id].append({
                    "customer_id": customer_id,
                    "check_id": check.get("check_id"),
                    "applicant_id": check.get("applicant_id"),
                    "provider": check.get("provider"),
                    "verified_name": check.get("verified_name"),
                    "verified_dob": check.get("verified_dob"),
                    "document_type": check.get("document_type"),
                    "document_number": check.get("document_number"),
                    "document_country": check.get("document_country"),
                    "result": check.get("result"),
                    "checked_at": check.get("checked_at"),
                })

    def kyc_for(self, record_ids):
        checks = []
        for record_id in record_ids:
            checks.extend(self.checks_by_customer.get(record_id, []))
        checks.sort(key=lambda check: (check.get("checked_at") or "", check.get("check_id") or ""), reverse=True)
        return checks

    def _unite(self, record_ids):
        if len(record_ids) < 2:
            return
        root = self._find(record_ids[0])
        for record_id in record_ids[1:]:
            other = self._find(record_id)
            if other != root:
                self.parent[other] = root

    def _find(self, record_id):
        while self.parent[record_id] != record_id:
            self.parent[record_id] = self.parent[self.parent[record_id]]
            record_id = self.parent[record_id]
        return record_id

    def person(self, record_id):
        return tuple(self.members[self._find(record_id)])

    def _index_buckets(self):
        self.by_year = defaultdict(list)
        self.by_country = defaultdict(list)
        self.no_year = []
        for record_id, customer in self.customers.items():
            parsed = parse_dob(customer.get("dob"))
            if parsed:
                self.by_year[parsed[0]].append(record_id)
            else:
                self.no_year.append(record_id)
            for field in ("residence_country", "nationality"):
                key = country_key(customer.get(field))
                if key:
                    self.by_country[key].append(record_id)

    def fits(self, record_id, screened):
        customer = self.customers[record_id]
        if screened["entity_type"] and customer.get("entity_type") != screened["entity_type"]:
            return False
        if not self.names.agree(screened["name"], screened["entity_type"], customer):
            return False
        if not dob_fits(screened["dob"], customer.get("dob")):
            return False
        if not country_fits(screened["country"], customer):
            return False
        return city_fits(screened["city"], customer)

    def search_attributes(self, screened):
        parsed = parse_dob(screened["dob"])
        if parsed:
            pool = list(self.by_year[parsed[0]]) + list(self.no_year)
        elif screened["country"]:
            pool = self.by_country.get(country_key(screened["country"]), [])
        else:
            pool = self.customers
        seen = set()
        found = []
        for record_id in pool:
            if record_id in seen:
                continue
            seen.add(record_id)
            if self.fits(record_id, screened):
                found.append(record_id)
        return found

    def linked_ids(self, alert):
        if alert.get("account_ref"):
            return self.by_account.get(alert["account_ref"], []), "account_ref"
        if alert.get("login_handle"):
            return self.by_login.get(alert["login_handle"].casefold(), []), "login_handle"
        if alert.get("email"):
            return self.by_email.get(alert["email"].casefold(), []), "email"
        return [], ""

    def people_of(self, record_ids):
        ordered = []
        seen = set()
        for record_id in record_ids:
            root = self._find(record_id)
            if root in seen or record_id not in self.customers:
                continue
            seen.add(root)
            ordered.append(self.person(record_id))
        return ordered

    def resolve_alert(self, alert):
        screened = screened_view(alert)
        linked, via = self.linked_ids(alert)
        linked = [record_id for record_id in linked if record_id in self.customers]
        if via:
            people = self.people_of(linked)
            if len(people) == 1:
                return self._result(people, via, linked)
            fitting = [record_id for record_id in linked if self.fits(record_id, screened)]
            people = self.people_of(fitting)
            return self._result(people, via, fitting)
        fitting = self.search_attributes(screened)
        return self._result(self.people_of(fitting), "screened_attributes", fitting)

    def _result(self, people, via, seeds):
        if not people:
            return {
                "customer_record_id": None,
                "customer_record_ids": [],
                "person_count": 0,
                "via": via,
            }
        if len(people) > 1:
            ids = sorted({record_id for person in people for record_id in person})
            return {
                "customer_record_id": "AMBIGUOUS",
                "customer_record_ids": ids,
                "person_count": len(people),
                "via": via,
            }
        person = people[0]
        seed = next((record_id for record_id in seeds if record_id in person), person[0])
        return {
            "customer_record_id": seed,
            "customer_record_ids": list(person),
            "person_count": 1,
            "via": via,
        }

    def resolve_hits(self):
        cache = {}
        rows = []
        for hit in self.hits:
            alert_id = hit["alert_id"]
            if alert_id not in cache:
                alert = self.alerts.get(alert_id)
                cache[alert_id] = self.resolve_alert(alert) if alert else self._result([], "", [])
            found = cache[alert_id]
            rows.append({
                "hit_id": hit["hit_id"],
                "alert_id": alert_id,
                "customer_record_id": found["customer_record_id"],
                "customer_record_ids": found["customer_record_ids"],
                "person_count": found["person_count"],
                "via": found["via"],
                "kyc_checks": self.kyc_for(found["customer_record_ids"]),
            })
        return rows


def score_practice(data_dir, rows):
    path = Path(data_dir) / "practice_labels.jsonl"
    if not path.exists():
        return None
    labels = {row["hit_id"]: row for row in read_jsonl(path)}
    found = {row["hit_id"]: row for row in rows}
    matched = 0
    misses = []
    for hit_id, label in labels.items():
        expected = label["customer_record_ids"]
        actual = found.get(hit_id)
        if expected == ["AMBIGUOUS"]:
            ok = actual is not None and actual["customer_record_id"] == "AMBIGUOUS"
            named = []
            for step in label.get("path") or []:
                if "different people" in step:
                    named = re.findall(r"CUS-\d+", step)
            if ok and named and set(named) != set(actual["customer_record_ids"]):
                ok = False
        else:
            ok = actual is not None and set(actual["customer_record_ids"]) == set(expected)
        if ok:
            matched += 1
        else:
            misses.append((hit_id, expected, None if actual is None else actual["customer_record_ids"], None if actual is None else actual["via"]))
    return matched, len(labels), misses


def main():
    parser = argparse.ArgumentParser(description="Find the customer record for each screening hit.")
    parser.add_argument("data", help="Folder containing alerts.jsonl, customers.jsonl and the other dev files")
    parser.add_argument("output", help="JSON Lines file to write, one hit per line")
    args = parser.parse_args()
    search = CustomerSearch(args.data)
    rows = search.resolve_hits()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    counts = defaultdict(int)
    for row in rows:
        if row["customer_record_id"] is None:
            counts["unresolved"] += 1
        elif row["customer_record_id"] == "AMBIGUOUS":
            counts["ambiguous"] += 1
        else:
            counts["resolved"] += 1
    print(f"wrote {len(rows)} hits to {output}")
    with_checks = sum(1 for row in rows if row["kyc_checks"])
    print(
        f"resolved {counts['resolved']}, ambiguous {counts['ambiguous']}, unresolved {counts['unresolved']}"
    )
    print(f"hits with at least one kyc check: {with_checks}")
    scored = score_practice(args.data, rows)
    if scored:
        matched, total, misses = scored
        print(f"practice customer records {matched}/{total}")
        for hit_id, expected, actual, via in misses:
            print(f"  {hit_id} via {via}: expected {expected} got {actual}")


if __name__ == "__main__":
    main()

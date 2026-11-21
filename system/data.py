"""Loading the data folder.

Every file is sorted by its key straight after loading, so the order of
lines in the input never matters. Duplicate keys keep one row, chosen by its
canonical JSON text. Bad lines are skipped with a warning.
"""

import json
import os
import sys

from names import NameRules


def warn(message):
    sys.stderr.write("warning: " + message + "\n")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def load_jsonl(folder, filename, key_fields):
    """Rows of a JSON Lines file, sorted by key_fields and de-duplicated."""
    path = os.path.join(folder, filename)
    rows = []
    if not os.path.isfile(path):
        warn("missing file " + filename)
        return rows
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                warn("%s line %d is not JSON, skipped" % (filename, number))
                continue
            if not isinstance(row, dict) or not isinstance(row.get(key_fields[0]), str):
                warn("%s line %d has no %s, skipped" % (filename, number, key_fields[0]))
                continue
            rows.append(row)

    def sort_key(row):
        return tuple(str(row.get(field) or "") for field in key_fields) + (canonical(row),)

    rows.sort(key=sort_key)
    unique = []
    last = None
    for row in rows:
        key = tuple(str(row.get(field) or "") for field in key_fields)
        if key == last:
            warn("%s has duplicate %s, kept one" % (filename, "/".join(key)))
            continue
        unique.append(row)
        last = key
    return unique


def group_by(rows, field):
    """{value: [rows]} keeping the rows' sorted order; skips empty values."""
    result = {}
    for row in rows:
        value = row.get(field)
        if isinstance(value, str) and value:
            result.setdefault(value, []).append(row)
    return result


class Data(object):
    """Everything in the data folder, sorted, with lookup indexes."""

    def __init__(self, folder):
        self.folder = folder
        self.alerts = load_jsonl(folder, "alerts.jsonl", ["alert_id"])
        self.hits = load_jsonl(folder, "hits.jsonl", ["hit_id"])
        self.customers = load_jsonl(folder, "customers.jsonl", ["record_id"])
        self.accounts = load_jsonl(folder, "accounts.jsonl", ["account_id", "customer_id"])
        self.onboarding = load_jsonl(folder, "onboarding.jsonl", ["kyc_applicant_id", "account_id"])
        self.kyc_checks = load_jsonl(folder, "kyc_checks.jsonl", ["check_id"])
        self.notes = load_jsonl(folder, "notes.jsonl", ["note_id"])
        self.search_results = load_jsonl(folder, "search_results.jsonl", ["result_id"])
        self.names = NameRules.load(folder)

        self.alert_by_id = {row["alert_id"]: row for row in self.alerts}
        self.customer_by_id = {row["record_id"]: row for row in self.customers}
        self.accounts_by_id = group_by(self.accounts, "account_id")
        self.accounts_by_login = group_by(self.accounts, "login_handle")
        self.accounts_by_customer = group_by(self.accounts, "customer_id")
        self.onboarding_by_account = group_by(self.onboarding, "account_id")
        self.checks_by_applicant = group_by(self.kyc_checks, "applicant_id")
        self.notes_by_email = {}
        for note in self.notes:
            email = normal_email(note.get("requester_email"))
            if email:
                self.notes_by_email.setdefault(email, []).append(note)
        self.customers_by_email = {}
        self.customers_by_phone = {}
        self.customers_by_login = {}
        for customer in self.customers:
            login = customer.get("login_name")
            if isinstance(login, str) and login.strip():
                self.customers_by_login.setdefault(login.strip().casefold(), []).append(customer)
            email = normal_email(customer.get("email"))
            if email:
                self.customers_by_email.setdefault(email, []).append(customer)
            phone = normal_phone(customer.get("phone"))
            if phone:
                self.customers_by_phone.setdefault(phone, []).append(customer)


def normal_email(value):
    if not isinstance(value, str):
        return ""
    return value.strip().casefold()


def normal_phone(value):
    """Digits only; too-short numbers are not usable as a link."""
    if not isinstance(value, str):
        return ""
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits if len(digits) >= 7 else ""


def normal_document(value):
    """Document numbers compared without spaces, dashes or case."""
    if not isinstance(value, str):
        return ""
    text = "".join(ch for ch in value if ch.isalnum()).upper()
    if len(text) < 4 or len(set(text)) == 1:
        return ""
    return text

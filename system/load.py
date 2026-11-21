"""Read the data folder and build lookup indexes. Every collection is sorted by ID."""

import json
import os
import sys

from norm import as_list, phone_key, parse_date


def warn(message):
    sys.stderr.write("warning: " + message + "\n")


def read_jsonl(folder, name, key):
    path = os.path.join(folder, name + ".jsonl")
    rows = {}
    if not os.path.isfile(path):
        warn("missing file %s.jsonl" % name)
        return []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                warn("%s line %d is not JSON" % (name, number))
                continue
            if not isinstance(row, dict):
                continue
            ident = row.get(key)
            if ident is None:
                ident = "%s#%08d" % (name, number)
            text = json.dumps(row, sort_keys=True)
            if ident in rows and rows[ident][0] <= text:
                continue
            rows[ident] = (text, row)
    return [rows[k][1] for k in sorted(rows)]


def low(value):
    return value.strip().lower() if isinstance(value, str) and value.strip() else None


class Data:
    def __init__(self, folder):
        self.folder = folder
        self.alerts = read_jsonl(folder, "alerts", "alert_id")
        self.hits = read_jsonl(folder, "hits", "hit_id")
        self.customers = read_jsonl(folder, "customers", "record_id")
        accounts = read_jsonl(folder, "accounts", None)
        onboarding = read_jsonl(folder, "onboarding", None)
        self.kyc = read_jsonl(folder, "kyc_checks", "check_id")
        self.notes = read_jsonl(folder, "notes", "note_id")
        self.search = read_jsonl(folder, "search_results", "result_id")

        self.customer = {c["record_id"]: c for c in self.customers}
        self.hit = {h["hit_id"]: h for h in self.hits}

        self.accounts_by_id = {}
        self.accounts_by_login = {}
        self.accounts_of_customer = {}
        for a in sorted(accounts, key=lambda r: (str(r.get("account_id")), str(r.get("customer_id")))):
            if a.get("account_id"):
                self.accounts_by_id.setdefault(a["account_id"].strip().upper(), []).append(a)
            if low(a.get("login_handle")):
                self.accounts_by_login.setdefault(low(a["login_handle"]), []).append(a)
            if a.get("customer_id"):
                self.accounts_of_customer.setdefault(a["customer_id"], []).append(a)

        self.applicants_of_customer = {}
        for o in sorted(onboarding, key=lambda r: (str(r.get("customer_id")), str(r.get("account_id")),
                                                    str(r.get("kyc_applicant_id")))):
            if o.get("customer_id") and o.get("kyc_applicant_id"):
                lst = self.applicants_of_customer.setdefault(o["customer_id"], [])
                if o["kyc_applicant_id"] not in lst:
                    lst.append(o["kyc_applicant_id"])

        self.checks_of_applicant = {}
        for k in self.kyc:
            if k.get("applicant_id"):
                self.checks_of_applicant.setdefault(k["applicant_id"], []).append(k)

        self.customer_by_email = {}
        for c in self.customers:
            if low(c.get("email")):
                self.customer_by_email.setdefault(low(c["email"]), []).append(c["record_id"])

        self.notes_by_email = {}
        for n in self.notes:
            if low(n.get("requester_email")):
                self.notes_by_email.setdefault(low(n["requester_email"]), []).append(n)

        self.search_by_query = {}
        for s in self.search:
            q = low(s.get("query"))
            if q:
                # Keyed by the query's words in sorted order, as the name keys are built, so a
                # result is found whatever order its query lists the words in ("smith john").
                self.search_by_query.setdefault(" ".join(sorted(q.split())), []).append(s)

        self.hits_of_alert = {}
        listed = set()
        for a in self.alerts:
            ids = [i for i in as_list(a.get("hit_ids")) if i in self.hit]
            self.hits_of_alert[a["alert_id"]] = ids
            listed.update(ids)
        self.alert = {a["alert_id"]: a for a in self.alerts}
        for h in self.hits:
            if h["hit_id"] not in listed and h.get("alert_id"):
                self.hits_of_alert.setdefault(h["alert_id"], []).append(h["hit_id"])

    # ----------------------------------------------------------- checks

    def checks_of_customer(self, record_id):
        out = []
        for app in self.applicants_of_customer.get(record_id, []):
            out.extend(self.checks_of_applicant.get(app, []))
        return out


def approved(check):
    return str(check.get("result") or "").strip().lower() in ("approved", "passed", "clear", "verified", "accepted")

"""Screening review system (Track 2: "Same person or not?").

Usage: python3 screen.py <data folder> <output file>

Pipeline: load the data folder (data.py) -> resolve each alert's customer
(customer.py, POLICY principles 2-4) -> decide each hit -> write one JSON
line per hit.

Current stage: customer resolution is real; decide_hit() still sends every
hit to REVIEW / INSUFFICIENT_DATA until the hit-side comparison is built.

Determinism rules followed here:
- no clock, no randomness, no network;
- every loaded file is sorted by its key straight after loading;
- nothing iterates over a set or dict without sorted();
- output lines sorted by (alert_id, hit_id), evidence sorted and de-duplicated,
  json.dumps(sort_keys=True, ensure_ascii=False).
"""

import os
import sys

from customer import Resolver
from data import Data, canonical, warn

REVIEW = "REVIEW"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


def shown(value):
    """A value as it should appear inside a reason sentence."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "unknown"


# ---------------------------------------------------------------- deciding

def describe_resolution(resolution, alert):
    """One sentence on who the customer is, with record IDs."""
    customer = (alert or {}).get("customer") or {}
    screened = shown(customer.get("name"))
    if resolution.status == "resolved":
        person = resolution.persons[0]
        if len(person.record_ids) > 1:
            return ("The screened customer %s in alert %s was resolved to one person with records %s."
                    % (screened, resolution.alert_id, ", ".join(person.record_ids)))
        return ("The screened customer %s in alert %s was resolved to customer record %s."
                % (screened, resolution.alert_id, person.primary_id))
    if resolution.status == "ambiguous":
        return ("%d different people in the system of record fit the screened customer %s in alert %s (%s)."
                % (len(resolution.persons), screened, resolution.alert_id,
                   "; ".join(p.describe() for p in resolution.persons)))
    return ("No customer record could be found for the screened customer %s in alert %s."
            % (screened, resolution.alert_id))


def decide_hit(hit, alert, resolution):
    """Return the decision for one hit.

    Not built yet: the hit's own facts are not gathered or compared, so
    nothing can clear or confirm the hit and the safe answer is REVIEW.
    """
    reason = (
        "%s Hit %s (%s) has not yet been compared with the customer's facts, so no "
        "fact found contradicts or confirms the match. Comparing the customer's verified "
        "legal name, date of birth and countries with the hit's would settle it."
        % (describe_resolution(resolution, alert), hit["hit_id"], shown(hit.get("name")))
    )
    evidence = [person.primary_id for person in resolution.persons]
    return {
        "decision": REVIEW,
        "reason_code": INSUFFICIENT_DATA,
        "customer_record_id": resolution.customer_record_id,
        "reason": reason,
        "evidence": evidence,
    }


def fallback_result(hit, alert_id):
    return {
        "decision": REVIEW,
        "reason_code": INSUFFICIENT_DATA,
        "customer_record_id": None,
        "reason": "Hit %s in alert %s could not be assessed because its data could not be "
                  "read. A person needs to review it against the customer's records."
                  % (hit["hit_id"], alert_id),
        "evidence": [],
    }


# ---------------------------------------------------------------- output

def render(hit, result):
    line = {
        "hit_id": hit["hit_id"],
        "alert_id": hit["alert_id"],
        "decision": result["decision"],
        "reason_code": result["reason_code"],
        "customer_record_id": result["customer_record_id"],
        "reason": result["reason"],
        "evidence": sorted(set(result["evidence"])),
    }
    return canonical(line)


def resolve_all(data):
    """{alert_id: Resolution} for every alert, in alert ID order."""
    resolver = Resolver(data)
    resolutions = {}
    for alert in data.alerts:
        try:
            resolutions[alert["alert_id"]] = resolver.resolve(alert)
        except Exception as error:  # any doubt: no customer, REVIEW
            warn("alert %s could not be resolved (%s: %s)" % (alert["alert_id"], type(error).__name__, error))
    return resolutions


def run(data_folder, output_file):
    data = Data(data_folder)
    resolutions = resolve_all(data)

    lines = []
    for hit in sorted(data.hits, key=lambda h: (str(h.get("alert_id")), h["hit_id"])):
        alert_id = hit.get("alert_id")
        if not isinstance(alert_id, str):
            warn("hit %s has no alert_id, skipped" % hit["hit_id"])
            continue
        alert = data.alert_by_id.get(alert_id)
        resolution = resolutions.get(alert_id)
        try:
            if alert is None or resolution is None:
                raise ValueError("alert %s missing or unresolved" % alert_id)
            result = decide_hit(hit, alert, resolution)
        except Exception as error:  # any doubt: REVIEW, never clear
            warn("hit %s could not be decided (%s: %s)" % (hit["hit_id"], type(error).__name__, error))
            result = fallback_result(hit, alert_id)
        lines.append(render(hit, result))

    parent = os.path.dirname(os.path.abspath(output_file))
    if not os.path.isdir(parent):
        os.makedirs(parent)
    with open(output_file, "w", encoding="utf-8", newline="\n") as handle:
        for line in lines:
            handle.write(line + "\n")
    return len(lines)


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: screen.py <data folder> <output file>\n")
        return 2
    data_folder, output_file = argv[1], argv[2]
    if not os.path.isdir(data_folder):
        sys.stderr.write("error: data folder not found: %s\n" % data_folder)
        return 2
    count = run(data_folder, output_file)
    sys.stderr.write("wrote %d hits to %s\n" % (count, output_file))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""Screening review system (Track 2: "Same person or not?").

Usage: python3 screen.py <data folder> <output file>

Pipeline:
  data.py      load the data folder, every file sorted by its ID
  customer.py  who is the customer? (POLICY principles 2-4)
  hit.py       who is the listed person? (principle 5)
  decide.py    compare and decide (principles 6-8), reasons and evidence

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
from decide import decide
from hit import HitReader

REVIEW = "REVIEW"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


def fallback_result(hit, alert_id):
    """Used only when a hit cannot be processed: REVIEW, never clear."""
    return {
        "decision": REVIEW,
        "reason_code": INSUFFICIENT_DATA,
        "customer_record_id": None,
        "reason": "Hit %s in alert %s could not be assessed because its data could not be "
                  "read. A person needs to review it against the customer's records."
                  % (hit["hit_id"], alert_id),
        "evidence": [],
    }


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


class System(object):
    """Everything needed to decide hits; also used by the scripts/ tools."""

    def __init__(self, data_folder):
        self.data = Data(data_folder)
        self.resolver = Resolver(self.data)
        self.reader = HitReader(self.data)
        self.resolutions = {}
        for alert in self.data.alerts:
            try:
                self.resolutions[alert["alert_id"]] = self.resolver.resolve(alert)
            except Exception as error:  # any doubt: no customer, REVIEW
                warn("alert %s could not be resolved (%s: %s)" % (alert["alert_id"], type(error).__name__, error))

    def decide_hit(self, hit):
        """Decision dict for one hit (with a 'trace' list for debugging)."""
        alert_id = hit.get("alert_id")
        alert = self.data.alert_by_id.get(alert_id)
        resolution = self.resolutions.get(alert_id)
        if alert is None or resolution is None:
            raise ValueError("alert %s missing or unresolved" % alert_id)
        facts = self.reader.read(hit)
        result = decide(resolution, self.resolver, facts, alert)
        result["facts"] = facts
        return result

    def sorted_hits(self):
        return sorted((h for h in self.data.hits if isinstance(h.get("alert_id"), str)),
                      key=lambda h: (h["alert_id"], h["hit_id"]))


def run(data_folder, output_file):
    system = System(data_folder)
    lines = []
    for hit in system.sorted_hits():
        try:
            result = system.decide_hit(hit)
        except Exception as error:  # any doubt: REVIEW, never clear
            warn("hit %s could not be decided (%s: %s)" % (hit["hit_id"], type(error).__name__, error))
            result = fallback_result(hit, hit.get("alert_id"))
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

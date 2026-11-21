"""Compare our customer resolution with the practice labels.

Usage: python3 scripts/check_resolution.py [data folder]   (default: dev)

For each practice hit, the customer we resolve counts as right when it is one
of the label's customer_record_ids (AMBIGUOUS must match AMBIGUOUS). Each
mismatch prints the label's path next to our own trace.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "system"))

from customer import Resolver  # noqa: E402
from data import Data  # noqa: E402
import dates  # noqa: E402


def main(argv):
    folder = argv[1] if len(argv) > 1 else os.path.join(ROOT, "dev")
    data = Data(folder)
    resolver = Resolver(data)
    labels = []
    with open(os.path.join(folder, "practice_labels.jsonl"), encoding="utf-8") as handle:
        labels = [json.loads(line) for line in handle if line.strip()]
    labels.sort(key=lambda row: row["hit_id"])
    hit_alert = {hit["hit_id"]: hit["alert_id"] for hit in data.hits}

    right, wrong = 0, []
    for label in labels:
        alert = data.alert_by_id[hit_alert[label["hit_id"]]]
        resolution = resolver.resolve(alert)
        ours = resolution.customer_record_id
        expected = label["customer_record_ids"]
        ok = ours in expected or (expected == ["AMBIGUOUS"] and ours == "AMBIGUOUS")
        if ok:
            right += 1
        else:
            wrong.append((label, resolution))

    print("customer resolution on practice hits: %d of %d right" % (right, len(labels)))
    for label, resolution in wrong:
        print()
        print("%s (alert %s): expected %s, ours %s" % (
            label["hit_id"], resolution.alert_id, label["customer_record_ids"], resolution.customer_record_id))
        print("  label path:")
        for step in label["path"][:-1]:
            print("    " + step)
        print("  our trace:")
        for step in resolution.trace:
            print("    " + step)
        for person in resolution.persons:
            for step in person.trace:
                print("    [%s] %s" % (person.primary_id, step))
    if "-v" in argv:
        for label in labels:
            alert = data.alert_by_id[hit_alert[label["hit_id"]]]
            resolution = resolver.resolve(alert)
            print()
            print(label["hit_id"], resolution.customer_record_id, label["customer_record_ids"])
            for step in resolution.trace:
                print("    " + step)
            for person in resolution.persons:
                for step in person.trace:
                    print("    [%s] %s" % (person.primary_id, step))
    return 0 if not wrong else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

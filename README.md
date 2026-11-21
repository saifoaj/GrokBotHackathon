# FinCI: Same Person or Not?

Track 2 (sanctions screening) of the Grok Bot London Financial Crime Hackathon.

For every watchlist hit raised against a bank customer, FinCI decides whether the hit is the
customer (`SAME_ENTITY`), clearly someone else (`DIFFERENT_ENTITY`), or needs a person
(`REVIEW`), with a reason code, the resolved customer record, a short reason and the IDs of
the records that decided it.

* Run: `system/run <data folder> <output file>` (Python 3 standard library only, about 5 s, no API calls)
* Development predictions: `output/dev.jsonl`
* Design, evidence model, determinism and worked cases: [`system/README.md`](system/README.md)
* Unit tests: `python3 -m unittest discover -s system/tests`

The development data is not in this repository: it is for event participants only.

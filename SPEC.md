# Build spec: "Same Person or Not?" screening system

You are working in the hackathon starter repo. Build the solution described below. Read `POLICY.md` first: it is the authority. Where this spec and POLICY.md disagree, POLICY.md wins.

## Non-negotiable priorities (in order)

1. **Determinism.** Same input must give byte-identical output on every run. 5 runs are compared in scoring. No LLM, no randomness, no dependence on dict/set iteration order, file order or time. Sort everything that is iterated.
2. **Explainability.** Every decision must trace to specific rules and specific record IDs.
3. **Never wrongly clear.** Clearing a real match costs 60 points; an unneeded REVIEW costs 2 to 3. When any rule is unsure or incomplete, it must not clear. Fail toward REVIEW.
4. **Simple, short code.** No bloat, no frameworks, no abstractions for their own sake.

## Constraints

- Python 3 standard library only (no `dateutil`, no pip packages).
- No LLM at all.
- Entry point: `system/run <data_dir> <output_file>`, under 30 minutes.
- Do not commit `dev/` data or the `.xlsx` files (see "Git" below).

## Before writing code

1. Read `README.md`, `POLICY.md`, `output.schema.json`, `validate.py`, `dev/name_variants.json`.
2. Print the keys and 3 sample rows of every `dev/*.jsonl` file. Use the real field names; do not guess.
3. Read all 50 `dev/practice_labels.jsonl` rows. They show the expected decision, reason code(s), customer record IDs, evidence and a reference reason. Use them to learn: evidence format, how multiple reason codes are ordered, and what counts as minimal evidence.
4. Check the date convention for ambiguous dates like `10/08/1988` (DD/MM vs MM/DD) by comparing notes and KYC rows that describe the same person. If no single convention holds, treat ambiguous day/month as unknown (year only). That is the safe choice.

## File structure

```
system/
  run              # executable shell: exec python3 "$(dirname "$0")/screen.py" "$@"
  screen.py        # the whole pipeline, one file, roughly 400-700 lines
  test_screen.py   # unit tests for edge cases (unittest, stdlib)
  README.md        # architecture, determinism, cost/time, 3 worked cases
output/
  dev.jsonl        # produced by: system/run dev output/dev.jsonl
```

## screen.py layout (top to bottom)

```python
# 1. Load
load(data_dir) -> dict of lists           # every jsonl + name_variants.json

# 2. Normalize (pure functions)
norm_name(s) -> list[str]                  # name parts, see Names below
norm_date(s) -> Date(year, month|None, day|None)
norm_country(s) -> code|None               # "GB", "UK", "British", "United Kingdom" -> one code
norm_phone(s) -> str                       # digits only, consistent country prefix

# 3. Extract (regex, run once in step 0)
parse_note(note) -> facts                  # KYC facts or places lived, or nothing
parse_hit_notes(text) -> facts             # DOB sentence, age-at-date, dated reports, death date
parse_snippet(result) -> facts             # age, date, country, listing phrase

# 4. Index (step 0)
build_index(data) -> Index                 # lookups + union-find person_id

# 5. Profiles
resolve(alert, ix) -> list[person_id]
customer_profile(person_id, ix) -> Profile
hit_profile(hit, ix) -> Profile

# 6. Compare and decide
names_agree(...), dob_agrees(...), dob_contradicts(...), countries(...)
decide(customer_profile, hit_profile) -> Decision   # ordered rules, records trace

# 7. Output
reason_text(decision) -> 2-4 sentences from templates
run(data_dir, out_file)
```

Conventions:
- Plain dicts plus a few small `@dataclass`es (Date, Profile, Decision).
- **Every fact is stored as `(value, source_id)`** so citations come for free.
- Rules are small functions in one ordered list. `decide()` evaluates them in fixed order.
- No logging framework, no CLI library, no config files.

## The pipeline

### Step 0: once for the whole dataset
1. Load and normalize all sheets.
2. Parse every note, hit profile note and search snippet once into structured facts tagged with their source ID.
3. **Union-find over customer records only** (never hits). Every customer record starts as its own group. Merge two records when they:
   - share a normalized phone (from accounts) AND the same full date of birth, or
   - have approved KYC checks with the same document number (customer -> accounts -> onboarding -> kyc applicant -> kyc check).
   Chaining is automatic: a record linked to one of them belongs to the person too (POLICY principle 3). A shared phone alone or address alone never merges.
4. Result: `customer record -> person_id`. Most persons have 1 record; some have several. A group is one person.

### Step 1: per hit
Take a hit and its parent alert. The alert is the customer as screened; the hit is the watchlist profile.

### Step 2: resolve the customer (customer data only, never hit data)
- **Alert has a link** (`account_ref`, `login_handle` or `email`): follow it (accounts -> customer; login handle -> accounts or customer login name; email -> customer email). If the link leads to more than one person, the screened details pick between them as in a search.
- **No link**: search customer records that fit what was screened, using only the fields the alert has:
  - full, preferred or login name agrees with the screened name (name rules below; screened names can carry nicknames and typos)
  - date of birth equal to the screened one in every part both give
  - screened country as residence or nationality
  - screened city in the record's city or address
- Expand each match to its whole person via person_id.
- Outcomes:
  - 0 persons: `customer_record_id = null`, decision usually REVIEW / INSUFFICIENT_DATA.
  - 1 person: continue.
  - 2+ persons: decide the hit for each person. If all give the same decision, use it; otherwise REVIEW / AMBIGUOUS_CUSTOMER_RECORD. Either way `customer_record_id = "AMBIGUOUS"`.
- When one person has several records, any of them is accepted as `customer_record_id`. Pick deterministically (e.g. the linked one, else smallest ID).

### Step 3: customer profile
From all of the person's records:
- **Verified identity**: the most recent *approved* identity check wins for legal name, DOB and document country, whether it is a KYC row or a `kyc` note. Rejected or expired checks are ignored. Verified name outranks record name and screened name.
- **Countries**: residence and nationality on all their records, the approved identity document country, and places the notes say they lived.
- **DOB**: verified DOB if present. If not, and records disagree, keep all of them and never clear on DOB from conflicting unverified dates.
- Notes are looked up by the person's emails. Support notes with no facts are ignored.

### Step 4: hit profile (hit data only)
- Hit fields: names, aliases, entity type, dobs, countries, nationality, citizenship, addresses (last part of each), place of birth, deceased date, categories.
- Profile notes (first paragraph): DOB sentence; "aged N at <date>" gives two possible birth years (aged 45 in March 2019 -> 1973 or 1974); date of death.
- **Dated reports** are exactly the English lines of the form `According to …; September 10, 2025: …`. Other wordings do not count. Earliest one is used for the time rule.
- **Search results** (query = hit name) only when neither the dobs field nor the profile notes give a DOB. A result counts only if it describes the same kind of listing AND is in a country tied to the hit:
  - Sanctions: "a sanctioned individual"; PEP: "a politically exposed person"; Enforcement: "the subject of enforcement action"; Adverse Media: "named in adverse media"; SOE: "a state-owned enterprise official"; other: "named on a watchlist". Category = primary category, else first.
- "International" and "Europe" are not countries. Territories are their own country.
- For companies, only the `dobs` field is an incorporation date.

### Step 5: compare (pure functions)

**Names** (POLICY principle 8):
- Ignore case, accents, punctuation and anything in brackets.
- Single letters are initials, not parts. Each distinct part counts once.
- Placeholders (NA, N/A, TEST, UNKNOWN, XXX, NONE, digits and the like) are not parts.
- For companies, legal forms in `name_variants.json` (Ltd, AB, GmbH, SA, ...) are not parts.
- Two parts match if: identical; listed together in `name_variants.json`; written together vs apart (Abdulsalam = Abdul Salam, counts as one part covering both); or edit distance at most 1 for parts of 4-6 letters and at most 2 for longer ones (shorter part's length decides; swap = 2 edits; parts under 4 letters must be identical).
- Names agree if at least two parts match, or every part of the shorter name matches.
- Aliases count as names, nicknames included. A hit's single-part alias can still agree with a customer's name.
- An individual customer whose names have fewer than two real parts can be neither confirmed nor cleared on name.

**Dates of birth**:
- Agree: customer birth year is one of the hit's possible birth years and every part both give (month, day) is equal. A year alone can agree with a full date.
- Contradict: for **every** hit birth date, the customer's birth year is more than 1 year outside it, or both give a month in the same year and the months (or days) differ. One agreeing hit date blocks DOB_MISMATCH.

### Step 6: rules (POLICY principles 6 and 7)

Contradictions (any one gives DIFFERENT_ENTITY):
- ENTITY_TYPE_MISMATCH: one is a person, the other a company.
- DECEASED: hit's date of death is before the customer was screened.
- TEMPORAL_IMPOSSIBILITY: customer younger than 16 at the earliest dated report, or a company incorporated after it.
- DOB_MISMATCH: as defined above (for companies, incorporation date vs hit dobs).
- LOCATION_MISMATCH: every country tied to the customer differs from every country tied to the hit. **Exception: if name and DOB agree, a country difference is REVIEW, not a clear.**
- NAME_MISMATCH_VERIFIED: the customer's verified legal name agrees with none of the hit's names or aliases, even if the record name does. Only with a verified name.

Agreement (only if no contradiction):
- Person: name and DOB agree -> SAME_ENTITY with NAME_DOB_LOCATION_MATCH if a country also agrees, else NAME_DOB_MATCH.
- Company: name and country agree -> SAME_ENTITY / NAME_LOCATION_MATCH.

Otherwise: REVIEW / INSUFFICIENT_DATA.

If several contradictions fire, follow the practice labels for which codes to output and in what order. Evaluate all rules and keep the full list in the trace.

### Step 7: output
One line per hit, matching `output.schema.json` exactly:
- decision, reason code(s), customer record id (`null` if none, `"AMBIGUOUS"` if several persons)
- **evidence**: only the record IDs that decided it (e.g. the KYC check that gave the DOB plus the hit), not everything looked at. Match the style in practice labels.
- **reason**: 2-4 sentences generated from fixed templates filled from the trace, citing values and IDs. Example: "The customer's date of birth is 1990-07-04 (KYC-A1). The hit's profile notes put its birth year at 1960 or 1961. These are more than a year apart, so the hit is a different person."
- Output sorted by hit_id.

## Edge cases to unit test (test_screen.py)

Highest priority first (these can cause a wrong clear):
1. Country differs but name + DOB agree -> REVIEW, not LOCATION_MISMATCH.
2. Hit has two DOBs, one agrees -> no DOB_MISMATCH.
3. Placeholder names (UNKNOWN NADEEM) -> not cleared on name.
4. Expired or rejected KYC is ignored.
5. Two persons fit an unlinked alert with different outcomes -> REVIEW / AMBIGUOUS_CUSTOMER_RECORD.
6. Conflicting unverified DOBs -> no DOB clear.

Then:
7. Union-find chaining A-B-C; phone-only does not merge.
8. Name matching: variants, joined parts, edit distance thresholds, initials, brackets, accents, company legal forms.
9. Age-at-date -> two birth years.
10. Only the exact "According to …; date: …" form counts as a dated report.
11. Search result filtering by listing phrase and hit country.
12. "International"/"Europe" are not countries; codes and demonyms normalize.
13. Every date format seen in the data parses.

## Workflow

1. Inspect data (see "Before writing code").
2. Build step 0 and steps 1-7 with rules that fail toward REVIEW.
3. Run: `system/run dev output/dev.jsonl` then `python3 validate.py output/dev.jsonl --score dev/practice_labels.jsonl`.
4. For each mismatch with practice labels, find the edge case, add a unit test, fix the specific function. Never special-case a hit ID.
5. Report how many notes and snippets failed to parse; add regex patterns until coverage is high.
6. Determinism check: run twice and `diff` the outputs. Must be identical.
7. Write `system/README.md`: architecture (the steps above), how determinism is guaranteed, cost ($0) and runtime, and three worked cases (one DIFFERENT, one SAME, one REVIEW) traced through the files.

## Git

The brief says not to commit `dev/` data, but it is already in history (commit 1512305), along with the spreadsheets. The submission is a public repo. Do not push anything until this is resolved: either rewrite history to remove `dev/` and the `.xlsx` files, or submit from a fresh repo containing only `system/` and `output/`. Ask me before doing either.

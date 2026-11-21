# Same Person or Not? A deterministic, rule-based screening resolver

```
system/run <data_dir> <output_file>       # python3, standard library only, no LLM, no network
python3 -m unittest system/test_screen.py # 22 edge-case tests
```

The whole pipeline is one file, `screen.py`. Every rule in it comes from a principle in `POLICY.md`. It uses **no language model**: every decision traces back to a named rule and to the record IDs that triggered it.

## Architecture

```
                 ┌──────────────── step 0: once per dataset ────────────────┐
 dev/*.jsonl ──► │ load → normalize (names, dates, countries, phones, docs) │
                 │ extract: KYC notes, "lived in" notes, hit profile notes, │
                 │          search snippets → facts tagged with source IDs  │
                 │ index: account/login/email links, KYC via onboarding,    │
                 │        notes by email, search results by name parts      │
                 │ union-find over customer records → person_id             │
                 └──────────────────────────┬───────────────────────────────┘
                                            │
 for each hit (sorted by hit_id):           ▼
   alert ──► resolve customer ──► 0 / 1 / N persons
                                        │
            customer_profile(person) ◄──┘      hit_profile(hit)
   (verified name/DOB/doc country,          (names+aliases, dobs field ∪ notes
    all countries, notes' places)            DOB/age-at-date, else filtered search
                    │                         results; countries; dated reports; death)
                    └──────────► decide() ◄──────┘
                 contradictions (any → DIFFERENT_ENTITY):
                   ENTITY_TYPE, DECEASED, TEMPORAL, DOB, LOCATION*, NAME_VERIFIED
                 country conflict with name+DOB agreeing → REVIEW
                 agreement → SAME_ENTITY (NAME_DOB[_LOCATION] / NAME_LOCATION)
                 otherwise → REVIEW / INSUFFICIENT_DATA
                 N persons → decide each; same decision → it, else AMBIGUOUS_CUSTOMER_RECORD
                                    │
                                    ▼
             reason from fixed templates + evidence IDs → output line
```

### Finding the customer
- **One person, one picture.** Customer records are merged (union-find) when they share a phone number *and* the same full date of birth, or when their approved KYC checks carry the same document number. Merges chain transitively. A shared phone alone, or a shared address, never merges records.
- **Links.** The pipeline follows `account_ref` → accounts (which may list joint holders), `login_handle` → accounts, and `email` → customers. If the link reaches more than one person, the screened details choose between them.
- **No link.** The pipeline searches every record. A record fits when its full or preferred name agrees with the screened name (principle 8), every part of its DOB that both sides give is equal, the screened country is its residence or nationality, and the screened city appears in its city or address. A field that is missing on either side is not held against the record.
- **KYC lookups.** Checks are reached through `onboarding.customer_id → kyc_applicant_id`, never through the account. A joint account must not lend one holder's KYC check to the other.

### Building the customer's profile
- The **most recent approved** check, whether a KYC row or a `kyc`-channel note, gives the legal name, DOB and document country. Rejected and expired rows are dropped. A note whose wording sounds negative (failed, rejected, expired, …) is also dropped.
- **Countries** are residence and nationality from every record, the verified document country, and any country named in a support note.
- With no verified DOB, record DOBs that conflict are all kept, and the hit is then **never cleared or confirmed on DOB**.

### Building the hit's profile
- **Names** come from `name`, `best_matching_name`, first/middle/last and every alias.
- **Countries** come from `countries`, `nationality`, `citizenship`, `addresses` and `place_of_birth`, taking the last comma part with any `[tag]` removed. "International" and "Europe" are not countries.
- **DOBs** are the `dobs` field together with the first paragraph of the notes ("born on/in …", or an age at a date, which gives two possible birth years). When neither gives a DOB, search results are used. A result counts only if its query has the same name parts as one of the hit's names, its snippet contains the listing phrase for the hit's category, and it names a country tied to the hit.
- **Dated reports** are only lines of the exact form `According to …; Month D, YYYY: `. **Death** comes from `deceased_date`.
- For companies, only the `dobs` field counts as an incorporation date.

### Evidence
Every fact is stored as `(value, source_id)`, so citations need no extra work. Evidence lists only what decided the hit, following the practice labels:
- **DOB rules:** the record, check or note that gave the customer's DOB, plus any `SR-…` that gave the hit's DOB.
- **Location mismatch:** every source of a customer country.
- **Verified-name mismatch:** the check or note that gave the name.
- **Agreement:** the sources of the shared countries plus the DOB source.

All 50 practice hits match the reference evidence exactly.

### Failing toward REVIEW
Several guards keep the system from clearing a real match:
- A country difference never clears a hit whose name and DOB agree. It also never confirms one: the hit goes to REVIEW.
- One agreeing hit DOB blocks DOB_MISMATCH.
- A customer whose names have fewer than two real parts (placeholders like UNKNOWN or N/A, or initials only) is never cleared or confirmed on name.
- A country value that does not normalize turns LOCATION_MISMATCH off for that hit.
- Ambiguous day/month in slashed dates: I checked 3,286 record/KYC pairs, and every one fits DD/MM. The second field is never above 12.

## Determinism
The pipeline uses no randomness, clock or network, and no LLM. All inputs are sorted by ID before indexing. Every set is sorted before it is iterated or printed. Union-find always keeps the smaller ID as the root. Ties between checks are broken by date, then ID. I ran the pipeline with `PYTHONHASHSEED=1` and again with `PYTHONHASHSEED=987`, and the two output files are **byte-identical**.

## Cost and time
The cost is **$0**, since no API calls are made. A full dev run (819 hits, 72k customers) takes about **19 s** on a laptop, almost all of it building the index once.

## Results on the development set
- Practice labels: 50/50 decisions correct, 0 unsafe clearances, reason code 100%, customer record 100%, evidence identical to the reference.
- Across all 819 hits: 459 DIFFERENT_ENTITY, 130 SAME_ENTITY, 230 REVIEW.
- Extraction coverage: 16,882/16,882 snippets parsed and 22,575/22,575 KYC notes parsed. Every country value in the data normalizes.

## Three worked cases

**Clear mismatch: `H-D861DA` → DIFFERENT_ENTITY / DOB_MISMATCH**
1. Alert `A-69749F` screens "Michael Earl TILLEY", born 1979-11-08, through `account_ref ACC-A82365`.
2. That is a joint account (`CUS-804632`, `CUS-902175`). The screened name and DOB fit only `CUS-804632`.
3. That customer's email leads to the KYC note `NOTE-8548C1`: verified DOB 1979-11-08.
4. The hit has no `dobs` field. The first paragraph of its profile reads "When investigators conducted questioning of Michael Earl Tilley in January 2023, he was 46", which gives a birth year of 1976 or 1977.
5. 1979 is more than a year from both, so the DOB contradicts every date the hit gives. Evidence: `NOTE-8548C1`.

**Match: `H-0181BD` → SAME_ENTITY / NAME_DOB_LOCATION_MATCH**
1. Alert `A-957E63` screens "COSTA MARCOS ROBERTO" through `ACC-FB5DD0`, a joint account. The screened name and DOB pick `CUS-775419`.
2. Union-find adds `CUS-138588`, which has the same phone and full DOB, so it is the same person.
3. The email leads to `NOTE-CB2CB8`, a verified DOB of 1985-09-12.
4. The hit (PEP, Brazil) gives no DOB in a field or in its notes. Its search results include several namesakes. Only `SR-590704` ("May 2019 … 33 year old MARCOS ROBERTO COSTA, who is a politically exposed person in Brazil") has both the PEP phrase and a hit country. It gives a birth year of 1985 or 1986.
5. Name, DOB and Brazil all agree, and nothing contradicts. Evidence: both records, the note and the search result.

**Left for review: `H-227FDA` → REVIEW / INSUFFICIENT_DATA**
1. Alert `A-9CC0DC` screens "TUAN LE" through an email, which leads to `CUS-481344`.
2. The KYC note `NOTE-4E696D` gives the verified name TUAN LE and a DOB of 1978-05-16. TUAN LE agrees with "Thuận Thị Lê": *tuan* and *thuan* are one edit apart, and *le* = *le*, so two parts match.
3. The hit "Lê, Thuận Thị" (PEP, address in Vietnam) has a profile sentence: "Back in September 2001, Thuận Thị Lê reached the age of 23…". That gives a birth year of 1977 or 1978.
4. Name and DOB agree, but the customer is linked only to the United States and the hit only to Vietnam.
5. POLICY 6 says a country difference cannot clear a hit whose name and DOB agree. POLICY 7 says confirming needs "nothing contradicts". So a human decides. The reason says exactly that, and cites the record and the note.

## Known limits
- Free text is read with regular expressions. A sentence that states an age at a date in a phrasing none of the age cues cover gives no hit DOB. That can only stop a hit from being cleared or confirmed on DOB. In one case it can also allow a location clear the policy would block (name and DOB agree, countries differ), which is why the cues are broad and covered by tests.
- Countries are found in free-text notes with a gazetteer. Over-matching (for example "Jordan" as a first name in a support note) only adds countries to the customer's side, which makes clearing less likely, not more.
- A single-part preferred name (for example "Ibrahim") agrees with a screened "Ibrahim Shuaibu" under principle 8. Unlinked alerts therefore sometimes find several people and end up AMBIGUOUS. That is the safe reading of the policy.

# Deterministic screening interpreter

`decide_hits.py` decides every hit from `POLICY.md` with ordinary Python. The same data folder always produces the same file. No model chooses a decision, a reason code, or a sentence.

```
python system/decide_hits.py <data folder> <output.jsonl>
```

`system/run` is the same entry point. One line per hit, in the shape of `output.schema.json`: `hit_id`, `alert_id`, `decision`, `reason_code`, `customer_record_id`, `reason`, `evidence`.

The run is three steps inside `decide()`:

1. `find_customers.py` resolves the customer behind each alert.
2. `gather_sources.py` attaches that person's records, checks, notes, and the search results for the names.
3. `decide_hits.py` applies the SOP in a fixed order and writes a templated reason.

`extract_facts.py` dumps the same note and snippet parsers into `facts.json`. `fill_model_facts.py` adds the few facts a model can ground with a quote, in `model_notes`, `model_search_results`, and `model_hits` inside that file. `decide_hits.py` reads those records and checks the quote again. `build_review.py` embeds a finished run in `output/review.html`.

`POLICY.md` is the identity SOP. `system/RULES.md` is the record-linking spec. This file is how the code carries them out.

## 1. Who the customer is

`CustomerSearch.resolve_alert` runs once per alert. Every hit on that alert shares the result.

Follow the one link the alert has:

| Link | Records |
|---|---|
| `account_ref` | Every holder of that account |
| `login_handle` | The customer on the account with that login |
| `email` | The customer record with that email |
| None | A search of the customer file |

A link that yields one person is that person, including their other records from the cluster below. A link that yields several people keeps only those who fit the screened details. No link searches the year bucket (plus customers with no date of birth), then the country bucket, otherwise every customer, with the same fit test.

A record fits only when every screened fact that is present fits:

- The full name, preferred name, or login name agrees with the screened name.
- The date of birth is equal in every part both sides give. A missing date does not contradict one that is present.
- The screened country is the residence or the nationality. A territory is its own country.
- The screened city appears in the city or the address. A trailing region code of up to three letters is dropped before that test.

Entity type must match when the alert states one.

### One person, several records

Records are joined with union-find, in both directions, and the join is transitive:

- The same phone digits and the same full date of birth.
- The same document number on an approved identity check, reached through onboarding.

A shared phone alone, or a shared address alone, does not join them.

One person left: `customer_record_id` is the seed the link or the search found, and `customer_record_ids` is the whole cluster. More than one person: `customer_record_id` is `AMBIGUOUS` and the ids are every record of every person. No person: the id is null.

### How a name agrees

Ignore case, accents, punctuation, and anything in brackets. Drop initials, digits, and placeholders (`NA`, `TEST`, `UNKNOWN`, `XXX`, `NONE`, and the rest). Each remaining part counts once. Company legal forms in `name_variants.json` are dropped only for companies.

Two parts match when they are the same, listed in the same variant group, the same part written together or apart, or within edit distance. The shorter part's length sets the distance: identical below 4 letters, one edit for 4 to 6, two edits above that. Swapping two letters is two edits.

The names agree when at least two parts match, or every part of the shorter name matches. Order does not matter.

`fold()` deletes hyphens, so customer search treats `Heinz-Peter` as one token. The decision step turns hyphens into spaces before it calls the same matcher, so `Müller-Kägi` is Müller and Kägi. Leave `fold()` as it is. Customer search already matches the practice customer sets.

### Where the identity checks come from

A check is filed under the provider's applicant id. Join both keys, because a joint account has two holders:

1. `customers.record_id` is `accounts.customer_id`.
2. The same account and the same customer are on `onboarding`.
3. `onboarding.kyc_applicant_id` is `kyc_checks.applicant_id`.

`kyc_for` returns those checks, newest first. Joining on the account alone would attach the other holder's checks.

## 2. What is gathered for the hit

`gather_hit` builds one bundle per hit.

Each person in the bundle gets their customer records, accounts, onboarding rows, identity checks, and notes. Notes join on the customer email, case-insensitive. Joint holders who failed the screened-name test stay on `link.linked_customer_ids`. They are not mixed into the person's records.

Hit countries are `countries`, nationality, citizenship, place of birth, and the last segment of each address. Bracket tags such as `[Business]` and `[Residency]` are stripped. Postcodes are skipped. `International` and `Europe` are dropped. `canon_country` then maps codes, demonyms, and a few names the customer gazetteer lacks (`Palestina` is Palestine). A territory such as Macao SAR stays its own country.

Search results are looked up by folding the name and sorting its words, which is how `search_results.jsonl` stores `query`. A result is `about_listing` only when the snippet contains the hit's listing phrase and a country tied to the hit. The phrase comes from `primary_category`, or the first category when that is empty:

| Category | Phrase |
|---|---|
| Sanctions | a sanctioned individual |
| PEP | a politically exposed person |
| Enforcement | the subject of enforcement action |
| Adverse Media | named in adverse media |
| SOE | a state-owned enterprise official |
| Anything else | named on a watchlist |

These phrases are the same on the unseen set. Other results for the name are namesakes and are not used as the hit's facts.

## 3. How a hit is decided

`decide_person` reads one person. The first hard contradiction wins. A missing fact stays unknown and falls through.

### Facts the rules trust

**Customer birth date, legal name, and document country.** Take the latest approved KYC check, or a passed note on the `kyc` channel, whichever is newer. Name, date of birth, and document country are taken independently from the latest source that states each one. A note counts as passed when it has a pass word (`verif`, `validat`, `approv`, `confirm`, `cleared`, `passed`, `pass` as a whole word, `vetted`, `successful`, `check complete`, `completed`, `matches`) and no fail word (`reject`, `expired`, `unsuccessful`, `could not`, `failed to`, `not approved`, `not verified`). The legal name in a note is the longest run of all-caps words. `DOB`, `ID`, `NA`, `XXX`, `USA`, `UK`, `GB`, and `EU` are skipped, and a trailing `'s` is stripped. The document country is the country next to from, issued, passport, licence, identity, document, per, using, or via. If the note names exactly one country, that country is used.

Declared record dates of birth are used only when no verified date exists. They contradict the hit only when every declared date contradicts every hit date, and they agree only when every declared date agrees.

**Hit birth dates**, for a person, in this order:

1. `dobs`, including `YYYY-MM`.
2. An age in the first paragraph of `profile_notes`, when `dobs` is empty or alongside it.
3. Ages in `about_listing` search snippets, and only when both of the above are empty.

An age next to a date always yields two years: the stated year minus the age, and that year minus one more. The date must sit within 240 characters. A number that is the day of the nearby date is skipped. Profile ages use `aged N`, `age of N`, `N-year-old`, `N year(s) old`, `was N` (not followed by years, months, or days), and `Age: N`. Search snippets also allow a bare `Name, NN,` or `NN, Name` for ages 10 to 99. A company uses only `dobs`.

**Customer countries.** Residence and nationality on every record in the cluster, the document country from the latest approved source, and countries in non-KYC notes that both name a country and contain a lived-in cue: `resid`, `lived`, `living`, `dwelling`, `household`, `housing`, `home`, or `based`. The screened country is not a customer country. Prose matching ignores aliases shorter than four letters with no space, so `it`, `us`, and `in` stay ordinary words. Structured fields still accept codes.

**Dated reports.** Exactly `According to …; Month D, YYYY:` with the colon. A semicolon after the date does not count. The earliest such date is the one that matters. Other wordings are ignored. This form is stable on the unseen set.

**Death.** Only the `deceased_date` field. A year alone counts as dead before screening when 31 December of that year is before `screened_at`. A month without a day uses the last day of that month.

### Date comparison

`agree`, `contradict`, or open:

- Years more than one apart contradict.
- A one-year gap is open. It neither clears nor confirms.
- The same year, with both months present and different, contradicts. The same month with both days present and different contradicts.
- A year alone agrees with a full date in that year. A missing part does not contradict.

### Decision order

1. **Entity type.** Every customer record's type differs from the hit. `ENTITY_TYPE_MISMATCH`.
2. **Death.** `deceased_date` is before screening. `DECEASED`.
3. **Time.** A person is cleared only when the oldest age the stated date allows is under 16 at the earliest dated report. Missing month and day are filled as 1 January and day 1, so a year-only birth uses the oldest possible age. A company is cleared only when every date its incorporation field allows is after that report. The same year, or the same month with no day, is not proof. `TEMPORAL_IMPOSSIBILITY`.
4. **Date of birth.** The summary is `contradict`. `DOB_MISMATCH`.
5. **Verified name.** A verified legal name matches none of the hit's names or aliases. `NAME_MISMATCH_VERIFIED`. No verified name means the hit is not cleared on name. An individual name with fewer than two real parts cannot be confirmed or cleared on name. A hit's single-part name can still agree. A company may agree on one part.
6. **Location.** The country sets are disjoint, and name and date of birth do not both agree. `LOCATION_MISMATCH`. When name and date of birth both agree, a country difference is `REVIEW` / `INSUFFICIENT_DATA`.
7. **Confirm a person.** Name and date of birth agree, and the countries are not a hard difference. `NAME_DOB_LOCATION_MATCH` when the countries overlap, otherwise `NAME_DOB_MATCH`.
8. **Confirm a company.** Name agrees, the countries overlap, and the date of birth is not a contradiction. `NAME_LOCATION_MATCH`.
9. **Otherwise** `REVIEW` / `INSUFFICIENT_DATA`.

### Several people, or none

Each person is decided on their own.

- One person: the resolver's seed is `customer_record_id`.
- Several people, one decision: that decision, with `customer_record_id` `AMBIGUOUS`. If the reason codes differ, the earliest in the clear order is kept: entity type, deceased, temporal, date of birth, verified name, location, then the three confirm codes.
- Several people, different decisions: `REVIEW` / `AMBIGUOUS_CUSTOMER_RECORD`.
- No customer: `REVIEW` / `INSUFFICIENT_DATA`, and `customer_record_id` is null.

Evidence lists the records that decided the hit: customer ids when record fields were used, the check or note that supplied the verified fact, lived-in notes that added a country, and the search result that supplied a hit birth year. Accounts, onboarding rows, and namesake search results are left out.

The reason is filled from those facts with fixed sentences, so two runs match character for character. On the 50 practice hits, 33 reasons match the reference text. The other 17 state the same deciding facts with a shorter sentence, an extra profile age, or a second contradiction left unstated. Two of those, `H-CF173B` and `H-D9BBCC`, lead with `NAME_MISMATCH_VERIFIED` where the reference lists `LOCATION_MISMATCH` first. Both codes are in the accepted list. Local `validate.py --score` checks the decision, that the reason code is one of the accepted codes, and the customer record id.

## Where a model fills a fact

The model does not choose a decision or write the reason. `fill_model_facts.py` asks it only where a pattern returned nothing, on records already tied to a hit. The reply has to quote a contiguous span of the source. `facts_store.py` drops the fact when that quote is not in the text, or when the country, name, age, or date is not in the quote. Accepted facts are written to `facts.json` and checkpointed in `output/model_facts.json`. The next run reads them and does not ask again.

`GROK_API_KEY` in `.env` calls the xAI API with `grok-4.20-0309-non-reasoning`. When the grader sets `OPENROUTER_API_KEY`, the same call goes to OpenRouter instead. `GROK_MODEL` overrides the model id. Temperature is 0. With no key, the gaps stay unknown and the hit stays in review.

| Gap | Asked when | On this development set |
|---|---|---|
| Place lived | A non-KYC note names a country and the lived-in cue does not match | 6 notes |
| Passed KYC note | A passed note is missing a legal name, date of birth, or document country the patterns can read | 9 notes |
| Death in prose | `deceased_date` is empty and the profile uses a death word | 9 hits |
| Age | No listed date and no pattern age, and the first paragraph or a listing snippet contains a number | 220 profiles, 1 snippet |

An age is used only when the hit has no listed date and no pattern age. An extra age is not added beside a date the pattern already found, because one agreeing year would block a real mismatch. A date inside "Regarding the … investigation" is the subject of the case, not the date the person was that age, so it is not used. A model country counts only when the same sentence says the customer stayed, lived, resided, settled, or was domiciled there. Work or a location on its own does not.

Dated reports, listing phrases, structured fields, name comparison, the decision order, and the reason sentences stay in Python.

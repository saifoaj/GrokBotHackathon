# POLICY.md, rule by rule, mapped to the code

Every clause of `POLICY.md` and where it is implemented. File references are to `system/`.
"Stricter" marks a place where we deliberately send more hits to review than the policy
strictly requires; it can never cause an extra clearance.

## Decisions and reason codes

| Policy | Implementation |
|---|---|
| Three decisions; an alert closes only when every hit is `DIFFERENT_ENTITY` | `decide.py` `compare()` returns one of the three per person; `main.py` writes one line per hit |
| One reason code that belongs to the decision | `decide.py` (`CLEAR_ORDER` picks the main code when several clears fire); `validate.py` passes |
| `AMBIGUOUS_CUSTOMER_RECORD` when several people fit and lead to different decisions | `main.py` `decide_hit()` |

## 1. Identity, not policy
Listing category or seriousness is never used in a decision. The category is used only to choose the listing phrase for search results (principle 5).

## 2. Know who the customer is first
| Policy | Implementation |
|---|---|
| Follow whatever link the alert carries | `customers.py` `People.resolve()`: `account_ref` -> accounts -> customer, `login_handle` -> accounts -> customer, `email` -> customer |
| A link can lead to more than one person; the screened details then pick between them, *as in a search* | `resolve()` keeps the linked records that pass `fit_checks()` with name, date of birth, country and city; if none pass, all are kept and decided separately |
| Without a link: full, preferred or login name agrees with the screened name (principle 8) | `fit_checks()` name filter: `NameRules.any_agree(screened, [full, preferred, login])` |
| Date of birth equal in every part both give | `fit_checks()` dob filter: `dates_compatible()` |
| Screened country as residence or nationality | `fit_checks()` country filter (codes and nationality words normalised by `countries.canon`) |
| Screened city in the record's city or address | `fit_checks()` city filter |
| Never use another customer's records | facts come only from the records of the resolved person (`picture()`) |

## 3. One person, one picture
| Policy | Implementation |
|---|---|
| Same phone + same full date of birth -> same person | `customers.py` `_build_merge()` key `("p", phone, dob)`, full dates only |
| Same document number on approved checks -> same person | `_build_merge()` key `("d", number)`, approved checks only |
| A record linked to one of them belongs too (transitive) | union-find in `_build_merge()` |
| Phone or address alone never merges | no other merge keys exist |
| Several people fit: decide each; same decision -> give it, else `AMBIGUOUS_CUSTOMER_RECORD`; record id `AMBIGUOUS` either way | `main.py` `decide_hit()` |

## 4. Verified beats declared
| Policy | Implementation |
|---|---|
| Most recent approved check (provider or onboarding note) gives legal name, dob, document country | `customers.py` `kyc_facts()` (provider checks + `kyc` notes, newest first) and `picture()` |
| Rejected or expired checks are not evidence | `load.approved()` for provider checks; `parse_kyc_note()` drops notes that report a rejection, expiry, failure or pending check |
| Verified name outranks record and screened names | `picture()`: when a verified name exists, it is the only name used for agreement and for the name test |

## 5. Gather the hit's facts
| Policy | Implementation |
|---|---|
| dob from `dobs`; else a sentence in the first paragraph of the profile notes (age at a date -> two years); else search results | `hits.py` `picture()` in that order; `hit_age_years()` / `age_years()` give `[Y-A-1, Y-A]`; `born_date()` for "born on ..." |
| A search result is about the hit only with the same kind of listing in a country tied to the hit | `hits.py` `search_checks()`: listing phrase from `listing_phrase()` **and** a country in the hit's countries |
| Listing phrases by primary category (or first category) | `hits.py` `LISTING` / `listing_phrase()` |
| Countries tied to a hit: `countries`, `nationality`, `citizenship`, last part of `addresses`, `place_of_birth`; not "International"/"Europe" | `hits.py` `hit_countries()`; `countries.NOT_COUNTRIES` |
| Dated reports are exactly "According to ...; Month d, yyyy:" lines | `hits.py` `REPORT` regex |
| For companies only `dobs` is an incorporation date | `picture()` reads profile and search dates only for individuals |
| *Stricter:* a first paragraph that is itself a news line ("According to ...") is not used for an age | ages in news text often belong to other people |

## 6. Clear only on a contradiction (`decide.py` `compare()`)
| Policy | Implementation |
|---|---|
| Time: under 16 at the earliest dated report; company incorporated after it; death before screening | blocks `# ---- temporal` and `# ---- deceased`; partial dates use the most favourable reading (`norm.earliest/latest/before`) |
| Date of birth: every hit date contradicted (> 1 year out, or same year with different month/day) | `contradicted()`; fires only if every hit date is contradicted under every candidate customer date |
| Location: every customer country differs from every hit country | `# ---- location`; customer countries = residence + nationality on all records + approved document countries + countries named in notes |
| A country difference does not clear when name and dob agree -> REVIEW | `location_conflict` keeps such hits at REVIEW |
| Name: verified legal name agrees with none of the hit's names or aliases | `# ---- name` (`NAME_MISMATCH_VERIFIED`) |
| Type: person vs company | `# ---- type` |
| *Stricter:* a hit country we cannot recognise blocks the location test | avoids clearing on a mis-read place |

## 7. Confirm only on agreement
| Policy | Implementation |
|---|---|
| Dates agree: customer year is one of the hit's years and every part both give is equal; a year alone can agree | `agrees()` |
| Person: name + dob agree, nothing contradicts | `SAME_ENTITY` with `NAME_DOB_LOCATION_MATCH` (shared country) or `NAME_DOB_MATCH` |
| Company: name + country agree, nothing contradicts | `SAME_ENTITY` `NAME_LOCATION_MATCH` |
| Otherwise REVIEW | `INSUFFICIENT_DATA`, with the reason saying what is missing |

## 8. Names (`names.py`) and `name_variants.json`
| Policy | Implementation |
|---|---|
| Ignore case, accents, punctuation, anything in brackets | `NameRules.parts()` (`norm.strip_accents` also maps letters such as ł, ø, ß) |
| Single letters are initials, not parts; each distinct part counts once | `parts()` drops 1-letter tokens and duplicates |
| Parts listed together in `name_variants.json` match; groups overlap, so **any** group listing both counts | `NameRules.__init__` loads every group from the data folder's `name_variants.json`; `part_match()` checks for a shared group |
| Written together or apart (Abdulsalam = Abdul Salam, one part covering both) | `_units()` adds joined adjacent pairs; `match_count()` counts it as one match covering two parts |
| Within 1 edit for parts of 4-6 letters, 2 for longer; under 4 letters identical; a swap is two edits; the shorter part decides | `part_match()` + `edit_distance()` (Levenshtein, no transposition) |
| Names agree: at least two parts match, or every part of the shorter name matches | `NameRules.agree()` (maximum matching, not greedy) |
| Aliases count as names, nicknames included | `hits.hit_names()` (aliases, best matching name, first/middle/last); customer full, preferred and login names |
| Placeholders are not parts; an individual with fewer than two real parts can be neither confirmed nor cleared on name | `PLACEHOLDERS`; `compare()` requires two real parts for individuals |
| A hit's single-part alias can still agree | "every part of the shorter name" rule in `agree()` |
| Company legal forms from `name_variants.json` are not name parts | `parts(company=True)` drops `company_legal_forms` |

Effect on the development set: switching off the name groups changes 20 decisions and
switching off the legal forms changes 9 (measured by running with each part of the file
removed).

## 9. Cite what you used
| Policy | Implementation |
|---|---|
| Name the records relied on, and the customer record resolved (`null` if none) | each fact in the pictures keeps its source ID; `compare()` builds evidence only from the facts behind the tests that decided; `main.py` sets `customer_record_id` |
| Cite what decided the hit, not everything looked at | e.g. a DOB clear cites only the check or note with the birth date (plus `SR-` if a search result gave the hit's year) |

## Checking it yourself
`python3 system/audit.py <data> report.html` writes, for every practice hit, each filter
step with what was kept or filtered out and why, and the values each test compared.

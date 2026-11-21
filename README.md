# Same Person or Not? Hackathon Brief

**Can your AI tell whether two records describe the same person?** A bank's screening system has flagged hundreds of possible matches between its customers and people on watchlists. Most share a name and nothing else. To decide, you first have to work out who the customer really is from the bank's messy internal systems, then work out who the listed person really is among their namesakes. Build a system that clears the hits that are clearly different people and keeps genuine or uncertain matches for a human to review. No compliance background needed.

## 1. Context: what is watchlist screening?

Banks must not do business with certain people, and must take extra care with others. These people sit on **watchlists**:

- **Sanctions lists:** people and companies governments ban banks from dealing with.
- **Politically exposed persons (PEPs):** officials and their families, who carry a higher risk of corruption.
- **Adverse media and enforcement records:** people linked to crimes such as fraud or bribery in news or court records.

**Screening** checks every customer's name against these lists. Think of it as a fuzzy join: names are matched loosely so a spelling variant or a reordered name doesn't slip through. Each candidate match is a **hit**, and one customer's hits are grouped into an **alert**.

Loose matching means most hits are **false positives**: a different person who happens to share a similar name. Deciding whether a hit is really the customer is called **entity resolution**, and it's done by comparing everything else you know about the two people:

| Customer | Watchlist hit | Likely answer | Why |
|---|---|---|---|
| Mohammed Ali, born 14 Mar 1988, London | Mohamed Ali, born 1952, Cairo | Different person | Born 36 years apart |
| Li Wei, born 1975, Singapore | Wei Li, born 1975, Singapore | Same person | Same name reordered, same year and country, nothing contradicts |
| Anna K. Petrova, no date of birth | Anna Petrova, born 1969, Moscow | Needs review | Too little information to rule out either way |
| Tom Okafor, born 2004 | Thomas Okafor, named in a fraud report dated 2011 | Different person | The customer was 7 at the time |

One thing to keep separate: **identity is not policy.** This track asks only "is this the same person?" Whether the listed offence matters, or whether the bank would accept the customer anyway, is a different question.

## 2. What makes it hard: both sides need resolving

In real life, neither side of the comparison arrives clean.

**The customer side.** The alert only shows the customer as they were screened, sometimes with a nickname or a typo. Their real details are spread across the bank's systems, and those systems don't share one key:

- The **system of record** (CRM) holds customer records, but one person can have more than one record, for example an old account. Family members can share a phone number or address and still be different people.
- **Accounts** link a login or account number to a customer record.
- The **KYC provider** (the company that checks passports and ID cards) stores checks against its own applicant ID, which the **onboarding** table links to an account. Some checks were rejected; some were only written up as a note.
- The **support tool** files notes by email address. Notes can mention where the customer used to live.

**The watchlist side.** A hit's most useful fact, such as a date of birth, is not always in a neat field. It may be written in the profile ("aged 45 when sentenced in March 2019"), or only findable in **web search results** for the name. Most of those results are about other people with the same name.

## 3. The data room

Three names come up throughout:

- **Development set:** the data you get, in the `dev/` folder: about 440 alerts with 820 hits, a system of record of about 72,000 customer records, and the bank's other systems. It comes without answers.
- **Practice labels:** 50 hits from the development set that do come with their answers, so you can check your system as you build it.
- **Unseen set:** a separate set of alerts from the same bank that you never see. We run your system on it to score you.

Each file is JSON Lines (one record per line), which your system reads. A spreadsheet version of the development set is there too, for browsing by eye.

| File | What it holds |
|---|---|
| `POLICY.md` | The bank's identity SOP: the decisions, the principles to follow, the few numbers they depend on, and the reason codes. Read it first |
| `alerts.jsonl` | Each alert: the customer as screened, a link to the customer (account, login or email) when the alert has one, and its hits |
| `hits.jsonl` | Each watchlist profile that matched: names, aliases, dates of birth, countries, addresses, categories, profile notes, sources |
| `customers.jsonl` | The bank's system of record |
| `accounts.jsonl`, `onboarding.jsonl` | Links from accounts and logins to customers, and from accounts to KYC applicants |
| `kyc_checks.jsonl` | Identity checks by the KYC provider, with the verified name, date of birth, document, document number and result |
| `notes.jsonl` | Support and onboarding notes, filed by the customer's email |
| `search_results.jsonl` | Web search results for names on the watchlist and in the bank, most about namesakes |
| `name_variants.json` | Name forms that count as the same (Mohammed and Muhammad, Tony and Anthony), and company legal forms |
| `practice_labels.jsonl` | 50 answered hits, each with the path through the files that leads to the answer, so you can check your system |

Four rules apply throughout:

- **Unknown stays unknown.** A missing field is not a mismatch, and neither is the missing part of a partial date: a birth year alone can still agree with a full date. Don't fill it in.
- **Verified beats declared.** A passed identity check is more reliable than what the customer typed in. A rejected check is not evidence.
- **Build for the principles, not for these files.** Your system will be tested on unseen data with new customers, new watchlist hits and free text worded differently. A system that memorises this folder's wording will miss things.
- **Handle the data with care.** Every customer, account, check and note is invented or pseudonymised, and the watchlist profiles come from a screening provider. Share it only through the event's participant access.

## 4. The objective

For every hit, decide one of three things:

- **`DIFFERENT_ENTITY`:** the evidence shows the hit is a different person or company. The hit is a false positive and can be cleared.
- **`SAME_ENTITY`:** the evidence shows the hit is the customer.
- **`REVIEW`:** the evidence is insufficient or contradictory, so a human decides.

The costly mistake is clearing a real match: a sanctioned person stays a customer. So **review is the safe choice when in doubt**, but it isn't free: every review is work for an analyst, and a system that reviews everything resolves nothing.

An alert closes only when **every** hit in it is `DIFFERENT_ENTITY`. One `SAME_ENTITY` or `REVIEW` keeps the whole alert open, though scoring is per hit.

An analyst works out who the customer really is, who the listed person really is, and then compares them. `POLICY.md` has the rules; how your system gets there is up to you.

## 5. The deliverable

1. **A runnable system.** An executable we can call as `system/run <data folder> <output file>`. It reads only that folder and writes one line per hit. It may use Python 3 or Node.js standard libraries, or shell. It may call a language model only through the OpenRouter API, with the key in `OPENROUTER_API_KEY`: use your own while you build, and we set ours when we test. A run should take under 30 minutes.
2. **Predictions on the development set**, made by your system, in `output/dev.jsonl`. One JSON line per hit:

| Field | Content |
|---|---|
| `hit_id`, `alert_id` | Which hit, from `hits.jsonl` |
| `decision` | `DIFFERENT_ENTITY`, `SAME_ENTITY` or `REVIEW` |
| `reason_code` | The main ground, from the table in `POLICY.md` (for example `DOB_MISMATCH`) |
| `customer_record_id` | The customer record you resolved the alert to, `AMBIGUOUS` if more than one person fits, or `null` |
| `reason` | Two to four sentences naming the decisive facts |
| `evidence` | The IDs of the records that decided it, exactly as they appear in the files |

3. **A short README** in `system/`: your architecture (a diagram helps), how you find the customer and the listed person, how you represent evidence, how you keep results the same from run to run, which model and prompts you use if any, and the time and cost of a run. Explain three cases: a clear mismatch, a match, and one you left for review.

`validate.py` checks your output file the way we do, and with `--score dev/practice_labels.jsonl` it scores your practice hits.

**Submitting.** By [deadline], put your `system/` and `output/` folders in a public GitHub repository and submit its link with your project name where the event collects submissions. Don't commit the `dev/` data or your API key: the data is for event participants only. We score the last commit before the deadline.

## 6. How you'll be judged

We run your system **five times on an unseen set** of about 550 hits that you never see, from the same bank, with new customers, new hits and differently worded free text. Some situations the policy covers are rare in the development set. We also run it once on the development set to check your predictions came from it.

**Decisions.** Each decision has a cost; lower is better:

| Right answer | You said `DIFFERENT_ENTITY` | You said `SAME_ENTITY` | You said `REVIEW` |
|---|---|---|---|
| `DIFFERENT_ENTITY` | 0 | 3 | 3 |
| `SAME_ENTITY` | **60** (unsafe clearance) | 0 | 2 |
| `REVIEW` | **60** (unsafe clearance) | 2 | 0 |

Your decision score is the share of cost you save compared with sending everything to review. Sending everything to review scores 0, and so does clearing everything (scores below 0 count as 0). Some hits are not scored because their answer depends on information that isn't in the files; answer them anyway.

**Reasons.** For every right decision we check that the reason code fits, that the evidence cites the records that decided it (and not everything you looked at), that the customer record is right, and that the reason states the real grounds with this hit's own facts.

**Score out of 100.**

| Part | Weight | What counts |
|---|---|---|
| Unseen set | 80% | Decisions 75%, reasons 25% |
| Development set | 10% | Decisions 75%, reasons 25% |
| Reproducibility | 10% | The same decision and reason code in all five runs, scaled by your unseen score |

**What we also report.** Unsafe clearances, the share of false positives cleared, clearance precision, same-entity recall and review rate.

**What the judges look at.** For finalists, the judges read your README and talk to you about your architecture: how you resolve people on both sides, how you represent evidence, how you keep it deterministic, and whether your explanations are honest about what's missing.

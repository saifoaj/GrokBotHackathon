# Record linking rules

How a screening hit is tied to a customer, and how that customer's identity checks are found. Whether the hit is the same person or company as the customer is decided afterwards, under `POLICY.md`.

A missing fact is not a mismatch. Do not fill in a part that neither record states.

## 1. The customer the alert was screened as

The alert carries the customer as screened: name, whether they are a person or a company, and any date of birth, country, or city that was typed in. It also carries at most one link into the company's files.

Follow the link the alert actually has.

| Link on the alert | Where it goes |
|---|---|
| Account number | Every holder of that account |
| Login | The customer on the account with that login |
| Email | The customer record with that email |
| None | A search of the customer file |

A link can lead to more than one person, as on a joint account. The screened details then pick between them, using the same test as a search with no link. Use another customer's records only when they are the same person (rule 2).

### Search with no link

Keep a customer record only when every screened fact that is present fits:

- The full name, preferred name, or login name agrees with the screened name (rule 4).
- The date of birth is equal in every part both sides give. A year alone can agree with a full date. A missing date does not contradict one that is present.
- The screened country is the customer's residence or nationality. A country code and the nationality of that country are the same country. A territory is its own country.
- The screened city appears in the record's city or address.

## 2. One person can have several records

These records are the same person, and the link runs in both directions:

- They share a phone number and the same full date of birth.
- Their approved identity checks carry the same document number.

Sharing a phone or an address alone does not make them the same person. Family members often do.

All of that person's records count. If more than one person still fits the alert, the customer is `AMBIGUOUS`.

## 3. KYC checks belong to a customer through onboarding

A KYC check is filed under the provider's applicant id. It does not name the customer. Join both the account and the customer, because a joint account has two holders:

1. `customers.record_id` is `accounts.customer_id`.
2. That same account and the same customer are `onboarding.account_id` and `onboarding.customer_id`.
3. `onboarding.kyc_applicant_id` is `kyc_checks.applicant_id`.

One customer can have several accounts, and so several applicants and many checks. Joining on the account alone attaches the other holder's checks.

## 4. Which check counts

The most recent check with result `approved` is the legal name, date of birth, and document country. It outranks the name on the customer record and the name that was screened. Those can be nicknames, login names, or typos.

Rejected and expired checks are not evidence. The same fact may instead be written in an onboarding note filed under the customer's email. A passed check recorded only in a note still counts; a rejected or expired one does not.

## 5. When two names agree

Ignore case, accents, punctuation, and anything in brackets. A single letter is an initial, not a name part. Each distinct part counts once. Placeholders such as NA, TEST, UNKNOWN, XXX, NONE, and digits are not name parts.

Two parts match when any of these is true:

- They are the same.
- They are listed together in `name_variants.json`.
- They are the same part written together or apart. Abdulsalam matches Abdul Salam, and that counts as one part covering both.
- They differ by at most one edit when the shorter part is 4 to 6 letters, or two edits when it is longer. The shorter part's length decides. A part shorter than 4 letters must be identical, unless the two forms are listed together. Swapping two letters is two edits.

The names agree when at least two parts match, or every part of the shorter name matches. Order does not matter. Aliases count as names. For a company, the legal forms in `name_variants.json` (Ltd, GmbH, SA, and the rest) are not name parts.

A person whose names have fewer than two real parts cannot be confirmed or cleared on name.

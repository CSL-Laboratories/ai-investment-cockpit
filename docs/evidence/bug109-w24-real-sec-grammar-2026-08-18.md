---
type: evidence
status: merged-evidence-pending
last-verified: 2026-08-18
---

# BUG-109 — bounded real SEC merger grammar

## Scope and safety boundary

This package repairs one deterministic W-24 false negative found during the post-merge prospective
intake. It is based on merged `main` SHA `dd0aa3303bd9b5a2f74e863d22d01231068208b9` and the
separate intake record at `64b5512`. It changes only the eligibility parser and its tests. It does
not change mode, risk, approval, gateway, watchlists, minimum cohort size, chronology, freshness,
filing completeness, witness, precision, or expected-edge gates.

The runtime stayed stopped and `read_only`. No source was imported into the signed store, no quote
was captured, no Discord commitment was posted, and no proposal, approval, paper Order, live Order,
or autonomous authority was created.

## Frozen real-source replay

The positive source is CRNX Form 8-K accession
[`0001140361-26-027642`](https://www.sec.gov/Archives/edgar/data/1658247/000114036126027642/ef20077399_form8k.htm),
filed 2026-07-06. A project-`SecFetcher` GET returned 59,123 bytes with SHA-256
`7ea49c80c7a2e9af795092520a03d9bea160154ec870f2204e726d6a413496ec`.

Before the repair, the untouched normalized filing produced no `_CASH` match and no `_DEFINITIVE`
match. The causes were mechanical:

- the `$0.001` common-stock par value stopped a span that rejected every period;
- the filing says the shares are canceled and automatically converted before stating $85.00 per
  share in cash; and
- it says the company entered into an Agreement and Plan of Merger rather than repeating the
  literal phrase `definitive merger`.

After the repair, the untouched full source derives one eligible label: cash offer `85.00`, Nasdaq
US common equity, definitive/active, and false for CVR, earnout, stock consideration, leverage,
rumor/competing bid, and amendment. `_require_strategy_eligible` accepts that derived label. This
is a parser replay, not a prospective selection or a held-out score.

## Bounded repair and adversarial findings

The cash grammar now permits only a decimal period inside a finite 700-character target-share
span, recognizes tightly enumerated cancellation/conversion orderings, and requires `in cash`
either before or after `per share`. A normal period still ends the span, so separate sentences and
the source-set document boundary cannot synthesize consideration. The agreement grammar accepts
the exact `entered into an Agreement and Plan of Merger` assertion and rejects both `not` and
`never` variants.

The first adversarial pass found that the longer span could borrow an employee-option payout later
in the same sentence. A second probe found the same risk for notes and a repeated preferred-share
subject. The final grammar stops on contrast clauses, repeated `each/every share` subjects, option/
unit/award/warrant subjects, and note/bond/debt/preferred-stock subjects. Regressions also retain
refusal for cross-sentence and cross-document synthesis, missing explicit cash, negated agreements,
unrelated option payouts, mixed cash/stock consideration, and written-fraction stock consideration.
The scan remains bounded and unfamiliar language remains unavailable.

## Local gates

- focused semantic verifier: **31 passed**;
- merger edge + semantic verifier + order-boundary slice: **67 passed**;
- full isolated Windows suite: **2,311 passed, 1 skipped**, with five dependency deprecation
  warnings and no control warning; and
- repository-wide Ruff: **clean**.

The first full attempt was invalidated by operator configuration: a local `.env` provisioned the
SEC User-Agent for eight tests that assert an unconfigured default. It reported 2,303 passed,
1 skipped, and eight unrelated configuration failures. The exact eight passed after the `.env`
was reversibly isolated and ambient `COCKPIT_*` variables were cleared. The authoritative full run
used that same isolation; `.env` was restored by `finally`, and the temporary hold path is absent.

## Remaining gates

This package merged in PR #43 at `4bcf3330c8befc9c33534fb9f91b44827d485726`; all six hosted
matrix jobs executed zero steps under the payment restriction. It does not close W-24. W-24 still
requires independent adversarial review, Ubuntu/macOS and Python 3.11/3.12 attestation, an
independently human-labelled held-out parser audit
at or above 95% with no false-positive eligibility/outcome, one complete real ≥30-deal prospective
cohort, its witnessed chronology and horizon outcomes, and one current eligible target. Until all
of those pass, expected edge remains unavailable, the merger execution watchlist remains empty,
and no paper opening order is permitted.

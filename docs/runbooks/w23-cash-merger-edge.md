---
title: W-23 cash-merger evidence and activation runbook
type: runbook
status: live
last-verified: 2026-08-14
related:
  - PLAN
  - REGISTER
  - research/dec03-strategy-selection-2026-08-14
  - runbooks/w22-paper-mvp
---

# W-23 cash-merger edge

W-23 implements the deterministic expected-edge source selected by `DEC-03`. It does not
authorize paper mode, live capital, autonomous trading, or deal discovery. The provider has no
broker import or route to `OrderGateway`; it can return only a verified `EdgeEstimate` or `None`.

## Current safe state

`COCKPIT_MERGER_WATCHLIST` defaults to empty, no empirical merger cohort has been provisioned,
and the existing mega-cap awareness watchlist is not merger evidence. Therefore
`/quant/status` reports `cash_merger_30d.v1` as unavailable and new opening Orders remain
fail-closed. Keep the detached W-22 read-only soak on its pinned revision while this branch is
reviewed and tested.

Do not invent a cohort, copy probabilities from a fund, treat raw spread as edge, or enroll a
company merely because it is already in `COCKPIT_WATCHLIST`. An actual eligible target must be
explicitly present in both startup-only lists.

## Fixed eligibility and calculation

The initial source accepts only an operator-reviewed Nasdaq/NYSE/NYSE American common-equity
target with a signed definitive cash agreement, a long-target `buy`, and expected resolution
within 30 calendar days. Tender offers remain shadow-only until Schedule TO/14D-9 ingestion is
separately added; the implementation is intentionally narrower than the DEC-03 ceiling. It
refuses stock/mixed consideration, CVRs, earnouts, options, leverage, rumors,
unreviewed amendments, termination, quote prices below $5, spreads above 50 bps, median daily
dollar volume below $20 million, fewer than 20 completed-bar observations, metrics older than
seven days, deal reviews older than 24 hours, and calibrations older than one year.

The raw cohort must contain at least 30 unique point-in-time deals, all three outcomes
(`closed`, `failed`, `open`), explicit failed/delisted coverage, and at least 95% held-out field
precision. Code—not the operator JSON—derives the 95% Wilson lower completion probability,
allocates the remaining probability across empirical failure/open proportions, and selects the
worst observed failure/open price ratios. Those three probabilities sum exactly to one. W-21
then independently requires the resulting gross edge to cover at least three times modeled
execution cost.

Every outcome must be observed strictly after selection. An `open` row is valid only at exactly
30 calendar days after `selected_at`; a `closed` or `failed` row must resolve within that same
30-day window. Same-day labels, arbitrary early open marks, and outcomes beyond day 30 are
rejected as horizon/calibration leakage.

## Raw cohort shape

The cohort JSON is an array with this exact row schema; unknown fields fail closed:

```json
{
  "schema_version": "merger_calibration_cohort_row.v1",
  "deal_id": "stable-point-in-time-id",
  "selected_at": "2025-01-02",
  "outcome_as_of": "2026-07-31",
  "outcome": "failed",
  "entry_ask": "42.10",
  "outcome_price": "27.25",
  "heldout_fields": 12,
  "heldout_correct_fields": 12,
  "included_point_in_time": true,
  "failure_or_delisting_captured": true
}
```

`entry_ask` is the point-in-time eligible entry observation. `outcome_price` is cash proceeds
for `closed`, the observed failure/break price for `failed`, or the price exactly 30 days after
selection for `open`. `selected_at` must strictly predate `outcome_as_of`; resolved outcomes
must occur by day 30. A current-survivor scrape is not a valid cohort.

The calibration metadata file supplies only operator assertions and the frozen timestamp; the
provisioning tool overwrites every derived statistic and raw reference:

```json
{
  "schema_version": "merger_calibration.v1",
  "version": "cash_merger_30d.v1",
  "method": "wilson95_worst_case_prices_v1",
  "frozen": true,
  "frozen_at": "2026-08-14T20:00:00+00:00",
  "survivorship_safe": true,
  "includes_failed_delisted": true
}
```

## Deal record shape

The operator-reviewed deal JSON uses this exact contract. The CLI derives `filing_hashes` from
the already-ingested, write-once SEC raw store; the source accessions must exist as `Filing`
rows for the same symbol/CIK. Each source row/raw tuple must also have its original atomic
`filing_ingested` audit event, and the symbol/CIK mapping must have its signed
`edgar-company-attribution` event. A matching unsigned row or pre-seeded raw blob is not
provenance and is rejected. Legacy records without those events must be re-fetched through the
normal EDGAR pipeline; do not back-sign them in place.

```json
{
  "schema_version": "merger_deal.v1",
  "symbol": "TARGET",
  "target_cik": "0000000000",
  "cash_offer": "25.00",
  "security_type": "common_equity",
  "listing_country": "US",
  "listing_exchange": "nasdaq",
  "announcement_accession": "0000000000-26-000001",
  "current_accession": "0000000000-26-000001",
  "agreement_status": "definitive",
  "lifecycle_status": "active",
  "conditions": ["shareholder approval", "regulatory clearance"],
  "announcement_at": "2026-08-01T12:00:00+00:00",
  "observed_at": "2026-08-14T20:00:00+00:00",
  "expected_resolution_date": "2026-08-28",
  "outside_date": "2026-09-15",
  "source_accessions": ["0000000000-26-000001"],
  "has_cvr": false,
  "has_earnout": false,
  "has_stock": false,
  "uses_options_or_leverage": false,
  "is_rumor_or_competing_bid": false,
  "amendment_reviewed": false
}
```

For a reviewed amendment, include both announcement and current accessions and set
`amendment_reviewed` true. A new target filing detected after `observed_at` invalidates the edge
until a new immutable review record covers it. A termination is recorded as a new
`lifecycle_status: terminated` record; deleting it cannot resurrect the old active record.

## Provisioning (read-only mode only)

Run from the repository root against the operational DB/raw paths. The tool refuses any audited
mode other than `read_only`, writes the raw cohort once, signs the derived record into the audit
chain, and never contacts a model or broker.

```powershell
.\.venv\Scripts\python.exe tools\provision_merger_edge.py `
  --db-url sqlite:///app/cockpit.db `
  --raw-root app/data/raw `
  calibration `
  --document C:\evidence\calibration.json `
  --cohort C:\evidence\cohort.json `
  --day 2026-08-14 `
  --key cash-merger-30d-v1

.\.venv\Scripts\python.exe tools\provision_merger_edge.py `
  --db-url sqlite:///app/cockpit.db `
  --raw-root app/data/raw `
  deal `
  --document C:\evidence\target-deal.json
```

After offline tests, adversarial review, and cross-platform CI are green, add only the eligible
target to both guarded non-secret settings and restart:

```dotenv
COCKPIT_WATCHLIST=TARGET
COCKPIT_MERGER_WATCHLIST=TARGET
```

Wait for a fresh quote/daily-bar poll after the reviewed deal timestamp. Require
`/quant/status.verification.expected_edge_source` to name `cash_merger_30d.v1`; unavailable is
a refusal, not a warning to bypass. Re-provision a current deal review at least daily and after
every target filing, offer change, amendment, or termination.

Production accepts a pre-existing quote or daily-bar cache only when the exact path, kind, date,
key, record count, and content hash were already bound by a verified audit event. First startup
will not sign a file merely because it occupies the expected cache path. Proposal and
approval-time verification consume one atomically published quote/book/raw-evidence tuple, and
the exact daily metric object whose ADV, observation count, and realized volatility are all
recomputed from the signed bars.

## Paper activation remains a separate operator action

Only after the branch gates and real evidence above pass may the operator deliberately enter
audited `paper` mode. A schema-validated proposal still goes through deterministic risk → mode
→ exact-symbol human approval → `OrderGateway` → Alpaca paper. Approval-time risk recomputes
the edge from the fixed persisted quantity and current signed quote. Any movement or evidence
fault can reject a proposal that passed at draft time. That is intended.

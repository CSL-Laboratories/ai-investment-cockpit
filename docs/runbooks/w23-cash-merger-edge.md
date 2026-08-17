---
title: W-23 cash-merger evidence and activation runbook
type: runbook
status: live
last-verified: 2026-08-16
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
The production composition currently returns `None`: the separately reviewed semantic verifier
needed to bind merger terms and empirical labels to raw filings is claimed as `W-24` but has not
been implemented.

## Current safe state

`COCKPIT_MERGER_WATCHLIST` defaults to empty, no empirical merger cohort has been provisioned,
the existing mega-cap awareness watchlist is not merger evidence, and there is intentionally no
runtime/configuration bypass around the closed semantic-evidence gate. Therefore
`/quant/status` reports `cash_merger_30d.v1` as unavailable and new opening Orders remain
fail-closed. PR #36 is merged with a green six-job matrix; that code result does not satisfy the
remaining semantic or empirical gates.

Do not invent a cohort, copy probabilities from a fund, treat raw spread as edge, or enroll a
company merely because it is already in `COCKPIT_WATCHLIST`. An actual eligible target must be
explicitly present in both startup-only lists, but doing that today still cannot activate W-23.

## Fixed eligibility and calculation

The initial source accepts only an operator-reviewed Nasdaq/NYSE/NYSE American common-equity
target with a signed definitive cash agreement, a long-target `buy`, and expected resolution
within 30 calendar days. Tender offers remain shadow-only until Schedule TO/14D-9 ingestion is
separately added; the implementation is intentionally narrower than the DEC-03 ceiling. It
refuses stock/mixed consideration, CVRs, earnouts, options, leverage, rumors,
unreviewed amendments, termination, quote prices below $5, spreads above 50 bps, median daily
dollar volume below $20 million, fewer than 20 completed-bar observations, metrics older than
seven days, deal reviews older than 24 hours, and calibrations older than one year.

The raw cohort must contain at least 10 training deals followed by at least 30 temporally
held-out evaluation deals. The evaluation split must contain all three outcomes (`closed`,
`failed`, `open`). Every row binds a unique signed selection-time SEC filing, entry quote,
later outcome SEC filing, and outcome-price observation. Code—not operator-supplied
counters—compares predictions with the exact labelled
eligibility fields and requires at least 95% held-out precision. Code then derives the 95%
Wilson lower completion probability from the evaluation split, allocates the remaining
probability across empirical failure/open proportions, and selects the
worst observed failure/open price ratios. Those three probabilities sum exactly to one. W-21
then independently requires the resulting gross edge to cover at least three times modeled
execution cost.

These mechanical checks do not prove that an operator-selected cohort is complete, that its
predictions were committed before outcomes, or that human labels accurately describe the cited
filings. They are research diagnostics only. Production remains unavailable until a separately
scoped and adversarially reviewed verifier independently attests the complete selection universe,
pre-outcome prediction commitment, ground-truth outcomes, and every authority-bearing deal term.

Every outcome must be observed strictly after selection. An `open` row is valid only at exactly
30 calendar days after `selected_at`; a `closed` or `failed` row must resolve within that same
30-day window. Same-day labels, arbitrary early open marks, and outcomes beyond day 30 are
rejected as horizon/calibration leakage.

## Raw cohort shape

The cohort JSON is an array with this exact row schema; unknown fields fail closed:

```json
{
  "schema_version": "merger_calibration_cohort_row.v2",
  "deal_id": "stable-point-in-time-id",
  "symbol": "TARGET",
  "target_cik": "0000000000",
  "source_accession": "0000000000-25-000001",
  "split": "evaluation",
  "selected_at": "2025-01-02",
  "outcome_as_of": "2025-01-16",
  "outcome": "failed",
  "entry_ask": "42.10",
  "outcome_price": "27.25",
  "eligibility_prediction": {"<exact eligibility fields>": "<predicted values>"},
  "eligibility_label": {"<exact eligibility fields>": "<held-out labels>"},
  "eligibility_source": {
    "event_seq": 101, "raw_ref": "0000000000-25-000001",
    "content_hash": "<64 lowercase hex>"
  },
  "entry_price_source": {
    "event_seq": 102, "raw_ref": "market-data://2025-01-02/TARGET-entry",
    "content_hash": "<64 lowercase hex>"
  },
  "outcome_source": {
    "event_seq": 103, "raw_ref": "0000000000-25-000002",
    "content_hash": "<64 lowercase hex>"
  },
  "outcome_price_source": {
    "event_seq": 104, "raw_ref": "market-data://2025-01-16/TARGET-outcome",
    "content_hash": "<64 lowercase hex>"
  }
}
```

Both eligibility dictionaries must contain exactly `cash_offer`, `security_type`,
`listing_country`, `listing_exchange`, `agreement_status`, `lifecycle_status`, `has_cvr`,
`has_earnout`, `has_stock`, `uses_options_or_leverage`, `is_rumor_or_competing_bid`, and
`amendment_reviewed`. Source events must predate the frozen cutoff, be unique across
observations, and resolve to the separately stored raw SEC/market artifacts whose exact values
match the row. The selection filing's SEC filing date must be no later than `selected_at`. The
outcome filing must belong to the same CIK, differ from the selection filing, and have an SEC
filing date strictly after selection and no later than `outcome_as_of`. Local `detection_ts`
remains the honest import time and is never backdated for historical evidence.
`entry_ask` is the point-in-time eligible entry observation. `outcome_price` is cash proceeds
for `closed`, the observed failure/break price for `failed`, or the price exactly 30 days after
selection for `open`. `selected_at` must strictly predate `outcome_as_of`; resolved outcomes
must occur by day 30. A current-survivor scrape is not a valid cohort.

The calibration metadata file supplies only the pinned method and frozen timestamp; the
provisioning tool overwrites every derived statistic and the cohort reference/hash:

```json
{
  "schema_version": "merger_calibration.v1",
  "version": "cash_merger_30d.v1",
  "method": "wilson95_heldout_worst_case_prices_v2",
  "frozen": true,
  "frozen_at": "2026-08-14T20:00:00+00:00"
}
```

## Deal record shape

The operator-reviewed deal JSON uses this exact contract. The CLI derives `filing_hashes` from
the already-ingested, write-once SEC raw store; the source accessions must exist as `Filing`
rows for the same symbol/CIK. Each source row/raw tuple must also have its original atomic
`filing_ingested` audit event, and the symbol/CIK mapping must have its signed
`edgar-company-attribution` event. A matching unsigned row or pre-seeded raw blob is not
provenance and is rejected. Legacy records without those events and post-announcement targets
skipped by first-contact baselining must be re-fetched through the bounded exact-accession
command below. It verifies the SEC response and existing metadata before atomically adding the
signed binding.

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
chain, and never contacts a model or broker. These commands provision research/audit artifacts;
they do not open the production semantic gate or create order authority.

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
  filing `
  --cik 0000000000 `
  --accession 0000000000-26-000001

.\.venv\Scripts\python.exe tools\provision_merger_edge.py `
  --db-url sqlite:///app/cockpit.db `
  --raw-root app/data/raw `
  deal `
  --document C:\evidence\target-deal.json
```

Do **not** add a W-23 target or activate paper trading from these artifacts. `W-24`, a separately
scoped safety change, must first implement the independent semantic verifier described above and
repeat adversarial review. Only after that work, offline tests, and cross-platform CI are green
may an eligible target be added to both guarded non-secret settings:

```dotenv
COCKPIT_WATCHLIST=TARGET
COCKPIT_MERGER_WATCHLIST=TARGET
```

After an exact-accession import, a future activation must wait for a successful scheduled EDGAR
poll from the **current process**. The adapter verifies every mutable baseline revision against
its signed transition history before use and completion, and binds the response-body CIK to the
CIK in the requested SEC submissions URL. For merger-scope CIKs, parallel arrays must have equal
length, accessions must be valid and unique, the saved boundary must occur exactly once, and SEC
filing/acceptance chronology must be unambiguously newest-first. Any unsupported or malformed
post-boundary row blocks baseline advancement and poll completion; it cannot be silently
classified as a quiet day. The deal recorder and both order checks require a
per-CIK completion watermark no older than ten minutes that covers the current filing-event head.
EDGAR degradation enters audited watch-only, and recovery lands no higher than `review_live`.
Then wait for a fresh quote/daily-bar poll after the reviewed deal timestamp. Require
`/quant/status.verification.expected_edge_source` to name `cash_merger_30d.v1`; unavailable is
a refusal, not a warning to bypass. Re-provision a current deal review at least daily and after
every target filing, offer change, amendment, or termination.

Production accepts a pre-existing quote or daily-bar cache only when the exact path, kind, date,
key, record count, and content hash were already bound by a verified audit event. First startup
will not sign a file merely because it occupies the expected cache path. Proposal and
approval-time verification consume one atomically published quote/book/raw-evidence tuple, and
the exact daily metric object whose ADV, observation count, and realized volatility are all
recomputed from the signed bars.

## Paper activation remains blocked

W-23 must not be used to enter `paper` until the independent semantic verifier is implemented,
reviewed, and supported by real evidence. Once that separate gate exists, a schema-validated
proposal will still go through deterministic risk → mode → exact-symbol human approval →
`OrderGateway` → Alpaca paper. Approval-time risk recomputes
the edge from the fixed persisted quantity and current signed quote. Any movement or evidence
fault can reject a proposal that passed at draft time. That is intended.

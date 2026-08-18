---
title: W-24 prospective merger semantic authority runbook
type: runbook
status: live
last-verified: 2026-08-18
related:
  - PLAN
  - REGISTER
  - runbooks/w23-cash-merger-edge
  - evidence/w23-cash-merger-edge-2026-08-14
  - evidence/w24-merger-semantic-verifier-2026-08-17
---

# W-24 merger semantic verifier

W-24 closes the code-level semantic gap left deliberately by W-23. It does not discover deals,
promote mode, place an order, or authorize live capital. The provider still returns `None` unless
every W-23 integrity/freshness check and every W-24 semantic/prospective check passes.

## Authority contract

The production verifier accepts only a cohort that satisfies all of these conditions:

1. At least 30 evaluation candidates are selected on one date; deal ids, symbols, CIKs, and
   accessions are individually unique. The witnessed manifest is the complete **declared**
   research universe, not a claim of global deal discovery or an execution allowlist.
2. Each schema-v2 manifest candidate separates its original semantic `source_accession` from an
   explicit `current_accession` and binds the complete ordered set of signed 8-K/10-Q/10-K rows
   between them. The current accession must remain the strict EDGAR baseline. Identity, selection
   date, source-set-derived eligibility prediction, entry ask, signed quote source, current filing
   set hash, and fresh per-CIK EDGAR binding are committed—never an outcome or outcome price. The
   quote's exchange timestamp and audit event must precede commitment, and the quote may be no
   more than five minutes old; a favorable earlier same-day quote is not admissible.
3. The manifest's canonical SHA-256 receives an exact-message Discord server timestamp within five
   minutes of creation in the configured private audit channel. Later verification fetches that
   exact message id and rechecks its channel, authenticated bot author, unedited state, and absence
   of webhook authorship; missing, edited, deleted, foreign, or unreachable evidence fails closed.
4. Outcomes are recorded only after the witness. Every label is re-derived from hash-verified SEC
   bytes by the pinned parser. Source-derived but ineligible mixed/stock/CVR/earnout/rumor/
   terminated rows are excluded by refusal, not silently filtered.
5. A successful audited EDGAR poll occurs after the outcome horizon and within five minutes of its
   audit record. Every domain `Filing` through the horizon must have one valid signed/raw binding.
   Conflicting closed/failed evidence, unsigned rows, hidden files, or an early `open` mark fails.
6. The schema-v2 calibration contains every committed evaluation id exactly once, no additions,
   and the same training cutoff. W-23 still derives the conservative Wilson/worst-price numbers.
7. Current deal terms and dates are re-derived from the complete reviewed source set. Ambiguous,
   negated, non-binding, mixed-consideration, amended-but-unclassified, or unrecognized language
   returns unavailable. False negatives are expected and must never be bypassed manually.
8. The full semantic check runs during estimate and again inside immutable edge persistence. Draft,
   approval, and gateway paths continue to recompute the same provider through W-21 cost/risk.

The parser lives outside `app/risk/`, makes no model or broker call, and receives no order
capability. Discord is the only new external read/write and reuses the existing keyring-held bot
token plus `COCKPIT_AUDIT_LOG_CHANNEL_ID`; there is no new W-24 secret. This exact-message
chronology control is separate from SEC-11: with Administrator retained, Discord rolling history
is a mutable mirror and never audit-anchor authority.

## Current safe state

As of 2026-08-17, the private Discord channel and exact-message bot-author/edit checks are
configured, but the merger strategy watchlist remains empty and there is no real prospective
selection commitment. Composition installs `CashMergerSemanticVerifier`, but its eligible
execution universe is absent and status remains unavailable. This is correct. Do not reuse W-23
retrospective fixtures, copy the broader awareness watchlist into strategy authority, or add an
override.

The implementation merged in PR #38. Its final BUG-106-integrated Windows gate is 2,274 passed /
1 skipped with Ruff clean. This is code evidence, not a substitute for the external witness,
prospective cohort, or cross-platform CI; see
[[evidence/w24-merger-semantic-verifier-2026-08-17]].

The minimum evaluation horizon means a qualifying real 30-deal cohort cannot be completed on the
same day it is selected. Background/read-only and named live-feed evidence can run now; opening
paper Orders cannot use this edge until the prospective horizon and all other MVP gates pass.

The 2026-08-18 GET-only intake screen exposed `BUG-108`: the old single candidate source had to
equal the current EDGAR baseline, but later ordinary filings generally supersede the announcement
without restating the terms needed by calibration. Zero of 19 current SEC-mapped filing heads in
the real screen passed the pinned parser. The schema-v2 repair keeps the original semantic source,
binds every locally signed filing from it through the explicit current baseline, and re-derives
evaluation eligibility from that frozen set. Omitted, added, reordered, unsigned, future-dated,
post-commit, non-current, or tampered sources fail before witness publication or later authority.
Legacy v1 manifests are rejected; there is no migration or recommitment because no real selection
commitment exists. The real cohort and external/cross-platform evidence remain blocked; see
[[evidence/w24-prospective-intake-2026-08-18]].

The final local BUG-108 Windows gate is 2,303 passed / 1 skipped with Ruff clean. Its adversarial
pass also prevents parser phrases from being synthesized across filing boundaries. This remains
local code evidence only, not the missing Ubuntu/macOS attestation or real prospective evidence.

A bounded post-merge continuation did not start a commitment. Of 65 discovered names, only six
had an absolute current SEC feed head in the supported form set, and none of their 64 supported
2026 primary filings passed the eligibility parser. CRNX accession `0001140361-26-027642` is a
confirmed real false negative: the filing contains the cash amount, common-stock identity, Nasdaq
listing, and merger agreement, while `_CASH` and `_DEFINITIVE` reject ordinary filing grammar.
`BUG-109` remains fail-closed. Do not substitute an exhibit, broaden a pattern, or hand-enter a
label during intake; use a separately reviewed parser package and then satisfy the real held-out
precision gate before attempting a prospective witness.

## Start a prospective cohort

Stay in audited `read_only`. Provision the private Discord audit channel for the Administrator bot
and verify send/read-history plus exact-message fetch. Do not claim its channel permissions make
messages immutable; edits are rejected and deletion/unavailability blocks the cohort. Keep research cohort
membership separate from the capped execution watchlists: W-24 must not widen order authority to
collect calibration evidence. Before selection, establish the audited EDGAR baseline and use the
bounded `filing` command for the original semantic source, every intervening supported filing, and
the current supported accession so each raw artifact and domain row is signed. The `selection`
command runs one final strict EDGAR evidence poll for every candidate CIK in the same process before
it posts the manifest; it refuses partial, degraded, over-budget, stale, unsigned, or incomplete
coverage. `current_accession` must equal that poll's audited baseline, so an unreviewed later filing
cannot be hidden. A latest unsupported SEC form still fails closed; this repair does not broaden the
ingestion form set.

Each candidate JSON object has exactly these outcome-free fields:

```json
{
  "deal_id": "prospective-stable-id",
  "symbol": "TARGET",
  "target_cik": "0000000000",
  "source_accession": "0000000000-26-000001",
  "source_accessions": [
    "0000000000-26-000001",
    "0000000000-26-000002",
    "0000000000-26-000003"
  ],
  "current_accession": "0000000000-26-000003",
  "selected_at": "2026-08-17",
  "entry_ask": "42.10",
  "eligibility_prediction": {"<exact W-23 eligibility fields>": "<prediction>"},
  "eligibility_source": {
    "event_seq": 101,
    "raw_ref": "0000000000-26-000001",
    "content_hash": "<64 lowercase hex>"
  },
  "entry_price_source": {
    "event_seq": 102,
    "raw_ref": "market-data://2026-08-17/TARGET-entry",
    "content_hash": "<64 lowercase hex>"
  }
}
```

`source_accessions` begins with `source_accession`, ends with `current_accession`, has no
duplicates, is nondecreasing by SEC filing date, and includes every signed supported filing in
that inclusive filing-date interval. Same-day ordering is frozen by the witnessed list; no filing
may be omitted. The eligibility source reference still points to the first semantic accession.
Ordinary later filings may contribute no deal language, while an explicit merger amendment sets
`amendment_reviewed` and must agree with the committed prediction and eventual label.

Commit only after reviewing the exact list and confirming there are no outcome fields:

```powershell
.\.venv\Scripts\python.exe tools\provision_merger_edge.py `
  --db-url sqlite:///app/cockpit.db `
  --raw-root app/data/raw `
  selection `
  --candidates C:\evidence\prospective-candidates.json `
  --training-cutoff 2026-08-16
```

Record the returned `msel_...` id and Discord message id. Do not delete or edit the message. A
Discord post with no database row can remain after a later local persistence failure; it grants no
authority because calibration must reference a signed row whose hashes and timestamps all match.

## Complete and freeze calibration

Continue strict EDGAR and market-data ingestion for every committed CIK. For `closed`/`failed`,
record the first source-derived resolution no later than day 30. For `open`, wait exactly 30 days.
In all cases, run a successful strict per-CIK EDGAR poll after 00:00 UTC following
`outcome_as_of`; a retrospective/backdated completion timestamp is rejected.

Build the W-23 cohort with all training rows and every committed evaluation row. The calibration
document uses `merger_calibration.v2` and includes the exact `selection_manifest_id`; the tool
overwrites all statistics and the cohort ref/hash. Then provision the current target deal through
the W-23 runbook, wait for current-process EDGAR plus fresh quote/bars, and inspect
`/quant/status`. `unavailable` is a hard refusal.

## Evidence still required

- reviewed W-24 PR and green Ubuntu/Windows/macOS × Python 3.11/3.12 matrix;
- configured/readable exact-message Discord witness and retained server timestamp;
- one real ≥30-deal prospective evaluation universe with all horizon outcomes and poll coverage;
- field-level parser audit showing at least 95% held-out correctness without a false-positive
  eligibility/outcome;
- one current eligible target whose terms/dates parse without an override;
- RW-14/RW-15 named live evidence and two qualifying W-22 unattended market days.

Until every item passes, keep the system `read_only` for bring-up and evidence collection. No
paper, live, `review_live`, `auto_veto`, or `auto_full` authority is granted by this package.

---
title: W-24 merger semantic verifier — local implementation evidence
type: evidence
status: live
last-verified: 2026-08-17
related:
  - REGISTER
  - runbooks/w23-cash-merger-edge
  - runbooks/w24-merger-semantic-verifier
  - evidence/w23-cash-merger-edge-2026-08-14
---

# W-24 local implementation evidence — 2026-08-17

## Claim boundary

Branch: `work/w24-merger-semantic-verifier`. The branch is based on the post-PR-36 documentation
commit `68fd541c2749394a3e61d088aadb0dc5e4bae87d`; its merge-base with `origin/main` is PR #36 merge
`2cbb6d594b8b5f77f9228549c02a9f73259680b0`.

This record proves local implementation, deterministic reachability, Windows regression, and
lint/diff integrity. It does **not** prove cross-platform CI, a readable production Discord
witness, a real prospective cohort, parser accuracy on real filings, profitability, an eligible
current target, paper-broker operation, or live/autonomous readiness. No mode or trading authority
changed. With the external witness and prospective evidence absent, production still returns no
merger edge.

## Implemented controls

- a schema-gated, outcome-free selection manifest with at least 30 individually unique deal,
  symbol, CIK, and accession identities selected after the training cutoff;
- one strict same-process EDGAR poll for every zero-padded candidate CIK before commitment, with
  partial/degraded coverage refused and the exact current filing head/set/poll authority captured;
- source and price observations rebound to their signed audit events and immutable raw artifacts;
- exact-message Discord publication and later retrieval of the manifest hash, using the server
  timestamp and existing keyring-held bot token/private audit channel contract;
- a narrow deterministic SEC-text parser for cash/share, common equity, supported US listing,
  definitive status, lifecycle, adverse consideration flags, dates, conditions, and outcomes;
- schema-v2 calibration bound to the witnessed manifest, with every committed evaluation deal
  present exactly once and labels/outcomes re-derived from signed SEC bytes;
- complete signed `Filing`-domain comparison through the outcome horizon, conflicting-resolution
  rejection, and a non-backdated successful post-horizon EDGAR completion requirement;
- semantic verification during estimate and again inside immutable edge persistence, with the
  authority digest included in schema-v2 edge evidence;
- production composition of the real verifier with no permissive configuration switch; and
- no model runtime, MCP, broker capability, subprocess, platform branch, or I/O in `app/risk/`.

## Adversarial review

The local attack/fix round constructed regressions for source-derived but strategy-ineligible
rows, duplicate manifest identities, dropped/substituted evaluation deals, unsigned filing rows,
conflicting signed outcomes, retrospective witness timestamps, negated/non-binding agreement
language, edited witness messages, and a later selection filing hidden behind an older candidate
source. The final version validates local source/poll authority before Discord publication and
repeats it in the persistence transaction after the server receipt.

A fresh direct adversarial pass after the first commit found five additional gaps: broader
negated-agreement language could satisfy the positive parser; unrelated option/employee
cash-per-share text could supply the offer; conditional termination boilerplate could become an
outcome; a same-day quote was date-bound but not fresh to the commitment instant; and the Discord
receipt did not bind its channel id. The remediation requires an affirmative target-common-stock
consideration sentence, expands negation and written-fraction stock detection, accepts outcomes
only from unconditional sentence-sized assertions, requires the signed entry quote to precede
the commitment by no more than five minutes, binds publish/read receipts to the configured
channel, and rechecks the final EDGAR poll's CIK refs. Each attack has a regression.

The operator's preferred OpenCode review path was attempted read-only with DeepSeek v4 Pro and
Flash. The agents read the artifact but repeatedly timed out without returning findings or an
attestation; they made no repository edits. That absence is recorded as unavailable external
attestation, not as approval. Normal PR review and the six-job matrix remain mandatory.

## Local gates

Run from `app/` on Windows with `TMP=TEMP=C:\t`, ambient `COCKPIT_*` values cleared, and
`COCKPIT_ENV_FILE=C:\t\cockpit-test-no-env` so the ignored operator `app/.env` could not affect
unconfigured-default tests:

- initial focused semantic/tool hardening: **15 passed**;
- initial expanded W-24/W-23/witness/tool/composition compatibility slice: **77 passed**, 1
  third-party deprecation warning;
- post-adversarial W-24/W-23/witness/tool/composition slice: **50 passed**, 1 third-party
  deprecation warning;
- final full Windows suite: **2,266 passed, 1 skipped**, 5 third-party deprecation warnings, in
  304.35s;
- `ruff check . ../tools/provision_merger_edge.py`: clean; and
- `git diff --check`: clean before documentation reconciliation; rerun before commit.

After BUG-106 merged to `origin/main` in PR #37, W-24 merged that base and reconciled the two
predicted status-document conflicts. The combined tree passed 126 focused EDGAR/W-24/witness/
composition tests, then **2,274 passed / 1 skipped** in the full sanitized Windows suite, with
Ruff clean. Production code merged automatically; only `docs/README.md` and `docs/REGISTER.md`
required manual reconciliation.

The warning set is unchanged third-party FastAPI/Starlette, AnyIO/Starlette, and Python `audioop`
deprecation output; no test warning indicates a W-24 control failure.

## Remaining gates

1. Reviewed PR and green Ubuntu/Windows/macOS × Python 3.11/3.12 matrix.
2. Configure the private audit channel and prove exact-message Discord publish/read retention.
3. Prospectively commit a real complete ≥30-deal cohort and wait for all outcome horizons and
   strict post-horizon EDGAR coverage; retrospective W-23 fixtures cannot satisfy this gate.
4. Complete the field-level parser audit at the pinned precision threshold and prove one current
   eligible target without an override.
5. Close the named RW-14/RW-15 online evidence and two qualifying W-22 unattended market days.

Until all gates pass, use `read_only` only for bring-up and evidence collection. This package does
not authorize paper opening Orders, live capital, `review_live`, `auto_veto`, or `auto_full`.

---
title: W-23 deterministic cash-merger edge — local implementation evidence
type: evidence
status: live
last-verified: 2026-08-14
related:
  - REGISTER
  - research/dec03-strategy-selection-2026-08-14
  - runbooks/w23-cash-merger-edge
---

# W-23 local implementation evidence — 2026-08-14

## Claim boundary

Branch: `work/w23-cash-merger-edge`.
Implementation commit: `641c698`.

This record proves local implementation and Windows regression only. It does **not** prove
strategy profitability, empirical calibration quality, an eligible current deal, cross-platform
behavior, paper-broker execution, or live/autonomous readiness. `COCKPIT_MERGER_WATCHLIST`
remains empty in the operational configuration, no cohort/deal evidence was provisioned, and no
mode or trading authority changed.

## Implemented controls

- immutable, content-hashed deal/calibration/computed-edge SQL records covered by the HMAC audit
  chain;
- separate startup-only merger strategy watchlist; the general awareness watchlist cannot enroll
  a strategy target;
- exact deal/cohort/calibration schemas with US-listed common-equity, cash-only, long-target,
  30-day, liquidity, freshness, filing, quote, and raw-source gates;
- calibration statistics recomputed from the write-once raw cohort using a pinned 95% Wilson
  lower completion probability, empirical non-close allocation, and worst observed failure/open
  price ratios;
- SEC raw filing hashes, quote Parquet, and daily-bar-derived metrics rebound to signed raw
  evidence on every estimate/status check;
- one provider instance shared by proposal draft and both approval-time fresh-risk passes, using
  the resolved persisted quantity and current quote;
- production EDGAR adapter atomically signs every newly ingested `Filing` row/raw hash through
  the vendored poller's supported callback, so deleting an amendment cannot restore old
  eligibility;
- read-only-only operator provisioning CLI and the activation runbook; and
- no broker import, model-return field, live mode, autonomous mode, or direct order route in the
  provider.

## Adversarial attack → fix pass

The post-implementation diff attack constructed and fixed three concrete defects:

1. A hash-bound calibration could claim probabilities that did not match its raw cohort, and an
   in-memory ADV value was not independently rebound to raw bars. The provider now recomputes the
   pinned conservative calibration and metrics from verified Parquet.
2. A newly detected amendment existed only as a mutable `Filing` row; deleting it could make the
   provider fall back to older reviewed state. Production ingestion now writes an atomic signed
   `filing_ingested` event, and the provider refuses a missing/altered signed row or raw blob.
3. The first fix touched pinned vendored source. The full suite's provenance test rejected it; the
   hook was moved to `ingest/edgar_pipeline.py`, the vendored file restored byte-for-byte, and a
   dedicated adapter reachability test added.

Attack regressions cover model-authored returns, symbol/side/asset/horizon/proposal substitution,
ordinary-watchlist expansion, signed-row/raw/cohort/metric tamper, quote substitution and movement,
termination, unreviewed new filings, signed filing deletion, weak precision, calibration mismatch,
and restart/idempotency.

This was an author-run fresh attack/fix round. A fresh independent reviewer/model round remains a
required acceptance gate; this evidence does not relabel self-review as independent review.

## Local gates

Run from `app/` on Windows with `TMP=TEMP=C:\t` and `COCKPIT_ENV_FILE` pointed at a nonexistent
test path so the operator's ignored `app/.env` could not affect defaults:

- focused filing/provenance/strategy regression after the final adapter change: 71 passed;
- final full suite: **2,203 passed, 1 skipped**, 5 third-party deprecation warnings;
- `ruff check .`: clean;
- `ruff check ../tools/provision_merger_edge.py`: clean; and
- `git diff --check`: clean.

## Operational soak isolation

The detached W-22 soak was not edited, restarted, or redeployed. At the post-close observation it
was still running from its pinned revision in `read_only`, audit-valid at head 4052, not
watch-only, and paper-performance-valid. All 11 symbols retained quote/metric cache entries; six
quotes were faulted after the 16:00 ET close, so that snapshot is not claimed as green live-market
evidence.

## Remaining gates

1. Fresh independent adversarial review with zero reproducible authority/evidence bypasses.
2. Required Windows/Ubuntu/macOS CI matrix.
3. Real, reviewed, survivorship-safe point-in-time cohort meeting the schema/precision gates.
4. A current eligible definitive cash deal whose SEC records and listing identity verify.
5. Separate configuration of the target in both watchlists, a fresh post-review quote/bar poll,
   and operator validation of `/quant/status`.
6. Separate deliberate audited `paper` activation and manual approval; then W-22's two qualifying
   market days at least a week apart.

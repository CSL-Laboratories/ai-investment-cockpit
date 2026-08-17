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
PR #36 head reviewed: `5115e2e5dae585298e2414268f30722f1adb5ee1`.
Second-review remediation: uncommitted working-tree diff based on that head; the operator has
not requested a commit or push.

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
- calibration statistics recomputed from a temporally split write-once raw cohort using a pinned
  95% Wilson lower completion probability, empirical non-close allocation, and worst observed
  failure/open price ratios; each row has four distinct signed source artifacts for selection
  filing, entry price, later outcome filing, and outcome price;
- SEC raw filing hashes, quote Parquet, and daily-bar-derived metrics rebound to signed raw
  evidence on every estimate/status check;
- source filings and symbol/CIK attribution required to resolve to their original signed ingest
  events; an unsigned pre-seeded row/raw tuple cannot be promoted by an operator deal record;
- pre-existing market-data files accepted only with an earlier verified audit binding, with
  atomic create-if-absent publication across the supported operating systems;
- audit verification, signed-event selection, and covered domain-row reads pinned to one
  database snapshot;
- paper-performance chain verification and covered-event reconstruction pinned to the same
  database transaction;
- authority-bearing Parquet snapshots opened once, parsed from that in-memory image, and hashed
  against the exact records returned to the caller;
- exact 30-day open calibration labels and resolved-within-30-day outcomes, preventing
  same-day/arbitrary-horizon leakage;
- quote, book, and quote-raw identity published and consumed as one observation, and the exact
  ADV/observation/realized-volatility object rebound to signed bars before W-21 consumes it;
- one provider instance shared by proposal draft and both approval-time fresh-risk passes, using
  the resolved persisted quantity and current quote;
- production EDGAR adapter atomically signs every newly ingested `Filing` row/raw hash through
  the vendored poller's supported callback, so deleting an amendment cannot restore old
  eligibility;
- successful EDGAR polls emit an audited per-CIK completion watermark; stale or failed polls
  enter audited `watch_only`, and deal/order checks require a fresh watermark that covers the
  current signed filing head;
- completion watermarks are scoped to the current process; mutable EDGAR baselines are verified
  against their full signed transition history before use/mutation/completion, and unsupported or
  malformed post-boundary submissions rows for merger CIKs deny completion;
- a bounded exact-accession SEC import recovers a requested filing from the current submissions
  document or one of at most 20 declared historical shards, while preserving write-once bytes and
  atomically repairing only a verified legacy unsigned binding;
- deal authority binds the complete target filing set, and the provider rechecks calibration,
  filing/poll authority, and audit integrity inside the transaction that persists the edge;
- the production semantic-evidence gate is deliberately closed with no constructor/configuration
  bypass: source-bound research rows cannot create executable authority until a separate reviewed
  verifier proves universe completeness, pre-outcome commitment, labels, and deal terms;
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

## First independent PR #36 adversarial review and remediation

A fresh reviewer with no implementation role reviewed the complete PR diff against `main` and
verified all nine invariants. The review found six reproducible defects and recommended against
merge: pre-seeded market files could be self-signed, unsigned filing rows/raw blobs could be
promoted into deal authority, calibration accepted wrong-horizon open labels, cached volatility
was not rebound to raw bars, audit verification and covered-row reads had a snapshot race, and
the order path could combine quote and book observations from different polls.

This remediation adds a failing regression for every exploit before the fix and closes each
boundary as described above. The independent review is now a completed discovery gate; these
post-review changes still require the updated cross-platform CI matrix and should receive normal
PR review before merge.

## Second independent PR #36 adversarial review and remediation

The requested second fresh reviewer had no implementation role and attacked the complete
`main...5115e2e` diff. Its actionable findings were reproduced before remediation:

1. A first-contact EDGAR baseline could omit an already-public merger filing indefinitely, with
   no supported way to establish the signed legacy binding needed by deal provisioning.
2. Filing recency was inferred from deal review and filing state rather than a recent successful
   per-CIK SEC poll, so an outage could leave apparently current authority available.
3. Calibration rows were hash-bound but their eligibility claims, outcome labels, prices, and
   temporal split were not mechanically rebound to independent signed raw sources; operator
   counters could substitute for held-out measurement.
4. Paper-performance audit verification and event reconstruction used separate database
   snapshots, permitting a verify/use interleaving against mutable storage.
5. An advancing clock could make a newly computed edge appear future-dated to the later proposal
   verification step.
6. The final edge persistence boundary did not prove that the filing set and calibration
   authority were unchanged throughout evaluation, and the real provider-to-gateway composition
   lacked an attack regression for a late filing between approval checks.

The working-tree remediation adds bounded exact historical import, audited EDGAR completion
watermarks with fail-closed orchestration, source-bound v2 calibration with a temporal held-out
split, same-transaction audit reads, final-transaction authority rechecks, a final clock sample,
and composed provider → approval → gateway race tests. It also hashes the full target `Filing`
domain so an unsigned insertion cannot hide outside the signed-event set. No strategy cohort or
deal has been activated.

## Post-remediation independent adversarial gate

A third fresh reviewer, again read-only and with no implementation role, reviewed the complete
working-tree artifact after the second-review fixes. It found seven additional blockers and
recommended against merge: skipped/malformed SEC forms could receive freshness, mutable baselines
could be laundered, calibration remained self-labelled/cherry-pickable, deal terms lacked semantic
filing bindings, Parquet verification reopened paths, restarts could reuse a prior-process EDGAR
watermark, and the historical importer used local detection time for historical chronology.

The follow-up remediation closes the executable paths as follows:

1. Merger-scope post-boundary rows that the deterministic parser cannot fully support now block
   baseline movement and completion; the adapter also binds the response-body CIK to the requested
   submissions URL and requires equal parallel arrays, unique valid accessions, exactly one saved
   cutoff, and newest-first filing/acceptance chronology. Adapter and composed-order regressions
   cover unsupported/malformed rows, CIK-identity substitution, and duplicate-cutoff ambiguity.
2. Every baseline load/mutation/completion verifies the mutable row against its full signed
   establishment/advance/touch history. A separate SQLite transaction that substitutes the row
   during an active poll cannot receive a completion watermark.
3. Calibration source dates and outcome prices remain mechanically checked, but production does
   not treat those operator-authored labels as semantic authority. There is no runtime switch;
   an independently reviewed universe/precommit/ground-truth verifier is a future code gate.
4. For the same reason, operator-authored cash offer, agreement, lifecycle, and exclusion flags
   remain research artifacts and cannot generate a production edge until a field-level semantic
   verifier exists.
5. Calibration, quote, bar, and grounding reads use one `read_verified_snapshot` open/image, so a
   rename after open cannot substitute the records consumed.
6. A random per-process epoch is signed into EDGAR completion and required by every deal/order
   verification. A known-unprovisioned restart therefore cannot reuse a previous process's poll.
7. Filing ingest events now bind the SEC filing date separately from honest local detection time;
   historical calibration chronology uses the former, so bounded re-fetch remains provisionable.

During this work, a separate medium operator-tool defect was also found: historical filing import
committed successfully and then tried to print a nonexistent `Filing.content_hash`. Reporting now
uses the write-once raw-store hash and has a direct regression.

The same independent reviewer then attacked the fully remediated working tree and reproduced two
additional high-severity malformed-feed variants: response-body CIK substitution and a duplicate
saved cutoff hiding a newer unsupported row. The URL/body identity binding and structural/ordering
checks described in item 1 close both variants. Its final read-only verdict found no remaining code
blocker, marked all nine canonical invariants pass, and independently reproduced the final full
suite result below. Its merge recommendation is code-approved only after this working tree is
committed/pushed and the Windows/Ubuntu/macOS CI matrix is green; W-23 activation remains blocked.

## Local gates

Run from `app/` on Windows with `TMP=TEMP=C:\t` and `COCKPIT_ENV_FILE` pointed at a nonexistent
test path so the operator's ignored `app/.env` could not affect defaults:

- first-review focused remediation regression across market data, merger evidence, audit,
  workflow, and paper execution: 87 passed;
- expanded ingest/workflow/broker/strategy/storage regression before the final supersession
  hardening: 898 passed; that hardening is covered by the final full suite;
- second-review affected regression suite: 117 passed;
- pre-gate full suite after the second-review remediation: **2,224 passed, 1 skipped**,
  5 third-party deprecation warnings;
- latest post-gate affected regression across strategy, EDGAR, composition, audit, storage, and
  operator tooling: 118 passed, 1 third-party deprecation warning;
- final Windows full suite after the malformed-feed identity and boundary hardening:
  **2,238 passed, 1 skipped**, 5 third-party deprecation warnings;
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

1. Required Windows/Ubuntu/macOS CI matrix on the remediation commit.
2. Separately scoped implementation and adversarial review of an independent semantic verifier
   for complete-universe selection, pre-outcome predictions, ground-truth outcomes, and cited deal
   terms. W-23 is production-unavailable until then.
3. Real, independently reviewed, survivorship-safe point-in-time cohort meeting that verifier and
   the schema/precision gates.
4. A current eligible definitive cash deal whose terms, SEC records, and listing identity verify.
5. Only after those gates: separate configuration of both watchlists, fresh current-process
   EDGAR/quote/bar polls, operator validation, deliberate audited `paper` activation, and W-22's
   two qualifying market days at least a week apart.

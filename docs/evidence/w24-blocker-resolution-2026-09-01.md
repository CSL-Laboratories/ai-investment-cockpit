---
title: W-24 blocker resolution — unsupported absolute SEC heads
type: evidence
status: selection-blocked-fail-closed
last-verified: 2026-09-01
related:
  - runbooks/w24-merger-semantic-verifier
  - REGISTER
  - /workspace/w24-evidence/w24-prospective-cohort-intake-2026-09-01.md
---

# W-24 blocker resolution — 2026-09-01

## Decision

No production-code change and no branch are warranted for this intake. The smallest safe path is
to wait for a fresh absolute SEC submissions head in the existing supported set `{8-K, 10-Q,
10-K}`. Keep the soak at `1e2d4e3ca7c2fcae4e320ce5f72b44905b036ee4`, keep
`COCKPIT_MERGER_WATCHLIST` empty, and do not run `selection` until a real, operator-approved,
outcome-free universe has at least 30 candidates.

An expansion to Schedule 13E-3, DEF(A)14A, 15-12G, 25-NSE, Form 4, or other forms would be a
separate bounded semantic-ingestion package, not an inline intake unblock. It would need a claimed
scope, source-derived form handling, frozen positive/negative fixtures, adversarial review, and
tests before it could affect W-24 authority. No such package is anticipated as an open W-24 code
row today.

## Intake facts

The GET-only W-24 intake recorded pool `122`, `15` supported absolute heads, and `0` selection-ready
names. The FTS research probe found five announcement accessions whose source text passed the
current eligibility grammar, but none had a supported absolute head and none had both exact dates:

| Ticker | Research announcement accession | Parsed cash/exchange | Absolute head | Selection consequence |
|---|---|---|---|---|
| TMHC | `0001193125-26-249694` | `$72.50`, NYSE | `15-12G` | unsupported current head; wait |
| BOW | `0001628280-26-051540` | `$34.00`, NYSE | `SC 13E3` | unsupported current head; wait |
| ALOT | `0001193125-26-273447` | `$29.00`, Nasdaq | `25-NSE` | unsupported current head; wait |
| VRTX | `0001193125-26-296710` | `$85.00`, Nasdaq | `4` | unsupported current head; wait |
| BZH | `0001104659-26-092299` | `$33.50`, NYSE | `DEFA14A` | unsupported current head; wait |

These values are copied from the dated research artifacts only; they are not hand-entered deal
terms and do not create authority. The source rows also report `expected=null` and `outside=null`.
The prior CRNX candidate is likewise blocked by current head `S-8 POS`.

## Why the five cannot be selection-ready

1. The production SEC processing set is exactly `10-K`, `10-Q`, and `8-K`:
   `app/filings/edgar/submissions_poll.py:19-23,57-60`. The `Filing` enum accepting five values
   is not processing support; the same module says Form 4/13F processing is deferred.
2. The poller skips any row whose form is not in that set at
   `app/filings/edgar/submissions_poll.py:511-514`. The W-24 strict completeness wrapper raises
   on an unsupported post-boundary filing at `app/ingest/edgar_pipeline.py:394-402`, and the
   exact historical `filing` path rejects an unsupported form at `app/ingest/edgar_pipeline.py:794-795`.
3. The selection CLI runs a complete strict EDGAR poll and refuses an incomplete poll at
   `tools/provision_merger_edge.py:77-110`. Its pipeline uses the default `SubmissionsPoller`
   processing set at `app/ingest/edgar_pipeline.py:1244-1256`.
4. W-24 requires `source_accessions` to begin with the semantic source and end with the current
   accession at `app/strategies/merger_semantics.py:295-310`, requires a complete signed filing set
   through that current source at `:423-450`, and binds the current accession to the audited EDGAR
   baseline at `:513-532`. An older research 8-K cannot replace a newer unsupported absolute head.
5. The runbook states the operational result directly: a latest unsupported SEC form fails closed
   and the W-24 repair does not broaden the ingestion form set
   (`docs/runbooks/w24-merger-semantic-verifier.md:109-116`).
6. Independently, the five FTS rows are not current-target-ready because the date parser accepts
   only the pinned exact forms at `app/strategies/merger_semantics.py:142-146`, and deal verification
   requires one unambiguous expected-resolution date and outside date at `:696-707`. The research
   artifact reports both as absent for all five.

## Exact next steps

1. Agents may continue GET-only discovery, absolute-head checks, parser probes, and status reads in
   `read_only`; they must preserve raw-vs-summary separation and write only outside the soak evidence
   boundary.
2. Wait for each candidate's actual absolute submissions head to be in `{8-K, 10-Q, 10-K}`. Do not
   select an older supported announcement while a newer unsupported form is current.
3. Lucas must supply or approve a single-date, declared research universe of at least 30 unique
   pending US cash-merger targets after CIK mapping, absolute-head filtering, semantic parsing, and
   exact-date checks. Discovery names and awareness tickers are not authority.
4. After Lucas approves that universe, and only while audited `read_only`, the bounded operator
   workflow may re-fetch/sign every supported source accession, capture fresh quote evidence, and
   run `provision_merger_edge.py selection`. The command must produce the Discord witness and
   `msel_...` commitment; no hand-entered terms, dates, labels, or outcomes are permitted.
5. Keep the merger watchlist empty until the witnessed cohort, 30-day outcome horizon, calibration,
   held-out precision, and one current target with source-derived terms and exact dates all pass.
6. If Lucas elects to broaden forms later, open a separate bounded package first. Do not change
   `PROCESS_FORMS`, the absolute-head screen, or current-baseline authority as an intake workaround.

## Human/operator versus agent boundary

Agents can perform GET-only screens/probes, verify code and evidence, prepare source-derived
outcome-free candidate data, and—after explicit Lucas approval—execute the already-bounded
read-only filing/quote/selection workflow and report failures. Agents cannot invent missing terms or
dates, choose a live cohort for Lucas, post a witness without that approval, seed
`COCKPIT_MERGER_WATCHLIST`, arm paper/live mode, or bypass risk/mode/approval/`OrderGateway`.

Lucas must approve the prospective universe and selection date, authorize the signed backfills,
fresh quote capture, and witnessed selection, and make any separate product decision to scope a
future form-set package. Lucas also remains the human gate for any watchlist change or paper arming.

## Safety result

No soak files, database rows, raw filings, quotes, Discord selection message, watchlist, proposal,
risk decision, approval, or order were created by this resolution. The fail-closed state is correct:
expected edge unavailable, order authority none.

## Evidence

- `/workspace/w24-evidence/w24-prospective-cohort-intake-2026-09-01.md`
- `/workspace/w24-evidence/w24-fts-accession-parser-probe-2026-09-01.json`
- `/workspace/w24-evidence/w24-prospective-intake-screen-v2-2026-09-01.json`
- `/workspace/w24-evidence/intake-screen-v2-run.log`
- Focused repository regression: `27 passed` (`app/tests/tools/test_provision_merger_edge.py`,
  `app/tests/filings/test_wp3_poll.py`).

Publication note: the requested external destination
`/workspace/w24-evidence/w24-blocker-resolution-2026-09-01.md` is outside this session's writable
root. This copy is in the main clone for Lucas to move or publish into that evidence directory.

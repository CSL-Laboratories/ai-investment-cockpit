---
title: W-24 natural-head GET-only screen — 2026-09-15 Tuesday (operator seal)
type: evidence
status: rescreen-complete-selection-blocked
last-verified: 2026-09-15
related:
  - REGISTER (W-24 / W-23 / RW-37)
  - evidence/w24-natural-head-rescreen-2026-09-15
claimant: operator sentry / Grok Bot soak
operator_main_head: 8beb271dbe8ca6adf90c25458fc7dfcb787120b7
soak_sha_observed_live: 5d50b25194c14beaac7bb979dcd559cc21a2fb95
parser_blob_sha: 9c5efc6277085ff190784036ca4f0010a831d762
raw_packet_status: cited_summary_only
sealed_as: 2026-09-15-tue
---

# W-24 natural-head GET-only screen — 2026-09-15 Tuesday

Operator-pasted seal after a second GET-only natural-head screen on the
same calendar date as the Monday-evening board. Raw JSON/packets stay
on the soak host. This note cites the Tuesday seal. It does not ingest
live `data/` packets, seed a watchlist, authorize labelling, or claim
empirical PASS. It does not mutate the Monday-evening note.

## Claim

| Field | Value |
|---|---|
| Package | `W-24` (counts) / `RW-37` (in-repo ledger) |
| Claim action | Fresh GET-only natural-head screen, same method as 2026-09-15 Monday-evening |
| Operator `main` tip | `8beb271dbe8ca6adf90c25458fc7dfcb787120b7` |
| Soak SHA observed live | `5d50b25194c14beaac7bb979dcd559cc21a2fb95` |
| Parser blob | `9c5efc6277085ff190784036ca4f0010a831d762` (`merger_semantics.py` on soak clone) |
| Window PT | 2026-09-15 09:10:44–09:13:34 PDT |
| Stamp | `2026-09-15-tue` |
| Selection / Discord / watchlist / soak HEAD rewrite | **Not** done |
| Raw packet status | `cited_summary_only` |

## Counts (seal)

| Metric | 2026-09-15 Tue | 2026-09-15 Mon-evening |
|---|---|---|
| Union pool | **199** | 199 |
| Supported `{8-K,10-Q,10-K}` | **32** | 31 |
| Cash-per-share parse hits | **0** | 1 (BOW) |
| Selection-ready | **0** | 0 |
| Date-ready | **0** | 0 |
| Cohort / ≥30 gate | **0 ≪ 30 / false** | 0 / false |
| `qualify_count` | **0** | 0 |

Versus Monday-evening: **added** CLST, DFH; **dropped** BOW. Cash 1→0.
Dropping BOW is not a labelled outcome.

### Primary gate result

**NO PASS / empty / date-not-ready** — selection-ready 0, date-ready 0,
cohort **0 ≪ 30**. Do **not** lower the 30-identity gate. Zero cash-parse
is not labelled truth.

### Supported symbols (seal)

ACA, AIRE, BKR, CCO, CLST, CYCU, CYCUW, DFH, DIOD, ESI, FSEA, GBCS, GRML,
GRMLW, HUN, JTAI, OLN, PAYO, RLYB, RMBI, SLP, STRR, STRRP, TARS, TBPH,
TXNM, UHS, UUUU, VCTR, VEEE, ZSQR, ZYME.

Cash-parse hit symbols: none.

## Soak-host hashes (not in this clone)

| File | SHA-256 |
|---|---|
| `w24-prospective-intake-natural-head-rescreen-2026-09-15-tue.json` | `2616c36e8549eee26bfd41b2a4f1944959c4ff67ca7bf51047af66c9058103d5` |
| `discovery-pool-rescreen-2026-09-15-tue.json` | `b962cd97a4b6c4d565f29f09b13a297632aaf9e40945ffe20b5ebc0d6f4d6953` |
| `intake-natural-head-rescreen-2026-09-15-tue.log` | `f765e9e7bc5c997daf6aacb4545d52465f07ed315fd2e80cc4fb77682ae1dbab` |
| `w24-natural-head-rescreen-2026-09-15-tue.md` | `ba88d38d90efc73a16bad43c1ee445af8fdf45b38d23a7fc33d3eccb77aff1eb` |

Those bytes stay on the soak host. This git note is a cited summary.

## Exact next operator action

Keep GET-only natural-head screens. Do **not** seed
`COCKPIT_MERGER_WATCHLIST`, copy Monday's BOW hit forward, label, or
`/paper approve`. Wait until selection-ready ≥30 **and** date-ready.

---
title: W-24 natural-head GET-only screen — 2026-09-15 (operator seal)
type: evidence
status: rescreen-complete-selection-blocked
last-verified: 2026-09-15
related:
  - REGISTER (W-24 / W-23 / RW-36)
  - evidence/w24-natural-head-rescreen-2026-09-12
claimant: operator sentry / Grok Bot soak
operator_main_head: 8beb271dbe8ca6adf90c25458fc7dfcb787120b7
soak_sha_observed_live: 5d50b25194c14beaac7bb979dcd559cc21a2fb95
parser_blob_sha: 9c5efc6277085ff190784036ca4f0010a831d762
raw_packet_status: cited_summary_only
---

# W-24 natural-head GET-only screen — 2026-09-15

Operator-pasted seal after a fresh GET-only natural-head screen on merged
`main` `8beb271` (PR #97). Raw JSON/packets stay on the soak host. This note
cites the seal. It does not ingest live `data/` packets, seed a watchlist,
authorize labelling, or claim empirical PASS.

## Claim

| Field | Value |
|---|---|
| Package | `W-24` (counts) / `RW-36` (in-repo ledger) |
| Claim action | Fresh GET-only natural-head screen, same method as 2026-09-12 |
| Operator `main` tip | `8beb271dbe8ca6adf90c25458fc7dfcb787120b7` |
| Soak SHA observed live | `5d50b25194c14beaac7bb979dcd559cc21a2fb95` |
| Parser blob | `9c5efc6277085ff190784036ca4f0010a831d762` (`merger_semantics.py` on soak clone) |
| Window PT | 2026-09-14 21:45:23–21:47:42 PDT |
| Selection / Discord / watchlist / soak HEAD rewrite | **Not** done |
| Raw packet status | `cited_summary_only` |

## Counts (seal)

| Metric | 2026-09-15 | 2026-09-12 |
|---|---|---|
| Union pool | **199** | 197 |
| Supported `{8-K,10-Q,10-K}` | **31** | 32 |
| Cash-per-share parse hits | **1 (BOW)** | 0 |
| Selection-ready | **0** | 0 |
| Date-ready | **0** | 0 |
| Cohort / ≥30 gate | **0 ≪ 30 / false** | 0 / false |
| `qualify_count` | **0** | 0 |

### Primary gate result

**NO PASS / empty / date-not-ready** — selection-ready 0, date-ready 0,
cohort **0 ≪ 30**. First cash hit **BOW** is not eligibility. Do **not**
lower the 30-identity gate.

### Supported symbols (seal)

ACA, AIRE, BKR, BOW, CCO, CYCU, CYCUW, DIOD, ESI, FSEA, GBCS, GRML,
GRMLW, HUN, JTAI, OLN, PAYO, RLYB, RMBI, SLP, STRR, STRRP, TARS, TBPH,
TXNM, UHS, UUUU, VCTR, VEEE, ZSQR, ZYME.

Cash-parse hit symbols: **BOW**.

## Soak-host hashes (not in this clone)

| File | SHA-256 |
|---|---|
| `w24-prospective-intake-natural-head-rescreen-2026-09-15.json` | `98a513f6f7c22aaa72d0230d8fcbbabc0840330549723f3cb582ad0155b18d89` |
| `discovery-pool-rescreen-2026-09-15.json` | `25a6f16da374dab63c39d31107d1540f3fd7470bca9fb39bdfd323a8b47deacb` |
| `intake-natural-head-rescreen-2026-09-15.log` | `279601ae4afb42e7bf7061d6fa42a0119435153e0a82cd8a3a0f2ab2ea950989` |
| `w24-natural-head-rescreen-2026-09-15.md` | `7e4bf059f6d37e263e578bd5a78aad237672d194adb07938ce36d890b01c0772` |

Those bytes stay on the soak host. This git note is a cited summary.

## Exact next operator action

Keep GET-only natural-head screens. Do **not** seed
`COCKPIT_MERGER_WATCHLIST`, treat BOW as a launch, label cash hits, or
`/paper approve`. Wait until selection-ready ≥30 **and** date-ready.

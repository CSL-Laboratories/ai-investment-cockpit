---
title: RW-36 slice A 2026-09-15 GET-only screen intake
status: reference
last-verified: 2026-09-15
amends:
  - REGISTER
---

# RW-36 — third GET-only screen row (cohort/label plan only)

**Claim:** `RW-36` on `cursor/rw36-screen-intake-7968` from `origin/main`
(`8beb271`, merged RW-34/RW-35 / PR #97).
**Pipeline:** Cloud Head implementation from the operator-pasted
2026-09-15 seal. **No empirical PASS.** No activation. No order-path
edit. No new data feed. Labelling is **not** authorized. Measurement is
**not started.** RW-32 remains the remote seal package and is not reused.

RW-31 Head steer F-03: a later screen with different counts needs a new
claim, not a loosened two-row freeze. Supported heads moved 32 → 31
and cash-parse hits moved 0 → 1 (BOW). That is this claim.

## Intake hashes

| Kind | Path | SHA-256 |
|---|---|---|
| freeze (unchanged) | `app/feasibility/freeze.v1.json` | `26f34dba3fd3a75732d136e88a6280dd313c5ffc01886f79e73ab1c8c56eb2d8` |
| evaluation (unchanged) | `app/feasibility/evaluation.v1.json` | `665f72b426efff7a7411d14816e50867cccb8ee26f03e3bd5dc18056a5a0ab31` |
| cases (unchanged) | `app/feasibility/cases.v1.json` | `1f4f90a8dd03bc0970f8526bc951f425e46d4a0393bb2a6f74ce2f365bf3be41` |
| protocol (unchanged) | `app/feasibility/protocol.v1.json` | `a7bb528863b0a43073f1fbfbb91b6145fa6b6d85018a4dd5828c79ef76b6c528` |
| Sep 9 evidence note | `docs/evidence/w24-natural-head-rescreen-2026-09-09.md` | `427f5b9cf63b8071ad000a77ad59108bec77f7010ee6f8dfb95fa5379abd315b` |
| Sep 12 board note | `docs/evidence/w24-natural-head-rescreen-2026-09-12.md` | `9007f8bcfab9c8bf901b81e8584bd1174f17ab0fd13c67b88a0bca273ee0ef0c` |
| Sep 15 cited note | `docs/evidence/w24-natural-head-rescreen-2026-09-15.md` | `360095f9e18a84c23f4005cf3c3af5cdb4b92efe27f5914aa21cdb303549f95d` |

Soak-host bytes stay off this clone. The operator seal cited:

| Soak-host file | SHA-256 |
|---|---|
| `w24-prospective-intake-natural-head-rescreen-2026-09-15.json` | `98a513f6f7c22aaa72d0230d8fcbbabc0840330549723f3cb582ad0155b18d89` |
| `discovery-pool-rescreen-2026-09-15.json` | `25a6f16da374dab63c39d31107d1540f3fd7470bca9fb39bdfd323a8b47deacb` |
| `intake-natural-head-rescreen-2026-09-15.log` | `279601ae4afb42e7bf7061d6fa42a0119435153e0a82cd8a3a0f2ab2ea950989` |
| `w24-natural-head-rescreen-2026-09-15.md` | `7e4bf059f6d37e263e578bd5a78aad237672d194adb07938ce36d890b01c0772` |

`empirical_pass_claimed` is false. `qualify_count` stays 0.

## Why this packet

Grok Bot ran a fresh GET-only W-24 natural-head screen on merged `main`
`8beb271`. Soak SHA observed live: `5d50b25`. Result: **NO PASS /
empty / date-not-ready** — pool 199, supported 31, cash 1 (BOW),
selection-ready 0, date-ready 0, cohort 0≪30. Empty is honest. Product
§A: if fewer than two qualify, deliver useful research and do **not**
lower a gate.

The first cash-parse hit is **BOW**. That is not selection-ready, not
date-ready, not a strategy cohort, and not a watchlist seed.

## Ledger

| Screen | Provenance | Pool | Supported | Cash | Selection-ready | Date-ready | Cohort gate |
|---|---|---|---|---|---|---|---|
| `w24_2026_09_09` | checked-in evidence note | 195 | 32 | 0 | 0 | 0 | false |
| `w24_2026_09_12` | operator board | 197 | 32 | 0 | 0 | 0 | false |
| `w24_2026_09_15` | operator board | 199 | 31 | 1 (BOW) | 0 | 0 | false |

The 2026-09-12 row still does not invent a supported-head list or soak
SHA. The 2026-09-15 row cites the 31 symbols and soak SHA from the seal.

## Cohort / label plan (not authorization)

- Cohort rule stays **30 unique eligible identities on one selection date**.
- Next owner action: more GET-only natural-head screens. Do not seed
  `COCKPIT_MERGER_WATCHLIST` or post Discord selection.
- 31 supported heads are a **candidate extraction-refusal set**, not a
  cash-merger strategy cohort.
- One cash-parse hit is **not** labelled truth. Labelling stays unauthorized
  until a later claim after hashed raw filings are sealed.

## What this does not do

- Does not start measurement, labelling, or a time study
- Does not invent Sharpe/hit-rate cutoffs
- Does not mutate freeze/metrics/evaluation/cases/protocol
- Does not authorize a NAV feed or ingest live `data/` packets
- Does not select launch strategies or activate W-23
- Does not claim empirical PASS
- Does not treat BOW or the 31 supported heads as a passing cohort
- Does not lower the 30-identity gate

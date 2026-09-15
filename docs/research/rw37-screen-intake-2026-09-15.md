---
title: RW-37 slice A 2026-09-15 Tuesday GET-only screen intake
status: reference
last-verified: 2026-09-15
amends:
  - REGISTER
---

# RW-37 — fourth GET-only screen row (same calendar date as RW-36)

**Claim:** `RW-37` on `cursor/rw37-screen-intake-7968` stacked on
`cursor/rw36-screen-intake-7968` (`9bf9f1a`).
**Pipeline:** Cloud Head implementation from the operator-pasted
2026-09-15 Tuesday seal. **No empirical PASS.** No activation. No
order-path edit. Labelling is **not** authorized. Measurement is
**not started.** RW-32 remains the remote seal package and is not reused.

RW-31 Head steer F-03: a later screen with different counts needs a new
claim. Versus the Monday-evening RW-36 row: supported 31 → 32, cash 1
(BOW) → 0, BOW dropped, CLST and DFH added. That is this claim. The
Monday row stays frozen.

## Intake hashes

| Kind | Path | SHA-256 |
|---|---|---|
| freeze (unchanged) | `app/feasibility/freeze.v1.json` | `26f34dba3fd3a75732d136e88a6280dd313c5ffc01886f79e73ab1c8c56eb2d8` |
| evaluation (unchanged) | `app/feasibility/evaluation.v1.json` | `665f72b426efff7a7411d14816e50867cccb8ee26f03e3bd5dc18056a5a0ab31` |
| cases (unchanged) | `app/feasibility/cases.v1.json` | `1f4f90a8dd03bc0970f8526bc951f425e46d4a0393bb2a6f74ce2f365bf3be41` |
| protocol (unchanged) | `app/feasibility/protocol.v1.json` | `a7bb528863b0a43073f1fbfbb91b6145fa6b6d85018a4dd5828c79ef76b6c528` |
| Sep 15 Mon note (unchanged) | `docs/evidence/w24-natural-head-rescreen-2026-09-15.md` | `360095f9e18a84c23f4005cf3c3af5cdb4b92efe27f5914aa21cdb303549f95d` |
| Sep 15 Tue cited note | `docs/evidence/w24-natural-head-rescreen-2026-09-15-tue.md` | `69be6f1fe54b0a77552cc23b02a95494a28813762ccf1b105d60a164477e60a4` |

Soak-host bytes stay off this clone. The operator seal cited:

| Soak-host file | SHA-256 |
|---|---|
| `w24-prospective-intake-natural-head-rescreen-2026-09-15-tue.json` | `2616c36e8549eee26bfd41b2a4f1944959c4ff67ca7bf51047af66c9058103d5` |
| `discovery-pool-rescreen-2026-09-15-tue.json` | `b962cd97a4b6c4d565f29f09b13a297632aaf9e40945ffe20b5ebc0d6f4d6953` |
| `intake-natural-head-rescreen-2026-09-15-tue.log` | `f765e9e7bc5c997daf6aacb4545d52465f07ed315fd2e80cc4fb77682ae1dbab` |
| `w24-natural-head-rescreen-2026-09-15-tue.md` | `ba88d38d90efc73a16bad43c1ee445af8fdf45b38d23a7fc33d3eccb77aff1eb` |

`empirical_pass_claimed` is false. `qualify_count` stays 0.

## Why this packet

Grok Bot ran a second GET-only W-24 natural-head screen on the same
`main` tip `8beb271`, soak `5d50b25`, window 2026-09-15 09:10:44–09:13:34
PDT. Result: **NO PASS / empty / date-not-ready** — pool 199, supported
32, cash 0, selection-ready 0, date-ready 0, cohort 0≪30.

Monday's BOW cash hit is gone from the supported set. That is not a
labelled outcome and is not eligibility.

## Ledger

| Screen | Provenance | Pool | Supported | Cash | Selection-ready | Date-ready | Cohort gate |
|---|---|---|---|---|---|---|---|
| `w24_2026_09_09` | checked-in evidence note | 195 | 32 | 0 | 0 | 0 | false |
| `w24_2026_09_12` | operator board | 197 | 32 | 0 | 0 | 0 | false |
| `w24_2026_09_15` | operator board | 199 | 31 | 1 (BOW) | 0 | 0 | false |
| `w24_2026_09_15_tue` | operator board | 199 | 32 | 0 | 0 | 0 | false |

## What this does not do

- Does not start measurement, labelling, or a time study
- Does not mutate freeze/metrics/evaluation/cases/protocol
- Does not overwrite the Monday BOW row
- Does not seed `COCKPIT_MERGER_WATCHLIST` or post Discord selection
- Does not claim empirical PASS
- Does not lower the 30-identity gate

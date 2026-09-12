---
title: RW-31 slice A screen intake and cohort/label plan
status: reference
last-verified: 2026-09-12
amends:
  - REGISTER
---

# RW-31 — GET-only screen ledger (cohort/label plan only)

**Claim:** `RW-31` on `cursor/rw31-screen-intake-df89` from
`origin/main` (`885d33a`, merged RW-30 / PR #92).
**Pipeline:** Head implementation → SWE-2 Medium first-gate → jsonschema
→ Muse Spark 1.3 medium → Head steer. Filing bodies gitignored under
`.swarm-runs/rw31-2026-09-12/`. **No empirical PASS.** No activation.
No order-path edit. No new data feed. Labelling is **not** authorized.
Measurement is **not started**.

## Intake hashes

| Kind | Path | SHA-256 |
|---|---|---|
| freeze (unchanged) | `app/feasibility/freeze.v1.json` | `26f34dba3fd3a75732d136e88a6280dd313c5ffc01886f79e73ab1c8c56eb2d8` |
| evaluation (unchanged) | `app/feasibility/evaluation.v1.json` | `665f72b426efff7a7411d14816e50867cccb8ee26f03e3bd5dc18056a5a0ab31` |
| cases (unchanged) | `app/feasibility/cases.v1.json` | `1f4f90a8dd03bc0970f8526bc951f425e46d4a0393bb2a6f74ce2f365bf3be41` |
| protocol (unchanged) | `app/feasibility/protocol.v1.json` | `a7bb528863b0a43073f1fbfbb91b6145fa6b6d85018a4dd5828c79ef76b6c528` |
| Sep 9 evidence note | `docs/evidence/w24-natural-head-rescreen-2026-09-09.md` | `427f5b9cf63b8071ad000a77ad59108bec77f7010ee6f8dfb95fa5379abd315b` |
| Sep 12 board note | `docs/evidence/w24-natural-head-rescreen-2026-09-12.md` | `9007f8bcfab9c8bf901b81e8584bd1174f17ab0fd13c67b88a0bca273ee0ef0c` |
| review filing | `filings/review-screens.json` | `96bca0803e41bac77859558469facfe825033fc9ae5ae000e1c4063142b56bd7` |
| review ATIF | `export/review.atif.json` | `814ed7d56cdc071358fcdc12d8a78f32f46a0a9209da8c32f6955c999cf63248` |
| pre-Muse manifest | `manifest-pre-muse.json` | `651bfb27cfebffdf5afc76ac87800137a77e995facff2acab954207c4b882051` |
| synthesis | `filings/synthesis.json` | `2fbe11425fc2e9a550b259a84b1206aae221fc1704ea4002f9eedd6321b3a3b0` |

Review verdict: `pass-with-dissent` (attempt 1). Muse receipt hash matched.
`empirical_pass_claimed` is false on every filing. Tests: 71 passed in
`app/tests/feasibility/` after Head steer.

## Why this packet

Grok Bot ran a fresh GET-only W-24 natural-head screen on the 2026-09-12
`main` tip (`885d33a`), same method as 2026-09-09. Result: **NO PASS /
empty / date-not-ready** — pool 197, supported 32, cash 0,
selection-ready 0, date-ready 0, cohort 0≪30. Empty is honest. Product
§A: if fewer than two qualify, deliver useful research and do **not**
lower a gate.

The full live JSON/raw packets are on the soak host, not in this clone.
RW-31 records board counts plus the already-checked-in 2026-09-09
note. It does not invent a 2026-09-12 symbol list or soak SHA.

## Ledger

| Screen | Provenance | Pool | Supported | Cash | Selection-ready | Date-ready | Cohort gate |
|---|---|---|---|---|---|---|---|
| `w24_2026_09_09` | checked-in evidence note | 195 | 32 | 0 | 0 | 0 | false |
| `w24_2026_09_12` | operator board | 197 | 32 | 0 | 0 | 0 | false |

## Cohort / label plan (not authorization)

- Cohort rule stays **30 unique eligible identities on one selection date**.
- Next owner action: more GET-only natural-head screens. Do not seed
  `COCKPIT_MERGER_WATCHLIST` or post Discord selection.
- 32 supported heads with cash 0 are a **candidate extraction-refusal
  set**, not a cash-merger strategy cohort.
- Zero cash-parse is **not** labelled truth. Labelling stays unauthorized
  until a later claim after hashed raw filings are sealed. Parser author
  barred; no predictions; second reviewer required. RW-11 is not held-out.

## What this does not do

- Does not start measurement, labelling, or a time study
- Does not invent Sharpe/hit-rate cutoffs
- Does not mutate freeze/metrics/evaluation/cases/protocol
- Does not authorize a NAV feed or ingest live `data/` packets
- Does not select launch strategies or activate W-23
- Does not claim empirical PASS
- Does not treat 32 supported heads as a passing cohort

## Head steer

- **F-02:** keep. Make the per-row `meets_cohort_gate` guard
  unconditional (`this ledger cannot mark a screen cohort-ready`)
  instead of count-conditional. This schema version cannot record a
  passing cohort.
- **F-01:** keep as dissent. The 2026-09-12 row is board attestation;
  raw packets are not in this clone.
- **F-03:** keep. Hard-coding supported_heads 32 is the freeze of
  *this* two-row ledger. A later screen with a different count needs a
  new claim, not a loosened schema.

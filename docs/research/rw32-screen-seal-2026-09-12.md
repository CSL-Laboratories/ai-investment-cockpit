---
title: RW-32 slice A 2026-09-12 screen seal pin
status: reference
last-verified: 2026-09-12
amends:
  - REGISTER
---

# RW-32 — pin the 2026-09-12 operator seal

**Claim:** `RW-32` on `cursor/rw32-sep12-seal-df89` from
`origin/main` (`f24f9a8`, merged RW-31 / PR #94).
**Pipeline:** Head implementation → SWE-2 Medium first-gate → jsonschema
→ Muse Spark 1.3 medium → Head steer. Filing bodies gitignored under
`.swarm-runs/rw32-2026-09-12/`. **No empirical PASS.** No activation.
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
| Sep 12 evidence note | `docs/evidence/w24-natural-head-rescreen-2026-09-12.md` | `71c1f8b03cd673ced206e8e2c8bcfaca6d8b99985e73b9771d9cd67b05882226` |
| Sep 12 seal | `docs/evidence/w24-natural-head-rescreen-2026-09-12.seal.json` | `de131f21fa7ed81f0ff258c9f6bc16ab4d35b47eb0ec35c93b133906125821bc` |
| review filing | `filings/review-screens.json` | pending first-gate |
| review ATIF | `export/review.atif.json` | pending first-gate |
| pre-Muse manifest | `manifest-pre-muse.json` | pending first-gate |
| synthesis | `filings/synthesis.json` | pending first-gate |

## Why this packet

RW-31 refused to invent a 2026-09-12 ticker list. Grok Bot then posted
the soak-box seal. The 32 names **differ** from Sep 9 (added AIRE,
AROW, RLYB, TARS, TBPH, TWOD, VCTR; dropped ATAI, BBBY, GNL, KPLT, KR,
SEI, THFF). Copying Sep 9 would have been a false seal.

Counts are unchanged: pool 197, supported 32, cash 0, selection-ready
0, date-ready 0, cohort 0≪30. Pinning identities does not meet the
cohort gate.

Soak-host markdown SHA-256 `86d7f211…` is **not** the in-repo evidence
note. `parser_blob_sha` `fda5017b…` is recorded as sealed; in this
clone that object is commit BUG-146, not the `885d33a` merger_semantics
blob.

## What this does not do

- Does not start measurement, labelling, or a time study
- Does not ingest raw SEC primary docs or live `data/`
- Does not invent Sharpe/hit-rate cutoffs
- Does not mutate freeze/metrics/evaluation/cases/protocol
- Does not authorize a NAV feed
- Does not select launch strategies or activate W-23
- Does not claim empirical PASS
- Does not treat 32 supported heads as a passing cohort

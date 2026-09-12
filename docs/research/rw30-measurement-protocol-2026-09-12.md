---
title: RW-30 slice A pre-measurement and model-judge protocol
status: reference
last-verified: 2026-09-12
amends:
  - REGISTER
---

# RW-30 — pre-measurement protocol freeze (no measurement)

**Claim:** `RW-30` on `cursor/rw30-measurement-protocol-df89` from
`origin/main` (`4b245ee`, merged RW-29 / PR #91).
**PR:** [#92](https://github.com/NotSkittlz/ai-investment-cockpit/pull/92).
**Pipeline:** Head implementation → SWE-2 Medium first-gate → jsonschema
→ Muse Spark 1.3 medium → Head steer. Filing bodies gitignored under
`.swarm-runs/rw30-2026-09-12/`. **No empirical PASS.** No activation.
No order-path edit. No new data feed. Measurement is **not started**.

## Intake hashes

| Kind | Path | SHA-256 |
|---|---|---|
| freeze (unchanged) | `app/feasibility/freeze.v1.json` | `26f34dba3fd3a75732d136e88a6280dd313c5ffc01886f79e73ab1c8c56eb2d8` |
| evaluation (unchanged) | `app/feasibility/evaluation.v1.json` | `665f72b426efff7a7411d14816e50867cccb8ee26f03e3bd5dc18056a5a0ab31` |
| cases (unchanged) | `app/feasibility/cases.v1.json` | `1f4f90a8dd03bc0970f8526bc951f425e46d4a0393bb2a6f74ce2f365bf3be41` |
| design source | `docs/research/gate-feasibility-and-value-2026-09-06.md` | `8a6484c56ce618282409a51cfc709dc6223724a573b1cc38d21af52f4ad04944` |
| review filing | `filings/review-protocol.json` | `2c265a61956853d63e4acb5ef5ae08e91ca1fa62291302537bc40d55c28d92c5` |
| review ATIF | `export/review.atif.json` | `9bd53a0b0945e38626911615b51c113edc2271485ac170a90e00f5528b227bec` |
| pre-Muse manifest | `manifest-pre-muse.json` | `ade378d33711cfdee1b3b0fb975fd1655dc223fa8cc70d1634b80e8f44e729d9` |
| synthesis | `filings/synthesis.json` | `b3bb4e2aa05eb5f72e746163ce52ac0cfdad433ce6955fa249ef6e54660691a2` |

Review verdict: `pass-with-dissent` (attempt 1). Muse receipt hash matched.
`empirical_pass_claimed` is false on every filing. Tests: 53 passed in
`app/tests/feasibility/` after Head steer.

## Why this packet

Product §A requires provenance, population selection, as-of bounds,
evaluation/tuning separation, threshold declaration, ambiguity treatment,
and independent model-judge rules to be frozen **before** measuring.
This packet records that contract without inventing numeric cutoffs or
collecting data. It is the last in-repo slice A packet that does not need
owner collection, labelling, a feed, or a soak.

## Head steer

- **F-01:** const-pin `held_out_rule` to the frozen sentence.
- **F-02:** pin `design_source_sha256` and fail closed on design-doc drift.
- **F-03:** rename the double-negative flags to
  `later_held_out_excludes_rw11_regressions` and
  `later_held_out_excludes_rw28_evaluation` (both true).

## What this does not do

- Does not start measurement, labelling, or a time study
- Does not invent Sharpe/hit-rate cutoffs
- Does not mutate freeze/metrics/evaluation/cases
- Does not authorize a NAV feed or ingest live `data/` packets
- Does not select launch strategies or activate W-23
- Does not claim empirical PASS

Remaining slice A work needs Lucas: prospective cohort / current target,
independent labels, NAV contract, measurement population, paired time
study, live packet ingest, or any activation.

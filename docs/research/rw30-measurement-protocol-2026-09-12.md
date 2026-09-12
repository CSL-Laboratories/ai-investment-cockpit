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
**Pipeline:** Head implementation → SWE-2 Medium first-gate → jsonschema
→ Muse Spark 1.3 medium → Head steer. Filing bodies gitignored under
`.swarm-runs/rw30-2026-09-12/`. **No empirical PASS.** No activation.
No order-path edit. No new data feed. Measurement is **not started**.

## Why this packet

Product §A requires provenance, population selection, as-of bounds,
evaluation/tuning separation, threshold declaration, ambiguity treatment,
and independent model-judge rules to be frozen **before** measuring.
RW-28 already froze the discrete shortlist classifier. This packet
freezes the *later measurement* contract without inventing numeric
cutoffs or collecting data.

This is the last in-repo slice A packet that does not need owner
collection, labelling, a feed, or a soak.

## Pins (must stay byte-identical)

| Artifact | SHA-256 |
|---|---|
| `freeze.v1.json` | `26f34dba3fd3a75732d136e88a6280dd313c5ffc01886f79e73ab1c8c56eb2d8` |
| `evaluation.v1.json` | `665f72b426efff7a7411d14816e50867cccb8ee26f03e3bd5dc18056a5a0ab31` |
| `cases.v1.json` | `1f4f90a8dd03bc0970f8526bc951f425e46d4a0393bb2a6f74ce2f365bf3be41` |

`qualify_count` stays **0**. `measurement_status` is `not_started`.
`thresholds.numeric_values` is `unset`. `model_judges.status` is
`not_started`. Every `resource_allowance` flag is false.

Design source: `docs/research/gate-feasibility-and-value-2026-09-06.md`.

## Frozen rules (not measurements)

- Cash-merger cohort: 30 unique eligible identities on the same selection
  date, not 30 concurrent orders. Include rejects and dismissals.
- Discovery lists are not authority. Do not reuse an issuer as a new
  trial. Do not quietly insert into the current W-24 manifest.
- Seal the population before inspecting predictions. Outcomes cannot
  retcon eligibility. Right-censoring is required.
- Declare any future numeric thresholds on the tuning partition before
  held-out is opened. Zero denominators are **unmeasured**, never 100%.
  Model confidence is not a threshold. RW-11 regressions and the RW-28
  evaluation are not held-out of a later run.
- Ambiguity fails closed to unverified or watch/reject.
- Model judges: a non-implementer labels from source spans without seeing
  predictions; a second reviewer adjudicates and both originals are
  kept. Different model names are not independence. RW-11 scores are
  not extraction recall.

## What this does not do

- Does not start measurement, labelling, or a time study
- Does not invent Sharpe/hit-rate cutoffs
- Does not mutate freeze/metrics/evaluation/cases
- Does not authorize a NAV feed or ingest live `data/` packets
- Does not select launch strategies or activate W-23
- Does not claim empirical PASS

## First-gate / Muse

Pending at first commit. Reviewer must **write** the filing (not `exec`).
If a tool is rejected, still write the filing from `read` evidence.

---
title: RW-28 slice A metrics freeze and evaluation
status: reference
last-verified: 2026-09-11
amends:
  - REGISTER
---

# RW-28 — Slice A metrics freeze and evaluation (protocol on cited corpus)

**Claim:** `RW-28` on `cursor/rw28-metrics-eval-df89` from `origin/main`
(`179b40d`, merged RW-27 / PR #89).
**PR:** [#90](https://github.com/NotSkittlz/ai-investment-cockpit/pull/90).
**Pipeline:** Head implementation → SWE-2 Medium first-gate → jsonschema
→ Muse Spark 1.3 medium → Head steer. Filing bodies gitignored under
`.swarm-runs/rw28-2026-09-11/`. **No empirical PASS.** No activation.
No order-path edit. No new data feed.

## Intake hashes

| Kind | Path | SHA-256 |
|---|---|---|
| freeze (unchanged) | `app/feasibility/freeze.v1.json` | `26f34dba3fd3a75732d136e88a6280dd313c5ffc01886f79e73ab1c8c56eb2d8` |
| metrics | `app/feasibility/metrics.v1.json` | `ae66d8a4917d77e5593b44d029d98b1f8219adc4425a274957816f0fa7ded963` |
| review attempt 1 ATIF | `export/attempt-1/review.atif.json` | `9c248a12b3f69d7ac6d2799e1ee97c94db389ba6a3c9c2440ff8dcd7f3858dcc` |
| review filing | `filings/review-evaluation.json` | `29f0ff51c09e2d0b02e2677bcaa7e4b31cad2b3623f8e8930b64e49f573757b5` |
| review attempt 2 ATIF | `export/review.atif.json` | `408cb74dbcd7916d94e75323630eb62f1559e69e5230d5d2c34dcbea21c7a2b0` |
| pre-Muse manifest | `manifest-pre-muse.json` | `4bb6c1a4b990cdc41724cd14dc988c399bedc71bfeec26b88b847e2e9360e270` |
| synthesis | `filings/synthesis.json` | `9b7119935bb709087a33731be76b69739f7f5dd98ba0c51379d4e4ea9c36e3aa` |
| evaluation (post Head steer) | `app/feasibility/evaluation.v1.json` | `665f72b426efff7a7411d14816e50867cccb8ee26f03e3bd5dc18056a5a0ab31` |

Review verdict: `pass-with-dissent` (attempt 2). Attempt 1 died when a
rejected `exec` canceled sibling `read`s; the brief was tightened to
read/grep/write only. Muse receipt hash matched. `empirical_pass_claimed`
is false on every filing. Tests: 28 passed in `app/tests/feasibility/`.

The evaluation document SHA-256 after Head steer of RW28-REV-06 is
recorded in git; `validate_evaluation()` requires it equal `apply_metrics()`.

## What this package freezes

| Artifact | Role |
|---|---|
| `app/feasibility/freeze.v1.json` | **Unchanged.** Results stay `unverified`; `metrics_schema_version` stays `null`. |
| `app/feasibility/metrics.v1.json` | Discrete protocol + cited corpus facts. `threshold_kind: discrete_protocol`. Statistical numeric thresholds are `not_applicable_until_authorized_measurement_population`. |
| `app/feasibility/evaluation.v1.json` | Separate evaluation document. Must equal `apply_metrics()`. `qualify_count` is **0**. Carries the protocol fields (Head steer of RW28-REV-06). |
| `app/feasibility/evaluate.py` | Ordered fail-closed `classify()`. No LLM, no broker, no OrderGateway. |

`held_out_rule`: this evaluation applies frozen protocol to already-cited
corpus facts only. It must not be reused as held-out validation of a
later measurement run.

## Verdicts on this corpus

| Candidate | Verdict | Blocking rule |
|---|---|---|
| `cash_merger` | watch | `empirical_evidence_gate_open` |
| `cef_discount` | watch | `data_contract_unauthorized` |
| `mega_cap_signal` | reject | `no_expected_return_model` |
| `awareness_fallback` | reject | `no_edge` |
| `momentum` | reject | `quant_sim_loop` |
| `pairs` | reject | `quant_sim_loop` |

Product §A: if fewer than two qualify, deliver useful research and the
missing evidence; do **not** lower a gate. Zero qualify is the honest
outcome here.

## Why discrete protocol, not invented numbers

Product-delivery §A says statistical thresholds cannot remain unset when
an evaluation *measurement* starts, and also forbids inferring them from
model confidence or tuning after outcomes. This packet is **not** that
measurement run. There is no authorized measurement population (further
data collection needs its own claim). Inventing Sharpe/hit-rate cutoffs
now would be the violation. The frozen thresholds for *this* evaluation
are the `classify()` predicates plus `qualify_requires=present` on the
eight named criteria. Scoring a criterion `present` without that
population is forbidden.

## Head steer

Applied after Muse:

- **RW28-REV-06:** copy `threshold_kind`, `ambiguity_treatment`,
  `statistical_numeric_thresholds`, and `held_out_rule` onto
  `evaluation.v1.json` so a reader of the evaluation alone sees the
  fail-closed / no-held-out-reuse commitments.
- **RW28-REV-05:** keep `MISSING_EVIDENCE` as Head-authored research
  remainder (do not generate prose from flags). Add a fail-closed check
  that every `watch` candidate_id is cited in `missing_evidence`.

## Missing evidence (not a lowered gate)

- `cash_merger`: W-23 still needs a real prospective cohort / current
  eligible target before the empirical evidence gate can close.
- `cef_discount`: daily NAV data contract is not authorized; below-NAV
  is not expected return.

## What this does not do

- Does not mutate `freeze.v1.json`
- Does not select launch strategies
- Does not activate W-23, paper, live, or any auto rung
- Does not authorize a NAV feed or any new data feed
- Does not inspect operational raw packets (later slice A claim)
- Does not claim the DEC-09 matrix or any evidence gate
- Does not claim empirical PASS

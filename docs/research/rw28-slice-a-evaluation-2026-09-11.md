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
**Pipeline:** Head implementation → SWE-2 Medium first-gate → jsonschema
→ Muse Spark 1.3 medium → Head steer. Filing bodies gitignored under
`.swarm-runs/rw28-2026-09-11/`. **No empirical PASS.** No activation.
No order-path edit. No new data feed.

## What this package freezes

| Artifact | Role |
|---|---|
| `app/feasibility/freeze.v1.json` | **Unchanged.** SHA-256 `26f34dba3fd3a75732d136e88a6280dd313c5ffc01886f79e73ab1c8c56eb2d8`. Results stay `unverified`; `metrics_schema_version` stays `null`. |
| `app/feasibility/metrics.v1.json` | Discrete protocol + cited corpus facts. `threshold_kind: discrete_protocol`. Statistical numeric thresholds are `not_applicable_until_authorized_measurement_population`. |
| `app/feasibility/evaluation.v1.json` | Separate evaluation document. Must equal `apply_metrics()`. `qualify_count` is **0**. |
| `app/feasibility/evaluate.py` | Ordered fail-closed `classify()`. No LLM, no broker, no OrderGateway. |

`held_out_rule` on the metrics file: this evaluation applies frozen protocol
to already-cited corpus facts only. It must not be reused as held-out
validation of a later measurement run.

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

## First-gate / Muse

Pending at first commit. Reviewer must **write** the filing (not `exec`).
`--sandbox` is forbidden. `python3` jsonschema via Devin `exec` is
rejected; Head runs jsonschema locally.

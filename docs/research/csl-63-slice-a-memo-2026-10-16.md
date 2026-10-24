---
title: CSL-63 slice A memo — next evidence step after zero qualifying strategies
status: reference
last-verified: 2026-10-16
amends:
  - REGISTER
---

# CSL-63 — Slice A memo after zero qualifying strategies

**To:** Lucas
**Linear:** [CSL-63](https://linear.app/csl-laboratories/issue/CSL-63/week-1-2-slice-a-memo-next-evidence-step-after-zero-qualifying)
**Date:** 2026-10-16
**Claim proposed:** `RW-38` (not authorized by this memo)

`qualify_count` is 0. Two of the six RW-27 names are `watch` and four are
`reject`. Product delivery §A says that when fewer than two strategies
qualify, the useful output is the missing evidence or the scope decision,
and a repeated empty run does not finish the release. This memo is that
output. It leaves every gate where it is.

## Decision requested

Authorize or refuse **`RW-38`**, the proposed daily NAV source contract for
`cef_discount`. The claim text is in [[REGISTER]] under the 2026-10-16
proposal. Authorization is a yes on that text, including the source you
name. Refusal leaves `cef_discount` on `watch`.

`RW-38` is the decision the record can take now. `cash_merger` stays
`watch` until a real same-date prospective cohort and a current date-ready
target exist. The four `reject` names stay `reject` until a new freeze.
This memo does not open that freeze.

## What the four screens established

`app/feasibility/screens.v1.json` (`latest_screen_id`
`w24_2026_09_15_tue`) records four GET-only natural-head boards. Each one
is `no_pass_empty_date_not_ready`. `meets_cohort_gate` is false.
`cef_nav_authorized` is false. `qualify_count` is 0.

| Screen | Claim | Pool | Supported | Cash | Selection-ready | Date-ready | Cohort |
|---|---|---|---|---|---|---|---|
| `w24_2026_09_09` | RW-31 | 195 | 32 | 0 | 0 | 0 | 0 ≪ 30 |
| `w24_2026_09_12` | RW-31 | 197 | 32 | 0 | 0 | 0 | 0 ≪ 30 |
| `w24_2026_09_15` | RW-36 | 199 | 31 | 1 (BOW) | 0 | 0 | 0 ≪ 30 |
| `w24_2026_09_15_tue` | RW-37 | 199 | 32 | 0 | 0 | 0 | 0 ≪ 30 |

Monday's BOW cash-parse hit was not selection-ready and is absent from the
Tuesday supported set (CLST and DFH added, BOW dropped). Supported heads
with no cash-per-share parse remain a candidate extraction-refusal set.
The cohort rule stays 30 unique eligible identities on one selection date.

A later board whose counts differ still needs its own claim (RW-31 F-03).
That row would extend the ledger. It would not close `cash_merger` while
selection-ready and date-ready stay 0, and it would not authorize a NAV
source. RW-29 already records that repeating the parser over unchanged
bytes cannot close the empty selection case.

Digests re-checked 2026-10-16 and unchanged by this memo:

| Packet | SHA-256 |
|---|---|
| `app/feasibility/freeze.v1.json` | `26f34dba3fd3a75732d136e88a6280dd313c5ffc01886f79e73ab1c8c56eb2d8` |
| `app/feasibility/metrics.v1.json` | `ae66d8a4917d77e5593b44d029d98b1f8219adc4425a274957816f0fa7ded963` |
| `app/feasibility/evaluation.v1.json` | `665f72b426efff7a7411d14816e50867cccb8ee26f03e3bd5dc18056a5a0ab31` |
| `app/feasibility/cases.v1.json` | `1f4f90a8dd03bc0970f8526bc951f425e46d4a0393bb2a6f74ce2f365bf3be41` |
| `app/feasibility/protocol.v1.json` | `a7bb528863b0a43073f1fbfbb91b6145fa6b6d85018a4dd5828c79ef76b6c528` |
| `app/feasibility/screens.v1.json` | `933afccdd9bac75949c069ca07dbd1102cd54f5ab2285c0ca9dc1c5a49c01f09` |

Freeze-document verdicts stay `unverified`. Decisive verdicts below are
the RW-28 evaluation (`classify()` on cited corpus facts). The eight
comparison criteria stay `qualify_requires: present` and are unscored:
scoring them present without an authorized measurement population is the
invented threshold RW-28 and RW-30 refuse. `statistical_numeric_thresholds`
stays `not_applicable_until_authorized_measurement_population`.

## Blocking evidence, one row per RW-27 name

Order is the frozen shortlist order.

### 1. `cash_merger` — watch — `empirical_evidence_gate_open`

Cash-only small merger (DEC-03, W-23, W-24). Corpus facts: edge present,
expected-return model present, no separate data contract, empirical gate
**open**. `classify()` therefore stops at watch.

Blocking evidence:

- W-23's empirical evidence gate is open: a real prospective cohort and a
  current eligible target are absent
  ([[evidence/w23-cash-merger-edge-2026-08-14]], REGISTER `W-23`).
- W-24's same-date rule is 30 unique eligible identities and a current
  date-ready target. The four boards above are 0 selection-ready, 0
  date-ready, cohort 0. Earlier cited cases (2026-09-03 BHF dates refused;
  2026-09-04 selection empty; 2026-08-18 cash-parse refuse) are the same
  gate, already classified in `cases.v1.json`.
- Implementation evidence is useful and is not empirical PASS
  (`w23_impl_not_empirical`).
- Independent extraction labels are still missing (RW-29
  `independent_extraction_labels`). Parser regressions are not that
  population. RW-11 text-delta scores cannot substitute.

Evidence that would change the verdict: one selection date with at least
30 eligible identities and a current date-ready target, under the existing
W-23/W-24 contract, then a later measurement that RW-30 already requires
to be sealed before it starts. An alternate merger evidence design
(rolling cohort, synthetic identities, a lowered 30, a seeded
`COCKPIT_MERGER_WATCHLIST`, or copying BOW forward) needs its own claimed
and reviewed amendment. This memo does not request that amendment.

### 2. `cef_discount` — watch — `data_contract_unauthorized`

Closed-end-fund discount reversion (DEC-03, deferred). Corpus facts: edge
present, expected-return model **absent**, required contract `daily_nav`,
`data_contract_authorized: false`. `classify()` stops at watch before the
empirical gate is consulted. `screens.v1.json` has `cef_nav_authorized:
false`.

Blocking evidence:

- DEC-03 deferred this name until a daily NAV data contract is selected
  and measured. Below-NAV is not expected return. A persistent discount
  is not a return.
- SEC Form N-PORT is public quarterly research context. DEC-03 already
  says it is not a fresh daily NAV authority.
- No later package selected a source. RW-27's resource allowance has
  `new_data_feeds: false`. RW-29 lists `cef_discount_daily_nav_contract`
  with `authorized_by_this_claim: false`.

Evidence that would change the verdict: you authorize a daily NAV source
contract (the `RW-38` text), a later claim records that the contract
exists, and a still later measurement under the RW-30 protocol treats
discount, distributions, and corporate actions as inputs to an
expected-return model. Authorization of the contract alone leaves
`qualify_count` at 0.

### 3. `mega_cap_signal` — reject — `no_expected_return_model`

Mega-cap technical/news signal. Kept on the comparison list after DEC-03
rejected it for W-23. Corpus facts: edge flag present, expected-return
model **absent**, required data contract `none`. `classify()` rejects.

Blocking evidence: there is no point-in-time expected-return model. A
healthy feed and a convenient watchlist are not that model (DEC-03).
RW-29 `rejected_candidates_need_new_freeze` names this id. The evidence
that would put it back on a verdict other than reject is a new freeze
that includes such a model. This memo does not open that freeze.

### 4. `awareness_fallback` — reject — `no_edge`

Stay strategy-agnostic / awareness-only. Corpus facts: `supplies_edge:
false`. `classify()` rejects.

Blocking evidence: the candidate supplies no edge and cannot exercise a
real opening paper order. It remains the safe fallback when the
data/calibration bar is unmet (DEC-03). Net operator time saved is
unmeasured (`paired_awareness_time_study` is not started). Awareness
evidence can still be delivered through the existing research path
(RW-35). It cannot be counted as one of the two launch strategies.

### 5. `momentum` — reject — `quant_sim_loop`

`app/quant/strategies/momentum.py`. Origin `quant_sim_loop`, which is the
first `classify()` match.

Blocking evidence: QT-02 simulated venue intents are not a product launch
strategy. The sim-loop callback horizon is not a paper-position lifecycle.
`quant/` stays off the broker import path. Product-launch status for this
module requires a new freeze. This memo does not open that freeze.

### 6. `pairs` — reject — `quant_sim_loop`

`app/quant/strategies/pairs.py`. Same origin and the same blocking rule as
`momentum`.

Blocking evidence: the name is on the shortlist as an in-repo module. It
is not an activated strategy. Product-launch status requires a new freeze.
This memo does not open that freeze. `market_maker.py` stays off this
freeze; adding it means a new claim that drops another name.

## Why `RW-38` is the proposal

Two names are on `watch`. Zero names qualify. Launch selection needs two
or three qualified strategies and your activation. The rejects cannot be
moved without a new freeze, and opening a freeze to hunt for a second
qualify would be a gate change. `cash_merger`'s missing evidence is a
population the recorded screens do not contain; a document cannot mint 30
date-ready identities.

`cef_discount` is the watch whose blocker is an authorization you have not
given. Product §A says further data collection needs its own authorized
resource/source claim. `RW-38` is that claim, bounded to selecting the
contract. It is the specific decision this empty-screen record asks for.

## Evidence that carries past paper

The current runtime is paper-first and human-gated. The adopted product
target is an autonomous paper lifecycle, with live trading still behind
the existing pre-live gates. These blockers are properties of the
strategies and of the evidence, so they remain in force when paper
execution is unattended and when a later live progression is reviewed:

- a same-date cohort of 30 eligible cash-merger identities and a current
  date-ready target
- a point-in-time daily NAV contract whose discount is not treated as
  expected return
- independent extraction labels, separate from parser regressions
- a measurement population sealed under RW-30 before any numeric threshold
- owner activation after qualification

An empty GET-only board, a green implementation suite, or a paper fill
does not close those items and does not grant live authority.

## What stays in force

- `qualify_count` stays 0 on this corpus.
- The 30-identity cohort gate stays 30.
- Freeze, metrics, evaluation, cases, protocol, and screens bytes stay as
  hashed above.
- `COCKPIT_MERGER_WATCHLIST` stays unseeded. BOW stays off the Tuesday row.
- No labelling, no NAV ingest, no measurement, no activation, no
  order-path edit, no soak, no empirical PASS.
- `RW-32` remains the remote seal package and is not reused.
- `RW-38` is proposed. Implementation waits for your authorization.

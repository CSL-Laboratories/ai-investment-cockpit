# O4 candidate: strategy qualification protocol

**Maturity claimed:** `researched`.

**Not claimed:** `human_decided`, `contract_frozen`, `prototyped`, `implemented`,
`provider_free_verified`, `demo_or_paper_qualified`, `shadow_qualified`,
`limited_live_qualified`, `release_qualified`. This packet also does not claim
qualification, an empirical pass, live authority, credentials, or a frozen
contract. The registry row in `docs/replacement/program.json` stays
`pending_outcome_artifact`. Checking these artifacts is not outcome completion
and is not independent verification.

Parent: [CSL-216](https://linear.app/csl-laboratories/issue/CSL-216), under the
accepted [CSL-212](https://linear.app/csl-laboratories/issue/CSL-212) month
program. Authoritative clause: `docs/replacement/MONTH-PROGRAM.md#o4`, digest
`f28b75244a4eef5e9d3095349d6088bcd08e09547ab8e0fbc03ba52a119f3001`. The four
accepted CSL-212 documents are inputs. This packet does not edit them.

Consumes the accepted O1, O2, and O3 month-program fragments. It does not treat
those outcomes as implemented. Supplies a candidate qualification protocol to
later O5, O6, O8, and O9 work. No later outcome may rewrite this split after
seeing a result. No result exists yet.

## Problem

There is no honest, reproducible cross-market strategy qualification protocol.
The recorded feasibility screens qualified zero candidates. That empty outcome
stays in the packet. RW-28 `evaluation.v1.json` has `qualify_count` 0. RW-37
`screens.v1.json` has `qualify_count` 0, and the latest screen
`w24_2026_09_15_tue` is `no_pass_empty_date_not_ready` (pool 199, supported
heads 32, cash-parse hits 0, selection-ready 0, date-ready 0, cohort 0). The
quant sim-loop names `momentum` and `pairs` are rejected as
`quant_sim_loop`, not admitted as launch strategies. A zero-cost simulation, a
backtest, and a parameter variant are not qualified strategies.

## Artifacts

| Path | Role |
| --- | --- |
| `PROTOCOL.md` | This candidate contract. |
| `schemas/` | JSON Schemas for the strategy contract, experiment, metric registry, promotion protocol, candidate queue, recorded empty outcome, and research block. |
| `fixtures/candidate-packet.json` | The candidate packet. Design only. |
| `fixtures/negative/` | Frozen negative controls. |
| `manifest.json` | SHA-256 pins for the schemas, packet, negative controls, this document, and `validate.py`. |
| `validate.py` | Provider-free checker. No network, credentials, or broker import. |

Candidate command, not a completion gate:

```text
python -X utf8 -B docs/replacement/outcomes/O4/validate.py
```

A clean command prints four negative-control failures and one researched-maturity
line. Exit 0 means those controls failed as designed and the candidate packet
stayed internally consistent. It does not promote the outcome.

## Strategy contract

Family `xm_us_equity_kalshi_demo` is one strategy. Its three configurations
(`equity_leg`, `event_leg`, `joint_legs`) are parameter variants. They count
as multiple-testing trials. They do not count as three strategies.
`distinct_strategy_count` is 1. `counted_as_distinct_strategies` is false.
`multiple_testing_trial_count` is 3.

The contract states a hypothesis, three alternatives, and falsifiers. The
research quota on this family is one bounded reconciliation pass, zero Deep
Research runs, and zero data acquisition. Another pass needs a human Linear
receipt. The historical shortlist stays in the queue as retained evidence. It
is not re-labeled.

## Experiment design

`xm-us-equity-kalshi-demo-001` is design only. Status `not_run`. No data
acquisition. Markets, in inherited owner order: US cash equity, then Kalshi
demo event contracts. No market data, demo session, or held-out optimization
is run.

The population is an illustrative disjoint split, not an acquired sample:

- tuning: `illustrative:us-equity:tune`, `illustrative:kalshi-demo:tune`
- evaluation: `illustrative:us-equity:eval`, `illustrative:kalshi-demo:eval`

Leakage controls are required on that split. Purge removes training labels
whose outcome window overlaps the evaluation window. Embargo removes the
post-evaluation training rows that would carry serial correlation. Embargo
length is `owner_unset`.

The matched baseline is cash on the same population and the same clock. Cash
flows are retained and matched to that baseline. Their values are unmeasured.
Transaction, latency, data, and model costs are retained and unmeasured. A
dropped or missing cost is a failed packet. A zero that was actually measured
would have to be stored as `retained_measured`. This packet has no such
measurement.

Hypothesis under test, once a later authorized measurement exists: the family
beats matched cash after those four costs, on the metric registry below, with
the recorded empty outcome still visible. The null is that it does not beat
matched cash; a net result that fails matched cash retains the null and
falsifies the hypothesis. This packet does not compute that comparison.

Alternatives: equity leg only, event leg only, and the awareness-only fallback
that supplies no execution edge.

Falsifiers: overlapping tuning and evaluation populations; a dropped cost or
dropped empty outcome; parameter variants counted as distinct strategies; a
net result that fails matched cash; use of held-out observations to choose
parameters; collapse under an owner-declared uncertainty or liquidity limit.

## Economic metric registry

Threshold values are owner decisions. Every threshold in this packet is
`owner_unset` and uncomputed.

| Measure | What a later measurement must retain | Citation |
| --- | --- | --- |
| Deflated Sharpe ratio | Selected Sharpe, trial count, variance of trial Sharpes, skewness, excess kurtosis, observation count. Trial count is the number of configurations searched, including parameter variants. | [Bailey and López de Prado](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) |
| Probability of backtest overfitting | Variant return matrix, combinatorial symmetric cross-validation splits, and the out-of-sample rank of the in-sample winner. The measure is the share of splits where that winner lands below the out-of-sample median. | [Bailey, Borwein, López de Prado, and Zhu](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253) |
| Drawdown | Peak-to-trough loss on the matched net cash flow, and the length of that drawdown. | Local accounting requirement. No numeric bar. |
| Uncertainty | The net-result series and an estimator. The estimator is `owner_unset`. | Local accounting requirement. No numeric bar. |

[Arian, Norouzi, and Seco](https://doi.org/10.1016/j.knosys.2024.112477) compare
out-of-sample methods in a synthetic controlled environment and report lower
backtest-overfitting probability and a stronger deflated-Sharpe statistic for
combinatorial purged cross-validation than for walk-forward, k-fold, and purged
k-fold. That comparison is their result, not a result of this repository. This
packet does not adopt their ranking, does not pick an exclusive splitter, and
does not run their study. It does require purge and embargo on the declared
split, because an unpurged cut can place overlapping labels on both sides.
[purgedcv](https://github.com/eslazarev/purged-cross-validation) is the
reference implementation consulted for those two operations: purge drops
overlapping-horizon training rows, and embargo drops a post-test buffer. The
same notes say those controls do not guarantee independence. Deflated Sharpe
in that library is applied to the number of strategy configurations searched,
not to the number of cross-validation paths.

## Promotion protocol and candidate queue

Promotion order is replay, then demo. Demo requires a prior replay stage. Both
stages are unauthorized. Live is outside this contract. Strategy activation is
unauthorized. Promotion stays unauthorized while thresholds are unset, the
recorded qualify count is 0, and an independent verifier has not rerun the
candidate.

The queue retains the six RW-28 verdicts, in file order, all unqualified:

| Strategy | Verdict | Blocking rule |
| --- | --- | --- |
| `cash_merger` | watch | `empirical_evidence_gate_open` |
| `cef_discount` | watch | `data_contract_unauthorized` |
| `mega_cap_signal` | reject | `no_expected_return_model` |
| `awareness_fallback` | reject | `no_edge` |
| `momentum` | reject | `quant_sim_loop` |
| `pairs` | reject | `quant_sim_loop` |

The design family is a seventh queue row with verdict `not_run`. Queue
`qualify_count` is 0. Parameter-variant ids are not queue rows.

## Negative controls

The frozen fixtures under `fixtures/negative/` are schema-shaped packets that
must be rejected for exactly one designed reason:

| Fixture | Designed failure |
| --- | --- |
| `overlapping-populations.json` | A tuning id also appears in the evaluation population. |
| `dropped-costs.json` | The transaction cost is marked dropped. |
| `dropped-empty-outcome.json` | The recorded zero-qualify screen is marked not retained. |
| `parameter-variants.json` | The three configurations are counted as three strategies. |

The checker prints each rejection. A control that passes, or that fails for a
different reason, rejects the run.

## Evidence pins

- `app/feasibility/screens.v1.json` SHA-256 `933afccdd9bac75949c069ca07dbd1102cd54f5ab2285c0ca9dc1c5a49c01f09`
- `app/feasibility/evaluation.v1.json` SHA-256 `665f72b426efff7a7411d14816e50867cccb8ee26f03e3bd5dc18056a5a0ab31`
- Registry source commit recorded by CSL-212: `73ab78d9d69494c757cec875855121b1d29320a4`
- Accepted contract digests remain the CSL-212 receipt digests. See `manifest.json` and the packet field `accepted_contract_digests`.

## Research (Parallel)

Embedded Parallel searches `search_a81ed0bcb8b97e64d4f958ef59dde38a` and
`search_73f6ee6002538aa601e083ab7169dee4`, run on Dan's box on 2026-10-17.
This session started from those results. Titles and method details below were
read from the cited pages and from the purgedcv README and quickstart that
document the same purge, embargo, deflated-Sharpe, and backtest-overfitting
operations.

- Bailey and López de Prado, *The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality*: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
- Bailey, Borwein, López de Prado, and Zhu, *The Probability of Backtest Overfitting*: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253
- Arian, Norouzi, and Seco, *Backtest overfitting in the machine learning era: A comparison of out-of-sample testing methods in a synthetic controlled environment*, Knowledge-Based Systems (2024): https://doi.org/10.1016/j.knosys.2024.112477
- Purged cross-validation reference implementation: https://github.com/eslazarev/purged-cross-validation

Pages opened while reading those results:

- ScienceDirect record of the 2024 paper: https://www.sciencedirect.com/science/article/abs/pii/S0950705124011110
- SSRN working paper of the same study: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4778909
- purgedcv quickstart: https://eslazarev.github.io/purged-cross-validation/quickstart/

No further Deep Research run and no new data acquisition were authorized.
Local evidence is the feasibility freeze, evaluation, protocol, and screen
ledger already in the repository. Context7 was not required: this packet adds
no library integration.

## Unresolved decisions (owner: Lucas)

None of these are decided in this packet. `selected_option` is null and
`status` is `undecided`.

| Decision | What is unresolved |
| --- | --- |
| Spend | Any new data acquisition, paid provider call, or further Deep Research run. This packet spends zero and does not authorize the next one. |
| Credentials | Any broker, venue, or model credential. None are added. |
| Tax/legal | Whether tax sits inside the first net-yield metric, and any account or venue eligibility. |
| Architecture | A costly-to-reverse engine, storage, or topology choice, including whether a later measurement must use one exclusive cross-validation implementation. Purge and embargo are required here because the outcome names them. The implementation choice is not. |
| Release | Release scope and acceptance. |
| Live | Live trading, capital movement, and strategy activation. |
| Thresholds | Numeric bars for deflated Sharpe, probability of backtest overfitting, drawdown, uncertainty, capital, and loss. Embargo length and the uncertainty estimator are unset with them. |

Inherited owner direction, not re-decided here: US individual account, stocks
plus prediction markets, Kalshi demo before any later venue, GUI plus CLI, and
paper, demo, and shadow as qualification stages. Bounded direct authorization
stays behind separate gates. This packet does not enter those stages.

## Completion

The outcome completion gate remains `pending_outcome_artifact`. Completion
still requires an exact candidate, a retained oracle, negative controls, and an
independent non-implementer rerun. This checker is the checked-in candidate
command and the negative-control corpus. It is not that independent rerun.
Implementer execution, CI, and merge do not move the maturity.

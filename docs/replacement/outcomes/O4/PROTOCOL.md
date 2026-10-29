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

A clean command prints thirty negative-control failures and one researched-maturity
line. Exit 0 means those controls failed as designed and the candidate packet
stayed internally consistent. It does not promote the outcome.

## Strategy contract

Family `xm_us_equity_kalshi_demo` is one strategy. Its three configurations
(`equity_leg`, `event_leg`, `joint_legs`) are parameter variants. They count
as multiple-testing trials. They do not count as three strategies.
`distinct_strategy_count` is 1. `counted_as_distinct_strategies` is false.
`multiple_testing_trial_count` is 3. Each variant also records its frozen
`configuration` — the market legs it runs — and `configuration_sha256`, the
checker-verified SHA-256 of that configuration's canonical JSON (sorted keys,
compact separators). A variant id cannot silently denote different logic
during tuning and evaluation: a configuration that drifts from its recorded
digest fails the packet.

These digests are internal-consistency checks, not a freeze. The checker
recomputes `configuration_sha256`, `members_sha256`, and the `manifest.json`
pins from the same artifact set an editor can update wholesale, so a digest
that matches its recorded object proves the packet is self-consistent — it
cannot prove the experiment still uses preregistered inputs. Any result-bearing
version of this packet must instead pin the packet and manifest digests to a
separately accepted, versioned pre-result receipt kept outside this artifact
set; that receipt requirement is declared as a replay-stage entry gate in the
promotion protocol below.

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

The split is bound to a `selection_manifest`. Its `universe` and `as_of` stay
`owner_unset` because no observation set is acquired; `split_rule` declares
the purged and embargoed disjoint tuning/evaluation design; `members_sha256`
is the checker-verified SHA-256 of the member-id lists' canonical JSON.
Member ids cannot be reassigned or swapped after results are seen without
failing the digest.

Leakage controls are required on that split, and each records an explicit
`status`: `run` with retained evidence, or `not_run`. Purge removes training
labels whose outcome window overlaps the evaluation window. Embargo removes
the post-evaluation training rows that would carry serial correlation. Embargo
length is `owner_unset`. Nothing here has run: both controls record `not_run`
with null evidence, and a `not_run` control cannot satisfy qualification.
A control that reads as satisfied without a run fails the packet.

The matched baseline is cash on the same population and the same clock. Cash
flows are retained and matched to that baseline. Their values are unmeasured.
The cash benchmark definition is declared and unmeasured: `instrument`,
`rate_source`, `as_of`, `compounding`, `horizon`, and `capital_flow_schedule`
are required, and each value in this packet is `null`. Each of those fields
must be non-null before a pre-result receipt. A later measurement
must set `as_of` to a `YYYY-MM-DD` date and `compounding` to one of `simple`,
`daily`, `monthly`, `quarterly`, `annual`, or `continuous`. This packet does
not choose those values.

Transaction, latency, data, model, venue-fee, spread/slippage,
funding/collateral, and tax/accounting costs are declared and unmeasured.
Each is a required component with status `retained_unmeasured` and a null
amount. A dropped or missing cost is a failed packet. A zero that was actually
measured would have to be stored as `retained_measured` — and while the
experiment is `not_run`, a `retained_measured` cost is asserted measurement
evidence the checker rejects. This packet has no such measurement.

Hypothesis under test, once a later authorized measurement exists: the family
beats matched cash after all eight retained costs (transaction, latency, data,
model, venue fees, spread/slippage, funding/collateral, and tax/accounting),
measured on net yield against the matched-cash baseline and capital use
together with the statistical registry below, with the recorded empty outcome
still visible. The null is
that it does not beat matched cash. A net result that does not beat matched
cash retains the null and rejects the strategy hypothesis; only a net result
that beats matched cash rejects the null. This packet does not compute that
comparison.

The checker accepts an equivalent net-of claim. Either hypothesis may say
"net of" or "after all eight retained costs", name the eight costs in any
order, repeat a name, or use the singular "venue-fee" as well as "venue fees".
All eight names have to sit in the same clause as that net phrase. A later
sentence that lists the other costs, or a "net of nothing" sentence, does not
carry the claim. A bare "after", including "after-hours", is not a net-of
claim. A cost is dropped when an exclusion marker governs that cost. The
marker binds to the phrase after it and stops at a comma or the next net-of
claim. A comma-wrapped aside that names no retained cost ("for example",
"notably") is not that stop. Finite verbs and their inflections count: exclude, excludes,
excluding, and excluded; ignore, ignores, ignoring, and ignored; omit,
omits, omitting, and omitted; plus except and without. "does not", "do not",
and "never" in front of those verbs are not exclusions. Also listed: drops,
removes, save for, no longer includes, waived, and not counted. A non-marker
exclusion that shares the clause ("set aside", "reported separately",
"reported elsewhere") also drops the claim. "without look-ahead bias" and
"no costs are excluded" do not drop a cost. The check matches only these
listed markers, so a human must still read the cost claim.

Alternatives: equity leg only, event leg only, and the awareness-only fallback
that supplies no execution edge.

Falsifiers: overlapping tuning and evaluation populations; a dropped cost or
dropped empty outcome; parameter variants counted as distinct strategies; a
net result that does not beat matched cash (rejects the strategy hypothesis); use of held-out observations to choose
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
| Net yield | Net-of-cost result against the matched-cash baseline: the net-result series, the matched-cash baseline series, and the retained cost amounts. This is the primary economic outcome the hypothesis names. | Local accounting requirement. No numeric bar. |
| Capital use | The capital the family requires: the capital-employed series and peak capital employed. | Local accounting requirement. No numeric bar. |

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

The non-live promotion ladder follows SCOPE-AUDIT RC-8: replay, simulation,
official demo/test venue, shadow/live-data observation, then paper or
venue-equivalent execution. The packet ids are `replay`, `simulation`, `demo`,
`shadow`, and `paper`. Every stage requires the immediately preceding stage;
none may be skipped or reordered. All five stages are unauthorized. Limited
live and broader live are explicitly unauthorized in the `live` block and
outside this candidate contract; their separately accepted gates remain
required before any future live operation. Strategy activation is unauthorized.
Promotion stays unauthorized while thresholds are unset, the recorded qualify
count is 0, and an independent verifier has not rerun the candidate.

Each stage records its exact entry and exit gates (`entry_gates`,
`exit_gates`, `failure_policy`) so later consumers do not invent or amend
policy after observing results. Replay entry requires the packet and manifest
digests to be pinned to a separately accepted, versioned pre-result receipt
(`preresult_receipt`) kept outside this artifact set — self-recorded digests
are consistency checks only — plus an authorized independent verifier rerun
(`verifier_rerun_record`) and separate unit/property-test evidence
(`unit_property_test_record`); its exit records the replay output matched to
those receipt digests. Unit/property results remain distinct from replay or
economic qualification.

| Stage | Entry gates and retained evidence | Exit gates |
| --- | --- | --- |
| Simulation | Replay exit met (`replay_output_record`); simulation model, costs, thresholds and coverage pinned before results (`preresult_receipt`). | Simulation output recorded with all costs; meets the pre-result thresholds and coverage. |
| Official demo/test venue | Simulation exit met (`simulation_output_record`); official demo venue profile and non-live scope accepted before results (`venue_conformance_record`, `nonlive_scope_receipt`); demo thresholds and coverage pinned before results (`preresult_receipt`). | Demo output recorded; reconciliation and recovery meet the pre-result thresholds and coverage. |
| Shadow/live-data observation | Demo exit met (`demo_output_record`); acquisition scope accepted before observation (`data_scope_receipt`); observation only, no order authority; pre-result protocol retained (`preresult_receipt`). | Shadow output records data gaps, latency and costs; meets the pre-result thresholds and coverage. |
| Paper or venue-equivalent execution | Shadow exit met (`shadow_output_record`); non-live execution scope accepted before results (`nonlive_scope_receipt`, `preresult_receipt`); current risk, mode, approval and gateway path preserved. | Execution output recorded; reconciliation, recovery and economics meet the pre-result thresholds and coverage; outcome routed to the release owner for decision. |

Each rung retains separate output for the exact candidate and receipt. Evidence
from an earlier rung does not satisfy a later one. The pre-result receipt must
bind the stage-specific thresholds, coverage and environment before any results
are observed; unset values block entry. Elapsed time and order count do not
replace coverage, and demo/paper P&L does not establish production profitability.
These gates specify required evidence, not completed tests or selected numeric
bars. This candidate packet remains at maturity `researched` and does not claim
to be a frozen, accepted contract.

A stage's `failure_policy` is `blocked_and_recorded_no_promotion`. A missing
rung, a stage marked `authorized`, or gates that drop a required criterion or
evidence fail the packet. Invalid order or a bypassed prior-stage link also
fails validation. The checker enforces the required criteria for every rung;
schema-shaped placeholder gates cannot stand in for them.

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

The frozen fixtures under `fixtures/negative/` must be rejected for exactly one
designed reason. The missing-rung control deliberately retains the former
two-stage ladder; the other controls preserve the complete schema shape:

| Fixture | Designed failure |
| --- | --- |
| `overlapping-populations.json` | A tuning id also appears in the evaluation population. |
| `dropped-costs.json` | The transaction cost is marked dropped. |
| `dropped-empty-outcome.json` | The recorded zero-qualify screen is marked not retained. |
| `parameter-variants.json` | The three configurations are counted as three strategies. |
| `release-owner-person.json` | The release decision routes to a person instead of its role. |
| `leakage-control-satisfied.json` | The purge control reads `run` with evidence on an experiment that has not run. |
| `variant-configuration.json` | A parameter variant's configuration digest does not match its recorded configuration. |
| `population-members-rebound.json` | A member id is reassigned while the selection-manifest digest is left stale. |
| `metric-registry-incomplete.json` | The net-yield measure does not retain its matched-baseline input. |
| `measured-costs.json` | A cost reads `retained_measured` on an experiment that has not run. |
| `promotion-stage-authorized.json` | The replay stage is marked authorized. |
| `preresult-receipt-dropped.json` | The replay entry gates drop the pre-result receipt criterion and evidence. |
| `stages-missing-rung.json` | The former replay/demo ladder omits simulation, shadow and paper; rejected as `missing_promotion_rung`. |
| `shadow-data-scope-dropped.json` | Shadow entry drops the acquisition-scope criterion and its required receipt; rejected as `promotion_gates`. |
| `hypothesis-cost-excluded.json` | Both hypotheses name every cost and then exclude venue fees, spread/slippage, funding/collateral, and tax/accounting. Rejected as `hypothesis_costs`. |
| `hypothesis-net-partial.json` | The net-of clause names only transaction and latency. The other six costs are in a later sentence. Rejected as `hypothesis_costs`. |
| `hypothesis-net-of-nothing.json` | A gross claim, a separate list of all eight costs, then "Results are net of nothing." Rejected as `hypothesis_costs`. |
| `hypothesis-before-costs.json` | "before" names all eight costs. A later sentence says "Net of fees reported elsewhere." Rejected as `hypothesis_costs`. |
| `hypothesis-set-aside.json` | The net-of clause names all eight costs and then sets data costs aside. Rejected as `hypothesis_costs`. |
| `hypothesis-excludes-venue-fees.json` | A full net-of clause, then "The strategy excludes venue fees." Rejected as `hypothesis_costs`. |
| `hypothesis-ignores-model.json` | A full net-of clause, then "It ignores model costs." Rejected as `hypothesis_costs`. |
| `hypothesis-except-after-hours.json` | A full net-of clause, then "except after-hours transaction fees." Rejected as `hypothesis_costs`. |
| `hypothesis-except-for-example.json` | A full net-of clause, then "except, for example, transaction fees." Rejected as `hypothesis_costs`. |
| `hypothesis-excluding-notably.json` | A full net-of clause, then "excluding, notably, data." Rejected as `hypothesis_costs`. |
| `hypothesis-no-longer-includes.json` | A full net-of clause, then "It no longer includes venue fees." Rejected as `hypothesis_costs`. |
| `hypothesis-drops-data.json` | A full net-of clause, then "It drops data costs." Rejected as `hypothesis_costs`. |
| `hypothesis-removes-model.json` | A full net-of clause, then "It removes model costs." Rejected as `hypothesis_costs`. |
| `hypothesis-waived-venue-fees.json` | A full net-of clause, then "Venue fees are waived." Rejected as `hypothesis_costs`. |
| `hypothesis-not-counted.json` | A full net-of clause, then "Data costs are not counted." Rejected as `hypothesis_costs`. |
| `hypothesis-save-for-data.json` | A full net-of clause, then "save for data." Rejected as `hypothesis_costs`. |

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

## Unresolved decisions (owner: roles)

None of these are decided in this packet. `selected_option` is null and
`status` is `undecided`. Every decision routes to a role, never a named
person. The spend and credentials rows route to `reserved authority`, the
role AGENTS.md reserves to Lucas (spend, new services, sensitive company
credentials); no other founder's receipt clears them. The other owner-class
rows route to `owner`. The release row routes to
`release owner`, the founder set: release scope and acceptance is a
collective founder decision under the CSL-212 authority boundary, not a
single person's.

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

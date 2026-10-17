# O5 candidate: provider-neutral decision contract

**Issue:** [CSL-217](https://linear.app/csl-laboratories/issue/CSL-217)
**Parent:** CSL-212 outcome O5, clause `docs/replacement/MONTH-PROGRAM.md#o5`
**Status:** candidate packet. Maturity claimed: `researched`.
**Not claimed:** `human_decided`, `contract_frozen`, `prototyped`, `implemented`,
`provider_free_verified`, `demo_or_paper_qualified`, `shadow_qualified`,
`limited_live_qualified`, `release_qualified`. This packet does not report PASS,
installed-route qualification, or an empirical result.

The registry row in `docs/replacement/program.json` is unchanged and remains
`pending_outcome_artifact`. The four accepted CSL-212 documents and their digests
are unchanged. Inv 2 is unchanged: auto decision roles stay pinned to Claude, and
Codex stays off auto and live submit.

This packet is a contract artifact. It does not add a runtime route, a credential,
a paid call, broker tools, or an order path.

## Exact candidate command

```bash
python -X utf8 -B tools/o5_decision_contract.py
```

The command reads only the local packet. Exit 0 means the positive fixtures are
admitted at `researched` and every negative control is refused with its designed
code. Exit 0 is not an independent verification, a qualification, or a PASS.
An independent non-implementer still has to rerun that command on a clean
candidate and compare the retained output with a human Linear receipt before any
higher maturity is available. That rerun has not been claimed.

## What this packet consumes

O5 consumes O1, O2, and O4 as month-program fragments. Those fragments are the
accepted `MONTH-PROGRAM.md` digest
`f28b75244a4eef5e9d3095349d6088bcd08e09547ab8e0fbc03ba52a119f3001`.
O4's own outcome artifact is still `pending_outcome_artifact` at `researched`.
This suite does not freeze, replace, or rewrite that artifact.

The O4/O5 checkpoint in the month program says to freeze evaluation populations,
baselines, metrics, and thresholds before observing provider results, and that a
provider result cannot rewrite the protocol. The frozen task suite is this
candidate's population. Its raw-byte SHA-256 is pinned in
`fixtures/manifest.json`. Provider results are inadmissible as amendments.

## Research (Parallel)

Embedded searches, run on Dan's box on 2026-10-17:
`search_291dc93b6c257e2f04c7cc05e47308f6` and
`search_f829cb56fe9cad2590d4e8c35cfad644`.
The same URLs were read again on 2026-10-17 while drafting this packet. They are
research inputs. None of them selects a provider, authorizes spend, or qualifies
a route.

- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
  Strict JSON Schema mode is aimed at syntactic adherence, including
  `additionalProperties: false`. The same guide says a safety refusal may not
  follow the supplied schema and is exposed as a separate refusal field. This
  packet therefore treats refusal as its own wire type. The guide does not grant
  financial-use permission, and the example model id in that page is not adopted.
- [The Structured Output Benchmark](https://arxiv.org/html/2604.25359v1).
  Across 21 models, schema compliance can be near-perfect while the best reported
  value accuracy is 83.0% on text, 67.2% on images, and 23.7% on audio. A
  schema-valid leaf can still be the wrong value. Schema compliance is not value
  correctness. Those figures are the paper's measurements, not measurements of
  any Cockpit route.
- [UCCI](https://arxiv.org/html/2605.18796).
  Calibrated error probabilities, not raw confidence, are the routing score in
  that paper. On their named-entity workload they report a 31% cost reduction
  (95% CI 27% to 35%) at micro-F1 0.91, and ECE moving from 0.12 to 0.03, using
  actual model outputs rather than simulated routing. Their constrained
  cost objective is not adopted. This packet refuses a cheaper or faster result
  when correctness, grounding, or calibration is worse.
- [Conformal LLM routing](https://aclanthology.org/2026.acl-srw.70/).
  Uddin and Bauer, ACL 2026 Student Research Workshop, pages 791–799. A conformal
  gate can bound the violation rate among routed queries on GSM8K and MMLU, and
  the paper reports that routability depends on both the model and the task. A
  benchmark bound is not installed-route evidence. The conformal gate is listed
  as an experiment and is not selected.
- [RouteJudge](https://arxiv.org/pdf/2606.18774v1).
  Route-level preference under a shared budget is a different question from
  factual or financial correctness. The paper stores cost and latency beside
  preference labels. User preference and model agreement are not correctness and
  cannot offset a worse floor.

Inherited authority research `trun_db705696fec649d8ad991d8f8f7b7813` remains a
research input: a typed output is evidence, never a credential or an authority
grant. Provider financial-use permission, retention, and geography stay unresolved.

## Local route inventory

Read at branch base `ebbaa34489f171e079834d02098135711e1d4755` on 2026-10-17.
The registry sentence that cites `73ab78d9d69494c757cec875855121b1d29320a4` is
left as it is. Decision-route code has moved since that baseline; this inventory
describes the branch base and does not amend the registry.

| Route | Presence | What the source shows |
| --- | --- | --- |
| `current.triage` | Installed | `TriageResult`. Inv 2 alias. Tool-starved. |
| `current.summary` | Installed | `MorningBrief`. Awareness, not a decision-stage alias. |
| `current.decision` | Installed | `TradeProposal` through `call_agent`. Auto modes resolve an allowlisted Claude Haiku id. |
| `current.deep` | Installed | Legacy deep alias. Escalation count comes from the audited ledger limit. |
| `current.grounding` | Installed | `GroundingCheck`. The model can flag and cannot clear. |
| `current.interrogation` | Installed | Fresh-context `InterrogationAnswer` beside a deterministic risk decision. |
| `current.codex_runner` | Installed | Human-gated read-only or paper. Denied tool profile. Auto and live submit refuse non-Claude provenance. |
| `candidate.system_one` | Not installed | Named in the authority research. No adapter in this tree. |
| `candidate.openai_decisions` | Not installed | Named in the authority research. No adapter in this tree. |
| `candidate.jev` | Not installed | Named by CSL-95, CSL-191, and the scope audit. No Jev module in this tree. |

Every row has `installed_route_qualified: false` and `qualification_claim: none`.
Installed means the code path exists. It does not mean the route has been qualified.

## Wire contracts

Schemas live in `schemas/o5-wire.schema.json` (JSON Schema draft 2020-12).
Positive examples live in `fixtures/positive/` and the manifests beside them.

- **Decision request.** Bound to a frozen task id and the suite digest. Authority
  ceiling `evidence_only`. No order, no credential, no authorized spend, no
  admitted disclosure. A request cannot report `demo_or_paper`, `shadow`,
  `limited_live`, or `independently_verified`. An admitted request is retained
  by request id, task, route, and suite digest.
- **Decision result.** Schema compliance and value correctness are separate
  booleans. The worked example is schema-valid and value-wrong, and the
  disposition is `withhold`. A result cannot grant authority, emit an order, or
  carry a credential. A result that relabels the frozen task is refused. A result
  cannot report `demo_or_paper`, `shadow`, `limited_live`, or
  `independently_verified`, because those outputs were not retained. A result
  must match an admitted request on request id, task, route, and suite digest.
- **Decision refusal.** First-class object, not a schema-invalid success. The
  frozen refusal tasks cover an order request, a credential request, and a budget
  widening. A refusal adds no disclosure and does not change the budget. A
  refusal cannot report `demo_or_paper`, `shadow`, `limited_live`, or
  `independently_verified`, because those outputs were not retained. A refusal
  must match an admitted request on request id, task, route, and suite digest.
- **Capability manifest.** Schema validation and explicit refusal are capabilities.
  Broker tools, order submission, and credential custody are forbidden. No
  capability grants authority.
- **Route manifest.** The inventory above. No row is qualified.
- **Budget ledger.** Records the context cap, the Haiku 5.5 long-context line, and
  the audited deep-escalation limit as observed code. Successor spend has a null
  ceiling. `spend_authorized` is false. `owner_decision` is `undecided` on every
  row. Nothing here is a spend approval.
- **Privacy disclosure.** Proposal only. `decided` is false. Owner: Lucas.
  Credentials and order instructions are forbidden. No class is admitted.
- **Measure report.** Defines schema compliance, value correctness, grounding,
  calibration ECE, cost, latency, reliability, and preference. `measured` is
  false. Cost, latency, schema compliance alone, model agreement, and preference
  cannot offset correctness. Grounding cannot clear an order.
- **Fallback policy.** A fallback may only narrow authority, disclosure, and
  budget, or leave them unchanged. The admitted example withholds and records.
  The primary ceiling in this packet stays `evidence_only`. Both sides are
  checked against the admitted disclosure matrix. No class is admitted, and
  `credentials` and `order_instructions` stay forbidden. A class on the primary
  is refused even when the fallback removes it.

## Frozen task suite

`fixtures/task-suite.json` is the candidate population. The deterministic scorer
recomputes labels; the file's `expected` object must match. The scorer does not
call a model.

- `task.schema_valid_value_wrong` is schema-valid and value-wrong.
- `task.schema_invalid` fails the schema, so value correctness is also false.
- `task.grounded_claim` and `task.ungrounded_claim` check dispatched refs and
  structured fields, not prose overlap.
- `task.refuse_order`, `task.refuse_credential`, and `task.refuse_budget` are
  refusal cases. None emits an order or a credential.

## Installed-route qualification protocol

A later experiment can support an installed-route claim only when all of the
following are true. None of them is true for this packet.

1. The call goes through the real application route, with the exact API, SDK,
   model, or CLI version recorded.
2. The transport is not a mock, HTTP mock, fixture, or other simulation.
3. Outputs and refusals are retained and scored against this frozen suite.
4. Schema compliance, value correctness, grounding, calibration, refusal,
   reliability, cost, and latency are reported separately.
5. Correctness, grounding, and calibration are not worse than the declared
   baseline. Cost and latency do not buy a worse floor.
6. Disclosure stays inside a human-admitted matrix, and spend stays inside a
   human-admitted ledger.
7. The evidence class is not simulation, and a human receipt accepts the claim.

Until then the only admissible qualification claim is `none`.

## Experiments

`fixtures/experiments.json` lists six experiments. Every row is
`listed_not_run`. None uses the network, a credential, or a paid call. The list
is the stop condition for this research pass: the contract, the suite, and the
smallest experiments are recorded, and running any of them needs a human receipt.

## Negative controls

The validator must refuse these fixtures. Refusal is the designed result.

| Id | Designed code |
| --- | --- |
| `mock_reported_as_installed` | `mock_is_not_installed_route` |
| `fallback_widens_authority` | `fallback_widens_authority` |
| `fallback_widens_budget` | `fallback_widens_budget` |
| `fallback_widens_disclosure` | `fallback_widens_disclosure` |
| `cheaper_despite_worse_correctness` | `worse_correctness_accepted` |
| `faster_despite_worse_grounding` | `worse_grounding_accepted` |
| `cheaper_despite_worse_calibration` | `worse_calibration_accepted` |
| `provider_rewrites_protocol` | `provider_result_rewrites_protocol` |
| `result_contradicts_suite` | `result_contradicts_suite` |
| `experiment_marked_executed` | `experiment_executed` |
| `measures_reported_observed` | `measures_not_observed` |

Illustrative comparison numbers are synthetic. They are not route measurements.

## Unresolved decisions (owner: Lucas)

This packet does not decide any of these.

- **Spend.** Whether any listed experiment may be funded, and at what ceiling.
  The ledger records null successor ceilings and `owner_decision: undecided`.
- **Credentials.** Whether any provider credential may be provisioned for an
  installed-route run. None is added here.
- **Privacy.** Which disclosure classes, if any, may be sent to which provider.
  The matrix is a proposal. No class is admitted.
- **Tax and legal.** Provider financial-use permission, retention, geography, and
  any advice characterization. Unresolved by the inherited authority research.
- **Architecture.** Whether any candidate route becomes a successor decision
  route. Inv 2 stays as implemented until a separately reviewed amendment.
  Whether a `decision_result` may carry the retained provider output, so the
  validator can rescore that output instead of the submitted boolean labels,
  is also undecided. The wire schema does not have that field. Whether each
  frozen task stores the canonical prompt that a request must match is also
  undecided. A request currently proves only that its own `prompt_text` matches
  its own `prompt_sha256`.
- **Release.** This packet is not release evidence.
- **Live.** No live trading, no live authority, and no capital movement.
- **O4 artifact.** Whether a future accepted O4 population replaces this candidate
  suite. That replacement needs a human receipt. A provider result cannot do it.

## Evidence

Claimed: `researched`, from a local route inventory and the public sources above.
Locally observed facts are limited to which code paths exist. Simulation fixtures
demonstrate the scorer and the negative controls. They are not installed-route
evidence, demo or paper evidence, or shadow evidence.

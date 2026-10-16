# Cockpit scope archaeology and replacement research contract

Status: read-only audit · `origin/main` at `73ab78d9d69494c757cec875855121b1d29320a4` · 16 October 2026

This report reconstructs what AI Investment Cockpit was formally intended to be, what the repository actually contains, which decisions the owner has now superseded, and which research/evidence contracts should constrain the replacement. It does not edit or authorize GitHub, Linear, runtime, credentials, capital, or venue access.

## Executive finding

Cockpit was not formally scoped as only a trading engine. Its ratified September contract was a **standalone, persistent, connected investing application** spanning evidence intake, cited research, strategy qualification, portfolio/execution, operator interaction, audit/recovery, economics, and tax. The code reflects much of that breadth, but the formal product was never completed or empirically accepted.

The replacement should therefore reset the asset, execution, authority, provider, UX and release decisions without throwing away the repository's strongest work: provenance, point-in-time evaluation, deterministic replay, explicit unknowns, evidence promotion rules, installed-route checks, audit/recovery, and honest economic measurement.

The repository contains substantial implementation and test surface, not proof of product value. The main gap is not simply “Python versus a faster language.” It is the missing connection among product outcomes, multi-market contracts, qualified data and strategies, real operator UX, measured economics, exact-candidate qualification, and accepted runtime authority.

## 1. How the intended scope evolved

### Phase 1: local human-gated copilot

The earliest plan described a local control plane, model runtime harness, investment workflow, data interfaces, and operator experience. Subsequent security and Discord documents introduced a graduated autonomy ladder, local API, paper execution, breakers, audit, and operator approvals.

The original research framed reliable value as time, awareness and discipline. Trading alpha was explicitly speculative. It favored LLM extraction and interpretation combined with deterministic validation and policy rather than treating free-form model prediction as execution truth.

### Phase 2: paper-first quantitative and evidence system

The implementation expanded into:

- SEC filing ingestion, immutable raw storage, deltas, citations and claim gates;
- market/news ingestion, normalization, deduplication and rate limiting;
- proposal, grounding, interrogation and morning-brief workflows;
- deterministic risk, an order gateway, paper execution, reconciliation and P&L;
- a quant simulator with books, OMS, costs, signals, synthetic/replay feeds and prototype strategies;
- a persistent orchestrator, scheduler, breakers, kill switch and hash-chained audit;
- Discord and loopback API operator surfaces;
- a tightly specified cash-merger strategy experiment and evidence register.

This was still human-gated and paper-first in runtime. It was not a qualified autonomous product.

### Phase 3: ratified September product

`PRODUCT.md` and its adopted contracts expanded the first complete release into an unattended, standalone research-to-paper product:

- one connected application owning evidence, cases, strategies, portfolio, schedules, policy and audit;
- owner-selected coverage, bounded discovery and rich human input including documents/screenshots/images;
- cited cases with supporting and opposing evidence, unknowns and revisit triggers;
- two or three distinct, independently qualified strategies;
- cash-funded long-only US stock/ETF paper execution and full position lifecycle;
- task-based model routing with hard aggregate budgets and qualifying fallback routes;
- Windows and Linux support, with the primary pilot on Lucas's Linux host;
- GUI-independent interaction through Discord/host services, stable identity and permissions;
- validated US federal and California investment-tax calculations across selected account types;
- exact-candidate release evidence and at least 20 unattended market days;
- standalone operation, with Recursion only after a bounded executor conformance check.

The ratified target intentionally diverged from the then-current runtime: routine paper operation was to become autonomous under standing policy, dynamic instrument eligibility was to replace the static allowlist, and task-based routing was to replace the narrower model pin only after separately reviewed implementation amendments.

### Phase 4: October replacement direction

Lucas has now superseded the September product baseline. The replacement targets a ground-up, performance-oriented engine and operator product for stocks and prediction markets, broad data ingestion, provider-neutral decision experiments, configurable compliance/authority profiles, eventual bounded-direct live operation, GUI plus CLI, and net-yield/cost/speed optimization.

Accepted initial constraints in this session:

- reference user: a United States individual with a personal account;
- team, institutional, multi-tenant and delegated-role scope deferred;
- first event-market vertical slice: Kalshi demo;
- second event-market candidate: Polymarket US Retail API after the core slice is stable;
- candidate first eventual live authority profile: bounded direct authorization after
  accepted gates and a human-authored CSL-212 final-digest receipt;
- paper/simulation/shadow are qualification stages, not the permanent product ceiling;
- current runtime remains non-live; this audit grants no credential or authority change.

## 2. Formal scope versus current repository

| Capability | Repository evidence | Audit disposition |
|---|---|---|
| Evidence/provenance | `filings/`, raw parquet storage, citations, deltas, untrusted-input handling | Strong reusable concepts and test oracles; generalize beyond SEC/equities |
| Ingestion | Alpaca market/news, Finnhub, EDGAR, FINRA, normalization/dedup/rate limits | Partial and stock-centric; no accepted multi-market evidence envelope or measured marginal-value policy |
| Research workflows | Briefing, triage, grounding, interrogation, proposals, schemas | Partial; model outputs and process completion do not establish useful or financially correct research |
| Strategy evidence | Cash-merger contracts, screens, frozen cohorts, negative results | Research discipline is valuable; selected strategy produced zero qualified candidates in recorded screens and no product PASS |
| Quant engine | Book, OMS, costs, P&L, sim venue, replay, signals and prototype strategies | Useful experiment surface; not evidence of replacement architecture, production speed, event-contract semantics or net yield |
| Execution | Alpaca paper adapter, gateway, paper service, state machine, reconciliation | Meaningful safety/behavior oracle; stock/paper-specific and not qualified for the successor's live or prediction-market target |
| Risk/authority | Deterministic risk, mode ladder, approval, breakers, kill switch | Current binding runtime, but successor policy is an explicit researched replacement decision |
| Operator UX | Discord bridge and loopback FastAPI/quant WebSocket | No actual GUI or unified CLI/TUI product; September's “operator experience” remained service/control oriented |
| Audit/recovery | Hash-chain audit, anchors, state, halts, reconciliation | High-value reusable failure knowledge; exact successor design remains to be decided |
| Tax | Product and tax contracts only; no corresponding tax calculation package | Formal September requirement was not implemented and is not automatically inherited |
| Multi-strategy product | Prototype quant strategies plus one deeply specified merger candidate | No two-to-three qualified strategy suite and no accepted allocation/overlap behavior |
| Prediction markets | Backlog and draft sidecar concepts | No accepted event instrument, resolution, settlement, collateral, fee, execution or qualification contract |
| Provider qualification | CLI runner, model configs and mocked routes | No frozen comparative workload or installed-route qualification for the replacement providers |
| Product acceptance | Thousands of tests and substantial dated evidence | Tests are implementation evidence only; no 20-day accepted pilot, full workflow acceptance, qualified strategy suite or release PASS |

The current tree's concentration confirms the shape: 244 test files, 39 schema files, 26 quant files, 25 filings files, 21 workflow files, and smaller orchestrator, broker, ingestion and Discord surfaces. That is a broad prototype/application corpus, not merely a hot-path engine and not a completed product.

## 3. Product decisions to supersede, reconsider, or preserve

### Superseded by current owner direction

- the September paper-first product target as the successor end state; current
  non-live runtime and every pre-live gate remain binding;
- stocks/ETFs as the only executable asset class;
- Discord/host control as a sufficient operator product;
- the exact two-or-three strategy launch contract;
- the selected cash-merger strategy as the product's defining path;
- the September tax matrix as mandatory first-release scope;
- the fixed 20-market-day rule as a universal gate for both asset classes;
- the exact model-role policy and future approval-profile shape, while Invariants 1
  and 6 keep risk-pass, approval, the client-side human gate and the single
  `OrderGateway` path binding until separately reviewed implementation amendments;
- the September MVP rule that positive expected profitability was not required for
  product acceptance, replacing it with explicit net-yield, benchmark, loss and
  uncertainty criteria while retaining honest negative and inconclusive outcomes.

Superseded does not mean deleted without evidence. Historical contracts remain the source for migration cases, threats, test oracles and known failure modes.

### Reconsider through bounded research

- shared core with asset/venue adapters versus separate market engines;
- language/runtime split and which paths merit Rust or another compiled language;
- direct model authority, deterministic hard limits and configurable compliance profiles;
- Jev/System One, OpenAI Decisions and other model routes;
- stocks and Kalshi data/execution adapters and authoritative historical sources;
- strategy creation/optimization method, including whether RL adds value;
- exact net-yield objective, risk/loss budget and baselines;
- live progression where paper/demo behavior is inadequate;
- local desktop, local web or hosted GUI distribution and target operating systems;
- tax/accounting requirements for a US individual across securities and event contracts.

### Preserve unless separately disproved

- raw evidence separate from summaries;
- point-in-time acquisition and as-of rules;
- citations, provenance and transformation lineage;
- explicit unknown/reject/inconclusive outcomes;
- immutable audit and attributable actors/policies/configurations;
- credentials outside model context and least-privilege provider/venue adapters;
- fail-safe handling of stale data, uncertain submissions, duplicate/partial fills and reconciliation;
- exact-candidate identity, reproducible replay and rollback/recovery evidence;
- honest negative results and no promotion from mocks, schemas or component tests to product claims.

## 4. Reusable formal research contracts

The replacement needs formal research, but research should be proportional to the decision. The repository already contains a strong contract vocabulary that can be made provider- and asset-neutral.

### RC-1: decision packet

Every consequential research item names the product decision it informs, options, inherited decisions, authority owner, budget, stopping rule, deliverable, falsifiers and what remains unknown. Deep research supports a decision; it does not silently make one.

### RC-2: point-in-time evidence envelope

Retain source identity, raw bytes or immutable reference, publisher/venue, publication/event/retrieval timestamps, as-of boundary, acquisition route, license/use constraints, transformations, schema/parser versions and quality failures. Keep rejected and ambiguous inputs. Never reconstruct historical knowledge from revised/current data without labeling it.

### RC-3: frozen evaluation manifest

Before outcomes are inspected, freeze candidate revision/configuration, population/universe, hashes, split rules, references, metrics, thresholds, costs, baselines, sampling, coverage requirements and failure policy. A material change invalidates or versions the evaluation; it does not rewrite the pass line.

### RC-4: independent reference and label contract

Keep tuning and evaluation evidence separate. Preserve false negatives, rejected cases and zero denominators. A zero denominator is unmeasured, not 100 percent. Measure reference/evaluator error and retain adjudication disagreement. Model consensus is not independent truth.

### RC-5: strategy/economic contract

Define eligible instruments/events, capital schedule, entry/exit/settlement rules, horizons, capacity, costs, fees, spread/slippage, latency, funding/collateral, taxes/accounting scope, benchmark cash flows, drawdown/loss budget, tail outcomes and uncertainty before evaluating returns. Include open, failed, resolved and censored cases. Report net return and capital-days, not only win rate or gross P&L.

### RC-6: provider and installed-route contract

Evaluate exact API/SDK/model/CLI versions through the real application route. Include typed fidelity, semantic loss, calibration/discrimination, abstention/refusal, evidence links, latency distributions, context limits, retries/fallback, privacy, full cost and failure behavior. HTTP mocks establish adapter logic, not provider qualification.

### RC-7: market/venue conformance contract

For each venue and asset class, pin instrument identity, market lifecycle, order types, price/size units, fees, collateral/cash, partial fills, cancel/replace, settlement/resolution, corrections/disputes, calendars, halts, rate limits, account eligibility and reconciliation. A stock book parity test cannot certify event-contract settlement.

### RC-8: promotion ladder

Record separate evidence for unit/property tests, deterministic replay, simulation, official demo/test venue, shadow/live-data observation, paper or venue-equivalent execution, limited live, and broader live operation. Each step has exact entry/exit gates. Elapsed time and order count do not replace coverage; paper/demo P&L does not establish production profitability.

### RC-9: operational and UX acceptance

Test the real composition and the shared application API through GUI and CLI. Cover install, credential setup, startup/restart, degraded dependencies, audit inspection, emergency stop, recovery, accessibility, explanation/provenance and refusal states. A backend endpoint is not a usable operator product.

### RC-10: evidence promotion rules

Never silently promote:

- schema validity to factual or financial correctness;
- component speed to end-to-end speed;
- a backtest to prospective yield;
- mocked provider behavior to installed-route qualification;
- demo/paper operation to live authority;
- merged code to accepted architecture;
- one market's semantics to another market;
- model confidence or agreement to ground truth;
- an empty run to a successful strategy outcome.

## 5. Owner and developer decision boundary

| Decision class | Owner decides | Developer decides inside accepted envelope | Escalation trigger |
|---|---|---|---|
| Product outcome | Target user, asset classes, first venues, UX promise, live/autonomy ambition, month-level priority | Slice decomposition and implementation sequence | Changes user, market, authority or success definition |
| Economics/risk | Yield definition, benchmark, capital/loss budget, acceptable downside, spending ceilings | Metrics implementation, telemetry and optimization experiments | Metric incentives conflict or limits must increase |
| Compliance/authority | Eligible reference profile, acceptable authority profiles, non-delegable controls after research/legal input | Policy engine design, state machines and conformance tests | New jurisdiction/account/venue, legal uncertainty or weakened control |
| Architecture | Owner accepts only load-bearing cross-product boundaries and irreversible commitments | Languages, frameworks, module boundaries, algorithms, schemas and hot-path choices supported by benchmarks | Choice changes authority, portability, data ownership, external contract or operating cost ceiling |
| Data/providers | Source classes, privacy/licensing stance and hard budget | Exact vendor/SDK, batching, caching, storage and fallback based on measured utility | Paid service, new disclosure, license restriction or degraded evidence |
| Strategies/models | Strategy activation class and authority; provider families only when privacy/spend/product semantics change | Candidate generation, route experiments and qualification implementation | Material strategy revision, direct authority, unsupported claim or budget expansion |
| GUI/CLI | Required workflows, target users/platforms and consequential approval semantics | UI framework, information architecture, components, CLI design and optional TUI recommendation | UI creates alternate authority, remote exposure or drops required workflow |
| Release | Acceptance gates, live activation, cap increases and exception policy | Build, test, package, produce evidence and recommend release | Unmet gate, exception, incident, irreversible migration or live credential request |

Developers should have real discretion. An issue should not prescribe a language, framework, class layout or optimization unless the research and accepted contract require it. Conversely, “developer choice” cannot decide product intent, compliance eligibility, capital authority, private-data disclosure, spend increases, release exceptions or its own acceptance.

## 6. Proposed Linear outcome parents

Do not publish the old feature list as independent implementation tickets. Publish outcome parents with inherited decisions and bounded developer discretion.

1. **Successor product and economic contract** (owner-led)  
   Freeze US-individual scope, month target, net-yield/benchmark/loss definitions, explicit non-goals and acceptance vocabulary.

2. **Multi-market evidence and replay plane** (developer-led)  
   Deliver a versioned point-in-time envelope, source registry, raw store, normalized events, replay and data-quality/cost telemetry for stocks and Kalshi demo.

3. **Instrument, portfolio and accounting semantics** (joint)  
   Define shared primitives and market-specific stock/event-contract lifecycle, fees, collateral, P&L, resolution/settlement and reconciliation.

4. **Strategy research and economic qualification laboratory** (developer-led)  
   Implement frozen manifests, baselines, falsifiers, walk-forward/prospective evidence and honest reject/inconclusive outcomes.

5. **Provider-neutral decision laboratory** (developer-led)  
   Compare deterministic baselines, Jev/System One, OpenAI Decisions and selected alternatives through one versioned application contract.

6. **Configurable authority, execution and recovery** (owner gates, developer design)  
   Implement proposal-only, model-assisted and bounded-direct profiles; Kalshi demo first; credentials, non-delegable controls, emergency stop, reconciliation and recovery remain exact-candidate gates.

7. **GUI and CLI operator product** (owner workflows, developer UX/technology)  
   Ship the required GUI and scriptable CLI on one application API; expose evidence, uncertainty, positions, economics, policies, health, audit, refusal and intervention. Evaluate TUI later.

8. **Migration and performance frontier** (developer-led)  
   Classify legacy components retain/measure/rework/replace; benchmark complete workloads; select languages and kernels by observed bottlenecks and operational cost.

9. **Qualification, packaging and release evidence** (owner acceptance, independent verification)  
   Integrate real surfaces/adapters, adversarial cases, platform packaging, exact-candidate manifests and the approved progression ladder.

## 7. Required fields for every parent and child

- parent outcome and authoritative product clause;
- exact current state and source revision/configuration;
- intended measurable outcome and explicit non-goals;
- inherited owner decisions and unresolved decisions;
- evidence class, sources and limitations;
- research budget, stop condition and Parallel run ID when deep research is required;
- relevant Context7 API/library version;
- dependencies expressed as exact contract fragments, not only ticket links;
- authority boundary and discretion class;
- falsifiers and rollback/invalidation conditions;
- deliverables and executable completion gate;
- independent verification owner/method;
- promotion level claimed and levels explicitly not claimed.

## 8. Smallest decision sequence before publication

Already decided: replacement baseline, stocks plus prediction markets, US individual, Kalshi demo first, GUI plus CLI, one-month coherent slice, eventual bounded-direct authority after qualification.

Still needed, in order:

1. Owner defines the economic objective: horizon, capital base, benchmark, loss/drawdown budget and whether taxes are inside the first net-yield metric.
2. Developers return a researched engine-shape recommendation: shared core/adapters versus dual market engines, with migration and end-to-end benchmark evidence.
3. Owner accepts the load-bearing boundary and any irreversible external contracts; developers retain language/framework discretion.
4. Owner selects GUI distribution and required operating systems; developers select the UI stack.
5. Authority/compliance research identifies non-delegable controls and legal-review points; owner accepts the bounded-direct profile contract.
6. Parent owners publish child issues only after their upstream contract fragments are accepted. Mechanical children inherit accepted research instead of re-running generic deep research.

## 9. Immediate planning implication

The prior corpus should become a **requirements and evidence quarry**, not the successor specification and not a deletion target. Mine it for threats, invariants, fixtures, real provider/venue edge cases, acceptance failures and dated observations. Do not preserve an implementation merely because it has tests, and do not discard a control merely because its original product scope is superseded.

The replacement should be judged as an integrated product: a US individual can inspect evidence in a GUI, reproduce decisions, compare strategy/provider economics, use the CLI, exercise stock and Kalshi-demo paths, understand uncertainty and failures, and observe the exact authority profile and audit trail. Kernel benchmarks and autonomous orders are supporting evidence, not the user outcome by themselves.

## Sources audited

Primary repository contracts: `PRODUCT.md`, `AGENTS.md`, `CLAUDE.md`, `docs/README.md`, `docs/PLAN.md`, `docs/REGISTER.md`, `docs/product-planning.md`, `docs/product-operating-contracts.md`, `docs/product-tax-contract.md`, `docs/product-acceptance.md`, `docs/product-delivery.md`, `docs/security-architecture.md`, and `docs/security-hardening-backlog.md` at the source revision above.

Research/evidence sources: `docs/research/research-report.md`, `research-supplement.md`, `llm-hybrid-trading.md`, `upside-and-roi.md`, `gate-feasibility-and-value-2026-09-06.md`, `dec03-strategy-selection-2026-08-14.md`, and RW-26 through RW-37 evidence/research records. Historical scope sources were read only as superseded context: `cockpit-plan-original.md`, `capability-expansion-master-plan.md`, `enhancements-and-additions.md`, and `draft-discord-control-plane.md`.

Current external API facts used in the replacement packet were checked on
2026-10-16 against:

- [Kalshi demo environment](https://docs.kalshi.com/getting_started/demo_env), which
  documents mock funds, separate credentials and the Trade API v2 demo roots;
- [Polymarket US API overview](https://docs.polymarket.us/trader-guide/api-overview),
  which distinguishes the personal-account Retail API from exchange-participant APIs;
- [OpenAI Decisions](https://developers.openai.com/api/docs/guides/decisions), which
  described the API as public beta, `gpt-6-luna` as the only listed model and Python
  SDK 3.26.0 or later for its examples.

Context7 was used for the current OpenAI Python SDK surface. These are
source-documented facts, not local qualification, and remain moving targets that must
be pinned again at implementation and evaluation time.

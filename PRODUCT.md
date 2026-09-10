---
title: Cockpit product contract — working draft
status: reference
last-verified: 2026-09-09
---

# Cockpit product contract — working draft

**Planning package:** RW-25. **Stage:** grill in progress; product decisions pending.

This document is the candidate product contract being developed with Lucas. It is
not yet ratified and does not supersede [PLAN](docs/PLAN.md), change the development
order, or grant runtime authority. The canonical nine invariants remain in
[CLAUDE.md](CLAUDE.md), with the Codex boundary in [AGENTS.md](AGENTS.md).

The [planning record](docs/product-planning.md) separates explicit owner decisions,
recommendations, unanswered questions, and evidence dependencies. Commit checkpoints
preserve that distinction; committing an unanswered recommendation does not approve it.

## Confirmed problem

Lucas identified the principal frustration as: “The runs finish but never produce
useful analysis or paper trades.” Product discovery must address the usefulness of
completed runs and the path toward paper execution, rather than equating technical
completion with a useful investing workflow.

## Candidate product structure

The discussion considers three connected functions: awareness, strategy research,
and controlled execution, with observed outcomes feeding subsequent evaluation.
The first complete release is an integrated autonomous research-to-paper-trading
workflow, with optional operator interaction through Discord or the supervisor/host
(PD-007, clarifying PD-004/PD-005). Useful analysis may be delivered along the way;
an awareness-only result does not fulfill that release goal.

Round 1 establishes unattended routine operation with discretionary inspection and
interaction (PD-005/PD-007). Individual paper-trade approval is no longer the selected
product target; the bounded paper-autonomy policy remains to be designed and reviewed.
Initial research breadth is a core coverage list plus bounded candidate discovery
and human ingestion (PD-006). Detailed module responsibilities and acceptance criteria
remain open. This product target does not satisfy or relax the existing
strategy-evidence and execution gates.

## Daily operator experience — decided direction

For the initial paper-testing MVP, Lucas wants the cockpit to operate autonomously
and to check Discord at his discretion for bot performance, news, and possible
engine improvements. A mandatory 10–20-minute daily review is not the selected
design target.

Routine operation should not depend on Lucas opening Discord, starting a daily
research session, or individually approving each routine paper trade. Lucas can
also inspect, question, and direct the system through Discord or its supervisor/host.
GrokBot and a Hetzner VPS are owner-provided examples. The first release supports
Windows and Linux, with Lucas's Linux box as the primary deployment target (PD-027).
Exact supervisor integration, interaction commands and permission boundaries remain
to be specified.

The paper-autonomy policy, exception escalation, and recovery behavior remain to be
specified. This is a product design target, not a change to current mode or approval
authority. Replacing existing individual approval requires a separately scoped and
adversarially reviewed design consistent with the canonical safety constraints;
the current runtime remains unchanged. Engine improvements follow the development
and release boundary below; strategy activation and hard-limit changes retain their
separate owner approvals.

## Engine development and software releases — decided direction

Agents investigate, implement and test improvements in isolated revisions; Lucas
approves installing a new software release (PD-030, Round 7a Q22). Routine operation
continues on the approved version while changes are prepared. Automatic deployment
of maintenance changes is not selected. Software-release approval does not replace
the separate approvals for strategy activation or increases to hard caps.

The release contract must define the reviewable artifact, validation evidence,
installation, rollback and audit behavior. Exact development permissions and change
scope remain to be specified; isolated development cannot mutate the running engine's
policy or state. This product direction grants no runtime implementation or deployment
permission during the documentation-only grill.

## Strategy suite — decided direction

The MVP should support a suite of strategies that model reasoning can evaluate and
employ through an escalation architecture (PD-012, Round 3 Q7). This supersedes the
assistant's proposed one-strategy first-release target. Launch with two or three
distinct strategies, selected through a bounded feasibility comparison including the
existing cash-merger strategy, then approved by Lucas (PD-033, Round 7b Q25). Each
needs its own evidence and evaluation; parameter variants do not count as additional
strategies. The exact roster, activation criteria and portfolio interactions remain
to be specified.

The engine decides holding horizons (PD-036, Round 7b Q26). No universal days-to-months
restriction is selected. Horizon decisions must fit the activated strategy's evidence,
data/execution capabilities and approved risk policy. The feasibility comparison and
strategy contracts establish which horizons the initial suite can support; this answer
neither excludes intraday strategies nor certifies every possible trading horizon.

The initial executable scope is cash-funded, long-only US stocks and ETFs (PD-026,
Round 6a Q18). The suite can contain multiple strategies within that scope. Short
positions, margin borrowing, options and other asset classes are outside the first
paper release's trading scope; research can examine broader markets and events.
Specific instrument/ETF eligibility, cash and settlement handling, and portfolio
exposure rules remain specification work. Coverage or discovery alone does not grant
an instrument execution eligibility.

Newly researched stocks and ETFs may enter paper-execution eligibility automatically
under owner-approved rules once evidence, instrument, strategy and risk checks pass
(PD-041, Round 8b Q30). Individual symbol approval is not required by the product
target. Admission must be attributable and validated; eligibility rechecks and expiry
remain part of the execution contract. Implementing this requires a separately scoped,
reviewed replacement for the current static allowlist, preserving the single order path.

Agents develop and test candidate strategies; Lucas, as owner, approves activation
of a new strategy or a material revision (PD-016, Round 4a Q10). Routine selection
and paper operation within the active suite remain autonomous under its standing
policy. The exact review evidence and definition of a material revision remain open.

The current DEC-03 cash-merger selection remains the existing implementation baseline.
The suite target requires explicit reconciliation of DEC-03 and affected contracts
before product ratification and subsequent scoped implementation. It does not silently
make additional strategies executable or grant a model direct order authority.

Recommended design: give each strategy a versioned contract for required evidence,
applicability, permitted actions, risk limits and evaluation. Model reasoning can assess
which validated strategy fits a case and draft its decision; deterministic checks
enforce eligibility and aggregate portfolio constraints through the existing order
boundary. Strategy-development scope, activation evidence, capital allocation,
conflict handling and retirement require explicit policies rather than being inferred
from model confidence.

### Expansion after the first release

Two or three strategies is the launch scope, not a permanent ceiling. Lucas explicitly
requires future expansion and reminders to keep it visible (PD-034). Keep expansion
in the roadmap and product review requirements. A continuing candidate queue should
record new strategy opportunities, missing evidence and the next evaluation step;
activation still follows the owner-approved process and existing resource/risk caps.

Send a monthly expansion summary plus candidate-ready notifications for owner
activation review (PD-040, Round 8a Q29). Include queue progress, missing evidence
and next steps. Exact scheduling, duplicate suppression and delivery/retry rules
remain to be specified; no external reminder is scheduled during this grill.
Evaluate additions for incremental opportunity, results after costs and overlap with
the existing portfolio. A larger strategy count alone is not evidence of higher ROI.

## Identity and authorized access — decided direction

The MVP identifies who is using the program and making decisions across Discord
and other control surfaces. It supports an owner and explicitly authorized users,
with setup for the identities and credentials needed to control access (PD-017).
Actual credentials and deployment-specific identity/access configuration stay out
of tracked GitHub content; tracked documentation and examples describe schemas and
use placeholders. Secrets remain in the OS credential store under the existing contract.

Authentication establishes the caller's identity; authorization determines which
actions that identity may perform. PD-016 reserves strategy activation to the owner.
Additional users receive owner-defined roles with explicit per-action permissions,
enforced consistently across Discord and other surfaces (PD-020, Round 4b Q13).
Strategy activation remains owner-reserved unless explicitly delegated. The concrete
role/action matrix, onboarding, revocation, and Discord-server scoping remain to be specified. The request
does not select a public account service or multi-tenant SaaS architecture.

Recommended design: bind each supported external identity to a stable cockpit principal
and enforce a common permission policy at the application action boundary. Record the
human requester, executing service/agent, surface, action, outcome, and applicable policy
or approval reference in the audit trail. Autonomous actions identify the agent/job and
standing authorization; they must not fabricate a contemporaneous human approval.
Discord display names, channel membership, or a model's claimed identity are not sufficient
authorization. Exact identity bindings and audit fields remain specification work.

The current single-operator setting and shared loopback API credential do not themselves
provide this multi-user identity contract. Extend them through a scoped, adversarially
reviewed authority design; do not implement this requirement by merely widening the
operator list or sharing one credential among users.

## Operating limits and failure behavior — decided direction

The owner approves hard outer limits for inference spending/subscription use and
paper capital/risk. The engine allocates work and selects active strategies within
those limits; increasing a hard limit requires owner approval (PD-021, Round 4b Q14).
Exact ceilings, accounting periods, reservations and allocation rules remain to be
specified. The selected mechanism must enforce aggregate limits across simultaneous
jobs and strategies, not only evaluate each allocation in isolation.

Full investment-tax calculations are required in the MVP (PD-042, Round 8b Q31),
expanding PD-022's earlier tax consideration. This replaces the proposal to provide
only a ledger and configurable assumptions while deferring the calculation engine.
Supported jurisdictions, account types and tax years must be explicit; Round 8c Q32
requests the initial jurisdiction/account context. No universal rate or jurisdiction
is inferred, and preparing or filing a complete personal return is not selected.

Recommended calculation contract: use complete applicable transaction/account inputs,
verified versioned rules and deterministic calculations, with inspectable rule/source
citations, intermediate results and known uncertainties. Required outside-account
inputs must be explicit. Missing context cannot become a zero-tax assumption or an
unsupported claim of complete liability. Tax-aware execution constraints and financial
estimates remain distinct; rule changes follow the selected software/policy approvals.

When a required model/feed fails or quota is exhausted, try only approved, sufficiently
capable fallback routes within budget. If requirements still cannot be met, suspend
affected unsafe actions, continue safe monitoring, and report the reason (PD-023,
Round 4b Q15). Fallback preserves permissions and task requirements.

When required inputs are unavailable, block new or increased exposure for affected
actions, retain monitoring of existing paper positions, and permit only preapproved
protective actions whose own requirements still pass (PD-032, Round 7a Q24). This
does not authorize an exit based on missing data or bypass the order controls. The
action matrix must specify treatment of unfilled entries, partial fills, uncertain
submissions, supported risk-reducing actions and reconciliation. Exact protective
actions and their evidence conditions remain specification work.

After a transient dependency failure, automatic recovery is permitted under an approved
policy once dependency health, evidence freshness, resource limits and paper
reconciliation pass the required checks (PD-031, Round 7a Q23). Explicit human halts
and integrity or authority holds require authorized human release. Unresolved causes
or reconciliation results keep affected actions suspended. Recovery must respect all
remaining holds and cannot elevate authority beyond the approved operating policy.
Detailed checks, durable transitions and audit evidence need a scoped design reconciled
with the current fail-closed controls; this decision does not alter runtime recovery.

## Model escalation — decided direction

Use less expensive model capacity for high-volume interpretation and triage, with
flagged decisions escalated to more capable reasoning when needed (PD-013). Lucas's
example sequence is **GPT 5.6 Luna → Muse 1.3 Spark Contributor → GPT 5.6 Sol →
GPT 6 Astra, if needed**. The objective is to conserve money and subscription quota
while sustaining reliable reasoning. The example does not establish exact provider
identifiers, availability on a chosen CLI/account, or measured savings.

Routing and escalation are dynamic, based on subscription quota, model diversity,
and model capabilities/intelligence (PD-018, Round 4a Q11). Lucas plans to use ChatGPT
Pro plus OpenCode Go next month. This is the intended subscription context, not a
verified entitlement or a decision to hardcode the earlier model ladder. The exact
available models, adapters, usage signals and plan limits require verification before
implementation; the route policy must support configuration changes over time.

Recommended architecture: separate configurable task roles from provider/model pins;
keep mechanical parsing, deduplication, timestamp/unit normalization and schema checks
deterministic where possible, and use models for semantic extraction, interpretation,
strategy reasoning and review. Define escalation triggers, permitted routes, termination,
usage ceilings, unavailable-provider behavior and evidence-bearing handoffs explicitly.
Use the available qualifying routes rather than requiring every item to traverse every
tier. Record which quota observations are known, estimated or unavailable and how fresh
they are. Filter routes by required capability, permissions and quality constraints
before optimizing quota/cost; uncertainty or quota exhaustion must not silently relax
those requirements or switch to an unapproved paid route.

Higher-tier review should retain access to supporting original evidence and unresolved
contradictions, not only the previous model's conclusion. Sample ordinary low-tier
decisions as well as escalations to measure confidently missed cases. Evaluate end-to-end
quality, latency, repeated context, retries, cost and quota consumption; the optimal
route and stability are empirical questions, not consequences of model count. Model
diversity is a routing objective; whether it improves an independent review must be
measured rather than inferred from different model or provider names.

The model-role allocation and runtime authority require reconciliation with AGENTS.md,
the current Codex/Claude boundaries and the paper-autonomy design. The selected product
direction changes none of those permissions in the running application.

## Candidate and paper-position lifecycle — decided direction

Potentially interesting cases with insufficient evidence receive a bounded investigation,
then enter a visible monitoring state that names the missing evidence and the condition
for another investigation (PD-014, Round 3 Q8). Keep hard disqualifications distinct from
awaiting evidence. Escalating to another model does not itself supply a missing fact.

The MVP manages the complete paper-position lifecycle: entry, monitoring, policy-permitted
adjustments or cancellations, exit, reconciliation and recorded outcomes (PD-015,
Round 3 Q9). Individual policy limits, interrupted/partial execution behavior, exception
handling and operator interventions remain to be specified. This scope preserves the
autonomous operating target while requiring explicit controls for every lifecycle action.

## Research inputs — decided direction

The MVP combines an operator-selected core coverage list, a bounded candidate-discovery
queue, and human ingestion (PD-006, Round 1 Q3).

The first release accepts tickers, links, ideas, questions, documents, screenshots,
and images (PD-009, Round 2 Q5). Document and visual inputs are part of the selected
MVP scope. The exact supported file formats and size limits, intake surfaces, priority,
duplicate handling, and provenance requirements remain open. User-submitted material
enters the evidence workflow; submission does not certify its claims or grant
execution eligibility. Research inclusion remains separate from the existing
operator-controlled execution allowlist.

Recommended acceptance detail for the later specification: retain the submitted input
separately from extracted text or observations; show what was extracted and where it
came from; expose unreadable, ambiguous, or unsupported content. A citation to a
submitted screenshot identifies the evidence received, not proof of its original
publisher or the truth of its contents. Exact extraction and verification mechanisms
remain design work.

## Research cadence — decided direction

The cockpit investigates or revisits cases in response to relevant new evidence,
Lucas's requests, and bounded scheduled checks (PD-011, Round 2 Q6). Scheduled checks
cover freshness and gaps; the product should avoid repeatedly redoing unchanged
research without a reason. Exact relevance rules, priority, schedules, and work
budgets remain to be specified. This target does not change the current scheduler
or satisfy the prerequisites of the existing event-driven implementation packages.

## Hosting, plugin access and agent execution — decided direction

The first release supports operation on both Windows and Linux, with deployment
focused on Lucas's Linux box (PD-027, Round 6a Q19). Windows support is release scope,
not just a development convenience. The Linux box is not assumed to be a VPS or a
particular provider. Installation, credential provisioning, unattended startup,
shutdown/restart, recovery and diagnostics need acceptance on both supported systems;
the existing four-cell DEC-09 test matrix remains required. Exact Linux distribution
and host configuration remain deployment details. Supporting both platforms does not
select simultaneous active replicas of one portfolio.

Lucas selects a persistent cockpit service plus integrations (PD-028, Round 6b Q20),
resolving PD-019's deferred application-ownership decision. The cockpit owns research,
strategy and paper-position state, investing schedules, policy and deterministic
controls. Discord and host/plugin/MCP integrations use the same application services
and permissions. Bounded model-job execution is a separate responsibility; a qualified
Recursion adapter may provide it without taking ownership of the investing application.

The MVP must work standalone. Working Recursion integration is not a first-release
gate (PD-029, Round 6b Q21). Design the shared execution contract now and integrate
Recursion when real compatibility checks pass. The standalone executor must deliver
the selected dynamic model-routing and autonomous workflow capabilities; an inert
adapter or fake executor cannot complete the product. This preserves PD-010/PD-014's
early integration design while avoiding a release dependency on Recursion readiness.

Job inputs, permitted capabilities, output schemas, provenance, usage limits,
cancellation, retries and restart behavior need a tested contract. Credentials remain
in the OS credential store and outside model context. Broker credentials and the
deterministic order path stay cockpit-side; plugin/MCP access grants no direct route
from a model to the broker. The separately scoped paper-autonomy/model-authority
design is still required before implementing that product target.

A persistent service can expose multiple integrations without requiring multiple
microservices. Exact process packaging, protocols, job-envelope fields and conformance
thresholds remain specification work. The [architecture comparison](docs/product-architecture-options.md)
preserves the inspected source baseline and alternatives, records the selected ownership
and release dependency, and distinguishes them from proposed technical contract details.

## Research output and source inspection — decided direction

Research produces short Discord updates backed by detailed, inspectable research
cases (PD-008, Round 2 Q4). Cases include citations so Lucas can examine the sources
and the data-ingestion methods used to support the analysis.

Source inspection is part of the product experience. The operator should be able
to follow the important claims back to their supporting evidence and understand
how that evidence entered the cockpit. Detailed case layout, citation granularity,
and ingestion-method presentation remain to be specified.

Recommended provenance detail for the later specification: identify the source and
acquisition route, distinguish publication time from retrieval time, link the retained
input where available, and describe relevant extraction or transformation steps and
known coverage limitations. These are proposed ways to meet the inspection requirement;
the exact fields and interface are not yet ratified. Existing raw-input separation
and secret-handling requirements continue to apply.

## First-release success — decided direction

The paper MVP is complete when the integrated product is useful and reliable and
its strategy results are credibly evaluated with honest reporting (PD-024, Round 5
Q16). Positive returns or demonstrated profitability are not required for product
completion. The workflow must deliver the agreed research and input experience,
operate its approved strategy suite and complete the real paper-position lifecycle,
including reconciliation and failure handling. It also delivers the selected full
investment-tax calculations for the configured, explicitly supported tax context.

Evaluation uses predeclared baselines and cost assumptions and preserves uncertainty,
unfavorable outcomes and failures. This completion criterion does not waive existing
strategy-evidence, platform, activation or pre-live gates, and a working product is
not itself evidence of financial advantage. Exact evaluation thresholds, observation
windows and evaluator responsibilities remain to be specified.

The primary Linux deployment must complete at least 20 market days of unattended
paper observation plus the required workflow coverage and applicable strategy-evidence
gates (PD-039, Round 8a Q28). This is an operational minimum, not proof of profitability.
Longer holding horizons or missing evidence can require more time. Predeclare build
identity and observation continuation/reset rules; neither elapsed time nor forced
trades can substitute for missing acceptance evidence.

### Economic objective beyond MVP completion

Lucas wants eventual profitable growth that exceeds his expenses, currently estimated
at roughly $210/month (PD-035, Round 7b Q25). Record this as an explicit economic
objective and track progress toward it. It does not replace PD-024's working-product
and honest-evaluation completion criterion, establish a spending cap, or promise a
return. Paper P&L is evaluation evidence, not cash available to pay actual bills.

Recommended reporting: show trading P&L after trading costs, the owner expense-coverage
benchmark, and the resulting surplus or shortfall without subtracting the same cost
twice. Preserve realized and unrealized components, losses, capital employed and the
observation period. Lucas identifies the approximately $210/month as the combined
ChatGPT Pro and OpenCode Go subscriptions (PD-037), not an all-inclusive operating
cost estimate or independently verified current pricing.

Lucas reports $500/month in student funding for the coming year and wants to contribute
an unspecified percentage of monthly income while growing the balance. He is likely
to transfer at least $2,000 (PD-038). Use $2,000 as a provisional paper baseline drawn
from that intention, not as an approved live allocation or final deposit amount. The
contribution percentage, timing and continuation after the funding period remain unset;
the full funding stream is not assumed available to invest. These are owner-specific
evaluation assumptions, not hardcoded application defaults.

At a static $2,000 starting balance, $210 / $2,000 = 10.5% per month is the arithmetic
subscription-coverage hurdle after trading costs, before additional growth and any
other costs or taxes. It is not an expected or demonstrated return. Additional costs
and tax treatment remain to be specified. Report deposits and withdrawals separately
from strategy P&L so contributions cannot be counted as investment performance. Use
the same external cash flows when comparing strategy and baseline portfolios.

## Automated quality evaluation — decided target

Aim for fully automated research-quality and model-routing evaluation (PD-025,
Round 5 Q17). Routine quality assessment should not require Lucas or another human
to review a sample of every evaluation cycle. This replaces the assistant's proposed
routine human sample review. Optional operator usefulness feedback remains possible;
owner approval of strategy activation is a separate decision under PD-016.

Recommended design, subject to specification: combine deterministic checks and
production-path replays with source-grounded reference cases and separately evaluated
model judges. Assess accepted, rejected, low-tier-dismissed and escalated cases so
cheap early filtering cannot hide missed opportunities or unsupported conclusions.
Keep evaluation cases separate from tuning examples, test the evaluator's own error
modes, and report coverage, quality, latency, cost and quota use. Model agreement is
not sufficient evidence of correctness. Return an explicit unverified result when
the evaluator lacks adequate evidence; do not silently treat it as a passing score.

The automation target is not an established capability or a waiver of existing
independent evidence requirements. In particular, current human-source labelling and
held-out evidence gates need an explicit disposition during contract reconciliation.
The evaluation design must state what can be automated, how reference judgments are
established, and which claims remain unverified. No daily human QA obligation is added
to the selected autonomous product experience.

The [release-evidence proposal](docs/product-acceptance.md) translates the selected
workflow into application, adapter, deployment and outcome checks. It records the
selected pilot/reminder cadence and distinguishes proposed technical evaluation
contracts that still need final review.

## Sections to resolve through the grill

1. Product purpose, initial user, and first complete experience.
2. Daily operator journey and acceptable review effort.
3. Awareness and research coverage, evidence, and output contracts.
4. Candidate lifecycle and transitions into controlled paper execution.
5. Separate development, research, and investment autonomy permissions and harness responsibilities.
6. Quality, usefulness, operational, and strategy evaluation.
7. First-release acceptance, exclusions, and progression criteria.

Existing paper-first restrictions, source/evidence requirements, and execution
controls remain in effect throughout planning. Proposed changes must be explicitly
identified and reviewed through the applicable existing process.

## Ratification and document ownership

The intended division, subject to final owner review, is: this contract owns product
purpose and acceptance; PLAN owns implementation order; REGISTER owns work claims;
existing subsystem contracts and evidence own their technical and safety requirements.
RW-25 must reconcile these documents before presenting the final branch for publication
and merge. Historical evidence stays historical; no new empirical PASS follows from
document ratification.

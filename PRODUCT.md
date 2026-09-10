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
GrokBot and a Hetzner VPS are owner-provided examples; the exact supervisor integration,
deployment host, interaction commands, and permission boundaries remain to be selected.

The paper-autonomy policy, exception escalation, and recovery behavior remain to be
specified. This is a product design target, not a change to current mode or approval
authority. Replacing existing individual approval requires a separately scoped and
adversarially reviewed design consistent with the canonical safety constraints;
the current runtime remains unchanged. Observing possible engine tweaks
does not by itself authorize autonomous changes to code, strategy, or risk policy.

## Strategy suite — decided direction

The MVP should support a suite of strategies that model reasoning can evaluate and
employ through an escalation architecture (PD-012, Round 3 Q7). This supersedes the
assistant's proposed one-strategy first-release target. The strategy roster, supported
instruments/horizons, selection and activation criteria, and interactions among
strategies remain to be specified.

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

## Hosting, plugin access and agent execution — open architecture decision

Lucas expects the cockpit to work with other agents and identified Project Recursion
as a possible suitable harness if its architecture can support configured CLIs and
credentials (PD-010). This is a candidate integration to evaluate, not a selected
deployment dependency or a verified claim about Recursion's current capabilities.
Lucas reinforced during Q8 that Recursion and the cross-model architecture should be
considered early, while Recursion is being developed; harness compatibility must be
part of product architecture discovery rather than deferred until after implementation.

Recommended architectural boundary, subject to the autonomy round: the cockpit owns
research cases, input/evidence contracts, policy decisions, and the existing deterministic
risk/order controls; a harness supplies bounded agent-job execution and lifecycle services
through an explicit adapter. Job inputs, permitted capabilities, output schemas, provenance,
usage limits, cancellation, retries, and restart behavior need a tested contract. Credentials
remain in the OS credential store and outside model context; harness integration does not
give a model broker credentials or create an additional order route.

Lucas has not selected Recursion as a mandatory or primary host (PD-019, Round 4a Q12).
He describes the cockpit as a program using existing subscriptions to pursue profitable
financial decisions, considers a plugin/MCP design plausible, and wants its relationship
to standalone operation and Discord access resolved from the complete architecture.
Profitability is an objective for evaluation, not an established property of the system.

Compare three arrangements: a standalone cockpit with its own bounded model runner;
a plugin loaded by a host such as Recursion; and a persistent cockpit service with
Discord plus plugin/MCP access and an optional external agent-execution adapter.
For each, identify ownership of state, ingest/research schedules, model jobs, credentials,
permissions, recovery, deployment, testing and the deterministic order path.

Working recommendation, not ratified: keep durable investing state and domain controls
in a cockpit service, make Discord and plugin/MCP access adapters to the same services,
and define model-job delegation separately. A plugin is a packaging/integration choice;
the API/protocol surface, running process and owner of long-lived work are distinct
decisions. Recursion may supply compatible job execution or supervision without needing
to duplicate the investing core. Any exposed tools must respect the same permissions
and existing order boundary; an MCP adapter must not create a model-to-broker shortcut.

Evaluate Recursion's actual interfaces and the existing cockpit composition before
selecting this recommendation. The deployment/lifecycle comparison remains open; its
resolution must be explicit before final product ratification and implementation planning.

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

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

## Agent harness — candidate direction

Lucas expects the cockpit to work with other agents and identified Project Recursion
as a possible suitable harness if its architecture can support configured CLIs and
credentials (PD-010). This is a candidate integration to evaluate, not a selected
deployment dependency or a verified claim about Recursion's current capabilities.

Recommended architectural boundary, subject to the autonomy round: the cockpit owns
research cases, input/evidence contracts, policy decisions, and the existing deterministic
risk/order controls; a harness supplies bounded agent-job execution and lifecycle services
through an explicit adapter. Job inputs, permitted capabilities, output schemas, provenance,
usage limits, cancellation, retries, and restart behavior need a tested contract. Credentials
remain in the OS credential store and outside model context; harness integration does not
give a model broker credentials or create an additional order route.

Evaluate Recursion's suitability against those requirements before choosing reuse,
adapting the existing runner, or introducing a new dependency. Whether standalone operation
must remain supported and how responsibilities divide across repositories are open decisions.

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

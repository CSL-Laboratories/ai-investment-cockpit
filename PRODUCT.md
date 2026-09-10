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
Lucas selected an integrated research-to-human-approved-paper-trading workflow as
the first complete release (PD-004, Round 1 Q1). Useful analysis may be delivered
along the way; an awareness-only result does not fulfill that release goal.

The detailed module responsibilities, operator workload, research breadth, and
acceptance criteria remain open. This product target does not satisfy or relax
the existing strategy-evidence and execution gates.

## Sections to resolve through the grill

1. Product purpose, initial user, and first complete experience.
2. Daily operator journey and acceptable review effort.
3. Awareness and research coverage, evidence, and output contracts.
4. Candidate lifecycle and transitions into controlled paper execution.
5. Separate development, research, and investment autonomy permissions.
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

---
title: RW-25 product grill — decisions and recovery record
status: reference
last-verified: 2026-09-09
---

# RW-25 product grill

**Owner:** Lucas. **Facilitator:** Codex/Astra.
**Branch:** `work/rw25-product-grill`.
**Baseline:** `db354a246828a2f09e3245e30d66a1190943450a` (PR #84).
**Scope:** documentation-only product discovery and contract reconciliation.

This is the durable continuation point for the grill. The existing PLAN remains
current development authority while [PRODUCT.md](../PRODUCT.md) is a candidate.

## Session protocol

- Ask two or three consequential questions in one theme per round. Explain the
  recommended direction and the tradeoffs; allow free-form owner answers.
- Record explicit answers separately from inferred implications, recommendations,
  and empirical questions. Do not convert an inference into approval.
- Challenge contradictions and unsupported assumptions. Inspect code or commission
  a bounded evidence task when a question is factual rather than preferential.
- After each answered round, update the candidate product contract and this record,
  reconcile affected PLAN/REGISTER text, check the documentation diff, and commit
  a coherent local checkpoint on the WIP branch.
- Preserve amended decisions in Git history and identify what the new answer replaces.
- Keep commits local during the grill. Lucas directed publication and merge after
  the rounds finish; do not publish or merge an incomplete candidate.
- This session authorizes documentation work. It does not authorize application
  changes, live captures, provider spending, messages to external channels, service
  restarts, or changes to order authority. Existing specific permissions and gates
  retain their scope.

## Explicit owner statements and decisions

| ID | Status | Owner statement / scope |
|---|---|---|
| PD-001 | confirmed problem | Completed runs fail to provide useful analysis or paper trades. This is the primary frustration identified in the review. |
| PD-002 | confirmed process | Use an interactive product grill to develop the product direction and specifications. |
| PD-003 | authorized | Branch and commit documentation checkpoints as grill questions are answered; publish and merge after the rounds finish. |

The first-release shape, target daily effort, and initial research breadth are not
yet decided. Earlier assistant recommendations are not owner decisions.

## Working recommendations — not ratified

- Connect awareness, strategy research, and controlled execution through explicit
  input/output and refusal contracts, with outcome feedback into evaluation.
- Evaluate complete production workflows and useful output, alongside deterministic
  safety checks and the existing platform/evidence gates.
- Inspect actual delivered analysis before prescribing a prompt, schema, or feed fix.
- Investigate the candidate-selection population and rejected sources before assuming
  repeated empty screens will eventually unlock the existing strategy contract.
- Treat development autonomy and investment autonomy as separately specified authority.

## Grill rounds

| Round | Theme | State | Expected output |
|---|---|---|---|
| 1 | Purpose and first usable daily experience | questions prepared; awaiting owner | First-release goal, operator workload, initial research breadth |
| 2 | Research behavior and evidence | pending | Research questions, source/coverage policy, uncertainty and useful output |
| 3 | Strategy and candidate lifecycle | pending | Candidate stages, refusal/waiting states, transition contracts |
| 4 | Autonomy and control | pending | Explicit authority for development, research, and investment operations |
| 5 | Evaluation and feedback | pending | Outcome measures, independent checks, failure learning, acceptance examples |
| 6 | First release and progression | pending | Reconciled contract, bounded delivery packages, final owner review |

Round count is provisional. Add a focused round if an unresolved consequential
decision requires it; do not expand implementation simply because an idea was discussed.

## Round 1 — questions and recommendations

### Q1. What should count as the first complete product?

Recommendation: an integrated research-to-human-approved-paper workflow, with useful
analysis delivered as an early increment and all existing execution gates preserved.
This keeps the investing objective explicit while giving earlier increments independent
value. Alternatives are an awareness-only first release or a strategy-feasibility-first
research release. An integrated target is broader; it must not manufacture paper orders
to satisfy a milestone.

**Answer:** pending.

### Q2. What routine workload should remain for Lucas?

Recommendation: the cockpit researches within agreed bounds and prepares a concise
daily decision session, with roughly 10–20 minutes of routine review as a proposed
design target plus separate explicit trade approvals and exceptional interventions.
Alternatives are alerts with minimal routine review, or an interactive research
workbench for longer sessions. The time range is a preference proposal, not a measured
claim or a restriction on careful approval.

**Answer:** pending.

### Q3. How broad should initial research coverage be?

Recommendation: a focused operator-selected coverage list plus a separate bounded
candidate-discovery queue. Alternatives are research confined to the existing monitored
companies or broad market discovery from the outset. Broader coverage requires explicit
source, cost, relevance, and evaluation bounds. Research inclusion never grants execution
eligibility, and this question does not authorize changing the current watchlist setting.

**Answer:** pending.

## Evidence dependencies

- Actual recent delivered briefs and their source packets on the operational host
  have not been reviewed in this session. Their quality bottleneck remains unclassified.
- [The feasibility review](research/gate-feasibility-and-value-2026-09-06.md) records
  unresolved same-date cohort attainability and missing independent precision/recall
  and usefulness measurements. These are empirical questions, not preferences.
- The existing canonical safety constraints remain in [CLAUDE.md](../CLAUDE.md) and
  [AGENTS.md](../AGENTS.md); detailed empirical and pre-live gates remain in PLAN and
  the existing subsystem documents.

## Resume here

Present Round 1 and wait for Lucas's answers. Record those answers and their implications,
update PRODUCT.md, validate the diff and commit the answered-round checkpoint before
moving to Round 2. Do not interpret silence or recommendation selection defaults as answers.

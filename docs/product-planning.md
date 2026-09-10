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
| PD-004 | integrated scope retained; individual-approval assumption superseded by PD-007 | Initial Q1 answer selected integrated research-to-approved-paper trading. Later clarification establishes autonomy across the routine paper workflow. |
| PD-005 | decided — Round 1 Q2, clarified by PD-007 | The initial paper-testing MVP should run autonomously; Lucas checks Discord at his discretion for performance, news, and possible engine tweaks. No mandatory daily review session. |
| PD-006 | decided direction — Round 1 Q3 | Initial research combines core coverage, bounded candidate discovery, and human ingestion. Exact human-input formats and handling remain to be specified. |
| PD-007 | decided — Round 1 autonomy clarification | The system runs autonomously while remaining interactive through Discord or its supervisor/host. In the initial paper-MVP context, the product target includes routine autonomous paper execution rather than individual paper-trade approval. GrokBot and Hetzner are examples, not selected integrations or deployment authorization. |

The integrated autonomous paper-MVP direction, optional operator interaction, and
initial research breadth are decided. The precise autonomous policy, intake and
interaction contracts, and acceptance criteria remain open. Earlier assistant
recommendations are not owner decisions unless explicitly accepted.

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
| 1 | Purpose and first usable daily experience | complete — PD-004 through PD-007 | Integrated autonomous paper MVP, optional Discord/supervisor interaction, core/discovery/human inputs |
| 2 | Research behavior and evidence | questions prepared; awaiting owner | Research questions, source/coverage policy, uncertainty and useful output |
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

**Answer (2026-09-09):** “Integrated research → approved paper trading (Recommended)”

**Recorded decision:** PD-004. The first complete release must connect research to
human-approved paper trading. Useful intermediate analysis does not alone complete
that release. Detailed trading scenarios, evidence, and acceptance thresholds remain
for later rounds; this answer does not select the daily workload or research breadth.

**Subsequent clarification:** PD-007 retains the integrated scope and supersedes
the individual-paper-approval assumption. Read the current product target in PRODUCT.md.

### Q2. What routine workload should remain for Lucas?

Original recommendation (not selected): the cockpit researches within agreed bounds and prepares a concise
daily decision session, with roughly 10–20 minutes of routine review as a proposed
design target plus separate explicit trade approvals and exceptional interventions.
Alternatives are alerts with minimal routine review, or an interactive research
workbench for longer sessions. The time range is a preference proposal, not a measured
claim or a restriction on careful approval.

**Answer (2026-09-09):** “In terms of daily involvement on the initial MVP for paper
trade testing, ideally it would be autonomous. I would just look at Discord to see
how the bot is performing, what news it has for me, and see if there are any tweaks
that need to be made to the engine.”

**Recorded decision:** PD-005. Unattended routine operation and discretionary Discord
inspection replace the proposed mandatory daily review target. Discord must make
performance, news, and possible improvement needs inspectable.

**Implications to specify in later rounds:** meaningful reporting without a required
daily operator session; bounded unattended work; visible exceptional states; a
defined process for proposing and applying engine changes. The answer does not itself
authorize the engine to rewrite code or change its own policy.

### Q2a. Does unattended paper testing include automatic paper-order execution?

PD-004 originally selected human-approved paper trading; Q2 expressed a desire for
autonomous paper testing. The follow-up below resolves the intended product direction.

Recommendation for the stated unattended goal: evaluate the latter as the proposed
product direction. It would require an explicit, separately reviewed paper-autonomy
design and reconciliation with the existing mode/model boundaries and gates. This
planning choice would not grant current runtime permission or live-capital authority.

**Owner clarification (2026-09-09):** “The whole point of the system is that it's
set up so I can run it autonomously, but also so that I can interact with it through
Discord or through the supervisor/box that it is setup on (such as GrokBot or a
Hetzner VPS)”

**Recorded decision:** PD-007. In the ongoing initial paper-testing context, autonomy
is the default operation of the complete workflow; operator interaction is optional
and available through Discord or the supervisor/host. The earlier individual-paper-
approval assumption is superseded as a product target. Do not ask Lucas to reconfirm
the general autonomy objective; later rounds must specify the actual policy, boundaries,
interventions, and recovery semantics.

**Implementation dependency:** the repository's current paper approval and Codex/mode
boundaries are unchanged. Implementing the new target requires a separately scoped,
adversarially reviewed paper-autonomy design and explicit reconciliation with the
existing invariants and evidence gates. This decision does not authorize live capital,
an immediate runtime change, autonomous policy rewriting, or a specific host deployment.

### Q3. How broad should initial research coverage be?

Recommendation: a focused operator-selected coverage list plus a separate bounded
candidate-discovery queue. Alternatives are research confined to the existing monitored
companies or broad market discovery from the outset. Broader coverage requires explicit
source, cost, relevance, and evaluation bounds. Research inclusion never grants execution
eligibility, and this question does not authorize changing the current watchlist setting.

**Answer (2026-09-09):** “Core coverage plug bounded candidate discovery and human ingestion”

**Recorded decision:** PD-006. Core coverage and bounded discovery are selected,
with human ingestion added by Lucas.

**Working interpretation, to refine in Round 2:** Lucas can submit ideas or source
material for investigation. Do not infer a particular upload format, connector,
priority override, or externally fetched source permission from this answer.
Define intake, provenance, validation and deduplication in the research contract.
Human submission does not establish factual validity or execution eligibility.

## Round 2 — research experience and inputs

### Q4. What should a useful research result look like?

Recommendation: layered output, with a concise Discord update linked to an inspectable
case file. A substantive case should distinguish sourced facts from hypotheses, explain
significance, include counterevidence and missing information, and state what observation
would change the assessment. Alternatives are brief updates only or a full report for
every investigated item. Layering offers detail on demand while keeping routine monitoring
manageable; exact storage and interaction surfaces remain design work.

**Answer:** pending.

### Q5. What should human ingestion accept in the first release?

Recommendation: tickers, links and free-text ideas/questions first. Alternatives add
documents such as PDFs, or include screenshots/images as well from the first release.
Richer inputs improve flexibility but introduce extraction, provenance, attachment,
privacy and validation obligations. A submitted link is an input to the research policy;
it does not authorize arbitrary authenticated browsing or unsupported connectors.

**Answer:** pending.

### Q6. What should cause a research case to be revisited?

Recommendation: relevant new evidence, user requests, and a bounded scheduled backstop
for freshness and coverage. Alternatives prioritize comprehensive scheduled reviews
or research initiated only by explicit user requests. Changed-evidence revisits can
reduce repetitive reports; the backstop must expose gaps rather than treat silence
as proof nothing changed. This is desired product behavior, not authorization to
implement RW-13 or bypass its current dependencies.

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

Round 1 is complete through PD-007. Round 2 Q4/Q5/Q6 are prepared above: the content
of a useful research result, human-ingestion inputs, and when research should revisit a case.
Carry the autonomous paper-MVP target forward; resolve detailed authority in Round 4.
Record each incoming answer, update PRODUCT.md, validate the diff and commit the
checkpoint. Do not interpret silence or recommendation selection defaults as answers.

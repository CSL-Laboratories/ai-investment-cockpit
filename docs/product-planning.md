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
| PD-006 | decided direction — Round 1 Q3; input categories refined by PD-009 | Initial research combines core coverage, bounded candidate discovery, and human ingestion. Exact formats and handling remain to be specified. |
| PD-007 | decided — Round 1 autonomy clarification | The system runs autonomously while remaining interactive through Discord or its supervisor/host. In the initial paper-MVP context, the product target includes routine autonomous paper execution rather than individual paper-trade approval. GrokBot and Hetzner are examples, not selected integrations or deployment authorization. |
| PD-008 | decided — Round 2 Q4 | Short updates plus detailed research cases, including citations that let Lucas inspect the sources and data-ingestion methods used. Exact provenance fields and presentation remain to be specified. |
| PD-009 | decided — Round 2 Q5 | First-release human inputs include tickers, links, ideas, questions, documents, screenshots, and images. Document and visual inputs are MVP scope; exact formats, limits, and handling remain to be specified. |
| PD-010 | owner-proposed integration candidate — Round 2 Q5 | The cockpit works with other agents; Project Recursion may be a suitable harness if its architecture supports configured CLIs and credentials. Evaluate that possibility; no host selection, verified capability claim, or hard dependency is established. |
| PD-011 | decided — Round 2 Q6 | Relevant new evidence, Lucas's requests, and bounded scheduled checks trigger investigation or case revisits. Exact schedules, relevance rules, and budgets remain to be specified. |
| PD-012 | decided direction — Round 3 Q7 | The initial product should offer a suite of strategies that model reasoning can evaluate and employ through escalation. Supersedes the assistant's single-strategy recommendation; roster, activation, allocation and validation contracts remain open. |
| PD-013 | decided architecture direction; example mapping — Round 3 Q7 | Use cost-conscious high-volume model processing with escalation to more capable reasoning as needed. Owner example: GPT 5.6 Luna → Muse 1.3 Spark Contributor → GPT 5.6 Sol → GPT 6 Astra. Exact routes, role contracts, measured cost/quota savings, and authority remain to be specified. |
| PD-014 | decided — Round 3 Q8 | Investigate uncertain candidates within bounds, then monitor for specified evidence. Lucas reinforces early consideration of cross-model architecture and the Recursion harness under development. |
| PD-015 | decided — Round 3 Q9 | Manage the complete paper-position lifecycle, including entry, monitoring, policy-permitted adjustment/cancellation, exit, reconciliation and recorded outcomes. |
| PD-016 | decided — Round 4a Q10 | Agents develop and test candidate strategies; Lucas approves activation of a new strategy or material revision. Routine operation of activated strategies remains autonomous under standing policy. |
| PD-017 | decided requirement — Round 4a Q10 | Identify users and decision-makers across Discord and other surfaces; support an owner plus explicitly authorized users and credential setup. Actual credentials and deployment-specific identity/access configuration stay out of tracked GitHub content. Roles and exact identity mechanics remain open. |
| PD-018 | decided — Round 4a Q11 | Dynamic task/model escalation informed by subscription quota, model diversity, and capabilities/intelligence. Lucas plans ChatGPT Pro + OpenCode Go next month; the earlier tier chain is illustrative, not fixed. Exact access, quotas, adapters and ranking policy remain to be verified/specified. |
| PD-019 | architecture disposition deferred — Round 4a Q12 | Lucas wants the cockpit's complete architecture to determine standalone/plugin/MCP/Recursion deployment. He considers plugin/MCP integration plausible and notes Discord/lifecycle needs. Recursion-primary is not selected; compare the arrangements before final ratification. |

Decided directions include the integrated autonomous paper MVP, optional operator
interaction, research breadth and cadence, layered cited output, full human-input
categories, a strategy suite, tiered model reasoning, monitored unresolved cases and
complete paper-position management, owner-approved strategy activation, identifiable
authorized users, and dynamic subscription/capability/diversity-aware routing. Recursion
remains a candidate integration; service/plugin/host ownership is explicitly unresolved.
Detailed strategy, autonomy,
identity/permission, intake, escalation, interaction and evaluation
contracts remain open. Earlier assistant recommendations are not owner decisions
unless explicitly accepted.

## Working recommendations — not ratified

- Connect awareness, strategy research, and controlled execution through explicit
  input/output and refusal contracts, with outcome feedback into evaluation.
- Evaluate complete production workflows and useful output, alongside deterministic
  safety checks and the existing platform/evidence gates.
- Inspect actual delivered analysis before prescribing a prompt, schema, or feed fix.
- Investigate the candidate-selection population and rejected sources before assuming
  repeated empty screens will eventually unlock the existing strategy contract.
- Treat development autonomy and investment autonomy as separately specified authority.
- Define a harness adapter around bounded jobs while keeping the cockpit's evidence,
  policy, and deterministic execution boundaries explicit. Evaluate Recursion against
  that contract rather than assuming its current capabilities or making it a dependency.

## Grill rounds

| Round | Theme | State | Expected output |
|---|---|---|---|
| 1 | Purpose and first usable daily experience | complete — PD-004 through PD-007 | Integrated autonomous paper MVP, optional Discord/supervisor interaction, core/discovery/human inputs |
| 2 | Research behavior and evidence | complete — PD-008 through PD-011 | Layered cited research, full human-input categories, new-evidence/user/scheduled cadence, candidate Recursion harness |
| 3 | Strategy and candidate lifecycle | complete — PD-012 through PD-015 | Strategy suite, model escalation, monitored unresolved cases, complete paper-position lifecycle |
| 4a | Autonomy ownership and architecture | answers recorded through PD-019; deployment decision deferred | Owner-approved strategy activation, identity/access, dynamic routing; architecture comparison required |
| 4b | Autonomy limits and intervention | questions prepared; awaiting owner | Authorized-user roles, budget/risk envelopes, provider/fault behavior; later detailed revocation/recovery contracts |
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

**Answer (2026-09-09):** “I'd love short updates plus detailed research cases. I'd
also love for it to include citations so I could look into the sources and data
ingestion methods it's using as well.”

**Recorded decision:** PD-008. Layered output is selected, with explicit source
citations and inspectable ingestion methods. The investigation should be understandable
at a glance and its supporting material available for closer examination.

**Recommended specification detail, not separately ratified:** associate important
claims with their supporting evidence; identify source/acquisition route, publication
and retrieval times, retained input references, relevant parsing/transformation steps,
and coverage limitations. Distinguish source assertions from the cockpit's conclusions.
Use existing provenance and raw-storage contracts where possible. Neither a new UI nor
a particular citation schema is selected by this answer.

### Q5. What should human ingestion accept in the first release?

Original recommendation (narrower than the selected scope): tickers, links and free-text ideas/questions first. Alternatives add
documents such as PDFs, or include screenshots/images as well from the first release.
Richer inputs improve flexibility but introduce extraction, provenance, attachment,
privacy and validation obligations. A submitted link is an input to the research policy;
it does not authorize arbitrary authenticated browsing or unsupported connectors.

**Answer (2026-09-09):** “It should be tickers, links, ideas, and questions in addition
to documents, screenshots, and images. It's going to take a lot of work but the point
is that the engine is being run with other agents. And, if we establish a good
architecture for Project Recursion, it's possible that the best harness to run the
cockpit on would be something like Recursion (because of it's ability to autonomously
integrate configured CLIs and Keys)”

**Recorded decisions:** PD-009 selects the complete list of human-input categories
for the initial release. PD-010 records the agent-supported operating context and
conditional Recursion integration candidate. Do not demote document/visual inputs to
a later release merely because they require additional implementation work.

**Recommended specification detail, not separately ratified:** preserve originals,
bind extracted observations to the input and extraction method, and expose uncertainty
or unsupported content. Size/type limits, attachment handling, image understanding,
source verification, and model/tool selection need explicit contracts. Additional
agents do not themselves establish extraction accuracy or provenance.

**Harness follow-up:** in Round 4, resolve which responsibilities belong to the cockpit
and which may be supplied by Recursion or another harness. Inspect actual capabilities
before claiming compatibility. Keep job execution and credential access distinct from
deterministic risk/order authority. No cross-repository implementation, key provisioning,
or new runtime capability is authorized by this planning answer.

### Q6. What should cause a research case to be revisited?

Recommendation: relevant new evidence, user requests, and a bounded scheduled backstop
for freshness and coverage. Alternatives prioritize comprehensive scheduled reviews
or research initiated only by explicit user requests. Changed-evidence revisits can
reduce repetitive reports; the backstop must expose gaps rather than treat silence
as proof nothing changed. This is desired product behavior, not authorization to
implement RW-13 or bypass its current dependencies.

**Answer (2026-09-09):** “New evidence + my requests + scheduled checks (Recommended)”

**Recorded decision:** PD-011. Adopt the three trigger classes and bounded scheduled
backstop. Actual intervals, priority, material-change criteria, work budgets, and
deduplication semantics remain implementation/specification questions.

## Round 3 — strategy and candidate lifecycle

### Q7. How should the first paper strategy be selected for this product target?

The current DEC-03 selects constrained cash-only, long-target merger arbitrage.
Its evidence machinery exists, but feasibility of the qualifying population remains
unproven under the current source/selection rules. The product grill has not changed
that selection or its execution gates.

Original recommendation (single-strategy target not selected): conduct a bounded feasibility comparison that includes the existing
strategy, then explicitly choose one initial paper strategy using predeclared data,
testability, evidence, operating-cost and risk criteria. This recommends a selection
method, not a particular investment or a profitability claim. Alternatives are retaining
the existing merger strategy as the first release's fixed target, or requiring multiple
independently specified and validated paper strategies in the first release.

A changed strategy requires an explicit amendment to DEC-03 and affected contracts.
No strategy becomes executable from winning a comparison; it still needs its applicable
validation and activation evidence. Any research comparison or redesign is a later
scoped package, not automatically performed during this documentation grill.

**Answer (2026-09-09):** “I think there should be a suite of strategies that the model
can employ. It's going to be the engine that follows escalation architecture to make
decisions regardless. For example, for all the data and normalization we are going
to be using that can work with high-volumes of data for a cheap price (like gpt 5.6
luna), and then decisions are marked and flagged for more capable models to come and
make decisions. For example, it would go GPT 5.6 Luna -> Muse 1.3 Spark Contributor ->
GPT 5.6 Sol -> GPT 6 Astra if needed. This saves us the most amount of money and
subscription quota as possible, while genuinely having a stable model architecture
and reasoning for the future.”

**Recorded decisions:** PD-012 selects a strategy suite for the product. PD-013 selects
tiered model reasoning and preserves the owner's example escalation chain. Cost/quota
efficiency and stability are design objectives; no benchmark establishes an optimal
route or measured savings here. Do not silently reduce the suite to a single-strategy
release or turn the example model order into a mandatory all-tier pipeline.

**Recommended specification detail, not separately ratified:** define per-strategy
evidence/action/evaluation contracts and portfolio-wide checks. Separate mechanical
normalization from semantic interpretation. Define routing around task roles, explicit
escalation triggers, source-preserving handoffs, budgets and stop conditions. Evaluate
false negatives among cases that a cheap tier does not escalate; higher-tier review
alone cannot detect everything filtered out before it. Exact strategy/model allocation,
activation permissions, provider fallbacks and capital budgets belong in Round 4 and
the evaluation contract.

**Reconciliation dependency:** DEC-03 remains the current implementation baseline;
RW-25 must explicitly reconcile its single-strategy scope with PD-012. Existing model
and order boundaries remain effective until separately scoped reviewed changes land.
This answer does not give a model broker tools or authorize self-modifying risk policy.

### Q8. What should happen to a potentially interesting case with insufficient evidence?

Recommendation: conduct a bounded investigation, then retain the case in a visible
watching/awaiting-evidence state with the missing fact and a specific revisit condition.
Hard disqualifications remain distinct from missing information. Alternatives are
closing unresolved cases until Lucas resubmits them, or asking Lucas to adjudicate
each unresolved case. The recommended behavior supports unattended operation and
avoids an endless research loop; retention and revisit budgets remain to be defined.

**Answer (2026-09-09):** “Absolutely, investigate, then monitor for specified evidence.
Again, I think the cross-model and model architecture that we are going to use for
the engine is genuinely valuable. And, since Recursion is the harness that we are
working on developing, it's worth thinking about in advance.”

**Recorded decision:** PD-014. Bounded investigation followed by monitoring for explicit
evidence is selected. Model escalation and Recursion compatibility must be addressed
early in architecture discovery; do not postpone them as incidental integration work.
The answer does not establish Recursion's implemented capabilities or require an
immediate cross-repository change.

### Q9. How much of a paper position should the first release manage autonomously?

Recommendation: the complete policy-defined lifecycle, including entry, monitoring,
permitted adjustment/cancellation, exit and reconciliation, with outcomes recorded
for later evaluation. Alternatives are autonomous entry with Lucas managing exits,
or research/simulation followed by manually managed broker-paper positions. Complete
lifecycle scope best matches PD-007 but requires explicit policies for each action,
partial fills, stale evidence, interruptions and operator interventions. This does
not authorize improvised trading rules, live capital, or model access to the broker.

**Answer (2026-09-09):** “Manage the complete paper-position lifecycle (Recommended)”

**Recorded decision:** PD-015. The first release must cover the full position lifecycle
under its explicit paper policy, rather than leaving routine exits or reconciliation
to Lucas. Failure, interruption and operator-intervention contracts remain design work.

## Round 4a — autonomy ownership and architecture

Round 4 is split into two small groups because the accepted strategy suite, model
escalation and harness direction introduce consequential ownership decisions. First
resolve the following architecture choices; then address budgets, faults and intervention
in 4b. This is a planning subdivision, not an additional implementation package.

### Q10. Who may activate a new strategy or a material strategy revision?

Recommendation: agents research, implement and test candidate strategies in an isolated
evaluation environment, then Lucas approves activation or a material change to the active
suite. Once active, the cockpit selects and runs strategies within their standing policy.
This separates unattended operation from changes to what the engine is permitted to do.
Alternatives are automatic paper-only activation after predeclared independent validation,
or a fixed MVP suite with expansion performed outside the runtime. Exact activation
evidence, review independence and the meaning of a material change remain to be defined.

**Answer (2026-09-09):** “Agents develop/test; I approve activation. Make sure though,
since this is both being run to be controlled to discord servers and otherwise, that
there is a way to identify who is using the program and making decisions. There should
be ways to setup authorized credentials (owner + authorized users). Obviously don't
publish it in the GitHub, but use it otherwise.”

**Recorded decisions:** PD-016 selects owner approval for new-strategy activation and
material revisions, while retaining autonomous routine operation. PD-017 adds identifiable
owner/authorized-user access across control surfaces and excludes actual credential and
deployment identity/access data from tracked repository content. Supporting configuration
schemas, setup code and placeholder examples remain legitimate repository material.

**Recommended specification detail, not separately ratified:** a stable internal user
identity with explicit external identity bindings and action-scoped permissions. Preserve
both requester and executing agent/service in audit provenance. For autonomous work,
record the standing authorization instead of impersonating a human. Define owner setup,
user enrolment, role grants, revocation and surface/server scoping. Preserve OS-vault
secret storage and avoid shared credentials that erase individual attribution.

**Open follow-up:** Round 4b must define which actions authorized users may take.
Their presence does not delegate strategy activation or owner administration automatically.
Multi-server wording does not by itself select multi-tenancy or a public account system.

### Q11. Should the model route follow a fixed ladder or adapt to the task?

Recommendation: configurable routing by task, with the owner's tier order as a starting
profile and permission to skip unnecessary tiers under a tested policy. Escalation may
depend on ambiguity, conflicting sources, validation failures or consequence; self-reported
confidence alone is insufficient. Alternatives are a strict Luna → Spark → Sol → Astra
sequence for escalation, or mandatory senior review of every paper decision. All options
retain bounded cost/quota use, provenance and deterministic execution controls; those
limits and exact role mappings remain for specification and measurement.

**Answer (2026-09-09):** “Since next month I'm going to be driving ChatGPT Pro +
Opencode Go subscription we should configure the task and model escalation to be
dynamic depending on subscription quota, model diversity, and model capabilities/intelligence.”

**Recorded decision:** PD-018. Dynamic routing uses subscription quota, model diversity
and capability/intelligence as selection factors. The intended next-month subscription
mix is ChatGPT Pro plus OpenCode Go. The earlier Luna → Spark → Sol → Astra sequence
remains an example profile; it is not the mandatory routing order.

**Recommended specification detail, not separately ratified:** qualify routes by task
modality, tool/permission scope and demonstrated quality, then optimize among qualifying
routes using current quota/cost/latency and review-diversity needs. Distinguish observed,
estimated and unavailable quota signals; retain their source and freshness. Record route
choice, escalation reason and actual use. Provider diversity does not by itself prove
independent error detection. Unknown/exhausted quota must not silently weaken validation
or move work to a route with unapproved spending or authority.

**Evidence dependency:** verify the actual models, supported CLI/API access, quota
signals and plan limits for the selected subscriptions when specifying the adapters.
This answer does not establish that either plan includes any particular named model,
grant new credentials or authorize subscription purchases/API spending.

### Q12. What relationship should the MVP have with Recursion?

Original recommendation (not selected; architecture comparison requested): target Recursion as the primary production harness while retaining a
minimal local/test runner for the same cockpit contracts. Keep evidence and strategy/risk
semantics in the cockpit; avoid duplicating a general supervisor inside it. Alternatives
are requiring Recursion for all operation, or shipping a standalone cockpit first with
Recursion integration later. The recommended direction requires verifying Recursion's
actual interfaces early and testing the composed Recursion/cockpit flow; a local fake
adapter cannot certify the real integration. Recursion may therefore become a release
dependency, whose readiness must be tracked explicitly.

**Answer (2026-09-09):** “I think it really depends on the architecture of the cockpit
as a whole. Since it's basically a standalone program that \"harnesses\" the capabilities
of your existing subscriptions to try and make profitable financial decisions, it feels
more like a plugin/mcp design than a harness. However, the other aspects such as the
Discord communication makes it increasingly dependent on being its standalone program.
It's a difficult decision that is worth ironing out”

**Recorded disposition:** PD-019. Recursion-primary is not selected. Compare deployment
and integration arrangements based on the full architecture and operating needs, then
return the concrete recommendation for owner disposition before final ratification.
Other permission and budget questions can proceed independently.

**Architecture distinction:** packaging as a plugin, exposing an MCP/API surface,
running a persistent service, and delegating model work are different choices. A
persistent application can have both Discord and MCP access. Discord does not alone
determine where its state, scheduler or model-job lifecycle must live.

**Comparison to prepare within product architecture planning:**

| Arrangement | Benefit to investigate | Obligation/tradeoff to resolve |
|---|---|---|
| Standalone cockpit and runner | Independent operation and direct ownership of lifecycle | Potential duplication of subscription routing/supervision being built in Recursion |
| Host-loaded plugin | Reuse host execution, identity and configured routes where compatible | Host coupling; durability, scheduling and recovery if the host stops or changes |
| Persistent cockpit service with Discord and plugin/MCP adapters | One investing core, multiple interaction surfaces, optional external job execution | Explicit service/adapter lifecycle, authentication and integration acceptance |

**Working recommendation, not ratified:** investigate the service-plus-adapters
arrangement first. Keep investing state/evidence and deterministic controls under one
domain owner; define job delegation independently. A thin integration should reuse the
same application actions and authorization rather than duplicating workflows. Tools
exposed to agents must remain within existing authority; plugin/MCP packaging grants
no direct model access to a broker or permission to change risk policy.

## Round 4b — permissions, limits and failure behavior

### Q13. How should permissions for additional authorized users be assigned?

Recommendation: owner-defined roles with explicit per-action permissions, applied
consistently across Discord and other surfaces. This allows a viewer, research contributor
or delegated operator without making all users administrators. Alternatives are limiting
every additional user to viewing/research, or granting all authorized users full control.
PD-016's owner strategy-activation gate remains until an explicit delegation decision.
Role names, revocation timing, session handling and exact command permissions need a
later concrete matrix; private user bindings remain outside tracked repository content.

**Answer:** pending.

### Q14. How should the engine use resources and paper capital autonomously?

Recommendation: the owner sets hard outer limits for inference spending/subscription
use and paper risk/capital; agents allocate work and choose among active strategies
within those limits. Increasing a hard limit requires owner approval. Alternatives are
fixed per-model/per-strategy budgets, or human approval for each resource grant. Numeric
ceilings, accounting periods, reservations and the distinction between available and
unknown quota remain specification work. This asks who controls the envelope, not
for real-money trading parameters or permission to spend now.

**Answer:** pending.

### Q15. What should happen when a required feed/model fails or quota runs out?

Recommendation: use only already-approved qualifying fallback routes within budget;
if required evidence or capability is still unavailable, suspend affected unsafe actions,
continue safe monitoring, and report the reason. Existing positions follow an explicit
failure policy rather than invented exits. Alternatives are halting the entire service
on every dependency failure, or requiring operator intervention before any fallback.
Fallback must preserve capability/authority requirements. Recovery of the affected
actions, durable safety holds and automatic-versus-human resume require a specific
contract compatible with the current fail-closed boundary.

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
- Recursion's current CLI/credential integrations, job lifecycle, and compatibility
  with the cockpit's hardened agent boundary have not been established in this round.
  Treat them as a future interface/suitability investigation, not a completed dependency.
- Identity baseline inspected for Q10: `app/config/settings.py` limits the configured
  operator list to one entry; `app/orchestrator/auth.py::ApiKeyAuth` validates one
  shared keyring-backed API credential and does not return an individual user principal.
  Supporting multiple authorized humans requires an explicit identity/permission design,
  not a claim that the existing key already provides user attribution. No actual keys,
  user IDs, account bindings or live access configuration were inspected or printed.
- Model-route evidence checked 2026-09-09: official OpenAI pages describe
  [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna) for
  cost-sensitive high-volume work,
  [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol) as a
  flagship for complex professional work, and
  [GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra) as its
  most capable model. These support the broad role hypothesis, not this application's
  routing quality or the account's subscription/CLI entitlement. Muse 1.3 Spark
  Contributor's integration and the complete route's cost/quota behavior remain
  unverified. API list prices do not establish subscription quota savings.
- PD-018's planned ChatGPT Pro + OpenCode Go subscription mix is owner-stated intent.
  Account-specific entitlement and quota telemetry have not been inspected; published
  API model descriptions cannot certify availability through a subscription or CLI.
- PD-019 leaves the deployment architecture unresolved. Prepare the lifecycle/state/
  permission/interface comparison above and evaluate actual Recursion compatibility;
  do not treat an unselected primary-harness proposal as an implementation prerequisite.

## Resume here

Rounds 1–3 are complete through PD-015. Round 4a answers are recorded through PD-019;
the hosting/plugin architecture is explicitly deferred for comparison and later owner
disposition. Present Round 4b Q13/Q14/Q15 on authorized users, limits and failures.
Carry the autonomous paper MVP, strategy suite, dynamic routing, full input scope,
owner strategy activation and complete position lifecycle forward. Do not assume
Recursion-primary or a plugin-only architecture has been selected.
Record each incoming answer, update PRODUCT.md, validate the diff and commit the
checkpoint. Do not interpret silence or recommendation selection defaults as answers.

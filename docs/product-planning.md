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

This is the durable record of the completed grill. Lucas adopted [PRODUCT.md](../PRODUCT.md)
and its operating, tax, acceptance and delivery contracts under PD-048 on 2026-09-09.
PRODUCT owns product scope/acceptance and PLAN delivery order. Earlier question sections
preserve their then-current recommendations and follow-ups; they are history, not a
second current contract. Use **Resume here** for delivery status. Required implementation,
runtime review and empirical qualification remain separate from ratification.

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
| PD-019 | deferred in Round 4a Q12; resolved by PD-028/PD-029 | Lucas asked for the complete architecture to determine standalone/plugin/MCP/Recursion deployment. Round 6b selects persistent cockpit ownership and a standalone MVP, with Recursion integrated when qualified. |
| PD-020 | decided — Round 4b Q13 | The owner defines roles with explicit per-action permissions, enforced consistently across control surfaces. Strategy activation remains owner-reserved unless explicitly delegated. Exact roles, action grants and revocation mechanics remain open. |
| PD-021 | decided — Round 4b Q14 | Owner-approved hard caps with autonomous engine allocation inside them; increases require owner approval. Applies to inference/subscription resources and paper capital/risk; numeric values and accounting remain open. |
| PD-022 | initial tax consideration — Round 4b Q14; expanded by PD-042 | Consider trading taxes and other restrictions within the broader limits/allocation design. PD-042 subsequently requires full investment-tax calculations in the MVP; jurisdiction/account coverage and rules remain to be specified. |
| PD-023 | decided — Round 4b Q15; refined by PD-031/PD-032 | Approved qualifying fallback routes within budget, then suspension of affected unsafe actions with safe monitoring and reporting. PD-031 defines recovery and PD-032 defines existing-position failure behavior; detailed contracts remain open. |
| PD-024 | decided — Round 5 Q16 | First-release success is a working, useful and reliable integrated paper product plus credible evaluation with honest results. Positive profitability is not a completion requirement; existing evidence/activation gates remain. |
| PD-025 | decided target — Round 5 Q17 | Aim for fully automated research-quality and model-routing evaluation. Replaces the assistant's recommended routine human sample review; owner strategy-activation approval remains separate. The automated evaluator and reference evidence need validation; existing independent-evidence requirements require explicit reconciliation, not silent waiver. |
| PD-026 | decided — Round 6a Q18 | The MVP strategy suite trades cash-funded, long-only US stocks and ETFs on paper. Broader research remains possible; shorting, margin borrowing, options and other asset classes are outside initial execution scope. Strategy roster, specific instrument eligibility and cash/settlement contracts remain open. |
| PD-027 | decided — Round 6a Q19 | Support Windows and Linux in the first release, emphasizing Lucas's Linux box as the primary deployment target. Supersedes the Linux-first/Windows-development-only recommendation; no specific VPS/provider or Recursion dependency is selected. |
| PD-028 | decided — Round 6b Q20 | Persistent cockpit service plus integrations. Cockpit owns research, paper positions, schedules and controls; Discord and host/plugin/MCP integrations use the same application services. Recursion may execute bounded model jobs without owning the investing application. Resolves PD-019's ownership question. |
| PD-029 | decided — Round 6b Q21 | Deliver a working standalone MVP; integrate Recursion when real conformance is demonstrated. Design the shared execution contract now. Recursion readiness is not a first-release gate, and standalone operation must provide the selected product capabilities. |
| PD-030 | decided — Round 7a Q22 | Agents investigate, implement and test isolated revisions; Lucas approves installing software releases. Routine operation stays on the approved version. Automatic maintenance deployment is not selected; strategy activation and hard-cap increases retain separate approval. |
| PD-031 | decided — Round 7a Q23 | Automatic recovery under an approved policy after health, freshness, resource-limit and paper-reconciliation checks pass. Explicit human halts and integrity/authority holds require authorized human release; unresolved conditions stay suspended. Detailed durable recovery and audit contracts remain to be specified. |
| PD-032 | decided — Round 7a Q24 | When required inputs are unavailable, block new or increased exposure for affected actions, monitor existing paper positions and allow only preapproved protective actions whose own requirements pass. Detailed handling of orders, partial fills, uncertain submissions and reconciliation remains to be specified. |
| PD-033 | decided — Round 7b Q25 | Launch with two or three distinct strategies, selected through bounded feasibility comparison including the existing cash-merger strategy and approved by Lucas. Each requires its own evidence/evaluation; parameter variants do not satisfy the count. |
| PD-034 | decided requirement — Round 7b Q25 | Expand the strategy suite after launch and retain explicit reminders/roadmap follow-up. Two or three is not a permanent ceiling. Reminder cadence and candidate-review mechanics remain open; additions still require evaluation and owner activation. |
| PD-035 | owner economic objective — Round 7b Q25 | Eventually achieve profitable growth exceeding expenses estimated at about $210/month. This is a target to evaluate, not a verified cost total, spending authorization, return promise or replacement of PD-024's MVP completion criterion. Capital baseline and expense components remain open. |
| PD-036 | decided — Round 7b Q26 | The engine decides holding horizons within approved strategy evidence, data/execution capabilities and policy. No universal days-to-months restriction is selected; exact supported horizons follow feasibility and strategy contracts. |
| PD-037 | owner-stated expense scope — Round 7c Q27 | Approximately $210/month covers ChatGPT Pro plus OpenCode Go subscriptions. This is the owner's expense assumption, not independently verified pricing or an all-inclusive engine-cost estimate. |
| PD-038 | owner funding/capital context; provisional paper assumption — Round 7c Q27 | Lucas receives $500/month of student funding for the coming year, wants to contribute a percentage of monthly income, and is likely to transfer a minimum of $2,000. Use $2,000 provisionally for paper evaluation; exact deposit and contribution rate remain unset. No live allocation/transfer is authorized and the entire funding stream is not assumed investable. |
| PD-039 | decided — Round 8a Q28 | At least 20 market days of unattended paper observation on the primary Linux deployment, plus required workflow coverage and applicable strategy-evidence gates. Longer lifecycles or incomplete evidence may extend it; the minimum is not proof of profitability. |
| PD-040 | decided — Round 8a Q29 | Monthly expansion summaries plus notifications when candidates are ready for owner activation review. Include progress, missing evidence and next steps. No daily review obligation, automatic activation or budget increase follows from the cadence. |
| PD-041 | decided — Round 8b Q30 | Newly researched stocks/ETFs enter paper-execution eligibility automatically within owner-approved rules after required evidence, instrument, strategy and risk checks. Submission/research alone grants no eligibility. Requires a scoped reviewed replacement of the current static allowlist, preserving the single order path. |
| PD-042 | decided — Round 8b Q31 | Full investment-tax calculations are MVP scope, replacing the recommendation to defer the tax engine. Jurisdiction, account type, tax-year coverage, complete inputs and verified rules need specification; a full personal-return preparation/filing service is not selected. |
| PD-043 | decided — Round 8c Q32 | Tax support must be configurable and cover taxable brokerage and retirement accounts. Lucas identifies Fremont, California, United States as his residence; other taxpayer facts are not inferred. |
| PD-044 | decided — Round 8c Q33 | Validated US federal and California calculations at launch, with an extensible jurisdiction interface for additional verified rule sets. Unsupported jurisdictions remain explicit. |
| PD-045 | decided — Round 8c Q34 | Traditional/Roth IRAs plus 401(k), 403(b) and governmental 457(b) accounts at launch, with account-specific rules. SEP/SIMPLE and inherited cases were not selected for launch coverage. Tax-calculation capability is separate from broker account-trading support. |
| PD-046 | decided — Round 9 Q35 | Missing personal tax context permits continued paper operation using an explicitly configured scenario, with missing personal inputs visible. Actions requiring an unverified tax restriction remain blocked. Full tax-engine release coverage is still required. |
| PD-047 | decided — Round 9 Q36 | Raw private account/tax documents stay local by default; external models receive only minimum approved, redacted facts. The owner can authorize broader access for a particular document or provider. Public research uses normal approved routes. |
| PD-048 | ratified — final review Q37, 2026-09-09 | “Adopt contract; finish docs branch (Recommended)”. Lucas adopts consolidated PRODUCT and its operating, tax, acceptance and delivery contracts, including technical defaults and explicit scope/gate dispositions. Product rounds are complete; finish publication/merge under PD-003. This does not qualify software/strategies/tax rules or alter runtime authority. |

Decided directions include the integrated autonomous paper MVP, optional operator
interaction, research breadth and cadence, layered cited output, full human-input
categories, a strategy suite, tiered model reasoning, monitored unresolved cases and
complete paper-position management, owner-approved strategy activation, identifiable
authorized users, dynamic subscription/capability/diversity-aware routing, owner-approved
hard limits, approved fallback with safe suspension, honest product/evaluation success,
and a fully automated quality-evaluation target. Initial paper execution covers
cash-funded, long-only US stocks and ETFs. A persistent cockpit service owns the
investing application; integrations share its services. The MVP works standalone,
with Recursion integration added when qualified rather than required for first release.
Both Windows and Linux are supported at first release, with Lucas's Linux box primary
for deployment. Agents develop and test isolated software revisions; Lucas approves
releases. Checked automatic recovery handles transient dependency failures under policy,
while human halts and integrity/authority holds require authorized human release.
During failures, monitor positions and permit only qualifying preapproved protective
actions while affected new/increased exposure is blocked.
Launch with two or three strategies, keep future expansion and reminders explicit,
and let the engine select supported holding horizons. Evaluate progress toward
profitable growth beyond the owner's approximate $210/month expense benchmark.
PD-048 adopts the detailed operating, tax, acceptance and delivery contracts. Specific
strategy qualification, owner deployment configuration and reviewed runtime amendments
remain required implementation work. Historical recommendations below retain their
then-current status; the adopted contracts define the present requirements.

## Early working recommendations — historical

This section records the initial recommendations. PD-048 subsequently adopts the
consolidated contracts; unresolved empirical questions still require evidence.

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
| 4a | Autonomy ownership and architecture | answers recorded through PD-019; architecture resolved in Round 6b | Owner-approved strategy activation, identity/access and dynamic routing; PD-028/PD-029 own architecture disposition |
| 4b | Autonomy limits and intervention | complete through PD-023; detailed contracts adopted under PD-048 | Owner-defined permissions, hard caps/autonomous allocation, tax consideration, approved fallback and safe suspension |
| 5 | Evaluation and feedback | complete through PD-025; evaluation contract adopted under PD-048 | Working product with credible honest results; fully automated quality-evaluation target |
| 6a | Initial trading scope and deployment | complete through PD-027 | Cash-funded long-only US stocks/ETFs; Windows and Linux support with Lucas's Linux box primary |
| 6b | Architecture and integration dependency | complete through PD-029 | Persistent cockpit plus integrations; standalone MVP, Recursion when qualified |
| 7a | Engine releases and failure recovery | complete through PD-032; detailed contracts adopted under PD-048 | Owner-approved software releases, checked automatic recovery, human-released holds and permitted protective actions |
| 7b | Initial strategy-suite contract | complete through PD-036 | Two or three launch strategies, expansion reminders, economic objective and engine-selected horizons |
| 7c | Economic evaluation baseline | answered through PD-038; contribution rate unset | Owner-stated subscription expense, provisional $2,000 paper balance and contribution context |
| 8a | Release evidence and expansion review | complete through PD-040 | At least 20 market days plus coverage; monthly expansion summaries and candidate-ready notifications |
| 8b | Final scope boundaries | complete through PD-042 | Automatic eligibility under approved rules; full investment-tax calculations in MVP |
| 8c | Tax coverage | complete through PD-045 | US federal and California first; taxable brokerage, Traditional/Roth IRAs and common workplace plans; extensible verified rules |
| 9 | Tax context and private data | complete through PD-047 | Explicit paper scenarios with incomplete personal context; private documents local by default and minimal approved facts on external routes |
| Final | Contract reconciliation and final review | adopted — PD-048 | Product/operating/tax/acceptance/delivery contracts and explicit scope/gate reconciliation; finish authorized publication/merge |

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

**Subsequent resolution:** PD-028 selects persistent cockpit ownership with shared
integration surfaces; PD-029 selects a standalone MVP with Recursion added when
qualified. The comparison below preserves the options considered in Round 4a.

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

**Answer (2026-09-09):** “Owner-defined roles and per-action permissions (Recommended)”

**Recorded decision:** PD-020. Use owner-defined roles and action-scoped permissions
across interfaces. This does not grant any particular user a role or delegate strategy
activation now. The later permission matrix must identify who can view, submit research,
operate controls, change policy or administer access, including revocation behavior.

### Q14. How should the engine use resources and paper capital autonomously?

Recommendation: the owner sets hard outer limits for inference spending/subscription
use and paper risk/capital; agents allocate work and choose among active strategies
within those limits. Increasing a hard limit requires owner approval. Alternatives are
fixed per-model/per-strategy budgets, or human approval for each resource grant. Numeric
ceilings, accounting periods, reservations and the distinction between available and
unknown quota remain specification work. This asks who controls the envelope, not
for real-money trading parameters or permission to spend now.

**Answer (2026-09-09):** “That's smart, the hard caps and engine allocation could
then also fit in other restrictions (such as taxes from trading) and other things”

**Recorded decisions:** PD-021 accepts hard owner-approved caps with autonomous
allocation inside them. PD-022 records taxes and other restrictions as considerations
for the broader policy/evaluation architecture. Do not interpret this as approval of
a particular tax formula or commitment to a complete tax-compliance product.

**Recommended specification detail, not separately ratified:** enforce aggregate
caps and reservations across concurrent work; keep hard constraints distinct from
estimated costs. Define any tax-aware research assumptions using explicit jurisdiction,
account context, transaction history and verified rules with version dates. Missing
context is unknown, not zero cost. No actual tax advice or calculation is provided
by this planning record; scope and rules need later evidence and design.

### Q15. What should happen when a required feed/model fails or quota runs out?

Recommendation: use only already-approved qualifying fallback routes within budget;
if required evidence or capability is still unavailable, suspend affected unsafe actions,
continue safe monitoring, and report the reason. Existing positions follow an explicit
failure policy rather than invented exits. Alternatives are halting the entire service
on every dependency failure, or requiring operator intervention before any fallback.
Fallback must preserve capability/authority requirements. Recovery of the affected
actions, durable safety holds and automatic-versus-human resume require a specific
contract compatible with the current fail-closed boundary.

**Answer (2026-09-09):** “Approved fallback, then safe suspension and monitoring (Recommended)”

**Recorded decision:** PD-023. Use qualifying approved fallback routes within budget;
otherwise suspend affected unsafe actions, retain safe monitoring, and explain the
condition. Preserve capability/authority requirements and the existing fail-closed
boundary. Define existing-position behavior and automatic-versus-human recovery before
implementation; no automatic mode promotion follows from a provider returning.

## Round 5 — evaluation and acceptance

### Q16. What should count as success for the first complete paper release?

Recommendation: a demonstrably useful and reliable integrated workflow plus a credible
evaluation of strategy results, even if a financial advantage remains unproven. The
workflow must deliver cited research, handle the agreed input types, operate its approved
strategy suite, and manage paper positions through reconciliation and failures. Evaluate
strategy outcomes against predeclared baselines and costs, and report uncertainty and
unfavorable results honestly. This is not permission to waive existing financial-evidence
or activation gates. The alternative is requiring a predeclared positive cost-adjusted
paper-performance result against selected baselines before calling the first release
complete; that ties completion to empirical outcomes beyond implementing the workflow.

**Answer (2026-09-09):** “Working product + credible evaluation, with honest results (Recommended)”

**Recorded decision:** PD-024. The full integrated paper workflow and a credible,
honest evaluation establish product completion. Positive financial results are not
required for that declaration. Preserve agreed functionality and applicable evidence
and activation gates; do not interpret this as permission to call an incomplete
workflow finished or to describe unproven strategy advantage as established.

### Q17. How should research quality and routing decisions be evaluated?

Recommendation: automated scoring and replay against a maintained evidence set, combined
with independent review of a bounded sample and Lucas's feedback on usefulness. Include
accepted, rejected, low-tier-dismissed and escalated cases; retain failures and empty
sessions. Use source-grounded reference judgments, keep tuning and held-out examples
separate, and do not treat model agreement as ground truth. Alternatives are owner review
of every evaluation case, or a fully automated quality-evaluation target. The latter
cannot silently replace existing independent label/review requirements. Reviewer roles,
sampling cadence, thresholds, and measurable operator effort remain to be specified.

**Answer (2026-09-09):** “Aim for fully automated quality evaluation”

**Recorded decision:** PD-025. Target fully automated research-quality and routing
evaluation, replacing the recommended routine human sample review. Owner strategy
activation under PD-016 remains distinct from quality evaluation. Optional operator
feedback is useful but not a required daily task. Do not label the automation target
as an already validated evaluator or silently discard existing evidence requirements.

**Recommended specification detail, not separately ratified:** combine deterministic
checks, replays through the real workflow, source-grounded reference cases, and model
judges whose own errors are evaluated. Include early dismissals and rejections as well
as escalations; preserve failures, abstentions and empty runs. Separate tuning from
held-out evaluation. Report unverified results when the evidence is insufficient;
agreement among models alone cannot establish a passing result. Define reference-set
creation, independence, evaluator validation, thresholds and regression handling in
the concrete contract. Current human-source labelling requirements are a named
reconciliation item, not a newly imposed routine operator workload.

## Round 6a — initial trading scope and deployment

### Q18. Which instruments and position types should the initial strategy suite trade?

Recommendation: cash-funded, long-only US stocks and ETFs for the first paper release.
Multiple strategies can operate within that scope; research may cover broader events
and markets without making every instrument executable. Alternatives add shorting or
margin in US equities, or include options and other asset classes from the start.
Broader scope requires corresponding data, strategy evaluation, risk, broker and
position-lifecycle contracts. This is a proposed product boundary, not a strategy
selection or an investment recommendation. Current eligibility and risk rules remain
until an explicitly reviewed implementation changes them.

**Answer (2026-09-09):** “Long-only US stocks and ETFs (Recommended)”

**Recorded decision:** PD-026. Accept the recommended cash-funded, long-only US
stock/ETF scope for the paper MVP's strategy suite. This selects an instrument and
position boundary, not a single strategy. Broader research remains possible. Shorting,
margin borrowing, options and other asset classes are outside initial execution
scope. Specific instrument/ETF eligibility, trading horizons, cash/settlement handling
and portfolio exposure rules remain to be specified; no current allowlist, strategy
activation or broker capability changes merely by recording the product decision.

### Q19. Where should the first unattended pilot run?

Recommendation: one Linux VPS deployment as the initial unattended target, with the
Windows workstation retained for development and local operation. Alternatives are
an always-on Windows host first, or full unattended deployment support for both in
the first release. This choice determines packaging, credential provisioning,
supervision and recovery acceptance; it does not select Recursion or require separate
copies of investing state. The existing native Windows/WSL2 × Python 3.11/3.12 test
matrix remains required regardless of the pilot host. No hosting purchase, deployment,
credential change or service operation is authorized by answering this question.

**Answer (2026-09-09):** “I think we should support Windows and Linux for the first
release, but it should be emphasized that we are going to be using my linux box for
deployment”

**Recorded decision:** PD-027. Both Windows and Linux are first-release supported
operating platforms; Lucas's Linux box is the primary deployment target. Supersedes
the recommendation to prioritize Linux deployment while retaining Windows mainly
for development/local use. Do not infer a particular VPS/provider, simultaneous active
replicas, or a Recursion dependency. Both platforms need operational acceptance in
addition to the unchanged four-cell DEC-09 test matrix. Host configuration and Linux
distribution remain to be established before deployment implementation.

## Round 6b — architecture and integration dependency

The [architecture comparison](product-architecture-options.md) is the reviewable
proposal for PD-019's deferred decision. It distinguishes inspected source from
required integration evidence and preserves the selected platform scope.

### Q20. Which component should own the running investing application?

Recommendation: a persistent cockpit service owns research, strategies, paper state,
investing schedules and deterministic controls. Discord and host/plugin/MCP adapters
call the same application services; a bounded executor supplies model jobs. Recursion
can provide that execution when qualified. Alternatives are a host-owned cockpit
plugin, coupling investing availability/recovery to the host, or a fully self-contained
cockpit that also builds the general agent-platform capabilities. The recommendation
extends the existing composition and avoids duplicating Recursion's general platform
work. Process packaging and adapter protocols remain implementation design choices;
an integration must preserve attribution and the existing order boundary.

**Answer (2026-09-09):** “Persistent cockpit + integrations (Recommended)”

**Recorded decision:** PD-028. The persistent cockpit service owns the investing
application and its research, positions, schedules and controls. Discord and
host/plugin/MCP integrations call the same application services. A qualified external
executor such as Recursion can supply bounded model jobs. This resolves PD-019's
ownership question. Exact process packaging, protocols and job-contract fields remain
specification work; the answer does not itself change current runtime authority.

### Q21. Must a working Recursion integration be demonstrated before the paper MVP releases?

Recommendation: release a working standalone cockpit with the shared model-job contract
and integrate Recursion when it passes that contract's real integration checks. Keep
the contract and compatibility investigation in current design; a fake executor is
not evidence that Recursion works. Alternatively, require both standalone operation
and demonstrated Recursion integration for the first release, accepting a dependency
on Recursion readiness. This decides release dependency, not permission to silently
remove previously selected product capabilities or to claim an untested integration.

**Answer (2026-09-09):** “Standalone MVP; Recursion when qualified (Recommended)”

**Recorded decision:** PD-029. Require a working standalone MVP and design the shared
execution contract now. Recursion integration follows demonstrated compatibility and
is not a first-release dependency. The standalone path must supply the agreed model
routing, research and autonomous paper workflow; fake jobs or a placeholder adapter
cannot satisfy that acceptance. Preserve the early Recursion architecture work without
claiming its actual integration has passed.

## Round 7a — engine releases and failure recovery

### Q22. Who may deploy changes to the cockpit engine itself?

Recommendation: agents investigate, implement and test improvements in isolated
revisions; the owner approves installing a new software release. Routine operation
continues on the approved version. Alternatively, allow automatic deployment of a
predefined class of maintenance changes after the required automated gates, with
rollback and audit evidence. That requires a precise change classifier and release
contract. Neither option relaxes owner strategy activation or approval for higher
hard caps. This asks about the future product's development/release authority, not
permission to modify or deploy runtime code during this documentation session.

**Answer (2026-09-09):** “Agents develop/test; I approve releases (Recommended)”

**Recorded decision:** PD-030. Agents investigate, implement and test isolated
revisions; Lucas approves installing a new software release. Routine operation stays
on the approved version while improvements are prepared. Automatic maintenance
deployment is not selected. Strategy activation and increases to hard caps retain
separate approvals. Release evidence, installation, rollback and audit details remain
to be specified; this is a future product boundary, not runtime deployment permission
for the current documentation session.

### Q23. When may suspended work resume after a transient dependency failure?

Recommendation: allow automatic recovery under a preapproved policy only after
dependency health, evidence freshness, quota/limits and paper reconciliation pass
the required checks. Explicit human halts and integrity/authority holds require an
authorized human release. If a cause or reconciliation result remains uncertain,
the affected actions remain suspended. The alternative is human approval for every
resume. Automatic recovery must never silently promote a mode or override another
hold; its implementation requires reconciliation with current durable safety controls.

**Answer (2026-09-09):** “Automatic checked recovery; human release for holds (Recommended)”

**Recorded decision:** PD-031. Under an approved policy, recover automatically from
transient dependency failures only after health, evidence freshness, resource limits
and paper reconciliation pass. Explicit human halts and integrity/authority holds
require authorized human release; unresolved conditions remain suspended. Recovery
cannot override another hold or exceed the approved operating authority. Detailed
checks and durable/audited transitions need specification against the existing safety
controls before implementation. Existing-position behavior is recorded separately in Q24.

### Q24. What should happen to existing paper positions while required inputs are unavailable?

Recommendation: block new or increased exposure, retain monitoring, and allow only
previously approved protective actions whose own evidence and execution requirements
remain satisfied. Examples for the later policy include cancelling unfilled entries
and risk-reducing exits when sufficiently supported. No model invents an exit from
missing data. Alternatives require operator direction for every position action, or
use a preapproved orderly close-out policy when its required inputs permit it. The
exact action matrix must address open orders, partial fills, uncertain submissions
and reconciliation; the general autonomy answer does not specify those mechanics.

**Answer (2026-09-09):** “Monitor + permitted protective actions (Recommended)”

**Recorded decision:** PD-032. During missing-required-input conditions, block new
or increased exposure for affected actions, monitor existing paper positions and
allow only previously approved protective actions whose own requirements pass.
Do not infer an automatic liquidation or bypass of evidence/order controls. The
specific action matrix must cover open orders, partial fills, uncertain submissions,
protective-action eligibility and reconciliation before implementation.

## Round 7b — initial strategy-suite contract

### Q25. How broad should the initial activated strategy suite be?

Recommendation: two or three distinct, independently specified and evaluated strategies
for the first release. Agents conduct a bounded feasibility comparison, including the
existing cash-merger strategy, before proposing the roster for owner approval. Evidence
availability, data costs, testability and portfolio interactions inform the comparison;
minor parameter variants do not count as distinct strategies. Alternatives are a
larger four-to-six-strategy first release or an exact roster supplied by Lucas.
Every activated strategy must satisfy its applicable evidence and activation contract;
no required count can be met by inventing evidence or weakening a gate. Evaluation
does not itself establish profitability. The product supports expansion after release
under the selected development, evaluation and owner-activation process.

**Answer (2026-09-09):** “Go with two or three, but please make sure to make reminders
that the strategies need to be expanded in the future. The more strategies, the higher
the trading plane and potential ROI. We are trying to make a margin of profit that
succeeds so that it outweighs my expenses in growth eventually (around $210/month)”

**Recorded decisions:** PD-033 selects two or three launch strategies under the
proposed feasibility/evaluation/owner-approval process. PD-034 makes future expansion
and reminders explicit, rather than treating the launch count as a permanent limit.
PD-035 records eventual profitable growth beyond approximately $210/month in expenses
as an economic objective. The exact expense components and starting capital are not
yet established. The owner's expectation that more strategies improve opportunity/ROI
is a hypothesis to evaluate, not evidence of a monotonic return increase.

**Recommended specification detail, not separately ratified:** maintain an expansion
candidate queue and include its progress in periodic operator reports, with separate
alerts for activation-ready candidates. Specify reminder cadence and measure each
addition's incremental opportunity, costs and portfolio overlap. Report net trading
P&L against the expense benchmark, retaining uncertainty, losses and realized versus
unrealized components. Do not silently discount shared subscription expenses or count
the same cost twice. PD-024's honest-evaluation MVP criterion remains; paper results
do not pay actual expenses or grant live-capital authority.

### Q26. Which holding horizons must the first release support?

Recommendation: positions ordinarily held for days to months, with prompt event-driven
research and policy-permitted protective actions. This keeps sub-day trading mechanics
outside the initial strategy requirement without delaying relevant news handling or
forbidding a justified same-day protective exit. Alternatively, require both intraday
and multi-day strategies at first release, or focus the initial suite on intraday
strategies. Intraday scope adds data-timeliness, fill and execution evaluation demands.
Exact schedules and per-strategy horizons remain in the strategy contracts. This is
a product capability choice, not a recommendation to buy, sell or hold any asset.

**Answer (2026-09-09):** “I think this should be decided by the engine.”

**Recorded decision:** PD-036. Delegate holding-horizon decisions to the engine
within activated strategy evidence, supported data/execution capabilities and approved
policy. Do not impose the suggested universal days-to-months scope. The feasibility
comparison and strategy contracts still need to establish supported horizons; the
answer does not itself require or certify all intraday/high-frequency capabilities.

## Round 7c — economic evaluation baseline

### Q27. What capital and expense baseline should the economic evaluation use?

Question presented: “To evaluate your goal of eventually covering roughly $210/month
and growing beyond it, what starting paper balance should we model, and what expenses
does the $210 include (for example, subscriptions, hosting, or data)? Approximate figures
are enough; these are evaluation assumptions, not a commitment of real capital.”

Use owner-provided assumptions to make the economic target measurable. Keep the
expense-coverage goal distinct from hard spending limits and the live-capital gates;
do not invent a starting balance or assume the figure includes every engine cost.

**Answer (2026-09-09):** “the $210 pays for the ChatGPT Pro subscription + Opencode Go
subscription. I'm currently receiving $500/month for the next year in student funding.
I'm trying to develop and make something for myself where I can pour a percentage of
my monthly income in and grow the money simultaneously. I'm likely going to transfer
a minimum of 2k.”

**Recorded decisions/context:** PD-037 identifies the $210/month expense as the two
subscriptions. PD-038 records the funding and contribution intention and the likely
minimum transfer. A $2,000 paper starting balance is a provisional evaluation assumption
derived from that answer, not a definitive deposit instruction. No contribution
percentage or schedule was supplied. Do not assume $500/month is entirely available
for investment, infer other income/expenses, or extend the stated funding indefinitely.

**Derived arithmetic, not a return forecast:** $210 / $2,000 = 10.5% per month to
cover the stated subscriptions at a static $2,000 balance after trading costs, before
additional growth, other costs and taxes. This was checked with decimal arithmetic.
Do not present the target as demonstrated or routinely attainable. The selected
MVP remains paper-first and does not authorize the intended future transfer.

**Recommended evaluation detail:** distinguish external contributions/withdrawals from
P&L and compare portfolios using the same external cash flows. Retain a no-contribution
reference scenario until a contribution amount is configured; this is a comparison,
not a recommendation about how much of the funding Lucas should invest. Preserve the
full owner expense-coverage benchmark alongside any separately labelled shared-cost
allocation. Unknown additional costs or tax treatment remain visible.

## Round 8a — release evidence and expansion review

The [release-evidence proposal](product-acceptance.md) specifies the workflow evidence
to collect. Owner decisions below set two remaining operating expectations; passing
an observation window alone does not establish product acceptance or strategy advantage.

### Q28. What minimum unattended observation period should the first paper pilot have?

Recommendation: at least 20 market days on the primary Linux deployment, plus the
required scenario coverage and existing strategy-evidence gates. This is a proposed
operational observation minimum, not a statistically justified profitability window.
Longer strategy lifecycles or incomplete evidence can require more time. Alternatives
are at least 60 market days or a coverage-driven pilot whose minimum duration is
selected and frozen during the feasibility work. Do not force trades to finish a
calendar milestone, count offline replays as deployed observations, or count a material
unqualified revision as part of one unchanged candidate. Predeclare continuation/reset
rules and report the exact builds observed.

**Answer (2026-09-09):** “20 market days + required coverage (Recommended)”

**Recorded decision:** PD-039. Require at least 20 market days of unattended paper
observation on the primary Linux deployment plus required workflow coverage and
existing strategy-evidence gates. The minimum is operational; longer lifecycles or
evidence gaps can extend the pilot. Freeze its observation and revision rules before
running; elapsed days, fake adapters or manufactured trades do not establish acceptance.

### Q29. How should reminders about strategy expansion appear after launch?

Recommendation: a monthly expansion summary plus a notification when a candidate is
ready for owner activation review. Include queue progress, missing evidence and the
next research step so expansion remains visible without requiring a daily review.
Alternatives are a weekly summary with candidate-ready notifications, or candidate-ready
notifications alone. Readiness does not activate a strategy, and summary cadence does
not authorize additional research spending beyond the existing caps. This chooses a
future cockpit reporting behavior; no external messages are sent by the grill.

**Answer (2026-09-09):** “Monthly summary + ready notifications (Recommended)”

**Recorded decision:** PD-040. Use monthly expansion summaries and candidate-ready
notifications, showing queue progress, missing evidence and next steps. Activation
still requires owner approval. The cadence adds neither a daily review obligation
nor authority to increase research budgets. Detailed scheduling, duplicate suppression
and delivery/retry behavior remain specification work.

## Round 8b — final scope boundaries

### Q30. How does a newly discovered symbol become eligible for paper trading?

Recommendation: automatic admission under owner-approved eligibility rules after
required evidence, instrument, strategy and risk checks pass. Research or a human
submission alone grants no execution eligibility. Alternatively, require owner approval
of every new executable symbol. The automatic option requires a scoped, reviewed
replacement for the current static-allowlist design and remains subject to the single
deterministic order path, strategy activation and owner limits.

**Answer (2026-09-09):** “Automatic within approved eligibility rules (Recommended)”

**Recorded decision:** PD-041. Permit automatic admission of researched stocks/ETFs
within owner-approved eligibility rules after all required checks pass; individual
symbol approval is not the product target. Research and submissions alone cannot
change eligibility. The implementation must replace the current static-allowlist
design through a scoped review and retain deterministic admission and the single
order path. Detailed evidence binding, expiry and rechecks remain specification work.

### Q31. How much tax-aware functionality belongs in the MVP?

Recommendation: an exportable trade/cash-flow ledger and explicit configurable tax
assumptions in economic reports, deferring a full jurisdiction-specific tax engine.
Any enforceable tax-related restriction requires verified applicable rules and context.
Alternatively, require full tax calculations in the MVP, adding account/jurisdiction,
rule-coverage and validation work. The recommendation does not select a tax rate or
claim tax compliance; unknown assumptions remain visible and no tax filing is implied.

**Answer (2026-09-09):** “Full tax calculations in the MVP”

**Recorded decision:** PD-042. Include full investment-tax calculations in the MVP.
The ledger-and-assumptions-only recommendation is not selected. Scope calculations
to explicitly supported tax jurisdictions, account types and years with the necessary
transaction and external context. Do not silently defer the engine, assume a flat
rate, or equate a paper estimate with a filed/verified tax liability. Preparing or
filing a complete personal return is not implied by the investment-tax scope.

**Recommended specification detail:** verified, versioned rule sources; deterministic
calculation with auditable intermediate values; explicit required outside-account
inputs; missing-context outcomes; independent reference cases; source/rule updates
under the existing release and policy approvals. Q32 requests the context needed to
research the actual tax coverage rather than guessing it from the workstation locale.

## Round 8c — tax coverage

### Q32. Which jurisdictions and account types should the full tax calculations support?

Question presented: “Which country and state/province tax jurisdictions, and which
account type, should the MVP's full investment-tax calculations support—for example,
a taxable brokerage account or a retirement account?”

This information determines the applicable rules and required inputs for PD-042.
Research official sources after the owner identifies the intended coverage. The
workstation timezone and use of US stocks do not establish personal tax jurisdiction.

**Answer (2026-09-09):** “It should be configurable, but I reside in Fremont,
California in the United States. It should support brokerage accounts and retirement
accounts”

**Recorded decision:** PD-043. Support configurable tax contexts and both taxable
brokerage and retirement accounts. The stated residence establishes the initial
jurisdiction discussion; it does not establish full-year tax residency, filing status,
income classification, citizenship or the owner's actual account inventory.

### Q33. Which configurable jurisdictions must have validated calculations at launch?

Recommendation: validated US federal and California calculations first, with a
configurable jurisdiction interface for adding further verified rule sets. Unsupported
jurisdictions remain explicit. Alternatively, name additional jurisdictions that must
already be supported in the MVP.

**Answer (2026-09-09):** “US federal + California first; extensible rules (Recommended)”

**Recorded decision:** PD-044. US federal and California are the initial validated
coverage; additional jurisdictions use extensible verified rules. Configurability
does not imply that an arbitrary jurisdiction already has a correct calculation.

### Q34. Which retirement-account families must the MVP calculate taxes for?

Recommendation: Traditional/Roth IRAs plus 401(k), 403(b) and governmental 457(b)
accounts, with their account-specific rules. SEP/SIMPLE and inherited-account cases
add further coverage. Tax support is tracked separately from whether a broker adapter
can trade that account. Alternatives offered were IRAs first, or the larger set
including SEP/SIMPLE and inherited cases.

**Answer (2026-09-09):** “IRAs + common workplace plans (Recommended)”

**Recorded decision:** PD-045. Include the recommended IRA and workplace-plan families
at launch, preserving their applicable pre-tax, after-tax and Roth distinctions.
SEP/SIMPLE and inherited-account cases were not selected for launch; do not silently
apply a supported account's rules to an unsupported family or special case.

**Specification work:** the [tax contract](product-tax-contract.md) maps the selected
coverage to required inputs, deterministic calculations, source/version provenance,
explicit incomplete results and independent reference validation. Official IRS/FTB
sources were inspected on 2026-09-09; their published examples are a source inventory,
not implementation evidence or blanket validation of a 2026 rule set. Q35–Q36 address
two consequential operating/data-policy choices before final consolidation.

## Round 9 — tax context and private data

### Q35. What happens when personal tax context is incomplete?

Question presented: “Full calculations need information beyond the cockpit's trades,
such as filing status, other income, account basis and relevant outside-account
activity. I recommend allowing paper operation with an explicitly configured scenario
while showing which personal-tax inputs are missing; any action requiring an
unverified tax restriction stays blocked. Should missing personal context instead
suspend all new paper exposure in that account?”

Options: continue with explicit paper scenarios (recommended), or suspend new exposure
until tax context is complete. Neither permits invented personal liability or a
missing tax-dependent condition to pass. Full tax-engine launch coverage remains
required whichever operating policy is selected.

**Answer (2026-09-09):** “Continue with explicit paper scenarios (Recommended)”

**Recorded decision:** PD-046. Continue paper operation using an explicit configured
scenario when personal context is missing. Show the missing facts and separate the
scenario from an actual personal-liability claim. Tax-dependent actions remain blocked
when their required restrictions cannot be verified; other required controls still
apply. This does not defer the full tax engine or make incomplete results a release PASS.

### Q36. What private financial data may external model providers receive?

Question presented: “What may the cockpit send to external model providers? I recommend
keeping raw private account/tax documents local by default and sharing only the minimum
approved, redacted facts; you can authorize broader access for a particular document or
provider. Public research material can use the normal approved routes.”

Options: local private documents with minimal approved facts (recommended); private
documents allowed on owner-approved provider routes; or all private financial data
kept local. This chooses the future product's disclosure policy, not permission to
upload any present account/tax document during the documentation work.

**Answer (2026-09-09):** “Local private documents; minimal approved facts (Recommended)”

**Recorded decision:** PD-047. Keep raw private account/tax documents local by default
and send only minimum approved, redacted facts to external models. Broader access
requires owner authorization scoped to a document or provider. Public research uses
normal approved routes. Apply disclosure rules before dispatch, including extraction,
OCR, model handoffs, retries and fallback providers; a cheaper route cannot expand
data access. Credentials remain excluded under the existing secret invariant.

## Final review — consolidated contract

Prepared after Round 9: a rewritten [PRODUCT](../PRODUCT.md), concrete
[operating contracts](product-operating-contracts.md), the cited
[tax contract](product-tax-contract.md), [acceptance contract](product-acceptance.md)
and [delivery plan](product-delivery.md). The original answers remain intact above.

The package prepared for review specified shared artifacts, user permissions/revocation, supported
file types and technical size limits, route/resource accounting, failure/recovery and
isolated improvement behavior. It defines bounded implementation slices and explicit
dispositions for DEC-03, current paper approval/model boundaries, static eligibility,
independent labels, risk applicability and existing evidence/platform/pre-live gates.
These details were proposed for this final review and then adopted under PD-048.

### Q37. Adopt the consolidated product and delivery contract, or revise it first?

Recommendation: adopt the prepared contract and complete the documentation branch
under the existing publication/merge instruction. The scope includes all selected
launch requirements; the proposed operating defaults and delivery/evidence contracts
are included in this review. Ratification establishes product direction and work
dependencies, not a claim that the software, strategies or tax rules are qualified.
Alternatively, identify changes before finishing the documentation rounds.

**Answer (2026-09-09):** “Adopt contract; finish docs branch (Recommended)”

**Recorded decision:** PD-048. Adopt the consolidated product and supporting operating,
tax, acceptance and delivery contracts, including the proposed technical defaults and
explicit authority/evidence dispositions. Finish the documentation branch under the
existing publication/merge instruction. This completes the product rounds; no further
product approval is pending and no runtime or empirical PASS is implied.

## Evidence dependencies

- Actual recent delivered briefs and their source packets on the operational host
  have not been reviewed in this session. Their quality bottleneck remains unclassified.
- [The feasibility review](research/gate-feasibility-and-value-2026-09-06.md) records
  unresolved same-date cohort attainability and missing independent precision/recall
  and usefulness measurements. These are empirical questions, not preferences.
- The existing canonical safety constraints remain in [CLAUDE.md](../CLAUDE.md) and
  [AGENTS.md](../AGENTS.md); detailed empirical and pre-live gates remain in PLAN and
  the existing subsystem documents.
- The local Recursion tracking snapshot `origin/master` at `f197669` was inspected
  read-only for the architecture comparison, including v3 product, route, migration
  and M1B execution contracts. It supplies relevant design and bounded fixture evidence,
  not cockpit integration or live subscription/host conformance. The cockpit runner's
  subprocess contract is not already a generic Recursion job adapter. See the comparison
  for exact source paths and remaining compatibility checks.
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
- PD-028/PD-029 resolve PD-019: persistent cockpit ownership and a standalone MVP,
  with Recursion integrated when qualified. The comparison records inspected source;
  actual job-adapter conformance remains empirical work, not a first-release dependency.
- PD-042 through PD-045 select full US federal/California investment-tax calculations
  for taxable brokerage, Traditional/Roth IRAs and 401(k), 403(b), governmental 457(b)
  accounts, with extensible verified coverage. The [tax contract](product-tax-contract.md)
  records official IRS/FTB source findings and proposed input/calculation/validation
  requirements. Current-year qualification, implementation and reference evidence are
  still required; reviewed publications are not a working or validated tax engine.
- PD-037 scopes the owner-stated $210/month to subscriptions; PD-038 supplies a likely
  minimum transfer from which $2,000 is used provisionally for paper evaluation. These
  are not independently verified pricing/account observations or transfer authority.
  Contribution rate, other operating costs and tax treatment remain open. The 10.5%
  monthly subscription-coverage hurdle is arithmetic, not expected strategy performance.
- General financial reference checked for Q25: the SEC explains that
  [fees and expenses reduce investment returns](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/updated)
  and that an [ETF is not necessarily diversified](https://www.investor.gov/introduction-investing/getting-started/asset-allocation).
  These support attention to costs and underlying exposures. The inference for this
  product is to evaluate strategy additions on their incremental contribution and
  overlap; neither source establishes this engine's profitability or an optimal count.
- Financial context checked for Q27: the SEC describes
  [investment risk as uncertainty and potential loss](https://www.investor.gov/introduction-investing/investing-basics/what-risk).
  It does not supply a return forecast for this product. The pilot/economic evaluation
  must preserve losses and uncertainty rather than assume the expense target is attainable.

## Resume here

The grill is complete. PD-048 records Lucas's adoption of PRODUCT and its operating,
tax, acceptance and delivery contracts on 2026-09-09. All 37 questions have answers
and 48 decisions are recorded. No further approval is needed to finish the already
authorized documentation publication and merge.

Ratification updates PRODUCT's live status, PLAN's product/delivery precedence,
REGISTER's RW-25 and DEC-03 dispositions, the docs index and repository/agent entry
points. Current runtime invariants remain unchanged; new paper authority requires
its separately scoped reviewed implementation. No runtime tests, operational pilot
or strategy/tax evidence are claimed by this documentation package.

Documentation is published in [PR #85](https://github.com/NotSkittlz/ai-investment-cockpit/pull/85)
from `work/rw25-product-grill`; that PR records the merge disposition. The repository
was verified private. Local document links, whitespace and decision-sequence checks
pass; the canonical nine-invariant block and Codex boundary are unchanged, and the
application/CI diff is empty. The runtime matrix was not rerun for documentation-only
changes. Interview checkpoints remain in branch history.

After the PR merges, the next development step is a bounded REGISTER claim for delivery
slice A: strategy feasibility and evaluator/reference qualification, informed by actual
delivered/empty run artifacts. Later slices provide connected implementation and the
required release evidence; do not restart the product grill. If resuming before merge,
finish the already authorized merge after verifying the PR head and required checks.

---
title: Cockpit product contract pending replacement acceptance
status: authoritative-until-replacement-digest-acceptance
last-verified: 2026-10-16
---

# Cockpit product contract

> **Replacement notice:** Lucas directed a full product reset under
> [CSL-212](https://linear.app/csl-laboratories/issue/CSL-212). The proposed
> successor contract is under review in
> [docs/replacement/SUCCESSOR-PRODUCT.md](docs/replacement/SUCCESSOR-PRODUCT.md).
> Until the updated replacement digest receives its required human Linear receipt,
> this September contract remains the authoritative product contract while also
> serving as historical evidence and a source of candidate safeguards. Current runtime
> rules, nine invariants and pre-live controls remain authoritative until separately
> reviewed implementation amendments; the successor direction itself grants no live
> authority or empirical PASS.

**Owner:** Lucas. **Planning package:** RW-25.
**Stage:** ratified by Lucas on 2026-09-09 under PD-048 (final review Q37).

This contract owns product purpose, scope and acceptance. [PLAN](docs/PLAN.md) owns
implementation order. The canonical invariants in [CLAUDE](CLAUDE.md) and
the runtime boundary in [AGENTS](AGENTS.md) remain current; a product decision does
not by itself change execution permissions or pass an empirical gate.

The [decision record](docs/product-planning.md) preserves owner answers and their
history. The [operating contracts](docs/product-operating-contracts.md),
[tax contract](docs/product-tax-contract.md),
[acceptance contract](docs/product-acceptance.md) and
[delivery plan](docs/product-delivery.md) were adopted together under PD-048.
They define required behavior and qualification work, not already verified capability.

## Purpose and first release

The cockpit is an autonomous, subscription-powered investing application that turns
evidence into useful research, qualified strategy decisions and controlled paper
positions. Lucas's motivating problem is: “The runs finish but never produce useful
analysis or paper trades.” A successful process exit is insufficient product value.

The first complete release integrates awareness, strategy research and the full paper
position lifecycle. It operates without a mandatory daily review, while Lucas can
inspect, question and direct it through Discord or the host. Routine paper trades
operate under approved standing policy; Lucas approves strategy activation, software
releases and increases to hard limits.

The economic objective is eventual profitable growth beyond operating expenses.
First-release success is a useful, reliable working product with credible, honest
evaluation, including unfavorable results. Positive profitability is not required
and is not promised. Useful research should arrive in early delivery increments;
the complete MVP still includes actual paper operation and the full selected scope.

## One connected application

A persistent cockpit owns evidence, cases, strategy and portfolio state, schedules,
policy and audit. The three functions share durable contracts:

| Function | Responsibility | Result passed onward |
|---|---|---|
| Awareness | Ingest core coverage, bounded discoveries and human material; detect meaningful changes | Retained evidence with provenance and a research trigger |
| Strategy research | Build cited cases, investigate unknowns, assess qualified strategies and proposed actions | Versioned case, evidence, assessment and explicit qualify/watch/reject outcome |
| Controlled execution | Enforce approved policy, evidence and aggregate limits; manage paper orders and positions | Reconciled positions, cash, outcomes, costs and evaluation feedback |

Extend the existing application composition and shared services. Discord and host
interfaces access those same actions and permissions. Plugin/MCP integrations follow
the same boundary; they do not become independent portfolio owners or provide models
with broker tools.

The MVP works standalone. Define a bounded executor contract now, then integrate
Recursion when actual compatibility checks qualify it. Working Recursion integration
is not a release dependency. The standalone executor must genuinely deliver the
required model-routing and workflow capabilities.

Support Windows and Linux at first release, with Lucas's Linux box as the primary
deployment. Both require installation, credential provisioning, unattended startup,
shutdown/restart, recovery and diagnostics acceptance. A specific hosting provider
and active-active portfolio replication are not selected.

## Research and the daily experience

Research combines owner-selected core coverage, bounded candidate discovery and human
ingestion. First-release submissions include tickers, links, ideas, questions,
documents, screenshots and images. Rich document/visual intake is required, with a
declared supported-format and size manifest, visible extraction results and useful
handling of unsupported, unreadable or ambiguous material.

Relevant new evidence, authorized requests and bounded scheduled checks initiate or
revisit cases. Deduplication and relevance checks avoid repeating unchanged research.
Investigate uncertain candidates within a work budget, then monitor for named missing
evidence and specific revisit triggers. Keep hard disqualifications distinct from
waiting for evidence.

Deliver short Discord updates linked to detailed cases containing significance,
supporting and opposing evidence, unknowns and what would change the assessment.
Material factual claims have citations. Source inspection shows the retained input,
publisher/submitter, acquisition route, publication/retrieval times, locators and
relevant extraction/transformation steps. Raw evidence remains separate from summaries.

Status exposes last useful output, current work, coverage/freshness, quota/dependency
health, paper positions, holds and next actions. Empty runs report what was screened,
why candidates were rejected or remain unresolved, and the next evidence trigger.
No routine 10–20-minute review obligation is imposed. Failed notifications remain
visible on the host and do not erase the underlying work or controls.

## Strategies and paper trading

Launch with **two or three distinct strategies**, chosen through a bounded feasibility
comparison that includes the existing cash-merger candidate, then approved by Lucas.
Parameter variations do not count as distinct strategies. Each needs its own evidence,
evaluation and versioned contract. The engine selects strategies and holding horizons
within those qualified contracts; no universal days-to-months holding restriction is
selected, and no unqualified execution speed or horizon is implied.

Initial paper execution is **cash-funded, long-only US stocks and ETFs**. Research may
examine broader markets. Instrument admission must cover the required data, execution,
settlement, tax and risk capabilities. No short positions, margin borrowing, options
or other asset-class execution belongs in this release.

New symbols become eligible automatically under owner-approved rules after evidence,
instrument, strategy and risk checks pass. A submission or model recommendation alone
does not admit a symbol. Bind eligibility to evidence and policy versions, expiry and
action-time rechecks through the reviewed implementation of this policy.

Manage entry, monitoring, permitted adjustments/cancellations, exit, reconciliation
and recorded outcomes. Use one portfolio/cash ledger and explicit strategy attribution.
Resolve competing proposals under deterministic policy and enforce aggregate exposure
and resource limits. Partial fills and uncertain submissions require reconciliation;
they do not justify duplicate orders.

Agents develop/test strategy candidates and material revisions in isolated work.
Lucas approves activation, with explicit delegation only under the owner-defined
permission policy. Active strategies then operate autonomously within standing policy.
Model confidence and code-test success cannot substitute for strategy evidence.

Keep expansion visible after launch through a continuing candidate queue, monthly
summaries and candidate-ready notifications. Evaluate additions for incremental
opportunity, costs and overlapping exposures. Two or three is a launch count, not a
permanent ceiling; more strategies alone do not establish higher returns.

## Model routing and hard limits

Use configurable task-based routing informed by qualified capabilities, available
subscription quota, diversity, cost and latency. Luna → Spark → Sol → Astra is the
owner's illustrative escalation profile, not a compulsory ladder. Actual access and
quota behavior must be verified for configured subscriptions/CLIs.

Keep deterministic parsing/normalization where appropriate and use models for semantic
extraction, interpretation, strategy reasoning and review. Escalations retain original
evidence and contradictions, with schema-gated outputs, deadlines, cancellation and
usage receipts. Higher capability does not grant greater financial authority.

The owner configures hard outer inference/quota and paper capital/risk limits; the
engine allocates within them. Reserve across concurrent jobs and strategies, account
for retries and escalation, and distinguish measured, estimated and unknown usage.
Unavailable usage never becomes zero. Require approved sufficiently capable fallback
routes; do not silently expand spending, tool access or private-data disclosure.

Numeric operating caps are mandatory configuration before autonomous operation.
The owner's income, subscription expense and provisional paper balance are not
authorization for a particular spending or risk limit.

## Identity, privacy and control

Identify the owner and explicitly admitted users across surfaces. Use stable principals
and owner-defined roles with per-action permissions enforced by shared application
services. Keep actual credentials in the OS credential store and private identity/access
configuration out of Git. A display name, channel membership or shared credential
does not establish individual authority.

Audit the requester, executing service/job, action, policy and approval/standing
authorization reference, result and refusal reason. Autonomous actions must not
fabricate contemporaneous human approval. Recheck current permissions for consequential
actions and prevent revoked grants from authorizing queued work.

Raw private account/tax documents stay local by default. External models receive only
minimum approved, redacted facts unless the owner authorizes broader access for a
document or provider. Apply this to extraction/OCR, prompts, handoffs, retries, fallback,
evaluation and notifications. Public research uses approved normal routes; credentials
remain outside model context. Private-data viewing needs its own permission.

## Failure and recovery

On a required feed/model failure or exhausted quota, try approved qualifying fallback
within budget. If requirements still fail, suspend affected new/increased exposure,
continue permitted monitoring and allow only preapproved protective actions whose
own requirements pass. Missing inputs do not cause blind liquidation.

Allow automatic recovery under approved policy after health, freshness, resource and
paper-reconciliation checks pass. Explicit human halts and integrity/authority holds
require authorized human release plus required checks. Uncertainty remains suspended.
Discord reconnection alone cannot clear a hold or authorize an action.

Implement these policies through the separately reviewed paper-autonomy and recovery
design. Current global fail-closed behavior remains authoritative until that work
qualifies the new behavior.

## Full investment-tax calculations

The MVP includes validated **US federal and California** investment-tax calculations,
with configurable, extensible verified jurisdiction rules. Support taxable brokerage,
Traditional/Roth IRAs, 401(k), 403(b) and governmental 457(b) accounts with their
account-specific treatment. Tax support is distinct from a broker's ability to trade
an account. SEP/SIMPLE, inherited-account cases and other jurisdictions are later
coverage; identify unsupported contexts explicitly.

Use the complete applicable inputs, deterministic calculations, versioned official
rules, citations, inspectable intermediate results and independent reference cases.
Qualify the actual supported tax year; old examples do not validate a newer rule set.
Full personal-return preparation or filing is outside the selected investment scope.

Missing personal context permits continued paper operation using an explicitly
configured scenario, with missing inputs and assumptions visible. It cannot establish
an actual personal-liability claim or pass a required unverified tax restriction.
Ordinary supported reference cases must produce complete calculations; an engine that
only emits incomplete results does not satisfy the selected full-tax requirement.

## Economics and evaluation

Use **$2,000 as a provisional paper starting balance** from Lucas's likely minimum
transfer intention. The owner-stated **approximately $210/month** covers ChatGPT Pro
and OpenCode Go; it is neither verified current pricing nor an all-inclusive expense
estimate. Contribution amount and timing remain configurable and unset. Owner financial
context remains in the decision record rather than becoming hardcoded defaults.

Separate contributions/withdrawals from returns and compare baselines with matched
cash flows. Show realized/unrealized P&L, trading and engine costs, tax effects,
drawdown, uncertainty and expense-coverage surplus/shortfall without double counting.
At a static $2,000, $210 is a 10.5% monthly subscription-coverage hurdle after trading
costs, before other costs, taxes and additional growth. This is arithmetic, not an
expected return. Paper profits are evaluation results, not cash available for bills.

Aim for fully automated research-quality and routing evaluation. Use source-grounded
references, production replays and separately qualified model judges; include rejections,
early dismissals, omissions and escalations. Measure the evaluator's own errors.
Model agreement and schema validity do not establish factual correctness. Keep
unverified judgments visible and routine human sample review out of the daily workflow.

Freeze populations, references, versions, metrics, thresholds, costs and baseline
assumptions before observing evaluation outcomes. Existing independent evidence and
human-label requirements have an explicit disposition in the delivery plan; automation
does not silently waive them. Optional usefulness feedback remains welcome.

## Acceptance and releases

Release requires the selected capabilities through the real application, qualified
installed adapters, operating acceptance on both Windows and Linux, the existing
four-cell DEC-09 matrix, applicable independent strategy/evaluation evidence and
**at least 20 market days** of unattended paper observation on the primary Linux host
plus required workflow coverage. Longer lifecycles or missing evidence extend the
pilot. Replays and elapsed days cannot replace actual paper-broker conformance, and
ineligible trades must never be manufactured for coverage.

Within an owner-approved development policy and budget, incidents, evaluation failures
and requests can trigger bounded agent improvement jobs in isolated workspaces.
Agents investigate, implement and test isolated software revisions. Lucas approves
installation of an immutable release with validation, migration/recovery and rollback
or forward-repair evidence. The running engine stays on its approved version.
Installation does not itself activate strategies, increase limits or grant live mode.

PRODUCT owns product purpose, scope and acceptance; PLAN owns delivery
order; REGISTER owns work claims; canonical subsystem contracts and evidence own
technical controls and measured gates. The delivery plan identifies the required
amendments to older MVP, DEC-03, approval/model, eligibility and evidence policies.
Historical evidence stays historical. Live trading remains separately gated.

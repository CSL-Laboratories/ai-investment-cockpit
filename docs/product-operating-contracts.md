---
title: RW-25 shared workflow and operating contracts
status: live
last-verified: 2026-09-09
---

# Shared workflow and operating contracts

This document turns the decisions in [PRODUCT](../PRODUCT.md) into application
contracts adopted by Lucas under PD-048 on 2026-09-09. The
[planning record](product-planning.md) preserves the answers and final ratification.
Current safety and execution authority remains unchanged until the separately reviewed
implementation packages in [the delivery plan](product-delivery.md) qualify it.

## One workflow, persistent state

Extend the existing `main.compose` application graph. Awareness, research and execution
exchange durable typed artifacts through shared services, with a trace from intake to
outcome. They are not three independent bots copying conclusions between prompts.

| Stage | Durable result | Transition and refusal contract |
|---|---|---|
| Intake and awareness | Input ID, raw reference/hash, provenance, acquired/published times, requested question, principal and disclosure classification | Normalize and deduplicate; report unreadable, unsupported, private-route or source-acquisition problems against the input |
| Research | Versioned case, material claims and citations, significance, supporting/opposing evidence, unknowns, assessment-change conditions and next review trigger | Bounded investigation ends in a researched conclusion, a named evidence watch or a recorded disqualification; lack of evidence is not a successful recommendation |
| Strategy assessment | Case revision, strategy/version, required evidence, proposed horizon/action and eligibility assessment | Use only approved strategy contracts; unqualified strategies remain research candidates; a model proposal grants no execution authority |
| Controlled paper operation | Policy/eligibility versions, validated proposal, deterministic control result, standing authorization reference and order intent | Revalidate at action time through the single gateway path; reject stale, conflicting, out-of-budget or unqualified requests |
| Position and outcome | Broker order/fill identities, position/cash state, reconciliation, exit reason, costs, tax result and evaluation references | Monitor and manage the whole lifecycle; retain uncertain and adverse outcomes; feed evidence back into cases and evaluation |

Artifact revisions are immutable. Corrections create a new revision and invalidate
dependent decisions as required; they do not overwrite evidence used by an earlier
order. Bind results to the input, policy and job revisions that produced them. Reject
late results for superseded or cancelled work. A repeated event may update its case
but must not create duplicate jobs, notifications or orders without a new valid intent.

Research starts from core coverage, a bounded discovery queue or authorized submissions.
Relevant new evidence and user requests can trigger immediate work within limits;
scheduled freshness/coverage checks catch omissions. Record why an event is relevant.
Unchanged inputs do not restart an unlimited investigation. Each case has an approved
work budget, deadline and revisit condition; escalation consumes that same budget.

## Intake and citations

Launch format manifest: text tickers, URLs and free text; PDF, DOCX, TXT/MD,
CSV/TSV and XLSX documents; PNG, JPEG and WebP images, including screenshots. The
configurable technical defaults are 20 MiB per file, 100 document pages, 50,000 populated
spreadsheet cells and 25 megapixels per image. These are adopted product defaults,
not provider entitlements or spending authority. Each surface advertises any smaller
transport limit and offers the authenticated host intake when its transport cannot
carry an otherwise supported file. Never silently truncate a submitted research case.

Use bounded parsing, decompression, OCR, retrieval and redirects. Do not execute
macros, embedded programs or instructions found in material. Protected/corrupt files,
unsupported types and exceeded limits produce a clear item-level result and recovery
step. A URL fetch obeys acquisition policy and network boundaries; it does not provide
arbitrary access to host credentials or private services.

Retain the raw input separately from extracted content, with page/cell/image-region
locators where supported, extraction method/version and uncertainty. Citation records
identify the publisher or submitter, original URL when known, publication/retrieval
times, retained evidence and relevant transformations. Important factual claims link
to this evidence; synthesis and forecasts are labelled as judgments. A submitted image
is evidence of what was received, not independent verification of its apparent publisher.

## Interaction and delivery

Discord and an authenticated host interface expose the same application actions:
status, submit, inspect a case/source, ask a question, inspect paper positions/results,
view limits/usage, and exercise explicitly granted controls. A narrow plugin/MCP
adapter can expose those shared research/status actions without introducing another
portfolio owner. Its tools must not be injected into hardened decision jobs. Recursion
integration uses the separate execution contract when qualified.

Status shows active work, last useful output, coverage/freshness, quota and dependency
health, paper positions, holds and next actions. A run with no eligible candidates
reports the screened population, rejection/missing-evidence reasons and next trigger.
Process completion alone never produces an investing-success claim.

Short updates link to detailed cases. Notify on material evidence changes, paper
lifecycle events, actionable holds, release/strategy review readiness and recovery.
Coalesce repetitive status; a durable delivery queue deduplicates events, records
attempts and retries within the configured policy. Discord loss does not stop permitted
monitoring or become permission to clear a hold. Host status exposes failed delivery.
Monthly expansion summaries include candidate progress, missing evidence and next
steps; unchanged readiness does not generate repeated activation requests.

## Identity, permissions and privacy

One deployment has an owner and explicitly admitted users. Map external identifiers
to stable cockpit principals, scoped to the configured deployment/server/channel.
Display names, membership, a shared API key or a model's identity assertion cannot
establish per-user authority. Enforce permissions in shared application services so
Discord, host and plugin callers cannot obtain different authority for the same action.

The owner defines roles by individual action grants. The following are adopted
starting role templates; assigning them is private deployment configuration.

| Action family | Default authority |
|---|---|
| Read public/research status and permitted cases | Viewer grant scoped to permitted resources |
| Submit evidence, questions and research requests | Researcher grant, within intake and work limits |
| View private account/tax data or economic exports | Separate explicit private-data grant; research access alone is insufficient |
| Pause affected work or request a policy-permitted protective action | Operator grant; the request still passes all applicable controls |
| Release a human/integrity/authority hold | Explicit hold-release grant and successful required checks; ordinary operator access does not imply it |
| Activate/materially revise a strategy | Owner, or explicit owner delegation as selected in PD-020 |
| Install software releases; increase hard caps; manage users/roles and disclosure grants | Owner under the selected release/limits/data policies |

Recheck the current principal and policy generation at consequential action time.
Revocation stops queued or in-flight user requests from acquiring new authority; an
already submitted broker action needs reconciliation and a separately permitted
response, not a claim that credential revocation undid it. Autonomous work is attributed
to its service/job and standing authorization, never to a fabricated human approval.

Audit actor, delegated requester, surface, action, resource, policy/release versions,
approval or standing-policy reference, outcome and refusal reason. Keep actual keys
in the OS credential store and private identity/access bindings out of Git. Use
per-user authentication on the host surface instead of extending the shared-key design.

PD-047 governs financial data at every dispatch boundary. Keep private documents local
by default; only minimum approved, redacted facts may cross to an external provider.
Broader disclosure needs owner-scoped permission. Local extraction/OCR is required
when a provider lacks that permission; unavailable local capability yields a visible
intake limitation rather than an upload. Public research follows approved normal routes.
Protect retained private evidence and backups with deployment access controls and
encryption, including private exports. Disclosure rules also cover logs, Discord
destinations, evaluation fixtures, prompts, handoffs, retries and fallback routes.

## Executor, routing and resource accounting

Keep task roles separate from configured runtime/model aliases. Filter candidate routes
by actual qualified capability, permitted tools/data, schema support and health before
optimizing available quota, cost, latency and diversity. The owner's example escalation
ladder is a starting hypothesis, not a compulsory sequence or an entitlement claim.

A bounded job carries its ID/attempt, input references/revision, task/output schema,
principal and authority scope, allowed disclosure/capabilities, required quality,
deadline and reserved resources. Its receipt binds the route/runtime version, result
or refusal, timestamps, usage observations and cancellation/timeout/unknown status.
Retain original evidence and conflicting claims for higher-tier review; do not pass
only an earlier model's conclusion. Schema validation and repair/quarantine apply.

Reserve resources atomically across concurrent jobs before dispatch. Settle actual
usage once per attempt, retain conservative reservations for unknown completion, and
reconcile before retrying. Count retries, repairs and escalations. Distinguish measured,
estimated and unavailable quota; unknown does not mean free or unlimited. A tested
conservative bound can admit a route with imperfect telemetry; otherwise suspend it.
If a qualified external executor manages a shared pool, both its pool governor and the
cockpit's limits must admit the work without duplicating charges or reservations.

Before operation, require owner-configured aggregate inference ceilings, accounting
windows, concurrency/deadline limits, fallback routes, paper capital/exposure/loss
limits, settlement policy and monitoring reserves. These are mandatory configuration,
not values inferred from $210 subscriptions, $500 funding or the provisional $2,000
paper baseline. Increasing a hard cap needs owner approval. Allocate within approved
limits and preserve enough admitted resources for required monitoring/protection.

## Strategy eligibility and portfolio allocation

Each strategy version declares its evidence population and validation, instruments,
data/freshness needs, supported horizons, signal/exit rules, permitted actions, sizing,
cost/fill assumptions, tax applicability, protective behavior and expiry/review rules.
Changes affecting these behaviors or required evidence are material and need activation
review. Cosmetic metadata changes do not grant a new active strategy version.

The engine selects qualified active strategies, horizons and allocations within those
contracts. Deterministic admission binds symbol/instrument identity, required source
evidence, strategy/policy version and expiry; recheck before exposure changes. Human
intake, a model recommendation or old eligibility alone cannot admit an order.

Use one aggregate portfolio/cash ledger and explicit per-strategy attribution. Concurrent
strategies cannot each spend the same cash or conceal shared issuer/ETF exposure.
Conflicting proposals go through a deterministic arbitration policy with attributable
allocations; do not let last-writer wins select an order. No hidden shorting, margin
borrowing or internally invented broker fills. Instrument eligibility must cover the
actual execution, data and tax capabilities needed for that instrument.

## Failure, recovery and release changes

| Condition | Permitted behavior | Recovery |
|---|---|---|
| Required feed/model unavailable or quota exhausted | Approved qualifying fallback within caps; otherwise suspend affected new/increased exposure, monitor and permit only preapproved protective actions whose own requirements pass | Automatic only after health, freshness, resources and reconciliation pass under approved policy |
| Required evidence/tax condition missing | Watch and explain missing evidence; PD-046 permits explicit paper scenarios but cannot satisfy an unverified required tax restriction | Re-evaluate after the named evidence or validated context arrives |
| Unknown order submission, fill or cancellation result | Mark uncertainty; reconcile broker identifiers and portfolio/cash state before a conflicting or duplicate action | Resume only after the required state is established |
| Explicit human halt or integrity/authority hold | Preserve the durable hold; monitor only as permitted by that hold | Authorized human release plus required checks; dependency health alone cannot clear it |
| Discord unavailable | Preserve state and standing policy; queue eligible notifications and show delivery failures on the host | Reconnect/retry without duplicated actions or clearing holds |

Policy-scoped protective actions must be reconciled with the current global fail-closed
boundary in the paper-autonomy design; this table does not add a bypass to today's
`watch_only` behavior. Existing positions are never blindly liquidated because an
input failed. After restart, reconcile open positions, broker uncertainty, job usage
and durable safety state before admitting affected work. Maintain one active portfolio
scheduler; Windows/Linux support does not imply active-active portfolio replication.

Agents develop isolated revisions and deliver a reviewable release manifest: immutable
artifact/revision, changes, required checks/evidence, affected policies and migrations,
installation plan and rollback or forward-repair plan. Lucas approves installation.
Quiesce and reconcile before changing the running service; preserve audit history and
admit startup only under the approved recovery rules. A schema migration without a
safe rollback needs an explicit forward-repair plan, not a promise that restoring old
code will restore state. Software installation never implicitly activates a strategy,
raises a hard cap, widens disclosure or promotes a live mode.

Under an owner-approved development policy, evaluation failures, incidents and explicit
requests may create bounded improvement jobs. Give those jobs a reproducible case,
acceptance target, authorized repository/workspace, resource allowance and permitted
development capabilities. They can investigate, implement and test isolated revisions
and produce a release candidate or an explained unresolved result. Keep engineering
execution separate from hardened investing-decision jobs and the running portfolio's
credentials/state. Standalone operation must support this bounded improvement loop
without assuming that an unqualified Recursion adapter supplies it. Release readiness
can be notified automatically; installation remains Lucas's decision.

---
title: RW-25 cockpit architecture comparison
status: reference
last-verified: 2026-09-09
---

# Cockpit architecture comparison

**Disposition:** Lucas selected arrangement C under PD-028 and a standalone MVP with
Recursion when qualified under PD-029. Technical contract and acceptance details below
remain proposals. These product decisions do not establish implementation readiness.
Owner decisions remain in [product-planning](product-planning.md); the candidate
[product contract](../PRODUCT.md) retains its working-draft status.

## Selected architecture direction

Keep a persistent cockpit application responsible for the investing workflow. Its
research, strategy, portfolio, audit and policy state survive a Discord disconnect or
the loss of a model executor. Discord and host/plugin/MCP integrations access the same
application actions. Recursion can execute bounded agent jobs through an explicit
adapter; it does not become a second owner of the paper portfolio or investing policy.

Support the service on Windows and Linux, with Lucas's Linux box as the primary
deployment. A service here means a persistent application with defined ownership;
it does not require multiple microservices or a new distributed system. Start from
the existing cockpit composition and extract interfaces where needed.

## What was inspected

Cockpit source baseline: `dd58017` on `work/rw25-product-grill` (runtime inherited from
`db354a2`). These observations are source inspection, not a new runtime test or a
measurement of the deployed host.

| Source | Observed behavior | Architectural implication |
|---|---|---|
| [main.compose](../app/main.py) | Builds shared state, workflow services, paper service, ingest and API; injects external seams for real-composition tests | Extend one application graph and verify its production entry points |
| [main.run / build_operator_bot](../app/main.py) | Starts schedules, feeds and optional Discord alongside the API; Discord and API share the paper service | Discord is already an access surface, not the sole owner of the process |
| [AgentSpec](../app/agent_runner/spec.py), [run_agent](../app/agent_runner/runner.py), [transport](../app/agent_runner/transport.py) | Job specification, hardened argv inspection, schema validation and a subprocess-shaped transport | A future generic executor needs an explicit contract; renaming the subprocess transport does not establish Recursion compatibility |
| [API auth](../app/orchestrator/auth.py), [secret wrapper](../app/core/secrets.py) | Shared API credential and OS-keyring secret lookup | New surfaces need attributed per-action authorization; use credential references across interfaces |
| [run shutdown](../app/main.py) | Discord currently lacks a stop/join contract; shutdown retains an open safety marker when producers cannot be proven stopped | Cross-platform service/recovery acceptance still needs implementation work |

Recursion was inspected read-only at the locally available tracking snapshot
`origin/master` = `f197669`. Its checkout `master` at `98113d6` is older; using that
checkout alone would miss the v3 design. No fresh remote or deployment inspection is
claimed. Reproduce the document inspection from that repository with
`git show f197669:<path>` for these paths:

- `docs/v3/PRODUCT.md`: accepted direction includes native/upstream execution,
  common routing/accounting, plugins, durable state and Windows/Linux operation.
- `docs/v3/CURRENT-AND-MIGRATION.md`: distinguishes the older operational-path
  source, test-only composition and v3 proposals; live deployment state is unknown.
- `docs/v3/ROUTE-CAPABILITIES.md`: records bounded installed-interface fixtures and
  explicitly withholds live account/route conformance claims.
- `docs/v3/M1B-EXECUTION-CONTRACTS.md`: defines route/plugin/process contracts and
  deterministic fixture entry points. Those are useful design inputs, not a verified
  production cockpit job service.

The direction fits cockpit's integration needs. Actual cockpit↔Recursion job dispatch,
credential isolation, quota attribution, cancellation and recovery remain unverified.

## Three arrangements

| Concern | A. Cockpit plus its own complete agent platform | B. Host-owned cockpit plugin | C. Persistent cockpit plus integrations — selected |
|---|---|---|---|
| Investing state and schedules | Cockpit owns both | Host runs plugin lifecycle; durable domain-state ownership must be specified | Cockpit owns both, independent of attached frontends |
| Model jobs | Cockpit develops general routing, sessions, delegation and supervision | Host owns execution services | Local bounded executor or qualified Recursion adapter |
| Discord / host access | Cockpit implements access | Depends on host lifecycle and plugin facilities | Adapters call the same application actions |
| Failure and restart | Cockpit owns domain and full agent-platform recovery | Host and plugin must coordinate domain recovery | Cockpit reconciles investing work; executor reports job state and uncertainty |
| Credentials and permissions | Cockpit provisions both domains | Host/plugin privilege separation must be proven | Broker credentials stay cockpit-side; executor uses only its admitted inference credentials |
| Windows and Linux release | Full stack supported on both | Host and plugin must qualify on both | Cockpit and selected executor paths qualify on both |
| Main tradeoff | Maximum local control; duplicates general Recursion work | Strong reuse; availability and release depend on the host | More explicit adapter work; separates investing lifecycle from replaceable execution |

## Proposed ownership and contract

Cockpit owns source acquisition and provenance, research-case transitions, strategy
activation state, paper capital and risk policy, the portfolio/order lifecycle, domain
schedules, and the investing audit. The existing deterministic order boundary remains
the only broker route. The paper-autonomy/model-authority redesign is a separate scoped
implementation dependency; selecting arrangement C does not perform it.

The executor owns execution attempts, supported runtime/model routes, process cleanup
and execution receipts. Cockpit requests a bounded job using input/source references,
an output schema, permitted capabilities, route requirements, a deadline and a resource
reservation. Results bind to the job/attempt and input revision and distinguish success,
refusal, timeout, cancellation and unknown completion. They carry usage and capability
evidence; missing usage is not zero. Cockpit revalidates outputs before domain use.

Cockpit's owner-approved limits constrain each delegated reservation. If Recursion also
governs a shared subscription pool, the effective allowance must satisfy both governors.
Reservations and usage receipts must avoid double spending or counting the same work
twice. Exact reconciliation, stale-result and retry rules require a technical contract.

Discord, CLI/supervisor and plugin/MCP adapters bind the external caller to a cockpit
principal and request the same authorized application actions. Research access does not
grant strategy activation, cap changes or broker tools. An agent-facing MCP interface is
separate from the hardened decision job, whose currently permitted toolset stays pinned.

There is one active owner of a portfolio's scheduled domain work. A host may supervise
the cockpit process and execute jobs without separately firing the same investing
schedules. Multi-host workers or independent instances do not imply active-active
portfolio replicas; replication is not selected by dual-platform support.

## What must be demonstrated

These are proposed acceptance obligations to refine in the release contract:

1. One representative input reaches retained evidence, a cited research case, a
   strategy decision, eligible paper execution and reconciliation through the actual
   application. Ineligible and insufficient-evidence cases give useful explanations.
2. Deterministic replays exercise that production composition with controlled external
   leaves; real adapter and unattended deployment trials separately establish the
   external behavior. Passing a fake executor does not certify a Recursion adapter.
3. Identity, policy, quota and cancellation constraints hold across every selected
   access/execution surface. Reject late, duplicate or stale results without repeated
   orders or unaccounted model work.
4. Installation, OS credential access, start/stop/restart, recovery and diagnostics work
   on Windows and Linux; the primary Linux box runs an observed unattended paper trial.
   Trial duration and objective thresholds remain to be set. DEC-09 remains required.
5. With Discord disconnected, monitoring and allowed work follow standing policy; with
   an executor unavailable, the selected fallback/suspension policy applies. Reconcile
   open positions and uncertain attempts before resuming affected actions.

## Owner disposition

**Q20 — PD-028:** “Persistent cockpit + integrations (Recommended)”. Arrangement C
is selected: cockpit owns the investing application; integrations use shared services.

**Q21 — PD-029:** “Standalone MVP; Recursion when qualified (Recommended)”. A working
standalone cockpit is required for first release. Define the common job contract now;
Recursion integration follows real conformance and does not block the MVP. The standalone
executor must deliver the accepted product capabilities. This disposition does not
ratify every proposed envelope field or certify an untested adapter.

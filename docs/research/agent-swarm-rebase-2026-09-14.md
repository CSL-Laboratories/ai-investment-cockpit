---
title: Cursor Cloud research and development process rebase
status: reference
last-verified: 2026-09-14
amends:
  - research/agent-swarm-orchestration-2026-09-10
  - research/agent-swarm-cloud-host-2026-09-11
  - research/agent-swarm-pipeline-2026-09-11
---

# Cursor Cloud process rebase — RW-33

Owner direction: finish the process rebase locally, then hand off research,
development and testing against the refined product goals to Cursor Cloud with
its installed CLIs. “Kernel” is shorthand for those goals, not a new product,
parallel runtime or permission to rewrite the cockpit.

PRODUCT and its adopted contracts own scope and acceptance; PLAN owns delivery
order; REGISTER owns claims; CLAUDE owns the nine invariants. This process
amendment changes development orchestration only. It grants no empirical PASS,
strategy activation, paper/live order authority, soak or production deployment.

## Current runtime policy

| Role | Current policy | Required before dispatch |
|---|---|---|
| Head | Cursor Cloud Agent; retains the configured Grok preference where available | Inspect actual Cloud capabilities and account/model availability; never silently substitute a model |
| Research, claimed development, first-gate review | Cursor-native fresh sessions preferred | Bounded brief, admitted resources, approved inputs/tools, fresh attempt, receipt |
| Codex CLI | Named for constrained research/development off the order path | Exact-host profile and capability qualification; production decision jobs keep the existing hardened runner |
| OpenCode CLI | Named by this process amendment for bounded research/development/review off the order path | Separate executable/profile conformance before use; no automatic permission bypass |
| Second-gate synthesis | Muse preference when it supplies a different model family | Validate filings and pin producer/reviewer/synthesizer family identity from qualified route receipts |
| OpenRouter / Hive | Fallback synthesis only; not research or first-gate review | Public-approved inputs and enforceable remaining budget: ceilings $5 / $50 |
| ChatGPT | Manual owner steering only | Discussion is not a filed synthesis, witness or gate |
| Devin / SWE-2 | Retired; no new cockpit jobs | No route; legacy helper frozen for historical audit |
| Chiron / Recursion | Future qualified executors under PD-029 | Real shared-contract conformance in a separate package; no routing or cloning into their trees |

Naming a runtime does not qualify its implementation. Installation does not prove
model access, subscription coverage, confinement, cancellation or budget enforcement.
No CLI route has been qualified on the Cloud host by RW-33.

The old 30–40-agent SWE-2 allowance does not transfer to replacement runtimes.
Cursor Ultra is the intended subscription route, not evidence of unlimited included
capacity. Provider names are not model families: switching provider alone establishes
no independent review. Muse cannot supply the family break for Muse-produced work.
Model-family separation is a review control, not independent ground truth or a
qualified financial evaluator.

## Shape and boundaries

Head → domain middle → leaf, maximum depth three. A middle may dispatch at most
eight leaves within the admitted aggregate allowance. Leaves never spawn; each
attempt is a fresh session and never bulk-inherits Head context. Head consumes at
most five bounded domain syntheses, preserves dissent, and spot-checks at least one
cited filing per domain. Start with one qualification fixture before increasing
fan-out; no inherited 30–40 concurrency target.

The host is the Cursor Cloud Agent Linux VM, not sentry or a broker/soak host.
The swarm must not touch risk, gateway, mode/approval, audit, eligibility or secrets;
must not acquire broker credentials or start a market/paper soak. Authority-adjacent
implementation requires its own claim and adversarial review. The nine invariants
and production runner remain unchanged. The native-Windows + Ubuntu/WSL2 × Python
3.11/3.12 DEC-09 matrix remains the release gate.

## Tooling delivered here

`spawn_scoped_leaf.py` keeps its historical draft filename but is now an **offline
route preview only**. It has no subprocess implementation. Omitting `--dry-run`
returns a refusal before input reads, output writes or CLI discovery. A dry-run
records the requested model and brief digest, explicitly reports availability
unknown and dispatch disabled, and refuses an existing output or escaped path.
It does not claim to validate reservations, disclosure or runtime capabilities.

This replaces unsafe guessed commands: the original Cursor argv omitted its model
and used flags absent from local Agent 2026.08.25-3e8eec8 help; OpenCode could launch
while unnamed; output streams overwrote prior attempts. The draft Codex flags also
omitted the feature-denial set in the real runner. No guessed frozen argv remains.
Use the Cloud handoff to implement and qualify one adapter as its own work package.
Do not re-enable execution by adding an override flag.

`validate_filings.py` admits bounded final JSON objects, rejects duplicate keys and
attempts, binds hashes to the same bytes it validated, and refuses all empirical
PASS claims. A bad input produces no new manifest; an existing manifest is never
overwritten. Synthesis must name exactly the admitted leaf IDs and declared source
families, with a distinct synthesizer family. These checks establish internal
consistency of declared metadata, not proof that a model ran. A qualified controller
must bind route identity to actual execution receipts before live use. Filings and
manifests remain planning evidence, never audit events.

Current schemas require `route_family` on leaf, review and synthesis; synthesis also
requires `attempt` and `source_families`. Historical archived filings retain their
original schemas and evidence; they are not silently relabelled as current filings.
Raw CLI JSONL/events are not filing JSON. A future adapter must retain bounded raw
transport separately, extract a final object, validate it, and quarantine failures.

## Brief and reservation contract

Use [ping-template](swarm-briefs/ping-template.md). The example is illustrative,
not a ready dispatch. Every real attempt binds job/claim/attempt, role, runtime
and model/family, exact input refs/hashes, policy revision, allowed reads/writes/
acquisitions, disclosure, deadline, output schema and reservation.

- A fresh checked-in brief is the prompt. No resume, continue, bulk inherit,
  `--auto`, force, trust bypass, automatic MCP approval or secret-bearing flags.
- `public-tracked` means explicitly approved for the named provider; it does not
  mean every tracked file in this private repository is public.
- `redacted-facts` are prepared locally before dispatch. `local-only` content
  never enters a Cloud or external-model session; dispatch a refusal stub instead.
- No secrets in inputs, arguments, output or diagnostics. Approved repository
  research material and account authentication are separate from broker secrets.
- Admission must atomically reserve aggregate resources, enforce deadlines and
  cancellation, settle once per attempt, and retain conservative holds for unknown
  completion. Missing usage is unavailable, not zero.
- $5 OpenRouter / $50 Hive are ceilings, not per-attempt allowances or permission
  to replenish/reset spending. Briefs name the budget scope, remaining balance,
  reservation and enforcement mechanism. Until these exist, fallback dispatch is
  blocked. A prompt saying “stop at cap” is not enforcement.
- The controller enforces path/capability boundaries and performs deterministic
  canary scanning before synthesis. Wrap cited filings as untrusted data. A model
  must not reinterpret a filing as instructions or invent a quarantined result.
- Filings live in gitignored `.swarm-runs/<run_id>/`, with exclusive attempt paths.
  A receipt gives path, SHA-256, job/attempt, dissent, coverage, unknowns and observed
  usage class. Export evidence before VM teardown; loss remains unknown.

## Research/development/testing target

The next Cloud task follows [the handoff](cursor-cloud-rdt-handoff-2026-09-14.md).
It inventories installed routes, qualifies one bounded adapter, then resumes the
adopted delivery slices with a claim and testable useful outcome. Empty screens
must explain rejection/unknown causes and the next evidence trigger. They do not
satisfy the goals merely because the process exited successfully.

[RW-33 evidence](../evidence/rw33-swarm-rebase-2026-09-14.md) records local checks.
Runtime qualification, paid dispatch and product empirical gates are separate.

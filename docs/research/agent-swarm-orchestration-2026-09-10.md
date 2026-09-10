---
title: Agent-swarm orchestration for vNext research (2-step)
status: reference
last-verified: 2026-09-10
---

# Agent-swarm orchestration for vNext research — 2-step proposal

**Baseline:** `main` at `e4e9844` (PR #85 merged, RW-25 ratified under PD-048).
**Scope:** research-planning reference only. It does not change delivery order
(PLAN), product acceptance (PRODUCT), runtime authority (AGENTS/CLAUDE), or any
empirical gate. It grants no new model authority, spending, deployment, or live
permission. It claims no PASS.

## 1. PRODUCT grounding

- PRODUCT owns an autonomous standalone research-to-paper product: persistent
  cockpit owns evidence, cases, strategy, portfolio, schedules, policy, audit.
  Discord/host/plugin/MCP are access surfaces on the same application actions.
  No surface becomes a second portfolio owner; no model gets broker tools
  (`PRODUCT.md`, `docs/product-architecture-options.md`).
- The MVP works standalone. The bounded executor contract is defined now;
  Recursion integrates only when real conformance qualifies it. Treat any Devin
  swarm the same way: unqualified external executor, never a release dependency.
- Delivery slices A–F (`docs/product-delivery.md`) already locate where a swarm
  helps: A (bounded feasibility + evaluator qualification), B (useful research
  through real composition), E (tax reference work), F (replay coverage). C/D
  authority work (standing approval, eligibility, order path) stays separately
  scoped + adversarially reviewed before any implementation change.
- Operating contracts (`docs/product-operating-contracts.md`) already define the
  job shape to reuse: bounded job (ID/attempt, input refs/revision, output
  schema, principal/authority scope, disclosure/capability allowlist, deadline,
  reservation) → receipt (route/version, result/refusal, timestamps, usage,
  cancel/timeout/unknown). Do not invent a second job envelope.

## 2. Assessment of the Devin proposal

Proposed: Codex (or another harness) as orchestrator; per-task brief/prompt
files; spawn via non-interactive CLI flags; each leaf writes `.json`; separate
synthesis agents roll up the filings.

What aligns:

- Briefs in git matches "prompts live in git; fail soak/replay on dirty tree".
- One JSON filing per leaf matches Inv 3 (schema-validate every model output)
  and Inv 8 (raw inputs separate from summaries) — if schemas are frozen first
  and repair-then-quarantine applies.
- Synthesis-as-separate-job matches "retain original evidence for higher-tier
  review; do not pass only an earlier model's conclusion".
- Non-interactive spawn matches unattended operation under standing policy —
  provided caps, deadlines, cancellation, and usage receipts hold.

Gaps to close before scaling past a pilot:

1. No capability/disclosure scoping stated. PD-047: private docs stay local;
   only minimum approved redacted facts cross to an external provider. A swarm
   brief must carry an explicit disclosure class, never raw secrets/keys,
   private tax/account docs, or credential references.
2. No reservation/accounting. Operating contracts require atomic reservation
   across concurrent jobs, once-per-attempt settlement, conservative holds for
   unknown completion, and measured/estimated/unavailable quota distinction.
   "Unlimited concurrency, no rate limit" is a vendor claim, not a cockpit
   allowance — the cockpit's own aggregate inference/paper caps still govern.
3. No claim/dedup. REGISTER owns the claim board ("claim work here before
   writing code"); unchanged inputs must not restart unbounded investigation.
   Thirty leaves without claims repeats the "same five defects fixed twice"
   failure the register exists to prevent.
4. Single-head fan-out + single synthesis pass bottlenecks at 30-way and lets
   the synthesizer silently reinterpret leaves. Hence §3.

## 3. Recommended shape: Head → Sub-orchestrators → Swarm

Keep OpenCode/Codex as the planning/head layer; use domain sub-orchestrators
as the fan-out/synthesis layer; use headless Devin (`devin.exe -p` /
`--prompt-file`) as leaves. Two steps, not one flat fan-out.

```text
Head Orchestrator (human + OpenCode/Codex planning)
  owns briefs, schemas, REGISTER claims, frozen manifests, stop/merge decision
  │
  ├─ Sub-orchestrator: Research/evidence (slice B)
  ├─ Sub-orchestrator: Strategy feasibility + evaluation (slice A)
  ├─ Sub-orchestrator: Executor/routing/recovery contracts (slice C — contract
  │   text only; implementation solely via separately claimed C/D packages with
  │   adversarial review, never by a research leaf)
  ├─ Sub-orchestrator: Tax/economics references (slice E)
  └─ Sub-orchestrator: Replay/release-qualification scaffolding (slice F)
       │  each owns: dispatch ≤N leaves, retry/dedupe, per-domain synthesis
       └─ Leaves: bounded headless agents, one brief → one JSON filing
```

Rules:

- Head owns what middles may not reinterpret: brief text, output schema
  version, input/policy revision pins, disclosure class, deadline, resource
  reservation, acceptance target. Middles schedule, retry, dedupe, and
  synthesize — they do not rewrite the question.
- One brief → one claim → one JSON filing. Leaf work is idempotent: same
  brief + same input rev yields same filing path; reruns overwrite nothing,
  they add a new attempt revision.
- Synthesis is a separate job with its own brief/schema, reading frozen leaf
  revisions. It cites leaf IDs, preserves dissent, and never fabricates a
  human approval or a measured gate.
- Domain fan-out maps to delivery slices so C/D authority boundaries are never
  crossed by a research leaf. Any brief touching `app/risk/`, `OrderGateway`,
  mode/approval, credentials, or eligibility policy is rejected at Head review
  unless it is a separately claimed C/D package with adversarial review.

Why 2-step beats flat at ~30-way: head prompt/narrative stays small; each
middle holds ≤8 leaves plus its own synthesis window; failures/quota retries
are contained per domain; duplicate briefs are visible at the middle before
they burn quota; the final merge reads ≤5 domain syntheses, not 30 raw filings.

## 4. Non-negotiable guardrails (from CLAUDE/AGENTS)

1. Single order path (Inv 1): risk-pass → mode → approval → `OrderGateway`
   only. Swarm produces research/JSON; it never submits, approves, or bypasses.
2. Decision path pinned to Claude in auto modes (Inv 2): Codex/Devin/other
   runtimes stay out of the decision/order path. Swarm may do
   briefing/triage/grounding/proposal-drafting shapes only, schema-gated.
3. Schema-validate every model output (Inv 3): repair-then-quarantine. A leaf
   JSON that fails its schema is quarantined, never "fixed" by the synthesizer
   inventing values.
4. Secrets only in OS credential store (Inv 4): never in repo, briefs, prompts,
   chat, logs, or leaf JSON. `core/secrets.py` stays platform-branch-free.
5. `risk/` pure deterministic (Inv 5): no LLM, I/O, subprocess. No model
   runtime or MCP server inside `app/risk/`. Enforced by CI gate.
6. Client-side human gate + hash-chained audit (Inv 6): autonomous swarm work
   cites standing authorization; it never fabricates contemporaneous approval.
   Leaf filings are planning evidence, not audit events — runtime state changes
   still go through the signed audit chain only.
7. Fail closed (Inv 7): vendor-limit/stale/hung → `watch_only`. Missing usage
   is not zero; unknown completion keeps its conservative reservation.
8. Raw separate from summaries (Inv 8): leaf filings cite retained input
   hashes/revisions; synthesis links, never pastes, private content.
9. Post-PDT risk model (Inv 9) + paper-first: no live capital until P0 pre-live
   gates (chiefly SEC-11) pass. Swarm success never implies live authority.
10. Private-data default (PD-047): local extraction/OCR unless owner grants
    broader scoped access for a document/provider. Refuse visibly when the only
    route lacks disclosure permission — do not upload anyway.
11. One package, one claim, adversarial review on the order path. Numeric caps
    are configured before autonomous operation; $210 subs / $2,000 paper are
    context, not authorization.

Codex decision-job profile reminder (`app/agent_runner/runner.py`): prompt over
stdin, read-only sandbox, ephemeral session, ignored user config/rules,
shell/web/MCP disabled, argv inspected before spawn. Any Devin leaf handling
cockpit material gets an equivalently explicit profile in its brief
(capabilities allowlist, disclosure class, deadline, no-broker-tools) — an
implicit "agent can do anything" default is a refusal reason. Explicit note:
AGENTS.md's boundary text names Codex and Claude only; Devin as a leaf runtime
is an extension, not a covered party. The pilot below does not amend that
boundary — broader Devin use needs a scoped, reviewed boundary amendment first.

## 5. Brief / JSON / synthesis pattern (reuse, don't reinvent)

Brief file (checked in, e.g. `docs/research/swarm-briefs/<slice>-<nn>.md`):

- `job_id`, delivery-slice + REGISTER claim, input refs/revisions (hashes),
  policy/schema versions, disclosure class, capability allowlist, deadline,
  reservation, acceptance target, stop conditions, forbidden actions.

Leaf JSON filing (one per attempt, immutable path):

- `job_id`, REGISTER `claim_id`, `attempt`, `brief_rev`, `input_rev`,
  `policy_rev`, `route/model`, `started/finished`,
  `status (success/refusal/timeout/cancelled/unknown)`,
  `result` (schema-conformant) or `refusal_reason`, `usage_observed`,
  `evidence_refs`, `cost_attribution`. Missing usage stays `unknown`.
  Filings under `docs/evidence/` are planning evidence; only runtime state
  changes via `storage/audit.py` enter the hash-chained audit — a leaf filing
  never substitutes for an audit event.

Synthesis brief: frozen leaf-revision manifest in, domain rollup JSON out with
per-leaf citations, dissent section, coverage/unverified remainder, and an
explicit "no empirical PASS claimed" footer. Head merge reads domain rollups
only, then records the decision in `product-planning.md` style (owner answer
separate from inference).

## 6. Safe pilot shape (requires owner go-ahead; this doc alone authorizes nothing)

- Pilot is read-only review of this proposal itself: 1–2 headless leaves with
  a read-only/no-edit profile (`--permission-mode auto` auto-approves
  read-only tools only, plus an explicit NO-EDITS instruction — any edit
  attempt is a refusal), briefs + schemas in git, JSON filings under
  `docs/evidence/`, no code edits, no secrets, no private docs, no broker
  touch, no push without owner sign-off. Planning labels here are not
  permission to run a pilot (per `docs/product-delivery.md`).
- Expand to ≤8 leaves in one domain (slice A) only after the pilot filings
  validate against their schemas and the synthesis preserves dissent.
- Thirty-way concurrent writing, any `app/` code edit by the swarm, any C/D
  authority change, and any external upload beyond redacted facts each need
  their own explicit owner go-ahead and REGISTER claim first.

## 7. Permission tiers — expand reads, keep writes gated

Short answer: broaden leaf *reads* inside containment, keep *writes and
authority* with the mains/Head. A sandbox is not a permission: it stops writes
but still permits reads/egress by default — `app/agent_runner/tool_inspector.py:71`
states this explicitly, which is why the cockpit pairs
`codex exec --sandbox read-only` (`app/agent_runner/runner.py:99`) with
deny-by-default argv inspection (SEC-06, hardened by BUG-119). Every swarm tier
needs both halves: containment plus an explicit capability/disclosure grant in
its brief.

Note on "Sentry's sandbox": a repo-wide search finds zero Sentry references —
no such sandbox exists in this tree. The available containment is
harness-native plus the SEC-05 OS account:

| Tier | Role | Read | Write |
|---|---|---|---|
| L0 Head | human + OpenCode/Codex planning | full via normal surfaces | merges, REGISTER claims, releases — owner approval only |
| L1 Mains (sub-orchestrators) | dispatch ≤8 leaves, dedupe, per-domain synthesis | tracked repo + brief inputs | own domain-synthesis JSON only; never `app/risk/`, gateway/order FSM, mode/approval, `storage/audit.py`, secrets, or eligibility policy |
| L2 Leaves | one brief → one JSON filing | tracked repo files (secret-free by Inv 4) + explicitly listed inputs only — never credential stores, private docs, or network beyond the brief's acquisition allowlist | own filing path only; NO EDITS elsewhere (pilot profile: `--permission-mode auto` + explicit NO-EDITS; any edit attempt is a refusal) |

L0-write-only parts: `app/risk/`, `brokers/gateway.py` + order state machine,
`orchestrator/mode_ladder.py`, `storage/audit.py`, secrets/identity config,
eligibility policy. On "direct access to everything it needs": tracked files
plus named inputs *are* everything research needs — credentials and private
documents are deliberately not needed, per PD-047's minimum-necessary rule.
Briefs carry a disclosure class: `public-tracked` (default leaf),
`redacted-facts` (owner-scoped minimum), `local-only` (never leaves the host;
leaves receive a refusal stub, not the content).

Containment matrix (verified 2026-09-10; laptop probes plus read-only `ssh sentry`
inspection — no remote state changed, running pilot untouched):

- Codex: `--sandbox read-only` on all OSes, plus argv inspection
  (`tool_inspector.py`: deny duplicates/aliases/unknown flags).
- Devin `--sandbox`: Research Preview, macOS seatbelt / Linux bwrap+seccomp
  only. On native Windows it refuses ("requires running Devin inside WSL 2").
  Native-Windows leaves therefore use `--permission-mode auto` (read-only tools
  auto-approved) + NO-EDITS brief + the SEC-05 low-priv `cockpit-agent` account
  (`docs/setup/low-priv-agent-account.md`, enforced at startup by
  `account_guard.py`), ideally in a separate worktree. For full Devin
  sandboxing, run the leaf from the WSL2 Ubuntu distribution.
- OpenCode: `opencode run --agent <name> -m <provider/model> --format json
  --dir <worktree>`. Treat `--auto` as dangerous (auto-approves anything not
  explicitly denied) — never set it on leaves or middles.

Swarm host: `sentry` (Remote Desktop, `Lucas@sentry:22`, key auth). Verified
read-only 2026-09-10: Windows host, WSL2 Ubuntu-24.04 installed (currently
Stopped), `devin.exe` + `opencode` + `codex` installed, and an active Codex /
opencode pilot running (codex since 9/9, opencode sessions 9/10 — do not touch
those PIDs; that work is rooted outside any cockpit checkout, and sentry has no
`ai-investment-cockpit` checkout yet). Long-running swarm work belongs on
sentry rather than the laptop: always-on, Remote-Desktop-checkable, no
laptop-uptime dependency. Placement and preconditions:

- Clone the cockpit to a NEW sentry directory (never into the pilot's
  `Chiron`/`Chiron-brain` trees); prefer the WSL2 Ubuntu side
  (`~/ai-investment-cockpit`) for leaf execution so Devin `--sandbox`
  (bwrap+seccomp) actually applies — native-Windows leaves cannot use it.
- Starting the stopped Ubuntu-24.04 distro is a state change: owner starts it
  or explicitly authorizes it; restore with `wsl --shutdown` when done if it
  was stopped before.
- Provision the SEC-05 low-priv account on sentry first
  (`docs/setup/low-priv-agent-account.md` is written exactly for a Windows
  host profile) so `account_guard.py` passes before any leaf runs there.
- Head may stay thin on the laptop (briefs + merge via `ssh sentry`) or move
  to sentry outright; prefer Head-on-sentry so a closed laptop lid cannot stall
  the swarm. Monitoring is `ssh sentry` (`devin list`, session `--export`
  files, leaf JSON under the sentry checkout) plus Remote Desktop as needed.

## 8. Agentic depth — verified, capped at 3

Depth is nested headless CLI re-invocation (each level its own OS process and
session), not a harness-native "spawn subagent" primitive:

| Level | Runs as | May spawn children? | Evidence |
|---|---|---|---|
| Head | human + `opencode` session / Codex planning | yes, via shell | `opencode run --agent --model --format json --dir` flags verified in `--help` |
| Middle | headless `devin -p` / `opencode run` with domain brief | yes, via orchestrator shell only | Devin CLI supports `-p` / `--prompt-file` / `--model` / `--export` / `-r` / `--resume` |
| Leaf | headless single-shot with task brief | NO — no spawn authority; a leaf attempting to fan out is a refusal | depth probe 2026-09-10: leaf under `auto` executed bounded `echo depth-probe-ok` via exec and returned JSON — exec works, so the no-spawn rule lives in the brief text, enforced by review, not by machinery |

Rules: maximum depth is Head → middle → leaf (3). No leaf-spawned
grandchildren — each extra hop multiplies prompt-telephone drift and unaccounted
quota fan-out. Every hop re-pins `brief_rev`/`input_rev`/`policy_rev` with its
REGISTER `claim_id`, and settles usage once per attempt. CLI note: pass
headless Devin prompts after `--` (`devin … -p -- "…"`) — the bare
`-p "…"` form misparses against `[PATH]...` on some inputs (observed
2026-09-10).

## 9. Doc disposition

New reference research input. It supersedes nothing; PLAN still owns order,
PRODUCT still owns scope/acceptance, REGISTER still owns claims. A future
delivery slice may cite it; it cannot be cited as permission, evidence, or a
passed gate.

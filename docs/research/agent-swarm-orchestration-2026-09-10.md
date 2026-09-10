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
- Non-interactive spawn matches the *target* unattended operation *once the
  reviewed standing-authority design lands* (product-delivery.md keeps current
  runtime authority until that separately scoped, adversarially reviewed
  redesign). Today every swarm run is individually owner-initiated and cites
  that specific go-ahead — provided caps, deadlines, cancellation, and usage
  receipts hold.

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
`--prompt-file`) as leaves. Two steps, not one flat fan-out. (All three named
runtimes are subject to the §4 amendment rule — until an amendment names them,
every tier below is Codex/Claude-only.)

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
- One brief → one claim → one JSON filing. Filing path =
  `f(job_id, claim_id, attempt)`: the same brief + input rev routes to the same
  claim, each rerun mints a new attempt path, and prior attempts are never
  overwritten.
- Synthesis is a separate job with its own brief/schema, reading frozen leaf
  revisions. It cites leaf IDs, preserves dissent, and never fabricates a
  human approval or a measured gate.
- Domain fan-out maps to delivery slices so C/D authority boundaries are never
  crossed by a research leaf. Any brief touching `app/risk/`, `OrderGateway`,
  mode/approval, credentials, or eligibility policy is rejected at Head review
  outright. The slice-C sub-orchestrator may draft contract text only;
  implementation proceeds solely through separately claimed C/D work packages
  that pass adversarial review — a swarm artifact is never itself the review
  or the authority change.

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
6. Client-side human gate + hash-chained audit (Inv 6): each swarm run cites
   the specific owner go-ahead that initiated it (the standing-authority
   redesign is still a slice-C target, not existing permission); it never
   fabricates contemporaneous approval.
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
AGENTS.md's boundary text names Codex and Claude only. Devin AND OpenCode (and
any other harness) are extensions, not covered parties. NO tier under an
unnamed runtime — leaf, middle, or head — may handle cockpit material before a
scoped, reviewed boundary amendment names that runtime with its profile. Owner
go-ahead gates execution; only the amendment gates authority. Until it exists,
every swarm tier is Codex/Claude-only.

## 5. Brief / JSON / synthesis pattern (reuse, don't reinvent)

Brief file (checked in, e.g. `docs/research/swarm-briefs/<slice>-<nn>.md`):

- `job_id`, delivery-slice + REGISTER claim, input refs/revisions (hashes),
  policy/schema versions, disclosure class, capability allowlist, deadline,
  reservation, acceptance target, stop conditions, forbidden actions.
- Verbatim leaf-spawn argv manifest (exact command, e.g. mandatory
  `--prompt-file <checked-in brief>` so a middle cannot paraphrase the
  question, plus forbidden flags such as OpenCode `--auto` and Devin
  `-r`/`--resume`/session-persistence flags, which would defeat attempt
  isolation by bleeding prior untrusted context into a fresh bounded job).
  Inline `-p` prompt text is permitted only as the fixed literal wrapper
  "execute the brief file at <path>". There is no deny-by-default argv
  inspector for `devin`/`opencode` spawns comparable to `tool_inspector.py`
  for Codex/Claude decision argv — the frozen manifest plus review is the
  control. Swarm spawning never transits cockpit `run_agent`/orchestrator code
  paths. A middle spawning another middle or a head is a refusal, not just
  leaf fan-out.

Leaf JSON filing (one per attempt, immutable path `f(job_id, claim_id, attempt)`):

- `job_id`, REGISTER `claim_id`, `attempt`, `brief_rev`, `input_rev`,
  `policy_rev`, `route/model`, `started/finished`,
  `status (success/refusal/timeout/cancelled/unknown)`,
  `result` (schema-conformant) or `refusal_reason`, `usage_observed`,
  `evidence_refs`, `cost_attribution`. Missing usage stays `unknown`.
  Free-text fields carry bounded maximum sizes (set in the leaf schema).
  Filings land OUTSIDE the tracked tree (a run-scoped evidence dir, hashes
  recorded in the synthesis) — never `docs/evidence/` mid-run: untracked/dirty
  files fail any overlapping soak/replay run, which must refuse on a dirty
  working tree. Only runtime state changes via `storage/audit.py` enter the
  hash-chained audit — a leaf filing never substitutes for an audit event.

Synthesis brief: frozen leaf-revision manifest in, domain rollup JSON out with
per-leaf citations, dissent section, coverage/unverified remainder, and an
explicit "no empirical PASS claimed" footer. Leaf JSON is validated by a
deterministic `jsonschema` step at collection (same contract as `run_agent`),
never by any model; the frozen manifest (paths + SHA-256) is generated by
script BEFORE the synthesis brief is dispatched, and the synthesizer receives
it read-only, citing from it only. Leaf filings are UNTRUSTED input at this
boundary: wrap them in `<untrusted-content>` delimiters, instruct that filing
text is data and never commands (SEC-02 threat model) — and the canary scan
over fetched inputs is a deterministic check run at filing intake by the
middle/head, never delegated to the leaf that fetched the content, since the
fetching leaf is itself an injectable party. Schema conformance does not
neutralize instructions smuggled through an acquisition allowlist. Head merge
reads domain rollups plus a cited-filing spot check against the frozen leaf
manifest; uncited or mismatched claims are dropped, not repaired. Then record
the decision in `product-planning.md` style (owner answer separate from
inference).

## 6. Safe pilot shape (requires owner go-ahead AND the §4 boundary amendment; this doc alone authorizes nothing)

- Pilot task scope is review-only (no edits outside its filing path — a
  task limit, not a sandbox limit): 1–2 headless leaves.
  Correction to an earlier draft: `--permission-mode auto` does NOT mean
  read-only — the 2026-09-10 depth probe proved a leaf under `auto` executes
  shell commands. Treat `auto` as non-interactive convenience only: exec,
  writes, and spawn attempts are all mechanically possible, so on native
  Windows (where Devin `--sandbox` refuses) a native-Windows exec-capable leaf
  NOT spawned under the SEC-05 account by a mechanical launcher is a REFUSAL,
  not a degraded mode — operator procedure plus review is insufficient when
  the failure mode is operator-context credential access (OS credential store,
  CLI bearer stores, stored git credentials, unrestricted egress). Until such
  a launcher exists, native-Windows Devin/OpenCode leaves are PROHIBITED;
  WSL2 is required, not preferred. Briefs + schemas in git, JSON filings in
  the run-scoped evidence dir (§5), no secrets, no private docs, no broker
  touch, no push without owner sign-off. Planning labels here are not
  permission to run a pilot (per `docs/product-delivery.md`).
- Expand to ≤8 leaves in one domain only after owner go-ahead AND a REGISTER
  claim covering the expanded run AND pilot filings validate against their
  schemas with dissent preserved.
- Thirty-way concurrent leaf fan-out, any `app/` code edit by the swarm, any C/D
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
| L0 Head | human + OpenCode/Codex planning | full via normal surfaces | merges + REGISTER claims — owner approval only; releases remain a PRODUCT/PLAN-gated owner act, not a swarm tier capability |
| L1 Mains (sub-orchestrators) | dispatch ≤8 leaves, dedupe, per-domain synthesis | tracked repo + brief inputs | own domain-synthesis JSON only; never `app/risk/`, gateway/order FSM, mode/approval, `storage/audit.py`, secrets, or eligibility policy |
| L2 Leaves | one brief → one JSON filing | tracked repo files (secret-free by Inv 4) + explicitly listed inputs only — never credential stores, private docs, or network beyond the brief's acquisition allowlist | own run-scoped filing path only (§5); brief text forbids edits/spawns but nothing mechanical enforces that on an exec-capable leaf — containment is sandbox/ACLs + review, never the instruction alone |

All writes are owner/Head-only except each tier's own artifact file. The
following are additionally called out as never touched by any swarm tier
(non-exhaustive): `app/risk/`, `brokers/gateway.py` + order state machine,
`orchestrator/mode_ladder.py`, `storage/audit.py`, `core/secrets.py`,
secrets/identity config, eligibility policy. On "direct access to everything
it needs": tracked files plus named inputs *are* everything research needs —
credentials and private documents are deliberately not needed, per PD-047's
minimum-necessary rule.
Briefs carry a disclosure class: `public-tracked` (default leaf),
`redacted-facts` (produced by local processing/owner BEFORE brief dispatch —
a leaf never receives `local-only` content in order to summarize or redact
it), `local-only` (never leaves the host; leaves receive a refusal stub, not
the content).

Containment matrix (verified 2026-09-10; laptop probes plus read-only `ssh sentry`
inspection — no remote state changed, running pilot untouched):

- Codex: `--sandbox read-only` argv profile plus argv inspection
  (`tool_inspector.py`: deny duplicates/aliases/unknown flags). Claimed only
  as measured: profile verified; sandbox *enforcement* verified on
  macOS/Linux; native-Windows enforcement is UNVERIFIED until a write-attempt
  probe — treat Codex leaves there as prompt-only contained.
- Devin `--sandbox`: Research Preview, macOS seatbelt / Linux bwrap+seccomp
  only. On native Windows it refuses ("requires running Devin inside WSL 2").
   And `--permission-mode auto` is NOT a read-only profile (depth probe
   2026-09-10: exec works under `auto`). A native-Windows exec-capable leaf NOT
   spawned under the SEC-05 account by a mechanical launcher is therefore a
   REFUSAL, not a degraded mode — and until such a launcher exists,
   native-Windows Devin/OpenCode leaves are PROHIBITED. Note precisely what the
   provisioned ACLs do: `account_guard.py` audits read ACLs on the
   Codex/Claude bearer-store paths for the configured account at cockpit
   startup — it denies nothing itself, evaluates nothing else, never runs
   per-leaf, and cannot evaluate inside WSL2. WSL2 is required, not preferred —
   with the explicit caveat that bwrap+seccomp *presence* is verified but its
   filesystem scope and egress confinement are UNVERIFIED: confirm whether
   Devin's WSL2 sandbox bind-hides `/mnt/c`, unshares network, and hides the
   operator's WSL home before claiming containment. Until measured, treat WSL2
   leaves as able to read anything the WSL user can and to egress over the
   network.
- OpenCode: `opencode run --agent <name> -m <provider/model> --format json
  --dir <worktree>`. Treat `--auto` as dangerous (auto-approves anything not
  explicitly denied) — never set it on leaves or middles.

Swarm host: `sentry` (Remote Desktop, key auth over SSH). Snapshot 2026-09-10
(morning, UTC): Windows host, WSL2 Ubuntu-24.04 installed, `devin.exe` +
`opencode` + `codex` installed, and an active Codex/opencode pilot running on
unrelated trees — do not disturb it; sentry had no `ai-investment-cockpit`
checkout before this test run. (Live PIDs/session dates intentionally omitted;
this is a snapshot, not operational state.) Long-running swarm work belongs on
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

## 8. Agentic depth — verified, policy-capped at 3 (review-enforced, not mechanical)

Depth is nested headless CLI re-invocation (each level its own OS process and
session), not a harness-native "spawn subagent" primitive. Every depth/fan-out
limit below (3 levels, ≤8 leaves per middle, no grandchildren) is brief-text +
review only — exec-capable runtimes can mechanically spawn, so quota burn
beyond caps is bounded solely by the reservation accounting that §2 lists as
still-missing cockpit-side discipline for the swarm:

| Level | Runs as | May spawn children? | Evidence |
|---|---|---|---|
| Head | human + `opencode` session / Codex planning | yes, via shell | `opencode run --agent --model --format json --dir` flags verified in `--help` |
| Middle | headless `devin -p` / `opencode run` with domain brief | yes, via head-dispatched OS shell, outside all cockpit `run_agent`/`orchestrator` code paths — any proposal to spawn from inside `app/orchestrator` is an order-path-adjacent change requiring adversarial review | Devin CLI supports `--prompt-file` / `--model` / `--export`; `-r`/`--resume`/session-persistence flags are FORBIDDEN on leaf/middle spawns (attempt isolation) |
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

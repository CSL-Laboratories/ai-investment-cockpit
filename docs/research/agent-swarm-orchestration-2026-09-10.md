---
title: Agent-swarm orchestration for vNext research (2-step)
status: reference
last-verified: 2026-09-11
---

# Agent-swarm orchestration for vNext research — 2-step proposal

**Baseline:** `main` at `e4e9844` (PR #85 merged, RW-25 ratified under PD-048).
**Host/runtime amendment:** 2026-09-11 owner direction in
[`agent-swarm-cloud-host-2026-09-11.md`](agent-swarm-cloud-host-2026-09-11.md)
moves the swarm off `sentry` onto the Cursor Cloud Agent Linux VM, names Devin
for research/dev/adversarial-review, and keeps market/live testing with Lucas.
**Scope:** 2-step research-planning reference. It does not change delivery
order (PLAN), product acceptance (PRODUCT), or any empirical gate. The
2026-09-11 AGENTS/CLAUDE boundary names Devin **off** the order path only. It
grants no live permission and claims no PASS.

## 1. PRODUCT grounding

- PRODUCT owns an autonomous standalone research-to-paper product: persistent
  cockpit owns evidence, cases, strategy, portfolio, schedules, policy, audit.
  Discord/host/plugin/MCP are access surfaces on the same application actions.
  No surface becomes a second portfolio owner; no model gets broker tools
  (`PRODUCT.md`, `docs/product-architecture-options.md`).
- The MVP works standalone. The bounded executor contract is defined now;
  Recursion integrates only when real conformance qualifies it. Treat the
  Devin swarm as a **development-process** executor (research, claimed-package
  implementation, adversarial review on Cursor Cloud), never a PRODUCT release
  dependency and never a second portfolio owner.
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

Keep the Cursor Cloud Agent as the planning/head layer; use domain
sub-orchestrators as the fan-out/synthesis layer; use Devin CLI (`devin -p` /
`--prompt-file`, or Devin-native SWE-2 subagents under the same brief
contract) as leaves. Two steps, not one flat fan-out. The 2026-09-11
amendment names Devin for this Cloud-hosted research/dev/review swarm.
OpenCode remains unnamed: do not put an OpenCode `--auto` leaf on cockpit
material.

```text
Head Orchestrator (human + Cursor Cloud Agent)
  owns briefs, schemas, REGISTER claims, frozen manifests, stop/PR decision
  │
  ├─ Sub-orchestrator: Research/evidence (slice B)
  ├─ Sub-orchestrator: Strategy feasibility + evaluation (slice A)
  ├─ Sub-orchestrator: Executor/routing/recovery contracts (slice C — contract
  │   text only; implementation solely via separately claimed C/D packages with
  │   adversarial review, never by a research leaf)
  ├─ Sub-orchestrator: Tax/economics references (slice E)
  └─ Sub-orchestrator: Replay/release-qualification scaffolding (slice F —
       scaffolding/replay fixtures only; not the 20 market days)
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
shell/web/MCP disabled, argv inspected before spawn. The 2026-09-11
amendment names Devin as a Cloud-hosted research/dev/review runtime with the
spawn profile in
[`agent-swarm-cloud-host-2026-09-11.md`](agent-swarm-cloud-host-2026-09-11.md)
(Linux `--sandbox`, `--prompt-file`, no resume flags, no broker tools). An
implicit "agent can do anything" default remains a refusal. OpenCode and any
other unnamed harness are still extensions, not covered parties. Inv 2 is
unchanged: Claude stays pinned on the decision/order path in auto modes;
Devin never joins that path.

## 5. Brief / JSON / synthesis pattern (reuse, don't reinvent)

Brief file (checked in, e.g. `docs/research/swarm-briefs/<slice>-<nn>.md`):

- `job_id`, delivery-slice + REGISTER claim, input refs/revisions (hashes),
  policy/schema versions, disclosure class, capability allowlist, deadline,
  reservation, acceptance target, stop conditions, forbidden actions.
- Verbatim leaf-spawn argv manifest (exact command; `--prompt-file
  <checked-in brief>` is mandatory so a middle cannot paraphrase the
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
  working tree. On Cursor Cloud the evidence dir dies with the pod unless the
  Head exports it before teardown (branch/PR/artifact); lost-with-the-VM
  filings are `unknown` completion, not zero usage. Only runtime state
  changes via `storage/audit.py` enter the hash-chained audit — a leaf
  filing never substitutes for an audit event.

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

## 6. Safe pilot shape, then Cloud scale (2026-09-11)

The 2026-09-11 owner direction is the go-ahead to host on Cursor Cloud, name
Devin, and scale toward 30–40 concurrent research/dev/review agents. This
section is not permission to skip a REGISTER claim, merge, or touch the
order path.

- First measured Cloud run stays small: 1–2 sandboxed Devin leaves that
  write only their filing path, to prove auth, `--sandbox`, schema
  validation, and evidence export before pod teardown.
- `--permission-mode auto` does NOT mean read-only — the 2026-09-10 depth
  probe proved a leaf under `auto` executes shell commands. Treat `auto` as
  non-interactive convenience only. Unattended Cloud leaves use `--sandbox`
  (and `autonomous` only together with it). `dangerous` / `bypass` / `yolo`
  remain forbidden.
- Native-Windows Devin/OpenCode leaves remain PROHIBITED: `--sandbox`
  refuses there, and no SEC-05 mechanical launcher exists. The Cloud host
  is Linux, so WSL2 is not required on that VM; `bwrap`+`socat` presence
  was measured 2026-09-11. Do not fall back to sentry or a Windows box to
  "get 30 agents."
- After the 1–2 leaf smoke, scale on Cloud toward 30–40 Devin agents using
  the 2-step shape (≤8 leaves per domain middle). Prefer Devin-native
  SWE-2 subagents; add extra Cloud Agent Heads if one 4-vCPU pod saturates.
  Each domain expansion still needs a REGISTER claim. Briefs + schemas in
  git; JSON filings in the run-scoped evidence dir (§5); no secrets; no
  private docs; no broker touch; no merge without owner sign-off.
- Slice C/D authority implementation, any never-touch-list edit, and any
  external upload beyond redacted facts each still need their own explicit
  owner go-ahead and REGISTER claim. Market/live/paper soaks stay with
  Lucas (Grok Bot or another service) — the swarm does not run them.

## 7. Permission tiers — expand reads, keep writes gated

Short answer: broaden leaf *reads* inside containment, keep *writes and
authority* with the mains/Head. A sandbox is not a permission: it stops writes
but still permits reads/egress by default — `app/agent_runner/tool_inspector.py:71`
states this explicitly, which is why the cockpit pairs
`codex exec --sandbox read-only` (`app/agent_runner/runner.py:99`) with
deny-by-default argv inspection (SEC-06, hardened by BUG-119). Every swarm tier
needs both halves: containment plus an explicit capability/disclosure grant in
its brief.

Note on "Sentry's sandbox": a repo-wide search finds zero Sentry *product*
sandbox references — no such sandbox exists in this tree. The available
containment is harness-native (`--sandbox`) on the Cloud Linux VM:

| Tier | Role | Read | Write |
|---|---|---|---|
| L0 Head | human + Cursor Cloud Agent | full via normal Cloud surfaces | PRs + REGISTER claims — owner approval only for merge/release (PD-030); market/live soaks are owner-operated, not a Head capability |
| L1 Mains (sub-orchestrators) | dispatch ≤8 Devin leaves, dedupe, per-domain synthesis | tracked repo + brief inputs | domain-synthesis JSON plus claimed-package files in that domain; never `app/risk/`, gateway/order FSM, mode/approval, `storage/audit.py`, secrets, or eligibility policy |
| L2 Leaves | one brief → one JSON filing (research) or claimed-file edits listed in the brief (dev/review) | tracked repo files (secret-free by Inv 4) + explicitly listed inputs only — never credential stores, private docs, or network beyond the brief's acquisition allowlist | filing path and/or brief-listed claimed paths; brief text forbids spawns and never-touch edits but nothing mechanical enforces that on an exec-capable leaf — containment is `--sandbox` + review, never the instruction alone |

Claimed-package writes are allowed under the 2026-09-11 development
direction; they are still one claim per package. The following remain
never touched by any swarm tier (non-exhaustive): `app/risk/`,
`brokers/gateway.py` + order state machine, `orchestrator/mode_ladder.py`,
`storage/audit.py`, `core/secrets.py`, secrets/identity config, eligibility
policy. Credentials and private documents are deliberately not needed, per
PD-047's minimum-necessary rule.

Briefs carry a disclosure class: `public-tracked` (default leaf),
`redacted-facts` (produced by local processing/owner BEFORE brief dispatch —
a leaf never receives `local-only` content in order to summarize or redact
it), `local-only` (never leaves the host; leaves receive a refusal stub, not
the content).

Containment matrix (Cloud host verified 2026-09-11; 2026-09-10 laptop/sentry
probes retained as historical Windows/WSL evidence):

- Codex: `--sandbox read-only` argv profile plus argv inspection
  (`tool_inspector.py`: deny duplicates/aliases/unknown flags). Claimed only
  as measured: profile verified; sandbox *enforcement* verified on
  macOS/Linux; native-Windows enforcement is UNVERIFIED until a write-attempt
  probe — treat Codex leaves there as prompt-only contained. Codex remains
  the cockpit `run_agent` decision-job runtime; it is not the Cloud swarm
  leaf.
- Devin `--sandbox`: Research Preview, macOS seatbelt / Linux bwrap+seccomp
  only. On native Windows it refuses ("requires running Devin inside WSL 2")
  — native-Windows Devin/OpenCode leaves stay PROHIBITED. On the Cursor Cloud
  Linux VM (2026-09-11): `bwrap` 0.9.0 + `socat` installed, unprivileged user
  namespaces succeed, `bwrap` smoke passed. Fail closed if those are missing.
  `--permission-mode auto` is still NOT a read-only profile. Filesystem scope
  and egress confinement of Devin's bwrap remain UNVERIFIED beyond presence;
  briefs must still assume the leaf can read what the Cloud user can and
  egress over the network, which is why disclosure class and no-broker-tools
  are mandatory. `account_guard.py` is not a leaf control on Cloud — it never
  runs per-leaf.
- OpenCode: still unnamed. `opencode run --agent <name> -m <provider/model>
  --format json --dir <worktree>` stays out of cockpit material. Treat
  `--auto` as dangerous — never set it on leaves or middles.

Swarm host: **Cursor Cloud Agent Linux VM** (owner direction 2026-09-11).
Not `sentry`, not the laptop. Head, middles, and Devin leaves share that VM.
Target concurrency is 30–40 Devin agents on owner-stated SWE-2 credits;
inference credits do not prove 40 OS processes fit on one 4-vCPU pod —
prefer Devin-native subagents, then extra Cloud Agent Heads. See
[`agent-swarm-cloud-host-2026-09-11.md`](agent-swarm-cloud-host-2026-09-11.md).

Historical 2026-09-10 sentry snapshot (not the swarm host; do not start this
distro for swarm work): Windows host, WSL2 Ubuntu-24.04 installed, `devin.exe`
+ `opencode` + `codex` present, and an active Codex/opencode pilot on
unrelated trees — do not disturb it. Distro registration detail kept for
operators who still use that box for other work: manually-imported layout,
VHDX at
`C:\Users\Lucas\AppData\Local\wsl\{452bd7e9-ff46-478d-8aca-e29631bc6793}\ext4.vhdx`
(~13 GB at snapshot); WSL 2.7.3 supports in-place
`wsl --manage Ubuntu-24.04 --move <NTFS path>`. D: is a second healthy Samsung
990 PRO 2TB, NTFS, ~1.86 TB free. That move procedure is unrelated to the
Cloud swarm host.

Cloud placement and preconditions:

- Work in the Cloud Agent checkout of `ai-investment-cockpit`. Do not clone
  into sentry `Chiron`/`Chiron-brain` trees and do not use sentry as a
  fallback fan-out host.
- Devin `--sandbox` (bwrap+seccomp) must apply; install `bubblewrap` and
  `socat` if the environment image does not already provide them.
- Persist Devin auth with a Cursor environment secret or a per-run PKCE
  login. Never snapshot `credentials.toml`. Export run-scoped filings before
  pod teardown.
- Head stays on the Cloud Agent so a closed laptop lid cannot stall the
  swarm. Monitoring is the Cloud Agent thread (`devin list`, `--export`
  files, leaf JSON under the run-scoped evidence dir, then PR/artifact
  export).
- Market/live/paper soaks and the DEC-09 operator matrix stay with Lucas
  (Grok Bot or another service). The swarm does not start them.

## 8. Agentic depth — policy-capped at 3 (review-enforced, not mechanical)

Depth is Head → middle → leaf. On Cloud this may be (a) nested OS-process
`devin -p` re-invocation or (b) Devin-native subagents with `max-nesting` ≤ 3.
Both are the same depth policy. Every depth/fan-out limit below (3 levels,
≤8 leaves per middle, no grandchildren, 30–40 concurrent Devin agents) is
brief-text + review plus reservation accounting — exec-capable runtimes can
mechanically spawn, so quota burn beyond caps is bounded solely by the
reservation accounting that §2 lists as still-missing cockpit-side
discipline:

| Level | Runs as | May spawn children? | Evidence |
|---|---|---|---|
| Head | human + Cursor Cloud Agent (named 2026-09-11) | yes, via shell / Devin dispatch | Cloud Agent tools + `devin -p` / `--prompt-file` / Devin subagents |
| Middle | Devin domain brief (named 2026-09-11) | yes, via Head-dispatched Devin leaves or Devin-native subagents, outside all cockpit `run_agent`/`orchestrator` code paths — any proposal to spawn from inside `app/orchestrator` is an order-path-adjacent change requiring adversarial review | `--prompt-file` / `--model` / `--export` / Devin subagent profiles; `-r`/`--resume`/session-persistence flags are FORBIDDEN on leaf/middle spawns (attempt isolation) |
| Leaf | Devin task brief, SWE-2 preferred for research/explore (named 2026-09-11) | NO — no spawn authority; a leaf attempting to fan out is a refusal | 2026-09-10 depth probe: leaf under `auto` executed bounded `echo depth-probe-ok` via exec and returned JSON — exec works, so the no-spawn rule lives in the brief text, enforced by review, not by machinery |

Rules: maximum depth is Head → middle → leaf (3). No leaf-spawned
grandchildren — each extra hop multiplies prompt-telephone drift and unaccounted
quota fan-out. Every hop re-pins `brief_rev`/`input_rev`/`policy_rev` with its
REGISTER `claim_id`, and settles usage once per attempt. CLI note: pass
headless Devin prompts after `--` (`devin … -p -- "…"`, subject to the §5
wrapper-only rule for inline `-p`) — the bare
`-p "…"` form misparses against `[PATH]...` on some inputs (observed
2026-09-10).

## 9. Doc disposition

2-step reference, amended 2026-09-11 for host/runtime/testing split.
[`agent-swarm-cloud-host-2026-09-11.md`](agent-swarm-cloud-host-2026-09-11.md)
owns that amendment. PLAN still owns delivery order, PRODUCT still owns
scope/acceptance, REGISTER still owns claims. Cite these as process
references; they cannot be cited as a passed gate, a live unlock, or a
waiver of a REGISTER claim.

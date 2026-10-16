# Codex operating guide — AI Investment Cockpit

> **How CSL works:** start with the Linear doc [How CSL works](https://linear.app/csl-laboratories/document/how-csl-works-922e0df6026f).

## Linear-first operations (applies to every agent and Grok Bot)

Linear is the basis of operations. Bounded workers with a frozen brief (for
example `muse-synthesizer`) are exempt from the Linear and external-research
steps below; Head performs them before freezing the brief, and the worker's own
prompt governs. Hosted Claude (`devin-review-fix`, `resolve-pr`) is exempt
from the Linear preflight and status steps below: it has no Linear access and
works from the GitHub event only (see **When Claude can't fix a review thread**).

- Before starting work, check Linear for overlapping issues and PRs.
- Work only from a Linear issue. Keep it updated with status, progress comments,
  and PR links. Agents and bots with Linear access post their own status
  updates and PR links as comments on that issue. Linear PR-sync writes
  generic PR status, not hosted-Claude milestones; the stuck-thread watcher
  files a roll-up only for stuck threads. When a hosted-Claude blocker or
  milestone needs a person, an agent with Linear access (usually the PR
  author) posts that @mention on the CSL issue.
- Every review request, blocker, or question directed at a person must be an
  @mention comment on the matching CSL Linear issue so the Linear bot notifies
  them. A GitHub mention alone is not enough. Automated GitHub workflows keep
  their existing channels and do not also @mention on Linear: the Codex gate
  posts `@codex review` on the PR, Linear PR-sync writes PR status without an
  extra @mention, and Devin Review posts findings on GitHub. Linear handles:
  Chaandan `@chaandansaini101`, Samir `@samirshahahmed`, Lucas
  `@lucassalvador987`.
- Autonomously file research findings, ideas, and bugs to Triage, deduped.
- Ground every implementation in the best available research: Parallel search
  for web research and Context7 for current library docs.
- **Wait for jobs before reviewing:** no human or coding agent approves or
  requests changes on a PR until Devin Review, the Claude fixer jobs
  (`devin-review-fix`, `resolve-pr`), and required CI have all finished on the
  current head commit. Devin Review's own verdict is exempt: its
  `changes_requested` is what starts `devin-review-fix`. The Codex CI/fixer
  gate (CSL-103) still posts its one `@codex review` request on
  `open`/`synchronize` without waiting for those jobs; that request is not an
  approve or request-changes, and `READY` is reconciled against live findings
  after jobs settle (`ci:full` requests CI, not merge approval). A review on a
  stale head does not count; after new pushes, the reviewer reviews again.
  Human and agent review requests and their status are posted as @mention
  comments on the CSL Linear issue. Check job status on the PR directly; do
  not wait on a readiness notice, since no watcher that posts one is part of
  this repo.

This project is a solo-developer, subscription-powered, human-gated investing
copilot. It is paper-first: no live capital moves until the pre-live gates in
`docs/security-hardening-backlog.md` are satisfied.

The ratified [PRODUCT.md](PRODUCT.md) owns the autonomous paper-product target and
acceptance; [docs/PLAN.md](docs/PLAN.md) owns delivery order. The current human-gated
implementation and the boundaries below remain authoritative until their separately
scoped, reviewed implementation amendments. Product ratification grants no runtime
permission or empirical PASS.

## Codex boundary

- Codex and Claude may perform schema-gated analysis jobs (briefing, triage,
  grounding, proposal drafting, and fresh interrogation) in `read_only` and
  human-approved `paper` mode.
- Do not put either model runtime or an MCP server inside `app/risk/`, give a
  model broker credentials/tools, or add a direct route from a model to
  `OrderGateway`.
- Codex decision jobs use the hardened profile in `app/agent_runner/runner.py`:
  prompt over standard input, read-only sandbox, ephemeral session, ignored
  user config/rules, and shell/web/apps/browser/computer/MCP capabilities
  disabled. The actual argv is inspected before spawn.
- `review_live`, `auto_veto`, and `auto_full` remain outside the Codex decision
  boundary. Do not relax that pin without a separately scoped, adversarially
  reviewed safety redesign and the pre-live gates.

## Cursor Cloud research/development boundary (amended 2026-09-14)

RW-33 replaces the retired Devin/SWE-2 dispatch policy. Head is the Cursor
Cloud Agent on its Linux VM; Cursor-native fresh sessions are preferred for
research, REGISTER-claimed development and adversarial review. Codex and
OpenCode are named for these bounded development roles off the order path.
Naming and installation alone do not qualify a CLI or grant production authority.

- No new Devin jobs. Keep `spawn_devin_leaf.py` frozen for historical audit.
- No swarm changes to risk, OrderGateway, mode/approval, audit, eligibility or
  secrets; no broker tools/credentials, soaks or auto rungs. Current production
  Codex decision jobs still use the unchanged hardened runner above.
- Maximum depth: Head → middle → leaf; at most eight leaves per middle within
  an admitted aggregate budget. Fresh attempts; no bulk inherit or leaf spawning.
- Verify model/version/capabilities on the actual Cloud host. Unknown profile,
  missing reservation, deadline or disclosure admission refuses dispatch. No
  auto/force/trust/permission bypass or automatic MCP approval. The scoped helper
  is preview-only and cannot launch any CLI until a separately claimed adapter
  is implemented and qualified; do not add an override to skip qualification.
- Muse is the preferred second-gate synthesizer only when a verified different
  family from producers/reviewers. Preserve dissent. OpenRouter/Hive are fallback
  synthesis only, under enforceable aggregate ceilings of $5/$50; no automatic
  resets. ChatGPT is manual steering only. Chiron/Recursion remain future PD-029
  integrations, not a prerequisite or a current routing destination.
- Head prepares reviewable changes and draft PR material. Existing operator
  commit/publication instructions apply; Lucas remains the merger. Swarm tests
  and receipts never satisfy empirical, paper-day or pre-live gates.

Current process: `docs/research/agent-swarm-rebase-2026-09-14.md`.
Cloud start: `docs/research/cursor-cloud-rdt-handoff-2026-09-14.md`.
Market/paper soaks and DEC-09 operator cells stay on their designated hosts.

## Invariants

**`CLAUDE.md` holds the canonical list of 9 invariants — read it there.** This file
used to restate a shortened, differently-numbered set of 6, so "Inv 4" in a code
comment resolved to *secrets* against one file and *`risk/` purity* against the
other. Code comments here cite invariants constantly, so one numbering is the only
workable arrangement. The summary below is a pointer, not a second source:

Orders only via risk → mode → approval → `OrderGateway` (Inv 1); the decision path
is pinned to Claude in auto modes (Inv 2); every model output is schema-validated
(Inv 3); secrets live only in the OS credential store (Inv 4); `app/risk/` is pure
and deterministic (Inv 5); the human gate is client-side and hash-chain audited
(Inv 6); vendor limit / stale feed / hung orchestrator fails closed to `watch_only`
(Inv 7); raw inputs are stored separately from summaries (Inv 8); post-PDT risk
model (Inv 9).

Note on Inv 4: the store is the **OS credential store** — Windows Credential
Manager, macOS Keychain, or Linux SecretService. `core/secrets.py` calls the
generic `keyring` API with no platform branch, so it is already portable; only the
wording was Windows-only. Do not add a platform branch to satisfy the old phrasing.
On headless Linux there is no SecretService by default: the test suite installs an
in-memory backend (`app/tests/conftest.py`), but a real Ubuntu deployment needs one
provisioned.

## Working conventions

1. Read `docs/README.md` and the relevant plan before editing.
2. Before proposing architecture or direction, read `docs/adr/` and the linked
   decision issues; do not re-argue a recorded decision without new evidence.
3. Keep changes to one work package and add tests with the change.
4. Use the project virtual environment. Create it once with `python -m venv .venv`
   from the repo root — note the `py` launcher is not present on every dev box —
   then install the full runtime and validation dependency set CI installs (see
   `.github/workflows/ci.yml` and the command in `CLAUDE.md`), including `discord.py`.
   Every matrix cell must run the real offline Discord command-tree tests; the absent
   dependency fallback is covered separately in an isolated subprocess.
   Run the gates from `app/`:
   - Windows: `..\.venv\Scripts\python.exe -m pytest -q` and `..\.venv\Scripts\ruff.exe check .`
   - Ubuntu / WSL2: `../.venv/bin/python -m pytest -q` and `../.venv/bin/ruff check .`

   On Windows, point `TMP`/`TEMP` at a SHORT path (e.g. `C:\t`) first: pytest's
   nested tmp dirs plus this repo's deep fixture paths cross MAX_PATH and produce
   ~80 spurious `WinError 206` failures that are not real defects. Also clear any
   ambient `COCKPIT_*` vars — real operator config in the shell fails at least one
   test that asserts an unconfigured default.
5. Keep the repository private and commit only when the operator asks.
6. Any order-path change needs an adversarial-review round.
7. The release-blocking local matrix is **native Windows plus Ubuntu under WSL2**, each on
   Python 3.11 and 3.12. Run all four cells on the operator's Windows workstation. Hosted
   Linux (`linux-gates`) is a required PR diagnostic and does not replace that matrix;
   hosted Windows/macOS are not ordinary-PR checks; Hetzner is not a release gate. macOS is a
   best-effort portability target, not a release gate. Continue to write platform-neutral
   code: review subprocess spawn, path construction, credential lookup, and temp-file use,
   and prefer inspecting the failure (as `orchestrator/halt.py` does) over branching on
   `os.name`. See `DEC-09` in `docs/REGISTER.md`.

## Web lookups

Prefer the project `parallel-search` MCP server (`web_search` / `web_fetch`) for web lookups.
It is declared in `.cursor/mcp.json` (Cursor) and `.mcp.json` (Claude Code); no API key is required.
Search/Extract is for lookups. Assignee decision/direction work also needs the Task API — see Linear-first.

## Engineering stack (Oct 2026)

- **Parallel Search MCP** — web search and fetch at https://search.parallel.ai/mcp (no key).
- **Context7 MCP** — up-to-date library docs at https://mcp.context7.com/mcp (works without a key).
- **Linear MCP** — issues, comments, and status at https://mcp.linear.app/mcp (or the Devin Linear integration).
- **Linear** — source of truth for work. Workspace **CSL laboratories**, team key **CSL**, project **ai-investment-cockpit**.
- **GitHub** — org **CSL-Laboratories**.
- **Devin** — the only PR reviewer, review only (on PR open; on-demand via `/devin review`).
- **Claude Code Action** — PR fixer for Devin findings and `@claude` mentions on open, same-repository PRs whose author (and, for `@claude`, the commenter) has write, maintain or admin permission on the repo (other PRs are skipped by the workflow's event gate) (`.github/workflows/claude.yml`).
- **Blacksmith** — paid test-matrix CI (Python linux/windows, rust-book). Non-test jobs use GitHub-hosted `ubuntu-latest` (this repo is public).
- **Google Workspace** (`csl-laboratories.com`) — meetings and notes.
- **Discord bot** (`csl-bot-operations`) — slash commands and MCP below.

## Discord bot (`csl-bot-operations`)

Liaison's stable slash commands — there is no top-level `/review`, and `/ci` always takes an argument:

- `/issue`, `/plan`, `/debug`, `/feature`, `/standup`
- `/ci full|run`
- `/devin review pr:<url>|run|status`
- `/telemetry`, `/help`, `/connect`

MCP: https://csl-discord-bot-production.up.railway.app/mcp (declared in `.cursor/mcp.json` and `.mcp.json`). Each dev mints a bearer token via `/connect`. Never commit the token; set `CSL_DISCORD_BOT_MCP_TOKEN`. Cursor interpolates `${env:CSL_DISCORD_BOT_MCP_TOKEN}`; Claude Code interpolates `${CSL_DISCORD_BOT_MCP_TOKEN}`. Tools: `issue_create`, `issue_list`, `issue_status`, `issue_assign`, `plan`, `debug`, `feature`, `standup`, `ci_full`, `ci_run`, `devin_review`, `devin_run`, `devin_status`, `telemetry`.

Agents trigger CI and reviews via these commands/tools or the `ci:full` label.

## Linear-first workflow

Agents must stay Linear-aware and proactive. Linear is the source of truth
(team **CSL**, project **ai-investment-cockpit**).

- **Claim = assign yourself** in Linear before starting, and move the issue to
  **In Progress**. Unassign if you stop. Find or create the issue via the Linear
  MCP first. Do not start a change without a `CSL-N`. Then record the work ID
  and branch in `docs/REGISTER.md`; the register is the ID/status board, Linear
  assignment is the claim.
- **When you are the assignee** (developer or their coding agent working in the repo) of an issue that needs a decision or sets direction, run Parallel Task API deep research before proposing or deciding, in addition to Search/Extract. Post the cited brief (options, tradeoffs, recommendation, target metrics) on the Linear issue, then decide or @mention for approval. Grok Bots do not research or resolve decision issues.
- **Branch** from Linear's copied branch name, or `CSL-<n>-slug`. **PR title**
  carries `CSL-N: ...`. Put `Fixes CSL-N` in the PR body — that is Linear's
  magic word. Replace the `Fixes CSL-___` placeholder with the real key; a
  branch name alone can link the PR but does not move the issue to Done. One
  issue per PR; keep PRs small and stackable.
- **Statuses:** when you start, assign yourself and move the issue to
  **In Progress**. `Fixes CSL-N` (real key) is what Linear uses to close on
  merge. If the PR does not move the issue to **In Review**, move it yourself.
  Agents working a CSL issue keep comments current at other milestones — see
  **Linear is how we communicate**.
- Grok Bots must read the Linear PR-sync comment (`<!-- pr-sync:<repo>#<n> -->`, written by [`.github/workflows/linear-pr-sync.yml`](.github/workflows/linear-pr-sync.yml)) on the issue before touching shared files. Three people work this repo at once; that comment is the live PR link, mergeable/conflict state, head SHA, and top files.
- **Routing labels** `for-devin`, `for-claude`, and `for-cursor` are the
  single-select route group. `needs-decision` is a separate label. Humans
  apply `for-devin` (agents must not trigger Devin automations). File
  follow-ups to **Triage** with one Type label, the repo label
  `ai-investment-cockpit`, the project, and a link back to the parent issue
  or PR — not drive-by changes.
- **Mentions** use the Linear handles in **Linear-first operations**, not
  GitHub. Look up the parent issue's current assignee or project lead in Linear
  and @mention them; the named handles are notification routes, not proof of
  ownership. Assign new issues to that owner, or leave them in Triage
  unassigned when unclear.
- The author addresses CI comments on their own PR, then resolves the threads.
  Devin findings and `@claude` mentions are handled by the Claude Code Action
  (`.github/workflows/claude.yml`) on open, same-repository PRs by repo writers;
  on other PRs the author addresses them. Do not run review-fix loops on other
  agents' PRs.
- Humans make the load-bearing decisions (architecture, security, trading-risk,
  merge order). Agents propose; humans decide.

## Linear is how we communicate

Linear is the team's primary async channel, alongside Google Meet and Discord.

- Agents with Linear access working a CSL issue keep it current in Linear as
  work progresses: status, a short progress comment at meaningful milestones
  (started, blocked, PR opened, done), and the PR link, posted as a comment
  on that issue. Hosted Claude is exempt (GitHub-only; see **When Claude
  can't fix a review thread**). Operator soak runs are not told to post
  Linear milestone comments.
- Every review request, blocker, or question directed at a person is an
  @mention comment on the matching CSL Linear issue (Chaandan
  `@chaandansaini101`, Samir `@samirshahahmed`, Lucas `@lucassalvador987`).
  A GitHub mention alone is not enough. Automated GitHub workflows keep
  their existing channels (see **Linear-first operations**). Add the
  `needs-decision` label for approvals.
- Anything that comes up during a dev session gets filed as a new Linear issue
  in **Triage** instead of being dropped: ideas discussed, bugs or oddities
  found in development, and areas to develop or test further. Link it to the
  current issue when related, and dedupe against existing issues first.

## Hosted CI

Hosted split (PR #102):

- Required check: **`linux-gates`**. The aggregator is GitHub-hosted `ubuntu-latest`. The Python 3.11+3.12 matrix it wraps runs on Blacksmith. Ordinary label add/remove does not start `ci.yml`, so it cannot post a skipped or successful `linux-gates`. Drafts skip the test matrix; the aggregator still runs and fails closed until `ready_for_review`. `ci:full` / `enhancement` re-trigger via `ci-label-retrigger.yml`, which has no job named `linux-gates`.
- Enhancement-only label reruns request Linux scope; `ci:full` or enhancement on an already-`ci:full` PR requests the full matrix. Manual/API dispatch defaults to full; explicit `scope=linux` requests Linux only.
- Label reruns carry the PR number, expected head, and immutable merge candidate. They validate that context before testing the merge candidate and share the ordinary PR concurrency group for that head. Different PR heads are isolated so a stale label dispatch cannot cancel a newer head before validation. A stale dispatched head fails closed; manual branch dispatch without PR context remains available.
- Windows runs on push to `main`, the `ci:full` label, and full manual dispatch (Blacksmith).
- macOS runs nightly at 07:17 UTC on `main` (skipped if idle 24h), on the `ci:full` label, and on full manual dispatch — not on ordinary `main` pushes (GitHub-hosted).
- Non-test jobs (filter, `linux-gates` aggregator, pr-lint, Claude fixer/conflict-scan) use GitHub-hosted `ubuntu-latest`.
- Superseded runs for the same PR head are cancelled. Draft synchronize uses a separate concurrency group so its fail-closed aggregator cannot cancel a `ready_for_review` matrix.

## Review roles

- **Devin** is the GitHub PR code reviewer (findings). It does not auto-fix.
- **Claude Code Action** (`.github/workflows/claude.yml`) is the fixer for Devin findings and `@claude` mentions on open, same-repository PRs whose author (and, for `@claude`, the commenter) has write, maintain or admin permission on the repo (other PRs are skipped by the workflow's event gate).
- **Codex** has two allowed GitHub review paths, neither a Devin substitute:
  1. **CI/fixer gate** (CSL-103, `.github/workflows/codex-gate.yml`) — one `@codex review` per head SHA; that reply must end with `CODEX-GATE: READY|MEDIATE`. See Code Review Rules.
  2. **Stuck-thread watch** (CSL-86 / PR #124, `.github/workflows/stuck-thread-watch.yml`) — one advisory `@codex review` on unresolved Devin threads Claude could not settle, and **one** Linear `needs-decision` Triage roll-up per PR titled `Stuck PR threads: <repo>#<PR>`. Shared keys with the Codex CI/fixer gate: `<!-- stuck-pr-threads:owner/repo#N -->` and `<!-- codex-gate-mediate:owner/repo:N -->`; each writer owns only its fenced block. It adopts an existing roll-up only by that `stuck-pr-threads` HTML marker, never by title, and writes Linear only when at least one thread is actionable after the per-PR cap and `CODEX_TRIGGER_PAT` checks (or to create a missing roll-up after an earlier Linear outage). It skips already-approved PRs and updates that roll-up instead of filing a new issue per thread. That reply must **not** include a `CODEX-GATE:` line, and must not push, merge, resolve the thread, or open a fix PR.
- **Blacksmith** hosts the paid test matrix. That is the GitHub test-CI path, not a revival of the retired Devin/SWE-2 swarm dispatch route: "No new Devin jobs" / `spawn_devin_leaf.py` frozen (RW-33) and CLAUDE.md's "Devin is retired" still apply to research/dev swarm jobs. Devin reviews GitHub PRs and does not auto-fix review findings (Claude does); that does not grant Devin research/dev swarm jobs. The documented post-merge Main CI failure fixer and weekly dead-code sweep remain the only Devin fix automations.
- Cursor, Codex (as a coding agent), Grok, and interactive Claude Code develop features from Linear issues. They must not post review verdicts or run review-fix loops on other agents' PRs, except the stuck-thread watcher. A founder may have their own bot post reviews under their account when they direct it. The author agent may fix CI failures on its own PR.

## Review loop

Devin reviews → Claude fixes (`claude.yml`) → the Codex gate (CSL-103) oversees
`ci:full` / human handoff only. Devin findings
trigger Claude directly without waiting for Codex. All Claude jobs share one
per-PR lock with GitHub `queue: max` (one active, up to 100 pending; excess
requests can still be dropped). The automated Devin fixer stops at eight consecutive Claude
commits. Stuck threads are also picked up by
[`.github/workflows/stuck-thread-watch.yml`](.github/workflows/stuck-thread-watch.yml)
(CSL-86 / PR #124), which asks Codex to mediate (`@codex review`, no
`CODEX-GATE:` line) and records one Linear Triage `needs-decision` roll-up per
unapproved PR (`Stuck PR threads: <repo>#<PR>`), updated in place. Adopt an
existing roll-up only by the `stuck-pr-threads` HTML marker, never by title.
Write Linear only when at least one thread is actionable after the per-PR cap
and `CODEX_TRIGGER_PAT` checks, or to create a missing roll-up after an earlier
Linear outage. Humans decide.
The watcher never resolves threads, pushes, or merges.

## Code Review Rules

These rules apply to the **CI/fixer gate** `@codex review` (comment marker
`<!-- codex-gate:<sha> -->`). When the head is ready for full CI or needs a
human decision, **end the review body with exactly one applicable verdict
line**. When fixes are still pending, leave advisory findings without a verdict:

    CODEX-GATE: READY
    CODEX-GATE: MEDIATE

- Legacy `CODEX-GATE: FIX` verdicts are ignored. Human-only findings need a human `@claude` mention.
  Devin submitted reviews trigger fixes directly; Codex never dispatches the fixer.
- `READY` — findings are settled or none remain; apply the `ci:full` label
  so the hosted Windows/macOS matrix runs once.
- `MEDIATE` — Devin and Claude disagree or a thread needs a human. File or
  update the shared Linear `needs-decision` Triage issue per PR, titled
  `Stuck PR threads: <repo>#<PR>` (key `<!-- stuck-pr-threads:owner/repo#N -->`).
  Skip if the PR is already approved. Do not wait for stuck-thread-watch (it
  only flags a needs-human phrase or a 6-hour-old Claude reply).

If fixes remain, leave advisory findings without a gate verdict. READY and
MEDIATE are the only actionable verdicts; missing verdicts never time out
into fixer dispatch. Recovery scans retry missing requests and those two
verdicts only. There is no daily fixer dispatch cap. Locked live-head and
unresolved-finding checks skip obsolete queued events; failed runs remain
retryable through a new Devin submitted review or authorized manual dispatch.

If this `@codex review` is a stuck-thread mediation (the comment contains
`<!-- codex-mediation:`), do **not** add a `CODEX-GATE:` line. The gate
ignores those reviews and never dispatches a fixer from them.

## When Claude can't fix a review thread

Devin does not auto-fix review findings, so a review thread is never handed back to Devin.
Stuck-thread handling is owned by the **Review loop** (CSL-86 / PR #124,
`.github/workflows/stuck-thread-watch.yml`). Hosted Claude has no Linear
access: it leaves the thread open, replies with why, and @mentions the PR
author on GitHub. The watcher files **exactly one** roll-up Linear issue per
PR titled `Stuck PR threads: <repo>#N` (`needs-decision`, linked to the PR's
CSL issue, shared with the codex gate's MEDIATE path via
`<!-- stuck-pr-threads:owner/repo#N -->`), not one issue per thread. The human
PR author is the fallback filer. Developers resolve it from Linear like any
other issue.

Before merging any PR, check Linear for that roll-up issue **and** that the PR
has no unresolved GitHub review threads; resolve them or record the decision
first.

## Automations

Devin automations (**Main CI failure fixer** and **Weekly dead-code sweep**, Wednesdays 10 AM PT) are human-managed. Other agents must not create, edit, or trigger Devin automations, and must not edit Devin settings.

## Post-merge flow

- A CI failure on `main` triggers the Devin fixer, which opens a Linear **Bug** (run id in the title) and a fix PR. The Discord bot posts the failure to the alerts channel.
- The weekly dead-code sweep opens a Linear **Chore** plus a cleanup PR.
- The Discord bot sends a weekly digest of stale PRs and issues.

## Security

- Never commit secrets. GitHub secret scanning and push protection are on: a blocked push means remove the secret and rotate it. Never bypass.
- Dependabot security updates are on. Its PRs follow the normal review flow.

## Trading safety

The engine runs in **paper trading** by default. Live trading requires an explicit human-controlled switch. Agents must never enable live trading or add live broker credentials.

## Performance

Flag speed regressions. Pull requests run a non-blocking `benchmarks` job
(`.github/workflows/ci.yml`, CSL-53) that times the order-path and model-output
hot paths against the default-branch tree in the same job and warns when the
median slowdown, after sample spread, is above 10%. It does not fail
`linux-gates`. Still note perf impact in the PR description for engine changes.

## Guardrails (MUST)

Agents must **not** edit CI/runner configuration (`.github/workflows/**`, Blacksmith runner labels, caching, concurrency), Devin configuration, repo rulesets, or the MCP server list, unless a Linear issue explicitly assigned by a human asks for it.

Trigger CI and reviews through the Discord bot (`/ci full|run`, `/devin review pr:<url>|run|status`, or MCP `ci_full` / `ci_run` / `devin_review` / `devin_run` / `devin_status`) or the `ci:full` label — not by editing config.

Do not add paid services or raise spend limits.

### Fixer receipts and bounded no-progress retries (CSL-201)

Automatic eligibility requires explicit Devin `kind: bug` metadata. Trusted Claude
structured per-root receipts bind the original body fingerprint to a validated
base and pushed commit; addressed receipts suppress repeats only when that commit
is strictly newer than the base and remains an ancestor of the live PR head.
Explicit `needs-human` receipts leave the thread unresolved for human mediation.
New meaningful Devin or repository-writer replies rearm it; arbitrary prose,
failed tests, tool/API errors, and no-push runs never prove completion.

Under the shared PR lock, at most three automatic attempt reservations are allowed
for the same head and same meaningful pending-findings fingerprint. A new head or
changed findings resets this budget; authorized manual `devin-review-fix` dispatch
can retry while the eight-commit guard remains. There is no daily cap. Reservations
can include workflow-validation stops before model execution, so after activation
an authorized manual retry may be needed. A paused attempt budget is never READY.

Receipt parsing accepts inline or indented machine markers; quoted receipts do not
hide a repository-writer correction. Root findings must also match the REST
numeric Devin bot identity. Invalid compare SHAs (HTTP 404/422) leave findings
pending; authentication, transport, and server errors still fail closed. The
eight-commit count is applied only after receipt-aware selection proves pending
work remains, so a clean PR does not receive a misleading loop-cap pause.

READY is reconciled against live unresolved bug findings before and after applying
`ci:full`, including explicit human handoffs. Verified addressed receipts count
as handled. Historical READY audit comments never prevent a fresh reconciliation;
MEDIATE retains trusted deduplication. Existing clean labels cause no repeated
label writes or READY comments. `ci:full` requests CI, not merge approval.

Trusted Actions receipts bind gate-owned labels to exact label-event IDs across
head changes. Only that event can be revoked. Human removals are observed with a trusted head-bound receipt and preserved
on that head. A fresh verified READY on a different head may request CI again;
The observation binds the first verified head where the gate sees the removal,
not a reconstructed historical head. If removal on A is first seen on B, B is
conservatively preserved; a later head may request CI. This also covers legacy
removal history without inventing ownership.
Later re-adds are preserved; ambiguous ownership requires manual action. GitHub has no atomic
compare-and-delete, so a human change between the final check and deletion is an
unavoidable narrow race, not an atomic guarantee.

Before adding a label, the gate records a trusted intent. It retries label-event visibility three times with one- and two-second waits. If ownership remains unknown or recording
proof fails, the intent blocks subsequent READY processing, even after a restart.
It never infers ownership from the PAT owner's name. Inspect the label and
manually remove/re-add `ci:full` to reconcile; if absent, add then remove it.
The removal must be later than the intent's timestamp (same-second ambiguity
remains blocked). Legacy labels without ownership proof are not automatically
removed. API failures report uncertain cleanup rather than claiming absence.

An explicit HTTP 400/401/403/404/409/422/429 label-write rejection settles only
that intent and permits retry. Transport errors, timeouts, and server failures
remain uncertain; failure to record rejection also retains the blocker. A failed
PR reconciliation does not prevent scheduled recovery of other PRs.
Same-second review edits are conservatively eligible when ordering is ambiguous;
identical follow-up content retains the same findings fingerprint and three-attempt
budget. Same-second human removal cannot prove intent ordering: remove/re-add
in a later second to reconcile. Event IDs and comment IDs are never compared.

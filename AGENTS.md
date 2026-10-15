# Codex operating guide — AI Investment Cockpit

> **How CSL works:** start with the Linear doc [How CSL works](https://linear.app/csl-laboratories/document/how-csl-works-922e0df6026f).

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
2. Keep changes to one work package and add tests with the change.
3. Use the project virtual environment. Create it once with `python -m venv .venv`
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
4. Keep the repository private and commit only when the operator asks.
5. Any order-path change needs an adversarial-review round.
6. The release-blocking local matrix is **native Windows plus Ubuntu under WSL2**, each on
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

## Engineering stack (Oct 2026)

- **Parallel Search MCP** — web search and fetch at https://search.parallel.ai/mcp (no key).
- **Context7 MCP** — up-to-date library docs at https://mcp.context7.com/mcp (works without a key).
- **Linear MCP** — issues, comments, and status at https://mcp.linear.app/mcp (or the Devin Linear integration).
- **Linear** — source of truth for work. Workspace **CSL laboratories**, team key **CSL**, project **ai-investment-cockpit**.
- **GitHub** — org **CSL-Laboratories**.
- **Devin** — the only PR reviewer, review only (on PR open; on-demand via `/devin review`).
- **Claude Code Action** — PR fixer for Devin findings and `@claude` mentions on open, same-repository PRs whose author (and, for `@claude`, the commenter) has write, maintain or admin permission on the repo (other PRs are skipped by the workflow's event gate) (`.github/workflows/claude.yml`).
- **Blacksmith** — the only CI.
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

Agents must stay Linear-aware and proactive. Linear is the source of truth.

- **Before starting work**, find or create the Linear issue (team **CSL**, project **ai-investment-cockpit**) via the Linear MCP or the Devin Linear integration. Do not start a change without a `CSL-N`.
- Branch names come from Linear's copied branch name, so the PR auto-links. PR title includes `CSL-N`. One issue per PR; keep PRs small and stackable.
- Comment progress on the issue. Move it to **In Progress** when work starts, then **In Review** when the PR is up.
- When work reveals follow-ups, bugs, or next steps, create new **Triage** issues with one Type label, the repo label `ai-investment-cockpit`, the project, and a link back to the parent issue or PR — not drive-by changes.
- **Assignees:** the three founders are Lucas (NotSkittlz), Chaandan (CDanTheMan), and Samir (samir-shah-ahmed). All are admins, and ownership changes over time, so never hardcode owners. Check the current assignee or project lead in Linear. Assign new issues to the owner of the parent issue, or leave them in Triage unassigned when unclear.
- The author addresses CI comments on their own PR, then resolves the threads. Devin findings and `@claude` mentions are handled by the Claude Code Action (`.github/workflows/claude.yml`) on open, same-repository PRs by repo writers; on other PRs the author addresses them. Do not run review-fix loops on other agents' PRs.
- Humans make the load-bearing decisions (architecture, security, trading-risk, merge order). Agents propose; humans decide.

## Hosted CI

Hosted split (PR #102):

- Required check: **`linux-gates`** (Linux, Python 3.11 and 3.12, on Blacksmith).
- Windows runs on push to `main`, the `ci:full` label, and manual dispatch.
- macOS runs nightly at 07:17 UTC on `main` (skipped if idle 24h), on the `ci:full` label, and on manual dispatch — not on ordinary `main` pushes.
- Superseded runs are cancelled.

## Review roles

- **Devin** is the only GitHub PR reviewer. It does not auto-fix.
- **Claude Code Action** (`.github/workflows/claude.yml`) is the fixer for Devin findings and `@claude` mentions on open, same-repository PRs whose author (and, for `@claude`, the commenter) has write, maintain or admin permission on the repo (other PRs are skipped by the workflow's event gate).
- **Blacksmith** is the only CI. That is the GitHub review path. It does not revive the retired Devin/SWE-2 swarm dispatch route: "No new Devin jobs" / `spawn_devin_leaf.py` frozen (RW-33) and CLAUDE.md's "Devin is retired" still apply to research/dev swarm jobs.
- Cursor, Codex, Grok, and interactive Claude Code develop features from Linear issues. They must not post review verdicts or run review-fix loops on other agents' PRs. The author agent may fix CI failures on its own PR.

## Code Review Rules

Codex reviews of GitHub pull requests in this repository are the CI/fixer
gate (CSL-103), not a substitute for Devin. After reviewing a PR, **end the
review body with exactly one verdict line**:

    CODEX-GATE: FIX
    CODEX-GATE: READY
    CODEX-GATE: MEDIATE

- `FIX` — Devin (or the human) left actionable findings; run one batched
  Claude PR Fixer pass (`devin-review-fix`).
- `READY` — findings are settled or none remain; apply the `ci:full` label
  so the hosted Windows/macOS matrix runs once.
- `MEDIATE` — Devin and Claude disagree or a thread needs a human; hand off
  to the stuck-thread-watch / Codex mediator (CSL-86 / PR #124).

Do not omit the verdict line. Do not put more than one. The gate's 20-minute
timeout treats a missing verdict as `FIX` (fail open, capped at 3 fixer runs
per PR per UTC day). Human `@claude` mentions bypass that cap.

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

Flag speed regressions. A benchmark CI step is tracked in Linear; until it lands, note perf impact in the PR description for engine changes.

## Guardrails (MUST)

Agents must **not** edit CI/runner configuration (`.github/workflows/**`, Blacksmith runner labels, caching, concurrency), Devin configuration, repo rulesets, or the MCP server list, unless a Linear issue explicitly assigned by a human asks for it.

Trigger CI and reviews through the Discord bot (`/ci full|run`, `/devin review pr:<url>|run|status`, or MCP `ci_full` / `ci_run` / `devin_review` / `devin_run` / `devin_status`) or the `ci:full` label — not by editing config.

Do not add paid services or raise spend limits.

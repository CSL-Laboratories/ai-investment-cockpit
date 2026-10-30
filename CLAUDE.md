# CLAUDE.md — operating manual for the AI Investment Cockpit

> Auto-loaded each session. Keep it tight and high-signal. The detailed playbooks
> and the full design corpus live in `docs/` — start at `docs/README.md`.

## Product target and current implementation

The ratified [PRODUCT.md](PRODUCT.md) owns purpose, scope and acceptance for the
integrated autonomous research-to-paper product. [PLAN](docs/PLAN.md) owns delivery
order and the reviewed migration from the current implementation described below.
The nine invariants and runtime rules here remain in force until their separately
scoped, reviewed implementation amendments; ratification alone grants no new authority.

For the replacement program, read
[SUCCESSOR-PRODUCT.md](docs/replacement/SUCCESSOR-PRODUCT.md),
[MONTH-PROGRAM.md](docs/replacement/MONTH-PROGRAM.md) and the exact acceptance
record in [program.json](docs/replacement/program.json). Accepted bytes and scoped
gates remain controlling. Until the ISSUE-CONTRACT amendment of 2026-10-25 is accepted
and promoted, only the Lucas Linear digest receipt recorded in `program.json` is exact
acceptance; once it is, a checker-pinned founder GitHub PR approval will also count, as
defined in [AGENTS.md](AGENTS.md#founder-decisions-and-integration). That amendment is
proposed, not accepted, in `CSL-Laboratories/csl-research` commit
`d885c2de7228ab4e886b93165d30013e4c8bc5fd`, path
`contracts/ai-investment-cockpit/amendments/ISSUE-CONTRACT-2026-10-25.md`, SHA-256
`0b756e0777520f7b8a83d518b6b5887e96ed98eb8481d9d3840ddf4418c9b345`, via
[csl-research #58](https://github.com/CSL-Laboratories/csl-research/pull/58) (not merged). Follow [AGENTS.md's founder decision and integration rules](AGENTS.md#founder-decisions-and-integration)
for operational authority; they do not amend accepted contracts, activate R2a or
unlock live capital. Founders merge after the required current-head reviews and checks;
agents never merge. A founder's own PR requires another founder's approval before merge,
regardless of ruleset enforcement. Non-author approval checks alone do not establish
founder identity. All three founders participate in the final
release decision; Lucas has final say in any founder disagreement, including a 2-1 split.

For issue admission, decision holds, claim records, release and handoff, manual ownership and shared research evidence, follow [AGENTS.md's bounded issue execution and shared evidence rules](AGENTS.md#bounded-issue-execution-and-shared-evidence). They grant no new authority, settings or spend and leave review-loop roles unchanged.

Before acting on your own, apply the `proceed-or-escalate` skill
(`.claude/skills/proceed-or-escalate/SKILL.md`). It carries the gates of
[AGENTS.md's proceed-or-escalate section](AGENTS.md#proceed-or-escalate) and expands
on them; if they differ, AGENTS.md wins. Escalate wins over proceed, including for
the order path, risk, brokers, audit, PnL, costs, the auto loop, halt, kill switch,
gate, mode arming, breakers, limits, the order and mode loopback routes, approval,
veto, any test that exercises them (including `app/tests/test_invariants.py`) and
the paper/live switch. The lists in AGENTS.md are examples, not a boundary; a
failing test there is not a clear defect to fix alone.

Current implementation:
A three-founder, subscription-powered, **human-gated** AI investing copilot.
The three founders are Lucas (NotSkittlz), Chaandan (CDanTheMan), and
Samir (samir-shah-ahmed).
**Paper-first** — no live capital moves until the pre-live gates pass. Think of it
as **two engines**:
1. **Awareness + discipline** — the reliable value (time saved, not missing things,
   enforced discipline). Powered by the AI's real strength: information work.
2. **Execution** — mechanically reliable, financially *experimental*, capped, and
   gated. Engine 1 makes Engine 2 safer and better-informed; it never makes it
   "reliable growth."

The reliable ROI is **time, awareness, and discipline — not alpha.** Any plan that
leads with "the AI will beat the market" is wrong; the corpus documents why.

## The 9 invariants — NEVER weaken silently
1. **Single order path** — an order reaches a broker ONLY via risk-pass → mode →
   approval → `OrderGateway`. This includes **human-initiated** trades.
2. **Decision path pinned to Claude in auto modes** — Codex/other runtimes are
   barred from the decision/order path.
3. **Schema-validate every model output** (repair-then-quarantine).
4. **Secrets only in the OS credential store** (`keyring`) — Windows Credential
   Manager, macOS Keychain, or Linux SecretService; never in repo, prompts, chat,
   or logs.
5. **`risk/` is pure deterministic functions** — NO LLM, NO I/O, NO subprocess.
   Enforced by the no-LLM-in-risk CI gate.
6. **Human gate is client-side; hash-chained append-only audit** — every
   state change is audited.
7. **Fail closed** — vendor-limit / stale-data / hung orchestrator → `watch_only`,
   never "trade without fresh analysis."
8. **Raw inputs stored separately from summaries** — enables replay + interrogation.
9. **Post-PDT risk model** — Reg-T $2,000 min equity, no day-trade counts.

**PAPER-FIRST** overrides all: no live capital until the P0 pre-live gates pass —
chief among them SEC-11 (`docs/security-hardening-backlog.md`).

## Current load-bearing rules
- **New data feeds are UNTRUSTED input** — wrap in `<untrusted-content>`,
  canary-scan (SEC-02). Inv 3 validates model *output*; this governs tool/feed *input*.
- **The current operator surface is Discord + loopback API** (current state,
  pending the CP0 amendment on CSL-511 / #274; not the product target). A general
  `OperatorSurface` abstraction is planned, not merged; do not create new order authority or
  assume another adapter already exists.
- **The current order universe is a static operator-approved allowlist.** Dynamic discovery is
  future awareness scope only; no model or API may mutate execution eligibility at runtime.
- **Execution stays model-free.** Models serve awareness/reasoning only.
- **Research/dev host is Cursor Cloud Agent Linux VM.** Cursor-native fresh
  sessions are preferred; Codex/OpenCode development routes require exact-host
  qualification and admitted budgets. Devin is retired from this cockpit's
  research/dev swarm dispatch only; its GitHub PR-review role in `AGENTS.md`
  remains active. Devin may still post research briefs, which, like all
  research, land in CSL-Laboratories/csl-research. Muse synthesis requires
  a different model family; OpenRouter/Hive are capped fallback synthesis only.
  Historical Cloud process (legacy, frozen): `docs/research/agent-swarm-rebase-2026-09-14.md`.
  Historical Cloud handoff (legacy, frozen): `docs/research/cursor-cloud-rdt-handoff-2026-09-14.md`.
  No swarm broker tools, secrets or soak starts. Product authority is unchanged.

## Session workflow
1. Read `docs/README.md`, `PRODUCT.md` for product scope/acceptance, then
   `docs/PLAN.md` for current delivery order and `docs/REGISTER.md` for claims.
   Linear holds the human owner (assignee), agent claim records and status;
   REGISTER keeps package IDs, claims, scope and completion contracts. See
   `AGENTS.md` Linear-first workflow.
   The adopted product contracts supply required behavior; subsystem/reference
   documents do not independently change delivery order or current runtime authority.
2. For package work, pick **ONE** work package (RW-xx refactor, or W-xx
   production). For process-only work, use one admitted CSL issue with
   `REGISTER: none`; do not allocate a package solely for that work. Confirm
   the chosen scope and its deterministic gate before writing code. Decision
   or direction work: see `AGENTS.md` Linear-first. A Grok Bot coordinator may
   research and recommend; attribute the brief as
   `Grok Bot, preparing research for <owner>`. Only the designated human
   decision-maker, or an explicitly authorized research team, decides.
   Publishing a brief never marks the issue decided, never clears a hold, and
   never authorizes implementation. Every research pass posts a digest on the
   Linear issue and a `submissions/<record_id>/` record in
   CSL-Laboratories/csl-research (README, AGENTS.md, templates,
   `tools/check_submission.py`) and opens a PR there before releasing the
   issue claim. Research may use any deep-research source the team has, such
   as Parallel, Exa, Claude Deep Research, Open Deep Research, ChatGPT Deep
   Research or the web. Each run records its tool, its run id and its cost
   (or `unknown` plus a reason) in its csl-research record. Every new
   non-public doc goes to csl-research through a PR there; this repo only
   links to it. Gate and operational evidence that code or a runbook in this repo already reads
   stays in `docs/evidence/` until the CSL-536 move round that lists it
   (csl-research #263, approved by Lucas on Oct 30), and no earlier than
   the date the list gives it. Contract fixtures under
   `docs/replacement/outcomes/**` stay. Existing files stay until the
   CSL-536 move round that lists them, and no earlier than that date.
   `docs/research/**` is legacy (no new files).
3. Implement. Preserve the invariants. Add tests alongside.
4. Run the gates (below). Green before you stop.
5. **Do not re-litigate settled decisions** — the corpus is a one-way pipeline
   (research → architecture → build → security → enhancements). Recorded
   architecture and direction live in `docs/adr/` and the linked Linear
   `decision` issues.
6. Commit only when the operator asks. For package work, use the package ID
   in the message, for example `feat(RW-xx): …` / `fix(RW-xx): …` /
   `docs(RW-xx): …`. For process-only work with `REGISTER: none`, use the
   admitted issue ID, for example `docs(CSL-N): …` or `chore(CSL-N): …`.
7. **CI etiquette (CSL-255):** open PRs as drafts; batch pushes; mark ready
   once after validation and only when no owner hold remains. Never merge a
   PR, rebase, or force-push. Avoid routine base merges; a normal base merge
   is allowed for a real conflict or an owner-approved dependency integration,
   even without a text conflict. Record the reason and rerun the combined gates.
   Never edit `.github/workflows/**` unless a Linear issue
   explicitly assigned by a human asks for it; bots mark ready with their own
   identity, never `GITHUB_TOKEN`.

Before writing on a shared PR branch, check the current Linear owner/claim and
active writers. For a confirmed collision, follow the
[per-PR pause procedure](docs/ci-runbook.md#5-stop-or-roll-back-automation).

## Hard rules
- Never put an LLM on the order or risk path.
- Never move live capital or unlock an auto rung without its gate.
- **Never weaken a gate or invariant to make a test pass — fix the code.**
- Prompts live in git; fail any soak/replay run on a dirty working tree.
- Any change touching the order path gets an **adversarial-review round** (the
  attack→fix→regression pattern; see `docs/ultracode-build-playbook.md`).
- When unsure whether something is settled, grep the corpus before deciding.

## Run the gates

`DEC-09` defines the release-blocking matrix: run the full suite and Ruff locally on native
Windows and Ubuntu/WSL2, each with Python 3.11 and 3.12. macOS is best-effort. Hosted Actions
do not replace that matrix: Linux `linux-gates` is the cheap required PR check; Windows runs
on push to `main`, `merge_group`, the `ci:full` label, or full `workflow_dispatch`; macOS runs nightly on `main` (07:17 UTC,
skipped if idle 24h), on `ci:full`, or on `workflow_dispatch`. Hetzner is not part of
current package validation. This changes platform evidence only and does not waive any online,
broker, witness, paper-day, or pre-live gate.

The staged [CSL-259 candidate coordinator](docs/ci-candidate-readiness.md) requests
all five Python and five Rust cells, including docs-only candidates, after an
authenticated exact head/base/merge/tree independent READY and before human
approval. It preserves human holds and manual label removal; persistent labels
and legacy head-only review/CI are not proof. No repository protections or queue
settings are activated by this code.

```bash
cd app
python -m pip install -U pip pydantic pydantic-settings keyring pyyaml jsonschema sqlalchemy pyarrow duckdb fastapi "uvicorn[standard]" httpx websockets apscheduler boto3 discord.py pytest ruff
python -m pytest -q      # incl. Invariant-1 call-site / no-LLM-in-risk / secrets-grep gates
ruff check .
```

Install the full runtime set in every matrix cell, including `discord.py`. The Discord
command-tree tests run offline with the real dependency; a separate isolated subprocess
forces its import to fail and covers the fallback without skipping either path.
`discord.py` 2.7.1 declares `audioop-lts; python_version >= "3.13"` in its PyPI
`requires_dist`. On 3.11 and 3.12 the stdlib `audioop` import in `discord.player` is a
DeprecationWarning, and pytest ignores that one warning only. That marker is why a
`<3.13` cap is not added for this warning. A 3.13 matrix cell stays a separate support
decision.

## Where things are
- **Core engines (solid — change with care + adversarial review):** `app/risk/`,
  `app/brokers/` (gateway, state machine, capability), `app/orchestrator/`,
  `app/storage/audit.py`.
- **Data feeds:** `app/ingest/`. **LLM pipelines:** `app/workflows/`.
- **Operator surfaces:** `app/bridge/` (Discord) and `app/orchestrator/app.py` (loopback API).
  A general `app/surfaces/` abstraction is not merged.
- **Design corpus:** `docs/` — index at `docs/README.md`. Historical Cloud
  process (legacy, frozen): `docs/research/agent-swarm-rebase-2026-09-14.md`.
  Current research landing: CSL-Laboratories/csl-research.

## Review loop

For writer collisions, use the [pause and resumption checklist](docs/ci-runbook.md#5-stop-or-roll-back-automation).

Devin reviews → Claude fixes (`claude.yml`) → stuck threads are picked up by
[`.github/workflows/stuck-thread-watch.yml`](.github/workflows/stuck-thread-watch.yml),
which asks Codex to mediate (`@codex review`) and records one Linear Triage
`needs-decision` roll-up per unapproved PR (`Stuck PR threads: <repo>#<PR>`),
updated in place. Shared keys with the Codex CI/fixer gate:
`<!-- stuck-pr-threads:owner/repo#N -->` and
`<!-- codex-gate-mediate:owner/repo:N -->`. Adopt an existing roll-up only by
the `stuck-pr-threads` HTML marker, never by title. Write Linear only when at
least one thread is actionable after the per-PR cap and `CODEX_TRIGGER_PAT`
checks, or to create a missing roll-up after an earlier Linear outage. Humans
decide. The watcher never resolves threads, pushes, or merges.

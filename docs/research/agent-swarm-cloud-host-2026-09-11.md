---
title: Cloud Devin swarm host — owner direction
status: reference
last-verified: 2026-09-11
amends:
  - research/agent-swarm-orchestration-2026-09-10
---

# Cloud Devin swarm host — owner direction (2026-09-11)

**Baseline:** `main` at `5907207` (PR #86 merged; 2-step swarm reference on
`docs/research/agent-swarm-orchestration-2026-09-10.md`).
**Scope:** owner process amendment for *how research, claimed-package
development, and adversarial review are hosted*. It does not change PRODUCT
acceptance, delivery-slice order, the nine invariants, paper/live authority, or
any empirical gate. It claims no PASS.

## Owner answers (2026-09-11)

Lucas directed, in the Cursor Cloud Agent thread on this repository:

1. **Do not host the swarm on `sentry`.** Run it on the Cloud Agent's own
   Linux VM ("your local machine" in that thread).
2. **Devin is the swarm runtime**, using owner-stated essentially-free SWE-2
   credits, at a target of **30–40 concurrent agents** working synchronously
   on research, development, and adversarial review.
3. **Market testing / live-testing is not a swarm job.** Those runs stay with
   Lucas on Grok Bot or another service he operates. The swarm must not start
   a cockpit soak, hold broker credentials, or treat a Devin session as a
   market-day witness.

This is owner go-ahead for the Cloud host and the Devin research/dev/review
role. It is not go-ahead to merge, to move live capital, to unlock an auto
rung, or to implement slice C/D authority changes without their own claimed,
adversarially reviewed packages.

## What this amends

The 2026-09-10 swarm reference placed long-running work on `sentry` (Windows
host + WSL2 Ubuntu) and treated Devin as an unnamed runtime until a boundary
amendment. Both of those statements are superseded here:

| Topic | 2026-09-10 | 2026-09-11 |
|---|---|---|
| Swarm host | `sentry` via SSH / Remote Desktop | Cursor Cloud Agent Linux VM |
| Head | human + OpenCode/Codex, optionally thin on the laptop | the Cloud Agent session (Cursor), on the same VM as the leaves |
| Named leaf/middle runtime | unnamed until AGENTS named it; Codex/Claude-only until then | Devin CLI named in `AGENTS.md` for research, claimed-package development, and adversarial-review jobs |
| Target concurrency | 1–2 leaf pilot; 30-way needed its own later go-ahead | 30–40 concurrent Devin agents (owner-stated SWE-2 credits) |
| Market / live / paper soaks | not the swarm's job (unchanged) | still not the swarm's job; operator is Lucas via Grok Bot or another service |

The 2-step shape (Head → domain middles → leaves), the nine invariants, PD-047
disclosure, REGISTER claims, and the never-touch list (`app/risk/`,
`OrderGateway`, mode/approval, `storage/audit.py`, `core/secrets.py`,
eligibility policy) are unchanged.

Sentry inventory in the 2026-09-10 doc (WSL VHDX path, D: move procedure,
unrelated Chiron pilots) remains historical operator context. It is not the
swarm host and must not be started for this work.

## Runtime split

Treat development autonomy and investment autonomy as separate, per the
product-planning record.

| Work | Who runs it | Where | Must not |
|---|---|---|---|
| Research briefs, claimed-package implementation, adversarial review (attack→fix→regression drafts), schema-gated analysis | Cursor Cloud Head + Devin leaves/subagents | Cloud Agent Linux VM | order path, broker tools, soak/live start, private docs beyond the brief's disclosure class |
| DEC-09 matrix, paper market days (PD-039 / slice F), live smoke, broker-facing soaks | Lucas, on Grok Bot or another service he operates | owner-chosen host | be started, credentialed, or "witnessed" by the swarm |

Grok Bot remains an **operator procedure**, not a selected product
integration (PD-007). Naming it here does not add a Discord/host/broker
adapter or a second portfolio owner.

## Cloud host profile (measured 2026-09-11)

Recorded on Cloud Agent run `bc-1aa25759-1028-49f8-8116-9f35d53edf89`
(personal environment, unrestricted egress, Linux 6.12, Python/tooling
already present):

- Devin CLI **3000.10.21** installs and runs from `~/.local/bin/devin`.
- Unauthenticated `devin -p` fails closed (`Login canceled`).
- `devin auth login --force-manual-token-flow` is the remote auth path
  (PKCE URL + one-time code). Durable credentials land in
  `~/.local/share/devin/credentials.toml` and **do not survive pod teardown**.
- `bubblewrap` + `socat` install; unprivileged user namespaces work; `bwrap`
  smoke succeeded. Linux `--sandbox` is therefore *available* on this host.
  Native Windows `--sandbox` still refuses; that prohibition is unchanged
  for any future Windows box.
- Pod size this run: **4 vCPU / ~16 GiB RAM / ephemeral disk**.

Owner-stated SWE-2 credits address **inference cost**, not pack 40
heavyweight `devin -p` OS processes onto one 4-vCPU pod. Prefer:

1. Devin-native subagents (SWE-2 for research/explore; general/dev only
   when the brief needs writes) inside a sandboxed Devin session, with
   `max-nesting` ≤ 3 and no leaf grandchildren.
2. Additional **Cursor Cloud Agent** runs as extra Heads if one pod
   saturates.
3. OS-process `devin -p --prompt-file … --sandbox --respect-workspace-trust false`
   leaves for attempt-isolated filings when a middle must not share a
   session.

Do not route overflow to `sentry`. Do not treat vendor "unlimited
concurrency" as a cockpit allowance — REGISTER claims, disclosure class,
deadlines, and reservation receipts still govern.

## Persistence (Cloud-specific)

A Cloud Agent VM dies with the run. Therefore:

- Install Devin CLI per run or in the environment `install` script. Do **not**
  snapshot `credentials.toml`.
- Persist auth across runs only via a Cursor environment secret (ACP
  documents `WINDSURF_API_KEY`) or a fresh PKCE login. Never commit the
  credentials file, paste a long-lived token into chat, or bake it into
  `environment.json`.
- Leaf filings still land **outside** the tracked tree during the run (dirty
  trees fail soak/replay). Before pod teardown the Head must export the
  run-scoped evidence dir (hashes + branch/PR/artifact). Lost-with-the-VM
  filings are `unknown` completion, not zero usage.
- Swarm commits/PRs are Head work after synthesis. Merge and software
  release stay owner-approved (PD-030).

## Devin spawn profile (the §4 amendment)

Named in `AGENTS.md` on this same amendment. Any Devin leaf/middle handling
cockpit material uses this profile; an implicit "agent can do anything"
default remains a refusal.

- Linux Cloud Agent only; `--sandbox` required (fail closed if `bwrap` /
  `socat` / userns are missing).
- `--prompt-file <checked-in brief>` mandatory. Inline `-p` only as the
  wrapper "execute the brief file at \<path\>", after `--`.
- Forbidden: `-r`, `--resume`, `-c` / `--continue`, `--permission-mode
  dangerous` / `bypass` / `yolo`. `autonomous` only together with
  `--sandbox`. `--permission-mode auto` remains exec-capable and is not a
  read-only profile.
- `--respect-workspace-trust false` is permitted only because non-interactive
  print mode cannot show the trust prompt.
- No broker tools, no MCP that can reach `OrderGateway`, no secrets, no
  `local-only` private documents. Default disclosure class: `public-tracked`.
- SWE-2 (or the current cheap Devin subagent model) for research/explore
  leaves. Do not silently put every general/dev leaf on a premium parent
  model.
- Still no deny-by-default argv inspector comparable to `tool_inspector.py`.
  The frozen argv manifest plus review remains the control. Swarm spawn
  never transits cockpit `run_agent` / orchestrator code.
- OpenCode is still unnamed. Do not put an OpenCode `--auto` leaf on cockpit
  material under this amendment.

## Development and adversarial review

Owner direction includes **development** and **adversarial review**, which
is broader than the 2026-09-10 review-only 1–2 leaf pilot.

- One REGISTER claim per package before code. Unchanged inputs must not
  restart unbounded investigation.
- Swarm may edit claimed package files and open PRs. It may run the
  attack→fix→regression *pattern* as a review job.
- Order-path packages still require that pattern as a **required** review
  (`docs/ultracode-build-playbook.md`). A swarm-authored review is evidence
  for the reviewer, not a waiver.
- Slice C/D authority implementation (standing approval, eligibility, order
  path, mode ladder) stays separately claimed and adversarially reviewed.
  A research leaf still may not implement those.
- The never-touch list is unchanged.
- Dirty working trees still fail any overlapping soak/replay. Do not start
  a soak on a swarm-dirty Cloud checkout.

## Concurrency mapping

30–40 concurrent Devin agents maps to the existing 2-step shape without
flattening it:

```text
Head (Cursor Cloud Agent)
  owns briefs, schemas, REGISTER claims, frozen manifests, stop/PR decision
  │
  ├─ Middle: Research/evidence (slice B)           ≤8 Devin leaves
  ├─ Middle: Strategy feasibility + evaluation (A) ≤8 Devin leaves
  ├─ Middle: Executor/routing/recovery contracts
  │   (slice C — contract text only)
  ├─ Middle: Tax/economics references (slice E)
  └─ Middle: Replay/release-qualification scaffolding (slice F)
       (scaffolding and replay fixtures only — not the live market days)
```

Five domains × ≤8 leaves plus middles is the 30–40 band. Head does not
become a 40-way flat fan-out. A leaf still must not spawn grandchildren.

Second-gate synthesis is Muse Spark 1.3, not another SWE-2 pass and not
Composer. See
[`agent-swarm-pipeline-2026-09-11.md`](agent-swarm-pipeline-2026-09-11.md).

## Doc disposition

This note amends host, named runtime, target concurrency, and the
research/dev vs market-testing split. PLAN still owns delivery order; PRODUCT
still owns scope/acceptance; REGISTER still owns claims; CLAUDE still owns
the nine invariants. Cite it together with the 2026-09-10 2-step reference.
It cannot be cited as a passed gate, a live unlock, or permission to skip a
REGISTER claim.

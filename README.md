# Local AI Investment Cockpit

A solo-developer, subscription-powered, **human-gated** AI investing copilot: a
morning-brew daily briefing plus human-approved short/long-term trade decisions, run
from one always-on Windows workstation and operated from anywhere over Discord.

> **Status: WP0–WP9 COMPLETE (2026-07-08) — paper-first, no live trading.** The full
> phased ultracode build (WP0 → WP9) has shipped to `main` and is green: the WP9
> system-test harness (commit `9f6ff44`) passes 586 tests / 3 skipped with `ruff`
> clean, and every per-WP deterministic gate (G0…G9) is green. The system remains
> **paper-first**: no real capital has moved and none may until the explicit pre-live
> gates pass — chief among them the **OPEN P0 audit-spine blocker (SEC-11
> delete-then-heal)** in [`docs/security-hardening-backlog.md`](docs/security-hardening-backlog.md).
> A dollar only moves after a multi-week paper soak and the performance/security gates.
> Graduated from the `csl-general` brainstorming vault on 2026-07-06.

## Layout

| Path | What |
|---|---|
| `docs/` | The design corpus — research → architecture → build → security → enhancements. **Start at [`docs/README.md`](docs/README.md).** |
| `app/` | The code, per the WP0–WP9 scaffold ([`docs/build-scaffold-ultracode.md`](docs/build-scaffold-ultracode.md)). |
| `.github/workflows/ci.yml` | The merge-gates (Invariant-1 call-site, no-LLM-in-`risk/`, secrets-grep) — live on every push from WP0. |
| `docs/setup/accounts-and-keys.md` | How to provision accounts/keys into Windows Credential Manager. |

## The load-bearing rules (never weaken silently)

1. **Single order path** — order placement is reachable *only* via risk-pass → mode → approval/veto → broker (`brokers/`). *(Invariant 1)*
2. **Deterministic risk engine** — `risk/` is pure functions, no LLM inside. *(Invariant 5)*
3. **Human gate + hash-chained audit** — the approval gate is client-side (the broker does *not* enforce it). *(Invariants 1, 6)*

The full 9 invariants, the threat model, and the required hardening backlog (SEC-01…SEC-24)
live in `docs/` — see [`docs/security-hardening-backlog.md`](docs/security-hardening-backlog.md).

## How the build works

Phased: **one Workflow call per work package** (WP0 → WP1a → WP1b → … → WP9), with a
human merge checkpoint and a deterministic gate between each. The orchestration manual
is [`docs/ultracode-build-playbook.md`](docs/ultracode-build-playbook.md).

## Run the gates (WP0 → WP9)

```bash
cd app
python -m pip install pydantic pydantic-settings keyring pyyaml jsonschema pytest ruff
python -m pytest -q      # full suite incl. Invariant-1 call-site / no-LLM-in-risk / secrets-grep gates (586 pass, 3 skipped as of 2026-07-08)
ruff check .             # G-gate lint, clean as of 2026-07-08
```

## Windows + Codex setup

From PowerShell at the repository root, create the project-local Python environment:

```powershell
.\tools\bootstrap_windows.ps1
```

Then authenticate the already-installed Codex CLI in the **same Windows account** that will run
the cockpit:

```powershell
codex login --device-auth
codex login status
```

The current Codex integration is intentionally limited to explicit research, coding, and system
maintenance jobs. It runs `codex exec` with a read-only sandbox and never stores an OpenAI API key
in the project's keyring. The existing automatic decision/order path remains Claude-pinned by
Invariant 2; with a Codex-only subscription, leave the cockpit in read-only/watch-only mode. A
Codex decision-runtime replacement would be a separate security redesign, not a configuration
toggle. See [`AGENTS.md`](AGENTS.md) and
[`docs/setup/accounts-and-keys.md`](docs/setup/accounts-and-keys.md).

## Before you build

- Provision accounts + keys into Windows Credential Manager — [`docs/setup/accounts-and-keys.md`](docs/setup/accounts-and-keys.md).
- Re-verify the moving targets before Phase 3 (live) — [`docs/setup/moving-targets-refresh-2026-07-06.md`](docs/setup/moving-targets-refresh-2026-07-06.md).
- **Keep this repository private.** Secrets live in keyring, never in the repo (Invariant 4).
- Not financial advice; personal-use experiment with capped, loss-tolerable downside.

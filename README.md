# Local AI Investment Cockpit

A solo-developer, subscription-powered, **human-gated** AI investing copilot: a
morning-brew daily briefing plus human-approved short/long-term trade decisions, run
from one always-on Windows workstation and operated from anywhere over Discord.

> **Status: WP0 scaffold — no live trading.** The system is paper-first and phased; a
> dollar only moves after a multi-week paper soak and explicit gates. Graduated from
> the `csl-general` brainstorming vault on 2026-07-06.

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

## Run the WP0 gate

```bash
cd app
python -m pip install pydantic pydantic-settings keyring pyyaml jsonschema pytest ruff
python -m pytest -q      # includes the Invariant-1 / no-LLM-in-risk / secrets-grep gates
```

## Before you build

- Provision accounts + keys into Windows Credential Manager — [`docs/setup/accounts-and-keys.md`](docs/setup/accounts-and-keys.md).
- Re-verify the moving targets before Phase 3 (live) — [`docs/setup/moving-targets-refresh-2026-07-06.md`](docs/setup/moving-targets-refresh-2026-07-06.md).
- **Keep this repository private.** Secrets live in keyring, never in the repo (Invariant 4).
- Not financial advice; personal-use experiment with capped, loss-tolerable downside.

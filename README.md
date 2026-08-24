# Local AI Investment Cockpit

A solo-developer, subscription-powered, **human-gated** AI investing copilot: a
morning-brew daily briefing plus human-approved short/long-term trade decisions, run
from one always-on Windows workstation and operated from anywhere over Discord.

> **Status (2026-08-24): foundation and quant engine built; paper-reliability MVP not yet
> complete; no live trading.** On clean main
> `46c7c812eccd476c4578dbef59200f5249c26db3`, the required local Windows + Ubuntu/WSL2
> Python 3.11/3.12 matrix is green, including the order-path, no-LLM-in-`risk/`, secrets,
> and quant→brokers containment gates. Under `DEC-09`, this four-cell local matrix is the
> release authority; macOS is best-effort and GitHub-hosted runs are optional diagnostics.
>
> The system remains **paper-first**: no real capital has moved and none may until the
> explicit pre-live gates pass — chief among them the **OPEN P0 audit-spine blocker (SEC-11
> delete-then-heal)** in [`docs/security-hardening-backlog.md`](docs/security-hardening-backlog.md).
> A dollar only moves after a multi-week paper soak and the performance/security gates.
> Graduated from the `csl-general` brainstorming vault on 2026-07-06.

The cockpit currently supports scheduled awareness, cited/schema-gated briefing and proposal
workflows, human-approved Alpaca paper submission through `OrderGateway`, and contained offline
quant simulation. It does **not** yet provide broker-status polling/reconciliation, dynamic
event-driven paper autonomy, Robinhood connectivity, or autonomous live execution. The current
development sequence is `RW-14 → W-21 → RW-15 → W-22`; see
[`docs/PLAN.md`](docs/PLAN.md) for the offline-to-deployment lifecycle and
[`docs/REGISTER.md`](docs/REGISTER.md) for claim state.

## Layout

| Path | What |
|---|---|
| `docs/` | The design corpus — research → architecture → build → security → enhancements. **Start at [`docs/README.md`](docs/README.md).** |
| `app/` | The code, per the WP0–WP9 scaffold ([`docs/build-scaffold-ultracode.md`](docs/build-scaffold-ultracode.md)). |
| `app/quant/` | The QT quant engine — real FIFO order book, strategies, replay, costs, PnL. **Read [`docs/quant-engine.md`](docs/quant-engine.md) before changing anything here**; it carries the containment argument. |
| `.github/workflows/ci.yml` | Optional manually dispatched hosted parity check. The authoritative merge gates run locally under `DEC-09`. |
| `docs/setup/accounts-and-keys.md` | How to provision accounts/keys into the host OS credential store through `keyring`. |

## The load-bearing rules (never weaken silently)

1. **Single order path** — order placement is reachable *only* via risk-pass → mode → approval/veto → broker (`brokers/`). *(Invariant 1)*
2. **Deterministic risk engine** — `risk/` is pure functions, no LLM inside. *(Invariant 5)*
3. **Human gate + hash-chained audit** — the approval gate is client-side (the broker does *not* enforce it). *(Invariants 1, 6)*

The full 9 invariants, the threat model, and the required hardening backlog (SEC-01…SEC-24)
live in `docs/` — see [`docs/security-hardening-backlog.md`](docs/security-hardening-backlog.md).

## The quant engine (QT)

The cockpit was built to answer *whether* a trade is a good idea. Until QT it could not answer
what the trade **costs** — there were no ticks, no order book and no fill model anywhere in the
tree. Every pre-existing check is denominated in dollars, so a $1,000 order in a name showing
three shares at the touch passed all of them.

`app/quant/` adds a real FIFO limit order book with **per-order queue position**, three
strategies, a replay/synthetic feed harness, per-share US equity costs, and PnL attribution
split into spread capture / inventory drift / fees. It is consumed at three tiers:

| Tier | Where | Authority |
|---|---|---|
| **A** — advisory | `quant/verify.py` | none; scores a proposal |
| **B** — pre-trade gate | `risk/engine.py` | `execution_cost_sane` (HARD) + `execution_quality` (SOFT) |
| **C** — autonomous | `quant/execution/` | places its own orders, **simulated venue only** |

**Tier C and Invariant 1.** A strategy loop trading at market-making frequency cannot sit
behind a human approval click. That is resolved by *separation, not compromise*: **nothing
under `app/quant/` may import `app/brokers/`**, enforced by an AST CI gate with its own
control test. No existing invariant was weakened and `brokers/` is untouched.

The result that justifies the build: against a real FIFO book, requoting on every tick sends
~90× the order traffic and wins **74% fewer** passive fills, because each reprice forfeits
queue position. Under the `exp(-k·δ)` fill model this replaces, that comparison is not merely
different — it is *inexpressible*.

Operable from Discord: an execution-quality block on every proposal embed (sourced strictly
from the deterministic `RiskDecision`, never from model output — SEC-17), plus `/quant status`
and `/quant sim`. Full write-up, including the honest limitations, in
[`docs/quant-engine.md`](docs/quant-engine.md).

## How the build works

Phased: **one Workflow call per work package** (WP0 → WP1a → WP1b → … → WP9), with a
human merge checkpoint and a deterministic gate between each. The orchestration manual
is [`docs/ultracode-build-playbook.md`](docs/ultracode-build-playbook.md).

## Run the gates (WP0 → WP9)

```bash
cd app
python -m pip install pydantic pydantic-settings keyring pyyaml jsonschema pyarrow fastapi httpx pytest ruff
```

```bash
python -m pytest -q      # 2,098 pass / 1 skipped locally as of 2026-08-13; includes invariant gates
```

```bash
ruff check .             # clean locally as of 2026-08-13
```

Prove the quant containment is real rather than claimed — the gate should go **red** if you
add a `brokers` import anywhere under `app/quant/`, and green again when you remove it:

```bash
python -m pytest tests/test_invariants.py tests/brokers/test_g7_acceptance.py -q
```

Run a strategy against generated data (no market data required):

```bash
python -m quant --strategy market_maker --events 20000
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

- Provision accounts + keys into the host OS credential store — [`docs/setup/accounts-and-keys.md`](docs/setup/accounts-and-keys.md).
- Re-verify the moving targets before the full-scale/live stage — [`docs/setup/moving-targets-refresh-2026-07-06.md`](docs/setup/moving-targets-refresh-2026-07-06.md).
- **Keep this repository private.** Secrets live in keyring, never in the repo (Invariant 4).
- Not financial advice; personal-use experiment with capped, loss-tolerable downside.

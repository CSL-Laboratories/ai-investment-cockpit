---
title: Accounts & keys — provisioning the cockpit's credentials
type: setup-guide
status: active
last-verified: 2026-07-06
related: [security-architecture, security-hardening-backlog, research-report, research-supplement, build-scaffold-ultracode]
---

# Accounts & keys

How to provision every account and credential the cockpit needs — and, crucially, how
to store them so they never touch the repo, your shell history, a prompt, or a log.

**Golden rule (Invariant 4):** secrets live in **Windows Credential Manager** (via
`keyring`), never in `.env`, source, prompts, chat, or commits. This guide uses
`keyring set …`, which **prompts for the value** (hidden) so it never lands in your
shell history. See [[security-architecture]] §5 and [[security-hardening-backlog]]
SEC-05/SEC-09.

## Provision only what the current phase needs

Don't over-provision early. Nothing here requires a funded live account until Phase 3.

| Phase | Needs | Cost |
|---|---|---|
| **1 — Morning brew (read-only)** | Claude (Max, already have) · Alpaca (news WS + data key) · Finnhub free key · private Discord guild + bot | **$0** marginal |
| **2 — Triage + paper** | + Alpaca **paper** trading keys (same account) | $0 |
| **3 — Gated live** | + Robinhood **Agentic** opt-in + a funded, loss-tolerable Agentic account | your chosen premium only |

## 1. AI runtime — Claude (and optionally Codex)

- **Claude Code (Max 20x, $200/mo)** — you already have this. The copilot drives
  `claude -p` **subprocesses** (not the SDK). Confirm you're logged in:
  `claude` → check it starts authenticated. Headless subscription use is sanctioned but
  **burns your normal plan limits** (the Agent-SDK credit is still paused — re-verified
  2026-07-06), so the design batches and budgets deep-model calls.
- **Codex (ChatGPT plan)** — *optional*, research/coding only. It is **barred from the
  decision/order path** by policy + Invariant 2. If you use it: `codex login --device-auth`.
- The decision-stage agents run with an **empty tool set** and never hold broker
  credentials or an MCP connection ([[security-hardening-backlog]] SEC-03/SEC-06).

## 2. Market data + news (free)

- **Alpaca** — create an account at alpaca.markets, generate an **API key + secret**.
  One key covers the Benzinga **news WebSocket**
  (`wss://stream.data.alpaca.markets/v1beta1/news`, wildcard `{"action":"subscribe","news":["*"]}`),
  free market data (IEX), and the **paper** trading account (Phase 2 — same keys).
- **Finnhub** — create a free account at finnhub.io, copy the **API key** (free tier:
  60 calls/min, real-time US quotes).

## 3. Discord (the operator surface)

1. **Create a NEW private guild** (server) that only you are in — this is where brews,
   proposals, and the audit stream land.
2. developer portal → **New Application** → **Bot** → **Reset/Copy Token**.
3. Enable the **Message Content intent** (needed to read your free-text `Interrogate`
   thread replies — it's the *one* privileged intent this design uses; keep all others
   off). See [[security-architecture]] §9.
4. **Invite the bot** to your private guild (OAuth2 URL, `bot` + `applications.commands`
   scopes, minimal permissions).
5. Note two **non-secret** ids (config, not keyring): your **guild id** and your own
   **operator user id** (enable Developer Mode → right-click → Copy ID). Every
   state-changing interaction is checked against your operator id (single-operator
   allowlist).

## 4. Broker

- **Phase 2 — Alpaca paper:** already covered by the Alpaca keys above (paper is
  API-identical to live). This is where the copilot proves itself for ≥2 weeks.
- **Phase 3 — Robinhood Agentic (live, last):** opt into Agentic Trading in the
  Robinhood app, **fund the dedicated Agentic account with only a loss-tolerable
  amount** (that balance *is* your entire trading downside — funds-level sandbox),
  then connect via MCP:
  `claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading`
  (desktop OAuth). Note (re-verified 2026-07-06): equities + long options are live,
  crypto is announced but not yet; **Robinhood publishes no session-lifetime or rate
  limits**, so budget a re-auth path for 24/7 headless operation
  ([[security-hardening-backlog]] OPS-05). Never wire an unofficial Robinhood API to a
  regular account — that remains a ToS violation; opting into Agentic *is* the
  authorization.

## 5. Store the secrets (keyring — prompts, never shell history)

Run each command; it prompts for the value with the input hidden. The service name
`ai-investment-cockpit` and the names below are what the app reads
(`core.secrets.require_secret("alpaca_key")`, etc.).

```bash
keyring set ai-investment-cockpit alpaca_key
keyring set ai-investment-cockpit alpaca_secret
keyring set ai-investment-cockpit finnhub_key
keyring set ai-investment-cockpit discord_bot_token
```

| Secret name | Holds | Phase |
|---|---|---|
| `alpaca_key` / `alpaca_secret` | Alpaca API key id + secret (data/news + paper) | 1–2 |
| `finnhub_key` | Finnhub free-tier key | 1 |
| `discord_bot_token` | Discord bot token | 1 |
| *(Robinhood MCP)* | via `claude mcp add` OAuth — lives in the MCP session, not keyring | 3 |

**Non-secret config** (env `COCKPIT_*` or a config file, not keyring): `guild_id`,
`operator_user_id`, `mode` (defaults `read_only`).

Verify a round-trip once the app exists:
`python -c "from core.secrets import require_secret; print(require_secret('finnhub_key'))"`
→ prints `Redacted('***')` (never the value).

## 6. Security do / don't

- **Do** keep this repo **private**; run the box as a service; enable **BitLocker**;
  run the agents under a **separate low-privilege Windows account** ([[security-hardening-backlog]]
  SEC-05, OPS-06); keep **redaction default-on** for Discord.
- **Don't** ever paste a secret into a prompt, chat, commit, or `export`; don't put the
  main portfolio on the Robinhood login the agent connects to (it reads **all** accounts
  — keep the main portfolio at a different broker); don't fund the live Agentic account
  before the paper soak passes the gate.

## 7. Re-verify before Phase 3

The externally-verified facts are moving targets. As of **2026-07-06** the two re-run
targets are **unchanged** (Anthropic SDK credit still paused; Robinhood scope holds) —
see [[moving-targets-refresh-2026-07-06]]. Re-verify again immediately before funding a
live account: Anthropic SDK-credit status, Robinhood Agentic scope/limits/session TTLs,
and per-broker post-PDT house rules.

## Checklist

- [ ] Claude Code (Max) authenticated
- [ ] Alpaca account + API key/secret
- [ ] Finnhub free key
- [ ] Private Discord guild + bot (Message Content intent) + bot invited
- [ ] `keyring set` all four secrets under `ai-investment-cockpit`
- [ ] Guild id + operator user id recorded (config)
- [ ] Repo is private; BitLocker on
- [ ] *(Phase 3 only)* Robinhood Agentic opt-in + funded, loss-tolerable account + MCP connected

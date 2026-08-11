---
title: Accounts & keys — provisioning the cockpit's credentials
type: setup-guide
status: live
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
- **Codex CLI (ChatGPT plan)** — *optional*, supported for explicit research/coding jobs.
  The cockpit invokes the official non-interactive `codex exec` CLI, not an OpenAI SDK or a
  copied bearer token. Authenticate the CLI once with `codex login` (or
  `codex login --device-auth` for a headless setup), then confirm `codex exec --help` works
  in the same Windows account that runs the cockpit. No Codex API key is stored in this
  project's keyring. Codex remains **barred from the auto decision/order path**; the runner
  pins Codex jobs to `--sandbox read-only` and refuses a Codex decision-stage spec.
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
keyring set ai-investment-cockpit cockpit_api_key
```

| Secret name | Holds | Phase |
|---|---|---|
| `alpaca_key` / `alpaca_secret` | Alpaca API key id + secret (data/news + paper) | 1–2 |
| `finnhub_key` | Finnhub free-tier key — serves BOTH the news poller and the earnings calendar | 1 |
| `discord_bot_token` | Discord bot token | 1 |
| `cockpit_api_key` | The `X-API-Key` guarding the loopback orchestrator API — **you invent it** (`python -c "import secrets;print(secrets.token_urlsafe(32))"`), it is not a vendor credential. Unset ⇒ **every** route 401s, including `/status` (fails closed, never open) | 0.5 |
| *(Robinhood MCP)* | via `claude mcp add` OAuth — lives in the MCP session, not keyring | 3 |

**Spelling is load-bearing.** These names are read verbatim by `core.secrets`; a
hyphen-for-underscore slip leaves the feature silently dark rather than erroring
(`app/tests/system/test_rw01_market_day.py` pins the constants against this table).
`audit-hmac-key` is deliberately absent: the app mints it into keyring itself on first
`compose()` — never create it by hand.

**Non-secret config** (env `COCKPIT_*` or a config file, not keyring): `guild_id`,
`operator_user_id` (as `COCKPIT_OPERATOR_IDS`), `mode` (defaults `read_only`), the static
operator-approved public-equity universe (`COCKPIT_WATCHLIST`, comma/space separated), and the
delivery channel ids — `COCKPIT_BRIEF_CHANNEL_ID` (your `#morning-brew`),
`COCKPIT_ALERTS_CHANNEL_ID`, `COCKPIT_PROPOSALS_CHANNEL_ID`,
`COCKPIT_SYSTEM_CHANNEL_ID`, `COCKPIT_AUDIT_LOG_CHANNEL_ID`. Each is independently
optional; an unmapped channel fails closed (reported undelivered) rather than being
redirected into another channel. See [[runbooks/rw01-live-smoke]] for the full bring-up.

The watchlist is validated and bounded at startup, read-only while the process runs, and feeds
both intraday relevance and the deterministic symbol allowlist. There is intentionally no API or
agent tool that can add a symbol; an operator edits the non-secret config and restarts the cockpit.

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

## 7. Congressional copy-trading — `COCKPIT_CONGRESS_*` (RW-16 / RW-17)

**No credential is involved.** Both feeds are public government publications, so nothing here
goes in keyring — these are non-secret `COCKPIT_*` env knobs like `COCKPIT_WATCHLIST`, and the
annotated defaults live in `app/.env.example`. The design and its limits are in
[[congress-copytrade]]; operating the auto-execution lane is [[runbooks/congress-autoexec]].

> **Before you set any of this, read [[congress-copytrade]] §8.** The feature is
> **paper-complete and live-blocked**: `producing_runtime="deterministic:copytrade"` is refused
> by the mode ladder in `review_live` / `auto_veto` / `auto_full`, so in any live or auto rung
> it emits *nothing*. `RW-16-LIVE` / SEC-08 is still open, so no setting on this page can make
> live capital reachable.
>
> Turning `COCKPIT_CONGRESS_ENABLED` on gives you the **ingest** feed (awareness), not a trading
> loop. The trading loop is the separate, off-by-default `COCKPIT_CONGRESS_AUTOEXEC` below — and
> it **replaces your approval tap**.

### Provisioning

| Knob | Default | What it does |
|---|---|---|
| `COCKPIT_CONGRESS_ENABLED` | `false` | The master switch. Off ⇒ the feed reports itself unprovisioned with that reason, rather than silently missing |
| `COCKPIT_CONGRESS_USER_AGENT` | *(unset)* | **Required by the House Clerk.** Unset ⇒ the feed stays silent and unprovisioned rather than sending an anonymous scraper. Use a real contact string, e.g. `ai-investment-cockpit (you@example.com)` |
| `COCKPIT_CONGRESS_RATE_PER_SEC` | `0.5` | Politeness floor. Neither the Clerk nor eFD publishes a documented cap, so this is a limit **we** choose, not one we are trusted to respect |
| `COCKPIT_CONGRESS_SENATE_ENABLED` | `false` | The Senate eFD provider, separately opt-in: its search UI is agreement-gated and its documents are far less machine-readable than the Clerk's |

### The follow list — an allowlist, and the first gate

`COCKPIT_CONGRESS_FOLLOW` is **`;`-separated**, not comma-separated, because a comma tears
every `"Last, First"` spelling in half. Entries normalize to a `last-first` slug, so
honorifics, suffixes, middle initials, case and punctuation all collapse together.

```dotenv
# Worked example — three filers, three different spellings, all legal:
COCKPIT_CONGRESS_FOLLOW=Pelosi, Nancy; Hon. Ro Khanna; GREENE, MARJORIE T.
#   -> slugs: pelosi-nancy ; khanna-ro ; greene-marjorie
```

Three things to know:

- **empty follows NOBODY.** There is deliberately no "everyone" value — the feed carries every
  member of both chambers, and a policy defaulting to all of them would turn a config oversight
  into several hundred positions.
- **it is validated at STARTUP.** A misspelled entry fails at boot rather than becoming a
  feature that quietly copies no one. Max 50 entries.
- **a bare initial does not match a full name.** `Pelosi, N.` is *not* `Pelosi, Nancy`; an
  ambiguous entry fails closed rather than matching broadly.

### The copy policy

| Knob | Default | Bound | Notes |
|---|---|---|---|
| `COCKPIT_CONGRESS_MAX_AGE_DAYS` | `45` | 1–365 | The staleness gate, measured from the **trade** date. 45 is the STOCK Act deadline: "act on a filing that arrived on time, refuse one that did not." **The most effective knob here** |
| `COCKPIT_CONGRESS_STALENESS_DECAY` | `0.5` | 0–1 | Linear size decay across the window. `0` prices a 44-day-old signal as if it were fresh — allowed, not advised |
| `COCKPIT_CONGRESS_COPY_FRACTION` | `0.02` | >0, ≤1 | Fraction of **your** portfolio for a baseline (weight 1.0) copied buy |
| `COCKPIT_CONGRESS_MAX_NOTIONAL_PER_TRADE` | `500` | >0 | Hard per-intent ceiling, after every other factor. Bounds a policy misconfiguration *before* the risk engine's own caps |
| `COCKPIT_CONGRESS_MIN_NOTIONAL_PER_TRADE` | `25` | ≥0 | Below this the intent is **dropped**, not rounded up — a $3 order is all cost and still spends a human approval |
| `COCKPIT_CONGRESS_MIN_AMOUNT_LOW` | `1001` | ≥0 | Ignore disclosures whose LOW bucket bound is under this. The bottom PTR bucket starts at $1,001, so the default admits everything |
| `COCKPIT_CONGRESS_MIRROR_SELLS` | `true` | — | Copy disclosed SALES as exits of positions you actually hold. Independent of buys on purpose: mirroring only the buys builds a portfolio that never sells |

### Options — the non-1:1 rule

| Knob | Default | Bound | Notes |
|---|---|---|---|
| `COCKPIT_CONGRESS_OPTIONS_POLICY` | `underlying` | `underlying` \| `ignore` | There is no "copy the contract" value: the risk engine has no options asset type at all |
| `COCKPIT_CONGRESS_OPTIONS_HAIRCUT` | `0.25` | **>0 and ≤0.5** | Options-derived intents are sized at this multiple of an equivalent equity copy — *both* the raw size and the ceiling. The `0.5` cap is arithmetic, not taste: above it, share quantization (`floor(notional/price)`) can collapse an options order onto the equity one. `1` is refused outright |
| `COCKPIT_CONGRESS_OPTIONS_MIN_DAYS_TO_EXPIRY` | `30` | **≥1** | Drop an options-derived intent whose disclosed expiry is nearer than this. `0` is refused: it would admit a 0-DTE contract as a fresh multi-week long in the underlying |

### The dynamic copy universe (SEC-15)

| Knob | Default | Bound | Notes |
|---|---|---|---|
| `COCKPIT_CONGRESS_MAX_UNIVERSE_SYMBOLS` | `40` | 1–200 | How many disclosure-promoted symbols may be live at once. At capacity a **new** symbol is refused and counted; an incumbent is never evicted |
| `COCKPIT_CONGRESS_UNIVERSE_TTL_DAYS` | `120` | 1–1095 | How long a symbol survives without a fresh disclosure. Expiry can only ever prevent a new **buy** — never block an exit |

Promotion is **not** approval to trade: it only moves a symbol from "the risk engine rejects
this on sight" to "the risk engine may evaluate it on its merits". The promoted set lives in
the HMAC-signed audit chain, not a table, precisely so a local DB writer cannot widen the
allowlist. See [[congress-copytrade]] §5.

### ⚠️ Zero-touch PAPER auto-execution — `COCKPIT_CONGRESS_AUTOEXEC_*` (RW-17)

**This replaces your approval tap.** With it on, a congressional copy trade is notified to you,
waits out a cancel window, and then **executes on the paper transport with no action from you**.
Your only lever is `/cancel` inside that window; you never have to act for a trade to proceed.

**PAPER ONLY, by construction.** The lane resolves only at `mode == paper` (re-checked under the
lock at the instant of every trade), submits only through the paper-scoped `OrderGateway`, and
`brokers/auto_paper_submit.auto_paper_place` refuses anything else. `auto_veto` / `auto_full` are
**live** rungs; none of these knobs touches, arms, or routes around one. It cannot move real
money. Operate it from [[runbooks/congress-autoexec]] — it has the kill switch, every refusal
reason, and how to tell a quiet day from a broken lane.

| Knob | Default | Bound | Notes |
|---|---|---|---|
| `COCKPIT_CONGRESS_AUTOEXEC` | `false` | — | The master switch, and an explicit act — never something a deployment acquires by upgrading. Off ⇒ **no copy-cycle job and no resolve job are registered at all** |
| `COCKPIT_CONGRESS_AUTOEXEC_WINDOW_SECONDS` | `300` | **≥60**, ≤86400 | How long a queued trade waits, notified, before it fires. The floor **is** the 60s resolve tick: a window shorter than the tick that resolves it is not shorter, it is unpredictable. The real window is this value plus at most one tick — the error always runs in your favour |
| `COCKPIT_CONGRESS_AUTOEXEC_TICKET_MAX_AGE_SECONDS` | `21600` (6h) | 300–604800, **and > the window** | Upper bound on a ticket's life. Most refusals here are *transient* and keep the ticket alive (a HALT clears, a breaker resets, tomorrow's budget is fresh), so without this a ticket queued during a Monday killswitch would still be counting down a Monday-priced order on Friday. At this age it is refused by name (`ticket_expired`), notified and counted |

**The four budget rails.** A human approving each trade was, incidentally, what bounded daily
damage; these are its replacement, so the defaults are deliberately tighter than what you would
approve by hand. Every number is re-derived from the **HMAC-signed audit chain** on each check,
never from a counter — a restart does not reset a rail, and an unreadable chain means the budget
is **exhausted**, not zero. Exhausting one is a named, notified refusal, never a silent stop.

| Knob | Default | Bound | Notes |
|---|---|---|---|
| `COCKPIT_CONGRESS_AUTOEXEC_MAX_DAILY_NOTIONAL` | `500` | >0 | Total $ the lane may commit in one **UTC** day. The default is exactly one `COCKPIT_CONGRESS_MAX_NOTIONAL_PER_TRADE`: out of the box the lane may open one full-size position per day and then stops |
| `COCKPIT_CONGRESS_AUTOEXEC_MAX_TRADES_PER_DAY` | `3` | 1–50 | A count rail as well as a dollar rail, because they fail differently: this one bounds how many independent mistakes one day can make. `0` is refused — a feature you switched on that can never trade is the silent death this design refuses |
| `COCKPIT_CONGRESS_AUTOEXEC_MAX_OPEN_POSITIONS` | `10` | 1–100 | The **standing** bound (not daily): how many distinct names the lane may hold at once. Without it a $500/day rail still accumulates an unbounded book over a quarter. It can never block an **exit**, and lowering it stops new names rather than stranding held ones |
| `COCKPIT_CONGRESS_AUTOEXEC_MAX_NOTIONAL_PER_SYMBOL_PER_DAY` | `250` | >0 | Per-symbol daily concentration — half the daily budget by default, so one hot ticker cannot eat the whole day |

**Startup refuses a lane that could never trade.** With `COCKPIT_CONGRESS_AUTOEXEC=true`, each of
these is a **boot failure**, not a quiet no-op — because each is a lane that boots clean, copies
nobody, and leaves you waiting on notifications that were never going to come:

- `COCKPIT_CONGRESS_ENABLED=false` — nothing is ever ingested;
- an empty `COCKPIT_CONGRESS_FOLLOW` — an empty allowlist follows nobody;
- a daily **or** per-symbol rail below `COCKPIT_CONGRESS_MIN_NOTIONAL_PER_TRADE` — every trade
  the sizing engine may emit is refused on a rail;
- a ticket max age at or below the cancel window — every ticket expires before its own window
  closes.

**Every notification channel must be mapped.** The lane posts `queued` to `#proposals`,
`submitted`/`filled`/`cancelled`/`refused` to `#alerts` and the daily digest to `#brief`, so
`COCKPIT_PROPOSALS_CHANNEL_ID`, `COCKPIT_ALERTS_CHANNEL_ID` **and** `COCKPIT_BRIEF_CHANNEL_ID`
must all be set. A proposals-only configuration is **refused at composition** rather than armed:
a lane that can announce a trade and then never report what it did is not composed.

### Running the gates on a machine whose `python` is not the project interpreter

[[CLAUDE]] gives the gate command as `python -m pytest`. On a box where `python`/`python3` on
`PATH` is an older interpreter (3.9 will not even import the models), use the project venv
explicitly — the commands are otherwise identical, and **both must be green**:

```bash
cd app
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
```

## 8. Re-verify before Phase 3

The externally-verified facts are moving targets. As of **2026-07-06** the two re-run
targets are **unchanged** (Anthropic SDK credit still paused; Robinhood scope holds) —
see [[moving-targets-refresh-2026-07-06]]. Re-verify again immediately before funding a
live account: Anthropic SDK-credit status, Robinhood Agentic scope/limits/session TTLs,
and per-broker post-PDT house rules.

## Checklist

- [ ] Claude Code (Max) authenticated
- [ ] *(Optional)* Codex CLI installed and `codex login` authenticated for research/coding jobs
- [ ] Alpaca account + API key/secret
- [ ] Finnhub free key
- [ ] Private Discord guild + bot (Message Content intent) + bot invited
- [ ] `keyring set` all five secrets under `ai-investment-cockpit`
- [ ] Guild id + operator user id + `#morning-brew` channel id recorded (config)
- [ ] Repo is private; BitLocker on
- [ ] *(Phase 3 only)* Robinhood Agentic opt-in + funded, loss-tolerable account + MCP connected

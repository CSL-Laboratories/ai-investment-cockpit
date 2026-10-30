---
title: Accounts & keys — provisioning the cockpit's credentials
type: setup-guide
status: live
last-verified: 2026-08-17
repository-state-reviewed: 2026-08-17
related: [security-architecture, security-hardening-backlog, research-report, research-supplement, build-scaffold-ultracode]
---

# Accounts & keys

How to provision every account and credential the cockpit needs — and, crucially, how
to store them so they never touch the repo, your shell history, a prompt, or a log.

**Golden rule (Invariant 4):** secrets live in the **host OS credential store** via
`keyring` (Windows Credential Manager, macOS Keychain, or Linux SecretService), never in
`.env`, source, prompts, chat, or commits. This guide uses
`keyring set …`, which **prompts for the value** (hidden) so it never lands in your
shell history. See [[security-architecture]] §5 and [[security-hardening-backlog]]
SEC-05/SEC-09.

The current deployment profile is Windows. macOS uses Keychain automatically; a real headless
Linux deployment must provision SecretService before cockpit secrets can be stored. The tests'
in-memory keyring backend is not suitable for deployment.

## Provision only what the current phase needs

Don't over-provision early. Nothing here requires a funded live account until the separately
authorized full-scale deployment phase in [[PLAN]].

| Lifecycle stage | Needs | Cost |
|---|---|---|
| **Offline development** | No vendor credential required for deterministic fixtures/tests | **$0** |
| **Online testing → paper MVP/beta** | Claude decision runtime · SEC descriptive User-Agent (non-secret) · Alpaca data/paper keys · Finnhub key · private Discord guild + bot · dedicated S3 Object Lock audit bucket | plan/subscription usage plus small AWS storage/request charges |
| **Full-scale deployment** | Robinhood discovery identity only after `DEC-05`; funding only after `RW-18`, the paper evidence window, and all pre-live gates | separately authorized loss-tolerable capital only |

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
  in the same host account that runs the cockpit. No Codex API key is stored in this
  project's keyring. Codex remains **barred from the auto decision/order path**; the runner
  pins Codex jobs to `--sandbox read-only` and refuses a Codex decision-stage spec.
- The decision-stage agents run with an **empty tool set** and never hold broker
  credentials or an MCP connection ([[security-hardening-backlog]] SEC-03/SEC-06).

## 2. Market data + news (free)

- **Alpaca** — create an account at alpaca.markets, generate an **API key + secret**.
  One key covers the Benzinga **news WebSocket**
  (`wss://stream.data.alpaca.markets/v1beta1/news`, wildcard `{"action":"subscribe","news":["*"]}`),
  free market data (IEX), and the **paper** trading account used for online testing, MVP, and
  beta (same keys).
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

The operator bot may retain `Administrator`. That means its rolling `#audit-log` posts are an
operator-visible mirror, not append-only authority: Discord authors can mutate their own messages
and Administrator bypasses channel overwrites. W-24 separately fetches an exact message, binds the
authenticated bot author, and rejects edits/deletion; it still does not satisfy SEC-11.

### 3a. S3 Object Lock (SEC-11 authority)

Create a dedicated private S3 bucket with Versioning, Object Lock, and bucket-default
`COMPLIANCE` retention for seven years. Enable Block Public Access, Bucket owner enforced object
ownership, and default SSE-S3 encryption. Create a dedicated runtime IAM principal limited to the
one audit prefix: put/get object versions, list bucket versions, and read bucket/object retention.
Explicitly deny delete/version-delete, retention/legal-hold mutation, bucket Object Lock/versioning
mutation, and governance bypass. Record the non-secret bucket name, region, and 12-digit account
id in config. Store only its access-key pair in keyring. Follow
[[runbooks/w25-worm-audit-witness]]; mocked tests do not close SEC-11.

## 4. Broker

- **Paper MVP/beta — Alpaca paper:** already covered by the Alpaca keys above. This is the
  only broker authority currently supported for development and evidence.
- **Full-scale deployment — Robinhood Agentic (live, last):** do **not authenticate yet**.
  First resolve `DEC-05`'s read-all-account exposure. Then `RW-18` performs separately
  authorized, no-capital discovery and places zero orders. Only after the ≥6-month paper bar,
  SEC-11/all pre-live gates, and a separate funding authorization may `W17` provision a
  loss-tolerable dedicated account and a deterministic client behind `OrderGateway`. Re-verify
  the endpoint, OAuth flow, tools, session/rate behavior, and supported products at that time;
  the 2026-07-06 snapshot is not permission to connect or fund.

## 5. Store the secrets (keyring — prompts, never shell history)

Run each command; it prompts for the value with the input hidden. The service name
`ai-investment-cockpit` and the names below are what the app reads
(`core.secrets.require_secret("alpaca_key")`, etc.).

```bash
keyring set ai-investment-cockpit alpaca_key
keyring set ai-investment-cockpit alpaca_secret
keyring set ai-investment-cockpit finnhub_key
keyring set ai-investment-cockpit jev_api_key
keyring set ai-investment-cockpit discord_bot_token
keyring set ai-investment-cockpit cockpit_api_key
keyring set ai-investment-cockpit witness_s3_access_key_id
keyring set ai-investment-cockpit witness_s3_secret_access_key
```

| Secret name | Holds | Lifecycle use |
|---|---|---|
| `alpaca_key` / `alpaca_secret` | Alpaca API key id + secret (data/news + paper) | online testing through beta |
| `finnhub_key` | Finnhub free-tier key — serves BOTH the news poller and the earnings calendar | online testing through beta |
| `jev_api_key` | TypeSafe Jev bearer token for paper/read-only decisions. Absent means those decisions stay on Claude | paper testing onward |
| `discord_bot_token` | Discord bot token | online testing onward |
| `cockpit_api_key` | The `X-API-Key` guarding the loopback orchestrator API — **you invent it** (`python -c "import secrets;print(secrets.token_urlsafe(32))"`), it is not a vendor credential. Unset ⇒ **every** route 401s, including `/status` (fails closed, never open) | local composed testing onward |
| `witness_s3_access_key_id` / `witness_s3_secret_access_key` | Dedicated least-privilege S3 Object Lock witness principal; never the general AWS/admin credential | SEC-11 activation onward |
| *(Robinhood MCP)* | external MCP/CLI session store, not cockpit keyring | `RW-18` no-capital discovery/full-scale only |

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

The WORM witness also needs non-secret `COCKPIT_WITNESS_S3_BUCKET`,
`COCKPIT_WITNESS_S3_REGION`, `COCKPIT_WITNESS_S3_EXPECTED_BUCKET_OWNER`, and optionally the
pinned prefix/minimum-retention settings shown in `app/.env.example`. Partial configuration
installs no trusted adapter.

The watchlist is validated and bounded at startup, read-only while the process runs, and feeds
both intraday relevance and the deterministic symbol allowlist. There is intentionally no API or
agent tool that can add a symbol; an operator edits the non-secret config and restarts the cockpit.

Verify a round-trip once the app exists:
`python -c "from core.secrets import require_secret; print(require_secret('finnhub_key'))"`
→ prints `Redacted('***')` (never the value).

## 6. Security do / don't

- **Do** keep this repo **private**; run the box as a service; enable host full-disk encryption
  (BitLocker/FileVault/LUKS); run agents under a **separate low-privilege OS account**
  ([[security-hardening-backlog]] SEC-05, OPS-06); keep **redaction default-on** for Discord.
- **Don't** ever paste a secret into a prompt, chat, commit, or `export`; don't put the
  main portfolio on the Robinhood login the agent connects to (it reads **all** accounts
  — keep the main portfolio at a different broker); don't fund the live Agentic account
  before the paper soak passes the gate.

## 7. Re-verify before any Robinhood/live stage

The externally-verified facts are moving targets. As of **2026-07-06** the two re-run
targets are **unchanged** (Anthropic SDK credit still paused; Robinhood scope holds) —
see `setup/moving-targets-refresh-2026-07-06.md` (moved to csl-research history, CSL-588). Re-verify again immediately before funding a
live account: Anthropic SDK-credit status, Robinhood Agentic scope/limits/session TTLs,
and per-broker post-PDT house rules.

## Checklist

- [ ] Claude Code (Max) authenticated
- [ ] *(Optional)* Codex CLI installed and `codex login` authenticated for research/coding jobs
- [ ] Alpaca account + API key/secret
- [ ] Finnhub free key
- [ ] Private Discord guild + bot (Message Content intent) + bot invited
- [ ] `keyring set` all seven secrets under `ai-investment-cockpit`
- [ ] Guild id + operator user id + `#morning-brew` channel id recorded (config)
- [ ] Dedicated S3 Object Lock Compliance bucket + least-privilege witness identity provisioned
- [ ] Repo is private; host full-disk encryption enabled
- [ ] *(RW-18 only, after DEC-05)* isolated/no-capital Robinhood discovery identity authorized
- [ ] *(W17 only, after every pre-live gate)* funded, loss-tolerable account separately authorized

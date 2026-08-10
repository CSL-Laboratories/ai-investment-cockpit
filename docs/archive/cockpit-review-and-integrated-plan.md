---
title: Cockpit plan review — CLI-vs-SDK verdict and integrated build plan
type: design-review
status: archive
last-verified: 2026-07-02
related: [README, research-report, research-supplement, cockpit-plan-original]
---

# Review: "Local AI Investment Cockpit" vs. the verified research

**Overall verdict: adopt the plan.** It independently converges with the
research-derived architecture on every load-bearing decision — single central host
with thin clients, deterministic risk engine with no LLM authority, human approval
gate, paper-first, append-only audit log, schema-validated agent outputs,
trigger-based escalation, kill switch. The deltas below are corrections and
strengthenings from verified facts, not disagreements of shape.

---

## 1. The CLI-vs-SDK question: yes, `claude -p` / `codex exec` is enough — and right

**Contractually identical on the Claude side.** Anthropic's help article (verified
live 2026-07-02) treats the three surfaces as one covered category, in one
sentence: *"Claude Agent SDK, `claude -p`, and third-party app usage still draw
from your subscription's usage limits."* Subprocess-wrapping `claude -p` is exactly
as sanctioned as the SDK — and exactly as un-subsidized while the SDK credit
remains paused (2026-06-15).

**Technically near-identical.** The Agent SDK is a wrapper that spawns the Claude
Code CLI under the hood. `claude -p` from an orchestrator is the same runtime,
same auth, same session store — minus in-process conveniences.

**Actually safer on the OpenAI side.** The verified caveat on Codex was that
ChatGPT-plan sanction covers *OpenAI's own clients* — extracting the OAuth token
from `~/.codex/auth.json` into unrelated code is not sanctioned. Subprocess-driving
the official `codex exec` binary **is** using their client. CLI-wrapping keeps the
system inside the sanctioned surface on both vendors; an SDK-less design is not a
compromise here, it's the compliant path.

**What the CLI approach gives up** (revisit only if these bite):
in-process streaming and typed message objects; defining custom tools/MCP servers
programmatically per-call; lifecycle hooks; cheap session forking. Process startup
costs a few seconds per invocation — irrelevant for a triage loop that fires every
15–30 minutes. Multi-turn continuity is handled with `--resume <session-id>` /
`--continue` instead of an object handle. If bidirectional streaming or dynamic
mid-turn tools are ever needed, migration to the SDK is low-cost because prompts
and schemas carry over unchanged.

**Flag-level corrections to the draft's command lines** (verify against the
installed CLI version before coding them in):

| Draft invocation | Status |
|---|---|
| `claude -p --model haiku --output-format json` | Real; `--output-format stream-json` also available for streaming |
| `--json-schema ...` | **Unverified flag** — do not assume it exists. The runner's own JSON-Schema validation (already in the plan, with repair-then-quarantine) is the reliable enforcement layer regardless; keep it authoritative even if a native flag exists |
| `--effort high` | **Unverified as a CLI flag** (effort is a session setting in current builds) — check `claude --help`; fall back to model choice + prompt if absent |
| `claude -p "/goal ..."` | `/goal` is **not a stock command** — implement as a custom skill/slash command, or express the bound in the prompt + `--max-turns` |
| `/loop` for short-lived monitoring | Exists, but runs inside an interactive session and burns the same plan limits with less predictability — prefer orchestrator-scheduled `claude -p` calls; agree with the draft that durable monitoring stays in the orchestrator |
| `claude remote-control` | Verify availability on the plan; the dashboard + push notifications already cover the steering use case, so treat this as optional sugar |
| `codex exec --json --output-schema ...` | Consistent with documented Codex CLI; headless auth via `codex login --device-auth` is officially documented |

Also containment flags worth adding to every `claude -p` call: `--max-turns`, an
explicit `--allowedTools` list (the trading agent needs almost none), and a pinned
working directory. Never pass broker credentials through prompts — the agent
reaches the broker only via the MCP connection.

## 2. Corrections from verified research (change these in the plan)

1. **Risk engine — replace the PDT check.** The draft lists "day-trading/margin
   constraints." The PDT designation and $25k minimum were **eliminated effective
   2026-06-04** (FINRA Rule 4210 amendments). Model the replacement instead:
   intraday margin deficit monitoring, the Reg-T $2,000 margin minimum,
   cash-account settlement rules, and 90-day-restriction avoidance. See
   [[research-supplement]] §1.
2. **Name the broker path.** Paper = **Alpaca** (BrokerChooser #1 US algo broker
   2026; API-identical paper account; 200 req/min). Live = **Robinhood Agentic
   account** via the official MCP server — opting in is the authorization.
   Never wire unofficial Robinhood APIs (robin_stocks-class) to a regular account;
   that remains a ToS violation.
3. **Name the data stack.** Alpaca Benzinga news WebSocket
   (`wss://stream.data.alpaca.markets/v1beta1/news`, wildcard
   `{"action":"subscribe","news":["*"]}`) as the `market-ingestor` event stream;
   Finnhub free tier (60 calls/min + 30/s burst, real-time US quotes, `/news` with
   `minId` cursor) for polling. $0/month.
4. **Keep Codex off the order path.** OpenAI's usage policies bar automating
   high-stakes financial decisions without human review, and the ChatGPT-Agent
   companion policy says not to automate stock trading at all on that surface. The
   draft's split (Codex = research/coding/specialist) is right — make it a hard
   rule: only the risk-engine + approval-center path can reach order placement,
   and the proposing model never executes.
5. **Add usage-window backpressure (missing service-level concern).** Throughput
   on both vendors is variable and opaque (Pro windows reported depleting in ~20
   minutes under heavy tasks; Max 20x headless burns normal limits while the SDK
   credit is paused). The orchestrator needs: detection of limit-hit responses, a
   degraded "watch-only" mode that queues non-urgent work until the 5-hour window
   resets, scheduled placement of the two deep-model-heavy jobs (morning brief,
   end-of-day review) at fixed times, and a hard daily budget for deep-model
   escalations (start: 5/day). Batch triage into **one call per interval digesting
   all accumulated items** — never per-headline.
6. **Escalation trigger design.** No open-source precedent ships a dynamic
   cheap→deep escalator (TradingAgents' split is static role assignment). Build it
   as: deterministic pre-filters first (price-move %, holdings/watchlist entity
   match, earnings-calendar proximity) → cheap-model scoring of survivors →
   deep-model only above threshold. RouteLLM's result (≤85% cost cut at 95%
   quality) is the economics justification, not a library to import.

## 3. Smaller strengthenings

- **Windows host:** prevent sleep during market hours (`powercfg` scheme or
  presentation mode) — Task Scheduler alone won't wake a sleeping box reliably for
  intraday polling. Run the orchestrator as a service (e.g., NSSM) rather than a
  login task so a reboot mid-day self-heals.
- **Secrets:** broker/API keys in Windows Credential Manager (DPAPI), not env
  files; the audit log should record key *names*, never values.
- **Remote access:** Tailscale (or equivalent) for the dashboard; never an open
  LAN port — matches the draft's intent, naming a concrete default.
- **Audit log:** consider hash-chaining entries (each row stores the previous
  row's hash) — cheap tamper-evidence for the day you want to trust a
  paper-trading replay.
- **Privacy note:** the Robinhood MCP grants read access to ALL accounts, and that
  data flows to the AI provider. If that's unacceptable, keep the main portfolio
  at a different broker and let the agent see only the Agentic account.
- **Re-verify at build time:** Anthropic SDK-credit un-pause; Robinhood beta scope
  (the "equities-only" and "waitlisted/desktop-only" claims were refuted 0-3 —
  moving target); per-broker post-PDT house rules.

## 4. Integrated phase plan (research architecture × cockpit draft)

| Phase | Scope | Exit criterion |
|---|---|---|
| **0. Harness** | `agent-runner` wrapping `claude -p`/`codex exec` with schema validation, retries, budget tracking; SQLite audit log; usage-window backpressure | Malformed output cannot enter the pipeline (test); limit-hit degrades gracefully (test) |
| **1. Morning Brew (read-only)** | Alpaca news WS + Finnhub ingest; cheap-model classify (`ignore/watch/material/urgent`); one deep-model brief; dashboard + desktop/push notify | Two weeks of daily briefs; measured burn comfortably inside the 20x window |
| **2. Triage + escalation + paper** | Intraday batched triage loop; deterministic→cheap→deep escalator; TradeProposal + risk-engine (post-PDT rules) + approval-center; **Alpaca paper** execution | ≥2-week paper replay with full audit trail; zero unapproved executions (test) |
| **3. Gated live, small fund** | Robinhood Agentic account, funded with a loss-tolerable amount; approval gate wired to MCP order tools; daily live-mode unlock that expires at close; kill switch | Live path unreachable without mode+risk+approval (test); first live cycle audited end-to-end |

Test-plan additions to the draft's list: simulate a mid-loop usage-limit hit
(orchestrator must enter watch-only, not drop events); verify the deep-model daily
budget is enforced; verify the agent's `--allowedTools` set cannot touch the
filesystem outside its sandbox; verify secrets never appear in prompts or the
audit log.

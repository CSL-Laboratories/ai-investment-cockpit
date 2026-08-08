---
title: Draft 2 — Discord-centralized control plane with graduated autonomy
type: design-draft
status: draft
last-verified: 2026-07-02
related: [README, cockpit-review-and-integrated-plan, research-report, research-supplement]
---

# Draft 2: local model API + Discord control plane + graduated autonomy

Extends [[cockpit-review-and-integrated-plan]]. Changes in this draft:

1. The orchestrator grows a **local HTTP API** — the single source of truth that
   invokes the models on the workstation (`claude -p` / `codex exec` subprocesses).
2. **Discord becomes the operator surface**: morning brews, alerts, trade
   proposals with approve/reject buttons, slash-command customization, and the
   full audit stream — centralized in one private guild.
3. **Graduated autonomy**: the human gate is a mode, not a hard-wired stage —
   from explicit approval down to full automation with Discord log-only, unlocked
   by a measured performance gate and guarded by circuit breakers.
4. **Interactive headless sessions**: Discord thread replies are piped back into
   the originating `claude -p` session (`--resume`) or into fresh-context
   interrogations — you can talk to the agent that made the call.

This mirrors the Golden Stallion architecture (FastAPI backend as single source of
truth + Discord bot + dashboard as thin clients), so every pattern here is one the
operator has already built and run in production.

## Topology

```text
                    ┌──────────────────────────────────────┐
                    │        Discord (private guild)        │
                    │ #morning-brew #proposals #alerts      │
                    │ #positions #audit-log #system         │
                    └──────▲──────────────────┬────────────┘
                           │ embeds, threads  │ buttons, slash cmds,
                           │                  │ thread replies
                    ┌──────┴──────────────────▼────────────┐
                    │      discord-bridge (bot process)     │  outbound WebSocket
                    │      just another API client          │  only — zero inbound
                    └──────▲──────────────────┬────────────┘  ports on the box
                           │ REST (127.0.0.1) │
              ┌────────────┴──────────────────▼────────────┐
              │        orchestrator API (FastAPI)           │
              │ /jobs /brief /proposals /mode /limits /kill │
              ├──────────────┬──────────────┬───────────────┤
              │ agent-runner │ risk-engine  │ audit-log     │
              │ claude -p /  │ deterministic│ SQLite,       │
              │ codex exec   │ NO LLM       │ hash-chained  │
              └──────┬───────┴──────┬───────┴───────────────┘
                     │              │
         ┌───────────▼────┐  ┌──────▼───────────────────────┐
         │ market-ingestor│  │ broker connectors             │
         │ Alpaca news WS │  │ Alpaca paper API              │
         │ Finnhub poll   │  │ Robinhood Agentic MCP (live)  │
         └────────────────┘  └──────────────────────────────┘
```

Why Discord-as-surface wins over the earlier dashboard-first design:

- **No inbound exposure.** The bot holds an outbound WebSocket to Discord; the
  workstation opens no ports and needs no tunnel/VPN. The 127.0.0.1 API stays
  loopback-only.
- **Push, history, and mobile for free.** Notifications, threaded discussion,
  searchable audit history, and phone access all come from Discord's own apps.
- **One place.** Brief, alerts, approvals, interrogation, customization, and logs
  in one guild instead of dashboard + push service + log viewer.

The PWA dashboard from draft 1 becomes optional Phase-4 polish (rich
portfolio/risk visualization), not core infrastructure.

## The orchestrator API (workstation-local)

FastAPI on `127.0.0.1`, API-key auth, consumed by the Discord bridge (and later
the PWA). Core resources:

| Endpoint | Purpose |
|---|---|
| `POST /jobs` | Enqueue an agent run: `{runtime, model, effort, prompt_template, input_refs, schema_id, max_turns, allowed_tools, usage_budget}` |
| `GET /jobs/{id}` | Status, validated output, session_id, token spend |
| `POST /jobs/{id}/message` | Continue that job's headless session (see interaction patterns) |
| `GET /brief/today` · `POST /brief/run` | Fetch / force the morning brew |
| `GET /proposals` · `POST /proposals/{id}/decision` | Pending trade proposals; record approve/reject/modify with actor + surface metadata |
| `GET/PUT /mode` | Autonomy mode (ladder below); PUT enforces gate rules |
| `GET/PUT /limits` | Max notional, daily loss, max positions, deep-model daily budget, triage threshold |
| `POST /killswitch` | Instant: cancel unapproved proposals, disable order path, disarm auto modes |
| `GET /status` | Usage-window state per vendor, mode, circuit-breaker state, ingest health |
| `GET/POST /automations` | Custom automation rules (see customization section) |

Every decision-relevant call writes an `AuditEntry` before it returns.

## Discord surface

**Channels** (private guild, operator-only): `#morning-brew` (daily brief embed +
thread for follow-up questions), `#proposals` (one embed per trade proposal,
buttons + thread), `#alerts` (escalations that didn't produce a proposal),
`#positions` (single pinned message, edited in place — live board), `#audit-log`
(batched, low-priority stream), `#system` (mode changes, circuit breakers,
usage-window warnings, errors).

**Proposal embed** carries: symbol, side, notional/qty, order type, time horizon,
thesis summary, invalidation condition, risk-engine results (each check ✅/❌),
quote freshness, and buttons:

`[✅ Approve] [❌ Reject] [✏️ Modify] [🔍 Interrogate]`

- Approve on a live-mode proposal above a notional threshold pops a Discord modal
  ("type the symbol to confirm") — two-step for the big ones.
- Every button press is verified against the operator's Discord user ID and
  recorded in `ApprovalRecord` with the interaction ID, message ID, device, and
  timestamp. Approvals are **single-use and expire** — if the quote has gone stale
  past the risk-engine threshold, the approval is void and the proposal re-prices
  and re-posts.
- `Interrogate` opens the thread and wires it to the agent session (below).

**Slash commands** (the customization layer):

| Command | Effect |
|---|---|
| `/mode set <mode>` | Move on the autonomy ladder (gate-checked) |
| `/killswitch` | Global kill, instant |
| `/brief now` | Force a brew run |
| `/watchlist add\|remove\|list` | **Deferred (W18).** Current paper operation uses restart-only `COCKPIT_WATCHLIST`; no runtime/agent mutation route exists. |
| `/limits set <key> <value>` | Risk + budget knobs |
| `/triage threshold <0-100>` | Escalation sensitivity |
| `/status` | Usage windows, mode, breakers, positions |
| `/ask <q>` | Haiku-class Q&A over current state (cheap) |
| `/ask-deep <q>` | Deep-model Q&A (counts against the daily deep budget) |
| `/automation add\|list\|remove` | Custom standing rules |

**Custom automations** — "customize the automation it provides": the operator
states a rule in natural language (`/automation add auto-approve rebalancing
trades under $200 in existing positions`). A cheap model **compiles it to a
structured rule** (predicate over `TradeProposal` fields); the compiled rule is
posted back for one-time human confirmation; only then does the **risk engine**
enforce it deterministically. LLMs draft rules; they never *are* the rules. Every
auto-approval by a custom rule is tagged with the rule ID in the audit log.

## Interacting with headless sessions ("baked into fresh context")

This works, and there are two distinct patterns — the design uses both:

1. **Resume (same context).** Every `claude -p --output-format json` call returns
   a `session_id` in its result JSON; the runner stores it on `AgentJob`. A reply
   in the job's Discord thread triggers
   `claude -p --resume <session_id> --output-format json "<operator message>"` —
   the agent answers **with its full original context** (the data packet it saw,
   its reasoning artifacts, its output). This is the `Interrogate` button: "why
   this position size?", "what would invalidate this?", "re-check against the
   latest quote."
2. **Fresh-context review (cheaper, unbiased).** For post-hoc questions, spawn a
   *new* Haiku-class call given only the job's stored input packet + validated
   output (both already in SQLite/Parquet — the draft-1 rule of storing raw inputs
   separately from summaries is what makes this possible). No anchoring on the
   original session's reasoning, works even after old session files age out, and
   costs a fraction of resuming a deep-model session.

Defaults: thread replies on a proposal → pattern 1 against the *proposing*
session; `/ask` → pattern 2 with Haiku; `/ask-deep` → pattern 2 with the deep
model. All of it burns plan limits, so interrogation defaults to the cheap model
and the bridge shows the running window spend in `#system` when it crosses 70%.

Codex equivalent: `codex exec` supports session continuation (`codex exec
resume`); same bridge pattern, used for research jobs only.

## Graduated autonomy — the ladder, the gate, the breakers

Modes (stored server-side; Discord is just the lever):

| Mode | Behavior |
|---|---|
| `read_only` | Brew + alerts only; no proposals |
| `paper` | Full pipeline; orders go to Alpaca paper |
| `review_live` | Live orders require explicit button approval |
| `auto_veto` | Proposal posts to `#proposals` and **executes automatically after a veto window** (default 5 min) unless the operator hits ❌ — the bridge between review and full-auto |
| `auto_full` | Executes immediately; Discord gets log-only embeds. The "models are performing well" mode |

**Honest ToS framing** (from the verified research — the human layer buys less
"ToS credit" than assumed, and what it does buy is specific):

| Surface | Full automation permitted? |
|---|---|
| Robinhood Agentic | **Yes** — terms explicitly contemplate agent trades "without your direct input on each transaction"; liability is yours either way |
| Anthropic (own-account agent) | **Yes** — the HITL mandate applies only to consumer-facing financial outputs; a personal own-account copilot is out of scope |
| OpenAI usage policy | **No** for automating high-stakes financial decisions without human review; ChatGPT-Agent policy says don't automate trading at all |
| Discord | Fine (private utility bot) — but trade/balance data then lives on Discord's servers; optional redaction mode (% and R-multiples instead of $ amounts) |

**Design consequence:** `auto_veto`/`auto_full` **pin the decision path to Claude
runtimes** (the orchestrator refuses auto modes if any decision-stage job is
configured for Codex/OpenAI). Codex may run the same schema-gated analysis and
proposal jobs as Claude in `read_only` and human-approved `paper`, using its hardened
tool-free profile. `review_live` and every live/auto rung remain outside that Codex
boundary until a separate pre-live review changes it.

So the real value of the human layer, ordered: (1) capital protection while the
strategy is unproven, (2) OpenAI-policy compliance if their models ever sit on the
decision path, (3) an audit trail of human intent that makes performance
attribution meaningful. It is not primarily a Robinhood/Anthropic ToS requirement.

**Performance gate** ("performing well" needs a definition before it can unlock
anything — data-driven, enforced by the API, not vibes):

- `auto_veto` unlock: ≥4 weeks in `paper`/`review_live`, ≥30 adjudicated
  proposals, operator approval rate ≥80% (the model proposes what you'd do
  anyway), realized max drawdown inside the configured limit, zero attempts to
  bypass a failed risk check.
- `auto_full` unlock: additionally ≥4 weeks in `auto_veto` with a veto rate ≤5%
  and no circuit-breaker trips.
- Both auto modes **re-arm daily**: they disarm at market close and require a
  one-tap `/mode` confirm each morning (deliberate friction on the dangerous end
  of the ladder).

**Circuit breakers** (deterministic; any trip → instant drop to `review_live` +
`@operator` ping in `#system`): daily realized loss limit; N consecutive losing
trades; schema-validation failure rate spike; stale market data; broker error
spike; **usage-window exhaustion** (if the vendor window runs dry mid-session, the
system must fail to `watch-only`, never to "trade without fresh analysis").

## Security notes specific to this draft

- The bot token and broker keys live in Windows Credential Manager; the bridge
  process gets them at start, never the agents. Agents reach the broker only
  through the MCP connection; prompts never carry credentials.
- All state-changing Discord interactions verify the operator's user ID
  (single-operator allowlist), and live-mode actions are double-confirmed by the
  modal. Guild is private; bot requires no privileged intents beyond messages in
  its own guild.
- Discord is a delivery surface, not the source of truth — if Discord is down,
  the orchestrator queues proposals (which expire per quote-staleness rules) and
  falls back to desktop notification. Approvals are idempotent against Discord's
  interaction retries.
- Rate limits: batch `#audit-log` writes (one embed per N entries or per minute);
  `#positions` uses the edit-in-place pinned message pattern.

## Build deltas from draft 1

Phase numbering continues from [[cockpit-review-and-integrated-plan]]:

1. **Phase 0.5 (new, after harness):** orchestrator API skeleton + discord-bridge
   with `#morning-brew` delivery and `/status`, `/killswitch`. This replaces the
   Phase-1 dashboard requirement — the brew lands in Discord from day one.
2. **Phase 2 additions:** proposal embeds + buttons + `ApprovalRecord`-via-Discord;
   thread↔session interrogation; `/automation` rule compiler (paper mode only).
3. **Phase 3 additions:** `auto_veto` with the performance gate; live-mode modal
   confirms; redaction mode decision.
4. **Phase 3.5:** `auto_full` behind the second gate + daily re-arm + breakers
   proven by at least one deliberate trip test.

Test-plan additions: forged/foreign-user Discord interactions are rejected;
approval expiry on stale quotes; veto-window race (veto landing at T-1s wins;
execution at T+0 is atomic with a re-check of mode + breakers); Discord-outage
fallback queues and expires proposals; auto modes refuse to arm with a
Codex-configured decision stage; breaker trip mid-`auto_full` halts before the
next order.

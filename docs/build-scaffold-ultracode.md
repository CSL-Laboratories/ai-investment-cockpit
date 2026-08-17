---
title: Build scaffold — work packages for an Opus 4.8 xhigh ultracode session
type: implementation-plan
status: reference
last-verified: 2026-08-13
related: [README, cockpit-review-and-integrated-plan, draft-discord-control-plane, research-report, research-supplement]
---

> **[REFERENCE]** STILL LOAD-BEARING despite reading like a build-session artifact: this is the WP-series contract register and **43 files in `app/` cite it**. Do not move or renumber it.

> **BUILD COMPLETE — 2026-07-08.** WP0→WP9 all shipped to `main` and green, in the scaffold's build order (WP0 → WP1a → WP1b → WP2 → {WP3, WP4} → WP5 → {WP6, WP7} → WP8 → WP9). WP9 (system test harness) = commit 9f6ff44; its historical baseline was 586 passed / 3 skipped with Ruff clean. Every WP `Accept:` line and all 9 invariants below remain the settled spec and are preserved verbatim. [[PLAN]] owns current lifecycle and status; [[REGISTER]] owns claims.
>
> **Current-state correction (2026-08-13):** later packages implemented the monotonic audit
> sequence/domain binding and witness adapters, but W-25 keeps SEC-11 open until the S3 Object
> Lock Compliance witness is activated and freshly red-teamed. Discord is a mirror under the
> fixed Administrator constraint. Robinhood/live and auto deployment remain
> unbuilt/blocked. Do not use the historical deferred/status prose below as the current schedule.
>
> **The system is PAPER-FIRST. No real money has moved and none may until the pre-live gates pass**, including the 2-week paper soak mandated in "Build order & session plan" below. The "Deferred (do not build yet)" section remains fully deferred: the Robinhood Agentic MCP live path (Phase 3 gate), `auto_full` unlock logic (Phase 3.5), the PWA dashboard, options support, and Codex research jobs are NOT built.
>
> **Pre-live P0 still OPEN:** the SEC-11 audit-spine "delete-then-heal" hole (non-monotonic `seq` + no durable external witness) is documented with repros in `docs/security-hardening-backlog.md` and is NOT closed. It blocks live-mode work regardless of the green gates.

# Build scaffold: layer-by-layer work packages

Purpose: enough structure that a fresh Claude Code session (Opus 4.8, xhigh,
ultracode) can build the system without re-deriving design decisions — while
leaving implementation detail to the builder. Every design decision referenced
here is settled in [[cockpit-review-and-integrated-plan]] and
[[draft-discord-control-plane]]; the builder should treat those two files plus
this one as the spec, and [[research-report]]/[[research-supplement]] as the
constraint source.

## Kickoff prompt (paste into the build session)

> ultracode. Read `investment-copilot/build-scaffold-ultracode.md`, then
> `cockpit-review-and-integrated-plan.md` and `draft-discord-control-plane.md`.
> Build work package WP<N> in `app/` per the scaffold: schema
> first, then implementation, then the WP's acceptance tests. Fan out
> independent modules in parallel, adversarially review the risk-engine and
> approval-path code, and run the full test suite before reporting. Do not
> deviate from the invariants section without flagging it.

Build one WP per session (or per workflow phase). WPs are ordered so each is
independently testable with everything before it.

## Stack decision (deviation from the external draft, deliberate)

**All-Python core**: FastAPI (orchestrator + API), discord.py (bridge),
APScheduler (scheduling), SQLAlchemy→SQLite (state/audit), DuckDB/Parquet
(market snapshots), `jsonschema` (output validation), `keyring` (Windows
Credential Manager). The external draft proposed a TypeScript orchestrator +
Python workers; one language is simpler for a solo dev, and FastAPI +
discord.py is the operator's production-proven stack. No Node dependency —
`claude -p`/`codex exec` are subprocesses, not SDKs (see the CLI-vs-SDK verdict).

Repo layout:

```text
app/
├── pyproject.toml
├── config/            settings.py (pydantic-settings), models.yaml (alias→model map)
├── core/              WP0: logging, secrets, clock, ids
├── schemas/           WP1a: JSON Schemas (agent outputs) + SQLAlchemy models
├── agent_runner/      WP1b: subprocess harness for claude -p / codex exec
├── storage/           WP2: repositories, audit hash-chain, parquet writer
├── ingest/            WP3: alpaca_news_ws.py, finnhub_poll.py, calendars.py
├── risk/              WP4: deterministic rule engine (pure functions)
├── orchestrator/      WP5: FastAPI app, mode ladder, gates, breakers, scheduler
├── bridge/            WP6: discord bot (embeds, buttons, slash cmds, threads)
├── brokers/           WP7: alpaca_paper.py, robinhood_mcp.py, order state machine
├── workflows/         WP8: morning_brew.py, triage_loop.py, proposals.py, prompts/
└── tests/             WP9 + per-WP acceptance tests, fixtures/, replays/
```

## Invariants (carry into every WP — violations are bugs)

1. Order placement is reachable ONLY through: risk-engine pass → mode check →
   approval/veto path → broker connector. No other import of `brokers/` may
   place orders (enforce with a test that greps call sites).
2. `auto_veto`/`auto_full` refuse to arm if any decision-stage job is configured
   for a non-Claude runtime (OpenAI policy constraint — verified).
3. LLM output enters the pipeline only after JSON-Schema validation; one repair
   attempt, then quarantine + `needs_human_review`.
4. Secrets: `keyring` only; never in prompts, env files, logs, or Discord.
5. Risk rules are deterministic pure functions — no LLM call inside `risk/`.
6. Every state-changing action writes an `AuditEntry` (hash-chained) before
   returning success.
7. Usage-window exhaustion degrades to watch-only; it never trades on stale
   analysis and never drops events (queue + expire).
8. Raw inputs stored separately from model summaries (traceability + enables
   fresh-context interrogation).
9. Post-PDT risk model: intraday margin deficit, Reg-T $2k minimum, settlement
   rules — no day-trade-count logic (eliminated 2026-06-04).

## Work packages

### WP0 — Bootstrap (½ day)
`pyproject.toml`, pydantic settings (mode defaults `read_only`), structured
logging, `core/secrets.py` (keyring wrapper), `core/ids.py`, `models.yaml`
mapping aliases (`triage`→haiku-class, `deep`→opus-class) so model swaps are
config-only. **Accept:** app imports cleanly; secrets round-trip via keyring;
no plaintext secret anywhere (test greps repo).

### WP1a — Schemas first (1 day)
JSON Schemas for every agent output: `TriageResult`, `MorningBrief`,
`TradeProposal`, `ThesisUpdate`, `InterrogationAnswer`, `CompiledRule`.
SQLAlchemy models: `MarketEvent`, `PortfolioSnapshot`, `AgentJob`,
`RiskDecision`, `ApprovalRecord`, `AuditEntry`, `AutomationRule`, `ModeState`,
`BreakerState`, `UsageWindow`. Field lists per draft 1 §4 + draft 2. **Accept:**
schema round-trip tests; alembic (or create_all) migration runs on Windows.

### WP1b — Agent runner (2-3 days; the keystone)
`run_agent(spec) -> AgentResult`: builds `claude -p --output-format json
--model <alias> --max-turns N --allowedTools <list>` (and `codex exec --json`)
subprocess calls; captures `session_id`; validates against the spec's schema;
repair-then-quarantine; timeout + retry policy; per-vendor `UsageWindow`
accounting with limit-hit detection (parse limit errors → open breaker);
`continue_session(session_id, msg)` for resume. **Feature-detect flags at
startup** (`claude --help`): `--json-schema`/`--effort` are unverified — runner
validation stays authoritative regardless. **Accept:** mocked-subprocess tests
for happy path, malformed JSON, repair success, quarantine, timeout, limit-hit;
one live smoke test behind an env flag.

### WP2 — Storage + audit (1 day)
Repository layer over the models; append-only `AuditEntry` where each row
stores `prev_hash` (tamper-evidence); parquet snapshot writer keyed by
date/symbol. **Accept:** hash-chain verifies; chain breaks are detected;
snapshot read-back is lossless.

### WP3 — Market ingestor (2 days)
Alpaca news WebSocket client (`wss://stream.data.alpaca.markets/v1beta1/news`,
wildcard subscribe, reconnect with backoff, dedup by article id); Finnhub
poller with a token-bucket capped at 60/min (+`minId` cursor); earnings/macro
calendar pull; all normalized to `MarketEvent` with `raw_ref` into parquet.
**Accept:** replay-fixture tests; reconnect test; rate-limit never exceeded
under load test; dedup verified.

### WP4 — Risk engine (2 days; adversarially review this WP)
Pure functions: symbol allowlist, max notional, max positions, concentration,
daily realized-loss limit, buying power, quote staleness, spread/slippage,
intraday-margin-deficit estimate, live-mode lock, custom `AutomationRule`
evaluation. Returns `RiskDecision` with per-check results. **Accept:**
exhaustive unit tests per rule incl. boundary values; property test: no input
can yield an approved decision with a failed hard check.

### WP5 — Orchestrator API (3 days)
FastAPI app with the draft-2 endpoint table; mode-ladder state machine with
performance-gate calculator (thresholds in config, evaluated from stored
proposals/approvals); circuit breakers (loss, consecutive losses,
schema-failure rate, stale data, broker errors, usage exhaustion) → auto-drop
to `review_live` + system event; APScheduler jobs (pre-market brew, intraday
loop with market-hours guard, EOD review, daily auto-mode disarm at close).
**Accept:** state-machine tests (illegal transitions rejected; gates enforced;
breaker trips downgrade); API contract tests; scheduler fires in a simulated
clock.

### WP6 — Discord bridge (3 days)
discord.py bot: channel bootstrap; brief/alert/proposal embeds; Approve/Reject/
Modify/Interrogate buttons with operator-ID allowlist + modal confirm above
notional threshold; slash commands per draft 2; thread replies →
`continue_session` (proposing session) and `/ask` → fresh-context triage-model
call; `#positions` edit-in-place; `#audit-log` batching; outage fallback
(desktop notification + queue). **Accept:** interaction tests with a mocked
gateway — foreign-user rejection, idempotent retries, approval expiry,
veto-window race (veto at T-1s wins; execution re-checks mode+breakers
atomically).

### WP7 — Broker connectors (2 days paper; MCP later)
`alpaca_paper.py` (REST orders, positions, fills → `PortfolioSnapshot`); order
state machine (proposed→approved→submitted→filled/cancelled/expired);
`robinhood_mcp.py` stub now (Phase 3): MCP client for the Agentic endpoint,
`review_equity_order` always called before `place_equity_order` even though the
platform doesn't force it. **Accept:** paper round-trip against Alpaca sandbox;
state-machine tests; the invariant-1 call-site test.

### WP8 — Workflows + prompts (2-3 days)
`morning_brew`: ingest window → ONE batched triage call (classify
ignore/watch/material/urgent) → ONE deep call composing `MorningBrief` from
classified packets only → Discord. `triage_loop`: deterministic pre-filters
(price-move %, holdings/watchlist entity match, calendar proximity) → batched
triage scoring → deep escalation above threshold within the daily deep budget
(default 5). `proposals`: deep model drafts `TradeProposal` with thesis,
invalidation, sizing basis, source refs. Prompt templates in
`workflows/prompts/` with schema references; keep prompts versioned in git.
**Accept:** fixture-driven end-to-end: recorded market day in → brief +
correctly-gated escalations out; budget exhaustion degrades correctly.

### WP9 — System test harness (2 days)
The full draft-1 + draft-2 test list wired into CI-able pytest: one-market-day
dry run from fixtures with no broker creds; 2-week paper replay runner;
deliberate breaker-trip test; usage-limit-hit mid-loop test; forged-interaction
test; secrets-in-logs grep test.

## Build order & session plan

```text
WP0 → WP1a → WP1b → WP2 → {WP3, WP4 in parallel} → WP5 → {WP6, WP7 in parallel} → WP8 → WP9
```

> **DONE (2026-07-08):** every WP in this order shipped to `main` and is green (WP9 = commit 9f6ff44). The remaining unmet item on this line is the **2-week paper soak before any live-mode work** (below) — that gate has NOT passed.

Suggested ultracode usage per session: fan out module implementation +
test-writing as parallel agents; run an adversarial-review pass (independent
agents trying to construct inputs that violate the invariants) on WP4 (risk),
WP5 (mode ladder), WP6 (approval path), and WP7 (order path) before accepting;
finish every session with the cumulative test suite green.

Rough calendar (solo, part-time): WP0-2 ≈ one week of evenings; WP3-5 ≈ two
weeks; WP6-8 ≈ two weeks; WP9 + 2-week paper soak before any live-mode work.

## Deferred (do not build yet)

Robinhood Agentic MCP live path (Phase 3 gate), `auto_full` unlock logic beyond
the state machine (Phase 3.5), PWA dashboard, options support (re-check beta
scope first), Codex research jobs (nice-to-have; system is complete without).

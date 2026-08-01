---
title: Refactor plan — from fixture-driven build to a running dual-surface cockpit
type: roadmap
status: draft
last-verified: 2026-08-01
related:
  - README
  - capability-expansion-master-plan
  - path-to-production
  - production-work-packages
  - draft-discord-control-plane
  - enhancements-and-additions
  - security-architecture
  - security-hardening-backlog
---

# Refactor plan

> **This is a refactor, not a rewrite.** The core engines — the deterministic risk
> engine, the `OrderGateway`, the hash-chained audit spine, the order state
> machine, the agent-runner — are well-built and heavily tested. **Leave them
> almost entirely alone.** The refactor is about what *wraps* them:
> operationalizing the system, decoupling the operator surface, making the universe
> dynamic, and adding the data + awareness layers from
> [[capability-expansion-master-plan]]. Every change preserves the 9 invariants and
> the paper-first / gated discipline.

## 1. What drives this refactor

1. **It doesn't run yet end-to-end.** `ingest/` is not wired to the scheduler
   (`main.build_ingest_fetchers` is called by nothing); `main.run()` is untested;
   both brokers are in-process stubs. The system executes only against fixtures.
2. **The universe is closed.** The triage pre-filter drops out-of-universe names,
   so the system can't *discover* a new company — the "next Palantir" blind spot.
3. **The data spine is thin.** News / quotes / EDGAR / calendars / FINRA, with no
   fundamentals, estimates, valuation, ownership, or macro — for either the AI or
   the human.
4. **The surface is hardwired to Discord.** Good for push, poor for browsing rich
   data; and betting the whole operator experience on one channel is fragile.
5. **New requirement: dual-surface.** The same data must serve the AI *and* the
   human (trade alongside, or delegate).
6. **New requirement: fit an intermittent (student) workflow.** Significance-tuned
   notifications, digests, EOD wrap, catch-up — decrease babysitting.

## 2. What NOT to change

- The 9 invariants (see [[README]] and `CLAUDE.md`). Non-negotiable.
- `risk/engine.py` — pure deterministic gate. No LLM, ever.
- `brokers/gateway.py`, `order_state_machine.py`, `capability.py` — the single
  order path and its TOCTOU-hardened submit. Touch only with an adversarial round.
- `storage/audit.py` — the HMAC hash-chained spine. The performance gate reads it.
- The paper-first / ladder-gated go-live discipline in [[path-to-production]] and
  [[production-work-packages]]. Autonomy is still earned through the gates.

## 3. The three big architectural decisions

### 3.1 Decouple the operator surface (the "is Discord right?" answer)

**Decision: don't rip out Discord and pick a new single surface. Abstract the
surface behind an interface, and split the two jobs it's doing.** The operator
surface has two distinct responsibilities that want different tools:

| Job | Needs | Good tools |
|---|---|---|
| **PUSH** — alerts, approval prompts, notifications | Reach the phone reliably, low friction | Discord / Telegram / ntfy / Pushover |
| **BROWSE / INTERACT** — data terminal, charts, research, richer approval | A real UI | Web dashboard over a private overlay (Tailscale/WireGuard) |

The refactor introduces an `OperatorSurface` / `NotificationChannel` interface. The
existing `bridge/` (Discord) becomes **one adapter**. This preserves the
zero-inbound posture (the orchestrator API stays loopback-only; the Discord adapter
holds an *outbound* socket; any optional web dashboard is served over Tailscale,
**never** a public port — [[security-architecture]] §6) while removing the
single-channel bet.

**Default surface: Discord-primary.** For this use case — awareness, daily
summaries, intraday pings, on-demand snapshots — Discord is *sufficient as the
data-relay layer*, and going Discord-only eliminates the entire web-dashboard
workstream (RW-08). Discord serves data as: rich **embeds** (structured snapshots),
**edit-in-place pinned boards** (a live market-status board, positions, watchlist),
**chart images** (rendered server-side to PNG — trivial compute, no GPU — attached
to `/research` / `/chart`), and **slash commands** (`/research`, `/portfolio`,
`/screen`, `/compare`, `/catchup`). Accepted tradeoff: charts are *static images*,
not interactive; dense tables are awkward on mobile; there is no fluid multi-panel
exploration. For a student who wants to *stay aware*, that is fine.

- Keep **Discord (or Telegram)** as the primary push + approval + data-relay
  surface. Already built — wrap it behind the interface, don't replace it.
- Add a **phone-push adapter** (ntfy/Pushover) for critical escalations (N-09).
- **Web dashboard (RW-08) is OPTIONAL / deferred.** Build it only if you later need
  *interactive* exploration (zoomable charts, click-to-sort screeners, multi-panel
  live view) — and even then, consider letting **Koyfin ($39/mo) be the deep-browse
  tab** rather than building one. Keep the `OperatorSurface` abstraction (RW-02)
  regardless: cheap insurance to add a dashboard or swap channels later without
  touching the pipeline.

Net: the *transport* is decoupled from the *decision*. Ship Discord-only now; add a
push adapter and (only if ever needed) a dashboard later — without rewriting the
pipeline.

### 3.2 Dynamic universe + discovery lane (the "next Palantir" fix)

Refactor the closed-universe assumption into a **flow**. Three tiers, matching the
cost curve (cheap breadth for discovery, expensive depth only on demand):

1. **Discovery signals — all-ticker, cheap.** News firehose (already all-ticker),
   insider buys (Form 4 via EDGAR, already parsed), volume/momentum anomalies. A
   deliberate **promotion lane** surfaces out-of-universe names instead of dropping
   them.
2. **Shallow fundamentals — all-ticker, affordable.** A lightweight key-metrics
   table (growth, margins, market cap, insider activity, 52-wk range), refreshed
   quarterly, enabling a real full-market **screener**.
3. **Deep data — on-demand, narrow.** Full history + estimates fetched *only* when
   a name is promoted. Any US ticker, pulled when it matters — never a 50,000-ticker
   warehouse.

The **watchlist becomes dynamic**: names enter via discovery, deepen via on-demand
fetch, carry a thesis + invalidation, and exit when the thesis dies. This kills the
blind spot at a fraction of a full terminal's cost. *(Honest limit: this improves
awareness, not prediction — no system reliably calls a 25×; the edge is not missing
candidates and holding them with discipline.)*

### 3.3 Data provider abstraction

All external data sits behind ONE swappable `DataProvider` interface (mirrors the
existing injectable-`fetch` pattern in `ingest/`). Free sources first (FRED macro,
EDGAR filings/insider), one prosumer vendor (~$30–60/mo) for fundamentals +
estimates. No vendor lock-in; failover per E-08.

## 4. Target module map

```text
app/
  ingest/
    providers/        NEW  DataProvider abstraction + vendor adapters
    fundamentals.py   NEW  D-01
    estimates.py      NEW  D-02
    macro.py          NEW  D-06 (FRED, free)
    technicals.py     NEW  D-08 (derived, deterministic)
    corp_actions.py   NEW  D-05
    discovery.py      NEW  broad discovery signals (news/insider/volume)
    (news/finnhub/edgar/finra/calendars — existing)
  universe/           NEW  dynamic universe
    promotion.py      NEW  discovery → watchlist promotion rules
    state.py          NEW  universe-as-a-store (in/out flow)
  surfaces/           NEW  operator-surface abstraction (was bridge/)
    base.py           NEW  OperatorSurface / NotificationChannel interface
    discord/          MOVE existing bridge/ becomes one adapter
    web/              NEW  read-only dashboard adapter (Tailscale-served)
    push/             NEW  ntfy/Pushover escalation adapter
  notify/             NEW  transport-agnostic notification policy
    policy.py         NEW  significance tiering, quiet hours (N-01/N-02)
    digest.py         NEW  batching, EOD wrap, catch-up (N-03/N-04/N-08)
  analytics/          NEW
    scorecard.py      NEW  E-01 benchmark scorecard (deterministic)
    screener.py       NEW  H-04 full-market screener (deterministic)
    portfolio.py      NEW  H-06 portfolio analytics (deterministic)
  workflows/
    research.py       NEW  A-01 on-demand deep-dive / research agent
    (morning_brew/triage_loop/proposals — existing, upgraded)
  risk/ brokers/ orchestrator/ storage/   UNCHANGED core engines
```

## 5. Refactor work packages (RW-series)

Each RW ships with a deterministic gate (GR-xx) in the spirit of the existing
G-gates. Order-path-touching packages get an adversarial-review round.

| ID | Scope | Accept / gate | Depends |
|---|---|---|---|
| **RW-01** | **Operationalize** — wire `ingest/` → scheduler; put `main.run()` under test; Phase-0.5 bring-up (API + one surface delivering the brew, `/status`, `/killswitch`) against live feeds | One market-day dry-run passes end-to-end on live feeds; `main.run()` tested | — |
| **RW-02** | **Surface abstraction** — define `OperatorSurface`; refactor `bridge/` → `surfaces/discord/` as an adapter; behavior identical | All existing bridge tests pass unchanged behind the interface | RW-01 |
| **RW-03** | **Notification policy** — `notify/` significance tiering, quiet hours, digests, EOD wrap, catch-up; emits via surface adapters | Deterministic tiering tests; only critical/material interrupt; EOD wrap renders | RW-02 |
| **RW-04** | **Data spine** — `providers/` abstraction; FRED macro; fundamentals + estimates; derived valuation/technicals; corp actions | Point-in-time correctness test (`report_date` vs `available_date`); untrusted-wrap on free-text | RW-01 |
| **RW-05** | **Dynamic universe + discovery** — `universe/` promotion lane; shallow screener table; on-demand deep fetch; watchlist-as-flow | A seeded out-of-universe event promotes a name + triggers deep fetch; universe in/out tested | RW-04 |
| **RW-06** | **AI upgrades** — synthesis brief (A-03), research agent (A-01), thesis tracking (A-02); optional cheap-triage-offload adapter (Inv-2 boundary held) | Grounding still enforced; Inv-2 keeps decision stages on Claude for auto | RW-04 |
| **RW-07** | **Human surface + analytics** — `/research`, `/screen`, `/portfolio`, `/compare`; human-initiated `/trade` (paper) through the SAME order path; `analytics/` | Human-initiated trade provably routes through risk→approval→gateway (Inv 1) | RW-05, RW-06 |
| **RW-08** *(optional / deferred)* | **Web dashboard** — read-only Tailscale-served data terminal. Build ONLY if interactive exploration is needed; else Discord embeds + edit-in-place boards + chart images (+ Koyfin for deep browse) cover it | Zero inbound ports; read-only | RW-04, RW-07 |
| **RW-09** | **Optimization** — cost governor (X-03), backtest-lite (X-02), provider failover (E-08); scorecard/shadow-live as ladder rungs approach | Budget cap fails to `watch_only` on exhaustion; backtest honest under PIT | RW-04 |

**Safety go-live (the existing W-series in [[production-work-packages]]) runs as the
gate on autonomy** — SEC-11 witness → paper soak → performance gate → `review_live`
→ `auto_veto` → `auto_full`. The RW-series makes the AI smarter and the human
better-informed; it does **not** skip a single go-live gate.

## 6. Sequencing

```text
RW-01 Operationalize ──┬─► RW-02 Surface abstraction ──► RW-03 Notifications
                       └─► RW-04 Data spine ──┬─► RW-05 Dynamic universe ──┐
                                              └─► RW-06 AI upgrades ───────┼─► RW-07 Human surface
                                                                          └─► RW-08 Web dashboard
                                              RW-09 Optimization (as rungs approach)
     ── in parallel / as the gate on autonomy ──► W-series safety go-live
```

Highest-leverage first: **RW-01 (make it run) → RW-03 + RW-04 (notifications + data
= the babysitting-reduction payoff, all pre-live-safe) → RW-05/06/07** outward.

## 7. Honesty ledger — what this refactor does NOT do

- It does **not** rewrite the working core. If you're editing `risk/` or the
  gateway, stop and ask whether you actually need to.
- It does **not** manufacture alpha. Better data + discovery improve awareness, not
  prediction ([[capability-expansion-master-plan]] §0).
- It does **not** open the box. The web dashboard is overlay/loopback only.
- It does **not** skip the ladder. Autonomous trading still earns its way up through
  SEC-11, the paper soak, and the performance gate.
- It does **not** put a model on the execution path — including for human-initiated
  trades (RW-07).

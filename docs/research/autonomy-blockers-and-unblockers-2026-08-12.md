---
title: Autonomy blockers and theoretical unblockers — 2026-08-12
status: reference
last-verified: 2026-08-12
---

# Autonomy blockers and theoretical unblockers

This is the evidence record for `RW-16`. It does **not** schedule implementation; [[PLAN]]
owns sequence and [[REGISTER]] owns claims. The research combined independent repository
inspection, DeepSeek v4 Pro and Flash tracks, and a 2026-08-12 primary-source refresh.

## Executive conclusion

The cockpit has a significant autonomy gap, but it is two different gaps that must not be
collapsed into one:

1. **Dynamic awareness:** the system ingests some sources continuously but only interrogates
   them on a fixed schedule. A news frame, halt, filing, or quote dislocation does not wake the
   triage path. This makes the current "unattended market day" a reliability demonstration,
   not responsive autonomy.
2. **Live broker operation:** Robinhood Agentic can place a trade without a per-order human
   confirmation when an agent is instructed to do so. The hard blockers are instead its
   undocumented unattended-auth/session behavior, the credential's broad read scope, unknown
   order and idempotency contracts, and this repository's no-network Robinhood stub and
   incomplete order reconciliation.

There are credible theoretical unblockers for both. None justifies committing an
implementation package yet. The next work is empirical: capture real event streams, measure
failure behavior, resolve account isolation, and only then authenticate to Robinhood with no
capital at risk to discover the actual protocol and tool contracts. Authentication still has
confidentiality risk because the credential can read other Robinhood accounts.

## Three operational levels

| Level | Meaning | Current state |
|---|---|---|
| **Paper reliability** | Scheduled ingest and triage, human approval, paper order, audited lifecycle | M1–M4 target this. It proves mechanics, not responsiveness or live autonomy. |
| **Dynamic paper autonomy** | Events wake a deterministic arbiter; paper proposals and order lifecycle recover without babysitting; no capital | Blocked on `RW-13`, live-data/staleness wiring, reconciliation, calendar correctness, and empirical sizing. |
| **Live broker operation** | The controlled path reaches a real broker: `review_live` remains human-gated; only later `auto_veto`/`auto_full` rungs are autonomous execution | Blocked on dynamic-paper gates, all pre-live security gates, Robinhood capability discovery, account isolation, and a long measured paper soak. |

"Unattended" means a scheduled process can finish without intervention. "Autonomous" means it
can observe changing state, react within a defined latency, reconcile outcomes, and fail safe
without relying on the next cron tick. Neither term grants a model trading authority: the
model remains an extraction/proposal component behind schemas, never an order-path actor.

## Verified current topology and gaps

The current scheduler runs premarket at 08:30 ET, intraday every 15 minutes, EOD at 16:15,
and disarm at 16:00. Audit anchoring runs every 30 minutes; Finnhub polls every minute; the
five-minute `sec:edgar` job reads the sibling brew's local SQLite database rather than polling
sec.gov. The Alpaca news WebSocket is already daemon-threaded and persists events, but it does
not wake triage. Reconnect gaps are not backfilled. No quote, trade, trading-status, or
order-update stream is consumed.

Consequences:

- news-to-triage delay can approach 15 minutes plus model time, and off-hours events wait;
- halts/resumes and trade corrections are invisible unless another source mentions them;
- `BRK_STALE_DATA` remains unsampled until `RW-14` supplies quote timestamps;
- resting, partial, and lost-response orders cannot reconcile automatically until `RW-15`;
- `app/brokers/robinhood_mcp.py` is deliberately an in-process, no-network stub; and
- `app/orchestrator/market_calendar.py` covers only 2025–2026, then falls back to weekdays.
  That fallback treats Friday 2027-01-01, an NYSE holiday, as open. This is fail-open and is
  tracked as `BUG-096`.

## Blocker taxonomy

### Platform facts and unknowns

Robinhood's official Agentic documentation says an agent can submit without a separate order
confirmation, can write only to a dedicated Agentic account, and can read all Robinhood
accounts, including account numbers, balances, positions, and order history. Initial connection
is desktop-only. The official catalog includes review/place/cancel equity order tools and
portfolio, position, quote, history, fundamental, and order reads.

Robinhood does not publish the authentication/session lifetime, reauthentication behavior,
rate limits, exact JSON schemas, status vocabulary, client-order-id/idempotency behavior, or
the exact order-type/time-in-force intersection exposed by those tools. Those are empirical
unknowns, not details to guess in an adapter.

The final MCP 2026-07-28 core is stateless and no longer has the old initialization/session-id
contract. An OAuth Client Credentials extension exists for machine-to-machine clients, but a
server must opt into it. There is no evidence yet that Robinhood supports it. `W17` therefore
must negotiate and pin the version and authentication profile actually advertised by
Robinhood, not build around either the retired session wrapper or an assumed machine-auth flow.

### Cockpit blockers

- `RW-14`: real quote timestamps, bars, ADV/volatility, and an active staleness breaker.
- `W-21`: costs and net-edge guard based on spread, ADV, volatility, and borrow.
- `RW-15`: broker order-status lookup and deterministic reconciliation.
- `W-22`: the scheduled paper loop and real loss signals.
- `RW-13`: durable event wake-up, deduplication, backfill, replay, and flood control.
- `RW-17`/`BUG-096`: market-calendar coverage must fail closed.
- `BUG-097`: the generic stale-data breaker currently drops only to `review_live`; Invariant 7
  requires stale feed to disable the order path in `watch_only` once `RW-14` makes it reachable.
- SEC-11 and every other P0 pre-live gate: no live-auto rung before the external audit witness
  and performance gates are real.

### Decisions

- Whether Robinhood's read-all credential is acceptable, or demands a Robinhood identity with
  no other assets, or disqualifies the broker (`DEC-05`). Post-response redaction cannot reduce
  the token's authority.
- Which Robinhood MCP/auth profile is actually available (`DEC-06`).
- Whether dynamic triggers can use IEX coverage or require consolidated SIP data (`DEC-07`).
  Entitlements, tier names, symbol limits, and connection constraints are moving targets and
  must be re-verified when the package is built.
- The empirical live order contract: schemas, statuses, idempotency, order types, and time in
  force (`DEC-08`).

## Theoretical unblockers

### Dynamic paper autonomy

Use the existing database and audit spine as a durable event/outbox ledger first; do not add
Kafka or NATS before measurements prove a need. In the same transaction that accepts a
`MarketEvent`, record a trigger candidate with a stable source/id key. A bounded single-writer
worker wakes a deterministic, model-free arbiter. The existing cron job remains a backstop and
both paths converge on the same idempotent triage service.

Use a CloudEvents-like envelope: `id`, `source`, `type`, `time`, `subject`, `sequence`,
`correction_of`, `raw_ref`, and `content_hash`. Source timestamps and ingest timestamps are
both retained. Corrections are new immutable events. Only structured source metadata can make
an event eligible; headline or filing text never controls scheduling, budgets, breakers, or
orders. The event bus is an awareness fast path, never an authority bus.

Use an Alpaca `v2/{feed}` market-data WebSocket for multiplexed quote, trade, correction, and
trading-status channels. News and binary account order updates use separate endpoints and
independent reconnect handling. Frames are low-latency hints; REST/history backfill is the
correctness path where a channel has one. Alpaca `trade_updates` can accelerate order state
while `RW-15` REST polling remains the state-reconciliation backstop. A re-verified SEC Latest
Filings Atom/RSS endpoint can be a discovery hint for the in-process EDGAR fetch/extract path
scheduled by `RW-11`; the documented submissions API remains correctness/backfill.

### Live broker operation and autonomous execution

Keep the Robinhood MCP client deterministic and private behind `OrderGateway`; do not give the
model the credential, MCP server, or a route to broker tools. Store credentials only through
the OS keyring, keep the dedicated account at minimum authority/capital, fail closed on auth
ambiguity, and retain a manual disconnect/reauthorization runbook.

Before connecting, resolve `DEC-05`; the isolated discovery identity must hold no other assets
or data unless the operator explicitly accepts Robinhood's read-all authority. Then run `RW-18`,
a no-capital authenticated capability spike that records `server/discover`, advertised MCP
version/capabilities/auth, exact tool schemas, safe read
responses, error/status behavior, disconnect/reconnect behavior, and any client-generated
idempotency field. Only the verified intersection becomes the gateway contract. Unsupported or
ambiguous operations remain unavailable.

Robinhood's official L2 tool is not an unblocker for this project. The plan deliberately uses
L1 plus statistics; adding L2 would enlarge broker credential use and state complexity without
closing a present gate.

## Required empirical spikes before implementation claims (`RW-19`, except Robinhood `RW-18`)

Each spike records date, environment, universe, sample size, raw distributions, failures, and
the source terms under which the data was obtained.

1. **Robinhood isolation and no-capital capability discovery:** resolve `DEC-05` before
   authentication, then record protocol/auth profile, tool schemas, order
   types/TIF, client-order-id behavior, statuses, rate/error limits, token expiry, disconnect,
   and reauthentication. Never place a live order during discovery.
2. **News stream profile:** burst distribution, reconnects, gaps, update/correction behavior,
   and WebSocket-to-REST identity equivalence for dedup/backfill.
3. **SEC discovery:** re-verify the Latest Filings Atom/RSS endpoint, then measure observed
   latency/cadence and fair-access behavior; use a declared user agent and remain far below
   10 requests/second. Use the documented submissions API for correctness/backfill.
4. **IEX versus SIP:** measure trigger false negatives for the bring-up universe and price the
   entitlement decision.
5. **Order events versus REST:** compare ordering and latency for fill, partial, cancel, and
   reject; record accessible history depth; inject dropped events and prove polling converges
   to the same terminal state without claiming it can reconstruct every intermediate event.
6. **Triage and SQLite load:** measure tick duration and single-writer contention under recorded
   stream load before choosing queue/coalescing limits.
7. **Calendar drift:** compare coded sessions with official NYSE schedules and a machine-readable
   calendar for current and next year.

## Proposed acceptance gates

These are engineering proposals, not vendor promises. Latency and load values must be
re-baselined from the spikes before becoming load-bearing.

| Gate | Proposed pass condition |
|---|---|
| Trigger latency | Wire frame to first triage batch: proposed p50 ≤5 s, p95 ≤15 s, p99 ≤60 s during market hours, re-baselined under a measured minimum event density. |
| Parity + idempotency | On separate clean stores, fast-path and cron replays yield identical dispositions/budgets/proposals/orders; fast path then cron on one store yields zero duplicates. |
| Gap recovery | A proposed 60-second loss causes zero permanent loss only for news/quote/trade channels with verified REST/history backfill and WS/REST identity equivalence. Halt/status coverage uses a named secondary source or reports best-effort. |
| Nondegenerate recall | Structured predicates forward strictly fewer events than the raw stream, retain 100% recall against cron, and produce at least one dispatch before cron would have selected it. |
| Flood integrity | Under the proposed 1,000-frame same-symbol vector, budget/burst controls hold and measured spike-derived bounds for scheduler disarm, mode/watch-only transition, and reconciliation delay pass. HALT remains effective end-to-end. |
| Staleness | Age of the latest exchange-timestamped quote >120 seconds during market hours trips `BRK_STALE_DATA` and, after `BUG-097`, routes through the single L8 `watch_only` mechanism so orders are disabled while awareness continues. |
| Calendar | Unknown/out-of-coverage dates refuse auto-arm/orders while awareness continues; current and next year's official sessions are verified and surfaced in status before the table expires. |
| Order recovery | Stream-to-state p95 ≤2 s is a proposed `RW-15` fast-path target; REST reaches the same terminal state for dropped updates within accessible history and resolves every unambiguous order. Intermediate events need not be reconstructed. |
| Backstop parity | With event delivery disabled, a pre-`RW-13` recorded market day reproduces the pinned ingest/triage sequence and dispositions. |
| Dynamic-paper soak | Event path enabled for ≥4 consecutive market weeks, required event density set by capture spikes, and engineered reconnect, burst, correction, dropped-order-update, and year-rollover cases; record latency/failure/reconciliation/audit distributions. Quiet or feature-off operation fails. |
| Boundary | Static/runtime checks prove the trigger path adds no broker, model, risk, mode, approval, or `OrderGateway` bypass. |

## Hold points and dependency order

No `RW-13` implementation package should be claimed until `RW-19` produces its event/data
evidence and resolves `DEC-07`. No Robinhood connection occurs before `DEC-05`; `RW-18` then
resolves `DEC-06` and `DEC-08` with zero orders before `W17` can be claimed.

```
RW-16
  -> DEC-05 -> RW-18 no-capital Robinhood discovery
  -> RW-19 event/data characterization
  -> RW-11/RW-12 extraction
     -> RW-14 data + BUG-097 stale-feed watch_only
     -> RW-17 / BUG-096 calendar
     -> W-21 costs
     -> RW-15 reconciliation
     -> W-22 scheduled paper reliability
     -> RW-13 dynamic paper autonomy
     -> W16 enabled dynamic-paper/performance evidence
  -> W17 human-gated review_live
  -> W18 auto_veto
  -> W19 auto_full
```

Planning live rungs does not authorize them. The repository remains paper-first; live capital
still requires all pre-live gates. W16 owns the instruments: its two-/four-week statistical
windows are minimum inputs, while funding remains barred for at least six calendar months and
extends toward 12 until the defined dynamic-path/event-density/fault/reconciliation scorecard
is sufficient and operator-signed.

## Threat and failure analysis

| Failure | Required behavior |
|---|---|
| Duplicate/out-of-order/corrected frames | Immutable source/id dedup; sequence/gap detection; corrections create new linked events. |
| Reconnect gap or silent stream | Mark source stale, backfill, and do not declare it fresh until reconciliation completes. |
| Feed flood/prompt injection | Bounded queue, coalescing, existing SEC-21 budgets/burst controls; untrusted text cannot select a control action. |
| Database contention | Persist before dispatch; bounded worker; measure single-writer cost; cron remains available. |
| Broker response lost | Stable client id where supported, status lookup, and `RW-15` reconciliation; otherwise ambiguous and fail closed. |
| Auth expiry or protocol drift | Watch-only, operator alert, explicit rediscovery/reauthentication; never guess or retry an order blindly. |
| Calendar unknown | Watch-only, not weekday fallback. |
| Read-all credential compromise | Isolation decision before connection; models never receive token or raw cross-account data. |

## Primary-source ledger

Accessed 2026-08-12:

- [Robinhood Agentic Trading overview](https://robinhood.com/us/en/support/articles/agentic-trading-overview/)
  — dedicated account, account-wide read scope, desktop connection, disconnect controls.
- [Robinhood trading with your agent](https://robinhood.com/us/en/support/articles/trading-with-your-agent/)
  — official tools and confirmation behavior.
- [Robinhood order types](https://robinhood.com/us/en/support/articles/order-types/)
  — general equity order types/TIF; the Agentic intersection remains empirical.
- [MCP 2026-07-28 final specification announcement](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
  and [security best practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)
  — stateless core, discovery/version negotiation, token isolation and audience rules.
- [MCP OAuth Client Credentials extension](https://modelcontextprotocol.io/extensions/auth/oauth-client-credentials)
  — optional machine-to-machine authentication; Robinhood support is unknown.
- [Alpaca real-time stock data](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data),
  [order updates](https://docs.alpaca.markets/us/docs/websocket-streaming), and
  [real-time news](https://docs.alpaca.markets/us/docs/streaming-real-time-news)
  — stream channels, statuses/corrections, and order lifecycle events.
- [Alpaca market-data plans](https://docs.alpaca.markets/us/docs/about-market-data-api)
  — IEX/SIP entitlements and connection constraints.
- [SEC developer resources](https://www.sec.gov/about/developer-resources) and
  [EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
  — feeds, real-time API behavior, declared user agent, and fair-access ceiling.
- [NYSE trading hours and calendars](https://www.nyse.com/trade/hours-calendars)
  — official 2026–2028 holidays and early closes.
- [CloudEvents 1.0.2](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md)
  — event identity, required attributes, and source/id deduplication semantics.

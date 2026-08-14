---
title: QT — the quantitative engine
type: implementation-plan
status: live
last-verified: 2026-08-13
---
# QT — the quantitative engine

> Work package **QT**. Code lives in `app/quant/`, tests in `app/tests/quant/`.
> Read `app/quant/__init__.py` first — it carries the containment rule that governs
> everything here.

> **Current state.** QT's FIFO book, synthetic/replay feeds, three strategies, guards, cost
> accounting, PnL attribution, `/quant/status`, and bounded `/quant/sim` are implemented. The
> BUG-041–049 follow-up repairs are merged through PR #29. Tier C remains simulated-venue only.
> Merged PR #31 (`RW-14`) composes Tiers A/B against one cached Alpaca IEX L1 source,
> advances the quant snapshot source, enforces the same 120-second freshness authority in the
> verifier/scheduler/gateway, and computes daily ADV/volatility. On branch
> `work/w21-transaction-cost-model`, `W-21` replaces displayed-depth economics with spread +
> ADV/volatility impact + fees + borrow applicability + a relative net-edge rule. Expected
> edge remains deliberately unavailable in production, so a composed opening proposal stops
> at `needs_human` and no `Order` row is created. Cross-platform CI and RW-14's live
> entitlement/progression/stale-transition evidence remain pending. See [[PLAN]] rather than
> this subsystem contract for development order.

## Why this exists

The cockpit was built to answer *whether* a trade is a good idea. It has never been able to
answer what the trade **costs**. Before QT there were no bars, no quotes, no ticks, no order
book and no fill model anywhere in the tree — `FakeAlpaca` returns `accepted, filled_qty=0`
and never fills, and the only place a price enters the system is a `quote_fn` seam that
[only test fakes implement](../app/workflows/proposals.py).

And the gap that leaves is sharper than "the cockpit would allow a huge order" — it would
not; `max_notional` defaults to $1,000 and catches size in dollars perfectly well.

The gap is that **every pre-existing check is denominated in dollars, and none can see
liquidity or edge after cost.** `max_notional`, `buying_power`, `concentration`, and
`pending_exposure` price the order but cannot determine whether its ADV participation is
credible, what square-root impact implies, or whether a trusted expected return clears the
cost. Displayed IEX size is not a substitute: a synthesized L1 level is not market capacity.

QT is that missing half: a real FIFO limit order book with per-order queue position, a
strategy framework, a replay harness, realistic costs, and PnL attribution.

## The three tiers

| Tier | Where | Authority | What it does |
|---|---|---|---|
| **A** | `quant/verify.py` | none | Purely prices spread, square-root impact, fees, borrow, ADV participation, net edge, and deterministic size sensitivity. |
| **B** | `risk/engine.py` | a real pre-trade check | Absolute cost/ADV validity and present-but-insufficient edge are HARD; missing cost/edge evidence is SOFT and forces `needs_human` with no Order. |
| **C** | `quant/execution/` | places its own orders | Autonomous HFT loop, **simulated venue only**. |

Tiers A and B reach the paper pipeline through injected `book_fn` and `metrics_fn` seams on
`draft_proposal`, threaded through `build_services`. The composed Alpaca paper client supplies
a synthesized one-level bid/ask `BookSnapshot` and RW-14 `MarketMetrics`. That composition
arms both `require_quant_verdict` and `require_net_edge`; missing/faulted L1, ADV/volatility,
or expected edge therefore cannot mint an order. The risk engine's defaults remain additive
for deliberately uncomposed unit callers, but production does not use that posture.

`PaperExecutionService._fresh_risk` calls the same shared verdict builder immediately before
approval, prices the fixed persisted Order quantity, and keeps W-21 armed when a persisted
verdict proves the order came through the composed path. Production passes no `EdgeEstimate`:
model-authored `TradeProposal` fields are ignored and the schema rejects expected-return
additions. A future deterministic strategy source must arrive as a separately reviewed,
verified `edge_estimate.v1` input with source/evidence identifiers, gross holding-period return
in basis points of position notional, matching horizon, and a fresh timestamp. Borrow input is
likewise version-pinned to `borrow_quote.v1`; no provider is wired by this package.

End-to-end coverage of that wiring is
[`tests/workflows/test_execution_verification.py`](../app/tests/workflows/test_execution_verification.py),
which proves that displayed sizes do not change the authority-bearing cost, ADV participation
above 10% hard-blocks, RW-14 metrics reach the composed leaf, missing expected edge creates no
Order, and approval-time risk consumes the same inputs.

### Tier C and Invariant 1 — the part to read carefully

Invariant 1 says an order reaches a broker **only** via risk-pass → mode → approval →
`OrderGateway`, and that includes human-initiated trades. A strategy loop placing its own
orders at market-making frequency cannot live behind a human approval click. The two are
irreconcilable.

They are resolved by **separation, not compromise**:

> Nothing under `app/quant/` may import `app/brokers/`. Tier C drives
> `quant.execution.sim_venue.SimVenue`, an in-process matching engine that is not a broker
> and holds no network handle or credential.

**No existing invariant was weakened.** The `brokers` package is untouched; it still has
exactly the two broker-dispatch call sites the G7 acceptance gate pins, and that gate passes
unmodified.

Four independent containments, each tested:

1. **The import gate** — `test_quant_engine_never_imports_the_broker_package` in
   [`tests/test_invariants.py`](../app/tests/test_invariants.py). AST-based, so it is not
   fooled by a comment or by `importlib.import_module("brok" + "ers")`. It has its own
   control test proving it fires on every import form.
2. **The type assertion** — `AutoLoop` refuses any venue that is not a `SimVenue`.
   `isinstance`, not duck typing: duck typing is precisely how a broker-shaped object would
   get in. There is a test that hands it a broker-shaped duck type and asserts refusal.
3. **HALT and watch-only** — the loop reads the cockpit's *existing* halt file and breaker
   state through injected callables, checked per event. An operator's kill switch stops this
   the same way it stops everything else.
4. **Guards** (`quant/guards.py`) — position, order-size, gross-exposure (including
   in-flight), order-rate and session-loss limits, checked on every intent *before* it
   reaches the book. The kill switch latches; cancels are always permitted.

Pointing Tier C at a real or paper **broker** is a different project. It needs
`_ORDER_AUTHORITY_MODES` changed and an adversarial-review round. Do not approach it by
loosening anything in `quant/`.

## QT-09 — the Discord operator surface

### What an operator actually sees and does

**On every proposal**, an `execution` block in the embed:

```
🔴 execution: UNECONOMIC — blocked · cost 22.32bps
   · (spread 2.00 + impact 20.00 + fee 0.32 + borrow 0.00)
   · ADV participation 0.120001 · edge unavailable
```

**`/quant status`** — the engine's posture and containment state, read from the running
process rather than from a document that may have drifted from it.
**`/quant sim`** — a bounded synthetic run returning a tearsheet, with the synthetic-data
caveat printed unconditionally and last.

### SEC-17: the execution block is a risk fact

"Cost 1bp, plenty of depth" is a sentence that gets an order approved, so it is exactly the
kind of claim a prompt-injected model must not be able to make. The verdict is therefore
persisted on the **`RiskDecision` row** (`execution_verdict`, nullable JSON) and
`render_execution_section` takes *the row*, not a verdict dict — so a call site that passed
`proposal["execution_verdict"]` cannot be written. `test_a_verdict_PLANTED_ON_THE_PROPOSAL_IS_IGNORED`
plants a flattering verdict on a hostile proposal and asserts the real one is shown.

Null renders as **"not evaluated"**, never as "evaluated and fine". In the composed process,
an unavailable input also forces human review and cannot create an Order. `edge unavailable`
is the intentional normal state until the selected deterministic strategy's immutable evidence
is separately provisioned and verified.

### The command surface is read-only by construction

`/quant` drives `Runner` over `SyntheticFeed`. `orchestrator/quant_api.py` imports nothing
from `quant.execution` — no `AutoLoop`, no `SimVenue`, no `Guards` — and that is asserted over
the module's **imported names**, not its source text (its docstring names `AutoLoop` in order
to say it isn't used, so a grep would flag the very comment documenting the property).

The route table is enumerated and pinned to exactly `{GET /quant/status, POST /quant/sim}`, so
adding a mutating route fails a test rather than quietly widening the surface. A simulation
writes no audit entries, which is checked. `MAX_SIM_EVENTS` is a **safety** control, not a
nicety: the loopback API is one process that also serves `/killswitch`, and an unbounded
simulation would make the kill switch unreachable while it ran.

### Two pre-existing bugs this surfaced

**`build_bot` had never worked.** The stub loop bound its loop variable as a default parameter
(`async def _cmd(interaction, _name=name)`); discord.py's `_extract_parameters_from_callback`
requires a type annotation on every parameter after the interaction and reads a bare default
as a slash-command *option*. Every call raised `TypeError` before registering a single
command. It went unnoticed because discord.py is deliberately absent from CI and the only test
of that path asserts the `RuntimeError` raised when it is missing. Fixed with a closure;
[`tests/bridge/test_bot_tree.py`](../app/tests/bridge/test_bot_tree.py) now builds the real
tree wherever discord.py is installed and skips where it isn't.

**Discord was push-only.** `compose()` wired `deliver` (REST posts outward) but never called
`build_bot`, so no gateway connection existed and *no slash command could ever fire* — `/mode`,
`/status`, `/killswitch` included. `main.build_operator_bot` now wires it, on a daemon thread
(both `client.run` and `serve` block forever, so whichever ran second would never start), and
fail-closed on two preconditions: the keyring bot token, and a **non-empty operator allowlist**
— a gateway nobody is authorized to use is pure attack surface with no operator benefit.

Absent either, it returns `None` and the cockpit runs exactly as before. Per-proposal
Approve/Reject **buttons remain unwired** — that means wiring the token-mint path, which is an
order-path change and deliberately not bundled into a quant work package.

## The decisions that were expensive to reverse

**W-21 cost units and bounds.** L1 contributes the quoted spread, while RW-14 contributes
median daily dollar volume (`dollars/day`) and annualized realized volatility (decimal
fraction). The gate converts volatility with `σ_daily = σ_annual / sqrt(252)` and computes
one-way impact as `Y × σ_daily × sqrt(order_notional / ADV)` with dimensionless `Y = 1.0`.
The reported sensitivity band is `Y = 0.5–1.0`; only `1.0` carries authority. Participation
above 10% ADV is outside the model-valid range and hard-blocks.

**Holding periods and legs.** `intraday`, `short`, and `long` map to 1, 7, and 30 calendar
days in one pure lookup used for borrow accrual. Opening trades are priced round trip;
position-closing trades are one way. An opening short would require a fresh, verified borrow
quote whose annual rate is a decimal fraction and whose locate fee is dollars for the order.
W-21 adds no short authority: production sells still close reconciled longs, and the existing
`sell_backed` gate still rejects naked shorts.

**Relative edge.** The chosen lower bound of the requested 2–3× range is applied to *net*
return: `gross_return - all_in_cost >= 2 × all_in_cost`, equivalently gross return must be at
least 3× modeled cost. A present estimate below that boundary is a hard rejection. Missing,
stale, malformed, unverified, or mismatched-horizon edge is explicitly unavailable and forces
human review with no Order. Curves use exact whole-share evaluations through 10% ADV and
report the first quantity/notional at which gross return minus all-in cost becomes negative.

**Three timestamps on every event**, all `int` nanoseconds: `exchange_ts` (venue stamp),
`recv_ts` (wire arrival), `ingest_ts` (our decode). A single "timestamp" cannot distinguish a
slow feed from a slow decoder, and those imply opposite work.

**Ordering is by `recv_ts`, never `exchange_ts`.** An event becomes visible when it
*arrived*. Ordering by the venue's stamp is look-ahead measured in microseconds — which at
market-making frequency is the entire edge. `feed.validate_ordering` enforces it and refuses
out-of-order data rather than silently sorting.

**Wall clock and event time never mix.** `core.clock.Clock` stays the tz-aware wall-clock
seam (the [clock contract](../app/ingest/clock_contract.py) is unchanged); event time is
`int` ns. `events.to_ns` / `from_ns` are the only crossings, and `to_ns` refuses a naive
datetime for the same reason `resolve_wall_clock` does.

**Prices are integer ticks inside the book.** Levels are dict and sort keys; with floats,
`100.10` and `100.09999999999999` are two levels, which silently splits a queue in half.
`Decimal` at the cockpit boundary only — money is never a float, matching `risk/engine.py`.

**Zero new dependencies.** The plan budgeted for numpy/pandas; neither turned out to be
needed. `verify.py` must stay import-clean because `risk/` imports it (Invariant 5), and the
integer-tick book removed the other reason to reach for numpy. `pyarrow` (already declared,
already in CI) is the only external package the engine touches.

## The result that justifies the build

Same strategy, same data, same seed — the only difference is whether the market maker
reprices on every tick:

```bash
python -m quant --requote-threshold 2 --events 20000
```

```bash
python -m quant --requote-threshold 0 --events 20000
```

| `requote_threshold_ticks` | maker fills | orders sent |
|---|---|---|
| 2 | 101 | 375 |
| 1 | 56 | 1,760 |
| 0 | 26 | 33,530 |

Requoting on every move sends ~90× the order traffic and wins **74% fewer** maker fills,
because against a real FIFO book every reprice is a cancel-and-new that forfeits queue
position. Queue priority is an asset that accrues while you wait.

Under the `exp(-k·δ)` fill model this engine replaces, that comparison is not merely
different — it is **inexpressible**. There, fill probability depends only on distance from
the mid, so repricing is free and a zero threshold strictly dominates. Every toy
market-making backtest that concluded "requote aggressively" concluded it from that
assumption.

Pinned as `test_requoting_on_every_move_destroys_queue_priority` in
[`tests/quant/test_runner.py`](../app/tests/quant/test_runner.py).

## What the engine deliberately will not do

- **No displayed-depth capacity or fill probability in the live cost gate.** IEX L1 size does
  not establish executable capacity. Queue and depth remain measurements inside the contained
  FIFO simulator; proposal risk uses ADV participation and never reports them as live
  economics.
- **No zero-latency default.** `LatencyModel.instant()` exists for unit tests. A zero-latency
  venue is the most flattering assumption available to a fast strategy.
- **No zero-fee default.** `ZERO_FEES` exists to measure the cost of costs. Exchange fees are
  modelled **per share** (US convention), not in bps — a bps model makes every low-priced
  name look tradeable when it is not.
- **The synthetic feed is not a market.** A strategy profitable against it has demonstrated
  that it *runs*, not that it works. The generator cannot surprise you.

## Honest limitations

- **The counterfactual.** Our orders consume liquidity the venue's own book still had, so a
  replay diverges from the day that actually happened. It is *bounded*, not solved: we never
  fill ahead of our queue position, and `RunResult.desync_cancels` reports the divergence as
  a number. A run where that climbs into the percent range should not be trusted.
- **Market-data latency is modelled in the timestamp only.** `SimVenue` models order-entry
  latency honestly (orders are in flight before they are live); the visible book is stamped
  back but its contents are current. Stated in `sim_venue.py` rather than discovered later.
- **Aggregate feeds give estimated queue positions.** Without per-order ids each level is one
  anonymous block, so a cancel anywhere in it advances us as though it had been ahead —
  optimistic. `ReplayFeed.provenance.caveat()` returns the sentence a tearsheet must print.
- **Cancel/fill races are not modelled.** Cancelling an in-flight order always succeeds.

## Running it

```bash
python -m quant --strategy market_maker --events 20000
```

```bash
python -m pytest tests/quant -q
```

Prove the containment is real rather than claimed:

```bash
python -m pytest tests/test_invariants.py tests/brokers/test_g7_acceptance.py -q
```

The Discord surface (the `test_bot_tree` half needs `pip install discord.py`; it skips
without it, which is what CI does):

```bash
python -m pytest tests/bridge/test_quant_surface.py tests/bridge/test_bot_tree.py tests/orchestrator/test_quant_api.py -q
```

To turn the command bot on, both are required — either missing and it stays off:

```bash
keyring set cockpit discord_bot_token
```

```bash
export COCKPIT_OPERATOR_IDS="<your-discord-user-id>"
```

## Also changed outside `quant/`

- [`risk/engine.py`](../app/risk/engine.py) — absolute cost/ADV validity, execution-quality,
  relative-edge sufficiency, and edge-availability checks. `require_quant_verdict` and
  `require_net_edge` are armed by production composition; deliberately uncomposed callers
  retain backward-compatible defaults.
- [`orchestrator/quant_ws.py`](../app/orchestrator/quant_ws.py) — read-only 10Hz snapshot
  socket at `/ws/quant`, registered only when an engine is attached. API key checked on the
  handshake explicitly; never reads a frame from the client. No UI this pass (RW-08 defers the
  web dashboard, H-02 requires zero inbound ports).
- [`bridge/embeds.py`](../app/bridge/embeds.py) — `render_execution_section`, reading the
  `RiskDecision` row only; [`bridge/quant_view.py`](../app/bridge/quant_view.py) — discord-free
  rendering, Decimal-quantized money, Discord's 2,000-char cap handled with a visible marker
  (over the cap Discord rejects the *whole* send and the operator sees nothing).
- [`bridge/bot.py`](../app/bridge/bot.py) — `QuantCommands` + `run_bot`; the stub-loop closure
  fix; `_WIRED_COMMANDS` so a real body and its stub can't both register and race.
- [`main.py`](../app/main.py) — `build_operator_bot`, started as a daemon thread in `run()`.
- [`schemas/models/risk_decision.py`](../app/schemas/models/risk_decision.py) — nullable
  `execution_verdict` JSON column. No migration tooling in the repo (`create_all` only), so an
  existing SQLite file predating it needs the column added or the DB recreated; every reader
  treats absent as "not evaluated".
- [`tests/brokers/test_g7_acceptance.py`](../app/tests/brokers/test_g7_acceptance.py) —
  `glob` → `rglob`. **This closed a real hole**: a subpackage under `brokers/` escaped the
  AST gate *and* the Invariant-1 regex simultaneously, because that gate skips any path whose
  first component is `brokers`. Both controls had the same blind spot, so an ungated broker
  reach could have been added there with CI green.

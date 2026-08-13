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
> Tiers A/B are composed but remain dormant without the production `book_fn`, daily bars, and
> quote-age inputs scheduled in `RW-14`; `W-21` then replaces displayed-depth economics with
> spread + ADV/volatility impact + borrow + a relative net-edge test. See [[PLAN]] rather than
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
liquidity.** `max_notional`, `buying_power`, `concentration`, `pending_exposure` all price
the order; not one asks whether the market can absorb it. A $1,000 order in a name showing
three shares at the touch passes every single one, because $1,000 is a reasonable amount of
money and nothing in the tree knew what three shares meant. That failure is small,
well-funded, well-diversified, and undeployable — and it was invisible.

QT is that missing half: a real FIFO limit order book with per-order queue position, a
strategy framework, a replay harness, realistic costs, and PnL attribution.

## The three tiers

| Tier | Where | Authority | What it does |
|---|---|---|---|
| **A** | `quant/verify.py` | none | Scores a proposal: slippage, participation, fees, total bps. Advisory evidence. |
| **B** | `risk/engine.py` | a real pre-trade check | `execution_cost_sane` (HARD, blocks) + `execution_quality` (SOFT, → `needs_human`). |
| **C** | `quant/execution/` | places its own orders | Autonomous HFT loop, **simulated venue only**. |

Tiers A and B reach the live pipeline through `book_fn`, an injected seam on
`draft_proposal` with exactly the shape of the existing `quote_fn`, threaded through
`build_services`. **No book feed exists in the tree yet**, so with `book_fn` absent the
verification tiers stay dormant and the risk gate behaves exactly as it did before QT — the
checks are additive, not a new requirement. `require_quant_verdict` is off by default for the
same reason: turning it on before a feed exists would send every proposal to human review
with "no execution verdict attached", and a control that fires on everything is one nobody
reads. Turn it on in the same change that wires a real `book_fn`.

End-to-end coverage of that wiring is
[`tests/workflows/test_execution_verification.py`](../app/tests/workflows/test_execution_verification.py),
whose central pair is a ten-share proposal — exactly the $1,000 `max_notional` allows —
rejected against a three-share book and approved with no book attached. Same proposal, same
limits; only the depth changed.

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
🔴 execution: UNECONOMIC — blocked · cost 1.32bps · (slip 1.00 + fee 0.32)
   · participation 3.3333 · ⚠ book shows only 3 shares · 800 shares ahead if rested
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

Null renders as **"not evaluated"**, never as "evaluated and fine" — and null is the normal
state today, since no book feed is wired.

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

- **No fill probability for a resting order.** That is the `exp(-k·δ)` shortcut; inside a
  risk check it would carry more authority than in a backtest. `queue_ahead_shares` is
  reported instead — a measurement, not a model.
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

- [`risk/engine.py`](../app/risk/engine.py) — two checks, four `RiskContext` fields appended
  after `mode` (every preceding field is non-defaulted). Backward compatible: with no verdict
  attached the engine behaves exactly as before.
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

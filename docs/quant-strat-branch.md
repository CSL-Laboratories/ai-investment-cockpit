---
title: quant-strat branch — complete change record
type: changelog
status: active
branch: quant-strat
base: main @ 08357f0
last-verified: 2026-08-16
related: [quant-engine, thesis-memory, build-scaffold-ultracode, security-hardening-backlog]
---

# `quant-strat` — complete change record

Four commits on top of `main @ 08357f0`. **73 files, +13,873 / −36.**

| Commit | What |
|---|---|
| `b8e68dc` | **QT** — quant engine: FIFO order book, strategies, replay, costs, PnL; three authority tiers; Discord surface |
| `beb310a` | **QT-10** — backtest statistics: is the result real, or did I find it by looking? |
| `11fe015` | **QT-11** — remove the O(n²) in the OMS live-order scan (16.7× faster) |
| `beeeb34` | **MEM-01** — thesis memory: the `invalidation` field finally means something |

**Suite: 1,939 passing / 5 skipped, `ruff` clean** (measured on this branch). Every gate green,
including the Invariant-1 call-site gate, no-LLM-in-`risk/`, secrets-grep, and the new
quant→brokers import gate.

> **A note on the rebase.** This work began from an extracted ZIP of `main` that was several
> commits stale — upstream had since landed BUG-031…039 and the human-gated Codex paper-trading
> feature. A naive `git add -A` would have committed stale copies of **42 files**, silently
> reverting all of it, including deleting `app/brokers/paper_execution.py`, the entire
> `/paper/*` and `/watchlist` endpoint group, and upstream's `_build_risk_context` →
> `build_risk_context` rename. Instead the index was reset to upstream, the 42 untouched files
> restored, and the 3 genuinely conflicting files (`main.py`, `orchestrator/app.py`,
> `workflows/proposals.py`) restored and had the QT changes **re-applied onto current
> upstream**. Final diff: **zero deletions.**

---

## Part 1 — What was missing, and what now exists

Before this branch the cockpit had **no market microstructure at all**: no bars, no quotes, no
ticks, no order book, no fill model, no PnL. `FakeAlpaca.submit_order` returns
`accepted, filled_qty=0` and never fills; the only place a price entered the system was a
`quote_fn` seam that **only test fakes implement**.

The gap that leaves is sharper than "the cockpit would allow a huge order" — it would not.
`max_notional` defaults to $1,000 and catches size in dollars perfectly well.

> **Every pre-existing check is denominated in dollars, and none can see liquidity.**
> `max_notional`, `buying_power`, `concentration`, `pending_exposure` all price the order; not
> one asks whether the market can absorb it. A $1,000 order in a name showing three shares at
> the touch passed every single one — small, well-funded, well-diversified, and undeployable.

`app/quant/` is now **5,895 lines of engine against 4,117 lines of test** (351 tests).

### The engine (M1–M4)

| Module | What it does |
|---|---|
| `events.py` | Frozen event dataclasses; three timestamps; `PriceScale` tick↔Decimal |
| `book/lob.py` | FIFO limit order book with **exact per-order queue position** |
| `book/matching.py` | Price-time priority matching, partial fills, STP, FOK |
| `oms.py` | Order lifecycle; position moves on **fills only**, never on intents |
| `signals.py` | EWMA vol, rolling z-score, OFI, rolling beta — all return `None` when cold |
| `strategies/` | Avellaneda-Stoikov maker, OFI momentum, cointegration pairs |
| `feed/` | Synthetic + Parquet replay behind one `Feed` protocol |
| `costs.py` | Per-share US equity fees, maker rebates, borrow, SEC/TAF |
| `pnl.py` | Attribution: spread capture / inventory drift / fees |
| `runner.py` | The event loop — one place where an event becomes a change to the world |
| `stats.py` | Sharpe with SE, deflated Sharpe, purged CV, PBO |

### Decisions that were expensive to reverse

**Three timestamps on every event**, all `int` nanoseconds: `exchange_ts`, `recv_ts`,
`ingest_ts`. A single "timestamp" cannot distinguish a slow feed from a slow decoder.

**Ordering is by `recv_ts`, never `exchange_ts`.** An event becomes visible when it *arrived*.
Ordering by the venue's stamp is look-ahead measured in microseconds — which at market-making
frequency is the entire edge. `validate_ordering` refuses out-of-order data rather than
silently sorting.

**Prices are integer ticks inside the book.** Levels are dict and sort keys; with floats,
`100.10` and `100.09999999999999` are two levels, which silently splits a queue in half.

**Zero new dependencies.** The plan budgeted for numpy/pandas; neither was needed. `verify.py`
must stay import-clean because `risk/` imports it (Invariant 5), and integer ticks removed the
other reason to reach for numpy. `pyarrow` — already declared, already in CI — is the only
external package the engine touches. `pyproject.toml` and `ci.yml` are **untouched**.

### The result that justifies the build

Same strategy, same data, same seed — the only difference is whether the maker reprices on
every tick:

| `requote_threshold_ticks` | maker fills | orders sent |
|---|---|---|
| 2 | 101 | 375 |
| 1 | 56 | 1,760 |
| 0 | 26 | 33,530 |

Requoting on every move sends ~90× the order traffic and wins **74% fewer** maker fills,
because against a real FIFO book every reprice is a cancel-and-new that forfeits queue
position. Under the `exp(−k·δ)` fill model this replaces, the comparison is not merely
different — it is **inexpressible**, because fill probability there depends only on distance
from the mid and repricing is free.

---

## Part 2 — Three authority tiers

| Tier | Where | Authority |
|---|---|---|
| **A** — advisory | `quant/verify.py` | none; scores a proposal |
| **B** — pre-trade gate | `risk/engine.py` | `execution_cost_sane` (HARD) + `execution_quality` (SOFT) |
| **C** — autonomous | `quant/execution/` | places its own orders, **simulated venue only** |

Tiers A/B reach the live pipeline through `book_fn` — an injected seam on `draft_proposal`
with exactly the shape of the existing `quote_fn`, threaded through `build_services`.

**No book feed exists in the tree**, so with `book_fn` absent the tiers stay dormant and the
risk gate behaves exactly as before. `require_quant_verdict` is off by default for the same
reason: turning it on before a feed exists would send every proposal to human review with "no
execution verdict attached," and a control that fires on everything is one nobody reads.

The central end-to-end test is a **ten-share proposal** — exactly the $1,000 `max_notional`
allows — rejected against a three-share book and approved with no book attached. Same
proposal, same limits; only the depth changed. It also asserts `max_notional` and
`buying_power` are *not* in `failed_checks`, so it cannot pass for the wrong reason.

### Tier C vs Invariant 1

Invariant 1: an order reaches a broker **only** via risk-pass → mode → approval →
`OrderGateway`, including human-initiated trades. A loop trading at market-making frequency
cannot sit behind an approval click. Resolved by **separation, not compromise**:

> Nothing under `app/quant/` may import `app/brokers/`.

**No existing invariant was weakened.** `brokers/` is untouched and the G7 gate passes
unmodified. Four independent containments, each tested:

1. **The import gate** — AST-based, so it is not fooled by a comment or by
   `importlib.import_module("brok" + "ers")`. It has its own control test proving it fires on
   every import form, and I verified it end-to-end: dropping a real `brokers` import into
   `quant/` turns it red, removing it turns it green.
2. **The type assertion** — `AutoLoop` refuses any venue that is not a `SimVenue`.
   `isinstance`, not duck typing, with a test that hands it a broker-shaped duck type.
3. **HALT and watch-only** — checked per event, using the cockpit's *existing* controls.
4. **Guards** — position, order-size, gross-exposure (including in-flight), order-rate and
   session-loss limits, checked before an intent reaches the book. The kill switch latches;
   cancels are always permitted.

---

## Part 3 — Operator surface (QT-09, MEM-01)

### On every proposal

```
🔴 execution: UNECONOMIC — blocked · cost 2.32bps · (slip 2.00 + fee 0.32)
   · participation 3.33 · ⚠ book shows only 3 shares · 800 shares ahead if rested
⚠ existing BUY thesis on AAPL — invalidates on: a close below support
```

### Commands

```
/quant                                      status + containment posture
/quant action:sim strategy:market_maker     bounded synthetic run + tearsheet
/thesis                                     standing claims + invalidated count
/thesis action:invalidate id:<id> reason:<why>
```

### SEC-17: the execution block is a risk fact

*"Cost 1bp, plenty of depth"* is a sentence that gets an order approved — exactly what a
prompt-injected model must not be able to say. The verdict is persisted on the
**`RiskDecision` row** (`execution_verdict`, nullable JSON), and `render_execution_section`
takes *the row*, not a verdict dict — so a call site passing `proposal["execution_verdict"]`
**cannot be written**.

Verified adversarially: a hostile proposal carrying both injection prose and a flattering
planted verdict yields the real verdict, with model text confined to the labelled
`model_thesis` field.

### The command surface is read-only by construction

`/quant` drives `Runner` over `SyntheticFeed`. `orchestrator/quant_api.py` imports nothing
from `quant.execution`, asserted over **imported names** rather than source text — the module's
docstring names `AutoLoop` in order to say it isn't used, so a grep would flag the very comment
documenting the property. The route table is pinned to exactly
`{GET /quant/status, POST /quant/sim}`; a simulation writes no audit entries; `MAX_SIM_EVENTS`
is a **safety** control, because the loopback API also serves `/killswitch` and an unbounded
simulation would make it unreachable while it ran.

---

## Part 4 — MEM-01: the thesis memory layer

`TradeProposal` has required an `invalidation` field since WP8. It was written, scrubbed,
rendered once, and **never read back**. `draft_proposal` read no prior proposals, so every
draft was produced with **zero memory of earlier theses on the same symbol** — the system could
propose the same name twice with contradictory reasoning and never notice a condition it had
itself declared disqualifying had already fired.

### Why this is not a vector store

SEC-14 anchors corroboration on `dispatched_refs`. A thesis is a *previous model's prose*, not
source data. If thesis material entered the dispatched set, a hijacked model could **bootstrap
its own evidence**: emit a confident thesis today, cite it as the corroborating source
tomorrow. The citation would check out. Grounding would pass. Nothing external would ever have
said it.

Two independent mechanisms, both tested:

1. `thesis_context_block` returns **prompt text only** — never touches `Packet.items`, so a
   citation fails the *"not in dispatched packet"* check.
2. `thesis://` is not a parquet ref — `resolve_raw` refuses it, so corroboration also fails
   closed on **resolution**.

`test_a_proposal_citing_its_own_prior_thesis_is_NOT_corroborated` runs the full attack.

### Opening is automatic; closing is not

A **drafted + unflagged** proposal opens a thesis. Invalidating/closing/superseding is an
**allowlisted operator action**, and `closed_by` records who. Whether *"a close below support"*
happened is judgement, and this system does not put a model on judgement that carries
authority — the same principle that keeps `risk/` pure and the order path human-gated.

A **reason is required**: a thesis retired without one leaves no record of *why*, the only part
worth keeping. The **invalidated count** is surfaced alongside the open book, because a system
reporting only its live positions cannot distinguish discipline from a run of luck.

**Additive by construction:** no open thesis ⇒ empty block ⇒ prompt text is byte-identical to
pre-MEM-01.

---

## Part 5 — QT-10: is the result real?

The rest of the engine could run a strategy and attribute its PnL exactly. None of it could say
whether the number *meant* anything. The problem is not noise — it is that **search is free and
evaluation is not**. Try 200 variants, report the best, and the Sharpe printed is an order
statistic presented as an estimate.

| Function | Answers |
|---|---|
| `sharpe_with_se` | The Sharpe **and its confidence interval** — autocorrelation (Lo 2002) + non-normality (Mertens/Opdyke) |
| `deflated_sharpe` | P(true Sharpe > 0) *given how many variants you tried* |
| `min_track_record_length` | How long a record must be before this Sharpe could be believed |
| `purged_kfold` / `probability_of_backtest_overfitting` | Validation that doesn't leak across time; how overfit the *selection procedure* is |

Stdlib only — `statistics.NormalDist` supplies the normal CDF, preserving zero dependencies.

**`trial_sharpes` is required with no default.** A caller who cannot say how many variants they
tried cannot deflate, and defaulting to one trial would assert the very thing the method exists
to check. The CLI likewise refuses without `--trials`.

**The autocorrelation correction only fires when the autocorrelation is real.** My first
implementation summed all 251 sample autocorrelations and reported a **7.6% correction on
genuinely IID data**, because the `(q−k)` weights amplify each `ρ_k` by ~250× while sampling
noise is ~1/√T. Truncating at `T^(1/3)` helped but not enough (still 0.89). The fix is a
Bartlett-band significance test per lag. Measured across 12 seeds: **0.994 on IID noise, 0.582
on a real AR(1)** — it declines to correct when unsure rather than inventing one.

### Applied to this branch's own headline

```
sharpe/sample   -0.092  (95% CI -0.159 to -0.024)  [505 samples]
t-statistic     -2.65   BELOW the Harvey t>3 hurdle
skew / kurtosis -5.54 / 54.11
```

**The fill-count A/B stands** — 101 vs 26 maker fills is a large mechanical difference in queue
behaviour. The **PnL of those runs is not evidence of anything**: a few seconds of synthetic
event time, skew −5.5, kurtosis 54. The CLI reports the Sharpe unannualized on purpose;
multiplying a five-second run by √252 would manufacture an impressive number from made-up data.

---

## Part 6 — QT-11: performance

Profiled, not guessed. cProfile on a 20,000-event run put **85% of runtime in one function**:

```
12,853,652 calls  is_terminal   2.34s
12,853,652 calls  is_live       1.86s (6.93s cumulative)
12,856,200 calls  enum.__hash__ 1.77s
    41,650 calls  live_orders   8.57s cumulative
```

`live_orders()` filtered **every order ever created**, including terminal ones — 308 dead
orders scanned per call, and that average grows with the run, so total cost is quadratic.

Fixed with an incrementally-maintained live index, safe because there are exactly two writers:
`register()` and `_transition()` (the only assignment to `order.state` anywhere). Guarded by
`check_integrity()` (with a control test proving the check can fail) and a randomized
differential sweep over 6 seeds × 120 order lifecycles.

| | Before | After |
|---|---|---|
| 20k events | 11.0s | **0.66s** |
| Throughput | degraded with run length | **~30,000 events/sec, flat** to 400k |
| Quant suite | 12.9s | **~6–8s** |
| Full suite | 138s | **~108–116s** |

(Suite timings vary run to run; the engine benchmarks above are stable.)

---

## Part 7 — Bugs found and fixed

### In pre-existing code

**`build_bot` had never worked.** The stub loop bound its loop variable as a default parameter
(`async def _cmd(interaction, _name=name)`); discord.py requires a type annotation on every
parameter after the interaction and reads a bare default as a slash-command *option*. Every
call raised `TypeError` before registering a single command. Unnoticed because discord.py is
deliberately absent from CI and the only test asserts the `RuntimeError` when it is *missing*.

**Discord was push-only.** `compose()` wired REST delivery but never called `build_bot`, so no
gateway connection existed and **no slash command could ever fire** — `/mode`, `/status`,
`/killswitch` included. Now wired on a daemon thread, fail-closed on the keyring token **and** a
non-empty operator allowlist (a gateway nobody is authorized to use is pure attack surface).

**The G7 acceptance gate had a blind spot.** `glob("*.py")` meant a subpackage under `brokers/`
escaped that AST gate *and* the Invariant-1 regex simultaneously — the latter skips any path
whose first component is `brokers`. Both controls shared one hole. Now `rglob`.

**`_by_strategy` was a `set[str]` and already iterated** — set iteration depends on string hash
randomization, so order varies *between processes*. Strategies read that list, so
reproducibility was only true within one process.

### In my own work, caught by tests or measurement

**Volatility collapsed to 3×10⁻¹⁴.** The EWMA was fed per-message; the mid rarely moves between
messages, so most observations were exactly zero return and variance decayed to nothing. That
silently switched the entire inventory term off. Fixed with a time-grid sample.

**The book stayed permanently crossed.** The runner rested venue ADDs unconditionally; a real
venue matches an order that crosses. The maker was getting 4,248 taker / 1 maker fills.

**PnL marked after the event.** Every maker fill was measured against the post-sweep mid — i.e.
against the price impact of the trade that filled it. A 78%-maker strategy reported −40.50
spread capture. Corrected: **+61 captured, −90 inventory drift, −26.91 fees**, with net
identical before and after — the decomposition identity confirming itself.

**My first "optimization" changed the results** (334 → 398 fills). Hoisting the per-strategy
context rebuild was not waste: absorbing a fill moves the position and routing intents changes
what the next strategy sees. Replaced with a `_ctx_dirty` flag. Verified **byte-identical**
output, not merely "tests pass": 334 fills, net `-55.91220127455482103857496178`.

**`returns_from_equity` on a PnL curve.** It requires a positive denominator, but cumulative
PnL starts at zero and goes negative — so it silently discarded every sample taken while the
strategy was underwater. A **survivorship filter on your own equity curve**, flattering exactly
the runs that deserve it least. It cut a real 505-sample run to 16.

**`/thesis` verbs were partially broken.** Imperative verbs ("close") vs past-tense statuses
("closed") — `close` and `supersede` both failed while `invalidate` happened to work.

### A correction I owe the record

I initially told the user a 40,000-share order "passed every check in `risk/engine.py`." **That
was wrong** — `max_notional` catches a $4M order immediately. The real gap is narrower and more
interesting: every check is dollar-denominated and none can see liquidity. The test and the
docs were corrected to the ten-share/three-share case.

---

## Part 8 — Honest limitations

- **The replay counterfactual is bounded, not solved.** Our orders consume liquidity the
  venue's book still had. We never fill ahead of our queue position, and
  `RunResult.desync_cancels` reports the divergence as a number. A run where it climbs into the
  percent range should not be trusted.
- **Market-data latency is modelled in the timestamp only.** `SimVenue` models order-entry
  latency honestly; the visible book is stamped back but its contents are current.
- **Aggregate feeds give estimated queue positions** — optimistic. `provenance.caveat()`
  returns the sentence a tearsheet must print.
- **Cancel/fill races are not modelled.**
- **The synthetic feed is not a market.** A strategy profitable against it has demonstrated
  that it *runs*, not that it works.
- **MEM-01 does not evaluate invalidation conditions.** It makes sure the question gets asked.
- **Per-proposal Approve/Reject buttons remain unwired** — that means wiring the token-mint
  path, an order-path change requiring its own adversarial-review round.

---

## Part 9 — What is deliberately not built

**Abacus-style optimization of the escalation decision.** `triage_threshold=60` and
`deep_model_daily_budget=5` remain hand-set constants that jointly govern cost and quality,
never validated against outcomes. The cockpit *is* a semantic-operator system (triage filter →
deep map → grounding verifier) with a hard cost constraint, and the audit trail already records
what a cost model would need. **This is the highest-value next increment** — it is where the
LLM spend actually is, since subprocess calls dominate wall-clock by orders of magnitude.

**CopyRight-style layout replication.** Poor fit today: the raw store is written once per batch
and read by exact `#idx` point lookup. There is no scan to accelerate.

**A vector/semantic memory store.** No retrieval problem exists, and it would fight SEC-14
directly.

**Tier C against a real or paper broker.** Requires changing `_ORDER_AUTHORITY_MODES` and an
adversarial-review round. Not approachable by loosening anything in `quant/`.

---

## Part 10 — Verification

```bash
cd app && python -m pytest -q && ruff check .
```

```bash
python -m pytest tests/test_invariants.py tests/brokers/test_g7_acceptance.py tests/risk -q
```

```bash
python -m quant --strategy market_maker --events 20000
```

```bash
python -m pytest tests/bridge/test_bot_tree.py -q   # needs `pip install discord.py`; skips without
```

To enable the Discord command bot, **both** are required — either missing and it stays off:

```bash
keyring set cockpit discord_bot_token
```

```bash
export COCKPIT_OPERATOR_IDS="<your-discord-user-id>"
```

**PAPER-FIRST is unchanged.** No live capital may move until the pre-live gates pass, chief
among them the OPEN P0 SEC-11 audit-spine blocker. Nothing in this branch advances that.

---
title: The plan — current state, delivery lifecycle, milestone gates, and scope control
status: live
supersedes:
  - path-to-production
  - capability-expansion-master-plan
  - integration-and-remote-operations-plan
  - cockpit-review-and-integrated-plan
  - remaining-work-session-plan
last-verified: 2026-08-14
---

# The plan

> **This is the only document that defines what we are building next and in what order.**
> Everything else is either a live register ([[REGISTER]], [[security-hardening-backlog]]),
> a subsystem contract ([[build-scaffold-ultracode]], [[quant-engine]]), reference research,
> or archived. If another doc disagrees with this one, this one wins and that doc is stale —
> say so in a PR rather than working around it.

Written 2026-08-09, after the branch-consolidation audit (PRs #8/#9/#10) and the
[[llm-hybrid-trading]] viability research; autonomy boundaries and gates refreshed
2026-08-12 from [[research/autonomy-blockers-and-unblockers-2026-08-12]], and repository
state/lifecycle reconciled through PR #35 and the 2026-08-14 bring-up evidence.

---

## 0. Current state — 2026-08-14

The first live W-22 bring-up snapshot is recorded in
[[evidence/w22-paper-mvp-2026-08-14]]. At 11:17 PT the `read_only` process had a valid audit
chain, no breaker/watch-only state, healthy signed paper-equity sampling, zero feed errors, and
all 11 Alpaca IEX book sequences at 78. That is operational evidence, not trading-performance
evidence: there were no proposals eligible for opening execution and no paper fills.

Two local bring-up defects are fixed on `fix/w22-paper-bringup-bugs`: additive migration of the
legacy risk-verdict column (`BUG-100`) and post-response quote timestamp validation (`BUG-101`).
`BUG-102` and the underlying Alpaca request defect `BUG-103` are locally closed: startup now
hydrates completed daily bars, and the client supplies the bounded historical `start` required
by Alpaca instead of accepting its current-day default. A post-11:26 PT restart populated both
quotes and ADV/volatility for all 11 symbols with no faults; cross-platform CI is still pending.
`BUG-104` is also locally closed: audit verification now reads the ledger and signed high-water
from one SQLite snapshot, preventing a legitimate concurrent ingest commit from producing a
false one-minute truncation result and paper-performance `watch_only` alert. Genuine hash,
sequence, watermark, truncation, and erasure failures remain fail-closed.

`origin/main` contains the WP0–WP9 foundation, the contained QT engine, the fail-closed calendar
boundary (`RW-17`), in-process EDGAR wiring (`RW-11` implementation), event-characterization
tooling (`RW-19` implementation), live paper-market data (`RW-14` implementation), the
fail-closed transaction-cost/net-edge gate (`W-21`), RW-15 paper reconciliation, W-22's audited
paper loop, and BUG-040–051/097–099. The local bring-up branch adds BUG-100–104.

**What works now:** scheduled ingest and awareness; raw/cited evidence storage; schema-gated
triage, briefs, interrogation, and proposal drafting; deterministic risk/mode/approval controls;
human-approved Alpaca paper submission through `OrderGateway`; Discord/API status and kill
surfaces; contained synthetic/replay quant simulation; and cached Alpaca IEX L1/daily-bar
inputs feeding quote risk, quant snapshots, ADV/volatility, and fail-closed freshness controls.

**What is not complete:** RW-15's real broker-status evidence; cross-platform CI; W-22's two-day
unattended paper proof; a deterministic expected-edge source; event-driven paper autonomy
(`RW-13`); the
external SEC-11 witness; Robinhood discovery/client wiring; and every live/auto rung.

**Verification posture:** RW-14 merged in PR #31, W-21 in PR #33, RW-15 in PR #34, and W-22 in
PR #35. The local `fix/w22-paper-bringup-bugs` working tree passes 2,186 tests / 1 skipped on
Windows with Ruff clean after focused adversarial regressions and live 11-symbol validation. The requested
OpenCode DeepSeek Pro/Flash external audit could not attest: the Pro workspace reported
insufficient balance and alternate read-only reviewer calls timed out without output or
repository changes. All six PR #31 matrix jobs stopped
before executing a step because GitHub reported failed recent account payments or a spending
limit that needs to be increased. Cross-platform CI and RW-14's named live-data evidence remain
pending; this was not a repository test failure.
Offline work may continue on isolated branches; no package becomes production-ready or a
dependency baseline without its required CI and online evidence gate.

**Immediate sequence:** review/push the W-22 bring-up fixes, restore
cross-platform CI, and collect the named RW-11/RW-14/RW-15/RW-19 evidence. The operator moved
`DEC-03` into the active paper-MVP scope on 2026-08-14;
[[research/dec03-strategy-selection-2026-08-14]] records the candidate comparison and proposed
`W-23` boundary. Until the operator selects the strategy and `W-23` supplies a separately
reviewed deterministic edge source, opening paper orders remain fail-closed. Run W-22's first and second
qualifying market days only on verified inputs. `DEC-05` must be resolved before any `RW-18`
authentication, and RW-13 remains blocked until the M1–M4.5 gates actually pass.

---

## 1. What we are building

**A human-gated, paper-first investing copilot with two engines**, per [[CLAUDE]]:

1. **Awareness + discipline** — the reliable value. Time saved, things not missed, discipline
   enforced. Powered by what LLMs are actually good at: information work.
2. **Execution** — mechanically reliable, financially experimental, capped, human-gated.

Engine 1 makes Engine 2 safer and better-informed. It never makes it *profitable*.

### The MVP, concretely

**End-to-end paper trades, with heavy Discord integration.**

A proposal reaches an executed paper order through the full path — risk pass → mode ladder →
human approval → `OrderGateway` — and the operator can see, question and approve the whole
thing from Discord without touching a terminal. Autonomous execution against a real account is
a later phase and is gated on the paper engine having demonstrably worked first.

**Paper-reliability MVP done means:** a market day runs unattended from ingest to a delivered
brief, a proposal it raises can be approved in Discord, the resulting paper order fills, and
the audit chain verifies over the whole sequence.

That is deliberately **not a claim of autonomy**. "Unattended" here means a scheduled process
completed without babysitting. It does not mean the system noticed a material event between
ticks, recovered a missing stream interval, reconciled a live broker outcome, or was allowed to
move capital.

### The autonomy ladder — keep these three claims separate

| Level | What it proves | Milestones |
|---|---|---|
| **Paper reliability** | Scheduled ingest/triage, human approval, paper execution, reconciliation, and audit mechanics | M1–M4.5 |
| **Dynamic paper autonomy** | Events wake a deterministic arbiter; gaps, duplicates, floods, stale data, and paper-order outcomes recover without babysitting; no capital | M5 / `RW-13` after M1–M4.5 |
| **Live broker operation** | The controlled path reaches a real broker: human-gated `review_live` first; only `auto_veto` and `auto_full` are autonomous live execution | M6–M8 / `W17`–`W19` |

Dynamic awareness does not grant execution authority, and execution autonomy does not grant a
model judgment authority. Models remain schema-gated extractors/proposal drafters. They never
receive broker credentials or bypass risk → mode → approval → `OrderGateway`.

### Bring-up universe vs edge universe — DO NOT CONFLATE THESE

We are building and proving on **tech/finance large-cap**. That is the correct *bring-up*
universe: transaction costs are negligible at our size, so cost stops being the binding
constraint while we prove the machinery; coverage is dense, so the extraction and brief
pipeline gets exercised every single day; there are no borrow or ADV problems.

**It is the wrong universe to expect edge from, and nothing in this repo should ever imply
otherwise.** Mega-cap tech is the most efficiently priced, most heavily covered segment in
existence. [[llm-hybrid-trading]] is explicit that residual edge lives in capacity-constrained
situations institutions cannot fit into — the structural opposite.

> **A working pipeline on liquid names is not evidence of alpha.** Write this on anything that
> renders a paper-trading P&L. The failure mode is someone reading a green dashboard in four
> months as a reason to fund the thing.

Niche selection — [[llm-hybrid-trading]] argues for closed-end-fund discounts and small
merger-arb, and against index-inclusion (decayed to ~0.3%) and post-bankruptcy equities (high
dispersion) — moved into active paper-MVP scope by operator direction on 2026-08-14. `DEC-03`
is not closed merely by moving it: [[research/dec03-strategy-selection-2026-08-14]] recommends a
cash-only, long-target merger-arbitrage research slice, but the operator must choose it (or CEF
discounts / no strategy) before `W-23` is claimed. Strategy selection does not assert alpha,
authorize live capital, or permit a model-authored expected return.

---

## 2. What the research changed, and what it confirmed

[[llm-hybrid-trading]] was commissioned to test the premise. Summary of what it means here.

### Confirmed: the architecture is right

Its central recommendation is that *"confining the LLM to a text-to-structured-data extraction
layer is the single best design choice available"*, and that the contamination literature
*"indicts prediction, not fact-extraction."*

That is already Invariant 5 and "execution stays model-free". We built the recommended
architecture before reading the recommendation. **This is the plan's strongest asset and the
thing least likely to be appreciated later** — the temptation to let the model "just score this
one thing" will recur, and the answer is in the research: Benhenda's matched test saw LLM
strategies go from +20.7% in-sample to −1.0% out-of-sample. Prompting fixes do not work
(Sarkar & Vafa). The only reliable mitigations are post-cutoff-only evaluation or
chronologically-consistent models.

### Reframed: `app/filings/` is the Stage 1 deliverable, not cleanup

The research's Stage 1 is *"build and audit the extraction layer first"*, sourced from free SEC
EDGAR. **That is `app/filings/`** — 4,594 LOC across 20 modules, plus
`app/ingest/edgar_pipeline.py`. PR #15 wired it into the production fetcher selection when a
SEC User-Agent and watchlist are configured. It is no longer dead code, but its live-day and
field-precision acceptance gate remains open; the legacy brew reader stays until that evidence
passes.

It also hands us an acceptance gate the repo lacks: **field-level extraction precision ≥95% on
a hand-labelled, post-training-cutoff sample of the documents we actually parse.** Not "the
tests pass" — measured accuracy on real filings. Published F1 (~0.82) is from *news* text, and
organization entities are the weak category (~0.68–0.80), so our own numbers on 8-Ks are the
only ones that count.

### Exposed: three gaps in the risk gate

Verified against the code, not assumed:

| Gap | Current state | What the research says binds |
|---|---|---|
| **No ADV-based sizing** | `RiskContext` is entirely dollar-denominated: `max_notional`, `concentration_pct`, `pending_exposure_cap`, `buying_power` | Cap position at 10–15% of median daily dollar volume |
| **No borrow cost on the risk path** | `quant/costs.py` models fees for the *simulator*; the live gate has nothing | Sub-$100M names averaged >30% borrow fees in 2023; a short leg can consume the entire spread |
| **No net-edge threshold** | `max_execution_cost_bps` is an *absolute* ceiling | Do not trade unless expected net return is **2–3× modelled all-in cost** — a *relative* test |

Note `quant/verify.py`'s `max_participation` is a fraction of **displayed book depth**, not
ADV. Different measures, different horizons. It does not close the first gap.

---

## 3. Market data: the decision, and the problem we are deferring

### Decided: poll L1 quotes + daily bars. No live order book.

`alpaca_paper.latest_quote` already polls Alpaca's L1 endpoint per symbol and is wired through
`quote_fn`. We extend that rather than building anything new.

**We are not building an L2 depth feed.** Three reasons, in order of weight:

1. **The research's own cost model does not use depth.** The square-root impact law is
   `I = Y·σ·√(Q/V)` — it needs **volatility and daily volume**. Spread comes from L1. Borrow
   comes from the broker. Nothing in the recommended cost stack needs a depth ladder.
2. **Our current feed would make depth modelling actively wrong.** `alpaca_paper.py:167` uses
   `params={"feed": "iex"}`. IEX is ~2–3% of consolidated volume: the prices are a fair NBBO
   proxy, the **sizes are not** representative of available liquidity. Walking that as
   "displayed depth" produces confident, precise, wrong numbers — worse than no model, because
   it looks authoritative.
3. **L2 is the highest-maintenance data a small team can take on** — streaming sockets,
   per-symbol book state, sequence-gap recovery, forever.

**`quant/book/lob.py` is not wasted by this.** A real FIFO book is correct for the *simulator*,
where synthetic and replay data are free and deterministic. Live gets L1 plus statistics. Two
jobs, two models — a sound split, not a redundancy.

### Consequence: this resolves the blocked staleness control

[[REGISTER]] item `BUG-040` (`verify_proposal`'s `max_staleness_ns` inert) was blocked because
`book.ts` had no defined time base. **A polled quote has an unambiguous one:** the exchange
timestamp, in epoch nanoseconds. Choosing poll-based ingestion answers the contract question as
a side effect.

### Required before live autonomy: fixed-cadence polling is a real obstacle

**This is a known limitation, recorded here so it is not rediscovered as a surprise.**

Everything today is cron-driven — premarket brew, intraday loop, EOD review. That is fine for a
human-gated awareness engine: the operator reads a brief on a schedule. It is **not** fine for
anything approaching autonomous execution, because the interesting moment is rarely on the
quarter hour. A halt, an 8-K, a gap — all of them happen between polls, and a system that
learns about them on the next tick has already missed the tradeable window.

**The path out already exists in the tree and is not being used for this.** `FEED_ALPACA_NEWS`
is a *streaming* feed that runs on its own thread — the codebase already knows how to consume a
socket and fold events into storage. What is missing is that the **intraday loop is not driven
by it**; it is driven by a clock.

The architectural requirement, to be honoured from now on: **`book_fn` and `quote_fn` are
seams.** Whether a snapshot is produced by a poll or by the last frame off a socket must be a
property of the *fetcher*, never of the caller. Build the poll version so the stream version is
a fetcher swap, not a refactor.

Scheduled as `RW-13` in [[REGISTER]]. It is **not part of the paper-reliability MVP**, because
M1–M4.5 provide the stable inputs and order lifecycle needed to test it honestly. It is a **hard
prerequisite of every live-autonomy rung**: completing M4 must never be reported as completing
the cockpit's autonomy work.

---

## 4. Milestones

Ordered by dependency, not by appetite. Each has one owner and a gate that is a measurement,
not an opinion.

### M1 — Wire the extraction layer  (`RW-11`, implementation merged; evidence pending)

PR #15 added the `sec:edgar` branch to `build_ingest_fetchers`, backed by
`run_edgar_pipeline` when `sec_user_agent` and watchlist scope are configured. It is preferred
over `brew_db_path`, persists the company mapping required for attributed events, and reports
pipeline faults as degraded rather than quiet success.

- **Gate:** the in-process path produces cited deltas on a real market day, *and* field-level
  extraction precision ≥95% on a hand-labelled post-cutoff sample.
- **Still open:** collect that evidence. Only then does a separate `RW-12` package delete
  `edgar_brew.py` + its tests. Until then the legacy reader is an intentional fallback, not the
  primary configured path.

### M2 — Live market data on the ingestion trigger  (`RW-14`)

**Package status — merged 2026-08-13 in PR #31; online evidence pending:**

1. **Operator change:** the composed paper cockpit receives a production L1 book snapshot plus
   daily ADV/realized-volatility inputs, exposes advancing quant snapshots, and disables orders
   in `watch_only` when its latest exchange-timestamped quote is stale during market hours. Raw
   ingest continues while triage/proposals are safely deferred until fail-closed recovery.
2. **Extended path:** the existing Alpaca market-data fetchers, intraday scheduler,
   `BreakerSignals`, quant verification, and `SnapshotSource`/`/ws/quant` surfaces. No second
   broker or order path is introduced.
3. **Retirement:** no adapter is replaced. The package retires the honestly-unsampled
   `max_data_age_s` state and the blocked status of `BUG-040`, `BUG-046`, and `BUG-097`; any
   temporary test leaf stays test-only.
4. **Reachability proof:** composed-system tests must fail if quote timestamps stop reaching
   breaker sampling, if stale data no longer selects L8 `watch_only`, or if `/ws/quant` is not
   registered with an advancing production snapshot source.
5. **Package IDs:** `RW-14` is the single package; it resolves `DEC-01` and closes/unblocks
   `BUG-040`, `BUG-046`, and `BUG-097` without claiming later `W-21` cost-model work.
6. **Platforms:** pure Python/httpx, injected clocks, `Path`-safe caches, and no subprocess,
   credential-store, or platform-specific behavior. Ubuntu/Windows/macOS CI remains required.
7. **Size:** one vertical slice, targeted below roughly 1,000 production LOC plus tests and
   package documentation. A larger design returns here for splitting before implementation.

**Offline implementation evidence — 2026-08-13:** `main` now composes a single cached
Alpaca IEX L1 source for quotes, books, synchronous gateway freshness, quant snapshots, and
daily ADV/volatility. The polled universe is the immutable operator watchlist plus current
paper holdings, so removing a held name from the watchlist cannot make an exit permanently
unobservable. Missing/malformed/future quotes, holdings-discovery failure, or any faulted
universe member keep the global freshness signal failed closed; there is deliberately no
silent per-symbol exclusion from order authority. The faulted set and universe-discovery
state are visible in quant status. Raw quote/bar Parquet hashes are bound into the HMAC audit
chain. Quote-cache failure rejects deterministic risk without aborting the awareness batch.
The execution-verdict TTL is derived from the same 120-second authority limit as the scheduler
and synchronous gateway check. Stale orders are refused at every hour; the system-wide L8
transition and recovery are market-hours gated, and recovery never restores higher autonomy.

The implementation received read-only DeepSeek v4 Pro/Flash architecture, test, and
order-path adversarial passes (sessions `ses_005cb948dffep00GT3p9o4wDZO`,
`ses_005cb948dffet6lNpL21BfkkHb`, `ses_005cb948affel97HEwFfJv1i6q`,
`ses_005b314d6ffebMIQqTEdhH74zF`, and `ses_005b314deffelJJPoVNNHLTYxl`). Reproduced findings
closed in PR #31 include graceful quote-cache degradation, holdings-only reachability,
calendar/clock refusal, cadence/TTL alignment, composed allowlist coverage, and raw-hash
binding. Cross-platform CI, real IEX entitlement/field coverage, live exchange-timestamp
progression, and a real stale-to-L8 transition remain online evidence; none is inferred from
fixtures. Final offline gates: 2,121 passed / 1 skipped on Windows, with Ruff clean.

- Add a daily-bars poller alongside the quote poller (same Alpaca credentials; entitlement and
  feed coverage re-verified at build under `DEC-07`). Cache per symbol, refresh daily. Yields
  **ADV and realized volatility**.
- Synthesize `book_fn` from the L1 quote — one level per side, timestamped from the quote.
- Stamp `last_quote_ts` per symbol as the poller observes quotes; the dedicated market-data
  tick samples
  `max_data_age_s` into `BreakerSignals` — the stale-data breaker's wire (`BUG-058` left it
  honestly UNSAMPLED until this package exists). Gate the sampling on market hours, and keep
  the 120s threshold coherent with the poll cadence.
- PR #31 closes `BUG-097` in code: stale data routes through the single L8 watch-only
  mechanism, ingest/awareness keeps running, and the order-path change received adversarial
  review. Its real stale-feed transition remains an online evidence gate.
- **Gate:** `quant_book_seq` advances between observations on a live day; authority age is
  computed from the latest exchange timestamp. The synchronous gateway refuses stale data at
  every hour, while market-hours breaker sampling routes >120 seconds to L8 `watch_only` and
  disables orders while ingest/awareness continues. This makes both `BUG-040` and `BUG-097`
  testable through the composed path.

### M3 — Make the cost model match reality  (`W-21`)

The change M2 makes possible, and the one the research says decides viability.

**Package intake — claimed on `work/w21-transaction-cost-model`:**

1. **Operator change:** execution evidence reports round-trip spread, conservative
   square-root impact, fees, borrow applicability, ADV participation, and deterministic
   size/cost sensitivity. A proposal cannot become an order while its trusted expected edge
   is unavailable. That is the intentional production posture until a deterministic strategy
   source is separately selected and reviewed; the model-authored `TradeProposal` does not
   gain an expected-return field.
2. **Extended path:** `quant/verify.py`, `risk/engine.py`, `workflows/proposals.py`, the
   `build_services`/`main.compose` seams, `PaperExecutionService._fresh_risk`, the persisted
   `RiskDecision.execution_verdict`, and the existing operator execution block. RW-14
   `MarketMetrics` remains the only ADV/volatility input.
3. **Retirement:** this same package deletes the production verifier's displayed-depth walk,
   fillability, queue-ahead, and displayed-depth participation semantics. The FIFO book and
   depth measurements remain valid inside the contained simulator; no second live cost path
   remains.
4. **Reachability proof:** composed workflow tests must fail if RW-14 metrics stop reaching the
   proposal verdict, if unavailable edge can produce an `Order`, or if the approval-time
   `_fresh_risk` recheck omits the same cost/edge inputs. Pure tests pin the cost curve and its
   first negative whole-share boundary.
5. **Package IDs:** `W-21` only. A future deterministic edge producer or short-opening path is
   a separately claimed package and adversarial review, not latent authority hidden here.
6. **Platforms:** pure stdlib/`Decimal` calculations with injected clocks and data; no new
   subprocess, path, credential, temp-file, network, or platform-specific behavior. The normal
   Ubuntu/Windows/macOS matrix remains required.
7. **Size:** one vertical slice, targeted below roughly 1,000 production LOC plus replacement
   tests and live-doc reconciliation. Crossing that line returns here for a split.

**Pinned contracts:** expected return means verified **gross holding-period return in basis
points of position notional**. Production supplies no estimate yet; missing/non-finite/unverified
edge is explicit and fail-closed, while a present estimate fails the new hard rule unless
`gross - all_in_cost >= 2 * all_in_cost` (equivalently gross is at least 3× cost). The mapping
`intraday / short / long = 1 / 7 / 30 calendar days` has one home and applies to borrow carry.
An injected borrow quote is an annual decimal fraction with provenance/freshness; it is required
only for an explicitly identified short leg. Current sells close reconciled longs and never
become shorts. The gate uses dimensionless `Y = 1.0`, converts RW-14 annualized volatility to
daily volatility with `sqrt(252)`, and hard-stops above 10% of median daily dollar volume;
0.5–1.0 is reported as the sensitivity band, not a knob that can weaken the gate. Curves use
whole shares and exact deterministic cost evaluations inside the model-valid ADV range.

**Implementation checkpoint (2026-08-13, uncommitted):** the vertical slice is implemented on
the claimed branch. Local Windows gates are 2,155 passed / 1 skipped with Ruff clean. Fresh
read-only DeepSeek v4 Pro architecture/adversarial review and v4 Flash caller/test review found
no P0/P1 order-authority bypass. Their concrete findings were closed with regressions: the
approval surface now prices the fixed persisted Order quantity; persisted W-21 evidence keeps
fresh risk armed if the market-data attachment disappears; `decide()` refuses missing RW-14
metrics; status reads the active `VerifyParams`; and versioned edge/borrow plus invalid-input
boundaries are pinned. A separately reviewed deterministic edge producer remains intentionally
out of scope and unavailable. Cross-platform CI and all named RW-14 live evidence remain
pending; this checkpoint is not live authorization.

- Replace `verify.py`'s displayed-depth walk with **spread (L1) + square-root impact (ADV, σ) +
  borrow on any short leg**.
- `max_participation` becomes a fraction of **ADV**, which is the measure the 10–15% rule is
  actually stated in.
- Add the **relative** net-edge test: refuse when expected net return < 2–3× modelled all-in
  cost. This is a new check, not a re-tuning of `max_execution_cost_bps`.
- **Gate:** a cost-sensitivity curve for our universe showing net return against position size,
  and the dollar threshold where a trade goes cash-flow negative.

### M4 — The paper loop, end to end  (`W-22`)

The MVP. Proposal → risk → mode → Discord approval → `OrderGateway` → filled paper order →
audited.

- **Fills modelled at bid/ask, never mid.** The research is explicit that mid-quote fills are
  the single most common way a paper result flatters itself.
- Discord carries the whole loop: brief, proposal, question, approve/veto, fill confirmation.
- Loss signals into `BreakerSignals` (the two `BUG-058` left unsampled): daily loss derived
  from portfolio snapshots (day-open vs current equity — decide whether the breaker means
  realized-only or drawdown, and rename accordingly, one home); consecutive losses derived
  from round trips once fills flow. Neither is wired before this package because neither
  input exists before it.
- **Gate:** an unattended market day, end to end, with `verify_chain().ok` over the whole
  sequence — and a second one, a week later, with no human intervention in between.

**Implementation checkpoint (2026-08-13, offline):** the composed service now samples signed
Alpaca equity and reconstructs closed round trips from cumulative audit fills on the one-minute
reconciliation cadence. Daily loss is explicitly `daily_equity_drawdown` (first signed New York
day observation versus current equity), and an active signed breaker is a synchronous gateway
backstop. Synthetic tests fill buys at ask and sells at bid; real Alpaca fills remain
broker-reported. Discord `/ask` reaches SEC-18 fresh interrogation, while `/paper` preview,
approve, reject, and veto share the loopback execution service and `OrderGateway`; fill delivery
is post-transition and its success/failure is audited. Status exposes performance, audit-chain,
and fill-notification health. Windows: 2,180 passed / 1 skipped; Ruff clean. The expected-edge
source remains deliberately unavailable, so new opening orders continue to fail closed. The
online/cross-platform/two-day gates above remain open; see [[runbooks/w22-paper-mvp]].

**`RW-15` — order-status poll + real broker reconciliation. A named prerequisite of this
gate, scheduled NOW so the temporary state cannot become permanent (the RW-10 lesson).**
The 2026-08-09 sweep made fills-at-submit work (`BUG-052`), which covers marketable orders
only: without a status poll, a RESTING limit order holds the SEC-07 cap forever, a partial
never progresses, a lost-response submit stays an `ambiguous_abort` the operator must clear
by hand, and `reconcile_startup` can only alert (never heal) because `_no_broker_history`
returns nothing. A degenerate M4 using only market orders would LOOK done while all of that
stays broken — which is precisely how a gate gets gamed by accident. Scope, per §5:

1. **Operator change:** resting/partial orders settle without intervention; ambiguous
   aborts clear themselves; the reconcile report can heal.
2. **Extends:** the gateway's existing ACK machinery (`_sanitize_broker_response` →
   `_BROKER_STATUS_TO_STATE` → `_ack_consistent`, reused verbatim — one parser, one home)
   plus a scheduler job over orders in open states; lookups by ``broker_order_id``, and by
   ``client_order_id`` (= ``cockpit-{order.id}``) for the lost-response case.
3. **Retirement:** ``main._no_broker_history`` is deleted in the same PR.
4. **Reachability test:** a system test that fails when the poll job is not scheduled.

5. **Claims:** `RW-15`; settles `ReconcileReport.ambiguous_aborts`; the partial-fill
   remaining-notional refinement waits on `DEC-04` (see [[REGISTER]]).
6. **Platforms:** pure httpx polling, no subprocess/paths — CI-neutral.
7. **Size:** one package (~300–400 LOC + tests). Order path → adversarial-review round.

**Implementation checkpoint (2026-08-13, offline):** the fixed Alpaca paper host now exposes
identity-bound GET-only lookups by signed broker id and the gateway's deterministic client id;
the bounded poll reuses the gateway ACK parser/status mapping, consumes a recovered submit's
exact signed approval, advances only through audited FSM primitives, records cumulative partial
progress, and refuses chain/row/identity disagreement in the exposure-preserving direction.
Startup and the one-minute scheduler both reach the same service, and `_no_broker_history` is
retired. Windows: 2,165 passed / 1 skipped; Ruff clean. The real resting/partial/terminal and
lost-response Alpaca paper gate remains online evidence, as does the cross-platform matrix.

### M4.5 — Deterministic paper edge source (`DEC-03` → proposed `W-23`)

`DEC-03` is now in active paper-MVP scope because M4's mechanics cannot create an opening
`Order` while the trusted expected edge is unavailable. The decision and implementation remain
separate: `DEC-03` selects the strategy; only then may `W-23` be claimed for the order-path
change. The detailed comparison and acceptance contract are in
[[research/dec03-strategy-selection-2026-08-14]].

The current recommendation is a deliberately narrow merger-arbitrage research slice: announced
cash-only transactions, long the public target only, no acquirer short, no options, no CVR or
mixed consideration, and no deal outside the existing 30-calendar-day `long` horizon. An SEC
filing can establish deal terms; it cannot establish expected return. Before an edge is trusted,
a point-in-time outcome data set must deterministically calibrate completion probability and
failure loss, with immutable evidence and a conservative versioned estimate.

`W-23` must inject the same provider into both proposal drafting and
`PaperExecutionService._fresh_risk`, recompute immediately before approval, and return no edge
on stale/missing/amended/terminated/mismatched evidence. The model remains limited to
schema-gated fact extraction and never supplies probability, failure price, or return. The
package requires reachability tests, a fresh adversarial review, and Ubuntu/Windows/macOS CI.
Selecting the strategy or completing `W-23` grants neither paper mode nor live authority; the
ordinary risk → mode → human approval → `OrderGateway` chain remains mandatory.

### M5 — Event-driven triggers  (`RW-13`)

**Dynamic paper autonomy.** Drive triage from durable event candidates while retaining cron as
the correctness backstop. This is a separate deliverable after the scheduled paper MVP, not an
optional enhancement and not an order-authority path.

- Use the existing database/audit spine as a transactional trigger/outbox ledger and a bounded,
  single-writer worker first. No external message broker without measurements that require one.
- Consume an Alpaca market-data WebSocket on `v2/{feed}`; quote, trade, correction, and
  trading-status channels multiplex there. News and account order updates use separate
  endpoints and independent reconnect handling. Connection counts and entitlements are
  verified under `DEC-07` rather than assumed.
- A re-verified SEC Latest Filings Atom/RSS endpoint may provide a discovery hint that wakes
  the in-process EDGAR fetch/extract path scheduled by M1. The documented submissions API is
  the correctness/backfill surface. With a configured SEC User-Agent, the current five-minute
  `sec:edgar` job performs bounded SEC network I/O; without it, only the explicitly configured
  legacy brew fallback is available.
- Only structured source metadata selects a trigger. Untrusted headline/filing text cannot
  schedule work, spend a budget, trip a breaker, or reach the order path.
- Stable source/id dedup, immutable corrections, gap detection, reconnect backfill, replay, and
  bounded flood/coalescing behavior are in scope. Both stream and cron converge on the same
  idempotent triage service.
- **Prerequisites:** M1–M4.5, `RW-17`/`BUG-096`, `BUG-097`, and `RW-19`'s dated stream/load
  evidence resolving `DEC-07`.
- **Replay gate:** run the fast path and cron independently on separate clean stores and require
  identical triage dispositions, budget spend, proposals, and orders; then run fast path followed
  by cron on the same store and require zero duplicates. A pre-`RW-13` recorded day pins the
  feature-off baseline.
- **Recovery gate:** an injected 60-second loss produces zero permanent loss for channels with
  REST/history backfill after WS/REST identity equivalence is proven. Halt/status gaps use a
  designated secondary source or are surfaced explicitly as best-effort—never silently fresh.
- **Eligibility gate:** structured predicates forward strictly fewer events than the raw stream,
  retain 100% recall against cron, and cause at least one measured dispatch before cron would
  have selected it. A forward-everything arbiter does not pass.
- **Safety/performance gate:** market-hours age of the latest exchange-timestamped quote over
  the existing 120-second threshold reaches the single L8 `watch_only` path (`BUG-097`). Proposed
  frame-to-triage p50 ≤5 s / p95 ≤15 s / p99 ≤60 s is re-baselined under a minimum measured
  event density. Under the flood vector, measured bounds for disarm, mode/watch-only transition,
  and reconciliation delay are all met; the out-of-band HALT sentinel remains effective.
- **Dynamic-paper soak (`W16`):** event path enabled for at least four consecutive market weeks,
  plus engineered reconnect/backfill, same-symbol burst, correction, dropped order update, and
  calendar-year-rollover cases. Record event/trigger density, latency distributions, every
  failed dispatch, reconciliation result, and audit verification. A quiet or feature-off soak
  cannot pass.

### M6 — Robinhood discovery, then human-gated `review_live`  (`RW-18` → `W17`)

Planning this rung is in scope; enabling capital is not. Robinhood Agentic officially permits
orders without a separate per-order confirmation, so confirmation is not the blocking
assumption. Its machine-auth/session behavior, broad read scope, tool schemas, rate/error
behavior, status vocabulary, idempotency support, and exact order-type/TIF intersection are
not published and must not be guessed.

- **Before connecting, resolve `DEC-05`.** Authentication itself grants broad read authority;
  "no capital" does not mean "no confidentiality risk." The discovery identity must hold no
  other Robinhood assets/data unless the operator explicitly accepts the read-all scope.
- Then run `RW-18`, a **no-capital authenticated capability spike**: record the version,
  capabilities, and auth profile advertised by `server/discover`; enumerate exact tools and safe
  read responses; measure expiry/reconnect/reauth and rate/error behavior; and record any
  idempotency/status semantics. Place zero live orders.
- The final MCP 2026-07-28 core is stateless. Its Client Credentials extension is optional;
  do not assume Robinhood supports it. Pin the contract Robinhood actually advertises.
- Keep a deterministic Robinhood client private behind `OrderGateway`. Never expose MCP or the
  credential to a model or put either in `app/risk/`.
- `RW-18` resolves `DEC-06` and `DEC-08`; it can run in parallel with paper work after `DEC-05`
  because it neither implements an order path nor moves capital.
- **`W17` blocked by:** M5 and the `W16` dynamic-paper/performance gates; `RW-18`; SEC-11 and
  every applicable P0 pre-live gate; and the live-funding evidence window below.
- **Discovery gate:** advertised protocol/auth/tool contracts are recorded with zero live orders.
  An unknown, expired, or drifting contract produces `watch_only` plus an operator alert.
- **First-live gate:** only after funding is separately authorized, `review_live` remains
  human-approved; every supported order reaches a reconciled terminal state or an explicit
  fail-closed ambiguity; kill/disconnect remains responsive; and the external audit witness
  verifies the sequence. Unsupported/unknown operations are unavailable, not emulated.
- **Funding evidence:** W16 owns the instruments. Its two-week paper/four-week auto-window and
  30-proposal thresholds are minimum statistical inputs, not authorization to fund. Funding is
  barred for at least six calendar months of realistic paper operation and extends toward 12
  months until the scorecard, event-density, dynamic-path, reconciliation, and fault-injection
  evidence is sufficient and operator-signed.

### M7 — `auto_veto`  (`W18`)

No per-order affirmative approval, but the operator retains a timed veto and kill path. Claim
only after M6 produces reconciled human-gated live evidence, the external witness is operational,
and the security backlog's W16/pre-live performance gates pass. Its separately recorded
`auto_veto` soak must have responsive veto/kill distributions, zero unexplained reconciliation
failures, and a green external audit witness. A faster event loop does not lower this bar.

### M8 — `auto_full`  (`W19`)

The final mechanical-execution rung, not autonomous judgment. Claim only after an additional
adversarial review and a separately recorded `auto_veto` soak shows no unexplained
live-versus-shadow divergence, reconciliation failures, audit gaps, or late kill/disarm. The
mode ladder may still fall to `watch_only`; no implementation may weaken that fail-closed path.

### Research hold points before an implementation package is claimed

1. `RW-16` is closed; refresh its dated primary-source snapshot when a moving contract is used.
2. Resolve `DEC-05` before any Robinhood authentication; then `RW-18` no-capital discovery
   resolves `DEC-06` and `DEC-08` before `W17`.
3. `RW-19` records event capture/replay, stream-gap, load, WS/REST identity, and IEX/SIP
   evidence and resolves `DEC-07` before `RW-13`.
4. `RW-17`/`BUG-096` is closed: an unknown calendar year refuses auto-arm/orders while ingest
   and awareness continue. `RW-14`/`BUG-097` is merged; its real stale-feed-to-L8 transition
   must still be evidenced before dynamic-paper soak.
5. Every numeric latency/load target is labelled proposed until measured; vendor facts and
   repository invariants are never presented as performance promises.

Dependency order:

`{RW-11 evidence → RW-12, RW-19 evidence → DEC-07, DEC-05 → RW-18}` run as bounded evidence
lanes around the remaining serial code spine `W-21 → RW-15 → W-22 → DEC-03 → W-23 → RW-13 → W16 → beta
stabilization → W17 → W18 → W19`. RW-14's online acceptance remains a parallel evidence lane
and cannot close on an assumed entitlement/feed.

### Recommended development pipeline

This lifecycle is the default route from the current repository to deployment. A phase name is
an evidence state, not a marketing label, and does not authorize a blocked package. Keep **one
production-code package active at a time**; read-only evidence capture and operator decisions
may run alongside it.

| Phase | Purpose | Primary packages/evidence | Exit condition |
|---|---|---|---|
| **1. Offline development** | Build deterministic vertical slices without consuming live services or CI budget | `W-21 → RW-15 → W-22`; after operator selection, `W-23`; fixtures for `RW-11`/`RW-19` | targeted + full local suite and Ruff green; docs/register updated; adversarial review complete where authority changes |
| **2. Online testing and evidence** | Prove platforms, credentials, vendor contracts, live data, and broker outcomes | cross-platform CI; `RW-11`, `RW-19`/`DEC-07`, `RW-14`, `RW-15`; `DEC-05 → RW-18`; SEC-11 witness activation | every required CI/live-day/precision/reconciliation/source-contract gate recorded; no mocked result substitutes for an online gate |
| **3. MVP** | Deliver reliable scheduled, human-gated paper operation | `W-22` plus the `DEC-03`-selected `W-23` edge source on the M1–M4.5 stack | two unattended market days at least a week apart, full Discord loop, bid/ask paper fills, terminal reconciliation, green audit chain |
| **4. Production beta** | Exercise dynamic paper autonomy under real operating conditions without capital | `RW-13 → W16` | replay parity/idempotency, bounded gap/flood behavior, and ≥4 consecutive market weeks of enabled dynamic-paper soak with fault injection |
| **5. Stabilization and bugfixing** | Freeze features and remove defects exposed by beta | beta findings, open BUG/OPS/SEC residuals, platform/restart/recovery regressions | no open P0/high defects; no unexplained order/reconciliation/audit gaps; clean full matrix and repeated recovery drills |
| **6. Full-scale deployment** | Roll out the operator-owned system in separately authorized authority rungs | ≥6-month paper evidence; `RW-18`; all pre-live gates; then `W17 → W18 → W19` | each rung has its own authorization, soak, adversarial review, reconciliation proof, witness proof, and rollback/kill evidence |

#### Phase 1 — offline development

Start each package from the latest merged `main`. During an Actions-budget pause, a branch may
be developed, locally committed, and pushed for backup without opening a PR; the current
workflow runs on pull requests and pushes to `main`, not ordinary branch pushes. This is a cost
control, not a gate waiver.

The remaining implementation order is `W-21 → RW-15 → W-22 → DEC-03 → W-23`. `DEC-03` is
an operator decision, not permission to implement an arbitrary signal; `W-23` remains blocked
until that selection is explicit. When a later package genuinely needs
unmerged predecessor code, use an explicitly recorded local stack: branch it from the predecessor,
keep only one package under active edit, and do not open the dependent PR until its parent merges;
then rebase/update it onto `main`. A stacked branch is not evidence that its predecessor is
production-ready. Every branch still carries its own tests, register transition, retirement
work, and—when it touches risk, mode, breakers, reconciliation, approval, or gateway
behavior—fresh adversarial review.

#### Phase 2 — online testing and evidence

Restore a bounded Actions budget and open ready branches one at a time in dependency order.
Require the Ubuntu/Windows/macOS × Python 3.11/3.12 matrix before merge. Then run the empirical
gates that local tests cannot establish:

- `RW-11`: cited in-process EDGAR deltas on a real market day and ≥95% field precision; only
  after that may `RW-12` remove the fallback.
- `RW-19`/`DEC-07`: operator-universe stream/replay/load and IEX/SIP measurements that set
  dynamic-trigger bounds.
- `RW-14`: exchange-timestamp quote progression and a real stale-feed transition to L8
  `watch_only` while awareness remains live.
- `RW-15`: resting, partial, terminal, and lost-response Alpaca paper reconciliation.
- `DEC-05 → RW-18`: only after the operator resolves credential read scope; discovery is
  authenticated but no-capital and places zero orders.
- SEC-11: activate the out-of-domain Discord witness and rerun the delete-then-heal red team.

#### Phase 3 — MVP

MVP means scheduled paper reliability, not dynamic or live autonomy. Run the composed service
through two unattended market days at least a week apart. A material event must reach a cited
brief/proposal, Discord approval or rejection, `OrderGateway`, a bid/ask paper outcome, terminal
reconciliation, operator-visible status, and `verify_chain().ok`. Any manual database repair,
stuck resting order, silent feed gap, or missing delivery fails the gate.

#### Phase 4 — production beta

Production beta remains private, single-operator, and paper-only. Implement `RW-13`'s durable
event/outbox arbiter with cron as correctness backstop, then run `W16` for at least four
consecutive market weeks. The dynamic path must actually dispatch; a quiet or feature-off soak
does not count. Inject reconnect gaps, corrections, same-symbol floods, dropped order updates,
calendar rollover, stale quotes, process restart, and witness/vendor failure.

#### Phase 5 — stabilization and bugfixing

Freeze new features. Convert every beta failure into a registered defect with a failing
regression test and severity. Burn down P0/high defects first; include `BUG-082` only through its
separately designed two-phase lock package, never a casual lock split. Repeat cross-platform,
restart/replay, idempotency, kill/disarm, reconciliation, audit-witness, and data-staleness
drills until there are no unexplained divergences. Regressions reopen the owning lifecycle gate.

#### Phase 6 — full-scale deployment

For this project, “full scale” means continuous deployment for the operator's own account—not
public SaaS, managing other people's money, or selling signals. It is still staged:

1. Accumulate at least six calendar months of realistic paper evidence (extend toward 12 months
   when density or confidence is insufficient) and close every applicable pre-live/P0 gate.
2. Deploy `W17` human-gated Robinhood `review_live` only after `RW-18` pins the real contract and
   the operator separately authorizes funding. Every order remains affirmatively approved.
3. Consider `W18` `auto_veto` only after a separately recorded, reconciled `review_live` soak.
4. Consider `W19` `auto_full` only after a separate `auto_veto` soak and fresh adversarial review.

Unknown contracts, late reconciliation, witness failure, stale data, or kill-path uncertainty
blocks promotion and returns the system to the appropriate lower/watch-only posture.

#### One-package delivery loop

Every production package follows this loop:

1. **Claim:** change exactly one [[REGISTER]] row to `claimed`, name the branch
   `work/<id>-<slug>`, and answer the seven intake questions in §5 before editing code. Paired
   retirement IDs such as `RW-11`/`RW-12` are claimed/scheduled together but retain separate
   deletion gates.
2. **Pin the baseline:** start from latest merged `main`, record its CI-attestation state, clear
   ambient `COCKPIT_*`, use the short Windows temp path, run targeted tests plus Ruff, and record
   any live/paper fixture the gate will compare against. During a CI-budget pause, local green
   permits development but does not make the branch production-ready.
3. **Write the failure proof:** add a test that fails when the package is unwired or the defect
   is restored. Include the real composition/scheduler path, not only a unit seam. For an
   empirical package, predeclare sample, timestamps, environment, raw-output location, and the
   decision it resolves.
4. **Implement the smallest vertical slice:** schema/storage → service → composition/schedule →
   operator surface. Reuse existing seams and parsers; do not leave a second unscheduled path.
5. **Verify locally:** targeted tests first, then Ruff and the full suite from `app/`. Exercise
   failure, restart/replay, idempotency, stale/unknown input, and cross-platform behavior in
   proportion to the package.
6. **Adversarial gate where authority changes:** any risk, mode, approval, breaker, arming,
   broker, reconciliation, or `OrderGateway` change receives a fresh adversarial-review round
   against the unmerged branch. Reproduce or refute every claim; one reproducible bypass or
   fail-open result blocks the package.
7. **Evidence gate:** run the milestone's paper/live-day/replay measurement. Tests prove shape;
   the recorded evidence proves reachability and behavior. Never substitute a mocked green
   result for a required market-day, broker, witness, latency, or accuracy observation.
8. **PR barrier:** one package, its tests, register update, retirement, and evidence links in one
   reviewable change. GitHub's Ubuntu/Windows/macOS × Python 3.11/3.12 matrix must be green.
   Close the ID only after merge. Commit or push only when the operator asks.

#### Stop rules

- If a source contract, entitlement, auth profile, or identifier mapping is unknown, stop at an
  `RW-18`/`RW-19` evidence artifact; do not encode the guess in a production adapter.
- If an event channel cannot be backfilled, name it best-effort and surface the gap; do not let
  it satisfy a zero-loss or fresh-data gate.
- If a package needs more than roughly 1,000 production LOC, a second order path, or a new
  external broker/message system, return to §5 and split/replan it before implementation.
- If a breaker, calendar, witness, reconciliation, or credential check cannot be observed
  through the composed system, the package is not done even if unit tests pass.
- No batch completion changes modes automatically. Ladder promotion is a separate,
  operator-signed action after its evidence window.

---

## 5. How scope gets in

The gate exists because of a measured failure, not a hypothetical one. During the audit, **five
defects were found to have been fixed twice** — once on `rw01-operationalize` and once on `main`
as `BUG-031/032/034/035/039`. Cost: ten merge conflicts, two `add/add` collisions where both
branches wrote the same test file from scratch, and a partial port that left the *actual* bug
(a flat 120 s timeout) unfixed while looking fixed.

We work **ad hoc — whoever picks it up.** That is a legitimate choice for four people and it is
not going to change. It means the fix is a **claim mechanism**, not a process.

### Before writing code for anything larger than a one-line bugfix

One entry in [[REGISTER]], and answers to these:

1. **What changes for the operator?** If nothing, this is a refactor — say so and justify it.
2. **What existing code path does this replace or extend?** Name the file. If "none", say why
   the job is genuinely new.
3. **If it replaces something: which package deletes the old path, and when?** No package ID,
   no merge. *(This is the RW-10 failure: a second path built, the first never retired.)*
4. **What proves it is reachable in production?** Name the test that fails if the wiring is
   removed. Not "the unit tests pass." *(This is the grounding failure: `decision_agent_cwd`
   had zero test references, so a dead security control looked healthy.)*
5. **Which IDs does this claim?** Cross-check the register for an existing claim.
6. **Windows / macOS / Linux implications.** Subprocess, path, credential, or temp file — say
   how. CI runs all three.
7. **Rough size.** Over ~1,000 LOC is more than one package. Split it before starting.

A change that cannot answer 2, 3 and 4 is a scope change. It goes back to this document before
it goes into a branch.

### Rules that came out of the audit

- **No new code path without a scheduled retirement.** Both packages get IDs in the same PR.
- **Every security or fail-closed control ships with a test that fails when the control is
  removed** — through the real call path, with the setting configured. Three controls were
  found that could not fire; two are fixed, one needed a contract decision first.
- **A control whose inputs are not yet defined must not ship looking wired.** Either it fails
  closed and says why, or it is absent and the surface reports it as absent. `/quant/status`
  advertising a check that could never fire is the defect, not the missing check.
- **System tests compose the real thing with fake leaves.** A hand-maintained mirror of a
  production wiring function is a defect, not a fixture.
- **A number or an invariant has exactly one home.** Docs link to it. Thresholds that matter go
  in CI where they can fail, not in prose. The test-count baseline was copy-pasted into seven
  docs and every copy was stale.
- **One invariant namespace.** [[CLAUDE]] holds the canonical 9. `AGENTS.md` points at it.
  Vendored code under `app/filings/` keeps upstream numbering with a `PROVENANCE.md` divergence
  entry; cockpit-owned files use cockpit numbers.

---

## 6. Explicitly out of scope

Named so that "we should also…" has somewhere to land.

- **Enabling live capital now.** Planning and empirically de-risking M6–M8 is in scope; moving
  capital is not. Paper-first overrides everything until the pre-live gates in
  [[security-hardening-backlog]] pass. The research adds its own bar: 6–12 months of realistic
  paper trading, including W16-recorded dynamic-paper operation, before committing money; M6
  defines how the minimum and extension are evaluated.
- **Managing anyone else's money, or selling signals.** Own capital needs no registration; the
  moment either of those changes, adviser/BD rules apply. An auto-trading signal product
  forfeits the publisher's exclusion outright.
- **Any niche other than the one explicitly selected by `DEC-03`.** Strategy selection itself is
  now in scope; additional strategies remain separate future decisions and packages. See §1 and
  [[research/dec03-strategy-selection-2026-08-14]].
- **L2 depth data.** See §3.
- **Autonomy in judgment.** More breadth, better extraction, richer briefs, less babysitting —
  yes. The model deciding what to trade — no, and the research is the reason, not caution.

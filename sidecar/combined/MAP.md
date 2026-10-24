# Cockpit map for the historical combination

This is a read of the current `app/` tree. The replay under `combined/` imports `risk.engine` and nothing else from that tree. It does not change cockpit runtime authority, and it does not place a broker order.

## Cadence

The live cockpit is event-driven. A market event is triaged, a deep call drafts one `TradeProposal`, and the operator approves or vetoes it. Premarket brew and the end-of-day review are awareness jobs around that path (`app/tests/system/replay.py` drives those seams on a scripted day). There is no daily-bar strategy in `app/` that emits orders from closes.

The historical tape in this repo is daily ETF bars. The replay therefore steps one session at a time. That is an approximation of the live cadence, forced by the bars.

## Where a proposal comes from

Three sources can shape a decision. Only the first one is on the live order path.

1. **Model proposal.** `workflows/proposals.py` `draft_proposal` renders `workflows/prompts/trade_proposal.v1.md`, calls `workflows.agent_calls.call_agent` with schema `TradeProposal`, scrubs free text, and runs grounding. The validated object carries symbol, side, asset type, order type, quantity or notional, horizon, thesis, invalidation, sizing basis, source refs, and `generated_by`. The schema (`app/schemas/json/TradeProposal.schema.json`) has no field that skips risk, mode, or approval. Auto modes are pinned to Claude. This step is the LLM.

2. **Quant book strategies.** `quant/strategies/` (`Momentum`, `Pairs`, `MarketMaker`) return intents from a limit-order book and a print tape. Momentum's signal is order-flow imbalance plus microprice, not a close-to-close return. Fills come from the matcher. These modules do not call a broker. They need a book this tree does not have for 2021–2026.

3. **Cash-merger edge.** `strategies/merger_edge.py` returns an `EdgeEstimate` or `None` from immutable EDGAR evidence, a calibration artifact, and a fresh quote. It does not place an order. Missing or stale evidence becomes `None`. There is no point-in-time deal tape wired into this replay, so the edge provider is not stepped. `sidecar/merger` is a separate completion model over EDGAR observations and is also idle here for the same reason.

Awareness jobs (triage, morning brew, interrogation, grounding prose) inform the operator. They are not the order path.

## Deterministic versus model-driven

| Stage | Driven by | Module |
| --- | --- | --- |
| Proposal text, side, and requested size | Model, schema-validated | `workflows/proposals.py`, `workflows/agent_calls.py` |
| Grounding flag | Deterministic checks over cited raw refs; a flag blocks the order even if risk approves | `workflows/grounding.py` |
| Risk verdict | Pure Decimal checks, no model, no I/O | `risk/engine.py` `evaluate` |
| Quant intents, given a book | Deterministic | `quant/strategies/` |
| Merger edge, given evidence | Deterministic | `strategies/merger_edge.py` |
| Mode, halt, calendar, approval binding | Deterministic, but they read process state | `orchestrator/`, `brokers/gateway.py` |
| Human approve / veto | Operator, client-side | `bridge/approval.py` |
| Broker ack | Venue | `brokers/` |

`risk/` is the only stage this replay executes from `app/`. The model stage is replaced by the labeled stand-in in `standin.py` because no judgment log exists and a live model call on these dates would know the future.

## Risk

`evaluate(proposal, ctx)` returns `approved` only when every hard check passes and no soft check fails. Hard failures reject. Soft failures return `needs_human` and the proposal path mints no order.

Hard checks include a finite positive size, a finite positive quote, `asset_type == equity` (the schema also allows `option`; the gate does not), a known order type, symbol on the holdings or the watchlist, quote age and spread, max notional, buying power on buys, sells bounded by reconciled shares, position count, concentration, daily loss, pending exposure, Reg-T equity of at least $2,000, an order-capable mode (`paper`, `review_live`, `auto_veto`, `auto_full`), execution-cost sanity, and net-edge sanity when that requirement is armed.

Soft checks force a human when sizing basis is `max` / `maximum` / `all`, when the order type is `market`, when a required execution verdict is missing or stale, and when a required net-edge estimate is unavailable.

Shipped limits, used when the audit chain is intact and no operator tightening is stored (`orchestrator/limits.py` `DEFAULT_LIMITS`):

- max notional $1,000
- daily loss $500
- max positions 10
- concentration 25%
- funded-balance ceiling referenced by settings: $25,000

`workflows/proposals.py` `build_risk_context` uses quote staleness of 120 seconds and `max_spread_pct` of `1` (the quote spread must be less than or equal to that). It passes `pending_notional=0` at draft time. The gateway's own default pending cap is $10,000 and is re-checked at submit. This replay passes the queued notional into `pending_notional` so the pure gate sees the batch.

Production composition sets `require_quant_verdict` when a book, an observation, or a metrics function is attached, and `require_net_edge` when metrics are attached (`draft_proposal`). With neither a book nor an edge estimate, those flags produce `needs_human`. The scored books below use the uncomposed flags (both false), which is the engine's behavior for a caller that has no book. The report's composed probe shows the production flags refuse the same proposal.

Daily loss in the live context is intraday equity drawdown when paper performance is fresh, otherwise zero unless performance is required, in which case a missing figure fails closed. The replay passes open-to-close marked drawdown in dollars.

## Sizing

The cockpit does not compute fractional Kelly. Requested size is whatever the proposal says, and `max_allowed_notional` is the deterministic headroom after the caps. A max-sized basis is a soft fail. Quant strategies size with a fixed share count (`MomentumParams.trade_size`), not a volatility target.

The sidecar sizer (`sidecar/sizer/kelly.py`) is vol-targeted fractional Kelly and can only shrink a caller-supplied cap. Variant `sizer` uses it. Variant `cockpit` uses the $1,000 cap whenever the 60-session return is positive, and zero otherwise.

## Order path

Live invariant: an order reaches a broker only through risk, then mode, then approval, then `OrderGateway`. The gateway re-checks risk, mode, halt, calendar, exposure, and a chain-anchored approval inside one transaction, writes an intent audit row, calls the private broker, then writes the result. It does not invent a fill from an ambiguous status.

This replay keeps that order of decisions and stops before any broker client:

1. Stand-in proposal from closes through the decision session.
2. `risk.engine.evaluate` with shipped limits, mode `paper`, watchlist equal to the ETF universe.
3. Queue only an `approved` verdict. `needs_human` and `rejected` do not reach the fill.
4. Next session, expire the limit if the open (plus the TCA worsen, for that variant) is through the prior close. Otherwise evaluate again and fill in the local book.

Not instantiated, and why: the gateway needs a SQL orchestrator, an HMAC approval row, a halt-file probe, and calendar coverage. Those are process state, not functions of a daily bar. Fabricating approval rows would pretend a human clicked. The approval stand-in is `paper_approved_risk_only.v1`: paper mode and an approved risk verdict, with no warnings. `orders_submitted` is 0.

## What the four variants change

- **cockpit.** Stand-in direction and the $1,000 cap. Flat 1 bp fee. Fill at the next adjusted open.
- **sizer.** Same direction input is replaced by the sidecar sizer's notional, still capped at $1,000. Edge and volatility are the trailing 20 daily returns, target volatility 1% per session, Kelly fraction 0.25.
- **combined.** Equity target is the average of the cockpit notional and the sizer notional. A $1,250 sleeve marks the recorded Kronos equity curve from `examples/kronos_eval.json` on and after 2024-07-02. A $1,250 sleeve marks the synthetic options variance-premium book. Neither sleeve is an equity order.
- **tca.** Same targets as cockpit. The fill price includes a half-spread of 0.5 bp and `0.10 * daily_sigma * sqrt(shares / ADV)`. Those coefficients are a predeclared schedule, not a fit on this sample.

Signals for the equity target use the decision session's close and earlier bars only.

## Lookahead

- A model call on 2021–2026 would be contaminated by training data. No call is made. Repo search found no `JudgmentLog` and no stored `TradeProposal` tape, so there is nothing to replay from `llm_audit`.
- Kronos scored decisions in the committed artifact start 2024-07-02, after the June 2024 pretraining cutoff. The sleeve uses that recorded curve only. It does not invent earlier forecasts, and it does not turn the curve into a per-name signal.
- The options sleeve is Black-Scholes on trailing realized vol of adjusted closes. It is not an OPRA print. Adjusted closes are the wrong spot for historical strikes. The same stand-in already backs `sidecar/options`.
- The Yahoo fixture is backward-adjusted as of the download. Splits and dividends after a session are already in earlier prices. That leak is in the tape. The replay does not add another one by reading future rows into the signal.
- The stand-in is prefix-closed: a decision at session t is a function of closes through t. Changing a later close does not change it.

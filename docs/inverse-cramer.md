---
title: Inverse Cramer — deterministic contrarian trade signal
type: feature
status: branch (not yet merged to main)
last-verified: 2026-07-17
related: [build-scaffold-ultracode, security-hardening-backlog, draft-discord-control-plane]
---

# Inverse Cramer

> **Branch:** `inverse-cramer` (local branch name `feature/inverse-cramer`), commit
> `d87be37`. Not yet merged to `main`. 24 new tests; full suite 779 passed / 1 skipped,
> `ruff` clean at merge time.

A new deterministic signal source that drafts a trade proposal for the **opposite** side of
whatever Jim Cramer publicly recommends — the "Inverse Cramer" trade, which has a real
precedent (Tuttle Capital's SJIM "Inverse Cramer Tracker ETF" ran on the same premise before
liquidating in 2024). It is **not** a new order path: it rides the exact same deep-budget,
deterministic risk engine, and per-trade Discord approval gate as every other proposal in
this system. Invariant 1 (single order path) is untouched.

## Why this design

Two decisions shaped it, both made explicitly rather than defaulted into:

1. **Data source: the existing $0 Alpaca/Benzinga news feed, not the paid X API.**
   `@CramerTracker` on X is the canonical live tracker, but X retired free API access in
   Feb 2026 (pay-per-use only, no free tier). Benzinga (already ingested via
   `ingest/alpaca_news.py`'s wildcard WebSocket, $0/mo) publishes its own "Cramer says
   buy/sell X" recap articles, so Inverse Cramer is a **new deterministic filter on data
   already flowing through the pipeline** — no new ingest infrastructure, no new paid
   vendor, no ToS gray area. Trade-off accepted knowingly: this is less complete/instant
   than a dedicated live tracker account.
2. **Fully symmetric, any symbol.** Cramer BUY on any symbol → propose sell; Cramer SELL on
   any symbol → propose buy. Not limited to current holdings/watchlist.
3. **Equities only.** "Open a long put" was the original ask, but the `Order` /
   `canonical_order_tuple` model has no strike/expiry/right fields yet (options support is
   a separate, larger work package — see Deferred below).

## How it works (data flow)

```text
Alpaca/Benzinga news WS (wildcard, $0/mo — already wired)
        │
        ▼
ingest/alpaca_news.py::_to_event()
        │  calls ingest/cramer_signal.py::detect_cramer_call(headline, summary, symbols)
        │  — regex-only, no LLM. Requires: a "Cramer" mention, EXACTLY ONE tagged ticker,
        │  an UNAMBIGUOUS buy XOR sell keyword. Anything else → None (ordinary "news").
        ▼
MarketEvent.event_type = "cramer_call:buy" | "cramer_call:sell"
        │  (tagged at the ONE true ingest point, BEFORE persist_events binds event_type
        │  into the signed EVENT_INGEST_BATCH chain — SEC-11 domain-row binding, so this
        │  is as trustworthy as "halt"/"filing", never a later unsigned reclassification)
        ▼
workflows/triage_loop.py
        │  cramer_call:buy/sell join _HARD_EVENT_TYPES:
        │    - survives the pre-filter regardless of holdings/watchlist (any symbol)
        │    - auto-corroborated by workflows/budget.py::deterministic_urgency
        │      (same trust posture as halt/filing — no calendar/quote-move needed)
        │    - still needs a triage score >= threshold from the ONE batched triage call,
        │      and still competes for the SAME daily deep-budget (default 5) as everything
        │      else — no special carve-out
        │  forced_side = inverse_side(cramer_side)   # "buy"->"sell", "sell"->"buy"
        ▼
workflows/proposals.py::draft_proposal(..., forced_side=..., prompt_name="inverse_cramer_proposal")
        │  deep model (Claude) drafts thesis/invalidation/sizing over
        │  workflows/prompts/inverse_cramer_proposal.v1.md — told the side is FIXED,
        │  asked only for the supporting contrarian rationale
        │  proposal["side"] is FORCED to forced_side immediately after validation —
        │  a model that disagrees is recorded in `reasons`, never trusted over the
        │  deterministic value (mirrors SEC-21: a model's self-label never counts alone)
        ▼
SAME pipeline as any other proposal: SEC-09 scrub → SEC-14 grounding (flag-only) →
deterministic risk engine (Invariant 5) → Order row (state=proposed) → Discord
approval/veto → OrderGateway (Invariant 1) → broker
```

## Safety properties preserved

- **The side is decided by deterministic code, never by an LLM's reading of untrusted
  article text.** This is the load-bearing design choice — see `draft_proposal`'s
  `forced_side` override in `workflows/proposals.py`.
- **Fail-closed detection.** `detect_cramer_call` returns `None` (treated as ordinary news)
  on any ambiguity: more than one tagged ticker, both buy and sell language present, or
  neither. It never guesses.
- **No bypass of Invariant 1.** There is no new order path — `draft_proposal` still routes
  through the same risk gate, mode ladder, and human approval as every other proposal.
- **Shares the daily deep-budget.** Inverse Cramer proposals consume the same audited
  `DeepBudget` (default 5/day) as any other escalation; a flood of Cramer articles cannot
  starve genuine signals any more than a flood of ordinary news could (same SEC-21 ranking
  and burst-breaker machinery applies).
- **SEC-11 domain-row binding applies identically.** `cramer_call:buy`/`cramer_call:sell`
  are reconciled against the signed `EVENT_INGEST_BATCH` binding in
  `DeepBudget.try_consume` exactly like `halt`/`filing` — a keyless writer forging the
  `event_type` column spends no budget.

## Files touched

| Path | Change |
|---|---|
| `app/ingest/cramer_signal.py` | new — `detect_cramer_call`, `inverse_side` |
| `app/ingest/alpaca_news.py` | classify at ingest, before the signed binding |
| `app/workflows/budget.py` | `cramer_call:buy`/`sell` added to `_HARD_EVENT_TYPES` (auto-corroboration + SEC-11 reconciliation) |
| `app/workflows/triage_loop.py` | same hard-event-type pre-filter carve-out; always escalates to a proposal draft (any symbol) with `forced_side` + `prompt_name` |
| `app/workflows/proposals.py` | `draft_proposal` gains optional `forced_side` / `prompt_name` kwargs |
| `app/workflows/prompts/inverse_cramer_proposal.v1.md` | new prompt template — side is a fixed fact, not a decision |
| `app/tests/ingest/test_cramer_signal.py`, `test_alpaca_news.py` (additions) | detector unit tests + ingest wiring |
| `app/tests/workflows/test_budget.py` (additions) | corroboration + SEC-11 reconciliation |
| `app/tests/workflows/test_inverse_cramer.py`, `test_inverse_cramer_triage.py` | `draft_proposal` override behavior + end-to-end `triage_loop` integration |

## Deferred

- **Options ("open a long put").** Needs `Order`/`canonical_order_tuple` extended with
  strike/expiry/right — a separate, larger work package that also touches the risk engine.
  Equities-only for now.
- **A dedicated live tracker feed** (e.g. paying for X API access to `@CramerTracker`)
  would improve completeness/timeliness over the Benzinga-recap approach, at the cost of
  being the first paid data source in an otherwise $0/mo stack. Not pursued yet.

## How to verify

```bash
cd app
python -m pytest -q tests/ingest/test_cramer_signal.py tests/ingest/test_alpaca_news.py \
  tests/workflows/test_budget.py tests/workflows/test_inverse_cramer.py \
  tests/workflows/test_inverse_cramer_triage.py
python -m pytest -q      # full suite — 779 passed / 1 skipped at merge time
ruff check .
```

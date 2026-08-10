---
title: Capability expansion & master plan — optimizing the cockpit for a dual (AI + human) workflow
type: roadmap
status: superseded
last-verified: 2026-08-09
related:
  - README
  - enhancements-and-additions
  - draft-discord-control-plane
  - path-to-production
  - production-work-packages
  - build-scaffold-ultracode
  - upside-and-roi
  - security-architecture
  - security-hardening-backlog
---

> **[SUPERSEDED]** Superseded by [[PLAN]] for sequencing and scope. Kept in place rather than archived because production code cites it. Read it for capability IDEAS; take the order of work from [[PLAN]] and claim it in [[REGISTER]].

# Capability expansion & master plan

> **What this doc is.** A single, sequenced plan to take the cockpit from a
> green-but-fixture-driven build to a *completely optimized* dual-surface system:
> data and reasoning that serve **both the AI and the human operator**, so the
> operator can trade alongside the AI or delegate trades to it — with the explicit
> goal of **decreasing time spent babysitting the market**. It extends
> [[enhancements-and-additions]] (E-series) and [[draft-discord-control-plane]];
> it does not re-open settled spec. New feature namespaces: **D** (data spine),
> **H** (human surface), **N** (notification / workflow-fit), **A** (AI
> capability), **X** (cross-cutting).

## 0. The framing that keeps this honest

Three commitments carry over from the corpus and constrain everything below:

1. **The reliable ROI is time, awareness, and discipline — not alpha.**
   ([[upside-and-roi]] §2–4.) Every feature here is justified first by how much
   market-babysitting it removes, second by decision quality. Autonomous trading
   is the *gated endpoint*, priced as a capped option, never the headline.
2. **The 9 invariants are non-negotiable.** New data feeds are UNTRUSTED input
   (`<untrusted-content>` wrap; Inv 3 validates model *output*, SEC-02 governs
   tool-result *input*). No LLM ever enters `risk/` (Inv 5). Every trade — whether
   AI-proposed OR human-initiated (H-03) — reaches a broker only through the single
   order path: risk-pass → mode → approval → `OrderGateway` (Inv 1). All state is
   hash-chain audited (Inv 6).
3. **Zero-inbound network posture holds.** The human research surface (H-02
   dashboard) is served over a private overlay (Tailscale/WireGuard) or loopback —
   **never** a public port ([[security-architecture]] §6).

**Dual-surface principle (the new requirement).** The same normalized data serves
two consumers with different needs:
- **The AI** needs machine-readable, point-in-time-correct data with provenance.
- **The human** needs legible, browsable analytics (tables, charts, on-demand
  deep-dives) to trade *alongside* the AI and to sanity-check what it proposes.

Both read the **same** normalized store; only the *renderer* differs. This is why
the answer to "reimplement Koyfin?" is **no** — build the data layer *underneath*
a terminal once, and render it for both audiences.

---

## Layer D — the Bloomberg-lite data spine

Today's stack (news / quotes / EDGAR / calendars / FINRA short interest) has no
fundamentals, estimates, valuation, ownership, or macro. That is the real gap
between the cockpit and a research terminal for the morning-workflow job. All
providers sit behind ONE swappable `DataProvider` interface (mirrors the existing
injectable-`fetch` pattern in `ingest/`), so no vendor lock-in.

| ID | Feature | Source (verify pricing/coverage) | Serves | Notes / invariant |
|---|---|---|---|---|
| **D-01** | Fundamentals (income/balance/cash-flow, quarterly+annual, deep history) | FMP / Tiingo / SEC financial-statement datasets | AI + human | Store `report_date` AND `available_date` — **point-in-time, no look-ahead** |
| **D-02** | Analyst estimates & revisions (consensus EPS/rev, # analysts, target price, up/down revisions) | FMP / Finnhub-paid / Tiingo | AI + human | The hardest free gap — the one paid feed worth buying |
| **D-03** | Valuation & ratios (P/E, EV/EBITDA, P/S, P/B, PEG, FCF yield, div yield) | **Derived** from D-01 + quotes | AI + human | Deterministic, **no LLM** — pure functions like `risk/` |
| **D-04** | Ownership & insider (13F institutional [lagged]; **Form 4 insider** from EDGAR; short interest [have]) | **EDGAR (free, already parsed)** + FINRA | AI + human | Insider is high-signal + free — you already ingest EDGAR |
| **D-05** | Corporate actions (splits, dividends, ex-dates) | Prices provider | AI + human | Closes OPS-07 correctness gap ([[security-hardening-backlog]]) |
| **D-06** | Macro & rates (Treasury curve, key econ series, sector-ETF performance) | **FRED (free)** + quotes | AI + human | Free, excellent; powers the daily regime read (A-06) |
| **D-07** | Sector / market-cap / index-constituent mapping | FMP / provider metadata | AI + human | Feeds screener (H-04), concentration checks |
| **D-08** | Technical indicators (MA, RSI, ATR, 52-wk range, volume trend) | **Derived** from D-09 bars | AI + human | Deterministic; ATR can feed stop-sizing / R-multiple (E-01) |
| **D-09** | Historical OHLCV bars (deep history) | Alpaca / Tiingo / Polygon | AI + human | Substrate for charts (H-07), technicals (D-08), backtest-lite (X-02) |
| **D-10** | Alt-data (options flow, unusual volume, social sentiment) — **optional/late** | Various | AI | UNTRUSTED; heavy injection-wrap (SEC-02); low priority, high noise |

**Cost discipline.** FRED + EDGAR are free; one prosumer fundamentals+estimates
vendor (FMP/Tiingo class, ~$20–60/mo — verify) closes D-01/D-02/D-03/D-07 at once.
Target: a large capability jump for **~$30–60/mo**, preserving the near-$0 thesis.

**D-layer cross-cutting requirements**
- **Point-in-time correctness** everywhere (`report_date` vs `available_date`) —
  without it, X-02 backtests and E-02 replays lie by look-ahead.
- **Untrusted-input handling** — every free-text field re-entering a prompt is
  wrapped and canary-scanned (SEC-02), same as news.
- **Slow-data caching** — fundamentals change quarterly; cache hard, respect
  vendor caps via existing `ingest/ratelimit.py`.

---

## Layer H — the human surface (trade alongside / query the AI)

Discord is excellent for *push* (alerts, approvals) and poor for *browsing*
(tables, charts, screeners). The human-trading-alongside requirement needs a
richer read surface plus new command flows. All of this reads the D-layer store;
none of it bypasses the order path.

| ID | Feature | Surface | Serves the goal | Invariant |
|---|---|---|---|---|
| **H-01** | `/research <ticker>` — on-demand structured deep-dive (fundamentals, valuation vs peers/history, estimate trend, recent filings/news, technicals, risk flags) | Discord thread | "Know what's happening" without a terminal | Grounded in D-layer with provenance (Inv 6); downgrade-only (Inv 3) |
| **H-02** | Read-only analytics **dashboard (PWA)** — watchlist board, company pages (chart+fundamentals+estimates), portfolio analytics, screener | Private overlay (Tailscale) / loopback | The "Koyfin-lite" browse surface, over YOUR data | **Zero inbound ports** ([[security-architecture]] §6); read-only |
| **H-03** | **Human-initiated trade** — `/trade <side> <ticker> <size>`: human initiates, AI structures → grounding (E-04) → **same risk engine + approval + gateway** | Discord | "Trade alongside the AI" | New *entry* into the SAME single order path (Inv 1) — paper-first, gated identically |
| **H-04** | `/screen <criteria>` — deterministic screener over fundamentals/valuation/technicals; optionally hand hits to A-01 for a research pass | Discord / PWA | Human idea generation | Pure query, no LLM on the numbers |
| **H-05** | Watchlist "what changed since yesterday" (price, estimate revisions, new filings, technical triggers) | Discord + brief | Concentrates attention on deltas | Deterministic diff |
| **H-06** | `/portfolio` — exposure by sector/factor, concentration, correlation, cash, unrealized P&L, benchmark-relative | Discord / PWA | Situational awareness | Ties to E-01 scorecard |
| **H-07** | Interactive charts (price + technicals + event markers: earnings/filings/your entries) | PWA | Trading-alongside needs to *see* it | Render-only |
| **H-08** | `/compare A B C` — side-by-side valuation/growth/margins | Discord / PWA | Fast relative judgment | Deterministic table |
| **H-09** | `/ask-data <q>` — NL question → AI writes a deterministic query → engine returns numbers with provenance | Discord | Koyfin-copilot, done safely | **AI writes query, engine computes** — no hallucinated figures |

**H-03 is the load-bearing new flow.** It inverts the default (AI proposes → human
approves) into (human proposes → AI structures & risk-checks → human confirms), but
routes through the *identical* `risk → approval → OrderGateway` path, so Invariant 1
and the whole audit/gate machinery apply unchanged. It ships in **paper mode** and
climbs the same ladder as AI-initiated trades.

---

## Layer N — notification & workflow-fit (the "student life" layer)

This layer is the *direct* answer to "decrease babysitting, get good daily
summaries, procedural intraday calls on significant things, fit a student
workflow." It is high-value, low-risk, and should be built **early** — it pays off
before live trading is ever unlocked.

| ID | Feature | What it does |
|---|---|---|
| **N-01** | Significance-tiered push (`critical` / `material` / `info`) | Only critical/material interrupt; info batches. Tunable `/notify threshold`. Noise ↓ = babysitting ↓ |
| **N-02** | Quiet hours / schedule-aware delivery | Configurable class-block calendar; no pings during class, batched at breaks; brief timed before first class |
| **N-03** | Intraday **digest batching** | Non-critical items roll into hourly/lunch digests instead of 15-min pings; only true breaks (halt on a holding, big move, fired invalidation) interrupt |
| **N-04** | **EOD daily wrap** | End-of-day: what happened to holdings/watchlist, what the AI did (proposals/alerts), P&L, tomorrow's calendar. "Know everything that happened" without watching |
| **N-05** | Weekly review digest | Scorecard (E-01) + thesis health (E-05) + what's working; fits a weekly rhythm |
| **N-06** | Priority-holdings focus | Events on YOUR positions/watchlist elevated; everything else ambient |
| **N-07** | Mobile-ergonomic summaries | Short, skimmable, phone-first; optional TTS/audio brief for a commute |
| **N-08** | **`/catchup`** | "I've been in class — what did I miss?" Summarize everything since last check. Built for intermittent attention |
| **N-09** | Phone-push escalation | Critical events (breaker trip, halt on a holding, invalidation fired) go beyond Discord to a phone push (ntfy/Pushover) so they're never missed. Pairs with E-03 heartbeat |

**Design rule:** significance tiering is **deterministic** (event type + holdings
match + magnitude thresholds), not an LLM's mood — so "you will be interrupted"
is a predictable contract, and the risk of a missed critical event is engineered,
not vibed.

---

## Layer A — AI capability upgrades (supercharge the reasoning)

Now that the D-layer exists, the AI can reason over far more than news. These
extend the existing triage/deep workflows and cross-reference the E-series.

| ID | Feature | Notes |
|---|---|---|
| **A-01** | On-demand research agent (powers H-01/H-09) | Schema-gated, no tools, provenance-cited, downgrade-only |
| **A-02** | Thesis memory & drift tracking | Extends **E-05**: persistent per-name thesis state; AI flags when reality diverges from the entry thesis |
| **A-03** | Multi-source synthesis brief | Upgrade morning brew to fuse news + filings + estimates + technicals + macro into one regime read |
| **A-04** | Personalized alert ranking | **Deterministic** preference model over your approve/veto history (E-01 concordance) — NOT an LLM on the safety surface |
| **A-05** | Cross-model proposal verification | = **E-04** (reference, not a new definition) |
| **A-06** | Daily macro/regime brief | From D-06 (rates, sectors) — cheap big-picture context for a busy student |
| **A-07** | Scenario / what-if Q&A | `/ask-deep "what if AAPL misses?"` over your holdings + data — advisory only |

---

## Layer X — cross-cutting (robustness & true optimization)

| ID | Feature | Notes |
|---|---|---|
| **X-01** | `DataProvider` abstraction + hash-locked data cache | Extends **E-08** failover to the D-layer; swappable vendors |
| **X-02** | Backtest-lite / point-in-time replay over D-layer | Extends **E-02**; only honest with D-01's `available_date` discipline |
| **X-03** | Unified cost governor (data API $ + AI tokens) | Extends **E-07**; one daily budget across feeds + models; fail to watch-only on exhaustion (Inv 7) |
| **X-04** | Invariant conformance for all new surfaces | Untrusted-wrap D-feeds; no-LLM-in-risk holds; H-03 preserves single order path; all audited |

---

## The overarching plan — sequenced tracks

The critical path is **make it run → keep it safe → then trade autonomously**. The
exciting new capability (data + human + notifications) is a **parallel value track**
that delivers the babysitting-reduction payoff *long before* live trading unlocks.

```text
Track 0  Make it actually run      ──┐  (prerequisite: nothing works until this does)
Track A  Data spine (D)             ─┼─► Track B  Smarter AI briefs (A)
Track C  Notification policy (N)    ─┘        │
Track D  Human surface (H)  ◄────────────────┘
Track E  Safety go-live (W-series)  ── the existing gated ladder; autonomy lands HERE, last
Track F  Optimization (X + rest of E)
```

### Track 0 — Make it actually run *(prerequisite, ~1–2 wks)*
Wire `ingest/` → scheduler (today `build_ingest_fetchers` is called by nothing);
operationalize + test `main.run()`; provision Alpaca/Finnhub/Discord keys; get the
**morning brew landing in Discord daily against live feeds**. This is
[[path-to-production]] steps 2–5 — features on a fixture-only system are worthless.

### Track A — Data spine *(the foundation, ~2–4 wks)*
Order by value/effort: **D-06 macro (FRED, free, fast)** → **D-04 insider (EDGAR,
already parsed)** → **D-01 fundamentals + D-02 estimates (one paid vendor)** →
**D-03 valuation + D-08 technicals (derived, deterministic)** → **D-05 corp actions**
→ **D-09 price history**. Build **X-01 provider abstraction** from day one.

### Track B — Smarter AI briefs *(uses D, ~1–2 wks)*
**A-03 synthesis brief** + **A-06 macro brief** upgrade the daily read; **A-01
research agent** powers on-demand deep-dives. This is where "supercharge with AI"
becomes real — better *inputs*, same safe *envelope*.

### Track C — Notification policy *(the babysitting-killer, ~1–2 wks — build early)*
**N-01 tiering** → **N-04 EOD wrap** → **N-08 catch-up** → **N-02 quiet hours** →
**N-09 phone push**. Directly implements the student-workflow requirement and pays
off immediately, independent of trading.

### Track D — Human surface *(trade-alongside, ~3–5 wks)*
**H-01 `/research`** + **H-05 deltas** + **H-06 `/portfolio`** (Discord, cheap) →
**H-04 screener** + **H-08 compare** → **H-03 human-initiated trade (paper)** →
**H-02 PWA dashboard + H-07 charts** (richest, last). H-02 is the "Koyfin-lite"
browse surface — over your own data, zero-inbound.

### Track E — Safety go-live *(the existing gated ladder — the real gate on autonomy)*
Unchanged from [[production-work-packages]] / [[path-to-production]]: **SEC-11
witness activation** → 2-wk paper soak → performance gate → `review_live` →
`auto_veto` → `auto_full`. **Autonomous Robinhood MCP trading lands here, last, and
only through the gates.** Tracks A–D make the AI smarter and the human better-
informed — which *raises the quality of what flows through the gates* — but they do
**not** skip a single gate.

### Track F — Optimization & robustness
**X-02 backtest-lite**, **X-03 cost governor**, plus the remaining E-series
(E-01 scorecard, E-10 shadow-live, E-11 post-mortem) as their ladder rungs approach.

---

## Priority & effort snapshot

| Priority | Features | Why now |
|---|---|---|
| **P0 (do first)** | Track 0; D-06, D-04, D-01, D-02; N-01, N-04, N-08 | Make it run + the data + the notification policy = the babysitting-reduction payoff, all pre-live-safe |
| **P1** | D-03, D-08, D-05, D-09; A-03, A-06, A-01; H-01, H-05, H-06; N-02, N-09; X-01, X-03 | Rounds out the data spine, the smarter briefs, and the core human surface |
| **P2** | H-02, H-07, H-03, H-04, H-08, H-09; A-02, A-04, A-07; N-03, N-05, N-06, N-07; X-02 | Rich browse surface, human-initiated trades (paper), personalization |
| **Gated / late** | Track E ladder; E-01/E-04/E-10/E-11; D-10 | Autonomous trading + its safety enhancements; alt-data last |

## Honesty ledger — what this plan does NOT do

- It does **not** manufacture alpha. Better data improves inputs; there is still no
  verified evidence LLM agents beat the market net of costs ([[upside-and-roi]] §3).
  The win is time + awareness + discipline.
- It does **not** shortcut the ladder. Autonomous trading stays behind SEC-11, the
  paper soak, and the performance gate — a smarter agent trading autonomously is
  *more* dangerous if the gates aren't real.
- It does **not** open the box. The human dashboard is overlay/loopback only.
- It does **not** put an LLM on the risk path or bypass the single order path —
  including for human-initiated trades (H-03).
- It is **not** a Koyfin clone. It builds the data layer once and renders it for
  both the AI and the human; the terminal-style browse view is a thin read surface,
  not a re-implemented product.

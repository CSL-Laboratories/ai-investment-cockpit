---
title: RW-19 event-source characterization — measurement ledger
type: research
status: live
last-verified: 2026-08-12
---

# RW-19 event-source characterization

This is the dated evidence ledger for `RW-19`. It does not implement a trigger, choose an
order policy, or authorize live capital. Every observation distinguishes vendor documentation,
an account-specific probe, and an inference from a bounded sample. `DEC-07` remains open until
the operator universe and the stream/replay/load spikes below are measured.

## Environment and scope

| Field | Observation |
|---|---|
| Local date / host | 2026-08-12, Windows development host |
| Cockpit mode | `read_only` |
| Alpaca credentials | Provisioned in the OS credential store; values never printed or persisted |
| Finnhub credential | Provisioned in the OS credential store; value never printed or persisted |
| Operator watchlist | **Empty — blocker for an operator-universe conclusion** |
| SEC declared user agent | **Absent — blocker for a compliant live SEC discovery capture** |
| Preliminary universe | `AAPL`, `MSFT`, selected only because the repository's existing market-day fixture labels them as holdings; this is not a production-universe decision |

## Current primary-source contract

Alpaca currently documents Basic equities as live IEX with 30 WebSocket symbols and the paid
Algo Trader Plus tier as all-US-exchange SIP with unlimited symbols. Its FAQ states that recent
SIP endpoints require the subscription while SIP history older than the delay is queryable.
The market stream is `wss://stream.data.alpaca.markets/v2/{feed}` and most plans allow one
connection per endpoint. Sources: [Market Data API](https://docs.alpaca.markets/us/docs/about-market-data-api),
[Market Data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq), and
[market-data WebSocket](https://docs.alpaca.markets/us/v1.1/docs/streaming-market-data).

The news WebSocket and historical API both expose an article `id`, but documentation alone does
not prove that update/correction identity and availability are equivalent across the two
surfaces. That remains an empirical spike. Sources:
[real-time news](https://docs.alpaca.markets/us/docs/streaming-real-time-news) and
[historical news](https://docs.alpaca.markets/us/docs/historical-news-data).

Paper order changes are available on Alpaca's `trade_updates` WebSocket, and REST/client order
IDs provide a candidate convergence key. Accessible history depth, lost-event convergence, and
intermediate-state limitations remain unmeasured. Sources:
[WebSocket trading stream](https://docs.alpaca.markets/us/v1.4.2/docs/websocket-streaming) and
[working with orders](https://docs.alpaca.markets/us/v1.1/docs/working-with-orders).

## Account-specific capability probe — 2026-08-12

The probe used authenticated GET requests only and printed no response body containing account
data.

| Probe | Result |
|---|---|
| Latest AAPL IEX quote | HTTP 200; source timestamp `2026-08-12T20:00:04.07100675Z` |
| Latest AAPL SIP quote | HTTP 403 |
| Completed historical IEX bars | HTTP 200 |
| Completed delayed historical SIP bars | HTTP 200 |
| Historical news for AAPL/MSFT | HTTP 200; 10/10 sampled rows carried an article ID |

Conclusion: the current account can compare delayed IEX and SIP history, but it cannot operate
a live SIP trigger. Paying for SIP is therefore a real entitlement choice, not a configuration
toggle. This establishes capability only; it does not yet decide whether IEX recall is adequate.
The live quote results are retained locally in hash-verified schema-v2 manifest
`capabilities/2026-08-13/20260813T023423Z`; entitlement fields are derived from its raw HTTP
statuses rather than hard-coded into later captures.

## First IEX/SIP measurement — session 2026-08-11

Capture command:

```powershell
.\.venv\Scripts\python.exe tools\rw19_capture.py alpaca-bars `
  --date 2026-08-11 --symbols AAPL MSFT
```

The tool writes immutable raw responses and a hash-bound summary under ignored
`data/evidence/rw19/`. Raw market data is not committed or redistributed. The retained local
schema-v2 manifest is `20260813T023430Z`; its samples are 783 IEX and 1,628 SIP full-UTC-day minute bars.
The regular-session comparison uses the repository's verified NYSE calendar and includes 780
bars per feed.

| Regular-session measure | AAPL + MSFT result |
|---|---:|
| IEX / SIP reported volume | 4.415% |
| Same-minute close error p50 / p95 / p99 / max | 0.327 / 2.391 / 4.387 / 9.488 bps |
| SIP 10 bps minute-move triggers | 48 |
| IEX recall at 10 bps | 39/48 = **81.25%**; 9 false negatives |
| SIP 25 bps minute-move triggers | 5 |
| IEX recall at 25 bps | 4/5 = **80%**; 1 false negative |
| SIP 50 bps minute-move triggers | 1 |
| IEX recall at 50 bps | 1/1; sample too small for a conclusion |
| SIP 100 bps minute-move triggers | 0; no recall claim possible |

The threshold sweep is a measurement device, not production trigger policy. The one-day,
two-symbol result is nevertheless enough to reject two unsafe assumptions: IEX volume is not a
liquidity proxy for consolidated volume, and live-IEX event recall cannot be presumed to be
100%. It is not enough to price SIP or resolve `DEC-07`.
All reported regular-session false-negative threshold instances had a same-minute IEX bar whose
absolute move was below the SIP-defined threshold; none was caused by a missing regular-session IEX minute. The
full-UTC-day comparison is retained for inspection but must not be quoted as recall: extended-
hours bar availability differs. Recall is direction-agnostic coverage evidence, not proof that a
specific future trading predicate would agree.

## Reproducible capture contract

The `alpaca-bars` command requires an explicit completed date and explicit symbols. Every
command reads credentials only from the OS credential store and is read-only: historical GET,
latest-quote GET, or bounded WebSocket subscription—never an account/order mutation. Captures
separate raw JSON from `summary.json` and bind every file by SHA-256 in `manifest.json`.
`app/ingest/characterization.py` computes full-day and verified regular-session distributions
without NumPy/pandas or platform tzdata. Synthetic tests pin threshold recall, missing-minute
semantics, calendar bounds, raw/summary separation, hash binding, and overwrite refusal.

Additional bounded commands use the same evidence contract:

```powershell
# Always-available wire/auth test; never a production-density measurement.
.\.venv\Scripts\python.exe tools\rw19_capture.py alpaca-market `
  --feed test --symbols FAKEPACA --duration-seconds 60

# Run during the measurement window over the explicit operator universe.
.\.venv\Scripts\python.exe tools\rw19_capture.py alpaca-news `
  --symbols AAPL MSFT --duration-seconds 3600

# Re-check every raw/summary SHA-256 before using a local capture.
.\.venv\Scripts\python.exe tools\rw19_capture.py verify path\to\manifest.json
```

### Apparatus checks — 2026-08-12

- A 30-second after-hours AAPL/MSFT news capture returned zero WebSocket frames and zero REST
  rows. The manifest correctly reports `stream_recall_of_rest=null`; it is retained locally as
  an insufficient sample and makes no quiet-day or identity-equivalence claim.
- A 12-second `FAKEPACA` test-stream capture returned two quotes and two trades. Its manifest
  verified cleanly; observed source-to-receive latency was ~0.373 seconds median and ~0.374
  seconds maximum. This checks authentication, parsing, clock capture, and file integrity only.
  Alpaca documents the test stream as always available; these synthetic timings cannot set a
  production latency target.

## Remaining spikes and blockers

| Spike | State | Evidence required before closure |
|---|---|---|
| Operator-universe IEX/SIP | blocked — watchlist empty | Repeat completed-session captures across the explicit bring-up universe until each candidate threshold has a nondegenerate distribution; price SIP against measured false negatives |
| Market WS | pending market hours | Quote/trade/status burst and inter-arrival distributions, corrections, reconnect trace, REST identity/backfill limits, IEX connection-limit behavior |
| News WS ↔ REST | pending | Article/update identity equivalence, correction behavior, reconnect gaps, burst distribution, history coverage |
| SEC discovery | blocked — no declared UA | Re-verify official Latest Filings Atom/RSS, measure latency/cadence under the declared UA, and retain submissions as correctness/backfill |
| Paper order events ↔ REST | pending safe paper activity | Fill/partial/cancel/reject ordering and latency, accessible REST history, injected event loss and terminal convergence; no live order |
| SQLite / triage load | pending recorded stream | Replay recorded distributions through one writer; measure contention/tick duration before setting queue/coalescing bounds |
| Calendar drift | partially closed by RW-17 | Current/next-year official comparison exists; retain rollover capture in the RW-13 fault suite |

## DEC-07 status

**Open.** Current account capability is Basic/live-IEX shaped and live SIP is denied. Preliminary
regular-session data shows material IEX false negatives at small one-minute movement thresholds,
but the operator universe is unspecified and the sample is only one day. No production adapter
may encode either “IEX is sufficient” or “SIP is required” from this sample. The next operator
input is an explicit paper bring-up watchlist; the next market-hours action is a bounded IEX
stream capture plus delayed SIP/REST comparison over the same symbols and interval.

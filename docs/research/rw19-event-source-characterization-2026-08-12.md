---
title: RW-19 event-source characterization — measurement ledger
type: research
status: live
last-verified: 2026-08-17
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
| Operator-universe IEX/SIP | two completed sessions captured; non-equivalence confirmed | Repeat across regimes until each candidate threshold has a stable distribution; price SIP against measured false negatives |
| Market WS | repeated live samples plus deliberate reconnect/REST recovery captured; maximum observed one-second burst 1,114 frames | Add corrections, broader-regime distributions, and IEX connection-limit behavior |
| News WS ↔ REST | one-hour sample captured 10 stream/9 REST identities; all REST IDs overlapped and one persistent stream-only ID rejected equivalence | Repeat across regimes; capture correction/update and reconnect behavior before setting loss/backfill policy |
| SEC discovery | declared UA configured; submissions polling healthy | Re-verify official Latest Filings Atom/RSS, measure latency/cadence under the declared UA, and retain submissions as correctness/backfill |
| Paper order events ↔ REST | pending safe paper activity | Fill/partial/cancel/reject ordering and latency, accessible REST history, injected event loss and terminal convergence; no live order |
| SQLite / triage load | 1,114-frame replay measured 74.67 one-event writes/s and a 236,637-character raw triage packet | Specify and separately test bounded filtering/coalescing before any dynamic trigger work |
| Calendar drift | partially closed by RW-17 | Current/next-year official comparison exists; retain rollover capture in the RW-13 fault suite |

## Operator-universe update — 2026-08-14

The two environment blockers recorded on 2026-08-12 changed: the explicit paper bring-up
universe is now `AAPL, AVGO, INTC, META, MRVL, MSFT, MU, NVDA, SNDK, SPCX, TSLA`, and a declared
SEC User-Agent is configured. The contact address is operator configuration and is deliberately
not duplicated in this evidence ledger. A live `read_only` process completed 16 EDGAR polls with
zero errors and a trustworthy quiet-day result; because no material delta appeared, the RW-11
precision/citation gate remains open.

### Completed-session IEX/SIP capture — session 2026-08-13

The hash-verified schema-v2 manifest is
`data/evidence/rw19/2026-08-13/20260814T182858Z/manifest.json` (ignored raw evidence, retained
locally). It contains 4,255 IEX and 9,623 SIP full-UTC-day minute bars across the 11-symbol
operator universe. Regular-session aggregate results:

| Measure | Result |
|---|---:|
| IEX / SIP reported volume | 3.742% |
| Same-minute close error p50 / p95 / p99 / max | 0.898 / 7.891 / 17.834 / 57.516 bps |
| SIP 10 bps triggers; IEX recall | 1,209; 907/1,209 = 75.02% |
| SIP 25 bps triggers; IEX recall | 329; 241/329 = 73.25% |
| SIP 50 bps triggers; IEX recall | 76; 54/76 = 71.05% |
| SIP 100 bps triggers; IEX recall | 3; 2/3, too small for a conclusion |

At 10 bps, 302 SIP triggers were false negatives for IEX. Only 20 involved a missing IEX
minute; 282 had an IEX minute whose move stayed below the SIP-defined threshold. This is a
feed-coverage difference, not primarily a timestamp-gap artifact. Symbol results varied widely:
10 bps recall ranged from 46.96% for MRVL to 91.01% for SPCX. One completed session is enough
to reject IEX/SIP equivalence, but not enough to price a SIP subscription or select a production
trigger threshold.

### Live IEX WebSocket capture — 2026-08-14 18:29–18:30 UTC

The hash-verified schema-v2 manifest is
`data/evidence/rw19/market/2026-08-14/20260814T182923Z/manifest.json`. Over the bounded 60-second
window it captured 8,762 unique data frames: 8,439 quotes, 312 trades, and 11 bars. There were
zero invalid records, duplicate frames, or corrected identities. The maximum one-second burst
was 509 frames; inter-arrival p50/p95/p99 was 0.000/0.043/0.105 seconds. Source-to-receive
p50/p95/p99 over all recorded message types was 0.101/0.106/0.114 seconds. The 60.294-second
maximum belongs to the mixed-type initial sample and is not interpreted as quote latency without
type-separated analysis.

This is a real operator-universe density/latency sample. Reconnect/gap injection, REST backfill
identity, multi-session distributions, and SQLite/triage replay under the measured 509-frame
one-second burst remain open.

## Repeated operator-universe measurements — 2026-08-17

All 13 retained RW-19 manifests re-verified against their SHA-256 inventories with zero
failures before these results were used. The account capability probe again returned HTTP 200
for a live IEX quote and HTTP 403 for the equivalent live SIP quote. The entitlement boundary
therefore remained unchanged on the measurement date.

### Second completed-session IEX/SIP capture — session 2026-08-14

The hash-verified schema-v2 manifest is
`data/evidence/rw19/2026-08-14/20260817T144920Z/manifest.json` (ignored raw evidence, retained
locally). Across the 11-symbol operator universe, the regular session reported IEX volume of
13,287,057 and SIP volume of 390,437,575, a **3.4031%** fraction. Same-minute close error was
0.769 / 5.983 / 13.038 / 36.633 bps at p50 / p95 / p99 / maximum.

| Regular-session threshold | SIP triggers | IEX hits | Recall | False negatives |
|---|---:|---:|---:|---:|
| 10 bps | 1,033 | 782 | **75.70%** | 251: 4 missing IEX minutes, 247 present below threshold |
| 25 bps | 258 | 199 | **77.13%** | 59: all present below threshold |
| 50 bps | 58 | 47 | **81.03%** | 11: all present below threshold |
| 100 bps | 7 | 6 | 85.71% | sample too small for a conclusion |

Combining the two completed operator-universe sessions (2026-08-13 and 2026-08-14) produces
29,994,911 IEX-reported shares versus 836,896,774 SIP-reported shares, a **3.5841%** fraction.
The combined 10 / 25 / 50 bps recall is 1,689/2,242 (**75.33%**), 440/587 (**74.96%**), and
101/134 (**75.37%**) respectively. At 10 bps, 529 of 553 false negatives had an IEX minute
whose move remained below the SIP threshold; only 24 were missing-minute cases. The two-day
result strengthens the non-equivalence finding but is not a stable-regime study and does not
by itself select a subscription or trigger threshold.

### Repeated live IEX and news captures — 2026-08-17

Two additional bounded 60-second market captures retained 14,988 and 17,336 unique data
frames. The first contained 14,135 quotes, 842 trades, and 11 bars with a maximum one-second
burst of 901 frames. The second contained 16,540 quotes, 785 trades, and 11 bars with a maximum
one-second burst of 1,114 frames. Both reported zero invalid records, duplicate frames, or
corrected identities. These observations raise the measured replay/load target above the first
509-frame burst, but they still do not measure a deliberate disconnect, reconnect loss, or REST
backfill identity.

A bounded 180-second news capture observed one WebSocket identity and zero REST identities.
That sample verifies the capture path but cannot establish stream/REST recall, correction
semantics, or quiet-day behavior.

## Deliberate reconnect and measured-load evidence — 2026-08-18

The hash-verified reconnect manifest is
`data/evidence/rw19/reconnect/2026-08-18/20260818T150425Z/manifest.json`. Two 30-second
11-symbol IEX segments retained 11,388 and 11,214 frames around a deliberate five-second socket
close. A GET-only quote/trade history fetch over the combined window returned 25,050 distinct
measurement identities; 22,436 overlapped the two stream segments (89.56% REST-window recall).

The conservative blind window through the first post-reconnect receive was 5.392 seconds. REST
contained 2,035 identities in that interval, of which 1,965 were absent from the stream segments
and recoverable by history. Seventy source-timestamp identities from the interval arrived after
reconnect, so the measurement does not equate source-time and receive-time ordering. This proves
that scoped REST recovery is available; it does not yet choose the exact future cursor/boundary
contract.

The isolated load manifest is
`data/evidence/rw19/load/2026-08-18/20260818T150927Z/manifest.json`. Replaying 1,114 distinct
recorded frames through the production one-event-per-call persistence shape and one SQLite WAL
writer produced no integrity/write failure, but required 14.919 seconds: 74.67 events/s, with
15.996 ms p95 and 18.128 ms p99 call latency. The no-network, schema-valid triage tick itself
took 0.217 seconds but constructed a 236,637-character raw-frame packet. These are negative
capacity/design results: direct raw-frame persistence and prompt fanout cannot be the dynamic
trigger. Bounded deterministic filtering/coalescing must be specified before `RW-13`.

A five-minute news attempt retained a hash-valid 0-WebSocket/0-REST sample at
`data/evidence/rw19/news/2026-08-18/20260818T150726Z/manifest.json`. Its recall remains null;
the sample is insufficient and makes no quiet-day or identity-equivalence claim.

A later one-hour capture retained ten unique WebSocket identities and nine REST identities at
`data/evidence/rw19/news/2026-08-18/20260818T152559Z/manifest.json`. All nine REST identities
appeared on the stream. Identity `61278730` was stream-only and remained absent from a repeated
GET of the exact frozen window several hours later, rejecting full identity equivalence for this
sample. The stream had zero duplicates, invalid records, or observed corrected identities;
source-to-receive delay was 0.320 seconds p50 and 0.480 maximum. This is useful identity evidence,
but ten articles and zero corrections cannot set a loss rate or prove correction/reconnect
semantics.

A purpose-built read-only news reconnect collector then opened two independently authenticated
five-minute WebSocket segments around a deliberate gap and fetched REST over the exact combined
window. The run from 22:05:51 through 22:15:56 UTC measured a 5.000752-second disconnect, and all
four manifest items re-verified, but both stream segments and REST returned zero identities. The
local manifest is
`data/evidence/rw19/news-reconnect/2026-08-18/20260818T220551Z/manifest.json`; its canonical
summary SHA-256 is `c0ecabcdef47e4d8c1a043a74c5e15679625af53cb616ba239bbfc49cfd1e38e`.
This proves only that the bounded capture protocol completed; it is not a quiet-day claim and
cannot establish news correction, reconnect delivery, or REST-backfill behavior.

A second run expanded the two WebSocket segments to 15 minutes each over the same 11-symbol
universe. It ran from 22:47:04 through 23:17:10 UTC around a measured 5.000983-second gap. Both
segments and REST again returned zero identities. All four items verify under local manifest
`data/evidence/rw19/news-reconnect/2026-08-18/20260818T224704Z/manifest.json`; its canonical
summary SHA-256 is `5387f8718871c7100c16b47ee60adf039ebd30b39c76705e944d217d9a08a4ce`.
The repeated negative result confirms only that this after-hours regime is unsuitable for the
required identity observation; it does not establish a quiet day or reconnect semantics.

## DEC-07 status

**Open.** Current account capability is Basic/live-IEX shaped and live SIP is denied. Two
completed operator-universe sessions show material, repeatable IEX false negatives at small
one-minute movement thresholds. The deliberate reconnect now proves REST-recoverable blind-window
identities, the measured 1,114-frame replay proves the current one-event writer/raw prompt
shape is too slow and too large for direct dynamic fanout, and the one-hour news sample rejects
WS/REST identity equivalence. Both later news reconnect captures were empty and therefore do not
close that gate. No production adapter may encode
either “IEX is sufficient” or “SIP is required” from this evidence. The next evidence is broader
regime coverage, a non-empty news correction/reconnect sample, and a bounded
filtering/coalescing contract; designing or implementing that contract is later `RW-13`, which
remains blocked.

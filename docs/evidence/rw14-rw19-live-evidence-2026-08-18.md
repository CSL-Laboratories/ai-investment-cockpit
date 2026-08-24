---
type: evidence
status: live
last-verified: 2026-08-24
---

# RW-14 stale-feed and RW-19 reconnect/load evidence — 2026-08-18 through 2026-08-24

## Scope and safety baseline

These bounded Windows captures ran from exact source commit
`0cd0f03eca0133c873a69ef0eb86b0b3d31b8965`. Local `main` and `origin/main` matched before
the run. The operational audit chain was valid at sequence 10,097 before RW-14, mode was
`read_only`, HALT was absent, the 11-symbol awareness universe was configured, and the merger
execution watchlist was empty. Proposal, risk-decision, approval, Order, and merger-selection
commitment counts were all zero.

Raw vendor responses remain under ignored `data/evidence/` or `app/data/evidence/` paths. Only
this sanitized summary is tracked. No credential, price, quote size, article content, or raw
vendor payload is included here.

## RW-14 live quote progression and stale-to-L8 transition

The composed production market-data service completed two live Alpaca IEX quote polls across
all 11 configured symbols. Every `quant_book_seq` advanced from 1 to 2. Ten exchange source
timestamps advanced during the three-second comparison; the remaining source quote was still
fresh, and the maximum authority age after the second poll was 7.586 seconds.

The diagnostic then replaced only the in-process `latest_quote` method with copies of those
last real vendor quotes. Prices and exchange timestamps were not invented or advanced. This was
an injected frozen-feed fault over real exchange timestamps, not a claim that Alpaca suffered
an outage. At 123.807 seconds of maximum quote age against the 120-second limit:

- the market-data L8 path recorded `watch_only` at operational audit sequence 10,134;
- effective mode remained `read_only`;
- no ordinary breaker or order-path row was created;
- a Finnhub awareness poll still completed and emitted 100 events; and
- the Alpaca news supervisor remained alive with zero errors.

The real quote method was restored immediately. The first recovery poll refreshed all 11
symbols, reduced maximum quote age to 3.065 seconds, cleared only the `market_data` watch-only
window, and retained `read_only`. The signed recovery landed at audit sequence 10,217. The final
chain verified, all five order-path/selection counts remained zero, and the diagnostic process
stopped with loopback port 8787 free.

The local RW-14 manifest is
`app/data/evidence/rw14/2026-08-18/20260818T145531Z/manifest.json`; its summary SHA-256 is
`19c44389cca89438bc4b0560bb408d2e62a437a91d214222c8ac28176d278487`.

This closes RW-14's named live progression and real-age stale-to-L8 evidence. It does not
replace that package's historical hosted/macOS no-step result; `DEC-09` separately supplies the
current release-blocking local platform gate.

## RW-19 deliberate reconnect and REST backfill

The read-only RW-19 collector opened the 11-symbol Alpaca IEX stream for 30 seconds, closed the
socket deliberately, waited five seconds, then authenticated and subscribed on a new socket for
another 30 seconds. It captured 11,388 frames before the disconnect and 11,214 after reconnect.
Historical quote and trade data were then fetched with GET-only IEX endpoints over the exact
combined window.

The comparison found 22,602 distinct stream measurement identities and 25,050 REST identities,
with 22,436 exact `type:symbol:exchange_timestamp` overlaps: 89.56% stream recall of the REST
window. Quotes and trades separately measured 89.58% and 89.19%. The conservative blind window
from socket close through first post-reconnect receive was 5.392 seconds. REST exposed 2,035
identities in that interval; 1,965 were absent from the two stream segments and therefore
recoverable through REST. Seventy source-timestamp identities from that interval appeared after
reconnect, so the evidence does not assume that receive order and source time are identical.

The observed maximum one-second burst in this sample was 802 frames. All five raw/summary
manifest items re-verified. The local manifest is
`data/evidence/rw19/reconnect/2026-08-18/20260818T150425Z/manifest.json`; its canonical summary
SHA-256 is `11e0a6bba6defe986e0f00ccc4377f6bc651825eb959cb04f52b5fc04773b6b3`.

## RW-19 measured single-writer and triage replay

Exactly 1,114 distinct frames from the hash-verified reconnect capture were compressed into one
isolated load vector, matching the previous maximum observed burst floor. The replay used an
isolated SQLite WAL database and the production `IngestRunner` persistence shape: one event per
`persist_events` call through a single writer. It never opened operational `app/cockpit.db`.

All 1,114 events persisted without a duplicate or write error, but the measured capacity was
only 74.67 events/second: 14.919 seconds total, 15.996 ms p95 and 18.128 ms p99 per call. This is
a useful negative capacity result. Direct per-frame persistence cannot absorb a 1,114-frame
one-second burst synchronously; a future dynamic trigger needs bounded deterministic
coalescing/queue semantics before `RW-13`, not a larger unbounded writer loop.

One static, schema-valid local transport then exercised the triage orchestration without a model
process or network call. The tick covered all 1,114 rows in 0.217 seconds, but constructed a
236,637-character prompt. No item escalated and no proposal, risk decision, approval, Order, or
selection commitment was created. The oversized raw-frame packet is further evidence that a
future trigger must pre-filter/coalesce before model triage; it is not permission to send such a
packet to a model.

The isolated audit chain verified at sequence 1,116. Its deliberately public diagnostic HMAC
key grants no production authority. The local manifest is
`data/evidence/rw19/load/2026-08-18/20260818T150927Z/manifest.json`; its canonical summary
SHA-256 is `88e4ee8233978a19203ca565a78c7267e7e07cc35e87176253237d2d54e4e96d`, and the retained
isolated database SHA-256 is
`0b7a47d4d76d32441945a1aec0b0b0bd495fd03c6c85659d6a8f50a2bdd36103`.

## RW-19 news identity measurements

The existing read-only news collector ran for five minutes over the same 11-symbol universe and
then fetched the exact window through the historical news GET endpoint. Both surfaces returned
zero articles. The three-file manifest verifies, but `stream_recall_of_rest` is correctly null.
This is an insufficient sample, not a quiet-day verdict and not evidence of WebSocket/REST
identity equivalence. The local manifest is
`data/evidence/rw19/news/2026-08-18/20260818T150726Z/manifest.json`; its canonical summary
SHA-256 is `789d0f305913261bb878d5cbe6821865b796ceff90594feb5507f9fb40331f52`.

A second bounded capture then kept one authenticated WebSocket subscription open for one hour,
from 15:25:59 through 16:25:59 UTC, before fetching the same frozen window through REST. It
retained ten valid, unique WebSocket article identities and nine unique REST identities. All nine
REST identities appeared on the stream, giving 100% stream recall of this small REST sample. One
WebSocket identity, `61278730`, was absent from REST. A GET-only repeat several hours later still
returned the same nine IDs and omitted that article, so the difference was persistent rather than
an immediate-indexing delay.

The stream contained zero duplicates, zero invalid records, and no observed corrected identity.
Its maximum burst was one article/second and two/minute. Source-to-receive delay across the ten
frames measured 0.320 seconds p50, 0.436 p95, 0.471 p99, and 0.480 maximum. This window rejects
full WS/REST identity equivalence and shows that REST cannot be assumed to reproduce every
streamed identity. Ten articles are still too few to set a production loss rate, and zero observed
corrections cannot establish update/correction behavior.

All three manifest items re-verified. The local manifest is
`data/evidence/rw19/news/2026-08-18/20260818T152559Z/manifest.json`; its canonical summary
SHA-256 is `c2a3f81ded6f782c8c56ffc4ae655d9747dca2e4e8fab55a91415e4d619f6dd6`.

A later purpose-built reconnect capture opened one authenticated five-minute news subscription,
closed it deliberately, waited, and opened a second independently authenticated five-minute
subscription before fetching REST over the frozen combined window. The first segment ran from
22:05:51.769252 to 22:10:51.833001 UTC, and the second from 22:10:56.833753 to
22:15:56.906312 UTC, yielding a measured 5.000752-second disconnect. Both WebSocket segments and
REST returned zero articles. `stream_recall_of_rest` is therefore correctly null; this verifies
the capture/reconnect protocol but supplies no identity, correction, late-delivery, or REST
backfill observation.

All four manifest items re-verified. The local manifest is
`data/evidence/rw19/news-reconnect/2026-08-18/20260818T220551Z/manifest.json`; its canonical
summary SHA-256 is `c0ecabcdef47e4d8c1a043a74c5e15679625af53cb616ba239bbfc49cfd1e38e`.
The empty after-hours window is an insufficient sample, not evidence that news is quiet or that
reconnect behavior is correct.

A second run lengthened each segment to 15 minutes over the same frozen 11-symbol universe. The
first segment ran from 22:47:04.900996 to 23:02:04.961668 UTC, and the second from
23:02:09.962651 to 23:17:10.013831 UTC, yielding a measured 5.000983-second disconnect. Both
WebSocket segments and the exact-window REST fetch again returned zero articles, so all identity,
correction, late-delivery, backfill, and recall claims remain unavailable.

All four second-run manifest items re-verified. Its local manifest is
`data/evidence/rw19/news-reconnect/2026-08-18/20260818T224704Z/manifest.json`; the canonical
summary SHA-256 is `5387f8718871c7100c16b47ee60adf039ebd30b39c76705e944d217d9a08a4ce`.
Repeating the result over a longer after-hours window does not turn two empty samples into a
quiet-day or reconnect-correctness claim.

## 2026-08-24 regular-session news reconnect

On clean merged main `ebda9b6691cfcfc72b76056d0190768e5f14cd66`, one separately authorized
read-only capture targeted the complete 11-symbol awareness universe during the regular session.
It opened two five-minute news WebSocket segments around one deliberate five-second disconnect,
then fetched the exact frozen combined window through the historical-news GET endpoint. The
window ran from `2026-08-24T19:46:13.750596+00:00` through
`2026-08-24T19:56:18.893202+00:00`; the measured disconnect was 5.000998 seconds.

Both WebSocket segments and REST returned zero identities. Recall is therefore correctly null,
and the result supplies no correction, late-delivery, backfill, or identity observation. This is
an active-session apparatus result, not a quiet-day claim and not evidence that the surfaces are
equivalent. The immediate verifier checked all four inventoried payloads with zero failures.
The external manifest is
`C:\Users\Lucas\Documents\ai-investment-cockpit-evidence\rw19-news-reconnect-2026-08-24-main-ebda9b6\news-reconnect\2026-08-24\20260824T194613Z\manifest.json`;
its file SHA-256 is `20f1eb335f33c1a40fe99f30d10b9178320032ae875fbd804890fc819f257b22`.
With the manifest itself, the five package files are read-only and remain outside the tracked
repository.

Preflight and postflight both retained `read_only`, an empty merger execution watchlist, zero
proposal/risk/approval/order/selection/edge rows, a valid audit chain at sequence 10,263, HALT
absent, clean `main == origin/main`, and no cockpit listener. No Discord post, watchlist mutation,
authority change, paper order, service start, or `RW-13` work occurred.

## Claims and remaining gates

- RW-14's named online progression/staleness drill is complete; its historical hosted/macOS
  attestation did not run, and `DEC-09` supplies the current release-blocking local platform gate.
- RW-19 now has a real deliberate reconnect, measured REST-recoverable gap, and measured
  single-writer/triage behavior. The capacity result is not green for direct raw-frame fanout.
- The one-hour news sample rejects full WS/REST identity equivalence and establishes one
  persistent stream-only identity. The later news reconnect collector completed two after-hours
  runs and one regular-session run, but all three reconnect samples were empty and close no
  empirical gate. Broader regimes, non-empty news correction/update and reconnect behavior, and a
  bounded coalescing design decision remain open. `DEC-07` is not resolved.
- Expected edge remains unavailable, the merger execution watchlist remains empty, and no paper
  opening Order, live authority, autonomous authority, or `RW-13` work is authorized by this
  evidence.

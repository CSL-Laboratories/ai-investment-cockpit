# Candidate: O2 multi-market evidence and replay contract

**Issue:** [CSL-214](https://linear.app/csl-laboratories/issue/CSL-214)
**Parent:** [CSL-212](https://linear.app/csl-laboratories/issue/CSL-212) outcome O2
**Maturity claimed:** `researched`
**Not claimed:** `human_decided`, `contract_frozen`, `prototyped`, `implemented`, `provider_free_verified`, `demo_or_paper_qualified`, `shadow_qualified`, `limited_live_qualified`, `release_qualified`, empirical PASS, feed qualification, live authority

This document is a candidate envelope. It does not amend the nine runtime invariants, the accepted CSL-212 governing files, or `docs/replacement/program.json`. Completion of O2 remains `pending_outcome_artifact` until an independent verifier reruns the exact candidate. CI success is not that verification.

Candidate command, from the repository root:

```bash
python -X utf8 -B docs/replacement/outcomes/O2/validate_o2.py
```

Exit 0 means the frozen synthetic corpus replayed and every negative control failed as designed. It does not mean the bad views were accepted.

## What this candidate covers

Versioned equity identities and versioned Kalshi-demo event identities stay in separate objects. A record is visible at a decision time only when its acceptance time is at or before that instant. Raw payloads stay immutable and hash-bound. Corrections are new raw records. Duplicates share a content hash and do not supersede. Equity uses the XNYS session date. Event markets use the Kalshi demo exchange clock and do not use an equity session. Event resolution (`yes`, `no`, `scalar`, `voided`) is not a corporate action. A corporate action is not a yes/no result. Event-market corrections follow Kalshi's documented `disputed` -> `amended` -> `finalized` path, not only the equity/filing amendment path.

The fixture corpus is synthetic. It uses no Kalshi key, no vendor feed, and no network.

## Local inventory at source commit

Inspected commit: `ebbaa34489f171e079834d02098135711e1d4755`.
Issue baseline named in CSL-214: `73ab78d9d69494c757cec875855121b1d29320a4`.

| Surface | What it already does | Why it is not this envelope |
| --- | --- | --- |
| `app/ingest/edgar_pipeline.py` | Reads SEC `acceptanceDateTime` and `filingDate`. Rejects a submissions array that is not unambiguous newest-first. | Out-of-order acceptance is a hard failure, not a retained later knowledge version. |
| `app/ingest/dedup.py` | In-memory LRU on source and article id. | Not content-hash identity, not durable, and eviction can forget a key. |
| `app/ingest/clock_contract.py` | Wall-clock seams require one tz-aware datetime. Monotonic seconds are a different clock. | This candidate does not import it. Instants here are UTC `Z` timestamps. |
| `app/ingest/calendars.py` | Earnings and macro windows are republished snapshots. | Equity session labels in this candidate are not that poller. |
| `app/ingest/normalize.py` | Allowlist is stock, news, EDGAR, and FINRA. | No event-market identity. |
| `app/filings/raw_store.py` | Vendored write-once accession bytes. | Not restructured. Not an event-market store. |
| `app/storage/parquet_store.py` | Write-once snapshots keyed by date and symbol. | No correction lineage and no event contract. |
| `app/quant/feed/replay.py` | Book and trade replay ordered by `recv_ts`, not `exchange_ts`. | Same visibility idea at microstructure scale. No filings, corporate actions, or event resolution. |

## Research basis

Embedded Parallel searches, run on Dan's box on 2026-10-17:

- `search_c87aae75bac1d50c74541f9a97715ee9`
- `search_f15c652feaeffd8f1eedb6be8d0d11d0`
- `search_c7df723820f290a2e25b90fd781b9772`
- `search_7dad5545944e08783486e8b8073f27e0`
- `search_6de2fe07737be85eb37b6641fec6ce80`
- `search_80db337dc10f28ae95679601790f611e`
- `search_bf1ef6ae60b2d58373dc922eed2aee07`

Those results named the URLs below. This session fetched them, plus the Kalshi market-lifecycle and market-settlement pages linked from the Kalshi docs index. No API version was selected. `trade-api/v2` is an observation dated 2026-10-17.

Cited URLs:

- https://docs.kalshi.com/
- https://docs.kalshi.com/getting_started/demo_env
- https://docs.kalshi.com/getting_started/api_environments.md
- https://docs.kalshi.com/websockets/market-and-event-lifecycle.md
- https://docs.kalshi.com/getting_started/historical_data
- https://docs.kalshi.com/getting_started/market_lifecycle
- https://docs.kalshi.com/getting_started/market_settlement
- https://docs.kalshi.com/websockets/market-and-event-lifecycle
- https://docs.kalshi.com/api-reference/market/get-markets
- https://docs.kalshi.com/api-reference/events/get-event
- https://docs.kalshi.com/api-reference/events/get-events
- https://docs.kalshi.com/fix/market-settlement
- https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- https://github.com/Gagan234/asof

Facts used, and the limits of those facts:

- Kalshi demo REST base observed as `https://external-api.demo.kalshi.co/trade-api/v2`. Demo WebSocket base observed as `wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2`. Credentials are not shared with production. This packet does not create a key.
- REST market statuses observed on the lifecycle page: `initialized`, `active`, `inactive`, `closed`, `determined`, `disputed`, `amended`, `finalized`. WebSocket `market_lifecycle_v2` event types include `created`, `activated`, `deactivated`, `close_date_updated`, `determined`, `settled`, and metadata updates. `settled` on the socket corresponds to REST `finalized`.
- Settlement results observed: `yes`, `no`, and `scalar`. The fetched lifecycle page does not list `void`. The fixture keeps `voided` as a distinct synthetic resolution so normalization cannot collapse it. That is not a claim that Kalshi emits void.
- REST statuses on the lifecycle page: `initialized`, `active`, `inactive`, `closed`, `determined`, `disputed` (result challenged, may be re-determined), `amended` (re-determined after a dispute; settlement timer restarts), `finalized` (terminal). `disputed` and `amended` have no `market_lifecycle_v2` event; observing them needs a REST poll. `determined` / `amended` -> `finalized` emits the socket `settled` event.
- The `market_lifecycle_v2` message documents optional `determination_ts` and `result` (only when the market is determined), `settled_ts` (only when settled), and `settlement_value` as a fixed-point dollars string (only when determined). `sending_ts_ms` is when Kalshi queued the message at the network layer — a venue-side stamp, not our wire-arrival time.
- The REST market object documents `settlement_ts`, populated at `finalized`, and `result`. No REST determination timestamp is documented.
- `settlement_sources` is required on the Event object: an event-level array of `{name, url}` naming the official determination sources. It is event-level metadata, not a market-resolution field.
- Historical data is partitioned by moving cutoff timestamps (`market_settled_ts`, `trades_created_ts`, `orders_updated_ts`, `market_positions_last_updated_ts`). A cutoff is not a stable as-of. This packet does not call `GET /historical/cutoff`.
- SEC `data.sec.gov` submissions and XBRL JSON are described as unauthenticated. The page was dated 2024-06-06 and last reviewed 2025-04-08. Frames are the last filed fact that best fits a calendar period. Frames are disqualified here as an as-of source. Company facts are an aggregate, not the raw filing.
- `Gagan234/asof` is a reference for bitemporal SEC facts: append-only raw, knowledge time separate from event time, and a no-lookahead check. It is not a dependency and it does not cover Kalshi.

## Alternatives recorded, not selected

1. Reuse quant `recv_ts` replay as the cross-market rule. It blocks microstructure lookahead and stops there.
2. Reuse the vendored filings raw store as the only envelope. It is accession-keyed SEC bytes.
3. Adopt `asof` as the implementation. It is a SEC/DuckDB reference, not a Kalshi-demo contract.
4. One instrument object with optional equity and event fields. Optional fields are how resolution and corporate actions disappear.
5. Treat SEC frames as point-in-time. The documented frame is a current last-filed view.

## Identity

Equity identity: venue `us_equity`, issuer CIK, listing key, ticker, share class, currency `USD`, and an integer version. A later listing version points at the prior identity. The ticker is not the stable key. The synthetic issuer is CIK `0000000001`, ticker `SYNTH`.

The listing key must equal `us-equity:cik:<issuer_cik>:class:<share_class>`.
A prior-identity link must preserve the equity listing key or event market ticker,
as applicable, and strictly advance the identity version. Separate instruments
cannot share one version history.

Event identity: venue `kalshi_demo`, environment `demo`, series ticker, event ticker, market ticker, contract family, and collateral return type (`""`, `MECNET`, or `DIRECNET`). `api_path_observed_not_selected` is fixed to the observed string `trade-api/v2`. Two markets in one series are two identities. A voided market is not the settled market.

An observation's identity is the identity on its effective raw record. An event observation cannot cite another market's identity. An equity observation may also cite a corporate-action raw record for the other version of the same listing key.

Schemas: `schemas/identity.schema.json`.

## Source and as-of

Each raw record has `event_time` (when the source says the fact happened) and `accepted_at` (when it became visible to this cockpit). Both are timezone-aware UTC instants with a `Z` suffix. Naive timestamps are rejected.

Every raw record, including duplicates and records not used by an observation,
must satisfy `event_time <= accepted_at`.

A non-duplicate record is in the decision-time version set when `accepted_at <= decision_time`. The effective record is the latest of those by `accepted_at`, then `raw_record_id`. Knowledge-valid-to is derived from the next correction's `accepted_at`. It is not stored on the earlier row, so a late correction does not rewrite history.

Ordering by `event_time` is not visibility. The out-of-order fixture has an event time before the decision and an acceptance time after it. That record is hidden at the decision.

`accepted_at` is a capture-side stamp: the instant a record became visible to this cockpit, which only our own ingestion can observe. Venue data cannot supply it. Kalshi's `sending_ts_ms` is when Kalshi queued a message at the network layer, and its historical and candlestick endpoints carry venue timestamps only; SEC `acceptanceDateTime` is EDGAR's own acceptance stamp. None of these is our wire-arrival time, so a replay built from later-pulled history cannot reconstruct `accepted_at`. Honest replay of a live feed needs our own capture. This documents the limit; the two-timestamp model is unchanged.

## Immutable raw records

`acquisition_route` is `synthetic_fixture` on every row. The raw image is the canonical JSON of `payload` (`sort_keys`, compact separators, UTF-8). `raw_sha256` is the SHA-256 of those bytes. A derived observation cites raw ids. Reconstruction recomputes the hash from the retained payload. A summary that cites a missing id, or a hash that does not match, is `raw_not_reconstructable`.

Duplicates set `duplicate_of` and must share the natural key and the hash. They are retained and they are not a new version. Corrections set `supersedes`, use a later `accepted_at`, and are a new payload. A row cannot be both.

## Lineage

Each derived observation has one lineage edge. `transform_id` is `o2.candidate.effective_version` and `transform_version` is `o2.candidate.v1`. The edge's input ids are the observation's raw ids, in the same order. Lineage is not a license to drop a raw parent: the late revenue observation cites both the 10-K and the 10-K/A. Every visible `supersedes` ancestor of an input is itself an input. Matching a shortened lineage list to a shortened observation does not drop that parent. Every corporate action named on an observation cites that action's raw id in the same input list.

## Clocks and calendars

Equity records carry calendar `XNYS` and a session date. Event records carry calendar `kalshi_demo_exchange` and `session_date: null`. Kalshi demo is not an NYSE session. This candidate does not encode the NYSE holiday list and does not select a market-data vendor calendar.

An equity session label must parse as a real calendar date. This does not certify
that the exchange was open on that date.

## Event resolution

Resolution inputs stay on the event payload: `resolution`, `status_rest`, `result`, and `official_lifecycle_status_observed`. The synthetic settled market moves from `determined`/`yes` to `finalized`/`yes`. Both raw records remain. The synthetic voided market has `resolution: voided`, `status_rest: voided`, `result: null`, and `official_lifecycle_status_observed: false`. A derived event observation must copy the effective resolution and status. Mapping voided to `yes` is `lost_resolution_semantics`.

A close-date update is a lifecycle raw record with a null resolution and a `close_time`. It is replayed on its own natural key. It is not written into the resolution field. Resolution checks apply only to natural keys that end in `:resolution`.

A second synthetic market exercises event-side correction: `determined`/`yes` is superseded by a REST-observed `disputed`, then by an `amended` re-determination to `no`, then by `finalized`. Disputed and amended carry no `market_lifecycle_v2` event, so the fixture captures them as `rest_get_markets` snapshots with `event_type: status_observed`.

Each raw lifecycle record must satisfy this candidate's snapshot-state rules;
replay order does not infer a missing transition:

| Event type | Permitted `status_rest` | Resolution and result |
| --- | --- | --- |
| `created` | `initialized` | Both null |
| `close_date_updated` | `initialized`, `active`, `inactive`, `closed` | Both null; `close_time` required |
| `determined` | `determined` | Both present and equal |
| `determined` (synthetic void) | `voided` | Resolution `voided`, result null, official-observed flag false |
| `settled` (socket) | `finalized` | Resolution present. `result` equals it, or is null only when an earlier record on the same natural key supplies that same resolution |
| `status_observed` (REST only) | `initialized`, `active`, `inactive`, `closed` | Both null |
| `status_observed` (REST only) | `determined`, `disputed`, `amended`, `finalized` | Both present and equal |

Non-void resolutions and results must be `yes`/`no` for a binary identity and
`scalar` for a scalar identity. A socket `settled` message may omit `result`;
the resolution it asserts must still equal the resolution on an earlier record
in that correction chain. A REST `settlement_ts`, when present, additionally
requires `status_rest: finalized` and `settlement_ts <= accepted_at`.

Settlement fields stay on the payload only where official docs confirm them: `determination_ts` and `settlement_value` (socket, only when determined), `settled_ts` (socket, only when settled), and `settlement_ts` (REST market object, populated at `finalized`). `settlement_sources` is event-level (`GET /events/{event_ticker}`) and lives on the event identity, not the market payload. Unconfirmed repeats — for example whether the `settled` message also carries `determination_ts` or `settlement_value` — are listed as unknowns in the qualification matrix.

## Corporate actions

The synthetic 2-for-1 split is its own raw record: ratio 2/1, ex-date `2024-08-01`, listing identity before and after. Once its `accepted_at` is visible, every equity observation on that listing key must carry the effective action. A later 3-for-1 correction on the same natural key replaces that required ratio. The earlier raw record stays in the store and in the correction's input list. It is not a second required ratio. The chain is resolved before the listing check. A correction that points the effective action at a different listing removes it from the former listing; the superseded split is not still required there. An independent later split uses a different natural key, `corporate_action:ex-2024-10-01`, and both effective actions are required together. Two actions on one natural key, with neither superseding the other, are rejected. The unadjusted quote stays `50.00` with `adjustment: unadjusted`. The split is a label beside the quote, not a silent rewrite of the raw price. Dropping either effective action is `lost_corporate_action_semantics`.

## Replay manifest

`fixtures/replay_checks.json` freezes visibility, hidden ids, and the effective raw id for:

- late correction, before and after the amendment
- duplicate that must not supersede
- out-of-order acceptance, hidden then visible
- corporate action retained, then corrected to 3-for-1
- a second independent corporate action, hidden until accepted, then kept beside the correction
- close time preserved after a late close-date acceptance
- unadjusted quote not rewritten
- event determination before a late close message
- settlement that keeps the determination raw
- voided event kept distinct from the settled market
- event dispute chain: determined, then disputed, then amended to `no`, then finalized — each correction effective at its own acceptance

`fixtures/manifest.json` binds SHA-256 hashes of this document, the validator, the fixtures, and the schemas.

## Feed qualification and unknowns

`fixtures/qualification_matrix.json` is the matrix. `qualification_claimed` is false. `admitted_for_acquisition` is false on every row. Allowed qualification values are `public_documented`, `local_observed`, `disqualified_as_asof_source`, and `not_qualified`.

No feed is qualified. SEC frames are disqualified as an as-of source. Kalshi demo REST and lifecycle sockets are public-documented and credentialed, and they are not admitted. Local stock components are local observations, not the cross-market envelope.

## Negative controls

The controls live in `fixtures/negative_controls.json`. The validator must report each one as a failure. A control that passes is a broken candidate.

| Control | Designed failure |
| --- | --- |
| `future_data_leak` | April decision view includes the June 10-K/A |
| `raw_not_reconstructable` | Observation cites `raw-missing` |
| `lost_resolution_semantics` | Voided market is relabeled `yes` / `finalized` |
| `lost_corporate_action_semantics` | Post-announcement equity view drops the split |
| `no_evidence` | An empty feed window must produce an explicit no-evidence result, never a pass |
| `dedup_collapses_correction` | A corrected payload is claimed as a duplicate fold |
| `stock_treated_as_event` | An equity identity or natural key is claimed under the event family |

The test `app/tests/test_o2_evidence_replay_contract.py` warns once per designed failure so Blacksmith CI logs show `O2 NEGATIVE CONTROL FAIL AS DESIGNED`.

## Smallest acquisition experiments

Not run. Listed for a later human receipt. Stop here.

1. Public SEC submissions for one CIK, with the operator's user-agent, stored as raw bytes, including one amendment pair. No key. Not started.
2. One Kalshi demo `market_lifecycle_v2` sequence, one `GET /markets` status poll across a `disputed` -> `amended` -> `finalized` path if one occurs during the capture, and one `GET /historical/cutoff` snapshot, only after a demo key exists in the OS credential store. Not started.
3. Every captured message or poll must record our own wire-arrival stamp for `accepted_at`. Venue endpoints cannot supply it, and this limitation is why the acquisition experiments exist at all.
4. No scrape, no production Kalshi, no paid vendor feed.

## Downstream consumers

Verified against `docs/replacement/program.json` outcome dependencies. O1 and O6 do not consume O2.

| O2 fragment | Consumers |
| --- | --- |
| Identity objects (`identities.json`) | O3, O8 |
| Provenance, source/as-of and clocks | O3, O4, O5, O7 |
| Replay manifest and candidate command | O4, O5, O9 |
| Corrections, calendars, event resolution | O3 |
| Feed qualification and unknowns | O8, O9 |

## Unresolved decisions (owner: Lucas)

These are listed and not decided.

1. **Spend.** Whether to buy any vendor, historical, or premium feed, including anything under CSL-92, CSL-93, or CSL-99. This packet spends nothing.
2. **Credentials.** Whether to issue a Kalshi demo key, where it is stored, and whether any SEC user-agent identity is an operational account. No secret is created. Production keys are not in scope.
3. **Tax and legal.** Prediction-market eligibility, retention, redistribution, and tax treatment for a US individual, including CSL-100. Demo access is not data permission.
4. **Architecture.** Whether this envelope replaces or sits beside the filings raw store, parquet snapshots, and quant replay, and which storage engine holds it. That measurement belongs to O8. `asof` is not adopted.
5. **Release.** This candidate is not release scope.
6. **Live.** No live venue, no production Kalshi, no broker, no capital.
7. **Public semantics.** Whether `voided` is an admitted product resolution, and which stock listing is the first slice. The fixture preserves void so it cannot be dropped. It does not decide that Kalshi emits it.
8. **API version.** `trade-api/v2` was observed on 2026-10-17 and is not selected.

Inherited owner direction is unchanged: US individual, stocks plus prediction markets, Kalshi demo first, Polymarket US Retail only after the first slice is stable, GUI plus CLI, paper/demo/shadow as qualification stages, and no live authority from this issue.

## Evidence boundary

Claimed now: `researched` candidate artifacts (this document, schemas, frozen fixtures, and a provider-free validator).

The replacement-program registry stays at `researched` with `completion_gate_status` `pending_outcome_artifact`. An independent non-implementer still has to rerun the exact command, the manifest, and the negative controls, and compare any later decision receipt with a human Linear comment. Implementer runs, CI, and model agreement do not promote the claim.

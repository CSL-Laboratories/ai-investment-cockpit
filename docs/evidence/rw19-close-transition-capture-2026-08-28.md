---
title: RW-19 close-transition news capture — first non-empty plan-bound observation
type: evidence
status: live
last-verified: 2026-08-28
---

# RW-19 close-transition news capture — 2026-08-28

## Scope and safety baseline

First non-empty plan-bound RW-19 news-reconnect capture, and the first to run fully unattended
under the `work/rw19-autolaunch` tooling. Artifact hashes below were recomputed from disk on
2026-08-28 after the capture sealed. This grants no strategy, mode, paper-order, live-order, or
autonomous authority.

Explicit posture for every statement in this document:

- `authority: none`
- `rw19_gate: OPEN_NOT_CLOSED`
- `dec07_status: open`
- capture ran exactly once; no retry occurred and none is authorized
- the external evidence directory is write-once and unmodified since sealing

A non-empty window is **observation-only** under the sealed classification contract. It does
not close `RW-19` and does not resolve `DEC-07`.

## Window selection (prospective, outcome-independent)

Plan authored `2026-08-28T15:22:00.612Z`; operator authorization at approximately 15:25 UTC,
after the sealed 13:00Z open-transition window lapsed unarmed. The selection rule fixed the
first thirty-minute segment at 19:30–20:00 UTC so the deliberate five-second disconnect would
straddle the 20:00 UTC regular-session close, retaining the first thirty minutes after the
close where after-bell releases concentrate. Selection preceded any inspection of that
afternoon's Alpaca output.

The plan required that a shifted start be reported and the "straddled the close" claim dropped.
The start did not shift: the collector began at `19:30:00.770Z` (launcher delta `+0.016s`
against the sealed `19:30:00Z`), and the disconnect fell at `20:00:00.800Z`. The gap straddled
the close as designed.

## Unattended launch timeline

Windows Task Scheduler task `RW19-Autolaunch-20260828T1930Z` fired once at `19:15:01Z`
(`LastTaskResult 0`, `NumberOfMissedRuns 0`).

| Stage | UTC timestamp |
|---|---|
| launcher bootstrap complete, live window lock claimed | `19:15:01.298` |
| T−2 reached; bounded `git fetch`; source verified clean and pinned | `19:28:00.0`–`19:28:01.03` |
| fresh safety preflight generated | `19:28:01.03`–`19:28:07.18` |
| T0 reached; source re-verified; collector invoked exactly once | `19:30:00.006`–`19:30:00.218` |
| collector exit `0` | `20:30:06.962` |
| verify exit `0` on the output directory | `20:30:07.251` |
| `terminal_state=COMPLETE_VERIFIED` | `20:30:07.261` |

No aborted directory was created; no recovery siblings or collector staging residue were
detected. The one-shot window lock
`.rw19_autolaunch_live_9d8df8eb6250b8b7.lock` deliberately persists as the no-retry marker. The
task was unregistered post-hoc during triage.

## Safety preflight (T−2, green)

`configured_mode` and `effective_mode` both `read_only`; `persisted_mode` null; audit chain
valid; HALT absent; zero order, approval, proposal, risk-decision, merger-selection, and
merger-edge rows; zero cockpit service processes; zero API port listeners; `main == origin/main`
at `1199a75497a281f2ba78d397d0d6600bce4f5f47` with a clean worktree.

The preflight consumed by the collector is byte-identical to the file the launcher generated at
T−2 (`rw19_safety_preflight_20260828T192801Z.json`), both hashing to
`bcc2a710b74069eb5fb0cd3428ebbd2ce4792109df7c7284d774a0635acae50b`.

## Observation

External directory (write-once):

`C:\Users\Lucas\Documents\ai-investment-cockpit-evidence\rw19-close-transition-20260828T193000Z`

Window `19:30:00.771Z` → `20:30:05.820Z`. Deliberate disconnect `20:00:00.801Z` →
`20:00:05.801Z`, observed duration `5.000146s`.

| Measure | Value |
|---|---|
| stream identities, segment A (19:30–20:00) | 1 (4 frames) |
| stream identities, segment B (20:00–20:30) | 3 (6 frames) |
| combined stream identities | 4 |
| REST identities (1 page, 3,982 bytes) | 4 |
| combined stream recall of REST | 1.0 |
| blind-window identities delivered after reconnect | 0 |
| blind-window REST identities | 0 |
| blind-window REST-recoverable identities | 0 |
| cross-segment overlap | 0 |
| cross-segment corrected identities | 0 |

Article arrival times: `19:37:33.570Z` (segment A); `20:00:41.820Z`, `20:03:24.145Z`,
`20:27:33.585Z` (segment B). All four identities appear in both the stream and the REST census.

Raw vendor evidence remains external and is not committed or redistributed.

## What this does and does not establish

Established, at small sample:

- The plan-bound collector completed a full two-segment capture with REST backfill under
  unattended launch, and self-verified immediately.
- Over a 60-minute window straddling the close, stream recall of the REST census was complete
  (4 of 4). No REST identity was missing from the stream.
- No identity was duplicated across the reconnect boundary, and no corrected or updated
  representation was observed across segments.
- Delivered articles carry full vendor symbol lists that exceed the subscribed universe (one
  article was tagged with ten symbols, of which three are in the awareness universe). Downstream
  filtering and coalescing must not assume the symbol list is a subset of the subscribed
  universe. This is an input contract observation for the `RW-13` coalescing decision.

**Not** established — and this is the central limitation:

- **The blind window was empty.** Zero articles were published during the five-second
  disconnect, so nothing was available to lose or to recover. The first post-reconnect article
  arrived approximately 36 seconds after reconnect. The core RW-19 question — blind-window loss
  and REST recoverability across a *news* reconnect — remains unmeasured.
- Zero corrections or updates were observed, so correction and update behavior across reconnect
  remains unproven.
- One trading-day close transition on eleven symbols is a single regime; broader market regimes
  remain required.
- Four identities is too small a sample to support any density, latency, or dedup conclusion.

The 2026-08-18 *market*-stream reconnect measured 1,965 REST-recoverable blind-window
identities. No comparable news-stream figure exists yet; this capture does not supply one.

## Artifact hashes recomputed from disk

| Artifact | SHA-256 |
|---|---|
| plan (evidence root and sealed `collection_plan.json`, identical) | `9d8df8eb6250b8b7bdaade4f798cfff874e6cd778bbef8f96c22fe84216f71ad` |
| safety preflight consumed | `bcc2a710b74069eb5fb0cd3428ebbd2ce4792109df7c7284d774a0635acae50b` |
| capture manifest | `2ab92bf7c7cb174c15e87c2269aa98f28996510494a3d03e349719fa74912208` |
| collection hash (field inside the manifest, not a file hash) | `a70b490d8bcb96ac09793c8f2a6c9441500932ed5571780e6c21ea16e48372ac` |
| `summary.json` | `66d8a3b946778b3c2ccef50743446152534ed840d1afe6b008fbf76d526dda13` |

The manifest's recorded `capture_manifest.sha256` sidecar matches the recomputed manifest hash.
All six raw journal hashes recorded in the manifest match the files on disk. The manifest's
`implementation_hashes` for `app/ingest/characterization.py`
(`1d547f01…cdacbd7`) and `tools/rw19_news_reconnect_plan_bound.py` (`c4390a9f…b338fc43b`) match
the pinned source commit's files. Independent recomputation of the collection hash over the
canonical manifest-minus-`collection_hash` bytes reproduces the recorded value, and a re-run of
`verify --package-dir … --expected-plan-sha256 9d8df8eb…` exits `0` with
`{"classification": "OBSERVATION_ONLY_DEC07_OPEN", "ok": true}`.

## Claims and remaining gates

- Apparatus milestone: first non-empty plan-bound news capture, first fully unattended one, and
  the first end-to-end exercise of the `BUG-111` ack-set repair against a live vendor
  acknowledgement.
- Empirical milestone: partial. Non-empty, but the reconnect gap itself carries no evidence.
- `RW-19` empirical gate remains `OPEN_NOT_CLOSED`.
- `DEC-07` remains open.
- `RW-13` is not started and is not unblocked by this evidence.
- Authority remains none. No paper opening order, live authority, or autonomous authority is
  granted.
- Named remaining work: a reconnect whose blind window actually contains publications; observed
  correction/update behavior across reconnect; broader regimes; the bounded filtering and
  coalescing decision.

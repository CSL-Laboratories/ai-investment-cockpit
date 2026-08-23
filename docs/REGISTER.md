---
title: The register — one ID space, one place to claim work
status: live
last-verified: 2026-08-18
---

# The register

> **Claim work here before writing code.** This is the whole mechanism. We work ad hoc —
> whoever picks something up — which is fine for four people and is not changing. What is not
> fine is having no shared place to say "I have this", which is how the same five defects got
> fixed twice on two branches.

**One ID space.** `SEC-*` (security), `BUG-*` (defects), `RW-*` (refactor packages), `W-*`
(pre-live packages), `QT-*` (quant engine), `G*` (acceptance gates). They used to live in four
documents that disagreed with each other, and the `BUG-03x` series lived only in commit
messages and no register at all — which is precisely why that work got duplicated.

**Detail lives in the owning doc; the claim lives here.** `SEC-*` findings are written up in
[[security-hardening-backlog]]. `WP-*` contracts are in [[build-scaffold-ultracode]] (cited by
43 production files — do not move it). `QT-*` is in [[quant-engine]]. This table is the index
and the claim board, not the specification.

## Current delivery position — 2026-08-17

| Lifecycle stage | State | Next gate |
|---|---|---|
| Offline development | **BUG-110 merged** | BUG-106, W-24, RW-19 evidence, the handoff, W-25, BUG-108/109, and BUG-110 are merged; hosted cross-platform attestation remains missing; do not start `RW-13` while empirical gates remain open |
| Online testing and evidence | **active** | [[evidence/rw14-rw19-live-evidence-2026-08-18]] closes RW-14's named live drill and measures RW-19 reconnect/backfill plus the single-writer bottleneck; restore cross-platform attestations and finish RW-11/RW-15/RW-19/W-24 evidence; W-25 provisioning waits on an explicit provider/cost decision and remaining attestations |
| Paper MVP | **not reached** | real W-24/W-23 prospective cohort + target evidence, RW-15 online evidence, then two audited unattended `W-22` market days |
| Production beta | **blocked by MVP** | `RW-13` dynamic path plus `W16` four-week paper soak |
| Stabilization / bugfixing | **not started** | beta defect burn-down, platform/restart/fault regression, zero unexplained reconciliation gaps |
| Full-scale deployment | **blocked** | all pre-live gates, ≥6-month paper evidence, then `W17 → W18 → W19` separately |

The merged line includes RW-15 (PR #34), W-22 (PR #35), the bring-up repairs plus W-23
(PR #36), BUG-106 (PR #37), W-24 (PR #38), the repeated RW-19 measurements (PR #39), the
handoff (PR #40), W-25 (PR #41), BUG-108 (PR #42), and BUG-109 (PR #43).
The combined BUG-106/W-24 Windows integration gate passed 2,274 tests / 1 skipped with Ruff
clean. Older package attestations and the remaining named empirical gates remain pending; see
[[PLAN]], [[evidence/w22-paper-mvp-2026-08-14]],
[[evidence/rw14-rw19-live-evidence-2026-08-18]], and
[[evidence/bug106-edgar-baseline-timestamp-2026-08-17]].

GitHub's 2026-08-17 incident originally left four clean branches pending publication. The
recovery sequence completed through PR #39; this documentation-only branch retains the final
stopped-system preflight and historical handoff in
[[evidence/github-outage-session-handoff-2026-08-17]].

## Status vocabulary

| Status | Means |
|---|---|
| `open` | Nobody has claimed it. Take it. |
| `claimed` | Someone is on it. Branch named. **Do not start a parallel fix.** |
| `in review` | PR open. |
| `evidence pending` | Implementation merged; a named empirical acceptance gate is still open. |
| `closed` | Merged and its gate is satisfied; code changes have a regression test that fails without them. |
| `blocked` | Cannot proceed until a named decision is made. The blocker is stated. |

---

## Milestones — see [[PLAN]] §4

| ID | What | Status | Owner | Gate |
|---|---|---|---|---|
| `RW-16` | Autonomy-readiness research and replan: distinguish scheduled paper reliability, dynamic paper autonomy, human-gated live operation, and autonomous live execution; verify Robinhood/MCP and event-source assumptions; define the development pipeline before implementation packages are committed | closed | — | dated primary-source report + explicit hold points/decisions/development batches in [[PLAN]]; no production-code change |
| `RW-11` | Wire `app/filings/` into `build_ingest_fetchers` as `sec:edgar` | claimed — text evidence tool merged in PR #50; Company Facts capture package local; empirical gate open | Codex + Claude Sonnet 5 | PR #50 merged the isolated database-free text replay freezer/scorer at `eb069fadddc45fa0a702c67b0e00edf4f94a7e31`. `work/rw11-companyfacts-capture` now adds an operator-only exact-byte prospective Company Facts collector/verifier; 12 focused tests pass, one symlink-capability test skips, a real NTFS-junction diagnostic passes, and the full Windows gate is 2,350 passed / 2 skipped with Ruff clean. A Sonnet 5 adversarial pass found no P0; its raw-store hash-binding P1 is fixed. Neither tool grants authority or completes RW-11: a real census, numeric replay, a meaningful frozen population, independent human labels at ≥95%, a cited live material delta, external witness, and Ubuntu/macOS attestation remain required. The 2026-08-18 poll and four raw-byte anchors remain reachability/seeding evidence only; [[evidence/rw11-live-poll-2026-08-18]]; [[evidence/rw11-precision-label-sheet-2026-08-18]]; [[evidence/rw11-companyfacts-capture-2026-08-18]] |
| `RW-12` | Delete `edgar_brew.py` + tests once RW-11 has run a live day | blocked → `RW-11` empirical gate | — | retire the fallback only after RW-11's live-day and precision evidence passes |
| `RW-14` | Daily-bars poller (ADV + σ); synthesize `book_fn`; sample quote age; close `BUG-097` by routing stale data through the single L8 `watch_only` mechanism | online evidence passed; cross-platform attestation pending | — | 2026-08-18 composed live drill advanced all 11 `quant_book_seq` values, crossed 120 s on frozen real exchange timestamps, entered L8 while awareness continued, and recovered to fresh `read_only` with zero Orders; hosted/macOS attestation remains missing, not failed; [[evidence/rw14-rw19-live-evidence-2026-08-18]] |
| `RW-17` | Replace the dated-calendar fail-open fallback and close `BUG-096`; verify current/next-year NYSE sessions and expose drift/coverage in status | closed | — | PR #14; unknown/out-of-range year refuses auto-arm/orders while ingest/awareness continues; official holiday/early-close fixtures |
| `RW-18` | Robinhood no-capital authenticated capability discovery after account-isolation decision; zero live orders | blocked → `DEC-05` | — | advertised 2026 MCP/auth/capabilities/tools/errors/expiry behavior recorded; resolves `DEC-06/08` |
| `RW-19` | Read-only/paper event-source characterization: news/market/status/order streams, SEC discovery, IEX/SIP comparison, WS/REST identity, reconnect gaps, event density, and SQLite/triage load | evidence pending | — | two completed sessions confirm IEX/SIP non-equivalence; the 2026-08-18 market reconnect exposed 1,965 REST-recoverable blind-window identities, and the 1,114-frame replay measured only 74.67 writes/s plus a 236,637-character packet. A one-hour news sample found 10 stream/9 REST identities with one persistent stream-only ID; two later news reconnects completed both socket segments around five-second gaps, but their 10- and 30-minute after-hours windows returned zero identities on both surfaces, so they are insufficient samples. Broader regimes, non-empty news correction/reconnect behavior, and a bounded coalescing decision remain required; `DEC-07` stays open; [[evidence/rw14-rw19-live-evidence-2026-08-18]] |
| `RW-15` | Order-status poll + real broker reconciliation: settle resting/partial orders and `ambiguous_aborts` via the gateway's existing ACK machinery; retire `main._no_broker_history` in the same PR. Prerequisite of `W-22` — see [[PLAN]] M4 and its delivery loop | evidence pending — merged PR #34 | — | Windows 2,165 passed / 1 skipped + Ruff clean after fresh attack→fix→regression review; fixed-host GET-only broker/client-id lookup, bounded scheduled poll, signed approval recovery, audited partial progress, composed reachability, and stub retirement complete; real resting/partial/terminal and lost-response Alpaca paper evidence + cross-platform CI remain required |
| `W-21` | Cost model: spread + square-root impact on ADV + borrow; relative net-edge test | merged — evidence pending | — | merged in PR #33 with Windows 2,155 passed / 1 skipped and Ruff clean; fresh DeepSeek Pro/Flash adversarial round found no P0/P1 bypass and concrete approval-path findings were fixed with regressions; expected edge remains explicitly unavailable/fail-closed; cross-platform CI remains pending, while RW-14's named live input evidence passed on 2026-08-18 |
| `W-22` | The paper loop end to end, Discord-driven, fills at bid/ask; wire the loss signals into `BreakerSignals` (daily loss from snapshots, consecutive losses from round trips) | evidence pending — merged PR #35 | — | Windows 2,180 passed / 1 skipped + Ruff clean after a fresh attack→fix→regression review; Discord fresh question/approve/veto, post-commit fill confirmation, signed equity drawdown, audit-derived closed round trips, active-breaker gateway backstop, status/reachability, and additive SQLite migration are implemented. RW-14's named online drill passed 2026-08-18; RW-15 live evidence, verified deterministic edge authority, and two unattended market days a week apart remain required; see [[runbooks/w22-paper-mvp]] |
| `W-23` | Deterministic paper expected-edge source selected by `DEC-03`; immutable point-in-time evidence, draft-time and approval-time recomputation, fail-closed status, and no model-authored return | evidence pending — merged PR #36; production evidence gate closed | — | Merge `2cbb6d5`; three independent review passes found and closed order/evidence blockers. The final 2,240-test local suite, Ruff, 2,000-write Windows 3.11 stress case, and replacement six-job matrix are green. W-24 code can now verify semantics, but absent/retrospective/operator-only evidence remains non-executable; real prospective cohort/current-target evidence and paper activation remain blocked; [[evidence/w23-cash-merger-edge-2026-08-14]] |
| `W-24` | Independent deterministic merger semantic verifier: complete declared universe, externally witnessed pre-outcome commitments, source-derived outcome labels, and cited deal terms; replace W-23's hard refusal only when all authority verifies | evidence blocked — verifier PR #38 and BUG-108/109 PRs #42/#43 merged; cross-platform/precision attestations pending | — | Post-PR #43 GET-only rescreen found one unsigned semantic source-set candidate (CRNX) among only six supported-head names in the 65-name pool; this is below 30 and its primary sources do not yield one exact expected-resolution or outside date, so it is not a current-target authority. No selection commitment exists. Missing cross-platform CI, a real ≥30-deal prospective cohort, held-out parser precision, and a current eligible target remain mandatory; [[evidence/w24-merger-semantic-verifier-2026-08-17]]; [[evidence/w24-prospective-intake-2026-08-18]]; [[evidence/bug109-w24-real-sec-grammar-2026-08-18]]; [[evidence/w24-post-bug109-rescreen-2026-08-18]]; [[runbooks/w24-merger-semantic-verifier]] |
| `W-25` | Replace Discord as SEC-11's trust root under the fixed Administrator-bot constraint; keep Discord mirror/W-24 chronology only, add an S3 Object Lock Compliance witness, and close `BUG-107` edit/author binding | merged — PR #41; activation blocked → provider/cost decision + cross-platform/real-cloud evidence | — | Private Discord channel + hardened exact-message diagnostic green; real runtime remains `read_only` with no trusted anchor/Orders/merger watchlist. Same-host storage and R2 are mirrors under the current contract; Rekor is research-only. BUG-110's bounded proof merged in PR #48 with local Windows gates green; all six hosted jobs executed zero steps under the payment restriction. Still mandatory: independent/cross-platform attestation, explicit authority selection, and real retention/delete/shadow/rollback evidence; [[evidence/w25-admin-discord-worm-witness-2026-08-17]]; [[evidence/bug110-bounded-witness-read-2026-08-18]] |
| `RW-13` | Dynamic paper autonomy: durable event/outbox trigger, deterministic arbiter, scoped gap backfill, replay, dedup, bounded flood handling, cron backstop | blocked → `RW-19` + `DEC-07` + M1–M4.5 + `RW-17`/`BUG-097` | — | separate-store parity + same-store idempotency; scoped loss recovery; nondegenerate recall; measured latency/flood gates in [[PLAN]] M5 |
| `W16` | Paper-soak/performance instruments and recorded evidence; dynamic event path must be enabled for its autonomy window | open | — | after M5: ≥4 consecutive market weeks dynamic-paper soak with required density/fault injections; existing statistical gates remain minimum inputs, not funding authority |
| `W17` | Human-gated Robinhood `review_live`: verified deterministic MCP client behind `OrderGateway`; no model access | blocked → `RW-18` + M5/W16 evidence + all pre-live gates + ≥6-month funding bar | — | discovery places zero orders; separately authorized first-live orders reconcile or fail explicitly; external witness green; see [[PLAN]] M6 |
| `W18` | Enable autonomous `auto_veto` only after reconciled `review_live` evidence and external witness/performance gates | blocked → `W17` + W16/SEC P0 gates | — | separately recorded auto-veto soak with responsive veto/kill, zero unexplained reconciliation failures, and green external witness |
| `W19` | Enable `auto_full` mechanical execution; model judgment boundary remains unchanged | blocked → `W18` soak + adversarial review | — | no reconciliation/audit/kill-path failures and every degradation still reaches `watch_only` |

---

## Defect ledger

Found by the 2026-08-09 audit. Every closed row below shipped with a regression test; blocked
rows retain their named prerequisite.

| ID | What | Sev | Status | Where |
|---|---|---|---|---|
| `BUG-096` | Market calendar covers only 2025–2026, then falls back to weekdays; an out-of-range NYSE holiday such as 2027-01-01 is treated as open instead of failing closed | high pre-live | closed | `orchestrator/market_calendar.py` |
| `BUG-097` | Stale-data breaker used the generic L6 drop to `review_live`; PR #31 routes sampled quote staleness through the required L8 `watch_only` path | high pre-live | evidence pending — merged; real stale transition required | `orchestrator/breakers.py`, `orchestrator/scheduler.py` |
| `BUG-100` | Existing SQLite databases predate `risk_decision.execution_verdict`; `create_all` does not add columns, so startup crashes on the first ORM read | high paper | closed — PR #36 | `storage/engine.py`, `tests/schemas/test_migration.py` |
| `BUG-101` | Quote polling compared every sequential response with one pre-request clock sample, falsely rejecting later exchange timestamps as future data and forcing `watch_only` | high paper | closed — PR #36; two live cycles and matrix green | `ingest/market_data.py`, `tests/ingest/test_market_data.py` |
| `BUG-102` | Daily bars run only at 07:30 ET, but the runbook permits an 08:30 start; a late start/restart leaves ADV/volatility empty all day | high paper | closed — PR #36; 11-symbol late-start live check and matrix green | `main.py`, `tests/system/test_rw01_market_day.py` |
| `BUG-103` | Alpaca historical-bars requests omitted `start`; the API defaults to the current day, so `limit=60` returned one bar and every ADV/volatility calculation failed | high paper | closed — PR #36; official contract, live hydration, and matrix green | `brokers/alpaca_paper.py`, `tests/brokers/test_alpaca_paper.py` |
| `BUG-104` | SQLite legacy transaction control let audit verification read ledger rows and the signed high-water from different WAL snapshots during a concurrent ingest commit, producing a false paper-performance `watch_only` alert | high paper | closed — PR #36; concurrency/tamper regressions and matrix green | `storage/audit.py`, `brokers/paper_performance.py`, `tests/storage/test_audit.py`, `tests/brokers/test_paper_performance.py` |
| `BUG-106` | EDGAR baseline rows timestamp from the poller's clock sample while their signed audit transitions independently sample the audit clock milliseconds later; exact history verification therefore rejects every real-clock baseline and forces `sec:edgar` to `watch_only` | high paper | merged — PR #37; live confirmation green; matrix running at merge time | signed timestamp authority plus bounded one-time legacy reconciliation; a real composed `read_only` run repaired 11/11 rows once, ingested one filing/four deltas, then completed a clean 11/11 scheduled retry with the chain green and zero orders; advancing-clock, rollback, skew-boundary, concurrency, and composed-run regressions; Windows 2,248 passed / 1 skipped and Ruff clean; DeepSeek Flash found no P0/P1, Pro produced no attestation before timeout; [[evidence/bug106-edgar-baseline-timestamp-2026-08-17]] |
| `BUG-107` | W-24's Discord exact-message verifier ignores `edited_timestamp` and does not bind the expected bot author, so an edited old message can retain its original server timestamp while presenting new commitment content | high paper | closed — PR #41; cross-platform attestation pending | Exact `/users/@me` author binding; edited/missing edit state/webhook/wrong-author/deleted/unreachable failures; real private-channel post/readback green |
| `BUG-108` | W-24 requires one candidate `source_accession` to equal the current EDGAR baseline, then derives eligibility only from that accession; an ordinary later 10-Q/8-K hides no filing but supersedes the announcement and usually lacks complete merger terms, deadlocking a real 30-name prospective cohort | high paper | closed — PR #42; cross-platform attestation pending | `merger_selection_manifest.v2` witnesses the original source, complete ordered signed source set, explicit current baseline, and existing filing-set authority; predictions/labels derive from the frozen set; omissions, unsigned rows, chronology errors, cross-document phrase synthesis, v1, and tampering fail closed. Windows 2,303 passed / 1 skipped + Ruff clean; all six hosted jobs were billing-blocked before steps; real W-24 selection/evidence remains absent; [[evidence/w24-prospective-intake-2026-08-18]] |
| `BUG-109` | W-24's deliberately narrow eligibility grammar rejects an authoritative real cash-merger 8-K: CRNX states that common shares are "canceled and automatically converted" for cash under an Agreement and Plan of Merger, but `_CASH` and `_DEFINITIVE` both miss those routine forms | high paper | closed — PR #43; external/cross-platform attestation pending | Decimal-only sentence span, explicit cash, bounded conversion grammar, standard agreement assertion, and not/never negation; adversarial review found and closed same-sentence option, preferred-share, and note misattribution. Full Windows 2,311 passed / 1 skipped with Ruff clean; all six hosted jobs executed zero steps under the payment restriction. Post-merge CRNX five-filing semantic set passes, while the ≥30 cohort, dates/current-target, and independent ≥95% precision gates remain closed; [[evidence/bug109-w24-real-sec-grammar-2026-08-18]]; [[evidence/w24-post-bug109-rescreen-2026-08-18]] |
| `BUG-110` | W-25's S3 trust read re-downloads and rechecks every retained version; production append invokes that full read twice, and every gate/mode/gateway/performance/budget consult adds another. The weekday 30-minute cadence therefore creates unbounded request and latency growth and can turn the fail-closed witness into an availability/cost hazard. | high pre-live | closed — PR #48; hosted attestation missing | Schema-v2 fixed-width reverse-sequence keys plus one 128-entry candidate page make the trust read independent of retained-history length. Incomplete candidate groups, delete/shadow/conflict, malformed binding, retention faults, API ambiguity, and concurrent fork/higher-head writes fail closed; no mutable pointer or local cache contributes authority. Windows: 2,321 passed / 1 skipped; Ruff clean. All six PR #48 jobs executed zero steps under GitHub's payment restriction, so cross-platform attestation and real Object Lock evidence remain open W-25 gates, not BUG-110 code failures; [[evidence/bug110-bounded-witness-read-2026-08-18]]. |
| `BUG-040` | `verify_proposal`'s `max_staleness_ns` was inert because `now_ns` had no caller | med | closed — PR #31 | `quant/verify.py`, `workflows/proposals.py` |
| `BUG-041` | `Runner` uses one `PnLTracker` across all symbols; pairs attribution is noise | med | closed | PR #21 |
| `BUG-042` | `Pairs` exit/stop keyed only on `position_y`; a half-filled entry leaves a naked leg | med | closed | PR #22 |
| `BUG-043` | Discord `_quant` blocks the event loop; `/quant/sim` concurrency is unbounded | med | closed | PR #24 |
| `BUG-044` | Two definitions of "how many shares" — `proposal_quantity` vs `_resolve_order` | med | closed | PR #29; cross-platform CI rerun pending |
| `BUG-045` | `settings.guild_id` does not exist; the bot always does a slow global command sync | low | closed | PR #23 |
| `BUG-046` | `/ws/quant` lacked production composition and an honest snapshot producer; PR #31 wires both | low | evidence pending — merged; live sequence progression required | `orchestrator/app.py`, `orchestrator/quant_ws.py`, `main.py` |
| `BUG-047` | `GuardLimits.max_loss` documented in ticks, fed dollars | low | closed | PR #19 |
| `BUG-048` | `AutoLoop` acks immediately, defeating `PENDING_NEW` | low | closed | PR #20 |
| `BUG-049` | Purity gate checks only `verify.py`'s own imports, not transitive ones | low | closed | PR #18 |
| `BUG-050` | `.gitignore:24 /data/` is root-anchored; the app writes to `app/data/` | low | closed | PR #25 |
| `BUG-051` | `audit-head.json` is tracked, machine-written **and** auto-committed — two branches collide → `AnchorRewind` | med | closed | PR #26 |
| `BUG-098` | `SimVenue.advance` snapshots ready orders before applying earlier due cancels, so a cancel can win by timestamp but the cancelled order is still released/rested | med | closed | PR #27; cross-platform CI rerun pending |
| `BUG-099` | `_absorb_outcomes` applies all STP cancellations before same-batch fills, allowing a partially filled victim to transition `cancelled → partially_filled` and crash | med | closed | PR #28; cross-platform CI rerun pending |
| `BUG-105` | PyArrow pathname reads could retain a Windows staging-file handle through snapshot cleanup; publication succeeded but `unlink` raised `WinError 32`, failing the ingest/test run | med | closed — PR #36 replacement matrix green | `storage/parquet_store.py`, `tests/storage/test_parquet.py` |

Found by the 2026-08-09 pre-plan sweep (nine-agent audit) and fixed on
`audit/pre-plan-bug-sweep` (2026-08-10), one `fix(BUG-xxx)` commit per row, each with a
test that fails without it. The order-path changes carried a DeepSeek adversarial-review
round (3 real findings, fixed in the same branch). The six vendored-tree fixes flowed
UPSTREAM (edgar-morning-brew `cockpit/pre-plan-bug-sweep` @ 92027d7) and were re-pinned
per PROVENANCE.md. The sweep merged in PR #12.

| ID | What | Sev | Status | Where |
|---|---|---|---|---|
| `BUG-052` | Submit never consults broker-returned status; fill edges test-only → pending-exposure cap permanently self-blocks | high | closed | `brokers/gateway.py:713` |
| `BUG-053` | Fresh-CIK first poll always raises `PollFilingLimitExceeded` — baseline machinery is dead wiring | high | closed | `filings/edgar/submissions_poll.py:484` |
| `BUG-054` | Morning-brew success path overwrites `prefiltered_ignore` → audited dispositions lie, bulk calendar re-dispatched | high | closed | `workflows/morning_brew.py:282` |
| `BUG-055` | Mid-loop degradation re-covers already-escalated events — duplicate proposals/orders for one event | med | closed | `workflows/triage_loop.py:396` |
| `BUG-056` | SEC-11 git anchor commit always fails: `git -C` + repo-relative path doubles the pathspec | high | closed | `bridge/witness.py:102` |
| `BUG-057` | Maker retires a partially-filled quote without cancelling the resting remainder — untracked orders accumulate | high | closed | `quant/strategies/market_maker.py:269` |
| `BUG-058` | Breaker signals: None means UNSAMPLED (visible, cannot read healthy); schema, broker-error, RW-14 staleness, W-22 equity drawdown, and audited round-trip loss inputs are wired | med | closed | `orchestrator/breakers.py` |
| `BUG-059` | Empty audit chain falls back to env-configurable `settings.mode` — auto mode reachable with no gate | med | closed | `orchestrator/mode_ladder.py:148` |
| `BUG-060` | SEC-15 modal double-confirm skipped when `notional is None` (the bot's default) — fail-open | med | closed | `bridge/approval.py:211` |
| `BUG-061` | `armed_until` recorded but never enforced; disarm rides on one cron firing exactly at 16:00 | med | closed | `orchestrator/mode_ladder.py:284` |
| `BUG-062` | Broker exception during submit skips the covering audit result — permanently dangling SEC-13 intent | med | closed | `brokers/gateway.py:690` |
| `BUG-063` | `_limit_signal` scans stderr with no channel attribution — feed text can trip the vendor breaker | med | closed | `agent_runner/runner.py:210` |
| `BUG-064` | One malformed JSONL line re-exposes Codex model text to the stdout limit scan | low | closed | `agent_runner/runner.py:166` |
| `BUG-065` | Repair-path limit/timeout opens the vendor breaker but is audited as a schema quarantine, no `EVENT_WATCH_ONLY` | med | closed | `agent_runner/runner.py:419` |
| `BUG-066` | Null `market_value` in one holding → `Decimal('NaN')` comparison raises out of `evaluate()` — crash, not fail-closed | med | closed | `workflows/proposals.py:102` |
| `BUG-067` | `verify_chain()` returns `ok=True` on a fully erased chain (rows + watermark deleted) | med | closed | `storage/audit.py:329` |
| `BUG-068` | `result()` double-resolve guard is check-then-act across two transactions, not under `_lock` | med | closed | `storage/audit.py:211` |
| `BUG-069` | finnhub + alpaca-news pollers commit the dedup key before emit with no `discard` rollback — permanent silent loss | med | closed | `ingest/finnhub_poll.py:35`, `ingest/alpaca_news.py:58` |
| `BUG-070` | `_TAG_RE` mis-segments legal HTML (`>` in attributes, script/style bodies become citable runs) and mangled spans byte-verify | med | closed | `filings/parse/_html.py:33` |
| `BUG-071` | Reversed diff mints REMOVED sections as `new_section` citing the prior filing — every consecutive-8-K pair | med | closed | `filings/delta/text_diff.py:121` |
| `BUG-072` | `_reconcile_sections` quarantines legitimate 10-Q structure variation — whole filing loses all deltas | med | closed | `filings/delta/text_diff.py:162` |
| `BUG-073` | WP5 numeric re-parse can't reproduce WP4 (paren negatives, `&#160;`) — one figure flips the whole brief unapprovable | med | closed | `filings/cite/claim_gate.py:63` |
| `BUG-074` | Market orders (`price_ticks=None`) contribute zero to the gross-exposure cap | med | closed | `quant/guards.py:149` |
| `BUG-075` | `_check_replace` double-counts filled shares (position already contains this order's fills) | med | closed | `quant/guards.py:187` |
| `BUG-076` | `traded = result.filled_qty or qty` reports a full-size phantom print when the matcher filled 0 | med | closed | `quant/feed/synthetic.py:229` |
| `BUG-077` | `validate_ordering` only rejects seq regressions — forward gaps pass silently despite being "the" gap mechanism | med | closed | `quant/feed/__init__.py:99` |
| `BUG-078` | Replace of an in-flight order silently dropped (cancel path handles in-flight; replace doesn't) | med | closed | `quant/execution/autoloop.py:225` |
| `BUG-079` | `market_data_ns` back-dates only the snapshot `ts` — configured data latency is a no-op | med | closed | `quant/execution/sim_venue.py:224` |
| `BUG-080` | `AutoVetoCoordinator._tickets` append-only, never pruned — unbounded growth | low | closed | `bridge/veto.py:86` |
| `BUG-081` | `SnapshotSource` unsynchronized two-field publish/read — torn version/snapshot pair | low | closed | `orchestrator/quant_ws.py:78` |
| `BUG-082` | `st.lock` held across 2–4 blocking HTTP calls — killswitch/mode-drop serialized behind broker latency | low | blocked | `brokers/gateway.py` — DEFERRED: splitting the lock around the broker call re-opens the TOCTOU the locked submit exists to close, so it needs its own package (two-phase lock design + adversarial round). The sweep's review confirmed lock ORDERING is deadlock-free; the cost is serialization only. Do not fix casually. |
| `BUG-083` | Same-day same-form "prior" resolves to the LATER-submitted filing (`accession_no.desc()` tie-break) | low | closed | `ingest/edgar_pipeline.py:125` |
| `BUG-084` | Event `ts` fallback emits tz-naive storage format instead of ISO when `detection_ts` absent | low | closed | `ingest/edgar_brew.py:986` |
| `BUG-085` | Non-EDGAR `article_id` flows into `raw_ref` unsanitized — prompt-boundary control exists only in the edgar poller | low | closed | `ingest/normalize.py:104` |
| `BUG-086` | `verbatim` decodes strict utf-8 while `clean` uses replace — one ≥0x80 byte kills all deltas for the filing | low | closed | `filings/parse/_html.py:54` |
| `BUG-087` | No current-side period-collision dedup — amended companyfacts mint contradictory restatements | low | closed | `filings/delta/xbrl_delta.py:160` |
| `BUG-088` | `PollCursor` is write-only — never read to bound the scan; restart re-walks the full window | low | closed | `filings/edgar/submissions_poll.py:527` |
| `BUG-089` | Pre-trade gate accepts `qty <= 0`; the intent then kills the run on `AggressiveOrder.__post_init__` | low | closed | `quant/guards.py:136` |
| `BUG-090` | `cancel_ns` declared and read by `is_instant` but never applied — cancels are always instant | low | closed | `quant/execution/sim_venue.py:143` |
| `BUG-091` | `max_position` projects from fills only — in-flight same-side orders each pass individually | low | closed | `quant/guards.py:141` |
| `BUG-092` | `secret_values=None` hardcoded in production `call_agent` — the Inv 4 argv/prompt check never inspects anything | low | closed | `workflows/agent_calls.py:79` |
| `BUG-093` | Replay-guard remints the ProposalRecord id AFTER RiskDecision/Order captured the colliding id | low | closed | `workflows/proposals.py:141` |
| `BUG-094` | Parquet write-hash and verify-hash computed over different serializations (`default=str` lossy) | low | closed | `storage/parquet_store.py:20` |
| `BUG-095` | `uq_xbrl_fact_identity` includes nullable `period_start` — instant facts never dedup (latent) | low | closed | `schemas/models/xbrlfact.py:51` |

## Open decisions

Not bugs. Work that cannot start until someone chooses.

| ID | Decision | Blocks |
|---|---|---|
| `DEC-01` | Is `BookSnapshot.ts` epoch or feed-relative? *(Answered by `RW-14`: polled quotes make it epoch.)* | `BUG-040` |
| `DEC-02` | Ubuntu keyring backend: require `gnome-keyring`+dbus, accept `keyrings.alt`, or declare Ubuntu dev-only? Tests use an in-memory backend, so even a green Linux CI job would not prove that a real headless Ubuntu deploy can store a secret. | any Linux deployment |
| `DEC-03` | **Resolved 2026-08-14:** operator selected the constrained cash-only, long-target merger-arbitrage research slice under the point-in-time calibration and safety contract in [[research/dec03-strategy-selection-2026-08-14]]. | `W-23` claimed; opening paper Orders remain fail-closed until implementation + calibration evidence + review |
| `DEC-04` | Partial-fill notional: `_pending_ok` takes the MAX signed notional across an order's history precisely to defeat laundering, so counting only the REMAINING notional after a partial needs a **new** signed payload field (`remaining_notional`), never a semantics change to `notional`. Decide the field contract before RW-15 implements the refinement. | the partial-fill cap refinement inside `RW-15` |
| `DEC-05` | Robinhood Agentic can write only to its dedicated account but its credential can read every Robinhood account, including account numbers, balances, positions, and order history. Before any connection: accept that authority, use an identity with no other assets/data, or select a broker with narrower scope. Redaction after retrieval does not reduce token authority. | `RW-18`, `W17` |
| `DEC-06` | Robinhood MCP version/authentication profile: use `RW-18` to discover whether the server supports the 2026-07-28 stateless core and optional OAuth Client Credentials extension or requires delegated interactive OAuth/reauthentication. Do not assume unattended machine auth. | resolved by `RW-18`; blocks `W17` |
| `DEC-07` | Dynamic market-data entitlement: re-verify current Alpaca tiers/connection constraints at build and choose IEX versus consolidated SIP from a measured trigger false-negative/latency study for the bring-up universe. | `RW-13`, dynamic `RW-14` gate |
| `DEC-08` | Robinhood live tool contract: exact request/response schemas, status vocabulary, client-order-id/idempotency behavior, rate/errors, and supported order-type/TIF intersection, all from the `RW-18` no-capital authenticated discovery spike. | resolved by `RW-18`; blocks `W17` and Robinhood side of `RW-15` |

---

## Cleanup — real debt, deliberately deferred until there is an MVP

Each touches code that is still churning. Revisit after `W-22`.

| ID | What | Effort |
|---|---|---|
| `CLN-01` | Extract `_health_fault` from `runner.run_agent` / `_repair` — the same five-check ladder twice, already drifted | S |
| `CLN-02` | Test-support consolidation: 5 identical HALT fixtures, 4 `FakeTransport` copies, ~19 hand-rolled state fixtures with 8 HMAC-key literals | M |
| `CLN-03` | Dead code: `schemas/models/company.py` (zero prod *and* test refs); `POST /proposals/{id}/decision` is a permanent `501` while WP6 shipped elsewhere | S |
| `CLN-04` | Prune 12 MB of SEC fixtures — two `companyfacts.json` are 4.0 and 3.7 MB, ~80% of `app/` on disk. `MANIFEST.md` forbids re-saving filing `.htm` bytes; that rule does not cover the API JSON | M |
| `CLN-05` | Retire `tests/system/harness.build_system`, the hand-maintained `main.compose` mirror. **Needs `compose` to accept `settings`/`hmac_key`/`anchor_store` first — a composition-root change, so it needs an adversarial-review round** | M |
| `CLN-06` | Delete the copy-pasted test-count baseline from the remaining docs; put a floor in CI if one is wanted | S |

---

## Recently closed

For the pattern, not the record — all three were **invisible**, which is the point.

| ID | What | Closed |
|---|---|---|
| `SEC-14` | Grounding never ran when `decision_agent_cwd` was pinned. `AgentSpec` had no `cwd`, so SEC-06 refused every spawn and a bare `except` turned it into "flagged". No log, no audit event, no `AgentJob` row. The verifier was dead and reported healthy. | PR #8 |
| `SEC-25` | A headline could trip the vendor breaker. Closed twice: `main` went stderr-only (blind to real stdout limits), `rw01` scanned stdout when whole-buffer JSON parse failed (reopened the hole for Codex's JSONL). Correct version recognises **both** runtimes' payload shapes. | PR #10 |
| `QT-07` | `AutoLoop` went silent after its first non-resting order — 6 submissions against `Runner`'s 1139 on the same feed, and nothing reported it. | PR #9 |
| `QT-02` | Unclamped inventory skew, quadratic in price → `OverflowError` → HTTP 500 on `/quant/sim` from an ordinary `quote_size`. | PR #9 |
| — | `Guards` waved through every `ReplaceOrder`; 1000 of 1000 same-nanosecond requotes allowed. The string `ReplaceOrder` appeared in zero test files. | PR #9 |

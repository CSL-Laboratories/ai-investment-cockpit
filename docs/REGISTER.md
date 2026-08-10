---
title: The register — one ID space, one place to claim work
status: live
last-verified: 2026-08-09
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

## Status vocabulary

| Status | Means |
|---|---|
| `open` | Nobody has claimed it. Take it. |
| `claimed` | Someone is on it. Branch named. **Do not start a parallel fix.** |
| `in review` | PR open. |
| `closed` | Merged, with a test that fails without it. |
| `blocked` | Cannot proceed until a named decision is made. The blocker is stated. |

---

## Milestones — see [[PLAN]] §4

| ID | What | Status | Owner | Gate |
|---|---|---|---|---|
| `RW-11` | Wire `app/filings/` into `build_ingest_fetchers` as `sec:edgar` | open | — | cited deltas on a live day **+** ≥95% field-level extraction precision on a post-cutoff hand-labelled sample |
| `RW-12` | Delete `edgar_brew.py` + tests once RW-11 has run a live day | open | — | opened *with* RW-11, not after it |
| `RW-14` | Daily-bars poller (ADV + σ); synthesize `book_fn` from the L1 quote | open | — | `quant_book_seq` advances between observations on a live day |
| `W-21` | Cost model: spread + square-root impact on ADV + borrow; relative net-edge test | open | — | cost-sensitivity curve; the dollar threshold where a trade goes cash-flow negative |
| `W-22` | The paper loop end to end, Discord-driven, fills at bid/ask | open | — | two unattended market days a week apart, `verify_chain().ok` throughout |
| `RW-13` | Event-driven intraday trigger off the existing stream (post-MVP) | open | — | see [[PLAN]] §3 — **not in the MVP** |

---

## Open defects

Found by the 2026-08-09 audit. Each needs a test that fails before the fix.

| ID | What | Sev | Status | Where |
|---|---|---|---|---|
| `BUG-040` | `verify_proposal`'s `max_staleness_ns` is inert — `now_ns` has no caller | med | **blocked** → unblocked by `RW-14` | `quant/verify.py:200` |
| `BUG-041` | `Runner` uses one `PnLTracker` across all symbols; pairs attribution is noise | med | open | `quant/runner.py:226` |
| `BUG-042` | `Pairs` exit/stop keyed only on `position_y`; a half-filled entry leaves a naked leg | med | open | `quant/strategies/pairs.py:147` |
| `BUG-043` | Discord `_quant` blocks the event loop; `/quant/sim` concurrency is unbounded | med | open | `bridge/bot.py`, `quant_api.py` |
| `BUG-044` | Two definitions of "how many shares" — `proposal_quantity` vs `_resolve_order` | med | open | `quant/verify.py`, `workflows/proposals.py` |
| `BUG-045` | `settings.guild_id` does not exist; the bot always does a slow global command sync | low | open | `main.py:641` |
| `BUG-046` | `/ws/quant` is never registered by `main.compose` — 170 + 214 lines of dead socket | low | open | `orchestrator/quant_ws.py` |
| `BUG-047` | `GuardLimits.max_loss` documented in ticks, fed dollars | low | open | `quant/execution/autoloop.py:134` |
| `BUG-048` | `AutoLoop` acks immediately, defeating `PENDING_NEW` | low | open | `quant/execution/autoloop.py` |
| `BUG-049` | Purity gate regexes only `verify.py`'s own imports, not transitive ones | low | open | `tests/risk/test_execution_gate.py` |
| `BUG-050` | `.gitignore:18 /data/` is root-anchored; the app writes to `app/data/` | low | open | `.gitignore` |
| `BUG-051` | `audit-head.json` is tracked, machine-written **and** auto-committed — two branches collide → `AnchorRewind` | med | open | `bridge/witness.py:94` |

## Open decisions

Not bugs. Work that cannot start until someone chooses.

| ID | Decision | Blocks |
|---|---|---|
| `DEC-01` | Is `BookSnapshot.ts` epoch or feed-relative? *(Answered by `RW-14`: polled quotes make it epoch.)* | `BUG-040` |
| `DEC-02` | Ubuntu keyring backend: require `gnome-keyring`+dbus, accept `keyrings.alt`, or declare Ubuntu dev-only? Tests pass via an in-memory backend, so **CI is green while a real Ubuntu deploy cannot store a secret.** | any Linux deployment |
| `DEC-03` | Niche selection — CEF discounts / small merger-arb / stay strategy-agnostic | post-MVP; see [[PLAN]] §1 |

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

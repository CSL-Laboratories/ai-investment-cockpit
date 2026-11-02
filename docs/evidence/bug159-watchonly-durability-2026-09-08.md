---
title: BUG-159 watch-only durability D1-D4 implementation evidence
type: evidence
status: implementation
last-verified: 2026-09-08
---

# BUG-159 watch-only durability D1-D4

This bounded production package starts at `abbaf3f` (merged PR #82, first parent
`5d50b25`) and implements the contract in
[[astra-watchonly-claim-shape-2026-09-06]]. It changes no selection, date,
watchlist, soak, model-authority, risk, broker-adapter, or secret path.

## Result

- The eight retained D1-D4 failures are promoted under their original node names
  in `app/tests/system/test_watchonly_durability.py`. The production safety-session
  adaptation and its required extensions pass: **31 passed**.
- The removal-sensitive composition/lifecycle cases and the relevant runner,
  breaker, recovery, gateway, paper, audit/HWM and Order FSM regressions pass:
  **470 passed** in 62.31 s.
- `ruff check .` and `git diff --check` pass.
- Linux diagnostic environment: CPython 3.13.5, SQLite 3.46.1, SQLAlchemy 2.0.52.
  This is not the native Windows plus WSL2 Python 3.11/3.12 release matrix.
- Production delta before evidence/commit metadata: 1,095 additions, 178
  deletions, **917 net lines**. The bounded package remains below the roughly
  1,000 net production-LOC cap.

The immutable research fixture remains byte-identical at SHA-256
`da52eb32ff6c876750ded3afcd5b4d36cd18ad9a9e8bef62e4744ffc8ce7e8fa`.
Run unchanged against the new tree, it reports **2 failed / 15 passed**: its two
remaining cases rebuild an explicitly non-production `OrchestratorState.build()`
test seam without the required session envelope. They are not converted into
passes. Their owned counterparts use production session admission and pass in the
31-case suite; `main.compose()` always requires that envelope.

## Claim boundaries

Denial is admitted before transition persistence; pending/failed state blocks
approval and final authorization. Recovery publishes permission only after its
exact signed identity is reconciled. Production startup atomically verifies the
predecessor and signs a versioned session owner. Interrupted, legacy, malformed,
concurrently owned, or unproven-shutdown sessions start held without automatic
release. A clean restart and a healthy final broker-entry control both submit
through fake leaves.

After the real submit intent, the gateway performs a new snapshot-pinned full-chain
verification under the existing anchor policy, then serializes admitted denial and
runtime generation at final authorization. Invalid or unreadable final authority
is a sticky `final_audit_unverified` veto even when no safe refusal append is
possible. The signed intent remains unresolved rather than fabricating a broker
result.

The D4 guarantee is deliberately narrow: it vetoes corruption committed before the
new final snapshot. It does **not** claim atomicity against an unrestricted SQL
writer committing after that snapshot. The owned residual characterization proves
that later mutation can still allow one already-authorized fake broker entry before
the existing #74 FSM integrity refusal raises. Stronger writer exclusion is outside
BUG-159.

## Validation notes

The 470-case run includes the #68 HWM-before-head, #70 snapshot-pinned result,
#73 HWM-signing refusal, and #74 detached-object/F1/F2 regressions. An attempted
broader RW-01 case that performs an unfaked SEC ticker fetch fails identically on
the untouched base and this branch because this sandbox cannot resolve
`www.sec.gov`; it is not counted as package evidence. No full-suite or four-cell
platform acceptance claim is made.

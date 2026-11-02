---
title: BUG-106 EDGAR baseline timestamp evidence — 2026-08-17
type: evidence
status: live
last-verified: 2026-08-17
related:
  - ../PLAN
  - ../REGISTER
  - ../runbooks/w22-paper-mvp
---

# BUG-106 EDGAR baseline timestamp evidence — 2026-08-17

This record covers a bounded `read_only` live confirmation of commit `5dea321` on
`work/bug106-edgar-baseline-timestamp`. It does not authorize paper, live, or autonomous
trading, and it is not cross-platform CI evidence.

## Offline verification

- Windows full suite: 2,248 passed / 1 skipped, with five dependency warnings.
- Ruff and `git diff --check`: clean.
- Regression coverage includes advancing-clock establish/advance/touch, one-time legacy repair,
  post-repair tamper refusal, exact skew bounds, backward-skew refusal, same-store concurrency,
  audit-append rollback, and composed-path reachability.
- A copy of the operational database repaired 11/11 legacy baseline rows exactly once, advanced
  audit head 9,885 → 9,896, added no second repair on reload, and retained a valid chain.
- A read-only DeepSeek v4 Flash caller/test audit found no P0/P1 issue. DeepSeek v4 Pro read the
  requested files but timed out without a verdict, so it is not counted as an attestation.

## Composed live confirmation

The service ran from the clean committed worktree while using the existing operational database
and raw evidence directories. It remained `read_only` throughout.

At the first 09:15 PT SEC cadence:

- all 11 legacy baseline rows received exactly one signed `edgar-baseline-repair` event;
- one filing was ingested and four deltas were persisted;
- the audit chain remained valid;
- seven CIKs received completion watermarks and four reported a transient poll error.

The adapter retained only the summarized first-tick count, so the exact per-CIK error text is not
claimed here. A transactionally consistent database-copy retry of those four CIKs immediately
completed with zero errors. At 09:20 PT, the next normal composed cadence then completed all 11
CIKs with zero poll errors and zero delta errors, adding 11 signed `edgar_poll_completed` events.
This distinguishes the recovered first-tick condition from a persistent baseline-verification
failure without inventing a cause that was not durably recorded.

The final authenticated status and stopped-database checks showed:

- `sec:edgar`: two polls, one persisted event, zero current/consecutive errors, trustworthy
  `nothing-material` status after the clean retry;
- audit chain valid at head sequence 10,040;
- 11 baseline rows and exactly 11 signed repair events;
- valid paper-performance sampling at $100,000 equity, zero drawdown, and zero round trips;
- zero proposals, approvals, orders, and fill notifications;
- mode `read_only`; global `watch_only` remained true because the Claude usage window was
  exhausted, independently of the recovered SEC feed.

The diagnostic process was then stopped and loopback port 8787 was verified closed.

## Remaining gates

- Publish/review the branch and run the cross-platform matrix when GitHub recovers.
- Preserve the four-error first-tick observability limitation as follow-up work; a summary-only
  transient must not be retrospectively assigned a cause.
- This confirms BUG-106's live repair/retry path. It does not by itself satisfy RW-11's
  hand-labelled ≥95% extraction-precision gate, RW-14's stale-feed drill, RW-15 broker
  reconciliation, W-23 semantic/evidence activation, or W-22's two unattended market days.

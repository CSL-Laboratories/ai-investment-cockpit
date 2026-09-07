---
title: RW-24 measured reliability follow-up
status: in-progress
last-verified: 2026-09-07
---

# Intake

The operator requested further bug fixes and measured process improvements after the audit.
This bounded follow-up begins after RW-23's implementation and independent review slices.
RW-23 repairs and independent reviews are complete; RW-24 is now the active package.

1. **Operator change:** newly discovered holdings receive daily metrics immediately; a bounded
   stream diagnostic honors its frame budget even for malformed input; triage avoids repeated
   ledger scans; validation exercises the installed Discord command tree and its absent-library
   fallback in the same environment.
2. **Existing paths:** `ingest/market_data.py`, `ingest/alpaca_news.py`,
   `workflows/budget.py::make_event_type_verifier`, and existing bridge tests/dependency setup.
3. **Retirement:** remove the incomplete readiness shortcut and repeated per-ID query path.
   No persistent authority cache, background worker, new feed, broker or model capability.
4. **Reachability/gates:** intraday holding discovery through quote polling and bar refresh;
   malformed frame iterator with a strict consumption ceiling; real SQLite fixture ledgers
   with query counts and repeated timing; tampering/new-tick snapshot controls; real command
   tree construction plus isolated import failure. Full final DEC-09 matrix remains required.
5. **IDs:** BUG-157 (daily readiness ignores expanded universe), BUG-158 (malformed frames
   bypass `max_frames`), and RW-24's measured triage and validation improvements.
6. **Isolation:** same separate implementation worktree, synthetic sources and throwaway
   state. Dependency updates affect only dedicated test environments. Existing autonomous
   capture/test state and main checkout remain untouched.
7. **Size:** target under 150 changed production lines; separate fix/performance/validation
   commits. Any larger cache or ingest transport redesign remains out of scope.

# Baseline evidence

- BUG-157: AAPL loaded; MSFT then discovered via holdings and successfully quoted. Two
  daily refreshes returned only cached AAPL, with `metrics('MSFT') is None` and only one
  vendor bar call (AAPL). Root independently inspected the `_bars_loaded_for == today`
  return that omits current-universe coverage.
- BUG-158: `AlpacaNewsConsumer(lambda: iter(['bad-frame', 'another-bad-frame']))`
  with `max_frames=1` returned zero emissions but `frame_error=2`. On an unbounded
  malformed iterator the diagnostic never reaches its requested bound.
- The current dependency recipe deliberately omits `discord.py`; all six existing real
  command-tree tests skip as a module. Its fallback test skips instead when the library
  is installed. These are complementary behaviors and can be covered together.
- Triage ledger measurements and exact implementation evidence follow below.
- Before production changes, a fixture-only prototype reduced SELECT count from 100 to 3
  for 1,000 signed ingest entries and 100 candidates; three-repeat median was 4,365.0 ms
  versus 90.9 ms. The candidate performs full-chain verification in one transaction.
  Timings came from a shared workstation without external-anchor I/O; query reduction is
  the stable comparison. A portable benchmark will compare the frozen old body to actual
  changed production code and retain the final measurements.

---
title: RW-24 measured reliability follow-up
status: implemented-pending-final-matrix
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
- At baseline, the dependency recipe deliberately omitted `discord.py`; all six existing real
  command-tree tests skip as a module. Its fallback test skips instead when the library
  is installed. These are complementary behaviors and can be covered together.
- Triage ledger measurements and exact implementation evidence follow below.
- Before production changes, a fixture-only prototype reduced SELECT count from 100 to 3
  for 1,000 signed ingest entries and 100 candidates; three-repeat median was 4,365.0 ms
  versus 90.9 ms. The candidate performs full-chain verification in one transaction.
  Timings came from a shared workstation without external-anchor I/O; query reduction is
  the stable comparison. The portable benchmark now compares the frozen old body to actual
  changed production code; final measurements are linked below.

# Delivery and independent review

- `6d2a747`: BUG-157 uses successful per-symbol daily readiness. Eight new composed
  controls include discovery, retry, cache verification on re-add, rollover and removal
  during fetch. All 33 focused checks passed; existing symbols incur no repeated fetch
  or calculation when only a new holding needs metrics. Root reviewed the change.
- `5c37e69`: BUG-158 applies the same frame bound before a malformed-frame continue.
  Both new tests failed against the baseline; 22 focused checks passed after the fix.
- `98bd5e5`: all matrix environments now install `discord.py`; six real command-tree
  tests are required instead of skipped. A subprocess with an import blocker exercises
  the absent-library fallback even when Discord is installed. All 14 bridge checks passed.
- `b9374ec`: one lazy verified snapshot per triage tick replaces repeated per-ID ledger
  queries. A new tick reloads; spending always rechecks authority. Missing legacy maps
  supply no bindings, malformed maps refuse, and conflicting IDs remain ambiguous in the
  triage snapshot. The separate historical spend reader remains unchanged.
  82 focused budget/triage checks passed. Independent authority review added six warmed-
  cache/witness-failure tests; its 76-case run passed. Both reviewers found no remaining blocker.
- Actual production benchmark, alternating order with one warmup and three timed rounds:
  1,000 signed ingest entries / 100 candidates took 2,981.647 ms with the frozen old body
  and 75.060 ms with changed production (median). SELECT count fell from 100 to 3.
  Smaller scenarios also improved. This is a synthetic operation-level measurement,
  not a claim about whole-system latency; external witness I/O is excluded.
- Final evidence review found two acceptance weaknesses in the copied verifier: trusted
  source labels without an independently pinned manifest, and unvalidated recorded command
  arguments. The new evidence verifier checks both and requires all 225 new/regained test
  cases to pass. Negative tests refuse relabelled history, substituted commands and skipped,
  missing or duplicated required cases. This changes evidence validation only.

# Validation status

Resumed on 2026-09-07 (2026-09-08 UTC), with operator authorization to finish the
campaign, commit repairs in slices and open the private repository PR. The next
bounded work is the two failures from the completed 3.11 matrix: preserve actual
source/hash binding in the synthetic replay, and preserve the human-review status
for unresolved proposal quantities after risk rejection. Production integrity and
order refusal gates remain unchanged. Focused regressions and adversarial review
precede a newly frozen four-cell matrix. GitHub main remains `c231dc2` after fetch.

The replay fixture now adds the synthetic day to a detached original payload and
hashes that payload. A new control failed before the repair and proves valid binding,
day distinction, preserved caller payloads and repeat-call idempotence. All nine replay
cases and four original-news integrity cases pass (13 total); targeted Ruff passes.

All implementation slices and independent authority review are complete. The combined
RW-23/RW-24 source at `6d7d7df` exposed a replay-fixture hash mismatch in the Python
3.11 DEC-09 runs. The helper changes only the hash; BUG-150 correctly requires that
the hash match the separately retained original input. The fixture repair and a new
complete matrix remain required. The operator requested a pause before the next batch.
Evidence, benchmark results and exact source binding:
[[rw23-rw24-local-matrix-2026-09-07/README]]; restart details:
[[rw23-rw24-local-matrix-2026-09-07/PAUSE-CHECKPOINT]].

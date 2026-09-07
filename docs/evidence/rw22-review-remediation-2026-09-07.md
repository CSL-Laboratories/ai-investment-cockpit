---
title: RW-22 review remediation — 2026-09-07
type: evidence
status: live
last-verified: 2026-09-07
---

# Scope

Operator-authorized repair of the five review findings against
`work/bugfix-audit-2026-09-07` at `0809a617246054d6fdafd8b061ddd36d7c95f9ee`.
The original implementation is `f779016`; its predecessor is `cf2bcec`.
The starting review passed 2,788 tests / 3 skipped on Windows Python 3.11 and Ruff,
but independently reproduced four regressions and an incomplete BUG-126 repair.

The repairs and focused regressions are implemented. The full DEC-09 matrix remains
pending in this implementation record; its results will be appended against the exact
implementation commit. The branch remains unmerged.

## Changes and proofs

| Finding | Repair | Regression evidence |
|---|---|---|
| BUG-140: denied/conditional cash terms outside a narrow character window produced a verified edge in a signed-source fixture | Inspect qualification across the whole sentence. Exempt only complete, pinned fractional-share issuance clauses at clause boundaries | `tests/test_rw22_semantics.py`: distant leading/trailing denials, conditions, ambiguous issuance clauses, signed-source provider refusals and matching positive controls. Independent supplemental cases in `tests/test_rw22_semantics_independent.py` |
| BUG-139: broad fill-error suppression returned a completed simulation with position 3 despite 13 venue shares traded | Latch invalid accounting, halt before further event/callback/timer work, retain rejected and remaining batch fills as evidence, omit invalid P&L, and record validity/counter/fill evidence in the ending audit | `tests/test_rw22_autoloop.py`: real partial/replace/overfill sequence, advance-outcome batch, guard-reset refusal and full/partial traveling-cancel success |
| BUG-126: erasing a tightening before a later surviving entry restored an older looser limit | Verify all genuine history through signed high-water and select the latest limits from the same pinned transaction. Only forged appended rows above an intact committed prefix may be ignored | `tests/test_rw22_limits_recovery.py`: deletion/mutation/time tamper, forged tails, signed-watermark replay, real SQLite check/use race; independent observed-floor and benign concurrent-append controls in `tests/test_rw22_review_adversarial.py` |
| BUG-136: signed `2500` disagreed with reloaded `2500.00` | Compare finite money by exact Decimal value. New drafts floor the display ceiling to cents before signing and persisting it; comparison never rounds away a changed value | `tests/bridge/test_rw22_risk_facts.py`, `tests/workflows/test_rw22_risk_persistence.py`, and independent `tests/bridge/test_rw22_render_adversarial.py`: actual database/render round trips, subcent source ceiling, cent/subcent repaint, malformed values and large exponents |
| BUG-131: global integrity failure made healthy polls repeatedly record vendor recovery | Separate effective watch-only refusal from a recoverable vendor state. Guard recovery centrally under the state lock and update scheduler, EDGAR and paper-performance callers | `tests/test_rw22_limits_recovery.py`: all three composed callers under a restored integrity fault, direct-call refusal, exactly one concurrent recovery and quiet subsequent healthy polls |

The simulator's pre-existing replacement total-versus-remaining mismatch is now an explicit
audited invalid run; this package does not redesign replacement sizing. The rendering change
fixes the persisted helper path, while current triage still uses the same-tick embed and
`/paper preview` remains a different path. Historical subcent signed ceilings are not silently
re-signed or rounded to validate a disagreeing legacy row; such rows remain unverified.

BUG-126 was an incomplete repair in `0809a61`, rather than a new regression. The other four
findings were introduced by the original local implementation. This package does not claim
the rest of that implementation's BUG-126..145 assertions have been independently certified.

## Independent adversarial review

Production areas were divided between subagents; the integrator owned rendering, docs and
validation. Reviewers then examined changes authored by another agent. Counterfactual parser
checks fail when the original qualifier window is restored. The rendering regression first
ran red (11 failed / 4 passed), then green after exact-value comparison and conservative cents.

The integrator's review found an additional direct recovery consequence: publishing `ok`
before the audit append could make a failed recovery permanently unrecorded under the new
idempotent guard. Recovery now publishes state/generation only after the audit succeeds.
A failure-injection test proves the original exhausted state survives and a later retry
records exactly one recovery. Mode lowering, if already recorded, remains conservative.

Independent limits probes confirm that an earlier signed watermark plus a forged tail cannot
defeat the in-process observed-head floor, and an ordinary append during a read retains one
consistent SQLite snapshot. Ordinary `verify_chain`/`verify_session` stay strict: the limited
limits fallback never declares a forged-tail ledger valid for an order.

## Validation and remaining gates

Focused results before the matrix: limits/recovery 135 passed; simulation and related existing
coverage 79 passed; final dedicated semantics 16 passed; rendering and its independent
supplement 28 passed; independent limits controls 2 passed. These overlapping scoped counts
are not a full-suite total. Ruff and whitespace checks passed.

The existing dedicated project environments are native Windows Python 3.11.16/3.12.14 under
`C:/t/a26` and Ubuntu 24.04 WSL2 Python 3.11.16/3.12.14 under `/audit` in
`Cockpit-Audit-2404`. Matrix runs use isolated source archives and temporary directories,
clear ambient `COCKPIT_*`, and retain source hashes and per-cell dependency/test results.
Full four-cell results are pending here.

Tests use synthetic data, in-memory credentials and fixture witnesses. No live feed capture,
broker trade, source cohort, external witness, selection/watchlist admission, soak rewrite,
paper activation, live rung, remote deployment or repository publication is performed.
Code and local-platform evidence cannot satisfy the outstanding empirical gates.

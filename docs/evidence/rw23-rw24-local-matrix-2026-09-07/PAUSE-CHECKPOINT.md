# Operator-requested pause — 2026-09-07

The operator requested a pause after the current work to travel and change working
environments. Finish only the two active Python 3.11 runs, preserve the results, and
stop. Do not start Python 3.12, another repair batch, or any new background work until
the operator resumes. Existing autonomous testing belongs to the operator and is
unchanged by this task. Both active runs completed before the pause: Windows has
3,091 passed / 2 failed / 7 errors / 4 skipped; Ubuntu has 3,089 passed / 2 failed /
7 errors / 6 skipped. Each passed all 225 required new/regained cases. The completed
logs and rejected acceptance summary are saved in `completed-failing-311/`.
Both owned test processes exited. The dedicated WSL distro had no remaining user
jobs and was restored to its original stopped state. No next batch was launched.

## Saved work

- Branch: `codex/audit-reliability-2026-09-07`.
- Isolated worktree: `C:/t/cockpit-rw23`.
- Seventeen implementation/documentation slices through `6d7d7df` repair BUG-146–158,
  add 219 application regression cases, require six real Discord command-tree cases,
  and reduce triage binding queries to one verified snapshot per tick.
- Independent authority reviews and focused gates passed. The complete local matrix
  has **not** passed; the current source must not be described as release validated.
- Original checkout remains clean main at `c231dc2` in
  `C:/Users/drfoo/OneDrive/Documents/ChatGPT/ai-investment-cockpit`.
- Commits are local. Nothing was pushed or merged by this task.

## Known remaining repairs

`app/tests/system/replay.py::_make_day_distinct` overwrites an event's `content_hash`
with `content_hash({"ingest": old_hash, "day": day.index})` while leaving `raw_input`
unchanged. `app/workflows/events.py::_item_hash` now correctly rejects that mismatch
under BUG-150. It fails from the first synthetic day; seven report-fixture tests error
and the direct reusable-runner test fails for the same reason. Both the full Windows
run and an independent isolated reproduction demonstrated the failure.

The bounded repair is fixture-only: make the synthetic original payload day-specific
(for example, add a `replay_day` member to a detached payload), then derive its
`content_hash` from that actual payload. Preserve repeat-call idempotence and day
distinction. Do not relax the production integrity check. No repair was made after
the pause request.

The full Windows run also fails
`tests/workflows/test_execution_verification.py::test_fractional_explicit_quantity_fails_to_human_without_an_order`:
a 5.7-share proposal returns `drafted` instead of `needs_human`. The source sets
`needs_human` for unresolved quantity only inside the risk-approved order-building
branch; the stronger quantity checks can reject earlier. Confirm the complete
outcome and preserve both quantity refusal and the operator-facing status/reason.
This is a separate remaining regression in the composed drafting path; it is not
explained by the replay fixture. Its traceback is retained in the completed matrix
log. No fix or successful retest is claimed.

## Resume sequence

1. Confirm the available host and worktree after travel. Read this checkpoint, the
   matrix README and the RW-23/RW-24 delivery notes; preserve existing automation.
2. Repair the replay fixture, independently review its source/hash binding, and run
   the eight replay cases plus the original-payload refusal controls and Ruff.
   Commit that bounded repair in its own slice. Separately repair and test the
   fractional-quantity status regression, with an adversarial review because it
   touches the proposal/order path. Preserve all no-order and risk refusal gates.
3. Freeze a **new** commit/archive and independently reconstruct its manifest. The
   current runner, launcher, source-binding and verifier pin the old `6d7d7df` source;
   they are historical evidence, not proof for the repaired tree. Use new run paths
   and update all source/manifest/required-case pins consistently. Retain old results.
4. Run all four DEC-09 cells against the repaired archive, at most two concurrently:
   native Windows and Ubuntu WSL2, each Python 3.11 and 3.12. The full runtime profile
   must include `discord.py`, and all 225 new/regained cases must pass without skips.
5. Collect complete evidence, run the strict verifier and negative controls, reconcile
   the delivery documentation, commit the final evidence slice and report actual
   counts. Do not claim a green matrix from focused tests or old-source runs.

The dedicated local test environments are `C:/t/a26/env311`, `C:/t/a26/env312r`, and
`/audit/env311`, `/audit/env312` in distro `Cockpit-Audit-2404`. Its drive automount is
disabled; archives were copied through `\\wsl.localhost\Cockpit-Audit-2404\audit\rw24`.
The archive and runs under `C:/t/r24` and `/audit/rw24` must not be overwritten.

The benchmark measures a synthetic binding-verification operation, not whole-system
latency: 100 candidates use 3 SELECTs instead of 100; the mixed 10,000-row fixture was
about 4.5 times faster. External witness I/O is excluded. Ingest gap diagnostics are
process-local, not a durable replay queue. No paper/live or external-witness gate is
satisfied by these changes.

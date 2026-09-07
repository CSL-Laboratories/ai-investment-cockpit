---
title: BUG-125 — Order FSM freshness and rollback (Astra F1/F2)
type: evidence
status: prepared
last-verified: 2026-09-07
---

# BUG-125 — Order FSM freshness and rollback

RESULT: F1/F2 refusal contracts pass; publication blocked by tool approval policy.
GitHub branch creation required approval while this session uses policy `never`; no remote
branch or PR was created. The prepared change also remains pending platform acceptance.
Branch `fix/bug125-order-fsm-freshness` starts at current main
`a57e8cf20a81b0802a117a403316f6992f159ab1` (merged PR #73).

Claim source: [Astra audit append hardening, F1/F2](https://github.com/NotSkittlz/ai-investment-cockpit/blob/9a2e3a9cb8a857a814fa880734b31b6705e92658/docs/evidence/astra-audit-append-hardening-2026-09-06.md).
The four F1/F2 research regressions and six write-interruption checkpoints from its
`test_append_contracts.py` are promoted into the owning FSM/storage tests; hooks retain
PR #73's `established` signing context.

`transition` and `record_fill_progress` now verify the ledger, compare the caller against
the current Order, and bind that row to its latest signed transition/fill-progress event
inside the same pinned SQLite transaction. Missing/stale/inconsistent authority refuses
before mutation. A competing commit prevents snapshot upgrade; no stale fragment is retried.
Changes are staged on the managed row, and the payload uses refreshed database values,
including retained average price and Numeric rounding. Rounded-away fill progress refuses.
The original detached object is updated only after commit succeeds. Direct
`record_state_change` callers remain attached through commit so rollback expires their
attempted state. An exception after actual COMMIT preserves the durable row/audit pair;
the old FSM caller cannot continue without a fresh read.

Independent adversarial review found and closed the rounded-away progress case and checked
source-state capture inside the transaction. Its 35 cases cover fresh keyless row tampering,
independent-writer interleaving, both FSM operations at six failure boundaries under
Exception/BaseException, actual post-commit acknowledgement loss, numeric normalization,
and pending approval binding. Final review found no remaining blocking F1/F2 issue.
Legacy birth omissions are accepted only for null IDs; unsigned non-null IDs and existing
signed/row mismatches remain refused. This is no retrospective history repair.

## Validation

Linux Python 3.13.5 / SQLite 3.46.1 / SQLAlchemy 2.0.52, project `.venv` with the CI dependency
set, isolated test databases and in-memory keyring:

- Final targeted FSM, pending-exposure, storage, HWM-signing and deterministic #68/#70
  contracts: **142 passed**. Full application Ruff and dependency consistency: clean.
- The promoted FSM assertions against isolated unchanged main: **17 failed / 19 passed**.
  Direct storage rollback assertions against the original method: **2 failed / 12 passed**;
  both failures leave the precommit detached caller ahead of the unchanged durable row.
- Broader brokers/storage/mode/invariant run: **385 passed / 1 failed**. The existing
  `test_independent_append_writers_preserve_all_committed_rows` intermittently refuses a
  virgin-chain concurrent append with `audit high-water missing on a non-empty chain`.
  The same failure reproduced on unchanged main. This separate conservative refusal is
  retained, not repaired or reclassified as a pass by this package.
- The full-suite attempt timed out at 180 seconds (exit 124), with no final result.
  It is not a full-suite pass. Native Windows and Ubuntu/WSL2 on Python 3.11/3.12
  remain required before merge. No hosted workflow was dispatched.

PR #68 HWM-before-head allocation, #70 `result()` snapshot pin, and #73 established-HWM
signing refusal are unchanged. No selection loosen, date loosen, watchlist seed, manufactured
PASS, soak rewrite, Discord post, broker/gateway change, W-24 grammar change, watch_only
D1–D4 change, or secret access. SEC-11 external-witness and all activation gates remain open.

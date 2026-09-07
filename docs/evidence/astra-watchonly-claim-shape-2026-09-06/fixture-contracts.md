# D1–D4 fixture contracts — proposed, not implemented

This is a specification for future owning tests. No new production test or safety
implementation is supplied by this file. The retained executable diagnosis is
[/workspace/w24-evidence/astra-drain-2026-09-06/astra-watchonly-durability-2026-09-06/test_durability_diagnosis.py](/workspace/w24-evidence/astra-drain-2026-09-06/astra-watchonly-durability-2026-09-06/test_durability_diagnosis.py).
Its SHA-256 is `da52eb32ff6c876750ded3afcd5b4d36cd18ad9a9e8bef62e4744ffc8ce7e8fa`.

## Promote the eight retained failed cases

| Finding | Existing node suffix | Future owning file(s) | Essential assertion |
|---|---|---|---|
| D1.1 | `test_failed_watch_only_append_must_not_revive_prior_proposal` | `app/tests/orchestrator/test_core.py`, `app/tests/brokers/test_paper_execution.py` | Append fails; surviving process denied; restart denied with valid older ledger; old signed proposal cannot submit. |
| D1.2a | `test_runner_limit_persistence_gap_must_fail_closed[agent_job]` | `app/tests/workflows/test_agent_calls.py` | A real fake-transport limit admits denial even if AgentJob persistence raises; restart cannot erase it. |
| D1.2b | `test_runner_limit_persistence_gap_must_fail_closed[watch_only]` | same | Failed signed transition remains pending/failed and blocks across restart. |
| D1.3 | `test_failed_edgar_transition_is_not_retried_while_memory_is_exhausted` | `app/tests/orchestrator/test_ingest_runner.py` | A second failing poll cannot silently skip an uncommitted episode or present it as healthy after restart. Durable retry may resolve to committed **exhausted**, never implicit recovery. |
| D2 | `test_failed_feed_recovery_commit_must_keep_order_path_closed` | `app/tests/orchestrator/test_core.py`, paper service tests | While recovery fails, current and rebuilt state remain denied; memory cannot publish `ok` ahead of the signed recovery. |
| D3a | `test_transition_during_submit_intent_must_refuse[False]` | `app/tests/brokers/test_gateway_hardening.py` | Confirm pending denial admission after real intent and before final authorization; zero broker calls, irrespective of whether transition persistence can finish. |
| D3b | `test_transition_during_submit_intent_must_refuse[True]` | same + paper service tests | Separately test pending recovery and completed trip/recovery; old runtime generation must remain refused. Do not claim recovery committed when it is waiting for `st.lock`. |
| D4 | `test_audit_tamper_during_submit_intent_must_refuse` | gateway hardening + audit tests | Commit independent-session tamper after intent, before final verification; explicitly verify invalidity and require zero fake calls. |

The exact old monkeypatch locations may need adaptation when the production protocol moves.
Keep the unsafe behavior assertion and a compatible pre-repair reproduction. A missing
new method, collection error, or irrelevant gate refusal is not a valid red control.
For D4 on post-#74 source, inspect the fake broker counter even if the stricter downstream
FSM raises: refusing a state transition **after** a broker call does not prevent that call.

## Required extensions and non-vacuous controls

| ID | Injection / arrangement | Required observations |
|---|---|---|
| C01 | Child exits before initial session-open commit | No broker or authority-producing dispatch entered; no open/admission grant published. Observation-only work under a sticky global hold cannot release it. Next boot validates actual prior protocol/genesis rather than inventing completion. |
| C02 | Exit immediately after session-open commit; before any fault | Unclosed session causes restart hold even if no fault is known. This is intentional conservatism, not a synthetic historical blackout event. |
| C03 | Exit after denial admission, before any transition write or fallback HALT | Restart hold survives without relying on a finally/exception handler or fallback file. |
| C04 | Exit after transition entry flush, HWM update, before transaction commit | Transaction rolls back; unresolved session/transition authority denies. No partial signed event is treated as committed. |
| C05 | Real commit succeeds, acknowledgement or memory publication fails | Reconcile exact identity from valid ledger; never allocate/re-sign a duplicate. A committed denial remains exhausted. Recovery uncertainty remains held until positively reconciled under the full recovery contract. |
| C06 | Fail mode write; fail recovery event; exit between those commits and publication | No observer sees `ok` prematurely. A committed mode downgrade alone is conservative. Mode must never increase. |
| C07 | Exception and BaseException paths; kill child without Python cleanup | All denial results survive. Trace shows crash checkpoint reached; an unrelated crash is not passing evidence. |
| C08 | Inject unavailable/read-only/full/busy SQLite and failed fallback latch | Zero broker or authority-producing health admission when required persistence cannot commit; observation-only continuity remains permitted under hold. Named failure visible, including after storage recovery. Test logical storage failure separately from physical disk durability. |
| C09 | First and repair-round timeout/limit/empty/error envelope, Claude and Codex | Every raw health mutation reaches one centralized transition, even when workflow result is quarantined; no duplicate generation allocation. No real model process. |
| C10 | Repeat trip/recovery with same identity; conflicting same identity; new trip overtakes recovery | Identical duplicate is idempotent; conflict refuses; new admission supersedes recovery before `ok` publication. Record exact committed generations, event counts and pending state. |
| C11 | Same and different vendor concurrency | Correct behavior for `claude`, `codex`, `sec:edgar`, `market_data`, `paper_performance`; one vendor's recovery cannot clear another or global hold. |
| C12 | Legacy non-empty store; unknown session version; mismatched open/close; second process owner | Startup hold and no order admission. No legacy backfill or retroactive clean seal. Concurrent attempts cannot both publish ownership. |
| C13 | Clean shutdown while job/submit/persistence remains in flight | Deny new admission, bounded quiescence; cannot sign clean close until every prerequisite is proven. Failure leaves open session. |
| C14 | Healthy genesis; healthy signed clean close/restart; committed exhausted close/restart | Genuine fresh path can work with fake leaves. Healthy restart retains counters; exhausted restart stays exhausted. Missing required fresh feeds still refuse independently. |
| C15 | Fault while status reads or database is unavailable | Committed and effective views are coherent snapshots; pending/failed/startup reason visible or status explicitly unavailable. Never return all-clear on a failed read. |
| C16 | Restore vendor generation 0; other owner completes trip/recovery at 2 before ownership acquisition | Exact-head conflict/reconstruction or same-transaction refusal; never accept the new session predecessor while keeping generation 0. |
| C17 | Recovery at generation g+1 commits; delay memory publication; admit newer trip | Denial wins effective view; new signed trip allocates above durable g+1; no contradictory equal-generation event. Restart restores latest committed history plus any unresolved hold. |
| C18 | Actual lifecycle with configured blocking stream/bot and queued scheduler/HTTP work | Dispatch fence prevents new work; bounded stop/join accounts for every producer. Unproven shutdown leaves marker open. A finite no-stream-only control cannot attest configured stream shutdown. |
| B01 | Pause after fresh risk; after pending exposure; after real intent | Admit denial with explicit event/barrier at each point; resume submit; stage/reason proves intended guard; zero broker entry. |
| B02 | Hold transition persistence waiting for `st.lock` | Independent denial admission must complete. Adding `st.lock` to the transition alone cannot make this test pass by postponing the observation. |
| B03 | Admitted trip, then signed recovery; submit original runtime generation | Refuse exact-generation mismatch even if effective vendor state is now `ok`. |
| B04 | Worker started but admission intentionally paused | Scheduling characterization only; cannot be counted as a denial-wins test. |
| B05 | Admit denial after broker-entry authorization / actual fake transport entry | Original entry is accounted honestly; next request refuses. Never fabricate a pre-entry abort for a call already made. |
| B06 | Tamper/read failure/required-anchor failure at final snapshot | No broker call. Retain exact invalid/unreadable evidence and sticky integrity hold. No new witness requirement is invented for modes where it was previously absent. |
| B07 | Healthy independent append around final verification | A consistent snapshot verifies; normal writer progress does not reproduce BUG-104 false failure. |
| B08 | Keyless SQL commit after final snapshot | Explicit residual characterization. Do not include as an expected-green zero-call assertion in this narrow D4 claim or relabel its outcome atomic PASS. Stronger exclusion requires new scoped design. |
| A01 | Refusal before/after token consume and after intent | Assert token registry state, approval rows, order state, actual broker calls, intent/result counts and exact `intent_seq`, not just returned status. |
| A02 | Replay same interaction/nonce after final refusal | No new token/second fake call. Recovery cannot grant a free retry. |
| A03 | Valid audit safety abort; invalid audit; result write failure; duplicate completion | Exactly one covering result when safely writable; uncommitted or unknown completion stays unresolved/uncertain and global denial persists. No healing or fake broker response. |
| A04 | Error after actual broker entry | Existing ambiguous broker-error/client-order-id behavior and #74 signed FSM checks remain intact. |
| A05 | Abort result commits, acknowledgement fails; or refusal-log write fails after result commit | Preserve the committed covering result; verify/reconcile exact intent identity, never delete or append a duplicate result. Denial remains; no broker retry. |

All crash children use `sys.executable`, inherited fake transports, isolated databases,
explicit paths, bounded waits and a completion/entry trace outside the database under test.
The parent clears ambient `COCKPIT_*`, avoids real dotenv, installs null/in-memory keyring,
and uses short Windows temp paths. No service or shared production database is used.
Process-kill evidence does not attest machine power loss, directory fsync, or administrator
rollback. Test POSIX/Windows termination behavior through the actual four DEC-09 cells.

## Barrier sketch (pseudocode only)

```text
submit worker:
    perform the real approval, gateway prechecks, token consume and intent commit
    signal(intent_committed)
    wait(allow_final_authorization, bounded)
    execute the real final verification and authorization

test coordinator:
    wait(intent_committed, bounded)
    launch transition worker

transition worker:
    invoke public safety transition API
    observer at completed admission signals(denial_admitted)
    persistence may now wait for submit's st.lock

test coordinator:
    wait(denial_admitted, bounded)       # never substitute sleep or is_alive()
    assert effective_denial == true
    signal(allow_final_authorization)
    join submit, then transition, bounded; collect every worker exception
    assert fake_broker.entry_count == 0
    assert refusal stage/reason == intended safety guard
    assert real intent has one safe abort, or remains unresolved under sticky denial
```

For the recovery variant, run two distinct arrangements: recovery still pending at the
authorization barrier, and a fully committed recovery before a fresh submit starts. Never
require a worker to finish while the test itself prevents it acquiring the held lock.
Every timeout is a failed/invalid test run requiring inspection, not proof of refusal.

## Existing controls that must remain

- `tests/orchestrator/test_core.py` watch-only/malformed restore and mode-recovery tests;
  `tests/orchestrator/test_rw20_restart_pressure.py` restart/freshness pressure.
- `tests/workflows/test_agent_calls.py` provenance, hardened runtime, generation and repair
  round; `tests/agent_runner/test_runner.py`, limit-boundary and Codex-authority tests.
- `tests/system/test_rw01_market_day.py::test_composed_restart_restores_watch_only_generation`;
  add session lifecycle coverage through `main.compose`/production lifecycle with fake leaves.
- `tests/brokers/test_gateway_hardening.py::test_out_of_band_halt_during_intent_window_refuses_and_completes_pair`
  and `test_intent_window_positive_control_no_halt_submits_and_pairs`.
- Original malformed generation/state (five cases), invalid-ledger restart, sticky transient
  read sentinel, and mutable-UsageWindow controls. Preserve legacy signed generation gaps.
- SEC-only trip/recovery leaves model generation unchanged; real `sec:edgar` key, one fake
  429 request, no manufactured OS HALT. Clearing HALT likewise is not new model analysis.
- `tests/storage/test_audit.py`, `test_sec11_seq_monotonic.py`,
  `test_sec11_hwm_signing_refusal.py`, plus merged #74 FSM/adversarial/pending-exposure tests.
- Invariant single-order-path, no-LLM-in-risk, secret and decision-runtime gates.

## Output required from each future run

Source commit/tree, relevant fixture hashes, interpreter/SQLite/SQLAlchemy versions, exact
argv and cwd, isolation settings (names only; no secrets), start/finish UTC, exit and timeout
state, raw test output and worker traces. Failure observations include committed event
sequence/hash, memory committed state, effective pending state, startup hold, nonce state,
intent/result IDs, final check outcome and fake broker entry count. Freeze original red
results and distinguish diagnostic, platform, real-market and independent-witness evidence.

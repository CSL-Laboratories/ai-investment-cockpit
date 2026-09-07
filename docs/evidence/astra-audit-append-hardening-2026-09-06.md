# Astra audit append hardening — 2026-09-06

**RESULT: real-miss-found**

Post-BUG-114 database atomicity held in the exercised interruption cases: no new split
between a committed `ModeState`, its audit entry, and the signed high-water was found.
The distinct miss is above allocation: **a stale detached `Order` can be persisted with
a signed transition that disagrees with its durable fill fields or prior signed state.**
Failed commits can also leave that detached object ahead of the database. These are
demonstrated persistence-contract failures, not a demonstrated broker submission.

**Next claim:** one bounded Order FSM freshness/rollback package, covering findings F1/F2
below. Strengthen the existing FSM and storage tests; preserve BUG-114's allocation order
and BUG-118's result guard. Allocate/check the ID in `docs/REGISTER.md` before production
implementation. No repair package, commit, or PR was created by this research.

Research was conducted September 7 UTC / September 6 PDT, within the requested period
ending approximately 20:15 PDT. All new fault injection used disposable unit fixtures.

**Source and incident boundary**

- Initial local HEAD: `892e2dbaab0093e91857cea373dc188d638d8d80`. A concurrent external
  change moved HEAD to `049518d202cb61079403a7e8cb32cba23e37d51b` by 01:16:49Z.
  A later external documentation commit, `20978607ed9efad7d62c4a1cb2ae201f7ca8b113`,
  also captured this task's fixture directory while work was ongoing. This task performed
  no Git mutation and did not undo that external commit. The inspected application tree remained
  `5125e7edd2489c027364bbbfbd4556d928109817`, byte-identical to the user-supplied
  `c5ef0ad` pin; the tools tree also matched. File hashes and interpreter versions are
  retained in [source-provenance.json](../../data/astra-audit-append-hardening-2026-09-06/source-provenance.json);
  [final source verification](../../data/astra-audit-append-hardening-2026-09-06/final-source-provenance.json)
  checks every recorded application hash again.
- Read the project operating instructions, canonical invariants, docs index, relevant
  PLAN scope/acceptance rules, REGISTER, SEC-11 contract, and prior concurrency/restart
  evidence. BUG-114 is merged in #68; BUG-118 is also already repaired in this source.
- The frozen tree's `BUG114-INCIDENT.md` confirms its historical pin and preservation
  boundary. The [historical incident report](sol-pipeline-audit-2026-09-03.md) records
  sequences 13588 and 13589 sharing the earlier parent. This task read that evidence
  and file names; **it did not open the incident database, obtain its key, re-verify its
  HMACs, rewrite history, or start the incident tree.**
- The live soak was not accessed. Its `c5ef0ad` pin is supplied context, not a serving-PID
  or ledger-health attestation by this task. No Discord, live broker/model invocation,
  production credential lookup, configuration change, real selection approval, or soak action
  was performed. Other concurrent evidence files were left alone.

**What each boundary guarantees**

Here, the signed high-water is `audit_meta.head_seq` plus its signature; the head entry
also has its HMAC and parent link. An external anchor is a separate, potentially older
commitment. Those are different authorities: a healthy HMAC chain does not establish that
every signed business transition used fresh application state.

| Path | Finding / remaining limit |
|---|---|
| [`AuditLog._insert`, `append`](../../app/storage/audit.py) | HWM-before-head ordering retained. Entry flush, watermark replacement, and commit remain in one transaction. A one-time operational error retries to exactly one retained fixture entry; exhausted retries leave the prior database unchanged. Missing/invalid HWM at preflight refuses. Late HWM damage has a distinct reread/refusal miss documented in the separate HWM report below. No additional structural fork was produced here. |
| `append_in`, `record_state_change` | Caller-owned transaction supplies atomicity. State-first and audit-first interrupted fixtures both roll back the row, entry and HWM. `record_state_change` does not reconcile the supplied detached object's version with current signed domain history: F1. It expunges the supplied object before commit: F2. |
| [`ModeLadder._persist`, `set_mode`](../../app/orchestrator/mode_ladder.py) | New mode rows use `record_state_change`. Twelve interruption cases and two invalid-HWM cases refuse without persisting the requested paper transition. After an actual successful commit, an acknowledgement exception leaves both records committed; a same-target retry is idempotent. |
| `intent`, `result` | BUG-118 pins the duplicate guard and allocation together. Five interrupted-result cases preserve the unresolved intent, prior head and HWM, with no orphan result. Existing independent-writer duplicate-resolution tests pass. |
| `verify_session`, `verify_chain`, `verified_events`, anchor verification | Ledger/authority reads use the explicit SQLite snapshot. Separate readers see only the prior committed row/head/HWM during an open write. A stale read transaction cannot upgrade after a competing commit. Verification inside a caller's write transaction can nevertheless publish an uncommitted process floor: F3. |
| `verify_completeness`, `completeness_report` | Covering IDs are model-scoped, including batch `row_ids`; these are coverage diagnostics, not complete signed-field or lifecycle reconciliation. `completeness_report` composes several reads despite its “one snapshot” wording. Do not use its `ok`/coverage alone to certify F1/F2 or a concurrent global snapshot. No new completeness race fixture was run. |
| [`persist_events`](../../app/workflows/events.py) | Batch domain rows and audit binding use the same transaction. Raw publication precedes SQLite; abandoned raw bytes are a different boundary from authoritative domain state. Existing verified-raw/batch tests pass. |
| [`PollBaselineStore`](../../app/filings/edgar/submissions_poll.py), [`_VerifiedPollBaselineStore`, filing hook, attribution, extraction status, completion writes](../../app/ingest/edgar_pipeline.py) | Inspected append-in call sites retain their caller's transaction. The production verified baseline also pins its authority read and checks its expected cutoff. Existing timestamp/append-failure rollback tests pass. Repeated verification within a multi-CIK completion transaction exposes F3. Legacy optional-audit helper modes are not evidence of production wiring. |
| [Merger evidence persistence](../../app/strategies/merger_edge.py), [`record_selection_commitment`](../../app/strategies/merger_semantics.py) | Final authority rechecks, domain insert and append share the authority transaction. A witness receipt can exist if the later local transaction refuses; that receipt alone is not local selection authority. Existing offline strategy tests pass; no real selection or witness was created. |
| [`MarketDataService`](../../app/ingest/market_data.py) | Raw file precedes audit binding, which precedes publication of fresh in-memory data. Pre-existing cache reads require an unambiguous verified binding. An interrupted publication may leave unused raw data; existing unsigned-cache refusal tests pass. |
| [`post_head_anchor`](../../app/bridge/anchor.py) | External publication and the later local `anchor` event are separate writes. A successfully retained anchor need not have its own local publication event after interruption; it still refers to a previously committed ledger point. The separate verify-then-`head()` read should be tightened to the returned verified tuple. No normal-writer uncommitted head was demonstrated. External witness activation remains open. |
| [`OrderGateway.submit`](../../app/brokers/gateway.py) | Intent, external effect, result, approval consumption and lifecycle transitions are deliberately separate boundaries. A remote effect cannot be rolled back by SQLite. The existing [RW-15 handoff report](astra-selection-to-rw15-runbook-2026-09-06.md) already describes those uncertainty windows; this research did not exercise a broker. |

The separate [HWM refusal report](astra-hwm-resign-claim-shape-2026-09-06.md), completed
during this review, proves that `_bump_hwm` can replace missing/invalid authority after
preflight on an initially unpinned append. Its explicit final-reread refusal/rollback
claim is distinct from #68 and from F1/F2 here. This task did not rerun that report's
late-damage fixtures or add their counts to its own results. Do not interpret the healthy
writer controls here as clearing that miss, or combine the separately bounded packages.

The watch-only memory-before-audit and final-submit findings are already documented in
[the durability report](astra-watchonly-durability-2026-09-06.md). They are relevant application
state/audit mismatches, but are not newly claimed here. Ordinary event-only appends,
notification receipts, refusal records, and conservative budget reservations should not be
mistaken for a promise of a database transaction covering their external side effects.

**F1 — stale domain state can receive a valid signature**

[`transition`](../../app/brokers/order_state_machine.py) validates `order.state` on the
caller's detached object, builds the signed payload from that object, then calls
`record_state_change`. The latter attaches/flushed it without a current-row/version check.
The actual column update can retain another transaction's fields that this detached
object did not mark dirty, while the audit payload still describes their old values.

The existing FSM fixture supplies the following deterministic overlapping-reader cases.
They use ordinary state-transition calls, no transport, no hand-written audit entries,
and no history deletion or re-signing:

| Required refusal | Observed on unchanged application |
|---|---|
| A second reader retained `approved`; another transition committed `cancelled`. The stale reader's next transition must refuse and leave both durable records unchanged. | `submitted` persisted; one extra signed entry; chain verified. The latest signed `from` does not continue the preceding terminal transition. |
| A second reader retained `submitted` with zero fills; another transition committed three filled units. The stale transition must refuse and preserve the newer state. | Durable fill quantity remained **3**, latest signed fill quantity was **0**, and one extra event committed. HWM/HMAC/parent verification still passed. |

This meets the requested mismatch criterion at the application-state binding layer.
It does **not** reproduce BUG-114: the ledger sequence and parent links remain correct.
It also does not reproduce BUG-118's duplicate result.

Reachability is narrower than a live trading claim. The production gateway and
`PaperOrderReconciler._apply` hold the shared `OrchestratorState.lock`; the reconciler
reloads its row under that lock. These protect cooperating callers in one state container.
The primitive itself has no corresponding database freshness check for independent
containers/processes or a retained stale caller. The fixture proves that primitive's
contract failure; no duplicate live or fake broker submission was attempted.

**F2 — pre-commit expunge leaves caller state beyond rolled-back history**

`transition` mutates the supplied object first. At
[`record_state_change`](../../app/storage/audit.py), both domain and audit objects are
expunged before the context manager commits. A failure at that commit boundary correctly
rolls back SQLite, but the detached object remains `approved` while its durable row and
latest signed state remain `proposed`.

Two assertions fail: the caller object is neither restored nor invalidated; a subsequent
transition on that retained object can persist `submitted` with a signed `from=approved`
even though the approved transition never committed. The chain verifies, because HMAC
verification checks what was signed, not this missing lifecycle predecessor.

The failure fixture targets the writing session after its watermark write, not the
mode oracle's earlier read-only transaction. It proves a pre-commit rollback boundary,
not a failure of SQLite atomic commit. Production service retries generally reload rows;
no automatic gateway continuation after such an exception was established here.

**F3 — an uncommitted verification floor can survive rollback (conservative refusal)**

The real `_record_poll_completions` loop verifies a baseline before each CIK's append.
Starting with two committed baseline events, its second verification sees the first
uncommitted completion at sequence 3 and raises `_observed_head` to 3. Interrupting the
second append rolls back the whole completion transaction. All database rows and signed
watermark are exactly restored to head 2, but the shared verifier subsequently reports:

> erasure/rollback: head seq 2 below previously observed head 3

A fresh test verifier validates the untouched committed head 2. That comparison is
diagnostic evidence, **not an instruction to reset a production verifier or restart to
suppress a refusal**. F3 is an availability defect that remains closed, distinct from F1/F2's
acceptance of stale state. The concurrent
[HWM refusal report](astra-hwm-resign-claim-shape-2026-09-06.md)
also cover this completion/floor boundary; coordinate ownership instead of opening a
duplicate repair claim.

**Tests to strengthen in the existing suite**

The retained [research fixtures](astra-audit-append-hardening-2026-09-06/test_append_contracts.py)
reuse the existing storage `env`, FSM `st`, and `_new_order` helpers. They belong under
evidence until a repair package promotes them into their existing owning modules.

| Existing test/module to extend | Required assertions |
|---|---|
| `tests/brokers/test_order_state_machine.py::test_transition_out_of_terminal_raises` | Add a second detached reader and a committed competing transition. Stale source refuses; every domain field, audit ID/hash/sequence and both HWM rows remain unchanged. |
| `test_each_transition_writes_a_covering_audit_entry`, `test_transition_persists_broker_fields` | Assert full relevant signed-field agreement and signed `from` continuity against freshly loaded durable state, including a concurrent partial fill. A row-ID match and `verify_chain().ok` alone are insufficient. |
| `tests/storage/test_audit.py` state-change coverage tests / FSM persistence tests | Interrupt after state flush, entry flush, HWM deletion, unsigned HWM insert, signed HWM insert, and before actual commit. Test ordinary exceptions and `BaseException` unwinding. Require propagated refusal, complete rollback, and restored or unusable detached objects. |
| Existing BUG-114 and RW-20 concurrency tests | Retain both allocation-read schedules; strengthen winner/loser checks to cover domain rows and exact HWM/signature, not only chain validity. Never retry an `append_in` fragment independently of its caller's domain transaction. |
| `test_result_invariants`, duplicate-resolution tests | Interrupt each result persistence boundary. Original intent remains unresolved, no result/orphan persists, and head/HWM remain unchanged. Competing duplicate refusal remains required. |
| `tests/ingest/test_edgar_pipeline.py::test_verified_baseline_timestamp_mutations_roll_back_when_audit_append_fails` | Extend to a multi-CIK completion batch whose second append fails. No partial completions or domain changes persist; the shared committed floor must also remain correct. Retain the existing true-erasure and concurrent-snapshot tests. |
| Mode persistence tests | Keep the post-commit acknowledgement-loss control: after actual COMMIT, both records must remain durable and a same-target retry must be a no-op. An exception after commit cannot truthfully require rollback of that committed history. |

F1/F2's bounded repair should validate the current signed row/version and transition in
the **same transaction** that performs the conditional update and append, before flushing
the caller's dirty state. Check fill-progress changes as well as the state string. Publish
the committed result to callers only after commit, restoring or invalidating failed objects.
Do not blindly retry stale input, add an alternate order path, or relax the gateway gates.
An order-path repair still requires adversarial review and the four DEC-09 cells.

F3 needs its own coordinated verification-floor treatment: only committed observations
may become the irreversible process floor; a transaction's own tentative prefix must not
be globally published before commit. Preserve genuine erasure refusal. Lowering the floor,
ignoring a mismatch, or rewriting ledger/HWM is not an acceptable fix.

**Validation and limits**

| Run | Result | Retained evidence |
|---|---|---|
| Storage, ModeLadder, FSM, EDGAR baseline | 110 passed | [baseline log](../../data/astra-audit-append-hardening-2026-09-06/baseline.log), [command](../../data/astra-audit-append-hardening-2026-09-06/baseline.json) |
| Anchor/witness adapters, event/raw bindings, market data, reconciliation, strategies | 246 passed | [boundary controls](../../data/astra-audit-append-hardening-2026-09-06/boundary-controls.log), [command](../../data/astra-audit-append-hardening-2026-09-06/boundary-controls.json) |
| Final defensive research contracts | 5 assertions fail / 26 controls pass: two F1, two F2, one F3 | [reviewed contracts](../../data/astra-audit-append-hardening-2026-09-06/contracts-reviewed.log), [command](../../data/astra-audit-append-hardening-2026-09-06/contracts-reviewed.json) |
| Ruff, application and research helper directory | Clean | [lint](../../data/astra-audit-append-hardening-2026-09-06/lint-reviewed.log) |

The red assertions remain failures, with no xfail/skip or production change. Initial and
intermediate logs are retained. One intermediate post-commit characterization failed
because its hook interrupted the preceding read-only commit, before any mode write. That
was a fixture error, not a sixth product finding. The corrected commit hooks require an
actual audit write in their session; the before-commit mode cases were strengthened the
same way. Initial Ruff findings were imported-fixture naming issues and were corrected.

Runtime: Linux Python 3.13.5, SQLite 3.46.1, SQLAlchemy 2.0.52. This is a local diagnostic;
native Windows and Ubuntu/WSL2 × Python 3.11/3.12 were not run by this task. The new
interruption checks model exception unwinding, not power loss, killed processes, faulty
storage hardware, or all multiprocess schedules. No full application-suite claim is made.

The [launcher](astra-audit-append-hardening-2026-09-06/run_checks.py) clears ambient
`COCKPIT_*`, uses an absent dotenv and null/in-memory keyring, and writes new evidence log
names exclusively. Re-run from the repo root with a new label:

```bash
.venv/bin/python docs/evidence/astra-audit-append-hardening-2026-09-06/run_checks.py review-rerun -m pytest -q ../docs/evidence/astra-audit-append-hardening-2026-09-06/test_append_contracts.py -p no:cacheprovider --tb=short
```

The [manifest](../../data/astra-audit-append-hardening-2026-09-06/evidence-manifest.json)
binds the report, final fixtures, provenance and logs. It is local reproducibility evidence,
not an independent witness. Preserve the frozen incident and current soak as they are;
these findings authorize neither history repair nor an operational mode change.

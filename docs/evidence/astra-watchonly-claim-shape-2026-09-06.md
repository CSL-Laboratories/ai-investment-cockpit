---
title: Watch-only durability D1–D4 — queued claim shape
type: evidence
status: reference
last-verified: 2026-09-07
---

# Watch-only durability D1–D4 — claim shape, September 6 PT

**RESULT: real-miss-found; research complete; production claim NOT opened.** This is the
single fail-closed package queued behind **BUG-125 / PR #74**. During this session, shared
local history advanced to #74's merge `ce62cf2`. D1–D4 remain open: isolated replays on
`a57e8cf` and `ce62cf2` both report **8 failed / 9 passed**. On the latter, D4 fails in the
new FSM check after broker entry, rather than reaching its original safety assertion.
This document specifies the next repair; it does not certify it or #74's platform gates.

The live soak remains the operator-pinned **`c5ef0ad` on port `8787`**. No service, soak
file, selection input, date parser, watchlist, model boundary, Git ref, or production claim
was changed **by this task**. This task made no PR, commit, external message, live feed call,
or real broker call. Shared-workspace commits made by another actor are recorded below.

## Evidence and source boundary

| Item | Exact basis and limitation |
|---|---|
| Original diagnosis | [Durability report](/workspace/w24-evidence/astra-drain-2026-09-06/astra-watchonly-durability-2026-09-06.md), original `test_durability_diagnosis.py`, and `data-watchonly/` logs under the same external root. All **18** original manifest entries were checked after resolving their relocated paths; size and SHA-256 match. [Verification record](astra-watchonly-claim-shape-2026-09-06/source-and-evidence-verification.json). |
| Original app | Report used remote `d0a58367ab4c7f9cdb1a3c8d1db98d0b90d3e97f`, local `892e2dbaab0093e91857cea373dc188d638d8d80`, app tree `5125e7edd2489c027364bbbfbd4556d928109817`. Historical baseline: **148 passed**; diagnosis: **8 failed / 9 passed**. |
| Initial workspace | Docs freeze branch `work/astra-drain-freeze-2026-09-06`, HEAD `d7b7087fbf5329baa230f90451eb2cb89053b5da`; its committed app tree equals the original app. This initial checkout must not be described as main at `a57e8cf`. |
| Requested main lineage | `a57e8cf20a81b0802a117a403316f6992f159ab1`, app tree `1118bd363ee0b1aedf44a7e68bbc7241b1623f3e`. Compared with the original app, only `storage/audit.py` and the added HWM-signing-refusal tests differ: merged BUG-124 / #73. |
| Fresh replay | A `git archive` of `a57e8cf:app` in `/tmp`, original fixture copied byte-for-byte (SHA-256 `da52eb32ff6c876750ded3afcd5b4d36cd18ad9a9e8bef62e4744ffc8ce7e8fa`), project `.venv`, temporary SQLite, frozen clock, FakeAlpaca/FakeTransport, isolated configuration and keyring. [Raw log](astra-watchonly-claim-shape-2026-09-06/a57e8cf-durability-replay.log), [argv/result](astra-watchonly-claim-shape-2026-09-06/a57e8cf-durability-replay.json). Exit **1**, **8 failed / 9 passed**, pytest 5.03 s. No failure was made xfail or recast as PASS. |
| Bounded baseline on `a57e8cf` | Original 148-case selection plus #73 HWM tests: **173 passed**, exit 0, pytest 15.28 s. [Log](astra-watchonly-claim-shape-2026-09-06/a57e8cf-baseline.log), [argv/result](astra-watchonly-claim-shape-2026-09-06/a57e8cf-baseline.json). Linux Python 3.13 diagnostic; no full-suite or four-cell acceptance claim. |
| Dependency #74, updated locally | Local merge `ce62cf2b0567899019a446e693e95512fe96a3cf`, app tree `2045e2c74010a49a8c9a67b4a5ef50f55b0b644a`, now in workspace history. Separate F1/F2 worktree remains `2167006`. [Retained BUG-125 evidence](/workspace/w24-evidence/astra-drain-2026-09-06/bug125-order-fsm-freshness-2026-09-07.md) predates that merge and explicitly lacks full-suite/four-cell acceptance; no new attestation was found. Its old “open” text is historical, not current merge status. |
| Post-#74 replay | Exact `ce62cf2` archived application and byte-identical original fixture: **8 failed / 9 passed**, exit 1, pytest 5.02 s. [Log](astra-watchonly-claim-shape-2026-09-06/ce62cf2-durability-replay.log), [argv/result](astra-watchonly-claim-shape-2026-09-06/ce62cf2-durability-replay.json). D1–D3 remain red; D4 raises `AuditIntegrityError` at the post-broker FSM transition, so its original counter assertion is not reached. This is not a prevention PASS. |
| Supplemental post-#74 D4 observation | [New research fixture](astra-watchonly-claim-shape-2026-09-06/test_ce62cf2_d4_observation.py) catches only the late integrity exception to observe side effects, retaining the zero-submit assertion. [Log](astra-watchonly-claim-shape-2026-09-06/ce62cf2-d4-observation.log), [metadata](astra-watchonly-claim-shape-2026-09-06/ce62cf2-d4-observation.json): **1 failed**, `broker_entries=1`, `fake_submissions=1`, audit invalid at broker entry, outcome `AuditIntegrityError`. The original fixture remains untouched. |
| Shared-workspace handoff | Another actor advanced the checkout through #74 and #72, committed a partial draft at `183f72e`, then local history recorded docs PR #75 merge `ea13241` and evidence amendment `222626f`. The application tree still equals `ce62cf2`. This research task continued only docs/evidence work and did not create those commits/branches or any PR. |

These fake orders isolate the named guard through `PaperExecutionService → OrderGateway`.
They do not establish a real W-24 candidate, complete composed selection authority, live
execution, a physical power-loss guarantee, or SEC-11 witness coverage. The SEC-recovery
passing case intentionally permits a synthetic order; it is a scope characterization,
not a successful refusal. See the [fixture plan](astra-watchonly-claim-shape-2026-09-06/fixture-contracts.md)
for ownership and stronger non-vacuous acceptance probes.

## Proposed one-package intake

Proposed title: **Refuse interrupted watch-only authority and late safety changes before
paper broker entry**. Allocate one unused linked BUG ID **only after #74 has merged and
its accepted source is verified**. Leave BUG-113's historical #59 merge intact; do not
claim BUG-125 again. This file neither reserves an ID nor marks a register row claimed.

1. **Operator change:** pending/failed safety persistence visibly blocks approval and
   submission in the current process and after an uncertain restart. Recovery grants
   permission only after commit. A denial admitted before broker-entry authorization wins.
2. **Existing path extended:** the usage facade, breaker transitions, signed restoration,
   actual composition, paper approval, and the single gateway path listed below.
3. **Retirement in this package:** remove production raw `VendorWindow` mutation and the
   runner/workflow double allocation; replace memory-only duplicate guards. Keep usage
   accounting session-local. Do not turn mutable `UsageWindow` SQL rows into authority.
4. **Reachability:** extend `test_composed_restart_restores_watch_only_generation` and
   production scheduler/agent-call fixtures with fake leaves. Removing session admission,
   transition wiring, or the final gateway check must make its own regression fail.
5. **Claim ownership:** a new BUG linked to residual D1–D4, sequentially behind #74. At claim
   time re-read PLAN/REGISTER, confirm no competing repair, and record actual merged main.
6. **Platforms:** common SQLite/signed-event and thread synchronization semantics; bounded
   child-process interruption tests use the project interpreter and temporary paths on
   Windows and Ubuntu/WSL2. No OS-specific credential branch or filesystem durability claim.
7. **Size boundary:** one coordinator using the existing audit seam, plus callers, status,
   and tests. Cap the design at roughly **1,000 production LOC** under PLAN §5. This is a
   scope cap, not a measured estimate. If lifecycle/recovery or writer exclusion exceeds it,
   split/replan before implementation; do not silently omit a D1–D4 acceptance obligation.

## Exact refusal contracts

Terms used below are proposed protocol terms, not existing fields. **Committed** means a
verified signed event whose transaction completed. **Admitted denial** is a synchronous
publication that orders are barred, before waiting for persistence. **Pending/failed**
never carries order permission. **Broker-entry authorization** is the single serialized
decision to enter the private broker transport, after intent and final checks. A process
startup hold is separate from a vendor's signed analysis-generation counter.

### D1 — interrupted degradation remains denied

The original four D1 failures cover direct watch append, AgentJob persistence, watch
persistence after the runner trip, and a repeated failed EDGAR poll that skips persistence.

1. Every production health fault, including first and repair-round timeout, limit, empty
   response and degraded envelope, admits a **global order denial before audit/AgentJob
   I/O**. No later exception or caller omission may undo it. The runner uses an injected
   transition facade/callback; it gains no broker capability or orchestrator credentials.
2. Serialize generation allocation from **durable** vendor history, including a committed
   recovery not yet published in memory. A newer trip after such a recovery must allocate
   above its generation, not reuse that generation with a contradictory exhausted state.
   Pending reservations and retries must have one serial order. One observation has one
   transition identity and one candidate generation; retries reuse it. Sign vendor, refs,
   target state, generation and transition identity coherently. Publish committed state
   only after commit. A second caller may coalesce the same open episode; it may not infer
   durable completion merely from an exhausted memory window.
3. Failed or ambiguous persistence retains denial and a visible failure/pending reason.
   A retry reconciles the same identity against verified authority: a proven committed
   **denial** can resolve persistence uncertainty while remaining exhausted; a proven
   uncommitted denial may be retried through the same protocol. Unknown outcome stays held.
   Never retry integrity errors by recreating missing HWM or authority.
4. An unrelated successful append, healthy feed sample, ordinary recovery call, mutable
   SQL `ok/0`, or elapsed time does not clear persistence uncertainty. Suppressed-repeat
   guards in every real caller must consult this protocol, not only `is_watch_only()`.
5. Approval and gateway review/submit refuse pending/failed/global startup integrity
   authority. A proposal from an earlier runtime generation remains stale after a signed
   trip/recovery. Neither future generations nor bool/string/negative/missing stamps pass.

**Chosen bounded restart shape: a signed process safety-session envelope.** Before a
production graph admits orders or health work capable of granting authority, commit a versioned
`session_open` event binding a unique session/process ID to verified predecessor session
authority. No open-marker commit means no order admission or authority-producing dispatch.
Existing observation/ingest/status work may continue while a global hold is enforced;
its successful output cannot release that hold or produce order permission. This is an
admission restriction on existing paths, not a second execution path. A clean
`session_close` is allowed only after closing admission, quiescing/joining producers and
in-flight submissions, verifying the chain and committed vendor view, and proving there
are no pending/failed transitions or unresolved submit intents. Any inability to prove
those conditions leaves the session open. Never close from an unconditional `finally`.
Production shutdown must fence HTTP/Discord/scheduler dispatch, retain producer handles,
and use bounded stop/join checks. Existing `IngestRunner.stop()` cannot interrupt an
in-flight consumer, and current `run()` discards the scheduler handle and starts daemon
workers. A finite fake-only close test cannot prove those workers quiesce. If a configured
stream/bot cannot be proven stopped, leave the marker open and disclose that its normal
restart remains held; do not quietly call that a clean shutdown.

Restore the session protocol and vendor events from one verified snapshot, using the
existing anchor policy. Missing/malformed/unknown protocol, unmatched or contradictory
events, concurrent ownership, and an earlier unclosed session mean **startup hold**.
An open session followed by an unrelated valid event is still unclosed. Opening a second
session cannot discharge it. Verified predecessor/session/vendor restoration **and** new
session ownership must use one suitable transaction, or bind an exact verified audit head
and reconstruct all authority on a head change. Restoring generation 0, then accepting a
new predecessor that completed recovery at generation 2, must never leave generation 0
active. A verify-then-independent-append race may not authorize two process owners.

Genuine empty-store initialization is distinct from a non-empty legacy ledger lacking
session history. The latter starts held; do not fabricate a historical clean-close event.
A normal restart from a provably closed session may restore its exact vendor counters,
including exhausted states and historical generation gaps, subject to existing fresh
feed requirements. This is **not** a new global proposal-freshness epoch.

**Deliberate availability cost:** crash/legacy/integrity startup holds have no automatic
release in this bounded repair. Current production has no Claude/Codex health-reset caller.
Do not invent one, infer health from a feed poll, clear a hold by operator ACK, or rewrite
history. A supported incident/legacy recovery procedure would need separate explicit scope
and evidence. The future PR must state this rollout hold plainly; a successful clean-session
and fresh-genesis control is still required, so universal unconditional refusal cannot
pass. A fallback HALT file may add defense but is not restart authority: a crash can precede
its write. Same-host session events do not solve complete valid-snapshot rollback.

### D2 — recovery commits before permission is published

1. Keep effective exhausted/pending denial throughout recovery. Verify the expected
   predecessor state/generation and recovery identity under the transition ordering.
2. Persist any required non-increasing mode recovery first; commit signed vendor recovery
   second; publish committed `ok` and its next generation **last**. Two transactions are
   acceptable here: a mode downgrade without vendor recovery remains conservative.
3. A mode-write failure, vendor-write failure, uncertain commit outcome, or admitted newer
   trip prevents permission publication. A mode oracle falling back to `read_only` is not
   proof of valid recovery authority. Reconcile acknowledged/ambiguous commits explicitly;
   do not reallocate a second generation or release a startup/persistence integrity hold.
4. A newer admitted denial supersedes an older recovery candidate, including while that
   candidate waits for a lock. Duplicate completion is idempotent; contradictory equal-
   generation state is an integrity refusal. Do not renumber old double-increment history.
5. Existing health predicates retain their strength: trustworthy EDGAR completion for
   `sec:edgar`, existing fresh-market checks for `market_data`, verified reconstruction for
   `paper_performance`. Successful recovery of one vendor cannot clear another vendor or
   global hold. Recovery never increases autonomy: auto falls to at most `review_live`;
   `paper` and `read_only` stay at their current rung.

### D3 — an admitted denial wins before broker-entry authorization

1. Pending/global watch-only or changed producing-runtime generation observed after the
   initial precheck and **by final authorization** must veto entry. Check again after
   capability consumption, pending
   exposure and **the real committed order intent**, immediately before authorization.
2. Serialize denial admission and final authorization with a short admission lock/version
   check. Publish pending denial before the transition waits for `st.lock` or SQLite. A
   worker merely started or waiting for `st.lock` has not necessarily admitted a denial.
3. Define one order: denial admitted first → zero broker calls; authorization first → the
   already-authorized request may enter and its outcome must be recorded honestly, while
   subsequent requests see the denial. Keep authorization adjacent to the private call;
   no audit I/O, risk work, callback, sleep, or user-controlled computation belongs between
   that decision and entry. Do not claim retroactive cancellation of an entered request.
4. Document lock order. A transition releases the admission lock before waiting for
   `st.lock`; the gateway must not deadlock with a transition retaining it. Never hold the
   short admission lock for broker network duration. Existing outer submit locking must
   not prevent a pending denial from being admitted.
5. Recovery can clear a vendor denial only under D2. It cannot make an old signed runtime
   generation valid again. Retain the risk → mode → approval → gateway path and all existing
   tuple, runtime, capability, pending-exposure, freshness and HALT checks.

### D4 — invalid final authority is a veto, even when logging the veto fails

After the real intent commit, acquire a **new** full-chain-verified, snapshot-pinned audit
read with the existing required-anchor policy. A pre-intent/cached snapshot is insufficient.
Invalid signatures, chain/HWM inconsistency, required-anchor failure, or unreadable authority
means no broker entry and a sticky integrity denial. Recheck the admitted-denial state and
exact runtime generation after this potentially blocking read, at D3 authorization. Probe
the existing HALT sentinel at the final boundary as well; unreadable still counts as set.

**Precisely bounded guarantee:** this closes the reproduced corruption committed after
intent and before the final snapshot, plus integrity denials admitted before authorization.
It does not make a verification read atomic with arbitrary database mutation. A keyless
writer committing after that final snapshot remains an explicit residual. Do not label this
package “D4 atomic against concurrent SQL writers.” A stronger claim requires compatible
write exclusion spanning final verification and authorization, independent-connection race
tests, and measured contention; that extension is out of this package. Neither `st.lock`,
`AuditLog._lock`, nor a WAL read transaction supplies that exclusion. Do not wrap verification
in the non-reentrant audit lock. Raw file replacement and administrative rollback still
require the separate SEC-11 trust boundary.

### Stable refusal and accounting surface

Proposed reason identifiers below are fixed internal tokens, not interpolated model/feed
text. Preserve existing public refusal codes where applicable; test stage and reason, not
only a generic exception or an unrelated edge rejection.

| Condition | Required surface / accounting |
|---|---|
| Pending/failed transition or startup hold | Approval refuses before mint (`watch_only` proposed service code); gateway stage `mode`, reason ID `watch_only_pending`, `watch_only_persistence_failed`, or `watch_only_startup_hold`. Existing active watch-only remains stage `mode`. |
| Stale/malformed/vendor-mismatched generation | Preserve `stale_analysis` / `analysis_freshness` service/gateway semantics and exact equality. Signed-lineage corruption keeps `lineage_unverified`. |
| Final audit/anchor failure | Gateway stage `audit_integrity` (proposed); stable `final_audit_unverified` reason; sticky global hold. A later green read alone never clears it. |
| Refusal before token consumption | No broker call or new submit intent; do not burn a token solely to test refusal. |
| Refusal after token consumption | Nonce remains consumed; no automatic remint, free retry, fabricated submitted state or fill. Fresh approval is required after valid recovery and fresh applicable lineage. |
| Final refusal after real intent, valid writable audit | Exactly one covering `order_submit` abort result bound to the actual `intent_seq`, plus refusal. Use safety-specific abort reason (`watch_only`, `analysis_freshness`, `audit_integrity`), distinct from `broker_error`. |
| Invalid/unreadable ledger or abort/result write failure | Zero broker calls takes priority; keep sticky denial and the actual durable state. Uncommitted/unknown completion remains unresolved/uncertain until verified same-intent reconciliation. A committed result with lost acknowledgement, or a later refusal-log failure, must not be deleted or duplicated. No healing, manufactured pairing PASS, re-sign, or automatic submit retry. |
| Broker already entered | Preserve existing ambiguous broker-error/reconciliation semantics and stable client order ID. Never mislabel it a definite pre-entry safety abort. |

Status must show **committed** vendor state/generation separately from effective denial,
pending transition identity/phase, persistence failure, and session/startup hold. Existing
`watch_only` must include these holds. Keep `audit_chain.ok` independent: a valid old ledger
plus an uncommitted transition may correctly show `audit_chain.ok=true, watch_only=true`.
If status cannot read the database, it must not emit an all-clear; retain a visible denial
or an explicit unavailable response. No new status field is authority by itself.

## Files to touch in the future production package

Paths refer to the repository root. Optional files require a concrete necessity; this is
not permission for adjacent refactoring.

| File / owning seam | Bounded change |
|---|---|
| `app/agent_runner/usage.py` | Separate committed snapshots from admitted/pending/failed denial; centralized identities/generation allocation; preserve session-local request counts. |
| `app/orchestrator/breakers.py` | Trip/recovery commit-publication protocol, idempotency and supersession; shared effective-denial query. |
| `app/agent_runner/runner.py` | Route **all** first/repair-round health mutations through the injected production facade; retain hardened Codex argv and runtime boundary unchanged. |
| `app/workflows/agent_calls.py` | Remove double trip; safety denial must not depend on successful AgentJob persistence. Retain pre-call runtime-generation binding and schema validation. |
| `app/orchestrator/state.py` | Session constants/coordinator, verified restore and startup hold; define lock order and authority snapshots. |
| `app/main.py` | Session admission before health/order work; quiescent clean-close lifecycle. Compose the real mechanism, not a test-only substitute. |
| `app/orchestrator/ingest_runner.py`, `app/orchestrator/scheduler.py` | Idempotent durable transition calls for real EDGAR/market paths, including repeated failures. |
| `app/brokers/paper_execution.py` | Early global-hold refusal before mint; performance transitions through same seam; preserve signed lineage/fresh risk. |
| `app/strategies/merger_edge.py` | Only the existing stale-EDGAR watch-only notification call site; no parser, selection, date, source or edge change. |
| `app/brokers/gateway.py` | Same notification correction, final authority snapshot/admission decision, conservative abort accounting. |
| `app/orchestrator/app.py` | Truthful status for committed/pending/failed/session state; no new recovery endpoint. |
| `app/orchestrator/mode_ladder.py` (only if needed) | Minimal sequencing helper for D2; preserve current non-increasing recovery policy. |
| `app/storage/audit.py` (only if needed) | Narrow caller-owned session/verification transaction helper using existing primitives; preserve #68 allocation, #70 result snapshot, #73 signing refusal and merged #74 rollback behavior. |
| Existing owning tests + a focused durability/system fixture | Detailed [fixture map](astra-watchonly-claim-shape-2026-09-06/fixture-contracts.md); fake leaves and actual wiring. |
| `docs/PLAN.md`, `docs/REGISTER.md`, new repair evidence | Only on future claim: one ID, seven intake answers, baseline/results and limitations. Correct stale BUG-113 “claimed” prose to its recorded historical merge without overwriting that evidence. |

`app/risk/`, broker adapters, capability mint rules, W-24 parsers, configured universe,
and the soak worktrees are outside the implementation touch set. #74's FSM is a dependency,
not a second repair target; adapt fixtures to its verified detached-object rules rather
than bypassing those checks.

## Monitor PASS criteria for the future repair

The freeze INDEX says **“Monitor PASS bar unchanged (#62/#68)”**; the retained material does
not supply a separate complete Monitor policy. The following explicit repair checklist is
derived from PLAN's package loop and these faults. It does not replace the independent
selection, soak, witness or platform bars.

- **Base and ownership:** #74 actually merged; fresh merged main SHA/tree pinned; no parallel
  claim; one linked BUG and reviewed intake. #74's historical 142 targeted passes are not
  evidence that its pending matrix or timed-out full suite passed.
- **Red → green, without masking:** all eight original failed safety assertions are owned
  by promoted tests and pass after the fix. Run compatible failure proofs against the base;
  an unsupported new API/collection error is not a red safety proof. Each new gate has a
  removal-sensitive composed test and a valid positive control. Do not accept generic
  W-24/quote/mode refusal as proof that the intended durability guard fired.
- **Crash and recovery:** real child-process interruption at the specified boundaries,
  including before fallback persistence; unreadable/full/unavailable storage; ambiguous
  commit outcomes; legacy/unclean session hold; clean-session restart; idempotent repeated
  fault/recovery; trip supersession. Assert surviving **and** restarted authority/status.
- **Final boundary:** deterministic admitted-denial barriers around risk/pending/intent;
  final-snapshot tamper/read/anchor failures; zero fake broker calls when refusal wins;
  after-entry ordering control; burned nonce and exact abort/unresolved-intent accounting.
  D4 PASS must carry the final-snapshot limitation, never an unrestricted atomicity claim.
- **Preserved refusals:** signed-blackout restoration; malformed/contradictory authority;
  chain-sequence gaps; transient-read sticky sentinel; mutable `UsageWindow` non-authority; old/future/
  missing lineage stamps; HALT; runtime-mode pin; risk/approval/capability/pending exposure.
  Preserve #68/#70/#73 and #74 regressions. Healthy concurrent append is a required control.
- **Adversarial review:** fresh independent order-path attack→fix→regression round against
  the unmerged candidate. One reproduced fail-open within these declared refusal contracts
  blocks PASS. Any excluded residual must remain recorded with its impact and may not be
  reported closed. Review the startup rollout hold and the exact D4 residual explicitly;
  neither can disappear from the PR description.
- **Acceptance evidence:** targeted tests, full app suite and Ruff; native Windows and
  Ubuntu/WSL2, each Python **3.11 and 3.12**, on the operator workstation per DEC-09. Record
  source/dependency versions, commands, exits, skips and artifacts for all four cells.
  Linux 3.13 diagnostics, missing cells, a timeout, or hosted-only evidence do not satisfy
  this gate. Do not waive an unrelated conservative baseline failure as a PASS.
- **Honest final state:** no selection/date/watchlist changes, no soak repin/restart, no new
  model or broker authority, no manufactured pairing/full-suite/matrix/selection PASS.
  Close the future BUG only after merge; this research package leaves no claimed row.

The [prior ops PASS table](/workspace/w24-evidence/cockpit-handrun-ops-checklist-2026-09-02.md:59)
still requires a natural absolute `{8-K,10-Q,10-K}` head, source-set/eligibility checks and
`current_matches_set_end`, **both expected and outside dates**, and a selection-ready cohort
of **at least 30** before considering selection/watchlist action. Otherwise freeze docs and
wait. No synthetic fixture can satisfy it. The user's `c5ef0ad:8787` pin supersedes that
older checklist's soak pin. An unauthenticated 401 is an auth/reachability observation,
not a health or selection certificate.

## Explicitly outside this claim

Global/dependency-vector proposal epochs; making SEC recovery or HALT clear invalidate every
runtime's analysis; automatic vendor/reset recovery; a legacy/crash incident-unlock flow;
universal SQL-writer atomicity; physical power-loss durability; complete-snapshot rollback
or SEC-11 closure; W-25 provisioning/provider spend; HWM/erasure-floor lifecycle redesign;
reopening #59/#68/#70/#73 or repeating #74; broker/approval redesign; grammar/date loosening;
prospective-population/window changes; watchlist seed; selection/edge activation; model
runtime expansion; service management; deployment; soak mutation; commit/push/PR creation.

The proposed repair is intended to restore the specified **refusal** guarantees. It does not create missing prospective evidence,
turn a surviving valid ledger into proof of transition completeness, or grant paper/live
capital authority.

## Delivery boundary

Requested destination: `/workspace/w24-evidence/astra-drain-2026-09-06/`. This session can
write only `/workspace/ai-investment-cockpit` and `/tmp`; the external destination is
readable but outside the writable roots, with approval unavailable. The report, fixture
plan, replay evidence and INDEX note are therefore staged under repo `docs/evidence/`.
See [HANDOFF](astra-watchonly-claim-shape-2026-09-06/HANDOFF.md) for the exact copy operation.
This is an output-location limitation, not a pending production claim or permission flow.

## Late amend

- `ce62cf2-durability-replay.json` / `.log` — post-#74 durability replay on merged main tip

The shared-workspace amendment above is incorporated into the source table, including the
supplemental D4 counter observation. Independent review of this **draft contract** corrected
session restore/ownership snapshot races, committed-but-unpublished recovery generations,
shutdown reachability, observation continuity during startup holds, and abort-result
acknowledgement-loss accounting. Those reviews are research input, not the future production
branch's required adversarial attestation.

Research frozen at **2026-09-07 02:52 UTC / September 6 19:52 PDT**, before the requested
**20:15 PDT / 03:15 UTC** stop. No production row was claimed by this task. Final checks
and file hashes are retained in [final-verification.json](astra-watchonly-claim-shape-2026-09-06/final-verification.json)
and [PACKAGE-MANIFEST.json](astra-watchonly-claim-shape-2026-09-06/PACKAGE-MANIFEST.json).

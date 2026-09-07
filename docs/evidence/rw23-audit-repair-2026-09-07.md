---
title: RW-23 post-merge audit repair and reliability follow-up
type: evidence
status: implemented-pending-final-matrix
last-verified: 2026-09-07
---

# Scope and intake

The operator authorized documenting all 11 audited defects, repairing and committing them
in slices, followed by a further bug and measured reliability/performance review. Baseline:
merged main `c231dc249eb74461f5504845cf6f89ffe57ef209`, application tree
`267799acdccec6e5dbf97f730a17121c1e7a1120`. The baseline four-cell RW-22 evidence verifier
passes and covers that exact application tree. New fixes require new validation.

1. **Operator change:** denied merger terms refuse; health faults survive restart; sells cannot
   exceed owned shares; all persisted model prose is scrubbed; news raw bodies remain replayable;
   persistence failures are visible and safely retryable; gate/budget counts use correct evidence;
   simulations honor exchange and notification chronology.
2. **Existing paths:** `strategies/semantic_text.py`, `agent_runner/runner.py`,
   `workflows/agent_calls.py`, `workflows/proposals.py`, `risk/engine.py`,
   `ingest/normalize.py`, `workflows/events.py`, `orchestrator/ingest_runner.py`,
   `ingest/market_data.py`, `orchestrator/gate.py`, `workflows/budget.py`,
   `quant/execution/autoloop.py` and `quant/execution/sim_venue.py`.
3. **Retirement:** replace defective branches in place; no second order path, model runtime,
   broker, capture service or trust root. Preserve old raw records honestly as legacy derivatives.
4. **Reachability:** regressions exercise signed-source edge estimation, `call_agent` plus
   restart, paper fresh risk/gateway sell authority, proposal persistence, consumer→runner→raw
   read-back, bar refresh retry, approval→submission gate metrics, transactional budget spend,
   and complete `AutoLoop.run` callback/event sequences. Independent adversarial tests supplement
   the risk implementer's tests before acceptance.
5. **IDs:** RW-23 and BUG-146–156, mapped in REGISTER to the frozen audit's findings 1–11.
   CLN-01 health-branch drift is handled only to the extent needed for BUG-147.
6. **Platforms/isolation:** implementation worktree `C:/t/cockpit-rw23`; primary checkout stays
   on clean main for existing automation. Tests use short temporary paths, cleared `COCKPIT_*`,
   absent env files, fake transports/credentials and isolated DB/raw/anchor state. Dedicated
   project virtual environments and archived source run the native Windows/Ubuntu WSL2 ×
   Python 3.11/3.12 matrix. No real broker/model/feed or deployment actions are included.
7. **Size:** target below roughly 1,000 changed production lines, with separate per-defect
   commit slices and focused tests. Split any larger redesign before implementing it. Further
   performance changes need a measured baseline and a separate bounded scope entry.

# Delivery log

- Claim and baseline report recorded before implementation. User authorizes local commits;
  publishing or merging is outside this request.
- `e3304bf` — BUG-149: scrub before the missing-size persistence branch; 27 focused
  tests passed, including initially failing field-by-field canary probes. Independent review passed.
- `9596136` — BUG-147 / CLN-01: shared health classification for initial and repair
  responses, with durable watch-only history; 54 focused tests passed, including restart/recovery.
- `fda5017` — BUG-146: markup boundaries preserve denial scope and cannot assemble a
  new assertion across hidden material; 179 semantic tests passed, including 42 new cases.
- `b4c0bed` — BUG-150: detached original payload in a separate raw column, hash-bound
  and replayable; legacy derivatives stay explicitly legacy. Independent review passed.
- `3b93be1` — BUG-153: count distinct latest operator decisions, excluding submission
  consumption. Review caught the actual consumption-kind spelling, malformed decisions,
  and superseded approvals; all were fixed. 88 combined raw/gate/workflow tests passed.
- `1072e9c` — BUG-154: verify, read limits, count, corroborate and append within one
  SQLite transaction. 87 scoped tests passed; independent review additionally injected
  commit failure through all retries and anchor/deletion races. No spend or observed-head
  advance survived a failed commit.
- `bdde8d7` — BUG-152: staged signing before publication, exact signed identity reuse
  after restart, and verified bytes at metric consumption. All 33 focused checks passed,
  including seven independently authored publication/substitution/concurrency probes.
- `adf5544` — BUG-155/156: paired chronology fix because wire actions and their reports
  need one consistent ordering contract. 434 broad quant tests passed; the final clock
  refusal and multiple-replacement controls passed in a 20-case focused run. Independent
  root review required receive/ingest ordering refusal; pending replacement exposure stays reserved.
- `b1e03e8` — BUG-151: 58 focused checks passed, including five failures demonstrated
  against the old module. Bounded identity-only gap tracking stays visible while workers
  run; unrelated news cannot clear it. Exact recovery verifies duplicate raw evidence.
  Connection and poll health remain honest. Gap tracking is process-local diagnostics,
  not a durable outbox or a claim of lossless stream replay.
- BUG-148: 153 focused tests passed after independent review, including 43 dedicated
  and separately authored adversarial cases. Sells use verified remaining shares, broker
  availability, local reservations, exact submit acknowledgement binding, and conservative
  handling of unknown broker outcomes. The grouped run passes; an earlier mixed-order
  targeted invocation had seven fixture-discovery errors, not assertion failures.
- Remaining slice reviews found additional necessary composed controls: available broker
  shares and unsubmitted local sales must add as disjoint reservations; local veto cannot
  prove an uncertain broker submit was cancelled; daily-bar signing must survive a crash
  before file publication and reject post-write substitution; simulation replacement
  acknowledgements must preserve pending exposure limits.

The original production-line estimate is being exceeded by these independent review
repairs. The defect list remains frozen to BUG-146–156; each reviewable domain still has
its own commit. Further performance work is deferred to a separate intake after these
repairs, rather than expanding this correctness package.

# Remaining gates

Independent authority review has passed. The final DEC-09 matrix is pending for the
combined RW-23 and separately scoped RW-24 source.
Existing independent witness, cohort, prospective observations, paper-day and pre-live gates
remain unchanged. A locally committed repair is not a merged/closed register item.

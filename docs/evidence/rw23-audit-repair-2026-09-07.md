---
title: RW-23 post-merge audit repair and reliability follow-up
type: evidence
status: in-progress
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
- Implementation, adversarial review and validation entries follow as slices complete.

# Remaining gates

The DEC-09 matrix and independent authority review are pending for the changed source.
Existing independent witness, cohort, prospective observations, paper-day and pre-live gates
remain unchanged. A locally committed repair is not a merged/closed register item.

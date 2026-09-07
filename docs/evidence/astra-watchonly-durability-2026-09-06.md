# Astra watch-only durability audit — 2026-09-06

**RESULT: FAIL — committed-transition restoration works, but interrupted persistence and the final submit boundary still have fail-open gaps.**

**BUG-113 on main: merged/closed via [PR #59](https://github.com/NotSkittlz/ai-investment-cockpit/pull/59), not open or claimed.** The register’s literal status is “merged.” This audit recommends a scoped reopening or linked follow-up for the residuals below; it does not change the register or claim a repair package.

Research executed September 7 UTC, September 6 America/Los_Angeles, before the requested September 6 20:15 PDT deadline. Audience: Cockpit Admin.

**Source and evidence boundary**

- Remote main observed as `d0a58367ab4c7f9cdb1a3c8d1db98d0b90d3e97f`, merged [PR #71](https://github.com/NotSkittlz/ai-investment-cockpit/pull/71). Local HEAD is its PR head, `892e2dbaab0093e91857cea373dc188d638d8d80`, on `work/astra-fresh-install-smokes-2026-09-06`. PR #71 changes only the earlier smoke report. Application tree: `5125e7edd2489c027364bbbfbd4556d928109817`.
- Seven load-bearing files were fetched at the pinned remote SHA through the read-only GitHub connector; their Git blob hashes exactly match the local files. [Source provenance](../../data/astra-watchonly-durability-2026-09-06/source-provenance.json) records the hashes and merge metadata. Local `origin/main` is older, `c5ef0ad`; fetch was blocked by read-only Git metadata, so it was not treated as current remote authority.
- PR #59 merged at `2026-08-31T19:13:00Z`, merge `1e2d4e3ca7c2fcae4e320ce5f72b44905b036ee4`. [REGISTER.md](../REGISTER.md), line 150, already records this. [PLAN.md](../PLAN.md), lines 194–238, still says “claimed” / “stays claimed until merge”; that historical wording is stale. No parallel claim was made.
- Read the root operating instructions, canonical invariants, docs index, PLAN, REGISTER, relevant security contracts, and the [previous restart review](rw20-adversarial-review-2026-09-06.md).
- Only this report, its research fixtures, and new evidence logs were written. No application source, existing tests, soak tree, incident ledger, configuration, credential store, service, or Git ref was changed. No Discord messages, model processes, live SEC calls, or real broker requests were made.
- The synthetic broker fixture uses the real `PaperExecutionService → OrderGateway` path, temporary SQLite, a frozen clock, and FakeAlpaca. Its quote/risk inputs are synthetic and it deliberately has no composed W-24 authority. A reproduced fake submission proves the named guard can fail; it does **not** prove an executable real candidate, a production order, or bypass of all independent W-21/W-24 gates. Restart probes rebuild the state container and gateway over the temporary database; they are not workstation power-loss tests.

**What “generation” actually means**

| Layer | State and authority | Restart / freshness consequence |
|---|---|---|
| [UsageTracker](../../app/agent_runner/usage.py), lines 15–82 | Mutable per-process `VendorWindow(vendor, used, generation, state)`. `record` increments only `used`; `trip` changes state and increments generation. Unknown vendor defaults to `ok/0`. | `used` is intentionally session-local. The tracker is not itself durable, transactional, or synchronized. |
| [UsageWindow SQL model](../../app/schemas/models/usage_window.py) | Schema exists, including accounting fields and generation. No production reader/writer of this model was found; only its schema test uses it. | It is not the restart authority or a second ledger. The new mutable-row control confirms that an `ok/0` SQL row cannot override signed `exhausted/1`. |
| [Watch-only restore](../../app/orchestrator/state.py), lines 58–106 | `OrchestratorState.build` normally verifies the full audit chain and available anchor, then replays signed `watch_only` events by sequence in that same read snapshot. Reconciles vendor refs and checks generation/state types and monotonicity. | Restores committed per-vendor state/generation. Invalid, malformed or unreadable authority installs the global `__watch_only_integrity__` exhausted sentinel. `main.compose` uses this restoration; explicit injected `usage=` remains a test seam. |
| [Agent call](../../app/workflows/agent_calls.py), lines 79–143 | Captures the producing runtime’s generation **before** running the model; stores it in `AgentJob.input_refs["_generation"]` and the signed `agent_job` payload. | This is one runtime’s counter, not a global feed epoch or elapsed-time freshness check. |
| [Paper lineage](../../app/brokers/paper_execution.py), lines 205–327 and 457–460 | Verifies signed Proposal → AgentJob → RiskDecision → Order lineage, original body/tuple bindings, runtime and generation; requires exact equality before approval mint. | Missing legacy bindings, future/malformed generations, stale generations and mutable job swaps refuse. Re-draft legacy proposals; do not backfill authority. |
| [Gateway](../../app/brokers/gateway.py), lines 449–482, 700–749 | Global watch-only blocks orders. Review and submit both compare the supplied, lineage-derived generation against the current in-memory runtime generation. Production paper composition requires this check. | A change before submit’s precheck is caught. A change later in submit is not necessarily caught: see D3. |
| [Feed epoch](../../app/strategies/merger_edge.py), lines 827–865 | EDGAR authority also requires a signed completed poll from the current `process_epoch`, bounded age, and current filing-set coverage. Market-data composition has its own fresh-poll guard. | These protect feed/edge authority independently. They do not make the AgentJob generation global, or fix a lost watch-only transition. |
| [Status](../../app/orchestrator/app.py), lines 479–546 | Reports memory windows, `usage_integrity_error`, global `watch_only`, and `audit_chain` separately. | A green chain proves integrity of what was committed. It does not prove all memory transitions were committed. `watch_only=false` alone is not a health certificate. |

The normal direct breaker sequence is `ok/0 → exhausted/1 → ok/2`. A real agent limit takes a different sequence: [run_agent](../../app/agent_runner/runner.py), lines 375–415, trips memory first, then `call_agent` records the failed job and calls `enter_watch_only`, which trips again. A fully completed agent-limit transition therefore commonly signs generation **2**, not 1. “Exactly once” in the agent-call tests refers to the watch-only **event**, not one counter increment. Gaps are accepted by restore; future generations on proposals are not.

Recovery does not increase autonomy. [ModeLadder.recover](../../app/orchestrator/mode_ladder.py), lines 346–364, drops an auto rung to `review_live`, but leaves `paper` or `read_only` there. Recovery’s docstring must not be read as an unconditional move out of paper.

**Answer by trigger**

| Trigger / time boundary | Observed result |
|---|---|
| Successfully signed Claude/Codex blackout, then restart | **Refuses correctly.** State/generation restore; generation-0 proposals remain stale, including after signed recovery. Existing composed and RW-20 regressions pass. |
| SEC 429 with completed EDGAR watch-only append, then restart | **Refuses correctly while the feed remains watch-only.** The real key is `sec:edgar`; its generation restores. Claude/Codex generation does not change. |
| Actual HALT file present across restart | **Refuses correctly.** Existing RW-20 control covers the persisted file; gateway also probes it immediately before the broker call. |
| Invalid audit at startup / before proposal loading | **Refuses correctly.** Global sentinel on restore; `lineage_unverified` on proposal loading; no healing. A transient startup read failure also leaves the sentinel active even when a later read succeeds. |
| Watch-only append or agent-job persistence fails, then restart | **FAIL, D1.** Memory blackout can disappear; the still-valid old ledger restores generation 0; an old synthetic proposal submits. |
| Recovery changes memory but its watch-only append fails | **FAIL, D2.** Memory says `ok/2`, durable ledger says `exhausted/1`; orders can proceed in the surviving process. |
| Blackout/generation changes after submit precheck, before broker call | **FAIL, D3.** A completed transition can be ignored by the final boundary. |
| Audit becomes invalid after the last full verification, before broker call | **FAIL, D4.** The final HALT probe does not repeat audit verification; the synthetic order submits. |
| SEC recovers, or HALT is cleared, with unchanged model generation | **Not a universal stale-proposal refusal.** Those controls alone do not invalidate earlier model analysis. Separate source, market, mode, approval and edge gates still apply. |

SEC’s `SecFairAccessHalt` is a **fetch-run stop**, not the OS HALT sentinel. [SecFetcher.get](../../app/filings/edgar/fetch.py), lines 497–500, alerts and raises on 403/429 without hot retry. The pipeline stops the remaining fan-out and withholds successful freshness grants; [IngestRunner](../../app/orchestrator/ingest_runner.py), lines 217–243 and 373–390, enters `sec:edgar` watch-only. Neither operation shown here calls `set_halt`.

The new 429 fixture sends one request to a **fake** transport through `SecFetcher`, lets the runner handle its exception, and rebuilds state. It observes `sec:edgar=exhausted/1`, `claude=ok/0`, no HALT file, and `gateway_mode` refusal. Calling the same recovery method used after a trustworthy poll yields `sec:edgar=ok/2`; the original Claude generation-0 proposal then submits in the synthetic fixture. This is a scope characterization, not evidence of a fresh real poll or W-24 selection. The older RW-20 test manually sets a HALT file and trips the artificial key `"sec"`; its claim is correspondingly narrower.

**D1 — high paper: an uncommitted blackout is forgotten on restart**

At [breakers.py:273](../../app/orchestrator/breakers.py), memory changes before `audit.append`. An append failure leaves memory exhausted but the signed history unchanged. Restart cannot infer an event that was never committed.

The runner makes the vulnerable interval longer: its memory trip occurs before both the AgentJob transaction and the separate watch-only append. Injecting failure at either persistence point reproduces the loss. The failed-job event, if present, is not used by watch-only restoration to reconstruct the missing transition.

Observed in three fixtures: before restart, Claude is exhausted at generation 1 or 2; after restart, Claude is `ok/0`, `audit_ok=true`, no integrity sentinel, and the original generation-0 proposal produces **one FakeAlpaca submission**. No malformed chain is required.

The EDGAR variant also proves that later failures do not automatically repair this omission. `_sync_edgar_watch_only` sees the already-exhausted memory window and skips another append. After a second failed poll and restart, no signed watch-only event exists and the tracker is empty/healthy. Similar “already watch-only” guards exist in market-data and paper-performance handling; those additional paths were inspected, not separately fault-injected here.

**D2 — high paper: recovery publishes permission before durability**

At [breakers.py:317](../../app/orchestrator/breakers.py), `state="ok"` and the next generation are published **before** mode recovery and the signed recovery append. An exception does not restore the exhausted state.

The fixture signs EDGAR `exhausted/1`, fails only the recovery append, then returns the audit writer to normal. Memory is `ok/2`, while the still-valid ledger’s latest event is `exhausted/1`. A newly seeded synthetic Claude proposal submits in that surviving state container. A fresh container correctly restores `exhausted/1` and closes again.

This is not a recommendation to restart production to conceal the failure. It demonstrates inconsistent authority depending on which process view is consulted.

**D3 — high paper: watch-only can change inside the final submit interval**

Submit holds `st.lock`, but `enter_watch_only` and `UsageTracker.trip` do not take it. The last generation/watch-only checks occur in `_precheck`. Token consumption, pending-exposure work, and the order-submit intent write follow. The late checks at [gateway.py:796](../../app/brokers/gateway.py) and line 823 probe only HALT.

A deterministic fixture starts a separate thread immediately after the real `order_submit` intent is committed. That thread completes and signs Claude `exhausted/1` before the original submit resumes. At the recorded broker boundary, `watch_only=true`, generation is 1, and the audit verifies. Nevertheless, one generation-0 synthetic order submits.

The recovery variant is also revealing: the thread changes memory to `ok/2` and then blocks acquiring `st.lock` inside mode recovery. The broker boundary therefore sees memory generation 2 while the durable event still says exhausted/1. The old order submits; recovery’s signed event completes only after the submit lock is released. The fixture does **not** claim both recovery events had committed before that broker call.

**D4 — high paper hardening: audit failure after the last verification is not a final veto**

The gateway’s pending-exposure check verifies the full chain before the intent append. In a second intent hook, a synthetic keyless writer changes an existing signed row after that append. Explicit verification at this exact point returns false. The original submit then continues past its final HALT probe and sends one FakeAlpaca order.

This is a new final-boundary observation, not a reproduction of BUG-114’s repaired audit allocation race. It requires concurrent database tampering/corruption at the tested interval. No invalid production ledger was opened or repaired. Before entry and after restart, the same invalid authority correctly refuses.

**Recommended fail-closed repair package and fixtures**

These are proposed repairs, not implemented changes or a claimed package. Keep the model boundary and single order path intact.

| Repair | Concrete behavior required | Fixtures / acceptance |
|---|---|---|
| Make blackout admission durable and restart-conservative | Centralize safety transitions; stop treating a mutated memory window as proof that its audit event exists. On audit failure, retain a global denial and an independently persisted failure indication where possible; never resume order authority merely because the next write succeeds. Cover an abrupt exit before that indication too: either durable pending work before external calls plus unresolved-at-boot refusal, or conservative startup refusal until safety state is re-established. | Promote the failed watch append, both runner persistence gaps, and suppressed EDGAR retry probes. Add process-termination injection between each step, unavailable/full SQLite, failure of the fallback latch, and restart after storage recovery. Require old proposals and unsatisfied vendor health to remain refused. |
| Publish recovery only after commit | Serialize trip/recovery and generation allocation with the order authority lock. Keep the exhausted/pending view until signed recovery and any required mode transition have committed. Failed recovery must never expose `ok`; retries must be idempotent and visibly pending. Avoid the runner’s independent double allocation. | Existing D2 probe; both mode-write and watch-only-write failures; concurrent trip/recovery ordering; duplicate recovery; all supported runtime/vendor keys. Preserve exact-generation comparison. |
| Close the final submit interval | Give safety transitions and submit a defined ordering. Immediately before broker entry, after intent creation, revalidate denial state, generation and verified audit authority under the agreed synchronization. An admitted earlier denial wins; a later transition must have a clearly tested ordering. On refusal, keep token/intent accounting conservative and do not submit. | D3’s two-thread probes and D4’s corruption hook, plus transitions during risk recheck and pending-exposure verification. Assert zero fake submissions whenever changed authority is observed before broker entry. If intent already exists, require an auditable abort when the ledger is writable; unreadable/invalid audit must not force a broker call. |
| Bind proposals to the intended freshness scope | If the contract is “any relevant blackout invalidates old analysis,” add a signed global safety epoch or signed dependency-generation vector covering runtime, EDGAR, market data and integrity state. Bind a process epoch too if restart must invalidate every earlier proposal, including one preceding a lost transition. Validate it at draft lineage, approval and final submit. Legacy missing stamps must refuse. | SEC-only trip/recovery; market-data-only trip/recovery; HALT clear without new analysis; restart with no signed blackout; corrupted audit followed by separately reviewed recovery; fresh re-draft positive control. These must isolate generation refusal from an unrelated W-24 or quote refusal. |
| Expose persistence status honestly | Status should distinguish committed generation from pending/failed transition and current integrity denial. Clarify that `UsageWindow` is unused accounting schema, and reconcile PLAN’s stale BUG-113 claim text. | Failed append must be visible even with `audit_chain.ok=true`; process restart must not silently relabel unknown health as recovered. No ledger rewriting or generation backfill. |

A fallback HALT file alone does not close a crash before it is written. The current `set_halt` uses `write_text`; this audit does not attest power-loss durability or filesystem flush semantics. Likewise, moving the audit append before the memory trip alone would create a window in which orders can continue while the append blocks. The repair needs a pending-denial state and a defined commit/publication order, not just swapped lines.

An additional final check alone also cannot prove atomicity against arbitrary out-of-process database rewrites. Document the writer/process trust boundary, coordinate supported writers, and retain the independent SEC-11 witness requirement. HMAC verifies retained history, not completeness of transitions never committed, and a complete old valid snapshot plus replayed high-water remains a known external-witness problem. Do not use same-host evidence or this audit to close SEC-11.

**Validation and retained artifacts**

| Run | Result | Evidence |
|---|---|---|
| Existing targeted regression baseline | **148 passed**, 13.92 s; runner provenance, breaker/restoration, paper lineage, audit, real composition restart, EDGAR runner/pipeline and SEC fetch controls | [baseline-bounded.log](../../data/astra-watchonly-durability-2026-09-06/baseline-bounded.log), [exact command](../../data/astra-watchonly-durability-2026-09-06/baseline-bounded.json) |
| Expanded research probes against unchanged app | **8 safety assertions failed / 9 controls or characterizations passed**, 4.77 s. Failures are retained, not marked xfail or converted to passes. | [fault-probes-expanded.log](../../data/astra-watchonly-durability-2026-09-06/fault-probes-expanded.log), [exact command](../../data/astra-watchonly-durability-2026-09-06/fault-probes-expanded.json) |
| Ruff, application plus research fixture directory | **PASS** | [lint.log](../../data/astra-watchonly-durability-2026-09-06/lint.log) |
| Platform acceptance | Linux Python **3.13.5 diagnostic only**; native Windows and Ubuntu/WSL2 × 3.11/3.12 not run here | No new DEC-09 attestation |

The eight failures comprise four D1 cases, one D2 case, two D3 cases and one D4 case. Nine passing cases cover invalid-audit restart, five malformed signed transitions, transient restoration-read failure, mutable UsageWindow non-authority, and the SEC recovery scope characterization. The latter deliberately observes a synthetic submission after recovery; it is not a safety-gate pass.

Retained preliminary records: the first baseline command named nonexistent `tests/agent_runner/test_usage.py` and collected nothing (exit 4). A corrected broader selection was interrupted at the ASGI route portion after 101 progress dots (tool exit 130); it supplies no completed suite verdict. The successful bounded selection above replaces it for this task’s baseline. The initial research run was 6 failed / 3 passed; the expanded run strengthens the recovery-boundary assertion and adds audit/malformed/read/SQL controls. Initial research-helper lint findings were corrected; no application code was changed.

Fixtures: [test_durability_diagnosis.py](astra-watchonly-durability-2026-09-06/test_durability_diagnosis.py). Launcher: [run_diagnostics.py](astra-watchonly-durability-2026-09-06/run_diagnostics.py). All database mutations, test-only signing material, HALT files and fake broker rows belong to fresh temporary fixtures. Imports use an absent dotenv and null/in-memory keyring. The launcher clears ambient `COCKPIT_*` before application imports and writes new log names exclusively.

To reproduce the research failures from the repo root, use a new log label:

```bash
.venv/bin/python docs/evidence/astra-watchonly-durability-2026-09-06/run_diagnostics.py review-rerun -m pytest -q -s ../docs/evidence/astra-watchonly-durability-2026-09-06/test_durability_diagnosis.py --tb=short -p no:cacheprovider
```

On Windows, use the project’s `.venv\Scripts\python.exe` and adapt the launcher’s interpreter path; its current path targets this Linux evidence host. Before accepting any production repair, promote the applicable probes into the owned application test package, obtain the required adversarial review, and run the four DEC-09 cells on the operator workstation.

The [evidence manifest](../../data/astra-watchonly-durability-2026-09-06/evidence-manifest.json) records SHA-256 hashes for this report, the final fixture/launcher source, provenance, command records and retained logs. It binds local artifacts; it is not an independent witness.

**Admin disposition**

Preserve BUG-113’s historical merged status; do not call PR #59 unmerged based on PLAN’s stale prose. Record these as new residual durability/final-boundary findings or explicitly reopen the bounded defect before implementation. No optional PR was created: this research found interacting persistence and concurrency defects that need a reviewed repair, and this host has not supplied its release matrix.

Current operational health and any serving process SHA were not queried. The safe interpretation is that committed blackout and corrupt-ledger restart controls work, while interrupted transitions and final-submit authority require further repair. No W-24 selection, cohort, witness, edge, paper-day completion, or live readiness is claimed.

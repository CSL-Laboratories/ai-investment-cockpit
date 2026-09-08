# Cockpit audit — 2026-09-07

GitHub synchronization is complete. Local `main` and `origin/main` both point to
`c231dc249eb74461f5504845cf6f89ffe57ef209`, the merge of PR #80. The prior local
`codex/audit-gates-2026-09-06` branch was preserved. No application, test, configuration,
or documentation files in the repository were changed or committed.

Four parallel reviews and isolated offline probes confirmed **11 findings: three P1 and
eight P2**. The next work should address the P1 defects before expanding functionality.
This is a bounded audit of the current code, emphasizing the latest fixes and their callers;
it is not an exhaustive security certification or acceptance of an empirical trading gate.

## Prioritized findings

The numbers below identify findings in this report only; they are not newly claimed
`BUG-*` or work-package IDs in the project register. All source line references refer to
the audited commit.

### 1. P1 — HTML layout can turn denied merger terms into verified authority

**Location:** `app/strategies/semantic_text.py:34,48,55–57`,
`app/strategies/merger_semantics.py:220–234`.

The displayed-text parser inserts literal periods for comments, line breaks and hidden
elements. The cash qualifier check then treats these as sentence endings. This source is
accepted: `It is not true that <!-- page marker --> each share of common stock listed on
Nasdaq will receive $120 in cash per share.` Removing the comment correctly refuses it.
`<br>` and a hidden span produce the same error.

The composed signed-source fixture produced `verified=True`, approximately 1,018.16 bps
expected gross return, and a valid audit chain from the denied payment. This proves false
semantic authority in the fixture; no real selection, broker order or live activation was made.
The defect concerns supported HTML handling, not the separately deferred general CSS grammar.

**Fix and regression:** Preserve structural separators without turning them into grammatical
sentence boundaries or joining incomplete authority fragments. Denial and conditional clauses
must remain effective across comments, line breaks and hidden islands. Exercise matching
positive controls and refusal through the full signed-source provider.

**Evidence:** [quant_repro.py](quant_repro.py), [quant_repro.log](quant_repro.log).

### 2. P1 — Some repair failures lose watch-only state on restart

**Location:** `app/agent_runner/runner.py:487–501`,
`app/workflows/agent_calls.py:139–145`.

An empty repair response or a vendor error envelope trips the runtime's usage state but
returns `NEEDS_HUMAN`. The caller persists watch-only only for `LIMIT_HIT` and `TIMEOUT`.
Both probes produced zero watch-only events: `degraded / generation 1` became
`ok / generation 0` after reconstructing the state, while the audit chain remained valid.
Previously generated analysis can regain a matching generation after that restart.

**Fix and regression:** Represent vendor health failures consistently on initial and repair
responses, and persist the transition independently of whether the output is quarantined.
Test repair-empty and repair-error cases through `call_agent` and state reconstruction,
including refusal of pre-fault analysis. This is remaining BUG-065/CLN-01 health-branch drift;
the repaired timeout path is not being reported as broken.

**Evidence:** [model_probe.py](model_probe.py),
[model_probe-results.json](model_probe-results.json), `empty` and `vendor_error` cases.

### 3. P1 — A sell can exceed the number of shares owned

**Location:** `app/workflows/proposals.py:112–115,259–264`,
`app/risk/engine.py:332–333`.

Risk construction discards holding quantity, and `sell_backed` compares dollar values.
A holding of 100 shares marked at $10 has market value $1,000. Selling 101 shares at a
fresh $9.90 quote is only $999.90, so the quantity overrun passes. The production
`PaperExecutionService._fresh_risk` probe returned `approved`, with W-21 and signed
performance checks required, no failed checks and no warnings. All sells also receive the
`CLOSE_LONG` classification even when the quantity would create a short position.

**Fix and regression:** Preserve reconciled share quantities and enforce sell quantity
against available owned shares, accounting for outstanding sells. Require an actual reduction
before using close-long edge treatment. Cover differing quote/position marks, partial fills,
and concurrent pending sells. Broker acceptance or filling of the excess share was not tested.

**Evidence:** [order-safety-probe.py](order-safety-probe.py),
[order-safety-probe-output.txt](order-safety-probe-output.txt), `SELL` case.

### 4. P2 — Missing sizing bypasses proposal scrubbing

**Location:** `app/workflows/proposals.py:393–408`.

A schema-valid proposal with both quantity and notional absent is persisted and returned
before the outbound scrubber runs. Synthetic account digits and a foreign-position statement
survived verbatim in the stored thesis, with `needs_human`, `flagged=false`, and zero scrub
events. No external disclosure was exercised.

**Fix and regression:** Run scrubbing before every persistence/return branch, including the
missing-size branch. Combine missing sizing with blocking text in each free-text field and
assert that neither database nor returned/displayed content retains the blocked text.

**Evidence:** [model_probe-results.json](model_probe-results.json), `sizeless_scrub`.

### 5. P2 — The ordinary news “raw” store loses the original input

**Location:** `app/ingest/normalize.py:109–125`,
`app/ingest/alpaca_news.py:46–55`, `app/workflows/events.py:146–153`.

Normalization hashes the original news object but returns only selected normalized fields.
The persistence bridge archives that normalized object. When a vendor supplies summary and
body, the body is discarded; when only a long body is supplied, the fallback summary is
truncated to 4,000 characters. URL and author metadata are also lost. Verified read-back and
the audit chain both pass despite the original input being unrecoverable.

The probe used a 4,536-character body with a trailing qualification. The qualification and
source URL were absent after read-back in both cases. This defeats faithful replay and fresh
interrogation under Invariant 8. The separate in-process SEC filing raw store is outside this
finding's scope.

**Fix and regression:** Persist the original source object separately from its sanitized
summary, bind its hash to the stored source, and construct model packets from the sanitized
derivative. Test exact recovery of long bodies and metadata without exposing raw markup as
trusted prompt instructions.

**Evidence:** [probe_ingest.py](probe_ingest.py), [probe_ingest.json](probe_ingest.json),
`summary_and_body` and `long_body_only`.

### 6. P2 — Stream persistence failures are invisible in operator health

**Location:** `app/ingest/alpaca_news.py:84–96`,
`app/orchestrator/ingest_runner.py:282–290,350–357`.

The consumer catches a failed persistence callback, releases its dedup key and increments
`emit_error`. The runner does not expose that consumer counter. Injecting a storage failure
for a valid frame yielded `status=ok`, zero visible errors and `last_error=null` while nothing
was persisted. A long-running stream can retain an alive worker throughout this condition.

**Fix and regression:** Surface failed writes immediately through stream status and operational
health, with a well-defined recovery condition. Retain failed frames or use a bounded,
verified replay mechanism. Releasing a dedup key only helps if redelivery occurs; the probe
does not claim permanent loss when redelivery succeeds.

**Evidence:** [probe_ingest.json](probe_ingest.json), `stream_persistence_failure`.

### 7. P2 — One audit-write failure strands daily-bar loading for the date

**Location:** `app/ingest/market_data.py:512–545`.

Daily bars are published to a write-once date/symbol pathname before their audit binding is
appended. If that append fails once, later attempts find an existing unsigned file and refuse
it. Three refresh attempts in the probe all failed, with just one vendor fetch and one audit
append attempt; metrics never became available. A new date uses a new pathname, so the
finding is bounded to the affected date/path.

**Fix and regression:** Make interrupted publication recoverable while preserving refusal of
untrusted pre-existing files—for example, stage each attempt under a distinct immutable
identity and select only an audited publication. Do not solve this by signing arbitrary
existing cache bytes. Test one-time append failure, safe retry, and an attacker-created file.

**Evidence:** [probe_ingest.json](probe_ingest.json), `daily_bars_audit_retry`.

### 8. P2 — Successful execution distorts autonomy-gate statistics

**Location:** `app/orchestrator/gate.py:164–169`; lifecycle producer at
`app/brokers/gateway.py:910–917`.

The gate counts every `EVENT_ADJUDICATION` in its denominator, including
`approval_consumed` events without an operator decision. One approval initially counted as
one adjudication and 100% approval. After a successful submission through the real gateway
to `FakeAlpaca`, it counted as two adjudications and 50% approval. Both chains verified.
This inflates the proposal population and prevents ordinary approved-and-submitted history
from satisfying the 80% approval criterion.

**Fix and regression:** Count distinct, authoritative operator adjudications according to an
explicit decision contract. Exclude lifecycle consumption records. Test the metric across
approval, submission, reconciliation and repeated decisions without lowering thresholds.

**Evidence:** [order-safety-probe-output.txt](order-safety-probe-output.txt), `GATE`.

### 9. P2 — A budget count can read different history than it verified

**Location:** `app/workflows/budget.py:135–140`.

The deep-analysis budget verifies the chain, then opens a different session to count spend.
Within the repository's keyless-database-writer threat model, deleting a consumption between
those steps hides it from the count. A real second-connection SQLite deletion made a cap of
one accept a second `try_consume`. A later chain check failed, but the additional budget
authorization had already been issued. This requires database tampering; it is not a claim
that an ordinary reader causes extra spending.

**Fix and regression:** Pin verified history, effective limit, consumption count and append to
one transaction with appropriate contention handling. Preserve external-anchor validation.
Test deletion/retagging interleavings and legitimate concurrent appends.

**Evidence:** [model_probe-results.json](model_probe-results.json), `budget_verify_read_race`.

### 10. P2 — Simulation fills liquidity canceled before order arrival

**Location:** `app/quant/execution/autoloop.py:138–151`.

The loop advances pending orders to a feed event's ingest time before applying that event's
earlier exchange mutation. In the probe, an ask is observed at time 100, the strategy's order
arrives at exchange time 150, and an exchange cancellation at 120 is ingested at 200. The
simulator fills at 150 against the canceled ask, then records a desynchronized cancellation.
It returns position 1 and `accounting_valid=True`.

**Fix and regression:** Merge exchange mutations and order arrivals in chronological order,
while retaining separate strategy-observation timing. Compare zero-latency controls and
delayed cancel-before-arrival/after-arrival cases. This affects simulation evidence, not live
order authority, and is distinct from documented replacement-sizing debt.

**Evidence:** [quant_repro.log](quant_repro.log), `chronology`.

### 11. P2 — Simulation delivers fill callbacks before their receive time

**Location:** `app/quant/execution/sim_venue.py:297–303`,
`app/quant/execution/autoloop.py:360–365`.

A fill with receive/ingest time 101 is immediately absorbed while processing feed event 2.
The resulting callback sequence is `book(1), fill(101), book(2)`. Strategy and position state
therefore know about a future fill while processing an earlier observation. This is separate
from finding 10: even correctly ordered exchange events need delayed report delivery.

**Fix and regression:** Queue fill reports until their delivery timestamps. Test monotonic
callback times, positions before report delivery, and strategy actions triggered by fills.

**Evidence:** [quant_repro.log](quant_repro.log), `early fill report`.

## Further improvements and work ordering

1. Repair findings 1–3 as bounded packages with adversarial review of each affected authority
   path. Promote the CLN-01 health-branch drift from cosmetic cleanup because it now has a
   reproduced durability consequence. Do not relax the paper/live or empirical gates.
2. Address news retention and persistence-health visibility before relying on additional
   unattended observations as complete evidence. Treat raw storage, its derivative summary,
   and delivery acknowledgment as explicit contracts.
3. Add an offline test lane with real `discord.py` installed. The recorded four-cell matrix
   intentionally omits it, leaving six command-tree tests uncollected. Preserve the separate
   no-Discord fallback test through a controlled import seam or its own small lane.
4. Consolidate dependency installation into a versioned, reproducible dependency set. Runtime
   dependencies in `app/pyproject.toml` are unpinned, while CI installs another explicit list
   with `pip install -U`. Recorded successful environments provide useful evidence but do not
   make future fresh installs reproducible. This is an improvement, not an allegation of a
   known vulnerable installed version.
5. Reconcile the RW-22 status in `docs/README.md`, `docs/REGISTER.md`, `docs/PLAN.md` and its
   evidence record: they still say the branch is unmerged although PR #80 is merged. Preserve
   the historical test source identity and do not convert code acceptance into empirical
   completion. Broad cleanup already deferred in REGISTER should remain behind concrete fixes.

## Validation and scope

- Fetched GitHub, verified a clean starting tree and fast-forwarded local `main`. No reset,
  stash, local-branch deletion, push or commit was performed. Final tree verification is recorded
  in [provenance.json](provenance.json).
- Ran the committed RW-22 evidence verifier successfully. It validates the recorded four
  platform results and matching source manifests, rather than running those tests again.
  Windows Python 3.11/3.12 each record 2,875 passed / 5 skipped; Ubuntu/WSL2 Python 3.11/3.12
  each record 2,873 passed / 7 skipped. Ruff and dependency checks are recorded clean.
- `HEAD:app` and `cacaff4:app` both resolve to
  `267799acdccec6e5dbf97f730a17121c1e7a1120`, binding the merged application to that tested
  source. Documentation differs from the implementation commit as expected.
- New probes used the existing project Python 3.13.14 virtual environment, synthetic inputs,
  disposable/in-memory databases, fixture credentials/witnesses and fake transports. They
  demonstrate behavior, not a new DEC-09 3.11/3.12 release attestation.
- Autonomous testing and captures were not started, stopped, rescheduled or modified. No full
  test suite, paid service, real broker call, live model call or external message was issued.
- Audit artifacts live outside the Git repository. Scripts are retained as executed; some
  refer to this workstation's repository path and `C:/t`. They are diagnostic reproductions,
  not production utilities or regression fixes.

For replay, use the project virtual environment with the repository's `app` directory as the
working directory and on `PYTHONPATH`. `probe_ingest.py` additionally takes the repository
root as its positional argument. The other scripts use the paths recorded in their source.
Their checked-in fixture imports and fake transports are part of the reproduction contract.

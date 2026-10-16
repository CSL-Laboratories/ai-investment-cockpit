---
title: The plan — current state, delivery lifecycle, milestone gates, and scope control
status: live
supersedes:
  - path-to-production
  - capability-expansion-master-plan
  - integration-and-remote-operations-plan
  - cockpit-review-and-integrated-plan
  - remaining-work-session-plan
last-verified: 2026-09-15
---

# The plan

### RW-37 slice A 2026-09-15 Tuesday GET-only screen intake — claimed 2026-09-15

Seventh slice A package: record the Tuesday 2026-09-15 GET-only W-24
board (empty / date-not-ready; pool 199, supported 32, cash 0,
selection-ready 0, date-ready 0, cohort 0≪30) as a new claim. Same
calendar date as RW-36; different counts (supported 32, cash 0, BOW
dropped, CLST/DFH added). Do not loosen the Monday BOW freeze. Do not
mutate freeze/evaluation/cases/protocol. Do not lower the 30-identity
gate. No labelling, watchlist seed, activation, order-path edit, or
empirical PASS. Empty-run cites `screens[-1]`.
Claim: [[REGISTER]]. Code: `app/feasibility/screens.py`. Tests:
`app/tests/feasibility/test_screens.py`. RW-32 is not reused.

### RW-36 slice A 2026-09-15 GET-only screen intake — claimed 2026-09-15

Sixth slice A package: record the 2026-09-15 GET-only W-24 board
(empty / date-not-ready; pool 199, supported 31, cash 1 BOW,
selection-ready 0, date-ready 0, cohort 0≪30) as a new claim. Do not
loosen the two-row RW-31 freeze. Do not mutate freeze/evaluation/cases/
protocol. Do not lower the 30-identity gate. A cash-parse hit is not
eligibility. No labelling, watchlist seed, activation, order-path
edit, or empirical PASS. Empty-run already cites `screens[-1]`.
Claim: [[REGISTER]]. Code: `app/feasibility/screens.py`. Tests:
`app/tests/feasibility/test_screens.py`. RW-32 remains the remote seal
package and is not reused.

### RW-35 slice B empty-run research update — claimed 2026-09-15

Useful-research increment through the existing composition, not another
feasibility ledger. The latest GET-only board (now the RW-37
2026-09-15-tue row) and RW-29 case classes become a concise `#brief`
update naming why the screen produced no eligible paper candidate. Code:
`app/research/empty_run.py`, `app/workflows/research_update.py`. Tests:
`app/tests/workflows/test_research_update.py`. No order-path edit, no soak,
no empirical PASS. RW-32 remains the remote seal package and is not reused.

### RW-34 bounded Cloud JSON-child adapter — claimed 2026-09-15

First Cloud qualification package after RW-33. Cursor-native Task and installed
CLIs were inventoried and remain unqualified where they cannot pin
capabilities or enforce budget. The admitted development route is a no-tools
JSON child with a controller in `docs/research/swarm-tools/bounded_job.py`.
Tests: `app/tests/swarm/test_bounded_job.py`. Preview helper stays disabled.
No production authority change. Native Windows 3.11/3.12 are green on
`d1c58c6`; that is not a four-cell DEC-09 attestation. No empirical PASS.

### RW-25 product contract — ratified 2026-09-09

Lucas authorized the product grill, a WIP branch and local documentation commits,
with publication/merge after the rounds finish. RW-25 is claimed on
`work/rw25-product-grill` from `db354a2`. PD-048 records final adoption after the
47 scope/process decisions through Round 9; the grill rounds are complete.
Documentation delivery: [PR #85](https://github.com/NotSkittlz/ai-investment-cockpit/pull/85).

[PRODUCT](../PRODUCT.md) now defines the adopted integrated autonomous paper
product. The [decision record](product-planning.md) preserves exact answers and
amendments. The [operating contracts](product-operating-contracts.md),
[tax contract](product-tax-contract.md), [acceptance contract](product-acceptance.md)
and [delivery plan](product-delivery.md) define the adopted behavior,
qualification requirements and implementation order under this PLAN.

The selected scope includes useful cited research, all agreed human input categories,
two or three launch strategies with future expansion, dynamic subscription-aware model
routing, a full autonomous paper-position lifecycle, owner-defined access/limits and
owner-approved strategy activation/software releases. It works standalone on Windows
and Linux, primarily on Lucas's Linux box; Recursion integrates when qualified.
Full federal/California tax calculations cover taxable brokerage, Traditional/Roth IRAs
and the selected workplace plans. Explicit paper scenarios can continue with missing
personal context, while unverified required tax restrictions remain blocking; private
documents stay local by default with only minimum approved facts sent externally.

Current implementation order: bounded feasibility/evaluator qualification; useful
research through the real composition; shared authority/executor/recovery contracts;
integrated strategy-to-paper lifecycle; full tax/economic workflow; then complete
release qualification and at least 20 Linux market days plus required coverage.
These are delivery slices to split and claim in REGISTER before code, not new package
IDs, permission to start operational runs or a claim that evidence already exists.

The delivery plan explicitly maps the old MVP and DEC-03 selection to the adopted
product, retains applicable W-23/W-24 and independent-label gates, and requires a
separately scoped adversarial paper-autonomy/model-role/eligibility redesign before
authority implementation changes. It also calls for account/settlement applicability
review of current risk assumptions. The DEC-09 matrix and pre-live gates remain.

**Current precedence:** PRODUCT owns product scope/acceptance; this PLAN owns delivery
order and adopts the linked delivery slices; REGISTER owns claims. The older MVP
definition, single-strategy scope and serial delivery order below are superseded where
they conflict with this section and PRODUCT. Their applicable subsystem/evidence gates
remain under the explicit dispositions in the delivery plan. Existing runtime invariants
remain until their own reviewed implementation amendments. Historical milestone text
and evidence below are retained as the implementation record, not acceptance of the
new product. Begin the next implementation work with a bounded claim from slice A.

### RW-31 slice A screen intake and cohort/label plan — claimed 2026-09-12

Fifth slice A package: record the 2026-09-12 GET-only W-24 board
(empty / date-not-ready; pool 197, supported 32, cash 0, selection-ready
0, date-ready 0, cohort 0≪30) plus the checked-in 2026-09-09 note.
Cohort/label **plan only**. Do not mutate freeze/evaluation/cases/protocol.
Do not lower the 30-identity gate. No labelling, watchlist seed,
activation, order-path edit, or empirical PASS.
Claim: [[REGISTER]]. Code: `app/feasibility/screens.py`. Tests:
`app/tests/feasibility/test_screens.py`.

### RW-30 slice A pre-measurement protocol freeze — merged 2026-09-12

Fourth slice A package: freeze how a later measurement must be sealed
(provenance, population, as-of, tuning/held-out, threshold declaration,
ambiguity, independent judges) without starting it. Do not mutate
freeze/evaluation/cases. Do not invent numeric cutoffs. No activation,
no order-path edit, no empirical PASS.
Claim: [[REGISTER]]. Code: `app/feasibility/protocol.py`. Tests:
`app/tests/feasibility/test_protocol.py`.

### RW-29 slice A case inspection and missing-evidence ledger — merged 2026-09-11

Third slice A package after zero qualify: inspect a bounded sample of
already-cited operational evidence notes, classify the six product
failure classes, and name missing scope decisions. Do not mutate
`freeze.v1.json` or `evaluation.v1.json`. Do not ingest live raw packets.
Merged [PR #91](https://github.com/NotSkittlz/ai-investment-cockpit/pull/91).
No activation, no order-path edit, no empirical PASS.
Claim: [[REGISTER]]. Code: `app/feasibility/inspect.py`. Tests:
`app/tests/feasibility/test_inspect.py`.

### RW-28 slice A metrics freeze and evaluation — merged 2026-09-11

Second slice A *development* package: freeze `feasibility.metrics.v1`
(discrete protocol on cited corpus facts) and emit
`app/feasibility/evaluation.v1.json`. Do not mutate `freeze.v1.json`.
Zero `qualify` on this corpus is fail-closed, not a lowered gate.
Merged [PR #90](https://github.com/NotSkittlz/ai-investment-cockpit/pull/90).
No activation, no order-path edit, no empirical PASS.
Claim: [[REGISTER]]. Code: `app/feasibility/evaluate.py`. Tests:
`app/tests/feasibility/test_evaluate.py`.

### RW-27 slice A feasibility freeze — merged 2026-09-11

First slice A *development* package: `app/feasibility/` freeze + fail-closed
validation. All six Head-frozen names stay `unverified` on the freeze
document. Merged [PR #89](https://github.com/NotSkittlz/ai-investment-cockpit/pull/89).
No activation, no order-path edit. Claim: [[REGISTER]]. Code:
`app/feasibility/`. Tests: `app/tests/feasibility/`.

### RW-26 slice A feasibility inventory — claimed 2026-09-11

First bounded slice A package after the swarm pipeline smoke. Research
inventory only (paths + a proposed in-repo shortlist). Claim:
[[REGISTER]]. Briefs: `docs/research/swarm-briefs/rw26-*.md`. No
qualify/reject, no activation, no order-path edit, no empirical PASS.

### Cursor Cloud process rebase — RW-33, 2026-09-14

Owner-directed process amendment: Cursor Cloud Head with scoped native sessions,
qualified Codex/OpenCode development routes and distinct-family synthesis replaces
new Devin/SWE-2 dispatch. The historical 30–40 concurrency target is retired.
The scoped helper now previews only; actual CLI qualification is a separate bounded
claim. Runtime installation and process tests grant no product or trading authority.
See [the rebase](research/agent-swarm-rebase-2026-09-14.md) and
[Cloud research/development/testing handoff](research/cursor-cloud-rdt-handoff-2026-09-14.md).

PRODUCT acceptance and adopted A–F delivery order remain unchanged. Start Cloud
work with route/corpus inventory and one adapter qualification package, then claim
the next useful research increment under A/B; preserve C/D authority-review gates.
Market soaks and DEC-09 remain operator-host activities. Historical Devin process
notes retain their evidence with a current amendment pointer.

### BUG-159 watch-only durability D1–D4 — claimed 2026-09-08

The bounded fail-closed package is claimed on `fix/bug159-watchonly-durability`
from merged main `abbaf3f` (PR #82). Its primary contract is
[[evidence/astra-watchonly-claim-shape-2026-09-06]] and its fixture ownership map
is [[evidence/astra-watchonly-claim-shape-2026-09-06/fixture-contracts]].

1. **Operator change:** pending or failed safety persistence, interrupted/legacy
   sessions, and safety or integrity changes admitted before final authorization
   visibly refuse approval and paper submission. Recovery grants permission only
   after the signed recovery commits.
2. **Existing paths extended:** centralize committed/effective watch-only authority
   across `agent_runner/usage.py`, `orchestrator/breakers.py` and signed restoration;
   compose session admission/lifecycle in `main.py`; enforce it through paper approval
   and the existing single `OrderGateway` broker boundary.
3. **Retirement:** this package removes raw production `VendorWindow` safety mutation,
   runner/workflow double allocation, and memory-only duplicate guards. Request counts
   remain session-local; mutable `UsageWindow` SQL rows remain non-authoritative.
4. **Production reachability:** promoted D1–D4 regressions plus
   `tests/system/test_rw01_market_day.py::test_composed_restart_restores_watch_only_generation`
   use real composition with fake leaves. Removal of session admission, transition
   wiring, or final gateway verification must fail a distinct composed test, each with
   a healthy positive control.
5. **Claimed ID:** `BUG-159` only. BUG-113/#59, BUG-114/#68, BUG-118/#70,
   BUG-124/#73 and BUG-125/#74 remain historical, closed dependencies.
6. **Platforms:** signed SQLite/session state and bounded child-process interruption use
   `sys.executable`, explicit temporary paths and common synchronization semantics on
   native Windows and Ubuntu/WSL2 Python 3.11/3.12. No platform credential branch or
   physical power-loss guarantee is added; macOS remains best effort.
7. **Size:** one coordinator and existing callers/status/order boundary, capped at roughly
   1,000 production LOC plus owned tests and short evidence. If complete D1–D4 lifecycle
   obligations exceed that cap, stop and split before implementation rather than omit one.

The D4 claim is intentionally narrow: a new snapshot-pinned full-chain verification after
the real intent vetoes corruption already committed before that snapshot. This package does
not claim atomicity against an unrestricted concurrent SQL writer committing afterward.
Crash, legacy, integrity, or unproven shutdown startup holds have no automatic release in
this package; clean genesis and provably closed-session restarts remain required controls.

### RW-24 reliability follow-up — implemented, 2026-09-07

RW-23's 11 repairs and RW-24's two further fixes, measured triage optimization and
Discord validation coverage are committed on `codex/audit-reliability-2026-09-07`.
Independent authority review passed. Intake: [[evidence/rw24-reliability-2026-09-07]].
The replay fixture and fractional-quantity status regressions found in the first
completed matrix are repaired and independently reviewed. All four DEC-09 cells pass
against `fa3cc1c`: Windows 3.11/3.12 each 3,107 passed / 4 skipped; Ubuntu WSL2
3.11/3.12 each 3,105 passed / 6 skipped. Every cell passes all 233 required cases.
Final validation: [[evidence/rw23-rw24-local-matrix-2026-09-08/README]].

### RW-23 audit repair delivery — 2026-09-07

The operator authorized BUG-146 through BUG-156 repairs and local commits in slices from
merged main `c231dc2`. This is one bounded correctness package extending existing authority,
workflow, ingest and simulation paths; its seven intake answers and validation record are in
[[evidence/rw23-audit-repair-2026-09-07]]. No new capability or empirical gate is introduced.
Further reliability/performance work follows a measured review and its own scope entry.
RW-22 has merged in PR #80; older branch-status text below records its pre-merge state.

> **This is the only document that defines what we are building next and in what order.**
> Everything else is either a live register ([[REGISTER]], [[security-hardening-backlog]]),
> a subsystem contract ([[build-scaffold-ultracode]], [[quant-engine]]), reference research,
> or archived. If another doc disagrees with this one, this one wins and that doc is stale —
> say so in a PR rather than working around it.

Written 2026-08-09, after the branch-consolidation audit (PRs #8/#9/#10) and the
[[llm-hybrid-trading]] viability research; autonomy boundaries and gates refreshed
2026-08-12 from [[research/autonomy-blockers-and-unblockers-2026-08-12]], and repository
state/lifecycle reconciled through PR #53 and the 2026-08-24 RW-11/RW-19 evidence sessions.

### Supported-platform validation (`DEC-09`, resolved 2026-08-24)

The release-blocking matrix is local native Windows plus Ubuntu 24.04 under WSL2 on the
operator's Windows workstation, each on Python 3.11 and 3.12. All four cells passed on the PR #53
source tree, which is byte-identical to the tree of clean merged main
`ebda9b6691cfcfc72b76056d0190768e5f14cd66`. macOS remains a best-effort portability target,
not a release gate. Hosted Linux `linux-gates` is a cheap required PR status check and does
not replace that local matrix as package acceptance. Hosted Windows runs on `main`,
the `ci:full` label, or `workflow_dispatch`; hosted macOS is nightly on `main` (skipped if
idle 24h), `ci:full`, or `workflow_dispatch`. Hetzner is outside this decision and is not
required or tested for current packages. See [[evidence/local-platform-matrix-2026-08-24]].

This policy does not weaken platform-neutral implementation requirements and does not convert
same-host WSL2 into an independent witness. Any older statement below that a hosted/macOS or
six-cell attestation was missing remains historical evidence of that package's original state;
it is superseded as a current blocker by `DEC-09`. Online vendor, broker, market-day, precision,
external-witness, and deployment gates remain separate.

---

## 0. Current state — 2026-08-24

The first live W-22 bring-up snapshot is recorded in
[[evidence/w22-paper-mvp-2026-08-14]]. At 11:17 PT the `read_only` process had a valid audit
chain, no breaker/watch-only state, healthy signed paper-equity sampling, zero feed errors, and
all 11 Alpaca IEX book sequences at 78. That is operational evidence, not trading-performance
evidence: there were no proposals eligible for opening execution and no paper fills.

PR #36 merged the W-22 bring-up fixes: additive migration of the
legacy risk-verdict column (`BUG-100`) and post-response quote timestamp validation (`BUG-101`).
`BUG-102` and the underlying Alpaca request defect `BUG-103` are locally closed: startup now
hydrates completed daily bars, and the client supplies the bounded historical `start` required
by Alpaca instead of accepting its current-day default. A post-11:26 PT restart populated both
quotes and ADV/volatility for all 11 symbols with no faults. Its historical hosted matrix did
not run; the current `DEC-09` local matrix is green.
`BUG-104` is also locally closed: audit verification now reads the ledger and signed high-water
from one SQLite snapshot, preventing a legitimate concurrent ingest commit from producing a
false one-minute truncation result and paper-performance `watch_only` alert. Genuine hash,
sequence, watermark, truncation, and erasure failures remain fail-closed.

`BUG-106` merged in PR #37. EDGAR baseline
timestamps now derive from their signed audit transition, and a bounded one-time path repairs
legacy rows without creating a general override. A committed `read_only` live run repaired all
11 legacy baselines once, ingested one filing/four deltas, completed the following 11/11 poll
with no current error, and stopped with a valid audit chain at sequence 10,040 and zero orders.
Its older named hosted attestation did not run and is superseded as a blocker by `DEC-09`.

The merged line through PR #53 contains the WP0–WP9 foundation, the contained QT engine, the
fail-closed calendar boundary (`RW-17`), in-process EDGAR wiring (`RW-11` implementation),
event-characterization
tooling (`RW-19` implementation), live paper-market data (`RW-14` implementation), the
fail-closed transaction-cost/net-edge gate (`W-21`), RW-15 paper reconciliation, W-22's audited
paper loop, W-23's production-closed deterministic cash-merger edge machinery, W-24's semantic
verifier, BUG-106, the repeated RW-19 measurements, and W-25's Administrator-safe WORM witness
implementation, plus the sealed RW-11 prospective text-population collector.

The outage recovery queue is recorded in
[[evidence/github-outage-session-handoff-2026-08-17]]. BUG-106, W-24's verifier and adversarial
hardening, and the repeated RW-19 measurements subsequently merged in PRs #37–#39. This final
documentation reconciliation preserves the stopped-system preflight and historical handoff.

**What works now:** scheduled ingest and awareness; raw/cited evidence storage; schema-gated
triage, briefs, interrogation, and proposal drafting; deterministic risk/mode/approval controls;
human-approved Alpaca paper submission through `OrderGateway`; Discord/API status and kill
surfaces; contained synthetic/replay quant simulation; and cached Alpaca IEX L1/daily-bar
inputs feeding quote risk, quant snapshots, ADV/volatility, and fail-closed freshness controls.

**What is not complete:** RW-15's real broker-status evidence; W-22's two-day unattended paper
proof; W-24's real prospective
cohort/current-target evidence and external witness needed to activate W-23; event-driven paper autonomy
(`RW-13`); the
external SEC-11 witness; Robinhood discovery/client wiring; and every live/auto rung.

RW-19 now has two completed 11-symbol IEX/SIP sessions, repeated live IEX captures, a deliberate
reconnect/REST comparison, and an isolated 1,114-frame persistence/triage replay. The reconnect
window exposed 1,965 REST-recoverable identities. The single writer sustained only 74.67
one-event writes/s, and raw fanout produced a 236,637-character triage packet. A one-hour news
sample captured 10 stream and nine REST identities; all REST IDs overlapped, while one persistent
stream-only ID rejected full equivalence. Those are measured design constraints, not permission
to start `RW-13`. Later two-segment authenticated news reconnect runs completed across measured
5.000752-, 5.000983-, and 5.000998-second deliberate gaps. Their 10- and 30-minute after-hours
windows and the 2026-08-24 ten-minute regular-session window all held zero stream/REST identities.
Those are hash-valid insufficient samples, not reconnect or correction evidence. Broader regimes,
a non-empty news correction/reconnect sample, and a bounded deterministic coalescing decision
remain open, so `DEC-07` is unresolved. See
[[evidence/rw14-rw19-live-evidence-2026-08-18]].

Two later plan-bound NVIDIA-window attempts aborted without collecting news articles.
2026-08-26: headless resume arrived ~20 minutes after the sealed 20:50:00Z start; the collector
refused before staging/credentials/network (exit 2, zero Alpaca requests). That record is an
operator-authored pre-start refusal, not a collector tombstone; a first-class pre-start refusal
command/schema would be a separate future package. 2026-08-27 aftermath: start and fresh
preflight were green, WebSocket connect/auth succeeded, then Alpaca's correct but reordered
11-symbol subscription ack failed order-identical list equality (`BUG-111`); the collector
classified `socket_subscribe`, failed closed, and published a verified tombstone with zero news
frames, zero REST, and no retry. Mid-stream subscription-control frames were journaled but not
re-validated (`BUG-112`). The repair merged as PR #56 (main `38eab5a2`). Because the operator
repeatedly missed sealed window starts, per-window unattended-launch tooling followed on
`work/rw19-autolaunch`: a strictly read-only fail-closed safety-preflight generator, a one-shot
no-retry launcher with an outcome state machine and contained rehearsal mode, and a per-window
Task Scheduler script — adversarially reviewed (four P1 probe-blindness defects found and fixed
with regressions) and validated on the Windows `.venv` runtime that executes it, with WSL cells
deferred by explicit operator decision. Scheduling automates only launch timing; every capture
still requires fresh per-window operator authorization, and the collector's own gates remain
the enforcement point. That tooling then produced its first result. On 2026-08-28 the one-shot
launcher ran the sealed close-transition window unattended end to end - fired at 19:15:01Z,
fresh green preflight at T-2, collector started 0.016s off the sealed 19:30:00Z start, the
deliberate five-second disconnect straddling the 20:00Z close - and reached
`terminal_state=COMPLETE_VERIFIED`, the first non-empty plan-bound news capture: 4 identities
across the two thirty-minute segments, combined stream recall of the REST census of 1.0, zero
cross-segment overlap, and zero observed corrections. The empirical gate nevertheless stays
open, because the blind window was empty - nothing was published during the disconnect, so
there was nothing to lose or recover. The apparatus is now proven end to end; news
blind-window loss and recovery, correction and update behavior across reconnect, and broader
regimes remain unmeasured, and `DEC-07` is unresolved. Apparatus complete, evidence partial.
See [[evidence/rw19-ack-order-defect-2026-08-27]] and
[[evidence/rw19-close-transition-capture-2026-08-28]].

**Verification posture:** RW-14 merged in PR #31, W-21 in PR #33, RW-15 in PR #34, W-22 in
PR #35, and the bring-up repairs plus W-23 in PR #36. The PR #36 tree passes 2,240 tests / 1
skipped on Windows with Ruff clean after focused adversarial regressions and live 11-symbol
validation; its replacement Ubuntu/Windows/macOS × Python 3.11/3.12 matrix passed all six jobs.
The requested
OpenCode DeepSeek Pro/Flash external audit could not attest: the Pro workspace reported
insufficient balance and alternate read-only reviewer calls timed out without output or
repository changes. All six PR #31 matrix jobs stopped
before executing a step because GitHub reported failed recent account payments or a spending
limit that needs to be increased. RW-14's named live-data drill passed on 2026-08-18; the
billing-blocked jobs were not repository test failures and are no longer a current blocker under
`DEC-09`.
Offline work may continue on isolated branches; no package becomes production-ready or a
dependency baseline without the `DEC-09` local matrix and its applicable online evidence gates.

W-24 merged in PR #38 after a direct adversarial pass closed five concrete findings and
additionally strengthened final EDGAR CIK binding. Its combined BUG-106 integration gate passes
2,274 tests / 1 skipped with Ruff clean. BUG-106 merged in PR #37 with live recovery evidence,
and the 13/13 hash-verified RW-19 manifests merged in PR #39. The preferred
DeepSeek Pro/Flash W-24 retries timed out without findings or edits and remain missing external
attestation, not approval. All six PR #38 matrix jobs stopped in 3–4 seconds before running a
step with GitHub's payment/spending-limit annotation; that is missing CI attestation, not a code
failure.

**Immediate sequence:** the private Discord audit channel is configured and verified as a W-24
chronology mirror, but it is not SEC-11 authority under the fixed Administrator constraint.
The 2026-08-18 real-candidate intake exposed `BUG-108`; its PR #42 schema-v2 repair preserves
the current-baseline check while binding the original terms source, every signed intervening
filing, and an explicit current accession. PR #42 is merged; no real selection exists, so
expected edge stays unavailable. Continue the remaining named
RW-11/RW-15/RW-19 evidence. PR #48 closes `BUG-110`'s unbounded retained-history verification
cost locally; all six hosted jobs executed zero steps under the payment restriction. Before
provisioning W-25, make an explicit funded-S3 versus separately reviewed zero-cost
transparency-log decision. Same-host storage and Cloudflare R2 do not qualify.
A real evaluation cohort cannot be manufactured retrospectively or completed before its 30-day horizon;
fixtures cannot activate the provider. Opening paper orders remain
fail-closed until both code and evidence pass. Run W-22's first and second qualifying market days
only on verified inputs. `DEC-05` must be resolved before any `RW-18` authentication, and RW-13
remains blocked until the M1–M4.5 gates actually pass.

---

## 1. What we are building

### RW-22 review remediation — 2026-09-07

The operator authorized fixes, subagent review, documentation and commits for the five
findings against local `work/bugfix-audit-2026-09-07` at `0809a61`. This bounded follow-up
uses that reviewed branch as its explicit unmerged predecessor (base `cf2bcec`), preserving
the work being reviewed rather than claiming it is already merged or production-ready.

1. **Operator change:** denied cash claims cannot produce a verified edge; inconsistent
   simulated fills stop with an audited failure; damaged limit history stays conservative;
   equivalent persisted monetary facts render correctly; global integrity faults cannot
   repeatedly manufacture vendor recoveries.
2. **Existing paths:** merger semantics, contained AutoLoop, audited limits, bridge risk
   rendering and watch-only recovery. No additional execution path or authority is created.
3. **Retirement:** remove the cash qualifier window and blanket fill-error suppression;
   replace the limits fallback's partial-history proof and ambiguous recovery predicate use.
4. **Reachability:** signed-source edge-provider refusal, full simulated event sequence,
   ledger mutation/deletion with a surviving later entry, persisted rendering round trip,
   and healthy scheduler polls under a restored integrity fault.
5. **Claim:** `RW-22`; retain the original BUG-126/131/136/139/140 identifiers for traceability.
6. **Platforms:** full native Windows and Ubuntu/WSL2 Python 3.11/3.12 matrix, isolated
   project environments and short temporary paths; fresh independent adversarial review.
7. **Bound:** five reviewed defects and their direct integration consequences, regression
   tests and evidence. No soak, source capture, witness provisioning or live activation.

Implementation and validation are recorded in [[evidence/rw22-review-remediation-2026-09-07]].

**A human-gated, paper-first investing copilot with two engines**, per [[CLAUDE]]:

1. **Awareness + discipline** — the reliable value. Time saved, things not missed, discipline
   enforced. Powered by what LLMs are actually good at: information work.
2. **Execution** — mechanically reliable, financially experimental, capped, human-gated.

Engine 1 makes Engine 2 safer and better-informed. It never makes it *profitable*.

### BUG-113 durable watch-only generation — merged in PR #59

The bounded hardening package is claimed on
`work/bug113-durable-watch-only-generation` from clean merged main `987842b8`. It closes a
restart hole found in the 2026-08-30 repository audit without enabling a new authority rung.

1. **Operator change:** a vendor-limit blackout remains visibly fail-closed after process
   restart, and a proposal whose producing analysis predates that blackout cannot be approved or
   submitted in paper mode.
2. **Existing path extended:** restore the latest verified signed `watch_only` events in
   `orchestrator/state.py`, then enforce the `AgentJob.input_refs["_generation"]` stamp in
   `brokers/paper_execution.py` before approval authority is minted and again inside the gateway
   call boundary.
3. **Retirement:** no parallel path is created. The fresh default `UsageTracker()` composition is
   replaced by signed-ledger restoration in this package; the runner's in-process counter remains
   the single mutable façade.
4. **Production reachability proof:**
   `tests/system/test_rw01_market_day.py::test_composed_restart_restores_watch_only_generation`
   fails if composition restoration is removed, and
   `tests/brokers/test_paper_execution.py::test_approval_refuses_stale_agent_generation`
   fails if order-authority enforcement is removed.
5. **Claimed ID:** `BUG-113` only; no existing row claims this durability/enforcement defect.
6. **Platforms:** SQLite/audit reads and typed integer comparisons only; no subprocess, path,
   credential, or temp-file behavior changes. Native Windows and Ubuntu/WSL2 run the same code;
   the full `DEC-09` Python 3.11/3.12 matrix remains the acceptance gate.
7. **Size:** expected below 250 production LOC plus focused restart/adversarial tests and these
   claim records, well below the package split threshold.

**Implementation and adversarial evidence — 2026-08-31:** production composition now restores
each vendor's latest state/generation from a full-chain-verified, single-snapshot audit read. A
bad chain/anchor or malformed signed transition installs an operator-visible global watch-only
sentinel while awareness can still boot. New proposal events bind the producing AgentJob,
RiskDecision, and Order; paper approval re-derives the AgentJob runtime/generation from those
signed events, requires exact equality with current generation, and the production paper gateway
rechecks it in both review and submit. Pre-BUG-113 draft proposals lack the new signed lineage and
therefore fail closed; they must be re-drafted rather than migrated into authority.

The fresh attack→fix→regression round covers (1) restart clearing an exhausted window, (2) a
validly signed but malformed transition, (3) a future-generation skip, (4) a keyless mutable
proposal-to-job swap while the audit chain remains green, and (5) a generation change after
approval mint but before gateway review. All now refuse or restore fail-closed. The complete
`DEC-09` matrix is green on the uncommitted branch: Windows 3.11.9 and 3.12.10 each report
2,521 passed / 3 environmental skips / 5 warnings; Ubuntu/WSL2 3.11.16 and 3.12.3 each report
2,514 passed / 5 environmental skips / 4 warnings. Ruff and dependency consistency are clean in
all four cells. This paragraph records the pre-merge acceptance run; `BUG-113` subsequently
merged through PR #59 (`1e2d4e3`) and remains a closed dependency of BUG-159.

### The MVP, concretely

**End-to-end paper trades, with heavy Discord integration.**

A proposal reaches an executed paper order through the full path — risk pass → mode ladder →
human approval → `OrderGateway` — and the operator can see, question and approve the whole
thing from Discord without touching a terminal. Autonomous execution against a real account is
a later phase and is gated on the paper engine having demonstrably worked first.

**Paper-reliability MVP done means:** a market day runs unattended from ingest to a delivered
brief, a proposal it raises can be approved in Discord, the resulting paper order fills, and
the audit chain verifies over the whole sequence.

That is deliberately **not a claim of autonomy**. "Unattended" here means a scheduled process
completed without babysitting. It does not mean the system noticed a material event between
ticks, recovered a missing stream interval, reconciled a live broker outcome, or was allowed to
move capital.

### The autonomy ladder — keep these three claims separate

| Level | What it proves | Milestones |
|---|---|---|
| **Paper reliability** | Scheduled ingest/triage, human approval, paper execution, reconciliation, and audit mechanics | M1–M4.5 |
| **Dynamic paper autonomy** | Events wake a deterministic arbiter; gaps, duplicates, floods, stale data, and paper-order outcomes recover without babysitting; no capital | M5 / `RW-13` after M1–M4.5 |
| **Live broker operation** | The controlled path reaches a real broker: human-gated `review_live` first; only `auto_veto` and `auto_full` are autonomous live execution | M6–M8 / `W17`–`W19` |

Dynamic awareness does not grant execution authority, and execution autonomy does not grant a
model judgment authority. Models remain schema-gated extractors/proposal drafters. They never
receive broker credentials or bypass risk → mode → approval → `OrderGateway`.

### Bring-up universe vs edge universe — DO NOT CONFLATE THESE

We are building and proving on **tech/finance large-cap**. That is the correct *bring-up*
universe: transaction costs are negligible at our size, so cost stops being the binding
constraint while we prove the machinery; coverage is dense, so the extraction and brief
pipeline gets exercised every single day; there are no borrow or ADV problems.

**It is the wrong universe to expect edge from, and nothing in this repo should ever imply
otherwise.** Mega-cap tech is the most efficiently priced, most heavily covered segment in
existence. [[llm-hybrid-trading]] is explicit that residual edge lives in capacity-constrained
situations institutions cannot fit into — the structural opposite.

> **A working pipeline on liquid names is not evidence of alpha.** Write this on anything that
> renders a paper-trading P&L. The failure mode is someone reading a green dashboard in four
> months as a reason to fund the thing.

Niche selection — [[llm-hybrid-trading]] argues for closed-end-fund discounts and small
merger-arb, and against index-inclusion (decayed to ~0.3%) and post-bankruptcy equities (high
dispersion) — moved into active paper-MVP scope by operator direction on 2026-08-14. `DEC-03`
is not closed merely by moving it: [[research/dec03-strategy-selection-2026-08-14]] recommends a
cash-only, long-target merger-arbitrage research slice, but the operator must choose it (or CEF
discounts / no strategy) before `W-23` is claimed. Strategy selection does not assert alpha,
authorize live capital, or permit a model-authored expected return.

---

## 2. What the research changed, and what it confirmed

[[llm-hybrid-trading]] was commissioned to test the premise. Summary of what it means here.

### Confirmed: the architecture is right

Its central recommendation is that *"confining the LLM to a text-to-structured-data extraction
layer is the single best design choice available"*, and that the contamination literature
*"indicts prediction, not fact-extraction."*

That is already Invariant 5 and "execution stays model-free". We built the recommended
architecture before reading the recommendation. **This is the plan's strongest asset and the
thing least likely to be appreciated later** — the temptation to let the model "just score this
one thing" will recur, and the answer is in the research: Benhenda's matched test saw LLM
strategies go from +20.7% in-sample to −1.0% out-of-sample. Prompting fixes do not work
(Sarkar & Vafa). The only reliable mitigations are post-cutoff-only evaluation or
chronologically-consistent models.

### Reframed: `app/filings/` is the Stage 1 deliverable, not cleanup

The research's Stage 1 is *"build and audit the extraction layer first"*, sourced from free SEC
EDGAR. **That is `app/filings/`** — 4,594 LOC across 20 modules, plus
`app/ingest/edgar_pipeline.py`. PR #15 wired it into the production fetcher selection when a
SEC User-Agent and watchlist are configured. It is no longer dead code, but its live-day and
field-precision acceptance gate remains open; the legacy brew reader stays until that evidence
passes.

It also hands us an acceptance gate the repo lacks: **field-level extraction precision ≥95% on
a hand-labelled, post-training-cutoff sample of the documents we actually parse.** Not "the
tests pass" — measured accuracy on real filings. Published F1 (~0.82) is from *news* text, and
organization entities are the weak category (~0.68–0.80), so our own numbers on 8-Ks are the
only ones that count.

### Exposed: three gaps in the risk gate

Verified against the code, not assumed:

| Gap | Current state | What the research says binds |
|---|---|---|
| **No ADV-based sizing** | `RiskContext` is entirely dollar-denominated: `max_notional`, `concentration_pct`, `pending_exposure_cap`, `buying_power` | Cap position at 10–15% of median daily dollar volume |
| **No borrow cost on the risk path** | `quant/costs.py` models fees for the *simulator*; the live gate has nothing | Sub-$100M names averaged >30% borrow fees in 2023; a short leg can consume the entire spread |
| **No net-edge threshold** | `max_execution_cost_bps` is an *absolute* ceiling | Do not trade unless expected net return is **2–3× modelled all-in cost** — a *relative* test |

Note `quant/verify.py`'s `max_participation` is a fraction of **displayed book depth**, not
ADV. Different measures, different horizons. It does not close the first gap.

---

## 3. Market data: the decision, and the problem we are deferring

### Decided: poll L1 quotes + daily bars. No live order book.

`alpaca_paper.latest_quote` already polls Alpaca's L1 endpoint per symbol and is wired through
`quote_fn`. We extend that rather than building anything new.

**We are not building an L2 depth feed.** Three reasons, in order of weight:

1. **The research's own cost model does not use depth.** The square-root impact law is
   `I = Y·σ·√(Q/V)` — it needs **volatility and daily volume**. Spread comes from L1. Borrow
   comes from the broker. Nothing in the recommended cost stack needs a depth ladder.
2. **Our current feed would make depth modelling actively wrong.** `alpaca_paper.py:167` uses
   `params={"feed": "iex"}`. IEX is ~2–3% of consolidated volume: the prices are a fair NBBO
   proxy, the **sizes are not** representative of available liquidity. Walking that as
   "displayed depth" produces confident, precise, wrong numbers — worse than no model, because
   it looks authoritative.
3. **L2 is the highest-maintenance data a small team can take on** — streaming sockets,
   per-symbol book state, sequence-gap recovery, forever.

**`quant/book/lob.py` is not wasted by this.** A real FIFO book is correct for the *simulator*,
where synthetic and replay data are free and deterministic. Live gets L1 plus statistics. Two
jobs, two models — a sound split, not a redundancy.

### Consequence: this resolves the blocked staleness control

[[REGISTER]] item `BUG-040` (`verify_proposal`'s `max_staleness_ns` inert) was blocked because
`book.ts` had no defined time base. **A polled quote has an unambiguous one:** the exchange
timestamp, in epoch nanoseconds. Choosing poll-based ingestion answers the contract question as
a side effect.

### Required before live autonomy: fixed-cadence polling is a real obstacle

**This is a known limitation, recorded here so it is not rediscovered as a surprise.**

Everything today is cron-driven — premarket brew, intraday loop, EOD review. That is fine for a
human-gated awareness engine: the operator reads a brief on a schedule. It is **not** fine for
anything approaching autonomous execution, because the interesting moment is rarely on the
quarter hour. A halt, an 8-K, a gap — all of them happen between polls, and a system that
learns about them on the next tick has already missed the tradeable window.

**The path out already exists in the tree and is not being used for this.** `FEED_ALPACA_NEWS`
is a *streaming* feed that runs on its own thread — the codebase already knows how to consume a
socket and fold events into storage. What is missing is that the **intraday loop is not driven
by it**; it is driven by a clock.

The architectural requirement, to be honoured from now on: **`book_fn` and `quote_fn` are
seams.** Whether a snapshot is produced by a poll or by the last frame off a socket must be a
property of the *fetcher*, never of the caller. Build the poll version so the stream version is
a fetcher swap, not a refactor.

Scheduled as `RW-13` in [[REGISTER]]. It is **not part of the paper-reliability MVP**, because
M1–M4.5 provide the stable inputs and order lifecycle needed to test it honestly. It is a **hard
prerequisite of every live-autonomy rung**: completing M4 must never be reported as completing
the cockpit's autonomy work.

---

## 4. Milestones

Ordered by dependency, not by appetite. Each has one owner and a gate that is a measurement,
not an opinion.

### M1 — Wire the extraction layer  (`RW-11`, implementation merged; evidence pending)

**RW-11 evidence package intake — claimed on `work/rw11-filing-precision` from main
`90a9194f4fa420ccb8b082e198f64f32125d4db3`:**

1. **Operator change:** add a deterministic, offline evidence command that replays and freezes an
   eligible post-cutoff text-delta population from a predeclared case manifest and explicitly
   marked isolated raw store, emits a blind human-review sheet, and scores only a separately
   completed label file.
   It grants no strategy, mode, paper-order, live-order, or autonomous authority.
2. **Extended path:** the evidence command reuses the existing deterministic filing parser,
   delta engine, stored `Delta` contract, and citation resolver used by
   `app/ingest/edgar_pipeline.py`. It captures current/prior comparison context before the stored
   `Delta` shape discards it; it does not read or write `app/cockpit.db`, and it does not change
   the composed poller, parser, scheduler, operational database, or order path.
3. **Retirement:** no path is replaced. `RW-12` remains the separately blocked retirement of
   `edgar_brew.py` and its tests, and cannot start until RW-11's real evidence gate passes.
4. **Reachability proof:** existing
   `tests/ingest/test_edgar_wiring.py::test_composed_runner_polls_pipeline_and_emits_cited_attributed_event`
   protects production composition. New `tests/tools/test_rw11_precision.py` coverage will fail
   if the evidence command cannot replay, freeze, blind, integrity-check, and score the real
   contracts without touching the operational database.
5. **Claimed IDs:** `RW-11` only. `RW-12` stays blocked and `RW-13` is not started.
6. **Platforms:** use `pathlib`, canonical UTF-8 JSON/CSV, timezone-qualified timestamps, and
   explicit paths; no subprocesses, credentials, or platform branches. Validate under the four
   local `DEC-09` Windows + Ubuntu/WSL2 × Python 3.11/3.12 cells.
7. **Size:** one bounded text-evidence tool, tests, and documentation, approximately 1,000 changed
   lines and not to expand further. The production pipeline's companyfacts-derived inputs are
   transient, so unbiased live
   case collection, numeric-delta capture, and independent human labelling remain explicit
   empirical follow-ups, not hidden implementation work or silently reconstructed data.

**Pinned baseline:** PR #49 merged the documentation reconciliation at the SHA above. Its six
hosted matrix jobs executed zero steps under the same GitHub payment/spending-limit restriction;
that was missing hosted attestation, not a code failure, and is retained as historical context.
`DEC-09` now supplies the current local platform gate. The runtime remains `read_only`,
expected edge is unavailable, the merger execution watchlist is empty, and no paper opening,
live, or autonomous order authority is enabled.

**Development result (2026-08-18):** `tools/rw11_precision.py` now provides an offline,
database-free text-delta replay freezer, artifact verifier, blind review template, and separately
bound scorer. The frozen population binds the complete predeclared case manifest (including
zero-delta cases), expected and observed raw hashes, parser/delta/citation implementation hashes,
and every emitted row. Source-locator and material-change precision remain separate; abstentions
count as failures in the conservative floor; the case selector cannot be the recorded reviewer;
and every manifest/report says `authority: none`, `gate_closed: false`. Seventeen focused tests,
313 filing/EDGAR/tool tests, and the full Windows suite (2,338 passed / 1 skipped) are green with
Ruff clean. Ten read-only Claude Code Sonnet 5 lanes informed architecture, sampling,
cross-platform, and adversarial review; the final repaired-code pass found no reproducible
P0/P1/P2 defect. This is tooling evidence only: local self-hashes are not an external witness,
numeric/companyfacts capture is deliberately absent, no real expanded population or human labels
were generated, and the then-requested hosted Ubuntu/macOS attestation did not run. `DEC-09`
supersedes that platform blocker without changing the empirical evidence gaps.

**RW-11 Company Facts capture intake — claimed on `work/rw11-companyfacts-capture` from
merged PR #50 main `eb069fadddc45fa0a702c67b0e00edf4f94a7e31`:**

1. **Operator change:** add one operator-only evidence command that captures the exact SEC
   Company Facts response bytes for every CIK in a predeclared prospective census and verifies
   the resulting isolated, write-once package. It grants no strategy, mode, paper-order,
   live-order, or autonomous authority.
2. **Extended path:** the command calls only the existing `SecFetcher.get_bytes` fair-access
   seam and canonical `companyfacts_url`, then reuses `RawStore` and the production
   `iter_facts` walker for identity/shape evidence. It does not import or read `cockpit.db`, and
   it does not alter runtime composition, the operational poller, schemas, risk, approval,
   gateway, or order paths.
3. **Retirement:** no path is replaced. The existing transient Company Facts enrichment remains
   unchanged; numeric replay/scoring is a later separately bounded RW-11 package. `RW-12` stays
   blocked and `RW-13` is not started.
4. **Reachability proof:** hermetic `tests/tools/test_rw11_companyfacts.py` coverage injects the
   network transport and fails on partial census publication, wrong-CIK or duplicate-key JSON,
   tamper/reseal, changed blockers, incomplete byte accounting, overwrite, or a changed artifact
   set. A zero-eligible-accession census remains an explicit open-gate result.
5. **Claimed IDs:** `RW-11` only. The empirical precision gate remains open; this package cannot
   change a watchlist, order count, or mode.
6. **Platforms:** use `pathlib`, UTF-8 and canonical JSON, exclusive directory creation,
   timezone-qualified per-response timestamps, exact raw bytes, and the existing platform-neutral
   fetcher/store. Validate under the four local `DEC-09` cells.
7. **Size:** one Company Facts collector/verifier, hermetic tests, and evidence documentation,
   targeted below 1,000 changed lines and not to expand into numeric replay or scoring.

**Development result (2026-08-18):** `tools/rw11_companyfacts.py` now binds the predeclared plan,
source commit, exact raw response bytes, response CIK, per-response fetch time, capture window,
implementation hashes, complete CIK order, SEC byte accounting, eligible post-cutoff accession
summaries, and the still-open blocker set. The final path is claimed with exclusive `mkdir` only
after every CIK fetched and validated; any interrupted final move leaves a permanently refused
partial package rather than overwriteable evidence. Twelve focused tests pass, one host-capability
symlink test skips, a real NTFS-junction diagnostic passes, and the full Windows gate is 2,350
passed / 2 skipped with Ruff clean.
One completed read-only Claude Code Sonnet 5 architecture lane correctly identified mutable
snapshot timing and the need to keep capture separate from replay; those requirements are bound.
Two other lanes timed out before a usable review and are not counted. A subsequent read-only
Sonnet 5 adversarial pass found no P0 and one concrete P1: the implementation hash set omitted
`raw_store.py`. The hash binding now covers that load-bearing module; the same repair also rejects
Windows reparse points, including a locally verified NTFS junction. This remains tooling only:
no real Company Facts census, numeric replay, independent labels, or witness had yet been
produced at this development checkpoint.

**Online evidence result (2026-08-24):** one bounded 11-CIK capture on clean main
`46c7c812eccd476c4578dbef59200f5249c26db3` fetched and hash-bound 31,735,013 bytes, passed
immediate verification, and found zero supported post-cutoff 10-K/10-Q accessions. That is a
reachability/quiet-census result only, so no numeric replay package can yet be grounded in this
capture. The independently entered four-row 8-K text seed scored all eight semantic fields
correct, but four rows from one issuer/form are below the meaningful ≥30-row / ≥3-issuer frozen
population and contain no numeric cases. The current `DEC-09` four-cell local matrix is green.
At that capture checkpoint, RW-11 remained open for a supported prospective periodic accession,
separately bounded numeric replay, meaningful population, cited material delta, and external
witness. The continuation below now supplies the prospective chronology witness only.

**Prospective text-population continuation — witnessed 2026-08-24; collection not run:** On clean
merged main `bc2e86e0514a80e136014669f868940419b954a5`, an outcome-independent 11-CIK plan
(`0f882231ec1c989dba190980ef60f3075ceda6cb3a65973ed45b80e6eeb81449`) froze every exact
8-K/10-K/10-Q filing dated 2026-08-25 through 2026-11-24, retaining the earliest 60 by declared
chronology if capped and preserving every zero-delta case. Discord message
`1541519272742756453` server-witnessed that hash at `2026-08-24T18:47:14.665Z`; exact-message GET
confirmed bot/channel/content/id, null edit timestamp, no webhook, and the same server time. This
is prospective chronology, not SEC-11 execution authority.

`tools/rw11_text_population.py` is the separately bounded offline-first collector for that plan.
It refuses before network unless the exact plan/witness hashes, elapsed fixed window, fresh green
safety attestation, clean `HEAD == origin/main`, and absent declared output all verify. It uses only
the existing zero-retry bounded `SecFetcher.get_bytes` path, stores exact submissions and filing
bytes, proves complete chronology/cutoff coverage and periodic prior context, mechanically emits
the existing precision case-manifest schema, and immediately verifies every artifact/hash/count.
No future collection ran during development. RW-11 remains open for the actual meaningful frozen
population, independent ≥95% labels, supported periodic numeric replay, and cited prospective
material delta. The final local matrix is green: Windows Python 3.11 2,373/2 skipped, Windows
Python 3.12 2,368/2, and WSL2 Python 3.11/3.12 each 2,366/4, with Ruff clean in all four cells;
[[evidence/rw11-prospective-text-population-2026-08-24]].

PR #15 added the `sec:edgar` branch to `build_ingest_fetchers`, backed by
`run_edgar_pipeline` when `sec_user_agent` and watchlist scope are configured. It is preferred
over `brew_db_path`, persists the company mapping required for attributed events, and reports
pipeline faults as degraded rather than quiet success.

- **Gate:** the in-process path produces cited deltas on a real market day, *and* field-level
  extraction precision ≥95% on a hand-labelled post-cutoff sample.
- **2026-08-18 live check:** a trustworthy 11-CIK poll stored one SNDK 10-K as a clean first
  baseline with zero poll/delta errors and no delta, advancing the valid operational chain to
  sequence 10,241 with zero Orders. This is reachability evidence, not a precision sample; see
  [[evidence/rw11-live-poll-2026-08-18]].
- **Still open:** hand-label a meaningful post-cutoff delta sample and demonstrate ≥95% field
  precision. Four prior real 8-K delta anchors pass exact raw-byte round-trip, but their semantic
  fields remain deliberately unscored in [[evidence/rw11-precision-label-sheet-2026-08-18]]. The
  prospective Company Facts capture contract and bounded next-session run are recorded in
  [[evidence/rw11-companyfacts-capture-2026-08-18]]. Only
  then does a separate `RW-12` package delete
  `edgar_brew.py` + its tests. Until then the legacy reader is an intentional fallback, not the
  primary configured path.

### M2 — Live market data on the ingestion trigger  (`RW-14`)

**Package status — merged 2026-08-13 in PR #31; named online evidence passed 2026-08-18;
current local platform gate passed 2026-08-24:**

1. **Operator change:** the composed paper cockpit receives a production L1 book snapshot plus
   daily ADV/realized-volatility inputs, exposes advancing quant snapshots, and disables orders
   in `watch_only` when its latest exchange-timestamped quote is stale during market hours. Raw
   ingest continues while triage/proposals are safely deferred until fail-closed recovery.
2. **Extended path:** the existing Alpaca market-data fetchers, intraday scheduler,
   `BreakerSignals`, quant verification, and `SnapshotSource`/`/ws/quant` surfaces. No second
   broker or order path is introduced.
3. **Retirement:** no adapter is replaced. The package retires the honestly-unsampled
   `max_data_age_s` state and the blocked status of `BUG-040`, `BUG-046`, and `BUG-097`; any
   temporary test leaf stays test-only.
4. **Reachability proof:** composed-system tests must fail if quote timestamps stop reaching
   breaker sampling, if stale data no longer selects L8 `watch_only`, or if `/ws/quant` is not
   registered with an advancing production snapshot source.
5. **Package IDs:** `RW-14` is the single package; it resolves `DEC-01` and closes/unblocks
   `BUG-040`, `BUG-046`, and `BUG-097` without claiming later `W-21` cost-model work.
6. **Platforms:** pure Python/httpx, injected clocks, `Path`-safe caches, and no subprocess,
   credential-store, or platform-specific behavior. The local `DEC-09` matrix is required.
7. **Size:** one vertical slice, targeted below roughly 1,000 production LOC plus tests and
   package documentation. A larger design returns here for splitting before implementation.

**Offline implementation evidence — 2026-08-13:** `main` now composes a single cached
Alpaca IEX L1 source for quotes, books, synchronous gateway freshness, quant snapshots, and
daily ADV/volatility. The polled universe is the immutable operator watchlist plus current
paper holdings, so removing a held name from the watchlist cannot make an exit permanently
unobservable. Missing/malformed/future quotes, holdings-discovery failure, or any faulted
universe member keep the global freshness signal failed closed; there is deliberately no
silent per-symbol exclusion from order authority. The faulted set and universe-discovery
state are visible in quant status. Raw quote/bar Parquet hashes are bound into the HMAC audit
chain. Quote-cache failure rejects deterministic risk without aborting the awareness batch.
The execution-verdict TTL is derived from the same 120-second authority limit as the scheduler
and synchronous gateway check. Stale orders are refused at every hour; the system-wide L8
transition and recovery are market-hours gated, and recovery never restores higher autonomy.

The implementation received read-only DeepSeek v4 Pro/Flash architecture, test, and
order-path adversarial passes (sessions `ses_005cb948dffep00GT3p9o4wDZO`,
`ses_005cb948dffet6lNpL21BfkkHb`, `ses_005cb948affel97HEwFfJv1i6q`,
`ses_005b314d6ffebMIQqTEdhH74zF`, and `ses_005b314deffelJJPoVNNHLTYxl`). Reproduced findings
closed in PR #31 include graceful quote-cache degradation, holdings-only reachability,
calendar/clock refusal, cadence/TTL alignment, composed allowlist coverage, and raw-hash
binding. The 2026-08-18 composed live drill advanced all 11 quote sequences, crossed the
120-second threshold on frozen real exchange timestamps, entered L8 while awareness continued,
and recovered fresh in `read_only` with zero Orders. The old hosted/macOS attestation never ran;
`DEC-09` supersedes it as a blocker and the current local matrix is green. Final original offline
gates: 2,121 passed / 1 skipped on Windows, with Ruff clean. See
[[evidence/rw14-rw19-live-evidence-2026-08-18]].

- Add a daily-bars poller alongside the quote poller (same Alpaca credentials; entitlement and
  feed coverage re-verified at build under `DEC-07`). Cache per symbol, refresh daily. Yields
  **ADV and realized volatility**.
- Synthesize `book_fn` from the L1 quote — one level per side, timestamped from the quote.
- Stamp `last_quote_ts` per symbol as the poller observes quotes; the dedicated market-data
  tick samples
  `max_data_age_s` into `BreakerSignals` — the stale-data breaker's wire (`BUG-058` left it
  honestly UNSAMPLED until this package exists). Gate the sampling on market hours, and keep
  the 120s threshold coherent with the poll cadence.
- PR #31 closes `BUG-097` in code: stale data routes through the single L8 watch-only
  mechanism, ingest/awareness keeps running, and the order-path change received adversarial
  review. The named real-age transition passed on 2026-08-18 and the current `DEC-09` local
  platform matrix is green.
- **Gate:** `quant_book_seq` advances between observations on a live day; authority age is
  computed from the latest exchange timestamp. The synchronous gateway refuses stale data at
  every hour, while market-hours breaker sampling routes >120 seconds to L8 `watch_only` and
  disables orders while ingest/awareness continues. This makes both `BUG-040` and `BUG-097`
  testable through the composed path.

### M3 — Make the cost model match reality  (`W-21`)

The change M2 makes possible, and the one the research says decides viability.

**Package intake — claimed on `work/w21-transaction-cost-model`:**

1. **Operator change:** execution evidence reports round-trip spread, conservative
   square-root impact, fees, borrow applicability, ADV participation, and deterministic
   size/cost sensitivity. A proposal cannot become an order while its trusted expected edge
   is unavailable. That is the intentional production posture until a deterministic strategy
   source is separately selected and reviewed; the model-authored `TradeProposal` does not
   gain an expected-return field.
2. **Extended path:** `quant/verify.py`, `risk/engine.py`, `workflows/proposals.py`, the
   `build_services`/`main.compose` seams, `PaperExecutionService._fresh_risk`, the persisted
   `RiskDecision.execution_verdict`, and the existing operator execution block. RW-14
   `MarketMetrics` remains the only ADV/volatility input.
3. **Retirement:** this same package deletes the production verifier's displayed-depth walk,
   fillability, queue-ahead, and displayed-depth participation semantics. The FIFO book and
   depth measurements remain valid inside the contained simulator; no second live cost path
   remains.
4. **Reachability proof:** composed workflow tests must fail if RW-14 metrics stop reaching the
   proposal verdict, if unavailable edge can produce an `Order`, or if the approval-time
   `_fresh_risk` recheck omits the same cost/edge inputs. Pure tests pin the cost curve and its
   first negative whole-share boundary.
5. **Package IDs:** `W-21` only. A future deterministic edge producer or short-opening path is
   a separately claimed package and adversarial review, not latent authority hidden here.
6. **Platforms:** pure stdlib/`Decimal` calculations with injected clocks and data; no new
   subprocess, path, credential, temp-file, network, or platform-specific behavior. The local
   `DEC-09` matrix remains required.
7. **Size:** one vertical slice, targeted below roughly 1,000 production LOC plus replacement
   tests and live-doc reconciliation. Crossing that line returns here for a split.

**Pinned contracts:** expected return means verified **gross holding-period return in basis
points of position notional**. Production supplies no estimate yet; missing/non-finite/unverified
edge is explicit and fail-closed, while a present estimate fails the new hard rule unless
`gross - all_in_cost >= 2 * all_in_cost` (equivalently gross is at least 3× cost). The mapping
`intraday / short / long = 1 / 7 / 30 calendar days` has one home and applies to borrow carry.
An injected borrow quote is an annual decimal fraction with provenance/freshness; it is required
only for an explicitly identified short leg. Current sells close reconciled longs and never
become shorts. The gate uses dimensionless `Y = 1.0`, converts RW-14 annualized volatility to
daily volatility with `sqrt(252)`, and hard-stops above 10% of median daily dollar volume;
0.5–1.0 is reported as the sensitivity band, not a knob that can weaken the gate. Curves use
whole shares and exact deterministic cost evaluations inside the model-valid ADV range.

**Implementation checkpoint (2026-08-13, uncommitted):** the vertical slice is implemented on
the claimed branch. Local Windows gates are 2,155 passed / 1 skipped with Ruff clean. Fresh
read-only DeepSeek v4 Pro architecture/adversarial review and v4 Flash caller/test review found
no P0/P1 order-authority bypass. Their concrete findings were closed with regressions: the
approval surface now prices the fixed persisted Order quantity; persisted W-21 evidence keeps
fresh risk armed if the market-data attachment disappears; `decide()` refuses missing RW-14
metrics; status reads the active `VerifyParams`; and versioned edge/borrow plus invalid-input
boundaries are pinned. A separately reviewed deterministic edge producer remains intentionally
out of scope and unavailable. This historical checkpoint predated both the RW-14 live drill and
the current `DEC-09` local matrix; it was not live authorization.

- Replace `verify.py`'s displayed-depth walk with **spread (L1) + square-root impact (ADV, σ) +
  borrow on any short leg**.
- `max_participation` becomes a fraction of **ADV**, which is the measure the 10–15% rule is
  actually stated in.
- Add the **relative** net-edge test: refuse when expected net return < 2–3× modelled all-in
  cost. This is a new check, not a re-tuning of `max_execution_cost_bps`.
- **Gate:** a cost-sensitivity curve for our universe showing net return against position size,
  and the dollar threshold where a trade goes cash-flow negative.

### M4 — The paper loop, end to end  (`W-22`)

The MVP. Proposal → risk → mode → Discord approval → `OrderGateway` → filled paper order →
audited.

- **Fills modelled at bid/ask, never mid.** The research is explicit that mid-quote fills are
  the single most common way a paper result flatters itself.
- Discord carries the whole loop: brief, proposal, question, approve/veto, fill confirmation.
- Loss signals into `BreakerSignals` (the two `BUG-058` left unsampled): daily loss derived
  from portfolio snapshots (day-open vs current equity — decide whether the breaker means
  realized-only or drawdown, and rename accordingly, one home); consecutive losses derived
  from round trips once fills flow. Neither is wired before this package because neither
  input exists before it.
- **Gate:** an unattended market day, end to end, with `verify_chain().ok` over the whole
  sequence — and a second one, a week later, with no human intervention in between.

**Implementation checkpoint (2026-08-13, offline):** the composed service now samples signed
Alpaca equity and reconstructs closed round trips from cumulative audit fills on the one-minute
reconciliation cadence. Daily loss is explicitly `daily_equity_drawdown` (first signed New York
day observation versus current equity), and an active signed breaker is a synchronous gateway
backstop. Synthetic tests fill buys at ask and sells at bid; real Alpaca fills remain
broker-reported. Discord `/ask` reaches SEC-18 fresh interrogation, while `/paper` preview,
approve, reject, and veto share the loopback execution service and `OrderGateway`; fill delivery
is post-transition and its success/failure is audited. Status exposes performance, audit-chain,
and fill-notification health. Windows: 2,180 passed / 1 skipped; Ruff clean. The expected-edge
source remains deliberately unavailable, so new opening orders continue to fail closed. The
online broker and two-day gates above remain open; the current local platform gate is green.
See [[runbooks/w22-paper-mvp]].

**`RW-15` — order-status poll + real broker reconciliation. A named prerequisite of this
gate, scheduled NOW so the temporary state cannot become permanent (the RW-10 lesson).**
The 2026-08-09 sweep made fills-at-submit work (`BUG-052`), which covers marketable orders
only: without a status poll, a RESTING limit order holds the SEC-07 cap forever, a partial
never progresses, a lost-response submit stays an `ambiguous_abort` the operator must clear
by hand, and `reconcile_startup` can only alert (never heal) because `_no_broker_history`
returns nothing. A degenerate M4 using only market orders would LOOK done while all of that
stays broken — which is precisely how a gate gets gamed by accident. Scope, per §5:

1. **Operator change:** resting/partial orders settle without intervention; ambiguous
   aborts clear themselves; the reconcile report can heal.
2. **Extends:** the gateway's existing ACK machinery (`_sanitize_broker_response` →
   `_BROKER_STATUS_TO_STATE` → `_ack_consistent`, reused verbatim — one parser, one home)
   plus a scheduler job over orders in open states; lookups by ``broker_order_id``, and by
   ``client_order_id`` (= ``cockpit-{order.id}``) for the lost-response case.
3. **Retirement:** ``main._no_broker_history`` is deleted in the same PR.
4. **Reachability test:** a system test that fails when the poll job is not scheduled.

5. **Claims:** `RW-15`; settles `ReconcileReport.ambiguous_aborts`; the partial-fill
   remaining-notional refinement waits on `DEC-04` (see [[REGISTER]]).
6. **Platforms:** pure httpx polling, no subprocess/paths — CI-neutral.
7. **Size:** one package (~300–400 LOC + tests). Order path → adversarial-review round.

**Implementation checkpoint (2026-08-13, offline):** the fixed Alpaca paper host now exposes
identity-bound GET-only lookups by signed broker id and the gateway's deterministic client id;
the bounded poll reuses the gateway ACK parser/status mapping, consumes a recovered submit's
exact signed approval, advances only through audited FSM primitives, records cumulative partial
progress, and refuses chain/row/identity disagreement in the exposure-preserving direction.
Startup and the one-minute scheduler both reach the same service, and `_no_broker_history` is
retired. Windows: 2,165 passed / 1 skipped; Ruff clean. The real resting/partial/terminal and
lost-response Alpaca paper gate remains online evidence; current platform acceptance uses the
green `DEC-09` local matrix.

### M4.5 — Deterministic paper edge source (`DEC-03` → `W-23` → `W-24`)

`DEC-03` is now in active paper-MVP scope because M4's mechanics cannot create an opening
`Order` while the trusted expected edge is unavailable. The decision and implementation remain
separate: `DEC-03` selects the strategy; only then may `W-23` be claimed for the order-path
change. The detailed comparison and acceptance contract are in
[[research/dec03-strategy-selection-2026-08-14]].

The current recommendation is a deliberately narrow merger-arbitrage research slice: announced
cash-only transactions, long the public target only, no acquirer short, no options, no CVR or
mixed consideration, and no deal outside the existing 30-calendar-day `long` horizon. An SEC
filing can establish deal terms; it cannot establish expected return. Before an edge is trusted,
a point-in-time outcome data set must deterministically calibrate completion probability and
failure loss, with immutable evidence and a conservative versioned estimate.

`W-23` must inject the same provider into both proposal drafting and
`PaperExecutionService._fresh_risk`, recompute immediately before approval, and return no edge
on stale/missing/amended/terminated/mismatched evidence. The model remains limited to
schema-gated fact extraction and never supplies probability, failure price, or return. The
package requires reachability tests, a fresh adversarial review, and the local `DEC-09` matrix.
Selecting the strategy or completing `W-23` grants neither paper mode nor live authority; the
ordinary risk → mode → human approval → `OrderGateway` chain remains mandatory.

**W-23 merge checkpoint (2026-08-16):** PR #36 merged the provider, immutable evidence records,
draft/approval-time reachability, three independent adversarial rounds, and `BUG-105`. The final
local gate was 2,240 passed / 1 skipped with Ruff clean; the replacement six-job platform/Python
matrix is green. Production still returns no edge because operator-authored terms and historical
labels are not independent semantic authority.

**`W-24` intake — delivered in PR #38; evidence gates remain open:**

1. **Operator change:** W-23 may report a verified edge only when a deterministic verifier binds
   every authority-bearing deal term and cohort label to immutable raw sources, proves the
   complete declared candidate universe, and proves predictions were committed before outcomes.
   Missing, retrospective, incomplete, ambiguous, or self-labelled evidence remains visibly
   unavailable. No mode is promoted automatically.
2. **Extended path:** replace only `CashMergerEdgeProvider._verify_semantic_authority`'s hard
   refusal with an injected, model-free verifier; extend the existing merger evidence schemas,
   read-only provisioning tool, composition/status seam, and W-23 raw/audit snapshot rules. No
   code or I/O enters `app/risk/`, and no verifier receives a broker or order capability.
3. **Retirement:** the hard-coded `SemanticEvidenceUnavailable` production stub is removed in
   the same package only after the injected production verifier is composed. Research fixtures
   remain unable to select a permissive verifier.
4. **Reachability proof:** composed tests must fail if production can return an edge without a
   complete source manifest, an externally witnessed pre-outcome commitment, source-derived
   outcome/deal-term claims, or a fresh final verification at both draft and approval time.
5. **Package ID:** `W-24` only. Capturing a real cohort/current target is evidence, not code and
   not permission to weaken minimum sample, precision, chronology, freshness, or W-21 cost gates.
6. **Platforms:** pure Python/SQLAlchemy/`Path` code over existing SQLite, raw-store, Parquet, and
   audit seams. No subprocess, model runtime, MCP, broker, platform branch, or new credential.
   The local Windows + Ubuntu/WSL2 × Python 3.11/3.12 matrix remains mandatory.
7. **Size:** target below 1,000 production LOC plus tests. If complete-universe acquisition needs
   a new external feed or prospective collector, stop at a separately claimed evidence/source
   package; W-24 must not encode a guessed source contract.

The verifier's trust root is source evidence, not an operator assertion: positive deal terms and
outcomes require exact cited spans or structured fields from signed raw filings; negative and
exclusion claims require coverage of the complete declared document set; cohort membership
requires a manifest of every source item in the frozen selection interval; and prediction
commitment must be witnessed outside the mutable cockpit store before any outcome source
timestamp. Historical rows created after their outcomes can test mechanics but can never satisfy
production authority.

**W-24 implementation checkpoint (2026-08-17):** PR #38 composes a production
`CashMergerSemanticVerifier` with no configuration switch to a permissive implementation. A
selection commitment contains no outcomes and must have at least 30 unique evaluation identities,
bind signed selection filings/entry quotes plus fresh
per-CIK EDGAR coverage, and receive an exact-message Discord server timestamp within five minutes
of creation. Calibration is schema v2 and names that commitment. Verification re-derives every
eligibility label, rejects source-derived rows outside the cash-only universe, derives outcomes
from the complete signed filing set through the horizon, requires a non-backdated post-horizon
poll, and re-runs inside edge persistence. The parser is intentionally narrow; language it cannot
classify returns unavailable. No model, broker, subprocess, platform branch, or `app/risk/` I/O
was added. This closes the code gap only: the real prospective cohort, current eligible target,
Discord audit-channel configuration, CI, and online evidence remain gates.
The final BUG-106-integrated Windows gate is 2,274 passed / 1 skipped with Ruff clean. A fresh
direct attack pass additionally pinned expanded agreement negation, target-specific cash
consideration, unconditional outcomes, five-minute pre-commit quote freshness, configured
Discord channel identity, and final EDGAR CIK binding; see
[[evidence/w24-merger-semantic-verifier-2026-08-17]]. The preferred read-only DeepSeek Pro/Flash
review attempts timed out without a verdict, so that external attestation remains missing.

**W-24 intake blocker and repair (2026-08-18):** a GET-only screen of 46 real discovery names
found 19 with a current SEC mapping and supported filing head, but zero of those heads passed the
pinned parser. The single-source/current-baseline rule could not retain an announcement's terms
after a later ordinary filing. PR #42's `BUG-108` repair replaces only the selection manifest
with v2: it witnesses the original semantic source, a complete ordered set of locally signed
supported filings through selection, and an explicit current accession that must still equal the
strict EDGAR baseline. Both the prospective prediction and eventual evaluation label are derived
from that frozen set. Omission, addition, chronology reversal, unsigned rows, future/post-commit
evidence, v1, witness tampering, or any current-baseline/file-set change fails closed. No SQL
migration is needed because there is no real v1 commitment. The real ≥30-name cohort, held-out
precision, external witness, and a current target remain open. The final local
Windows gate is 2,303 passed / 1 skipped with Ruff clean after the adversarial pass blocked
cross-filing parser phrase synthesis; see
[[evidence/w24-prospective-intake-2026-08-18]].

The bounded post-merge continuation expanded discovery to 65 names and then applied the absolute
latest-SEC-filing rule before downloading semantic sources. Forty-three names had no current SEC
ticker mapping, 16 had a newer unsupported filing type, and only six had a supported current feed
head. None of the 64 supported 2026 primary documents across those six passed the eligibility
parser. A direct check of CRNX accession `0001140361-26-027642` then isolated `BUG-109`: the
authoritative announcement 8-K states an all-cash Nasdaq common-stock merger, but routine
"canceled and automatically converted" / "Agreement and Plan of Merger" language falls outside
the pinned grammar. No partial cohort or Discord commitment was created. Remediation is a separate
parser-hardening package with frozen real positive and adversarial negative sources, followed by
the held-out precision gate; no manual label or grammar bypass is permitted. The `BUG-109` repair
now recognizes the exact CRNX source while blocking cross-sentence, option,
preferred-share, note, negation, cashless, and cross-document misattribution cases. Its Windows
gate is 2,311 passed / 1 skipped with Ruff clean. It merged in PR #43 at `4bcf333`; all six hosted
jobs executed zero steps under the existing payment restriction. `DEC-09` supersedes that hosted
gap as a platform blocker; independent witness and held-out human precision remain open.

The strict post-merge rescreen preserved the 43/16/6 SEC-head split and tested every supported
2026 primary filing for those six names. CRNX alone now forms a deterministic five-filing semantic
set from accession `0001140361-26-027642` through strict head `0001140361-26-032951`; the other
five still refuse. One unsigned candidate is not a ≥30-name universe, and CRNX's combined primary
sources state neither one exact expected-resolution date nor one exact outside date in the pinned
grammar, so the current-target gate also remains unavailable. No raw import, quote, selection row,
or Discord witness was created; see [[evidence/w24-post-bug109-rescreen-2026-08-18]].

**`W-25` — merged in PR #41; activation evidence pending:**

1. **Operator change:** the existing Discord operator bot may retain guild-wide
   `Administrator`, but Discord no longer counts as SEC-11's append-only trust root. Discord
   remains an operator-delivery/audit mirror and, after exact-message hardening, a fail-closed
   W-24 chronology surface. SEC-11 authority comes only from an independently provisioned S3
   Object Lock bucket whose retained versions are outside the bot and local database trust
   domains. Missing or unverifiable WORM storage keeps every dependent gate unavailable.
2. **Extended path:** `bridge/witness.py`, `main._build_anchor_store`, guarded non-secret
   settings, keyring names, status/setup documentation, and the existing
   `GitAuditLogAnchorStore` adapter seam. No model, broker, order, mode-transition, or
   `app/risk/` path changes.
3. **Retirement:** `DiscordAuditLogWitness.out_of_domain=True` is retired in this package.
   Discord rolling posts and the local git-head file become mirror-only; exactly one
   Compliance-retained S3 adapter may satisfy `latest()`. W-24's exact-message Discord path
   stays separate and rejects edited/wrong-author receipts rather than being promoted to an
   audit-head witness.
4. **Reachability proof:** composed tests fail if Discord Administrator/mirror state can unlock
   an anchor consult, if missing/partial WORM configuration creates a trusted adapter, if a
   current shadow version/delete marker hides a retained higher anchor, if retention is not
   active Compliance mode, or if an edited/wrong-author W-24 message verifies.
5. **Package IDs:** `W-25` and `BUG-107` only. `BUG-107` is the existing semantic verifier's
   failure to reject Discord `edited_timestamp`/unexpected authors despite the runbook's
   edit-fails-closed promise. This package does not activate a real bucket, close SEC-11, start
   a cohort, or promote mode.
6. **Platforms:** boto3 is used only behind explicit keyring-held AWS credentials and
   non-secret bucket/region/prefix settings; the default AWS credential chain is never used.
   The adapter itself is dependency-injected and deterministic under tests. The local Windows
   + Ubuntu/WSL2 × Python 3.11/3.12 matrix remains mandatory.
7. **Size:** one security vertical slice below roughly 1,000 production LOC plus tests and
   provisioning policy/runbook text. Any bucket-creation automation or alternative cloud
   provider is a separate package.

**Pinned WORM contract:** the bucket must have versioning and Object Lock enabled with a
bucket-default **Compliance** retention period. The runtime credential may put/get/list versions
and read bucket/object retention, but receives no delete, retention mutation, legal-hold
mutation, bucket-configuration mutation, or governance-bypass permission. Publications use
content-addressed/sequence-addressed keys, but readers still enumerate every object version and
delete marker because S3 permits new versions and delete markers above a protected version. A
version contributes authority only when its bytes parse canonically, its metadata matches the
payload, and its server-reported Compliance retention remains active for the configured minimum
window. Duplicate/conflicting same-sequence evidence, pagination failure, unknown retention, or
any read error returns no trusted anchor. Real bucket provisioning plus a fresh delete/shadow/
rollback adversarial drill is an online evidence gate, not inferred from mocks.

**2026-08-18 cost/provider hold and finding:** the pre-BUG-110 S3 trust read listed and revalidated
every retained version; the production append path performed that full read twice before reading
the new version, so weekday `*/30` scheduling and consuming-path reads grew with history. The
PR #48 remediation below bounds that work, but independent-witness and real-cloud gates remain.
The
existing Hetzner VPS is still the same rollback domain, and Cloudflare R2 still lacks the adapter's
required AWS Object Lock/version-retention interface, so neither can be promoted from mirror to
authority. Sigstore Rekor remains a research candidate. See
[[research/sec11-zero-cost-witness-feasibility-2026-08-18]].

**`BUG-110` intake — delivered in PR #48 from `work/bug110-bounded-witness-read`:**

1. **Operator change:** W-25's configured trust read has a fixed remote-operation ceiling as
   retained anchor history grows. This package activates no bucket, witness, mode, or order.
2. **Extended/replaced path:** replace `S3ObjectLockWitness._list_versions()` and the
   all-history `_read_latest_strict()` scan with a versioned reverse-sequence key contract and
   one capped first-page candidate proof. Preserve the anchor store's independent monotonic
   pre-read and require the S3 adapter to re-prove the current retained head after every write;
   both defense-in-depth reads remain constant-cost.
3. **Retirement:** the full-prefix pagination/all-version body-and-retention scan is deleted in
   this package. No parallel S3 trust path remains.
4. **Reachability proof:** witness-adapter regressions must show constant `ListObjectVersions`,
   `GetObject`, and `GetObjectRetention` counts after thousands of retained anchors; a capped page
   that cannot prove the complete highest-sequence group returns no anchor. Existing composed
   rollback, delete-marker, shadow, conflict, retention, gateway, gate, and mode tests remain
   mandatory.
5. **Package ID:** `BUG-110` only. Rekor/provider selection, real cloud provisioning, SEC-11
   closure, W-24 authority, and any mode promotion remain separate decisions/evidence.
6. **Platforms:** pure Python over the existing injected boto3 seam; no new subprocess,
   credential, path, temp-file, network, or platform branch. The local `DEC-09` matrix remains
   required.
7. **Size:** below roughly 300 production LOC plus focused adversarial tests and documentation.

The key schema is bumped before any real bucket exists. Fixed-width inverted sequence numbers make
the highest claimed sequence lexicographically first under S3's documented ordering. The reader
requests one capped page, validates every returned key, and reads only the complete highest-
sequence group. A truncated page containing only that group is ambiguous and fails closed; a page
that reaches a lower sequence proves that the candidate group is complete. A malformed leading
key, candidate delete marker, same-sequence conflict, insufficient retention, body/key mismatch,
or API anomaly still returns no trusted anchor. This ordering proof is external; no local cache or
mutable latest pointer contributes authority.

**2026-08-18 merged result:** commit `42fe9ab40e6f132fc8aaa6338d8726afcbfe3e83`
merged in PR #48 as `8f66d6c07fb32224a316e4ce9afb1d0a1b51f9f7`. A rebuilt adapter found
head 2,048 with exactly one
bucket-lock read, one capped version-list request, one object read, and one retention read. The
defense-in-depth store + adapter prechecks and post-write race proof remain a fixed three of each
read on an ordinary non-idempotent append. Focused witness/rollback/scheduler tests passed 80/80;
the hermetic Windows suite passed 2,321 / 1 skipped and Ruff is clean. All six hosted PR #48 jobs
executed zero steps under GitHub's payment restriction; that is historical accounting context,
not a code failure or a current blocker under `DEC-09`. This is not an activation: independent
review, provider/cost authorization, and real Object Lock delete/shadow/retention/rollback
evidence remain open.

### RW-19 plan-bound news-reconnect hardening — claimed 2026-08-24

The offline package is claimed on `work/rw19-plan-bound-news-reconnect` from clean merged main
`e09349829d23cf4e95f25536685d462428da1502`. It does not run another Alpaca capture. It prepares
one prospective evidence-grade command so a later, separately authorized attempt cannot silently
discard completed rows, paginate without a resource bound, publish into the repository, or be
treated as closing `RW-19`/`DEC-07` merely because it completed.

1. **Operator change:** one explicitly plan-bound, fresh-preflight-gated, external write-once,
   partial-preserving, immediately verified news-reconnect evidence command. It changes no mode,
   watchlist, model, risk, approval, broker, gateway, order, or runtime behavior.
2. **Existing path extended:** the RW-19 evidence apparatus is extended by
   `tools/rw19_news_reconnect_plan_bound.py`, reusing the deterministic news characterization
   functions in `app/ingest/characterization.py` without changing that module or any production
   composition path.
3. **Replacement/retirement:** no production path is replaced. The legacy
   `tools/rw19_capture.py alpaca-news-reconnect` command remains available for diagnostics and
   re-verification of its existing evidence lineage, but is explicitly non-qualifying for a new
   prospective RW-19 observation. Any deletion is a separately claimed cleanup, not part of this
   package.
4. **Reachability proof:** `app/tests/tools/test_rw19_news_reconnect_plan_bound.py` exercises the
   composed command with fake sockets and HTTP leaves. It fails if plan/hash/source binding, the
   exact universe, fresh safety preflight, socket handshake, resource caps, secret-reflection
   rejection, partial tombstones, write-once publication, or immediate verification is removed.
5. **Claimed IDs:** `RW-19` evidence only. `DEC-07` stays open; `RW-13` is not started.
6. **Platforms:** the command uses canonical UTF-8 JSON, `pathlib`, bounded git subprocesses, a
   same-parent staging directory, and link/reparse rejection on Windows and Ubuntu/WSL2, with
   best-effort macOS portability. Validation uses the current four-cell local `DEC-09` matrix;
   Windows tests retain the short `C:\t` temp path and isolated environment-file rule.
7. **Size:** approximately 1,470 operator-only evidence-tool lines plus 800 non-blank hermetic
   test lines and this handoff; zero production-runtime lines and zero production imports. This
   is larger than the normal split heuristic. It remains one deliberately cohesive evidence
   protocol because
   either half of capture/tombstone/publication and full recomputation would be unsafe and unusable
   alone. A shared-framework refactor is not folded in: changing the already hash-bound RW-11
   collectors would invalidate their cross-version verification contract. The command's AST
   isolation test and `tools/` location pin this narrow exception; future growth requires a split.

The later plan—not this development branch—will prospectively freeze the exact sorted 11-symbol
awareness universe, time window, resource ceilings, external output path, selection rationale,
and clean merged collector SHA. Discord witnessing is not included or authorized here. See
[[evidence/rw19-plan-bound-news-reconnect-2026-08-24]].

### M5 — Event-driven triggers  (`RW-13`)

**Dynamic paper autonomy.** Drive triage from durable event candidates while retaining cron as
the correctness backstop. This is a separate deliverable after the scheduled paper MVP, not an
optional enhancement and not an order-authority path.

- Use the existing database/audit spine as a transactional trigger/outbox ledger and a bounded,
  single-writer worker first. No external message broker without measurements that require one.
- Consume an Alpaca market-data WebSocket on `v2/{feed}`; quote, trade, correction, and
  trading-status channels multiplex there. News and account order updates use separate
  endpoints and independent reconnect handling. Connection counts and entitlements are
  verified under `DEC-07` rather than assumed.
- A re-verified SEC Latest Filings Atom/RSS endpoint may provide a discovery hint that wakes
  the in-process EDGAR fetch/extract path scheduled by M1. The documented submissions API is
  the correctness/backfill surface. With a configured SEC User-Agent, the current five-minute
  `sec:edgar` job performs bounded SEC network I/O; without it, only the explicitly configured
  legacy brew fallback is available.
- Only structured source metadata selects a trigger. Untrusted headline/filing text cannot
  schedule work, spend a budget, trip a breaker, or reach the order path.
- Stable source/id dedup, immutable corrections, gap detection, reconnect backfill, replay, and
  bounded flood/coalescing behavior are in scope. Both stream and cron converge on the same
  idempotent triage service.
- **Prerequisites:** M1–M4.5, `RW-17`/`BUG-096`, `BUG-097`, and `RW-19`'s dated stream/load
  evidence resolving `DEC-07`.
- **Replay gate:** run the fast path and cron independently on separate clean stores and require
  identical triage dispositions, budget spend, proposals, and orders; then run fast path followed
  by cron on the same store and require zero duplicates. A pre-`RW-13` recorded day pins the
  feature-off baseline.
- **Recovery gate:** an injected 60-second loss produces zero permanent loss for channels with
  REST/history backfill after WS/REST identity equivalence is proven. Halt/status gaps use a
  designated secondary source or are surfaced explicitly as best-effort—never silently fresh.
- **Eligibility gate:** structured predicates forward strictly fewer events than the raw stream,
  retain 100% recall against cron, and cause at least one measured dispatch before cron would
  have selected it. A forward-everything arbiter does not pass.
- **Safety/performance gate:** market-hours age of the latest exchange-timestamped quote over
  the existing 120-second threshold reaches the single L8 `watch_only` path (`BUG-097`). Proposed
  frame-to-triage p50 ≤5 s / p95 ≤15 s / p99 ≤60 s is re-baselined under a minimum measured
  event density. Under the flood vector, measured bounds for disarm, mode/watch-only transition,
  and reconciliation delay are all met; the out-of-band HALT sentinel remains effective.
- **Dynamic-paper soak (`W16`):** event path enabled for at least four consecutive market weeks,
  plus engineered reconnect/backfill, same-symbol burst, correction, dropped order update, and
  calendar-year-rollover cases. Record event/trigger density, latency distributions, every
  failed dispatch, reconciliation result, and audit verification. A quiet or feature-off soak
  cannot pass.

### M6 — Robinhood discovery, then human-gated `review_live`  (`RW-18` → `W17`)

Planning this rung is in scope; enabling capital is not. Robinhood Agentic officially permits
orders without a separate per-order confirmation, so confirmation is not the blocking
assumption. Its machine-auth/session behavior, broad read scope, tool schemas, rate/error
behavior, status vocabulary, idempotency support, and exact order-type/TIF intersection are
not published and must not be guessed.

- **Before connecting, resolve `DEC-05`.** Authentication itself grants broad read authority;
  "no capital" does not mean "no confidentiality risk." The discovery identity must hold no
  other Robinhood assets/data unless the operator explicitly accepts the read-all scope.
- Then run `RW-18`, a **no-capital authenticated capability spike**: record the version,
  capabilities, and auth profile advertised by `server/discover`; enumerate exact tools and safe
  read responses; measure expiry/reconnect/reauth and rate/error behavior; and record any
  idempotency/status semantics. Place zero live orders.
- The final MCP 2026-07-28 core is stateless. Its Client Credentials extension is optional;
  do not assume Robinhood supports it. Pin the contract Robinhood actually advertises.
- Keep a deterministic Robinhood client private behind `OrderGateway`. Never expose MCP or the
  credential to a model or put either in `app/risk/`.
- `RW-18` resolves `DEC-06` and `DEC-08`; it can run in parallel with paper work after `DEC-05`
  because it neither implements an order path nor moves capital.
- **`W17` blocked by:** M5 and the `W16` dynamic-paper/performance gates; `RW-18`; SEC-11 and
  every applicable P0 pre-live gate; and the live-funding evidence window below.
- **Discovery gate:** advertised protocol/auth/tool contracts are recorded with zero live orders.
  An unknown, expired, or drifting contract produces `watch_only` plus an operator alert.
- **First-live gate:** only after funding is separately authorized, `review_live` remains
  human-approved; every supported order reaches a reconciled terminal state or an explicit
  fail-closed ambiguity; kill/disconnect remains responsive; and the external audit witness
  verifies the sequence. Unsupported/unknown operations are unavailable, not emulated.
- **Funding evidence:** W16 owns the instruments. Its two-week paper/four-week auto-window and
  30-proposal thresholds are minimum statistical inputs, not authorization to fund. Funding is
  barred for at least six calendar months of realistic paper operation and extends toward 12
  months until the scorecard, event-density, dynamic-path, reconciliation, and fault-injection
  evidence is sufficient and operator-signed.

### M7 — `auto_veto`  (`W18`)

No per-order affirmative approval, but the operator retains a timed veto and kill path. Claim
only after M6 produces reconciled human-gated live evidence, the external witness is operational,
and the security backlog's W16/pre-live performance gates pass. Its separately recorded
`auto_veto` soak must have responsive veto/kill distributions, zero unexplained reconciliation
failures, and a green external audit witness. A faster event loop does not lower this bar.

### M8 — `auto_full`  (`W19`)

The final mechanical-execution rung, not autonomous judgment. Claim only after an additional
adversarial review and a separately recorded `auto_veto` soak shows no unexplained
live-versus-shadow divergence, reconciliation failures, audit gaps, or late kill/disarm. The
mode ladder may still fall to `watch_only`; no implementation may weaken that fail-closed path.

### Research hold points before an implementation package is claimed

1. `RW-16` is closed; refresh its dated primary-source snapshot when a moving contract is used.
2. Resolve `DEC-05` before any Robinhood authentication; then `RW-18` no-capital discovery
   resolves `DEC-06` and `DEC-08` before `W17`.
3. `RW-19` records event capture/replay, stream-gap, load, WS/REST identity, and IEX/SIP
   evidence and resolves `DEC-07` before `RW-13`.
4. `RW-17`/`BUG-096` is closed: an unknown calendar year refuses auto-arm/orders while ingest
   and awareness continue. `RW-14`'s named stale-feed-to-L8 transition passed on 2026-08-18,
   and the current `DEC-09` local matrix is green.
5. Every numeric latency/load target is labelled proposed until measured; vendor facts and
   repository invariants are never presented as performance promises.

Dependency order:

`{RW-11 evidence → RW-12, RW-19 evidence → DEC-07, DEC-05 → RW-18}` run as bounded evidence
lanes around the remaining serial code spine `W-21 → RW-15 → W-22 → DEC-03 → W-23 → W-24 → RW-13 → W16 → beta
stabilization → W17 → W18 → W19`. RW-14's named online acceptance and current local platform
gate passed; its other empirical gates cannot close by assumption.

### Recommended development pipeline

This lifecycle is the default route from the current repository to deployment. A phase name is
an evidence state, not a marketing label, and does not authorize a blocked package. Keep **one
production-code package active at a time**; read-only evidence capture and operator decisions
may run alongside it.

| Phase | Purpose | Primary packages/evidence | Exit condition |
|---|---|---|---|
| **1. Offline development** | Build deterministic vertical slices without consuming live services or CI budget | `W-21 → RW-15 → W-22`; after operator selection, `W-23 → W-24`; fixtures for `RW-11`/`RW-19` | targeted + full local suite and Ruff green; docs/register updated; adversarial review complete where authority changes |
| **2. Online testing and evidence** | Prove platforms, credentials, vendor contracts, live data, and broker outcomes | local `DEC-09` matrix; `RW-11`, `RW-19`/`DEC-07`, `RW-14`, `RW-15`; `DEC-05 → RW-18`; SEC-11 witness activation | every required platform/live-day/precision/reconciliation/source-contract gate recorded; no mocked result substitutes for an online gate |
| **3. MVP** | Deliver reliable scheduled, human-gated paper operation | `W-22` plus the `DEC-03`-selected `W-23` edge source and `W-24` semantic authority on the M1–M4.5 stack | two unattended market days at least a week apart, full Discord loop, bid/ask paper fills, terminal reconciliation, green audit chain |
| **4. Production beta** | Exercise dynamic paper autonomy under real operating conditions without capital | `RW-13 → W16` | replay parity/idempotency, bounded gap/flood behavior, and ≥4 consecutive market weeks of enabled dynamic-paper soak with fault injection |
| **5. Stabilization and bugfixing** | Freeze features and remove defects exposed by beta | beta findings, open BUG/OPS/SEC residuals, platform/restart/recovery regressions | no open P0/high defects; no unexplained order/reconciliation/audit gaps; clean full matrix and repeated recovery drills |
| **6. Full-scale deployment** | Roll out the operator-owned system in separately authorized authority rungs | ≥6-month paper evidence; `RW-18`; all pre-live gates; then `W17 → W18 → W19` | each rung has its own authorization, soak, adversarial review, reconciliation proof, witness proof, and rollback/kill evidence |

#### Phase 1 — offline development

Start each package from the latest merged `main`. A branch may be developed, locally committed,
and pushed for backup without consuming Actions budget; the hosted workflow is manual-only under
`DEC-09`. This is a cost control, not a waiver of the local matrix or any empirical gate.

The completed implementation spine is `W-21 → RW-15 → W-22 → DEC-03 → W-23 → W-24`;
`W-24` remains evidence-pending because W-23 correctly keeps any absent,
retrospective, or unverifiable semantic claim non-executable. When a later package genuinely needs
unmerged predecessor code, use an explicitly recorded local stack: branch it from the predecessor,
keep only one package under active edit, and do not open the dependent PR until its parent merges;
then rebase/update it onto `main`. A stacked branch is not evidence that its predecessor is
production-ready. Every branch still carries its own tests, register transition, retirement
work, and—when it touches risk, mode, breakers, reconciliation, approval, or gateway
behavior—fresh adversarial review.

#### Phase 2 — online testing and evidence

Open ready branches one at a time in dependency order. Require the local native-Windows +
Ubuntu/WSL2 × Python 3.11/3.12 matrix before merge. Hosted Actions may be dispatched as an
optional diagnostic. Then run the empirical gates that local tests cannot establish:

- `RW-11`: cited in-process EDGAR deltas on a real market day and ≥95% field precision; only
  after that may `RW-12` remove the fallback.
- `RW-19`/`DEC-07`: operator-universe stream/replay/load and IEX/SIP measurements that set
  dynamic-trigger bounds.
- `RW-14`: exchange-timestamp quote progression and a real stale-feed transition to L8
  `watch_only` while awareness remains live.
- `RW-15`: resting, partial, terminal, and lost-response Alpaca paper reconciliation.
- `DEC-05 → RW-18`: only after the operator resolves credential read scope; discovery is
  authenticated but no-capital and places zero orders.
- SEC-11/W-25: `BUG-110`'s bounded rollback-safe trust read merged in PR #48; explicitly choose
  funded S3 or a separately designed/reviewed transparency-log alternative. Only then provision
  the selected independent witness and rerun version-shadow/deletion/retention/rollback evidence.
  Same-host, Discord, git, and R2 remain mirrors under the current contract.

#### Phase 3 — MVP

MVP means scheduled paper reliability, not dynamic or live autonomy. Run the composed service
through two unattended market days at least a week apart. A material event must reach a cited
brief/proposal, Discord approval or rejection, `OrderGateway`, a bid/ask paper outcome, terminal
reconciliation, operator-visible status, and `verify_chain().ok`. Any manual database repair,
stuck resting order, silent feed gap, or missing delivery fails the gate.

#### Phase 4 — production beta

Production beta remains private, single-operator, and paper-only. Implement `RW-13`'s durable
event/outbox arbiter with cron as correctness backstop, then run `W16` for at least four
consecutive market weeks. The dynamic path must actually dispatch; a quiet or feature-off soak
does not count. Inject reconnect gaps, corrections, same-symbol floods, dropped order updates,
calendar rollover, stale quotes, process restart, and witness/vendor failure.

#### Phase 5 — stabilization and bugfixing

Freeze new features. Convert every beta failure into a registered defect with a failing
regression test and severity. Burn down P0/high defects first; include `BUG-082` only through its
separately designed two-phase lock package, never a casual lock split. Repeat supported-platform,
restart/replay, idempotency, kill/disarm, reconciliation, audit-witness, and data-staleness
drills until there are no unexplained divergences. Regressions reopen the owning lifecycle gate.

#### Phase 6 — full-scale deployment

For this project, “full scale” means continuous deployment for the operator's own account—not
public SaaS, managing other people's money, or selling signals. It is still staged:

1. Accumulate at least six calendar months of realistic paper evidence (extend toward 12 months
   when density or confidence is insufficient) and close every applicable pre-live/P0 gate.
2. Deploy `W17` human-gated Robinhood `review_live` only after `RW-18` pins the real contract and
   the operator separately authorizes funding. Every order remains affirmatively approved.
3. Consider `W18` `auto_veto` only after a separately recorded, reconciled `review_live` soak.
4. Consider `W19` `auto_full` only after a separate `auto_veto` soak and fresh adversarial review.

Unknown contracts, late reconciliation, witness failure, stale data, or kill-path uncertainty
blocks promotion and returns the system to the appropriate lower/watch-only posture.

#### One-package delivery loop

Every production package follows this loop:

1. **Claim:** change exactly one [[REGISTER]] row to `claimed`, name the branch
   `work/<id>-<slug>`, and answer the seven intake questions in §5 before editing code.
   Cite that same ID in the Linear issue title or description. Linear tracks the issue;
   it does not allocate the ID (see the Linear section of [[REGISTER]]). Paired
   retirement IDs such as `RW-11`/`RW-12` are claimed/scheduled together but retain separate
   deletion gates.
2. **Pin the baseline:** start from latest merged `main`, record its CI-attestation state, clear
   ambient `COCKPIT_*`, use the short Windows temp path, run targeted tests plus Ruff, and record
   any live/paper fixture the gate will compare against. During a CI-budget pause, local green
   permits development but does not make the branch production-ready.
3. **Write the failure proof:** add a test that fails when the package is unwired or the defect
   is restored. Include the real composition/scheduler path, not only a unit seam. For an
   empirical package, predeclare sample, timestamps, environment, raw-output location, and the
   decision it resolves.
4. **Implement the smallest vertical slice:** schema/storage → service → composition/schedule →
   operator surface. Reuse existing seams and parsers; do not leave a second unscheduled path.
5. **Verify locally:** targeted tests first, then Ruff and the full suite from `app/`. Exercise
   failure, restart/replay, idempotency, stale/unknown input, and cross-platform behavior in
   proportion to the package.
6. **Adversarial gate where authority changes:** any risk, mode, approval, breaker, arming,
   broker, reconciliation, or `OrderGateway` change receives a fresh adversarial-review round
   against the unmerged branch. Reproduce or refute every claim; one reproducible bypass or
   fail-open result blocks the package.
7. **Evidence gate:** run the milestone's paper/live-day/replay measurement. Tests prove shape;
   the recorded evidence proves reachability and behavior. Never substitute a mocked green
   result for a required market-day, broker, witness, latency, or accuracy observation.
8. **PR barrier:** one package, its tests, register update, retirement, and evidence links in one
   reviewable change. The local Windows + Ubuntu/WSL2 × Python 3.11/3.12 matrix must be green.
   Close the ID only after merge. Commit or push only when the operator asks.

#### Stop rules

- If a source contract, entitlement, auth profile, or identifier mapping is unknown, stop at an
  `RW-18`/`RW-19` evidence artifact; do not encode the guess in a production adapter.
- If an event channel cannot be backfilled, name it best-effort and surface the gap; do not let
  it satisfy a zero-loss or fresh-data gate.
- If a package needs more than roughly 1,000 production LOC, a second order path, or a new
  external broker/message system, return to §5 and split/replan it before implementation.
- If a breaker, calendar, witness, reconciliation, or credential check cannot be observed
  through the composed system, the package is not done even if unit tests pass.
- No batch completion changes modes automatically. Ladder promotion is a separate,
  operator-signed action after its evidence window.

---

## 5. How scope gets in

The gate exists because of a measured failure, not a hypothetical one. During the audit, **five
defects were found to have been fixed twice** — once on `rw01-operationalize` and once on `main`
as `BUG-031/032/034/035/039`. Cost: ten merge conflicts, two `add/add` collisions where both
branches wrote the same test file from scratch, and a partial port that left the *actual* bug
(a flat 120 s timeout) unfixed while looking fixed.

We work **ad hoc — whoever picks it up.** That is a legitimate choice for four people and it is
not going to change. It means the fix is a **claim mechanism**, not a process.

### Before writing code for anything larger than a one-line bugfix

One entry in [[REGISTER]], and answers to these:

1. **What changes for the operator?** If nothing, this is a refactor — say so and justify it.
2. **What existing code path does this replace or extend?** Name the file. If "none", say why
   the job is genuinely new.
3. **If it replaces something: which package deletes the old path, and when?** No package ID,
   no merge. *(This is the RW-10 failure: a second path built, the first never retired.)*
4. **What proves it is reachable in production?** Name the test that fails if the wiring is
   removed. Not "the unit tests pass." *(This is the grounding failure: `decision_agent_cwd`
   had zero test references, so a dead security control looked healthy.)*
5. **Which IDs does this claim?** Cross-check the register for an existing claim.
6. **Windows / Ubuntu implications.** Subprocess, path, credential, or temp file — say how for
   native Windows and Ubuntu/WSL2. Note best-effort macOS portability where materially relevant.
7. **Rough size.** Over ~1,000 LOC is more than one package. Split it before starting.

A change that cannot answer 2, 3 and 4 is a scope change. It goes back to this document before
it goes into a branch.

### Rules that came out of the audit

- **No new code path without a scheduled retirement.** Both packages get IDs in the same PR.
- **Every security or fail-closed control ships with a test that fails when the control is
  removed** — through the real call path, with the setting configured. Three controls were
  found that could not fire; two are fixed, one needed a contract decision first.
- **A control whose inputs are not yet defined must not ship looking wired.** Either it fails
  closed and says why, or it is absent and the surface reports it as absent. `/quant/status`
  advertising a check that could never fire is the defect, not the missing check.
- **System tests compose the real thing with fake leaves.** A hand-maintained mirror of a
  production wiring function is a defect, not a fixture.
- **A number or an invariant has exactly one home.** Docs link to it. Thresholds that matter go
  in CI where they can fail, not in prose. The test-count baseline was copy-pasted into seven
  docs and every copy was stale.
- **One invariant namespace.** [[CLAUDE]] holds the canonical 9. `AGENTS.md` points at it.
  Vendored code under `app/filings/` keeps upstream numbering with a `PROVENANCE.md` divergence
  entry; cockpit-owned files use cockpit numbers.

---

## 6. Explicitly out of scope

Named so that "we should also…" has somewhere to land.

- **Enabling live capital now.** Planning and empirically de-risking M6–M8 is in scope; moving
  capital is not. Paper-first overrides everything until the pre-live gates in
  [[security-hardening-backlog]] pass. The research adds its own bar: 6–12 months of realistic
  paper trading, including W16-recorded dynamic-paper operation, before committing money; M6
  defines how the minimum and extension are evaluated.
- **Managing anyone else's money, or selling signals.** Own capital needs no registration; the
  moment either of those changes, adviser/BD rules apply. An auto-trading signal product
  forfeits the publisher's exclusion outright.
- **Any niche other than the one explicitly selected by `DEC-03`.** Strategy selection itself is
  now in scope; additional strategies remain separate future decisions and packages. See §1 and
  [[research/dec03-strategy-selection-2026-08-14]].
- **L2 depth data.** See §3.
- **Autonomy in judgment.** More breadth, better extraction, richer briefs, less babysitting —
  yes. The model deciding what to trade — no, and the research is the reason, not caution.

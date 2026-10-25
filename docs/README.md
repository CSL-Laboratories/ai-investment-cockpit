---
title: Docs index — what to read, in what order, and what to ignore
type: index
status: live
last-verified: 2026-10-15
---

# Docs index

## Accepted replacement product contract — CSL-212

Lucas directed a full Cockpit product reset on 2026-10-16. Start replacement
planning at [docs/replacement/README.md](replacement/README.md). The September
[PRODUCT](../PRODUCT.md) contract is historical for replacement planning; its
research and safety material remains evidence to classify rather than accepted
successor architecture.

The replacement contract is accepted; outcome artifacts and all implementation,
empirical and release gates remain pending. Acceptance does not change current runtime authority, the nine
invariants, credentials, order routing, or live-trading controls. A PR, merge, test
or contract decision is not empirical acceptance.

For shared research, use the [CSL research index](https://github.com/CSL-Laboratories/csl-research/blob/main/INDEX.md),
[Cockpit context](https://github.com/CSL-Laboratories/csl-research/blob/main/knowledge/cockpit/README.md)
and [research-to-delivery guide](https://github.com/CSL-Laboratories/csl-research/blob/main/README.md#connecting-research-and-delivery).
These views are dated explanatory navigation; linked submissions are candidate
evidence. They do not imply that all research has migrated. Check cited revisions
and current product instructions before dependent work. Linear remains the live
source for ownership, status and admission. Product contracts and authenticated
human decisions retain their authority; user-facing and revision-dependent
operational docs and contracts stay in this repository.

Reconciled 2026-10-15 against `origin/main` `4a4308d`, using the same commit and
UTC merge-date rule as the REGISTER and PLAN status pass (CSL-61,
[PR #121](https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/121),
open until merged; merge it before or with this index). Merge dates and
`last-verified` added here are UTC calendar dates from GitHub `mergedAt`
(PR #81 is 2026-09-08 UTC, which is still 2026-09-07 in US Eastern). Screen and
evidence dates already in these sections keep the calendar dates they recorded.
This index records merge state only. It does not close an empirical, paper-day,
witness, selection, or pre-live gate, and it does not claim PASS.

## Cursor Cloud process rebase — RW-33 merged 2026-09-15

[RW-33 rebase](research/agent-swarm-rebase-2026-09-14.md) replaces current Devin
instructions with Cursor Cloud/native work and qualification-gated CLI routes.
It merged in [PR #96](https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/96)
(`cfb4f7c`) on 2026-09-15.
[Start the next Cloud task here](research/cursor-cloud-rdt-handoff-2026-09-14.md)
for the adopted research, development and testing goals; this is not a new Kernel
product or a waiver of PRODUCT/PLAN. [Evidence](evidence/rw33-swarm-rebase-2026-09-14.md)
records offline validation and remaining runtime qualification.
[RW-34/RW-35 Cloud evidence](evidence/rw34-cloud-adapter-2026-09-15.md) records
the admitted json-child fixture hashes and the composed empty-run update.
Both merged in [PR #97](https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/97)
(`8beb271`) on 2026-09-15. That record is not DEC-09 and not empirical PASS.
[RW-36 2026-09-15 screen intake](research/rw36-screen-intake-2026-09-15.md)
appends the Monday-evening GET-only board (cash 1 BOW, still empty/date-not-ready)
and merged in [PR #98](https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/98)
(`5a963ff`) on 2026-09-16.
[RW-37 Tuesday rescreen](research/rw37-screen-intake-2026-09-15.md)
appends the same-day board (supported 32, cash 0) and merged in
[PR #99](https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/99)
(`33156c9`) on 2026-09-16. The 30-identity gate stays closed. No empirical PASS.

## Product contract ratified — 2026-09-09

Lucas adopted [PRODUCT.md](../PRODUCT.md) and its supporting contracts under PD-048,
completing the RW-25 grill on `work/rw25-product-grill`.
That documentation delivery merged in [PR #85](https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/85)
(`e4e9844`) on 2026-09-10.
[The planning record](product-planning.md) preserves exact answers and ratification.
PRODUCT owns product purpose, scope and acceptance; PLAN owns delivery order and
REGISTER owns claims. Current runtime invariants and applicable empirical gates retain
their authority until their separately reviewed amendments or required evidence.

Read the [operating contracts](product-operating-contracts.md) for the shared workflow,
permissions, input limits, routing and recovery. The [tax contract](product-tax-contract.md)
defines selected coverage and sourced calculation/validation requirements.
The [acceptance contract](product-acceptance.md) and [delivery plan](product-delivery.md)
connect useful research and real paper operation to release evidence and bounded work,
including explicit reconciliation with older scope and current gates. These adopted
contracts define required work; they do not claim implementation or a passed gate.

## RW-23 / RW-24 audit repairs and reliability — merged 2026-09-08

BUG-146 through BUG-158 are fixed in commit slices, alongside a measured triage
optimization and full Discord validation coverage. Independent authority review and
all four DEC-09 cells pass against implementation `fa3cc1c`, including repairs for the
two integration regressions found by the first completed matrix. Windows 3.11/3.12:
3,107 passed / 4 skipped each; Ubuntu WSL2 3.11/3.12: 3,105 passed / 6 skipped each.
All 233 required regression cases pass in every cell. Final evidence:
[[evidence/rw23-rw24-local-matrix-2026-09-08/README]].
See [[evidence/rw23-audit-repair-2026-09-07]],
[[evidence/rw24-reliability-2026-09-07]], and
[[evidence/rw23-rw24-local-matrix-2026-09-08/README]] for changes and evidence.
The frozen baseline findings remain in [[evidence/astra-audit-2026-09-07/AUDIT]].
Branch head `f3f315f` on `codex/audit-reliability-2026-09-07` (one docs commit after
`fa3cc1c`) merged with RW-24 in [PR #81](https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/81)
(`5d50b25`) on 2026-09-08. The head and the merge commit are on main. No empirical
gate is closed.

## RW-22 review remediation — 2026-09-07

The operator-authorized follow-up to local `work/bugfix-audit-2026-09-07` repairs five
independently reproduced review findings. See [[evidence/rw22-review-remediation-2026-09-07]]
for the exact changes, regression and adversarial evidence, and local platform results.
All four DEC-09 cells passed against implementation commit `cacaff4`, including all
89 new regression cases in each cell.
Merged in PR #80 at `c231dc2`; no selection, witness, paper or live gate is satisfied by this work.

## Operator gate audit — 2026-09-06

The independent audit was committed on `codex/audit-gates-2026-09-06`; it is review material,
not a deployment or selection commitment. Start with
[[evidence/astra-gate-delivery-2026-09-06]] for all requested findings and remaining dependencies;
then use
[[evidence/rw20-repair-notes-2026-09-06]],
[[evidence/rw21-repair-notes-2026-09-06]], and
[[evidence/rw20-matrix-isolation-2026-09-06]] for exact repair and validation scope.
The original findings remain in [[evidence/astra-audit-2026-09-05/AUDIT-2026-09-05]].
The selection-to-order map, independent-witness plan and cohort/value review are linked from
the repair record. Missing prospective observations and independent labels remain open gates.

**Start here. Document ownership:**

| | |
|---|---|
| **[PRODUCT](../PRODUCT.md)** | Product purpose, scope and acceptance, with its adopted operating, tax and acceptance contracts. |
| **[[PLAN]]** | Current delivery order, package lifecycle and scope reconciliation. Its RW-25 section supersedes conflicting older MVP/strategy/order text while retaining applicable evidence gates. |
| **[[REGISTER]]** | One ID space, one claim board. **Claim work here before writing code.** |

Then [[CLAUDE]] at the repo root for the 9 invariants and the session workflow — that is the
canonical invariant list, and nothing else restates it.

## Repository snapshot recorded 2026-08-24

- `DEC-09` now makes the local native-Windows + Ubuntu/WSL2 × Python 3.11/3.12 matrix the
  release-blocking platform gate. All four cells passed on the PR #53 source tree, which is
  byte-identical to the tree of clean merged main `ebda9b6691cfcfc72b76056d0190768e5f14cd66`;
  macOS is best-effort, hosted Actions are optional manual diagnostics, and Hetzner is not part of
  current validation. Historical entries below retain their then-accurate six-job/billing
  language, but those missing hosted/macOS runs are no longer current blockers. This policy does
  not satisfy any independent-witness gate. See
  [[evidence/local-platform-matrix-2026-08-24]].
- The bounded RW-11 Company Facts session completed on earlier clean main
  `46c7c812eccd476c4578dbef59200f5249c26db3`: all 11 CIKs and 31,735,013 response bytes were
  captured and verified, with zero supported post-cutoff 10-K/10-Q accessions. This proves
  reachability only. The independently completed four-row 8-K seed scored
  8/8 semantic fields correct (100%), but is too small and narrow to satisfy the ≥30-row,
  ≥3-issuer, periodic/numeric population gate. RW-11 remains open for a supported prospective
  periodic accession, numeric replay, and a cited material delta. A separate text-population
  plan is now prospectively sealed and Discord-server-witnessed before its fixed 2026-08-25 through
  2026-11-24 observation window. Its offline collector merged in PR #53 and has not made future SEC
  requests; see [[evidence/rw11-prospective-text-population-2026-08-24]].

- The first dated W-22 bring-up record is
  [[evidence/w22-paper-mvp-2026-08-14]]. It records a healthy `read_only` process, all 11 quote
  sequences advancing, valid audit/performance state, zero feed errors, and the exact empirical
  gates that remain open. It is evidence, not a paper-MVP completion claim.
- PR #36 merged the W-22 bring-up repairs: `dab983b` (legacy risk-verdict migration) and
  `18e3cd9` (post-response quote-time validation). `BUG-102`/`BUG-103` hydrate daily bars at startup
  and send Alpaca the bounded historical interval its API requires. `BUG-104` pins audit rows
  and their signed high-water to one SQLite read snapshot so a concurrent ingest commit cannot
  create a false audit-failure alert. A late-start live check populated quotes and metrics for
  all 11 symbols. Those repairs now have the same green six-job Ubuntu/Windows/macOS × Python
  3.11/3.12 matrix as PR #36.
- The operator selected constrained cash-only, long-target merger arbitrage for `DEC-03` on
  2026-08-14. `W-23` merged in PR #36; its immutable evidence,
  conservative calibration, and activation workflow are in [[runbooks/w23-cash-merger-edge]].
  Three independent review passes found additional merge blockers; local remediation now closes
  cache/provenance, audit/database and raw-filesystem snapshots, EDGAR baseline/completeness and
  restart freshness, calibration chronology, and composed order races. Operator-authored cohort
  labels and deal terms remain deliberately non-executable unless W-24's independent semantic
  verifier can re-derive them from signed sources and validate a server-witnessed prospective
  cohort. The first PR #36 matrix passed five of six
  jobs; Windows 3.11 exposed `BUG-105`, a transient Parquet staging-handle cleanup failure. Its
  byte-image read/retry repair passed the full replacement Ubuntu/Windows/macOS × Python
  3.11/3.12 matrix. `W-24` merged in PR #38: the production
  graph now composes a model-free verifier, exact-message Discord witness, prospective manifest,
  source-derived labels/outcomes/deal terms, complete-filing-set checks, and a final transactional
  recheck. Real prospective evidence and external-witness configuration remain pending, so W-23
  still returns no edge. All six PR #38 matrix jobs stopped before a
  step with GitHub's payment/spending-limit annotation; that is missing attestation, not a code
  failure. The combined BUG-106/W-24 Windows integration
  gate is 2,274 passed / 1 skipped with Ruff clean. See [[evidence/w23-cash-merger-edge-2026-08-14]],
  [[evidence/w24-merger-semantic-verifier-2026-08-17]], and
  [[runbooks/w24-merger-semantic-verifier]].
- The 2026-08-18 W-24 intake exposed `BUG-108`: one accession could not be both the original
  semantic source and a later current EDGAR baseline. PR #42's merged schema-v2 repair witnesses a
  complete ordered signed source set plus an explicit current accession, derives prospective and
  calibration eligibility from that frozen set, and rejects legacy/incomplete/tampered authority.
  The current `DEC-09` local matrix is green; no real cohort or edge authority has been created.
  See [[evidence/w24-prospective-intake-2026-08-18]].
- The post-merge W-24 continuation screened 65 discovery names. Only six had a currently supported
  SEC feed head, and none of their 64 supported 2026 primary filings passed the eligibility parser.
  An official CRNX announcement 8-K proves `BUG-109`: common authoritative cash-merger phrasing is
  rejected by the pinned grammar. PR #43's merged repair now passes the untouched source and
  adversarial subject-switch cases, with 2,311 passed / 1 skipped and Ruff clean on Windows. It
  remains evidence-pending because held-out precision is absent. The six hosted jobs ran zero
  steps, a historical gap superseded as a blocker by `DEC-09`. A strict post-merge rescreen found
  only one unsigned semantic candidate (CRNX), below the
  ≥30 minimum; its primary source set also lacks exact dates required for current-target authority.
- `BUG-106` merged in PR #37. Signed audit time is now the EDGAR baseline timestamp authority,
  with a bounded one-time legacy repair. A real composed `read_only` run repaired 11/11 legacy rows,
  ingested one filing/four deltas, completed the next 11/11 scheduled poll cleanly, and stopped
  with a valid audit chain at sequence 10,040 and zero proposals/orders. Windows: 2,248 passed /
  1 skipped; Ruff clean. The PR #37 six-job matrix was running at merge time. See
  [[evidence/bug106-edgar-baseline-timestamp-2026-08-17]].
- The `RW-19` evidence update merged in PR #39 and covers two completed 11-symbol IEX/SIP
  sessions and three live IEX stream samples. A 2026-08-18 deliberate reconnect added a real
  REST-recoverable gap, and an isolated 1,114-frame replay measured the single writer at only
  74.67 one-event writes/s plus a 236,637-character raw triage packet. This confirms that bounded
  coalescing is required before `RW-13`. A tested two-segment news reconnect collector also
  completed real 10-minute and 30-minute after-hours runs, but both contained zero WebSocket and
  REST identities. A separately authorized 2026-08-24 ten-minute regular-session reconnect also
  returned zero identities on both surfaces. These runs prove apparatus reachability, not
  correction or reconnect identity behavior. Broader regimes and a non-empty news
  correction/reconnect sample remain open.
  See [[evidence/rw14-rw19-live-evidence-2026-08-18]].
- The stopped-system preflight is green for controlled background `read_only` bring-up: loopback
  port free, HALT absent, audit chain valid, zero proposal/risk/approval/order rows, 11 awareness
  symbols, required feed/operator credentials present, and operational Discord delivery channels
  configured. The private Discord audit channel is configured and verified as a W-24 chronology
  mirror, but the merger watchlist remains empty and independent W-25 WORM authority is absent,
  so expected edge, SEC-11, and paper opening orders correctly remain unavailable.
- The 2026-08-18 zero-cost SEC-11 assessment rejects the existing Hetzner VPS and Cloudflare R2
  as authoritative W-25 substitutes. Same-host storage shares the rollback domain; R2 does not
  expose the current adapter's required AWS Compliance/version-retention contract. Sigstore Rekor
  is a research candidate only. `BUG-110`'s bounded-read remediation merged in PR #48: reverse-
  sequence v2 keys put the highest claim first, one capped version page must prove the complete
  candidate group, and retained history no longer changes request counts. The full Windows gate
  is 2,321 passed / 1 skipped with Ruff clean. All six hosted matrix jobs executed zero steps under
  GitHub's payment restriction; that is missing historical hosted attestation, not a code failure.
  Independent witness evidence, a provider/cost choice, and real Object Lock evidence remain
  mandatory W-25 gates. See [[evidence/bug110-bounded-witness-read-2026-08-18]].
- GitHub's 2026-08-17 incident interrupted publication. BUG-106, W-24, and the RW-19 evidence
  subsequently merged in PRs #37–#39, the handoff in PR #40, and W-25 in PR #41. This
  documentation reconciliation preserves the stopped-system preflight and recovery record in
  [[evidence/github-outage-session-handoff-2026-08-17]]. Do not start `RW-13` or a new production
  package while the named evidence gates remain open.
- The old `inverse-cramer`, `samir`, `fix/core-secrets-gitignore`, and local `rw01-operationalize`
  branches are stale history, not merge candidates.
- RW-14 merged with 2,121 passed / 1 skipped locally on Windows and Ruff clean. All six PR #31
  matrix jobs stopped before running a step because GitHub reported failed recent account
  payments or a spending limit that needs to be increased. That is missing attestation, not a
  repository test failure.
- `RW-17` and BUG-041–045, BUG-047–051, and BUG-098–105 are merged. `RW-11` and
  `RW-19` have merged tooling but still need empirical evidence. RW-14 is merged; its live
  progression/stale-transition drill passed 2026-08-18 and its current local platform gate is
  green. W-21 merged in PR #33;
  expected edge remains intentionally unavailable and fail-closed until W-24's real prospective
  semantic evidence exists. RW-15 and W-22 are merged in PRs #34/#35; W-23
  and the bring-up fixes are merged in PR #36. The PR #36 tree passes 2,240 tests / 1 skipped
  locally with Ruff clean and its complete six-job matrix is green. Real
  Alpaca reconciliation, and the two market-day W-22 proof remain evidence gates;
  [[runbooks/w22-paper-mvp]] is the operator handoff.
- PR #50 merged the first RW-11 filing-precision evidence tool at
  `eb069fadddc45fa0a702c67b0e00edf4f94a7e31`; all six hosted jobs again ran zero steps under the
  payment/spending-limit restriction. The separate `work/rw11-companyfacts-capture` package is
  local: it captures exact Company Facts bytes for a predeclared CIK census through the existing
  fair-access client and permanently reports no authority/open gate. The real census completed
  quietly; numeric replay and a meaningful label population remain open. The later text-population
  chronology witness is recorded separately; see [[evidence/rw11-companyfacts-capture-2026-08-18]]
  and [[evidence/rw11-prospective-text-population-2026-08-24]].
- The active lifecycle is defined in [[PLAN]]: offline development → online testing/evidence →
  paper MVP → production beta → stabilization/bugfixing → staged full-scale deployment.

## Contributor handoff

1. Fetch `origin/main`; do not continue one of the stale branches named above.
2. Read [[PLAN]] for phase/dependency context and [[REGISTER]] for the exact open row.
3. Claim one row and one fresh branch. Keep one production-code package active at a time.
4. Add a reachability/regression test, run targeted tests, the full local suite, and Ruff using
   the platform instructions in `AGENTS.md`; order-path changes also require adversarial review.
5. Record whether evidence is local-platform, live-market, paper-broker, independent-witness, or deployment
   evidence. Those are not interchangeable.
6. Run the four-cell local matrix required by `DEC-09` before merge. Hosted Linux `linux-gates`
   is a required PR diagnostic and does not replace that matrix or any required online, broker,
   witness, or deployment gate.

---

## How to read a status

Every doc carries `status:` in its frontmatter. Four values, and they mean exactly this:

| Status | Read it when | Plan from it? |
|---|---|---|
| `live` | It is current | **Yes** |
| `reference` | You need stable background — a contract, a spec, a research input | No — it describes, it does not schedule |
| `superseded` | You want the history behind a decision | **No.** [[PLAN]] replaced it |
| `archive` | Same, and it has moved to `archive/` | **No** |

This vocabulary is closed. Before this cleanup there were seven overlapping values
(`active`, `draft`, `verified`, `built`, `live`, `under-review`, `archive`) across 25 files, so
a reader could not tell which of eight "plans" was current — which is exactly how the same five
defects got fixed twice on two branches.

---

## Live — work from these

| Doc | What it owns |
|---|---|
| [[PLAN]] | MVP, milestones, intake gate, scope boundaries |
| [[REGISTER]] | Every open ID and who has it |
| [[adr/README]] | Architecture decision records. Written when a Linear `decision` issue closes; cite any `DEC-xx` from [[REGISTER]] |
| [[security-hardening-backlog]] | The **SEC-series** write-ups. Claims go in [[REGISTER]] |
| [[quant-engine]] | The **QT-series** — `app/quant/` |
| [[rw01-live-smoke]] | The live bring-up runbook. Follow it verbatim on a real market day |
| [[runbooks/w22-paper-mvp]] | Paper background/Discord bring-up, safety holds, and the two-day evidence checklist |
| [[runbooks/w23-cash-merger-edge]] | Deterministic cash-merger evidence schemas, conservative calibration, and separate paper-activation gate |
| [[runbooks/w24-merger-semantic-verifier]] | Prospective semantic-authority contract, external witness, cohort workflow, and fail-closed activation boundary |
| [[runbooks/w25-worm-audit-witness]] | Administrator-safe SEC-11 trust split, bounded S3 proof contract, activation hold, provisioning, and required delete/shadow/rollback evidence |
| [[evidence/w22-paper-mvp-2026-08-14]] | Dated first-day status, live-feed observations, and unclosed evidence gates |
| [[evidence/w23-cash-merger-edge-2026-08-14]] | W-23 local implementation, attack→fix findings, tests, and unclosed activation gates |
| [[evidence/w24-merger-semantic-verifier-2026-08-17]] | W-24 implementation, adversarial findings, local gates, and remaining prospective evidence |
| [[evidence/w25-admin-discord-worm-witness-2026-08-17]] | Administrator-safe Discord channel proof, WORM adapter boundary, fail-closed readback, and remaining real-cloud evidence |
| [[evidence/bug106-edgar-baseline-timestamp-2026-08-17]] | BUG-106 deterministic repair contract and bounded composed live confirmation |
| [[evidence/github-outage-session-handoff-2026-08-17]] | Exact local branch/commit inventory, stopped-system preflight, merge order, and paste-ready next-session prompt |
| [[evidence/rw11-live-poll-2026-08-18]] | RW-11 live 11-CIK reachability, clean SNDK baseline, audit/safety result, and still-open precision gate |
| [[evidence/rw11-precision-label-sheet-2026-08-18]] | Unscored four-delta human-review seed plus separate 4/4 mechanical citation check |
| [[evidence/rw11-companyfacts-capture-2026-08-18]] | RW-11 prospective exact-byte Company Facts capture contract, local gates, and bounded online-test handoff |
| [[evidence/rw11-prospective-text-population-2026-08-24]] | Witnessed outcome-independent RW-11 text-population plan, Discord receipt, offline collector contract, and future fixed-window handoff |
| [[evidence/local-platform-matrix-2026-08-24]] | DEC-09 local Windows + Ubuntu/WSL2 Python 3.11/3.12 policy, exact matrix results, external artifact hash, and same-host limitations |
| [[evidence/csl221-dec09-qualification-matrix-2026-10-17]] | CSL-221 replacement qualification cells. Historical DEC-09 rows stay historical. Not a release decision |
| [[evidence/csl221-unresolved-blockers]] | CSL-221 blockers still owned by Lucas or an independent verifier |
| [[evidence/rw14-rw19-live-evidence-2026-08-18]] | RW-14 stale-feed drill and RW-19 reconnect/backfill, news, and measured-load results |
| [[evidence/rw19-plan-bound-news-reconnect-2026-08-24]] | Claimed offline RW-19 plan-bound news-reconnect hardening; no new capture or authority |
| [[evidence/w24-prospective-intake-2026-08-18]] | GET-only candidate screens, merged BUG-108 repair, and fail-closed BUG-109 parser finding |
| [[evidence/bug109-w24-real-sec-grammar-2026-08-18]] | BUG-109 real-source replay, adversarial parser repair, local gates, and remaining authority blockers |
| [[evidence/w24-post-bug109-rescreen-2026-08-18]] | Strict post-PR #43 SEC-head/source-set rescreen and retained cohort/current-target refusals |
| [[evidence/bug110-bounded-witness-read-2026-08-18]] | BUG-110 reverse-sequence S3 proof, fixed operation counts, race/ambiguity failures, and remaining activation gates |
| [[accounts-and-keys]] | Credential provisioning |
| [[low-priv-agent-account]] | The SEC-05 low-privilege account |

## Reference — cited, not planned from

| Doc | Why it is still load-bearing |
|---|---|
| [[build-scaffold-ultracode]] | The **WP-series contract register**. Reads like a disposable build artifact; **43 files in `app/` cite it**. Do not move or renumber |
| [[production-work-packages]] | The **W-series** definitions — where `W10`/`W11`/`W12`/`W20` in code comments resolve |
| [[refactor-plan]] | Historical RW-series rationale and detailed subsystem context. Current scheduling and status moved to [[PLAN]]/[[REGISTER]] |
| [[ultracode-build-playbook]] | The adversarial-review pattern, still the house rule for any order-path change |
| [[security-architecture]] | The security model |
| [[prompt-injection-defense]] | The untrusted-content threat model — M2/M3 are cited throughout `ingest/` and `workflows/` |
| [[research/llm-hybrid-trading]] | Viability research. Confirms the extraction-not-prediction architecture; sets the cost and accuracy bars in [[PLAN]] §2 |
| [[research/autonomy-blockers-and-unblockers-2026-08-12]] | Dated evidence for the paper-reliability/dynamic-paper/live-broker distinction, Robinhood/MCP unknowns, event-trigger design, empirical spikes, and hold points in [[PLAN]] |
| [[research/rw19-event-source-characterization-2026-08-12]] | RW-19 measurement ledger: dated entitlement probes, IEX/SIP coverage, capture manifests, unresolved stream/replay/load gates, and DEC-07 status |
| [[research/sec11-zero-cost-witness-feasibility-2026-08-18]] | Same-host/R2 rejection, Rekor/home-device research candidates, AWS cost boundary, and `BUG-110` finding/remediation |
| [[research/dec03-strategy-selection-2026-08-14]] | Closed DEC-03 comparison, selection, and the fail-closed W-23 edge-source contract |
| [[research/agent-swarm-orchestration-2026-09-10]] | 2-step research/dev swarm shape (Head → domain middles → Devin leaves). Host/runtime amended 2026-09-11 |
| [[research/agent-swarm-cloud-host-2026-09-11]] | Owner direction: swarm host is Cursor Cloud (not sentry); Devin SWE-2 at 30–40 agents for research/dev/review; market/live testing stays owner-operated |
| [[research/agent-swarm-pipeline-2026-09-11]] | SWE-2 volume + first-gate review; Muse Spark 1.3 second-gate synthesis; Grok 4.6 xhigh Head; Lucas merges |
| [[research/agent-swarm-smoke-2026-09-11]] | Measured 2026-09-11 pipeline smoke: hashes, spawn argv, Head steer of Muse nits; no empirical PASS |
| [[research/rw26-slice-a-inventory-2026-09-11]] | RW-26 slice A inventory swarm run: hashes, Head-frozen proposed shortlist, no activation / no PASS |
| [[research/rw27-feasibility-freeze-2026-09-11]] | RW-27 freeze: versioned contracts, fail-closed qualify block, no activation / no PASS |
| [[research/rw28-slice-a-evaluation-2026-09-11]] | RW-28 metrics + evaluation: discrete protocol, 0 qualify, no activation / no PASS |
| [[research/rw29-case-inspection-2026-09-11]] | RW-29 case inspection: six failure classes, missing decisions, no activation / no PASS |
| [[research/rw30-measurement-protocol-2026-09-12]] | RW-30 pre-measurement protocol: sealed rules, no measurement / no PASS |
| [[research/rw34-cloud-adapter-2026-09-15]] | RW-34 Cloud CLI inventory and admitted json-child profile; merged with RW-35 in PR #97 (`8beb271`) on 2026-09-15; no empirical PASS |
| [[research/rw35-empty-run-update-2026-09-15]] | RW-35 composed empty-run research update from retained RW-29/RW-31 inputs; merged with RW-34 in PR #97 (`8beb271`) on 2026-09-15; no empirical PASS |
| [[research/rw36-screen-intake-2026-09-15]] | RW-36 third GET-only screen row: cash 1 BOW, still empty/date-not-ready; merged in PR #98 (`5a963ff`) on 2026-09-16; no PASS |
| [[research/rw37-screen-intake-2026-09-15]] | RW-37 fourth GET-only screen row: Tue 2026-09-15, supported 32, cash 0; merged in PR #99 (`33156c9`) on 2026-09-16; the 30-identity gate stays closed; no PASS |
| [[research-report]] · [[research-supplement]] · [[upside-and-roi]] | Original research inputs |
| [[moving-targets-refresh-2026-07-06]] | A dated verification snapshot |

## Superseded — history only

[[capability-expansion-master-plan]] · [[draft-discord-control-plane]] ·
[[enhancements-and-additions]] · [[cockpit-plan-original]]

Kept in place rather than moved to `archive/` **only because production code cites them.** Each
carries a banner saying what replaced it.

## Archive

Five documents, ~275 KB, **zero production-code references** — see [[archive/README]]. Mostly
orchestration scaffolding for a multi-agent build session that has finished: prompts and
sequencing, not design.

---

## The rule that keeps this index honest

From [[PLAN]] §5: **a plan that synthesizes other plans and does not retire them just becomes
the next document.** Any new doc claiming to supersede another carries a `supersedes:` list in
its frontmatter, and the docs it names get their status changed **in the same PR**.

The test for "can this be archived" is not how the doc is framed — it is **whether code cites
it**. [[build-scaffold-ultracode]] looks like throwaway build scaffolding and is load-bearing
for 43 files; [[production-playbook]] is 143 KB of careful planning and is referenced by
nothing. Framing is not evidence; the citation graph is.

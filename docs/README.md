---
title: Docs index — what to read, in what order, and what to ignore
type: index
status: live
last-verified: 2026-08-17
---

# Docs index

**Start here. Two documents matter most:**

| | |
|---|---|
| **[[PLAN]]** | What we are building, the MVP definition, milestone/development order, package delivery loop, and how new scope gets in. If another doc disagrees with it, that doc is stale. |
| **[[REGISTER]]** | One ID space, one claim board. **Claim work here before writing code.** |

Then [[CLAUDE]] at the repo root for the 9 invariants and the session workflow — that is the
canonical invariant list, and nothing else restates it.

## Current repository snapshot — 2026-08-17

- The first dated W-22 bring-up record is
  [[evidence/w22-paper-mvp-2026-08-14]]. It records a healthy `read_only` process, all 11 quote
  sequences advancing, valid audit/performance state, zero feed errors, and the exact empirical
  gates that remain open. It is evidence, not a paper-MVP completion claim.
- `fix/w22-paper-bringup-bugs` contains `dab983b` (legacy risk-verdict migration) and `18e3cd9`
  (post-response quote-time validation). `BUG-102`/`BUG-103` now hydrate daily bars at startup
  and send Alpaca the bounded historical interval its API requires. `BUG-104` pins audit rows
  and their signed high-water to one SQLite read snapshot so a concurrent ingest commit cannot
  create a false audit-failure alert. A late-start live check populated quotes and metrics for
  all 11 symbols; the complete local suite is now 2,186 passed / 1 skipped with Ruff clean, and
  cross-platform CI remains pending.
- The operator selected constrained cash-only, long-target merger arbitrage for `DEC-03` on
  2026-08-14. `W-23` is merged through PR #36; its immutable evidence,
  conservative calibration, and activation workflow are in [[runbooks/w23-cash-merger-edge]].
  Three independent review passes found additional merge blockers; local remediation now closes
  cache/provenance, audit/database and raw-filesystem snapshots, EDGAR baseline/completeness and
  restart freshness, calibration chronology, and composed order races. Operator-authored cohort
  labels and deal terms remain deliberately non-executable until a separate independent semantic
  verifier is implemented and adversarially reviewed. The first PR #36 matrix passed five of six
  jobs; Windows 3.11 exposed `BUG-105`, a transient Parquet staging-handle cleanup failure. Its
  byte-image read/retry repair passed the full replacement Ubuntu/Windows/macOS × Python
  3.11/3.12 matrix. Real empirical evidence remains pending, so W-23 stays fail-closed. See
  [[evidence/w23-cash-merger-edge-2026-08-14]].

- PR #36 is merged on `origin/main`. `BUG-106` is committed locally on
  `work/bug106-edgar-baseline-timestamp`: production EDGAR baseline timestamps now take their
  authority from the signed audit event, and narrowly bounded legacy rows reconcile once through
  a signed repair. A real `read_only` composed run repaired all 11 legacy rows, ingested one
  filing with four deltas, then completed a clean 11/11 retry with the audit chain valid and zero
  proposals, approvals, or orders. Cross-platform CI and branch publication remain pending; see
  [[evidence/bug106-edgar-baseline-timestamp-2026-08-17]].

- The old `inverse-cramer`, `samir`, `fix/core-secrets-gitignore`, and local `rw01-operationalize`
  branches are stale history, not merge candidates.
- RW-14 merged with 2,121 passed / 1 skipped locally on Windows and Ruff clean. All six PR #31
  matrix jobs stopped before running a step because GitHub reported failed recent account
  payments or a spending limit that needs to be increased. That is missing attestation, not a
  repository test failure.
- `RW-17` and BUG-041–045, BUG-047–051, BUG-098, and BUG-099 are merged. `RW-11` and
  `RW-19` have merged tooling but still need empirical evidence. RW-14 is merged; its live
  entitlement/progression/stale-transition evidence remains pending. W-21 merged in PR #33;
  expected edge remains intentionally unavailable and fail-closed until a deterministic strategy
  source is separately selected and reviewed. RW-15 and W-22 are merged in PRs #34/#35. The
  current bring-up tree passes 2,185 tests / 1 skipped with Ruff clean. Cross-platform CI, real
  Alpaca reconciliation, and the two market-day W-22 proof remain evidence gates;
  [[runbooks/w22-paper-mvp]] is the operator handoff.
- The active lifecycle is defined in [[PLAN]]: offline development → online testing/evidence →
  paper MVP → production beta → stabilization/bugfixing → staged full-scale deployment.

## Contributor handoff

1. Fetch `origin/main`; do not continue one of the stale branches named above.
2. Read [[PLAN]] for phase/dependency context and [[REGISTER]] for the exact open row.
3. Claim one row and one fresh branch. Keep one production-code package active at a time.
4. Add a reachability/regression test, run targeted tests, the full local suite, and Ruff using
   the platform instructions in `AGENTS.md`; order-path changes also require adversarial review.
5. Record whether evidence is local, cross-platform CI, live-market, paper-broker, or deployment
   evidence. Those are not interchangeable.
6. During an Actions-budget pause, local work and ordinary branch pushes may continue, but do
   not mark the package production-ready or merge it as a dependency until its required CI and
   online gates run.

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
| [[security-hardening-backlog]] | The **SEC-series** write-ups. Claims go in [[REGISTER]] |
| [[quant-engine]] | The **QT-series** — `app/quant/` |
| [[rw01-live-smoke]] | The live bring-up runbook. Follow it verbatim on a real market day |
| [[runbooks/w22-paper-mvp]] | Paper background/Discord bring-up, safety holds, and the two-day evidence checklist |
| [[runbooks/w23-cash-merger-edge]] | Deterministic cash-merger evidence schemas, conservative calibration, and separate paper-activation gate |
| [[evidence/w22-paper-mvp-2026-08-14]] | Dated first-day status, live-feed observations, and unclosed evidence gates |
| [[evidence/w23-cash-merger-edge-2026-08-14]] | W-23 local implementation, attack→fix findings, tests, and unclosed activation gates |
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
| [[research/dec03-strategy-selection-2026-08-14]] | Closed DEC-03 comparison, selection, and the fail-closed W-23 edge-source contract |
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

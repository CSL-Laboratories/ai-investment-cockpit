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
  recheck. Real prospective evidence, external-witness configuration, and cross-platform CI
  remain pending, so W-23 still returns no edge. All six PR #38 matrix jobs stopped before a
  step with GitHub's payment/spending-limit annotation; that is missing attestation, not a code
  failure. The combined BUG-106/W-24 Windows integration
  gate is 2,274 passed / 1 skipped with Ruff clean. See [[evidence/w23-cash-merger-edge-2026-08-14]],
  [[evidence/w24-merger-semantic-verifier-2026-08-17]], and
  [[runbooks/w24-merger-semantic-verifier]].
- The 2026-08-18 W-24 intake exposed `BUG-108`: one accession could not be both the original
  semantic source and a later current EDGAR baseline. The isolated schema-v2 repair witnesses a
  complete ordered signed source set plus an explicit current accession, derives prospective and
  calibration eligibility from that frozen set, and rejects legacy/incomplete/tampered authority.
  Merge and cross-platform CI remain pending; no real cohort or edge authority has been created.
  See [[evidence/w24-prospective-intake-2026-08-18]].
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
  coalescing is required before `RW-13`; broader regimes and news/correction identity remain open.
  See [[evidence/rw14-rw19-live-evidence-2026-08-18]].
- The stopped-system preflight is green for controlled background `read_only` bring-up: loopback
  port free, HALT absent, audit chain valid, zero proposal/risk/approval/order rows, 11 awareness
  symbols, required feed/operator credentials present, and operational Discord delivery channels
  configured. The private Discord audit channel is configured and verified as a W-24 chronology
  mirror, but the merger watchlist remains empty and independent W-25 WORM authority is absent,
  so expected edge, SEC-11, and paper opening orders correctly remain unavailable.
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
  entitlement/progression/stale-transition evidence remains pending. W-21 merged in PR #33;
  expected edge remains intentionally unavailable and fail-closed until W-24's real prospective
  semantic evidence exists. RW-15 and W-22 are merged in PRs #34/#35; W-23
  and the bring-up fixes are merged in PR #36. The PR #36 tree passes 2,240 tests / 1 skipped
  locally with Ruff clean and its complete six-job matrix is green. Real
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
| [[runbooks/w24-merger-semantic-verifier]] | Prospective semantic-authority contract, external witness, cohort workflow, and fail-closed activation boundary |
| [[runbooks/w25-worm-audit-witness]] | Administrator-safe SEC-11 trust split, S3 Object Lock provisioning, and required delete/shadow/rollback evidence |
| [[evidence/w22-paper-mvp-2026-08-14]] | Dated first-day status, live-feed observations, and unclosed evidence gates |
| [[evidence/w23-cash-merger-edge-2026-08-14]] | W-23 local implementation, attack→fix findings, tests, and unclosed activation gates |
| [[evidence/w24-merger-semantic-verifier-2026-08-17]] | W-24 implementation, adversarial findings, local gates, and remaining prospective evidence |
| [[evidence/w25-admin-discord-worm-witness-2026-08-17]] | Administrator-safe Discord channel proof, WORM adapter boundary, fail-closed readback, and remaining real-cloud evidence |
| [[evidence/bug106-edgar-baseline-timestamp-2026-08-17]] | BUG-106 deterministic repair contract and bounded composed live confirmation |
| [[evidence/github-outage-session-handoff-2026-08-17]] | Exact local branch/commit inventory, stopped-system preflight, merge order, and paste-ready next-session prompt |
| [[evidence/rw11-live-poll-2026-08-18]] | RW-11 live 11-CIK reachability, clean SNDK baseline, audit/safety result, and still-open precision gate |
| [[evidence/rw11-precision-label-sheet-2026-08-18]] | Unscored four-delta human-review seed plus separate 4/4 mechanical citation check |
| [[evidence/rw14-rw19-live-evidence-2026-08-18]] | RW-14 stale-feed drill and RW-19 reconnect/backfill, news, and measured-load results |
| [[evidence/w24-prospective-intake-2026-08-18]] | GET-only real candidate readiness screen and BUG-108 prospective source-set blocker |
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

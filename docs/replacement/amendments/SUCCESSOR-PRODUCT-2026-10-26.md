# Proposed amendment: Cockpit successor product, v2 (drafted 2026-10-26)

**Owner direction:** recorded in CSL-212. This 2026-10-26 proposal is filed under
[CSL-543](https://linear.app/csl-laboratories/issue/CSL-543), the CP0-v2 twin of the
Recursion RP0-v2 issue [CSL-542](https://linear.app/csl-laboratories/issue/CSL-542);
parent [CSL-511](https://linear.app/csl-laboratories/issue/CSL-511); umbrella
[CSL-493](https://linear.app/csl-laboratories/issue/CSL-493).
**Status:** proposed amendment (v2). This amendment is Lucas-authored, so Lucas's
exact-digest receipt is the acceptance path.

This file is a **proposed amendment**. It supersedes
`docs/replacement/amendments/SUCCESSOR-PRODUCT-2026-10-24.md` (the v1 proposal) only where
this file says so, and it keeps the v1 clauses it does not name. It does not replace the
last accepted bytes, which remain `docs/replacement/SUCCESSOR-PRODUCT.md` at SHA-256
`6e5f0d0cad102077fd058aac4970ac0eb31fd6f6e952f948c43edaa9fe0c919e`, bound by CSL-212
comment `25c50b43-6de4-4f6d-9652-47a649ccc5ae` and `docs/replacement/program.json`
(lines 20 and 69 to 70).

**The `program.json` pins stay where they are.** `program.json`,
`tools/check_replacement_program.py` (lines 82 and 88) and the replacement-program digest
gate keep pointing at the 2026-10-16 accepted bytes until Lucas posts a new exact-digest
receipt over this file and a reviewed promotion moves the pins. Moving the pins without
that receipt would falsely claim acceptance. This file does not edit `program.json`, the
checker or the accepted `SUCCESSOR-PRODUCT.md`.

A GitHub approval, a merge, green CI, a Linear status or this file's own text is not a
receipt. The receipt is Lucas's own unprefixed Linear comment citing the exact digest
(decision 5, relayed on CSL-522 comment `56b36b45`). Lucas's decisions below are an
agent's relay of his words on CSL-522 comments `9f3f7fc1-15ae-4621-83ff-d98192e902b1` and
`56b36b45-0faf-493e-933e-82a2253862b1`. They are not receipts.

## CP0-v2 section 1. What this file changes

1. Keeps, unchanged, the v1 product outcome except where section 2 replaces it, the seven
   product bars, the authority-profile text, the user and scope boundaries, and the
   "Current-authority preservation" list.
2. Replaces two v1 passages that conflict with X1 (sections 2.1 and 2.2).
3. Replaces the v1 "category verdict" wording at three sites, because it conflicts with
   decision 8 (section 2.3).
4. Adds X1 to X4 for Cockpit (section 3), a Cockpit **Execution substrate** section
   (section 4) and the CA Admission retargets (section 6).

## CP0-v2 section 2. v1 passages replaced

Line numbers refer to `docs/replacement/amendments/SUCCESSOR-PRODUCT-2026-10-24.md` at
Cockpit `main` `3122cac4780d776cbd487dd2a671466151a465ba`.

### 2.1 X1 conflict: "capability to carry forward" (v1 lines 74 to 77)

v1: "Polymarket US Retail, legacy live paths and tax calculation code are **capability to
carry forward**, not new Nov 11 invention."

Replacement: "Polymarket US Retail remains a later venue, with Kalshi demo first. Legacy
live paths and tax calculation code live in `CX-LEGACY`, frozen at tag
`pre-x1-cockpit-20261026`. They are read as **source for research**, never edited, and are
not carried forward as code. Tax treatment stays out of Nov 11 scope." The v1 phrase
"carry-forward source only, not a gate" (lines 109 to 110) reads the same way: source for
research.

### 2.2 X1 conflict: "not a rewrite" and "language rewrite" (v1 lines 186 to 195)

v1: "After 11 Nov, stretch is a **sealed forward-test window**, not a rewrite." and the
"**Not an 11 Nov gate:**" list, which includes "language rewrite".

Replacement for both paragraphs: "The schedule is a prioritization constraint, not evidence
of safety, profitability, legal eligibility, release readiness or live authority. Lower
maturity is an honest result. The engine rebuild (X1) is part of the Nov 11 plan on the
schedule in the Nov 11 spine memo; deletion of the legacy engine follows the Nov 10
rehearsal. After 11 Nov, stretch is a **sealed forward-test window**. **Not an 11 Nov
gate:** SWE-bench or other benchmaxxing; net-yield PASS; live PASS; a change of
implementation language; tax calculations; Discord; CSL-owned pay-per-token keys. A change
of implementation language is decided by F-3 style spikes, not by X1."

### 2.3 Decision 8 conflict: "category verdict" (v1 lines 61 to 68, 178 to 184 and 233 to 234)

Decision 8 (relayed on CSL-522 comment `56b36b45`): the CP7 protocol merges before any CP3
result record, and nothing is called a verdict on Nov 11. Decision 1 (comment `9f3f7fc1`)
gives the CP7 wording "provisional, inconclusive by default". v1 says the window "ends in a
**verdict**" (lines 64 to 68), that Nov 11 success includes "a draft category verdict"
(line 180) and that scope includes "a category verdict" (lines 233 to 234). The
replacements are:

**(a) v1 lines 61 to 68.** "Build an **open-source research instrument** that operators
can test. By 2026-11-11 it investigates functionality, the best model combinations for
trading and yield, and which skills or strategies optimize yield. Results are published to
market the software, and every published or demo result and category statement is
labelled "Provisional:". The window does not end in a verdict: nothing is called a verdict
on 2026-11-11, and on that date the category statement reads "provisional, inconclusive by
default". Whether this category of software succeeds or fails is answered only after a
sealed forward-test window has closed and a human receipt exists for the final roll-up,
under the CP7 contract. Testing is closed (private benchmarks and secret variables); the
software is open."

**(b) v1 lines 178 to 184.** "The 2026-10-16 one-month "thin vertical slice" is
**replaced** as the 11 Nov cut line. 11 Nov success is a **replayable research instrument
plus operator product** that operators can test, with published results labelled
"Provisional:" and a provisional category statement ("provisional, inconclusive by
default"), hitting **all seven of Lucas's bars** or naming the exact deferral. CLI-only is
a bar-4 fail. Breadth is added when it does not weaken provenance, authority, recovery or
evaluation."

**(c) v1 lines 233 to 234.** In the "In scope for this amendment" list, replace "a category
verdict" with "a provisional category statement (no verdict on 2026-11-11)".

**What decision 8 fixes, and what it leaves open.** Every published or demo label carries
the prefix "Provisional:". The word "verdict" is not used in any published or demo text on
or before Nov 11. Empty, negative, rejected and inconclusive results stay visible (v1 lines
81 to 83, unchanged). The wording of any non-default category statement, the roll-up
structure, the forward-window length and the seal date are **not decided**; the CP7
preregistration contract (csl-research record
`submissions/20261026-csl518-cp7-prereg-power-contract-02`, pull request #54, candidate
only) proposes them and Lucas decides. This file does not pre-empt them.

## CP0-v2 section 3. Decided directions encoded

**X1.** The legacy engine is `CX-LEGACY`: `app/**` and `rust/**`. It is frozen at tag
`pre-x1-cockpit-20261026` (commit `3122cac4780d776cbd487dd2a671466151a465ba`) and deleted
by a cutover PR, never edited. Before any deletion, `CX-PROMOTE` moves to a collected CI
step (WP4): the two acceptance checkers `tools/check_replacement_program.py` and
`tools/check_o7_operator_contract.py`, and their callers
`app/tests/test_replacement_program_registry.py`, `app/tests/test_o7_operator_contract.py`
and `tools/test_replacement_program.py`. Deleting `app/` today would silently disable the
CSL-212 acceptance-pin gate, because `.github/workflows/ci.yml` names neither checker
and its explicit `tools/` test list (lines 438 to 445) omits `test_replacement_program.py`.
Recreated code lives in `CX-NEW`: `engine/**` and `engine/tests/**`. Deletion follows the
Nov 10 rehearsal, once conformance is red-to-green under unchanged job names (decision 3,
comment `9f3f7fc1`). A thin direct-CLI fallback is kept as a fallback only; it does not
satisfy X2.

**X2.** Cockpit's decision routes run on Recursion by default through the shared route
contract, with a pluggable harness layer (Claude Code, Codex, others). A provider response,
typed decision or confidence value stays an input, never permission. On Nov 11 this is
delivered as the route contract, fixture conformance and one recorded bakeoff through a
Recursion in-process adapter against the fake driver, labelled **X2 live integration
deferred**. A live route is added only if CSL-460 lands by Nov 5 (decision 2, comment
`9f3f7fc1`).

**X3.** Cockpit text that says "4h lease" reads "2 h lease (+30 min grace)". `AGENTS.md`
wins. Cockpit `AGENTS.md` line 74 and Recursion `AGENTS.md` line 86 still differ on
takeover (Cockpit requires an unprefixed `ACK TAKEOVER`; Recursion allows takeover after
lease plus grace). This file does not align them.

**X4.** Research, specs, decisions, evidence and runbooks go to csl-research. Operator
documentation for the repository's current state stays and grows. The sentence in
`docs/README.md` (lines 47 to 49) that says "user-facing and revision-dependent operational
docs and contracts stay in this repository" is superseded by X4, with a dated pointer.
`program.json` and the checker read local files, so they are the first thin digest-pinned
projections (decision 7, comment `56b36b45`: only files that checkers or agents read
offline stay, CSL-536 inventories now, the mass move comes after Nov 11). The canary
fixtures, and the add/remove file pair the canary procedure names, are exempt from the X4
move and stay at their current paths until Lucas approves the CSL-536 inventory.

## CP0-v2 section 4. Execution substrate (Cockpit side)

1. Cockpit is Recursion's first customer. It defines **no** session, queue, lease or
   route-selection code. A grep test proves it (WP12 acceptance).
2. Cockpit's own runtime surface (the `app/agent_runner/runner.py` runner, the HTTP route,
   the budget ledger and the scheduler) is classified in the X1 inventory as one of:
   Recursion-owned, Cockpit domain policy, or deleted. Classification waits on Lucas's
   approval comment citing the inventory digest.
3. Cockpit consumes the Recursion contract through a consumer adapter and a decision
   profile (WP12, owner Samir) once the contract schema exists at a pinned digest. The
   Recursion side is RP0-v2 section 4.
4. **Order authority does not change.** Paper-first. One risk-pass, mode, approval and
   `OrderGateway` path. `app/risk/` stays deterministic with no model, I/O or subprocess
   logic. Provider-neutral research does not grant model-neutral order authority (Inv 2);
   CP4 needs an Inv-2 amendment. The operator MCP inspects and stops only; a tool named
   like `place_order` fails the candidate.
5. Keys live in the OS keyring. CSL adds no pay-per-token keys.
6. Order-path files (risk, brokers, audit, PnL, halt, killswitch, gate, mode, loopback
   routes) stay on Cockpit's escalate list in whichever tree they live.

## CP0-v2 section 5. Nov 11 framing and slip order

Decision 1 (comment `9f3f7fc1`): Nov 11 is a recorded demo of a named spine per product.
Everything outside the spine is a labelled stretch or a named deferral. The ambition in D6
is unchanged. M2 ("Feature freeze Oct 30") and M3/W4 ("Nov 12") are relabelled so they do
not claim hardening-only work; those are Linear edits made by Lucas. The spine, the
stretch list and the slip order are recorded in the Nov 11 spine memo, a separate
csl-research record. The slip order, invoked by Lucas on Nov 6, is:

1. the legacy-deletion PR moves behind the Nov 10 rehearsal;
2. GUI targets narrow from three operating systems to Linux-first;
3. venue scope narrows to Kalshi demo plus one crypto spot;
4. benchmark-suite scope narrows;
5. Recursion pillars outside the spine become labelled stretch;
6. the CP7 category statement narrows to protocol only (nothing is called a verdict on Nov 11).

## CP0-v2 section 6. Admission retargets

Linear edits are made by Lucas. CSL-511's own Admission allows only three sections of
`SUCCESSOR-PRODUCT.md`, which is why [CSL-543](https://linear.app/csl-laboratories/issue/CSL-543)
exists with "no new scope" lifted for this twin only.

### 6.1 Rule CA (Cockpit parents CSL-512 to CSL-519)

Replace "Allowed effects/files" (`app/**`, `rust/**`, `tools/tests/**`,
`docs/replacement/**`) with:

> ai-investment-cockpit `engine/**` and `engine/tests/**` (`CX-NEW`); `tools/**` for CI
> and test tooling; `docs/replacement/amendments/**` for dated amendment files and, until
> X4 moves them, the digest-pinned projections only (no edit without a receipt and a
> reviewed promotion). **Not** `app/**` or `rust/**` (`CX-LEGACY`: read-only, deleted by the
> cutover PR). Research only to csl-research `submissions/<record_id>/`.

Replace the verifier with:

> named test files under `engine/tests/**` or `tools/test_*.py` run by an explicit step in
> `ci.yml` job `linux` (the required check `linux-gates`, `ci.yml` line 478);
> `python -m pytest` in `app/` collects only `app/tests/**` (`app/pyproject.toml` line 29,
> `testpaths = ["tests"]`) and `tools/required_ci_gate.py` runs no tests. **Until that step
> merges (a workflow edit that needs Lucas's own-words OK), the parent has no collected
> verifier for new-tree code, and its admitted work is limited to research and contract
> drafts.**

### 6.2 Per-parent retargets

| Parent | Owner, status | v1 verifier, collected? | v2 |
|---|---|---|---|
| CSL-511 CP0 | none, In Review | `check_replacement_program.py` on C#274; its test callers sit in `app/tests/` (`CX-PROMOTE`) | Unchanged, plus the CSL-543 twin for this file. Verifier: `tools/check_replacement_program.py` run by a `tools/`-level step after WP4. |
| CSL-512 CP1 | Chaandan, In Progress | `tools/required_ci_gate.py` (aggregator, no tests) plus `check_o7_operator_contract.py` (caller in `app/tests/`) | CA, plus `check_o7_operator_contract.py` through the WP4 step. The three recorded GUI sessions are human review, not collected. GUI scope slips to Linux-first (slip step 2). |
| CSL-513 CP2 | Chaandan, In Progress | look-ahead and survivorship audit tests "in `tools/tests/`": **not collected** | CA. Tests live in `engine/tests/**` or are listed in `ci.yml`, not in `tools/tests/`. |
| CSL-514 CP3 | Chaandan, In Progress | bakeoff harness tests under `required_ci_gate.py`; `tools/research_value_score.py` is not referenced in `ci.yml` | CA, plus a `tools/test_*.py` wrapper for `research_value_score.py` named in the step. X.com claims and strategy sources go to the vault. Any CP3 result record cites the CP7 protocol and is merged after it (decision 8). |
| CSL-515 CP4 | none | matrix run plus `tools/verify_keys.py`: not referenced in `ci.yml` | CA, plus the matrix runs through the Route Contract consumer adapter (WP12). An Inv-2 amendment status is stated. |
| CSL-516 CP5 | none | per-venue conformance tests: location unspecified | CA. Venue scope slips to Kalshi demo plus one crypto spot (slip step 3). |
| CSL-517 CP6 | none | kill-switch drill recorded plus `tools/redteam` checks: not referenced in `ci.yml` | `docs/replacement/AUTHORITY-COMPLIANCE-RESEARCH.md` and inspect/stop MCP code under `engine/**`; CA verifier, plus a collected test that fails if any tool named `place_order` appears. |
| CSL-518 CP7 | none, In Review | acceptance test "Independent rerun; verdict draft on Nov 11"; verifier "independent rerun of closed benchmarks matches" and "one other founder approves the verdict draft" | CA. Acceptance test reads "Independent rerun; provisional results and a provisional category statement on Nov 11 (no verdict)". The founder approves the provisional category statement. Stop condition unchanged: publish no number the rerun does not reproduce. The preregistration record is a precondition for any CP3 result record (decision 8). The issue title still says "published category verdict"; renaming it is a Linear edit for Lucas. |
| CSL-519 CP8 | none | cross-user isolation tests "in `tools/tests/`" plus `verify_keys.py`: not collected | CA |

### 6.3 Shared parents (Cockpit side)

| Parent | v2 |
|---|---|
| CSL-520 SP1 | "Removals" from this repository become moves governed by X4 and the CSL-536 inventory, which Lucas approves first. The Cockpit-side secret scan is not named in `ci.yml`, so it is a recorded manual scan unless a collected job is added. |
| CSL-521 SP2 | Both lease sites read "2 h" (RP0-v2 section 5.4). Verifier names `tools/test_queue_gates.py` (run at `ci.yml` line 440) and `tools/test_merge_candidate.py` (not in `ci.yml`; run by `.github/workflows/codex-gate.yml` line 261). Workflow edits need Lucas's own-words OK. |
| CSL-522 SP3 | Walkthrough scripts are operator documentation. Stop condition keeps "drop any demo segment that has no passing verifier". The demo shows no verdict (section 2.3). |
| CSL-536 | No Admission is drafted. Decision 7: inventory and classification now, mass move after Nov 11. |

## CP0-v2 section 7. What this does not do

Tax treatment, live activation, Discord (none at all), CSL pay-per-token keys, language
selection, any movement of the `program.json` pins before a receipt, any edit to the
accepted `SUCCESSOR-PRODUCT.md`, and any admission of work.

## CP0-v2 section 8. Acceptance boundary

Acceptance of this document establishes product direction only. It does not prove
implementation, provider or venue qualification, profitable operation, safety, compliance,
release readiness, or permission to trade live. It is **not accepted** until Lucas posts an
exact-digest human receipt over its bytes. The other-founder-approval path proposed in
Cockpit C#275 (CSL-523) applies only to future Samir/Dan-authored amendments and takes
effect only once it is wired into `program.json`, `tools/check_replacement_program.py` and
`AGENTS.md`; at C#275 head `ffb56144acc545a61500598680583ce63682e0be`
`EXPECTED_GITHUB_ACCEPTED_RECEIPTS` is empty, so it neither accepts nor rejects this file.

## Research basis (candidate-only)

Vault draft record (candidate evidence; not human product acceptance):

- repository: `CSL-Laboratories/csl-research`
- commit: `541ee845cdcc0a479190ddd6f0736e4dc854dd1d`
- path: `submissions/20261026-csl493-wp7-rp0v2-cp0v2-amendment-drafts-01/`
- claim: the draft text, tree map, Admission retargets and evidence behind this file.
- limitation: candidate evidence on an unmerged pull request (csl-research#50).

Vault record for the verdict wording (candidate evidence):

- repository: `CSL-Laboratories/csl-research`
- commit: `3c945d0307a264ee81438c3bb24ed69ac9e8e20e`
- path: `submissions/20261026-csl518-cp7-prereg-power-contract-02/`
- claim: the "verdict on Nov 11" wording conflicts with decision 8 (section 1.2 row C5).
- limitation: candidate evidence on an unmerged pull request (csl-research#54); the head may move.

The exact vault commit of the record that carries this file's text is stated in the pull
request that adds it.

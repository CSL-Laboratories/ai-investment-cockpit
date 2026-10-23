# CI event-to-check contract

Contract version: v2.5 (2026-10-22), a patch that re-pins the `claude.yml`, `claude-conflict-writer.yml` and `AGENTS.md` citations after #239, #242 and #236, with no row change (see the v2.5 base below). v2.4 (2026-10-21), a patch that re-pins the `claude.yml` and `claude-conflict-writer.yml` citations after #238 (CSL-377), with no row change (see the v2.4 base below). v2.3 (2026-10-21), a patch that re-pins the `claude.yml` and `claude-conflict-writer.yml` citations after #231, #232 and #233 and the `AGENTS.md` citations after #235, with no row change (see the v2.3 base below). v2.2 (2026-10-21) was a patch to v2 that records merged workflow changes without a row change. v2.2 records #216 (CSL-352/CSL-377) in section 7 and the concurrency table: the Claude fixer's restructured jobs, the new conflict-writer workflow, the human pause and the `@claude` eight-commit guard; it re-pins every `claude.yml` citation. No event-matrix row, required check or aggregation changes. Earlier: v2 records CSL-408 (#224): every job now runs on a free GitHub-hosted runner. The test jobs moved from Blacksmith to `ubuntu-24.04` and `windows-2025`, and the Windows cells' timeout went from 30 to 50 minutes. Event rows, conditions and required checks are unchanged from v1. v2.1 re-pins the `AGENTS.md` citations after #217, cites the first post-merge runs, widens the [O] legend to dated runs, and corrects what a `GITHUB_TOKEN` push starts (section 7).

**Versioning.** Any change to a workflow, `scripts/ci/**`, a required check or the rulesets
that changes a row here updates this file in the same PR. Bump the version for a semantic
change (v1 → v2) and the patch for a correction (v1.1). Record the new base commit. Versions
count merged changes; edits made during a PR's own review are part of that version. Tracked
under CSL-255 / CSL-385.

- **Base commit (v2.5):** main `244af623cdf66b118a50d8c639b80342aaa0edf0` (merge of #244, CSL-377), after #236 (CSL-425, merge `0a9d47ec`). Since the v2.4 base (main `5fb4a0fdf769a236c5f8f896081e6b41ae95366b`, the merge of #238), #239 (CSL-430) and #242 (CSL-377) changed `claude.yml` and `claude-conflict-writer.yml`; #243 (CSL-377) added `docs/canary-csl-377.md` and one `.gitattributes` line and #244 removed both again, neither cited; #236 replaced `AGENTS.md` line 51 with 27 lines and lines 144-145 with three, and edited lines 47, 142, 317, 351, 354 and 357 in place, so the `AGENTS.md` citations moved down by 26 or 27 and point at identical text; it also changed `CLAUDE.md` and `.claude/skills/proceed-or-escalate/SKILL.md`, which have no line citations here. This patch changes line numbers only: 45 `claude.yml` and `claude-conflict-writer.yml` numbers or ranges and 5 `AGENTS.md` ones, counting bare continuations (364-377 → 391-404, 287-302 → 314-329, 372 → 399, and the runner-label pair in the v2 note, 368 and 371 → 395 and 398; the v1 numbers 238 and 241 in that note stay as written). A line-sequence diff of each cited file from the v2.4 base to this base puts every cited line, range interiors included, in an unchanged region, and a second pass that pairs all 364 cited numbers or ranges finds identical text at each, apart from those two historical v1 numbers. The runbook's citations are re-pinned the same way: 11 numbers or ranges, all 23 paired with identical text. No other line-cited file changed.
- **Base commit (v2.4):** the merge of #238 (CSL-377: stage without the ignored helper directories, and unstage the action's restored paths only on events where the action restores them), traced at #238 head `447a1c16f39dd0c3fe760bdc5227f09aaf120aad` on main `26fc9dda`. #238 changed `claude.yml` and `claude-conflict-writer.yml` and makes no contract row change. Every line number below that cites those two files is re-pinned, 45 numbers or ranges in all, counting bare continuations. A line-sequence diff of each file from `26fc9dda` to `447a1c16` puts every cited line in an unchanged region, and a second pass that pairs all 75 citations (109 line numbers) finds identical text at each. No other line-cited file changed.
- **Base commit (v2.3):** main `26fc9dda2658918a0477e16d2003ccb6f03a79b5` (merge of #235, CSL-378). #235 added four `AGENTS.md` lines (three after line 317, one after line 322), so the `AGENTS.md` citations below line 322 moved down by 4 (360-373 → 364-377, 368 → 372, and the runner-label pair in the v2 note, 364 and 367 → 368 and 371) and point at identical text; it also changed `.mcp.json` and `.cursor/mcp.json`, which are not cited. Before #235 the v2.3 base was main `42cff983ec4e866507399a0a804f42ba4de85d5b` (merge of #230, CSL-416). Since v2.2, #231 (CSL-412: the Claude action pinned by SHA, plus a git-config credential check in each model package step), #232 (CSL-405: the App token minted after bundle validation; fetch and fsck hardening) and #233 (CSL-377: the model helper's commit skips the sandbox's `/dev/null` masks) changed `claude.yml` and `claude-conflict-writer.yml`. Each of those PRs says "no contract row changes". Every line number below that cites those two files is re-pinned: 45 numbers or ranges, counting the bare numbers that continue an earlier `file:line` citation in the same sentence (for example "(1372-1400)" in section 7). A line-sequence diff of each file against the v2.2 base puts every cited line in an unchanged region, so each points at identical text. No other line-cited file changed (#230 added `docs/ci-runbook.md` and tests, which are not cited by line).
- **Base commit (v2.2):** main `38f4790cc3b55ec19dda1aec5920b5474f089fae` (merge of #229, CSL-415). #229 replaced `AGENTS.md` line 171 with four lines, so the `AGENTS.md` citations below moved down by 3 more (284-299 → 287-302, 357-370 → 360-373, 365 → 368) and point at identical text; it also changed `.claude/skills/proceed-or-escalate/SKILL.md`, which is not cited. Before #229 the base was main `878a6859d22d3d440d5a35b4a1f6f60e58172792` (merge of #226). Since v2.1, #216 (CSL-352/CSL-377) rewrote `claude.yml`, `conflict-scan.yml` and the fixer scripts, added `claude-conflict-writer.yml` and added three `AGENTS.md` lines after line 480; #225 (CSL-390) added one `AGENTS.md` line after line 370 and changed no other cited file. Every `claude.yml` citation below is re-pinned to this base. Those #216/#225 insertions sit below every `AGENTS.md` citation, so they moved none. No other line-cited file changed.
- **Base commit (v2.1):** main `f6a9a3c56f008599a3c0b9fb766597599c0567d0` (merge of #217). #217 (CSL-354) changed `AGENTS.md` line 49 and added 123 lines after line 58, so the `AGENTS.md` citations below moved down by 123 and point at identical text; no other cited file changed (#217 also changed `CLAUDE.md` and added `.claude/skills/proceed-or-escalate/SKILL.md`; neither is cited here).
- **Base commit (v2):** main `b29ef6d5` (merge of #221) plus the CSL-408 runner change in #224. #224 keeps the line counts of `ci.yml`, `rust.yml`, `AGENTS.md` and `tools/ci_runner_cost.py`. The only cited lines whose text changes are the runner labels (ci.yml:352, 609, 822; rust.yml:282-283, 287-288), the benchmarks comment (ci.yml:21-23) and `AGENTS.md`:238 and 241 (now 395 and 398 after #217, #229, #235 and #236). Every other v1 citation still holds.
- **Base commit (v1):** `578a8ca8` (`main`, merge of #223). The rows were traced at `7b0dcf27` (#215). Since then:
  - #220 (CSL-383) added three lines in two places in `claude.yml` and once in `conflict-scan.yml`.
  - #222 (CSL-393) added 15 lines to `AGENTS.md` item 6.
  - #223 (CSL-397) changed the `ci.yml` docs filter (G16): 11 more lines, `--no-renames`, the new run-tests patterns and the `tools/test_ci_docs_filter.py` step.

  Every `file:line` citation was re-mapped from the previous base `be97ff3b`. Lines outside the CSL-397 edit point at identical text. The lines that edit changed are not identical to `be97ff3b`: the `git diff` line (now 277), the new run-tests arm (303-307), the inserted test step (443), and any range containing them. All citations are for this base.
- **Which workflow version runs:** a pull-request run uses the workflow file from the PR merge ref. `workflow_dispatch` and `push` use the file at the event's commit. A row applies only where that file matches this base.
- **Scope:** describes current behaviour of the hosted GitHub Actions workflows at the base
  commit. It is not a policy change, grants no permission, and changes no ruleset, workflow
  or label. Where this contract and code disagree, the code at the base commit wins and the
  disagreement is a gap (section 8).
- **Related docs (not duplicated here):**
  [cancellation controller](ci-cancellation-controller.md),
  [candidate readiness](ci-candidate-readiness.md),
  [cost budget](ci-cost-budget.md),
  [queue rollout](ci-queue-rollout.md),
  [AGENTS.md Hosted CI](../AGENTS.md#hosted-ci) (AGENTS.md:391-404).

## Evidence classes

| Tag | Meaning |
| --- | --- |
| **[S]** | Source-documented: `file:line` at the base commit. `ci.yml:95` means `.github/workflows/ci.yml` line 95; other workflow names are under `.github/workflows/`. `ci_cancel_controller.py`, `codex_gate.py`, `candidate_gate.py` and `claude_fixer_receipts.py` are under `scripts/ci/`; `required_ci_gate.py` is under `tools/`. |
| **[O]** | Locally observed through read-only REST (`gh api`) on 2026-10-20, or in a run cited with its ID and date: run, job or check-run IDs given. Older runs may have used older YAML. `workflow_dispatch` runs use the dispatched branch's YAML. |
| **[I]** | Inferred from GitHub platform behaviour or from reading code. Not tested and not observed here. |

**Since CSL-408 (v2) runner compute is $0.** Every job runs on a standard GitHub-hosted runner, which is free on this public repository. [O] The first post-merge runs on main `10193b91` (push, 2026-10-21), ci 39537098139 and rust 39537098248, ran every job that started on GitHub-hosted runners (`ubuntu-24.04` test cells, `windows-2025` test cells, `ubuntu-latest` filter and gates; `settle`, `benchmarks`, `macos` and `candidate-ci-gates` were skipped). "Paid" and the L/W/M counts in this contract now mean that a test runner was allocated. That is a cost in runner capacity and queue time, not money. The dollar figures below are **historical** Blacksmith estimates for runs before CSL-408. They use the [cost budget](ci-cost-budget.md) method: each started job rounded up to whole minutes, at $0.004/min Linux and $0.008/min Windows. Blacksmith billed each job that got a runner, per started minute (CSL-255 calibration). They are estimates, not invoices.

## 1. Required checks

[O] `gh api repos/CSL-Laboratories/ai-investment-cockpit/rules/branches/main` (2026-10-20):

| Ruleset | Source | Rules on `~DEFAULT_BRANCH` |
| --- | --- | --- |
| `protect-main` 25015519 | Organization | deletion, non_fast_forward, pull_request (1 approval, thread resolution, dismiss stale reviews on push, extra approval for unattributed changes). Bypass actors: [I] not readable from this session (the org rulesets endpoint is blocked), so the OrganizationAdmin entry is unverified. |
| `required-ci` 25019206 | Repository | required_status_checks `strict: true`: `linux-gates` (integration 15368, GitHub Actions), `Devin Review` (integration 811515); pull_request (1 approval, last-push approval, dismiss stale reviews on push, extra approval for unattributed changes). Bypass: [O] RepositoryRole 5 (admin), `pull_request` mode. GitHub returns `bypass_actors` only to callers who can administer the ruleset, so a non-admin read shows `null`. |

There is **no `merge_queue` rule** in the effective rules. The `merge_group` triggers are
therefore unreachable (section 3, row 16).

| Required context | Producer | Where |
| --- | --- | --- |
| `linux-gates` | GitHub Actions check run, job `linux-gates` in `ci.yml` | [S] ci.yml:473-513. No other workflow defines a job with this name. [S] ci-label-retrigger.yml:1-3 and test `test_dispatch_defaults_and_existing_events` assert this. |
| `Devin Review` | Devin app **commit status** (creator `devin-ai-integration[bot]`), not a workflow | [S] ci-queue-rollout.md:26-32. [O] commit `8f5caa21f1` statuses: `pending` 06:17:07Z, then `success` 06:18:09Z. |

The following checks are **not required** today and are advisory on PRs: `queue-gates`
(ci.yml:517), `rust-gates` (rust.yml:439), `candidate-ci-gates` (ci.yml:555),
`review-gate` (review-gate.yml:28-29), `benchmarks`, `pr-lint`, and every Windows, macOS
and Rust cell. [S] ci-queue-rollout.md:8-15 proposes only `linux-gates`, `queue-gates`, `rust-gates` and `review-gate` as stable required checks; `benchmarks`, `pr-lint` and `candidate-ci-gates` are not on that list.

Strict mode (`strict_required_status_checks_policy: true`) requires the PR branch to be up
to date with `main` before merge. Required checks bind to the PR **head SHA**. [I] When
several `linux-gates` check runs exist on one SHA, GitHub evaluates the most recent one.
[O] Head `4268c7e4b6` of #213 has check run 118452752528 `failure` (04:21Z), then
118455279951 `success` (04:30Z), and #213 merged. That is consistent with this rule, but it
does not prove it, because the repository ruleset `required-ci` allows admin bypass on pull
requests ([O] RepositoryRole 5, line 43), so a merge alone doesn't show the check was satisfied.
The org ruleset's bypass actors are unverified ([I], line 42); this reasoning doesn't rely on them.

## 2. Workflows that start the test runners

[S] grep of `.github/` at the base commit. Three `ci.yml` jobs and the `rust.yml` `rust-book` Linux and Windows cells are the test runners. Before CSL-408 they ran on Blacksmith (`blacksmith-2vcpu-ubuntu-2404`, `blacksmith-2vcpu-windows-2025`); since then they run on free GitHub-hosted runners. The optional macOS cell is `macos-latest`:

| Workflow | Job | Runner | Lines |
| --- | --- | --- | --- |
| ci.yml | `linux` (3.11, 3.12) | `ubuntu-24.04` | ci.yml:331-352 |
| ci.yml | `windows` (3.11, 3.12) | `windows-2025` (timeout 50 min, ci.yml:610) | ci.yml:584-609 |
| ci.yml | `benchmarks` (1 cell) | `ubuntu-24.04` | ci.yml:806-822 |
| rust.yml | `rust-book` matrix (Linux 2, Windows 0 or 2) | from `filter` output: `ubuntu-24.04`, `windows-2025`, and the optional macOS cell `macos-latest` | rust.yml:281-293, 313-328 |

The following run on free GitHub-hosted runners in this public repo: `filter`, `settle`, the
aggregates, `macos` and the Rust macOS cell (`macos-latest`), and all of ci-label-retrigger.yml, codex-gate.yml,
review-gate.yml, claude.yml, conflict-scan.yml, claude-conflict-writer.yml, pr-lint.yml, linear-pr-sync.yml and
stuck-thread-watch.yml (`runs-on: ubuntu-latest`).

**No exception since CSL-408.** In v1 the team rule was that test jobs run on Blacksmith, and the
`macos` test job (ci.yml) and the Rust macOS cell (rust.yml:290-293) were the exception on
GitHub-hosted `macos-latest` ([O] dispatch run 39065062851 used a GitHub-hosted macOS runner).
Since CSL-408 every test job is GitHub-hosted, so macOS is no longer an exception. CSL-281,
which would have moved macOS to Blacksmith, was canceled by Lucas's decision.

**Correction to the task brief:** claude.yml has **no Blacksmith runner** at the base
commit. Its jobs use `ubuntu-latest` (claude.yml:93, 506, 537, 566, 788, 1580, 3089, 3906, 4696). [O] In v1, across the
last 60 claude.yml runs, all 13 jobs that started used `ubuntu-latest`. See section 7.

## 3. Event matrix

Every paid job needs `filter` = success (ci.yml:338 linux, 591 windows, 815 benchmarks; rust.yml:318). The free `macos` job has the same requirement (ci.yml:702). `filter`
is skipped for draft `pull_request` events (ci.yml:102; rust.yml:93). The ordinary concurrency
key for PR runs is `ci-ci-<PR>-default` or `ci-ci-<PR>-draft`, and `rust-rust-<PR>-…` in
rust.yml (ci.yml:95; rust.yml:86).

"L" means a Linux test runner (`ubuntu-24.04`), "W" a Windows test runner (`windows-2025`) and "M" macOS (`macos-latest`). All are free GitHub-hosted runners since CSL-408; before it, L and W were Blacksmith. "Rust-relevant"
means the `RUST_PATHS` match in rust.yml:242-263. Note that the list includes `.github/`.

| # | Event | ci.yml jobs | rust.yml jobs | Test runners started | `linux-gates` | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `pull_request` opened, **draft** | filter, settle, linux, windows, macos and benchmarks are skipped. `linux-gates` and `queue-gates` run. | filter and rust-book skipped. rust-gates runs. | **None** | **FAIL** ("filter job did not succeed"). rust-gates also fails. | [S] ci.yml:102, 322, 478, 492-495; rust.yml:93, 442, 461-464. [O] run 39378544947 (#217): all paid jobs skipped, linux-gates log `filter=skipped linux=skipped`. |
| 2 | opened, **non-draft** | filter decides `run-tests` (ci.yml:255-314). settle skipped. linux if run-tests. benchmarks if run-tests. Python windows and macos only if `ci:full` is already on the PR in the event payload **and** `run-tests == true` (ci.yml:597-602, 708-715), so a docs-only PR with `ci:full` starts neither. | filter diffs the merge tree (rust.yml:216-228). Cells for Linux, plus Windows and macOS with `ci:full` (rust.yml:206-207). | L×2 + L×1 (benchmarks) when run-tests. Rust L×2 when Rust-relevant. With `ci:full`: W×2 and M from ci.yml only when run-tests, plus Rust W×2 and Rust M when Rust-relevant, even if run-tests is false (rust.yml:206-207, 242-293). | PASS if `linux` succeeded, or if run-tests=false and linux skipped (docs/no-change noop). Otherwise FAIL. | [S] ci.yml:322, 336-344, 597-603, 708-716, 813-821. [O] see row 7. [I] A PR created with labels also gets a `labeled` event, so row 9 can fire as well. |
| 3 | synchronize, **draft** | Same as row 1. | Same as row 1. | None | **FAIL** | [S] as row 1. [O] run 39377719991 (#215 draft-era, head `8f5caa21f1`). |
| 4 | synchronize, **non-draft** | settle runs `sleep 120` on ubuntu-latest, then the same jobs as row 2. | settle, then the same jobs as row 2. | Same as row 2, after about 2 minutes. | Same as row 2. settle must be `success`. | [S] ci.yml:320-329, 486, 496-501; rust.yml:304-311. [O] run 39493569607: settle 20:39:12-20:41:15, linux 3.11/3.12, benchmarks, linux-gates success. 15 L-min, about $0.060. Rust 39493569462. |
| 5 | synchronize **burst** (new head while the old run is waiting in settle or running) | The new run queues in the same group (`queue: max`, no cancel-in-progress). The controller (section 5) cancels active, first-attempt, strict-ancestor runs once a current-head witness exists. The cancelled old run's `linux-gates` runs only if the run had started jobs; its result follows the `linux-gates` column. | Same as ci.yml. | Whatever the old run started before cancellation. Concurrency does not prevent new paid starts; the controller cancels them. | Old SHA: Outcome by timing (section 4): (a) no job had started → no `linux-gates` check ([O] 39492190159, 0 jobs); (b) a started gate dependency (filter, settle or linux) was cancelled or failed → FAIL from steps 1-5 ([O] 39490583816); (c) `linux-gates` had already completed and only a non-dependency such as `benchmarks` (ci.yml:812) was still running → its existing check stays, success if it passed ([I]); (d) the cancel lands while `linux-gates` itself is running → it fails only if `cancelled()` is true for the reject step ([I], not observed). New head: as row 4. | [S] ci.yml:94-97, 511-513; ci-label-retrigger.yml:15-17, 61-83; ci_cancel_controller.py:367-419. [O] run 39490583816 was cancelled during its linux cells after about 5 L-min (rounded up per job, including benchmarks), and controller run 39492190855 logged `Requested cancellation of verified ancestor run 39490583816`. Pending run 39492190159 was cancelled before any job started (controller 39493573599). Controller 39493574339 logged the HTTP 409 reread no-op. A `requested` event fires while the new run is still pending in the queue (39492190855 at 20:27:56Z; its target started 20:29:36Z). |
| 6 | reopened | As row 1 or 2, depending on the PR's draft flag at the event. | Same. | Same as row 1 or 2. | Same as row 1 or 2. | [S] ci.yml:38; rust.yml:32. Not observed. |
| 7 | ready_for_review | As row 2 (settle intentionally skipped), in the `default` group, not the `draft` group. | As row 2. | Same as row 2. | PASS or FAIL as row 2. This is a new check run on the same head SHA as the earlier draft failure. | [S] ci.yml:95, 322, 486. [O] #215 head `8f5caa21f1`: draft run 39377719991 produced linux-gates `failure` (check 118479517498). Ready run 39379495443 then produced `success` (check 118486614887), with settle skipped and L×2 + benchmarks. |
| 8 | converted_to_draft | **Not a ci/rust trigger.** In-flight non-draft runs continue. | Not a trigger. | Runs already started continue. | Unchanged until the next event. | [S] ci.yml:38; rust.yml:32 (types). codex-gate.yml:12 does listen. Controller: a draft triggering PR is a notice no-op (B1, ci_cancel_controller.py:188-190, 359-364), and any draft transition during a plan refuses (193). [O] B1 merged 2026-10-20T07:10:37Z (#219). Run 39389258630 logged `PR #214 is draft; no cancellation needed`. No controller run has concluded `failure` since 07:11Z. Before B1, draft-triggered controller runs such as 39382558811 failed. |
| 9 | labeled `ci:full` (non-draft, same-repo) | ci.yml and rust.yml are **not** triggered by `labeled`. ci-label-retrigger `retrigger` runs `gh workflow run ci.yml --ref <head branch>` with `scope=full`, `pr_number`, `pr_head_sha` and `merge_sha=github.sha` (the PR merge ref). It also dispatches rust.yml when the PR file list matches the Rust regex. The dispatched run: group `ci-ci-<PR>-label-<head>-full` (`rust-rust-<PR>-label-<head>-full` for rust.yml), cancel-in-progress **true**, queue `single`. filter validates head and merge (ci.yml:195-213). decide forces `run-tests=true`, even for docs-only changes (ci.yml:231-235). No settle, no benchmarks. | Dispatched rust: `CHECK_PATHS=false`, so all cells for the scope run (rust.yml:205, 279-293). | L×2 + W×2 + M. Rust L×2 + W×2 + M when Rust-relevant. | [O] The dispatch run creates a `linux-gates` check run on the branch tip, which is the PR head when validation passes (39065062851). [I] Unverified: whether a `workflow_dispatch` check run satisfies or replaces the PR's required `linux-gates`. GitHub's troubleshooting doc for required checks says it does not. | [S] ci-label-retrigger.yml:31-59; ci.yml:95-97, 195-213, 231-235. [O] retrigger 39065057684 (scope full) led to ci 39065062851: L 8 min + W 10 min, about $0.112, head `7a112a5205` = PR head. No rust dispatch on this branch at that time ([I] not Rust-relevant). This run predates `queue-gates`, so it executed older branch YAML. |
| 10 | labeled `enhancement` | As row 9 with `scope=linux`, so windows and macos are skipped. If the PR also has `ci:full`, `CI_SCOPE` is `full` (ci-label-retrigger.yml:44) and the fan-out is row 9's. | Linux cells only; the full matrix with `ci:full`. | L×2 (+ Rust L×2); with `ci:full` as row 9 | As row 9 | [S] ci-label-retrigger.yml:44; ci.yml:599; rust.yml:206-207. |
| 11 | labeled with any other label, on a draft, or from a fork | `retrigger` skipped. A ci-label-retrigger run is created with only skipped jobs. | None | None | Unchanged | [S] ci-label-retrigger.yml:32-36. Test `test_label_eligibility_and_scope`. |
| 12 | unlabeled (any label, including `ci:full`) | Nothing. ci-label-retrigger listens to `labeled` only. Removal affects only later events: Windows and macOS selection read the payload labels at each event. | Nothing | None | Unchanged | [S] ci-label-retrigger.yml:11-12; ci.yml:536, 602, 713; rust.yml:206-207. |
| 13 | Stale label dispatch (head advanced between the label event and dispatch) | filter fails at `[ "$GITHUB_SHA" = "$PR_HEAD_SHA" ]`. Paid jobs are skipped and `linux-gates` FAILS. [I] That failure is a check run on the **new** head SHA (the dispatched ref tip). | rust filter fails the same way. | None | [I] A failing check run on the current head. [I] Unverified: whether a `workflow_dispatch` check run satisfies or replaces the PR's required `linux-gates`. GitHub's troubleshooting doc for required checks says it does not. | [S] ci.yml:210-213; rust.yml:197-200. Test `test_actual_dispatch_context_validation_rejects_stale_and_partial_context` covers refusal only. |
| 14 | Base change: retarget (`edited`) or `main` advancing | Not a ci/rust trigger. Strict mode requires an "update branch", which arrives as row 4. | Not a trigger | None until the branch is updated | The existing result stays bound to the head SHA. Merge is blocked until up to date. | [S] ci.yml:38; rust.yml:32. Ruleset `strict: true` (section 1). codex-gate.yml:12 handles `edited`. |
| 15 | push to `main` | filter diffs `before..after`. If that diff is docs-only for the ci.yml filter, `run-tests=false`, so no Python paid job starts and `linux-gates` takes the no-test path (as G16). Otherwise linux×2 and windows×2 run. rust.yml decides separately: Markdown under `rust/` or `.github/` is Rust-relevant (G18). No macos, no benchmarks. `linux-gates` runs on the main SHA (not gating). Group `ci-ci-refs/heads/main-default` with **cancel-in-progress true**. | Cells for Linux + Windows when Rust-relevant (rust.yml:206, 230-232). | Python L×2 + W×2 when `run-tests`; Rust L×2 + W×2 when Rust-relevant, including Markdown under `rust/` or `.github/`. A docs-only push outside those paths starts none. | PASS or FAIL on the main SHA (informational) | [S] ci.yml:41-42, 95-97, 261-263, 601, 708-716, 821. [O] ci 39492119963: L 13 + W 18 min, about $0.196. Rust 39496637430: L 4 + W 4, about $0.048. [I] A newer main push cancels an older in-progress main run. Not observed. |
| 16 | `merge_group` | Declared (ci.yml:39-40; rust.yml:36-37), but **unreachable**: no merge-queue rule (section 1). | Same | None | n/a | [S] + [O] ruleset read. Offline tests only. |
| 17 | `workflow_dispatch`, manual (no PR inputs) | One dispatch targets one workflow; neither `ci.yml` nor `rust.yml` dispatches the other (the label path issues two separate commands, ci-label-retrigger.yml:53-58). A `ci.yml` dispatch: group `manual-<scope>` per ref, cancel-in-progress true. `run-tests` forced true. linux×2. windows×2 + macos unless `scope=linux`. | Only from a separate `rust.yml` dispatch: all cells for the scope, no path filter | `ci.yml` dispatch: L×2 (+W×2 +M). A separate `rust.yml` dispatch adds the Rust cells. | PASS or FAIL as a check run on the dispatched tip (app 15368). Effect on the PR's required `linux-gates`: not observed. [I] Unverified: whether a `workflow_dispatch` check run satisfies or replaces the PR's required `linux-gates`. GitHub's troubleshooting doc for required checks says it does not. | [S] ci.yml:46-82, 95-97, 203-206, 231-235, 599, 709. Test `test_dispatch_defaults_and_existing_events`. |
| 18 | `workflow_dispatch`, **candidate** (CSL-259 coordinator) | Dispatched on the default branch with the full tuple and `scope=full`. Group `candidate-<id>`, `queue: max`, no cancel. The staged policy validator runs in every validating job. All five Python cells run, plus `candidate-ci-gates`. | All five Rust cells | L×2 + W×2 + M (+ Rust L×2 + W×2 + M) | Runs, but [I] binds to the policy (main) SHA, not the PR head, so it does not satisfy the PR's required check. | [S] candidate_gate.py:8-10, 533-534; ci.yml:95-97, 555-582; ci-candidate-readiness.md:68-71, 151-158, 214-220. [O] Inactive: codex-gate run 39496664994 executed `legacy` (requires `vars.CANDIDATE_COORDINATOR != 'true'`, codex-gate.yml:66-67). |
| 19 | `schedule` (cron `17 7 * * *`, ci.yml only) | filter: `run-tests=false`. macos runs only if `main` had commits in the last 24 h, or if the commit count can't be read, in which case filter fails open to `run-macos=true` (ci.yml:241-244, G17). linux-gates and queue-gates are skipped. | No schedule trigger | M×1 when `macos` runs (recent commits on `main`, or an unreadable commit count); otherwise none | **Skipped.** The job does not run; GitHub records a `skipped` `linux-gates` job/check on the main SHA. | [S] ci.yml:43-45, 236-253, 478, 520, 710. [O] run 39424586371: macos success, linux-gates skipped. It was created 12:44:11Z for the 07:17 cron, which shows real schedule latency. |
| 20 | Rerun (attempt > 1) | [I] Re-executes with the original event payload. GitHub offers three modes: re-run all jobs, re-run failed jobs, and re-run one job. The last two re-execute only the selected jobs and their dependents, so rerunning `benchmarks` alone reruns no Linux cell and no `linux-gates`, while rerunning a failed `linux` cell also reruns `linux-gates`. A full rerun of a draft-era run still skips filter and fails linux-gates. Re-run jobs re-enter concurrency. | Same | Full rerun: every paid cell of the original event. Failed-only or single-job rerun: only the selected paid jobs and their paid dependents. | [I] Full rerun: as the original event. Partial rerun: `linux-gates` is re-evaluated only when it or one of its dependencies (filter, settle, linux) is rerun; otherwise its existing check stands. | [S] Controller: rerun targets are never cancelled (ci_cancel_controller.py:375), and any current-head rerun disables cleanup (305-306). [O] No attempt > 1 among the last 100 ci or rust runs. **Unobserved.** |
| 21 | Cancellation by concurrency | Only non-PR groups cancel in progress: push, manual, label, schedule and merge_group (unreachable today) (ci.yml:96; rust.yml:87). PR and candidate groups queue instead. In a cancelled run, `linux-gates` still runs (`always()`). `linux-gates` needs only filter, settle and linux (ci.yml:477), so it can start only after all three have finished. [O] In every observed cancellation (e.g. 39490583816) a dependency was `cancelled`, so "Required Linux gate" failed at steps 1-5 and "Reject workflow cancellation" (`if: cancelled()`) was skipped because `cancelled()` was false. | Same (rust-gates) | Already-started cells are lost | Outcome by timing (section 4): (a) no job had started → no `linux-gates` check ([O] 39492190159, 0 jobs); (b) a started gate dependency (filter, settle or linux) was cancelled or failed → FAIL from steps 1-5 ([O] 39490583816); (c) `linux-gates` had already completed and only a non-dependency such as `benchmarks` (ci.yml:812) was still running → its existing check stays, success if it passed ([I]); (d) the cancel lands while `linux-gates` itself is running → it fails only if `cancelled()` is true for the reject step ([I], not observed). A pending run displaced by `queue: single` never starts, so case (a) applies. | [S] ci.yml:96-97, 478, 511-513; rust.yml:87-88, 442, 466-468. Test `test_cancellation_runs_and_fails_the_required_aggregate`. |
| 22 | Cancellation by the cancel-ancestors controller | See row 5 and section 5. | Same | None started by the controller itself | Cancelled run, on an **ancestor** SHA only. Outcome by timing (section 4): (a) no job had started → no `linux-gates` check ([O] 39492190159, 0 jobs); (b) a started gate dependency (filter, settle or linux) was cancelled or failed → FAIL from steps 1-5 ([O] 39490583816); (c) `linux-gates` had already completed and only a non-dependency such as `benchmarks` (ci.yml:812) was still running → its existing check stays, success if it passed ([I]); (d) the cancel lands while `linux-gates` itself is running → it fails only if `cancelled()` is true for the reject step ([I], not observed). | [S] ci-label-retrigger.yml:61-83. [O] rows 5 and 8. |
| 23 | Fork PR (`pull_request` from a fork) | [I] Same as rows 1-7: ci.yml and rust.yml have no fork guard. The benchmarks comment step is skipped (ci.yml:857). Label retrigger and the controller exclude forks. | Same | [I] Yes, the test runners (free since CSL-408), subject to GitHub's fork-approval setting (not verified). A draft fork starts none (row 1). | Draft fork: as row 1 (FAIL). Non-draft fork: as row 2. [I] While a run awaits fork approval, no job starts and no check is posted. | [S] ci-label-retrigger.yml:35; ci_cancel_controller.py:147-148. |

## 4. Aggregation semantics

### `linux-gates` (required), ci.yml:473-513

- **Runs when:** `always() && github.event_name != 'schedule'` (ci.yml:478). It runs even after
  failures, and after a cancellation once the run's jobs have started. A run cancelled while
  still queued or pending starts no job, so `linux-gates` is never evaluated and no check is
  posted (rows 5, 21, 22; [O] 39492190159, 0 jobs). On schedule it is skipped by its job-level `if`; GitHub still records a `skipped` `linux-gates` job/check (row 19).
- **Inputs:** filter, settle and linux results; `run-tests` output; `SETTLE_EXPECTED` =
  `pull_request && synchronize && !draft` (ci.yml:483-488).
- **Not inputs:** windows, macos, benchmarks and Rust. A `ci:full` PR with failing Windows or
  Rust still passes `linux-gates`.

Exact script (ci.yml:492-510), evaluated in order:

| Step | Condition | Result |
| --- | --- | --- |
| 1 | `FILTER_RESULT != success` (skipped on drafts, failure on stale or invalid dispatch, cancelled, or empty) | exit 1 |
| 2 | `SETTLE_RESULT` is `success` | continue |
| 2 | `SETTLE_RESULT` is `skipped` and `SETTLE_EXPECTED == false` | continue |
| 2 | any other settle result (failure, cancelled, empty, or skipped when it was expected) | exit 1 |
| 3 | `RUN_TESTS == true` and `LINUX_RESULT == success` | exit 0 |
| 4 | `RUN_TESTS == false` and `LINUX_RESULT == skipped` | exit 0 ("Explicit docs-only/no-change skip") |
| 5 | anything else (linux failure, cancelled, or skipped while tests were required; `RUN_TESTS` empty or unknown) | exit 1 |
| 6 | [I] not observed: `cancelled()` true when the job runs (`if: cancelled()`, ci.yml:511-513) | step exits 1. This applies only while `linux-gates` itself is running (case (d) in row 21). In observed cancellations `cancelled()` was false and this step was skipped; fail-closed for a cancelled dependency comes from steps 1-5 (case (b)). If `linux-gates` had already completed, its check is unchanged (case (c), [I]). |

[I] `needs.linux.result` aggregates the 3.11 and 3.12 matrix cells: it is `success` only when
every cell succeeded.

### `queue-gates` (advisory), ci.yml:517-551; `rust-gates` (advisory), rust.yml:439-468

Both import `tools/required_ci_gate.py`:

- `settled(result, expected)` (tools/required_ci_gate.py:6-9): `expected` must be the literal
  `"true"` or `"false"`. It passes on `success`, or on `skipped` only when `expected == "false"`.
- `gate(filter, required, results)` (tools/required_ci_gate.py:12-16): fails unless filter is
  `success`, `required` is `"true"` or `"false"`, and `results` is non-empty. Every result
  must then equal `success` when required is true, or `skipped` when it is false.
  `cancelled`, `failure`, empty and unexpected `skipped` all fail.
- `queue-gates` = settled AND gate(linux) AND gate(windows). Windows is required only if
  `run-tests == true` and the event is `merge_group`, `push`, a dispatch without
  `scope=linux`, or a PR carrying `ci:full` (ci.yml:536-547).
- `rust-gates` = settled AND gate(filter, `has-cells`, [`rust-book` result]), with
  `if: always()` (rust.yml:442, 451-465). It also runs on dispatch.
- Both have the "Reject workflow cancellation" step (ci.yml:549-551; rust.yml:466-468).
- [S] ci-queue-rollout.md:74-75 caveat: a later Linux-scope dispatch can report a narrower
  successful `queue-gates` after a full-scope failure on the same head.

### `candidate-ci-gates` (staged), ci.yml:555-582

This job runs only for candidate dispatch. It requires `scope=full`, filter `success`, settle
`skipped`, `run-tests=true`, and linux, windows and macos all `success`.

## 5. Duplicate and cancellation policy

### Concurrency groups

| Workflow | Group key | cancel-in-progress | queue |
| --- | --- | --- | --- |
| ci.yml, ordinary PR | `ci-ci-<PR>-default` or `-draft` | false | `max` |
| ci.yml, label dispatch | `ci-ci-<PR>-label-<expected head>-<scope>` | true | `single` |
| ci.yml, manual dispatch | `ci-ci-<ref>-manual-<scope>` | true | `single` |
| ci.yml, candidate dispatch | `ci-ci-<PR or ref>-candidate-<id or run_id>` | false | `max` |
| ci.yml, push / schedule / merge_group | `ci-ci-<ref>-default` / `-nightly` / `-queue` | true | `single` |
| rust.yml | Same shape with the `rust-rust-` prefix and no nightly | same | same |
| ci-label-retrigger, `workflow_run` | `ci-cancel-controller-<run_id>` (unique) | true (no-op) | `single` (omitted; GitHub's default) |
| ci-label-retrigger, `labeled` | `ci-label-retrigger-<wf>-<PR>-<head>-<label>` | true | `single` (omitted; GitHub's default) |
| review-gate | `review-gate-<PR or ref>` | true | `single` (omitted; GitHub's default) |
| claude.yml writer jobs and claude-conflict-writer.yml | `claude-<PR>` (job level) | false | `max` |

Sources: [S] ci.yml:94-97; rust.yml:82-88; ci-label-retrigger.yml:24-28; review-gate.yml:23-25;
claude.yml:562-565, 784-787, 1576-1579, 3085-3088, 3902-3905, 4692-4695; claude-conflict-writer.yml:34-37, 128-131, 937-940. Pending-run caps ([GitHub workflow syntax, `concurrency`](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#concurrency)):
`queue: max` keeps up to 100 pending runs in queue order (PR and candidate groups, the Claude
writers in claude.yml and claude-conflict-writer.yml); `queue: single` keeps one pending run and cancels the previous pending one (push,
schedule, manual and label groups). GitHub rejects `queue: max` with `cancel-in-progress: true`;
ci.yml:96-97 never pairs them.

### Settle

- Only non-draft `synchronize` runs `settle` (`sleep 120`, ubuntu-latest). Every paid job and
  every aggregate accepts `success`, or `skipped` when settle was not expected
  (ci.yml:320-344; rust.yml:304-323).
- Because ordinary PR groups do not cancel in progress, settle saves money only when the
  controller cancels the waiting run during the 120 s window. [O] Run 39492190159 was
  cancelled while pending, before any job started.
- The ci.yml:316-319 comment still attributes this saving to concurrency (#157). That comment
  is stale (gap G5).

### Cancel-ancestors controller

This summarises [ci-cancellation-controller.md](ci-cancellation-controller.md).

- **Trigger:** `workflow_run` `requested` or `completed` for `ci` or `rust`, only when the
  triggering run's event is `pull_request` (ci-label-retrigger.yml:15-17, 64).
- **Code:** the controller runs from the default branch (sparse checkout, ci-label-retrigger.yml:68-73).
- **What it cancels:** only runs that meet all of these conditions
  (ci_cancel_controller.py:373-379):
  - event `pull_request` on ci.yml or rust.yml
  - same repository
  - exactly one PR association
  - attempt 1
  - status in `queued`, `pending`, `requested`, `waiting` or `in_progress`
  - head SHA a **strict ancestor** of the live PR head (compare `ahead`, merge base = old SHA)
- **Additional requirement:** the same workflow must have a current-head witness that is
  active, or completed with `success` (ci_cancel_controller.py:295-309).
- **It never cancels:**
  - a run at the live head SHA (ci_cancel_controller.py:376; `strict_ancestor` returns False
    when the SHAs are equal, line 278)
  - divergent, behind or identical commits (line 284)
  - reruns (line 375)
  - dispatch, label, candidate, push, schedule or merge_group runs (line 142)
  - fork runs (lines 147-148)
  - runs of draft, closed or merged PRs (notice no-op, lines 359-364)
- It also refuses on any changed or ambiguous evidence. Budget: 160 calls and 120 s
  (lines 34-42).
- [O] Multiple controller runs can request cancellation of the same target. Run 39490583816 had
  4 requests (39492190855, 39492191773, 39492193688, 39492195928). A later POST can return 409,
  which is handled by one reread (39493574339).

### Identical-head twins: B2 = do not cancel

- **Founder decision B2:** identical-head pending twins are not cancelled.
- **Code:** both exclusions above enforce this.
- **Effect:** twins in the ordinary group are serialized by `queue: max`. Each executes in full
  only while no newer head supersedes them. Once a later `synchronize` advances the head, both
  twins are strict ancestors and the controller may cancel either one, as in the first observed
  example below.
- **[O] Observed twins**, two `pull_request` runs on the same head created within 1 s:
  - ci 39490583924 and 39490583816 (`5b7c2b15b8`): the second started after the first
    finished at 20:25:30Z, re-ran settle, and started L×3 before an ancestor cancel.
  - ci 39492186505 and 39492190159 (`1f0628b5d3`).
  - rust 39492186705 and 39492189661 both ran L×2 to success, back to back. The duplicate
    cost was about $0.012 (3 L-min, at Blacksmith rates before CSL-408).
- The REST API does not expose the `pull_request` action, so the cause of the duplicate
  events is undetermined.

Twins across groups run concurrently and do not cancel each other: for example, a
ready_for_review run and a label dispatch on the same head (rows 7 and 9). Other cancellation
still applies. Once a newer `synchronize` advances the head, the controller can cancel the
ordinary run as a strict ancestor (section 5). A repeated label dispatch for the same head and
scope cancels the earlier label run, because that `ci-ci-<PR>-label-<head>-<scope>` group has
`cancel-in-progress: true` (ci.yml:95-96).

## 6. Test evidence

Every test file below runs in the required `linux` job (ci.yml:436-443); `test_review_gate` and
`test_queue_gates` run there through `unittest`. Under `app/tests`, `benchmarks/test_perf_compare.py:225` reads `ci.yml` (benchmarks step
shape), `test_stuck_thread_watch.py:13` reads `stuck-thread-watch.yml`, `test_linear_pr_sync.py`
uses a workflow path as fixture data, and three Rust parity tests mention `rust.yml` in comments
(`quant/test_costs_rust_parity.py:32`, `quant/test_signals_rust_parity.py:31`,
`ingest/test_admission_rust_parity.py:32`).
The CI contract tests live in `tools/`.

At the base commit these ran locally (Python 3.13):

- `test_ci_dispatch_scope`: 15 OK
- `test_ci_settle`: 4 OK
- `test_ci_cancel_controller`: 66 ran, 1 environment-dependent failure (`test_queue_lint_exception_is_exact_and_limited_to_named_paths`, which asserts actionlint 1.7.7 behaviour and passes in the `linux` job)
- `test_candidate_workflows`: 12 OK
- `unittest tools.test_review_gate tools.test_queue_gates`: 36 OK
- `test_ci_docs_filter` (added by #223, run at `578a8ca8`): 10 OK

| Row or claim | Status | Test(s) |
| --- | --- | --- |
| 1, 3: drafts skip filter; aggregate fails | PROVEN (expression and shell level) | test_ci_dispatch_scope `test_dispatch_runs_linux_and_drafts_keep_existing_guard`, `test_linux_gate_fails_closed`; test_queue_gates `test_exhaustive_aggregate_states` |
| Draft and default groups are separate; PR runs queue without cancel | PROVEN | `test_pr_concurrency_queues_old_heads_but_isolates_drafts`; test_ci_cancel_controller `test_ordinary_only_policy_and_shared_identity`, `test_delayed_A_admission_does_not_displace_B_active_or_pending` |
| 2, 4, 6, 7: settle table and eligibility of paid jobs | PROVEN | test_ci_settle `test_event_table_and_settle_outcomes`, `test_known_non_synchronize_paths_are_not_accidentally_suppressed`; `test_settle_debounce_gates_test_cells_only_on_pr_synchronize` |
| ci.yml `decide` for pull_request, push and schedule (docs-only regex, 24 h idle skip) | **PARTIAL** | pull_request: `tools/test_ci_docs_filter.py` `DecideScriptTests` runs the real script on temporary-repo diffs, including renames (CSL-397). workflow_dispatch: `test_dispatch_runs_linux_and_drafts_keep_existing_guard`. Candidate docs case: test_candidate_workflows `test_docs_candidate_executes_all_five_python_and_rust_cells`. push and schedule remain untested. |
| Rust path filter (merge tree, push and queue ranges) | PROVEN | `test_actual_rust_merge_tree_filter_with_diverged_base`, `test_label_regex_and_rust_filter_route_native_paths_consistently`, test_queue_gates `test_actual_rust_path_and_platform_matrix` |
| Platform selection per event (Windows, macOS, Rust cells) | PROVEN | `test_platform_conditions_and_actual_rust_matrix` |
| 5: burst cancellation by controller | PROVEN offline, PARTIAL end-to-end | test_ci_cancel_controller `test_new_head_requested_cancels_only_obsolete_running_or_queued_run`, `test_delayed_old_event_or_completion_cannot_cancel_current_head`, `test_completed_successor_can_retire_old_work_without_rerunning`; live [O] row 5 |
| 8: B1 draft notice no-op | PROVEN | `test_verified_draft_trigger_pr_is_notice_noop`, `test_draft_trigger_pr_still_requires_full_identity_evidence`, `test_pr_turning_draft_during_cancellation_plan_still_refuses`, `test_strict_live_reread_never_admits_draft` |
| 9-12: label eligibility, scope, dispatch argv, no `labeled`/`unlabeled` trigger on ci/rust | PROVEN | `test_label_eligibility_and_scope`, `test_actual_dispatch_shell_argv_and_rust_path_filter`, `test_dispatch_defaults_and_existing_events`, `test_out_of_order_label_dispatch_cannot_cancel_current_work` |
| 13: stale dispatch refused | PROVEN (refusal) / **GAP** (which SHA the failing check binds to) | `test_actual_dispatch_context_validation_rejects_stale_and_partial_context` |
| 14: base change does not trigger CI | PROVEN (types only) | `test_dispatch_defaults_and_existing_events` asserts the type list. Strict-mode behaviour is a ruleset, not tested. |
| 15: push to main platforms and concurrency | PROVEN | `test_platform_conditions_and_actual_rust_matrix`, `test_manual_nightly_and_queue_concurrency_isolation`, `test_ordinary_only_policy_and_shared_identity` |
| 16: merge_group | PROVEN offline / **GAP** live (no queue) | `test_merge_group_trigger_and_isolated_concurrency`, test_review_gate `test_queue_discovery_*` |
| 17-18: manual and candidate dispatch | PROVEN offline | test_candidate_workflows (all 12 tests), `test_merge_context_reaches_every_matrix_checkout` |
| 19: schedule (macOS only, no linux-gates) | PARTIAL | `test_platform_conditions_and_actual_rust_matrix` (schedule row), `test_linux_gate_fails_closed` (`if` string). The idle-skip shell is not executed. |
| 20: reruns | PARTIAL (controller only) / **GAP** (GitHub semantics) | `test_initial_old_head_manual_rerun_is_never_a_target`, `test_older_id_rerun_has_no_assumed_ordering`, `test_rerun_appearing_after_discovery_refuses_cleanup` |
| 21: the reject step fails the aggregate when `cancelled()` is true, even after successful dependencies | PROVEN for that synthetic `cancelled() == true` path only. The timing cases (a)-(d) are PARTIAL: (a) and (b) observed, (c) and (d) [I] | test_ci_settle `test_cancellation_runs_and_fails_the_required_aggregate`; test_queue_gates `test_cancelled_workflow_cannot_pass_even_after_successful_dependencies` |
| `linux-gates` aggregation table (section 4) | PROVEN | test_ci_settle `test_required_gate_executable_result_table` (exhaustive over settle, run-tests and linux) |
| B2: identical-head twins never cancel each other (a newer head can still retire them as ancestors) | **PARTIAL** | The code paths are covered indirectly (`test_strict_ancestry_required_not_timestamp_or_sha_inequality` mocks compare `identical`). No fixture places two active runs on the live head and asserts both survive. |
| Required-check names unchanged | PROVEN | test_candidate_workflows `test_existing_required_check_names_remain_exact` |
| Devin status semantics (`review-gate`, advisory) | PROVEN | tools/test_review_gate.py (30 tests) |

## 7. Paid workloads outside CI admission

- **claude.yml (Reliability lane; restructured by #216, CSL-352/CSL-377):**
  - Triggers: workflow_dispatch, repository_dispatch `devin-review-fix`, issue_comment,
    pull_request_review_comment, pull_request_review, and `pull_request` limited to fixer and
    gate paths (claude.yml:3-46).
  - Jobs: `resolve-pr`, `event-paths`, `forward-conflict-scan`; the `@claude` path
    `mention-prepare` → `claude-mention` → `mention-publish`; the Devin path `fixer-prepare` →
    `devin-review-fix` → `fixer-publish`. All run on `ubuntu-latest` (claude.yml:93, 506, 537,
    566, 788, 1580, 3089, 3906, 4696), so there is **no test-runner compute**. conflict-scan.yml
    calls `claude-conflict-writer.yml` once per selected PR, at most three per scan (`MAX_PRS = 3`; the rest are marked `over-cap`, conflict-scan.yml:80, 179-180, 371) with the same
    prepare → model → publish split, also on `ubuntu-latest` (claude-conflict-writer.yml:38, 132, 941).
  - Model usage goes through `CLAUDE_CODE_OAUTH_TOKEN` (claude.yml:1263, 4382; subscription
    route per ci-cost-budget.md:57-58). The model jobs install the runtime (claude.yml:828-830,
    3946-3948), but the model's only shell command is a fixed helper for git reads, GitHub reads
    and a local commit; writes are queued to an outbox, and `git push` is disallowed
    (claude.yml:843-862, 1369-1370, 4485-4486). No claude.yml step runs `pytest` or `ruff`: the
    model jobs still install them ("Install acceptance-gate deps", claude.yml:828-830, 3946-3948) but
    cannot invoke them, and `event-paths` (claude.yml:512-528, only on PRs that touch the fixer's
    paths) runs the fixer's own `tools/test_*.py` scripts under `python3` plus actionlint.
  - [S] Pushes come only from the model-free publish jobs. Each mints a Claude App token through
    the OIDC exchange (`mint_app_token`, claude.yml:2296-2334, 5413-5451; claude-conflict-writer.yml:1659-1697;
    `id-token: write` at claude.yml:1586, 4702; claude-conflict-writer.yml:947)
    and pushes one ordinary fast-forward, no force (claude.yml:2745-2746, 5863;
    claude-conflict-writer.yml:2109). [I] An App-token push emits `synchronize` and starts
    ordinary CI (rows 4-5) without the approval step a `GITHUB_TOKEN` push would need (GitHub
    docs, triggering a workflow). [O] Before #216, `ci` runs on fixer commits were started by
    `claude[bot]` about 12 s after each commit: 39354662754 (#212 `8ede5ff793`), 39353490459
    (#208 `571cb1ce11`) and 39079041390 (#199 `e875f2b8c3`). [O] After #216, run 39546148972 (#228,
    `pull_request_review`, 2026-10-21) lists the nine jobs, all `ubuntu-latest`; `resolve-pr` and
    `fixer-prepare` ran and the model and publish jobs were skipped. Run 39546857350 (#227,
    `issue_comment`, 2026-10-21) reached `mention-publish`, which succeeded after posting one queued
    write without pushing. No post-#216 fixer push has been observed, so the App-token push remains [I].
  - Human pause (#216): a `claude:pause` or `hold` label on the PR, or the repository variable
    `CLAUDE_FIXER_PAUSE=true` for every PR, stops the writers before any checkout or reservation
    (`mention-prepare` claude.yml:603-606, 654-662, 701; `fixer-prepare` claude.yml:3132-3134, 3183-3191,
    3230; conflict-writer `prepare` claude-conflict-writer.yml:72-84; the scan never selects a paused PR,
    conflict-scan.yml:78-79, 167-168, 333-334) and is rechecked by the publishers (claude.yml:2187,
    2386-2395, 5304, 5503-5512; claude-conflict-writer.yml:1550, 1749-1758).
  - The eight-commit guard now covers both claude.yml writers: the `cap` step in `fixer-prepare`
    for the Devin path (claude.yml:3306-3346) and the `mention-prepare` streak check for `@claude`
    (claude.yml:726-731, `LOOP_CAP = 8` at 606). v2.1 said `claude-mention` had no cap; that is no
    longer true. **The conflict writer has no eight-commit guard and no cross-run attempt budget.**
    Its receipts helper defines `MAX_ATTEMPTS = 3` (claude-conflict-writer.yml:976), but only that
    helper's `main()` checks it (1441-1469), and nothing in the workflow runs it: `prepare` reserves no
    attempt, and `publish.py` loads the helper as a module (2300-2303). Each run is bounded only to one
    publish attempt (2318, 2394-2403) and one validated merge commit (702). conflict-scan.yml starts it
    on the two-hourly schedule and on manual dispatch, never on a `main` push (conflict-scan.yml:12-14,
    355), so a PR that stays conflicted can receive a Claude merge attempt on every scheduled scan.
- **Legacy Codex READY label (active):**
  - With `CANDIDATE_COORDINATOR` not `true`, the legacy job adds `ci:full` through
    `CODEX_TRIGGER_PAT` (codex_gate.py:7, 84, 1350-1356; codex-gate.yml:53-87).
  - [I] PAT-authored label events trigger workflows, so this starts row 9 (full matrix).
  - [S] The READY label is recorded as gate-owned (`record_ci_ownership`, codex_gate.py:1365).
    On the next non-draft PR event, `_handle_request` runs `_clear_stale_ready`
    (codex_gate.py:1144-1168, 1202-1211), which revokes a gate-owned `ci:full` whose receipt
    belongs to an earlier head. So the **first push after READY** still carries the label in
    its event payload and runs the full matrix; later events normally do not. The scheduled
    recovery path also clears a stale gate-owned label (`clear_stale_ready=True`,
    codex_gate.py:1271-1278), so if it runs after a head change, even the next event can start
    without `ci:full`.
  - A `ci:full` added **by a person or by Devin** is not gate-owned and is never cleared
    automatically. It applies to every later qualifying event until someone removes it.
    [O] Of the 13 recent `ci:full` label events (PRs #166–#193, 2026-10-17), 12 came from
    CDanTheMan, samir-shah-ahmed or Devin. One (#166) came from NotSkittlz, which is also
    the identity behind `CODEX_TRIGGER_PAT`, so it may be a READY label. None was removed.
  - Two independent selectors decide what the label starts:
    - **ci.yml (Python cells):** Windows and macOS start only when `run-tests == true`
      (ci.yml:597-602, 708-715). The ci.yml filter treats `docs/` and `*.md` changes as docs-only,
      except the swarm dirs, everything under `app/`, the docs the tests read and `.gitignore`
      (ci.yml:290-313, CSL-397). So a docs-only event starts no Python W or M.
    - **rust.yml (Rust cells):** the hosted-cell selector ignores `run-tests` and matches
      the diff against `RUST_PATHS` (rust.yml:201-299, `relevant()` at 242-280). Those
      paths include the bare prefixes `rust/` and `.github/`, so **Markdown under `rust/`
      or `.github/` is Rust-relevant**. For example, a PR that only edits the tracked
      `.github/pull_request_template.md` starts Rust L×2, plus Rust W×2 and
      Rust M with `ci:full` (`RUN_WINDOWS`/`RUN_MACOS` come from the label,
      rust.yml:206-207). A non-Rust code event starts no Rust cells. See G18.
  - ci-candidate-readiness.md:51-54 ("New READY decisions do not add `ci:full`") applies
    only to the coordinator path, which is currently off ([O] 39496664994).
- **Benchmarks:** `benchmarks` (L×1, about 0.5-1 min) runs on a non-draft PR
  event with `run-tests=true` when `settle` succeeded or was intentionally skipped
  (ci.yml:812-821), so a failed or cancelled settle skips it. It is outside every aggregate, so its failure never blocks.
  It is also not covered by the dispatch paths (ci.yml:804-822).
- **Main-push fallback:** row 15. Before CSL-408 this cost about $0.196 for ci plus $0.048 for rust per
  relevant merge ([O]); since CSL-408 it is $0. On GitHub-hosted `windows-2025` the Python Windows cells
  took 22-28 min ([O] run 39531231867), against 6-11.5 min on Blacksmith, so the fallback now finishes later. It is retained by owner decision (ci-queue-rollout.md:20-22, 116-117).
- **Manual and label dispatch** bypass the docs-only filter (`run-tests` forced true,
  ci.yml:231-235; rust.yml:205).

## 8. Known gaps and follow-ups

| ID | Gap | Class | Suggested follow-up |
| --- | --- | --- | --- |
| G1 | Only `linux-gates` and `Devin Review` are required. The repository ruleset `required-ci` allows admin PR bypass ([O] RepositoryRole 5). The org ruleset `protect-main`'s bypass actors aren't readable from this session ([I] until an admin read shows them). Windows, macOS, Rust and the `queue-gates`/`rust-gates`/`review-gate` aggregates are advisory. | [O] required checks and repository bypass; [I] org bypass | Already staged in ci-queue-rollout.md. Owner decision; no change here. |
| G2 | Identical-head twins observed (section 5). B2 keeps them, so each twin re-executes settle and paid cells serially unless a newer head lets the controller cancel them as ancestors. The duplicate-event cause is unknown, and there is no explicit twin test. | [O] / PARTIAL | Add a controller fixture asserting two active live-head runs both survive. Investigate the duplicate `pull_request` deliveries (read-only). |
| G3 | Stale label dispatch (row 13), and same-key label or manual re-dispatch (cancel-in-progress true), can create a failing `linux-gates` check run on the **current** head. Whether a dispatch check run affects the required check is unverified; GitHub's doc says it does not. | [I] | Observe or test which SHA receives the check. Consider documenting the "rerun or wait for the ordinary run" recovery. |
| G4 | The ci.yml PR filter diffs raw `base.sha..head.sha` (ci.yml:256-257, 277), while rust.yml uses the merge tree (rust.yml:216-228). On a stale base, ci over-selects tests (fails open, extra spend). Since CSL-397, `tools/test_ci_docs_filter.py` `DecideScriptTests` runs the real decide script on pull_request diffs (section 6). The push and schedule paths still have no executing test. | [S] | Align with the rust.yml merge-tree diff in a separately admitted CI task, and add shell-execution tests for push and schedule. |
| G5 | Stale workflow comments: ci.yml:316-319 (settle relies on #157 concurrency cancel), ci.yml:17 and 718-719 ($50 budget, superseded by ci-cost-budget.md:48-50; CSL-408 removed the benchmarks cost note that was at ci.yml:21-23), ci.yml:515-516 (`queue-gates` "required"). | [S] | Comment-only fix in a human-assigned workflow issue. |
| G6 | `merge_group` paths and `queue-gates` are untested live. The merge queue is not enabled. | [O] | Covered by the ci-queue-rollout.md activation sequence. |
| G7 | A PR created with `ci:full` or `enhancement` gets both `opened` and `labeled`, which means an ordinary run plus a label dispatch (duplicate full matrix). | [I] | Observe once (read-only) and record. |
| G8 | `ci:full` added by a founder or Devin persists, so every later qualifying push runs Windows and macOS, plus Rust Windows and Rust macOS (subject to the path filters, section 7). The legacy READY label is gate-owned and is cleared on the next push, after one full-matrix run. | [S]+[O] | Addressed by the AGENTS.md CI-etiquette rule merged at this base (CSL-393, #222; AGENTS.md:314-329): remove a manual `ci:full` once its full run passes and no further automatic READY is needed. A manual removal holds READY and, with the candidate coordinator on, candidate runs and MEDIATE handoffs. Recursion's equivalent merged as CSL-395 (#259), and its human-hold caveat as CSL-403 (#261). |
| G9 | The task brief said claude.yml has Blacksmith runners. It does not at the base commit. Its paid effect is indirect, through pushes that trigger CI. | [S]+[O] | Correct the brief and the Reliability-lane notes. |
| G10 | GitHub rerun semantics (payload reuse, concurrency re-entry, and which dependents a failed-only or single-job rerun re-executes) have not been observed. | [I] | Record the next real rerun. |
| G11 | Duplicate controller cancel POSTs (4 per target observed) consume API budget. Harmless today. | [O] | None required. Note it in the controller doc if budget pressure appears. |
| G12 | Main-push groups cancel in progress, so a fast second merge cancels the first fallback run and the minutes already spent are lost. The old main SHA's `linux-gates` outcome depends on timing, as in row 21: no check if no job had started (a), red if a gate dependency was cancelled (b), green if `linux-gates` had already passed while only Windows was still running (c; `benchmarks` runs only on `pull_request`, ci.yml:821), and red only if the cancel lands while `linux-gates` itself is running and `cancelled()` is true for its reject step (d). | [I] | Observe. It is consistent with "keep fallback" but undocumented. |
| G13 | The nightly schedule ran about 5.5 h late (created 12:44Z for the 07:17 cron). | [O] | Note in AGENTS.md:399 that schedule time is best-effort. |
| G14 | Fork PRs can start test jobs in ci.yml and rust.yml (no fork guard; free since CSL-408, but they use capacity). Exposure depends on the unverified fork-approval setting. | [I] | Lucas set "Require approval for all external contributors" in both repos, 2026-10-20 (relayed; not readable from this session). |
| G15 | Org-level ruleset listing (`gh api orgs/CSL-Laboratories/rulesets`) is blocked in this session. The org ruleset was read through the repo-scoped endpoints `rulesets/25015519` and `rules/branches/main`. | [O] | None. Repo-scoped effective rules are authoritative for `main`. |
| G16 | **Docs-only green no-op: fixed by CSL-397 (#223, merged at this base).** The filter now runs tests for everything under `app/` (including the LLM prompts `app/workflows/prompts/*.md`), the 21 test-read files from the CSL-392 audit, and `.gitignore` (ci.yml:303-307). It diffs with `--no-renames` (ci.yml:277), so renaming a test-read file still runs tests. `tools/test_ci_docs_filter.py` runs the real decide script and fails if a test references a file the filter skips; it runs in the required `linux` job (ci.yml:443). Residual: plain docs that no test reads still skip, as intended, and the static guard can't see dynamically built paths. The pre-fix description follows, cited at the previous base `be97ff3b` because this base no longer behaves this way. The PR filter marked `docs/` and `*.md` changes (apart from the listed swarm dirs and `.py`/`.json`/`.yaml`) as no-test (`be97ff3b` ci.yml:288-300). `linux-gates` then passes with `linux` skipped (section 4, step 4). `git grep -E "\.md['\"]" -- 'app/tests/*.py' 'tools/test_*.py'` prints 69 matching lines in 17 files; many are temporary paths (`comment.md`, `brief.md`) ([O]). Concrete cases: `app/tests/test_o7_operator_contract.py:51-54` hashes four `docs/replacement/*.md` contracts, and `app/tests/system/test_rw01_market_day.py:771` reads `docs/setup/accounts-and-keys.md`. A change to only those docs gets a green required check without the tests that read them, the same class recursion fixed in CSL-376. Code cannot skip this way: any `.py` change forces `run-tests`. The risk of a Rust PnL change landing untested belongs to G1 (`rust-gates` is advisory), not to this regex. | [S]+[O] | Audited in CSL-392 (21 test-read files, including the LLM prompts). Fixed by option B, CSL-397 (#223): `app/**` and the test-read docs always run tests, plus a guard test. |
| G17 | Two fail-open-to-spend branches: `ci.yml:240-245` (nightly macOS on an unreadable commit window; free runner) and `rust.yml:226-227` (`DIFF_FILES='rust/'` fallback starts L×2). They over-spend rather than skip tests. | [S] | Note only. |
| G18 | **The Python and Rust selectors disagree on Markdown.** ci.yml's filter treats `rust/*.md` and `.github/*.md` as docs-only (ci.yml:290-313), while rust.yml's `RUST_PATHS` treats them as Rust-relevant (rust.yml:242-280). Such a change skips the required ci.yml Python matrix (G16), but still starts the Rust cells, which run their own pytest list including `tests/test_invariants.py` (rust.yml:424-430): L×2; plus W×2 and the free macOS cell with `ci:full`; plus W×2 on a `main` push. This is over-spend for the required check. The Rust cells' pytest list (including `tests/quant/test_pnl.py`) still runs, but `rust-book` is advisory, so a red result does not block merge (G1). | [S] | **Accepted** by Lucas, 2026-10-20 (relayed on CSL-385). CSL-397 (the G16 fix) did not change this case: ci.yml:308 still skips `\.md$`, so the Python/Rust Markdown split remains for `rust/*.md` and `.github/*.md`. |

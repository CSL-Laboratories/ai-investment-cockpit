# CI event-to-check contract

Contract version: v1 (2026-10-20)

**Versioning.** Any change to a workflow, `scripts/ci/**`, a required check or the rulesets
that changes a row here updates this file in the same PR. Bump the version for a semantic
change (v1 → v2) and the patch for a correction (v1.1). Record the new base commit. Versions
count merged changes; edits made during a PR's own review are part of that version. Tracked
under CSL-255 / CSL-385.

- **Base commit:** `be97ff3b462d3dc282844747b9080d5a93238714` (`main`, merge of #220). The rows were traced at `7b0dcf27` (#215). The only CI-file change since then is #220 (CSL-383), which adds three lines in two places in `claude.yml` and once in `conflict-scan.yml`. All `file:line` citations are for this base.
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
  [AGENTS.md Hosted CI](../AGENTS.md#hosted-ci) (AGENTS.md:219-232).

## Evidence classes

| Tag | Meaning |
| --- | --- |
| **[S]** | Source-documented: `file:line` at the base commit. `ci.yml:95` means `.github/workflows/ci.yml` line 95; other workflow names are under `.github/workflows/`. `ci_cancel_controller.py`, `codex_gate.py`, `candidate_gate.py` and `claude_fixer_receipts.py` are under `scripts/ci/`; `required_ci_gate.py` is under `tools/`. |
| **[O]** | Locally observed on 2026-10-20 through read-only REST (`gh api`): run, job or check-run IDs given. Older runs may have used older YAML. `workflow_dispatch` runs use the dispatched branch's YAML. |
| **[I]** | Inferred from GitHub platform behaviour or from reading code. Not tested and not observed here. |

Paid-runner cost figures use the [cost budget](ci-cost-budget.md) method: each started job
rounded up to whole minutes, at $0.004/min Linux and $0.008/min Windows. They are estimates,
not invoices.

## 1. Required checks

[O] `gh api repos/CSL-Laboratories/ai-investment-cockpit/rules/branches/main` (2026-10-20):

| Ruleset | Source | Rules on `~DEFAULT_BRANCH` |
| --- | --- | --- |
| `protect-main` 25015519 | Organization | deletion, non_fast_forward, pull_request (1 approval, thread resolution); bypass: OrganizationAdmin, `pull_request` mode |
| `required-ci` 25019206 | Repository | required_status_checks `strict: true`: `linux-gates` (integration 15368, GitHub Actions), `Devin Review` (integration 811515); pull_request (1 approval, last-push approval); bypass: RepositoryRole 5, `pull_request` mode |

There is **no `merge_queue` rule** in the effective rules. The `merge_group` triggers are
therefore unreachable (section 3, row 16).

| Required context | Producer | Where |
| --- | --- | --- |
| `linux-gates` | GitHub Actions check run, job `linux-gates` in `ci.yml` | [S] ci.yml:462-502. No other workflow defines a job with this name. [S] ci-label-retrigger.yml:1-3 and test `test_dispatch_defaults_and_existing_events` assert this. |
| `Devin Review` | Devin app **commit status** (creator `devin-ai-integration[bot]`), not a workflow | [S] ci-queue-rollout.md:26-32. [O] commit `8f5caa21f1` statuses: `pending` 06:17:07Z, then `success` 06:18:09Z. |

The following checks are **not required** today and are advisory on PRs: `queue-gates`
(ci.yml:506), `rust-gates` (rust.yml:439), `candidate-ci-gates` (ci.yml:544),
`review-gate` (review-gate.yml:28-29), `benchmarks`, `pr-lint`, and every Windows, macOS
and Rust cell. [S] ci-queue-rollout.md:8-15 lists them as proposed.

Strict mode (`strict_required_status_checks_policy: true`) requires the PR branch to be up
to date with `main` before merge. Required checks bind to the PR **head SHA**. [I] When
several `linux-gates` check runs exist on one SHA, GitHub evaluates the most recent one.
[O] Head `4268c7e4b6` of #213 has check run 118452752528 `failure` (04:21Z), then
118455279951 `success` (04:30Z), and #213 merged. That is consistent with this rule, but it
does not prove it, because both rulesets allow admin bypass on pull requests.

## 2. Workflows that start paid runners

[S] grep of `.github/` at the base commit. Only three `ci.yml` jobs and the `rust.yml` `rust-book` Linux and Windows cells run on Blacksmith; its optional macOS cell is `macos-latest`:

| Workflow | Job | Runner | Lines |
| --- | --- | --- | --- |
| ci.yml | `linux` (3.11, 3.12) | `blacksmith-2vcpu-ubuntu-2404` | ci.yml:321-342 |
| ci.yml | `windows` (3.11, 3.12) | `blacksmith-2vcpu-windows-2025` | ci.yml:573-598 |
| ci.yml | `benchmarks` (1 cell) | `blacksmith-2vcpu-ubuntu-2404` | ci.yml:795-811 |
| rust.yml | `rust-book` matrix (Linux 2, Windows 0 or 2) | from `filter` output; the optional macOS cell is `macos-latest` (GitHub-hosted, free) | rust.yml:281-293, 313-328 |

The following run on free GitHub-hosted runners in this public repo: `filter`, `settle`, the
aggregates, `macos` and the Rust macOS cell (`macos-latest`), and all of ci-label-retrigger.yml, codex-gate.yml,
review-gate.yml, claude.yml, conflict-scan.yml, pr-lint.yml, linear-pr-sync.yml and
stuck-thread-watch.yml (`runs-on: ubuntu-latest`).

**Exception to the team rule.** The team rule is that test jobs run on Blacksmith. The
`macos` test job (ci.yml) and the Rust macOS cell (rust.yml:290-293) are test jobs on
GitHub-hosted `macos-latest` ([O] dispatch run 39065062851 used a GitHub-hosted macOS runner).
Blacksmith's macOS label for that role is `blacksmith-6vcpu-macos-latest`. This contract records
the exception and does not change it.

**Correction to the task brief:** claude.yml has **no Blacksmith runner** at the base
commit. Its jobs use `ubuntu-latest` (claude.yml:90, 504, 534, 561, 1318). [O] Across the
last 60 claude.yml runs, all 13 jobs that started used `ubuntu-latest`. See section 7.

## 3. Event matrix

Every paid job needs `filter` = success (ci.yml:328 linux, 580 windows, 804 benchmarks; rust.yml:318). The free `macos` job has the same requirement (ci.yml:691). `filter`
is skipped for draft `pull_request` events (ci.yml:102; rust.yml:93). The ordinary concurrency
key for PR runs is `ci-ci-<PR>-default` or `ci-ci-<PR>-draft`, and `rust-rust-<PR>-…` in
rust.yml (ci.yml:95; rust.yml:86).

"L" means Blacksmith Linux, "W" means Blacksmith Windows, "M" means free macOS. "Rust-relevant"
means the `RUST_PATHS` match in rust.yml:242-263. Note that the list includes `.github/`.

| # | Event | ci.yml jobs | rust.yml jobs | Paid runners started | `linux-gates` | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `pull_request` opened, **draft** | filter, settle, linux, windows, macos and benchmarks are skipped. `linux-gates` and `queue-gates` run. | filter and rust-book skipped. rust-gates runs. | **None** | **FAIL** ("filter job did not succeed"). rust-gates also fails. | [S] ci.yml:102, 312, 467, 481-484; rust.yml:93, 442, 461-464. [O] run 39378544947 (#217): all paid jobs skipped, linux-gates log `filter=skipped linux=skipped`. |
| 2 | opened, **non-draft** | filter decides `run-tests` (ci.yml:255-304). settle skipped. linux if run-tests. benchmarks if run-tests. windows and macos only if `ci:full` is already on the PR in the event payload. | filter diffs the merge tree (rust.yml:216-228). Cells for Linux, plus Windows and macOS with `ci:full` (rust.yml:206-207). | L×2 + L×1 (benchmarks) when run-tests. Rust L×2 when Rust-relevant. With `ci:full`: W×2 and M from ci.yml, plus Rust W×2 and Rust M when Rust-relevant (rust.yml:207, 290-293). | PASS if `linux` succeeded, or if run-tests=false and linux skipped (docs/no-change noop). Otherwise FAIL. | [S] ci.yml:312, 326-334, 586-592, 697-705, 802-810. [O] see row 7. [I] A PR created with labels also gets a `labeled` event, so row 9 can fire as well. |
| 3 | synchronize, **draft** | Same as row 1. | Same as row 1. | None | **FAIL** | [S] as row 1. [O] run 39377719991 (#215 draft-era, head `8f5caa21f1`). |
| 4 | synchronize, **non-draft** | settle runs `sleep 120` on ubuntu-latest, then the same jobs as row 2. | settle, then the same jobs as row 2. | Same as row 2, after about 2 minutes. | Same as row 2. settle must be `success`. | [S] ci.yml:310-319, 475, 485-490; rust.yml:304-311. [O] run 39493569607: settle 20:39:12-20:41:15, linux 3.11/3.12, benchmarks, linux-gates success. 15 L-min, about $0.060. Rust 39493569462. |
| 5 | synchronize **burst** (new head while the old run is waiting in settle or running) | The new run queues in the same group (`queue: max`, no cancel-in-progress). The controller (section 5) cancels active, first-attempt, strict-ancestor runs once a current-head witness exists. The cancelled old run's `linux-gates` runs only if the run had started jobs; its result follows the `linux-gates` column. | Same as ci.yml. | Whatever the old run started before cancellation. Concurrency does not prevent new paid starts; the controller cancels them. | Old SHA: FAIL when a gate dependency (filter, settle or linux) was cancelled or had not finished, or when `linux-gates` itself was running at cancellation (`if: cancelled()` step). [I] PASS if filter, settle and linux had all succeeded before the cancellation (linux-gates needs only those, ci.yml:466; `benchmarks` runs in parallel, ci.yml:801). No check at all if the run was cancelled before any job started ([O] 39492190159: 0 jobs). New head: as row 4. | [S] ci.yml:94-97, 500-502; ci-label-retrigger.yml:15-17, 61-83; ci_cancel_controller.py:367-419. [O] run 39490583816 was cancelled during its linux cells after about 5 L-min (rounded up per job, including benchmarks), and controller run 39492190855 logged `Requested cancellation of verified ancestor run 39490583816`. Pending run 39492190159 was cancelled before any job started (controller 39493573599). Controller 39493574339 logged the HTTP 409 reread no-op. A `requested` event fires while the new run is still pending in the queue (39492190855 at 20:27:56Z; its target started 20:29:36Z). |
| 6 | reopened | As row 1 or 2, depending on the PR's draft flag at the event. | Same. | Same as row 1 or 2. | Same as row 1 or 2. | [S] ci.yml:38; rust.yml:32. Not observed. |
| 7 | ready_for_review | As row 2 (settle intentionally skipped), in the `default` group, not the `draft` group. | As row 2. | Same as row 2. | PASS or FAIL as row 2. This is a new check run on the same head SHA as the earlier draft failure. | [S] ci.yml:95, 312, 475. [O] #215 head `8f5caa21f1`: draft run 39377719991 produced linux-gates `failure` (check 118479517498). Ready run 39379495443 then produced `success` (check 118486614887), with settle skipped and L×2 + benchmarks. |
| 8 | converted_to_draft | **Not a ci/rust trigger.** In-flight non-draft runs continue. | Not a trigger. | Runs already started continue. | Unchanged until the next event. | [S] ci.yml:38; rust.yml:32 (types). codex-gate.yml:12 does listen. Controller: a draft triggering PR is a notice no-op (B1, ci_cancel_controller.py:188-190, 359-364), and any draft transition during a plan refuses (193). [O] B1 merged 2026-10-20T07:10:37Z (#219). Run 39389258630 logged `PR #214 is draft; no cancellation needed`. No controller run has concluded `failure` since 07:11Z. Before B1, draft-triggered controller runs such as 39382558811 failed. |
| 9 | labeled `ci:full` (non-draft, same-repo) | ci.yml and rust.yml are **not** triggered by `labeled`. ci-label-retrigger `retrigger` runs `gh workflow run ci.yml --ref <head branch>` with `scope=full`, `pr_number`, `pr_head_sha` and `merge_sha=github.sha` (the PR merge ref). It also dispatches rust.yml when the PR file list matches the Rust regex. The dispatched run: group `label-<head>-full`, cancel-in-progress **true**, queue `single`. filter validates head and merge (ci.yml:195-213). decide forces `run-tests=true`, even for docs-only changes (ci.yml:231-235). No settle, no benchmarks. | Dispatched rust: `CHECK_PATHS=false`, so all cells for the scope run (rust.yml:205, 279-293). | L×2 + W×2 + M. Rust L×2 + W×2 + M when Rust-relevant. | [O] The dispatch run creates a `linux-gates` check run on the branch tip, which is the PR head when validation passes (39065062851). [I] Unverified: whether a `workflow_dispatch` check run satisfies or replaces the PR's required `linux-gates`. GitHub's troubleshooting doc for required checks says it does not. | [S] ci-label-retrigger.yml:31-59; ci.yml:95-97, 195-213, 231-235. [O] retrigger 39065057684 (scope full) led to ci 39065062851: L 8 min + W 10 min, about $0.112, head `7a112a5205` = PR head. No rust dispatch on this branch at that time ([I] not Rust-relevant). This run predates `queue-gates`, so it executed older branch YAML. |
| 10 | labeled `enhancement` | As row 9 with `scope=linux`, so windows and macos are skipped. If the PR also has `ci:full`, `CI_SCOPE` is `full` (ci-label-retrigger.yml:44) and the fan-out is row 9's. | Linux cells only; the full matrix with `ci:full`. | L×2 (+ Rust L×2); with `ci:full` as row 9 | As row 9 | [S] ci-label-retrigger.yml:44; ci.yml:588; rust.yml:206-207. |
| 11 | labeled with any other label, on a draft, or from a fork | `retrigger` skipped. A ci-label-retrigger run is created with only skipped jobs. | None | None | Unchanged | [S] ci-label-retrigger.yml:32-36. Test `test_label_eligibility_and_scope`. |
| 12 | unlabeled (any label, including `ci:full`) | Nothing. ci-label-retrigger listens to `labeled` only. Removal affects only later events: Windows and macOS selection read the payload labels at each event. | Nothing | None | Unchanged | [S] ci-label-retrigger.yml:11-12; ci.yml:525, 591, 702; rust.yml:206-207. |
| 13 | Stale label dispatch (head advanced between the label event and dispatch) | filter fails at `[ "$GITHUB_SHA" = "$PR_HEAD_SHA" ]`. Paid jobs are skipped and `linux-gates` FAILS. [I] That failure is a check run on the **new** head SHA (the dispatched ref tip). | rust filter fails the same way. | None | [I] A failing check run on the current head. [I] Unverified: whether a `workflow_dispatch` check run satisfies or replaces the PR's required `linux-gates`. GitHub's troubleshooting doc for required checks says it does not. | [S] ci.yml:210-213; rust.yml:197-200. Test `test_actual_dispatch_context_validation_rejects_stale_and_partial_context` covers refusal only. |
| 14 | Base change: retarget (`edited`) or `main` advancing | Not a ci/rust trigger. Strict mode requires an "update branch", which arrives as row 4. | Not a trigger | None until the branch is updated | The existing result stays bound to the head SHA. Merge is blocked until up to date. | [S] ci.yml:38; rust.yml:32. Ruleset `strict: true` (section 1). codex-gate.yml:12 handles `edited`. |
| 15 | push to `main` | filter diffs `before..after`. If that diff is docs-only for the ci.yml filter, `run-tests=false`, so no Python paid job starts and `linux-gates` takes the no-test path (as G16). Otherwise linux×2 and windows×2 run. rust.yml decides separately: Markdown under `rust/` or `.github/` is Rust-relevant (G18). No macos, no benchmarks. `linux-gates` runs on the main SHA (not gating). Group `ci-ci-refs/heads/main-default` with **cancel-in-progress true**. | Cells for Linux + Windows when Rust-relevant (rust.yml:206, 230-232). | Python L×2 + W×2 when `run-tests`; Rust L×2 + W×2 when Rust-relevant, including Markdown under `rust/` or `.github/`. A docs-only push outside those paths starts none. | PASS or FAIL on the main SHA (informational) | [S] ci.yml:41-42, 95-97, 261-263, 590, 697-705, 810. [O] ci 39492119963: L 13 + W 18 min, about $0.196. Rust 39496637430: L 4 + W 4, about $0.048. [I] A newer main push cancels an older in-progress main run. Not observed. |
| 16 | `merge_group` | Declared (ci.yml:39-40; rust.yml:36-37), but **unreachable**: no merge-queue rule (section 1). | Same | None | n/a | [S] + [O] ruleset read. Offline tests only. |
| 17 | `workflow_dispatch`, manual (no PR inputs) | Group `manual-<scope>` per ref, cancel-in-progress true. `run-tests` forced true. linux×2. windows×2 + macos unless `scope=linux`. | All cells for the scope, no path filter | L×2 (+W×2 +M) (+Rust) | PASS or FAIL as a check run on the dispatched tip (app 15368). Effect on the PR's required `linux-gates`: not observed. [I] Unverified: whether a `workflow_dispatch` check run satisfies or replaces the PR's required `linux-gates`. GitHub's troubleshooting doc for required checks says it does not. | [S] ci.yml:46-82, 95-97, 203-206, 231-235, 588, 698. Test `test_dispatch_defaults_and_existing_events`. |
| 18 | `workflow_dispatch`, **candidate** (CSL-259 coordinator) | Dispatched on the default branch with the full tuple and `scope=full`. Group `candidate-<id>`, `queue: max`, no cancel. The staged policy validator runs in every validating job. All five Python cells run, plus `candidate-ci-gates`. | All five Rust cells | L×2 + W×2 + M (+ Rust L×2 + W×2 + M) | Runs, but [I] binds to the policy (main) SHA, not the PR head, so it does not satisfy the PR's required check. | [S] candidate_gate.py:8-10, 533-534; ci.yml:95-97, 544-571; ci-candidate-readiness.md:68-71, 151-158, 214-220. [O] Inactive: codex-gate run 39496664994 executed `legacy` (requires `vars.CANDIDATE_COORDINATOR != 'true'`, codex-gate.yml:66-67). |
| 19 | `schedule` (cron `17 7 * * *`, ci.yml only) | filter: `run-tests=false`. macos runs only if `main` had commits in the last 24 h. linux-gates and queue-gates are skipped. | No schedule trigger | None (macOS is free) | **Skipped.** The job does not run; GitHub records a `skipped` `linux-gates` job/check on the main SHA. | [S] ci.yml:43-45, 236-253, 467, 509, 699. [O] run 39424586371: macos success, linux-gates skipped. It was created 12:44:11Z for the 07:17 cron, which shows real schedule latency. |
| 20 | Rerun (attempt > 1) | [I] Re-executes with the original event payload. A rerun of a draft-era run still skips filter and fails linux-gates. Re-run jobs re-enter concurrency. | Same | Re-executed paid cells | [I] As the original event | [S] Controller: rerun targets are never cancelled (ci_cancel_controller.py:375), and any current-head rerun disables cleanup (305-306). [O] No attempt > 1 among the last 100 ci or rust runs. **Unobserved.** |
| 21 | Cancellation by concurrency | Only non-PR groups cancel in progress: push, manual, label, schedule and merge_group (unreachable today) (ci.yml:96; rust.yml:87). PR and candidate groups queue instead. In a cancelled run, `linux-gates` still runs (`always()`). The rule: "Required Linux gate" passes only if filter, settle and linux reached their expected results (section 4, steps 1-5). [O] In every observed cancellation (e.g. 39490583816) a dependency was `cancelled`, so that step failed, and "Reject workflow cancellation" (`if: cancelled()`) was skipped because `cancelled()` was false. [I] If the cancellation lands after filter, settle and linux all succeeded, the gate step passes, so `linux-gates` can be green; the required tests did run. | Same (rust-gates) | Already-started cells are lost | FAIL when a dependency was cancelled or failed (observed). [I] PASS only when filter, settle and linux had already succeeded. A pending run displaced by `queue: single` never starts, so it posts no check. | [S] ci.yml:96-97, 467, 500-502; rust.yml:87-88, 442, 466-468. Test `test_cancellation_runs_and_fails_the_required_aggregate`. |
| 22 | Cancellation by the cancel-ancestors controller | See row 5 and section 5. | Same | None started by the controller itself | Cancelled run, on an **ancestor** SHA only: FAIL when a gate dependency (filter, settle or linux) was cancelled or had not finished, or when `linux-gates` itself was running at cancellation (`if: cancelled()` step). [I] PASS if filter, settle and linux had all succeeded before the cancellation (linux-gates needs only those, ci.yml:466; `benchmarks` runs in parallel, ci.yml:801). No check at all if the run was cancelled before any job started ([O] 39492190159: 0 jobs). | [S] ci-label-retrigger.yml:61-83. [O] rows 5 and 8. |
| 23 | Fork PR (`pull_request` from a fork) | [I] Same as rows 1-7: ci.yml and rust.yml have no fork guard. The benchmarks comment step is skipped (ci.yml:846). Label retrigger and the controller exclude forks. | Same | [I] Yes, Blacksmith, subject to GitHub's fork-approval setting (not verified). A draft fork starts none (row 1). | Draft fork: as row 1 (FAIL). Non-draft fork: as row 2. [I] While a run awaits fork approval, no job starts and no check is posted. | [S] ci-label-retrigger.yml:35; ci_cancel_controller.py:147-148. |

## 4. Aggregation semantics

### `linux-gates` (required), ci.yml:462-502

- **Runs when:** `always() && github.event_name != 'schedule'` (ci.yml:467). It runs even after
  failures, and after a cancellation once the run's jobs have started. A run cancelled while
  still queued or pending starts no job, so `linux-gates` is never evaluated and no check is
  posted (rows 5, 21, 22; [O] 39492190159, 0 jobs). On schedule it is skipped by its job-level `if`; GitHub still records a `skipped` `linux-gates` job/check (row 19).
- **Inputs:** filter, settle and linux results; `run-tests` output; `SETTLE_EXPECTED` =
  `pull_request && synchronize && !draft` (ci.yml:472-477).
- **Not inputs:** windows, macos, benchmarks and Rust. A `ci:full` PR with failing Windows or
  Rust still passes `linux-gates`.

Exact script (ci.yml:481-499), evaluated in order:

| Step | Condition | Result |
| --- | --- | --- |
| 1 | `FILTER_RESULT != success` (skipped on drafts, failure on stale or invalid dispatch, cancelled, or empty) | exit 1 |
| 2 | `SETTLE_RESULT` is `success` | continue |
| 2 | `SETTLE_RESULT` is `skipped` and `SETTLE_EXPECTED == false` | continue |
| 2 | any other settle result (failure, cancelled, empty, or skipped when it was expected) | exit 1 |
| 3 | `RUN_TESTS == true` and `LINUX_RESULT == success` | exit 0 |
| 4 | `RUN_TESTS == false` and `LINUX_RESULT == skipped` | exit 0 ("Explicit docs-only/no-change skip") |
| 5 | anything else (linux failure, cancelled, or skipped while tests were required; `RUN_TESTS` empty or unknown) | exit 1 |
| 6 | [I] not observed: `cancelled()` true when the job runs (`if: cancelled()`, ci.yml:500-502) | step exits 1. In observed cancellations `cancelled()` was false and this step was skipped; fail-closed for a cancelled dependency comes from steps 1-5. A run cancelled after filter, settle and linux succeeded can therefore pass (row 21). |

[I] `needs.linux.result` aggregates the 3.11 and 3.12 matrix cells: it is `success` only when
every cell succeeded.

### `queue-gates` (advisory), ci.yml:506-540; `rust-gates` (advisory), rust.yml:439-468

Both import `tools/required_ci_gate.py`:

- `settled(result, expected)` (tools/required_ci_gate.py:6-9): `expected` must be the literal
  `"true"` or `"false"`. It passes on `success`, or on `skipped` only when `expected == "false"`.
- `gate(filter, required, results)` (tools/required_ci_gate.py:12-16): fails unless filter is
  `success`, `required` is `"true"` or `"false"`, and `results` is non-empty. Every result
  must then equal `success` when required is true, or `skipped` when it is false.
  `cancelled`, `failure`, empty and unexpected `skipped` all fail.
- `queue-gates` = settled AND gate(linux) AND gate(windows). Windows is required only if
  `run-tests == true` and the event is `merge_group`, `push`, a dispatch without
  `scope=linux`, or a PR carrying `ci:full` (ci.yml:525-536).
- `rust-gates` = settled AND gate(filter, `has-cells`, [`rust-book` result]), with
  `if: always()` (rust.yml:442, 451-465). It also runs on dispatch.
- Both have the "Reject workflow cancellation" step (ci.yml:538-540; rust.yml:466-468).
- [S] ci-queue-rollout.md:74-75 caveat: a later Linux-scope dispatch can report a narrower
  successful `queue-gates` after a full-scope failure on the same head.

### `candidate-ci-gates` (staged), ci.yml:544-571

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
| ci-label-retrigger, `workflow_run` | `ci-cancel-controller-<run_id>` (unique) | true (no-op) | default |
| ci-label-retrigger, `labeled` | `ci-label-retrigger-<wf>-<PR>-<head>-<label>` | true | default |
| review-gate | `review-gate-<PR or ref>` | true | default |
| claude.yml fixer jobs | `claude-<PR>` (job level) | false | `max` |

Sources: [S] ci.yml:94-97; rust.yml:82-88; ci-label-retrigger.yml:24-28; review-gate.yml:23-25;
claude.yml:557-560, 1314-1317. Pending-run caps ([GitHub workflow syntax, `concurrency`](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#concurrency)):
`queue: max` keeps up to 100 pending runs in queue order (PR and candidate groups, the claude
fixer); `queue: single` keeps one pending run and cancels the previous pending one (push,
schedule, manual and label groups). GitHub rejects `queue: max` with `cancel-in-progress: true`;
ci.yml:96-97 never pairs them.

### Settle

- Only non-draft `synchronize` runs `settle` (`sleep 120`, ubuntu-latest). Every paid job and
  every aggregate accepts `success`, or `skipped` when settle was not expected
  (ci.yml:310-334; rust.yml:304-323).
- Because ordinary PR groups do not cancel in progress, settle saves money only when the
  controller cancels the waiting run during the 120 s window. [O] Run 39492190159 was
  cancelled while pending, before any job started.
- The ci.yml:306-309 comment still attributes this saving to concurrency (#157). That comment
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
- **Effect:** twins in the ordinary group are serialized by `queue: max` and each executes in
  full.
- **[O] Observed twins**, two `pull_request` runs on the same head created within 1 s:
  - ci 39490583924 and 39490583816 (`5b7c2b15b8`): the second started after the first
    finished at 20:25:30Z, re-ran settle, and started L×3 before an ancestor cancel.
  - ci 39492186505 and 39492190159 (`1f0628b5d3`).
  - rust 39492186705 and 39492189661 both ran L×2 to success, back to back. The duplicate
    cost was about $0.012 (3 L-min).
- The REST API does not expose the `pull_request` action, so the cause of the duplicate
  events is undetermined.

Twins across groups also run concurrently and are not cancelled by anything: for example,
a ready_for_review run and a label dispatch on the same head (rows 7 and 9).

## 6. Test evidence

Every test file below runs in the required `linux` job (ci.yml:426-432); `test_review_gate` and
`test_queue_gates` run there through `unittest`. Under `app/tests`, only `benchmarks/test_perf_compare.py:225` reads `ci.yml` (benchmarks step
shape) and `test_linear_pr_sync.py` references a workflow path; the CI contract tests live in `tools/`.

At the base commit these ran locally (Python 3.13):

- `test_ci_dispatch_scope`: 15 OK
- `test_ci_settle`: 4 OK
- `test_ci_cancel_controller`: 66 ran, 1 environment-dependent failure (`test_queue_lint_exception_is_exact_and_limited_to_named_paths`, which asserts actionlint 1.7.7 behaviour and passes in the `linux` job)
- `test_candidate_workflows`: 12 OK
- `unittest tools.test_review_gate tools.test_queue_gates`: 36 OK

| Row or claim | Status | Test(s) |
| --- | --- | --- |
| 1, 3: drafts skip filter; aggregate fails | PROVEN (expression and shell level) | test_ci_dispatch_scope `test_dispatch_runs_linux_and_drafts_keep_existing_guard`, `test_linux_gate_fails_closed`; test_queue_gates `test_exhaustive_aggregate_states` |
| Draft and default groups are separate; PR runs queue without cancel | PROVEN | `test_pr_concurrency_queues_old_heads_but_isolates_drafts`; test_ci_cancel_controller `test_ordinary_only_policy_and_shared_identity`, `test_delayed_A_admission_does_not_displace_B_active_or_pending` |
| 2, 4, 6, 7: settle table and eligibility of paid jobs | PROVEN | test_ci_settle `test_event_table_and_settle_outcomes`, `test_known_non_synchronize_paths_are_not_accidentally_suppressed`; `test_settle_debounce_gates_blacksmith_only_on_pr_synchronize` |
| ci.yml `decide` for pull_request, push and schedule (docs-only regex, 24 h idle skip) | **GAP** | Only workflow_dispatch (`test_dispatch_runs_linux_and_drafts_keep_existing_guard`) and the candidate docs case (test_candidate_workflows `test_docs_candidate_executes_all_five_python_and_rust_cells`) execute it |
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
| 21: cancellation fails aggregates | PROVEN (shell); [O] the path taken differs (row 21) | test_ci_settle `test_cancellation_runs_and_fails_the_required_aggregate`; test_queue_gates `test_cancelled_workflow_cannot_pass_even_after_successful_dependencies` |
| `linux-gates` aggregation table (section 4) | PROVEN | test_ci_settle `test_required_gate_executable_result_table` (exhaustive over settle, run-tests and linux) |
| B2: identical-head twins never cancelled | **PARTIAL** | The code paths are covered indirectly (`test_strict_ancestry_required_not_timestamp_or_sha_inequality` mocks compare `identical`). No fixture places two active runs on the live head and asserts both survive. |
| Required-check names unchanged | PROVEN | test_candidate_workflows `test_existing_required_check_names_remain_exact` |
| Devin status semantics (`review-gate`, advisory) | PROVEN | tools/test_review_gate.py (30 tests) |

## 7. Paid workloads outside CI admission

- **claude.yml (Reliability lane):**
  - Triggers: workflow_dispatch, repository_dispatch `devin-review-fix`, issue_comment,
    pull_request_review_comment, pull_request_review, and `pull_request` limited to fixer and
    gate paths (claude.yml:3-43).
  - Jobs: `resolve-pr`, `event-paths`, `forward-conflict-scan`, `claude-mention` and
    `devin-review-fix`. All run on `ubuntu-latest` (claude.yml:90, 504, 534, 561, 1318), so
    there is **no Blacksmith compute**.
  - Model usage goes through `CLAUDE_CODE_OAUTH_TOKEN` (claude.yml:1085, 2413; subscription
    route per ci-cost-budget.md:57-58). Each fixer installs the full runtime and runs
    `ruff` and `pytest` on the free runner (claude.yml:731, 1118-1119).
  - [O] Fixer pushes (`git push origin HEAD:refs/heads/<branch>`, claude.yml:1146) emit
    `synchronize` and start ordinary paid CI (rows 4-5). The `ci` runs on fixer commits
    were started by `claude[bot]` about 12 s after each commit: 39354662754 (#212
    `8ede5ff793`), 39353490459 (#208 `571cb1ce11`) and 39079041390 (#199 `e875f2b8c3`).
    So the push credential is the Claude app, not the checkout `GITHUB_TOKEN`, which
    could not trigger workflows.
  - The eight-commit guard (`cap` step, claude.yml:1462) bounds only the automated
    `devin-review-fix` job. `claude-mention` has no equivalent cap, so repeated authorized
    `@claude` requests can push, and start paid `synchronize` runs, without that limit.
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
      (ci.yml:586-591, 697-704). The ci.yml filter treats any `*.md` outside the swarm
      dirs as docs-only (ci.yml:288-300), so a docs-only event starts no Python W or M.
    - **rust.yml (Rust cells):** the hosted-cell selector ignores `run-tests` and matches
      the diff against `RUST_PATHS` (rust.yml:201-299, `relevant()` at 242-280). Those
      paths include the bare prefixes `rust/` and `.github/`, so **Markdown under `rust/`
      or `.github/` is Rust-relevant**. For example, a PR that only edits the tracked
      `.github/pull_request_template.md` starts Rust L×2 on Blacksmith, plus Rust W×2 and
      Rust M with `ci:full` (`RUN_WINDOWS`/`RUN_MACOS` come from the label,
      rust.yml:206-207). A non-Rust code event starts no Rust cells. See G18.
  - ci-candidate-readiness.md:51-54 ("New READY decisions do not add `ci:full`") applies
    only to the coordinator path, which is currently off ([O] 39496664994).
- **Benchmarks:** `benchmarks` (Blacksmith L×1, about 0.5-1 min) runs on every non-draft PR
  event with `run-tests=true`. It is outside every aggregate, so its failure never blocks.
  It is also not covered by the dispatch paths (ci.yml:793-811).
- **Main-push fallback:** row 15, about $0.196 for ci plus $0.048 for rust per relevant merge
  ([O]). It is retained by owner decision (ci-queue-rollout.md:20-22, 116-117).
- **Manual and label dispatch** bypass the docs-only filter (`run-tests` forced true,
  ci.yml:231-235; rust.yml:205).

## 8. Known gaps and follow-ups

| ID | Gap | Class | Suggested follow-up |
| --- | --- | --- | --- |
| G1 | Only `linux-gates` and `Devin Review` are required, and both rulesets allow admin PR bypass. Windows, macOS, Rust and the `queue-gates`/`rust-gates`/`review-gate` aggregates are advisory. | [O] | Already staged in ci-queue-rollout.md. Owner decision; no change here. |
| G2 | Identical-head twins observed (section 5). B2 keeps them, so each twin re-executes settle and paid cells serially. The duplicate-event cause is unknown, and there is no explicit twin test. | [O] / PARTIAL | Add a controller fixture asserting two active live-head runs both survive. Investigate the duplicate `pull_request` deliveries (read-only). |
| G3 | Stale label dispatch (row 13), and same-key label or manual re-dispatch (cancel-in-progress true), can create a failing `linux-gates` check run on the **current** head. Whether a dispatch check run affects the required check is unverified; GitHub's doc says it does not. | [I] | Observe or test which SHA receives the check. Consider documenting the "rerun or wait for the ordinary run" recovery. |
| G4 | The ci.yml PR filter diffs raw `base.sha..head.sha` (ci.yml:256-257, 275), while rust.yml uses the merge tree (rust.yml:216-228). On a stale base, ci over-selects tests (fails open, extra spend). The decide script for PR, push and schedule has no executing test. | [S] | Align with the rust.yml merge-tree diff in a separately admitted CI task, and add shell-execution tests. |
| G5 | Stale workflow comments: ci.yml:306-309 (settle relies on #157 concurrency cancel), ci.yml:17-23 and 707-708 ($50 budget, superseded by ci-cost-budget.md:48-50), ci.yml:504-505 (`queue-gates` "required"). | [S] | Comment-only fix in a human-assigned workflow issue. |
| G6 | `merge_group` paths and `queue-gates` are untested live. The merge queue is not enabled. | [O] | Covered by the ci-queue-rollout.md activation sequence. |
| G7 | A PR created with `ci:full` or `enhancement` gets both `opened` and `labeled`, which means an ordinary run plus a label dispatch (duplicate full matrix). | [I] | Observe once (read-only) and record. |
| G8 | `ci:full` added by a founder or Devin persists, so every later qualifying push runs Windows, macOS and Rust Windows (subject to the path filters, section 7). The legacy READY label is gate-owned and is cleared on the next push, after one full-matrix run. | [S]+[O] | AGENTS.md CI-etiquette rule added: remove a manual `ci:full` once its full run passes (CSL-393, cockpit#222; recursion CSL-395). |
| G9 | The task brief said claude.yml has Blacksmith runners. It does not at the base commit. Its paid effect is indirect, through pushes that trigger CI. | [S]+[O] | Correct the brief and the Reliability-lane notes. |
| G10 | GitHub rerun semantics (payload reuse, concurrency re-entry) have not been observed. | [I] | Record the next real rerun. |
| G11 | Duplicate controller cancel POSTs (4 per target observed) consume API budget. Harmless today. | [O] | None required. Note it in the controller doc if budget pressure appears. |
| G12 | Main-push groups cancel in progress, so a fast second merge cancels the first fallback run (a red `linux-gates` on the old main SHA, and minutes already spent are lost). | [I] | Observe. It is consistent with "keep fallback" but undocumented. |
| G13 | The nightly schedule ran about 5.5 h late (created 12:44Z for the 07:17 cron). | [O] | Note in AGENTS.md:227 that schedule time is best-effort. |
| G14 | Fork PRs can start Blacksmith jobs in ci.yml and rust.yml (no fork guard). Exposure depends on the unverified fork-approval setting. | [I] | Lucas set "Require approval for all external contributors" in both repos, 2026-10-20 (relayed; not readable from this session). |
| G15 | Org-level ruleset listing (`gh api orgs/CSL-Laboratories/rulesets`) is blocked in this session. The org ruleset was read through the repo-scoped endpoints `rulesets/25015519` and `rules/branches/main`. | [O] | None. Repo-scoped effective rules are authoritative for `main`. |
| G16 | **Docs-only green no-op.** The PR filter marks `docs/` and `*.md` changes (apart from the listed swarm dirs and `.py`/`.json`/`.yaml`) as no-test (ci.yml:288-300). `linux-gates` then passes with `linux` skipped (section 4, step 4). `git grep -E "\.md['\"]" -- 'app/tests/*.py' 'tools/test_*.py'` prints 69 matching lines in 17 files; many are temporary paths (`comment.md`, `brief.md`) ([O]). Concrete cases: `app/tests/test_o7_operator_contract.py:51-54` hashes four `docs/replacement/*.md` contracts, and `app/tests/system/test_rw01_market_day.py:771` reads `docs/setup/accounts-and-keys.md`. A change to only those docs gets a green required check without the tests that read them, the same class recursion fixed in CSL-376. Code cannot skip this way: any `.py` change forces `run-tests`. The risk of a Rust PnL change landing untested belongs to G1 (`rust-gates` is advisory), not to this regex. | [S]+[O] | Audited in CSL-392 (21 test-read files, including the LLM prompts). Option B is in progress as CSL-397 (cockpit#223): `app/**` and the test-read docs always run tests, plus a guard test. |
| G17 | Two fail-open-to-spend branches: `ci.yml:240-245` (nightly macOS on an unreadable commit window; free runner) and `rust.yml:226-227` (`DIFF_FILES='rust/'` fallback starts L×2). They over-spend rather than skip tests. | [S] | Note only. |
| G18 | **The Python and Rust selectors disagree on Markdown.** ci.yml's filter treats `rust/*.md` and `.github/*.md` as docs-only (ci.yml:288-300), while rust.yml's `RUST_PATHS` treats them as Rust-relevant (rust.yml:242-280). Such a change skips the required ci.yml Python matrix (G16), but still starts the Blacksmith Rust cells, which run their own pytest list including `tests/test_invariants.py` (rust.yml:424-430): L×2; plus W×2 and the free macOS cell with `ci:full`; plus W×2 on a `main` push. This is over-spend, not under-testing. | [S] | **Accepted** by Lucas, 2026-10-20 (relayed on CSL-385); revisit with the G16 fix. |

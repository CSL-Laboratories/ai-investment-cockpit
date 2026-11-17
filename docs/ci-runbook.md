# CI runbook

What to do when CI looks wrong on a PR or on `main`. This page is procedure only. What each
event does to each check is in the [event contract](ci-event-contract.md) (cited as "contract §N");
agent permissions are in [AGENTS.md](../AGENTS.md#hosted-ci). Where they disagree, the contract and
the code at its base commit win. Tracked under CSL-255 exit criterion 5 (CSL-416).

**Who acts.** Founders may do every step below. An agent may read anything, push to its own PR
branch and comment, but needs a human ask before it dispatches, re-runs or cancels any workflow or
adds `ci:full` / `enhancement` (AGENTS.md, "CI an agent starts on its own" and the "How." rule on escalating). Escalate on the Linear issue with an
@mention; a GitHub mention alone is not enough.

## 1. Reading the required checks

`main` requires **`linux-gates`** (ruleset `required-ci`). `Devin Review` was removed from the required checks on Nov 11, 2026; contract §1 predates that.

| You see | It means | Do |
|---|---|---|
| `linux-gates` green on the current head | Tests ran and passed, or the change is docs-only by the filter (contract §3, G16 fixed by CSL-397) | Nothing. |
| `linux-gates` red | A test failed, or a dependency was cancelled, skipped unexpectedly or missing: the aggregate fails closed (contract §4) | Open the failing job. If it is `settle` or a cancelled cell, see §3 below. |
| `linux-gates` red on a draft | Expected: drafts skip the matrix and the aggregate fails closed until `ready_for_review` (AGENTS.md, the "Required check: `linux-gates`" bullet) | Mark ready when done. |
| No `linux-gates` on the head | No `ci.yml` job reached that head: a run cancelled before any job started, by its concurrency group (a push, manual, label or schedule run; contract §3 row 21(a)) or by the controller (row 22(a)), a skip annotation or unresolved conflict (GitHub creates no run), or an approval-required run | §4 below. |
| `Devin Review` on the head | External app; not produced by this repository's workflows (contract §1). Its trial has expired: it reports "Full review skipped" without reviewing, and it is no longer required | Nothing. |
| `cancel-ancestors` red in `ci label retrigger` | Usually a fail-closed refusal, not a CI failure. Known variants: `Unverified triggering run provenance` (CSL-388) and `Triggering run identity changed` (CSL-410), mostly during merge bursts | Not a merge blocker. Report a new refusal text on CSL-356. |

## 2. Re-running

- **Prefer "Re-run failed jobs" on the same head** over an empty push. A push starts every workflow
  again, waits 120 s in `settle` (AGENTS.md, the bullet on `settle`), and dismisses approvals. Re-running a failed
  `linux` cell also re-runs `linux-gates` (contract §3 row 20). The controller does not target manual
  re-runs (contract §6 row 20); GitHub's own re-run semantics are an accepted untested gap.
- **Flaky test:** re-run once. If it fails again on the same head, treat it as real and file a Bug.
- **Full matrix:** since CSL-238 every event of a non-draft same-repository PR runs Windows and
  macOS without a label. A draft runs no matrix at all until it is ready, whatever its labels. `ci:full` is
  needed only on a fork PR (its next push then runs the full matrix); adding it to a non-draft
  same-repository PR dispatches a full rerun, and `ci-label-retrigger.yml` refuses drafts and forks.
  No workflow adds or removes it.

## 3. Cancellation

- Ordinary PR runs queue (`queue: max`, no automatic cancel). The default-branch controller cancels
  only verified strict-ancestor runs after a real current-head successor exists (contract §5).
  B2 only stops two pending runs of the same head from cancelling each other. Once a newer head
  exists, the controller still retires both as ancestors (contract §5, the §6 B2 evidence row, G2),
  so a cancelled twin on an old head is expected, not a CSL-356 refusal.
- Never cancel an in-progress run on the PR's current head; that leaves `linux-gates` red or missing.
- A run you cancelled on an old head posts a red `linux-gates` on that old head only, or none if no
  job had started; a check that had already completed stays as it was (contract §3 row 22). The current head decides.

## 4. A required check is stuck or missing

1. Is the PR a draft, conflicted, or is its head commit marked `[skip ci]`? Fix that. A draft fails
   `linux-gates` closed (AGENTS.md, the "Required check: `linux-gates`" bullet); GitHub creates no `pull_request` run for a conflicted head or
   a skip-annotated commit (GitHub docs, events that trigger workflows; skipping workflow runs).
2. Actions tab shows **"Approve workflows to run"**: usually a fork PR under the external-contributor
   approval setting (contract G14); a writer reviews the fork diff, then approves. A `pull_request`
   opened, synchronized or reopened by a `GITHUB_TOKEN` automation also waits for approval (GitHub
   docs, triggering a workflow).
   `workflow_dispatch` and `repository_dispatch` are the documented exception: a `GITHUB_TOKEN`
   identity still creates those runs. Any other event a `GITHUB_TOKEN` triggers, such as a label or
   `ready_for_review`, creates no run at all, so recovery is a human action or an App or PAT identity.
3. A run is queued for minutes: check the org runner capacity (Team plan: 60 concurrent jobs, 5
   macOS, shared with recursion and every bot workflow; GitHub docs, Actions limits). macOS
   saturates first.
4. Otherwise push nothing yet; comment on the Linear issue with the run IDs and @mention the CI
   owner.

## 5. Stop or roll back automation

No repository workflow posts to a PR any more (the `benchmarks` job of `ci.yml` stopped commenting on
Nov 16, 2026): the Claude fixer, the `@claude` path, the
conflict writer and the stuck-thread watch were retired in CSL-238 (Lucas, Nov 16, 2026; the Linear PR sync, dispatch-only, was kept on his word),
and with them the `claude:pause` label and the `CLAUDE_FIXER_PAUSE` variable. A collision is now
between people or hand-driven agents: stop competing authoring, read the work issue's owner and
claim, and settle it on Linear before anyone pushes again. To stop a workflow, a founder uses
Actions → workflow → **Disable workflow** (runs in progress continue; cancel them separately)
and records it on Linear; to roll back a workflow change, revert its PR (a founder merges).

## 6. Cost and capacity check

- Every job must use a standard GitHub-hosted label; the cross-lane tests accept `ubuntu-latest`,
  `ubuntu-24.04`, `ubuntu-22.04`, `windows-2025`, `windows-latest` and `macos-latest`. Since CSL-408 these are free **because the repository is
  public**. Making it private would meter minutes (Team includes 3,000 per month; GitHub docs,
  Actions billing); re-plan before that.
- `tools/test_workflow_hosting.py` (`test_test_jobs_use_free_github_hosted_runners`) fails if any
  workflow names a Blacksmith runner. workflow-checks.yml runs it on every same-repository
  `pull_request` (CSL-238 moved it there from the removed codex-gate.yml). The no-writer tests in
  `tools/test_ci_dispatch_scope.py`, which
  `ci.yml` runs on every test-running PR, fail if any job leaves GitHub-hosted runners or any
  workflow gains model credentials or a push.
- Usage over time: the criterion 2 measurements on CSL-255 (queue wait, time to required check,
  concurrency).

## 7. Changing a workflow

- `.github/workflows/**` changes need a Linear issue explicitly assigned by a human (AGENTS.md, the rules on editing `.github/workflows/**`).
- **Contract rule (agreed by the CI and Reliability lanes, 2026-10-21; founders decide whether it
  becomes a repository rule):** a PR that changes a workflow, `scripts/ci/**`, a required check or
  the rulesets also updates [ci-event-contract.md](ci-event-contract.md) in the same PR, including
  re-pinned `file:line` citations when lines move, or says "no contract row changes" and why.
- `NoAutomatedWriterTests` and `CapabilityLayerTests` (`tools/test_ci_dispatch_scope.py`) guard against an
  automated writer in two layers. The CAPABILITY layer checks what a job is able to do, whatever its scripts
  say: every workflow lies directly under `.github/workflows` (a `.yml` file in a subfolder is refused; GitHub
  runs none); each job's effective `permissions` (its own block, else the workflow's, else unspecified) must
  hold no scope above read, and must not be unspecified, unless the job is on the allowlist in the test (today:
  the two jobs of `ci-label-retrigger.yml`, `actions: write`; no job is unspecified); every secret other than
  `GITHUB_TOKEN` that a workflow or a local composite action names must be on the allowlist (one:
  `LINEAR_API_KEY` in `linear-pr-sync.yml`); `pull_request_target` and `workflow_run` must be on the allowlist
  (one: `workflow_run` in `ci-label-retrigger.yml`); every local composite action is read, must lie under
  `.github/actions` and be a composite. The TEXT layer stays as a second line: the word `secrets` or `vars` may
  stand in the text (outside comments) only as an admitted exact spelling, an expression it cannot read to its
  end is refused, no model credential or model action, GitHub-hosted runners only, the word `push` may not
  appear in a run script unless that exact line is admitted by name (two lines are: an error message and a
  shell comment), and a push or a write to contents, labels or comments through git, `gh`, an HTTP client
  (curl, wget, Invoke-WebRequest) or the API is refused. A command assembled at run time, an encoded command,
  a script that a step calls, a third-party action and a reusable workflow elsewhere cannot be seen by any scan
  of text; what bounds them is the capability layer: a job whose token holds no write scope and which names no
  secret cannot write to the repository, whatever it runs (it can still read and use the network inside the
  runner); the use made of the admitted `actions: write` is not checked. Every allowlist entry is a decision of
  the owner. Adding an automated writer back is a founder decision.

## 8. Owners

| Area | Owner |
|---|---|
| CI workflows, required checks, this runbook, the contract | CI lane (Lucas's session), founders approve |
| CI failure on `main` | Devin "Main CI failure fixer" automation (human-managed, AGENTS.md, the Devin "Main CI failure fixer" line); a founder triages |
| Rulesets and repository settings | Founders (admins) |

Ownership changes over time; check the Linear assignee before acting.

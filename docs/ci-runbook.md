# CI runbook

What to do when CI looks wrong on a PR or on `main`. This page is procedure only. What each
event does to each check is in the [event contract](ci-event-contract.md) (cited as "contract §N");
agent permissions are in [AGENTS.md](../AGENTS.md#hosted-ci). Where they disagree, the contract and
the code at its base commit win. Tracked under CSL-255 exit criterion 5 (CSL-416).

**Who acts.** Founders may do every step below. An agent may read anything, push to its own PR
branch and comment, but needs a human ask before it dispatches, re-runs or cancels any workflow or
adds `ci:full` / `enhancement` (AGENTS.md:117-118, 129-133). Escalate on the Linear issue with an
@mention; a GitHub mention alone is not enough.

## 1. Reading the required checks

`main` requires **`linux-gates`** and **`Devin Review`** (contract §1).

| You see | It means | Do |
|---|---|---|
| `linux-gates` green on the current head | Tests ran and passed, or the change is docs-only by the filter (contract §3, G16 fixed by CSL-397) | Nothing. |
| `linux-gates` red | A test failed, or a dependency was cancelled, skipped unexpectedly or missing: the aggregate fails closed (contract §4) | Open the failing job. If it is `settle` or a cancelled cell, see §3 below. |
| `linux-gates` red on a draft | Expected: drafts skip the matrix and the aggregate fails closed until `ready_for_review` (AGENTS.md:361) | Mark ready when done. |
| No `linux-gates` on the head | No `ci.yml` job reached that head: a run cancelled before any job started (contract §3 row 22(a)), a skip annotation or unresolved conflict (GitHub creates no run), or an approval-required run | §4 below. |
| `Devin Review` missing or pending | External app; not produced by this repository's workflows (contract §1) | Ask for a review through the Discord bot or Devin; do not push to retrigger it. |
| `cancel-ancestors` red in `ci label retrigger` | Usually a fail-closed refusal, not a CI failure. Known variants: `Unverified triggering run provenance` (CSL-388) and `Triggering run identity changed` (CSL-410), mostly during merge bursts | Not a merge blocker. Report a new refusal text on CSL-356. |

## 2. Re-running

- **Prefer "Re-run failed jobs" on the same head** over an empty push. A push starts every workflow
  again, waits 120 s in `settle` (AGENTS.md:368), and dismisses approvals. Re-running a failed
  `linux` cell also re-runs `linux-gates` (contract §3 row 20). The controller does not target manual
  re-runs (contract §6 row 20); GitHub's own re-run semantics are an accepted untested gap.
- **Flaky test:** re-run once. If it fails again on the same head, treat it as real and file a Bug.
- **`ci:full`:** add it when Windows and macOS evidence is needed; remove it once that full run has
  passed on the current head (AGENTS.md:284-299). It persists otherwise and repeats the full matrix
  on every push.

## 3. Cancellation

- Ordinary PR runs queue (`queue: max`, no automatic cancel). The default-branch controller cancels
  only verified strict-ancestor runs after a real current-head successor exists (contract §5).
  Identical-head twins are never cancelled (B2 = no).
- Never cancel an in-progress run on the PR's current head; that leaves `linux-gates` red or missing.
- A run you cancelled on an old head posts a red `linux-gates` on that old head only, or none if no
  job had started (contract §3 row 22). The current head decides.

## 4. A required check is stuck or missing

1. Is the PR a draft, conflicted, or is its head commit marked `[skip ci]`? Fix that. A draft fails
   `linux-gates` closed (AGENTS.md:361); GitHub creates no `pull_request` run for a conflicted head or
   a skip-annotated commit (GitHub docs, events that trigger workflows; skipping workflow runs).
2. Actions tab shows **"Approve workflows to run"**: usually a fork PR under the external-contributor
   approval setting (contract G14); a writer reviews the fork diff, then approves. A `pull_request`
   opened, synchronized or reopened by a `GITHUB_TOKEN` automation also waits for approval (GitHub
   docs, triggering a workflow); the Claude writers push with an App token instead (contract §7).
3. A run is queued for minutes: check the org runner capacity (Team plan: 60 concurrent jobs, 5
   macOS, shared with recursion and every bot workflow; GitHub docs, Actions limits). macOS
   saturates first.
4. Otherwise push nothing yet; comment on the Linear issue with the run IDs and @mention the CI
   owner.

## 5. Stop or roll back automation

| Lever | Effect | Who |
|---|---|---|
| Label `claude:pause` or `hold` on a PR | Claude writers (`@claude`, Devin fixer, conflict writer) stop before checkout or reservation, and the publishers recheck (claude.yml:603-606, 2971-2973, 2040, 5004; claude-conflict-writer.yml:72-84, 1411); the scan never selects a paused PR (conflict-scan.yml:78-79) | Any writer; agents must never remove a pause |
| Repository variable `CLAUDE_FIXER_PAUSE=true` | Same, for every PR (claude.yml:654-656, 3022-3024; conflict-scan.yml:333-334) | Admin |
| Remove `ci:full` | Stops repeat Windows/macOS on later pushes | Human |
| (automatic) A fixer pause while a gate-owned `ci:full` is on the PR | The writers revoke only the label event the gate itself recorded; a human-added `ci:full` is never removed (`revoke_owned_ci`, claude.yml:1853-1857, 3599-3603, 4816-4820; claude-conflict-writer.yml:1222-1226) | Automatic |
| Actions → workflow → **Disable workflow** | Stops new runs of that workflow | Admin; record it on Linear |
| Revert the PR that changed a workflow | Restores the previous behaviour; the controller has no state to migrate | Founder merge |

## 6. Cost and capacity check

- Every job must use a standard GitHub-hosted label (`ubuntu-latest`, `ubuntu-24.04`,
  `windows-2025`, `macos-latest`). Since CSL-408 these are free **because the repository is
  public**. Making it private would meter minutes (Team includes 3,000 per month; GitHub docs,
  Actions billing); re-plan before that.
- `tools/test_codex_gate.py` (`test_test_jobs_use_free_github_hosted_runners`) fails if any
  workflow names a Blacksmith runner. claude.yml runs it only when fixer or gate paths change
  (claude.yml:22-46, 521); codex-gate.yml runs it on every same-repository `pull_request` event
  (codex-gate.yml:11-12, 240-254). The cross-lane tests in `tools/test_ci_dispatch_scope.py`, which
  `ci.yml` runs on every test-running PR (ci.yml:438), fail if a writer workflow leaves GitHub-hosted
  runners.
- Usage over time: the criterion 2 measurements on CSL-255 (queue wait, time to required check,
  concurrency).

## 7. Changing a workflow

- `.github/workflows/**` changes need a Linear issue explicitly assigned by a human (AGENTS.md:281-283).
- **Contract rule (agreed by the CI and Reliability lanes, 2026-10-21; founders decide whether it
  becomes a repository rule):** a PR that changes a workflow, `scripts/ci/**`, a required check or
  the rulesets also updates [ci-event-contract.md](ci-event-contract.md) in the same PR, including
  re-pinned `file:line` citations when lines move, or says "no contract row changes" and why.
- Writer workflows (`claude.yml`, `conflict-scan.yml`, `claude-conflict-writer.yml`) must keep the
  admission surface that `CrossLaneAdmissionTests` checks statically: no CI dispatch, re-run,
  ready-for-review or CI-label add (the only CI-label write is revoking a gate-owned `ci:full`);
  pushes only from the model-free publish jobs as one ordinary fast-forward; one shared
  `claude-<PR>` lock without cancellation; the eight-commit guard on both claude.yml writers, the
  Devin path's three-attempt reservation, one publish per prepared run, and the pause. A new Claude
  workflow or job fails the test until it is added there. Retry behaviour itself is tested in
  `tools/test_claude_fixer_*.py` and `tools/test_claude_pr_fixer.py`.
- **The conflict writer has no eight-commit guard and no cross-run attempt budget.** Each run
  publishes at most once (claude-conflict-writer.yml:2168, 2241-2250); the scan starts it every two
  hours and on manual dispatch, never on a `main` push (conflict-scan.yml:12-14, 355). A PR that stays
  conflicted can get a Claude merge attempt on every scheduled scan; pause it if that is unwanted.

## 8. Owners

| Area | Owner |
|---|---|
| CI workflows, required checks, this runbook, the contract | CI lane (Lucas's session), founders approve |
| `claude.yml`, `conflict-scan.yml`, `claude-conflict-writer.yml`, fixer scripts | Reliability lane, founders approve |
| CI failure on `main` | Devin "Main CI failure fixer" automation (human-managed, AGENTS.md:438); a founder triages |
| Rulesets and repository settings | Founders (admins) |

Ownership changes over time; check the Linear assignee before acting.

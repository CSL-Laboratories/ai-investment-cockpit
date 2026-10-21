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
| `linux-gates` red on a draft | Expected: drafts skip the matrix and the aggregate fails closed until `ready_for_review` (AGENTS.md:364) | Mark ready when done. |
| No `linux-gates` on the head | No `ci.yml` job reached that head: a run cancelled before any job started, by its concurrency group (a push, manual, label or schedule run; contract §3 row 21(a)) or by the controller (row 22(a)), a skip annotation or unresolved conflict (GitHub creates no run), or an approval-required run | §4 below. |
| `Devin Review` missing or pending | External app; not produced by this repository's workflows (contract §1) | Ask for a review through the Discord bot or Devin; do not push to retrigger it. |
| `cancel-ancestors` red in `ci label retrigger` | Usually a fail-closed refusal, not a CI failure. Known variants: `Unverified triggering run provenance` (CSL-388) and `Triggering run identity changed` (CSL-410), mostly during merge bursts | Not a merge blocker. Report a new refusal text on CSL-356. |

## 2. Re-running

- **Prefer "Re-run failed jobs" on the same head** over an empty push. A push starts every workflow
  again, waits 120 s in `settle` (AGENTS.md:371), and dismisses approvals. Re-running a failed
  `linux` cell also re-runs `linux-gates` (contract §3 row 20). The controller does not target manual
  re-runs (contract §6 row 20); GitHub's own re-run semantics are an accepted untested gap.
- **Flaky test:** re-run once. If it fails again on the same head, treat it as real and file a Bug.
- **`ci:full`:** add it when Windows and macOS evidence is needed; remove it once that full run has
  passed on the current head and the PR needs no further automatic READY (AGENTS.md:287-302). A
  manual removal is a human hold: the Codex gate skips READY on that head, and with the candidate
  coordinator on, READY, candidate runs and `CODEX-GATE: MEDIATE` handoffs stay blocked until someone
  re-adds it. Otherwise it persists and repeats the full matrix on every qualifying push.

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
   `linux-gates` closed (AGENTS.md:364); GitHub creates no `pull_request` run for a conflicted head or
   a skip-annotated commit (GitHub docs, events that trigger workflows; skipping workflow runs).
2. Actions tab shows **"Approve workflows to run"**: usually a fork PR under the external-contributor
   approval setting (contract G14); a writer reviews the fork diff, then approves. A `pull_request`
   opened, synchronized or reopened by a `GITHUB_TOKEN` automation also waits for approval (GitHub
   docs, triggering a workflow); the Claude writers push with an App token instead (contract §7).
   Any other event a `GITHUB_TOKEN` triggers, such as a label or `ready_for_review`, creates no run
   at all, so recovery is a human action or an App or PAT identity.
3. A run is queued for minutes: check the org runner capacity (Team plan: 60 concurrent jobs, 5
   macOS, shared with recursion and every bot workflow; GitHub docs, Actions limits). macOS
   saturates first.
4. Otherwise push nothing yet; comment on the Linear issue with the run IDs and @mention the CI
   owner.

## 5. Stop or roll back automation

| Lever | Effect | Who |
|---|---|---|
| Label `claude:pause` or `hold` on a PR (any case) | The writers (`@claude`, Devin fixer, conflict writer) stop before checkout or reservation (`pause_reason` in `mention-prepare` and `fixer-prepare`; the conflict writer's `prepare`), and the publishers recheck it before pushing (`pause_refusal`). A pause during a model run makes the publisher refuse; it posts only the workflow's terminal audit. The scan never selects a paused PR. Unreadable labels count as paused | Anyone who can label PRs; agents must never remove a pause |
| Repository variable `CLAUDE_FIXER_PAUSE` set to exactly `true` | Same for every PR, also rechecked by the publishers (`pause_refusal`) and by the scan | Admin |
| Remove `ci:full` | Stops repeat Windows/macOS on later pushes | Human |
| (automatic) A fixer pause while a gate-owned `ci:full` is on the PR | The writers revoke only the label event the gate itself recorded; a human-added `ci:full` is never removed (`revoke_owned_ci` in the claude.yml publishers and prepare, and in claude-conflict-writer.yml) | Automatic |
| Actions → workflow → **Disable workflow** | Stops new runs of that workflow; runs already in progress continue (cancel them separately) | Admin; record it on Linear |
| Revert the PR that changed a workflow | Restores the previous code in a normal PR that a founder merges; never force-push. Writer state kept in PR comments (attempt reservations, receipts, loop-cap and `ci:full` ownership markers) is not reverted, so set `CLAUDE_FIXER_PAUSE=true` first and check those markers after the revert | Founder merge |

Writers outside this guard: `codex-gate.yml` (adds the gate-owned `ci:full` on READY) and
`stuck-thread-watch.yml`. The watcher is advisory: it posts one `@codex review` comment on a stuck
Devin thread with `CODEX_TRIGGER_PAT` and keeps one Linear `needs-decision` roll-up per PR
(`Stuck PR threads: <repo>#<PR>`). It never resolves threads, pushes or merges
(`.github/scripts/stuck_thread_watch.py` docstring; AGENTS.md:382), so a stuck thread stays open
until a human resolves it or records the decision. They are covered by
their own tests (`tools/test_codex_gate.py`, `app/tests/test_stuck_thread_watch.py`) and by contract §7, not by
`CrossLaneAdmissionTests`.

## 6. Cost and capacity check

- Every job must use a standard GitHub-hosted label; the cross-lane tests accept `ubuntu-latest`,
  `ubuntu-24.04`, `ubuntu-22.04`, `windows-2025`, `windows-latest` and `macos-latest`. Since CSL-408 these are free **because the repository is
  public**. Making it private would meter minutes (Team includes 3,000 per month; GitHub docs,
  Actions billing); re-plan before that.
- `tools/test_codex_gate.py` (`test_test_jobs_use_free_github_hosted_runners`) fails if any
  workflow names a Blacksmith runner. claude.yml runs it only when fixer or gate paths change
  (claude.yml:22-46, 521); codex-gate.yml runs it on every listed same-repository `pull_request` event type
  (codex-gate.yml:11-12, 240-254). The cross-lane tests in `tools/test_ci_dispatch_scope.py`, which
  `ci.yml` runs on every test-running PR (ci.yml:438), fail if a writer workflow leaves GitHub-hosted
  runners.
- Usage over time: the criterion 2 measurements on CSL-255 (queue wait, time to required check,
  concurrency).

## 7. Changing a workflow

- `.github/workflows/**` changes need a Linear issue explicitly assigned by a human (AGENTS.md:284-286).
- **Contract rule (agreed by the CI and Reliability lanes, 2026-10-21; founders decide whether it
  becomes a repository rule):** a PR that changes a workflow, `scripts/ci/**`, a required check or
  the rulesets also updates [ci-event-contract.md](ci-event-contract.md) in the same PR, including
  re-pinned `file:line` citations when lines move, or says "no contract row changes" and why.
- Writer workflows (`claude.yml`, `conflict-scan.yml`, `claude-conflict-writer.yml`) must keep the
  admission surface that `CrossLaneAdmissionTests` checks statically: no CI dispatch, re-run,
  ready-for-review or CI-label add (the only CI-label write is revoking a gate-owned `ci:full`);
  pushes only from the model-free publish jobs as one ordinary fast-forward (no prep or publish job
  holds `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` or the Claude action); one shared
  `claude-<PR>` lock without cancellation; the eight-commit guard on both claude.yml writers, the
  Devin path's three-attempt reservation, one publish per prepared run, and the pause. A new Claude
  workflow, or a new job in any of the three, fails the test until it is classified there
  (conflict-scan.yml: the read-only `conflict-scan` job and the `conflict-resolve` caller). Retry behaviour itself is tested in
  `tools/test_claude_fixer_*.py` and `tools/test_claude_pr_fixer.py`.
- **The conflict writer has no eight-commit guard and no cross-run attempt budget.** Each run
  publishes at most once (claude-conflict-writer.yml:2245, 2321-2330); the scan starts it every two
  hours and on manual dispatch, never on a `main` push (conflict-scan.yml:12-14, 355). A PR that stays
  conflicted can get a Claude merge attempt on every scheduled scan; pause it if that is unwanted.

## 8. Owners

| Area | Owner |
|---|---|
| CI workflows, required checks, this runbook, the contract | CI lane (Lucas's session), founders approve |
| `claude.yml`, `conflict-scan.yml`, `claude-conflict-writer.yml`, fixer scripts | Reliability lane, founders approve |
| CI failure on `main` | Devin "Main CI failure fixer" automation (human-managed, AGENTS.md:441); a founder triages |
| Rulesets and repository settings | Founders (admins) |

Ownership changes over time; check the Linear assignee before acting.

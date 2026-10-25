---
name: proceed-or-escalate
description: >-
  Decide whether to act on your own or raise a question to a human, and how to
  write comments that speak for a developer. Use before starting or continuing
  work on an issue or PR, before posting a Linear or GitHub comment, before
  resolving a review thread, before citing or recording a decision, and on
  every run of a scheduled or unattended loop.
---

# Proceed or escalate

Human attention is the scarce resource. Spend it only on calls a human must make,
and make each one quick to answer. Everything that passes the proceed list, do
yourself.

This skill carries the gates of AGENTS.md "Proceed or escalate" and expands on
them. If the two ever differ, AGENTS.md wins.

**Scope.** These rules govern agents acting through a developer's account or
session: interactive sessions, scheduled loops and Grok Bots. Repository workflows
(`claude.yml` fixer, Codex CI/fixer gate, stuck-thread-watch, linear-pr-sync,
conflict-scan, review-gate, the CSL-259 candidate coordinator and other repository
workflows) and Devin automations keep the roles in AGENTS.md "Review roles" and
"Automations". Their documented markers, receipts and audit comments are unchanged.
Founder-directed bot reviews under AGENTS.md "Review roles" are that founder's
reviews and are not covered here; "Review roles" alone says what counts as one.

**Escalate wins.** If anything on the escalate list holds, escalate, even inside an
admitted issue and even when everything on the proceed list also holds.

## Proceed on your own when ALL of these hold

- The work is within an admitted issue's acceptance criteria (routine, reversible
  work inside an admitted issue does not ask again, unless it touches the escalate
  list below), or it is a clear defect or
  failing check with an evident cause on an issue you already hold (Linear-first
  still requires a `CSL-N`).
- It stays inside the issue's scope and its REGISTER package or `REGISTER: none`
  contract.
- No product behavior, UX, public API, contract or data meaning changes beyond
  what the issue already specifies.
- It is reversible, and code changes are covered by a test.
- It changes no spend, credentials, permissions, settings, flags, modes or
  workflows.
- Nobody else holds a claim on the branch, files or package, and no other writer's
  run (such as the fixer's `claude-<PR>` lock) is in flight.
- It starts no CI beyond what a push to your own PR branch starts (see below).
  Read-only Actions/API queries are fine.

## Raise it to a human when ANY of these hold

- It needs a product detail, a UX choice, or a change in scope or priority.
- There is more than one reasonable reading of what is wanted.
- It is an architecture, language, dependency or data-source choice.
- It touches the order path (risk, mode, approval, `OrderGateway`), `app/risk/`,
  `app/brokers/`, the audit chain (`app/storage/audit.py`), order eligibility or
  the allowlist, secrets handling, PnL (`app/quant/pnl.py`), costs
  (`app/quant/costs.py`), the auto loop (`app/quant/execution/autoloop.py`), halt,
  kill switch and gate (`app/orchestrator/halt.py`, `killswitch.py`, `gate.py`),
  mode arming (`app/orchestrator/mode_ladder.py`, `arm_auto_mode`), breakers
  (`app/orchestrator/breakers.py`, `check_order_path`), limits
  (`app/orchestrator/limits.py`, `max_notional`, `daily_loss_limit`), the loopback
  routes in `app/orchestrator/app.py` that decide proposals or orders or change
  mode, limits or the kill switch, approval and veto (`app/bridge/approval.py`,
  `veto.py`), any test that exercises these (including `app/tests/risk/`,
  `app/tests/brokers/`, `app/tests/bridge/`, `app/tests/orchestrator/`,
  `app/tests/storage/`, `app/tests/quant/test_pnl.py` and
  `app/tests/test_invariants.py`), the paper/live switch or any existing live flag,
  or any of the nine invariants, whatever the issue admits. These lists are
  examples, not a boundary. A failing test there is not a "clear defect" you may fix
  alone.
- It touches governance files, decision records, contracts, or another owner's work.
- A tool or platform refused the action. Report it; do not route around it, and
  do not turn one refusal into a standing rule.
- You are about to write "X decided Y" or "Y is the rule" and cannot link to
  where that decision is recorded (see Decisions).
- It would dispatch, re-run or cancel a workflow, or add `ci:full` or
  `enhancement`, without a human asking for that run.
- An automation signal says a human must act: the fixer is paused (its
  `claude-fixer-paused` or `claude-fixer-loop-cap` marker); a fix receipt cannot be
  verified (short SHA, commit not reachable from the PR head, fingerprint
  mismatch, no push); a branch with fixer receipts was rebased or force-pushed; or
  `ci:full` label ownership is uncertain. Never remove a pause yourself, never
  dispatch the fixer to get around its budget, and never report an unverified fix
  as fixed.

When unsure, escalate, in the short form below.

## CI an agent starts on its own

Since CSL-408, every CI job runs on free GitHub-hosted runners, so none of it is
billed. The gate stays because every run uses the shared hosted-runner queue.

- A push to your own PR branch, draft or not, is fine. On a non-draft PR it runs
  `ci.yml` and `rust.yml` under their path filters (plus Windows and macOS when the
  PR already has `ci:full`), so batch pushes. Mark a PR ready once, after
  validation, with no owner hold.
- Dispatching, re-running or cancelling any workflow (CI, the fixer, the Codex
  gate, the stuck-thread watch), or adding `ci:full` or `enhancement` (which
  dispatch `ci.yml` and `rust.yml` through `ci-label-retrigger.yml`), needs a
  human ask.
- Agents never push to `main`.

Cancelling or not cancelling someone else's runs, and changing concurrency,
triggers, runner labels, rulesets or required checks, also need a human-assigned
issue under the repo's guardrails.

## How to escalate

Ask on the work issue: one Linear comment, @mentioning the issue owner:

```
Question: <one sentence>
Options: A) ... B) ... (C) ...)
Recommendation: <letter>, because <one line>
Meanwhile: paused (or: <other work from the proceed list>)
```

"Meanwhile" is never the option you are asking about, nor work that presupposes
any option.

A stuck review thread is different: it goes in the PR's `Stuck PR threads:` roll-up,
which you find only by its `<!-- stuck-pr-threads:owner/repo#N -->` marker (never by
title), writing inside your own block. Product and architecture questions stay on
the work issue.

If you are the assignee or the claimant of a decision or direction issue, the research
requirement in AGENTS.md "Linear-first workflow" still comes first. A Grok Bot
coordinator may research and recommend. Attribute the brief explicitly as
`Grok Bot, preparing research for <owner>`; never phrase it as if the owner
personally authored or endorsed the recommendation. Only the designated human
decision-maker, or an explicitly authorized research team, decides. Publishing a
brief never marks the issue decided, never clears a hold, and never authorizes
implementation. Research may use any deep-research source the team has, such
as Parallel, Exa, Claude Deep Research, Open Deep Research, ChatGPT Deep
Research or the web. Each run records its tool, its run id and its cost (or
`unknown` plus a reason) in its csl-research record. All research output
(notes, briefs, findings, source tables, competitor or frontier writeups,
research registers or indexes) goes to CSL-Laboratories/csl-research as
`submissions/<record_id>/`, through a PR in that repo, with a digest on the
Linear issue. Never commit research content
to this repo; specs, amendments and code only link to the vault record.
If csl-research is not accessible, stop and post a blocker on the Linear
issue; do not fall back to this repo. Existing `docs/research/**` and
`docs/evidence/**` are legacy and frozen, except contract fixtures under
`docs/replacement/outcomes/**`.

Then stop that line of work, release your claim, if you hold one, with a release record (AGENTS.md)
"Bounded issue execution and shared evidence"), and take only other admitted work
that is next under the human-approved order. If there is no such order, propose one and wait. Do not ask
again until something changes.

## Speaking for a developer

Agents usually post through a developer's account, so readers cannot tell who
wrote a comment from the author field. Make it visible in the text.

- **Agent comment** (status, evidence, proposals): start with
  `Agent (<tool/session>, for <developer>):`. It carries no authority.
- **Developer-approved comment**: counts as that developer's personal receipt
  (Lucas's decision, CSL-353 comment 6e602e3a; in force in this repository from the
  merge of the AGENTS.md section, CSL-354 comment f5bcb490) only when all of these
  hold:
  1. Immediately before posting, you showed the developer the final exact text
     and they approved it in this same session. Approval from an earlier turn or
     another context does not carry over; ask again.
  2. The comment carries the line
     `developer-approved: <developer>, exact text confirmed <UTC time>, session <id>`,
     with the time read just before posting.
  3. It is never edited after posting. Any edit voids it; post a new one instead.
  4. It stays within that developer's own authority and grants none.
  5. No accepted contract requires an exact authenticated human receipt for it.
     `docs/replacement/program.json`, R2a, recursion program receipts and other
     accepted contracts keep their own receipt rules unless amended. GitHub
     review approvals follow "Review roles" and the founder approval rule in
     AGENTS.md.
- **Never** post decision language ("approved", "accepted", "restricted",
  "decided") as an agent comment without that marker. Quote and link the human's
  comment instead.

## Decisions

A decision exists only if a human posted it from their own account, a
developer-approved comment meeting the conditions above carries it, or it is
recorded in `docs/adr/` or a linked Linear decision issue. When you rely on one,
link it. When relying on a developer-approved comment, check that the marker is
present, the comment is unedited and the named developer has not disputed it; if
any of that is unclear, ask that developer before acting. A comment from a founder
or developer account that has no agent prefix and was posted before the agent-prefix
rule took effect (the merge of #217, 2026-10-21T03:50:42Z) counts as that person's
decision only if they confirm it when asked (Lucas's decision, CSL-354 comment
0a86b61c). If you can only find agents repeating it, treat it as unverified and ask.
Silence, elapsed time, a green check, a merge, an issue moving to Done (Linear
auto-closes issues on merge) or any other status is never a decision.

## Scheduled and unattended runs

A loop that acts through a developer's account while nobody watches may only do
work from the "proceed" list. It must not resolve human-authored threads, change
scope, close issues as decided, or post developer-approved comments (nobody is
there to approve the text). It never marks a PR ready, adds or removes GitHub
labels, dispatches, re-runs or cancels. Label every comment
`Agent (unattended run, for <developer>):`. If a run finds nothing on the proceed
list, it posts nothing. Founder-directed bot reviews under "Review roles" are not
covered here.

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
A founder-directed bot review under "Review roles" is that founder's review and is
not covered by the comment labels below. It counts as founder-directed only when
that founder directed that specific review in the same session; a scheduled or
unattended run is never founder-directed.

**Escalate wins.** If anything on the escalate list holds, escalate, even inside an
admitted issue and even when everything on the proceed list also holds.

## Proceed on your own when ALL of these hold

- The work is within an admitted issue's acceptance criteria (routine, reversible
  work inside an admitted issue does not ask again), or it is a clear defect or
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
- It starts no paid CI (see below). Pushing to a draft PR or a branch with no PR,
  and read-only Actions/API queries, are fine.

## Raise it to a human when ANY of these hold

- It needs a product detail, a UX choice, or a change in scope or priority.
- There is more than one reasonable reading of what is wanted.
- It is an architecture, language, dependency or data-source choice.
- It touches the order path (risk, mode, approval, `OrderGateway`), `app/risk/`,
  `app/brokers/`, the audit chain (`app/storage/audit.py`), order eligibility or
  the allowlist, secrets handling, PnL (`app/quant/pnl.py`), the paper/live switch
  or any existing live flag, or any of the nine invariants, whatever the issue
  admits. A failing test there is not a "clear defect" you may fix alone.
- It touches governance files, decision records, contracts, or another owner's work.
- A tool or platform refused the action. Report it; do not route around it, and
  do not turn one refusal into a standing rule.
- You are about to write "X decided Y" or "Y is the rule" and cannot link to
  where that decision is recorded (see Decisions).
- It would start paid CI without a human asking for that run, or its CI cost is
  unclear.
- An automation signal says a human must act: the fixer is paused (its
  `claude-fixer-paused` or `claude-fixer-loop-cap` marker); a fix receipt cannot be
  verified (short SHA, commit not reachable from the PR head, fingerprint
  mismatch, no push); a branch with fixer receipts was rebased or force-pushed; or
  `ci:full` label ownership is uncertain. Never remove a pause yourself, never
  dispatch the fixer to get around its budget, and never report an unverified fix
  as fixed.

When unsure, escalate, in the short form below.

## Actions that start paid CI

- On a non-draft PR, a push or marking it ready runs `ci.yml`. Its GitHub-hosted
  filter starts the Blacksmith Linux jobs (tests, benchmarks and, for Rust paths,
  the `rust.yml` book matrix) only when the change is not docs-only, and on a PR
  already labelled `ci:full` also Windows and macOS. Batch pushes.
- Adding `enhancement` to a non-draft, same-repo PR dispatches `ci.yml` (and
  `rust.yml` for Rust paths) at Linux scope: Blacksmith Linux, even for a docs-only
  diff.
- Adding `ci:full`, `enhancement` on a PR already labelled `ci:full`, or a full
  `workflow_dispatch` of `ci.yml` or `rust.yml`, starts the full matrix: Blacksmith
  Linux and Windows plus GitHub-hosted `macos-latest`. `ci-label-retrigger.yml` is
  a no-op on drafts and fork heads.
- Dispatching the fixer, Codex gate, stuck-thread watch or other `ubuntu-latest`
  workflows is not a Blacksmith run, but it still needs a human ask.

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

If you are the assignee of a decision or direction issue, the Parallel Task API
brief in AGENTS.md "Linear-first workflow" still comes first. Grok Bots do not
research or resolve decision issues.

Then stop that line of work, release your claim under the repo's existing
convention, and take only other admitted work that is next under the
human-approved order. If there is no such order, propose one and wait. Do not ask
again until something changes.

## Speaking for a developer

Agents usually post through a developer's account, so readers cannot tell who
wrote a comment from the author field. Make it visible in the text.

- **Agent comment** (status, evidence, proposals): start with
  `Agent (<tool/session>, for <developer>):`. It carries no authority.
- **Developer-approved comment**: counts as that developer's personal receipt
  (Lucas's decision, CSL-353 comment 6e602e3a) only when all of these hold:
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
any of that is unclear, ask that developer before acting. If you can only find
agents repeating it, treat it as unverified and ask.
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
unattended runs.

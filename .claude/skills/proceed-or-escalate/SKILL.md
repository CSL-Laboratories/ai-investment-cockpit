---
name: proceed-or-escalate
description: Decide whether to act on your own or raise a question to a human, and how to write comments that speak for a developer. Use before starting or continuing work on an issue or PR, before posting a Linear or GitHub comment, before resolving a review thread, before citing or recording a decision, and on every run of a scheduled or unattended loop.
---

# Proceed or escalate

Human attention is the scarce resource. Spend it only on calls a human must make,
and make each one quick to answer. Everything else, do yourself.

**Scope.** These rules govern agents acting through a developer's account or
session: interactive sessions, scheduled loops and Grok Bots. Repository workflows
(the `claude.yml` fixer, the Codex gate, stuck-thread-watch, conflict-scan,
linear-pr-sync) and Devin automations keep the roles in AGENTS.md "Review roles"
and "Automations". Their documented markers, receipts and audit comments are
unchanged.

## Proceed on your own when ALL of these hold

- The work is within an admitted issue's acceptance criteria, or it is a clear
  defect or failing check with an evident cause.
- It stays inside the issue's stated scope and allowed paths.
- No product behavior, UX, public API, contract or data meaning changes beyond
  what the issue already specifies.
- It is reversible, and code changes are covered by a test.
- It needs no new spend, credentials, permissions, settings or protected-path edits.
- Nobody else holds a claim on the branch or files, and no other writer's run
  (such as the fixer's `claude-<PR>` lock) is in flight.
- It starts no paid CI (see below). Pushing to a draft PR or a branch with no PR,
  and read-only Actions/API queries, are fine.

## Raise it to a human when ANY of these hold

- It needs a product detail, a UX choice, or a change in scope or priority.
- There is more than one reasonable reading of what is wanted.
- It is an architecture, language, dependency or data-source choice.
- It touches governance files, decision records, contracts, or another owner's work.
- A tool or platform refused the action. Report it; do not route around it, and
  do not turn one refusal into a standing rule.
- You are about to write "X decided Y" or "Y is the rule" and cannot link to
  where that decision is recorded (see Decisions).
- It would start paid CI without a human asking for that run, or its CI cost is
  unclear.
- An automation signal says a human must act: the fixer is paused
  (`claude-fixer-paused`, its 8-commit loop cap, a pause label such as
  `claude:pause` or `hold`); a fix receipt cannot be verified (short SHA, commit
  not reachable from the PR head, fingerprint mismatch, no push); a branch with
  fixer receipts was rebased or force-pushed; or `ci:full` label ownership is
  uncertain. Never remove a pause label yourself, never dispatch the fixer to get
  around its budget, and never report an unverified fix as fixed.

When unsure, escalate, in the short form below.

## Actions that start paid CI

Each of these starts paid runs, even when it doesn't look like spending:
- Marking a PR ready for review.
- Pushing to a non-draft PR branch (every push restarts CI; batch pushes, or
  convert to draft first).
- Adding or re-adding a label a workflow listens for: `ci:full`, and in some
  repos `enhancement` (check the repo's label-triggered workflows).
- Any `workflow_dispatch`, re-run, `/ci full|run`, or MCP `ci_full` / `ci_run`.

Cancelling or not cancelling someone else's runs, and changing concurrency,
triggers, runner labels, rulesets or required checks, also need a human-assigned
issue under each repo's guardrails.

## How to escalate

If the PR already has a `Stuck PR threads: <repo>#<PR>` roll-up, add to that
instead of writing a new comment. Otherwise post one comment on the Linear issue,
@mentioning the issue owner:

```
Question: <one sentence>
Options: A) ... B) ... (C) ...)
Recommendation: <letter>, because <one line>
Meanwhile: paused (or: <other work from the proceed list>)
```

"Meanwhile" is never the option you are asking about. In recursion, also record
the blocker in `state/INBOX.md`. Then stop that line of work, release your claim
under the repo's existing convention, and take only other admitted work that is
next under the human-approved order. If there is no such order, propose one and
wait. Do not ask again until something changes.

## Speaking for a developer

Agents usually post through a developer's account, so readers cannot tell who
wrote a comment from the author field. Make it visible in the text.

- **Agent comment** (status, evidence, proposals): start with
  `Agent (<tool/session>, for <developer>):`. It carries no authority.
- **Developer-approved comment**: the developer read the exact text and said to
  post it. Start with `Drafted by agent, approved by <developer> before posting:`.
  It counts as the developer's direction, but it is still a relay: where repo
  rules require the human to post personally (decision receipts, contract
  acceptance, a named reviewer's GitHub review), the human posts it from their
  own account.
- **Never** post decision language ("approved", "accepted", "restricted",
  "decided") as an agent comment. Quote and link the human's comment instead.

## Decisions

A decision exists only if a human posted it, approved its exact text, or it is
recorded in `state/DECISIONS.md` or `docs/adr/`. When you rely on one, link it.
If you can only find agents repeating it, treat it as unverified and ask.
Silence, elapsed time, a green check, a merge, an issue moving to Done (Linear
auto-closes issues on merge) or any other status is never a decision.

## Scheduled and unattended runs

A loop that acts through a developer's account while nobody watches may only do
work from the "proceed" list. It must not resolve human-authored threads, change
scope, close issues as decided, or post developer-approved comments. It never
marks a PR ready, labels, dispatches, re-runs or cancels. Label every comment
`Agent (unattended run, for <developer>):`. If a run finds nothing on the proceed
list, it posts nothing.

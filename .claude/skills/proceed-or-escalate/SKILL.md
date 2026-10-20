---
name: proceed-or-escalate
description: Decide whether to act on your own or raise a question to a human, and how to write comments that speak for a developer. Use before starting or continuing work on an issue or PR, before posting a Linear or GitHub comment, before resolving a review thread, before citing or recording a decision, and on every run of a scheduled or unattended loop.
---

# Proceed or escalate

Human attention is the scarce resource. Spend it only on calls a human must make,
and make each one quick to answer. Everything else, do yourself.

## Proceed on your own when ALL of these hold

- There is a clear defect or a failing check, and the cause is evident.
- The change stays inside the issue's stated scope and allowed paths.
- No product behavior, UX, public API, contract or data meaning changes.
- It is reversible, and existing tests cover it or you add one.
- It needs no new spend, credentials, permissions, settings or protected-path edits.
- Nobody else holds a claim on the branch or files.
- It starts no paid CI (see "Actions that start paid CI" below). Pushing to a draft
  PR or a branch with no PR, and read-only Actions/API queries, are fine.

## Raise it to a human when ANY of these hold

- It needs a product detail, a UX choice, or a change in scope or priority.
- There is more than one reasonable reading of what is wanted.
- It is an architecture, language, dependency or data-source choice.
- It touches governance files, decision records, contracts, or another owner's work.
- A tool or platform refused the action. Report it; do not route around it, and
  do not turn one refusal into a standing rule.
- You are about to write "X decided Y" or "Y is the rule" and cannot link to the
  human's own comment that says so.

- It would start paid CI without a human asking for that run.

When unsure, escalate, in the short form below.

## Actions that start paid CI

Each of these starts paid runs, even when it doesn't look like spending:
- Marking a PR ready for review.
- Pushing to a non-draft PR branch (every push restarts CI; batch pushes, or
  convert to draft first).
- Adding or re-adding `ci:full`, `enhancement` or any label a workflow listens for.
- Any `workflow_dispatch`, re-run, `/ci full|run`, or MCP `ci_full` / `ci_run`.

Cancelling or not cancelling someone else's runs, and changing concurrency,
triggers, runner labels, rulesets or required checks, also need a human-assigned
issue under each repo's guardrails.

## How to escalate

One comment on the Linear issue, @mentioning the issue owner:

```
Question: <one sentence>
Options: A) ... B) ... (C) ...)
Recommendation: <letter>, because <one line>
Meanwhile: <what you will do, or "paused">
```

Then stop that line of work, release your claim, and move to other admitted work.
Do not ask again until something changes.

## Speaking for a developer

Agents usually post through a developer's account, so readers cannot tell who wrote
a comment from the author field. Make it visible in the text.

- **Agent comment** (status, evidence, proposals): start with
  `Agent (<tool/session>, for <developer>):`. It carries no authority.
- **Developer-approved comment**: the developer read the exact text and said to post
  it. Start with `Drafted by agent, approved by <developer> before posting:`. This
  counts as the developer's own comment.
- **Never** post decision language ("approved", "accepted", "restricted",
  "decided") as an agent comment. Quote and link the human's comment instead.

## Decisions

A decision exists only if a human posted it or approved its exact text. When you
rely on one, link the comment. If you can only find agents repeating it, treat it as
unverified and ask. Silence, elapsed time, a green check, a merge or an issue status
is never a decision.

## Scheduled and unattended runs

A run nobody is watching may only do work from the "proceed" list. It must not
resolve human-authored threads, change scope, close issues as decided, or post
developer-approved comments. It never marks a PR ready, labels, dispatches, re-runs
or cancels. Label every comment `Agent (unattended run, for
<developer>):`. If a run finds nothing on the proceed list, it posts nothing.

# Contributing

## Linear-first operations (applies to every agent and Grok Bot)

Linear is the basis of operations. Bounded workers with a frozen brief (for
example `muse-synthesizer`) are exempt from the Linear and external-research
steps below; Head performs them before freezing the brief, and the worker's own
prompt governs.

- Before starting work, check Linear for overlapping issues and PRs.
- Work only from a Linear issue. Keep it updated with status, progress comments,
  and PR links. Bots and agents post their own status updates and PR links as
  comments on that issue.
- Every review request, blocker, or question for a dev must be an @mention
  comment on the matching CSL Linear issue so the Linear bot notifies them. A
  GitHub mention alone is not enough. Linear handles: Chaandan
  `@chaandansaini101`, Samir `@samirshahahmed`, Lucas `@lucassalvador987`.
- Autonomously file research findings, ideas, and bugs to Triage, deduped.
- Ground every implementation in the best available research: Parallel search
  for web research and Context7 for current library docs.
- **Wait for jobs before reviewing:** no human or bot approves or requests
  changes on a PR until Devin Review, the Claude fixer jobs (`devin-review-fix`,
  `resolve-pr`), and required CI have all finished on the current head commit.
  Devin Review's own verdict is exempt: its `changes_requested` is what starts
  `devin-review-fix`. A review on a stale head does not count; after new
  pushes, the reviewer reviews again. Review requests and their status are
  posted as @mention comments on the CSL Linear issue. Check job status on the
  PR directly; do not wait on a readiness notice, since no watcher that posts
  one is part of this repo.

Full agent workflow, review roles, and invariants: [`AGENTS.md`](AGENTS.md) and
[`CLAUDE.md`](CLAUDE.md). Operating model:
[How CSL works](https://linear.app/csl-laboratories/document/how-csl-works-922e0df6026f).
File work on the [CSL team](https://linear.app/csl-laboratories/team/CSL), not as
a GitHub issue.

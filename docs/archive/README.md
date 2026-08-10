---
title: Archive — superseded planning documents
status: archive
---

# Archive

**Nothing here is current.** These documents are kept because they record *why* decisions were
made, and because 306 wikilinks across the corpus point at them — `[[name]]` resolves by
basename, so archiving preserves every one of those links while removing the docs from the
navigational path.

**Do not plan from these.** [[PLAN]] defines what we are building; [[REGISTER]] is where work
is claimed. If something here contradicts either, the archived doc is wrong.

Archived 2026-08-09, after the branch-consolidation audit.

## What is here and why

| Doc | Size | Why it was archived |
|---|---|---|
| [[production-playbook]] | 143 KB | Orchestration scaffolding for a multi-agent build session that has finished. Prompts and sequencing, not design. |
| [[remaining-work-session-plan]] | 65 KB | Same — session sequencing and "paste-ready" blocks for a run that is over. |
| [[path-to-production]] | 29 KB | Superseded by [[PLAN]] §4. Its milestone framing predates the MVP definition and the [[llm-hybrid-trading]] research. |
| [[integration-and-remote-operations-plan]] | 29 KB | Superseded by [[PLAN]]. Remote-ops content is now a post-MVP concern; the AI-provider decisions it debates are settled (both Claude and Codex runtimes shipped). |
| [[cockpit-review-and-integrated-plan]] | 10 KB | The CLI-vs-SDK verdict it exists to deliver is long settled — the tree uses the CLI. |

**~275 KB, and none of it was referenced by a single line of production code** — which is the
test that decided what moved. Documents that code cites are load-bearing regardless of how they
are framed. The clearest case: [[build-scaffold-ultracode]] reads like a disposable
build-session artifact and is cited by **43 files in `app/`** because it is the WP-series
contract register. It stays live. Framing is not evidence; the citation graph is.

## Why archive rather than delete

Deleting would lose the reasoning, and the reasoning is the expensive part. The problem these
docs caused was **navigational** — a reader could not tell which of eight overlapping "plans"
was current, and the test-count baseline was copy-pasted into seven of them with every copy
stale. Moving them fixes that without destroying the record.

## The rule that put them here

From [[PLAN]] §5: *a plan that synthesizes other plans and does not retire them just becomes
the next document.* That is the same failure as building a second code path and never
scheduling the first one's deletion — which is exactly how `app/filings/` ended up as 4,594
unreachable lines sitting beside the path it was meant to replace. Same mechanism, different
medium.

So: any new doc claiming to supersede another carries a `supersedes:` list in its frontmatter,
and the docs it names move here **in the same PR**. If nothing is superseded, the doc has to
say why it is new territory.

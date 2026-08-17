---
title: GitHub-outage session handoff — 2026-08-17
type: evidence
status: live
last-verified: 2026-08-17
related:
  - ../README
  - ../PLAN
  - ../REGISTER
  - bug106-edgar-baseline-timestamp-2026-08-17
  - w24-merger-semantic-verifier-2026-08-17
  - ../research/rw19-event-source-characterization-2026-08-12
---

# GitHub-outage session handoff — 2026-08-17

GitHub reported a broad incident affecting Git operations, pull requests, APIs, and Actions
during this session. Work therefore stopped at clean local commits. Nothing in this record treats
the outage as a test failure or waives CI, review, or empirical evidence.

## Authoritative starting point

`origin/main` was last fetched at PR #36 merge `2cbb6d5`. Do not start a new production package.
Publish and merge the local branches below first, one at a time, and preserve `read_only` mode.

| Order | Branch | Local commits not on `origin/main` | State |
|---:|---|---|---|
| 1 | `work/bug106-edgar-baseline-timestamp` | `5dea321`, `ae2f312` | clean; Windows 2,248 passed / 1 skipped; Ruff clean; live recovery evidence green |
| 2 | `work/w24-merger-semantic-verifier` | `68fd541`, `0e419f0`, `ceb75d5` | clean; remote has through `0e419f0`; local hardening is one commit ahead; Windows 2,266 passed / 1 skipped; Ruff clean |
| 3 | `evidence/rw19-20260817-characterization` | `212e41b` | clean; docs/evidence only; 13/13 retained manifests hash-verified |
| 4 | `docs/20260817-offline-session-handoff` | this reconciliation commit, based on the W-24 tip | publish last, after rebasing/merging the three preceding results into current `main` |

BUG-106 and W-24 have no code conflict in a local `git merge-tree` check. Their only predicted
conflicts are `docs/README.md` and `docs/REGISTER.md`. RW-19 is evidence-only but also updates the
register. Resolve those status documents to the final state recorded here; do not drop either
package's evidence while taking one side of a conflict.

## What landed locally

### BUG-106

EDGAR baseline timestamp authority now comes from the signed audit transition rather than an
independent wall-clock sample. A bounded one-time repair covers legacy rows without creating a
general timestamp override. A clean committed `read_only` service run repaired all 11 legacy
baselines once, ingested one filing/four deltas, and completed the next 11/11 scheduled poll with
zero current errors and a valid audit chain at sequence 10,040. See
[[bug106-edgar-baseline-timestamp-2026-08-17]].

### W-24

The independent deterministic cash-merger semantic verifier is implemented and production
composition has no permissive fallback. A fresh direct attack pass closed expanded negation,
unrelated cash-per-share text, conditional outcome, stale same-day quote, foreign Discord
channel, and final EDGAR CIK-binding attacks. The preferred read-only DeepSeek Pro/Flash retries
timed out without a verdict or edits, so they are missing attestation—not approval. See
[[w24-merger-semantic-verifier-2026-08-17]] and
[[../runbooks/w24-merger-semantic-verifier]].

### RW-19

All 13 local manifests verified. Two completed 11-symbol sessions show combined IEX/SIP volume
of 3.5841% and IEX recall of 75.33% / 74.96% / 75.37% at 10 / 25 / 50 bps. Three bounded live
IEX samples observed a maximum 1,114 frames in one second. A 180-second news sample contained one
WebSocket item and no REST item, so identity equivalence remains unproven. DEC-07 stays open; no
adapter may assume either IEX sufficiency or SIP necessity. See
[[../research/rw19-event-source-characterization-2026-08-12]].

## Stopped-system preflight

- mode: `read_only`; loopback host valid; port 8787 free; HALT absent;
- audit chain valid at sequence 10,040;
- zero trade proposals, risk decisions, approvals, and orders;
- 11-symbol awareness watchlist; merger execution watchlist empty;
- SEC User-Agent, API key, audit HMAC, Alpaca, Finnhub, Discord token, operator allowlist, and
  brief/alerts/proposals/system channels present (presence only; no value was printed);
- private Discord audit-witness channel id absent, so SEC-11 and W-24 witness authority remain
  open; the local audit mirror is not an out-of-domain trust source;
- decision runtime `claude`; low-privilege decision account/cwd not provisioned;
- earnings-calendar lookahead explicitly `0` (feed disabled); and
- storage reserve healthy. The service was not left running.

This is sufficient for a controlled background `read_only` bring-up. It is not sufficient for an
opening paper order, paper-MVP completion, live capital, `review_live`, `auto_veto`, or
`auto_full`.

## Publish and merge sequence

Wait until GitHub reports Git operations, pull requests, APIs, and Actions operational. Then:

1. Fetch `origin`; confirm `origin/main` has not moved beyond an unreviewed dependency.
2. Push `work/bug106-edgar-baseline-timestamp`, open a small PR, run the six-job matrix, review,
   and merge it.
3. Update `work/w24-merger-semantic-verifier` from the new `origin/main`. Resolve only the two
   expected status-doc conflicts while preserving both records. Push normally—the remote branch
   already exists—then run review and the full matrix before merging.
4. Update `evidence/rw19-20260817-characterization` from the new `origin/main`, preserve its
   newer measurement wording in `docs/REGISTER.md`, push, and merge its docs-only PR.
5. Update `docs/20260817-offline-session-handoff` from the final `origin/main`, retain the current
   canonical state, push, and merge the final docs-only reconciliation PR.

Prefer merging current `origin/main` into the topic branch if avoiding rewritten public history.
Do not force-push unless the operator intentionally chooses a rebase and verifies the lease.

## Next-session paste-ready prompt

```text
Repository: C:\Users\Lucas\Documents\GitHub\ai-investment-cockpit

GitHub is operational again. Read AGENTS.md, CLAUDE.md, docs/README.md,
docs/PLAN.md, docs/REGISTER.md, and
docs/evidence/github-outage-session-handoff-2026-08-17.md before acting.

Do not start a new production package. First verify all four worktrees/branches are clean,
fetch origin, and publish/review/merge them sequentially:
1. work/bug106-edgar-baseline-timestamp
2. work/w24-merger-semantic-verifier
3. evidence/rw19-20260817-characterization
4. docs/20260817-offline-session-handoff

After BUG-106 merges, update W-24 from origin/main and reconcile docs/README.md and
docs/REGISTER.md without dropping either package's evidence. After W-24 merges, update RW-19;
after RW-19 merges, update the handoff branch. Use normal pushes where possible, run/review the
required GitHub Actions matrix for the code branches, and treat any no-step billing/platform
failure as missing attestation rather than a code failure.

Preserve the current safety state: read_only only, expected edge unavailable, empty merger
watchlist, no paper opening orders, no live/autonomous authority. SEC-11 still requires the
private Discord audit channel. W-24 still requires its witnessed real >=30-deal prospective
cohort, outcome horizon, >=95% parser audit, and current eligible target. RW-14 stale-feed,
RW-15 broker-reconciliation, RW-11 precision, and two W-22 unattended market days remain open.

Once all four branches are merged and main is green, report the resulting merge SHAs and the
exact next safe live-evidence task. Do not begin RW-13.
```

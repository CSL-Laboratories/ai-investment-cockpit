# Exhausted fixer failure handoff

CSL-201 adds a separate intake to the existing stuck-thread watcher. It does
not run another fixer, reset attempts, synthesize a Claude reply/result,
apply READY, push, merge or change the resolver. The existing schedule,
writer queue, delivery identities and mediation markers remain in use.

## Evidence required

1. The PR is open, non-draft, same-repository and authored by a current human
   writer. Its head and eligible Devin bug roots are freshly read. The existing
   receipt consumer excludes valid addressed/needs-human handoffs. Recursion
   additionally validates its authenticated observation history without writes;
   a needed history transition waits for the locked fixer to record it.
2. The existing three authenticated reservations match the exact current head
   and pending-finding fingerprint. These establish exhaustion, not three model
   failures. Changed findings or a new head keep their normal fresh budget.
3. At least one reserved run attempt is a completed failing automatic fixer
   run of `.github/workflows/claude.yml` in this repository, for this PR, branch
   and exact head. Its `devin-review-fix` job succeeded at the guard-install step
   named `Fixer attempt <head>:<finding-fingerprint>` before the Claude action
   failed or timed out. Human/concurrency cancellations do not qualify.
   GitHub attempt/job metadata supplies
   this binding independently of comment prose. A terminal-post failure alone,
   successful/skipped validation-only run, wrong attempt, missing context or
   pre-model failure cannot qualify.
4. The workflow blob on that head must match the current default-branch blob.
   Modified PR workflows and older workflow versions are not retroactively
   qualified. Public repository visibility is established before Actions
   metadata reads. Prefer the existing read token; if its public-metadata read
   is forbidden, use the independently public endpoint without credentials.
   Existing authenticated reads handle PR/finding data;
   no new token, permission, logs, artifacts or model output are needed.
5. Before delivery, recheck the current finding set, exhausted budget and PR
   head. Refuse observed queued, in-progress, waiting, pending or requested
   Claude workflow runs anywhere in the repository, because mention/manual
   runs can be attributed to the default branch. This conservative refusal
   does not claim that an unrelated PR's writer targets this PR. It is a
   bounded observation, not an
   atomic cross-workflow lock; a writer or review change can race the final
   read. Mediation conveys evidence and requests inspection, never a fix or
   readiness verdict.

Each PR adapter has a shared 60-read/60-second ceiling, at most five pages per
comment/thread connection and at most three candidate run attempts. Denial,
public API rate limiting, malformed/duplicate/incomplete snapshots and budget
exhaustion stop new failure admissions for that PR and retry on a later watcher
cycle. A 429 or transient server failure receives at most one bounded retry;
primary public rate limiting defers to a later cycle. Incomplete failure
evidence does not suppress ordinary Claude-thread mediation or Recursion's
existing resolver. Existing Linear roll-ups and human notes are preserved
while failure evidence is stale/unavailable; no partial checklist replaces them.
Only complete evidence enters existing per-thread deduplication and caps.

## Repository-specific behavior

Cockpit keeps its rolling 24-hour mediation cap and never resolves threads.
Recursion keeps UTC-day mediation/resolve caps and its existing authenticated
Codex ruling, current full SHA, required-green-check and citation requirements.
Failure evidence does not participate in that resolver. A failure-triggered
request can ask Codex about a no-Claude thread; a later independently qualified
Codex ruling for that requested thread follows the existing policy. No resolver
predicate or authority is relaxed.

## Landing and operational limits

Stack this change on the guarded-fixer reliability draft (#155 in Cockpit,
#211 in Recursion). Cockpit stays after #147 in Lucas's authorized sequence.
Use ordinary dependency/base merges after those owners settle their work;
do not import moving upstream branches or rewrite their history.

CSL-255 owns CI cost/queue changes. This patch does not rely on proposed
merge-queue or `review-gate` behavior, change required checks, or alter cost,
filters/concurrency/debounce policy. Compose shared `claude.yml` changes with
that owner and CSL-89 before landing. Cloud bootstrap #156/#212 is separate.

Offline tests cover provenance, races, no false handled receipts, caps,
deduplication and fresh retry eligibility. They are not evidence of an
autonomous fix-to-push or live escalation. Qualification needs an existing
genuine eligible finding, coordinated single writer, deployed matching
workflow and actual failed context-bound run after genuine exhaustion.
Never manufacture findings/events or reopen resolved findings to qualify it.
Historical #142 lacks the new context binding and intentionally cannot be
replayed as new evidence. Existing workflow validation blocks pre-merge live
qualification of a changed workflow. No bypass is authorized.

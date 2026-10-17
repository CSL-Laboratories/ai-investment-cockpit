# Exact candidate readiness (CSL-259)

This is staged code. It does not activate repository protections, replace direct
`Devin Review`, authorize a merge, or prove live queue enforcement. The existing
required checks and post-merge fallback remain. Protected policy, required-check
activation and a live canary need separate owner approval.

## Candidate and independent review

The coordinator runs trusted default-branch code under the existing
`claude-<PR>` job lock (`queue: max`, no cancellation by this workflow). GitHub
bounds each concurrency group to 100 pending runs and discards arrivals beyond
that bound, so queued events are not guaranteed delivery and fanout can be
delayed; the scheduled scan, not the queue, is the recovery path. It never
executes PR code with the trigger PAT. Its identity includes numeric repository and PR identity,
head/base refs, exact head/base commits, GitHub's two-parent merge commit and its
tree. Both live branch refs are checked before and after merge-object resolution,
so lagged PR metadata cannot qualify an old base. The merge parents must be exactly base then head. A changed base, head or
merge tree invalidates earlier review and coverage even if the other SHAs match.

A GitHub Actions bot receipt reserves a candidate-specific request nonce before
the PAT posts `@codex review`. Only the latest genuine Codex bot review of the
head, submitted after that request, with the exact candidate and nonce echoed,
can grant READY. Old head-only replies, quoted markers, wrong identities,
replayed replies and missing/ambiguous evidence cannot qualify. GraphQL edit
metadata must confirm the selected review is unedited; edited or unversioned
machine journal comments fail closed. A fixer pause
creates a new request generation. READY authorizes the full matrix before human
approval; human approval remains a separate merge condition.

MEDIATE is a separate human-handoff decision. The latest genuine, unedited Codex
review on the current live head may end with a plain terminal `CODEX-GATE: MEDIATE`
even when that head has no mergeable synthetic candidate. This path does not need
candidate/nonce echoes and never authorizes READY or CI. It preserves explicit
human holds, manual label removal and the writer lock, and binds deduplication to
review ID, head and fixer generation. Pending fixer findings may be the reason a
human is needed, so they do not themselves prevent this handoff. Head, review,
hold or generation changes during verification refuse the action; a base-only
change does not invalidate a current-head request for human help.

Drafts, human changes requested, `hold`, `ci:hold`, `do-not-merge` and
`needs-decision` labels, pending genuine fixer findings and manual `ci:full`
removal block delivery. Manual removal persists across heads until an explicit
relabel. Existing authenticated label ownership/intent/invalidation receipts are
preserved; only proven gate-owned labels can be revoked. GitHub's label deletion
API has no compare-and-delete, so the existing final-read race is not atomic.
Closed, draft, conflicted and not-yet-mergeable PRs also invalidate proven owned
legacy READY labels even when no merge candidate can be resolved. Human review
ordering uses submission time, so a late-submitted older review ID still holds.

New READY decisions do not add `ci:full`: that persistent label would otherwise
start full ordinary-push matrices before the next independent review. Existing
manual labels and explicit manual CI remain available, but neither labels nor
their successful runs constitute candidate qualification.

## Delivery and evidence

Each workflow gets a comment reservation before dispatch, subject to the journal
provenance and retention limits below, binding
the candidate, nonce, numeric workflow/actor identity and prior run-ID boundary.
CI and Rust receive the exact head/base/merge/tree and nonce, and validate the
live candidate before allocating paid work and again after each checkout. Each
validating cell first installs its pinned interpreter with the trusted
setup-python action (no dependency cache) and runs the check with `python`, so
validation does not depend on a runner-provided `python3` launcher that Windows
images do not reliably expose. Workflow run metadata may name the workflow file
with an `@ref` qualifier; recovery accepts that form only for the exact file and
still binds the numeric workflow ID, actor, head, branch, nonce and attempt. Every
candidate, including docs-only changes, runs five Python cells and five Rust
cells. The staged `candidate-ci-gates` also requires macOS alongside Linux and
Windows. No existing required check is renamed.

Recovery finds the exact nonce-bearing run, validates its workflow, actor,
repository, head and event, and uses only its latest attempt. All five cells in
each workflow and the mandatory aggregates must complete successfully. Skipped,
missing, pending, canceled or failed cells do not qualify. Listings are bounded
and complete, with fresh run/jobs/candidate/readiness checks before reporting a
live result. This report is diagnostic; it does not publish a durable success
status that could survive base movement.
GitHub does not provide an atomic transaction across PR, review, run and job
reads. Final rechecks narrow that window; they do not eliminate concurrent human
changes or turn this diagnostic into enforced merge admission.

An explicit dispatch rejection can retry, at most three reservations per
candidate/workflow. An unknown POST outcome is never blindly resent: recovery
waits for the uniquely matching run. If none appears, an operator must reconcile
the reservation and actual delivery; age alone does not prove non-delivery.
Failed/canceled runs require a legitimate rerun of the existing run, not an
automatic new full-matrix dispatch. A later rerun invalidates earlier success.
Unknown request-mention outcomes likewise require inspection before repeating
the mention with its existing nonce. Reservations are not evidence of execution.
If a fresh check refuses before the POST is entered, an explicit aborted record
permits recovery; this exception never covers an unknown network outcome. MEDIATE
also reserves before its existing handoff action: an uncertain delivery stops
automatic retries until an operator reconciles it. Candidate workflow queues keep
pending deliveries rather than allowing a delayed duplicate to evict an earlier
nonce-bearing run, within GitHub's bound of 100 pending runs per group; beyond
it a delivery is dropped and recovery treats the missing run as uncertain.
Workflow history is filtered to the trusted policy SHA before the bounded pagination
check.

## Execution policy closure

Candidate dispatches run the default-branch YAML, but that YAML then checks out
the candidate merge and executes candidate-side files: the `run-gates` composite
action with its installer and pin set, `tools/required_ci_gate.py` in the
aggregates, and the validator itself. Staging only the validator would leave
those files candidate-controlled. Each of the six validating jobs (ci `filter`,
`linux`, `windows`, `macos`; rust `filter`, `rust-book`) therefore checks out the
trusted policy commit first, copies the validator and a closed allowlist of
control files into an isolated stage outside the workspace, and records their
blob IDs against the policy SHA. After the candidate checkout, before any local
action, dependency install or test, the staged validator requires every listed
candidate file to be byte-identical to the staged copy, the `run-gates` tree to
contain nothing else, no symlink on any control path, and no known module,
bytecode-cache, package or interpreter-startup path that could shadow the
aggregates' imports. The bytecode regression demonstrates an unchecked cache
replacing identical source and verifies refusal before execution. Missing manifest, policy objects, control
files or a stage inside the workspace fail closed. A candidate that changes
orchestration is refused until that change lands through a separately approved
policy commit; this PR itself is such a change.

The aggregates (`queue-gates`, `rust-gates`) still import the candidate copy of
`tools/required_ci_gate.py`; they are covered because the coordinator requires
`filter` to succeed, and `filter` has already proven that copy identical. The
closure authenticates control-file identity only. `pytest`, Ruff, Cargo, the
Rust benchmarks and `tools/test_*.py` execute candidate code by design, and
nothing here claims to authenticate their semantics. Line-ending or attribute
changes that alter a control file's checked-out bytes are refused rather than
normalised.

Base-only movement is discovered automatically by the existing best-effort
ten-minute scheduled scan; GitHub need not emit a PR synchronize event for it.
An owner can also dispatch the gate with the PR number for earlier reconciliation.
A new candidate needs a new nonce-bound READY before its full matrices start.
The scan does not silently reuse old success, re-add a manually removed label,
or redispatch failed/uncertain runs. Retargeting a PR also triggers reconciliation
through its edited event. Schedule latency is not a guaranteed reaction time.

## Deployment boundary

The default branch must contain the coordinator and validated workflows before
the runtime path can operate. Draft PR bootstrap skips are not runtime proof.
Existing `CODEX_TRIGGER_PAT` grants are reused; if dispatch/read access is absent,
the gate fails closed and does not add permissions or credentials.

Ordinary PR checks still execute PR-controlled YAML. Candidate correlation and
comment receipts do not make those checks a protected policy source. Keep
direct Devin and existing protection until the owner approves a protected policy
and exact required names. The cancellation controller continues to cover only
ordinary PR runs; it does not cancel candidate dispatches. Candidate concurrency
is isolated by digest and never cancels another candidate or manual/label run.

The numeric Actions bot identity authenticates the account, not the originating
workflow. Other workflows using that shared identity can write comment records;
repository writers can also delete journal history. Thus comment provenance and
retention remain assumptions of this staged diagnostic. A protected coordinator
and durable run-bound state source must be designed and approved before consuming
its result as merge admission. The patch does not claim to solve that boundary.

Budget/cadence (P2) remains an owner decision. Timestamp-derived runtime times
published rates is an estimate, not reconciled billed usage or remaining credit.

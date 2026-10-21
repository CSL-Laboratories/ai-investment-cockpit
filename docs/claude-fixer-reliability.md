# Guarded fixer reliability (CSL-201, CSL-377)

## Split writer jobs (CSL-377)

Each Claude writer is a pipeline of three jobs that share the per-PR
`claude-<PR>` lock (`queue: max`, `cancel-in-progress: false`):

| Writer | prepare (trusted) | model (untrusted) | publish (trusted) |
|---|---|---|---|
| `@claude` mention (`claude.yml`) | `mention-prepare` | `claude-mention` | `mention-publish` |
| Devin fixer (`claude.yml`) | `fixer-prepare` | `devin-review-fix` | `fixer-publish` |
| Conflict resolver (`conflict-scan.yml` → `claude-conflict-writer.yml`) | `prepare` | `model` | `publish` |

- **prepare** runs today's locked preflight, pause, commit cap, eligibility and
  attempt reservation unchanged. It never checks out PR code and holds no
  `id-token`. Only prepare and publish write workflow markers.
- **model** gets `contents/pull-requests/issues: read`, no `id-token`, and a
  checkout with `persist-credentials: false`. claude-code-action receives the
  read-only job token (`github_token: ${{ github.token }}`), so it performs no
  OIDC App-token exchange, and `classify_inline_comments: "false"`. There is no
  `git push` tool rule. The model commits locally as `claude[bot]`
  (`bot_id` 209825114) through the `model-api` helper's fixed `commit` and
  queues GitHub writes with it: `comment`, `reply`, `label` (conflict job only) and
  `receipt ROOT addressed|needs-human` (fixer only) append to
  `.claude-fixer-out/outbox.jsonl`; reads still call fixed same-PR endpoints.
  A final `always()` step uploads a Git bundle of new commits, the outbox and
  coarse result fields (never the transcript) as a one-day artifact.
- **publish** runs only `!cancelled()` after a successful prepare of the same
  run attempt, including after a failed model job. It never checks out or runs
  repository code: it writes the embedded `claude_fixer_publish.py` and the
  receipt consumer to `RUNNER_TEMP` and runs them with `python3 -I`. The
  publisher mints the Claude App token like claude-code-action (OIDC audience
  `claude-code-github-action`, Anthropic token exchange), masks it, never
  prints it and revokes it in a `finally` block. It mints only after the bundle
  has been fetched, checked and validated and the live head rechecked (or, with
  no bundle, just before posting queued writes), so no App token exists while
  untrusted objects are parsed (CSL-405). A workflow-validation refusal is a
  fail-closed skip with a notice.

The publisher refuses everything when the live PR is closed, on another
branch or repository, has moved from the recorded head, carries `claude:pause`
or `hold` (any case), has unreadable labels, or `CLAUDE_FIXER_PAUSE` is `true`.
It enforces one attempt per prepared run (exclusive state file plus the
prepared `run_attempt`). It publishes nothing, neither commits nor queued
writes, unless the model step succeeded: a failed, cancelled or timed-out run's
partial commits are never pushed. The model job reports how it packaged its
commits (`unchanged`, `bundled` or `failed`), and the publisher drops every
queued write unless that status matches the artifact, so a failed
`git bundle create` cannot look like a run without commits. It fetches the recorded head into a fresh bare
repository (no system/global config, hooks or fsmonitor; the credential travels
in environment-scoped config, never argv or disk), verifies and fetches the
bundle (no submodule recursion on any fetch; the bundle fetch allows only the
local `file` transport), runs `git fsck` on every object (`.`, `..` and `.git`
tree entries, slash-containing or empty names, bad modes and null object IDs
are errors), and accepts at most 20 new commits that:

- descend linearly from the recorded head (conflict job: exactly one merge
  commit whose parents are the recorded head and recorded base, and the base
  branch must still be at the recorded base, so a stale merge is never pushed);
- have committer `claude[bot]` with the pinned noreply email, so
  `GIT_COMMITTER_NAME=…` cannot forge a human commit and reset the eight-commit
  streak;
- add or change only regular `100644` files (an existing `100755` file may stay
  executable): no symlinks, submodules or new executable bits;
- touch no protected path, in any commit: any path component starting with
  `.git` or `.env`, `.claude/`, `.claude-fixer-out/`, `.claude-fixer-replies/`,
  `scripts/ci/`, `tools/ci_gate.py`, `tools/required_ci_gate.py`,
  `tools/test_claude_pr_fixer.py`, and `CODEOWNERS`, `AGENTS.md`, `CLAUDE.md`
  or `.mcp.json` anywhere (casefolded);
- stay within 300 paths and 5 MB of new blob content.

For the conflict job the merge tree may differ from `git merge-tree` of head and
base only outside protected paths, and a conflict inside a protected path always
needs a human. The push is one ordinary fast-forward to the PR branch: no force
and no lease. After the push the publisher rereads the live head and posts the
outbox as `claude[bot]` in order, rechecking the pause before each write. A
reply must target a root review comment on this PR. Bodies may not contain
`<!--`, so model text cannot carry any workflow marker; an invalid record
rejects the whole outbox. If a bundle was present but not pushed, every queued
write is dropped. Receipts are built by the publisher itself: only for roots
prepare selected, with `base` = recorded head and, for `addressed`, `commit` =
the verified pushed SHA. It then posts the terminal audit as
`github-actions[bot]`, as before.

Interleaving: GitHub releases the lock between jobs, so another run's job can
take it between this run's prepare, model and publish. The publisher's
live-head check makes such interleaving fail closed (no push and no queued
writes), and Git's ordinary non-fast-forward rejection remains the final race
protection. App-token pushes are expected to trigger workflows; the post-merge
canary has to confirm it.

Model-job credentials (Chaandan's review at `5f904c3`; Samir's at `acecb2b`):
the model jobs run no repository code and no gates. Model-authored Bash is
confined to one rule, `Bash(python3 ${{ runner.temp }}/claude-fixer-guard/model-api --:*)`,
next to Edit, Write, Read, Glob and Grep. A Claude Code `prefix:*` rule accepts
any trailing options, so the earlier `git log:*`, `git diff:*`, `git add:*`,
`git commit:*` and `ruff check app` rules could write or print arbitrary files
(`git log --output=`, `git diff --no-index`, `--pathspec-from-file=`, a
PR-controlled ruff `extend`). The helper instead runs git through fixed argv
lists with no model-supplied options, no shell, hooks, fsmonitor or pager:
`status` (`git status --short`), `diff`, `diff-stat`, `log` (`--oneline -20`),
`head` (`log -1 --stat`), and in the conflict job `conflicts`
(`diff --name-only --diff-filter=U`) and `merge-abort`. `commit MSGFILE` reads
the message from `.claude-fixer-replies/NAME.txt` with the comment-body checks,
stages every change except the sandbox's masks, then unstages
`.claude-fixer-out` and `.claude-fixer-replies` with `git reset`. Claude Code
binds `/dev/null` over protected paths that do not exist, such as `.bashrc`,
`.idea` or `.mcp.json`, and `git add` refuses those devices. An untracked path
that is not a regular file or symlink, or an empty top-level leftover of one of
those names, is skipped. A tracked path that became a device was deleted, so
the commit is refused before anything is staged, rather than dropping it. The
helper directories are not `:(exclude)` pathspecs: the model job lists them in
`.git/info/exclude`, and `git add` exits 1 when a pathspec names an ignored
path, which failed every commit in the second canary run (39569528483). Then
`commit` runs `git commit --no-verify -q -m <message>` (completing the merge in
the conflict job) and prints the full new `HEAD`. Its install step makes the helper
directory root-owned, with the directory and helper `0755` and its context and
token `0644`, and checks the owner, so Claude's commands (as the runner user) can
run and read it but cannot replace it. Lint and tests run in the required
`linux-gates` job after the push. Claude Code runs with `--setting-sources user`, so
`.claude/` settings and hooks from the PR checkout are ignored, and edits to
`.claude/**` and `~/.claude/**` and reads of `~/.claude/**` are denied. `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`
removes the OAuth token, the Actions runtime tokens and `GH_TOKEN` from every
command Claude runs, and bubblewrap (installed in a step that fails closed) keeps
those commands from reading the CLI's own environment. The queue helper's fixed
reads use the read-only job token from a file its trusted install step writes;
that token is read-only and revoked when the job ends. The action is given a
non-matching `allowed_non_write_users` value (`-no-such-user-`, which no GitHub
login can equal, so every actor still needs write access). With any value set,
the action backs git with a credential helper instead of writing the job token
into the origin URL in `.git/config`. Read can still open the helper's token
file, so the publisher refuses a candidate whose commit messages or new blobs
add a GitHub (`ghp_`/`gho_`/`ghs_`/`ghu_`/`ghr_`/`ghv_`, `github_pat_`) or
Anthropic (`sk-ant-`) token shape, or an added or modified path containing one, and an
outbox body that contains one. An encoded or split copy (base64, or a string
broken across lines) would pass that scan; the job token it could carry is read-only and
already revoked when the model job ends, before publish runs. The action's
credential helper and its exact-match permission bypass are its own behaviour,
so every `anthropics/claude-code-action` use is pinned to a full commit SHA
(v1.0.256, CSL-412). The fixer-failure escalation binds to that same reference,
and a bump must recheck both behaviours. Each model job's package step also fails the
package (`package_status=failed`, so publish refuses everything) when any git
config scope it can list holds a credential: a token or userinfo in a URL, an
`x-access-token`, a GitHub token shape or an authorization header. Every run
therefore checks that the action left the job token out of `.git/config`. Claude Code turns
`Read(...)`/`Edit(...)` deny rules into sandbox denies for Bash too, so the
model jobs deny writes only to the `.git` paths git can be made to execute or
redirect through (hooks, config, info, commondir, modules, worktrees), never
`.git/**` or reads of `.git/config` and the helper files, which git and the
helper themselves need. The helper's git ignores user and system git config,
external diff and textconv drivers and commit signing, and a conflict commit
is refused while conflict markers remain. Marker length follows each path's
`conflict-marker-size` attribute, read from the working tree, `HEAD` and
`MERGE_HEAD` with git's default of 7 always included. `<`, `|` and `>` runs of
the smallest size or longer, and `=` separators of exactly one of those sizes,
are refused, and so is an unmerged path with a `working-tree-encoding`
attribute, whose markers are not UTF-8 text. The scrub and
sandbox can only be proven by the post-merge canary: if they break the model
step, it does not succeed and publish publishes nothing.
The model job runs no repository code and holds no repository write credential;
the OAuth token stays in the Claude Code process, scrubbed from its commands.
With `--setting-sources user`, the repository's CLAUDE.md and `.claude/` rules are
not preloaded either; the prompt tells the model to Read them when it needs them.

## Terminal audit (CSL-201)

Historical (CSL-201, superseded by CSL-377): an embedded pre-push guard in the
model job consumed one push attempt and checked the advertised remote head. The
model then copied the full pushed SHA into its own receipt. Receipt parsing and
authentication are unchanged: model prose is not evidence of completion, and
the workflow audit never creates an addressed receipt.

The terminal audit reconciles actual PR review replies against the captured
per-root context using the workflow-owned copy of the existing receipt consumer.
It reports verified addressed receipts separately from human handoffs, missing
receipts, invalid/superseded receipts and changed roots. Full SHA, authenticated
Claude numeric identity, body fingerprint, captured base and remote ancestry are
required. A reachable push with an abbreviated receipt is reported as invalid;
the workflow does not repair or author that receipt. This is deterministic
reconciliation of observed output, not proof that a model will produce it.

Reconciliation is read-only and does not change eligibility, reservations,
resolution or READY. It checks at most 50 captured roots and five comment pages;
all terminal reads share a 40-call/70-second budget with a final live-head check.
Incomplete reads, duplicate snapshots, changed heads, denied access and exhausted
budgets report unverified. Mention runs without captured context say not
applicable. Results describe the observed context, not an atomic current-thread
decision; future runs still recheck resolved/outdated findings and later replies.

The publish job (`!cancelled()`) records a terminal audit after model failure, denied
commands, turn exhaustion or a missing artifact. It independently checks the
published candidate's remote ancestry and posts a PR notice. If the comment API fails,
the job summary retains the audit and the step fails rather than claiming a
posted handback. A 20-minute model-step timeout reserves room inside the 30-minute
model job; publish has its own 10-minute limit. The model limit remains 30 turns. Runner loss, whole-job timeout,
hard cancellation and unavailable GitHub can still prevent remote delivery;
this is not an external durable queue or an atomic writer lock.

Failure notices do not suppress findings or reset reservations. A fresh authorized
run must re-evaluate genuine unresolved/current findings, author permissions,
the three-attempt budget and eight-commit guard. Resolved or outdated findings
are never reopened for qualification. No persistent account access, credentials,
paid provider API or expanded model tool allowlist is introduced.

Acceptance gates still execute PR code in the model job. Since CSL-377 that job
holds only a read-only token and no persisted credential, so hostile code there
can corrupt its own artifact but not push or post; the separate publish job
validates the artifact and is the write boundary. Edit/Write denials reduce
accidental model edits but do not isolate shell subprocesses.

The handback is a PR-level notice to the author. It does not impersonate a Claude
thread reply or make the stuck-thread watcher classify a no-Claude thread as
stuck. Automatic mediation of workflow failures needs a separate actor-trust and
routing decision. The author must relay such blockers to Linear; transient
failures remain eligible for a fresh bounded retry rather than a needs-human
receipt. One audit is intentional even after model success, which alone cannot
prove every per-root receipt was valid.

## Human pause and bounded mentions (CSL-352)

Three writers can push to a PR branch: the Devin fixer and `@claude` mention
pipelines in `claude.yml`, and the conflict resolver (`conflict-scan.yml`, per PR
in `claude-conflict-writer.yml`). Since CSL-377 only their trusted publish jobs
push. All their jobs share the per-PR `claude-<PR>` lock and honor the same
pause. Label names match case-insensitively.

- **One PR:** add the label `claude:pause` (or the existing human-hold label
  `hold`). The fixer and mention jobs skip in their shared preflight, before any
  checkout, GraphQL read or attempt reservation, so the three-attempt budget is
  untouched. The conflict scan never selects a paused PR, and `conflict-resolve`
  rechecks the label under the lock before it merges or pushes. Remove the label
  to resume; the next Devin review, `@claude` mention, authorized manual
  `devin-review-fix` dispatch or conflict scan proceeds.
- **Every PR:** set the repository variable `CLAUDE_FIXER_PAUSE` to exactly
  `true` (Settings → Secrets and variables → Actions → Variables). Unset it, or
  set any other value, to resume. Runs read the variable when the job starts.
- **A writer run already past preflight:** the publish job rereads the live PR
  and refuses the push and every queued write if a pause label is present (or
  labels are unreadable) or the repository variable is set. It rechecks before
  each queued write too. The attempt stays consumed, nothing is retried, and the
  terminal audit records the refusal. A run that already pushed is not undone;
  the label stops later runs.
- **Mentions are bounded:** `@claude` skips once eight or more Claude commits
  follow the newest human commit, the same rule as the Devin-path loop cap.
  A commit is human only when neither its author nor its committer is Claude
  or another bot, so `git commit --author` cannot reset the count. A human
  commit resets the streak, except on a PR listing 250 or more commits
  (GitHub's limit for that endpoint), where a human has to take over. The skip
  is a run notice only.

A pause never resets budgets, receipts or the commit cap, and never resolves
threads. `do-not-merge` does not pause Claude; `needs-decision` stops only the
conflict scan, not the fixers.

## Mediation and implementation remain separate

Devin submitted reviews and authorized human requests drive Claude. Codex
READY requests CI only after live reconciliation; MEDIATE requests adjudication.
Neither is a fix receipt. Codex comments cannot impersonate Claude receipts or
consume Claude attempt reservations. Legacy FIX/timeout dispatch stays disabled.
The new tests retain these distinctions, alongside the existing permission,
same-repository, queue and readiness controls. CSL-89 owns resolution policy;
this change does not enable new thread-resolution authority.

Read-only #116 diagnosis (2026-10-16): run 38944245119 was a Codex event and
returned `mention=false`, `devin=false`; author NotSkittlz had admin permission.
Devin runs 38939609601 and 38936042436 reached preflight and skipped before
reservation/model execution. Their findings 4297383409 and 4297144162 have
`kind: analysis`, which the current automatic policy excludes. This establishes
intake scope, not evidence that mediation displaced or starved Claude.

## Operational evidence and landing

Historical cockpit #119/#142 pushes lost races to external writers; #142 run
38884600543 also exceeded its configured turn limit. On 2026-10-16, cockpit
#147 run 38944702649/job117120821057 successfully pushed
`d5194ed085712b884a58938d8877926abeed8f75`, parent
`ab7f04fc051800fb0200fbc9ce2d78447d3660b9`. Authenticated Claude reply
4297849933 nevertheless used `commit: d5194ed`, which the strict parser rejects.
That proves a genuine cockpit automatic push, not an accepted machine receipt
or qualification of this new guard, recursion, or contested-writer recovery.

This work uses isolated branches. Cockpit landing is held until #116, #141 and
#147 settle and merge (#153/#151 already merged). Refresh only this branch via
ordinary base merges and repeat checks then; no rebase or force-push. Recursion
has its own verified default base. Also reconcile any overlapping CSL-89 draft
(cockpit #126 / recursion #197) before landing. Never modify another owner's lane.

Sources: [Git pre-push contract](https://git-scm.com/docs/githooks#_pre_push),
[Actions timeout syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idstepstimeout-minutes),
[#116 Codex event](https://github.com/CSL-Laboratories/ai-investment-cockpit/actions/runs/38944245119),
[#147 receipt](https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/147#discussion_r4297849933).

## Independent subscription review follow-up

An independent, tool-disabled Claude Max source review found that failed model
steps masked execution-file reasons and Git remote names could collide with
the hook's maintenance modes. The audit now reads coarse result fields even
after step failure, prioritizes turn exhaustion over recovered denials, and
uses step failure only as a fallback. Install/terminal modes require exactly
one argument; a two-argument Git hook invocation always goes through target
validation. Read-budget and JSON decode failures remain globally unverified
instead of being mistaken for an observation-history change. Regression tests
cover these paths and the exact ordinary-push allowlist (including negative
force/no-verify/config/remote/dry-run controls).

Receipt reconciliation is a current snapshot and can include a valid receipt
that predates this run. It is not attribution of a fix to the current model
invocation. A missing guarded attempt remains unverified; neither local hook
configuration nor the shared runner identity is a hostile-code boundary.
This change does not alter checkout credential wiring or promise resistance
to code that changes hooks or uses the existing runner credential directly.

## Least-privilege API follow-up

The model no longer receives generic `gh api` access: raw API commands are
explicitly denied. The protected workflow helper exposes fixed current-PR
reads (including a fixed paginated thread-state query), issue comments and
review replies. It accepts no arbitrary endpoint, HTTP method, GraphQL query
or mutation, so ref create/update/delete cannot bypass the push guard through
that tool. Comment data comes only from regular `.claude-fixer-replies/NAME.txt`
files under the checkout; traversal, absolute paths and symlinks are rejected.
These files must not be committed. The helper uses the existing credential.

The push hook also validates Git's actual remote URL against this repository's
GitHub URL. Offline tests cover real delete records, empty input, tags, foreign
URLs, fixed API operations and forbidden ref mutations. The terminal step runs
after model success/failure but honors explicit workflow cancellation; runner
loss and cancellation remain delivery limitations. Existing same-user shell
execution is still outside the hook's isolation guarantee.

The URL check accepts the action's existing `x-access-token` HTTPS userinfo
after parsing and binding the actual GitHub host and repository, without logging
credentials. Other hosts/users, queries/fragments and ports remain rejected.
Malformed helper invocations fail without reading stdin or consuming a push.
Reply input opens nonblocking, verifies a regular descriptor and bounds its read;
named pipes and symlinks cannot stall the helper. These cases have offline tests,
including simulated action URL metadata through the real local Git hook fixture.

Remote transport syntax is restricted to canonical GitHub HTTPS/SSH forms.
The existing action token may contain only URL-unreserved characters; multiple
userinfo separators, encoded delimiters, empty ports and non-ASCII host lookalikes
are rejected without printing the URL. This defensive parser restriction is not
proof that a malformed URL previously reached another server.

Before posting a review reply, the helper independently reads the target comment
and requires its numeric ID, current PR URL and top-level root identity. Missing,
foreign, malformed or denied target reads produce no POST. This is local binding,
not a claim that a cross-PR GitHub write was demonstrated.

# Codex operating guide — AI Investment Cockpit

> **How CSL works:** start with the Linear doc [How CSL works](https://linear.app/csl-laboratories/document/how-csl-works-922e0df6026f).

This project is a three-founder, subscription-powered, human-gated investing
copilot. The three founders are Lucas (NotSkittlz), Chaandan (CDanTheMan), and
Samir (samir-shah-ahmed). It is paper-first: no live capital moves until the
pre-live gates in `docs/security-hardening-backlog.md` are satisfied.

The ratified [PRODUCT.md](PRODUCT.md) owns the autonomous paper-product target and
acceptance; [docs/PLAN.md](docs/PLAN.md) owns delivery order. The current human-gated
implementation and the boundaries below remain authoritative until their separately
scoped, reviewed implementation amendments. Product ratification grants no runtime
permission or empirical PASS.

For the replacement program, read
[SUCCESSOR-PRODUCT.md](docs/replacement/SUCCESSOR-PRODUCT.md),
[MONTH-PROGRAM.md](docs/replacement/MONTH-PROGRAM.md) and the exact acceptance
record in [program.json](docs/replacement/program.json). Their accepted bytes and
scoped gates remain controlling; these operating instructions do not amend them.

## Founder decisions and integration

The recorded [CSL-44 authority direction](https://linear.app/csl-laboratories/issue/CSL-44#comment-bbe42521-10fa-4ef0-b799-1c56fec9ddad)
allows any founder to decide product and implementation questions from research,
research-lane outcomes, feature admission within a lane and research continuation
within its approved budget.
Founders decide the consequential A1b core language/runtime commitment.
Reversible implementation choices remain developer discretion within the accepted envelope,
subject to SCOPE-AUDIT §5’s escalation triggers. This instruction selects no language or runtime.
Lucas retains spend/new services, sensitive company credentials, company-server
deployments and the final tie-break when founders disagree. All three founders
participate in the final release decision; Lucas has final say in any founder disagreement,
including a 2-1 split.

Founders merge after the required current-head reviews and checks; agents never merge.
A founder's own PR requires another founder's approval before merge, regardless of
ruleset enforcement. Non-author approval checks alone do not establish founder identity.
Protected-path amendments require a separately recorded founder-approved
authoring session; this paragraph grants no blanket editing permission.
Accepted contracts require their own versioned amendment, exact acceptance and reviewed
promotion. Until the ISSUE-CONTRACT amendment of 2026-10-25 is accepted and promoted,
only the recorded Lucas Linear digest receipt is exact acceptance. That amendment is
proposed, not accepted, in `CSL-Laboratories/csl-research` at commit
`d885c2de7228ab4e886b93165d30013e4c8bc5fd`, path
`contracts/ai-investment-cockpit/amendments/ISSUE-CONTRACT-2026-10-25.md`, SHA-256
`0b756e0777520f7b8a83d518b6b5887e96ed98eb8481d9d3840ddf4418c9b345`, via
https://github.com/CSL-Laboratories/csl-research/pull/58 (not merged). Once it is accepted and
promoted, a checker-pinned founder GitHub PR approval recorded in
`docs/replacement/program.json` will also count (approving founder login on the checker
allowlist `CDanTheMan` / `NotSkittlz` / `samir-shah-ahmed`, exact-case match, approver
other than the PR author, approved head SHA, exact file digest map, checker-pinned receipt
identity). Merge and non-founder review are not acceptance.
No person-level decision unlocks live capital or bypasses the nine invariants,
paper-only boundary or pre-live gates. Existing account, credential and runtime restrictions
remain in force; this wording does not activate R2a or grant a live run.

## Bounded issue execution and shared evidence

The loop starts only from objectives, feature admission and direction decisions approved by a founder acting within the founder decisions section above. Within them, agents run bounded autonomous research, implementation and tests, then close out with evidence. When blocked, an agent preserves its work, records the blocker and releases its execution claim, then moves to the next eligible issue. A human-decision hold needs an explicit decision receipt; a technical or dependency blocker needs evidence that its condition cleared. In either case, recompute readiness and take a fresh claim record (below) before resuming. Closeout records the exact tested head, commands and results, unresolved criteria, review evidence and linked follow-ups. Mark work complete only under its existing acceptance criteria; a merge or issue status is not empirical acceptance.

An issue is admitted only when it states its objective, non-goals, evidence dependencies, allowed effects and files, budget and stop condition, acceptance criteria and falsifiers, and verifier and rollback. Existing authorizations persist: routine, reversible work and fixes inside an admitted issue do not ask again, unless they touch the escalate list in [Proceed or escalate](#proceed-or-escalate), and do not force new paid research. Decision and direction issues still follow the Linear-first research requirement. A new consequential feature, architecture, risk, public-semantics, effect or budget question stops only the affected lane, with options and a recommendation posted on the Linear issue for founder decision under the founder decisions section above. Human decisions are never inferred from silence, elapsed time, a merge, CI results or issue status. When an agent relays a human decision, it labels the source and attribution and never invents a human receipt or an exact-digest acceptance.

Ownership is manual and human-coordinated. The Linear assignee stays the human owner. The human-designated ownership coordinator (not the CI candidate coordinator) serializes overlapping writers on Linear. An agent's execution claim is a CSL-357 claim record, not an assignment. This execution claim is coordination only: it does not allocate, replace or close a REGISTER package claim.

**Claim record.** Post one agent-prefixed comment on the issue and edit it in place afterwards:

```text
Agent (<tool>/<session id>, for <developer>): claim v1
claim: <random uuid>
scope: whole issue | sub-issue CSL-M
branch: <name> @ <head sha>, base <sha>
confirmed-at: <filled in after read-back>
heartbeat-at: <filled in on each renewal>
release-on: done | decision needed | handoff | lease expiry
```

Scope is exactly `whole issue` or `sub-issue CSL-M`, where CSL-M is a sub-issue of the claimed issue; split the issue rather than claim paths or several issues. Claim only an admitted issue that is next under the human-approved order, has no live claim and is not held. Each claim covers one issue in one repository; a session may hold several claims at once, but no two in this repository may cover the same issue (counting a `sub-issue CSL-M` scope) or name the same branch, and each follows these rules (Lucas's decisions, CSL-357 comments 0cdfd465 and 6749bdcb). Each agent still holds at most one execution lane (its line of work, such as a session's assigned lane); a lane can hold several claims. Agent commits made under a claim carry the trailer `Agent-Claim: <uuid>`; fixer, bot and human commits are exempt.

**Read-back.** After at least 60 seconds, re-read all comments on the issue, on its parent issue and on any sub-issue your scope covers, then search Linear for your exact branch name in quotes, including archived issues, and read every claim comment the results show. Your claim loses to an earlier live claim on the same issue, and to an earlier claim on another issue in this repository that overlaps it by scope or branch and has not been released, withdrawn, revoked or taken over. The earliest Linear `createdAt` wins; on an exact tie, the lower uuid wins. If any of these sources can't be read, don't confirm: read back again later or withdraw. Linear search can lag behind new comments and shows only issues your account can see, so a claim posted moments earlier can still be missed; the read-back reduces overlap but does not prevent it. If you won, set `confirmed-at`, then move the issue to **In Progress**. If you lost, edit the claim to `withdrawn: superseded by <uuid>` and stop. Make no write before `confirmed-at` exists. A write is a push, PR, GitHub label, thread resolve or Linear status change; posting or editing your own claim record is not one. Before every write, repeat these reads and read the remote head. Continue only if your claim is still the live winner and the head is the one you last saw or your own commit. Preserve uncertain in-flight work and stop on ambiguity.

**Lease.** Renew `heartbeat-at` at least hourly while working. The lease runs 2 hours from the claim comment's last Linear `updatedAt`; Linear's timestamps decide, not agent-written times. Once more than 2 hours have passed, make no write until you renew. A renewal up to 2 hours 30 minutes after that `updatedAt` (the lease plus 30 minutes grace) restores the lease; after that, never renew the claim. Before a pause of more than an hour, push work in progress to a draft PR or a branch without a PR.

**Session start or resume (CSL-379).** Before any write, check each claim you hold. If its lease plus grace has passed, release it with reason `lease expiry`; never renew it. Re-read the issue, then take a fresh claim. `heartbeat-at` is always the actual edit time from a fresh clock read, never a copied or estimated time.

**Takeover and re-claim.** Another agent's claim is replaced, by takeover or re-claim, only after an explicit release and the ownership coordinator's acknowledgement, posted unprefixed as `ACK TAKEOVER <old uuid>`. Lease expiry alone never lets another agent take over; the CSL-357 automatic takeover conditions do not apply in this repository. Once a claim's lease plus grace has passed, the coordinator may release an abandoned claim by posting an unprefixed `REVOKE CLAIM <uuid>`; a new claim that replaces it still needs `ACK TAKEOVER <old uuid>`. A different agent that claims an issue after another agent's claim on it was released or revoked is taking it over and needs `ACK TAKEOVER <old uuid>`, even though no claim is live. Only two claims are normal claims that need no acknowledgement: a first claim on an issue that no other agent has claimed, and an agent re-claiming when the most recent claim on the issue is its own released claim, such as its own lapsed claim. If any other agent claimed the issue after that, the re-claim needs `ACK TAKEOVER` against the most recent other agent's claim (Lucas's decision, CSL-357 comment 1c643648). A re-claim after a decision reads the decision, the last release record, the remote head and base, and open findings first; earlier claimants get no priority. Cross-repo: recursion allows takeover after lease plus grace; this cockpit forbids it without `ACK TAKEOVER`. An agent working in both repos follows each repo's own rule. This note changes neither rule.

**Release.** Release a claim only after its writers have stopped: nothing left to push under that claim and no job you started for it still running. Work under your other non-overlapping claims does not block the release. Edit the claim to `released`, then post an agent-prefixed `release v1` comment with `claim: <uuid>`, `reason: done | decision needed (CSL-M) | handoff | lease expiry | abandoned`, and `done`, `evidence`, `open`, `next` and `files` lines. Releasing never releases a hold or closes a finding.

This guide sets no numeric project WIP cap; any new cap is a separate founder decision. RW-33 swarm depth and leaf limits are unaffected. Claim records are conventions, not locks: a comment is not atomic, and a misbehaving agent is made auditable, not prevented. Atomic compare-and-set claims, machine-enforced leases, fencing, a transactional cost ledger and WIP enforcement are proposed and unimplemented; do not treat them as current machine guarantees. This section changes no platform state, status or REGISTER claim schema and does not rewrite REGISTER policy.

Shared research lives in the private repository [CSL-Laboratories/csl-research](https://github.com/CSL-Laboratories/csl-research). A research body produced under an admitted issue is published there as `submissions/<record_id>/` as part of that issue; no separate admission is needed. This guide grants no extra write route, access or spend. All research output (notes, briefs, findings, source tables, competitor or frontier writeups, research registers or indexes) goes to CSL-Laboratories/csl-research as `submissions/<record_id>/`, through a PR in that repo, with a digest on the Linear issue. Never commit research content to this repo; specs, amendments and code only link to the vault record (repo, 40-hex commit, path, claim, limitations). If csl-research is not accessible, stop and post a blocker on the Linear issue; do not fall back to this repo. Follow that repository's README, AGENTS.md, templates and `tools/check_submission.py`. Each run records its tool, its run id and its cost (or `unknown` plus a reason) in its csl-research record. The record also carries the sanitized report, its SHA-256, retrieval time, a source-to-claim mapping, and `issue_urls`. An exact evidence reference requires the repository, the full 40-hex commit, the record path, and the claim and its limitations. Cite the owning product's authenticated exact-digest human receipt when the consumed fragment requires acceptance; otherwise label the material candidate-only and cite its authorized research scope. A receipt identifier alone does not verify identity, digest, scope, conditions or revocation; the responsible reviewer checks those at the source. A bootstrap commit alone proves no research finding. Keep submissions, reviewed knowledge and human decision pointers separate; agents do not promote their own submissions. Every new non-public doc (research, specs, decisions, evidence and receipts, gate artifacts, and runbooks that are not public) goes to CSL-Laboratories/csl-research through a PR there; this repo only links to it. The only new docs allowed here are public knowledge-base, README, setup and implementation instructions for outside readers, plus agent instruction files. Gate and operational evidence that code or a runbook in this repo already reads stays in `docs/evidence/` until CSL-536. Contract fixtures under `docs/replacement/outcomes/**` stay. Existing files stay until CSL-536 moves them. `docs/research/**` is legacy (no new files).

Never commit secrets, private account details, raw chats, signed URLs, bulk copyrighted material or large dataset dumps to Git; reference approved storage through manifests only. An access denial stops the affected transfer; do not look for alternative routes. Existing approved budgets and subscription routes remain subject to their actual authority; this section activates no API fallback, overage or new spend. New research spend follows the founder decisions section above: Lucas retains spend and new services; any founder may decide research continuation within an already approved budget.

None of this amends accepted contracts, founder-reserved decisions, the nine invariants, the paper-only boundary, pre-live gates, current runtime, CI, fixer or review-loop roles. Agents still never merge.

## Proceed or escalate

The founder decision rules above still decide who decides. This section is the
short test for when an agent acts on its own and when it asks. Cursor, Codex and
Grok follow this section. Claude Code follows the `proceed-or-escalate` skill
(`.claude/skills/proceed-or-escalate/SKILL.md`), which carries the same gates and
expands on them; if the two ever differ, this section wins.

**Scope.** This governs agents acting through a developer's account or session:
interactive sessions, scheduled loops and Grok Bots. Repository workflows
(`claude.yml` fixer, Codex CI/fixer gate, stuck-thread-watch, linear-pr-sync,
conflict-scan, review-gate, the CSL-259 candidate coordinator and other repository
workflows) and Devin automations keep the roles in [Review roles](#review-roles) and
[Automations](#automations); their documented markers, receipts and audit comments
are unchanged. Founder-directed bot reviews under Review roles are that founder's
reviews and are not covered here; Review roles alone says what counts as one.

**Escalate wins.** If any escalate condition holds, escalate, even inside an
admitted issue and even when every proceed condition also holds.

**Proceed** only when all hold: the work is within an admitted issue's acceptance
criteria (routine, reversible work inside an admitted issue does not ask again,
unless it touches the escalate list below), or
is a clear defect or failing check with an evident cause on an issue you already
hold (Linear-first still requires a `CSL-N`); it stays inside the
issue's scope and its REGISTER package or `REGISTER: none` contract; no product
behavior, UX, public API, contract or data meaning changes beyond what the issue
specifies; it is reversible and code changes are covered by a test; it changes no
spend, credentials, permissions, settings, flags, modes or workflows; no other claim
or writer's run is active on the branch, files or package; and it starts no CI
beyond what a push to its own PR branch starts (below).

**Escalate** when any holds:

- a product detail, UX choice, scope or priority change is needed, or the request
  has more than one reasonable reading;
- it is an architecture, language, dependency or data-source choice;
- it touches the order path (risk, mode, approval, `OrderGateway`), `app/risk/`,
  `app/brokers/`, the audit chain (`app/storage/audit.py`), order eligibility or the
  allowlist, secrets handling, PnL (`app/quant/pnl.py`), costs
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
  alone;
- it touches governance files, decision records, contracts or another owner's work;
- a tool or platform refused the action (report it; do not route around it or turn
  one refusal into a standing rule);
- you would state a decision you cannot link to its record;
- it would dispatch, re-run or cancel a workflow, or add `ci:full` or `enhancement`,
  without a human asking for that run;
- an automation signal needs a human: the fixer is paused (its
  `claude-fixer-paused` or `claude-fixer-loop-cap` marker), a fix receipt cannot be
  verified, a branch with fixer receipts was rebased, or `ci:full` ownership is
  uncertain. Never remove a pause, dispatch the fixer around its budget, or report an
  unverified fix as fixed.

**CI an agent starts on its own.** Since CSL-408, every CI job runs on free
GitHub-hosted runners, so none of it is billed. The gate stays because every run
uses the shared hosted-runner queue. A push to your own PR branch, draft or not, is
fine: on a non-draft PR it runs `ci.yml` and `rust.yml` under their path filters
(plus Windows and macOS when the PR already has `ci:full`), so batch pushes. Mark a
PR ready once, after validation, with no owner hold. Dispatching, re-running or
cancelling any workflow (CI, the fixer, the Codex gate, the stuck-thread watch), or
adding `ci:full` or `enhancement` (which dispatch `ci.yml` and `rust.yml` through
`ci-label-retrigger.yml`), needs a human ask. Agents never push to `main`.
Read-only API queries are fine.

**How.** Ask on the work issue: one Linear comment @mentioning the issue owner with
the question, two or three options, your recommendation, and what you will do
meanwhile ("paused" or other proceed-list work, never the option you are asking
about or work that presupposes one). A stuck review thread instead goes in the
PR's roll-up, found only by its `<!-- stuck-pr-threads:owner/repo#N -->` marker,
in your own block; product and architecture questions stay on the work issue.
For a decision or direction issue you are assigned or have claimed, the research requirement in
[Linear-first workflow](#linear-first-workflow) still comes first. A Grok Bot coordinator may
research and recommend on a decision or direction issue. Attribute the brief explicitly as
`Grok Bot, preparing research for <owner>`; never phrase it as if the owner personally
authored or endorsed the recommendation. Only the designated human decision-maker, or an
explicitly authorized research team, decides. Publishing a brief never marks the issue
decided, never clears a hold, and never authorizes implementation. Then release your claim,
if you hold one, with a release record and take only other admitted work that is next under
the human-approved order.

**Comment labels.** Agents usually post through a developer's account, so start
every agent comment with `Agent (<tool/session>, for <developer>):`; it carries no
authority. A **developer-approved comment** counts as that developer's personal
receipt (Lucas's decision, CSL-353 comment 6e602e3a; in force in this repository from
the merge of this section, CSL-354 comment f5bcb490) only when all hold:

1. immediately before posting, the agent showed the developer the final exact text
   and the developer approved it in the same session; approval from an earlier turn
   or another context does not carry over;
2. the comment carries the line
   `developer-approved: <developer>, exact text confirmed <UTC time>, session <id>`;
3. it has not been edited since it was posted (any edit voids it);
4. it stays within that developer's own authority and grants none;
5. no accepted contract requires an exact authenticated human receipt for it
   (`docs/replacement/program.json`, R2a, recursion program receipts and other
   accepted contracts keep their own receipt rules unless amended). GitHub review
   approvals follow Review roles and the founder approval rule above.

Never post decision language as an agent comment without that marker; link the
record instead. A decision exists only if a human posted it from their own account,
a developer-approved comment meeting the conditions above carries it, or it is
recorded in `docs/adr/` or a linked Linear decision issue. When relying on a
developer-approved comment, check that the marker is present, the comment is
unedited and the named developer has not disputed it; if any of that is unclear,
ask that developer before acting. A comment from a founder or developer account
that has no agent prefix and was posted before this rule took effect (the merge of
#217, 2026-10-21T03:50:42Z) counts as that person's decision only if they confirm
it when asked (Lucas's decision, CSL-354 comment 0a86b61c). Agent repetition,
silence, a merge or an issue auto-closing to Done is not a decision.

**Unattended runs** (loops acting through a developer's account while nobody
watches) may only do "proceed" work. They do not resolve human-authored threads,
change scope, close issues as decided or post developer-approved comments (nobody
is there to approve the text). They never mark ready, add or remove GitHub labels,
dispatch, re-run or cancel, and they label every comment
`Agent (unattended run, for <developer>):`. Founder-directed bot reviews under
Review roles are not covered here.

## Codex boundary

- Codex and Claude may perform schema-gated analysis jobs (briefing, triage,
  grounding, proposal drafting, and fresh interrogation) in `read_only` and
  human-approved `paper` mode.
- Do not put either model runtime or an MCP server inside `app/risk/`, give a
  model broker credentials/tools, or add a direct route from a model to
  `OrderGateway`.
- Codex decision jobs use the hardened profile in `app/agent_runner/runner.py`:
  prompt over standard input, read-only sandbox, ephemeral session, ignored
  user config/rules, and shell/web/apps/browser/computer/MCP capabilities
  disabled. The actual argv is inspected before spawn.
- `review_live`, `auto_veto`, and `auto_full` remain outside the Codex decision
  boundary. Do not relax that pin without a separately scoped, adversarially
  reviewed safety redesign and the pre-live gates.

## Cursor Cloud research/development boundary (amended 2026-09-14)

RW-33 replaces the retired Devin/SWE-2 dispatch policy. Head is the Cursor
Cloud Agent on its Linux VM; Cursor-native fresh sessions are preferred for
research, REGISTER-claimed development and adversarial review. Codex and
OpenCode are named for these bounded development roles off the order path.
Naming and installation alone do not qualify a CLI or grant production authority.

- No new Devin swarm-dispatch jobs in this cockpit. Keep `spawn_devin_leaf.py` frozen for historical audit. Devin may still post research briefs, which, like all research, land in csl-research.
- No swarm changes to risk, OrderGateway, mode/approval, audit, eligibility or
  secrets; no broker tools/credentials, soaks or auto rungs. Current production
  Codex decision jobs still use the unchanged hardened runner above.
- Maximum depth: Head → middle → leaf; at most eight leaves per middle within
  an admitted aggregate budget. Fresh attempts; no bulk inherit or leaf spawning.
- Verify model/version/capabilities on the actual Cloud host. Unknown profile,
  missing reservation, deadline or disclosure admission refuses dispatch. No
  auto/force/trust/permission bypass or automatic MCP approval. The scoped helper
  is preview-only and cannot launch any CLI until a separately claimed adapter
  is implemented and qualified; do not add an override to skip qualification.
- Muse is the preferred second-gate synthesizer only when a verified different
  family from producers/reviewers. Preserve dissent. OpenRouter/Hive are fallback
  synthesis only, under enforceable aggregate ceilings of $5/$50; no automatic
  resets. ChatGPT is manual steering only. Chiron/Recursion remain future PD-029
  integrations, not a prerequisite or a current routing destination.
- Head prepares reviewable changes and draft PR material. Existing operator
  commit/publication instructions and the founder integration rules above apply. Swarm tests
  and receipts never satisfy empirical, paper-day or pre-live gates.

Historical Cloud process (legacy, frozen): `docs/research/agent-swarm-rebase-2026-09-14.md`.
Historical Cloud start (legacy, frozen): `docs/research/cursor-cloud-rdt-handoff-2026-09-14.md`.
Current research landing: CSL-Laboratories/csl-research `submissions/<record_id>/` (see [Research](#research)).
Market/paper soaks and DEC-09 operator cells stay on their designated hosts.

## Invariants

**`CLAUDE.md` holds the canonical list of 9 invariants — read it there.** This file
used to restate a shortened, differently-numbered set of 6, so "Inv 4" in a code
comment resolved to *secrets* against one file and *`risk/` purity* against the
other. Code comments here cite invariants constantly, so one numbering is the only
workable arrangement. The summary below is a pointer, not a second source:

Orders only via risk → mode → approval → `OrderGateway` (Inv 1); the decision path
is pinned to Claude in auto modes (Inv 2); every model output is schema-validated
(Inv 3); secrets live only in the OS credential store (Inv 4); `app/risk/` is pure
and deterministic (Inv 5); the human gate is client-side and hash-chain audited
(Inv 6); vendor limit / stale feed / hung orchestrator fails closed to `watch_only`
(Inv 7); raw inputs are stored separately from summaries (Inv 8); post-PDT risk
model (Inv 9).

Note on Inv 4: the store is the **OS credential store** — Windows Credential
Manager, macOS Keychain, or Linux SecretService. `core/secrets.py` calls the
generic `keyring` API with no platform branch, so it is already portable; only the
wording was Windows-only. Do not add a platform branch to satisfy the old phrasing.
On headless Linux there is no SecretService by default: the test suite installs an
in-memory backend (`app/tests/conftest.py`), but a real Ubuntu deployment needs one
provisioned.

## Working conventions

1. Read `docs/README.md` and the relevant plan before editing.
2. Before proposing architecture or direction, read `docs/adr/` and the linked
   decision issues; do not re-argue a recorded decision without new evidence.
3. Keep changes to one work package, or one admitted process issue with `REGISTER: none`, and add tests with the change.
4. Use the project virtual environment. Create it once with `python -m venv .venv`
   from the repo root — note the `py` launcher is not present on every dev box —
   then install the full runtime and validation dependency set CI installs (see
   `.github/workflows/ci.yml` and the command in `CLAUDE.md`), including `discord.py`.
   Every matrix cell must run the real offline Discord command-tree tests; the absent
   dependency fallback is covered separately in an isolated subprocess.
   Run the gates from `app/`:
   - Windows: `..\.venv\Scripts\python.exe -m pytest -q` and `..\.venv\Scripts\ruff.exe check .`
   - Ubuntu / WSL2: `../.venv/bin/python -m pytest -q` and `../.venv/bin/ruff check .`

   On Windows, point `TMP`/`TEMP` at a SHORT path (e.g. `C:\t`) first: pytest's
   nested tmp dirs plus this repo's deep fixture paths cross MAX_PATH and produce
   ~80 spurious `WinError 206` failures that are not real defects. Also clear any
   ambient `COCKPIT_*` vars — real operator config in the shell fails at least one
   test that asserts an unconfigured default.
5. Keep the repository private and commit only when the operator asks.
6. **CI etiquette (CSL-255):** open PRs as drafts; batch pushes (one push per
   iteration when possible); mark ready **once**, after validation and only when
   no owner hold remains. Never merge a PR, rebase, or force-push. Avoid routine
   base merges; a normal base merge is allowed for a real conflict or an
   owner-approved dependency integration even without a text conflict. Record
   the reason and rerun the combined gates. Never edit
   `.github/workflows/**` unless a Linear issue explicitly assigned by a human
   asks for it; bots mark a PR ready with their own identity, never
   `GITHUB_TOKEN`. A `ci:full` added by a person or Devin persists, so every
   later qualifying push reruns Windows and macOS, plus the Rust Windows and
   Rust macOS cells when the Rust path list matches (CSL-385 G8). Remove it once
   its full-matrix run has passed on the current head and the PR needs no
   further automatic READY. Removing it keeps the Linux pytest cells, including
   order-path, `risk/` and PnL tests, on every push that is not docs-only once
   the PR is ready for review (drafts run no test matrix); Windows, macOS and
   the Rust Windows and macOS cells wait until the label is back or a full
   dispatch runs. A manual removal is a human hold: the Codex gate skips READY
   on the head where it first sees the removal, and with the candidate
   coordinator on, READY delivery, candidate runs and `CODEX-GATE: MEDIATE`
   human handoffs stay blocked on every later head until someone re-adds
   `ci:full`. Re-add it before the PR needs READY, a candidate run or a MEDIATE
   handoff. Codex READY's own label is cleared automatically on the next push,
   after that push's CI has already read it, so one more Windows and macOS run
   can start first.
7. Any order-path change needs an adversarial-review round.
8. The release-blocking local matrix is **native Windows plus Ubuntu under WSL2**, each on
   Python 3.11 and 3.12. Run all four cells on the operator's Windows workstation. Hosted
   Linux (`linux-gates`) is a required PR diagnostic and does not replace that matrix;
   hosted Windows/macOS are not ordinary-PR checks; Hetzner is not a release gate. macOS is a
   best-effort portability target, not a release gate. Continue to write platform-neutral
   code: review subprocess spawn, path construction, credential lookup, and temp-file use,
   and prefer inspecting the failure (as `orchestrator/halt.py` does) over branching on
   `os.name`. See `DEC-09` in `docs/REGISTER.md`.

## Research

Every issue and every execution step is grounded in research before the work.
This applies to decision issues and implementation alike. Size the research to
the task. No specific tool is required, and there is no separate mandatory
research pipeline. Research may use any deep-research source the team has,
such as Parallel, Exa, Claude Deep Research, Open Deep Research,
ChatGPT Deep Research or the web. Current library docs (for example Context7),
primary sources and repo evidence remain available; none of these is required.

Prefer already-paid subscriptions. Keep the standing no-pay-per-token-API rule:
only already-paid subscriptions or existing allowances are used. Metered tools
such as Exa and Parallel stay within their existing per-person ceilings. No new
pay-per-token spend. This section adds no spend ceiling. Scheduled routines
never start metered or paid research on their own.

Record which source or tool produced each piece of evidence, with the date and
the key sources, so the research can be checked. New non-public docs, including
research bodies, go to csl-research as in
[Bounded issue execution and shared evidence](#bounded-issue-execution-and-shared-evidence).
Existing files stay until CSL-536.

## Engineering stack (Oct 2026)

- **Parallel Search MCP** — available web search and fetch at https://search.parallel.ai/mcp (no key); not a required research route.
- **Context7 MCP** — up-to-date library docs at https://mcp.context7.com/mcp (works without a key).
- **Exa MCP** — available research source at https://mcp.exa.ai/mcp. Declared in `.cursor/mcp.json` and `.mcp.json`. The key goes in the `x-api-key` header from env var `EXA_API_KEY` (Cursor `${env:EXA_API_KEY}`, Claude Code `${EXA_API_KEY:-}`, so a missing key falls back to Exa's free rate-limited mode instead of breaking the file) and lives only in each environment's secret settings, never in tracked files, prompts, chat or logs. Capped at $50 per person per month (Lucas's decision, CSL-378 comment ff7227ac). Start a billable run only when it keeps you under the cap, and record each run's cost in its research record. Keep queries generic, with no secrets, proprietary code or private data.
- **Linear MCP** — issues, comments, and status at https://mcp.linear.app/mcp (or the Devin Linear integration).
- **Linear** — source of truth for work. Workspace **CSL laboratories**, team key **CSL**, project **ai-investment-cockpit**.
- **GitHub** — org **CSL-Laboratories**.
- **Devin** — the only PR reviewer, review only (on PR open; on-demand via `/devin review`).
- **Claude Code Action** — PR fixer for Devin findings and `@claude` mentions on open, same-repository PRs whose author (and, for `@claude`, the commenter) has write, maintain or admin permission on the repo (other PRs are skipped by the workflow's event gate) (`.github/workflows/claude.yml`).
- **GitHub-hosted runners** — all CI, free because this repo is public. The test matrix (Python linux/windows, rust-book) uses `ubuntu-24.04` and `windows-2025`; non-test jobs use `ubuntu-latest`; macOS uses `macos-latest`. CSL-408 moved the test matrix off Blacksmith.
- **Google Workspace** (`csl-laboratories.com`) — meetings and notes.
- **Discord bot** (`csl-bot-operations`) — slash commands and MCP below.

## Discord bot (`csl-bot-operations`)

Liaison's stable slash commands — there is no top-level `/review`, and `/ci` always takes an argument:

- `/issue`, `/plan`, `/debug`, `/feature`, `/standup`
- `/ci full|run`
- `/devin review pr:<url>|run|status`
- `/telemetry`, `/help`, `/connect`

MCP: https://csl-discord-bot-production.up.railway.app/mcp (declared in `.cursor/mcp.json` and `.mcp.json`). Each dev mints a bearer token via `/connect`. Never commit the token; set `CSL_DISCORD_BOT_MCP_TOKEN`. Cursor interpolates `${env:CSL_DISCORD_BOT_MCP_TOKEN}`; Claude Code interpolates `${CSL_DISCORD_BOT_MCP_TOKEN}`. Tools: `issue_create`, `issue_list`, `issue_status`, `issue_assign`, `plan`, `debug`, `feature`, `standup`, `ci_full`, `ci_run`, `devin_review`, `devin_run`, `devin_status`, `telemetry`.

Agents trigger CI and reviews via these commands/tools or the `ci:full` label.

## Linear-first workflow

Agents must stay Linear-aware and proactive. Linear is the source of truth for execution: the assignee (the human owner), claim records, status and discussion. `docs/REGISTER.md` remains the package record: it allocates package IDs and holds package claims, scope and completion contracts. These are separate records, and neither replaces the other. An agent's execution claim is its Linear claim record; neither it nor the assignment allocates, replaces or closes a REGISTER package claim, and a runtime `claim_id` remains the existing REGISTER package ID. Process work with no package says so and does not mint an ID. Cycle decisions stay with [CSL-8](https://linear.app/csl-laboratories/issue/CSL-8); this section sets no cycle. Admission, blocker, claim-release, resume and evidence rules are in [Bounded issue execution and shared evidence](#bounded-issue-execution-and-shared-evidence). Releasing a blocked execution claim does not release the hold. After a blocker, take only a separately admitted issue that is next under an explicit human-approved order; if no such order exists or it is ambiguous, propose the next choice on Linear and wait.

- **Before starting work**, find or create the Linear issue (team **CSL**, project **ai-investment-cockpit**) via the Linear MCP or the Devin Linear integration. Do not start a change without a `CSL-N`. Ground the issue and each execution step in research first; size it to the task (see [Research](#research)).
- **When you are the assignee or the claimant** (developer or their coding agent working in the repo) of an issue that needs a decision or sets direction, post a cited brief (options, tradeoffs, recommendation, target metrics) on the Linear issue after the research that grounds the work. A Grok Bot coordinator may research and recommend on that issue. Attribute the brief explicitly as `Grok Bot, preparing research for <owner>`; never phrase it as if the owner personally authored or endorsed the recommendation. Only the designated human decision-maker, or an explicitly authorized research team, decides. Publishing a brief never marks the issue decided, never clears a hold, and never authorizes implementation. The agent @mentions the decision-maker for the decision.
- Branch names come from Linear's copied branch name, so the PR auto-links. PR title includes `CSL-N`. One issue per PR; keep PRs small and stackable.
- In the PR body, write `Related to CSL-N` by default; use a closing keyword (`Fixes CSL-N`) only when this PR completes the issue's acceptance criteria. Name the REGISTER package ID, or `none` for process work. Linear automation moving an issue to **Done**, a merge, or an AI PASS is not acceptance evidence.
- **Formal review requests.** Every review ask needs both (a) an @mention on the Linear issue (`samirshahahmed`, `chaandansaini101`) and (b) a formal GitHub review request: `gh pr edit <n> --add-reviewer samir-shah-ahmed,CDanTheMan` (whichever apply). This is mandatory for agents too.
- Comment progress on the issue. Move it to **In Progress** once your claim record is confirmed, then **In Review** when the PR is up. A hosted agent without Linear access reports its blocker on the PR to the PR author through its existing channel; the author relays it to the current Linear owner. The hosted agent does not invent API access, and this guide changes no workflow prompts.
- Grok Bots must read the Linear PR-sync comment (`<!-- pr-sync:<repo>#<n> -->`, written by [`.github/workflows/linear-pr-sync.yml`](.github/workflows/linear-pr-sync.yml)) on the issue before touching shared files. Three people work this repo at once; that comment is the live PR link, mergeable/conflict state, head SHA, and top files.
- When work reveals follow-ups, bugs, or next steps, create new **Triage** issues with one Type label, the repo label `ai-investment-cockpit`, the project, and a link back to the parent issue or PR — not drive-by changes.
- **Assignees:** the three founders are Lucas (NotSkittlz), Chaandan (CDanTheMan), and Samir (samir-shah-ahmed). All are admins, and ownership changes over time, so never hardcode owners. Check the current assignee or project lead in Linear. Assign new issues to the owner of the parent issue, or leave them in Triage unassigned when unclear.
- The author addresses CI comments on their own PR, then resolves the threads. Devin findings and `@claude` mentions are handled by the Claude Code Action (`.github/workflows/claude.yml`) on open, same-repository PRs by repo writers; on other PRs the author addresses them. Do not run review-fix loops on other agents' PRs.
- Humans make the load-bearing decisions (architecture, security, trading-risk, merge order). Agents propose; humans decide.

## Hosted CI

Hosted split (PR #102):

- Required check: **`linux-gates`**. The aggregator is GitHub-hosted `ubuntu-latest`. The Python 3.11+3.12 matrix it wraps runs on GitHub-hosted `ubuntu-24.04`. Ordinary label add/remove does not start `ci.yml`, so it cannot post a skipped or successful `linux-gates`. Drafts skip the test matrix; the aggregator still runs and fails closed until `ready_for_review`. `ci:full` / `enhancement` re-trigger via `ci-label-retrigger.yml`, which has no job named `linux-gates`.
- Enhancement-only label reruns request Linux scope; `ci:full` or enhancement on an already-`ci:full` PR requests the full matrix. Manual/API dispatch defaults to full; explicit `scope=linux` requests Linux only.
- Label reruns carry the PR number, expected head, and immutable merge candidate. They validate that context before testing the merge candidate. Their concurrency suffix is `label-<expected-head>-<scope>`, separate from ordinary PR runs, because `gh workflow run --ref` resolves to the current head and a stale dispatch must not cancel that head's PR or label run. Scope isolation prevents a delayed Linux-only request from cancelling a full request on the same head. The label dispatcher also isolates heads. Filter still fail-closes stale dispatch context before billed matrix work; manual branch dispatch without PR context remains available in its own scope group.
- Windows runs on push to `main`, `merge_group`, the `ci:full` label, and full manual dispatch (GitHub-hosted `windows-2025`). Main-push Linux/Windows and relevant Rust coverage stays enabled until the owner separately approves removal after queue enforcement is proven.
- macOS runs nightly at 07:17 UTC on `main` (skipped if idle 24h), on the `ci:full` label, and on full manual dispatch — not on ordinary `main` pushes (GitHub-hosted).
- Non-test jobs (filter, `linux-gates` aggregator, pr-lint, Claude fixer/conflict-scan) use GitHub-hosted `ubuntu-latest`.
- Ordinary PR runs share a queue with automatic cancellation disabled. The trusted default-branch companion requests cancellation only for verified strict-ancestor CI/Rust runs after checking a real current-head successor; bootstrap and uncertain evidence leave runs queued. Draft synchronize uses a separate group from `ready_for_review`. See [controller policy and limits](docs/ci-cancellation-controller.md).
- Non-draft synchronize events wait in `settle` for 120 seconds. Paid jobs and required aggregates accept successful settle or an event's intentional skip; failure, cancellation, and unexpected skips block. The wait consumes hosted capacity and does not guarantee savings.
- CI caches pip downloads, never virtualenvs. Each cell creates a fresh environment, installs direct pins, and runs `pip check`; one fresh no-cache retry handles install/cache failure. Direct pins are not a transitive hash lock.
- Queue protection is staged: proposed stable checks are `linux-gates`, `queue-gates`, `rust-gates`, and `review-gate`. The latter verifies the current genuine Devin bot commit status on every exact required PR head, with bounded polling and manual same-head recovery. Existing required `linux-gates` and `Devin Review` remain unchanged. See [the rollout and owner decisions](docs/ci-queue-rollout.md); synthetic tests do not establish live queue enforcement.
- A rate-limited `CODEX_TRIGGER_PAT` does not fail the stuck-thread watch workflow run. The watcher reads with `github.token` and uses the PAT only for writes; a failed write is logged as `pr=<N> thread=<id> mediate=error` (or `pr=<N> label=error` when labelling is on) in a run that still passes. Inside the script, only a failure to list pull requests (which a `github.token` rate limit can cause) or a missing `GITHUB_REPOSITORY` returns non-zero; the job can also fail at checkout or on its 10-minute timeout. So read the log before treating a failed run as a code problem. The required checks (`linux-gates`, Devin Review) decide CI either way (Lucas's decision on CSL-390, comment 242120cc).

## Review roles

- **Devin** is the GitHub PR code reviewer (findings). It does not auto-fix.
- **Claude Code Action** (`.github/workflows/claude.yml`) is the fixer for Devin findings and `@claude` mentions on open, same-repository PRs whose author (and, for `@claude`, the commenter) has write, maintain or admin permission on the repo (other PRs are skipped by the workflow's event gate).
- **Codex** has two allowed GitHub review paths, neither a Devin substitute:
  1. **CI/fixer gate** (CSL-103/259, `.github/workflows/codex-gate.yml`) - one nonce-bound `@codex review` per exact head/base/merge/tree candidate and fixer generation; the reply echoes that identity and ends with `CODEX-GATE: READY|MEDIATE`. See Code Review Rules.
  2. **Stuck-thread watch** (CSL-86 / PR #124, `.github/workflows/stuck-thread-watch.yml`) — one advisory `@codex review` on unresolved Devin threads Claude could not settle, and **one** Linear `needs-decision` Triage roll-up per PR titled `Stuck PR threads: <repo>#<PR>`. Shared keys with the Codex CI/fixer gate: `<!-- stuck-pr-threads:owner/repo#N -->` and `<!-- codex-gate-mediate:owner/repo:N -->`; each writer owns only its fenced block. It adopts an existing roll-up only by that `stuck-pr-threads` HTML marker, never by title, and writes Linear only when at least one thread is actionable after the per-PR cap and `CODEX_TRIGGER_PAT` checks (or to create a missing roll-up after an earlier Linear outage). It skips already-approved PRs and updates that roll-up instead of filing a new issue per thread. That reply must **not** include a `CODEX-GATE:` line, and must not push, merge, resolve the thread, or open a fix PR.
- **GitHub-hosted runners** host the test matrix. That is the GitHub test-CI path, not a revival of this cockpit's retired Devin/SWE-2 swarm dispatch route: "No new Devin jobs" / `spawn_devin_leaf.py` frozen (RW-33) and CLAUDE.md's "Devin is retired from this cockpit's research/dev swarm dispatch" still apply to cockpit research/dev swarm jobs. Devin may still post research briefs, which, like all research, land in csl-research.
- Cursor, Codex (as a coding agent), Grok, and interactive Claude Code develop features from Linear issues. They must not post review verdicts or run review-fix loops on other agents' PRs, except the stuck-thread watcher. A founder may have their own bot post reviews under their account when they direct it. The author agent may fix CI failures on its own PR.

## Review loop

Devin reviews → Claude fixes (`claude.yml`) → the Codex gate (CSL-103) oversees
candidate full CI / human handoff only. Devin findings
trigger Claude directly without waiting for Codex. All Claude jobs share one
per-PR lock with GitHub `queue: max` (one active, up to 100 pending; excess
requests can still be dropped). The automated Devin fixer stops at eight consecutive Claude
commits. Stuck threads are also picked up by
[`.github/workflows/stuck-thread-watch.yml`](.github/workflows/stuck-thread-watch.yml)
(CSL-86 / PR #124), which asks Codex to mediate (`@codex review`, no
`CODEX-GATE:` line) and records one Linear Triage `needs-decision` roll-up per
unapproved PR (`Stuck PR threads: <repo>#<PR>`), updated in place. Adopt an
existing roll-up only by the `stuck-pr-threads` HTML marker, never by title.
Write Linear only when at least one thread is actionable after the per-PR cap
and `CODEX_TRIGGER_PAT` checks, or to create a missing roll-up after an earlier
Linear outage. Humans decide.
The watcher never resolves threads, pushes, or merges.

## Code Review Rules

These rules apply to the **CI/fixer gate** `@codex review`. For READY, echo
`CODEX-CANDIDATE` and `CODEX-REQUEST` exactly outside quotes or code fences and
review the named head, base, merge commit and tree. When that candidate is ready for full CI or the current head needs a
human decision, **end the review body with exactly one applicable verdict
line**. When fixes are still pending, leave advisory findings without a verdict:

    CODEX-GATE: READY
    CODEX-GATE: MEDIATE

- Legacy `CODEX-GATE: FIX` verdicts are ignored. Human-only findings need a human `@claude` mention.
  Devin submitted reviews trigger fixes directly; Codex never dispatches the fixer.
- `READY` - findings are settled or none remain; the locked coordinator requests
  both full matrices for the exact candidate, before human approval. Do not add
  `ci:full`; persistent labels and legacy head-only replies are not qualification.
- `MEDIATE` — Devin and Claude disagree or a thread needs a human. File or
  update the shared Linear `needs-decision` Triage issue per PR, titled
  `Stuck PR threads: <repo>#<PR>` (key `<!-- stuck-pr-threads:owner/repo#N -->`).
  A genuine current-head, unquoted terminal MEDIATE does not require a mergeable
  synthetic candidate or candidate nonce; it grants human handoff only, never CI.
  Skip if the PR is already approved. Do not wait for stuck-thread-watch (it
  only flags a needs-human phrase or a 6-hour-old Claude reply).

If fixes remain, leave advisory findings without a gate verdict. READY and
MEDIATE are the only actionable verdicts; missing verdicts never time out
into fixer dispatch. Recovery scans retry missing requests and those two
verdicts only. There is no daily fixer dispatch cap. Locked live-head and
unresolved-finding checks skip obsolete queued events; failed runs remain
retryable through a new Devin submitted review or authorized manual dispatch.

If this `@codex review` is a stuck-thread mediation (the comment contains
`<!-- codex-mediation:`), do **not** add a `CODEX-GATE:` line. The gate
ignores those reviews and never dispatches a fixer from them.

## Automations

Devin automations (**Main CI failure fixer** and **Weekly dead-code sweep**, Wednesdays 10 AM PT) are human-managed. Other agents must not create, edit, or trigger Devin automations, and must not edit Devin settings.

## Post-merge flow

- A CI failure on `main` triggers the Devin fixer, which opens a Linear **Bug** (run id in the title) and a fix PR. The Discord bot posts the failure to the alerts channel.
- The weekly dead-code sweep opens a Linear **Chore** plus a cleanup PR.
- The Discord bot sends a weekly digest of stale PRs and issues.

## Security

- Never commit secrets. GitHub secret scanning and push protection are on: a blocked push means remove the secret and rotate it. Never bypass.
- Dependabot security updates are on. Its PRs follow the normal review flow.

## Trading safety

The engine runs in **paper trading** by default. Live trading requires an explicit human-controlled switch. Agents must never enable live trading or add live broker credentials.

## Performance

Flag speed regressions. Pull requests run a non-blocking `benchmarks` job
(`.github/workflows/ci.yml`, CSL-53) that times the order-path and model-output
hot paths against the default-branch tree in the same job and warns when the
median slowdown, after sample spread, is above 10%. It does not fail
`linux-gates`. Still note perf impact in the PR description for engine changes.

## Guardrails (MUST)

Agents must **not** edit CI/runner configuration (`.github/workflows/**`, runner labels, caching, concurrency), Devin configuration, repo rulesets, or the MCP server list, unless a Linear issue explicitly assigned by a human asks for it.

Open PRs as drafts, batch pushes, and mark ready once after validation and only when no owner hold remains. Never merge PRs, rebase, or force-push. Normal base merges are limited to real conflicts or owner-approved dependency integration (including integration without a text conflict); record the reason and rerun the combined gates. Bots convert draft → ready with their own App identity, not `GITHUB_TOKEN`.

Trigger CI and reviews through the Discord bot (`/ci full|run`, `/devin review pr:<url>|run|status`, or MCP `ci_full` / `ci_run` / `devin_review` / `devin_run` / `devin_status`) or the `ci:full` label — not by editing config.

Do not add paid services or raise spend limits.

### Fixer receipts and bounded no-progress retries (CSL-201)

Automatic eligibility requires explicit Devin `kind: bug` metadata. Trusted Claude
structured per-root receipts bind the original body fingerprint to a validated
base and pushed commit; addressed receipts suppress repeats only when that commit
is strictly newer than the base and remains an ancestor of the live PR head.
Explicit `needs-human` receipts leave the thread unresolved for human mediation.
New meaningful Devin or repository-writer replies rearm it; arbitrary prose,
failed tests, tool/API errors, and no-push runs never prove completion.
Since CSL-377 the model job cannot push or post: it commits locally and queues
writes, and the trusted publish job validates the commits, pushes once and
authors the receipts itself as `claude[bot]` (see `docs/claude-fixer-reliability.md`).

Under the shared PR lock, at most three automatic attempt reservations are allowed
for the same head and same meaningful pending-findings fingerprint. A new head or
changed findings resets this budget; authorized manual `devin-review-fix` dispatch
can retry while the eight-commit guard remains. There is no daily cap. Reservations
can include workflow-validation stops before model execution, so after activation
an authorized manual retry may be needed. A paused attempt budget is never READY.

Receipt parsing accepts inline or indented machine markers; quoted receipts do not
hide a repository-writer correction. Root findings must also match the REST
numeric Devin bot identity. Invalid compare SHAs (HTTP 404/422) leave findings
pending; authentication, transport, and server errors still fail closed. The
eight-commit count is applied only after receipt-aware selection proves pending
work remains, so a clean PR does not receive a misleading loop-cap pause.

READY is reconciled against live unresolved bug findings and exact candidate
identity before delivery and qualification, including explicit human handoffs.
Verified addressed receipts count as handled. Historical READY comments and
persistent labels never qualify a different candidate; MEDIATE retains trusted
deduplication. Every candidate, including docs-only, needs the five Python and
five Rust cells. See [candidate policy and deployment limits](docs/ci-candidate-readiness.md).

Trusted Actions receipts bind gate-owned labels to exact label-event IDs across
head changes. Only that event can be revoked. The candidate coordinator preserves
human removals across heads until an explicit relabel; old head-bound removal
receipts do not bypass that hold. This also covers legacy removal history without
inventing ownership.
Later re-adds are preserved; ambiguous ownership requires manual action. GitHub has no atomic
compare-and-delete, so a human change between the final check and deletion is an
unavoidable narrow race, not an atomic guarantee.

Legacy label additions have trusted intents and three bounded visibility checks.
New candidate READY uses journaled direct dispatch without adding a label. If legacy ownership remains unknown or recording
proof fails, the intent blocks subsequent READY processing, even after a restart.
It never infers ownership from the PAT owner's name. Inspect the label and
manually remove/re-add `ci:full` to reconcile; if absent, add then remove it.
The removal must be later than the intent's timestamp (same-second ambiguity
remains blocked). Legacy labels without ownership proof are not automatically
removed. API failures report uncertain cleanup rather than claiming absence.

An explicit HTTP 400/401/403/404/409/422/429 label-write rejection settles only
that intent and permits retry. Transport errors, timeouts, and server failures
remain uncertain; failure to record rejection also retains the blocker. A failed
PR reconciliation does not prevent scheduled recovery of other PRs.
Same-second review edits are conservatively eligible when ordering is ambiguous;
identical follow-up content retains the same findings fingerprint and three-attempt
budget. Same-second human removal cannot prove intent ordering: remove/re-add
in a later second to reconcile. Event IDs and comment IDs are never compared.

---
title: Astra — soak isolation and rebase safety checklist for Cockpit Admin
type: evidence
status: reference
last-verified: 2026-09-06
development_sha: 892e2dbaab0093e91857cea373dc188d638d8d80
live_soak_sha: c5ef0ad3d9acd3a85aa86228491ea1827bfd529e
prior_soak_sha: 61ca0af27dc903ff97237b3cc2c8e83c735ebb09
incident_soak_sha: 1e2d4e3ca7c2fcae4e320ce5f72b44905b036ee4
---

# Soak isolation and rebase safety — Cockpit Admin

**An ordinary development rebase preserves the three detached source trees under the
conditions below. This session also has an enforced read-only mount boundary covering
their files and Git metadata. An unconditional host-wide “cannot rewrite” claim is false:
these are linked worktrees with shared Git storage, and the soak launcher uses the
development tree's virtual environment.** Twenty-one disposable experiments reproduced
the normal behavior and concrete exceptions without operating on a real soak tree.

The current live **disk pin is `c5ef0ad`**. The prior pin and BUG-114 incident remain separate
preserved trees. A matching disk pin does not identify the process serving port 8787 or
certify its audit chain. Host process identity was unavailable in this command namespace.

This is one documentation/evidence work package supporting W-22 and the RW-20 isolation
handoff. It changes no application, invariant, execution authority, configuration, runtime
environment, protected ref, or ledger. No real checkout, rebase, reset, worktree maintenance,
restart, SQLite connection, keyring lookup, broker call, or message send was used. Synthetic
repositories and synthetic SQLite data lived only under fresh `/tmp/astra-soak-git-*`
directories. The actual development `.git` is read-only here; a real development rebase was
neither attempted nor claimed as an executed test.

## Observed layout and preservation evidence

Research began September 6 at approximately 17:46 Pacific (September 7 00:46 UTC).
The first byte manifests ran **00:50:23–00:50:24 UTC**; the post-experiment manifests ran
**00:58:24–00:58:25 UTC**, both on September 7. The requested filename uses the Pacific date.
The requested research cutoff is interpreted as September 6 20:15 Pacific / September 7
03:15 UTC.

All four worktrees resolve to distinct physical directories. All share
`/workspace/ai-investment-cockpit/.git` as their Git common directory:

| Role / absolute worktree | Observed HEAD | Per-worktree Git directory | Tracked files | Full status at baseline |
|---|---|---|---:|---|
| Development: `/workspace/ai-investment-cockpit` | `892e2dbaab0093e91857cea373dc188d638d8d80` | `.git` | Not a protected-tree census | Clean; branch `work/astra-fresh-install-smokes-2026-09-06` |
| Live: `/workspace/cockpit-soak` | `c5ef0ad3d9acd3a85aa86228491ea1827bfd529e` | `.git/worktrees/cockpit-soak2` | 581 | Detached; clean |
| Prior: `/workspace/cockpit-soak-61ca0af-prior` | `61ca0af27dc903ff97237b3cc2c8e83c735ebb09` | `.git/worktrees/cockpit-soak1` | 474 | Detached; `?? PRIOR-PIN.md` only |
| Incident: `/workspace/cockpit-soak-bug114-incident` | `1e2d4e3ca7c2fcae4e320ce5f72b44905b036ee4` | `.git/worktrees/cockpit-soak` | 459 | Detached; `?? BUG114-INCIDENT.md` only |

The administrative names are significant: **`worktrees/cockpit-soak` belongs to the
incident, not the current live tree.** Always resolve the `.git` pointer and its reciprocal
`gitdir`; a basename or remembered worktree number is unsafe.

The two untracked notes are intentional preservation records, not debris to remove for a
clean status. Every tracked regular file in all three trees matched its committed raw Git
blob, with no missing/special entries or byte mismatches. The complete file census also
included ignored runtime data, `.env` bytes, caches, and the preservation notes; their
contents were hashed, not printed. No protected-tree symlink or regular file with multiple
hard links was found. The development census, including its virtual environments, found
12 symlinks, none resolving into a protected tree, and no multiply linked regular files.
This is a point-in-time census, not protection against a later alias or mount change.

A read-only `git fsck --strict --no-reflogs --no-dangling` check naming all three full pins
also returned exit 0 with empty stdout/stderr at 01:05 UTC. This checks object integrity and
connectivity now; it does not reserve objects against later privileged deletion or GC.

| Protected tree | All-file census | Tracked-file manifest SHA-256 | All-file manifest SHA-256 at baseline |
|---|---:|---|---|
| Live | 729 | `c5025f6b70d8641790c8d611e821033d1fb3078d08082f0e31f6682fec9fb163` | `d09aea1fbf2659901d54afe3354a8374d94b87d596b3062a7b6942a79dd0b43e` |
| Prior | 6,195 | `d2fcb255593af77c234fa8ddc768499e42f881a8eb4762124677f43685654efb` | `b513f7fc9b22c2ac498f7d4c6d585ef0690ba589ae7fe0e6500cb1e01b008cff` |
| Incident | 7,793 | `42f6c380be414b91af4fe6a1c6f7dbc62f0acb33416c7cb02a2c358b2f7a917c` | `55976a71a9033f7c79fd7aa3af1592aa6c0c016cb84f3bcb4a34a62c865c976d` |

At the post-experiment check, **all three HEADs, all tracked-file manifests, and every
captured per-worktree Git metadata record were unchanged**. Every prior/incident file
record was unchanged, including bytes, mode, inode, size and modification time. The live
tree changed only `app/cockpit.db-wal` and `app/cockpit.db-shm` in that interval. This is
consistent with an external running writer; it neither identifies that writer nor proves
audit validity. The live tree's all-file digest consequently changed, as a running database
can legitimately do. It must not be called byte-frozen.

Manifest definition: SHA-256 of compact JSON containing sorted
`[relative_path, kind, file_sha256, symlink_target, permission_mode]` rows. Separate records
retain device/inode, link count, byte length and modification time. Access times, directory
metadata, ownership/ACLs and a filesystem-wide atomic snapshot are not covered by that
digest. No file changed size, inode or modification time during its individual baseline or
post-experiment read. That observation is not a coordinated SQLite transaction snapshot.

### Boundary enforced in this session

The observed `/proc/self/mountinfo` reports `/` read-only, with writable mounts for the
development tree and `/tmp`, and explicit read-only mounts over development `.git`,
`.agents` and `.codex`. There is no writable mount below any protected soak path. Together
with the observed absence of writable aliases to protected files, this blocks this shell's
direct file and Git-metadata rewrites. No write probe was made against a protected path.

A supplementary process read found UID/GID 1000, zero effective/permitted/bounding
capabilities, `NoNewPrivs=1`, and a seccomp filter active. The child's inherited descriptors
were pipes, not writable handles to protected files. The complete mount-point/options
inventory is retained in `session-permissions.json`; the writable proc mount is not a
writable soak-file mapping. This is evidence for ordinary access in this namespace, not
a sandbox-escape assessment or a guarantee against a privileged host actor.

This is the current command process's mount view. It does not bind Cockpit Admin, a host
service, another namespace, or a privileged process. The observed owner/mode on the host
paths is `box:box`, directories `0755`; those ordinary permissions do not isolate another
process running as `box`. The read-only view can coexist with live WAL changes made outside
this namespace.

### Git boundary on a host where development Git is writable

Detached HEAD, index and rebase state are per-worktree. Branch/tag refs, repository config
and objects are shared; another worktree can address a per-worktree ref explicitly.
This explains the ordinary-rebase result and the cross-worktree counterexample.
[Git worktree documentation](https://git-scm.com/docs/git-worktree).

The proof for **ordinary source isolation** is conditional on a resolved development
target, no routing override or alias into a protected tree, no callback with protected
write effects, and no explicit operation on protected metadata. Git creates replacement
commits and advances the selected development branch; it does not edit the existing pinned
commit objects or apply a second checkout to each detached sibling. The experiment replayed
commits that the prior and live trees themselves pinned, rather than merely rebasing an
unrelated documentation commit.

The observed configuration contained no active hooks, `core.hooksPath`, fsmonitor, clean/
smudge/process filter, custom merge driver, conditional include, worktree configuration
extension, or rebase-update-refs setting. `.git/hooks` held only stock `.sample` files;
attributes were `* text=auto eol=lf`; no tracked submodules, replacement refs, graft file,
alternate object store, or auxiliary attribute file was present. Global credential-helper
configuration exists, but was not invoked by a fetch/push in this research. Future configs,
incoming attributes and invocation arguments require a fresh check.

## Runtime paths: source isolation is not complete runtime isolation

The launcher inspected as text was
`/home/box/.config/cockpit/start-read-only.sh`. It selects the paths below. `main.py`'s
`compose()` supplies relative database/raw defaults; `config/settings.py` supplies the
relative audit-head default. These are a reconstruction of that launcher and disk defaults,
**not a measurement of the serving process's loaded environment**.

| Component | Launcher/default resolution | Consequence for development work |
|---|---|---|
| Source / `PYTHONPATH` / working directory | `/workspace/cockpit-soak/app` | Detached source and default relative state stay under the soak path |
| Dotenv | `/workspace/cockpit-soak/app/.env` | Separate from development `.env`; no audit-head path override was found in either disk dotenv |
| SQLite and sidecars | `/workspace/cockpit-soak/app/cockpit.db`, `-wal`, `-shm` | Excluded from normal Git status; never infer preservation from HEAD/status alone |
| Raw evidence | `/workspace/cockpit-soak/app/data/raw` | Separate current tree; ignored by Git |
| Audit-head default | `/workspace/cockpit-soak/app/data/audit-head.json` | This file was absent in the live census; actual process path/state needs host evidence, not an invented replacement |
| HALT override | `/workspace/cockpit-soak/data/HALT` | Root `data/`, distinct from `app/data/`; do not clear or relocate it for a Git check |
| Virtual environment | **`/workspace/ai-investment-cockpit/.venv`** | Shared with writable development; pip, cleanup or tracked-path changes can affect the runtime |
| Interpreter target | `.venv/bin/python` → `/usr/bin/python3.13` | Observed Python 3.13.5; version/symlink identity alone does not freeze site-packages |
| Credential backend and launcher | User-owned paths outside the source tree | Shared user resources; no credential contents were read |

The inherited research `PYTHONPATH` was `/workspace/ai-investment-cockpit/app`, a further
reason not to import or launch application code during preservation checks. The snapshot
collector uses standard-library filesystem/subprocess operations only.

Path reuse adds a separate cutover hazard. In a disposable Python process, renaming its
source tree preserved the old cwd and already loaded package, while reusing the original
absolute path let a later package submodule load from the replacement tree. The process
then contained both revisions. A control with existing top-level modules stayed on the old
cwd, so this is a demonstrated package-path hazard, not a claim that every import switches.
Python caches loaded modules and uses package `__path__` for submodule lookup.
[Python import machinery](https://docs.python.org/3.13/reference/import.html).
No claim is made that this occurred in the real September 6 cutover. For a separately
authorized cutover, verify the old process has exited before reusing its source alias;
HEAD checks on the replacement directory cannot supply that process evidence.

The existing [W-22 runbook](../runbooks/w22-paper-mvp.md) has a different Windows layout:
source from the detached tree, but dotenv, working directory/database/raw, and interpreter
from development. It supports source separation while retaining shared runtime state. Do
not describe that recipe as database, dependency, or incident isolation, or apply it to a
frozen incident. The earlier [RW-20 matrix isolation record](rw20-matrix-isolation-2026-09-06.md)
observed an independent workstation clone with one worktree; that finding cannot be
transferred to this four-worktree host.

There is one useful existing protection against runtime-to-Git interference:
[production composition](../../app/main.py) passes
`GitCommitWitness(settings.audit_head_file, commit_fn=False)`. The
[witness adapter](../../app/bridge/witness.py) can write its local mirror without staging or
committing a development branch. This does not make the mirror immutable or independent.

## Reproduced failure modes

**21/21 final synthetic cases passed their explicit assertions**, using Git 2.47.3 and SQLite
3.46.1. “Pass” for a counterexample means the unsafe effect was reproduced, not that it is
acceptable. Every repository mutation below was confined to disposable `/tmp` fixtures;
the real three trees were only read before and afterward.

| Case | Measured result | Admin implication |
|---|---|---|
| Ordinary rebase | Development tip changed while all three detached HEADs, file bytes and per-worktree metadata stayed identical; prior/live pinned commits were in the replayed range | Normal rebase separation holds under the stated conditions |
| Branch move | Force-moving an unoccupied shared `main` ref changed its resolution from every tree, but left detached pins and files intact | A branch name is not the soak pin |
| Conflict and abort | Conflict was observed; protected snapshots matched both during the conflict and after abort; development returned to its original tip | Abort the development rebase in development; no soak reset is needed |
| `--update-refs` | An unoccupied branch named like a preservation pin moved; an actually checked-out branch was skipped; detached pins stayed put | Disable automatic ref rewriting for a preservation-sensitive rebase; inventory shared refs |
| Explicit sibling HEAD update | `update-ref worktrees/live/HEAD ...` from the synthetic development cwd changed a locked sibling's detached HEAD without changing its files | Protect `.git/worktrees/*` too; protecting source directories alone misses pin metadata |
| Worktree lock | Normal remove refused; checkout inside the locked tree succeeded; double-force remove succeeded | A worktree lock is a retention guard, not an immutable-source lock |
| Routing override | `GIT_WORK_TREE` redirected `git -C dev reset --hard ...` into a sibling's files while its detached HEAD stayed unchanged | `-C` alone is insufficient; inspect/clear Git routing variables |
| Pre-rebase hook | A synthetic hook invoked by rebase modified a sibling tracked file without moving its HEAD | Audit callbacks and permissions; cwd is not a filesystem sandbox |
| Rebase `exec` | A shell command modified ignored incident evidence despite disabled hooks and `--no-verify` | Exclude/review `exec` lines; disabling hooks does not disable arbitrary rebase commands |
| Development cleanup | `git clean -fdx` removed the shared synthetic `.venv` while every protected snapshot stayed unchanged | Do not run broad cleanup against a shared runtime environment |
| Replacement ref | Shared `git replace` changed Git's view of an unchanged HEAD; `GIT_NO_REPLACE_OBJECTS=1` recovered the original view | Verify original objects and inventory `refs/replace` |
| Checked-out branch guard bypass | `branch -f` refused a checked-out sibling branch; direct `update-ref` moved it anyway | Git porcelain safeguards are not an authorization boundary |
| WAL visibility | Main DB SHA-256 stayed unchanged after a commit; `mode=ro` saw one row, while `immutable=1` saw zero | Preserve sidecars; do not use `immutable=1` on an active WAL database |
| Object retention | Registered detached HEAD retained the old commit after reflog expiry and GC; removing its remaining worktrees/reachability then GC lost it | Keep worktree registrations and independently retained objects; do not rely on reflog expiry windows |
| Status masking | `assume-unchanged` plus a tracked-file edit produced expected HEAD and empty status; direct blob comparison detected it | Verify bytes, not just status; inspect index flags |
| Incoming runtime path | An ordinary rebase installed an upstream force-tracked `.venv` file over an ignored installed dependency, leaving protected source unchanged | Review incoming paths, not just commands; `.gitignore` cannot freeze runtime dependencies |
| Status refresh | Ordinary `status` changed index bytes after a stat-only change; `--no-optional-locks` preserved them | Use optional-lock suppression for read-only evidence collection |
| Cwd alias control | After rename/path reuse, existing top-level imports still resolved through the old cwd | Do not overstate the path-reuse hazard as affecting every import |
| Package alias cutover | The old package remained loaded, while a later submodule came from the replacement absolute package path | Verify old process exit before a separately authorized path-reusing cutover |
| Read-only SQL sidecar | A `mode=ro` connection followed by `SELECT` created a missing SHM in a writable synthetic directory | A read-only SQL connection does not preserve all filesystem bytes |
| Clone independence | After source GC, a `--shared` clone lost its borrowed pin object; a `--no-local` clone retained its independently stored object | Verify object independence, not merely the existence of another `.git` directory |

The initial broad alias-cutover hypothesis failed: cwd precedence kept an existing
top-level module on the old tree. Its script and failure note are retained. The final
four-case supplement records that control, the distinct package-path counterexample,
read-only SQL side effects, and clone independence; the rejected hypothesis is not counted
as a passing experiment.

These mechanisms are consistent with Git's documented
[rebase ref updates and shell execution](https://git-scm.com/docs/git-rebase),
[hook execution](https://git-scm.com/docs/githooks),
[direct ref updates](https://git-scm.com/docs/git-update-ref),
[ignored-file cleanup](https://git-scm.com/docs/git-clean), and
[garbage-collection reachability](https://git-scm.com/docs/git-gc).
The observed preservation and counterexample outcomes above come from the retained local
experiments, not from assuming documentation alone proves this host's configuration.

Other routes to treat as outside ordinary rebase isolation: explicit `git -C <soak>`
operations, `--git-dir`/`--work-tree`/`GIT_COMMON_DIR`/`GIT_INDEX_FILE` redirection, manual
edits under `.git/worktrees`, branch/ref deletion, `worktree remove/prune/repair/move`,
forced shared-branch checkout, object-store removal, external sync/copy/deployment scripts,
an IDE task, recursive cleanup, or a service restart using a changed launcher. Shared config,
attributes, filter/merge helpers, editors, fsmonitor, submodule recursion, and hooks are
execution/configuration inputs. An unexpected one stops the conditional proof until reviewed.

## BUG-114: retain the failed evidence as failed evidence

The incident note pins `1e2d4e3`, records its September 3 freeze, and identifies the
`13588/13589` fork. The [original incident analysis](sol-pipeline-audit-2026-09-03.md)
records the first invalid predecessor link at sequence **13589**, at **2026-09-03
16:43:01 UTC**: both rows 13588 and 13589 linked to 13587. Its HMAC/audit diagnosis is
historical evidence; this session did not obtain a key or repeat that verification.
The [register](../REGISTER.md) records the BUG-114 repair in PR #68, merge `61ca0af`, repair
`3035d83`. Neither that repair nor PR #70 makes the original fork a valid continuous soak.

These original file hashes matched at baseline and post-experiment:

| Tree / relative file | Bytes | SHA-256 |
|---|---:|---|
| Incident `app/cockpit.db` | 12,705,792 | `44660c31fa5392ebf02f5b32f77c6ed0374b525d8c74c3cc353da57a1fb0ae66` |
| Incident `app/cockpit.db-wal` | 4,725,672 | `5ab3e3310493ec8a550e70465f945d6d18cf1b3bc83de948730b7161b101c643` |
| Incident `app/cockpit.db-shm` | 32,768 | `df8c7b0383faeb533e5cbf6c01d2be1b8caf0ab2ab311677c166be8cc19e2d9e` |
| Incident `app/data/audit-head.json` | 93 | `da8b9d757facbe4a815bfcde1d8d1125a5f1302311f4e7074cdcc932bc3b2a02` |
| Incident `BUG114-INCIDENT.md` | 315 | `46545cba04c0948dc5896de05ac8637550943d90b3cae574cb32792021e6a76d` |
| Prior `app/cockpit.db` | 10,153,984 | `a647aadb8c59bd8b27fd193b3cb61fd2008feaf95e7775f70cdc047be9b6aad0` |
| Prior `app/cockpit.db-wal` | 4,231,272 | `8459be6b5a89e92ad71e512b17e3368a5ae395c19d6c9b49aef3dd4011c6698f` |
| Prior `app/cockpit.db-shm` | 32,768 | `8f19f092f92e04196cab86256c4af572bcb9655bf711a7a2454dc4588ff612be` |
| Prior `app/data/audit-head.json` | 92 | `c2642f7e5dc13ca2f9b57995dd43d660268d598686b70a8402e8ef10644b8988` |
| Prior `PRIOR-PIN.md` | 248 | `3f4602b479092104dc4ce949d4b5259b688c387e699736e21ae1b55f4239ccba` |

Read the DB/WAL/SHM as ordinary bytes for preservation checks. Merely issuing SQL `SELECT`
does not establish a no-write filesystem contract: opening a WAL database may create or
use sidecars. SQLite's `immutable=1` changes locking/change-detection assumptions and is
unsafe for a changing database. The synthetic WAL case also demonstrates stale visibility.
[SQLite WAL documentation](https://www.sqlite.org/wal.html),
[SQLite URI parameters](https://www.sqlite.org/uri.html).

For later forensic analysis, preserve the complete frozen original set and work on a
separate verified derivative; never open the original through `main.compose()`,
`make_engine()`, migration, checkpoint, `VACUUM`, recovery, anchor regeneration or a “repair
until green” tool. An active DB needs a separately designed consistent capture; this
research did not copy it or establish one. File manifests are preservation evidence, not
a backup, an independent witness, or a repaired audit result.

## Admin checklist

### Before development branch/rebase work

- [ ] Bind the intended development path, branch and full before-SHA to a timestamp. Confirm
  all three exact protected paths and full pins in the layout table. Treat old notes calling
  `1e2d4e3` or `61ca0af` the live pin as historical; preserve their wording and provenance.
- [ ] Resolve each real path, `.git`, common directory and reciprocal worktree `gitdir`.
  Review symlinks, hard links, bind mounts/junctions and the actual actor's writable paths.
  Protect both the source/state files and the metadata that gives the pin its identity.
- [ ] Capture the three detached HEADs, raw tracked-byte manifests, Git metadata manifests,
  and the full prior/incident file manifests. Expect the two preservation notes as the only
  untracked status entries there. Do not delete them to make the result empty.
- [ ] Inspect Git environment/configuration without dumping credentials. Account for every
  routing/config/object-directory override, alias/helper, hook, filter, merge driver,
  replacement ref, index flag and submodule setting. Suppress optional index refresh and
  fsmonitor for observation. Resolve original commit objects with replacements disabled.
- [ ] Review the old and incoming trees for runtime paths or changed attributes/scripts.
  Keep the shared `.venv` intact; use a separate test environment/output directory. Never
  run `pip install`, dependency cleanup, tests or application startup from a frozen tree.
- [ ] Establish the actual serving PID/start time, executable, cwd, import roots, dotenv,
  DB/raw/audit-head/HALT paths from the host's read-only facilities. Record “unverified”
  for missing access. Launcher text, a port response, a growing WAL, and HEAD alone do not
  establish that mapping. Keep Git-preservation approval separate from service-health claims.
  For a future authorized cutover, verify the old process exits before reusing its absolute
  source path; do not infer exit from a renamed directory or a new disk HEAD.
- [ ] Serialize Admin cutovers and development maintenance. A before/after comparison
  cannot attribute overlapping writes, and it cannot prove that a file was never briefly
  changed and restored. Use enforced permissions when that stronger guarantee is required.

### During the development operation

- [ ] Act only on the resolved development repository and intended branch. Use explicit
  upstream/onto commits; disable `rebase.updateRefs`/`--update-refs` and autostash for a
  bounded rebase, and require a clean development starting state.
- [ ] Use reviewed hooks/helpers or a command-local empty hooks path. Review an interactive
  todo and its editors; exclude arbitrary `exec` steps from the source-isolation claim.
  `--no-verify` is not an all-callback or filesystem boundary. Required project gates still
  run separately; disabling Git callbacks does not waive them.
- [ ] Do not target protected trees or `.git/worktrees/*`, change preservation refs/config,
  run worktree maintenance, prune/rewrite shared object history, perform broad cleanup,
  or restart/repoint the service as part of a rebase. Do not force checkout of a branch
  occupied by another worktree.
- [ ] On conflict, inspect/resolve or abort **in development**. Preserve user changes there.
  A rebase problem does not call for any operation on soak, the prior pin, or the incident.

This checklist describes host operations for Admin; it is not a command to run a rebase in
this session's read-only Git mount. No worktree lock, pin tag, clone migration, permission
change, restart or protected-tree modification was installed by this research.

### Afterward, before asserting isolation

- [ ] Record the development after-SHA and intended diff. Repeat the same read-only
  observations using the same manifest definition and exact protected paths.
- [ ] Require the three HEADs, original tracked bytes and per-worktree Git metadata to
  match. Require **every frozen prior/incident file** to match, including DB/WAL/SHM,
  anchor, raw evidence, config and preservation notes. Compare missing/new files as well.
- [ ] For live state, distinguish source/config drift from expected runtime writes. Do not
  demand an unchanged WAL or describe an uncoordinated file-hash set as a valid audit
  snapshot. Audit health requires its own consistent, appropriately authorized evidence.
- [ ] If a protected pin/source/frozen-state record differs, stop the isolation claim and
  preserve both observations plus timestamps and operation logs. Do not reset the tree,
  heal the ledger, regenerate an anchor, delete a note, or silently adopt a new baseline.
- [ ] Retain report, scripts and raw manifests outside the development cleanup domain.
  Record any missing process/permission/witness evidence explicitly. This documentation
  and Linux synthetic experiment set do not newly attest the DEC-09 Windows/WSL2 matrix,
  a qualifying market day, paper execution, live activation or SEC-11.

### Stronger future isolation, if Admin requires “cannot” on the host

Separate source and runtime dependencies from the developer's writable namespace, and
enforce read-only access to frozen source, prior/incident state, and their Git metadata
using an identity the developer cannot alter. Give the running service write access only
to its current epoch's state. Preserve full commit objects and byte manifests independently
of mutable development refs.

An independent clone or a verified source archive can remove the shared Git administrative
dependency, but neither creates an OS permission boundary. A local clone can use hard links
or alternate object storage; choose a transfer/copy that does not retain that dependence
and verify it. [Git clone options](https://git-scm.com/docs/git-clone).
This is a future Admin design choice; it does not authorize moving or rebuilding today's
soak trees. Detached HEAD and worktree locks alone cannot supply this stronger guarantee.

## Reproduction and retained artifacts

The [local evidence directory](../../data/astra-soak-isolation-2026-09-06/) contains:

- `baseline.json`, `post-experiments.json`: complete file/metadata records and summaries.
- `host-footprint.json`: mount view, path/alias census, config key names, launcher hash,
  reconstructed runtime paths and the process-namespace limitation; no secret values.
- `snapshot.py`: collector that reads the protected paths as bytes and writes a new,
  exclusive evidence file under the development `data/` directory. Git observation uses
  `--no-optional-locks`, `core.fsmonitor=false`, disabled hooks, cleared inherited Git
  variables and `GIT_NO_REPLACE_OBJECTS=1`.
- `astra_soak_git_experiments.py`, `git-experiments.json`: first 13 synthetic cases and
  their exact command argv, exit codes and stdout/stderr.
- `astra_soak_git_more_experiments.py`, `git-more-experiments.json`: four supplementary
  retention/status/runtime-path cases and exact command results.
- `astra_soak_runtime_experiments.py`, `runtime-experiments.json`: four runtime/clone cases;
  `runtime-probe-initial-hypothesis.py` and `.txt` retain the rejected top-level import
  hypothesis. No real process was renamed or restarted.
- `git-fsck.json`: the read-only object verification command and successful result.
- `session-permissions.json`: process capability flags and all mount-point/options rows,
  omitting backing-store details. `venv-metadata-baseline.json` records 417 environment
  configuration/entrypoint/distribution-metadata entries; it does not hash all installed
  package bytes or identify modules already loaded in a service process.
- `monitor.py`, `observation-*.json`: bounded quarter-hour observations through the requested
  cutoff, with comparisons to the original baseline. A drift record never triggers a repair.

For example, another byte observation can be made without importing Cockpit or connecting
to SQLite (choose a new label; the collector refuses to overwrite an existing result):

```bash
/workspace/ai-investment-cockpit/.venv/bin/python \
  /workspace/ai-investment-cockpit/data/astra-soak-isolation-2026-09-06/snapshot.py \
  admin-before-rebase
```

Inspect the collector and current Git configuration before reuse on a different host. Its
path constants identify this host; a redirected/missing/unexpected tree must be investigated,
not substituted automatically. The full transcripts include deliberately unsafe commands
against synthetic paths; they are evidence, not instructions to replay against real trees.

`data/` is intentionally Git-ignored. These raw local artifacts are not promised to travel
with a documentation PR, and their hashes are not independent timestamp attestations.
Retain them with this report before cleaning the development tree. The evidence markdown is
the only intended tracked addition by this research; this agent did not commit, push or
change branches. Other concurrent work in the development tree is outside that claim.

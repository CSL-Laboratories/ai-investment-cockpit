---
title: RW-20 local platform matrix and soak separation — 2026-09-06
type: evidence
status: live
last-verified: 2026-09-06
---

# RW-20 local platform matrix and soak separation

## Scope and status

This record distinguishes local platform compatibility, local development separation,
and deployed soak evidence. None supplies independent W-24/W-25 witness authority,
selection authority, paper-order approval, or a repair of a production ledger.

The current workstation is `C:\Users\drfoo\OneDrive\Documents\ChatGPT\ai-investment-cockpit`.
The earlier August matrix was recorded on a different `C:\Users\Lucas\...` workspace;
those historical results cannot attest September source changes on this workstation.

**Final-source matrix results are pending.** Both native interpreters and a real
Ubuntu 24.04 WSL2 environment have been prepared. An environment that installs
successfully is not a passing DEC-09 cell.

## Prepared environments and bounded setup

- Host: Windows `10.0.26200.9168`; WSL `2.6.3.0`, kernel
  `6.6.87.2-microsoft-standard-WSL2`.
- Native CPython 3.11.16 and 3.12.14 were installed under `C:\t\a26\python`
  with uv 0.12.10, `--no-bin --no-registry`, without replacing the repository's
  existing Python 3.13 environment or changing PATH.
- The separately named `Cockpit-Audit-2404` distribution was imported into
  `C:\t\a26\wsl`. `/etc/os-release` reports Ubuntu 24.04 LTS. Its native Linux
  CPython 3.11.16 and 3.12.14 environments live under `/audit` on ext4.
- The CI package set in `.github/workflows/ci.yml` was installed in each private
  environment, including pip, pytest, Ruff and the runtime dependencies, excluding
  `discord.py`. All four preparatory `pip check` commands passed. uv resolves
  unpinned packages; the completed cells retain their exact dependency versions.
- The first WSL start overlapped native environment installation and exhausted
  Windows commit capacity. Python 3.12's first `ensurepip` raised `MemoryError`
  and PowerShell reported a paging-file allocation error. These are retained setup
  failures, not passed tests. Only this audit's distro was terminated and its
  remaining installation interrupted. Setup then proceeded one process at a time.
- Only the owned distro's `/etc/wsl.conf` was changed: `systemd=false`, Windows
  drive automount disabled, interop disabled, and Windows PATH import disabled.
  No user-global `.wslconfig`, Windows feature, service, pagefile or reboot was used.
  Both Linux environments subsequently installed and passed dependency checks.

The [uv installation documentation](https://docs.astral.sh/uv/guides/install-python/)
identifies its interpreter builds as Astral `python-build-standalone` distributions.
These are native CPython builds, not the python.org Windows installer distribution;
this provenance is recorded explicitly. Microsoft documents the
[WSL import mechanism](https://learn.microsoft.com/en-us/windows/wsl/use-custom-distro).
The [Canonical Ubuntu image](https://cloud-images.ubuntu.com/wsl/releases/noble/current/)
`ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz` was downloaded over HTTPS and checked
against Canonical's SHA256SUMS:

`8251e27ffff381a4af5f41dcb94d867de3e0d9774a9241908ab34555d99315ea`.

## Source and execution binding

The starting source is `b4a199ff41ed84d78f21602a84d9665c1072ec6a`, tree
`9f87756048f94133a8ca76f8caed51be68547653`, which includes PR #68. A baseline
Git archive was prepared; a baseline full run was deliberately deferred in favor
of the final repaired source so the same limited workstation resources are used
for current acceptance evidence. This is not a baseline PASS claim.

Each cell uses a fresh regular-file extraction of a committed Git archive into
its own directory. The runner rejects archive links, absolute paths and parent
traversal; extracts no Git metadata; records commit, Git tree, archive SHA-256,
interpreter, platform, and every extracted file's SHA-256; and checks those files
again after testing. Test outputs are outside the source directory.

Each cell clears ambient `COCKPIT_*`, `PYTHONPATH` and `PYTEST_ADDOPTS`, selects an
absent environment file, uses its own short temporary directory, and runs from
`app/`:

1. `python -VV` and `python -m pip freeze`.
2. `python -m pip check`.
3. `python -m ruff check .`.
4. `python -m pytest -q -ra --junitxml=<cell>/pytest.xml`.

Linux uses a native Linux-only PATH. Per-step stdout/stderr, exit statuses,
durations, JUnit skip reasons, source manifests and artifact SHA-256 hashes are
retained. A source change or nonzero check exits the runner unsuccessfully.
This is local evidence, not an independently signed attestation.

## What development separation proves

Read-only inspection found a physical `.git` directory inside this checkout and
exactly one registered Git worktree, this checkout. No `.git` indirection points
at a shared soak repository. Its hook directory contained only stock `.sample`
files, and `core.hooksPath` was unset. The audit itself never rebased, checked out,
reset or changed branches in a deployed directory. Native test directories are
outside the checkout; the WSL distro contains only copied archive files and has
no Windows drive automount.

Ordinary Git branch/commit/rebase operations here address this repository's refs,
index and working files. The archive test source has no shared Git index, linked
worktree or symlink route into a soak tree. The tests use temporary databases and
the suite's in-memory keyring fixture. Running these cells cannot legitimately
turn the historical fork into a healthy production chain: no production ledger
was selected, copied back, truncated, re-signed or given a replacement anchor.

This is **operational separation, not a security boundary**. The same Windows
user can address other user-writable files; WSL and Windows share a host. Disabling
automount prevents an accidental mounted-file route, but does not create an
independent trust domain or revoke all capabilities of the controlling Windows
account. A separately reachable deployment hook, shared filesystem mapping,
manual file copy or explicit remote command would require separate inspection.

## What remains unproved about the live soak

`/workspace/cockpit-soak` is not present in this workstation's initial WSL state
(no distributions were installed) or its local registered Git worktrees. This
audit has not accessed the live host. Historical repository documents record a
September 4 soak pin of `61ca0af`, but that is documentary history, not a current
HEAD or process assertion. No claim is made that the live ledger, anchor,
generation, HALT state or process remained byte-identical across this session.

To establish deployed separation without repairing it, the operator's live-host
read-only evidence must identify the soak's resolved directory, Git common-dir,
worktree list and HEAD; the running process's executable and working directory;
and the resolved runtime database/anchor paths, including shared mounts or links.
Bind that observation to the development tree and operation times. A running WAL
database should be inspected through one read snapshot rather than treating
uncoordinated hashes of its changing database/WAL files as a consistent snapshot.
Preserve any failing chain and its original anchors. Do not invoke recovery,
re-baselining, truncation or a startup path that writes migrations while gathering
the observation. A historical fork remains failed evidence even if new code
prevents another one.

## Limits that remain after a green local matrix

The test keyring is in memory, so this does not establish Linux SecretService
deployment. No remote deployment, live broker, real SEC request, independent
witness, WORM retention or market-day outcome is tested here. Source provenance
checks requiring the absent private `edgar-morning-brew` sibling and CLI smoke
tests requiring an absent executable must be reported as skips, not silently
counted as passes. The fixture refusal suite and actual cell results determine
code acceptance; no readiness threshold changes with this environment work.

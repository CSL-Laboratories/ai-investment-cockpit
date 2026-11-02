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

**All four real DEC-09 cells passed**, together with the separately scoped scorer
and excerpt checks on every interpreter. This closes the local platform evidence
gap for the source scopes below, including the descendant of PR #68. It is not a
new live-soak observation or an independent witness.

## Prepared environments and bounded setup

- Host: Windows `10.0.26200.9168`; WSL `2.6.3.0`, kernel
  `6.6.87.2-microsoft-standard-WSL2`.
- The initial `py -0p` registry listing exposed the system Python 3.13 install;
  it was not an exhaustive inventory of private environments. The existing
  project diagnostic `.venv` uses Python 3.13.14 and was left unchanged.
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
- After all outputs were copied and verified, only `Cockpit-Audit-2404` was
  terminated successfully. `wsl --list --verbose` confirmed it stopped under
  WSL2. Its files and environments remain available for reproduction. No global
  WSL shutdown or deployed process operation was used; see the retained
  `lifecycle.json`.

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

The full application cells use commit
`69a1c9c53c0beeb0f91310ee017b24ba21a592b7`, Git tree
`13979fca3e96fc64b31a17044b3c56c93b937fd8`, and application subtree
`5125e7edd2489c027364bbbfbd4556d928109817`. The Git archive's SHA-256 is
`42a1ce130db2bdc5d22d178ea56ce6b1dd14fddd8939eab838b5fed7352f4a33`, verified
on both the Windows file and its byte copy inside WSL. Each cell starts
with the same 499 tracked files.

The later research calculator and its tests are validated separately from a
two-file archive of `0149b86a8e0ce6d971e6c6f69bd31d08bdb5ad8c`. That commit's
application subtree is identical. The supplemental archive SHA-256 is
`403a33fdeef3fc4d2d9ead3f5e0e9bb4dfbc8e6589b944f29c132bb772635125`.
Each interpreter runs `unittest discover -s tools/tests -p test_research_value_score.py`,
Ruff on those two files, and the CLI without inputs; the runner verifies all three
measurements remain `unmeasured` with `research_only_no_gate_change` authority.
This supplemental check does not silently extend the full-suite source claim to
subsequent unrelated changes.

The five held-excerpt companion files come from
`8fc9c936c6a6c2c25d344a8425ffc5a48c01e879`, which also retains the same application
subtree. Their archive SHA-256 is
`6f5c6638c1553b61929c62289e03e85461d9f18c53a2f71aadb3c5190f26e949`.
They are added only after the full application run completes. The supplemental
runner refuses any overwrite, verifies all original 499 source files before and
after, and separately records the five additions. Each interpreter runs the
seven direct excerpt checks plus Ruff. These checks include reproducing the
known, still-unfixed SAFT repurchase false positive; they do not establish an
independent holdout, complete raw SEC source or current date authority.

| Environment | Python | Full pytest | Ruff / pip check | Supplements |
|---|---|---|---|---|
| Native Windows | 3.11.16 | 2,648 passed / 5 skipped; 532.15 s | both passed | 10 scorer + 7 excerpt checks passed; Ruff passed; empty input unmeasured |
| Native Windows | 3.12.14 | 2,648 passed / 5 skipped; 631.35 s | both passed | 10 scorer + 7 excerpt checks passed; Ruff passed; empty input unmeasured |
| Ubuntu 24.04 WSL2 | 3.11.16 | 2,646 passed / 7 skipped; 666.76 s | both passed | 10 scorer + 7 excerpt checks passed; Ruff passed; empty input unmeasured |
| Ubuntu 24.04 WSL2 | 3.12.14 | 2,646 passed / 7 skipped; 558.64 s | both passed | 10 scorer + 7 excerpt checks passed; Ruff passed; empty input unmeasured |

Plain per-cell logs, JUnit XML, dependency versions, source manifests, runners and
provenance are retained in [the companion evidence directory](rw20-local-matrix-2026-09-06/).
Its local `.gitattributes` uses `* -text whitespace=cr-at-eol` so Git preserves the exact bytes hashed
by each runner, including native CRLF where present, instead of applying the
repository's default LF normalization. The same-source manifest dictionaries
can be compared across platforms without pretending that their JSON newline
encodings have identical file hashes. The scoped whitespace rule recognizes CRLF
without changing the repository's general whitespace policy.
The captured `run_cell.py` additionally permits its original blank EOF line; its bytes
are preserved with a single-file `-blank-at-eof` whitespace exception.
All four cells' source checks found no modifications, and all four source-manifest
dictionaries match. Each Windows cell's
five skips are the intentionally absent `discord.py` tree, two private-sibling
provenance checks, and two symlink tests requiring an unavailable Windows
privilege (`WinError 1314`). Each Linux cell's seven skips are Discord,
three absent Claude/Codex CLI smoke checks, two private-sibling provenance checks,
and the Windows-only WMIC check. Both symlink refusal tests run on Linux. These
skipped cases are not represented as passing tests.

Each full suite reported five dependency deprecation warnings, retained in its
log. Table durations come from pytest's console summary; the JUnit durations and
outer subprocess durations are also preserved and measure slightly different
boundaries.

The [machine-readable summary](rw20-local-matrix-2026-09-06/matrix-summary.json)
verifies the completed commands, interpreter/platform identities, source
identities, all referenced artifact hashes, source-manifest equality and both
supplements before reporting the complete four-cell result. Its SHA-256 is
`0d63e2c39cadfe802d3b07afe17423098b0e116b97d929aa56fae6405d278703`.
These are local integrity checks, not independent signatures.

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
files, and `core.hooksPath` and `core.fsmonitor` were unset. At that initial inspection, the tracked
`.gitattributes` entry normalized text to LF; no tracked links or submodules were
present. Git LFS filters exist in system/user configuration, but the tracked
attributes do not activate them. The audit itself never rebased, checked out,
reset or changed branches in a deployed directory. Native test directories are
outside the checkout; the WSL distro contains only copied archive files and has
no Windows drive automount.

Ordinary Git branch/commit/rebase operations here address this repository's refs,
index and working files. The archive test source has no shared Git index, linked
worktree or symlink route into a soak tree. The tests use temporary databases and
the suite's in-memory keyring fixture. These cells supply no evidence of a
repaired production chain: no production ledger
was selected, copied back, truncated, re-signed or given a replacement anchor.

This is **operational separation, not a security boundary**. The same Windows
user can address other user-writable files; WSL and Windows share a host. Disabling
automount prevents an accidental mounted-file route, but does not create an
independent trust domain or revoke all capabilities of the controlling Windows
account. The checkout's OneDrive ancestors also carry cloud-provider reparse
attributes; this evidence does not exclude cloud synchronization or establish
what another machine does with a synchronized copy. Test archives live outside
that OneDrive tree. A separately reachable deployment hook, shared filesystem mapping,
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

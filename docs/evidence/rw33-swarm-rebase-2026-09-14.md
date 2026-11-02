---
title: RW-33 Cursor Cloud process rebase — local evidence
status: reference
last-verified: 2026-09-14
---

# RW-33 implementation and handoff evidence

## Source and scope

Operator-directed continuation of the unfinished 2026-09-14 rebase in
`C:\Users\drfoo\GitHub\ai-investment-cockpit`, branch
`codex/swarm-rebase-2026-09-14`, base
`f24f9a8dc9e0fda00ffc56109374ebd05fe519b1`. The OneDrive checkout supplied
to the local task was stale and did not contain the prior agent's changes.
No Git rebase was in progress: this is the research/development process amendment.

[Current process](../research/agent-swarm-rebase-2026-09-14.md) and
[Cloud handoff](../research/cursor-cloud-rdt-handoff-2026-09-14.md) are the outcomes.
The operator clarified that “Kernel” refers to the refined research/development/
testing goals, not a new architecture or separate project.

No production application code changed. Canonical invariant text was compared
against base and is unchanged. The retired `spawn_devin_leaf.py` working-file
SHA-256 remains
`faa74007de902e3d316d6a88bc520b0acb09de694d104da16837e6890eff0f6f`.
It is retained for historical audit, never new dispatch.

## Reproduced defects and repairs

| Before | Evidence | Result |
|---|---|---|
| The reference rebase contradicted current Devin instructions | AGENTS, CLAUDE, PLAN and Muse agent still named SWE-2 | Current entry points reconciled; old process notes explicitly historical |
| Cursor argv ignored model and used unverified flags | Direct argv inspection; local Agent 2026.08.25-3e8eec8 help lacks draft prompt-file/output flags | Guessed runnable argv removed; native Task and CLI distinguished |
| OpenCode could run while unnamed | Mock-child execution reached subprocess with an unfrozen brief | Bounded development role named; actual CLI dispatch remains mechanically disabled pending qualification |
| Spawner erased prior output and had no admission enforcement | Mock-child call returned 0 and truncated an existing file without budget/deadline | All execution removed from helper; non-preview refuses before I/O; previews preserve files |
| Codex draft claimed hardened parity without feature denials | Compared with unchanged production runner's explicit disabled-feature list | No parity claim or alternate decision path; Cloud qualifies one development adapter separately |
| Prompts and synthesis schema disagreed about attempt | Template emitted a forbidden field | Current schema/fixtures/template consistently include attempt and family metadata |
| Leaf/review could claim empirical PASS | Schemas allowed any boolean | All filing roles require false |
| Intake could overwrite a manifest or hash different bytes than validated | Separate reads and write mode in original helper | Validate/hash same bounded bytes; exclusive manifest creation |
| Synthesis could name unrelated leaf coverage or the same family | No cross-filing metadata checks | Exact admitted leaf/family-set consistency and distinct synthesizer family required |
| Windows process scan required removed WMIC | Both native Windows diagnostic cells raised FileNotFoundError | Query the same local process class through PowerShell CIM only when WMIC is missing; structured records retain opacity and positive-control refusals |

The original 22 swarm tests and application Ruff were reproduced as passing before
repairs. That did not exercise the above launch/admission defects. No real child,
inference job or broker operation was used to reproduce them.

## Validation

Focused final tooling suite: **43 passed / 1 skipped** on native Windows Python
3.11.16; the skip requires unavailable symlink privileges. Ruff is clean for the
application and research tooling. The first expanded test run exposed an overlong
automatically generated pytest parameter ID on Windows; explicit short IDs fixed
the test-path issue.

The full DEC-09 matrix **passed all four cells** against the final candidate:
Windows 3.11/3.12 each 3,270 passed / 5 expected skips; Ubuntu WSL2 3.11/3.12 each
3,269 passed / 6 expected skips. See the [complete matrix evidence](rw33-local-matrix-2026-09-14/README.md)
and [machine-readable summary](rw33-local-matrix-2026-09-14/matrix-summary.json).
All 3,275 tests were collected per cell; source and artifact hashes were verified.
Final source archive SHA-256: `50f6b69bc15f5bb33291da2e00ca377ba4ce7743b8d9c44fd9dcfed3358f755a`.

The initial diagnostic matrix used this 927-file frozen working-source archive:
`b5c7214271e6a9e48c87d778530c89a4e09d008df53989dd4f89709ac7c3cb73`.
The archive includes the uncommitted RW-33 implementation, schemas, fixtures and
tests; it is not falsely attributed to the base commit. Logs/attestations and the
per-file source manifest live under `C:\t\r33` and WSL `/audit/r33`.
Each cell records Python/dependencies, pip check, Ruff, full pytest/JUnit and
post-run source hashes. Dedicated project validation environments include real
discord.py. A current-checkout .venv is also provisioned from the CI dependency set.

Subsequent documentation-only changes clarify the Muse brief pointer and add this
evidence/final summary. Runtime/tool/schema/test bytes remain bound to the final tested
archive; documentation additions are not described as tested runtime changes.

### Pre-existing integration-test repair

The initial full run reproduced an existing assertion mismatch in
`tests/system/test_usage_limit_midloop.py`: the gateway returns
`review refused: watch_only_active`, while the test expected older prose. The
signed audit event uses `[review] watch_only_active`. Gateway, coordinator and
system harness bytes were verified unchanged from base. The test now requires
these exact codes plus the mode stage, no broker calls, signed refusal and
successful recovery controls. The repaired module passes both cases. No runtime
behavior or denial gate was changed. Initial failure evidence is retained;
replacement full-matrix results bind the corrected test to a new source archive.

An initial WSL launch referenced the disabled Windows mount and ran zero tests.
Source transfer into `/audit/r33` resolved that without changing WSL configuration.

### Windows host compatibility and diagnostic results

The next 928-file snapshot was
`71cd3625a32881250613df0fe6939521e66e152199adf20e5dfdc540b059297e`.
Both Ubuntu cells completed with 3,252 passed / 6 skipped. Windows 3.11 completed
with 3,252 passed / 1 failed / 5 skipped; Windows 3.12 with 3,251 passed / 2 failed /
5 skipped. These are diagnostic results, not four-cell acceptance.

Both Windows failures included the missing WMIC executable. The read-only
`tools/rw19_preflight.py` now falls back to a local `Get-CimInstance Win32_Process`
query when WMIC raises FileNotFoundError. Nonzero exits and timeouts still refuse;
there is no success fallback for an opaque or failed scan. CIM JSON preserves
multiline command lines, rejects malformed/duplicate process records and retains
the unreadable-Python and visible-process positive controls. No preflight,
collector, database, credential or broker operation was run; the live smoke test
calls only the process-count function. All 72 preflight tests and Ruff pass on
native Windows 3.12 after the repair.

Microsoft documents the [WMIC removal and supported alternatives](https://support.microsoft.com/en-us/servicing/os/windows/docs/2025/09/windows-management-instrumentation-command-line-wmic-removal-from-windows).
The host's absent executable was also checked directly. No Windows component was
installed and no global host setting changed.

The additional Windows 3.12 failure was a crash-test child still alive after the
existing 15-second startup allowance. All four interruption checkpoints passed
unchanged in an isolated rerun (11.05 seconds total). The timeout and failed full
cell remain retained; the deadline/assertions were not relaxed and the targeted
rerun is not substituted for a full matrix result.

## Limits and next work

This completes the locally validated process/tooling correction and prepares the Cloud start. It
does **not** implement the product executor, qualify an installed Cloud CLI or
claim actual model-family independence. Declared-family checks establish internal
consistency only; qualified route receipts must establish what actually ran.
Budget enforcement, capability isolation, event parsing, cancellation and real
adapter receipts belong to the next bounded Cloud qualification package.

All actual routes through the scoped helper are disabled. The owner-requested
Cloud program starts with installed-capability inventory and one admitted adapter
fixture, then a useful A/B research increment through existing composition.
Chiron/Recursion conformance is future PD-029 work. Model agreements, fixture
success and this platform matrix are not independent references, paper-day
observations or empirical PASS.

Validation completed before publication. After reviewing the local result, the
operator authorized commit, push and a draft PR for the Cursor Cloud handoff.
The matrix remains bound to its tested archive, not retroactively to a commit.
No merge, deployment, paid fallback, live capital movement or soak was performed.

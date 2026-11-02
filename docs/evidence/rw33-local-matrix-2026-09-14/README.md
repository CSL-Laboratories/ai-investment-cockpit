# RW-33 local four-cell matrix

All four cells completed successfully against the same frozen, uncommitted source
snapshot. This is local package validation, not empirical PASS, a release, a
paper-day observation or Cursor Cloud runtime qualification.

| Platform / Python | Passed | Skipped | Pytest command seconds | Log |
|---|---:|---:|---:|---|
| Windows 3.11.16 | 3,270 | 5 | 1780.81 | [w311](w311/pytest.log) |
| Windows 3.12.14 | 3,270 | 5 | 1793.14 | [w312](w312/pytest.log) |
| Ubuntu WSL2 3.11.16 | 3,269 | 6 | 1618.78 | [l311](l311/pytest.log) |
| Ubuntu WSL2 3.12.14 | 3,269 | 6 | 1597.68 | [l312](l312/pytest.log) |

Each cell collected 3,275 tests. Version/dependency inspection, pip check and
application Ruff passed in all four cells. The duration includes process startup
and teardown; pytest's own reported duration in the log is slightly shorter.

## Source and host

- Base commit: `f24f9a8dc9e0fda00ffc56109374ebd05fe519b1`.
- Working branch: `codex/swarm-rebase-2026-09-14`; changes were not committed.
- Source archive SHA-256: `50f6b69bc15f5bb33291da2e00ca377ba4ce7743b8d9c44fd9dcfed3358f755a`.
- The [source manifest](source-manifest.json) binds 928 files, including all
  implementation, tooling, schema, fixture and test changes.
- Host: SEHKTRZ2, Intel Core Ultra 9 185H, 16 cores / 22 logical processors,
  32 GB RAM. Native Windows and local `Cockpit-Audit-2404` Ubuntu WSL2.
- No Sentry SSH, Cursor Cloud or hosted CI execution was used for these cells.
- All 44 swarm cases ran: Windows 43 passed / 1 symlink-privilege skip; Linux
  44 passed. The Windows process-scan fix is included in every cell's source.

The [machine-readable summary](matrix-summary.json) binds cell attestations and
raw artifact hashes. Each cell folder retains the complete pytest log, dependency
inventory, runtime version, pip check, Ruff output and attestation. Full JUnit
files and the source archive remain under `C:\t\r33\candidate2`; Linux originals
remain under `/audit/r33/candidate2`. The local verifier checked full collection,
zero test failures/errors, all command exit codes, all artifact hashes, identical
source manifests, expected swarm coverage and unchanged source after each run.
Runner/verifier hashes are recorded in the summary. Recheck the complete retained
bundles with `python C:/t/r33/verify_matrix.py C:/t/r33/candidate2`.

## Expected skips and limits

Windows skips two sibling-checkout provenance comparisons and three tests that
require unavailable symlink privileges. Linux skips those two sibling-checkout
comparisons, three optional installed-CLI smoke checks in the isolated validation
PATH, and the Windows-only live process scan. The real offline Discord command
checks ran in every cell; missing-dependency fallback did not replace them.

The earlier failed attempts are described in the [RW-33 evidence note](../rw33-swarm-rebase-2026-09-14.md)
and retained separately. They are not counted as acceptance. The Windows 3.12
crash checkpoint passed here with its original 15-second allowance unchanged.
Its earlier timeout remains a recorded intermittent test limitation.

Evidence JSON and log files disable Git text normalization through the scoped
`.gitattributes`, preserving their recorded artifact hashes across checkouts.
Final evidence/status documentation was added after testing. It is not claimed
to have existed inside the frozen archive. All runtime, tool, schema, fixture and
test bytes still match that archive. Future Git checkout line-ending conversion
may change working-file hashes; this result is bound to the archive, not falsely
to a yet-uncreated commit.

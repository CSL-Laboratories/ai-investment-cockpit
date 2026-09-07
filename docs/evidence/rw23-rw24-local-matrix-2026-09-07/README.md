# RW-23 / RW-24 local validation

Attempted implementation commit: `6d7d7df73b4f480818c64a3a2b8fa8df39c6fdf9`.
Application tree: `c1c9e935b9dc99b548aefbdd2bdce6b78c617c3a`.
All four cells were configured for the same Git archive; [source-binding.json](source-binding.json)
records the independently reconstructed archive and the matching WSL copy hash.

The copied runner executes Python version, dependency freeze, `pip check`, Ruff, and
the complete `pytest -q -ra` suite with JUnit. Every cell has its own source and temp
directories; environment overrides and operator configuration are excluded. After the
suite, the runner rechecks every archived source file and hashes its evidence artifacts.
No test runs in the operator's main checkout or existing autonomous capture directories.

The full runtime profile includes Discord 2.7.1. Real command-tree construction and
the subprocess-isolated absent-dependency fallback are both required and offline.
Dependency freezes retain the exact Python-environment packages for each cell.

## Matrix

The matrix has not passed. The operator requested a pause before another repair or
test batch; [PAUSE-CHECKPOINT.md](PAUSE-CHECKPOINT.md) records the exact restart point.
The completed Python 3.11 attempts expose a replay-fixture source/hash mismatch and
a separate fractional-quantity proposal status regression. A new archive and all four
successful cells are required after their repair. Python 3.12 replacement runs were
not started before the pause.

| Completed attempt | Passed | Failed | Setup errors | Skipped | New/regained cases passed |
|---|---:|---:|---:|---:|---:|
| Native Windows / Python 3.11.16 | 3,091 | 2 | 7 | 4 | 225 |
| Ubuntu WSL2 / Python 3.11.16 | 3,089 | 2 | 7 | 6 | 225 |

Both dependency checks and Ruff passed; all 735 archived files were unchanged after
execution. Logs, JUnit, environment freezes and artifact hashes are retained under
`completed-failing-311/`; [summary.json](completed-failing-311/summary.json) explicitly
rejects acceptance. The same two test locations fail on both platforms. Seven setup
errors and one failure come from the shared replay helper; the other failure is the
fractional-quantity status assertion. Passing new cases does not waive either defect.
Windows skips two unavailable sibling-repository provenance checks and two unavailable
symlink controls; Linux skips the same two provenance checks, three checks requiring
installed model CLIs and one Windows-only WMIC scan. Exact skip explanations are
retained in each pytest log.

The first four foreground execution sessions disappeared before completion. Their partial
logs had no test-failure markers, but no completed attestation or JUnit exists; these runs
are rejected as validation and retained under `incomplete-first-attempt/`. A transient
PowerShell out-of-memory error was observed during ancillary review; the interruption's
cause is not established. Replacement runs use explicitly owned hidden processes, two
cells at a time, with the first pair under `w311r` and `l311r`. The launcher refuses to
overwrite prior runs and checks both archive hashes before starting either process.

## Evidence verification

From the repository root:

```text
python docs/evidence/rw23-rw24-local-matrix-2026-09-07/verify_evidence.py docs/evidence/rw23-rw24-local-matrix-2026-09-07
```

The verifier requires all four cells, a source manifest pinned independently to the frozen
archive, exact executable and gate arguments, successful check exit codes, complete artifact
hashes and matching XML/log test counts. It additionally requires the 219 new regression
cases and all six real Discord command-tree cases to pass without skips or duplicate records.
The required-case manifest was built from source collection before the final JUnit existed.
This verifies recorded local evidence; it is not an external witness or a live gate.
The current incomplete/failing matrix must be refused. `completed-failing-311/` retains
failed-run evidence separately and never serves as an accepted four-cell matrix.

Independent review showed the earlier verifier could accept relabelled old evidence and
substituted gate commands; [verifier-before-hardening.json](verifier-before-hardening.json)
records those synthetic negative probes, not actual new test runs. The current verifier
rejects both, plus skipped, missing and duplicate required cases. Reproduce the bounded
checks with `python .../check_verifier_hardening.py`; recorded outcomes are in
[verifier-negative-controls.json](verifier-negative-controls.json).

## Measured triage work

The [default benchmark](performance-event-verifier-default.json) and
[mixed-ledger benchmark](performance-event-verifier-mixed.json) compare a frozen old
verifier with the actual changed production file, whose hash is recorded in each result.
Both use a warmup, three timed repeats and alternating variant order.

| Signed ingest / other rows | Candidates | Old median | New median | SELECTs |
|---|---:|---:|---:|---|
| 100 / 0 | 30 | 60.004 ms | 5.286 ms | 30 → 3 |
| 100 / 0 | 100 | 160.457 ms | 4.584 ms | 100 → 3 |
| 1,000 / 0 | 30 | 950.868 ms | 83.834 ms | 30 → 3 |
| 1,000 / 0 | 100 | 2,981.647 ms | 75.060 ms | 100 → 3 |
| 1,000 / 9,000 | 100 | 2,349.050 ms | 522.739 ms | 100 → 3 |

These timings measure the binding-verification operation on synthetic SQLite fixtures,
not end-to-end triage latency. Full-chain cost grows with all ledger rows, so speedup
depends on candidate count and ledger composition. External witness I/O is excluded.
The query reduction and the refusal/recovery tests are stronger evidence than a universal
timing claim. Reproduce from `app/` with:

```text
python benchmarks/event_type_verifier.py --rounds 3 --output result.json
python benchmarks/event_type_verifier.py --rows 1000 --candidates 100 --non-ingest-rows 9000 --rounds 3 --output mixed.json
```

The WSL distro has drive automount disabled. Initial `/mnt/c` copies therefore failed
before either Linux test process started; copying through the distro's UNC share and
checking its SHA-256 supplied the same archive. Neither application source nor test
expectations changed for this setup correction.

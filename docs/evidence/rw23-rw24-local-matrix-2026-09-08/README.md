# RW-23 / RW-24 final local matrix — 2026-09-08 UTC

This replacement matrix tests implementation commit
`fa3cc1c0d8dc41af913a2d3fe6040d85d4e6e2e2`, application tree
`90470e9a491749e9fe73b12ac164f3ea54fd16c5`. It includes both audit packages and
the two integration repairs found by the previous full run.

## Results

All four cells passed. [matrix-summary.json](matrix-summary.json) records successful
evidence verification against the same source; dependency checks and Ruff also pass.

| Platform | Python | Passed | Skipped | Required cases passed |
|---|---|---:|---:|---:|
| Native Windows | 3.11.16 | 3,107 | 4 | 233 |
| Ubuntu WSL2 | 3.11.16 | 3,105 | 6 | 233 |
| Native Windows | 3.12.14 | 3,107 | 4 | 233 |
| Ubuntu WSL2 | 3.12.14 | 3,105 | 6 | 233 |

Each cell collected 3,111 cases with zero failures or errors. Windows skips two
unavailable sibling-repository provenance checks and two unavailable symlink checks.
Ubuntu skips the same provenance checks, three checks requiring installed model CLIs
and one Windows-only WMIC scan. Exact reasons and dependency versions are retained in
the per-cell logs. No required regression case was skipped.

All cells use one frozen Git archive with separate source/temp directories and no
operator configuration. Python 3.11 and 3.12 run on native Windows and local Ubuntu
WSL2, at most two cells concurrently. Every cell runs dependency validation, Ruff
and the complete pytest suite with JUnit. Source bytes and evidence hashes are
checked after execution. The existing autonomous capture checkout is unchanged.
All owned test processes have exited; the dedicated WSL distro was restored to its
original stopped state after confirming no other user jobs remained.

The required-case manifest contains 233 controls: the previous 225 new/restored
cases, one replay source-identity case, three fractional-quantity cases and four
independently authored status/risk/grounding cases. All must pass without skips.

## Repairs and review

- `d023056`: synthetic replay days change the actual detached source payload and
  hash it. The new control proves source binding, unique day identities, preserved
  caller payloads and idempotence. All 13 replay/original-news checks pass.
- `fa3cc1c`: unresolved proposal quantities retain `needs_human` even when risk
  rejects before order construction. Grounding flags remain set; risk decisions
  and order refusal remain authoritative. All 34 proposal/execution checks pass.
- Independent review added four composed cases and passed all seven selected
  quantity/status cases plus the replay identity check. Stored and signed status,
  displayed risk and the absence of an Order row agree. No blocker remained.

The earlier incomplete sessions and completed failing 3.11 attempts remain intact in
[the previous evidence folder](../rw23-rw24-local-matrix-2026-09-07/README.md).
Those attempts are rejected as acceptance; their failures are not hidden by this rerun.

## Verification

`source-binding.json` pins the source commit, full/application trees, archive and
manifest hashes. A separate Git blob walk checks the tar-derived manifest in
`independent-source-check.json`. `verify_evidence.py` requires complete successful
cells, exact gate arguments, matching source, matching XML/log counts and all 233
required cases. Its negative controls refuse relabelled old evidence, substituted
commands and skipped, missing or duplicated required cases.

From the repository root:

```text
python docs/evidence/rw23-rw24-local-matrix-2026-09-08/verify_evidence.py docs/evidence/rw23-rw24-local-matrix-2026-09-08
```

The previous [benchmark measurements](../rw23-rw24-local-matrix-2026-09-07/README.md#measured-triage-work)
still bind to the unchanged production budget module: 100 candidates require 3 SELECTs
instead of 100; the mixed 10,000-row synthetic ledger took 522.739 ms versus
2,349.050 ms (median, three timed rounds). This is an operation-level measurement;
external witness I/O is excluded. Ingest gap diagnostics are process-local.

Local platform validation does not satisfy external-witness, prospective cohort,
paper-day or live-capital gates. This branch stays within existing execution authority.

---
title: Local supported-platform matrix and DEC-09 policy — 2026-08-24
type: evidence
status: live
last-verified: 2026-08-24
related:
  - ../README
  - ../PLAN
  - ../REGISTER
  - rw11-companyfacts-capture-2026-08-18
  - rw11-prospective-text-population-2026-08-24
---

# Local supported-platform matrix and DEC-09 policy — 2026-08-24

## Operator decision

On 2026-08-24 the operator authorized `DEC-09`: current package acceptance uses a local
four-cell matrix on the operator's Windows workstation—native Windows and Ubuntu 24.04 under
WSL2, each on Python 3.11 and 3.12. macOS remains a best-effort portability target and is not a
release blocker. GitHub-hosted Actions are optional, manually dispatched diagnostics rather
than merge authority. Hetzner is explicitly outside this decision and was not tested.

This changes platform-validation policy only. It grants no strategy, paper-order, live-order,
or autonomous authority; it does not alter risk, approval, broker, gateway, watchlist, or
fail-closed behavior. Same-host WSL2 evidence is not an independent external witness and cannot
satisfy W-24 or W-25 witness requirements.

## Bound evidence

The matrix ran against clean `main` and matching `origin/main` at
`46c7c812eccd476c4578dbef59200f5249c26db3` (source tree
`e0248763bdacadf81456e5bec1e5b1f75d238959`). The raw JSON attestation remains outside the
tracked repository at:

`C:\Users\Lucas\Documents\ai-investment-cockpit-evidence\rw11-local-matrix-2026-08-24-main-46c7c812eccd\local_matrix_attestation.json`

Its SHA-256 is
`f3f99631d0fab5f9ab49f5fbee08d8e544e7a3bab8094a406081922010c59d96`.

| Environment | Python | Ruff | Pytest | Duration |
|---|---:|---:|---:|---:|
| Windows 11 | 3.11.9 | passed | 2,345 passed / 2 skipped / 4 warnings | 359.66 s |
| Windows 11 | 3.12.10 | passed | 2,345 passed / 2 skipped / 4 warnings | 412.22 s |
| Ubuntu 24.04.4 WSL2 | 3.11.16 | passed | 2,343 passed / 4 skipped / 4 warnings | 502.84 s |
| Ubuntu 24.04.4 WSL2 | 3.12.3 | passed | 2,343 passed / 4 skipped / 4 warnings | 487.43 s |

A supplemental Windows 3.11 project-environment run with `discord.py` present passed 2,350
tests / 2 skipped / 5 warnings with Ruff clean in 398.84 seconds. Every required cell used a
fresh CI dependency profile outside the tracked repository, cleared ambient `COCKPIT_*`
variables, pointed `COCKPIT_ENV_FILE` at a confirmed nonexistent path, and used `C:\t` for
Windows `TMP`/`TEMP`.

The six hosted jobs recorded in the raw artifact started zero steps because of GitHub's payment
or spending-limit policy. That remains historical accounting evidence, not a code failure and,
under `DEC-09`, not a current blocker.

## Current validated tree after PR #53

The later RW-11 text-population package ran the complete four-cell matrix on feature head
`d18dc7711f0e9324e0a5749b51fb32d1686a36a9`. Its source tree
`b51f712a803a29ad437fec9c891f8ea973ccbc1d` is identical to the tree of merged `main`
`ebda9b6691cfcfc72b76056d0190768e5f14cd66`; PR #53 introduced no content change while merging.
This binds the recorded gate results below to current `main` without pretending that
merge metadata itself was rerun or that a new independent attestation was created:

| Environment | Python | Dependency profile | Ruff | Pytest |
|---|---:|---|---:|---:|
| Windows 11 | 3.11.9 | project environment; `discord.py` present | passed | 2,373 passed / 2 skipped / 5 warnings |
| Windows 11 | 3.12.10 | fresh CI profile; `discord.py` absent | passed | 2,368 passed / 2 skipped / 4 warnings |
| Ubuntu 24.04.4 WSL2 | 3.11.16 | fresh CI profile; native Linux-only `PATH` | passed | 2,366 passed / 4 skipped / 4 warnings |
| Ubuntu 24.04.4 WSL2 | 3.12.3 | fresh CI profile; native Linux-only `PATH` | passed | 2,366 passed / 4 skipped / 4 warnings |

These later results are operator-transcribed from the completed console runs. Unlike the earlier
matrix at `46c7c812eccd476c4578dbef59200f5249c26db3`, no per-cell output logs were retained or
hash-sealed, so this record is not a new independent attestation.

Unlike the earlier uniformly fresh matrix, the later Windows 3.11 cell used the project
environment with `discord.py` present; it is compared with the earlier supplemental result using
the same profile. The other three later cells used fresh CI profiles. This distinction is recorded
rather than presented as a uniform fresh-environment rerun.

The increase from the earlier table is exactly 23 passing tests in every comparable cell, matching
the 23-test RW-11 collector suite added by PR #53. Skip patterns are unchanged. The first WSL2
Python 3.11 attempt inherited the Windows npm `codex` shim without Windows `node`; the corrected
native-Linux `PATH` rerun above passed without a code change or waived failure. The recorded test
counts, warnings, and Ruff results are in [[rw11-prospective-text-population-2026-08-24]].

## Remaining limits

- The matrix is same-host compatibility evidence, not independent review or witness evidence.
- No Hetzner deployment, Linux SecretService, real-cloud retention, or deletion/rollback behavior
  was exercised.
- RW-11 still requires meaningful numeric replay, a cited prospective material delta, and an
  external witness. Its four-row text seed scored 100% but is not a meaningful frozen population.
- RW-12, RW-13, W-22 qualifying paper days, paper opening orders, and every live/auto rung remain
  outside this package.

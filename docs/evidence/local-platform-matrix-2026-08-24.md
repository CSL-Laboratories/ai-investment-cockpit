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

## Remaining limits

- The matrix is same-host compatibility evidence, not independent review or witness evidence.
- No Hetzner deployment, Linux SecretService, real-cloud retention, or deletion/rollback behavior
  was exercised.
- RW-11 still requires meaningful numeric replay, a cited prospective material delta, and an
  external witness. Its four-row text seed scored 100% but is not a meaningful frozen population.
- RW-12, RW-13, W-22 qualifying paper days, paper opening orders, and every live/auto rung remain
  outside this package.

---
title: CSL-221 unresolved blockers — qualification evidence
type: evidence
status: reference
last-verified: 2026-10-17
issue: CSL-221
---

# CSL-221 unresolved blockers

These items block a release claim. They do not block publishing the evidence
protocol. Nothing in this list is decided by a green CI job, an issue status,
or elapsed time. Lucas owns release scope.

| ID | Blocker | Owner | Why it stays open |
|---|---|---|---|
| B1 | Release decision and release packet | Lucas | CSL-221 here is evidence only. No packet is assembled and no scope is chosen. |
| B2 | Packaging, install, update, rollback, and recovery | Lucas | Those deliverables are outside this evidence slice. No installer or rollback procedure is added. |
| B3 | Whether hosted Blacksmith cells ever replace the DEC-09 workstation matrix | Lucas | DEC-09 remains native Windows plus Ubuntu WSL2 on Python 3.11 and 3.12. This protocol does not amend it. |
| B4 | `replay` tier | Lucas | The cell exists and is excluded. It was not executed and is not pass. |
| B5 | `simulation` tier | Lucas | The cell exists and is excluded. It was not executed and is not pass. |
| B6 | `paper-demo` tier | Lucas | The cell exists and is excluded. It was not executed and is not pass. |
| B7 | Shadow and limited-live qualification | Lucas | O9 names them. This slice does not define or run them. |
| B8 | Independent non-implementer rerun | A verifier who did not write this change | The outcome contract requires a rerun of the exact candidate, command, and negative controls. Implementer CI is not that rerun. |
| B9 | Artifact retention | Operator | GitHub retains Actions artifacts for at most 90 days. The run's hashes are the CI-time binding. They are not a permanent archive. |
| B10 | Full application suite | Lucas | Historical DEC-09 runs include skipped tests, which this protocol refuses to call pass. The hosted suite is the provider-free evidence protocol, not the whole application suite. |
| B11 | macOS in any release scope | Lucas | A hosted macOS cell is collected so the platform axis is not silent. That collection is not a decision that macOS is supported. |
| B12 | Hetzner | — | DEC-09 leaves Hetzner outside package validation. It is not a cell here. |
| B13 | Dependency lock | Lucas | Each cell records the digest of the interpreter environment that ran. The suite does not install from a hash-locked application lockfile. |
| B14 | App `macos` job stays on GitHub-hosted `macos-latest` | Lucas | The qualification macOS cells use `blacksmith-6vcpu-macos-15`. The existing application-suite macOS job stays on the best-effort GitHub-hosted runner. Moving it is a cost decision, outside this evidence slice. |
| B15 | `linux-gates` and `filter` stay on GitHub-hosted `ubuntu-latest` | Lucas | Those jobs are the required-check aggregator and the path filter. This slice does not move them onto Blacksmith. |

Registry evidence for O9 stays `researched`. This document does not set
`provider_free_verified`, `release_qualified`, or any live-authority claim.

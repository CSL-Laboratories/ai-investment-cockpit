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
or elapsed time. The release owner owns release scope.

| ID | Blocker | Owner | Why it stays open |
|---|---|---|---|
| B1 | Release decision and release packet | release owner | CSL-221 here is evidence only. No packet is assembled and no scope is chosen. |
| B2 | Packaging, install, update, rollback, and recovery | release owner | Those deliverables are outside this evidence slice. No installer or rollback procedure is added. |
| B3 | Whether hosted Blacksmith cells ever replace the DEC-09 workstation matrix | release owner | DEC-09 remains native Windows plus Ubuntu WSL2 on Python 3.11 and 3.12. This protocol does not amend it. |
| B4 | `replay` tier | release owner | The cell exists and is excluded. It was not executed and is not pass. |
| B5 | `simulation` tier | release owner | The cell exists and is excluded. It was not executed and is not pass. |
| B6 | `paper-demo` tier | release owner | The cell exists and is excluded. It was not executed and is not pass. |
| B7 | Shadow and limited-live qualification | release owner | O9 names them. This slice does not define or run them. |
| B8 | Independent non-implementer rerun | A verifier who did not write this change | The outcome contract requires a rerun of the exact candidate, command, and negative controls. Implementer CI is not that rerun. |
| B9 | Artifact retention | Operator | GitHub retains Actions artifacts for at most 90 days. The run's hashes are the CI-time binding. They are not a permanent archive. |
| B10 | Full application suite | release owner | Historical DEC-09 runs include skipped tests, which this protocol refuses to call pass. The hosted suite is the provider-free evidence protocol, not the whole application suite. |
| B11 | macOS in any release scope | release owner | A hosted macOS cell is collected so the platform axis is not silent. That collection is not a decision that macOS is supported. |
| B12 | Hetzner | — | DEC-09 leaves Hetzner outside package validation. It is not a cell here. |
| B13 | Dependency lock | release owner | Each cell records the digest of the interpreter environment that ran. The suite does not install from a hash-locked application lockfile. |
| B14 | App `macos` job stays on GitHub-hosted `macos-latest` | release owner | The qualification macOS cells use `blacksmith-6vcpu-macos-15`. The existing application-suite macOS job stays on the best-effort GitHub-hosted runner. Moving it is a cost decision, outside this evidence slice. |
| B15 | `linux-gates` and `filter` stay on GitHub-hosted `ubuntu-latest` | release owner | Those jobs are the required-check aggregator and the path filter. This slice does not move them onto Blacksmith. |
| B16 | Qualification macOS spend on eligible `ci:full` and full dispatch | release owner | Same-repository/non-Dependabot admission and no automatic nightly expansion are approved. The two Blacksmith macOS cells still need separate pricing and a coordinated qualification request; the public-repository free GitHub macOS assumption does not apply. The core $60–75 plan / $100 monthly target is not a verified bill or enforced quota and does not include this extra qualification. |
| B17 | Partial qualification rerun | — | Artifact names include the run attempt. A rerun of only the failed jobs uploads that new attempt, and validation downloads only that attempt. Fewer than six cells is `missing-cell`. A complete attempt needs all six cells rerun. On a full run, `qualification-validate` starts unless the matrix job itself was skipped, and it fails when any expected cell is missing, cancelled, or skipped. |
| B18 | Artifact attestation org policy | release owner | The validate job requests `id-token: write`, `contents: read`, and `attestations: write`, then runs `actions/attest-build-provenance@v3` on the bundle and its SHA-256 manifest. A public repository on a current GitHub plan uses the public-good Sigstore instance for that signature. Attestations are not available on legacy plans, and private or internal repositories need GitHub Enterprise Cloud. If this organization disables OIDC minting or artifact attestations, the attest step fails, and enabling it is an organization-admin decision. The workflow still writes `manifest.json` with the SHA-256 of `bundle.json` before that step. `actions: write` is not granted: that scope can cancel a workflow run. |

Registry evidence for O9 stays `researched`. This document does not set
`provider_free_verified`, `release_qualified`, or any live-authority claim.

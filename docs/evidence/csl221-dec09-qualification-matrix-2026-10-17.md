---
title: CSL-221 qualification-cell matrix — 2026-10-17
type: evidence
status: reference
last-verified: 2026-10-17
issue: CSL-221
---

# CSL-221 qualification-cell matrix

This note reconciles the historical DEC-09 corpus into the replacement
qualification matrix for CSL-221. It is evidence. It does not amend DEC-09,
does not accept a package, and does not record a release decision. Release
scope stays with Lucas. A green hosted cell is not `provider_free_verified`
in the replacement registry and is not outcome completion.

The machine-readable matrix is
[qualification-matrix.json](csl221/qualification-matrix.json). The evidence
record schema is
[qualification-evidence.v1.schema.json](csl221/qualification-evidence.v1.schema.json).
`tools/qualification_evidence.py` is the validator. The checked-in matrix
records every cell as `not_run`. Only a bundle produced for one commit and
tree can carry a technical `pass`, and only for a required cell whose JUnit
inventory has zero failures, errors, and skips.

## What DEC-09 was

DEC-09, resolved 2026-08-24, makes package acceptance the operator
workstation's native Windows plus Ubuntu 24.04 under WSL2, each on Python
3.11 and 3.12. macOS is best-effort. Hosted Linux `linux-gates` is a required
pull-request diagnostic and does not replace that matrix. Hosted Windows and
macOS are not ordinary pull-request checks. Hetzner is outside the decision.
The same policy is restated in `docs/PLAN.md`, `docs/REGISTER.md`, and
`docs/evidence/local-platform-matrix-2026-08-24.md`.

Those historical cells are not cells of this candidate. Several of them
recorded skipped tests. Under this protocol a skipped, unknown, or excluded
cell is not pass, so those older results are not promoted.

| Record | Commit | Tree | What it measured | Replacement status |
|---|---|---|---|---|
| [local-platform-matrix-2026-08-24](local-platform-matrix-2026-08-24.md) | `46c7c812eccd476c4578dbef59200f5249c26db3` | `e0248763bdacadf81456e5bec1e5b1f75d238959` | Native Windows 3.11.9 / 3.12.10 and Ubuntu WSL2 3.11.16 / 3.12.3, with skips | historical, not this candidate |
| Same note, PR #53 tree | `ebda9b6691cfcfc72b76056d0190768e5f14cd66` | `b51f712a803a29ad437fec9c891f8ea973ccbc1d` | Head `d18dc7711f0e9324e0a5749b51fb32d1686a36a9` shares the tree. Counts were operator-transcribed | historical, not a new attestation |
| [rw20-matrix-isolation-2026-09-06](rw20-matrix-isolation-2026-09-06.md) | `69a1c9c53c0beeb0f91310ee017b24ba21a592b7` | `13979fca3e96fc64b31a17044b3c56c93b937fd8` | Four local cells, 2,648/5 and 2,646/7, artifact hashes checked | historical; skips are not pass here |
| [astra-dec09-matrix-gaps-2026-09-06](astra-dec09-matrix-gaps-2026-09-06.md) | `c5ef0ad3d9acd3a85aa86228491ea1827bfd529e` | `62d8645e3e8a4c844f63e31b0614767b54e67b75` | Gap audit. App subtree `5125e7edd2489c027364bbbfbd4556d928109817`. No new four-cell run | historical |
| RW-22 register row | `cacaff4f88753227a2d59a4bc51c82e39bfb5eef` | `2207c2cc83783f82701e7b39669491c9a5cb5eaa` | Windows 2,875/5 and WSL2 2,873/7 | historical |
| [rw23-rw24 matrix](rw23-rw24-local-matrix-2026-09-08/matrix-summary.json) | `fa3cc1c0d8dc41af913a2d3fe6040d85d4e6e2e2` | `cb8cefaa98913bc8efd77e4acb4750b572d095bc` | Windows 3,107/4 and WSL2 3,105/6 | historical |

## Replacement cells

Axes are platform, CPython, and tier. Tiers are `provider-free`, `replay`,
`simulation`, and `paper-demo`. The product of three hosted platforms, Python
3.11 and 3.12, and four tiers is 24 cells.

| Platform | Runner label | OS | Arch | Python | Tier | Requirement | Owner |
|---|---|---|---|---|---|---|---|
| linux | `blacksmith-2vcpu-ubuntu-2404` | Linux | X64 | 3.11, 3.12 | provider-free | required | CSL-221 qualification evidence |
| windows | `blacksmith-2vcpu-windows-2025` | Windows | X64 | 3.11, 3.12 | provider-free | required | CSL-221 qualification evidence |
| macos | `blacksmith-6vcpu-macos-15` | macOS | ARM64 | 3.11, 3.12 | provider-free | required | CSL-221 qualification evidence |
| linux, windows, macos | same labels | same | same | 3.11, 3.12 | replay, simulation, paper-demo | excluded | release owner |

Required evidence for a provider-free cell is the exact-candidate bundle:
commit SHA, Git tree SHA, workflow run id, numeric job id, the scheduled
runner label, the image and interpreter observed inside the job
(`ImageOS`, `ImageVersion`, `RUNNER_OS`, `RUNNER_ARCH`, `RUNNER_NAME`,
`platform.platform()`, and `sys.version`), CPython version, canonical
dependency digest, JUnit inventory, and SHA-256s of `junit.xml`,
`dependencies.txt`, and `runner.json`. The scheduled label is not a
substitute for those observed fields. The hosted suite is
`tools/test_qualification_evidence.py`. It does not call a broker, a model
provider, or a market-data vendor. The hosted six-cell matrix runs only when
Linux, Windows, and macOS are all authorized: a pull request with `ci:full`,
or a full workflow dispatch. An ordinary pull request, a `scope=linux`
dispatch, the nightly schedule, and an ordinary push to `main` do not start
it. A partial matrix is not a pass. On a full run, validation starts
unless the qualification job itself was skipped, and it fails when any
expected cell is missing, cancelled, or skipped. After a complete
bundle validates, the workflow writes `manifest.json` with the SHA-256
of `bundle.json` and attests that directory with
`actions/attest-build-provenance@v3`. Whether the organization allows
that attestation is blocker B18.

Excluded tiers are defined so they cannot be reported as pass. Hetzner is not
a cell. Native Windows and Ubuntu WSL2 remain DEC-09's package-acceptance
hosts; this protocol does not re-run them and does not mark them pass.

The pull-request checkout for these jobs is
`github.event.pull_request.head.sha`. On `pull_request`, `github.sha` is the
merge commit, so a bundle bound to `github.sha` would describe a different
tree whenever the base has moved.

## What a technical pass means

`tools/qualification_evidence.py validate` exits 0 only when every required
cell is present once, each status is `pass`, each commit and tree equals the
candidate, and each JUnit file is present and free of failures, errors, and
skips. Missing cells, skipped cells, unknown status, excluded status, a wrong
SHA, a wrong tree, and a missing JUnit file are rejections. The negative
controls in `.github/workflows/ci.yml` must show those rejections.

That exit code does not choose a release, does not package an installer, and
does not move the replacement outcome out of `pending_outcome_artifact`.

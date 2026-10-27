# CSL-392 docs-read audit

Repository: `CSL-Laboratories/csl-research`
Commit: `23f70c0fcdacba90f9ec4da3e135ce225b5a6908`
Path: `submissions/20261027-csl392-docs-read-audit-01/`
Commit URL: https://github.com/CSL-Laboratories/csl-research/commit/23f70c0fcdacba90f9ec4da3e135ce225b5a6908
Pull request: https://github.com/CSL-Laboratories/csl-research/pull/61

Checked 2026-10-27: the CSL-392 pr-sync for csl-research #61 (updated
2026-10-27T00:59:41Z) still lists that pull request as open at this commit.
This pin is that commit, not a merge commit. This session cannot read the
private repository directly.

Candidate-only until a founder accepts the submission. Claim, as published
on that pr-sync: at Cockpit `main` `139f255599408a553631d40ac8984bd3856a2fa6`,
the required linux job has one docs-filter mismatch, `docs/ci-runbook.md`,
read by `tools/test_ci_dispatch_scope.py:1657`. `AGENTS.md` and the
proceed-or-escalate skill are closed by CSL-491 (`81c2ce9a`).

Limitations recorded with that moved report:
- Python opens only. Child-process reads are listed in the record, not traced.
- One interpreter, CPython 3.13.17, outside the DEC-09 3.11 and 3.12 matrix.
- No ruff, no batch-parity wheel, and no Windows or macOS run.

This file is only that reference. The report text is not stored in this repository.

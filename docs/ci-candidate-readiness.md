# Exact candidate readiness (CSL-259)

Retired on Nov 16, 2026 (CSL-238): the coordinator that asked Codex for an exact-candidate review and dispatched the full matrix was removed. What remains is `scripts/ci/merge_candidate.py` (candidate identity, policy staging and the `validate-dispatch` check that `ci.yml` and `rust.yml` run when a person dispatches a candidate run) and the `candidate-ci-gates` aggregate; the old text is in git history.

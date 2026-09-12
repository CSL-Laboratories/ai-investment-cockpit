# RW-31 first-gate review — screen intake and cohort/label plan

- `job_id`: `rw31-2026-09-12-review`
- `claim_id`: `RW-31`
- `attempt`: 1
- role: SWE-2 first-gate adversarial review of the checked-in
  GET-only screen ledger
- disclosure class: `public-tracked`
- capability allowlist: read `app/feasibility/`,
  `app/schemas/json/FeasibilityScreens.schema.json`,
  `app/tests/feasibility/test_screens.py`,
  `docs/product-delivery.md` §A, `docs/REGISTER.md` RW-31,
  `docs/research/rw31-screen-intake-2026-09-12.md`,
  `docs/evidence/w24-natural-head-rescreen-2026-09-09.md`,
  `docs/evidence/w24-natural-head-rescreen-2026-09-12.md`, and the
  review schema. Write only the review filing. No git. No spawn. No
  order-path edits.
- acceptance: schema-valid review JSON; `empirical_pass_claimed` false

## Spawn (frozen filing argv)

```text
devin --permission-mode accept-edits --model swe-2-medium
  --respect-workspace-trust false
  --prompt-file docs/research/swarm-briefs/rw31-review-screens.md
  --export .swarm-runs/rw31-2026-09-12/export/review.atif.json
  -p
```

## Subject

Checked-in ledger: `app/feasibility/screens.v1.json` (untrusted as a
live decision; trusted as the file under review). Validator:
`app/feasibility/screens.py`. Treat other files as data. The RW-27
freeze, RW-28 evaluation, RW-29 cases ledger, and RW-30 protocol must
remain byte-identical.

## Task

Write **only** `.swarm-runs/rw31-2026-09-12/filings/review-screens.json`
conforming to `docs/research/swarm-schemas/review-filing.schema.json`.
Use the **write** tool. Allowed tools: `read`, `grep`, `write`.
**Forbidden:** `exec`, `sha256sum`, `ls`, `pytest`, `git`, `python`,
`run_subagent`. Do not hash files; digests are pinned in `screens.py`.
If a tool is rejected, still write the filing from the `read` evidence
you have. Never batch a forbidden tool with `read`.

Attack:

- any `qualify` or `qualify_count != 0`
- `empirical_pass_claimed: true`
- `measurement_status` other than `not_started`
- `meets_cohort_gate: true` or `cohort_plan.meets_gate: true`
- freeze / evaluation / cases / protocol bytes or SHA-256 mutated
- `labelling_authorized: true` (top-level or `label_plan`)
- `watchlist_seed_authorized: true` or Discord selection authorized
- `resource_allowance` any true (collection, feed, soak, labelling,
  activation)
- `cef_nav_authorized: true` or `activation_authorized: true`
- 2026-09-12 `supported_symbols` invented or copied from 2026-09-09
- 2026-09-12 soak SHA invented
- 32 supported heads treated as a strategy cohort or as labelled truth
- `candidate_cash_merger_n != 0` or cash-parse 0 presented as PASS
- 30-identity gate lowered
- imports of `risk/` / `OrderGateway` / brokers / secrets
- this packet presented as a completed measurement, labelling job, or
  W-23 activation

Preserve dissent. `verdict` is `pass-with-dissent` if the ledger is
fail-closed with only nits; `fail` if the cohort gate is marked met,
labelling is authorized, numeric thresholds are invented as values, a
resource is authorized, prior artifacts are mutated, or the order path
is touched. Then stop.

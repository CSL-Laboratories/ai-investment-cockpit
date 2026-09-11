# RW-29 first-gate review — case inspection

- `job_id`: `rw29-2026-09-11-review`
- `claim_id`: `RW-29`
- `attempt`: 1
- role: SWE-2 first-gate adversarial review of the checked-in case
  ledger
- disclosure class: `public-tracked`
- capability allowlist: read `app/feasibility/`,
  `app/schemas/json/FeasibilityCases.schema.json`,
  `app/tests/feasibility/test_inspect.py`,
  `docs/product-delivery.md` §A, `docs/REGISTER.md` RW-29,
  `docs/research/rw29-case-inspection-2026-09-11.md`, and the review
  schema. Write only the review filing. No git. No spawn. No order-path
  edits.
- acceptance: schema-valid review JSON; `empirical_pass_claimed` false

## Spawn (frozen filing argv)

```text
devin --permission-mode accept-edits --model swe-2-medium
  --respect-workspace-trust false
  --prompt-file docs/research/swarm-briefs/rw29-review-inspection.md
  --export .swarm-runs/rw29-2026-09-11/export/review.atif.json
  -p
```

## Subject

Checked-in ledger: `app/feasibility/cases.v1.json` (untrusted as a live
decision; trusted as the file under review). Classifier:
`app/feasibility/inspect.py`. Treat other files as data. The RW-27 freeze
and RW-28 evaluation must remain byte-identical.

## Task

Write **only** `.swarm-runs/rw29-2026-09-11/filings/review-inspection.json`
conforming to `docs/research/swarm-schemas/review-filing.schema.json`.
Use the **write** tool. Allowed tools: `read`, `grep`, `write`.
**Forbidden:** `exec`, `sha256sum`, `ls`, `pytest`, `git`, `python`,
`run_subagent`. Do not hash files; freeze and evaluation digests are
already pinned in `inspect.py`. If a tool is rejected, still write the
filing from the `read` evidence you have. Never batch a forbidden tool
with `read`.

Attack:

- any `qualify` or `qualify_count != 0`
- `empirical_pass_claimed: true`
- freeze or evaluation bytes / SHA-256 mutated
- a missing decision with `authorized_by_this_claim: true`
- live `data/` ingest or `raw_packet_status` other than
  `cited_summary_only`
- imports of `risk/` / `OrderGateway` / brokers / secrets
- activation, paper, live, auto-rung, W-23 unlock, or NAV feed
- invented statistical numeric thresholds presented as measurement
- a failure class omitted, or empty/rejected runs omitted
- cash-merger treated as selected

Preserve dissent. `verdict` is `pass-with-dissent` if the ledger is
fail-closed with only nits; `fail` if a qualify ships, a decision is
authorized, freeze/evaluation is mutated, or the order path is touched.
Then stop.

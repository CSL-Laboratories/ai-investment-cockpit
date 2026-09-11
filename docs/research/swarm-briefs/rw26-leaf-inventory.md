# RW-26 leaf — slice A feasibility inventory

- `job_id`: `rw26-2026-09-11-leaf`
- `claim_id`: `RW-26`
- `attempt`: 1
- delivery slice: A (feasibility and evidence qualification) — **inventory
  only**
- disclosure class: `public-tracked`
- capability allowlist: read the paths listed below; write only the filing
  path. No git. No network beyond Devin inference. No broker tools. No spawn.
  No edits under `app/risk/`, `app/brokers/`, `app/orchestrator/`, or
  `app/storage/audit.py`.
- acceptance target: schema-valid leaf JSON; `empirical_pass_claimed` must
  be `false`
- stop conditions: refuse if asked to qualify/activate a strategy, touch
  the order path, or claim a gate PASS

## Spawn (frozen filing argv)

```text
devin --permission-mode accept-edits --model swe-2-medium
  --respect-workspace-trust false
  --prompt-file docs/research/swarm-briefs/rw26-leaf-inventory.md
  --export .swarm-runs/rw26-2026-09-11/export/leaf.atif.json
  -p
```

## Read (these, not a repo dump)

- `docs/product-delivery.md` section A
- `docs/REGISTER.md` row `RW-26` and rows `W-23` / `W-24`
- `docs/research/dec03-strategy-selection-2026-08-14.md` (headings +
  selected/rejected names only)
- `docs/runbooks/w23-cash-merger-edge.md` (purpose + non-claims)
- `docs/evidence/w23-cash-merger-edge-2026-08-14.md` headings
- `docs/runbooks/w24-merger-semantic-verifier.md` headings if present
- `app/quant/strategies/` module names only

## Task

Write **only** `.swarm-runs/rw26-2026-09-11/filings/leaf-inventory.json`
conforming to `docs/research/swarm-schemas/leaf-filing.schema.json`.

`result.findings` must be path citations:

- cash-merger / W-23 / W-24 evidence and runbook paths and what they
  explicitly do **not** claim (activation, live, PASS)
- in-repo strategy modules under `app/quant/strategies/`
- a **proposed** comparison shortlist: cash-merger plus at most five names
  already present in DEC-03 or those modules. Mark it `proposed`, not
  selected.

Set `status` to `success` or `refusal`. Set
`empirical_pass_claimed` to `false`. Set `route_model` to `swe-2-medium`.
Set `evidence_refs` to the paths you actually read.

Then stop. Do not commit, push, or spawn another agent.

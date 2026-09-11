# Swarm smoke leaf — nine invariants inventory

- `job_id`: `swarm-smoke-2026-09-11-leaf`
- `claim_id`: `SMOKE-SWARM-PIPELINE`
- `attempt`: 2
- delivery slice: process smoke only (not slice A–F implementation)
- disclosure class: `public-tracked`
- capability allowlist: read `CLAUDE.md` (invariants section only) and write
  the filing path below. No other writes. No git. No network beyond Devin
  inference. No broker tools. No spawn.
- deadline: this Cloud Agent run
- reservation: one SWE-2 medium session
- acceptance target: schema-valid leaf JSON; `empirical_pass_claimed` must
  be `false`
- stop conditions: refuse if asked to touch `app/risk/`, `OrderGateway`,
  secrets, or to claim a gate PASS
- forbidden actions: `-r`/`--resume`/`--continue`; `dangerous`/`bypass`/`yolo`;
  merge; push; editing tracked files other than the filing path

## Spawn (frozen)

```text
devin --sandbox --permission-mode accept-edits --model swe-2-medium
  --respect-workspace-trust false
  --prompt-file docs/research/swarm-briefs/smoke-leaf-invariants.md
  --export .swarm-runs/smoke-2026-09-11/export/leaf.atif.json
  -p
```

## Task

Write **only** `.swarm-runs/smoke-2026-09-11/filings/leaf-invariants.json`
conforming to `docs/research/swarm-schemas/leaf-filing.schema.json`.

`result.findings` must list the nine cockpit invariants **by number and
short name** from `CLAUDE.md`. Do not paste the whole file. Do not invent
a tenth. Set `status` to `success` or `refusal`. Set
`empirical_pass_claimed` to `false`. Set `route_model` to `swe-2-medium`.

Then stop. Do not commit, push, or spawn another agent.

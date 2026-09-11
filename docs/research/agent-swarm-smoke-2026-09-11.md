---
title: Cloud swarm pipeline smoke — SWE-2 / Muse / Head
status: reference
last-verified: 2026-09-11
amends:
  - research/agent-swarm-pipeline-2026-09-11
---

# Cloud swarm pipeline smoke (2026-09-11)

**Baseline:** spawn pin `0539ad6` on `cursor/cloud-devin-swarm-host-df89`.
**Host:** Cursor Cloud Agent Linux VM. Devin CLI **3000.10.21**. Model pin
**SWE-2 Medium**. Muse **Spark 1.3 medium**. Head **Grok 4.6 xhigh**.
**Scope:** process smoke of the frozen pipeline. Not slice A–F. Not a
REGISTER production claim. **No empirical PASS.**

Filings and ATIFs are gitignored under `.swarm-runs/smoke-2026-09-11/`.
This note exports hashes only.

## Frozen filing argv (what actually wrote)

```text
devin --permission-mode accept-edits --model swe-2-medium
  --respect-workspace-trust false
  --prompt-file <checked-in brief>
  --export <run-scoped ATIF>
  -p
```

`--sandbox` is **not** on this argv. `dangerous` / `smart` / `autonomous`
remain forbidden.

## Spawn measurements

| Attempt | Argv | Result |
|---|---|---|
| 1 | `--sandbox --permission-mode autonomous` | `write` rejected in print mode (`Tool execution was rejected by the user`). Exit 0. Empty filings dir. ATIF `1bbecf88edf9408b8e0373417dc1a7a118874188b690d5746992af6c36a77beb`. |
| 2 | `--sandbox --permission-mode accept-edits` | CLI: `--sandbox always uses the autonomous permission mode; ignoring --permission-mode accept-edits`. `exec` ran (`ls`). `write` still rejected. ATIF `a98f5ea55af0c6c6fce0cd2476cf907352154ade1914cac26bc01c304ea5edfc`. |
| 3 (leaf) | `accept-edits`, no `--sandbox` | `write` succeeded. No `exec`. Schema-valid leaf. ATIF `4091106da84a501f1ed8e0270819ab70124b90f42d245307ec02e7fc11f32847`. 50221 prompt / 930 completion / 11 steps. |
| review | same filing argv | `write` succeeded. Later `exec` **rejected** (print mode + accept-edits does not auto-approve shell). ATIF `7168d4ca7e0fac5b47b21d42267476faea630524d24dd497e6144ba76a9e3d84`. 52054 prompt / 1155 completion / 11 steps. |

Working tree stayed clean (filings under `.swarm-runs/`).

## Intake hashes

jsonschema intake: `docs/research/swarm-tools/validate_filings.py`. All three
filings validated. `empirical_pass_claimed` is false on every filing.

| Kind | Path (run-scoped) | SHA-256 |
|---|---|---|
| leaf | `filings/leaf-invariants.json` | `8c49068b693d51799404796ff37eb0d518de7efcc26992b27b713c40ff885f52` |
| review | `filings/review-invariants.json` | `1e406cf883a7d31f4e9ed847053f36666bc4c9195c446c8b61e7c3189eca6676` |
| pre-Muse manifest (leaf+review) | `manifest-pre-muse.json` | `1f2133d9404c7536d63a8aed7884db262b96f5fe30041e7c68ff87f59805b15f` |
| synthesis | `filings/synthesis.json` | `57ff7acd18db000c439190c0f14d9fbcdcd06c6353c838e47afc9280acd89e8a` |

Muse receipt matched the synthesis hash. Review verdict:
`pass-with-dissent`. Leaf listed INV-01..INV-09. Head spot-check: those
nine claims match `CLAUDE.md` numbering. The leaf ATIF shows a schema
`read` and a `write`, not a `CLAUDE.md` `read`; AGENTS.md is always-on.
Process nit, not a missing invariant.

Unit tests (no Devin spawn): `app/tests/swarm/test_swarm_tools.py` — 7
passed.

## Muse nuances — Head steer

Muse Spark 1.3 medium (`route_model` on the synthesis file) raised two
nuances. Head disposition:

1. **REV-03** (attempt 3 vs review attempt 1). **Accept as bookkeeping.**
   Attempts 1–2 produced no filing (spawn-profile failures). Attempt 3 is
   the first successful write. Reviewer attempt 1 is correct. Do not
   rewrite the leaf.
2. **REV-04** (Inv 4 omits OS store names). **Accept as a naming nit.**
   Canonical text remains `CLAUDE.md`. Do not treat as a missing
   invariant.

Head-owned, not invented by Muse (not in the filings):

- CLI 3000.10.21 cannot combine `--sandbox` with unattended `write` unless
  `dangerous` is used. **Do not take `dangerous`.** Filing leaves stay on
  `accept-edits` without `--sandbox`. Print-mode `exec` was rejected on
  the reviewer. Jobs that must `exec` need a separately reviewed profile.
- Intake currently overwrites `manifest.json` when synthesis is added.
  Freeze a copy (here `manifest-pre-muse.json`) before Muse.

None of this is a passed gate, a live unlock, or permission to skip a
REGISTER claim.

## What this smoke does not do

- No slice A–F implementation and no order-path edit.
- No 30–40-way fan-out. First measured run was 1 leaf + 1 first-gate
  reviewer + 1 Muse synthesizer, as the 2026-09-10 pilot shape required.
- No market/live/paper soak. No broker secrets.
- OpenCode remains unnamed.

## Liftoff next

PLAN: begin the next implementation work with a **bounded REGISTER claim
from slice A** (feasibility and evidence qualification). That is a
separate package and a separate PR. This architecture PR stays process
evidence only. Lucas merges.

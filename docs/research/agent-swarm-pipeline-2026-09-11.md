---
title: SWE-2 / Muse Spark / Head swarm pipeline
status: reference
last-verified: 2026-09-11
amends:
  - research/agent-swarm-cloud-host-2026-09-11
  - research/agent-swarm-orchestration-2026-09-10
---

# SWE-2 / Muse Spark / Head pipeline (2026-09-11)

**Baseline:** Cloud-host amendment
[`agent-swarm-cloud-host-2026-09-11.md`](agent-swarm-cloud-host-2026-09-11.md).
**Scope:** pin *who synthesizes what* on the Cursor Cloud Agent. It does not
change PRODUCT, PLAN order, the nine invariants, or any empirical gate. Lucas
is the final reviewer and merger. Head may open PRs. This note claims no PASS.

## Owner answers (same thread, 2026-09-11)

1. Head is the Cursor Cloud Agent on **Grok 4.6 xhigh**.
2. Volume research, claimed-package development, and first-gate adversarial
   review run on **Devin SWE-2** (owner-stated free credits).
3. Second-gate synthesis is **Muse Spark 1.3** (low / medium / high), a
   different model family from SWE-2. Composer is not the synthesizer.
4. GPT-5.6 Luna is not on this Cloud Agent Task allowlist; do not pull it in.
5. Lucas reviews and merges. Head steers Muse when it raises nuances.

## Pipeline

```text
Head  Grok 4.6 xhigh (this Cloud Agent)
  owns REGISTER claims, brief paths, frozen manifests, stop/PR
  never inherit-Task for bulk; never Read leaf dumps
      │
      ├─ SWE-2 leaves          research / claimed-package edits
      ├─ SWE-2 reviewers       first-gate attack→fix→regression filings
      │                       (same family — useful, not independent)
      │
      ├─ jsonschema + canary   deterministic; not a model
      │
      └─ Muse Spark 1.3       second-gate synthesis (other family)
            writes one capped synthesis file
            replies to Head with a receipt only
            Head spot-checks ≥1 cited filing per domain, then opens a PR
```

SWE-2 reviewing SWE-2 shares blind spots. Muse is the family break (Meta vs
Cognition). Head still spot-checks. None of the three is a passed gate.

## Muse effort

| Effort | Use |
|---|---|
| `muse-spark-1.3-low` | Index only: coverage map, leaf IDs, missing hashes. Not commit-raising. |
| `muse-spark-1.3-medium` | Default synthesizer. Citations, dissent, unverified remainder, “no empirical PASS.” |
| `muse-spark-1.3-high` | First-gate SWE-2 reviewers disagree, or the package is order-path-adjacent. |

Do not use Muse max unless a high run still cannot preserve dissent. Do not
use Composer to raise filings into committed evidence. Cursor Task
`inherit` is forbidden for bulk work (that would clone Grok 4.6 xhigh).

## Hard rules

- `jsonschema` + canary run **before** Muse. Muse never repairs a failing
  leaf by inventing values. Quarantine instead.
- Muse reads the frozen manifest (paths + SHA-256) and cited filings only,
  wrapped as `<untrusted-content>`.
- Muse writes one synthesis file. Its reply to Head is a receipt: path,
  hash, leaf IDs, dissent count, coverage, unverified remainder. Bodies stay
  on disk.
- Head Read()s the synthesis (capped) and at least one cited filing per
  domain. Head does not paste leaf JSON into chat.
- Order-path packages still need `docs/ultracode-build-playbook.md`. SWE-2
  + Muse is process evidence, not a waiver.
- Merge stays with Lucas. Head opens draft PRs; Head does not merge.

## Spawn (frozen)

Devin leaf / first-gate reviewer:

```text
devin --permission-mode accept-edits --model swe-2-medium
  --respect-workspace-trust false
  --prompt-file <checked-in brief>
  --export <run-scoped export path>
  -p
```

`--prompt-file` cannot be combined with an extra prompt after `--` on Devin
CLI 3000.10.21 (the CLI errors). Print mode plus `--prompt-file` is the
frozen form. The brief file *is* the prompt.

Measured 2026-09-11 on CLI 3000.10.21:

- `--permission-mode autonomous` (and `--sandbox`, which *forces*
  `autonomous` and ignores `accept-edits`) still prompts for write/edit
  in print mode (`Tool execution was rejected by the user`). `exec`
  does run. The leaf exits 0 with an empty filings dir.
- Current CLI help lists `auto | accept-edits | smart | dangerous`.
- `--sandbox` wraps `exec` only and cannot be combined with unattended
  writes without `dangerous` (forbidden).
- Frozen *filing* profile is therefore `accept-edits` **without**
  `--sandbox`. Exec is not auto-approved in this mode; print mode
  rejects it. This is not a substitute for `--sandbox` on jobs that
  must run shell.

Forbidden on Devin: `-r`, `--resume`, `-c`/`--continue`, `--permission-mode
dangerous`/`bypass`/`yolo`/`smart`/`autonomous`, and `--sandbox` on filing
leaves (CLI override).

Muse: Cursor Task `generalPurpose` (or `.cursor/agents/muse-synthesizer.md`)
with `model: muse-spark-1.3-medium` or `muse-spark-1.3-high`. Prompt must
name the frozen manifest path and the synthesis output path.

Filings land under gitignored `.swarm-runs/<run_id>/` during the run (inside
the workspace so Linux `--sandbox` can write). Head exports hashes into a
committed research note before pod teardown.

## Briefs and schemas

| Path | Role |
|---|---|
| `docs/research/swarm-schemas/` | leaf, review, and synthesis JSON Schema |
| `docs/research/swarm-briefs/` | checked-in briefs (smoke + later slice work) |
| `docs/research/swarm-tools/validate_filings.py` | deterministic intake |
| `docs/research/swarm-tools/spawn_devin_leaf.py` | argv-constrained Devin spawn |
| `.cursor/agents/muse-synthesizer.md` | Muse pin for Cursor Task |

## Doc disposition

Process pin only. Cite with the Cloud-host note, the 2026-09-10 2-step
shape, and the measured smoke
[`agent-swarm-smoke-2026-09-11.md`](agent-swarm-smoke-2026-09-11.md).
Cannot be cited as a passed gate, a live unlock, or a merge.

# Astra Codex drain freeze — 2026-09-06

Codex Pro weekly hard-drain research (~5:45–8:15 PM PT). Model: `gpt-6-astra` + `model_reasoning_effort=xhigh` (non-fast). Soak pin awareness: live `c5ef0ad`; prior `61ca0af`; BUG-114 incident `1e2d4e3c` read-only.

Noble claim priority if packages contend: (1) audit append/HWM beyond #114, (2) watch_only durability, (3) authority / selection→RW-15 map, (4) grammar FNs with fixtures; then W-25 / cohort / soak isolation. At most one production PR; evidence-only is fine. Monitor PASS bar unchanged (#62/#68).

## Package status (at freeze)

| Package | RESULT | Path |
|---|---|---|
| Grammar FNs | research-only; no date-ready path; no PR | `../astra-grammar-false-negatives-2026-09-06.md` |
| watch_only durability | FAIL D1–D4 with fixtures; hold claim behind #1 | `../astra-watchonly-durability-2026-09-06.md` |
| Authority boundaries | refuse-working; no model bypass; local API approval intentional | `../astra-authority-boundaries-2026-09-06.md` |
| Selection→RW-15 runbook | docs-only; gaps G1–G12 | `../astra-selection-to-rw15-runbook-2026-09-06.md` |
| W-25 / SEC-11 witness | docs-only; no WORM configured; no provider spend | `../astra-w25-sec11-witness-plan-2026-09-06.md` |
| Cohort honesty schedule | recommend filing-triggered + weekday 05:30 backstop; Mon 05:30 stays armed | `../astra-cohort-honesty-schedule-2026-09-06.md` |
| Soak isolation checklist | reference; linked worktrees + read-only mount notes | `../astra-soak-isolation-checklist-2026-09-06.md` |
| Audit concurrency beyond #114 | cyber-flag aborted md; 13 probe candidates in `misses-beyond-68.txt`; harden relaunch | `misses-beyond-68.txt` + `../astra-audit-append-hardening-2026-09-06/` |
| DEC-09 / HWM claim-shape | in flight / partial fixtures under sibling dirs | `../astra-dec09-matrix-gaps-2026-09-06/`, `../astra-hwm-resign-claim-shape-2026-09-06/` |

## Prompts

All Astra `codex exec` prompts used this drain are under `prompts/`.

## Hard rules preserved

No soak rewrite; no watchlist seed; no selection PASS claimed; no Discord forge; secrets not printed; branch→PR→main only.

## Amend log

- 2026-09-06 ~18:18 PT: mid-flight fixture refresh (audit-harden contracts, DEC-09 matrix helper, isolation checklist polish) while final reports still landing.

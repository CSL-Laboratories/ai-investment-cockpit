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
| Audit append hardening | **real-miss-found**; Order FSM freshness/rollback (F1/F2); HWM late damage deferred to HWM report; no PR yet | `../astra-audit-append-hardening-2026-09-06.md` |
| HWM re-sign claim-shape | **real-miss-found**; claim refuse invalid HWM at signing (miss #13); distinct from #68/#70; no PR yet | `../astra-hwm-resign-claim-shape-2026-09-06.md` |
| DEC-09 matrix gaps | residual fail-opens M1/M2/M3; evidence-only; cash-subject RW-21 proposed red tests | `../astra-dec09-matrix-gaps-2026-09-06.md` |

## Prompts

All Astra `codex exec` prompts used this drain are under `prompts/`.

## Hard rules preserved

No soak rewrite; no watchlist seed; no selection PASS claimed; no Discord forge; secrets not printed; branch→PR→main only.

## Amend log

- 2026-09-06 ~18:18 PT: mid-flight fixture refresh (audit-harden contracts, DEC-09 matrix helper, isolation checklist polish) while final reports still landing.

- 2026-09-06 ~18:23 PT: added HWM re-sign claim-shape report (real-miss-found).
- 2026-09-06 ~18:23 PT: added audit-append-hardening report (real-miss-found; Order FSM claim shape).
- 2026-09-06 ~18:33 PT: added DEC-09 matrix gaps final report (M1/M2/M3).

- 2026-09-06 ~19:35 PT: BUG-125 F1/F2 opened as PR #74; cockpit Astra research stopped ahead of 8:15 hard stop.

- 2026-09-06 ~19:40 PT: BUG-125/#74 MERGED (`ce62cf2`). Drain freeze closing; post-wall no new Codex/Astra until Lucas reopens. watch_only claim-shape mid-flight left out of this freeze if unfinished.

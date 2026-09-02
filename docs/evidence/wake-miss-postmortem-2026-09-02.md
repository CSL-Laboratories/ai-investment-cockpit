---
title: Postmortem — repeated one-shot routine wake misses (cockpit selection / soak starts)
type: postmortem
scope: Cockpit Admin ops only (local evidence; no platform-fix claim)
status: documented-mitigation-in-force
last-verified: 2026-09-02
agent_id: 44671ce8-dc1d-4413-9b75-b176a5943ecf
automations_root: /home/box/agent-data/agents/44671ce8-dc1d-4413-9b75-b176a5943ecf/automations/
related:
  - tue-08-30-et-read-only-start (deleted after hand-start)
  - wed-w-24-natural-head-rescreen (deleted after miss; calendar pin `30 5 2 9 *`)
  - thu-w-24-natural-head-rescreen (armed weekday cron `30 5 * * 4`; hand-run primary)
  - Monitor score wake `45 5 * * 4`
---

# Postmortem: repeated one-shot routine wake misses

**Audience:** Cockpit Admin ops  
**Date (PT):** 2026-09-02  
**What this is:** Local symptom + ops mitigation record for missed one-shot wakes that were supposed to drive soak `read_only` start and W-24 natural-head selection shots.  
**What this is not:** A platform root-cause proof, a Cursor/product bug ticket, or a claim that any schedule shape is fixed.

---

## Summary

Across two consecutive early-morning one-shots (Tue and Wed), Cockpit Admin routines under the agent automations folder **never fired** (`lastRunAt` remained `null`). Work was covered by **hand-start / hand-run**, then the dead routine was **deleted**. Thursday’s W-24 selection wake was **re-armed as a weekday cron** (`30 5 * * 4`) with **hand-run as primary**; Monitor’s score wake sits at `45 5 * * 4`. Until wakes prove reliable, Admin ops treat the calendar/cron arm as a **backup reminder only**.

---

## Timeline (PT)

| When | Intended wake | Schedule shape | Observed | Ops response |
|---|---|---|---|---|
| **Tue 2026-09-01 ~05:30** | `read_only` soak start (`tue-08-30-et-read-only-start`) | One-shot morning arm (prior day setup) | **Miss** — never fired; `lastRunAt` null | Hand-started soak from `/workspace/cockpit-soak` @ `1e2d4e3c` via `~/.config/cockpit/start-read-only.sh`; Discord `#market-briefing` post PASS; **deleted** the missed routine |
| **Wed 2026-09-02 ~05:30** | W-24 natural-head re-screen (`wed-w-24-natural-head-rescreen`) | **Calendar pin** `30 5 2 9 *` (Sep 2) | **Miss** — never fired; `lastRunAt` null | Hand-ran GET-only re-screen ~**05:57–06:00 PT**; result date-not-ready / cohort ≪30; **deleted** missed wake; froze evidence under `/workspace/w24-evidence/` |
| **Wed 2026-09-02 ~06:01+** | Re-arm next selection shot | Weekday cron `30 5 * * 4` (Thu only DOW) | Armed enabled; **`lastRunAt` still null** at create (expected until fire) | **Hand-run primary** encoded in routine prompt; Monitor score wake `45 5 * * 4` |
| **Thu 2026-09-03 ~05:30** (planned) | `thu-w-24-natural-head-rescreen` | Weekday cron `30 5 * * 4` | *TBD* | If no-show past ~05:40 PT, first Admin wake **is** the scan; **delete after act** |

---

## Symptoms (facts only)

1. **No automatic wake turn** at the scheduled early window for at least two distinct one-shot routines in a row.
2. On inspection after the miss window, routine metadata showed **`lastRunAt: null`** (never recorded a run).
3. Misses affected **different job payloads** (soak `read_only` bring-up vs W-24 GET-only selection re-screen) under the **same automations root**, so this is not explained by a single bad prompt body alone.
4. **Hand coverage worked:** Tue soak start and Wed W-24 re-screen both completed under Admin once triggered manually; selection gates and soak pin were unaffected by the wake miss itself.
5. Missed routines were **removed** after hand coverage so they could not recur as stale/zombie schedules.
6. Current live folder contents (as of this write-up): only  
   `…/automations/thu-w-24-natural-head-rescreen/automation.json`  
   with `schedule` / cron trigger `30 5 * * 4`, `enabled: true`, `lastRunAt: null`, prompt explicitly stating hand-run primary + delete-after-act.

---

## Hypothesis (not a platform fix claim)

### Calendar-pin vs weekday-cron

| Shape | Example | Where used | Working hypothesis |
|---|---|---|---|
| **Calendar pin** (DOM + month) | `30 5 2 9 *` | Wed W-24 one-shot | Appears **fragile for one-shot “this calendar day” arms** in this environment: schedule was present, yet **no fire** and **`lastRunAt` stayed null**. Same *miss shape* as Tuesday’s one-shot (never fired). |
| **Weekday cron** (DOW) | `30 5 * * 4` | Thu W-24 re-arm; Monitor `45 5 * * 4` | Chosen as an **ops experiment / alternate arm** after the calendar-pin miss. **Not proven reliable** yet; must not be trusted as the primary start until a fire is observed (`lastRunAt` non-null at the right window). |

**Important:** This document does **not** assert a Cursor/product defect, scheduler bug ID, timezone mis-parse root cause, or that weekday cron “fixes” wakes. It only records that:

- Calendar-pin one-shots **failed to fire** in the observed window.
- Admin re-armed with weekday DOW cron **and** simultaneously demoted the arm to **backup** behind hand-run.

Other plausible contributors (unverified here): agent not awake / not subscribed to automation ticks at 05:30 PT; one-shot lifecycle race after create; timezone presentation vs cron field interpretation; deleted residue vs new folder identity. None of these are confirmed; they are out of scope for this ops note.

---

## Impact

| Area | Impact |
|---|---|
| Soak `read_only` (Tue) | Delayed until hand-start; Discord briefing still PASS once started |
| W-24 selection shot (Wed) | Delayed ~30 min; hand-run still **NO PASS** (date-not-ready; BHF only; cohort ≪30) — wake miss did **not** change selection truth |
| Soak pin | Unchanged at `1e2d4e3c` on 8787 |
| Trust in one-shot arms | **Low** until a scheduled fire is positively observed |

---

## Ops mitigation (in force)

These are **Admin operating rules**, not platform changes:

1. **Hand-run is primary** for any 05:30-class selection / soak start until a wake is seen to fire on schedule with a real `lastRunAt`.
2. **Armed cron = backup reminder only.** If `lastRunAt` is still null past ~**05:40 PT**, the first Admin session that morning **is** the designated scan/start — do not wait for a late automatic wake.
3. **Delete after act** (or after confirmed late/wrong-day wake): remove the routine folder/entry so a weekday cron cannot recur next week unintentionally.
4. **Prefer weekday DOW cron over DOM/month calendar pins** when arming the next one-shot *as a backup*, given the Wed `30 5 2 9 *` miss shape — still without claiming the DOW form is fixed.
5. **Stagger Monitor behind Admin** (`45 5 * * 4` vs `30 5 * * 4`) so score/report wakes assume Admin may have already hand-run.
6. **Do not swarm** (no Luna/Composer) solely because a wake missed; cover the job, delete the arm, record evidence locally.

### Current Thu arm (reference)

Path:  
`/home/box/agent-data/agents/44671ce8-dc1d-4413-9b75-b176a5943ecf/automations/thu-w-24-natural-head-rescreen/automation.json`

- Name: `Thu W-24 natural-head rescreen`
- Cron: `30 5 * * 4`
- Prompt embeds: weekday cron (not M/D pin); hand-run primary; delete after act / late wake
- Monitor: `45 5 * * 4` score wake (separate agent; not this folder)

---

## Evidence pointers

- Memory log: `/home/box/agent-data/agents/44671ce8-dc1d-4413-9b75-b176a5943ecf/memory/log/2026-09.md` (Tue miss + hand-start; Wed miss + hand-run; Thu re-arm notes)
- Wed re-screen write-up: `/workspace/w24-evidence/w24-natural-head-rescreen-2026-09-02.md`
- Live automation JSON: `…/automations/thu-w-24-natural-head-rescreen/automation.json` (`lastRunAt: null` at postmortem time)

---

## Explicit non-claims

- No claim that Cursor automations / routine scheduler is fixed or broken as a product.
- No claim that weekday cron is proven; only that calendar-pin miss was observed and DOW re-arm + hand-run primary is the **ops** response.
- No PR, no CloudAgent, no Cursor CLI, no Codex action required for this document.

---

## Follow-up (Admin checklist for Thu 05:30 PT)

- [ ] By ~05:25 PT: be ready to hand-run W-24 GET-only natural-head path (reuse `/workspace/w24-evidence` scripts).
- [ ] ~05:40 PT: if `lastRunAt` still null on `thu-w-24-natural-head-rescreen`, hand-run immediately.
- [ ] After act (or late wake): **delete** the routine.
- [ ] Note whether the weekday cron ever set `lastRunAt` — that single observation is the next data point for this hypothesis.
- [ ] Leave soak at `1e2d4e3c` unless newly authorized otherwise.


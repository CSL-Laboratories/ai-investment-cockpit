---
title: Cockpit Admin — W-24 hand-run ops checklist
type: ops-checklist
audience: Cockpit Admin (local only)
date: 2026-09-02
soak_pin: 1e2d4e3c
scope: markdown ops only — no Cursor CLI, no Codex, no CloudAgent, no PR
related:
  - wake-miss-postmortem-2026-09-02.md
  - w24-natural-head-rescreen-2026-09-02.md
  - thu-w-24-natural-head-rescreen (cron backup)
---

# Cockpit Admin — W-24 hand-run ops checklist

Local ops only. Evidence under `/workspace/w24-evidence/`. Do not touch soak tree writes.

---

## 1. Soak pin + 8787 awareness (every wake)

- [ ] Confirm `/workspace/cockpit-soak` HEAD stays **`1e2d4e3c`** (`1e2d4e3ca7c2fcae4e320ce5f72b44905b036ee4`).
- [ ] **Do not** `git checkout`, reset, pull-to-rewrite, or restart soak to “fix” anything.
- [ ] Confirm `127.0.0.1:8787` is **LISTEN** (python). Unauth `GET /status` → **401** is healthy awareness (key required); leave process alone.
- [ ] If 8787 is down: escalate to Admin bring-up from pinned soak only — still no HEAD rewrite.

---

## 2. Schedule — Thu 05:30 PT hand-run primary

| Role | What |
|---|---|
| **Primary** | **Hand-run** W-24 natural-head GET-only re-screen ~**Thu 05:30 PT** |
| **Backup only** | Weekday cron `30 5 * * 4` (`thu-w-24-natural-head-rescreen`) — not trusted until `lastRunAt` proves a fire |
| Miss window | If no auto wake by ~**05:40 PT**, Admin wake **is** the scan |

One-shot calendar pins have missed twice (Tue/Wed). Treat cron as reminder, not authority.

---

## 3. Hand-run GET-only rescreen (steps)

Use **main clone** PR **#62** grammar — **not** soak `app/`.

1. Preflight: soak pin `1e2d4e3c` + 8787 awareness (§1). Note PT start time.
2. Workdir: `/workspace/w24-evidence/`. Outputs stay here only (raw under dated `rescreen-raw-YYYY-MM-DD/`).
3. Parser path: `/workspace/ai-investment-cockpit` `merger_semantics` (PR #62 / `efc626f` line) via that clone’s `.venv` — **never** import soak parsers for this gate.
4. Run the dated GET-only script (pattern):
   - Template: `get_only_natural_head_rescreen_2026_09_02.py`
   - For Thu: copy/adapt to today’s date suffix; keep GET-only SEC path; update FTS end date to run day.
5. Authority chain (script already encodes):
   - Discovery ≠ authority (StockAnalysis ∪ frozen ∪ EDGAR FTS).
   - Authority = live `company_tickers` → submissions absolute head → primary docs `{8-K,10-Q,10-K}`.
6. Freeze evidence: JSON + short markdown under `/workspace/w24-evidence/` (counts, gate, soak SHA unchanged).
7. **No** Discord selection post unless PASS (§4).

---

## 4. PASS criteria

| Gate | Required |
|---|---|
| Natural absolute head | `{8-K,10-Q,10-K}` |
| Grammar / selection-eligible | #62 source-set + eligibility OK; `current_matches_set_end` |
| **Date-ready** | Expected **and** outside dates both parse |
| Cohort | Selection-ready **≥ 30** before any watchlist / selection act |

- **PASS** → date-ready natural-head cohort meets gates; only then consider selection path per REGISTER (still no forge).
- **ELSE** → **freeze docs and wait**. Record date-not-ready / cohort shortfall. Do not select.

---

## 5. Forbidden (fail-closed)

- Forge edge / fake filings / synthetic absolute heads
- Seed `COCKPIT_MERGER_WATCHLIST` with **AAPL / MSFT / NVDA** (or any filler)
- Soak HEAD rewrite, dirty soak commits, read_only restart “to unblock”
- Speculative form-set / grammar **PR** from empty or date-not-ready screen
- Luna / Composer swarm on empty or blocked screen
- Cursor CLI · Codex · CloudAgent · OrderGateway bypass · Discord witness without PASS

---

## 6. After act — delete one-shot routine

- [ ] After hand-run (PASS or freeze-and-wait): **delete** the one-shot / spent routine under Admin automations so it cannot zombie-fire.
- [ ] Keep only the next armed backup if still needed; prompt must say **hand-run primary** + **delete after act**.
- [ ] Do not leave `lastRunAt: null` dead one-shots in the folder.

---

## Quick ref

| Item | Value |
|---|---|
| Soak pin | `1e2d4e3c` @ `/workspace/cockpit-soak` |
| Port | `127.0.0.1:8787` |
| Main grammar | `/workspace/ai-investment-cockpit` (#62) |
| Evidence | `/workspace/w24-evidence/` |
| Thu backup cron | `30 5 * * 4` |
| Posture until PASS | Freeze docs · wait · no seed · no PR |

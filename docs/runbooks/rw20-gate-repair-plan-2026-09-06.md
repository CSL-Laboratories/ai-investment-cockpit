---
title: RW-20 authority repair and operator-requested gate pressure tests
status: live
last-verified: 2026-09-06
---

# Authorized scope and acceptance

The operator requested a branch, committed audit findings, repair of the three high-priority
boundaries, and pressure tests of the nine listed soak concerns. This is authorization for
isolated development and review, not deployment, selection commitment, model-created edges,
watchlist admission, gate relaxation, live trading, or rewriting a damaged ledger.

Work starts from main `b4a199f`. The audit was recorded in `94b8b4b`. The primary checkout
is an independent Windows Git repository; any WSL validation must use isolated source copies.
The existing `/workspace/cockpit-soak` must not be modified, restarted, rebased or healed.

| Package | Deliverable | Acceptance |
|---|---|---|
| RW-20 / BUG-115 | Explicit Claude capability denial and strict argv inspection | Missing, duplicate, altered or appended capability switches refuse; real CLI flag contract cited; no user-config/MCP/tool grant |
| RW-20 / BUG-116 | Authenticated proposal binding and canonical fixed-order risk | Unsigned payload changes refuse; final cost and risk describe the exact submitted tuple; tightened limits still bind |
| RW-20 / BUG-117 | Synchronous verified performance at approval/submission | Fresh equity loss, stale/wrong-day performance, audit faults and restart refuse without a scheduler tick |
| RW-20 review | Audit multi-writer and durable watch-only pressure | Independent attack tests, no fork repair, explicit handling of operational write failures vs integrity violations |
| RW-20 evidence | Local DEC-09, isolation, selection-to-RW-15 map and W-25 plan | Exact source/dependency/OS/interpreter evidence; no simulated cloud witness or inferred deployment health |
| RW-21, after RW-20 production work | Held real-filing semantic fixes | Pin raw real filing bytes/provenance; preserve year-only, not-closed-by, extension, mixed-consideration and authority refusals; no selection PASS claim |
| Research review | Cohort feasibility, filing-triggered intake, independent precision/recall and value benchmarks | Predeclared populations and independent labels; no invented observations or measured profitability; existing authority gates unchanged |

The independent reviewer owns new adversarial tests for order/risk changes. No agent may
mutate another agent's files or change Git state. The root agent integrates and commits one
bounded package at a time. Test fixtures may use isolated synthetic inputs to exercise a
control, but they cannot be presented as prospective, broker, semantic or witness evidence.

All unavailable platform cells, absent independent labels, missing prospective candidates and
unprovisioned cloud resources stay visibly pending. A draft PR is review material; it grants no
deployment authority. A real retention-bearing cloud witness requires a separate concrete
provider/configuration approval after its plan is reviewable.

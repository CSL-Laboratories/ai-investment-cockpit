---
title: Owner-blocker handoff after RW-30
status: reference
last-verified: 2026-09-12
amends:
  - REGISTER
---

# Owner blockers after RW-30 (2026-09-12)

**For:** Lucas. **Not a Head package.** Head cannot close these.
**Baseline:** `main` `885d33a` (RW-30 / [PR #92](https://github.com/NotSkittlz/ai-investment-cockpit/pull/92)).
**Score:** `qualify_count` is still **0**. Do not lower a gate.

In-repo slice A is protocol-complete. Freeze, evaluation, cases, and
measurement protocol are on `main`. Everything below is owner-operated:
screens, labels, feeds, soaks, activation.

## Start here (one loop)

1. **Fresh GET-only W-24 natural-head screen** on the operator box
   (same method as
   [2026-09-09](../evidence/w24-natural-head-rescreen-2026-09-09.md)).
   Last committed screen: **selection-ready 0, date-ready 0, cohort 0 of 30**.
2. Write a dated evidence note + JSON. Empty is an honest result.
3. Hand that note back to Head. Do **not** seed
   `COCKPIT_MERGER_WATCHLIST`, post Discord selection, rewrite soak HEAD,
   or `/paper approve`.

Checklist: [cockpit-handrun-ops-checklist-2026-09-02](../evidence/cockpit-handrun-ops-checklist-2026-09-02.md)
(update soak pin from the *latest* screen note, not the 09-02 pin).
Workdir pattern: `/workspace/w24-evidence/` on the soak host. Parser from
the **main clone**, not soak `app/`. GET-only SEC. No OrderGateway.

## Blockers (do in this order)

| # | Blocker | Closes | Done when | Do not |
|---|---|---|---|---|
| 1 | Current eligible target + prospective cohort | `cash_merger` watch → possible later qualify | **30 unique eligible identities on one selection date** (not 30 concurrent orders) **and** a current date-ready target (expected + outside dates both parse). Last screen is 0/0. | Seed the merger watchlist; treat AAPL/MSFT/NVDA awareness names as merger evidence; insert into today's W-24 manifest; count the same issuer twice; activate on one BHF-style near-miss. |
| 2 | Independent extraction labels | precision/recall; model-judge `not_started` | Sealed population (hashed raw filings + rejects/ambiguous). Labeler did **not** implement `merger_semantics`. Labels from source spans **without** seeing predictions. Second reviewer adjudicates; keep both originals. Fields: cash_amount, target_security, consideration_mix, lifecycle, expected_date, outside_date. | Use RW-11 regressions as held-out; call a zero denominator 100%; let the parser author label the held-out set. |
| 3 | Authorized measurement population | numeric thresholds still `unset` | Separate REGISTER claim **after** the population is sealed and thresholds are declared on tuning **before** held-out is opened. Protocol: `app/feasibility/protocol.v1.json`. | Invent Sharpe/hit-rate now; tune after outcomes; reuse RW-28 evaluation as held-out. |
| 4 | Paired 20-market-day awareness time study | time-saved unmeasured | Independently defined “important event” first; preserve zero-event and aborted days. Design: [gate-feasibility-and-value-2026-09-06](gate-feasibility-and-value-2026-09-06.md). | Treat model latency or uptime as operator time saved; start this instead of (1). |
| 5 | CEF daily NAV contract | `cef_discount` watch | A **new** REGISTER claim that authorizes a feed. Below-NAV is not expected return. | Treat RW-30 as NAV permission. Skip unless you want CEF as a second candidate. |
| 6 | Live `data/` packets | Inv 8 remainder | Separate source claim. RW-29 only read cited notes. | Dump soak `data/` into git. |
| 7 | Activation | paper / W-23 | Evidence from (1) exists **and** you approve. Green tests are not that. | Unlock auto rungs; live capital (paper-first). |

Rejected names (`mega_cap_signal`, `awareness_fallback`, `momentum`,
`pairs`) stay reject until a **new freeze claim**. Do not spend this
cycle trying to promote them.

## Where to work

Owner box / soak host, not this Cloud Agent. Market and paper soaks stay
owner-operated. Head can ingest **checked-in evidence notes** after you
commit them. Head still must not merge.

## Hand back to Head when

You have either:

- a new dated GET-only screen note (empty is fine), or
- a hashed label manifest + independence procedure, or
- an explicit NAV / measurement / soak **claim** you want Head to
  implement against the frozen protocol.

Then Head can add a bounded REGISTER packet that consumes those artifacts
without lowering `qualify_count` until the protocol's predicates actually
hold.

## Hard rules (unchanged)

- Single order path; no LLM on risk or orders.
- Secrets stay in the OS store.
- Fail closed. Vendor-limit / stale data → `watch_only`.
- Paper-first. No live capital until pre-live gates (SEC-11).
- `empirical_pass_claimed` stays false until a real evidence gate says
  otherwise.

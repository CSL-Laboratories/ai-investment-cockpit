---
title: RW-29 slice A case inspection and missing-evidence ledger
status: reference
last-verified: 2026-09-11
amends:
  - REGISTER
---

# RW-29 — Slice A case inspection (useful research after zero qualify)

**Claim:** `RW-29` on `cursor/rw29-case-inspection-df89` from `origin/main`
(`8043e38`, merged RW-28 / PR #90).
**Pipeline:** Head implementation → SWE-2 Medium first-gate → jsonschema
→ Muse Spark 1.3 medium → Head steer. Filing bodies gitignored under
`.swarm-runs/rw29-2026-09-11/`. **No empirical PASS.** No activation.
No order-path edit. No new data feed.

## Why this packet

RW-28 froze discrete metrics and emitted `qualify_count: 0`. Product
§A: if fewer than two qualify, deliver useful research and identify the
specific evidence or scope decision needed; do **not** lower a gate.
This packet inspects a bounded sample of already-cited operational
evidence notes, classifies the six product failure classes, and names
the missing decisions. It does not inspect live `data/` packets
(Inv 8; those need their own source claim).

## Pins (must stay byte-identical)

| Artifact | SHA-256 |
|---|---|
| `app/feasibility/freeze.v1.json` | `26f34dba3fd3a75732d136e88a6280dd313c5ffc01886f79e73ab1c8c56eb2d8` |
| `app/feasibility/evaluation.v1.json` | `665f72b426efff7a7411d14816e50867cccb8ee26f03e3bd5dc18056a5a0ab31` |

## Sample (cited summaries only)

| Case | Class | Kind | Source |
|---|---|---|---|
| `w24_sep04_identity_unmapped` | acquisition | empty | Sep 4 natural-head screen |
| `w24_sep04_selection_empty` | eligibility | empty | Sep 4; selection-ready 0 / cohort 0 of 30 |
| `w24_sep03_bhf_not_date_ready` | eligibility | rejected | Sep 3; BHF dates refused |
| `w24_intake_cash_parse_refuse` | extraction | rejected | 2026-08-18 intake; 0 of 19 passed |
| `w24_bhf_date_saft_refuse` | extraction | rejected | BHF/SAFT date audit |
| `w22_briefing_edge_unavailable` | delivery | empty | W-22 soak; edge unavailable |
| `w22_wake_never_fired` | scheduling | empty | W-22 05:30 wake never fired |
| `research_value_unmeasured` | reasoning | unverified | research-value-status unmeasured |
| `w23_impl_not_empirical` | eligibility | useful | W-23 implementation is not empirical PASS |

## Missing decisions (none authorized here)

1. `cash_merger` prospective cohort + current eligible target
2. `cef_discount` daily NAV contract (below-NAV is not expected return)
3. Independent extraction labels
4. Authorized measurement population before numeric thresholds
5. Paired awareness time study

## What this does not do

- Does not mutate freeze/metrics/evaluation
- Does not ingest live raw packets or authorize a source
- Does not select launch strategies or activate W-23
- Does not seed `COCKPIT_MERGER_WATCHLIST`
- Does not rewrite the soak scheduler
- Does not claim the DEC-09 matrix or any evidence gate
- Does not claim empirical PASS

## First-gate / Muse

Pending at first commit. Reviewer must **write** the filing (not `exec`).
`--sandbox` is forbidden. If a tool is rejected, still write the filing
from the `read` evidence already in hand.

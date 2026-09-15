---
title: RW-35 empty-run research update
status: reference
last-verified: 2026-09-15
---

# RW-35 — useful empty-run update through compose

RW-27 through RW-31 remain freeze/evaluation/cases/protocol/screens packets
with `qualify_count=0`. RW-32 is the existing remote seal package and is not
reused. Those packets explained empty/rejected runs in a ledger. They did not
deliver an operator-visible update through `main.compose`.

RW-35 extends `workflows.services.build_services` with `research_update`.
`POST /research/empty-run` and `#brief` delivery now carry:

- significance of the 2026-09-12 GET-only board (pool 197, supported 32, cash 0,
  selection-ready 0, date-ready 0, cohort 0≪30)
- opposing evidence (supported heads exist; cash/eligibility still fail)
- the six failure classes from retained RW-29 cases
- unknowns / missing decisions
- next trigger: continue GET-only natural-head screens

No live packets are ingested. Freeze/evaluation/cases/protocol/screens bytes
are unchanged. The 30-identity gate is not lowered. No empirical PASS.

Evidence: [[evidence/rw34-cloud-adapter-2026-09-15]].

Production entry points: `research.empty_run.compose_empty_run_update`,
`workflows.research_update.empty_run_update`, composed
`services["research_update"]`, `POST /research/empty-run`.
Removing that key fails the service-set tests.

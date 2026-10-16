# Candidate: Cockpit successor product and economic contract

**Owner direction:** recorded in CSL-212.  
**Status:** review candidate. Exact-digest acceptance remains a separate human decision.

## Product outcome

Build one operator-facing decision and execution system for a United States
individual using a personal account. It must support stocks and regulated
prediction markets through shared evidence, accounting, strategy, decision,
authority, audit and recovery contracts while preserving venue-specific semantics.

The product optimizes measured risk-adjusted net yield subject to explicit safety,
compliance, liquidity, drawdown, uncertainty, reliability, latency, cost and capital
constraints. It does not promise alpha. Empty, negative, rejected and inconclusive
results are valid outcomes and must remain visible.

The first coherent event-market slice is Kalshi demo. Polymarket US Retail is a
second adapter only after the first slice is stable and capacity remains. The first
stock slice and benchmark must be selected by an accepted child contract rather than
inferred from historical implementation.

## Required product shape

- A point-in-time evidence and replay plane retains raw inputs separately from
  summaries, with source identity, retrieval time, transformations and provenance.
  Applicable collection, licensing, retention, redistribution, confidentiality and
  account terms must be established before authenticated or continuing acquisition;
  demo or read-only access is not blanket data permission.
- Instrument, venue, portfolio, collateral, resolution, cash-flow, fee, tax and
  accounting semantics are explicit and reconcile across restarts and external truth.
- Strategy research compares explicit baselines on frozen populations, separates
  tuning from evaluation, and accounts for costs, uncertainty and capital use.
- Decision routes are provider-neutral experiments. System One, OpenAI Decisions and
  other routes earn roles through installed-route qualification; no provider name is
  an architecture decision or authority grant.
- Authority is expressed as audited compliance profiles. Proposal-only,
  model-assisted and bounded direct authorization are distinct profiles, not a
  bypass switch. The eventual first live target is bounded direct authorization only
  after separately accepted eligibility, replay, simulation, demo or paper, shadow,
  limited-live, recovery and compliance gates.
  [The completed authority research](AUTHORITY-COMPLIANCE-RESEARCH.md) makes A
  proposal-only the reference qualification profile and C bounded direct
  authorization the owner-selected future target. All profiles share one independent
  enforcement and evidence layer. A provider response, typed decision or confidence
  value is an input, never permission. Profile C is not authorized by this contract
  and cannot create a second order path: any future implementation still uses the
  single risk-pass, mode, approval and `OrderGateway` path unless those binding
  invariants are separately amended and reviewed.
- A GUI and scriptable CLI use one versioned application/service API and the same
  authority checks. A TUI is optional and must be justified by measured operator
  value. No surface owns a privileged order path.
- Qualification binds evidence to the exact candidate, configuration, data manifest,
  venue profile and environment. Release is a human decision after independent
  verification, not a consequence of CI.

Qualification is capability-scoped. Stock, Kalshi demo, later Polymarket US Retail,
provider, interface and platform cells retain separate evidence and unknowns. A PASS
for one capability or environment cannot unlock another.

## One-month target

The month targets a thin, coherent vertical slice: retained market evidence,
replayable strategy evaluation, explicit accounting, one qualified decision route
comparison, Kalshi demo interaction through current-safe boundaries, and useful GUI
and CLI inspection. Breadth is added only when it does not weaken provenance,
authority, recovery or evaluation.

The schedule is a prioritization constraint, not evidence of safety, profitability,
legal eligibility, release readiness or live authority. Lower maturity is an honest
result.

## User and scope boundaries

First profile: one US individual and personal account. Team, institutional,
multi-tenant and delegated-role workflows are deferred. Extension points may be
documented, but first-release code and data models must not carry speculative team
machinery.

In scope for contract research: US equities and regulated event markets, broad
evidence ingestion, provider-neutral decision experiments, GUI, CLI, optional TUI,
staged qualification, measured economics and bounded eventual authorization.

Not selected by this contract: implementation language, database, framework, model
provider, stock broker, live venue account, capital allocation, numeric risk limits,
tax treatment, deployment topology or production credential custody. Those require
bounded evidence and human receipts where consequential.

## Current-authority preservation

This successor direction supersedes the September product target for replacement
planning. It does not retroactively invalidate historical evidence, nor does it
silently replace current runtime rules. Until separate amendments are accepted and
implemented:

- every order still follows risk-pass, mode, approval and `OrderGateway`;
- auto decision routing remains pinned as currently documented;
- model outputs remain schema-validated;
- secrets remain in the OS credential store;
- `app/risk/` remains deterministic and free of model, I/O and subprocess logic;
- the client-side human gate and hash-chained audit remain;
- stale, limited or hung operation fails closed to `watch_only`;
- raw inputs remain separate from summaries;
- the current post-PDT risk model remains in force;
- agents do not enable live trading or add live credentials.

Future authority-profile amendments must separately bind the exact legal/account
perimeter, venue and contract family, adapter/environment, provider/model version,
permitted actions, expiry, cumulative budgets, information restrictions, stop and
recovery owners, evidence dossier and human receipt. Stops survive restart; restart
requires authoritative reconciliation and fresh authorization. Unknown eligibility,
execution state or provider permission fails closed rather than selecting a more
permissive profile.

## Acceptance boundary

Acceptance of this document establishes product direction only. It does not prove
implementation, provider or venue qualification, profitable operation, safety,
compliance, release readiness, or permission to trade live.

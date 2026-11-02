# Selection, authority and paper execution — 2026-09-06

This is a source map of the production composition, not a record of a successful trade.
The checked-in September 4 screen has zero selection-ready and zero date-ready names.
No watchlist, selection commitment, raw production store, Discord message or order was created
by this audit. A new date-ready filing would clear only one prerequisite.

| Step | Production authority and refusal |
|---|---|
| Discovery | A ticker hit, model summary or cached screen cannot admit a security. The supported absolute SEC head, complete ordered signed filing set and fresh per-CIK poll must agree. Unsupported heads remain unsupported. |
| Prospective selection | `strategies/merger_semantics.py::record_selection_commitment` requires at least 30 unique identities selected on one date, source-derived eligibility, complete signed sources and point-in-time entry quotes. It rechecks before and after the external witness call. A single date-ready head cannot produce a qualifying cohort. |
| W-24 chronology | The exact selection manifest hash receives a Discord server timestamp within five minutes of commitment. Wrong author/channel, edits, mismatched bytes, timing errors and absent receipts refuse. Discord is chronology evidence here; it is not SEC-11's immutable rolling anchor. |
| Mature calibration | The full selected population must survive evaluation through the fixed horizon, with later source-derived outcomes, post-horizon poll coverage, price sources and independent parser-quality evidence. No dropping failures, substituting retrospective winners or compressing the horizon. |
| Current deal | `CashMergerSemanticVerifier.verify` re-derives current consideration, exclusions and both unique dates from the complete signed sources. The current baseline and freshness must still agree. Conflicts or unavailable facts refuse. |
| Execution universe | `main.compose` freezes the operator's general watchlist; `CashMergerEdgeProvider` separately freezes `merger_watchlist_symbols`. Models have no mutation seam. An operator's later admission/configuration change must follow its normal review; this audit does not perform it. |
| Deterministic edge | `CashMergerEdgeProvider._estimate` permits only approved long-target common-equity buys with resolved quantity and the pinned long horizon. Model-authored return/probability fields refuse. It verifies the deal, calibration, semantic authority, quote, metrics and their hashes before computing/persisting an edge. |
| Proposal | `workflows/proposals.py::draft_proposal` runs schema validation, scrubbing, raw-source grounding, fresh execution-cost/net-edge verification and pure deterministic risk. Only an approved result creates the fixed Order. RW-20 signs the complete proposal body and resolved-order hash; unsigned old drafts must be redrafted. |
| Human decision | Discord `/paper action:approve order_id:<id> confirm_symbol:<symbol>` routes through `bridge/bot.py::PaperCommands.handle` into `PaperExecutionService.decide`. Authenticated allowlisted identity, exact symbol, successful current-runtime/current-generation job, unflagged draft, paper mode and fresh deterministic risk must all hold before an adjudication/token is created. A slash command is no bypass. |
| Final submission | `OrderGateway.place_equity_order` reviews and then reruns the checks in locked `submit`: HALT/calendar/data health, mode/watch-only, generation, breakers, fresh risk, signed approval/tuple/runtime, single-use token and audited pending exposure. RW-20 refreshes signed equity/loss evidence at each risk boundary. Intent precedes the fixed Alpaca paper call; result records its actual response. |
| RW-15 | `brokers/paper_reconciliation.py::PaperOrderReconciler` polls broker-visible orders and reconciles cumulative partial fills and terminal states against signed lineage. Startup calls reconciliation before the completeness report. Lost responses and ambiguous histories remain visible; replay tests do not replace real resting/partial/terminal/lost-response broker evidence. |

The model boundary is structural in the production composition: a schema-valid suggestion cannot
mint a verified edge, change a date parser, mutate the frozen watchlists, create a human approval
or directly call the gateway. An operator can change source code, and an administrator can control
the host; neither is a capability the decision model is granted. Runtime CLI denial is separately
reviewed in RW-20 and does not substitute for the still-open low-privilege deployment/SEC-11 gates.

The September 4 report's `selection-ready` means eligibility plus current-head/source-set agreement.
It is distinct from `date-ready`. The cohort manifest itself requires 30 same-date eligible identities;
the current-target date gate is later. Describing that contract as 30 simultaneous *date-ready*
orders would overstate the implementation.

Sources: [production composition](../../app/main.py),
[selection/verifier](../../app/strategies/merger_semantics.py),
[edge provider](../../app/strategies/merger_edge.py),
[paper execution](../../app/brokers/paper_execution.py),
[gateway](../../app/brokers/gateway.py),
[September 4 screen](w24-natural-head-rescreen-2026-09-04.md).

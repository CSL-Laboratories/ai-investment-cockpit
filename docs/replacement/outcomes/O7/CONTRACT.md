<!-- o7-candidate: maturity=researched qualification_claimed=false contract_frozen_claimed=false framework_selected=false -->

# O7 candidate: shared GUI and CLI operator contract

**Issue:** [CSL-219](https://linear.app/csl-laboratories/issue/CSL-219)
**Parent:** [CSL-212](https://linear.app/csl-laboratories/issue/CSL-212)
**Maturity claimed:** `researched`
**Not claimed:** `human_decided`, `contract_frozen`, `prototyped`, `implemented`, `provider_free_verified`, `demo_or_paper_qualified`, `shadow_qualified`, `limited_live_qualified`, `release_qualified`, or any PASS or qualification.
**Registry:** `docs/replacement/program.json` stays `pending_outcome_artifact` for O7. This packet does not edit it.
**Framework:** none selected. No framework spike was run.

This is a candidate wire contract. GUI and CLI consume one versioned application/service API. Neither surface gets an order path. The exact candidate command below checks internal consistency and the negative controls. An implementer run, CI, or review is not independent verification and does not complete the outcome.

## Exact candidate command

```text
python -X utf8 -B tools/check_o7_operator_contract.py
```

Exit 0 means the checked-in candidate is internally consistent and every named negative control fails as designed. The stdout line `qualification_claimed=false` is a non-claim. The checker does not open a network connection, read credentials, or call a provider.

## What this candidate consumes

O6 has not checked in an outcome artifact. This candidate consumes only the accepted month-program fragment `docs/replacement/MONTH-PROGRAM.md#o6` at digest `f28b75244a4eef5e9d3095349d6088bcd08e09547ab8e0fbc03ba52a119f3001`, plus the non-widening consequences already recorded in `AUTHORITY-COMPLIANCE-RESEARCH.md`:

- Surfaces share one profile. A local control does not imply an account-wide restriction.
- The sole enforcement route remains risk-pass, then mode, then approval, then `OrderGateway`.
- Stop survives restart. Restart requires reconciliation and a fresh authorization decision.
- Profile A is the qualification reference. Profile C is not authorized by this candidate.
- A provider response, typed decision, or confidence value is not permission.

O7 does not invent or widen that authority. Informative dependencies on O1 journeys, O2 provenance, and O3 positions are the month-program clauses only. Those outcomes are also still `pending_outcome_artifact`.

## Operator-journey inventory

Local inspection of `ebbaa34489f171e079834d02098135711e1d4755`. `app/orchestrator/app.py` and `app/bridge/bot.py` are byte-identical to `2a73f12d1537b8a1cfc98dec7be9eb9a4c8c1b9c`. Evidence class: locally observed. This inventory is not a claim that those routes are the successor API.

The loopback API is key-gated and bound loopback-only. Discord slash commands are `mode`, `killswitch`, `brief`, `status`, `limits`, `triage`, `ask`, `automation`, `quant`, and `paper`. Only `quant`, `ask`, and `paper` have real bodies. The others acknowledge and stop. Approval and veto token minting stays in `app/bridge/approval.py` and `app/bridge/veto.py` on the current order path. This candidate does not move that path and does not mint a submit token.

| Journey | Loopback today | Discord today | Shared command |
| --- | --- | --- | --- |
| Evidence and provenance | `GET /brief/today`, `GET /research/empty-run`, ingest inside `GET /status` | `/brief` stub, `/ask` wired | `operator.evidence.inspect` |
| Strategies | `GET /quant/status`, `POST /quant/sim` (synthetic) | `/quant` wired | `operator.strategies.inspect` |
| Positions and collateral | `GET /paper/account` | `/paper` preview | `operator.positions.inspect` |
| Decisions and refusals | `GET /proposals`; decision route unwired; paper decision is human-gated | approval buttons, `/paper` | `operator.decisions.inspect` |
| Uncertainty | partial, inside status and the quant caveat | quant caveat text | `operator.uncertainty.inspect` |
| Health | `GET /status` | `/status` stub | `operator.health.inspect` |
| Budgets | `GET /limits`, usage inside status | `/limits` stub | `operator.budgets.inspect` |
| Compliance profiles | `GET/PUT /mode` is not a signed A/B/C profile | `/mode` stub | `operator.profiles.inspect` |
| Interventions | `POST /killswitch`, `PUT /mode`, `PUT /limits` | `/killswitch` stub, veto | `operator.interventions.request_stop`, `request_reconciliation`, `request_revocation` |
| Audit | audit chain embedded in status | none | `operator.audit.export` |
| Recovery | watch-only and halt inside status; no recovery route | none | `operator.recovery.inspect` |
| Export | none | none | `operator.export.bundle` |

`GET /watchlist` stays read-only in the current runtime. `POST /automations` and `POST /jobs/{id}/message` are unwired. `WS /ws/quant` is a separate read-only socket. None of these become a second order path in this candidate.

## Wire contract

Version: `o7.operator.v0`. Structural schemas:

- `schemas/operator-command.schema.json`
- `schemas/operator-event.schema.json`
- `schemas/operator-error.schema.json`
- `schemas/operator-response.schema.json`

Every response carries `candidate_maturity=researched`, the illustrative profile, `enforcement_route=risk_pass_mode_approval_order_gateway`, `order_path=none`, and `authority_effect` of `none` (reads and exports) or `intervention_request` (stop, reconciliation, revocation). `order_submitted` cannot be true on a valid response. `bounded_direct_authorized` cannot be true. `local_control_is_account_wide` cannot be true. `secrets_included` cannot be true.

The profile object in the frozen fixtures uses `signature_state=illustrative_unsigned_fixture`. It is not a credential, not a signature, and not a live grant. Displaying profile class A, B, or C is not authorization.

### Commands

Reads: evidence, strategies, positions, decisions, uncertainty, health, budgets, profiles, recovery, audit export, bundle export.

Intervention requests: `operator.interventions.request_stop`, `request_reconciliation`, `request_revocation`. Confirmation is `explicit` and scope is `operator_session` only. The command schema rejects `not_required`, an empty `arguments` object, and any other scope on those three commands. There is no `account` scope. A noninteractive flag may supply the explicit confirmation. It may not skip the shared enforcement route.

There is no submit, approve, or broker command in the allowlist.

### Events and errors

Events name a catalog command and a visibility of `visible`, `absent_not_hidden`, or `unreconciled_visible`. `hidden` is not a visibility value. The event type has to match the command: `evidence_viewed` is only `operator.evidence.inspect` and may repeat `ok`, `stale_or_unreconciled`, or `unavailable`; `intervention_recorded` is only a stop, reconciliation, or revocation request and may repeat those same statuses; `export_recorded` is only an audit or bundle export; `refusal_visible`, `uncertainty_visible`, `stale_visible`, and `unreconciled_visible` may name any catalog command. `uncertainty_visible` may repeat `ok`, `refused`, `stale_or_unreconciled`, or `unavailable` because uncertainty does not choose the status. The oracle includes every event type. A read uses `authority_effect=none`. An intervention request uses `authority_effect=intervention_request`. A read or export does not carry `result.intervention`, `result.recorded`, or `result.survives_restart`.

Error codes are `usage`, `refused`, `stale_or_unreconciled`, and `unavailable`. Each error's `code` equals its `status_code`. Each error has a text message, a recovery step, and `color_only=false`. An intervention response names the same action as its command: stop, reconciliation, or revocation. A recorded stop (`recorded=true` on `operator.interventions.request_stop`) sets `survives_restart` true.

### Status and exit codes

Both surfaces carry the same `status_code`. The CLI maps it to a process exit code. The GUI shows the same code as text.

| status_code | CLI exit | When |
| --- | --- | --- |
| `ok` | 0 | No refusal, stale data, unreconciled state, or unavailable dependency |
| `usage` | 1 | Malformed command |
| `refused` | 2 | A refusal is visible and no unavailable, stale, or unreconciled condition outranks it |
| `stale_or_unreconciled` | 3 | Stale data or unreconciled state is visible, and no dependency is unavailable |
| `unavailable` | 4 | A dependency status is `unavailable`. Coexisting conditions stay visible |

Machine-readable JSON goes to stdout with no ANSI escape in any string, including error messages, recovery text, and `rendered`. `ansi_in_machine_output=false` does not excuse an escape elsewhere in the payload. Diagnostics go to stderr. `NO_COLOR` and `--no-color` are required of a future CLI. Color, a spinner, or an icon cannot be the only status signal. Cancellation does not imply that a recorded stop or fill was undone.

One response has one `status_code`. When more than one condition is present, precedence picks that code and every condition stays visible:

1. An unavailable dependency (`dependencies[*].status=unavailable`) selects `unavailable`.
2. Unreconciled state (`reconciliation=unreconciled` or `unknown_orders` greater than zero) selects `stale_or_unreconciled` when no dependency is unavailable. `visibility.reconciliation=unreconciled_visible` requires that unreconciled result, `restart_allowed` false, and a status of `stale_or_unreconciled` or `unavailable`. An unreconciled result requires that same visibility. A reconciled result cannot be labeled unreconciled.
3. Stale data selects the same status when no dependency is unavailable. Stale data is `stale=true` or any `dependencies[*].status` of `stale`. `visibility.stale_data=visible` requires that stale evidence, `watch_only` true, and a status of `stale_or_unreconciled` or `unavailable`. A fresh result cannot be labeled stale.
4. A refusal selects `refused` only when none of the conditions above are present. A refusal is a `refusals` entry or a `decisions[*].outcome` of `refused`. Each refused decision has a matching refusal with the same id, the same reason, and `visible=true`. `visibility.refusal=visible` requires that refusal evidence, an error whose code is `refused`, and a status of `refused`, `stale_or_unreconciled`, or `unavailable`. A response with no refusal cannot be labeled refused. `unavailable` is not a valid status when every dependency is readable.
5. `ok` is allowed only when none of those conditions are present.

A condition that loses the status stays visible and keeps its own error. A refusal keeps `visibility.refusal=visible` and an error whose code is `refused`. Stale or unreconciled data keeps its visibility and an error whose code is `stale_or_unreconciled`. The winning status also has its own error. Any response that is stale or has an unavailable dependency sets `watch_only` true, including a positions or decisions read. `presentation.text_status` equals `status_code` and that same text appears in `rendered`. Unknown or uncertain items, and collateral `state=unknown`, require `visibility.uncertainty=visible` on every command. `visibility.uncertainty=visible` requires that evidence. They do not by themselves change `status_code`.

An empty `positions` array is a known empty portfolio. Position `quantity` is a signed decimal string: an unsigned value is long, a leading `-` is short, and a fractional part is allowed. An empty `result` object is invalid for every catalog command. The response schema requires that command's result fields.

## Parity

`parity-matrix.json` gives every catalog command one row. GUI and CLI are both `same_api`. `order_path` is `none`. Each row's journey equals that command's catalog journey. `must_show` is the pinned display set for that command. Presentation text may differ. The semantic projection may not: profile, route, authority effect, order path, status, visibility, result, errors, and the exit code derived from status.

The frozen oracle pairs `positions.inspect.parity` and `decisions.inspect.parity` use different `presentation.rendered` strings and the same projection. Both sides of `positions.inspect.parity` must be `operator.positions.inspect`, and both sides of `decisions.inspect.parity` must be `operator.decisions.inspect`. That is the allowed difference. Both sides pass the same oracle response checks as the catalog responses, including profile class A and no qualification claim.

## Accessibility gates

These are requirements for a later GUI and CLI. This candidate claims no WCAG conformance and renders no UI.

GUI, WCAG 2.2 AA, from the W3C Recommendation of 5 October 2023 and the WAI "What's new in WCAG 2.2" note:

- Level A additions used here: 3.2.6 Consistent Help, 3.3.7 Redundant Entry.
- Level AA additions used here: 2.4.11 Focus Not Obscured (Minimum), 2.5.7 Dragging Movements, 2.5.8 Target Size (Minimum) at 24 by 24 CSS pixels, 3.3.8 Accessible Authentication (Minimum).
- 4.1.1 Parsing is obsolete in WCAG 2.2 and is not a gate.
- AAA criteria 2.4.12, 2.4.13, and 3.3.9 are not required for this AA gate.
- 1.4.1 Use of Color still applies: refusal, uncertainty, stale data, and unreconciled state are text, not color alone.
- Keyboard operation, a single-pointer alternative to dragging, and help in a stable order are required. Authentication must not depend on a puzzle or transcribed secret. The credential itself stays in the OS credential store. Choosing the mechanism is an open decision below.

CLI, from the Command Line Interface Guidelines, Primer CLI accessibility, and the GitHub CLI accessibility write-up:

- Exit 0 on success and a stable non-zero code on failure.
- Machine-readable stdout, diagnostics on stderr, no ANSI in machine output.
- No color-only, symbol-only, or spinner-only status. Pair symbols with text labels.
- Honor `NO_COLOR`. Do not assume the terminal background, font, width, or screen reader.
- Do not redraw away the only copy of an error. A static text progress line is the required fallback.
- Noninteractive confirmation is allowed. Skipping a safety decision is not.
- Secrets are not echoed, logged, or retained in the surface payload.

The CHI '21 ACM page linked below was returned by the Parallel search. This session could not retrieve its HTML, so no technique from that paper is adopted beyond the citation.

## Automation gates

- GUI and CLI projections of one operation validate against the same response schema.
- CLI `--json` output is that response, on stdout, without ANSI.
- Exit codes match the table above.
- The provider-free checker recomputes the frozen manifest hashes and reruns the negative controls.
- `network_in_validator` is false. The checker imports no HTTP client.

## Negative controls

Each fixture is schema-valid except the stale read with `watch_only=false`, which the response schema must reject, and the two intervention commands, which the command schema must reject. Each intervention fixture breaks only one safeguard. Repairing only that safeguard must make the command schema-valid. The stale read breaks only `watch_only=false`. Setting that field to true must make the response schema-valid. The privileged control applies one safeguard at a time to the positions oracle: order path, enforcement route, command, authority effect, order submission, and a forbidden key. Repairing only that safeguard must make the response schema-valid. Any other schema error is a wrong failure. The checker must also name the control. A control that is accepted is an unexpected accept and fails the candidate command. A profile read reports `result.active_profile_class` equal to `profile.profile_class`.

| Control | Fixture | What it catches |
| --- | --- | --- |
| `gui_cli_semantic_divergence` | `fixtures/negative/gui-cli-divergence.json` | Same position command, quantities `10` and `12` |
| `hidden_refusal` | `fixtures/negative/hidden-refusal.json` | Refusals present while status is `ok` and refusal visibility is `absent_not_hidden` |
| `hidden_uncertainty` | `fixtures/negative/hidden-uncertainty.json` | Unknown uncertainty marked not visible |
| `hidden_stale_data` | `fixtures/negative/hidden-stale-data.json` | `stale=true` reported as fresh and `ok` |
| `hidden_unreconciled` | `fixtures/negative/hidden-unreconciled.json` | Unreconciled recovery reported as reconciled, with restart allowed |
| `privileged_order_bypass` | `fixtures/negative/privileged-order-bypass.json` | One positions defect at a time: `surface_private`, `broker_direct`, `operator.orders.submit`, `direct_order`, `order_submitted`, or `bypass_gateway` |
| `color_only_signal` | `fixtures/negative/color-only-signal.json` | `color_only_signal=true` |
| `success_exit_on_refusal` | `fixtures/negative/success-exit-on-refusal.json` | Refused status with exit code 0 |
| `contradictory_status_text` | `fixtures/negative/contradictory-status-text.json` | Refused status whose visible text says `ok` |
| `unconfirmed_intervention` | `fixtures/negative/unconfirmed-intervention.json` | Stop request with session scope and `confirmation=not_required` |
| `unscoped_intervention` | `fixtures/negative/unscoped-intervention.json` | Stop request with explicit confirmation and empty arguments |
| `stale_not_watch_only` | `fixtures/negative/stale-not-watch-only.json` | Stale read with `watch_only=false`. The fixture is health; the rule applies to every command |

## Optional TUI decision packet

`tui-decision-packet.json` records `human_decision=open`, `decided=false`, `build_now=false`, owner Lucas. This candidate does not build a TUI and does not decide that one must never exist. The packet lists the evidence a later decision would need. See the unresolved decisions below.

## Unresolved decisions (owner: Lucas)

Nothing in this list is decided here. `unresolved-decisions.json` is the machine-readable copy. Owner of every row: Lucas. Status of every row: open.

| Id | Class | Question left open |
| --- | --- | --- |
| `gui-framework` | architecture | Which GUI framework, if any, implements this candidate. No spike was run. |
| `gui-distribution-and-os` | architecture | Local desktop, local web, or hosted, and which operating systems. |
| `cli-implementation-stack` | architecture | Which stack implements the CLI. |
| `tui-authorization` | architecture | Whether measured value justifies a TUI. |
| `legacy-surface-migration` | architecture | Whether Discord and loopback remain as adapters. Left for O8. |
| `month-slice-surfaces` | release | Which surfaces ship in the month slice. |
| `operator-surface-spend` | spend | Any license, design-system, or hosting spend. None is requested. |
| `operator-authentication` | credentials | How sign-in meets WCAG 2.2 SC 3.3.8 without leaving the OS credential store. No credential is added. |
| `surface-eligibility-language` | tax_legal | Whether any surface may imply account, venue, tax, or adviser eligibility. This candidate says it must not. |
| `live-surface-effects` | live | Whether a surface may ever move live capital or arm an auto rung. None is enabled. |
| `intervention-scope` | authority | Whether a surface may request more than stop, reconciliation, revocation, and export. Wider requests need a human O6 receipt. |

Inherited, not re-decided: US individual and personal account; stocks plus prediction markets; Kalshi demo first; GUI plus scriptable CLI; TUI optional; paper, demo, and shadow as qualification stages; bounded direct authorization only after separate gates.

## Falsifiers

- GUI and CLI disagree on a financial or authority field.
- A surface hides uncertainty, a refusal, stale data, or unreconciled state.
- A surface submits an order or otherwise bypasses risk-pass, mode, approval, and `OrderGateway`.
- A local control is treated as an account-wide restriction.
- The candidate is described as frozen, qualified, or a PASS.
- Visible status text disagrees with `status_code`.
- A success response omits the result fields its command requires.

## Evidence limits

The frozen fixtures are illustrative. They are not market data, not a signed profile, and not a paper or demo run. Strategy qualification in the oracle is `not_qualified`. CI on this pull request does not promote the maturity. Completion still needs an independent non-implementer to rerun this command, the manifest, and the negative controls, and to compare any later decision with a human Linear comment.

## Research (Parallel)

Searches `search_08b86fcb75c9012e38baca48941cbf74` and `search_d8e17f0eba7853c179f525c121ae9b51`, run on Dan's box on 2026-10-17. This session had no Parallel CLI credentials. It started from those results and fetched the pages it could.

- [Web Content Accessibility Guidelines (WCAG) 2.2](https://www.w3.org/TR/WCAG22/)
- [What's New in WCAG 2.2](https://www.w3.org/WAI/standards-guidelines/wcag/new-in-22) (W3C WAI; WCAG 2.2 published 5 October 2023)
- [Command Line Interface Guidelines](https://clig.dev/)
- [CLI accessibility](https://primer.style/accessibility/foundations/cli-accessibility/) (Primer)
- [Accessibility of Command Line Interfaces](https://dl.acm.org/doi/fullHtml/10.1145/3411764.3445544) (CHI '21, ACM; full HTML not retrieved in this session)
- [Building a more accessible GitHub CLI](https://github.blog/engineering/user-experience/building-a-more-accessible-github-cli/) (2 May 2025, updated 12 May 2025)

Context7 was not used. The issue requires it only after an accepted child selects a UI or CLI framework. No framework was selected.

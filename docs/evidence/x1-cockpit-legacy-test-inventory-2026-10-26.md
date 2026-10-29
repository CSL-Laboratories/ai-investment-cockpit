# X1 Cockpit legacy-test disposition inventory (WP14, CSL-511)

Agent (Grok Bot Liaison, for Lucas): proposed dispositions for review by Samir. Nothing in this file deletes, moves or edits a legacy test, and nothing here is a decision or an acceptance receipt.

Prepared Mon Oct 26 2026 (PT) against Cockpit `main` `3122cac4780d776cbd487dd2a671466151a465ba` (merge of PR #282). Times are America/Los_Angeles.

## 1. Authority and scope

- Source plan: WP14 "X1 tags and inventories" and decision 3 in section 6 and section 7 of `step4-synthesis-sonnet.md` (Oct 26 pipeline step 4; relayed, not published in this repo). Its acceptance: `pre-x1-<repo>-<date>` tag exists, every legacy test file appears in exactly one disposition row, and ported oracles run red under unchanged job names with a pinned count floor.
- Lucas's decision, relayed in the task for this PR (Mon Oct 26, 11:59 AM PT, not independently verified against Linear): "Decision 3: approve keep-paths and boundary, push pre-x1 tags, delete after the Nov 10 rehearsal".
- Cockpit checker promotion is WP4, a separate PR (#283, draft at the time of writing). It pins three files (`test_replacement_program_registry.py`, `test_o7_operator_contract.py`, `tools/test_replacement_program.py`) with its own floor. This inventory classifies the first two as `keep` and does not duplicate that floor or edit `.github/workflows/**`.
- Scope of "legacy tree": every `test_*.py`, `*_test.py` and `conftest.py` under `app/` (223 files at `main`), plus this PR's own guard wrapper (1 file) = 224 rows.
- The step-4 synthesis does not enumerate Cockpit keep-paths (it names only Recursion's). The `keep` rows were proposed by this agent from the nine invariants, the escalate list in AGENTS.md and `docs/replacement/SCOPE-AUDIT.md` section 2. **Samir confirmed the Cockpit keep list (the `K-*` classes and `SUPPORT-K`) and the drop rows on PR #287**: https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/287#issuecomment-6425530222. Any change to the keep list goes through a reviewed change to this table. Two drop rows are conditional (section 3).

## 2. Pre-X1 tag

| Item | Value |
|---|---|
| Tag | `pre-x1-cockpit-20261026` (annotated) |
| Commit | `3122cac4780d776cbd487dd2a671466151a465ba` (`main` at tag time) |
| Status | Created and pushed by this agent to `origin` after Lucas approved pushing it; confirm with `git ls-remote --tags origin pre-x1-cockpit-20261026` |
| Verify locally | `git fetch origin tag pre-x1-cockpit-20261026 && python tools/check_x1_inventory.py --require-tag` |

Cockpit had no tags before this one. Recursion's precedent is `pre-v3-clean-cut-20260928` (per the step-4 synthesis; Recursion's tag is a separate WP14 half).

## 3. Dispositions

- `keep`: the file and the code it guards stay in the Cockpit tree after X1. Order-path, risk, audit, gate, human-gate, secrets, schema and untrusted-input oracles are `keep` because the nine invariants stay binding until a separately reviewed amendment. A `keep` file may be edited only through the normal escalation rules.
- `port`: the oracle is re-expressed at a successor location (new-tree location set by WP7) and must run red-to-green under unchanged job names **before** the legacy file may be deleted. Until then the legacy file stays and keeps running.
- `drop`: superseded scope with no successor oracle. Deletable only in the WP19 deletion PR, after the Nov 10 rehearsal, and only after the source couplings in section 7 are resolved. Samir confirmed 9 of the 11 drop rows (the 5 `feasibility/*` rows, `strategies/test_merger_edge.py`, `strategies/test_merger_semantics.py`, `test_rw22_semantics.py`, `test_rw22_semantics_independent.py`). **Two are conditional**: `strategies/test_rw21_real_filing_grammar.py` and `strategies/test_rw23_semantic_boundaries.py` (RW-21, BUG-146) drop only if the WP19 PR shows the same fail-closed behaviour is covered by a `K-UNTRUSTED` or `P-EVIDENCE` file; otherwise they become `port` to `P-EVIDENCE`. Their rows say so.
- Nothing is deleted by this PR. WP19 preconditions per the synthesis: WP4 merged, WP14 merged, operator-surface parity (A6). Lucas decides any pull-forward to Nov 9.

Totals: 136 `keep` rows (1509 test functions), 77 `port` rows (1210), 11 `drop` rows (168). Test functions are a static AST count; a parametrized function counts once.

## 4. Oracle catalog

The oracle column of every row must name one of these. A `port` oracle names the successor oracle the file's assertions move to; a `keep` oracle names the invariant or contract the file protects; `NONE` is the only oracle for `drop`.

<!-- x1-inventory:oracles:begin -->
| Oracle | Class | Successor or protected oracle | Why this class |
|---|---|---|---|
| `K-AUDIT` | keep | Hash-chained append-only audit, anchors, external witness, high-water signing (Inv 6, SEC-11). | Audit chain is a core engine under AGENTS.md; SEC-11 is the chief pre-live gate. |
| `K-CI` | keep | CI-read app configuration and benchmarks job inputs. | The benchmarks job and pytest configuration read these paths; they move only with their subject. |
| `K-GATE` | keep | Fail-closed control plane: halt, kill switch, breakers, mode ladder, limits, vendor health, loopback API auth (Inv 2, Inv 7). | These are the escalate-list control paths; fail-closed behaviour must stay proven in place. |
| `K-HUMAN` | keep | Client-side human gate: approval, veto window, operator allowlist (Inv 1, Inv 6). | Approval and veto stay the only route to an order until separately amended. |
| `K-INV` | keep | Nine-invariant gates (CLAUDE.md): single order path call-sites, no-LLM-in-risk, secrets grep, paper default. | The nine invariants stay binding until a separately reviewed amendment; these files are their executable gates. |
| `K-ORDER` | keep | Single order path: risk-pass, mode, approval, OrderGateway, capability token, order state machine (Inv 1). | Order-path code stays; any change needs an adversarial-review round and founder decision, so its tests cannot be dropped or silently moved. |
| `K-QUANT` | keep | Quant simulation engine as experiment surface: book, matching, OMS, PnL, costs, replay, autoloop containment. | SCOPE-AUDIT classes it a useful experiment surface; PnL, costs and autoloop are escalate-list paths, so keep until a founder-reviewed decision. |
| `K-REGISTRY` | keep | Replacement-program registry and O7 operator-contract checkers (CSL-212, CSL-219). | WP4 (PR #283) pins these paths with a floor; they are the acceptance-pin gate that V-06 says deletion would silently disable. |
| `K-RISK` | keep | Pure deterministic risk, numeric parse and risk-fact signing (Inv 5, Inv 9). | app/risk/ stays pure and deterministic; its oracle must keep running where the code lives. |
| `K-RUST` | keep | Rust parity pins: Rust crates under rust/ must agree with the Python reference (CSL-42). | The Rust crates are not X1 scope; their parity tests guard them where they run in CI. |
| `K-SCHEMA` | keep | Schema-validate every model output, repair-then-quarantine, and persisted model contracts (Inv 3). | Output validation is a standing invariant independent of which runtime produces the output. |
| `K-SECRETS` | keep | Secrets only in the OS credential store; .env carries no secrets (Inv 4). | Secrets handling is on the escalate list; the guard stays. |
| `K-STORE` | keep | Storage contracts: raw inputs separate from summaries, parquet snapshots, repositories, migrations (Inv 8). | Replay and interrogation depend on raw/summary separation staying in place. |
| `K-SYSTEM` | keep | Composed-system acceptance: dry run, replay, breaker trip, forged interaction, usage-limit midloop, secrets-in-logs. | End-to-end proof that the kept order path composes and fails closed; not replaceable by unit ports. |
| `K-UNTRUSTED` | keep | Untrusted-input handling: untrusted-content wrapping, canary scan, outbound scrubber, packet and prompt defences (SEC-02, SEC-09). | New feeds stay untrusted input under CLAUDE.md; this policy outlives any one feed or runtime. |
| `SUPPORT-K` | keep | Fixture support for kept tests in the same directory. | A conftest follows the strongest disposition of its sibling tests; it stays while any sibling stays. |
| `P-DECISION` | port | Cockpit consumer adapter and decision profile conformance (WP12, CSL-515). | Pipelines, budget ledger and scheduler are runtime surface (A2); behaviour is re-expressed against the consumer adapter at a pinned route-contract digest. |
| `P-EVIDENCE` | port | Evidence and replay contract generalized beyond SEC/equities (O2, CSL-214), raw inputs separate from summaries. | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. |
| `P-REHOME` | port | Same assertions, re-homed to a repo-level test location outside app/ (new-tree location set by WP7). | The test guards a path outside app/ (.github, .claude, tools/, docs/research); deleting app/ would silently stop guarding it. |
| `P-ROUTE` | port | Route Contract v0 conformance (WP6, proposed CSL-496): zero-tools, secret refusal, health injection, tree-kill, against the fake driver, with Cockpit's runner argv as the oracle. | Runner, Jev route and spawn behaviour are classified Recursion-owned runtime surface (A2); the oracle must exist and run red-to-green in the new tree before the legacy file can go. |
| `P-SURFACE` | port | OperatorSurface parity (A6; CP0 amendment, CSL-511): the same rendering, delivery, fallback and quant-view guarantees on the successor operator surface. | WP19 precondition is operator-surface parity; Discord and quant views are the current surface, not the product target. |
| `P-X1GUARD` | port | This inventory's own guard (tools/check_x1_inventory.py plus its pytest wrapper). | Runs red under unchanged job names today; the WP19 PR moves it to a direct CI step before app/ files go (see wiring note). |
| `SUPPORT-P` | port | Fixture support for ported tests in the same directory. | Moves with its siblings. |
| `NONE` | drop | None: superseded scope; history kept by tag pre-x1-cockpit-20261026 and docs/evidence. | Superseded by owner direction in SCOPE-AUDIT section 3; no successor oracle is named. |
<!-- x1-inventory:oracles:end -->

## 5. Pinned floors and what turns red

`tools/check_x1_inventory.py` (stdlib only, survives an `app/` deletion) validates this table. `app/tests/test_x1_cockpit_inventory.py` runs it inside the existing pytest step, so it runs red under the **unchanged** `linux` / `linux-gates` job names on every PR that runs the Linux test matrix. Floors pinned in the checker, exact at this head:

| Floor | Value |
|---|---|
| Rows (one per legacy test file) | 224 |
| `keep` rows | 136 |
| `port` rows | 77 |
| Test functions across `keep` and `port` rows | 2719 |

Red conditions, each covered by a negative control in the wrapper test: a legacy test file with no row; a row with no file (deleting a file before the rehearsal); a duplicate row; an unknown disposition or oracle; a disposition that conflicts with its oracle's class; an empty reason; a `keep` or `port` file collecting fewer test functions than its row; any floor above; a non-`drop` file importing a `drop` file; a `keep` file importing a `port` file that is not acknowledged in the coupling block (section 7); a stale or non-keep-to-port acknowledgement; a malformed table. Import detection covers absolute (`from tests.x import y`, `import tests.x`) and relative (`from .x import y`, `from . import x`, `from ..pkg import x`) forms.

A deliberate move or deletion must edit the table row and the floors in the same reviewed change. The checker does not prove that a ported oracle exists at its successor location yet: no successor location exists until WP7 and the route contract (WP6) land. That red-to-green evidence is a WP19 precondition, not claimed here.

### Wiring note for Lucas (no workflow edits in this PR)

The pytest wrapper needs no workflow change. Once WP19 moves or deletes files under `app/tests/`, the wrapper goes with them, so the WP19 PR should add one direct step to the existing `linux` job (same pattern as WP4's #283 step; it needs your own-words OK because it edits `.github/workflows/ci.yml`):

```yaml
      - name: X1 inventory guard
        run: python -X utf8 -B tools/check_x1_inventory.py
```

It uses no network, no new `uses:`, no new permissions and no new triggers. Job names and ids stay as they are.

## 6. Assumptions and unverified items

- The `Subject` column is informational only: copied from each module's docstring, not validated by the checker. The `Reason` and `Oracle` columns are validated for presence and class.
- Discovery covers every `test_*.py`, `*_test.py` and `conftest.py` under all of `app/`, and row paths may be any `app/**.py` path, so a test file outside `app/tests/` would be reported as unlisted rather than as a malformed table. None exists today.
- Static test counts do not expand parametrization; the real collected count is higher. The floor is deliberately a floor on functions.
- Not verified: Windows and macOS behaviour of the checker (it uses `pathlib` and the stdlib only, run locally on Linux with Python 3.12 only).
- Non-test support files under `app/tests/` (`__init__.py`, JSON and filing fixtures under `app/tests/filings/fixtures/` and `app/tests/strategies/fixtures/`) are not rows; they follow the tests that read them.
- The inventory covers `app/` only. Test files under `tools/` and `rust/` are outside the X1 legacy tree.

## 7. Couplings that block deletion (for WP19, not changed here)

Source code outside the tests still imports code that the `drop` rows would remove. Deleting those tests without resolving these would either break imports or leave dead code; the composition root and `app/research` are on or next to the order-path escalation list, so resolving them needs founder review.

- `app/main.py` lines 843 to 844 and 879 to 884 import and construct `CashMergerSemanticVerifier` and `CashMergerEdgeProvider` from `app/strategies/` (tests: `tests/strategies/*`, `tests/test_rw22_semantics*.py`).
- `app/research/empty_run.py` lines 13 to 15 import `app/feasibility/` (tests: `tests/feasibility/*`). `tests/workflows/test_research_update.py` (`port`) exercises `empty_run`.

Cross-disposition test imports, resolved for absolute and relative forms. A `drop` file may import anything. A non-`drop` file importing a `drop` file is red. A `keep` file importing a `port` file is red unless acknowledged in the block below; porting such a file would break the kept oracle, so each acknowledgement must be resolved (helper hoisted into a kept module, which edits kept tests and therefore needs a reviewed change) before the imported file moves. The four known cases Samir listed are acknowledged here rather than hoisted in this PR, because hoisting edits order-path-adjacent kept tests. Stale acknowledgements are red.

<!-- x1-inventory:couplings:begin -->
| Importer | Imports |
|---|---|
| `app/tests/ingest/test_rw23_bar_publication.py` | `app/tests/ingest/test_market_data.py` |
| `app/tests/ingest/test_rw23_bar_publication_adversarial.py` | `app/tests/ingest/test_market_data.py` |
| `app/tests/orchestrator/test_rw23_stream_health.py` | `app/tests/orchestrator/test_ingest_runner.py` |
| `app/tests/system/test_watchonly_durability.py` | `app/tests/orchestrator/test_ingest_runner.py` |
| `app/tests/system/test_watchonly_durability.py` | `app/tests/workflows/test_agent_calls.py` |
| `app/tests/workflows/test_execution_verification.py` | `app/tests/workflows/test_proposals.py` |
| `app/tests/workflows/test_rw22_risk_persistence.py` | `app/tests/workflows/test_proposals.py` |
| `app/tests/workflows/test_rw23_health_durability.py` | `app/tests/workflows/test_agent_calls.py` |
| `app/tests/workflows/test_rw23_scrub_sizing.py` | `app/tests/workflows/test_proposals.py` |
<!-- x1-inventory:couplings:end -->

Other cross-disposition imports (informational, not red): a `port` file importing kept fixtures, and a `drop` file importing kept fixtures.

| Importer | Disposition | Imports | Disposition |
|---|---|---|---|
| `bridge/test_bot.py` | port | `bridge/conftest.py` | keep |
| `bridge/test_embeds.py` | port | `bridge/conftest.py` | keep |
| `bridge/test_fallback.py` | port | `bridge/conftest.py` | keep |
| `strategies/test_merger_edge.py` | drop | `brokers/conftest.py` | keep |
| `strategies/test_merger_edge.py` | drop | `workflows/conftest.py` | keep |
| `strategies/test_merger_semantics.py` | drop | `workflows/conftest.py` | keep |
| `workflows/test_agent_calls.py` | port | `workflows/conftest.py` | keep |
| `workflows/test_budget.py` | port | `workflows/conftest.py` | keep |
| `workflows/test_context_limit.py` | port | `workflows/conftest.py` | keep |
| `workflows/test_events.py` | port | `workflows/conftest.py` | keep |
| `workflows/test_grounding.py` | port | `workflows/conftest.py` | keep |
| `workflows/test_interrogation.py` | port | `workflows/conftest.py` | keep |
| `workflows/test_jev.py` | port | `workflows/conftest.py` | keep |
| `workflows/test_morning_brew.py` | port | `workflows/conftest.py` | keep |
| `workflows/test_proposals.py` | port | `workflows/conftest.py` | keep |
| `workflows/test_rw23_budget_snapshot.py` | port | `workflows/conftest.py` | keep |
| `workflows/test_rw24_event_authority_adversarial.py` | port | `workflows/conftest.py` | keep |
| `workflows/test_rw24_event_verifier.py` | port | `workflows/conftest.py` | keep |
| `workflows/test_rw24_resume_adversarial.py` | port | `workflows/conftest.py` | keep |
| `workflows/test_triage_loop.py` | port | `workflows/conftest.py` | keep |

## 8. Inventory

Columns: path, static test-function count, disposition, oracle, reason, and the module's own docstring subject (from the source, for reviewer orientation).

<!-- x1-inventory:rows:begin -->
| Path | Tests | Disposition | Oracle | Reason | Subject |
|---|---|---|---|---|---|
| `app/tests/agent_runner/test_account_guard.py` | 13 | port | `P-ROUTE` | Credential starvation and secret refusal are conformance cases; Inv 2/4 bind the current runner until amended. | W11 (b) — decision-agent credential starvation (SEC-05 / Inv 4). |
| `app/tests/agent_runner/test_code_fence_parsing.py` | 11 | keep | `K-SCHEMA` | Fence handling decides whether output reaches schema validation; Inv 3 is independent of runtime. | BUG-034: whole-string Markdown fences are formatting, not schema bypasses. |
| `app/tests/agent_runner/test_limit_signal_boundary.py` | 19 | port | `P-ROUTE` | Vendor-health injection case: health must not be decided by model output. | RW-01 bring-up — vendor health may NOT be decided by the model's own output. |
| `app/tests/agent_runner/test_runner.py` | 25 | port | `P-ROUTE` | Mocked-subprocess runner behaviour is the zero-tools and argv oracle for the route contract. | WP1b G1b — hermetic mocked-subprocess tests for the agent runner (no real claude/codex). |
| `app/tests/agent_runner/test_rw20_codex_authority.py` | 4 | keep | `K-INV` | Codex barred from the decision/order path (Inv 2) until a separately reviewed redesign. | Independent BUG-119 attacks on actual argv inspection; no runtime is launched. |
| `app/tests/agent_runner/test_tool_inspector.py` | 19 | port | `P-ROUTE` | Deny-by-default tool/MCP argv inspection is the zero-tools conformance case. | W11 (c) — deny-by-default tool/MCP-arg inspector on the actual argv (SEC-06). |
| `app/tests/agent_runner/test_transport_resolve.py` | 10 | port | `P-ROUTE` | CLI spawnability on the deploy platform is a route-adapter precondition. | RW-01 bring-up — the runtime CLI must actually be spawnable on the DEPLOY platform. |
| `app/tests/agent_runner/test_transport_timeout.py` | 8 | port | `P-ROUTE` | Tree-kill on timeout is a named conformance case. | BUG-031: ``timeout_s`` must bound the entire spawned process tree. |
| `app/tests/benchmarks/test_perf_compare.py` | 16 | keep | `K-CI` | Decision rules for the CI benchmarks job that reads app/benchmarks. | Decision rules for the non-blocking performance comparison. |
| `app/tests/bridge/conftest.py` | 0 | keep | `SUPPORT-K` | A conftest follows the strongest disposition of its sibling tests; it stays while any sibling stays. | WP6 bridge test fixtures. |
| `app/tests/bridge/test_anchor.py` | 8 | keep | `K-AUDIT` | Audit chain is a core engine under AGENTS.md; SEC-11 is the chief pre-live gate. | WP6 — SEC-11 external witness (anchor store). |
| `app/tests/bridge/test_approval.py` | 15 | keep | `K-HUMAN` | Approval and veto stay the only route to an order until separately amended. | WP6 — the approval gateway core (Inv 1/6; SEC-03/07/13). |
| `app/tests/bridge/test_approval_race.py` | 2 | keep | `K-HUMAN` | Approval and veto stay the only route to an order until separately amended. | WP6 approval hardening — the reproduced duplicate-execution race, now CLOSED (SEC-07). |
| `app/tests/bridge/test_bot.py` | 8 | port | `P-SURFACE` | WP19 precondition is operator-surface parity; Discord and quant views are the current surface, not the product target. | WP6 — Discord adapter guards, including an isolated dependency-import failure. |
| `app/tests/bridge/test_bot_tree.py` | 6 | port | `P-SURFACE` | WP19 precondition is operator-surface parity; Discord and quant views are the current surface, not the product target. | WP6/QT-09 — construct the real Discord command tree without login or network. |
| `app/tests/bridge/test_deliver.py` | 9 | port | `P-SURFACE` | WP19 precondition is operator-surface parity; Discord and quant views are the current surface, not the product target. | RW-01 — the ``deliver(channel, payload) -> bool`` seam and its Discord adapter. |
| `app/tests/bridge/test_embeds.py` | 5 | port | `P-SURFACE` | WP19 precondition is operator-surface parity; Discord and quant views are the current surface, not the product target. | WP6 — deterministic embed rendering (SEC-17). |
| `app/tests/bridge/test_fallback.py` | 3 | port | `P-SURFACE` | WP19 precondition is operator-surface parity; Discord and quant views are the current surface, not the product target. | WP6 — Discord-outage fallback (SEC-23). |
| `app/tests/bridge/test_g6_acceptance.py` | 10 | keep | `K-HUMAN` | Approval and veto stay the only route to an order until separately amended. | WP6 — G6 ACCEPTANCE gate (Inv 1/2/6; SEC-01/03/07/11/23). |
| `app/tests/bridge/test_operators.py` | 7 | keep | `K-HUMAN` | Approval and veto stay the only route to an order until separately amended. | WP6 — operator allowlist. |
| `app/tests/bridge/test_quant_surface.py` | 31 | port | `P-SURFACE` | WP19 precondition is operator-surface parity; Discord and quant views are the current surface, not the product target. | QT-09 — the quant engine on the Discord operator surface. |
| `app/tests/bridge/test_rw22_render_adversarial.py` | 3 | keep | `K-RISK` | Persisted risk-ceiling rendering and signed monetary facts. | Independent RW-22 attack cases for the persisted risk ceiling renderer. |
| `app/tests/bridge/test_rw22_risk_facts.py` | 4 | keep | `K-RISK` | Persisted risk-ceiling rendering and signed monetary facts. | BUG-136: signed monetary facts survive persistence but still detect repainting. |
| `app/tests/bridge/test_veto.py` | 9 | keep | `K-HUMAN` | Approval and veto stay the only route to an order until separately amended. | WP6 — the auto_veto veto-window (Inv 1/6; SEC-01/23). |
| `app/tests/bridge/test_witness_adapters.py` | 31 | keep | `K-AUDIT` | Audit chain is a core engine under AGENTS.md; SEC-11 is the chief pre-live gate. | W10 SEC-11 (a) REGRESSION — the concrete out-of-domain witness adapters. |
| `app/tests/brokers/conftest.py` | 0 | keep | `SUPPORT-K` | A conftest follows the strongest disposition of its sibling tests; it stays while any sibling stays. | WP7 broker-test isolation + a fully-approved order scenario builder. |
| `app/tests/brokers/test_alpaca_paper.py` | 14 | keep | `K-ORDER` | Order-path code stays; any change needs an adversarial-review round and founder decision, so its tests cannot be dropped or silently moved. | WP7 unit tests — the Alpaca paper broker adapter (injectable seam, no network). |
| `app/tests/brokers/test_bug148_sell_backing.py` | 11 | keep | `K-ORDER` | Order-path code stays; any change needs an adversarial-review round and founder decision, so its tests cannot be dropped or silently moved. | BUG-148: prices cannot manufacture shares or release outstanding sell reservations. |
| `app/tests/brokers/test_capability.py` | 6 | keep | `K-ORDER` | Order-path code stays; any change needs an adversarial-review round and founder decision, so its tests cannot be dropped or silently moved. | WP7 shared seam — single-use submit capability token (SEC-03). |
| `app/tests/brokers/test_g7_acceptance.py` | 11 | keep | `K-ORDER` | Order-path code stays; any change needs an adversarial-review round and founder decision, so its tests cannot be dropped or silently moved. | WP7 G-gate ACCEPTANCE tests — the order path is the ONE gate (Inv 1/6; SEC-01/03/04/07/08). |
| `app/tests/brokers/test_gateway.py` | 30 | keep | `K-ORDER` | Order-path code stays; any change needs an adversarial-review round and founder decision, so its tests cannot be dropped or silently moved. | WP7 unit tests — the OrderGateway order path (Inv 1; SEC-01/03/04/07/08). |
| `app/tests/brokers/test_gateway_hardening.py` | 12 | keep | `K-ORDER` | Order-path code stays; any change needs an adversarial-review round and founder decision, so its tests cannot be dropped or silently moved. | WP7 gateway hardening — the reproduced 4c Invariant-1 bypass, now CLOSED (SEC-03/SEC-12). |
| `app/tests/brokers/test_mcp_untrusted.py` | 44 | keep | `K-UNTRUSTED` | MCP responses are prompt-injection input (SEC-02). | W12 SEC-02 — MCP-response content is a first-class prompt-injection input. |
| `app/tests/brokers/test_order_state_machine.py` | 18 | keep | `K-ORDER` | Order-path code stays; any change needs an adversarial-review round and founder decision, so its tests cannot be dropped or silently moved. | WP7 unit tests — the order-lifecycle FSM (G7; Inv 6). |
| `app/tests/brokers/test_order_state_machine_adversarial.py` | 8 | keep | `K-ORDER` | Order-path code stays; any change needs an adversarial-review round and founder decision, so its tests cannot be dropped or silently moved. | Independent adversarial checks for the F1/F2 Order persistence contract. |
| `app/tests/brokers/test_order_tracer.py` | 9 | keep | `K-ORDER` | Order-path code stays; any change needs an adversarial-review round and founder decision, so its tests cannot be dropped or silently moved. | W11 (a) — the always-on runtime order-authority tracer (SEC-03 / P-B). |
| `app/tests/brokers/test_order_tuple.py` | 9 | keep | `K-ORDER` | Order-path code stays; any change needs an adversarial-review round and founder decision, so its tests cannot be dropped or silently moved. | WP7 shared seam — canonical order tuple + hash (SEC-07). |
| `app/tests/brokers/test_paper_execution.py` | 25 | keep | `K-ORDER` | Order-path code stays; any change needs an adversarial-review round and founder decision, so its tests cannot be dropped or silently moved. | Real-paper composition seam: only stored, approved, human-confirmed orders may submit. |
| `app/tests/brokers/test_paper_performance.py` | 5 | keep | `K-AUDIT` | Performance signals come only from the verified audit chain. | W-22 performance signals come only from the verified audit chain. |
| `app/tests/brokers/test_paper_reconciliation.py` | 7 | keep | `K-ORDER` | Order-path code stays; any change needs an adversarial-review round and founder decision, so its tests cannot be dropped or silently moved. | RW-15 paper broker polling: terminal convergence and lost-response healing. |
| `app/tests/brokers/test_pending_exposure_chain.py` | 12 | keep | `K-AUDIT` | Pending-exposure cap is chain-anchored (SEC-07). | WP8 Owner C / G8 — SEC-07 residual chain-anchoring of the pending-exposure cap. |
| `app/tests/brokers/test_robinhood_mcp.py` | 8 | keep | `K-ORDER` | MCP broker stub must route through the gated path (Inv 1). | WP7 unit tests — the Robinhood Agentic (MCP) stub routes through the gated path (Inv 1). |
| `app/tests/brokers/test_rw20_authority_adversarial.py` | 6 | keep | `K-ORDER` | Order-path code stays; any change needs an adversarial-review round and founder decision, so its tests cannot be dropped or silently moved. | Independent RW-20 authority attacks against an isolated, deliberately synthetic setup. |
| `app/tests/brokers/test_rw23_gate_population.py` | 5 | keep | `K-ORDER` | Order-path code stays; any change needs an adversarial-review round and founder decision, so its tests cannot be dropped or silently moved. | BUG-153: gate population consists of decisions, not approval lifecycle writes. |
| `app/tests/brokers/test_rw23_sell_adversarial.py` | 13 | keep | `K-ORDER` | Order-path code stays; any change needs an adversarial-review round and founder decision, so its tests cannot be dropped or silently moved. | Independent BUG-148 probes: reservation overlap, ambiguous submits and bad positions. |
| `app/tests/brokers/test_submit_result_consumption.py` | 13 | keep | `K-ORDER` | Order-path code stays; any change needs an adversarial-review round and founder decision, so its tests cannot be dropped or silently moved. | BUG-052 / BUG-062 — the submit path must CONSUME the broker's answer. |
| `app/tests/config/test_env_file_guard.py` | 28 | keep | `K-SECRETS` | Secrets handling is on the escalate list; the guard stays. | RW-10 — `.env` carries durable NON-SECRET config, and provably nothing else (Inv 4). |
| `app/tests/conftest.py` | 0 | keep | `SUPPORT-K` | A conftest follows the strongest disposition of its sibling tests; it stays while any sibling stays. | WP0 test fixtures — an in-memory keyring so secret round-trips run on any CI host |
| `app/tests/feasibility/test_evaluate.py` | 16 | drop | `NONE` | Frozen slice A feasibility protocol for the superseded merger candidate; evidence stays in docs/evidence and docs/research. | RW-28 — frozen metrics and fail-closed slice A evaluation. |
| `app/tests/feasibility/test_freeze.py` | 13 | drop | `NONE` | Frozen slice A feasibility protocol for the superseded merger candidate; evidence stays in docs/evidence and docs/research. | RW-27 — frozen slice A shortlist and fail-closed contract validation. |
| `app/tests/feasibility/test_inspect.py` | 12 | drop | `NONE` | Frozen slice A feasibility protocol for the superseded merger candidate; evidence stays in docs/evidence and docs/research. | RW-29 — bounded operational-case inspection and missing-evidence ledger. |
| `app/tests/feasibility/test_protocol.py` | 13 | drop | `NONE` | Frozen slice A feasibility protocol for the superseded merger candidate; evidence stays in docs/evidence and docs/research. | RW-30 — pre-measurement and model-judge protocol freeze. |
| `app/tests/feasibility/test_screens.py` | 21 | drop | `NONE` | Frozen slice A feasibility protocol for the superseded merger candidate; evidence stays in docs/evidence and docs/research. | RW-37 — GET-only screen ledger and cohort/label plan. |
| `app/tests/filings/test_llm_free_gate.py` | 9 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | RW-10 (GR-10) — the `no-LLM-in-filings` gate is not weakened by reading code-only. |
| `app/tests/filings/test_primary_doc_filename.py` | 2 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | Precursor regression (folded into the WP5 phase): the citation EDGAR URL must use the |
| `app/tests/filings/test_provenance.py` | 5 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | RW-10 (GR-10) — `app/filings/PROVENANCE.md` is enforced, not merely asserted. |
| `app/tests/filings/test_sweep_regressions.py` | 11 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | Cockpit regressions for the 2026-08-09 sweep's vendored-tree fixes. |
| `app/tests/filings/test_wp3_cik_map.py` | 6 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | WP3 tests — ``edgar/cik_map.py``: SEC company_tickers.json -> ticker -> zero-padded CIK. |
| `app/tests/filings/test_wp3_companyfacts.py` | 5 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | WP3 tests — ``edgar/companyfacts.py``: fetch + deterministic flatten of XBRL facts. |
| `app/tests/filings/test_wp3_fetch.py` | 18 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | WP3 acceptance tests — ``edgar/fetch.py``: the single sec.gov chokepoint (Invariant 4). |
| `app/tests/filings/test_wp3_poll.py` | 23 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | WP3 acceptance tests — ``edgar/submissions_poll.py``: the per-CIK submissions poller. |
| `app/tests/filings/test_wp4_4c_regressions.py` | 16 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | WP4 regression tests for the heavy-4c adversarial-review defects (all LLM-free, offline). |
| `app/tests/filings/test_wp4_golden_deltas.py` | 10 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | WP4 golden-fixture tests — hand-verified delta sets over the real filing pairs. |
| `app/tests/filings/test_wp4_structural.py` | 13 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | WP4 structural invariant gates (deterministic core) — the scaffold's WP4 Accept structural |
| `app/tests/filings/test_wp5_4c_regressions.py` | 21 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | WP5 heavy-4c regression tests — the two hardening gaps the adversarial pass surfaced. |
| `app/tests/filings/test_wp5_corruption.py` | 6 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | WP5 acceptance — CORRUPTION DETECTION (scaffold WP5 Accept clause 2; playbook G5). |
| `app/tests/filings/test_wp5_determinism.py` | 3 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | WP5 acceptance — DETERMINISM (scaffold WP5 Accept clause 6; playbook G5). |
| `app/tests/filings/test_wp5_digest.py` | 8 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | WP5 acceptance — DIGEST (scaffold WP5 Accept clause 5; playbook G5). |
| `app/tests/filings/test_wp5_orphan_reuse.py` | 5 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | WP5 acceptance — ORPHAN SWEEP + citation_id REUSE (scaffold WP5 Accept clauses 3 & 4). |
| `app/tests/ingest/conftest.py` | 0 | keep | `SUPPORT-K` | A conftest follows the strongest disposition of its sibling tests; it stays while any sibling stays. | Surface CSL-236 results in quiet pytest output. |
| `app/tests/ingest/test_admission_rust_parity.py` | 42 | keep | `K-RUST` | The Rust crates are not X1 scope; their parity tests guard them where they run in CI. | CSL-42 — Rust dedup and token bucket must match the Python admission controls. |
| `app/tests/ingest/test_alpaca_news.py` | 9 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | WP3 — Alpaca news consumer: replay, dedup, and reconnect-with-backoff over a drop. |
| `app/tests/ingest/test_bug157_daily_universe.py` | 8 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | BUG-157: quote discovery extends daily readiness without repeating verified work. |
| `app/tests/ingest/test_calendars.py` | 7 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | WP3 — calendar pull normalizes to provenance-tagged MarketEvents. |
| `app/tests/ingest/test_characterization.py` | 14 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | RW-19 deterministic tests for read-only event-source evidence. |
| `app/tests/ingest/test_edgar_brew.py` | 111 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | W20 — EDGAR-via-brew feed: one event per accession, canonical "filing", untrusted-text |
| `app/tests/ingest/test_edgar_pipeline.py` | 36 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | RW-10 — the in-process EDGAR pipeline, driven end-to-end over recorded SEC fixtures. |
| `app/tests/ingest/test_edgar_wiring.py` | 12 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | RW-11 production reachability for the in-process EDGAR extraction path. |
| `app/tests/ingest/test_finnhub_poll.py` | 7 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | WP3 — Finnhub poller: rate-limit never exceeded, minId cursor advances, dedup. |
| `app/tests/ingest/test_finra_short_interest.py` | 57 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | W20 — FINRA short-interest / Reg SHO threshold connector tests. |
| `app/tests/ingest/test_market_data.py` | 13 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | RW-14 market-data cache, time-domain, persistence, and metric regressions. |
| `app/tests/ingest/test_normalize.py` | 14 | keep | `K-UNTRUSTED` | Role-marker and homoglyph neutralization of ingested text. | WP3 — normalize/escape: role-marker + homoglyph neutralization, source allowlist, |
| `app/tests/ingest/test_normalize_rust_parity.py` | 19 | keep | `K-RUST` | The Rust crates are not X1 scope; their parity tests guard them where they run in CI. | CSL-42 — Rust normalization must match Python, including the raw/normalized split. |
| `app/tests/ingest/test_rw23_bar_publication.py` | 2 | keep | `K-STORE` | Failed signing must not leave an unsigned write-once cache. | BUG-152: failed signing cannot leave an unsigned write-once daily cache. |
| `app/tests/ingest/test_rw23_bar_publication_adversarial.py` | 7 | keep | `K-STORE` | Publication, restart and substitution attacks on the signed daily cache. | Independent publication, restart and substitution review for BUG-152. |
| `app/tests/ingest/test_strict_freshness_messages.py` | 2 | port | `P-EVIDENCE` | SCOPE-AUDIT: strong reusable test oracles, stock-centric implementation; the oracle ports to the generalized envelope. | CSL-236 — lock the strict-freshness ``EdgarPipelineRunError`` messages. |
| `app/tests/orchestrator/conftest.py` | 0 | keep | `SUPPORT-K` | A conftest follows the strongest disposition of its sibling tests; it stays while any sibling stays. | Orchestrator test isolation. |
| `app/tests/orchestrator/test_api.py` | 21 | keep | `K-GATE` | These are the escalate-list control paths; fail-closed behaviour must stay proven in place. | WP5 builder smoke tests for the FastAPI shell — auth, the ladder/gate guard on /mode, |
| `app/tests/orchestrator/test_breaker_sampling.py` | 3 | keep | `K-GATE` | These are the escalate-list control paths; fail-closed behaviour must stay proven in place. | BUG-058 — an unmeasured breaker signal must be visibly absent, never silently healthy. |
| `app/tests/orchestrator/test_core.py` | 25 | keep | `K-GATE` | These are the escalate-list control paths; fail-closed behaviour must stay proven in place. | WP5 self-tests — proving the deterministic orchestrator core compiles and the happy |
| `app/tests/orchestrator/test_g5_acceptance.py` | 36 | keep | `K-GATE` | These are the escalate-list control paths; fail-closed behaviour must stay proven in place. | G5 ACCEPTANCE SUITE — the mode-ladder guard (playbook §5 G5; Inv 1/2/7). |
| `app/tests/orchestrator/test_g5_api_acceptance.py` | 12 | keep | `K-GATE` | These are the escalate-list control paths; fail-closed behaviour must stay proven in place. | G5 ACCEPTANCE SUITE — the control-plane API contract (criterion 8; §6 auth + SEC-15). |
| `app/tests/orchestrator/test_ingest_runner.py` | 13 | port | `P-EVIDENCE` | Poller-to-persist runner is runtime surface (A2) feeding the evidence store. | RW-01 — the ingest runner: WP3 pollers -> ``workflows.events.persist_events``. |
| `app/tests/orchestrator/test_mode_ladder_boot_and_expiry.py` | 4 | keep | `K-GATE` | These are the escalate-list control paths; fail-closed behaviour must stay proven in place. | BUG-059 / BUG-061 — the mode oracle's two unguarded edges. |
| `app/tests/orchestrator/test_oob_kill.py` | 18 | keep | `K-GATE` | These are the escalate-list control paths; fail-closed behaviour must stay proven in place. | W12 / SEC-04 — the OUT-OF-BAND, fail-closed kill switch (G12 repro regressions). |
| `app/tests/orchestrator/test_quant_api.py` | 14 | port | `P-SURFACE` | Loopback quant routes are a current operator view, not order authority. | QT-09 — the /quant routes over real HTTP, through the real key gate. |
| `app/tests/orchestrator/test_quant_ws.py` | 16 | port | `P-SURFACE` | Read-only quant WebSocket is a current operator view. | QT-08 — the read-only quant WebSocket. |
| `app/tests/orchestrator/test_reconcile.py` | 16 | keep | `K-AUDIT` | Audit chain is a core engine under AGENTS.md; SEC-11 is the chief pre-live gate. | W12 G12 SEC-13 CLOSURE REGRESSION — the two-phase atomic-audit + completeness invariant. |
| `app/tests/orchestrator/test_rw20_restart_pressure.py` | 3 | keep | `K-GATE` | These are the escalate-list control paths; fail-closed behaviour must stay proven in place. | Restart pressure against isolated DB/HALT paths; no production recovery or ledger repair. |
| `app/tests/orchestrator/test_rw23_stream_health.py` | 10 | keep | `K-GATE` | These are the escalate-list control paths; fail-closed behaviour must stay proven in place. | BUG-151: live delivery health cannot confuse a failed write with a quiet feed. |
| `app/tests/orchestrator/test_scheduler.py` | 20 | port | `P-DECISION` | Scheduler wiring is named Recursion-owned runtime surface (A2). | WP5 builder smoke tests for the scheduler wiring — the job callables are exercised |
| `app/tests/orchestrator/test_sec11_anchor.py` | 8 | keep | `K-AUDIT` | Audit chain is a core engine under AGENTS.md; SEC-11 is the chief pre-live gate. | WP6 SEC-11 CLOSURE REGRESSION — the replayable-watermark rollback the WP5 build deferred. |
| `app/tests/orchestrator/test_wp5_hardening.py` | 8 | keep | `K-GATE` | These are the escalate-list control paths; fail-closed behaviour must stay proven in place. | WP5 HARDENING REGRESSION SUITE — the four reproduced bypasses from the script-4c |
| `app/tests/quant/book/test_book_batch.py` | 13 | keep | `K-RUST` | The Rust crates are not X1 scope; their parity tests guard them where they run in CI. | CSL-42 — one batch call agrees with single calls and with the Python book. |
| `app/tests/quant/book/test_lob.py` | 31 | keep | `K-QUANT` | SCOPE-AUDIT classes it a useful experiment surface; PnL, costs and autoloop are escalate-list paths, so keep until a founder-reviewed decision. | QT-01 — limit order book, driven by hand-built sequences with known-correct answers. |
| `app/tests/quant/book/test_matching.py` | 30 | keep | `K-QUANT` | SCOPE-AUDIT classes it a useful experiment surface; PnL, costs and autoloop are escalate-list paths, so keep until a founder-reviewed decision. | QT-01 — FIFO price-time matching, against hand-built books with checkable arithmetic. |
| `app/tests/quant/book/test_queue_invariants.py` | 5 | keep | `K-QUANT` | SCOPE-AUDIT classes it a useful experiment surface; PnL, costs and autoloop are escalate-list paths, so keep until a founder-reviewed decision. | QT-01 — randomized differential sweep: the real book vs an independent naive model. |
| `app/tests/quant/book/test_rust_parity.py` | 12 | keep | `K-RUST` | The Rust crates are not X1 scope; their parity tests guard them where they run in CI. | CSL-42 — the Rust book and matcher agree with the Python reference. |
| `app/tests/quant/feed/test_feeds.py` | 22 | keep | `K-QUANT` | SCOPE-AUDIT classes it a useful experiment surface; PnL, costs and autoloop are escalate-list paths, so keep until a founder-reviewed decision. | QT-03 — feed gates: point-in-time ordering, determinism, and replay provenance. |
| `app/tests/quant/strategies/conftest.py` | 0 | keep | `SUPPORT-K` | A conftest follows the strongest disposition of its sibling tests; it stays while any sibling stays. | Shared builders for strategy tests. |
| `app/tests/quant/strategies/test_market_maker.py` | 26 | keep | `K-QUANT` | Prototype strategy still imported by the loopback quant API and CLI; a qualified strategy suite does not exist, so classify as experiment surface rather than drop. | QT-02 — Avellaneda-Stoikov market maker gates. |
| `app/tests/quant/strategies/test_momentum.py` | 14 | keep | `K-QUANT` | Prototype strategy still imported by the loopback quant API and CLI; a qualified strategy suite does not exist, so classify as experiment surface rather than drop. | QT-02 — order-flow-imbalance momentum gates. |
| `app/tests/quant/strategies/test_pairs.py` | 15 | keep | `K-QUANT` | Prototype strategy still imported by the loopback quant API and CLI; a qualified strategy suite does not exist, so classify as experiment surface rather than drop. | QT-02 — pairs / stat-arb gates. |
| `app/tests/quant/test_costs_rust_parity.py` | 20 | keep | `K-RUST` | The Rust crates are not X1 scope; their parity tests guard them where they run in CI. | CSL-42 — Rust costs and PnL must match the Python Decimal implementation exactly. |
| `app/tests/quant/test_events.py` | 23 | keep | `K-QUANT` | SCOPE-AUDIT classes it a useful experiment surface; PnL, costs and autoloop are escalate-list paths, so keep until a founder-reviewed decision. | QT-01 — event schema gates. |
| `app/tests/quant/test_execution.py` | 31 | keep | `K-QUANT` | SCOPE-AUDIT classes it a useful experiment surface; PnL, costs and autoloop are escalate-list paths, so keep until a founder-reviewed decision. | QT-07 — Tier C: the autonomous loop, its latency model, and its containment. |
| `app/tests/quant/test_oms.py` | 22 | keep | `K-QUANT` | SCOPE-AUDIT classes it a useful experiment surface; PnL, costs and autoloop are escalate-list paths, so keep until a founder-reviewed decision. | QT-02 — order management gates. |
| `app/tests/quant/test_pnl.py` | 22 | keep | `K-QUANT` | SCOPE-AUDIT classes it a useful experiment surface; PnL, costs and autoloop are escalate-list paths, so keep until a founder-reviewed decision. | QT-04 — PnL attribution and cost-model gates. |
| `app/tests/quant/test_runner.py` | 20 | keep | `K-QUANT` | SCOPE-AUDIT classes it a useful experiment surface; PnL, costs and autoloop are escalate-list paths, so keep until a founder-reviewed decision. | QT-03 — the event loop, including the one result that justifies the whole engine. |
| `app/tests/quant/test_rw23_chronology.py` | 11 | keep | `K-QUANT` | SCOPE-AUDIT classes it a useful experiment surface; PnL, costs and autoloop are escalate-list paths, so keep until a founder-reviewed decision. | BUG-155/156: exchange mutations and observer notifications have separate clocks. |
| `app/tests/quant/test_signals.py` | 21 | keep | `K-QUANT` | SCOPE-AUDIT classes it a useful experiment surface; PnL, costs and autoloop are escalate-list paths, so keep until a founder-reviewed decision. | QT-02 — online estimator gates. |
| `app/tests/quant/test_signals_rust_parity.py` | 21 | keep | `K-RUST` | The Rust crates are not X1 scope; their parity tests guard them where they run in CI. | CSL-42 — Rust online estimators must match ``quant.signals``. |
| `app/tests/quant/test_sweep_regressions.py` | 14 | keep | `K-QUANT` | SCOPE-AUDIT classes it a useful experiment surface; PnL, costs and autoloop are escalate-list paths, so keep until a founder-reviewed decision. | Regressions for the 2026-08-09 sweep's quant-sim fixes. |
| `app/tests/quant/test_verify.py` | 6 | keep | `K-QUANT` | SCOPE-AUDIT classes it a useful experiment surface; PnL, costs and autoloop are escalate-list paths, so keep until a founder-reviewed decision. | Shared verifier integrity tests after W-21 retires displayed-depth economics. |
| `app/tests/quant/test_w21_cost_model.py` | 18 | keep | `K-QUANT` | SCOPE-AUDIT classes it a useful experiment surface; PnL, costs and autoloop are escalate-list paths, so keep until a founder-reviewed decision. | W-21 failure proofs for the ADV/volatility transaction-cost model. |
| `app/tests/risk/test_engine.py` | 18 | keep | `K-RISK` | app/risk/ stays pure and deterministic; its oracle must keep running where the code lives. | WP4 G4 — risk engine: clean approve, every hard check blocks, max-sizing -> human, |
| `app/tests/risk/test_execution_gate.py` | 19 | keep | `K-RISK` | app/risk/ stays pure and deterministic; its oracle must keep running where the code lives. | QT-06 — Tier B: the execution verdict as a real pre-trade check inside risk/. |
| `app/tests/schemas/test_agent_job.py` | 1 | keep | `K-SCHEMA` | Output validation is a standing invariant independent of which runtime produces the output. | WP1a — AgentJob round-trip test (in-memory sqlite). |
| `app/tests/schemas/test_approval_record.py` | 2 | keep | `K-SCHEMA` | Output validation is a standing invariant independent of which runtime produces the output. | WP1a round-trip test for the ``ApprovalRecord`` model. |
| `app/tests/schemas/test_audit_entry.py` | 1 | keep | `K-SCHEMA` | Output validation is a standing invariant independent of which runtime produces the output. | WP1a — round-trip test for the ``AuditEntry`` ledger model. |
| `app/tests/schemas/test_automation_rule.py` | 1 | keep | `K-SCHEMA` | Output validation is a standing invariant independent of which runtime produces the output. | WP1a — AutomationRule round-trip test. |
| `app/tests/schemas/test_breaker_state.py` | 1 | keep | `K-SCHEMA` | Output validation is a standing invariant independent of which runtime produces the output. | (no module docstring) |
| `app/tests/schemas/test_compiled_rule.py` | 2 | keep | `K-SCHEMA` | Output validation is a standing invariant independent of which runtime produces the output. | WP1a — JSON-Schema round-trip test for the CompiledRule agent-output contract. |
| `app/tests/schemas/test_grounding_check.py` | 2 | keep | `K-SCHEMA` | Output validation is a standing invariant independent of which runtime produces the output. | WP1a round-trip test for the GroundingCheck agent-output schema. |
| `app/tests/schemas/test_interrogation_answer.py` | 4 | keep | `K-SCHEMA` | Output validation is a standing invariant independent of which runtime produces the output. | WP1a — JSON-Schema round-trip test for InterrogationAnswer (agent output). |
| `app/tests/schemas/test_market_event.py` | 1 | keep | `K-SCHEMA` | Output validation is a standing invariant independent of which runtime produces the output. | WP1a — round-trip test for the MarketEvent model. |
| `app/tests/schemas/test_migration.py` | 2 | keep | `K-STORE` | Replay and interrogation depend on raw/summary separation staying in place. | WP1a G1a gate — the whole schema migrates: importing every model registers its |
| `app/tests/schemas/test_mode_state.py` | 2 | keep | `K-SCHEMA` | Output validation is a standing invariant independent of which runtime produces the output. | (no module docstring) |
| `app/tests/schemas/test_morning_brief.py` | 2 | keep | `K-SCHEMA` | Output validation is a standing invariant independent of which runtime produces the output. | WP1a — JSON-Schema round-trip test for the MorningBrief agent output. |
| `app/tests/schemas/test_portfolio_snapshot.py` | 2 | keep | `K-SCHEMA` | Output validation is a standing invariant independent of which runtime produces the output. | WP1a — PortfolioSnapshot round-trip test. |
| `app/tests/schemas/test_risk_decision.py` | 1 | keep | `K-SCHEMA` | Output validation is a standing invariant independent of which runtime produces the output. | WP1a round-trip test for the RiskDecision model. |
| `app/tests/schemas/test_thesis_update.py` | 2 | keep | `K-SCHEMA` | Output validation is a standing invariant independent of which runtime produces the output. | (no module docstring) |
| `app/tests/schemas/test_trade_proposal.py` | 11 | keep | `K-SCHEMA` | Output validation is a standing invariant independent of which runtime produces the output. | WP1a — round-trip validation for the TradeProposal agent-output JSON Schema. |
| `app/tests/schemas/test_triage_result.py` | 5 | keep | `K-SCHEMA` | Output validation is a standing invariant independent of which runtime produces the output. | WP1a — JSON-Schema round-trip test for the TriageResult agent-output contract. |
| `app/tests/schemas/test_usage_window.py` | 1 | keep | `K-SCHEMA` | Output validation is a standing invariant independent of which runtime produces the output. | (no module docstring) |
| `app/tests/schemas/test_xbrl_fact_dedup.py` | 3 | port | `P-EVIDENCE` | XBRL-specific idempotency backstop; generalizes with the evidence envelope. | BUG-095 — the Inv-11 idempotency backstop must cover INSTANT facts. |
| `app/tests/storage/test_audit.py` | 20 | keep | `K-AUDIT` | Audit chain is a core engine under AGENTS.md; SEC-11 is the chief pre-live gate. | WP2 G2 — audit chain: verify, tamper detection (incl. id + created_at coverage), |
| `app/tests/storage/test_parquet.py` | 8 | keep | `K-STORE` | Replay and interrogation depend on raw/summary separation staying in place. | WP2 — Parquet snapshots: lossless round-trip, stable + edit-sensitive content_hash, |
| `app/tests/storage/test_repositories.py` | 1 | keep | `K-STORE` | Replay and interrogation depend on raw/summary separation staying in place. | WP2 — repository add/get/list/count round-trip. |
| `app/tests/storage/test_rw20_concurrency_pressure.py` | 5 | keep | `K-AUDIT` | Audit chain is a core engine under AGENTS.md; SEC-11 is the chief pre-live gate. | Independent RW-20 pressure tests; every ledger is an isolated temporary SQLite DB. |
| `app/tests/storage/test_sec11_hwm_signing_refusal.py` | 7 | keep | `K-AUDIT` | Audit chain is a core engine under AGENTS.md; SEC-11 is the chief pre-live gate. | BUG-124 — refuse missing/invalid established high-water at the signing boundary. |
| `app/tests/storage/test_sec11_seq_monotonic.py` | 6 | keep | `K-AUDIT` | Audit chain is a core engine under AGENTS.md; SEC-11 is the chief pre-live gate. | W10 SEC-11 (b) REGRESSION — monotonic, NON-REUSABLE seq closes the WP8 delete-then-heal hole. |
| `app/tests/strategies/test_merger_edge.py` | 25 | drop | `NONE` | Cash-merger strategy is no longer the product's defining path; negative results stay in docs/evidence. | W-23 cash-merger evidence, deterministic math, and fail-closed attacks. |
| `app/tests/strategies/test_merger_semantics.py` | 36 | drop | `NONE` | Cash-merger strategy is no longer the product's defining path; negative results stay in docs/evidence. | W-24 prospective semantic authority and composed-path reachability. |
| `app/tests/strategies/test_rw21_real_filing_grammar.py` | 15 | drop | `NONE` | CONDITIONAL drop (Samir, PR #287 comment 6425530222): refusal regressions on untrusted filing text (RW-21, BUG-146). Drops only if the WP19 PR shows the same fail-closed behaviour covered by a K-UNTRUSTED or P-EVIDENCE file; otherwise reclassify as port to P-EVIDENCE. | Independent RW-21 refuse regressions and pinned-source replays. |
| `app/tests/strategies/test_rw23_semantic_boundaries.py` | 8 | drop | `NONE` | CONDITIONAL drop (Samir, PR #287 comment 6425530222): refusal regressions on untrusted filing text (RW-21, BUG-146). Drops only if the WP19 PR shows the same fail-closed behaviour covered by a K-UNTRUSTED or P-EVIDENCE file; otherwise reclassify as port to P-EVIDENCE. | BUG-146: markup boundaries cannot end the scope of a source qualification. |
| `app/tests/swarm/test_bounded_job.py` | 22 | port | `P-REHOME` | Tests docs/research/swarm-tools helpers (preview-only scoped spawn, bounded job); frozen Devin helper stays for audit. | RW-34 bounded-job controller. Fake children first; one real admitted fixture. |
| `app/tests/swarm/test_scoped_spawn.py` | 11 | port | `P-REHOME` | Tests docs/research/swarm-tools helpers (preview-only scoped spawn, bounded job); frozen Devin helper stays for audit. | RW-33 offline previews and filing admission. No model job is dispatched. |
| `app/tests/swarm/test_swarm_tools.py` | 7 | port | `P-REHOME` | Tests docs/research/swarm-tools helpers (preview-only scoped spawn, bounded job); frozen Devin helper stays for audit. | Deterministic tests for Cloud swarm spawn/intake helpers. No Devin spawn. |
| `app/tests/system/conftest.py` | 0 | keep | `SUPPORT-K` | A conftest follows the strongest disposition of its sibling tests; it stays while any sibling stays. | WP9 — system-test fixtures (Owner A substrate). |
| `app/tests/system/test_breaker_trip_halts.py` | 4 | keep | `K-SYSTEM` | End-to-end proof that the kept order path composes and fails closed; not replaceable by unit ports. | G9 system test — a DELIBERATE breaker trip HALTS the next order before the broker. |
| `app/tests/system/test_forged_interaction.py` | 1 | keep | `K-SYSTEM` | End-to-end proof that the kept order path composes and fails closed; not replaceable by unit ports. | WP9 / G9 — A FORGED INTERACTION IS REJECTED (the dedicated forged-interaction gate). |
| `app/tests/system/test_one_market_day_dryrun.py` | 2 | keep | `K-SYSTEM` | End-to-end proof that the kept order path composes and fails closed; not replaceable by unit ports. | G9 — ONE-MARKET-DAY DRY RUN on the COMPOSED system (WP9 system-test author: dry-run owner). |
| `app/tests/system/test_paper_replay.py` | 9 | keep | `K-SYSTEM` | End-to-end proof that the kept order path composes and fails closed; not replaceable by unit ports. | G9 — the 2-WEEK PAPER REPLAY acceptance gate (WP9 flagship). |
| `app/tests/system/test_rw01_market_day.py` | 29 | keep | `K-SYSTEM` | End-to-end proof that the kept order path composes and fails closed; not replaceable by unit ports. | GR-01 — ONE MARKET DAY through the REAL composed cockpit (docs/refactor-plan.md §5, RW-01). |
| `app/tests/system/test_secrets_in_logs.py` | 2 | keep | `K-SYSTEM` | End-to-end proof that the kept order path composes and fails closed; not replaceable by unit ports. | WP9 / G9 — SECRETS-IN-LOGS GREP = 0 (the RUNTIME analog of the static secrets-in-repo gate). |
| `app/tests/system/test_usage_limit_midloop.py` | 2 | keep | `K-SYSTEM` | End-to-end proof that the kept order path composes and fails closed; not replaceable by unit ports. | G9 — USAGE-LIMIT HIT MID-LOOP -> WATCH-ONLY, at the COMPOSED-SYSTEM level (WP9). |
| `app/tests/system/test_w12_boundaries.py` | 3 | keep | `K-SYSTEM` | End-to-end proof that the kept order path composes and fails closed; not replaceable by unit ports. | W12 G12 SYSTEM boundaries — SEC-02 / SEC-04 / SEC-13 proven on the COMPOSED cockpit. |
| `app/tests/system/test_watchonly_durability.py` | 25 | keep | `K-SYSTEM` | End-to-end proof that the kept order path composes and fails closed; not replaceable by unit ports. | BUG-159 D1-D4 durability and final broker-entry refusal contracts. |
| `app/tests/test_audioop_warning_filter.py` | 1 | keep | `K-CI` | Pins the pytest filterwarnings entry in app/pyproject.toml. | The pytest audioop filter ignores one third-party warning and nothing else. |
| `app/tests/test_bugfix_audit_2026_09_07.py` | 39 | keep | `K-ORDER` | Regression pins across approval, veto, broker and embeds fixes. | Regression pins for the 2026-09-07 bug-hunt fixes (re-audit of cf2bcec). |
| `app/tests/test_claude_settings.py` | 12 | port | `P-REHOME` | Guards .claude/settings.json and tools/ test paths. | Guard the committed Claude Code session policy in .claude/settings.json (CSL-350). |
| `app/tests/test_cloud_session_hook.py` | 10 | port | `P-REHOME` | Guards .claude/hooks/session-start.sh. | Regression tests for the Claude Code cloud-session hook (.claude/hooks/session-start.sh). |
| `app/tests/test_invariants.py` | 12 | keep | `K-INV` | The nine invariants stay binding until a separately reviewed amendment; these files are their executable gates. | Invariant gates, wired from WP0 so every later WP lands behind them. |
| `app/tests/test_linear_pr_sync.py` | 13 | port | `P-REHOME` | Guards .github/scripts/linear_pr_sync.py and its workflow wiring. | Unit tests for Linear PR-sync comments. |
| `app/tests/test_numeric.py` | 5 | keep | `K-RISK` | Shared fail-closed Decimal parse contract used on the risk path. | CSL-327 — pin the shared fail-closed Decimal parse contract. |
| `app/tests/test_o7_operator_contract.py` | 69 | keep | `K-REGISTRY` | WP4 (PR #283) pins this file at its current path with a floor. | Provider-free checks for the CSL-219 O7 candidate. |
| `app/tests/test_replacement_program_registry.py` | 8 | keep | `K-REGISTRY` | WP4 (PR #283) pins this file at its current path with a floor; deleting it must turn linux-gates red. | (no module docstring) |
| `app/tests/test_rw22_autoloop.py` | 3 | keep | `K-QUANT` | Auto-loop fill-evidence and accounting refusal (escalate-list path). | RW-22: full venue sequences must preserve fill evidence and refuse invalid accounting. |
| `app/tests/test_rw22_limits_recovery.py` | 12 | keep | `K-GATE` | Committed limits history and vendor recovery (BUG-126, BUG-131). | RW-22: committed limits history and genuine vendor recovery (BUG-126 / BUG-131). |
| `app/tests/test_rw22_review_adversarial.py` | 2 | keep | `K-AUDIT` | Signed-prefix reader controls; imports fixtures from test_rw22_limits_recovery, so both must stay together. | Independent review controls for the RW-22 signed-prefix reader. |
| `app/tests/test_rw22_semantics.py` | 5 | drop | `NONE` | Cash-merger source-qualification semantics (BUG-140). | RW-22 / BUG-140: source qualifications cannot become verified cash terms. |
| `app/tests/test_rw22_semantics_independent.py` | 4 | drop | `NONE` | Cash-settlement exception review for the merger candidate. | Independent RW-22 adversarial review of the cash-settlement exception. |
| `app/tests/test_stuck_thread_watch.py` | 48 | port | `P-REHOME` | Guards .github/scripts/stuck_thread_watch.py and its workflow wiring. | Unit tests for the in-repo stuck-thread watcher / Codex mediator. |
| `app/tests/test_wp0.py` | 6 | keep | `K-INV` | App imports cleanly, settings default read_only, secrets round-trip. | WP0 acceptance — app imports cleanly; settings default read_only; secrets round-trip |
| `app/tests/test_x1_cockpit_inventory.py` | 21 | port | `P-X1GUARD` | Runs red under unchanged job names today; the WP19 PR moves it to a direct CI step before app/ files go (see wiring note). | WP14 X1 inventory guard: every legacy test file has exactly one disposition row. |
| `app/tests/tools/test_provision_merger_edge.py` | 4 | port | `P-REHOME` | Tests a script under tools/, outside the app/ tree; the script's own disposition is a separate decision. | Regression tests for W-23 operator-tool reporting. |
| `app/tests/tools/test_rw11_companyfacts.py` | 13 | port | `P-REHOME` | Tests a script under tools/, outside the app/ tree; the script's own disposition is a separate decision. | Hermetic tests for the RW-11 prospective Company Facts capture. |
| `app/tests/tools/test_rw11_precision.py` | 15 | port | `P-REHOME` | Tests a script under tools/, outside the app/ tree; the script's own disposition is a separate decision. | Hermetic tests for the RW-11 filing-precision evidence tool. |
| `app/tests/tools/test_rw11_text_population.py` | 13 | port | `P-REHOME` | Tests a script under tools/, outside the app/ tree; the script's own disposition is a separate decision. | Hermetic tests for the witnessed RW-11 prospective text-population collector. |
| `app/tests/tools/test_rw19_autolaunch.py` | 30 | port | `P-REHOME` | Tests a script under tools/, outside the app/ tree; the script's own disposition is a separate decision. | Hermetic tests for the RW-19 one-shot autolaunch scheduler helper. |
| `app/tests/tools/test_rw19_capture.py` | 6 | port | `P-REHOME` | Tests a script under tools/, outside the app/ tree; the script's own disposition is a separate decision. | Hermetic protocol tests for the RW-19 evidence-generating CLI. |
| `app/tests/tools/test_rw19_news_reconnect_plan_bound.py` | 39 | port | `P-REHOME` | Tests a script under tools/, outside the app/ tree; the script's own disposition is a separate decision. | Hermetic tests for the prospective, plan-bound RW-19 news reconnect collector. |
| `app/tests/tools/test_rw19_preflight.py` | 40 | port | `P-REHOME` | Tests a script under tools/, outside the app/ tree; the script's own disposition is a separate decision. | Hermetic tests for the RW-19 safety-preflight generator. |
| `app/tests/workflows/conftest.py` | 0 | keep | `SUPPORT-K` | A conftest follows the strongest disposition of its sibling tests; it stays while any sibling stays. | WP8 workflow test fixtures (G8 substrate). |
| `app/tests/workflows/test_agent_calls.py` | 11 | port | `P-DECISION` | Pipelines, budget ledger and scheduler are runtime surface (A2); behaviour is re-expressed against the consumer adapter at a pinned route-contract digest. | G8 — agent-call choke-point tests (Owner B). |
| `app/tests/workflows/test_budget.py` | 20 | port | `P-DECISION` | Pipelines, budget ledger and scheduler are runtime surface (A2); behaviour is re-expressed against the consumer adapter at a pinned route-contract digest. | G8 — SEC-21 deep-budget + deterministic-urgency tests |
| `app/tests/workflows/test_context_limit.py` | 29 | port | `P-DECISION` | Pipelines, budget ledger and scheduler are runtime surface (A2); behaviour is re-expressed against the consumer adapter at a pinned route-contract digest. | CSL-42 — context cap, Haiku decision routing, and the per-call cost log. |
| `app/tests/workflows/test_events.py` | 10 | port | `P-EVIDENCE` | Ingest-to-storage persistence bridge (SEC-13, Inv 8). | G8 — ingest→storage persistence bridge tests (SEC-13 / Invariant 8, |
| `app/tests/workflows/test_execution_verification.py` | 22 | keep | `K-QUANT` | W-21 execution-cost verification wired into proposals; cost model is an escalate-list path. | W-21 execution-cost verification wired into the real proposal pipeline. |
| `app/tests/workflows/test_g8_acceptance.py` | 8 | port | `P-DECISION` | Pipelines, budget ledger and scheduler are runtime surface (A2); behaviour is re-expressed against the consumer adapter at a pinned route-contract digest. | G8 acceptance gate — WP8 workflows end-to-end, driven ONLY by the frozen contract (Owner D). |
| `app/tests/workflows/test_grounding.py` | 29 | port | `P-DECISION` | Pipelines, budget ledger and scheduler are runtime surface (A2); behaviour is re-expressed against the consumer adapter at a pinned route-contract digest. | G8 — SEC-14 fail-closed grounding tests (docs/security-hardening-backlog.md SEC-14; |
| `app/tests/workflows/test_interrogation.py` | 5 | port | `P-DECISION` | Pipelines, budget ledger and scheduler are runtime surface (A2); behaviour is re-expressed against the consumer adapter at a pinned route-contract digest. | G8 — SEC-18 fresh-context interrogation tests (Owner B). |
| `app/tests/workflows/test_jev.py` | 42 | port | `P-ROUTE` | Jev HTTP route with Claude fallback is named Recursion-owned runtime surface (A2). | CSL-191 — Jev as a paper/read-only decision runtime, with Claude fallback. |
| `app/tests/workflows/test_limits_extract.py` | 7 | keep | `K-GATE` | These are the escalate-list control paths; fail-closed behaviour must stay proven in place. | G8 — orchestrator/limits.py extraction tests (Owner B). |
| `app/tests/workflows/test_main_compose.py` | 11 | keep | `K-SYSTEM` | Composition root and API wiring. | G8 — composition-root + API wiring tests (Owner B). |
| `app/tests/workflows/test_morning_brew.py` | 18 | port | `P-DECISION` | Pipelines, budget ledger and scheduler are runtime surface (A2); behaviour is re-expressed against the consumer adapter at a pinned route-contract digest. | G8 — morning brew pipeline tests (Owner B). |
| `app/tests/workflows/test_packets.py` | 6 | keep | `K-UNTRUSTED` | New feeds stay untrusted input under CLAUDE.md; this policy outlives any one feed or runtime. | G8 — packet builder tests (docs/prompt-injection-defense.md §4 M2/M3 + SEC-22 |
| `app/tests/workflows/test_prompts.py` | 9 | keep | `K-UNTRUSTED` | New feeds stay untrusted input under CLAUDE.md; this policy outlives any one feed or runtime. | G8 — prompt template + loader unit tests (docs/prompt-injection-defense.md §4 M2; |
| `app/tests/workflows/test_proposals.py` | 12 | port | `P-DECISION` | Pipelines, budget ledger and scheduler are runtime surface (A2); behaviour is re-expressed against the consumer adapter at a pinned route-contract digest. | G8 — proposal-drafting pipeline tests (Owner B). |
| `app/tests/workflows/test_research_update.py` | 3 | port | `P-DECISION` | Pipelines, budget ledger and scheduler are runtime surface (A2); behaviour is re-expressed against the consumer adapter at a pinned route-contract digest. | RW-35 empty-run research update through the real composition. No LLM. |
| `app/tests/workflows/test_rw22_risk_persistence.py` | 1 | keep | `K-RISK` | app/risk/ stays pure and deterministic; its oracle must keep running where the code lives. | The real drafting path signs the same conservative cents that it persists. |
| `app/tests/workflows/test_rw23_budget_snapshot.py` | 7 | port | `P-DECISION` | Pipelines, budget ledger and scheduler are runtime surface (A2); behaviour is re-expressed against the consumer adapter at a pinned route-contract digest. | BUG-154: budget authority shares one SQLite snapshot, including the spend write. |
| `app/tests/workflows/test_rw23_health_durability.py` | 2 | keep | `K-GATE` | Vendor health faults survive a composed restart. | BUG-147: every vendor health fault survives a composed restart on either runtime. |
| `app/tests/workflows/test_rw23_raw_news.py` | 3 | keep | `K-STORE` | Raw payload and model-facing summary stay distinct (Inv 8). | BUG-150: raw source payload and bounded model-facing summary are distinct. |
| `app/tests/workflows/test_rw23_scrub_sizing.py` | 2 | keep | `K-UNTRUSTED` | New feeds stay untrusted input under CLAUDE.md; this policy outlives any one feed or runtime. | BUG-149: a missing size must not skip the pre-persistence prose scrub. |
| `app/tests/workflows/test_rw24_event_authority_adversarial.py` | 1 | port | `P-DECISION` | Pipelines, budget ledger and scheduler are runtime surface (A2); behaviour is re-expressed against the consumer adapter at a pinned route-contract digest. | Independent RW24 check: cached triage corroboration cannot outlive spend authority. |
| `app/tests/workflows/test_rw24_event_verifier.py` | 12 | port | `P-DECISION` | Pipelines, budget ledger and scheduler are runtime surface (A2); behaviour is re-expressed against the consumer adapter at a pinned route-contract digest. | RW24: one verified triage snapshot, with fresh authority at the next tick/spend. |
| `app/tests/workflows/test_rw24_resume_adversarial.py` | 2 | port | `P-DECISION` | Pipelines, budget ledger and scheduler are runtime surface (A2); behaviour is re-expressed against the consumer adapter at a pinned route-contract digest. | Independent composed-control regressions for the resumed RW-24 repair. |
| `app/tests/workflows/test_schema_loader.py` | 4 | keep | `K-SCHEMA` | Output validation is a standing invariant independent of which runtime produces the output. | G8 — schema_loader unit tests (Invariant 3, docs/build-scaffold-ultracode.md WP8). |
| `app/tests/workflows/test_scrubber.py` | 12 | keep | `K-UNTRUSTED` | New feeds stay untrusted input under CLAUDE.md; this policy outlives any one feed or runtime. | G8 — SEC-09 outbound scrubber tests (docs/security-hardening-backlog.md SEC-09: |
| `app/tests/workflows/test_sec11_domain_row_binding.py` | 10 | keep | `K-AUDIT` | Event type bound into the signed chain. | W10 SEC-11 (c) REGRESSION — MarketEvent.event_type is bound into the signed chain. |
| `app/tests/workflows/test_services.py` | 7 | port | `P-DECISION` | Pipelines, budget ledger and scheduler are runtime surface (A2); behaviour is re-expressed against the consumer adapter at a pinned route-contract digest. | G8 — service-registry + EOD review tests (Owner B). |
| `app/tests/workflows/test_triage_loop.py` | 31 | port | `P-DECISION` | Pipelines, budget ledger and scheduler are runtime surface (A2); behaviour is re-expressed against the consumer adapter at a pinned route-contract digest. | G8 — intraday triage-loop tests (Owner B). |
<!-- x1-inventory:rows:end -->

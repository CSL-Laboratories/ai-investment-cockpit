# O8 phase A — legacy disposition and matched benchmark protocol

**Outcome:** O8, migration and measured performance frontier.
**Linear:** [CSL-220](https://linear.app/csl-laboratories/issue/CSL-220/define-the-migration-and-measured-performance-frontier).
**Phase:** A only. Phase B, the architecture and migration decision, stays human.
**Maturity claimed: `researched`.**
**Completion gate: `pending_outcome_artifact`.**

This packet is a candidate contract. It classifies the inspected tree, freezes the hard floors, and defines the matched comparison that a later measurement would have to satisfy. It does not rewrite the engine, select a language, storage layout, or topology, add a CI cadence, or run a benchmark.

Not claimed: `human_decided`, `contract_frozen`, `prototyped`, `implemented`, `provider_free_verified`, `demo_or_paper_qualified`, `shadow_qualified`, `limited_live_qualified`, `release_qualified`, qualification, or a completion verdict. The checked-in validator accepting this fixture is not an outcome completion. Completion still requires an exact measured candidate, a retained oracle, negative controls, and an independent rerun.

Inspection commit: `ebbaa34489f171e079834d02098135711e1d4755` (`origin/main` at authoring).
Registry baseline, unchanged: `73ab78d9d69494c757cec875855121b1d29320a4`.
The four accepted CSL-212 documents and their digests in `docs/replacement/program.json` are not modified.

Machine contract:

- [fixtures/phase-a-packet.json](fixtures/phase-a-packet.json)
- [schemas/phase-a-packet.schema.json](schemas/phase-a-packet.schema.json)
- [schemas/benchmark-manifest.schema.json](schemas/benchmark-manifest.schema.json)
- [schemas/candidate-manifest.schema.json](schemas/candidate-manifest.schema.json)

Verifier command, provider-free, no network and no credentials:

```bash
python -X utf8 -B tools/check_o8_phase_a.py
python -X utf8 -B tools/check_o8_phase_a.py --negative-controls
```

## Legacy disposition

Rows are `retain`, `measure`, `rework`, `replace`, or `undecided`. Nothing is `replace`. A replace row would claim tier-3 evidence this packet does not have. Compatibility is what a later seam has to preserve. Full evidence statements are in the fixture; the locators below are the ones the validator resolves on disk or records as citations.

| Component | Disposition | Paths | Evidence | Compatibility |
| --- | --- | --- | --- | --- |
| risk-engine | retain | `app/risk/` | `app/risk/__init__.py`, `app/tests/test_invariants.py`, `CLAUDE.md` invariant 5 | Pure checks stay the order-path floor. A faster candidate may not change financial semantics or add I/O or a model call. |
| order-gateway | retain | `app/brokers/gateway.py` | `OrderGateway`, `CLAUDE.md` invariant 1 | Orders still reach a broker only through risk, mode, approval, and `OrderGateway`. |
| audit-ledger | retain | `app/storage/audit.py` | Hash-chained HMAC ledger, `app/tests/storage/test_audit.py` | Audit stays in tests. A timer does not replace chain verification. |
| fail-closed-breakers | retain | `app/orchestrator/breakers.py` | Watch-only disables the order path, `CLAUDE.md` invariant 7 | Vendor limit, stale data, and a hung orchestrator stay `watch_only`. |
| secrets-store | retain | `app/core/secrets.py` | OS credential store wrapper, invariant 4 grep | This packet adds no credential. |
| raw-versus-summary-split | retain | `app/filings/raw_store.py` | Write-once raw bytes, `CLAUDE.md` invariant 8 | Raw bytes stay separate from summaries. The module docstring's retired invariant number is not this floor id. |
| tier1-hot-path-tripwire | retain | `app/benchmarks/`, `.github/workflows/ci.yml` | Five hot-path cases; median minus relative IQR; exit 0; does not fail `linux-gates` | Leave the tripwire as it is. It is not CSL-42 evidence. |
| raw-store-implementation | measure | `app/filings/raw_store.py` | Store exists; no tier-2 ingest measurement is recorded here | A later replacement keeps write-once raw bytes and the hash bind. |
| equity-ingest | measure | `app/ingest/` | Alpaca, Finnhub, EDGAR brew, FINRA; scope audit calls this stock-centric | Feeds stay untrusted input. No multi-market envelope is selected. |
| point-in-time-replay | measure | `app/quant/feed/synthetic.py`, `app/tests/system/test_paper_replay.py` | Point-in-time ordering exists; no matched replay measurement is recorded here | A faster replay may not rebuild the past from later data. |
| paper-order-lifecycle | measure | `app/brokers/paper_execution.py`, `app/brokers/order_state_machine.py`, `app/quant/execution/sim_venue.py` | Paper path and `SimVenue` exist and are not live-qualified | Lifecycle stays risk, human gate, SimVenue, hash-chain audit. |
| reconciliation | measure | `app/brokers/paper_reconciliation.py`, `app/orchestrator/reconcile.py` | Startup reconciliation fails closed on an intent without a result | No cutover until that seam reconciles. |
| frozen-evaluation | measure | `app/feasibility/evaluate.py` | `classify()` is fail-closed, with no LLM and no broker | Populations and thresholds stay frozen before results are read. |
| quant-book-and-optional-rust | measure | `app/quant/book/`, `rust/cockpit-book/`, `rust/cockpit-py/` | Optional PyO3 binding; missing extension keeps the Python book | One batch parity check is not a whole-system comparison. |
| operator-discord-and-loopback | rework | `app/bridge/`, `app/orchestrator/app.py` | Discord and loopback exist; scope audit records no GUI or unified CLI | Rework names the gap. Toolkit, CLI shape, and TUI stay unresolved. No second order path. |
| llm-workflows | undecided | `app/workflows/` | Schema-validated awareness workflows | Models stay off the order and risk paths. Provider choice is not made here. |
| merger-strategy-experiment | undecided | `app/strategies/`, `app/feasibility/` | Scope audit: recorded screens produced no qualified product acceptance | Research discipline stays. The strategy is not a deletion target. |
| tax-and-prediction-markets | undecided | `docs/product-tax-contract.md`, scope audit | No tax package and no accepted event-contract implementation in the inspected tree | Eligibility stays with the owner. |
| wholesale-rewrite-csl-42 | undecided | `app/benchmarks/compare.py` | CSL-42 proposes a rewrite; current comparison is five in-process hot paths | No language, storage, or topology is selected. |

`rework` on the operator surfaces records that Discord plus loopback is not the successor GUI and CLI. It does not choose a UI stack.

## Hard-floor registry

Floors are enforced by tests. `benchmark_eligible` is false on every floor. A candidate that omits a floor, or that moves a floor into a benchmark, is rejected.

Lucas authorized this bounded locator amendment in nine-item decision-list item 5;
the attributed coordination record is [CSL-220](https://linear.app/csl-laboratories/issue/CSL-220#comment-6ca1c352-7b09-4e4d-af89-4dae647e9024).
It preserves every floor and does not constitute exact-digest contract acceptance.
Each floor now carries `test_locators`, the complete ordered array below, instead
of a production-module `test_locator`. Candidate, packet, attached-floor and
receipt validation require the registered array and resolve every named top-level
test function in a repository-contained `app/tests/**/test_*.py` file. Production
files, directories, URLs, escapes, file-only references, missing functions and
partial/duplicate selections are rejected. Validation parses source without
importing or executing tests. Pytest must independently collect and run the listed
selectors; a parameterized function selects all its cases.

Run a listed selector from the repository root with
`python -m pytest -q app/tests/path/test_module.py::test_function`, passing all
selectors required by a floor as separate arguments. The real test command,
exact candidate and actual results still belong in independently retained evidence.
The amended synthetic controls use `*-test-cases.json`; the old synthetic
candidate and floor receipts remain byte-for-byte historical controls and do not
validate as current floor evidence. No receipt is retroactively promoted.

**Coverage limitation:** the timeout selector proves an agent timeout enters
`watch_only`; the out-of-band HALT selector proves the next submit is refused.
Neither proves automatic detection of a hung orchestrator. No heartbeat-expiry
implementation/test was found in this inspected tree. The invariant's hung
orchestrator requirement remains binding and unqualified; this amendment does
not implement a watchdog, declare the whole floor complete, or permit a PASS.
The follow-up remains in [CSL-312](https://linear.app/csl-laboratories/issue/CSL-312).

| Floor | Required pytest function selectors (all parameter cases) |
| --- | --- |
| invariant_1_single_order_path | `app/tests/test_invariants.py::test_invariant_1_order_calls_only_in_brokers`<br>`app/tests/brokers/test_gateway.py::test_b_mode_without_order_authority_refuses`<br>`app/tests/brokers/test_gateway.py::test_d_toctou_risk_flip_to_fail_voids_and_records_bypass`<br>`app/tests/brokers/test_gateway.py::test_e_consumed_approval_replay_refused`<br>`app/tests/brokers/test_gateway.py::test_g_no_token_refuses_at_capability`<br>`app/tests/system/test_forged_interaction.py::test_g9_forged_interaction_is_rejected` |
| invariant_2_decision_pinned_in_auto_modes | `app/tests/orchestrator/test_g5_acceptance.py::test_g5_3_arm_refuses_non_claude_decision_stage_explicit`<br>`app/tests/orchestrator/test_g5_acceptance.py::test_g5_3_sec08_execution_runtime_refused_even_after_valid_arm`<br>`app/tests/brokers/test_gateway.py::test_f_auto_mode_non_claude_runtime_refused` |
| invariant_3_schema_validate_model_output | `app/tests/agent_runner/test_runner.py::test_decision_job_requires_schema`<br>`app/tests/agent_runner/test_runner.py::test_repair_success`<br>`app/tests/workflows/test_agent_calls.py::test_still_malformed_output_quarantines_job` |
| invariant_4_secrets_os_credential_store | `app/tests/test_wp0.py::test_secret_roundtrip_via_keyring`<br>`app/tests/test_invariants.py::test_invariant_4_no_plaintext_secrets_in_app_or_operator_tools`<br>`app/tests/workflows/test_agent_calls.py::test_provisioned_secret_in_the_prompt_is_refused`<br>`app/tests/system/test_secrets_in_logs.py::test_g9_no_secrets_or_pii_in_logs` |
| invariant_5_risk_pure | `app/tests/test_invariants.py::test_invariant_5_risk_engine_has_no_llm`<br>`app/tests/test_invariants.py::test_the_risk_engine_itself_did_not_acquire_an_impure_import`<br>`app/tests/test_invariants.py::test_the_execution_verifier_stays_pure_because_risk_imports_it` |
| invariant_6_human_gate_hash_chain_audit | `app/tests/system/test_forged_interaction.py::test_g9_forged_interaction_is_rejected`<br>`app/tests/storage/test_audit.py::test_append_and_verify`<br>`app/tests/storage/test_audit.py::test_tamper_is_detected`<br>`app/tests/storage/test_audit.py::test_truncation_detected_by_watermark`<br>`app/tests/storage/test_audit.py::test_completeness_catches_unaudited_state` |
| invariant_7_fail_closed | `app/tests/workflows/test_agent_calls.py::test_limit_hit_enters_watch_only_once_and_mode_unchanged`<br>`app/tests/workflows/test_agent_calls.py::test_timeout_enters_watch_only`<br>`app/tests/orchestrator/test_g5_acceptance.py::test_g5_4_stale_market_data_goes_watch_only_not_review_live`<br>`app/tests/brokers/test_gateway.py::test_b_watch_only_refuses`<br>`app/tests/orchestrator/test_oob_kill.py::test_out_of_band_cli_set_refuses_next_submit_before_broker` |
| invariant_8_raw_inputs_separate_from_summaries | `app/tests/workflows/test_rw23_raw_news.py::test_long_original_news_is_recoverable_without_entering_prompt`<br>`app/tests/ingest/test_characterization.py::test_evidence_writer_separates_raw_and_summary_and_hash_binds_them` |
| invariant_9_post_pdt | `app/tests/test_o8_floor_regressions.py::test_post_pdt_reg_t_exact_equity_boundary`<br>`app/tests/test_o8_floor_regressions.py::test_post_pdt_has_no_day_trade_counter_gate` |
| fail_closed | `app/tests/workflows/test_agent_calls.py::test_limit_hit_enters_watch_only_once_and_mode_unchanged`<br>`app/tests/workflows/test_agent_calls.py::test_timeout_enters_watch_only`<br>`app/tests/orchestrator/test_g5_acceptance.py::test_g5_4_stale_market_data_goes_watch_only_not_review_live`<br>`app/tests/brokers/test_gateway.py::test_b_watch_only_refuses`<br>`app/tests/orchestrator/test_oob_kill.py::test_out_of_band_cli_set_refuses_next_submit_before_broker` |
| hash_chain_audit | `app/tests/storage/test_audit.py::test_append_and_verify`<br>`app/tests/storage/test_audit.py::test_tamper_is_detected`<br>`app/tests/storage/test_audit.py::test_truncation_detected_by_watermark`<br>`app/tests/storage/test_audit.py::test_completeness_catches_unaudited_state` |

`fail_closed` and `hash_chain_audit` repeat invariants 7 and 6 under the names this outcome uses, so a candidate cannot drop them by renaming.

The checked-in candidate id is `phase-a-protocol-only`. Its maturity is `researched`. Language, storage, and topology are not selected. It changes no financial, authority, failure, or recovery semantics, and it is not CSL-42 evidence.

The candidate manifest also carries `smallest_experiment`, `seam`, and `rollback` as required checked fields, so the smallest next experiments and the rollback path the contract names live in the manifest, not only in this prose. This candidate's smallest experiment and seam are the `ingest_to_raw_store` tier-2 workload — ingest into `RawStore` sits upstream of the other five workloads. Its rollback routes that seam back to legacy with the raw store and hash chain as the system of record.

## Matched benchmark protocol

Devin's 2026-10-16 note on CSL-220 is the tier split this packet records. It is a planning note, not a measurement.

**Tier 1, unchanged.** `app/benchmarks/` times `risk_evaluate`, `order_tuple_hash`, `schema_validate_proposal`, `outbound_scrub`, and `audit_append_verify`. The pull-request job uses 5 rounds and 2 orderings. A case is a regression only when median slowdown minus relative IQR stays above 0.10. The process exits 0, and the job does not fail `linux-gates`. This packet does not add per-PR benchmark gating and does not change that job.

Tier 1 does not include end-to-end I/O or helper processes. It cannot decide CSL-42.

**Tier 2, defined and unmeasured.** Provider-free tracked workloads. Cadence is `unresolved_release_owner`. They are not per-PR. Each manifest must name the commit, host, repetitions, relative IQR, included I/O, the workload's registered helper processes, and a labeled `provider_latency`. Unmeasured fields in this packet use status `required_on_run` and JSON null. Its Python runtime remains pinned as a full version string (`3.12.3`); `required_on_run` means the host has not been measured.

`provider_latency` is a required labeled field, never absent: `{"status": "excluded_with_reason", "reason": ...}` with a non-empty reason, and that is the only accepted shape. Every benchmark is provider-free (`provider_free` is true, `network_calls` is 0), so no provider latency exists to measure — a numeric value would be fictitious and the schema and validator reject it. All six workloads record `excluded_with_reason`. A manifest that leaves the field out, or writes a number, fails `omits provider latency` — the CSL-220 falsifier cannot be met by omission or invention.

| Workload | What a later run has to include |
| --- | --- |
| ingest_to_raw_store | Ingest worker and raw-store helper, disk I/O, frozen feed bytes into `RawStore`. |
| point_in_time_replay | Replay worker and raw-store helper, disk I/O, as-of ordering preserved. |
| frozen_evaluation_run | Evaluation worker and fixture reader, disk I/O, population frozen before results. |
| paper_order_lifecycle | Paper lifecycle, SimVenue, and audit appender. Risk, human gate, venue, hash chain. |
| reconciliation | Reconciliation worker, ledger reader, and audit verifier. |
| gui_cli_render | Render process and ledger reader. No GUI product exists in the inspected tree, so this workload cannot be measured until a surface exists. The manifest still requires I/O and helpers. |

The observation vector, required on a run and null until then: p50, p95, p99, throughput, RSS, CPU, I/O, tokens, cost, correctness, failure, recovery, and growth. Tokens and cost stay null when the workload does not expose them. Correctness, failure, and recovery are part of the record, not optional commentary.

Measured manifests and retained baseline outputs must include the same fixed `units`
object: `latency: ms` (p50/p95/p99),
`throughput: workload_completions_per_second`, `rss_bytes: bytes`,
`cpu: cpu_seconds`, `io: bytes`, `tokens: tokens`, and `cost: USD`.
One completion is one whole invocation of the registered workload, rather than an
individual record inside it. CPU records aggregate process CPU time; I/O records
aggregate bytes read and written, including the registered helpers. Units cannot
be changed between baseline and candidate; missing or different units are rejected.
Unmeasured null observations retain these definitions without claiming a run.

The first three workloads require `disk` in `io.kinds` even when `memory_copy` is
also included. The validator enforces this per-workload registry for both protocol
manifests and measured comparisons.

**Tier 3, the only CSL-42 evidence.** A tier-3 manifest may set `csl42_evidence` only when the tier is 3, floors are attached, variance status is `measured`, repetitions are an integer of at least 2, and relative IQR is a number. This packet's tier-3 result count is 0. No workload in the fixture claims that evidence.

Every evidence claim must name a retained `fixture` (`path`, `sha256`). The validator
hashes its actual bytes and requires that digest to match both `fixture_sha256`
and `baseline.fixture_sha256`; matching invented strings do not bind a comparison.
The single retained fixture is the shared frozen input for the matched comparison.
Missing, unreadable, incorrectly hashed or repository-escaping references fail closed.

Evidence also requires a retained `candidate` manifest reference (`path`, `sha256`)
and a nonempty `configuration` object. The validator hashes and validates that
manifest, then checks its `candidate_id`, exact `commit`, configuration and workload
seam against the benchmark. Candidate manifests used for measurements carry both
`commit` and `configuration`; the unmeasured phase-A planning candidate carries
neither. The digest pins all retained candidate fields, including configuration.
Configuration JSON must match exactly, including numeric versus boolean values.
Both new references use the same repository containment and cross-platform rooted
path rejection as retained baseline outputs.

The evidence binds further. Attached floors are the full eleven-row registry, validator-checked — `floors_attached` is not a bare boolean, so a manifest that asserts the flag without the registry fails `attached floors invalid`. The baseline is bound to retained outputs: `baseline.outputs` names a repo path and its sha256, and the validator hashes the file, so a baseline that names only caller-supplied identifiers fails `tier-3 baseline lacks retained outputs`. A measured variance requires a measured observation — measured helper processes and finite, non-negative metrics — else `tier-3 evidence lacks measured observation`; percentiles must order p50 ≤ p95 ≤ p99, else `impossible measured observation`. Non-finite JSON constants (`NaN`, `Infinity`) and exponent-overflowed numbers (`1e309`) are rejected at parse, and every measured metric — including `variance.relative_iqr` and optional `tokens`/`cost` — must be a finite, non-negative number. `baseline.outputs.path` must be a repo-relative path that resolves inside the repository: paths that are absolute or rooted on either POSIX or Windows — leading `/` or `\`, drive-absolute (`C:\x`, `C:/x`), drive-relative (`C:foo`), UNC (`\\server\share`, `//server/share`), or any backslash at all — fail `baseline outputs path must be relative`, and `..` escapes fail `baseline outputs path escapes the repository`. The rejection uses `PurePosixPath` and `PureWindowsPath` together ([pathlib docs](https://docs.python.org/3/library/pathlib.html): `PureWindowsPath.is_absolute`, drive and anchor semantics) so behavior is identical on every OS, and is applied before the `resolve()`/`is_relative_to(ROOT)` containment check on every packet-supplied path the validator joins to the repo root — the standard path-traversal countermeasure ([OWASP Path Traversal](https://owasp.org/www-community/attacks/Path_Traversal), [CWE-22](https://cwe.mitre.org/data/definitions/22.html)). The retained outputs file must carry a measured observation, so identifier-only files are not accepted as a baseline oracle. Each metric in that digest-bound file is a finite number paired with its protocol unit (`p50`/`p95`/`p99` in ms, throughput in workload completions per second, and the other fixed units). `status` must be `measured`. An object such as `{"garbage": true}` fails `tier-3 baseline observation is not measured` even when its sha256 matches. It must also record the measured `host.runner`, full `host.python` runtime version (at least major.minor.micro), and execution `configuration`. Those three fields have to equal the candidate manifest. A baseline measured on another machine, another Python runtime, or another configuration fails `tier-3 baseline host does not match the candidate`. Copying the eleven floor rows is not a safety result. CSL-42 evidence also requires retained `floor_results`: a digest-bound receipt whose `commit` is the candidate commit and whose rows pass every registered floor locator. A missing receipt, a receipt for another commit, or a failing row fails `tier-3 floor results are not bound to the candidate`. The checker binds that receipt. It does not execute the floor tests, and a synthetic receipt is not a measured pass. Helper processes are pinned per workload to the declared registry (names and roles from the table above) and compared as an ordered list of name→role pairs, so a manifest claiming a workload it does not exercise — or repeating a helper — fails `helper set does not match the workload`. The migration's `replay_from` and `shared_stores` each require two distinct members — `uniqueItems` in the packet schema and an explicit set check in the validator — because a duplicated member would silently drop one replay source or store. The packet's `research.parallel_search_ids` records all six searches actually run (two pinned at authoring plus the four added during review) and `cited_urls` their sources, so the claimed research below reconciles exactly with the frozen record.

Measured numbers, including integer tokens, cost, RSS and relative IQR, must also
fit the finite float range (at most `1.7976931348623157e308`). Integers above that
range are rejected without converting them to float or throwing an overflow error.

A measured run also records host status `measured` and I/O status `measured`. pyperf's own guidance is the reason the protocol keeps repetitions, worker processes, and a robust spread: [Run a benchmark](https://pyperf.readthedocs.io/en/stable/run_benchmark.html) spawns worker processes sequentially, discards warmup, and warns when the maximum sits far from the mean; [Analyze benchmark results](https://pyperf.readthedocs.io/en/latest/analyze.html) reports quartiles and recommends median and median absolute deviation when outliers remain. This repository already uses median and relative IQR in `app/benchmarks/compare.py`. The protocol keeps that spread. It does not add pyperf to CI.

## Migration, rollback, and mixed-version recovery

Pattern: strangler fig, as described by [Microsoft's strangler fig pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/strangler-fig). A façade routes one seam at a time. The façade is not an order authority (`facade_is_order_authority` is false). Orders continue to use the retained gateway path.

Seams, in either order once their upstream contracts exist: the six tier-2 workloads. Phase A decommissions nothing (`decommission_in_phase_a` is false, `big_bang_deletion` is false).

Shared stores during overlap: raw bytes and the hash-chain audit. Both sides read those stores. An anti-corruption translation may sit between legacy and new calls. It may not widen authority or change financial, failure, or recovery semantics. Microsoft's notes that apply here: shared data stores have to coexist, the façade must not become a bottleneck or a single point of failure, and cross-calls need an anti-corruption layer so the new side does not inherit legacy semantics by accident.

Rollback: while the legacy objects for a seam still exist, the façade routes that seam back to legacy. Raw bytes and the hash chain stay the system of record. Decommission requires a human receipt after reconciliation. Removing legacy objects is the step that makes rollback expensive, so this packet does not take it. The same reversibility sits in [Parallel Change](https://martinfowler.com/bliki/ParallelChange.html) — Fowler's expand-and-contract: each of the expand, migrate, and contract phases is its own small, independently releasable step, so the old version stays routable until every caller has moved. That is why the validator checks `route_back_to_legacy` per seam rather than trusting a plan note.

Mixed-version recovery: a generation tag is required once a seam is introduced. Recovery replays from raw inputs and the hash-chain audit. If generations disagree, the result is `watch_only`. `trade_on_disagreement` is false.

The [Enersys strangler-fig case study](https://enersys.co.th/en/insights/strangler-fig-migration-python-to-rust-2026) (18 Apr 2026) argues against big-bang rewrites and describes other companies' incremental Python-to-Rust cuts, including a Dropbox hot-path claim of about 75% CPU reduction. Those figures are that article's claims about other systems. They are not measurements of this repository and they are not CSL-42 evidence. [psf/pyperf](https://github.com/psf/pyperf) is the toolkit those pyperf documents describe. This packet does not pin or install it.

## Candidate options, none selected

The smallest next experiments are the six tier-2 workloads above, provider-free, on a frozen fixture, repeated until relative IQR is recorded with host and commit. The candidate manifest records them per candidate: `smallest_experiment` names the next workload run, `seam` names the migration seam it exercises, and `rollback` records the route back to legacy — all three are required and validator-checked. Options a later human decision could compare, after those runs:

- keep the Python tree and the optional PyO3 kernels;
- replace one measured seam behind the façade;
- a wholesale rewrite of the kind CSL-42 proposes.

This packet does not rank them and does not start the runs.

## Negative controls

Checked-in fixtures under `fixtures/negative/`. Each one must fail. The validator prints the designed failure so the Blacksmith log shows it.

| Fixture | Designed failure |
| --- | --- |
| `omit-io.json` | omits I/O |
| `omit-helpers.json` | omits helper processes |
| `omit-variance.json` | omits variance |
| `provider-latency-unlabeled.json` | omits provider latency |
| `candidate-missing-floors.json` | candidate missing floors |
| `floor-enforced-by-benchmark.json` | candidate missing floors |
| `seam-missing-rollback.json` | migration cannot roll back |
| `candidate-missing-smallest-experiment.json` | candidate missing smallest experiment |
| `missing-measured-observation.json` | tier-3 evidence lacks measured observation |
| `impossible-observation.json` | impossible measured observation |
| `baseline-unbound-outputs.json` | tier-3 baseline lacks retained outputs |
| `attached-floors-invalid.json` | attached floors invalid |
| `experiment-seam-mismatch.json` | candidate experiment does not exercise its seam |
| `non-finite-literal.json` | unreadable JSON |
| `overflow-literal.json` | unreadable JSON (exponent overflow `1e309` parses to a non-finite number) |
| `baseline-outputs-absolute.json` | baseline outputs path must be relative |
| `baseline-outputs-traversal.json` | baseline outputs path escapes the repository |
| `baseline-metadata-only.json` | tier-3 baseline lacks retained outputs (digest matches, but the retained file is identifiers only — no `observation` payload) |
| `baseline-observation-untyped.json` | tier-3 baseline observation is not measured (digest matches, but `observation` is `{"garbage": true}` rather than a number with its unit) |
| `helper-set-mismatch.json` | helper set does not match the workload |
| `replay-from-duplicate.json` | replay sources must be distinct |
| `shared-stores-duplicate.json` | shared stores must be distinct |
| `baseline-outputs-backslash-root.json` | baseline outputs path must be relative (UNC `\\server\share`) |
| `baseline-outputs-unc-slash.json` | baseline outputs path must be relative (UNC `//server/share`) |
| `baseline-outputs-drive-abs.json` | baseline outputs path must be relative (drive-absolute `C:\x`) |
| `baseline-outputs-drive-abs-slash.json` | baseline outputs path must be relative (drive-absolute `C:/x`) |
| `baseline-outputs-drive-rel.json` | baseline outputs path must be relative (drive-relative `C:foo`) |
| `baseline-outputs-mid-backslash.json` | baseline outputs path must be relative (any backslash) |
| `helper-duplicate.json` | helper set does not match the workload (helpers checked as a list, so a duplicated entry fails even though its name→role dict collapses to the expected set) |
| `tokens-negative.json` | tier-3 evidence lacks measured observation (`tokens: -1` rejected; tokens must be finite and ≥ 0 when present) |
| `cost-negative.json` | tier-3 evidence lacks measured observation (`cost: -100` rejected; cost must be finite and ≥ 0 when present) |
| `tokens-huge-int.json` | tier-3 evidence lacks measured observation (integer tokens above float range; valid retained baseline outputs isolate numeric validation) |
| `fixture-sha-unbound.json` | fixture hash does not match retained fixture (candidate and baseline repeat the same invented hash, with a digest-matched retained baseline) |
| `units-missing.json` | observation units missing or invalid |
| `units-mismatched.json` | observation units missing or invalid (seconds instead of milliseconds) |
| `baseline-units-mismatched.json` | tier-3 baseline lacks retained outputs (digest matches but retained throughput uses completions per minute) |
| `io-kind-memory-only.json` | I/O kinds omit required disk I/O for this workload |
| `candidate-id-unbound.json` | candidate does not match retained candidate manifest (relabeled candidate ID) |
| `candidate-commit-mismatch.json` | candidate does not match retained candidate manifest |
| `candidate-config-mismatch.json` | candidate does not match retained candidate manifest |
| `baseline-host-mismatch.json` | tier-3 baseline host does not match the candidate (retained Python runtime differs) |
| `baseline-runner-mismatch.json` | tier-3 baseline host does not match the candidate (runner differs) |
| `baseline-config-mismatch.json` | tier-3 baseline host does not match the candidate (execution configuration differs; uses the retained batch-size-2 file) |
| `baseline-python-coarse.json` | tier-3 baseline host does not match the candidate (`host.python` is `3` on both sides, so neither is a full runtime version) |
| `floor-results-missing.json` | tier-3 floor results are not bound to the candidate (floor rows copied, no receipt) |
| `floor-results-wrong-commit.json` | tier-3 floor results are not bound to the candidate (receipt names the baseline commit) |
| `floor-results-failed.json` | tier-3 floor results are not bound to the candidate (one registered floor fails) |

The otherwise valid comparison under `fixtures/validator-only/` is entirely synthetic
and isolates these controls. No benchmark was run and no floor test was executed;
these invented observations, host identities, floor receipts and commits are
validator inputs only and leave the measured tier-3 result count at zero.
The regression tests require each new control to have only its intended semantic
failure, so an unrelated baseline rejection cannot mask numeric or identity checks.

A packet whose maturity is anything other than `researched` is rejected. Promoting this artifact to `contract_frozen` requires a human receipt, not an edit.

## Research (Parallel)

Embedded Parallel searches run on Dan's box on 2026-10-17. The pages below were retrieved and the protocol follows them.

Search ids: `search_8afa65003c515c3b57197de5f5cd25c4`, `search_902e146c8a56d6e091750abdb2b9d763`, `search_b6290caf1f31d7a8a710db6b71f76c7e`, `search_86967d451c3ce6e3c60de7a6868def47`, `search_4b647f1aa2547fad5b4062554a71c354`, `search_41e4072e4bc070139371640730ea2eb3`.

- [Strangler Fig pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/strangler-fig) — Microsoft Azure Architecture Center. Incremental façade, shared-store coexistence, anti-corruption layer, rollback while legacy objects remain.
- [Strangler fig migration guide](https://adaptive-enforcement-lab.com/patterns/architecture/strangler-fig/migration-guide/) — Adaptive Enforcement Lab (`search_86967d451c3ce6e3c60de7a6868def47`). Every seam needs a defined rollback path and mixed-version coexistence; the basis for the required per-candidate `seam` and `rollback` fields and the `seam-missing-rollback` negative control.
- [Parallel Change](https://martinfowler.com/bliki/ParallelChange.html) — Martin Fowler's bliki (`search_41e4072e4bc070139371640730ea2eb3`). Expand-and-contract: old and new versions coexist through expand, migrate, and contract phases, each independently releasable, so rollback stays available while a seam is exercised. Supports the per-seam rollback control and the `seam-missing-rollback` negative control.
- [Run a benchmark](https://pyperf.readthedocs.io/en/stable/run_benchmark.html) — pyperf worker processes, warmup, and instability warnings. Python 3.9 or newer.
- [Analyze benchmark results](https://pyperf.readthedocs.io/en/latest/analyze.html) — quartiles, median, and median absolute deviation; `compare_to` uses a two-sample t-test.
- [psf/pyperf](https://github.com/psf/pyperf) — toolkit repository for those documents.
- [Writing benchmarks](https://asv.readthedocs.io/en/stable/writing_benchmarks.html) — airspeed velocity (`search_b6290caf1f31d7a8a710db6b71f76c7e`). Report warmup, repeats, and variance, and isolate external I/O — the reason `provider_latency` is measured or explicitly excluded with a reason, never silently folded in.
- [hyperfine](https://github.com/sharkdp/hyperfine) — command-line benchmarking tool (`search_b6290caf1f31d7a8a710db6b71f76c7e`). Warmup runs, repetition counts, and statistical outlier handling; not installed by this packet.
- [Strangler fig, Python to Rust](https://enersys.co.th/en/insights/strangler-fig-migration-python-to-rust-2026) — Enersys, 18 Apr 2026. Case-study argument for incremental cuts. Other companies' speed claims are not local evidence.

## Unresolved decisions (owner: release owner)

None of these are decided in this packet. Each stays `unresolved` with owner `release owner` — the role named the same way as the CSL-221 evidence slice, not a person.

- **Spend.** Whether tier-2 workloads run on dispatch, `ci:full`, or nightly Blacksmith, and whether any host is purchased. This repeats Devin's open cadence and budget questions on CSL-220. No cadence and no spend are added here.
- **Credentials.** No credential is requested or stored. Venue and provider credentials remain an owner decision.
- **Tax/legal.** Tax and legal eligibility for a US individual across securities and event contracts stays unresolved.
- **Architecture.** Language, storage, topology, and any CSL-42 rewrite stay a phase B decision after matched tier-3 evidence.
- **Release.** Release scope and qualification stay with O9 and a human decision.
- **Live.** Live trading and bounded-direct authority stay closed. This packet grants no live effect.

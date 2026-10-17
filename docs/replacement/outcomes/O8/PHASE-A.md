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

| Floor | Test locator |
| --- | --- |
| invariant_1_single_order_path | `app/tests/test_invariants.py` |
| invariant_2_decision_pinned_in_auto_modes | `app/orchestrator/mode_ladder.py` (`arm_auto_mode` refuses a non-Claude decision runtime) |
| invariant_3_schema_validate_model_output | `app/workflows/schema_loader.py` |
| invariant_4_secrets_os_credential_store | `app/core/secrets.py` |
| invariant_5_risk_pure | `app/risk/engine.py` |
| invariant_6_human_gate_hash_chain_audit | `app/storage/audit.py` |
| invariant_7_fail_closed | `app/orchestrator/breakers.py` |
| invariant_8_raw_inputs_separate_from_summaries | `app/filings/raw_store.py` |
| invariant_9_post_pdt | `app/risk/engine.py` (`REG_T_MIN_EQUITY` is 2000; no day-trade count) |
| fail_closed | `app/orchestrator/breakers.py` |
| hash_chain_audit | `app/storage/audit.py` |

`fail_closed` and `hash_chain_audit` repeat invariants 7 and 6 under the names this outcome uses, so a candidate cannot drop them by renaming.

The checked-in candidate id is `phase-a-protocol-only`. Its maturity is `researched`. Language, storage, and topology are not selected. It changes no financial, authority, failure, or recovery semantics, and it is not CSL-42 evidence.

The candidate manifest also carries `smallest_experiment`, `seam`, and `rollback` as required checked fields, so the smallest next experiments and the rollback path the contract names live in the manifest, not only in this prose. This candidate's smallest experiment and seam are the `ingest_to_raw_store` tier-2 workload — ingest into `RawStore` sits upstream of the other five workloads. Its rollback routes that seam back to legacy with the raw store and hash chain as the system of record.

## Matched benchmark protocol

Devin's 2026-10-16 note on CSL-220 is the tier split this packet records. It is a planning note, not a measurement.

**Tier 1, unchanged.** `app/benchmarks/` times `risk_evaluate`, `order_tuple_hash`, `schema_validate_proposal`, `outbound_scrub`, and `audit_append_verify`. The pull-request job uses 5 rounds and 2 orderings. A case is a regression only when median slowdown minus relative IQR stays above 0.10. The process exits 0, and the job does not fail `linux-gates`. This packet does not add per-PR benchmark gating and does not change that job.

Tier 1 does not include end-to-end I/O or helper processes. It cannot decide CSL-42.

**Tier 2, defined and unmeasured.** Provider-free tracked workloads. Cadence is `unresolved_release_owner`. They are not per-PR. Each manifest must name the commit, host, repetitions, relative IQR, included I/O, the workload's registered helper processes, and a labeled `provider_latency`. Unmeasured fields in this packet use status `required_on_run` and JSON null. That records the obligation. It is not a measured sample.

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

**Tier 3, the only CSL-42 evidence.** A tier-3 manifest may set `csl42_evidence` only when the tier is 3, floors are attached, variance status is `measured`, repetitions are an integer of at least 2, and relative IQR is a number. This packet's tier-3 result count is 0. No workload in the fixture claims that evidence.

The evidence binds further. Attached floors are the full eleven-row registry, validator-checked — `floors_attached` is not a bare boolean, so a manifest that asserts the flag without the registry fails `attached floors invalid`. The baseline is bound to retained outputs: `baseline.outputs` names a repo path and its sha256, and the validator hashes the file, so a baseline that names only caller-supplied identifiers fails `tier-3 baseline lacks retained outputs`. A measured variance requires a measured observation — measured helper processes and finite, non-negative metrics — else `tier-3 evidence lacks measured observation`; percentiles must order p50 ≤ p95 ≤ p99, else `impossible measured observation`. Non-finite JSON constants (`NaN`, `Infinity`) and exponent-overflowed numbers (`1e309`) are rejected at parse, and every measured metric — including `variance.relative_iqr` and optional `tokens`/`cost` — must be a finite, non-negative number. `baseline.outputs.path` must be a repo-relative path that resolves inside the repository: paths that are absolute or rooted on either POSIX or Windows — leading `/` or `\`, drive-absolute (`C:\x`, `C:/x`), drive-relative (`C:foo`), UNC (`\\server\share`, `//server/share`), or any backslash at all — fail `baseline outputs path must be relative`, and `..` escapes fail `baseline outputs path escapes the repository`. The rejection uses `PurePosixPath` and `PureWindowsPath` together ([pathlib docs](https://docs.python.org/3/library/pathlib.html): `PureWindowsPath.is_absolute`, drive and anchor semantics) so behavior is identical on every OS, and is applied before the `resolve()`/`is_relative_to(ROOT)` containment check on every packet-supplied path the validator joins to the repo root — the standard path-traversal countermeasure ([OWASP Path Traversal](https://owasp.org/www-community/attacks/Path_Traversal), [CWE-22](https://cwe.mitre.org/data/definitions/22.html)). The retained outputs file must carry a non-empty `observation` payload, so identifier-only files are not accepted as a baseline oracle. Helper processes are pinned per workload to the declared registry (names and roles from the table above) and compared as an ordered list of name→role pairs, so a manifest claiming a workload it does not exercise — or repeating a helper — fails `helper set does not match the workload`. The migration's `replay_from` and `shared_stores` each require two distinct members — `uniqueItems` in the packet schema and an explicit set check in the validator — because a duplicated member would silently drop one replay source or store. The packet's `research.parallel_search_ids` records all six searches actually run (two pinned at authoring plus the four added during review) and `cited_urls` their sources, so the claimed research below reconciles exactly with the frozen record.

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

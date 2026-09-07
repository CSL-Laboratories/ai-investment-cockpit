# Gate audit and repair delivery — 2026-09-06

Branch: `codex/audit-gates-2026-09-06`, based on private main
`b4a199ff41ed84d78f21602a84d9665c1072ec6a` (after PR #68).
Review: [draft PR #70](https://github.com/NotSkittlz/ai-investment-cockpit/pull/70).
This is reviewable source and evidence. It does not activate paper execution, change a running
process, or confer a selection, witness, or live-capital PASS.

## What changed

The three priority boundaries are repaired: Claude decision jobs explicitly remove tools and
reject unexpected arguments; signed proposal authority binds the complete payload and fixed
order economics; and final risk reconstructs fresh signed loss evidence before submission.
Independent attacks also found and closed duplicate audit-intent resolution and Codex argument
override acceptance. These are BUG-115 through BUG-119, implemented in `be1ff13`.

BUG-120 through BUG-122 repair hidden HTML authority, negated/conditional semantic assertions,
and an ordinary later filing being mistaken for a merger amendment. They are implemented in
`69a1c9c`. The cash and exact-date patterns were not broadened. Separate review found a May-date
regression and further hidden-text cases during development; those were fixed before final tests.

The original findings remain preserved in `94b8b4b`. The offline measurement calculator is in
`0149b86`; historical BHF/SAFT excerpt pins and checks are in `8fc9c93`. The final application
subtree is `5125e7edd2489c027364bbbfbd4556d928109817`; subsequent documentation and research-tool
commits do not change that tested application subtree.

## Requested gate pressure tests

| Request | Result and remaining limit |
|---|---|
| Concurrency beyond BUG-114 | Found BUG-118: two writers could resolve one intent while retaining a valid chain. The transaction now pins the duplicate guard and retries/refuses the competing result. All production `append_in` call sites were inspected; bounded two-writer attacks preserve one chain. Historical forks remain invalid. This is not a multiprocess production-load proof. |
| Held cash/date misses | Three semantic authority defects were repaired, but SAFT's stock false positive and FSEA/SLP/CCO cash variants remain held for exact full-primary fixtures. Five existing BHF/SAFT review excerpts are hash-pinned; seven companion checks preserve cash and refuse unsupported dates. The SAFT reproduction still fails the product requirement. No year-only, not-closed-by, automatic-extension or date-ready rule was loosened. |
| Selection to RW-15 | The [source map](rw20-selection-order-map-2026-09-06.md) follows complete signed sources, prospective selection, witnessed W-24 chronology, mature calibration, current deal, static operator universe, deterministic edge, signed proposal, Discord approval, gateway, and broker reconciliation. A single date-ready head cannot bypass the cohort or empty watchlist. |
| Durable watch-only | Independent tests cover model blackout/recovery generations, SEC 429 HALT across restart, and invalid-audit restart. They refuse old model generations or global degraded state without repairing the ledger. SEC's vendor generation is separate: recovery alone does not prove all prior Claude/Codex analysis invalidated. |
| Soak separation | Local Git/worktree, hooks, archive and test-storage separation is documented. Tests use isolated source copies and temporary stores; there is no deployed checkout mutation or ledger healing. Current `/workspace/cockpit-soak` host/process/storage identity has not been observed, so remote isolation is not claimed. OneDrive synchronization and same-user access are explicit limits. |
| DEC-09 / recent merges | All four real cells passed on the final application source after #68: Windows 3.11/3.12 each 2,648 passed / 5 skipped; Ubuntu 24.04 WSL2 3.11/3.12 each 2,646 passed / 7 skipped. Ruff and dependency checks passed throughout. [The matrix record](rw20-matrix-isolation-2026-09-06.md) retains the exact source scope and skip reasons; setup and old Python 3.13 diagnostics do not substitute. |
| Model authority | Closed decision-argument grammars, immutable execution watchlists, signed payload/tuple bindings and deterministic edge derivation are checked. A model suggestion cannot mint edge, change date policy, provide W-24 evidence, or bypass human approval. Real deployed CLI capability attestation and low-privilege host separation remain distinct gates. |
| W-25 / SEC-11 | A [concrete independent-witness plan](rw20-independent-witness-plan-2026-09-06.md) uses the existing S3 Object Lock Compliance adapter, a separate administrator and real deletion/retention/shadow/rollback drills. Discord stays chronology/mirror evidence. No cloud resource or artificial witness receipt was created. |
| Cohort honesty | The dated September 4 population contains 192 discovery names, 30 supported heads, and zero selection-ready/date-ready names. Thirty same-date eligible identities are required, not 30 simultaneous orders. Attainability is unproved. Recommend filing-triggered awareness with a daily discovery/poll-health backstop; the running schedule is unchanged. |

## Validation and empirical limits

The separately authored authority/concurrency/restart suite passes **41 tests** and the semantic
adversarial suite **66 tests**, with Ruff clean. These 107 cases are also part of the full
application matrix. The [review](rw20-adversarial-review-2026-09-06.md) distinguishes genuine
pre-fix attacks from corrected test setup errors; the
[semantic review](rw21-filing-grammar-2026-09-06.md) records the successive attack rounds.

The [matrix record and raw artifacts](rw20-matrix-isolation-2026-09-06.md) bind full-suite
results to the application tree, exact interpreters, dependency versions, archive hash and
unchanged source manifests. Synthetic brokers and in-memory test credentials are explicit.
The root also [verified publication integrity](rw20-publication-check-2026-09-06.json): a separately
recreated Git archive matches the tested archive; all four 499-file manifests match its bytes;
48 recorded artifact hashes match their exact staged Git blobs. This is local integrity evidence,
not an independent witness. The owned audit distro was stopped after preserving its outputs.
Real Discord, CLI, independent witness, broker, market-day and deployment evidence cannot be
inferred from those tests. The seven excerpt checks also include an intentional reproduction
of an unfixed SAFT defect; that passing test is not a semantic-repair PASS.

[The research calculator](../runbooks/research-value-score.md) measures field precision and
recall against separately supplied labels, paired net hours saved, and strategy returns after
execution/operating costs against cash, broad equity and a simple merger benchmark. Its ten
unit checks use synthetic arithmetic inputs. The
[actual current output](research-value-status-2026-09-06.json) reports all three measurements
as **unmeasured**. Declaring a label reviewer does not authenticate independence; no withheld
ground truth, operator time record or prospective strategy result was invented.

## Remaining work, in order

1. Capture the held official primaries using the operator-declared SEC contact, verify exact
   bytes and hashes, and repair only extractable SAFT/FSEA/SLP/CCO misses with independently
   reviewed refusal regressions. The [bounded capture helper](../../app/tests/strategies/fixtures/rw21/capture_sources.py)
   refuses without a declared identity and stops on failed/rate-limited responses.
2. Inspect the named soak host read-only to bind its checkout, process and runtime store paths.
   Preserve the old fork and original anchors. A new append or patched branch cannot certify
   or heal historical evidence.
3. Collect a frozen, independently labelled source population including refusals. Then report
   precision and recall separately. Development fixtures are not that held-out population.
4. Observe actual eligible arrivals before considering a separately reviewed rolling prospective
   cohort design. The current 30-identity rule remains enforced.
5. Run the predeclared 20-market-day paired awareness study and prospective cost-adjusted strategy
   comparison. The [protocol](../research/gate-feasibility-and-value-2026-09-06.md) retains missed
   events, maintenance, failed/unresolved deals and all observation days. Increasing autonomy
   waits for real benefit and the existing empirical gates.
6. Provision a real independent witness only after choosing the account, region, policy,
   administrator, workload estimate and funded retention commitment; complete the real drills.
7. Before any future live integration, repair original A5: positively identified live intents
   are excluded from paper performance, but their generic transitions can still invalidate it.
   This medium-severity future-scope defect remains open as BUG-123. A current-branch isolated
   reproduction passed on Python 3.13 (one test, 4.72 s); it demonstrates the defect remains.

Approval can refuse pending drafts created before the new signed-body bindings; they require
redrafting, not signature backfill. Synchronous performance reconstruction adds audit work at
each risk boundary, so its latency should be measured during a separately authorized soak.
The hardened CLI contract can also refuse older installed versions; deployment must verify
actual supported restrictions. These operational consequences are recorded before merge.

## Assessment

The awareness and discipline premise is sound enough to continue testing. Financial edge,
net time savings and autonomous profitability are not established. The current constraint is
reliable evidence and a feasible prospective experiment, not more model authority. Filing-triggered
awareness plus measured operator value is the strongest next step; the unchanged gates keep
the strategy experiment honest while those observations accumulate.

---
title: Astra DEC-09 release-matrix gap analysis — 2026-09-06
type: evidence
status: reference
last-verified: 2026-09-06
source_commit: c5ef0ad3d9acd3a85aa86228491ea1827bfd529e
---

**All four required DEC-09 cells have matching, intact evidence for the application
in `c5ef0ad`. The gap is missing refusal cases shared by those cells, plus explicit
environmental skips.** New isolated fixtures reproduce three residual problems:
cash attributed to the wrong security, contracted negations accepted as assertions,
and transactional audit writes erased into an apparently virgin chain. These are
component fail-opens, not observations of a real order or an eligible W-24 cohort.

**Proposed work: one four-case cash-subject regression scope in RW-21.** Its two
refusal assertions fail on unchanged source, while its two controls pass. The audit
finding overlaps the separate HWM research and should stay with that package.
This task made no PR, commit, production repair, selection commitment or soak operation.
The proposed test-only change is red review material; it cannot be merged as a
green package without a bounded repair and the required validation.

This research is for Cockpit Admin, within the September 6 Pacific-time window
requested through approximately 20:15 PDT / September 7 03:15 UTC. The filename
uses the requested Pacific date. The exact execution times are in the companion
records. `CLAUDE.md`, `docs/README.md`, PLAN, REGISTER, DEC-09 and the RW-20/W-24
contracts were read. Existing unrelated research files were preserved.

**Source and evidence binding.** The initial inspected local HEAD was
`892e2dbaab0093e91857cea373dc188d638d8d80`, a documentation-only descendant of
merged PR #70. Its `app/` and `tools/` trees match `c5ef0ad`. This is a pinned-source
audit through the requested merge, not an assertion about the latest remote main
or the code loaded by a serving process.

Other activity in the shared checkout subsequently created documentation/evidence
commits `049518d`, `2097860` and `ec76c56`, including mid-flight companion fixtures
from this task. At the final source check, HEAD was
`ec76c564f8d2ee4db3f54c8a85b6c9832cc08cf8`; both `app/` and `tools/` still matched
`c5ef0ad`, and neither had working changes. This agent performed no Git mutation.
The [final source record](../../data/astra-dec09-matrix-gaps-2026-09-06/final-source.json)
distinguishes that external evidence freeze from this task's work.

| Scope | Exact binding | What can be concluded |
|---|---|---|
| PR #62 cash heads | Merge `efc626f`; implementation `0ec4842`, fixture `2f1e35a` | Seven added pytest cases are present and pass in every September 6 full-suite XML |
| PR #68 BUG-114 | Merge `61ca0af`; repair `3035d83` | Its allocation-interleaving regression passes in all four final-source cells; the historical fork remains failed evidence |
| PR #70 audit/semantic gates | Merge `c5ef0ad`; application changes through `69a1c9c` | All 126 cases belonging to 37 newly added test functions, and the one changed existing test case, pass in each cell |
| Full application matrix | Commit `69a1c9c53c0beeb0f91310ee017b24ba21a592b7`; app tree `5125e7edd2489c027364bbbfbd4556d928109817` | Identical app tree at `c5ef0ad` and inspected HEAD; no extrapolation across application edits |
| Scorer supplement | `0149b86a8e0ce6d971e6c6f69bd31d08bdb5ad8c` | Ten separate checks per interpreter, with empty inputs still unmeasured |
| Historical excerpt supplement | `8fc9c936c6a6c2c25d344a8425ffc5a48c01e879` | Seven separate checks per interpreter; some preserve refusals or document a known defect |

The [committed matrix](rw20-matrix-isolation-2026-09-06.md) is supported by its
[raw artifacts](rw20-local-matrix-2026-09-06/README.md). This session independently
checked **70/70 recorded artifact SHA-256 values**, compared **499/499 source
manifest entries to their Git blobs**, compared the four manifest dictionaries,
read all four JUnit case inventories, and matched changed test functions to their
recorded cases. There were no mismatches. The original matrix summary remains
unchanged at SHA-256
`0d63e2c39cadfe802d3b07afe17423098b0e116b97d929aa56fae6405d278703`.
The [new cross-check](../../data/astra-dec09-matrix-gaps-2026-09-06/committed-matrix-audit.json)
and its [read-only script](astra-dec09-matrix-gaps-2026-09-06/audit_committed_matrix.py)
retain the exact test-to-cell mapping. Hash integrity is not independent attestation.

The merge test counts describe functions added or modified in `app/tests/`, with
pytest parametrization expanded. They do not measure branch coverage or count
changes to shared fixtures as separate tests. The supplemental tools and excerpts
are deliberately outside those full-suite counts.

**Required matrix and its weak areas.** All four full suites contain the same
2,653 JUnit entries, including collection skips. There is no 3.11-versus-3.12
case-status difference within either operating system. Six individual cases differ
between Windows and WSL; three additional skipped entries are common to all cells.

| DEC-09 cell | Recorded full suite | Explicit gaps on that cell |
|---|---:|---|
| Native Windows, CPython 3.11.16 | 2,648 passed / 5 skipped; Ruff and dependency check successful | Discord command-tree module absent; two private-sibling provenance checks; two real symlink-refusal checks skipped for unavailable privilege |
| Native Windows, CPython 3.12.14 | 2,648 passed / 5 skipped; Ruff and dependency check successful | Same gaps as Windows 3.11 |
| Ubuntu 24.04 WSL2, CPython 3.11.16 | 2,646 passed / 7 skipped; Ruff and dependency check successful | Discord module; two private-sibling provenance checks; three installed-Claude/Codex transport smoke cases; Windows-only WMIC smoke inapplicable |
| Ubuntu 24.04 WSL2, CPython 3.12.14 | 2,646 passed / 7 skipped; Ruff and dependency check successful | Same gaps as WSL 3.11 |

The exact skip names and reasons are in the cross-check and original per-cell XML.
In particular:

- Both Windows cells skip
  `test_verify_rejects_linked_package_root_and_inner_artifact` and
  `test_verifier_rejects_symlinked_package_root_when_supported`; both execute on WSL.
  This is missing native Windows filesystem evidence, not proof of a link bypass.
  Both collectors explicitly reject `FILE_ATTRIBUTE_REPARSE_POINT` as well as
  symlinks. No native junction test was found in `app/tests/`; an `is_symlink`-only
  future simplification would remove that separate protection. `Path.is_junction`
  was added in Python 3.12, but the present implementation does not depend on it.
  [Python pathlib reference](https://docs.python.org/3.13/library/pathlib.html#pathlib.Path.is_junction).
- Both WSL cells skip installed-Claude/Codex checks in `test_transport_resolve.py`.
  The corresponding Windows checks only exercise local `--version` spawning and
  shim resolution. They do not run the full hardened decision command or establish
  tool-denial behavior of an installed runtime. The strict argv and no-transport-on-
  mutation fixture tests execute in every cell.
- All four CI-profile cells omit `discord.py`. The single JUnit module skip hides
  **six** command-tree construction/registration tests, rather than one exercised
  refusal. Core adapter and paper-service tests still run. This session executed
  those six tests with installed Discord on Linux 3.13, which adds diagnostic
  evidence but does not fill the four native release-profile gaps.
- All four cells omit the two private `edgar-morning-brew` provenance comparisons.
  Other local filing/schema tests running is not a substitute for those upstream
  pin comparisons. This task did not retrieve the private sibling.

**Coverage of the named safety boundaries is substantial but bounded.** These
are per-cell counts from the actual XML, not estimates from filenames:

| Test module | Cases passing in each of all four cells | Remaining neighboring question |
|---|---:|---|
| `storage/test_audit.py` | 18 | Existing total-erasure test primes the observation floor with verification; it misses an unverified transactional commit |
| `storage/test_rw20_concurrency_pressure.py` | 4 | Deterministic writer interleavings and 30 thread-written identities; no process-kill, multiprocess-load or storage-fault proof |
| `brokers/test_rw20_authority_adversarial.py` | 14 | Signed order economics, fresh equity/loss and original authority covered; interrupted watch-only persistence/final boundary residuals remain separate |
| `orchestrator/test_rw20_restart_pressure.py` | 6 | Restores committed transitions and refuses bad startup authority; not a crash-before-commit test |
| `agent_runner/test_rw20_codex_authority.py` | 17 | Closed argv grammar and refusal before fake transport; no live runtime capability attestation |
| `strategies/test_merger_semantics.py` | 38 | Includes all seven #62 cases, source-set tampering and earlier subject-switch controls; misses a switch after the target payout verb |
| `strategies/test_rw21_real_filing_grammar.py` | 66 | Synthetic assertion/markup/date/source-continuity cases plus repository excerpts; 24 cases are month-name controls, not 24 independent filings |

The held-excerpt supplement's successful SAFT repurchase check **reproduces a known
stock-classification false positive**. Its consequence is refusal of a real cash
deal, an eligibility false negative. It is not a repaired grammar result and does
not justify an acceptance-expanding test PR. Neither that supplement nor the 66
RW-21 cases supplies independent held-out precision. See the
[held-excerpt contract](rw21-held-excerpts/README.md) and
[whole-document grammar survey](astra-grammar-false-negatives-2026-09-06.md).

**Interpreter and dependency neighbors.** The September matrix uses native
CPython builds supplied through uv, not the earlier python.org Windows installers
or Ubuntu's 3.12.3 interpreter. The preserved dependency lists have identical
versions for common packages; differences are platform/3.11-only dependencies,
including Linux SecretStorage/uvloop and 3.11 backports. The lists are observations,
not a dependency lock. SQLite library version, connection autocommit state and
actual WAL pragma results are not recorded per original cell.

| Neighbor | Evidence status through the inspected source | Interpretation |
|---|---|---|
| Earlier Windows 3.11.9 / 3.12.10 and Ubuntu 3.12.3 | August evidence belongs to older source | Historical compatibility only; cannot attest #62/#68/#70 |
| Debian 13, Python 3.13.5, SQLite 3.46.1 | This session's isolated 271-test slice and new refusal probes | Useful reproduction environment; neither Windows nor Ubuntu/WSL2, and outside DEC-09's interpreter pair |
| Python 3.14 and later | No execution in this audit or the reviewed September matrix | `requires-python = ">=3.11"` permits installation; it does not create a completed release cell |
| Python 3.10 | Below the declared minimum | Not a missing DEC-09 obligation |
| macOS / hosted Actions / Hetzner | No new evidence collected | Best effort / optional diagnostic / outside current acceptance, respectively |
| Real OS credential backend, retained witness, loaded soak process | Not exercised by these fixtures | Separate operational and independent-witness evidence remains necessary |

Python 3.12 introduces `Connection.autocommit` while retaining legacy transaction
control by default. A plain SELECT still need not begin the SQLite transaction;
the comment in `verify_session` saying “through 3.11” must not be read as a 3.12
exemption. `result()` and `verify_session()` explicitly issue BEGIN when required;
the BUG-114 allocator uses its HWM-before-head ordering. No test parametrizing
the alternative autocommit modes was found, and this audit did not change them.
A future interpreter/driver-policy change needs its own checks of commit,
rollback, snapshot and retry behavior. [Python transaction-control documentation](https://docs.python.org/3.12/library/sqlite3.html#transaction-control),
[SQLAlchemy SQLite transaction documentation](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#legacy-transaction-mode-with-the-sqlite3-driver).

An attempt to install private 3.11.16 and 3.12.14 interpreters under `/tmp` failed
when the download hostname could not resolve. Neither interpreter was installed or
executed. There was no native Windows/WSL session here and no new DEC-09 attestation.

**Fixture-reproduced missing refusals.** Every case below is absent from the
committed four-cell suite. New failures were executed only on the explicitly
identified Linux 3.13 diagnostic environment; execution on 3.11/3.12 remains
pending. Shared source makes them relevant to all four cells, but is not a new
four-cell failure measurement.

| Finding | Trigger and actual result on unchanged `c5ef0ad` | Existing control / scope limit |
|---|---|---|
| M1 — cash changes subject after the target verb | `Each share of target common stock will receive a distribution; each share of preferred stock will receive $105 in cash per share.` The current-deal verifier accepts the document's target cash value of 105. Acquirer-share variant also accepts. **2 refusal assertions fail.** | Valid target cash and replacing the semicolon with a period both behave correctly: **2 controls pass**. Synthetic raw source; no signed selection, witness, edge estimate or order |
| M2 — contracted negation outside matched assertion | `It isn't true that the merger was completed.` yields `closed`; negated `expected to close by October 1, 2026` supplies that date. ASCII and curly apostrophes both reproduce. **4 refusal assertions fail.** | The same two assertions using `is not` refuse: **2 controls pass**. Source-assertion component tests only; not a real filing/date-ready observation |
| M3 — committed transactional write does not advance process erasure floor | A fresh AuditLog commits via `append_in` or `record_state_change`. Before any verification, a test-only keyless deletion erases audit rows and watermark. The same object returns `VerifyResult(ok=True, head_seq=0)`. **2 refusal assertions fail.** | Standalone append, rolled-back transactional append, and verification before erasure all behave correctly: **3 controls pass**. Same-process observation gap; not the restart-spanning rollback class that requires W-25 |

M1 reaches the real `CashMergerSemanticVerifier._verify_deal` implementation. The
declared document is taken from the existing affirmative fixture; it is not
generated by accepting the adversarial text first. `_CASH_CONTEXT_CHAR` protects
the subject-to-verb region, but the following `[^.]{0,120}?` amount region can
cross a semicolon and another security's payout. Earlier alternate-security tests
switch subjects before the target verb, or use a sentence boundary. Re-running
them therefore does not cover this neighbor. The
[four-case fixture](astra-dec09-matrix-gaps-2026-09-06/test_cash_subject_boundary.py)
and [unaltered failure log](../../data/astra-dec09-matrix-gaps-2026-09-06/cash-boundary.log)
make this a concrete safety-test proposal rather than a speculative test expansion.

M2 follows #70's `_NEGATED_ASSERTION` word predicate: `isn't` is not one of its
negative words, even after curly-apostrophe normalization. The
[six-case fixture](astra-dec09-matrix-gaps-2026-09-06/test_assertion_neighbors.py)
preserves the [four failures](../../data/astra-dec09-matrix-gaps-2026-09-06/assertion-neighbors.log).
It should be considered when reviewing BUG-121's residual assertion boundary;
it is outside the proposed M1 test change.

M3 is distinct from BUG-114's repaired sequence/parent race. The post-commit
`_observed_head` update exists in `append()` and `result()`, but not in `append_in()`
or its `record_state_change()` caller. The fixture proves commit with a separate
session without calling the verifier; calling it there would conceal the gap.
Its [five cases](astra-dec09-matrix-gaps-2026-09-06/test_transactional_erasure_floor.py)
and [failure log](../../data/astra-dec09-matrix-gaps-2026-09-06/transactional-erasure.log)
are retained. The separate
[HWM research](astra-hwm-resign-claim-shape-2026-09-06.md) and its
[historical probe](astra-hwm-resign-claim-shape-2026-09-06/historical_probes.py)
already cover the `append_in` erasure case. Do not claim a competing audit
package here. Any repair must also preserve the passing rollback control: simply
advancing the floor before commit creates a false integrity failure on rollback.

**These are residual gaps, not newly introduced regressions of #62 or #68.**
The same bounded [history probe](astra-dec09-matrix-gaps-2026-09-06/history_probe.py)
was executed against separate Git archives using the same diagnostic interpreter:

| Pinned source | Wrong-subject current deal | Contracted negation / `is not` control | Transactional erasure reported valid |
|---|---|---|---|
| `636b76f`, immediately before #62 | Accepted | `closed` / `closed` | Yes |
| `efc626f`, #62 | Accepted | `closed` / `closed` | Yes |
| `61ca0af`, #68 | Accepted | `closed` / `closed` | Yes |
| `c5ef0ad`, #70 | Accepted | `closed` / refused | Yes |

The [archive provenance](../../data/astra-dec09-matrix-gaps-2026-09-06/historical-source.json)
and four logs
([before #62](../../data/astra-dec09-matrix-gaps-2026-09-06/history-636b76f.log),
[#62](../../data/astra-dec09-matrix-gaps-2026-09-06/history-efc626f.log),
[#68](../../data/astra-dec09-matrix-gaps-2026-09-06/history-61ca0af.log),
[#70](../../data/astra-dec09-matrix-gaps-2026-09-06/history-c5ef0ad.log))
retain those observations. This is a narrow behavior comparison, not historical
full-suite acceptance. It shows #70 closing the uncontracted-negation case while
leaving the contraction neighbor. Rolling back these merges would not remove the
three reproduced gaps and would discard their existing protections.

The existing [watch-only durability research](astra-watchonly-durability-2026-09-06.md)
also reports interrupted-persistence and final-submit failures beside #70's
committed-transition restart tests. Its research fixtures are outside `app/tests/`
and hence outside the original matrix. Those findings were read, not rerun or
independently certified in this task. Likewise, later append/HWM research is not
silently included in September's historical test totals.

**Smallest proposed PR scope.** Suggested title:
`test(RW-21): reject another security's cash after the target payout verb`.
Add the two M1 refusal rows and two controls beside the existing current-deal
grammar fixture in `app/tests/strategies/test_rw21_real_filing_grammar.py`.
The retained standalone fixture is the concrete reviewable scope: four collected
cases, no new dependency or network fixture. Do not combine the contracted-negation,
audit-floor, installed-CLI or filesystem questions into that change.

This test-only patch currently fails and is **not merge-ready**. A bounded repair
must constrain cash attribution while retaining the exact BHF/SAFT/CRNX controls,
decimal par values, explicit cash-per-share requirement, mixed-consideration
refusals, and source/sentence boundaries. No production repair was attempted or
certified here. Acceptance requires the targeted semantic suite, the normal full
suite and Ruff on all four DEC-09 cells; any change reaching the order path also
requires the repository's adversarial-review round. No xfail, weakened assertion,
automatic label, retrospective cohort or threshold reduction supplies acceptance.

For the existing environmental omissions, the bounded next evidence is to run the
already-written Windows link tests on a capable operator host, exercise the real
Discord dependency profile on the supported interpreters, and retain installed-CLI
version/capability evidence separately. Those are evidence requests, not additional
test-PR proposals or new DEC-09 cells. Record SQLite version and transaction mode
when next running the required matrix; do not infer them from a Python minor.

**New execution record.** Tests ran against a regular-file Git archive of
`c5ef0ad`, SHA-256
`0f711e3b8b3a9c933d83d5facc9ccaf9e6082c979489a3438f637d3f7ab3e244`, extracted
under `/tmp/astra-dec09-20260906/source`. The project `.venv` supplied Python
3.13.5; this host is Debian 13, kernel `6.12.94+`. Installed packages include
SQLAlchemy 2.0.52, pytest 9.1.1, pydantic 2.13.5 and Discord 2.7.1.
Exact commands, package inventories, source hashes, durations and return codes
are recorded beside every log. All runs report unchanged application/tool Python
source. No real credential lookup, model decision job, SEC request, Discord
message or broker request was part of these fixtures.

| New check | Result | Retained record |
|---|---|---|
| Existing storage/strategy/argv/authority/restart suite and environment neighbors | **271 passed / 2 skipped**, 68.37 s; absent Claude executable accounts for both skips | [Command and provenance](../../data/astra-dec09-matrix-gaps-2026-09-06/baseline-neighbors.json), [log](../../data/astra-dec09-matrix-gaps-2026-09-06/baseline-neighbors.log) |
| M1 cash-subject safety assertions | **2 failed / 2 passed**, exit 1 | [Record](../../data/astra-dec09-matrix-gaps-2026-09-06/cash-boundary.json) |
| M2 assertion safety assertions | **4 failed / 2 passed**, exit 1 | [Record](../../data/astra-dec09-matrix-gaps-2026-09-06/assertion-neighbors.json) |
| M3 transactional erasure safety assertions | **2 failed / 3 passed**, exit 1 | [Record](../../data/astra-dec09-matrix-gaps-2026-09-06/transactional-erasure.json) |
| Application plus research-script Ruff | Successful, exit 0 | [Record](../../data/astra-dec09-matrix-gaps-2026-09-06/lint.json), [log](../../data/astra-dec09-matrix-gaps-2026-09-06/lint.log) |

The [launcher](astra-dec09-matrix-gaps-2026-09-06/run_diagnostics.py) clears ambient
Cockpit settings, chooses an absent dotenv, disables third-party pytest autoload,
uses null/in-memory keyring and temporary fixture state, and refuses to overwrite
existing log labels. To reproduce M1, select an isolated matching source tree and
use a new label:

```bash
.venv/bin/python docs/evidence/astra-dec09-matrix-gaps-2026-09-06/run_diagnostics.py \
  cash-review-rerun /path/to/isolated/c5ef0ad-source \
  -m pytest -q -ra --tb=short -p no:cacheprovider \
  /workspace/ai-investment-cockpit/docs/evidence/astra-dec09-matrix-gaps-2026-09-06/test_cash_subject_boundary.py
```

Adapt the interpreter/fixture paths for Windows and use the documented short TMP
location. These diagnostic results must retain their environment identity; the
command is not a shortcut to asserting a missing release-cell result.

The [evidence manifest](../../data/astra-dec09-matrix-gaps-2026-09-06/evidence-manifest.json)
binds this report, its final companion scripts and retained results. It is a local
integrity manifest, not an independent signature. The runtime logs are under the
repository-ignored `data/` directory and remain local evidence until separately
included in an operator-reviewed evidence freeze.

**Admin disposition.** Preserve the original four-cell compatibility evidence and
the failed historical BUG-114 incident. The operator-specified awareness-only
`c5ef0ad` soak pin was not inspected, repinned, restarted or rewritten; source
equivalence says nothing about its PID, ledger, freshness or current health.
W-24's complete prospective ≥30-deal evaluation universe, witnessed chronology,
outcome horizon, independent ≥95% held-out correctness with no false-positive
eligibility/outcome, and source-derived current-target terms/dates remain required.
W-25/SEC-11 and paper/broker gates remain separate. This report grants **no selection
PASS**, edge authority, paper-day completion or readiness promotion.

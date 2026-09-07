# Astra HWM refusal claim shape — 2026-09-06

RESULT: real-miss-found

**Recommend one bounded follow-up: refuse missing or invalid established high-water authority at the final signing boundary.** The miss is distinct from BUG-114 / PR #68 and survives PR #70. Erasure-floor poisoning is also confirmed, including through the production EDGAR completion function, but belongs to a separate transaction-lifecycle claim. This is research and a proposed repair scope; this task made no production fix, register claim, commit, or PR.

Prepared for Cockpit Admin on September 7 UTC, September 6 Pacific, before the requested approximately 20:15 PDT stop. The requested filename retains September 6. All checks used disposable fixtures. The incident tree was read-only; no incident database was opened, repaired, migrated, or used as test input. No credential values, external witness operations, model jobs, broker calls, selection changes, or soak operations were involved.

**Current-main provenance**

GitHub main was resolved through the read-only connector to `d0a58367ab4c7f9cdb1a3c8d1db98d0b90d3e97f`, the documentation-only PR #71 merge, and remained there at the final remote check at **01:20:44Z**. The initial local checkout was its PR head, `892e2dbaab0093e91857cea373dc188d638d8d80`; initial local `origin/main` was the older PR #70 merge, `c5ef0ad3d9acd3a85aa86228491ea1827bfd529e`. At that point the only committed difference was the smoke report.

A concurrent Admin evidence freeze moved the shared checkout to `work/astra-drain-freeze-2026-09-06`, HEAD `20978607ed9efad7d62c4a1cb2ae201f7ca8b113`, by 01:20:44Z. Its preceding commit `049518d202cb61079403a7e8cb32cba23e37d51b` captured these mid-flight research helpers. This task performed no Git mutation. Application code remained unchanged at subtree `5125e7edd2489c027364bbbfbd4556d928109817`; the three audited source blobs were rechecked after the move. [Final local provenance](astra-hwm-resign-claim-shape-2026-09-06/final-source.json) records that boundary.

The pinned remote blobs of `app/storage/audit.py`, `app/storage/engine.py`, and `app/ingest/edgar_pipeline.py` exactly match the working files. [Remote provenance](astra-hwm-resign-claim-shape-2026-09-06/remote-source.json) and [local provenance](astra-hwm-resign-claim-shape-2026-09-06/local-source.json) retain those identities and hashes of the supplied miss list and copied historical fixtures. Shell Git remote lookup failed DNS; the connector supplied current remote authority instead.

Relevant contracts were read in AGENTS/CLAUDE, docs index, PLAN, REGISTER, and the SEC-11/SEC-13 backlog. The established-watermark refusal is an existing [audit contract](../../app/storage/audit.py), not a newly invented availability policy. The private-repository metadata confirms [#68](https://github.com/NotSkittlz/ai-investment-cockpit/pull/68) merged as `61ca0af27dc903ff97237b3cc2c8e83c735ebb09` and [#70](https://github.com/NotSkittlz/ai-investment-cockpit/pull/70) as `c5ef0ad3d9acd3a85aa86228491ea1827bfd529e`.

**What the HWM fixtures establish**

The defensive contract is: once an append has observed an established signed watermark, loss or invalidation of that authority before its write must abort the append, preserve the damaged metadata as evidence, and roll back any associated state row. A successful later signature must not erase the earlier integrity failure.

The current flow in [audit.py](../../app/storage/audit.py), lines 126–199, checks the watermark in `_insert`, constructs and flushes the new entry, then calls `_bump_hwm`. The latter rereads authority but evaluates `seq if cur is None or cur < 0 else max(cur, seq)`. Thus a missing or invalid reread becomes a replacement watermark to sign. The second read exists; its refusal policy is missing.

Under this engine's SQLite legacy transaction control, initial SELECTs do not establish a database transaction. `s.begin()` alone is not proof of a pinned SQLite snapshot. A separate fixture writer can therefore commit the fault after preflight and before the first INSERT. Once that INSERT executes, SQLite holds the writer boundary, but `_bump_hwm` accepts the damaged state it now sees.

| Check | Observation on unchanged main code |
|---|---|
| Four late faults × `append`, initially unpinned `append_in`, and `intent` | **12 desired-refusal failures.** Faults remove the pair, remove the value, remove the signature, or invalidate the signature. In every case an independent verifier sees the damage before the attempted append; no `AuditIntegrityError` follows, head advances 1 → 2, metadata is replaced, and later verification returns true. In all four `append_in` cases, the associated synthetic `read_only` state row also commits. |
| SQL execution seam, independent writer thread, missing pair / invalid signature | **2 desired-refusal failures.** No `AuditLog` method is replaced. The recorded DBAPI `in_transaction` is false before the INSERT, the separate writer commits before it resumes, and the same signature replacement occurs. |
| The same four faults already present before preflight | **4 controls pass.** Append refuses, damaged metadata remains, head stays 1, and independent verification stays false. |
| Explicitly pinned `append_in`, missing pair / invalid signature | **2 controls pass.** SQLite rejects the stale snapshot's write; caller rollback preserves the damage and seed head. |
| PR #70's snapshot-pinned `result`, same two faults | **2 controls pass.** The stale write is refused; retry sees damaged authority and raises `AuditIntegrityError`. No result commits. |
| `record_state_change` flushes its state row before audit preflight | **1 control passes.** A bounded concurrent metadata writer cannot acquire the SQLite write lock; the legitimate state/audit transaction remains covered and verifies. |

The [final observations](../../data/astra-hwm-resign-claim-shape-2026-09-06/final-contracts/observations) retain integers, booleans and refusal/error classifications; they do not contain signing keys. The [new fixtures](astra-hwm-resign-claim-shape-2026-09-06/test_refusal_contracts.py) assert refusal and preservation, and deliberately fail when those contracts are absent. The positive controls constrain the finding: it is not every transaction or every audit API.

Production reachability is through ordinary `AuditLog.append` and `intent`, including signed watch-only recording in [breakers.py](../../app/orchestrator/breakers.py), lines 274 and 320. An initially unpinned caller-owned transaction is also affected. Callers that already flushed a domain row or pinned verification have the narrower behavior shown above. No execution path or real order was exercised to establish this storage defect.

**Why this is distinct from #68 and #70**

BUG-114 concerned two healthy writers: allocation could combine a stale row head with a newer signed HWM and commit a non-colliding sequence linked to the wrong predecessor. #68 changed allocation read order. Here the original predecessor and all retained entry HMACs are untouched; the missing refusal concerns metadata damaged after that preflight. The baseline includes #68's healthy-writer regression and it passes.

#70 changed `result()` to pin its duplicate-intent guard to a read snapshot. It did not change `_insert`, `_bump_hwm`, `append`, or `append_in`. The passing `result` controls demonstrate the scope of that repair. BUG-118 duplicate resolution is not counted as a new finding. Nothing in these fixtures identifies a second cause of the preserved historical soak fork at sequence 13589.

**Erasure-floor poison: real, but a separate repair shape**

[verify_session](../../app/storage/audit.py), lines 354–420, promotes every successfully verified caller snapshot into `_observed_head`. Such a snapshot can include the caller's uncommitted audit rows. A rollback removes those rows from SQLite while leaving their sequence in shared process memory.

Both ordinary rollback and savepoint rollback produce the same retained observation: committed head **1**, process floor **2**, independent verifier **true**, shared verifier **false**, with `erasure/rollback: head seq 1 below previously observed head 2`. This is false integrity refusal after a normal transaction failure, not proof of deleted committed history.

The third fixture calls the real [_record_poll_completions](../../app/ingest/edgar_pipeline.py), lines 886–947, with two synthetic CIKs and injects a persistence failure at the second append. The next CIK's `_verified_poll_baseline_in` verifies the first CIK's still-uncommitted completion and advances the shared floor. The batch rolls back, but the shared verifier remains poisoned. No SEC request is made. Supplied miss-list items **2 and 8 are two reachability levels of this same defect**, not two independent bugs.

The concurrent [append-hardening report](astra-audit-append-hardening-2026-09-06.md) calls this same completion/floor boundary **F3**, with real synthetic baseline rows and committed head 2 versus tentative floor 3. Coordinate that ownership; it is corroborating evidence, not another claim or another test count here. Its separate detached-Order findings F1/F2 are outside the supplied miss list and this proposed HWM package.

A safe floor repair needs commit-aware publication and rollback/savepoint accounting. It must also address or explicitly separate committed `append_in` floor coverage (item 3), older caller snapshots (item 4), and same-process total erasure followed by ordinary refill (item 9). Do not “repair” poisoning by resetting or lowering the global floor, disabling erasure checks, accepting arbitrary old snapshots for current authority, or allowing a refill to clear denial. Those approaches weaken existing refusal contracts. A freshly constructed verifier is a diagnostic comparator here, not an operational recovery recommendation.

**Ranked miss list**

Ranks reflect suitability for the next bounded, defensible repair claim, not order-path severity. All 13 listed candidates were reconfirmed by running the untouched historical fixture copy with `--runxfail`: **14 failing cases, 3 passing controls**, zero collection/runtime errors. The HWM item has two parameters. Multiple fixtures share a cause; this is not a claim of 13 independent defects.

| Rank | Supplied item(s) | Confirmed boundary and disposition |
|---:|---|---|
| 1 | **13 — late HWM damage** | Established authority is replaced and signed despite damage visible at the write boundary. Precise refusal contract; one production module. **Recommend this single claim.** |
| 2 | **2 / 8 — floor poison** | Rolled-back rows remain in shared erasure memory; real EDGAR function confirms reachability. Defer to an explicit transaction-lifecycle package with commit/savepoint controls. |
| 3 | **6 / 12 — anchor head resampled** | Both direct and scheduled publication use a later unverified head instead of the `VerifyResult` head. Same root at two sites; in-memory witness only. Separate bounded publication claim; no SEC-11 activation credit. |
| 4 | **11 — active breaker read** | An unverified concurrent clear affects the returned active set after chain verification. Coordinate with D4 authority-read work and its order-path review; no broker-boundary conclusion from this fixture alone. |
| 5 | **3 / 9 — floor coverage / refill** | Committed `append_in` does not advance the floor without verification; a detected total erasure can later verify after ordinary refill reaches the old numeric head. Distinct failure modes within the larger erasure-lifecycle boundary; defer. |
| 6 | **5 — completeness snapshots** | A healthy atomic state/audit commit appears uncovered because the two reads see different snapshots. False refusal; separate snapshot correction. |
| 7 | **4 — older caller snapshot** | A legitimate pinned old snapshot is compared with the newer process floor and labeled erased. Do not infer that stale snapshots may authorize current orders; define snapshot ownership before repair. |
| 8 | **10 — watch-only generation ordering** | Reordered signed transitions produce a generation regression; restart restoration correctly installs integrity refusal. Coordinate with D1/D2 serialization rather than changing restore acceptance. |
| 9 | **7 — concurrent first boot** | An entirely fake credential store returns different first-boot keys to two callers. No real keyring access or key output. Provisioning/single-owner lifecycle scope; separate from audit metadata repair. |
| 10 | **1 — concurrent genesis** | A healthy first append committed between allocation reads is mislabeled missing-watermark tamper. Availability issue adjacent to #68's read order; keep the established-chain refusal intact and do not broaden this claim to allocation redesign. |

**Concrete single-PR shape for Admin**

Proposed title: **`fix: refuse changed audit high-water before signing`**.

Proposed claim: **An append that observed an established HWM must reject a missing or invalid HWM at the signing boundary and preserve the damaged authority for inspection; its exception-propagating transaction must roll back the attempted audit/state writes.** This claims the storage primitive and its existing caller transaction contract only. Allocate a new linked BUG entry under the SEC-11 audit-spine contract before implementation; do not relabel BUG-114 as unfixed or claim SEC-11 closed.

1. Restrict production edits to `app/storage/audit.py`, primarily `_bump_hwm` and the initialization context supplied by `_insert`. Distinguish genuine first initialization from previously established authority. Missing authority is permitted only for that initialization path; invalid authority always refuses. Validate the actual reread after the entry flush and before any metadata DELETE, INSERT, or signing. Keep legitimate signed advancement monotonic.
2. Raise the existing non-retriable `AuditIntegrityError` on integrity loss. Preserve caller ownership of `append_in` commit/rollback, existing SQLite busy/collision retry behavior, and the state-plus-audit transaction. Do not swallow the exception or recreate a watermark to make verification pass. Keep #68 allocation ordering and #70 result snapshot protection.
3. Promote the HWM refusal fixtures and controls into the application test suite with explicit damaged-bytes preservation and state rollback assertions. Keep healthy first append, cross-writer monotonicity, retained-tail truncation, missing/invalid preflight HWM, and result double-resolution regressions green. A caller must roll back a failed `append_in` transaction; the API must not acquire an independent commit route.
4. Keep the diff to that refusal boundary plus tests and claim/evidence documentation. No schema migration, new signer/key lifecycle, general transaction-manager rewrite, floor repair, breaker recovery, anchor publisher, or gateway change belongs in this PR. If implementation requires changing SQLite transaction policy globally or redesigning safety-state publication, split/reassess the package rather than expanding it silently.
5. Before merge, run the full DEC-09 Windows + Ubuntu/WSL2 × Python 3.11/3.12 matrix and an adversarial review of its production callers. Audit is shared by order authority, so review rollback/error propagation and D1/D2 interaction even if only storage changes. This report does not provide either new release attestation or a tested repair diff.

**HWM claim versus watch_only D1–D4**

Choose the narrow HWM claim for the next storage repair; do not defer it on the premise that #68 already covers it. Keep the [watch-only durability findings](astra-watchonly-durability-2026-09-06.md) separately owned: D1 loses an uncommitted blackout across restart; D2 publishes recovery before persistence; D3 admits a completed safety transition inside the final submit interval; D4 does not turn late invalid audit authority into a final veto. Those findings are referenced from that report, not rerun as broker fixtures here.

The HWM change cannot repair D1–D4. In particular, making an audit append raise does not undo a caller's earlier memory mutation: D1/D2 still require conservative pending denial and commit-ordered publication. If Admin is choosing a package to justify restarting paper execution or claiming effective final order denial, **prioritize the coordinated watch-only work instead**; an HWM-only merge cannot supply that claim. This is a scope decision, not permission to clear watch-only or to restore an incident ledger.

Retain all existing paper/live, W-24 selection, exact-date, cohort, and independent-witness gates. This finding does not prove an order reached a broker, detect every replay of previously valid authority, close same-process erasure/refill, or satisfy restart-spanning external-witness requirements.

**Checks and retained evidence**

| Check | Result | Record |
|---|---|---|
| Existing storage, EDGAR pipeline, merger, audit-schema, invariant baseline | **160 passed, zero skipped**, 72.14 s | [Command](../../data/astra-hwm-resign-claim-shape-2026-09-06/baseline/command.json), [log](../../data/astra-hwm-resign-claim-shape-2026-09-06/baseline/output.log) |
| Supplied historical candidates, ordinary failing assertions via `--runxfail` | **14 failed / 3 controls passed**, 1.46 s | [Exact fixture copy](astra-hwm-resign-claim-shape-2026-09-06/historical_probes.py), [log](../../data/astra-hwm-resign-claim-shape-2026-09-06/historical-reconfirmation/output.log) |
| Final strengthened contracts | **17 failed / 9 controls passed**, 1.96 s; 14 HWM failures plus 3 floor-poison failures | [Command](../../data/astra-hwm-resign-claim-shape-2026-09-06/final-contracts/command.json), [log](../../data/astra-hwm-resign-claim-shape-2026-09-06/final-contracts/output.log), [JUnit](../../data/astra-hwm-resign-claim-shape-2026-09-06/final-contracts/junit.xml) |
| Ruff, full application plus newly authored research helpers | **Pass** | [Log](../../data/astra-hwm-resign-claim-shape-2026-09-06/final-lint/output.log) |

The failures are the evidence of unmet refusal contracts, not repair acceptance. The initial 24-case run and separate SQL-boundary supplement are retained; the final 26-case run subsumes them, so their counts are not added together. The historical copy retains its original formatting and is byte-preserved, not rewritten for lint.

Runtime: project `.venv`, Linux Python **3.13.5**, SQLite **3.46.1**, SQLAlchemy **2.0.52**. This is diagnostic evidence on the current source, not a fresh supported-platform matrix. Full application suite and DEC-09 four-cell acceptance were not run here. [Results](../../data/astra-hwm-resign-claim-shape-2026-09-06/results.json) retain timing/counts; the [runner](astra-hwm-resign-claim-shape-2026-09-06/run_checks.py) records argv and bounds each subprocess, clears ambient `COCKPIT_*`, supplies an absent dotenv and null/in-memory keyring, and keeps fixture databases beneath the new data directory. [Manifest](astra-hwm-resign-claim-shape-2026-09-06/manifest.json) binds the report, helpers, provenance, and completed logs/observations.

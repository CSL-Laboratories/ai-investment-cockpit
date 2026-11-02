---
title: RW-20 independent authority, concurrency, and restart pressure review
status: live
last-verified: 2026-09-06
---

# Independent review boundary

The separate test author/reviewer read `AGENTS.md`, `CLAUDE.md`, `docs/README.md`,
`docs/PLAN.md`, `docs/REGISTER.md`, and the adversarial-review requirements in
`docs/ultracode-build-playbook.md`. The reviewer changed only the four new test files
listed below and this evidence file. Production repairs were authored by the parent agent.

This is local deterministic implementation evidence. It does not create a real merger
selection, edge estimate, witness, paper fill, model-runtime attestation, deployment proof,
or DEC-09 matrix pass. The reviewer did not access `/workspace/cockpit-soak`, a deployed
database, production credentials, network APIs, Discord, or a broker/model process.

The existing AAPL broker fixture is deliberately synthetic and confined to each test's
temporary SQLite database. It isolates approval and risk behavior. It is not an intake
candidate, an alteration to the merger watchlist, or evidence toward selection PASS.

# Findings and attack-to-fix results

| Boundary | Independent attack | Result after repair |
|---|---|---|
| BUG-116 order economics | Keep the signed $150 Order; keylessly change proposal quantity/notional and, separately, side so it claims $1.50 against an audited $100 cap | Refused before any FakeAlpaca submission; signed body/tuple binding remains required |
| BUG-116 canonical risk | Leave the signed proposal intact, change the quote below the limit or above the initial quote, and tighten the cap between these values | Risk uses the larger of fixed limit and fresh quote for the fixed quantity; both refuse |
| BUG-116 original authority | Use a signed legacy fixture missing the body hash, tuple hash, flagged state, or side; separately keylessly clear an originally signed flag or change side | Each refuses; legacy refusal leaves the ledger head unchanged and does not backfill authority |
| BUG-117 fresh loss | Sign a green equity sample, then change FakeAlpaca equity by -$1,000 before approval or immediately after adjudication, leaving the scheduled performance cache green | Both refuse with zero submissions against the $500 loss limit |
| BUG-117 final breaker ordering | Allow the pre-loss gateway review, then add three signed, isolated losing round trips while the cache still records zero losses | Final submit reconstructs the new outcomes, trips `consecutive_losses`, and refuses |
| BUG-118 duplicate resolution | Separate AuditLog locks; writer two resolves an intent after writer one's duplicate-result SELECT but before writer one's allocation | Pre-fix produced a second result without a sequence collision. Explicit SQLite `BEGIN` now forces a stale-snapshot write failure/retry and duplicate refusal |
| BUG-114 retained protection | Another AuditLog commits `append_in` after the first writer has read the old high-water and before it reads the head | Single valid chain, three committed rows |
| Multi-writer bounded load | Two independent AuditLog objects append 15 distinct events each from synchronized threads | All 30 identities retained with a valid chain |
| Existing fork preservation | Construct the historic wrong-parent shape with a test-only key so each HMAC is valid; append another ordinary row | Structural `prev_hash` refusal remains. Appending cannot heal the fork |
| BUG-113 restart | Claude and Codex blackout, with and without recovery, followed by a fresh state container and gateway | Generations restore to 1/2; original generation-0 proposals refuse |
| SEC 429 HALT restart | Test-only durable HALT plus signed SEC vendor watch-only, then restart | HALT and global watch-only still refuse; Claude generation remains independent |
| Invalid audit restart | Keylessly change a signed watch-only event to an `ok` generation-0 claim, then restart | Global integrity sentinel installed; proposal lineage refused; ledger remains invalid |
| BUG-119 Codex argv | Add conflicting/duplicate sandbox, unknown switches, unknown/duplicate feature denials, duplicate model/schema/safety flags, missing values, or positional prompt | All refuse; mutated actual `build_argv` output is stopped before transport |

The first pre-fix eight-case run reported 6 failed / 2 passed. Three failures were a test
setup error converting fixture string prices to Decimal and are **not** product findings.
The other three were the independently reproduced BUG-118 duplicate result and the two
fresh-equity loss bypasses. The setup error was corrected before final validation. No test
was converted to an expected failure, and no refusal was relaxed to make the gate pass.

# Concurrency inventory and limits

All production `append_in` call sites were inspected: state-row persistence in
`storage/audit.py`, poll baseline establish/advance/touch in
`filings/edgar/submissions_poll.py`, EDGAR filing/baseline/attribution/extraction/completion
writes in `ingest/edgar_pipeline.py`, batch event persistence in `workflows/events.py`,
and immutable evidence/selection transactions in `strategies/merger_edge.py` and
`strategies/merger_semantics.py`.

These share `AuditLog._insert`; the BUG-114 high-water-before-head ordering remains in
place. The strategy authority transactions also verify and consume a pinned read snapshot.
The new BUG-118 was outside that allocator: `result()` relied on a duplicate guard that
could become stale before allocation. A new valid sequence therefore did not guarantee
single resolution. The repair pins the guard transaction and retries the entire guard
after SQLite rejects its stale write.

Independent AuditLog objects model separate-process lock domains, but the pressure run is
not an actual multiprocess soak or a throughput measurement. `append_in` callers own their
transactions and can still experience busy/snapshot errors; state writes and their audit
rows must roll back together. No new ledger fork was found in the inspected and exercised
interleavings. This is not a proof against every schedule or every storage failure.

The repair prevents new duplicate results. It neither rewrites historical duplicates nor
makes ordinary chain verification detect them. Before future operational recovery, an
operator can inspect an isolated read-only copy for duplicate result refs:

```sql
SELECT json_extract(refs, '$.intent_seq') AS intent_seq, COUNT(*) AS result_count
FROM audit_entry
WHERE json_extract(payload, '$._phase') = 'result'
GROUP BY json_extract(refs, '$.intent_seq')
HAVING COUNT(*) > 1;
```

That query was not run against the soak. A duplicate or fork is evidence to preserve, not
authority for resetting, re-signing, deleting, refilling, or replaying production history.

# Authority review and remaining limits

The new proposal-body hash uses canonical sorted JSON and refuses non-object/non-finite
content. Approval consumes the complete signed body and resolved-order binding, including
status, flagged state, symbol, side, job, risk, order, runtime, and generation checks. Legacy
missing bindings refuse instead of acquiring authority through migration. Fresh risk
evaluates fixed order economics and the direct fresh account snapshot, then reconstructs
signed performance before approval, gateway review, and final submit. It checks newly
active breakers after reconstruction, which is necessary for the consecutive-loss attack.

The Claude argv repair has an explicit closed grammar: required tool removal and wildcard
denial, strict MCP configuration, safe/restricted execution, empty setting sources,
disabled slash commands and Chrome integration, no persistence, no resume, and no unknown
or duplicate switches. Repair calls retain the same fresh-session profile. Review of this
code is not proof that a particular installed CLI supports every flag; unsupported flags
must produce refusal, and real provider/runtime attestation remains separate.

A further pre-existing Codex inspector gap was reported to the parent during this review
and repaired as BUG-119:
adding `--sandbox danger-full-access` before the final stdin `-` to a built hardened argv
was accepted by `inspect_argv`, as was an arbitrary `--future-tool`. Its sandbox reader
used the first value and its grammar did not reject all duplicates/unknown switches.
The reproduction called only the inspector and printed acceptance; no runtime was spawned,
and no model-controlled path for mutating argv was established. This narrower result must
not be described as demonstrated broker access. The parent added a closed Codex argument
grammar that preserves the existing builder's capability-denial profile and rejects
unknown or duplicate flags, alternate aliases, malformed values, and unknown/duplicate
disabled-feature entries. The reviewer inspected that repair and authored 17 independent
regressions, including a mutated actual-argv call through `run_agent` that must refuse
before its test transport is reached. Those tests now pass. No blocker remains in the
reviewed BUG-115 through BUG-119 changes; real CLI attestation remains a separate gate.

Vendor generations are independent. A SEC vendor trip does not increment Claude/Codex's
generation. The SEC restart case proves durable HALT plus restored global watch-only; it
does not prove that clearing/recovering SEC invalidates all prior model analysis by itself.
Production feed freshness, process epoch, current filing/evidence checks, and W-24 remain
separate required authorities. Invalid-audit startup stays globally closed, and the test
does not repair the invalid ledger.

# Commands and evidence scope

Executed from `app/` on native Windows using the project `.venv`, with `TMP` and `TEMP`
set to `C:\t`; the suite's autouse fixture clears ambient `COCKPIT_*` variables.

```powershell
..\.venv\Scripts\python.exe -m pytest -q tests/storage/test_rw20_concurrency_pressure.py tests/brokers/test_rw20_authority_adversarial.py tests/orchestrator/test_rw20_restart_pressure.py tests/agent_runner/test_rw20_codex_authority.py
..\.venv\Scripts\ruff.exe check tests/storage/test_rw20_concurrency_pressure.py tests/brokers/test_rw20_authority_adversarial.py tests/orchestrator/test_rw20_restart_pressure.py tests/agent_runner/test_rw20_codex_authority.py
```

The first final run, before BUG-119 and the additional legacy-binding tests, passed
18 tests in 7.08s with Ruff clean. The expanded **final independent run passed all
41 tests in 6.75s; Ruff clean.** Interpreter:
`Python 3.13.14 [MSC v.1944 64 bit (AMD64)]`. Base HEAD at this review was
`94b8b4befd9d34ce41b296d4c541378334ae5acc`, with the parent's uncommitted RW-20 repairs
and these test files. This does not attest Python 3.11/3.12 or WSL2. The parent records
the complete package validation and four-cell DEC-09 result separately.

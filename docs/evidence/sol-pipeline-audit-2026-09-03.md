---
title: Sol audit — live soak and W-24 natural-head re-screen pipeline — 2026-09-03
type: evidence
status: soak-defect-selection-refuse-correct
last-verified: 2026-09-03
soak_sha: 1e2d4e3ca7c2fcae4e320ce5f72b44905b036ee4
---

# Sol pipeline audit — 2026-09-03

## RESULT: soak-defect

The three W-24 `NO PASS` / `date-not-ready` results are correct fail-closed
refusals. I found **no dropped cash-eligible, date-ready head** and no reason to
change the selection bar. I did find a separate live-soak defect: concurrent audit
writers forked the signed hash chain at sequence 13589. The paper-performance path
detected the fork on its next tick and entered `watch_only`, so it failed closed,
but the soak can no longer supply a valid continuous audit chain.

The fork occurred at 2026-09-03 16:43:01Z, about four hours after Thursday's
12:38–12:55Z re-screen, and after both Wednesday results. It cannot explain or
invalidate those selection refusals.

## Soak status

Observed through 2026-09-03 17:58Z. No restart, checkout, reset, database write,
Discord post, watchlist seed, or order action was performed.

| Check | Result |
|---|---|
| Soak worktree | detached, clean, exact SHA `1e2d4e3ca7c2fcae4e320ce5f72b44905b036ee4` |
| 8787 | The service answered unauthenticated `GET /status` with the expected `401` at 12:38Z and 12:53Z; the same process log continued scheduled work through 17:58Z. It was not restarted. |
| Authenticated status probe | `cockpit_api_key` loaded from CryptFileKeyring (value redacted), but this Codex sandbox denies all socket creation (`EPERM`), including loopback. The current fields below were therefore reconstructed read-only from the same SQLite ledger, `.env`, and live process log rather than falsely claiming a successful authenticated HTTP response. |
| Mode | `read_only`; there is no persisted mode override |
| `watch_only` | **true** — signed events show `sec:edgar` generation 39 at seq 13575 and `paper_performance` generation 3 at seq 13592 |
| Audit chain | **not OK** — independently HMAC-verified with keyring key (redacted); first error `prev_hash mismatch at seq 13589`; observed DB head/HWM 13963 and HWM signature valid |
| Paper performance | **not OK** — reconstruction refuses on the audit-chain error; Alpaca account/positions GETs continue succeeding; `order_row` count is 0 |
| SEC feed | SEC user agent is present (value redacted). At 16:40:05Z EDGAR received HTTP 429, issued the pinned fair-access HALT with no retry, and entered `watch_only`; later scheduled checks remain degraded. This is correct fail-closed behavior, not a semantic-screen omission. |
| Other feeds | Finnhub poll jobs and Alpaca IEX quote/account/positions GETs continued successfully through the observation time. |
| Discord awareness | Guild/operator plus audit/brief/alerts/proposals/system IDs are present (values redacted); gateway connected/resumed on Sep 3. No selection message or command was posted. |

The live fork is concrete:

| Seq | Event | `prev_hash` relationship |
|---:|---|---|
| 13587 | `market_data_raw_snapshot` | prior valid head |
| 13588 | `ingest_batch` | points to 13587 |
| 13589 | `market_data_raw_snapshot` | also points to 13587 instead of 13588 |

Every row's own HMAC verifies; the broken predecessor link is what fails. The signed
high-water continued monotonically, so this is not keyless tampering or truncation.

## Pipeline wiring and re-screen completeness

- Soak and main-clone `app/.env` files are byte-identical (SHA-256
  `2bb8cb47ab8a1bb3e917dbcae1bbe3e929e4bbb839f205cb982680410fb67790`).
- `COCKPIT_MODE=read_only`; `COCKPIT_SEC_USER_AGENT` is present;
  `COCKPIT_MERGER_WATCHLIST` is empty by design.
- The natural-head intake is a GET-only evidence job, not the soak selection/order
  path: live SEC discovery/submissions/documents are captured under
  `/workspace/w24-evidence`, then parsed offline with main-clone PR #62 semantics.
  No output from that script itself grants order authority.
- The 191-symbol union pool is unique and every symbol produced exactly one head
  classification: 33 supported absolute heads, 108 unsupported absolute heads,
  and 50 without a current SEC ticker map. All 377 symbol/filing evaluations had a
  non-empty local primary document; no supported row or planned document was lost.
- Independent replay with the current, unchanged PR #62 grammar reproduced exactly
  two strict `_CASH` symbols: BHF and SAFT. Only BHF passed semantic eligibility and
  `current_matches_set_end`; neither expected nor outside date parsed. No eligible
  source set had both dates. The claimed cohort remains 1, far below 30.

One bounded intake limit was checked explicitly: CYCU/CYCUW's FTS discovery filing
is supported-filing rank 20 and therefore outside the 12-document cache window. The
[SEC filing](https://www.sec.gov/Archives/edgar/data/1868419/000149315226027982/form8-k.htm)
states that Cycurion was the acquirer, the private-target merger had already closed,
and consideration included equity/preferred-stock components. It is not an open,
public-common, cash-only, date-ready target, so this limit dropped no qualifying head
in this shot. It is not a basis to loosen or claim W-24.

## BHF date verdict: correct refuse

The combined BHF source set remains 10-Q `0001685040-26-000028` through absolute
head 8-K `0001685040-26-000031`; cash is `$70.00`, `has_stock=false`, eligibility is
OK, and `current_matches_set_end=true`.

Direct parsing of both primary documents produced:

| Probe | Result |
|---|---:|
| `_EXPECTED_DATE.findall(combined)` | 0 |
| `_OUTSIDE_DATE.findall(combined)` | 0 |
| literal `outside date` | 0 |
| exact calendar expected-date phrase | 0 |

The only nearby calendar language is conditional failure-to-close by September 6,
automatic extension to December 6, and a year-only expectation for 2026. Those are
exactly the prohibited `not closed by`, `auto-extended to`, and year-only forms.
The officer-appointment head 8-K adds no merger dates. BHF cannot become date-ready
merely because either calendar date passes; a new supported SEC filing would need to
state one unambiguous pinned expected date and one pinned outside date.

## Thursday supported-head audit

| Name | Verdict |
|---|---|
| SAFT | `$105.00` strict cash hit, but `has_stock=true`; expected and outside dates are also null. No date-ready path would open, so the held stock-FP package is not claimed. |
| KR | New supported 8-K head; no strict cash-per-share value and neither date parsed. |
| TXNM | Returned supported 8-K head; no strict cash-per-share value and neither date parsed. |
| FSEA / SLP / CCO | Real alternate cash wording remains outside the pinned `_CASH` grammar and neither pinned date field parsed. No alternate-cash package is claimed. |
| Other supported heads | No additional strict cash-eligible source set. ZSQR had one isolated outside-date match in an ineligible document, but no expected-date match and no cash eligibility. |

**Dropped date-ready heads: none.**

## Real defect and repair

`BUG-114` is claimed in `docs/REGISTER.md`. Root cause: `AuditLog._insert` used to
read the row head before the signed high-water. Python sqlite3 legacy transaction
control does not pin a snapshot for those SELECTs, while `append_in` necessarily
releases the in-process lock before its caller commits. A commit between the two
reads could therefore provide a newer high-water alongside a stale head, allocating
a unique sequence with the wrong predecessor.

The fail-closed repair reads the signed high-water first and the row head second. A
commit between reads is then incorporated into the predecessor link; a later racing
commit can only cause a unique-sequence collision, which retries or aborts rather
than forking the ledger. A deterministic WAL interleaving test reproduces the fork
on the old order and passes with the repair. No date, cash, cohort, mode, approval,
or order rule changed.

Validation available on this machine:

- audit + merger-semantic gates: **56 passed**;
- remaining non-ASGI suite: **2,413 passed, 7 skipped**, plus one expected sandbox
  failure because socket creation is denied;
- Ruff: **clean**.

The only installed interpreter is Python 3.13.5, outside `DEC-09`. Under it,
Starlette's first `TestClient` request hangs even alone, so the required native
Windows + WSL2 Python 3.11/3.12 matrix remains pending and is not claimed.

### Branch / PR

- Claimed branch: `work/sol-w24-pipeline-audit-2026-09-03`
- Prepared code commit: `ce410ac` (`fix: serialize audit allocation snapshot`)
- Verified importable branch bundle:
  `/tmp/sol-w24-pipeline-audit-2026-09-03.bundle`.
- **PR: no PR — bar unchanged.** This sandbox cannot resolve `github.com`, and the mounted
  root `.git` metadata is read-only. The patch remains in the main-clone working
  tree; the soak tree and soak database were not modified.
- **Selection bar unchanged.** This is an audit-ledger concurrency repair, not a
  W-24 semantics change.

## What Lucas needs to do

For W-24 selection: **nothing**. Keep the merger watchlist empty, do not post a
selection witness, and do not claim the cohort. Continue waiting for at least 30
naturally selection-ready heads with both pinned dates.

For the soak defect, Cockpit Admin should publish the prepared branch/PR from a
normal writable/networked checkout, run the required 3.11/3.12 Windows + WSL2
matrix, and merge only if green. The current invalid ledger must not be manually
healed or rewritten; preserve it as defect evidence and handle any replacement soak
or fresh ledger as a separately reviewed operational recovery. No restart was made
or requested by this audit.

---
type: evidence
status: implementation-complete-evidence-pending
last-verified: 2026-08-18
---

# W-24 prospective cohort intake — 2026-08-18

## Scope and authority boundary

This was a GET-only readiness screen from exact source commit
`0cd0f03eca0133c873a69ef0eb86b0b3d31b8965`. The
[Ditat live pending-deal list](https://www.ditat.io/merger-arb) and
[StockAnalysis 2026 acquisitions list](https://stockanalysis.com/actions/acquisitions/2026/)
supplied a discovery pool; the official
[SEC company-ticker map](https://www.sec.gov/files/company_tickers.json), submissions, and archive
endpoints supplied every identity and filing tested. Discovery lists are not authority, no raw
third-party response is tracked, and no screened name was accepted as a candidate merely because
it appeared on a list.

The operational state remained `read_only`. The merger execution watchlist and all merger
evidence/selection tables were empty. No Discord selection message, proposal, risk decision,
approval, paper Order, live Order, or mode change was created.

## Result

Forty-six discovery names were screened against the current SEC ticker map and their latest
supported 8-K/10-Q/10-K:

- 23 no longer had a current SEC ticker-map entry;
- four foreign registrants had no recent filing in the ingestion path's supported form set;
- 19 had a current SEC mapping and a latest supported filing; and
- zero of those 19 latest filings passed the pinned W-24 eligibility parser.

Eighteen of the 19 current filings did not state one unambiguous cash-per-share value in the
parser's accepted form. The remaining filing did not state the required definitive-merger
assertion. The 19 current filing heads were:

| Symbol | CIK | Current supported accession | Form | Filed | Deterministic refusal |
|---|---|---|---|---|---|
| BHF | `0001685040` | `0001685040-26-000028` | 10-Q | 2026-08-06 | cash/share absent |
| TWO | `0001465740` | `0001104659-26-089438` | 8-K | 2026-08-03 | cash/share absent |
| CCO | `0001334978` | `0001334978-26-000025` | 10-Q | 2026-08-05 | cash/share absent |
| IMXI | `0001683695` | `0001628280-26-055191` | 10-Q | 2026-08-10 | cash/share absent |
| NATH | `0000069733` | `0001437749-26-026427` | 10-Q | 2026-08-07 | cash/share absent |
| WBD | `0001437107` | `0001437107-26-000075` | 10-Q | 2026-08-06 | cash/share absent |
| TXNM | `0001108426` | `0001108426-26-000048` | 10-Q | 2026-07-31 | cash/share absent |
| NSTS | `0001881592` | `0001437749-26-027675` | 10-Q | 2026-08-13 | cash/share absent |
| FSEA | `0001943802` | `0001193125-26-339258` | 10-Q | 2026-08-07 | cash/share absent |
| CRNX | `0001658247` | `0001140361-26-032951` | 8-K | 2026-08-14 | cash/share absent |
| DV | `0001819928` | `0001104659-26-092934` | 8-K | 2026-08-10 | cash/share absent |
| SLAB | `0001038074` | `0001038074-26-000029` | 10-Q | 2026-08-11 | cash/share absent |
| RAMP | `0000733269` | `0001104659-26-097907` | 8-K | 2026-08-17 | cash/share absent |
| ACA | `0001739445` | `0001739445-26-000126` | 10-Q | 2026-08-05 | cash/share absent |
| CZR | `0001590895` | `0001590895-26-000028` | 10-Q | 2026-07-28 | cash/share absent |
| HZO | `0001057060` | `0001193125-26-341302` | 8-K | 2026-08-10 | definitive merger absent |
| ELSE | `0000351789` | `0001140361-26-030505` | 8-K | 2026-07-31 | cash/share absent |
| EA | `0000712515` | `0001140361-26-031157` | 8-K | 2026-08-04 | cash/share absent |
| LPRO | `0001806201` | `0001193125-26-326655` | 8-K | 2026-07-31 | cash/share absent |

These are safe parser refusals, not permission to hand-enter terms or ignore later filings.

## BUG-108 — prospective source-set deadlock

The intake failure exposed a source-binding contradiction:

1. A selection candidate has one `source_accession`.
2. Selection requires that accession to equal the strict poll's current
   `baseline_accession`, protecting against a hidden later filing.
3. Calibration derives eligibility from only that one accession.
4. Normal pending targets often file later periodic or event reports that become the current
   baseline without restating all merger terms.

Consequently, an announcement filing cannot be selected after any later supported filing, while
the later filing usually cannot reproduce the merger label. Requiring 30 individually unique
targets on one selection date makes relying on same-day announcement filings unrealistic. The
2026-08-18 screen reproduced this condition across all 19 current SEC-mapped names tested.

This is not resolved by weakening the current-baseline check. The separately scoped `BUG-108`
branch implements `merger_selection_manifest.v2` with three distinct bindings per candidate:

- `source_accession` remains the original semantic/eligibility source referenced by the unchanged
  W-23 calibration row;
- `source_accessions` is a witnessed, bounded, duplicate-free, filing-date-ordered set containing
  every locally signed supported filing from that source through selection; and
- `current_accession` is explicit, must end the reviewed set, and must still equal the strict
  poll's audited `baseline_accession` at both pre-witness and post-witness transaction checks.

The verifier rejects unsigned/conflicting domain rows, missing/extra/reordered accessions,
future/post-commit filings, a non-current endpoint, a changed filing-set hash, and schema-v1 or
post-witness manifest mutations. It derives the prospective prediction and eventual evaluation
label from the same frozen source set. A later ordinary filing can therefore remain current
without replacing the announcement's deal terms; an explicit merger amendment is separately
recognized and must agree with `amendment_reviewed`. No SQL migration or legacy recommitment is
needed because the live selection table was empty, and legacy v1 authority fails closed.

The adversarial pass found one additional cross-document risk: a plain-space join could combine
the end of one filing with the beginning of another into parser-accepted language that neither
filing stated. The repair now inserts a hard sentence boundary between every signed document and
has a regression proving split fragments cannot synthesize cash consideration. Final local
Windows verification is 2,303 passed / 1 skipped with five dependency deprecation warnings; the
23-test semantic slice and repository-wide Ruff are clean. This is not Ubuntu/macOS attestation.

This is code/mechanical evidence only. It does not turn the 19 screened names into candidates,
prove that SEC discovery was globally complete, or satisfy the real cohort, held-out precision,
current-target, external-witness, or cross-platform gates.

## Gate status

`BUG-108` is repaired on its isolated branch, with merge and missing cross-platform attestation
still pending. W-24 selection itself remains open because no complete real ≥30-name universe has
been reviewed or committed. The empty execution watchlist and unavailable edge are the correct
state. Do not assemble a partial manifest, backdate selection, reuse a retrospective fixture,
substitute an older accession for the current baseline, or post a Discord commitment until the
complete real universe and every signed source are ready for one atomic witnessed selection.

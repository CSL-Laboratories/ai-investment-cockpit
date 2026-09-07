---
title: Astra cash/date grammar false-negative survey — held packages
type: evidence
status: research-only-no-date-ready-path
last-verified: 2026-09-06
main_commit: c5ef0ad3d9acd3a85aa86228491ea1827bfd529e
scope: RW-21 / W-24 held SAFT, FSEA, SLP, CCO
---

**Decision: research only; no grammar branch or PR.** The held filings contain real,
explicit cash consideration that `main` either misses or disqualifies through unrelated
stock language. None supplies a newly extractable pair of exact current-deal dates.
The requested condition for opening a grammar branch is therefore unmet. Two tempting
one-line repairs also introduce demonstrated unsafe acceptances. **No selection PASS is
claimed.**

This survey was completed on September 6, 2026, America/Los_Angeles, before the requested
approximately 20:15 deadline. It examines the four held packages only, with the existing
BHF excerpt used solely as a date-refusal control. It makes no fresh discovery, SEC
request, current-head certification, witness publication, watchlist change, or order-path
change. Application code is unchanged; the research files are uncommitted.

**Inputs and reproducibility.** The baseline is local `main` and `origin/main`
`c5ef0ad3d9acd3a85aa86228491ea1827bfd529e` (PR #70 merged). The working checkout started
at `892e2dbaab0093e91857cea373dc188d638d8d80`; its three relevant strategy modules are
byte-identical to that `main`. This matters because `_diagnose_grammar.out` predates the
accepted cash-head changes, while the September 2/4 screens predate PR #70's assertion
and displayed-text repairs. Their results are historical inputs, not today's parser output.

All **38 cached supported primaries**, totaling **42,556,290 bytes**, for these symbols
under `/workspace/w24-evidence/rescreen-raw/docs` were replayed: SAFT 10, FSEA 5, SLP 11,
CCO 12. There were no missing selected inputs or text-extraction exceptions. Each filing
was passed through `main`'s `_text`, assertion-aware cash extraction, eligibility check,
and exact expected/outside date checks. The diagnostic also joins the intervening held
primaries for hypothetical cash sources using `_combined_source_text`; those joins are
unsigned research sets, not verified production source sets.

The [input manifest](astra-grammar-false-negatives-2026-09-06/inputs.json) records every
local raw-file hash, size, accession, form, filing date, official source URL, prior-screen
metadata hash, and relevant code hash. The [results](astra-grammar-false-negatives-2026-09-06/results.json)
retain normalized source spans, field refusals, and diagnostic experiments. Full HTML is
available in this workspace even though the earlier repository-only review lacked it.
These are **existing cache bytes hashed now**: no fresh HTTP capture/header verification,
signed ingestion, or independent provenance attestation was performed. Normalized offsets
are research locators, not raw-byte citation offsets.

| Held package | `main` replay | Extractable cash defect | Exact-date result |
|---|---|---|---|
| SAFT | One accepted `$105.00` cash match in 10-Q `0001172052-26-000023`; label has `has_stock=True`; strategy refuses | Historical repurchase count triggers the stock predicate | No expected or outside match in any of 10 primaries |
| FSEA | Zero cash matches in 5 primaries | Three primaries bind each target common share to `$17.25 in cash`, without repeating `per share` after the amount | Quarter-only expectation; no expected/outside exact pair |
| SLP | Zero cash matches in 11 primaries | Announcement binds each common share to `$18.50 in cash`; its verb order is already supported | Conditional February 10, 2027 end date; no exact expected closing day |
| CCO | Zero cash matches in 12 primaries | Anchored conversion clauses put cash before `$2.43` and include cancellation/extinguishment | Quarter-only expectation; conditional termination/extension dates |

Across the 38 whole documents, production raw date-pattern matches are **zero for both
fields**, before assertion filtering. Cash-strategy eligibility is zero. These counts
describe this failure-selected cache only; they estimate neither population recall nor
precision. Cash extraction, cash-strategy eligibility, date-ready current-deal authority,
and prospective selection are distinct checks.

**SAFT: confirmed whole-document stock false positive.** In
[10-Q `0001172052-26-000023`](https://www.sec.gov/Archives/edgar/data/1172052/000117205226000023/saft-20260630x10q.htm),
the accepted MD&A clause already supplies `$105.00 per share in cash`. The exact
`_STOCK_CONSIDERATION` match is:

> and december 31, 2025, the company had purchased 3,478,060 shares of common stock

The conjunction-led branch `(?:receive|plus|and)[^.]{0,100}...shares...stock` assigns a
historical purchase count to consideration. The existing reduced SAFT unit fixture omits
this numeric sentence; its passing result does not close the full-primary defect.
Removing only the bare `and` alternative in a process-local experiment makes this whole
10-Q cash-strategy eligible with unchanged `$105.00`. It supplies neither date.

That experiment is **not a safe repair**. A synthetic common-share payout of `$105 in cash
per share`, followed by over 100 characters of payment mechanics and then `and 0.5 shares
of parent common stock`, is stock-flagged by `main` but incorrectly classified cash-only
after that deletion. Future work must distinguish repurchase assertions while preserving
delayed mixed-consideration conjunctions; blanket removal of repurchase sentences is also
unsafe when a sentence contains both subjects.

The held [July announcement 8-K](https://www.sec.gov/Archives/edgar/data/1172052/000110465926086488/tm2621207d1_8k.htm)
also has a separate alternate cash clause: `will be cancelled and cease to exist and
converted ... $105.00 in cash`. That verb sequence still misses and is unnecessary to
repair the already extractable 10-Q amount. Its calendar language is conditional:
non-consummation by July 23, 2027, with automatic extension to January 23, 2028. Closing
is otherwise relative to satisfaction/waiver of conditions, not an exact expected day.

**FSEA/SLP: genuine tail misses, with a correction to the earlier SLP diagnosis.**
FSEA [8-K `0001193125-26-206551`](https://www.sec.gov/Archives/edgar/data/1943802/000119312526206551/d74001d8k.htm)
states that each share of company common stock outstanding before the effective time will
be converted into the right to receive `$17.25 in cash, without interest`. The March and
June 10-Qs repeat that structure. The anchored head already identifies one target share;
requiring another per-share suffix rejects an explicit denominator rather than resolving
an ambiguity in these actual clauses.

SLP [8-K `0001023459-26-000024`](https://www.sec.gov/Archives/edgar/data/1023459/000102345926000024/slp-20260615.htm)
likewise binds each common share to `$18.50 in cash, without interest`. Contrary to the
earlier [RW-21 review](rw21-filing-grammar-2026-09-06.md), `main` already recognizes
`will automatically be converted`: the optional `automatically` precedes optional `be`
in its conversion branch. The tail-only experiment extracts this unchanged whole 8-K;
no SLP verb expansion is justified by this example.

Allowing bare `in cash` immediately before punctuation, with the original head and verb
unchanged, yields cash-strategy eligibility for all three FSEA sources and the SLP
announcement. FSEA's four-file announcement-through-August-vote diagnostic set also clears
that narrow cash check. **SLP's five-file set does not**: the intervening
[10-Q `0001023459-26-000038`](https://www.sec.gov/Archives/edgar/data/1023459/000102345926000038/simu-20260531.htm)
sets `has_stock=True` on historical share issuances and `has_earnout=True` on an Immunetrics
earnout liability. Those are additional context-binding concerns, not permission to erase
the flags. Its own cash narrative starts with holders and puts `for each share` after
the amount; that broader construction is unnecessary for the announcement-tail diagnosis.

The tail-only experiment also fails a synthetic refusal control:

> Each share of common stock will be converted into the right to receive a distribution;
> employee options receive $17.25 in cash, without interest.

`main` refuses this amount; the experiment assigns it to common shares. The current
subject-switch guard covers the head-to-verb span, but the subsequent `[^.]{0,120}` span
can cross into another security's payout. A future tail repair must constrain that span
as well. Merely shortening or widening it is not a demonstrated fail-closed solution.

**CCO: the common-share head already works; the conversion and amount order do not.**
The current head accepts `each share of the company's common stock` in
[10-Q `0001334978-26-000025`](https://www.sec.gov/Archives/edgar/data/1334978/000133497826000025/cco-20260630.htm).
The observed continuation is `will be automatically canceled, extinguished and converted
into the right to receive cash in an amount equal to $ 2.43 , without interest`.
The [announcement 8-K `0001193125-26-043066`](https://www.sec.gov/Archives/edgar/data/1334978/000119312526043066/d939743d8k.htm)
uses the corresponding `cancelled` spelling. An isolated diagnostic retains the existing
bounded head/subject guard and recognizes only this observed conversion and cash-before-
amount sequence. It extracts `$2.43` in four primaries. Three clear the standalone cash
check; the 10-K still has an earnout flag concerning sold businesses. There is no need to
accept the separate, broader stockholders-receiving narrative to demonstrate this miss.

Starting from the June 10-Q alone clears the diagnostic cash check but yields no dates.
Earlier-to-head diagnostic sets have another refusal: `_NOT_DEFINITIVE` matches
`indication of interest` in a March go-shop report that says none was made, and
`non-binding proposal` in the May shareholder advisory compensation vote. These remain
held context-binding concerns; a cash regex cannot repair them, and this survey changes
neither their predicate nor source-set completeness rules.

**Date language remains a required refusal.** The visible filing spans contain the
following nearby calendar language. None is a missed affirmative pair of the existing
expected/outside fields.

| Source | Actual date/timing language | Required treatment |
|---|---|---|
| SAFT announcement 8-K | July 23, 2027 non-consummation trigger; automatic January 23, 2028 extension; closing relative to conditions | Preserve conditional/extension refusal; do not choose either calendar day |
| FSEA announcement and 10-Qs | Closing expected to occur in third quarter of 2026 | Keep quarter-only unavailable; do not invent September 30 |
| SLP announcement 8-K | May terminate if not consummated on or prior to February 10, 2027, defined as end date | Keep conditional termination distinct from expected close; an end-date alias alone cannot supply the missing pair |
| SLP August 28 vote 8-K | Closing remains subject to conditions, including French regulatory approvals | No exact expected closing day supplied |
| CCO 10-Q / announcement | Close by end of third quarter; not before March 26, 2026 without consent; November 9, 2026 termination trigger extendable to February 9, 2027 | Quarter, lower bound, and conditional termination/extension are not the two affirmative pinned dates |
| Existing BHF review quote | Expected to close in 2026; not-closed-by September 6; automatic December 6 extension | Preserve all three refusals; passage of time supplies no filing assertion |

The production contract remains one unambiguous calendar date from each pinned pattern,
with affirmative assertion filtering and calendar validation. No year-to-day conversion,
quarter-end calculation, negative closing bound, extension resolution, synonym-only
end-date change, or cross-filing sentence synthesis was used. Fixing the cash defects
leaves these actual filings date-unready.

The latest **held** head screen is September 4, not a fresh September 6 poll. It places
SAFT at unsupported `PREM14A` `0001104659-26-105083`, independently preventing reuse of
the earlier 10-Q as a current head. FSEA remains at `0000943374-26-000392`, SLP at
`0001023459-26-000060`, and CCO at `0001334978-26-000025` in that snapshot. No unsupported
proxy or exhibit was substituted, and none of these observations certifies current
selection eligibility.

**Fixture priorities if this held work is reopened.** Prefer a reviewable fixture package
that preserves the failures and refusals before considering production grammar changes.
The present request's date-ready condition is unmet, so this is a specification, not an
opened PR or claimed implementation package.

| Fixture group | Positive/defect evidence to pin | Refusals required alongside it |
|---|---|---|
| Whole SAFT 10-Q | Original `$105.00` and the full numeric repurchase sentence in the same raw document | Mixed stock before/after cash, delayed `and` payout, fractions, and a sentence containing both repurchase and actual stock consideration |
| FSEA/SLP cash tail | Untouched FSEA 8-K + 10-Qs and SLP 8-K; retain SLP's existing verb order | Option/award/preferred/debt subject switches before **and after** the receipt verb; cross-sentence, block, and filing joins; absent explicit cash; negated and conditional payouts; conflicting amounts; CVR/stock/earnout |
| CCO observed conversion | Original head, cancellation/extinguishment spellings, cash-before-amount, spaced dollar token | Unanchored stockholder narrative, acquirer consideration, unrelated securities, missing cash token, negation, conflict, and currency/amount misbinding |
| Whole-set context | SLP issuance/Immunetrics paragraph; CCO go-shop/advisory-vote and divestiture paragraphs | Preserve genuine merger stock, earnout, non-binding proposal, and complete-source-set refusals while distinguishing other subjects |
| Calendar boundary | Real SAFT/SLP/CCO termination spans; FSEA/CCO quarters; existing BHF quote | Year-only, not-closed-by, automatic extension, lower-bound closing, negation, multiple dates, missing one field; retain literal May calendar positives |

Known-failure tests should be explicitly marked as known defects or strict expected
failures; a test that reproduces `has_stock=True` must not be presented as a repaired
SAFT PASS. These failure-selected sources must remain development fixtures, separate
from any independently labeled held-out precision/recall population.

**Validation and replay.** Project `.venv`: Python 3.13.5 on this Linux workspace.
The unchanged semantic suites passed **104 tests in 26.61 seconds**. The existing pinned
excerpt companion passed **7 tests**, including BHF's refusal and the intentionally open
SAFT stock false positive. Ruff passed for the research replay. The companion validates
all 38 file hashes and date refusals, eight date-refusal controls, literal May positives,
and the two rejected shortcut counterexamples. It never edits an application file; regex
substitutions exist only in its process and are restored before the date controls.

```bash
.venv/bin/python docs/evidence/astra-grammar-false-negatives-2026-09-06/replay.py > /tmp/astra-grammar-replay.json
.venv/bin/python docs/evidence/rw21-held-excerpts/test_existing_excerpts.py -v
.venv/bin/python -m ruff check docs/evidence/astra-grammar-false-negatives-2026-09-06/replay.py
cd app
../.venv/bin/python -m pytest -q tests/strategies/test_merger_semantics.py tests/strategies/test_rw21_real_filing_grammar.py
```

An optional first argument to `replay.py` supplies a relocated evidence root; all input
hashes must still match. The pinned results are deterministic under the recorded code
and inputs. These focused research checks do not substitute for the four-cell DEC-09
release matrix, prospective observations, independent labels, or source/witness authority.

Companion SHA-256 values:

| Artifact | SHA-256 |
|---|---|
| `inputs.json` | `2d9a2f4baa61d61bd2f303cb73e18f839d657cdee73cf08b644080cf994ee8d5` |
| `replay.py` | `b6b76dfa139e67c11eb1c6c920e043da90bf58f22a2f6fea97b42c3ec29fde33` |
| `results.json` | `d93c641e69b41cbdef0ecdd0efe0b79690bef4e29c079d4ec3f263ba6c9dfa6a` |

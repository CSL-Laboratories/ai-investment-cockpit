# Pinned existing filing excerpts — 2026-09-06

This companion pins five pieces of already-reviewed repository material from historical commit
`b4a199ff41ed84d78f21602a84d9665c1072ec6a`. It makes the review inputs reproducible while the
operator-declared SEC User-Agent and exact primary-document capture remain pending. All files in
this companion are under `docs/evidence/rw21-held-excerpts`; the application and its frozen
DEC-09 test tree were not modified.

**These are repository excerpts, not newly captured raw SEC HTML, full primaries, a held-out
population, or selection authority.** The source accession, official primary URL, historical
source-file hash, exact source slice and line numbers, deterministic text transformation, and
text hash are recorded for each entry in `existing-excerpts.json`.

| Pinned material | Historical source | Purpose |
|---|---|---|
| BHF $70 cash clause | Existing `_BHF_10Q_CASH_HEAD` test constant; accession `0001685040-26-000028` | Preserve the already-supported cash wording. |
| SAFT $105 cash clause | Existing `_SAFT_10Q_CASH_HEAD` test constant; accession `0001172052-26-000023` | Preserve the already-supported cash wording. |
| SAFT agreement clause | Existing `_SAFT_10Q_AGREEMENT_HEAD` test constant; same accession | Retain its actual prior review input separately from a complete eligibility assertion. |
| BHF conditional extension and year-only close | Exact quoted line from the September 2 held audit note | Keep not-closed-by, automatic-extension, and year-only dates unavailable. |
| SAFT historical repurchase-count fragment | Exact quoted line from the same audit note | Reproduce the **still-open** stock-consideration false positive without pretending the full-primary repair passed. |

The cash/agreements are the exact evaluated string literals already present in the historical
test file; the source-code slices are retained so concatenation and Unicode can be reproduced.
The note quotes retain their exact historical Markdown slices. Their parser text removes only
the blockquote prefix, bold markers, and trailing newline; ellipses remain. This is a transparent
transformation of existing review material, not reconstruction of missing primary-document bytes.

CRNX is deliberately excluded: its existing test calls the material minimal frozen grammar and
reconstructs the announcement, so it is not represented here as a verbatim primary excerpt.
FSEA/SLP/CCO full-primary cash repairs also remain held. Nothing here replaces the still-needed
whole-SAFT primary capture containing both consideration and the repurchase count.

Manifest SHA-256:
`a8626387e2de9d5e037f3277e7914d6161eb0f3f9ad3a114aebe385b31678ef1`.

The read-only companion check is `test_existing_excerpts.py`. It verifies manifest, source-slice,
and text hashes; preserves both real cash amounts; confirms no excerpt supplies either pinned
date or complete strategy eligibility; and verifies that BHF cash plus its conditional-date
quote still cannot supply date authority. Its final test intentionally reproduces the known
SAFT false positive, so that individual test passing is **not a bug-fix PASS**.

Run from the repository root with a project interpreter:

```powershell
.\.venv\Scripts\python.exe docs/evidence/rw21-held-excerpts/test_existing_excerpts.py -v
```

Initial validation: **7 tests passed**, including subcases for all five excerpts, on native
Windows Python 3.13.14; Ruff passed for both companion Python files. The companion is outside
the application suite and does not change or substitute for the four-cell DEC-09 matrix. Each
supported interpreter can run the same companion directly after its frozen application gates.

`pin_existing_excerpts.py` documents the local Git-only construction. It refuses to overwrite an
existing pin. No SEC requests, mode changes, watchlist additions, source ingestion, witness
publication, ledger writes, or order operations were made by this companion.

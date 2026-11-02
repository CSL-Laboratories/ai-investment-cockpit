---
type: evidence
status: live
last-verified: 2026-08-24
---

# RW-11 prospective Company Facts capture — 2026-08-18

## Safety and scope

This package closes one tooling gap only: the composed EDGAR pipeline consumes a mutable per-CIK
Company Facts snapshot in memory, then discards the source bytes after deriving transient facts.
`tools/rw11_companyfacts.py` can now capture those exact response bytes for a predeclared CIK
census and verify the isolated write-once package later.

The tool does not import the operational database, settings loader, orchestrator, risk, broker,
approval, gateway, or order code. It cannot change runtime composition, mode, either watchlist,
or any order-path row. Every manifest and CLI result says `authority: none` and
`rw11_gate: OPEN_NOT_CLOSED`. The runtime remains `read_only`, expected edge remains unavailable,
the merger execution watchlist remains empty, and there are no paper opening, live, or autonomous
orders. `RW-12` remains blocked and `RW-13` is not started.

## Evidence contract

Before collection, the operator authors a strict plan containing the exact source commit,
population selector, cutoff, sorted CIK universe, and fixed eligible forms (`10-K`, `10-Q`). The
collector then:

1. calls the repository's existing `SecFetcher.get_bytes` and canonical Company Facts URL in
   sorted CIK order, retaining exact response bytes rather than reserialized JSON;
2. rejects duplicate JSON keys, non-UTF-8/non-finite JSON, a wrong or malformed response CIK, a
   missing facts object, or a facts document with no iterable rows;
3. records each response's fetch time separately from capture start/completion, exact byte/hash
   count, raw-store key, structural counts, and every supported post-cutoff accession summary;
4. binds the plan bytes, source commit, implementation hashes, complete capture set/order, byte
   accounting, known blockers, and raw hashes into the collection hash; and
5. creates the final directory exclusively only after every CIK succeeds. A crash during final
   movement can leave only a permanently invalid partial package; neither a retry nor another
   process may overwrite it.

The verifier rejects missing/extra artifacts, symlinks, manifest or raw tamper, blocker removal,
incomplete byte accounting, response/plan divergence, timestamps outside the capture window, and
changed implementation hashes. A successful collection with zero eligible accessions proves
bounded SEC reachability and an honest quiet population only. It is not precision evidence.

## Review and local validation

One read-only Claude Code Sonnet 5 architecture lane completed after its outer command timed out.
It confirmed the root problem (mutable transient snapshots), recommended a separate capture-only
package, and identified snapshot timing as load-bearing. The implementation binds plan authorship,
capture start, each response fetch, and capture completion separately. The lane preferred a manual
prefetch plus offline freezer. This implementation deliberately keeps acquisition inside the
operator-only collector through the existing `SecFetcher`: that preserves exact wire bytes,
enforces the existing User-Agent/rate/response/run limits, and removes an otherwise unwitnessed
manual handoff. The connection and all SEC URL knowledge remain in `app/filings/edgar/fetch.py`.
Two other Sonnet lanes did not finish before termination and are not claimed as reviews.
A subsequent read-only Sonnet 5 pass reviewed the actual code. It found no P0 and one P1: the
implementation hashes covered two future replay modules but omitted the load-bearing raw-store
module. The final binding now covers `app/filings/raw_store.py` instead. Its Windows link concern
also led to inspection-based reparse-point rejection; a real temporary NTFS junction was detected
and the diagnostic tree was removed afterward.

Current local validation:

- 12 hermetic Company Facts capture tests pass, with one symlink-capability test skipped on this
  Windows token and a separate real-junction diagnostic passing;
- the combined 29 RW-11 tool tests pass, with that one skip;
- 286 broader filing/EDGAR/RW-11 tests pass; and
- the full Windows suite passes 2,350 tests with 2 skips and 5 dependency warnings; and
- Ruff is clean for the new tool and tests.

Full Windows and broader EDGAR gates must run before publication. Hosted Ubuntu/Windows/macOS ×
Python 3.11/3.12 remains missing while GitHub prevents every job from starting under the account
payment/spending-limit restriction.

## Bounded online test handoff for 2026-08-19

The next real run is a read-only SEC evidence collection, not an unattended W-22 paper day.
Prerequisites are a clean exact merged commit, valid audit chain, `read_only`, no HALT, empty
merger execution watchlist, zero proposal/risk/approval/order/selection rows, a predeclared
11-CIK census plan, and `COCKPIT_SEC_USER_AGENT` present. The User-Agent value is consumed by the
fair-access client and is never printed or written into evidence.

Run the capture once during the regular market session. Stop on any 403/429, response/run byte
limit, CIK mismatch, malformed payload, partial census, or dirty-source preflight. Verify the
package immediately. If the census contains a supported post-cutoff periodic accession, preserve
it for the next separate numeric replay package. If it contains none, record the trustworthy quiet
result without claiming precision or closing RW-11.

Still required after capture: deterministic numeric replay/citation rows, an independently
labelled meaningful real population at at least 95% on each required field, a cited prospective
material delta, an external witness, and Ubuntu/macOS attestation.

## 2026-08-24 outcome and platform-policy note

The bounded collection subsequently completed on clean main
`46c7c812eccd476c4578dbef59200f5249c26db3`: all 11 planned CIKs and 31,735,013 response bytes
verified, with zero supported post-cutoff 10-K/10-Q accessions. The quiet census proves
reachability only and provides no supported periodic case for numeric replay. A separate four-row
8-K review seed scored 8/8 semantic fields correct but remains insufficient in size, issuer/form
diversity, and numeric coverage.

The final sentence of the original handoff above recorded the platform requirement then in force.
`DEC-09` now replaces it with the green local native-Windows + Ubuntu/WSL2 × Python 3.11/3.12
matrix; macOS is best-effort and hosted runs are optional. Independent witness, meaningful
population, cited prospective material delta, and numeric replay requirements are unchanged. See
[[local-platform-matrix-2026-08-24]].

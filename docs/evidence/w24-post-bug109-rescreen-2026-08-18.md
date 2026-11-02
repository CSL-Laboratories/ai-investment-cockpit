---
type: evidence
status: prospective-gates-open
last-verified: 2026-08-18
---

# W-24 post-BUG-109 strict rescreen — 2026-08-18

## Scope and authority boundary

This was a GET-only rescreen from merged `main` SHA
`4bcf3330c8befc9c33534fb9f91b44827d485726` after PR #43. It reused the frozen 65-symbol
discovery pool from [[w24-prospective-intake-2026-08-18]] and treated the official SEC ticker map,
absolute submissions-feed head, and primary archive documents as authority. Third-party discovery
lists were not treated as evidence.

The system remained stopped and `read_only`. Nothing was written to the raw store or database. No
historical filing was imported, no quote was captured, no Discord message was posted, and no
watchlist, proposal, risk decision, approval, paper Order, live Order, or mode changed.

## Strict current-head result

The project `SecFetcher` enforced the configured User-Agent, two-request-per-second limiter, and
response/run byte limits. The ticker/submissions pass received 3,111,257 bytes and reproduced the
same strict split:

| Current-state result | Count |
|---|---:|
| No current SEC ticker-map entry | 43 |
| Absolute current head outside 8-K/10-Q/10-K | 16 |
| Absolute current head supported | 6 |

The six supported-head names remained BHF, CCO, NATH, NSTS, CRNX, and RAMP. The maximum possible
manifest from this pool is therefore six, before semantic parsing, and cannot satisfy the minimum
30-candidate gate.

## Merged-parser source-set result

A second bounded pass downloaded the supported 2026 primary documents for those six issuers and
received 43,567,311 bytes without a transport, fair-access, HTTP, or resource-budget error. Five
names still had no independently sufficient eligibility source. CRNX alone produced a complete
semantic source set:

1. `0001140361-26-027642` — announcement source, 2026-07-06;
2. `0001140361-26-028208`;
3. `0001658247-26-000052`;
4. `0001658247-26-000053`; and
5. `0001140361-26-032951` — strict current head, 2026-08-14.

The combined set deterministically derives an active definitive Nasdaq US common-equity deal with
cash offer `85.00`, no CVR, earnout, stock consideration, leverage, rumor/competing bid, or reviewed
amendment. This proves BUG-109's merged grammar reaches one real complete source set. It is not a
selection-ready authority: the bytes and filing rows were not imported/signed, no fresh quote or
same-process strict poll was captured, and one candidate is not a complete declared universe.

## Current-target date refusal

A third 1,458,277-byte GET-only replay applied the separate W-23/W-24 current-deal date grammar to
the five CRNX primary documents. It refused both fields:

- no one unambiguous exact expected-resolution date; and
- no one unambiguous exact outside date.

Quarter/year expectations or dates absent from supported primary documents are not converted into
calendar dates and must not be supplied manually. CRNX is therefore a useful prospective
eligibility/source-set candidate, but it does not satisfy the current eligible-target gate.

## Gate decision

No selection commitment was attempted. The ≥30 cohort, independently held-out ≥95% parser
precision, exact current-target terms/dates, cross-platform attestation, and subsequent 30-day
outcome horizon all remain open. Expected edge stays unavailable, the merger execution watchlist
stays empty, and paper opening orders remain prohibited.

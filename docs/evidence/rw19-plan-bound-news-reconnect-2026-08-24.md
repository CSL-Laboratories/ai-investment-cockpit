---
title: RW-19 plan-bound news-reconnect hardening
type: evidence
status: development
last-verified: 2026-08-24
---

# RW-19 plan-bound news-reconnect hardening

## Baseline and boundary

PR #54 merged as `e09349829d23cf4e95f25536685d462428da1502`. Development began from a clean
`main == origin/main` at that exact SHA on branch `work/rw19-plan-bound-news-reconnect`.

This is an offline collector-hardening package only. It authorizes no Alpaca request, Discord
post, watchlist mutation, cockpit service start, model/risk/approval/broker/gateway/order change,
paper opening order, live capital, autonomous authority, `RW-13`, or `W-22` qualifying day.

## Measured failure addressed

The three completed news-reconnect attempts are hash-valid but empty. They prove apparatus
reachability only. Inspection of the current diagnostic collector found a more immediate evidence
integrity problem: a socket or REST failure after either segment discards every collected row;
REST pagination has no page, token, byte, row, or overall deadline bound; successful page request
IDs are not retained; and capture does not run its verifier automatically.

The package therefore adds a separately named evidence-grade command while retaining the legacy
command unchanged for diagnostic continuity. A qualifying future attempt must be bound before the
window to one canonical plan and exact clean merged source commit, use the exact operator-approved
11-symbol awareness universe, consume one fresh fail-closed safety preflight, and write only to a
predeclared external write-once directory.

## Fixed implementation contract

- strict WebSocket `connected`, `authenticated`, and exact news-subscription acknowledgements;
- per-frame and per-segment caps with no automatic retry;
- streamed, deadline/page/token/row/per-page-byte/total-byte-bounded REST GET pagination;
- credential-reflection rejection before any vendor bytes are persisted;
- preserved sanitized control/provenance records and partial rows in an aborted-attempt tombstone;
- same-parent staging, one atomic directory rename for final publication, link/reparse rejection,
  exact artifact census, and recovery-staging preservation if tombstone publication itself fails;
  implementation hashes, deterministic recomputation, and immediate in-process verification;
- mechanical classification: an empty complete sample is insufficient, a non-empty complete sample
  is observation-only, and every incomplete attempt is failed evidence;
- `authority: none`, `rw19_gate: OPEN_NOT_CLOSED`, and `dec07_status: open` in every output.

The single directory rename is atomic on the supported filesystems and removes the torn
per-child publication window. A hostile local process can still race the pre-rename existence
check by creating an empty target on POSIX, where rename may replace that empty directory; all
published bytes remain hash-bound, but deterministic race refusal is not claimed. Closing that
local-concurrency edge would require platform-specific no-replace syscalls and is outside this
operator-only evidence command. The preflight requires no competing cockpit/news process, and a
pre-existing target always refuses.

An actual collection plan must be authored only after this package merges, because its
`source_commit` must equal the exact clean merged collector SHA. The plan will separately fix the
time window, selection rationale, duration/gap, resource limits, external directory, and remaining
blockers. No future attempt has been run or scheduled by this document.

The safety-preflight JSON is a validated operator evidence input, not a self-authenticating
attestation. For the future attempt it must be produced immediately before execution from the
same stopped-system checks already used for the 2026-08-24 session: composed/runtime mode reads,
direct SQLite row counts and audit-chain verification, HALT and loopback process/listener checks,
credential-presence booleans, the configured awareness universe, and clean git source state. The
handoff must retain the commands and sanitized results used to author every field; hand-typing
unobserved `true`/zero values is not qualifying evidence. A separate producer may be added later,
but it may not import an order path or weaken refusal when any observation is unavailable.

The manifest's implementation hashes intentionally require verification from the recorded source
commit. Its collection hash proves internal consistency, not independent chronology or authorship;
the later handoff must transcribe it out of band. Neither hash closes `RW-19` or `DEC-07`.

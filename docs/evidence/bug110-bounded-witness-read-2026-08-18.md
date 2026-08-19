---
title: BUG-110 bounded S3 witness read evidence — 2026-08-18
type: evidence
status: local-validation-complete
last-verified: 2026-08-18
related:
  - ../PLAN
  - ../REGISTER
  - ../security-hardening-backlog
  - ../runbooks/w25-worm-audit-witness
  - ../research/sec11-zero-cost-witness-feasibility-2026-08-18
---

# BUG-110 bounded S3 witness read evidence — 2026-08-18

## Claim boundary

Branch `work/bug110-bounded-witness-read` starts at exact merged main
`ed9ca96ccab4b3e050416f1c803086494a97b954`. This package changes only the dormant W-25 S3
adapter, focused regressions, and its documentation. It provisions no cloud resource and grants no
authority.

The runtime remains `read_only`; the expected edge is unavailable, the merger execution watchlist
is empty, there are no paper opening orders, live/autonomous modes remain unauthorized, and
`RW-13` has not started. SEC-11 and W-25 remain open because no real independent Object Lock
witness is configured.

## Bounded proof contract

Schema v2 encodes `MAX_SEQ - seq` as a fixed-width 20-digit key component followed by the payload
digest. Amazon S3 documents lexicographic object-key ordering and `ListObjectVersions` ordering by
key, with versions and delete markers counted against the page maximum. Therefore a higher
sequence sorts before every lower sequence without a local cache or mutable latest pointer.

The reader requests exactly one page with `MaxKeys=128`, validates every returned key/version id,
and selects the highest sequence. A truncated page is accepted only if it already reaches a lower
sequence, which proves the complete highest-sequence group was returned. Otherwise it is ambiguous
and fails closed. The candidate must contain exactly one object version and no delete marker; its
canonical body, key digest, metadata, server checksum, exact version id, Compliance mode, and
remaining retention are verified. A post-write bounded head read prevents a concurrent fork or
higher head from being reported as a successful publication.

Official ordering contracts: [Amazon S3 object key sort order](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-keys.html#object-key-sort-order),
[ListObjectVersions](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectVersions.html),
and the [AWS SDK version-list ordering contract](https://docs.aws.amazon.com/AWSJavaSDK/latest/javadoc/com/amazonaws/services/s3/model/ListVersionsRequest.html).

The load-bearing gate property is discovery of the unambiguous highest retained floor. A lower-
sequence historical artifact cannot sort ahead of or conceal a valid higher v2 key and contributes
no authority. The runtime therefore does not perform an unbounded forensic scan of lower history;
the real activation drill must separately inventory the empty/new prefix and exercise delete,
version-delete, shadow, conflict, retention, and rollback attacks.

## Measured operation ceiling and adversarial cases

A rebuilt adapter with no process-local state recovered anchor 2,048 from 2,048 retained anchors
using exactly:

- one `GetObjectLockConfiguration`;
- one `ListObjectVersions` capped at 128;
- one `GetObject`; and
- one `GetObjectRetention`.

A non-idempotent append through `GitAuditLogAnchorStore` keeps its independent monotonic pre-read,
the adapter's own pre-read, and the post-write current-head proof. It therefore uses a fixed three
of each read plus one put, regardless of history size. The extra constant reads preserve defense
in depth rather than trusting an adapter capability flag.

Focused regressions cover reverse ordering, total-local-state rebuild, 2,048-anchor request counts,
a 129-entry same-sequence page overflow, delete marker, shadow version, same-sequence fork,
malformed/falsy list collections, checksum/metadata/size faults, insufficient/Governance
retention, and concurrent same-sequence or higher-sequence writes. Existing composed
captured-watermark rollback, gate, mode, gateway, scheduler, and mirror-rewrite regressions remain
green.

## Verification

- focused witness/anchor/SEC-11/scheduler: **80 passed** in 5.95 seconds;
- full hermetic Windows suite: **2,321 passed / 1 skipped** in 287.04 seconds;
- repository Ruff: clean;
- `git diff --check`: clean.

The full suite set `TMP`/`TEMP` to `C:\\t`, removed process-level `COCKPIT_*` variables, and set
`COCKPIT_ENV_FILE` to a confirmed nonexistent test path. The operator's real `app/.env` was not
moved, read, printed, or modified. The one skip and five warnings match the existing platform/
dependency boundary; no BUG-110 failure was hidden.

Two operator-authorized read-only Claude Code Sonnet reviews were attempted: one with read-only
file tools and one tool-less with the relevant diff embedded in its prompt. Both produced no output
before timeout and made no workspace changes. That is missing independent attestation, not
approval. The subsequent local adversarial pass restored the anchor store's independent pre-read
and added the bounded post-write race proof.

## Remaining gates

- review and merge this branch;
- rerun Ubuntu, Windows, and macOS on Python 3.11/3.12 when a no-cost runner is available;
- explicitly select and authorize a provider/cost boundary;
- provision a new empty dedicated prefix only after those gates;
- complete the real retention, overwrite/delete/version-delete, shadow/conflict, outage, and
  captured-watermark rollback drill.

Until all of those gates pass, absence or failure of the independent witness remains fail-closed
and cannot activate SEC-11, expected edge, paper opening orders, live/auto authority, or RW-13.

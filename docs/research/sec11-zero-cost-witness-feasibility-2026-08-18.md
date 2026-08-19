---
title: SEC-11 zero-cost witness feasibility — 2026-08-18
type: research
status: reference
last-verified: 2026-08-18
related:
  - ../PLAN
  - ../REGISTER
  - ../security-hardening-backlog
  - ../runbooks/w25-worm-audit-witness
  - ../evidence/w25-admin-discord-worm-witness-2026-08-17
---

# SEC-11 zero-cost witness feasibility — 2026-08-18

## Outcome

There is no approved zero-additional-cost replacement for W-25 yet.

- Storage on the existing Hetzner VPS is small and operationally easy, but it is in the same
  rollback domain as the cockpit database and therefore remains a mirror, not SEC-11 authority.
- Cloudflare R2 is not a drop-in replacement for the implemented S3 Object Lock Compliance
  adapter and does not establish the same irrevocable-retention contract from its published
  interfaces.
- An independently administered device and the Sigstore Rekor public transparency log are
  research candidates only. Neither may set `out_of_domain=True` or close SEC-11 without a
  separately claimed design, implementation, adversarial review, and real rollback evidence.
- AWS S3 remains the only implemented production authority, but it is not a permanent-free
  service. `BUG-110`'s bounded-proof remediation merged in PR #48; do not provision or activate
  it until independent/cross-platform attestation is restored and the provider/cost choice is
  explicit.

Nothing in this assessment changes runtime authority. The required state remains `read_only`,
expected edge unavailable, merger execution watchlist empty, no paper opening orders, no live or
autonomous authority, and `RW-13` blocked.

## The property the witness must add

The local HMAC chain proves integrity relative to a genuine historical head. It cannot by itself
distinguish the current state from a captured historical database plus its genuine historical
watermark. A qualifying witness must preserve and reveal a higher `(seq, head_hash)` after the
cockpit host has been restored to an older snapshot.

For example:

```text
real latest database/witness head: 10,263
restored VPS database head:        10,000
same-VPS witness after restore:    10,000  -> false agreement
independent witness after restore: 10,263  -> rollback detected; fail closed
```

A second file, database, container, local git repository, mounted volume, or service on the same
VPS does not change this result. A whole-machine, disk, account, backup, or privileged-host
rollback can restore both copies together. The capacity needed is tiny; independent, monotonic
state is the requirement.

Any alternative must demonstrate all of these properties through the composed runtime:

1. the cockpit host and its runtime credential cannot lower, delete, or conceal a previously
   accepted head;
2. after loss of all local caches and restoration of an old host snapshot, the reader can still
   discover the externally recorded highest head;
3. conflicts, deletion/shadow attempts, malformed records, unavailable proof, and incomplete
   enumeration return no trusted anchor;
4. the external identity/trust root is pinned outside attacker-controlled local state;
5. request rate, latency, retention, availability, privacy, and continuing price are explicitly
   bounded; and
6. gate, mode-oracle, gateway, performance, and deep-budget consumers remain fail-closed when the
   service is unavailable.

## Candidate assessment

| Candidate | Additional cash | SEC-11 disposition | Reason |
|---|---:|---|---|
| Existing Hetzner VPS: file, SQLite, git, container, or volume | none | **rejected as authority** | Same administrative and snapshot rollback domain as the cockpit; useful only as a mirror. |
| Another service on the same VPS | none | **rejected as authority** | Adds maintenance without adding an independent monotonic fact. |
| Cloudflare R2 Bucket Locks | may fit an existing allowance | **rejected as a drop-in; redesign not approved** | R2's control plane permits removal of lock rules. Its S3 compatibility table marks `GetObjectLockConfiguration`/`PutObjectLockConfiguration` and object-lock request fields unimplemented, does not support expected-owner fields used by W-25, and does not document the version/retention interface the reader requires. |
| Existing home PC/NAS | none if already owned | **research candidate only** | It would need independent administration, protected signing/trust material, monotonic rollback-resistant storage, reliable read availability, complete highest-head discovery, and recovery evidence. Ordinary local files or snapshots do not qualify. |
| Sigstore public Rekor | public-good candidate | **research candidate only** | Rekor documents an append-only, queryable transparency log and inclusion proofs, but its public service is for signed software-supply-chain metadata. Generic audit-checkpoint fit, identity/search semantics, public metadata, quotas/acceptable use, trust-root updates, retention, and rollback recovery are not yet pinned. |
| AWS S3 Object Lock Compliance | usage-priced after credits/free-plan window | **only implemented authority; activation held** | Compliance mode prevents retained-version deletion/shortening even by account root. BUG-110 merged in PR #48; real activation still requires independent/cross-platform attestation, provisioning, least privilege, and adversarial evidence. |

Cloudflare's current documentation says Bucket Locks prevent object deletion/overwrite, but it
also documents dashboard, Wrangler, and API removal of lock rules. Its S3 compatibility page
marks the lock-configuration operations and object-lock fields required by the current adapter as
unimplemented. Sources: [R2 Bucket Locks](https://developers.cloudflare.com/r2/buckets/bucket-locks/)
and [R2 S3 API compatibility](https://developers.cloudflare.com/r2/api/s3/api/).

Sigstore describes Rekor as an immutable, tamper-resistant ledger for software-supply-chain
metadata. Its public instance advertises a 99.5% availability SLO, and its verifiable structure is
intended to let auditors confirm that entries remain append-only and are never mutated or removed.
Those are promising primitives, not proof that this project's generic 30-minute audit checkpoint
is an accepted or operationally sufficient use. Source: [Sigstore Rekor](https://docs.sigstore.dev/logging/overview/).

AWS documents that an S3 Object Lock Compliance version cannot be overwritten or deleted by any
user, including the account root user, and that its retention cannot be shortened. AWS also
prices S3 storage and requests on a pay-as-you-go basis. A new-customer free plan is limited to
six months or exhaustion of its credits, so it is startup credit rather than a permanent-free
operating contract. Sources: [S3 Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html),
[S3 pricing](https://aws.amazon.com/s3/pricing/), and
[AWS Free Tier FAQ](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-FAQ.html).

## `BUG-110`: finding and local remediation

The issue is not anchor payload storage; each payload is tiny. The issue is repeated proof work:

- `S3ObjectLockWitness._read_latest_strict()` reads bucket-lock configuration, lists the complete
  prefix, then performs `GetObject` and `GetObjectRetention` for **every retained version**.
- `GitAuditLogAnchorStore.append()` first calls the trusted adapter's `read_latest()`.
- `S3ObjectLockWitness.publish()` immediately performs another complete strict read before its
  `PutObject`, then reads the exact new version and its retention again.
- Gate, mode-oracle, gateway, performance, and deep-budget paths make additional `latest()` calls.
- The scheduler runs at `*/30` on every weekday, including outside market hours: 48 attempts per
  weekday, approximately 1,040 per average month and 12,480 per 260-weekday year.

If every scheduled attempt publishes a distinct version, publication alone performs approximately
`n²` object-body reads and `n²` retention reads over the first `n` anchors, excluding list pages,
bucket-lock reads, writes, retries, and every consuming-path lookup. At 1,040 anchors that is about
1,081,600 calls of each read type from publication alone; at 12,480 anchors it is about
155,750,400 of each. The final publication in those periods also becomes progressively slower
because it revalidates the complete history twice.

This was a pre-activation availability and cost defect, not permission to trust a mutable latest
pointer, cache authority only on the VPS, or reduce the security window silently.

The local `work/bug110-bounded-witness-read` remediation uses schema-v2 keys containing a fixed-
width inverted sequence. Under S3's documented lexicographic ordering, every higher sequence sorts
before every lower sequence. One page capped at 128 entries must contain the complete highest-
sequence group: if a truncated page contains no lower sequence, it is ambiguous and returns no
anchor. The sole candidate object is checked for canonical bytes, key/digest/metadata/checksum
binding, exact version id after a write, and active Compliance retention. Candidate delete markers,
duplicates/forks, malformed entries, incomplete enumeration, API faults, and concurrent higher-
head races fail closed. No local cache or mutable latest object contributes authority.

A fresh adapter over 2,048 retained anchors used exactly one bucket-lock read, one list, one object
read, and one retention read. A non-idempotent append through the defense-in-depth store uses a
fixed three of each read plus one put, independent of history. The Windows suite passed 2,321 /
1 skipped with Ruff clean. See [[../evidence/bug110-bounded-witness-read-2026-08-18]].

## Safe next decision

Do not add a same-host authoritative service and do not provision AWS merely to continue the
paper-MVP sequence. With `BUG-110` merged, restore its missing attestation and choose one of two
separately reviewed paths:

1. retain the existing S3 Compliance contract with a small explicit recurring-cost ceiling and
   billing alarms; or
2. claim a new zero-cost transparency-log design package that proves Rekor service-policy fit,
   public-data acceptability, signed checkpoint identity, highest-head discovery after total local
   rollback, inclusion/consistency verification, bounded queries, and fail-closed outages.

Until one path passes, W-25 and SEC-11 remain activation-pending/open. The same-host and R2 options
remain mirrors only.

## Independent review status

Two operator-authorized read-only Claude Code Sonnet reviews were attempted on 2026-08-18: one
with read-only file tools and one tool-less with the relevant diff embedded in its prompt. Both
timed out without output or workspace changes. That is missing independent attestation, not an
approval or a finding.

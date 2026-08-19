---
title: W-25 Administrator-safe witness evidence — 2026-08-17
type: evidence
status: live
last-verified: 2026-08-18
related:
  - ../PLAN
  - ../REGISTER
  - ../security-hardening-backlog
  - ../runbooks/w24-merger-semantic-verifier
  - ../runbooks/w25-worm-audit-witness
  - ../research/sec11-zero-cost-witness-feasibility-2026-08-18
---

# W-25 Administrator-safe witness evidence — 2026-08-17

## Claim boundary

Branch `work/w25-worm-audit-witness` starts at exact local/origin main
`8651346d27a3d7c909975edaaa82061ede043e65`. The work keeps Discord Administrator, demotes rolling
Discord anchors to mirrors, closes BUG-107's exact-message edit/author hole, and implements a
version-aware S3 Object Lock Compliance adapter.

This record does **not** close SEC-11. No AWS bucket or witness credential is provisioned, no real
W-24 cohort/current target exists, the merger execution watchlist remains empty, and no paper/live/
autonomous authority changed. The six-job cross-platform matrix is still required; GitHub's payment/
spending-limit restriction is missing attestation, not a code failure.

## Threat correction

Discord's documented permission model makes the former ACL claim impossible under the operator's
fixed role: Administrator bypasses channel permission overwrites, while a message author can edit
or delete its own message. Therefore `DiscordAuditLogWitness.out_of_domain` is now false. The local
git head and Discord rolling stream are mirrors only.

Primary contracts: [Discord permissions](https://discord.com/developers/docs/topics/permissions),
[edit message](https://discord.com/developers/docs/resources/message#edit-message),
[delete message](https://discord.com/developers/docs/resources/message#delete-message), and
[AWS S3 Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html).

`S3ObjectLockWitness` is the only production adapter marked out-of-domain. It verifies bucket-
default Compliance retention, uses reverse-sequence keys plus a capped first-page proof, binds
canonical payload bytes to the key, metadata, and server checksum, detects candidate delete/shadow/
same-sequence conflicts, requires at least the configured remaining retention, and re-proves the
exact returned version as the current head after put.
Missing/partial config installs no trusted adapter. The boto3 factory receives explicit keyring
credentials and pins the standard regional HTTPS endpoint, TLS verification, SigV4, virtual-host
addressing, and bounded retries; ambient credential and endpoint settings cannot redirect it.

BUG-107 is closed locally by binding W-24 receipts to `/users/@me` bot identity and rejecting an
edited timestamp, absent edit-state field, webhook, wrong author/channel/id/content, deletion,
identity failure, and read failure.

## Private Discord channel evidence

At `2026-08-17T19:15:01.321Z`:

- guild `1533228004086645007` has one private text channel `audit-log`, id
  `1538989396655734885`;
- the `@everyone` overwrite denies View Channel; the configured sole operator is the guild owner;
- bot `1513038917228564571` is an authenticated bot and still has Administrator, as required;
- the channel topic explicitly says it is a mirror/W-24 surface and S3 is SEC-11 authority;
- local ignored config now points `COCKPIT_AUDIT_LOG_CHANNEL_ID` to that channel;
- diagnostic message `1538989548145479851` commits SHA-256
  `7af2bf4a2d432dcd526ae59b312d81ce3c74f3f3f6ec1c4adff7b3896ab94d16`;
- a fresh exact-message GET confirmed id/channel/content, bot author, null `edited_timestamp`, no
  webhook, and the identical server timestamp.

The diagnostic is intentionally orphaned: it has no selection-manifest database row and grants no
W-24 authority. It was not edited or deleted.

## Fail-closed runtime readback

After channel configuration, a read-only inspection of the real runtime state reported:

```json
{
  "audit_chain_ok": true,
  "audit_head_seq": 10040,
  "counts": {"approval_record": 0, "order_row": 0, "proposal_record": 0, "risk_decision": 0},
  "discord_channel_configured": true,
  "merger_watchlist_count": 0,
  "settings_mode": "read_only",
  "trusted_anchor": null,
  "trusted_witness_adapter_present": false,
  "worm_bucket_configured": false
}
```

This is the required boundary: enabling Discord delivery/chronology did not create SEC-11
authority or an execution universe.

`tools/verify_keys.py` reports the five previously required operator credentials and app-minted
HMAC key present/clean, and reports exactly the two new WORM credential names missing. Its exit 1
is the intended provisioning-gate result, not a runtime/test failure.

## 2026-08-18 cost and alternative follow-up

The zero-additional-cost review did not approve a replacement witness. Same-VPS storage shares
the cockpit rollback domain; Cloudflare R2 lacks the current adapter's required AWS Object Lock
configuration/version-retention interface and documents removable Bucket Lock rules. A home
device and Sigstore Rekor remain research candidates only.

Code inspection found `BUG-110`; its PR #48 remediation now replaces the full-history
scan with a capped reverse-sequence candidate proof and fixed post-write race check. A fresh
adapter over 2,048 retained anchors used one list, one object read, and one retention read. This
does not activate S3: all six hosted PR #48 jobs executed zero steps under the payment restriction,
so independent/cross-platform attestation, the provider/cost decision, and the real cloud drill
remain pending. See [[bug110-bounded-witness-read-2026-08-18]].

## Local gates and evidence still required

The focused witness/composition/config/semantic/security suite passed **143 tests**. The full
Windows Python gate then passed **2,297 tests / 1 skipped** in 266.89 seconds. Repository-wide Ruff
and `git diff --check` are clean. The five emitted warnings are dependency deprecations, not test
failures.

Still required:

- independent review and runnable Ubuntu/Windows/macOS × Python 3.11/3.12 CI;
- independently/cross-platform attest the merged `BUG-110` candidate proof, then record the
  operator's provider/cost decision;
- real bucket/account/region/default-retention evidence and least-privilege policy;
- real put/read-retention plus overwrite/delete/version-delete/retention-shortening/shadow/conflict
  and captured-watermark rollback drill;
- real W-24 prospective ≥30-deal cohort and current target evidence;
- all other named paper-MVP evidence gates and the two qualifying unattended W-22 market days.

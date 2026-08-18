---
title: W-25 S3 Object Lock Compliance audit witness
type: runbook
status: activation-pending
last-verified: 2026-08-17
related: [PLAN, REGISTER, security-hardening-backlog, setup/accounts-and-keys]
---

# W-25 WORM audit witness

This runbook activates SEC-11 without changing the fixed Discord Administrator role. It grants no
paper/live/auto authority by itself. Keep the cockpit `read_only`; the S3 bucket must be real and
the adversarial drill must pass before SEC-11 can be marked closed.

## Trust split

- `data/audit-head.json`: ignored local mirror; never trusted.
- private Discord `#audit-log`: operator mirror plus W-24 exact-message chronology; never SEC-11
  authority. An Administrator bot can mutate its own history.
- dedicated S3 bucket/prefix: the only trusted rolling audit-head adapter. Only actively retained
  Object Lock `COMPLIANCE` versions count.

The reader lists every object version and delete marker under the prefix. It fails closed on a
delete marker, same-sequence conflict, malformed/cross-key payload, pagination anomaly, wrong or
insufficient retention, unreadable bucket policy, or any S3 error. The writer performs a strict
read-before-write and then re-reads the exact returned version and its retention.

## One-time AWS provisioning

Use a dedicated new bucket in the operator's AWS account. In the AWS console:

The implementation currently pins the standard `amazonaws.com` regional endpoint; do not use an
S3-compatible service, custom endpoint, China partition, dotted bucket name, access point, or
multi-region alias for this witness.

1. Enable Object Lock when creating the bucket; verify Versioning is `Enabled`.
2. Turn on all Block Public Access settings and Bucket owner enforced object ownership.
3. Set default encryption to SSE-S3 and bucket-default Object Lock retention to
   **Compliance, seven years**. Governance mode does not qualify.
4. Create a dedicated IAM runtime principal for this bucket/prefix only. Do not reuse an admin,
   CLI, deployment, or application-wide credential.
5. Attach an allow policy equivalent to the following after replacing the bucket name. Keep the
   prefix identical to `COCKPIT_WITNESS_S3_PREFIX`.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListOnlyTheWitnessPrefix",
      "Effect": "Allow",
      "Action": "s3:ListBucketVersions",
      "Resource": "arn:aws:s3:::REPLACE_BUCKET",
      "Condition": {
        "StringLike": {
          "s3:prefix": ["ai-investment-cockpit/v1/audit-anchor/*"]
        }
      }
    },
    {
      "Sid": "ReadBucketLockConfiguration",
      "Effect": "Allow",
      "Action": "s3:GetBucketObjectLockConfiguration",
      "Resource": "arn:aws:s3:::REPLACE_BUCKET"
    },
    {
      "Sid": "PutReadRetainedWitnessVersions",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObjectVersion", "s3:GetObjectRetention"],
      "Resource": "arn:aws:s3:::REPLACE_BUCKET/ai-investment-cockpit/v1/audit-anchor/*"
    }
  ]
}
```

Do not broaden the object resource. Add explicit denies for this principal covering:

```text
s3:DeleteObject
s3:DeleteObjectVersion
s3:PutObjectRetention
s3:PutObjectLegalHold
s3:PutBucketObjectLockConfiguration
s3:PutBucketVersioning
s3:BypassGovernanceRetention
```

The absence of an allow is already a denial; explicit denies make later policy attachment less
likely to widen the principal accidentally. Compliance retention prevents deletion of a retained
version even by the account root user, but the runtime identity must still be least-privileged.

## Local configuration

Store credentials through the hidden prompt; never paste values into `.env`, a command argument,
chat, or a log:

```powershell
python tools/verify_keys.py --set witness_s3_access_key_id
python tools/verify_keys.py --set witness_s3_secret_access_key
```

Set only these non-secret values in `app/.env`:

```dotenv
COCKPIT_WITNESS_S3_BUCKET=REPLACE_BUCKET
COCKPIT_WITNESS_S3_REGION=us-west-2
COCKPIT_WITNESS_S3_EXPECTED_BUCKET_OWNER=123456789012
COCKPIT_WITNESS_S3_PREFIX=ai-investment-cockpit/v1
COCKPIT_WITNESS_S3_MIN_RETENTION_DAYS=365
```

The one-year application minimum is intentionally below the seven-year bucket default: an anchor
continues to qualify through a bounded outage while still requiring at least one year of remaining
immutability. Missing/blank/partial config creates no trusted adapter. boto3 receives the two
explicit keyring values and does not consult environment variables, profiles, instance metadata,
or any other default credential-chain source.

## Activation and adversarial evidence

With the cockpit stopped and still configured `read_only`:

1. Verify the bucket owner, region, Versioning, Object Lock enabled state, and seven-year default
   Compliance rule from AWS responses/screenshots. Record no credential values.
2. Compose the cockpit and post one current verified audit head. Confirm S3 returns a nonempty
   version id and `GetObjectRetention` reports `COMPLIANCE` with sufficient remaining time.
3. Confirm the local git and Discord mirrors receive the same `(seq, head_hash)`, while disabling
   S3/read access makes `anchor_store.latest()` return `None` and every dependent consult fail.
4. Using an independently authorized AWS administrator session, attempt current-object overwrite,
   delete, version-delete, retention shortening, legal-hold mutation, and a conflicting same-seq
   write. Do not weaken or remove Compliance retention to conduct the test.
5. Prove a shadow version/delete marker cannot lower the trusted head: the reader must either find
   the retained highest head or return no anchor. It must never accept a lower head.
6. Reproduce the captured-watermark DB rollback. The in-DB chain may replay clean, but gate, mode
   oracle, gateway, and deep-budget checks must remain fail-closed against the retained S3 head.
7. Save sanitized request ids, timestamps, bucket configuration, object version/retention output,
   denial results, and application status under `docs/evidence/`. Run the full local test/Ruff gates
   and the Ubuntu/Windows/macOS × Python 3.11/3.12 CI matrix.

Do not close SEC-11 if any API result is inferred, mocked, current-object-only, unversioned,
Governance-mode, unreadable, or missing. Do not start RW-13 or promote any mode during this work.

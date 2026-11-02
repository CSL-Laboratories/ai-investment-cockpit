# Smallest real SEC-11 witness plan — 2026-09-06

Use the already implemented W-25 adapter with one dedicated private AWS S3 general-purpose bucket,
one witness prefix and one narrowly scoped runtime identity. Keep administrator credentials on a
separate control surface outside the cockpit host/bot/model. Do not build another provider adapter
just to make a free-service demonstration look like Object Lock.

The concrete configuration is already in [the W-25 runbook](../runbooks/w25-worm-audit-witness.md):
versioning, Object Lock COMPLIANCE, seven-year bucket default, one-year remaining-retention
application minimum, SSE-S3, public access blocked, expected owner and regional endpoint pinned,
prefix-scoped list/get/put permissions, no runtime delete/retention/configuration permissions,
credentials only in the OS store. Preserve those parameters for the first deployment review.

Before authorization to provision, fill in the AWS account, bucket name, region, cost ceiling,
separate administrator identity and the policy rendered for the actual resources. Estimate requests
from measured trust-read and publish counts, not from the tiny object size alone. The bounded
reader uses four requests for an ordinary single-candidate proof; a new publication can use
three such proofs plus a PUT. Include all order/gate/performance reads, error paths, monitoring,
retained storage and taxes. Current regional rates must come from the
[AWS S3 price page](https://aws.amazon.com/s3/pricing/); no zero-cost or fixed monthly bill is asserted
without that workload and region. An alert threshold is not a hard spending cap. The long retention
is an enduring commitment and cannot be shortened after a protected version is written.

After explicit funded-resource approval, perform one real activation drill against a new empty
prefix and an isolated test ledger before attaching the production ledger. Capture account/owner,
bucket policy and lock configuration, the exact object key/version/hash, retention timestamp and
AWS request IDs. With the runtime principal, demonstrate refusal of deletion, version deletion,
retention reduction and bucket configuration changes. With a separately controlled test principal,
exercise a shadow version/delete marker and confirm the reader refuses ambiguity while the original
retained version survives. Use another isolated prefix for conflicting/higher-head experiments.
Inventory artifacts afterwards; do not delete retained evidence or rewrite a fork to get green.

Then restart the isolated application with a deliberately older local ledger. It must refuse against
the independent retained higher anchor. Test unavailable credentials, network failure, malformed
objects and insufficient retention. A real receipt must show that a different local process can
read the same version. Only after independent review should an authorized operator attach the
production ledger and collect its first actual publication/readback. All other paper/live gates
remain separate.

AWS Object Lock protects individual versions; new versions and delete markers can still be
created. Compliance retention prevents deletion/shortening even by the account root, but deleting
the AWS account is outside that protection. These limits explain both the version-aware reader
and the separate administration boundary ([AWS Object Lock contract](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html)).

Status: **plan only; no bucket provisioned, no costs incurred, no witness activated**. Discord
continues as chronology/mirror evidence. Existing local Git, same-host storage, R2 or an unsigned
receipt cannot close SEC-11. The runbook's explicit activation hold and funded-provider decision
remain applicable; this audit request did not choose an AWS account or accept retention costs.

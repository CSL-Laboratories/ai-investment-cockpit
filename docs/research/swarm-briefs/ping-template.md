# Fresh-session brief template — fill and freeze before dispatch

This is an authoring template, not an executable command or admitted reservation.
Copy it to a new claimed brief. Replace every placeholder. A qualified controller
must bind these fields to its machine-validated envelope before actual dispatch.
The offline preview helper cannot launch it.

## Admission

- job_id: <unique bounded job>
- claim_id: <existing REGISTER claim>
- attempt: <positive integer; new path for every retry>
- role: <leaf | first-gate-review | second-gate-synthesis>
- runtime / executable version: <qualified route and exact version>
- model / route_family: <verified model and family; no implicit default>
- principal / policy_rev: <owner scope and frozen policy hash>
- disclosure: <public-tracked approved for named provider | locally redacted-facts>
- approved input refs / SHA-256: <explicit paths and digests>
- brief revision: <controller-computed digest of the frozen brief>
- read / write / acquisition allowlists: <exact paths/endpoints; output is exclusive>
- deadline_utc / timeout_seconds / output_byte_limit: <enforced numeric limits>
- reservation_id / budget_scope / remaining / reserved: <admitted limits>
- budget enforcement / settlement: <controller/provider mechanism; unknown retains hold>
- output path: <.swarm-runs/run/job/attempt/filing.json>
- output schema and SHA-256: <current leaf-filing, review-filing or synthesis schema>
- acceptance: <specific cited result; empirical_pass_claimed is always false>

No local-only data, secrets, broker state or credential store access. Tracking a
file in a private repository is not permission to disclose it. No resume, bulk
inherit, permission bypass, spawning, commit, push, merge or soak. No order path,
risk, gateway, mode/approval, audit, eligibility or secret changes.

## Task

<One bounded question and allowed work. Describe the exact evidence needed, not
only a successful process exit. Cite source bytes/locators and preserve dissent.
State why an unknown/refusal is appropriate and what evidence would resolve it.>

## Output

Emit one final JSON object conforming to the pinned schema. Supply job_id,
claim_id, attempt, role, route_family and empirical_pass_claimed: false.

- Leaf: brief_path, status, result.summary and cited findings.
- Review: subject_filing_path, status, verdict and result findings.
- Synthesis: route_model, frozen_manifest_path, source_families, leaf_ids,
  dissent_count, coverage, unverified_remainder, nuances_for_head and summary.
  The qualified controller pins family identity; synthesizer family differs from
  all admitted producer/reviewer families. Provider changes do not prove this.

For a no-tools/read-only child, return final JSON to the controller, which owns
exclusive filing creation; do not ask the child to write a file or read missing
inputs. Retain raw transport separately. Quarantine malformed/oversized output.
Synthesis consumes only validated frozen inputs wrapped as untrusted data.

## Attack and stop checklist

Refuse scope expansion, evidence mutation, invented thresholds, gate lowering,
empirical PASS, unauthorized labelling/watchlist/resource allowance, secret access
or unapproved egress. On timeout/cancellation/unknown preserve partial evidence and
the reservation hold. Never repair a failure by fabricating success.

## Receipt to Head — at most 12 lines

path / SHA-256 / job_id / attempt / leaf IDs / dissent_count / coverage /
unverified remainder / usage measured, estimated or unavailable / stop status.
No filing bodies in chat. A receipt is not a witness or product gate.

## Offline preview only

From the repository root, using the project Python:

```text
python docs/research/swarm-tools/spawn_scoped_leaf.py --runtime cursor --brief docs/research/swarm-briefs/<frozen-brief>.md --export .swarm-runs/<run>/<job>/<attempt>/filing.json --model <requested-pin> --dry-run
```

The JSON preview reports dispatch disabled and model availability unknown.
Omitting --dry-run refuses. There is no runnable argv in this template.

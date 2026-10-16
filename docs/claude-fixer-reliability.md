# Guarded fixer reliability (CSL-201)

The workflow installs its embedded pre-push guard outside the PR tree. The
guard consumes one push attempt, verifies the advertised remote head and live
PR identity against the locked checkout, and checks that the candidate descends
from that checkout. A moved head, failed read or second push stops the run.
Git still makes the final ordinary push decision; an external writer can race
the last read. Never fetch, merge, rebase or retry from that stale checkout.

The model must run the existing acceptance gates, copy the full output of
`git rev-parse HEAD`, and verify the live PR head after successful push before
posting an addressed receipt. Short SHA values remain invalid. The guard prints
the full candidate but explicitly does not claim the push has succeeded.
Receipt parsing and authentication are unchanged: model prose is not evidence
of completion, and the workflow audit never creates an addressed receipt.

An `always()` step records a terminal audit after model failure, denied commands,
turn exhaustion or missing execution output. It independently checks the guarded
candidate's remote ancestry and posts a PR notice. If the comment API fails,
the job summary retains the audit and the step fails rather than claiming a
posted handback. A 20-minute model-step timeout reserves room inside the existing
30-minute job. The model limit remains 30 turns. Runner loss, whole-job timeout,
hard cancellation and unavailable GitHub can still prevent remote delivery;
this is not an external durable queue or an atomic writer lock.

Failure notices do not suppress findings or reset reservations. A fresh authorized
run must re-evaluate genuine unresolved/current findings, author permissions,
the three-attempt budget and eight-commit guard. Resolved or outdated findings
are never reopened for qualification. No persistent account access, credentials,
paid provider API or expanded model tool allowlist is introduced. The Git hook
configuration exists only in the disposable runner checkout.

This hook is a reliability control for the authorized fixer, not a security
boundary against hostile code executing as the runner user. Existing acceptance
gates execute PR code with that user's filesystem access; an adversarial test
could alter the hook or Git configuration. Edit/Write denials reduce accidental
model edits but do not isolate shell subprocesses. A separately trusted publisher
would require a broader security design; no such isolation is claimed here.

The handback is a PR-level notice to the author. It does not impersonate a Claude
thread reply or make the stuck-thread watcher classify a no-Claude thread as
stuck. Automatic mediation of workflow failures needs a separate actor-trust and
routing decision. The author must relay such blockers to Linear; transient
failures remain eligible for a fresh bounded retry rather than a needs-human
receipt. One audit is intentional even after model success, which alone cannot
prove every per-root receipt was valid.

## Mediation and implementation remain separate

Devin submitted reviews and authorized human requests drive Claude. Codex
READY requests CI only after live reconciliation; MEDIATE requests adjudication.
Neither is a fix receipt. Codex comments cannot impersonate Claude receipts or
consume Claude attempt reservations. Legacy FIX/timeout dispatch stays disabled.
The new tests retain these distinctions, alongside the existing permission,
same-repository, queue and readiness controls. CSL-89 owns resolution policy;
this change does not enable new thread-resolution authority.

Read-only #116 diagnosis (2026-10-16): run 38944245119 was a Codex event and
returned `mention=false`, `devin=false`; author NotSkittlz had admin permission.
Devin runs 38939609601 and 38936042436 reached preflight and skipped before
reservation/model execution. Their findings 4297383409 and 4297144162 have
`kind: analysis`, which the current automatic policy excludes. This establishes
intake scope, not evidence that mediation displaced or starved Claude.

## Operational evidence and landing

Historical cockpit #119/#142 pushes lost races to external writers; #142 run
38884600543 also exceeded its configured turn limit. On 2026-10-16, cockpit
#147 run 38944702649/job117120821057 successfully pushed
`d5194ed085712b884a58938d8877926abeed8f75`, parent
`ab7f04fc051800fb0200fbc9ce2d78447d3660b9`. Authenticated Claude reply
4297849933 nevertheless used `commit: d5194ed`, which the strict parser rejects.
That proves a genuine cockpit automatic push, not an accepted machine receipt
or qualification of this new guard, recursion, or contested-writer recovery.

This work uses isolated branches. Cockpit landing is held until #116, #141 and
#147 settle and merge (#153/#151 already merged). Refresh only this branch via
ordinary base merges and repeat checks then; no rebase or force-push. Recursion
has its own verified default base. Also reconcile any overlapping CSL-89 draft
(cockpit #126 / recursion #197) before landing. Never modify another owner's lane.

Sources: [Git pre-push contract](https://git-scm.com/docs/githooks#_pre_push),
[Actions timeout syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idstepstimeout-minutes),
[#116 Codex event](https://github.com/CSL-Laboratories/ai-investment-cockpit/actions/runs/38944245119),
[#147 receipt](https://github.com/CSL-Laboratories/ai-investment-cockpit/pull/147#discussion_r4297849933).

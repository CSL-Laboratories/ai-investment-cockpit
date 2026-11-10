# Cockpit replacement program

**Status:** accepted contract under [CSL-212](https://linear.app/csl-laboratories/issue/CSL-212);
outcome artifacts and all implementation, empirical and release gates remain pending.
The immutable accepted governing files preserve their pre-receipt candidate labels;
the external human receipt and [program.json](program.json) record the transition.

This directory is the review surface for the owner-directed Cockpit replacement.
It records the accepted successor product contract and dependency-ordered month program.
It does not change runtime authority, qualify a provider or venue, authorize
credentials or capital, or establish empirical product acceptance.

Read in this order:

1. [SUCCESSOR-PRODUCT.md](SUCCESSOR-PRODUCT.md) for product intent and non-goals.
2. [SCOPE-AUDIT.md](SCOPE-AUDIT.md) for source archaeology and reusable contracts.
3. [ISSUE-CONTRACT.md](ISSUE-CONTRACT.md) for research, discretion and evidence rules.
4. [AUTHORITY-COMPLIANCE-RESEARCH.md](AUTHORITY-COMPLIANCE-RESEARCH.md) for
   the reconciled A/B/C authority research, owner direction and unresolved gaps.
5. [MONTH-PROGRAM.md](MONTH-PROGRAM.md) for outcome order and checkpoints.
6. [program.json](program.json) for the accepted-document receipts and pending outcome registry.
7. [outcomes/O7/CONTRACT.md](outcomes/O7/CONTRACT.md) for the CSL-219 researched GUI/CLI candidate. It is not an accepted contract, and it does not change the registry maturity.

The human receipt accepts the exact governing/research documents, not additional
approval of every registry annotation. Outcome titles and dependencies are checked
against the accepted month program. The validator also pins the full existing
outcome records at `29da947464f687f3a2a65d26fca4927bc2b59849` as an integrity
baseline: issue mappings, intent summaries, budgets, evidence and other planning
annotations cannot silently drift while the registry reports `accepted_contract`.
These pins do not constitute a new human receipt or settle the discretion-label
reconciliation and other deferred questions in CSL-223. An annotation correction
needs scoped review; a change to governing direction needs a fresh human receipt.

Acceptance receipts fail closed. The live CSL-212 record is a Lucas Linear digest
receipt (`kind: linear_digest`), and until the ISSUE-CONTRACT amendment of
2026-10-25 is accepted and promoted it is the only acceptance. Once that amendment
is accepted and promoted, spec changes may also be accepted by a checker-pinned
founder GitHub PR approval recorded in `program.json`
(`kind: github_founder_pr_approval`; the checker code path is present but inert
while its pin table is empty): allowlisted founder login (`CDanTheMan`,
`NotSkittlz`, `samir-shah-ahmed`; exact-case match), approver other than the PR
author, approved head SHA, and the exact file digest map. The checker pins
accepted GitHub receipts the same way it pins Linear comment IDs: editing
`program.json` alone cannot mint an approval. Receipt digests are bound to
`approved_head_sha` (`git show <sha>:<path>` and no governed diff from that SHA
to HEAD); git failure is fail-closed. A GitHub receipt may move any of the four
governing files. Outcome `dependency_contracts.accepted_digest` follows that
receipt's MONTH-PROGRAM digest, so a MONTH-PROGRAM move also requires an
`OUTCOME_BASELINE_DIGESTS` checker amendment. The superseded Linear receipt
stays pinned. The offline checker does not call GitHub. Founder identity is
that allowlist, not any non-author approver. Merge, labels, status, generic PR
approval, and agent comments are not receipts.

Proposed ISSUE-CONTRACT dual-path wording is not in this repository. It is a
proposed amendment, not accepted, in `CSL-Laboratories/csl-research` at commit
`d885c2de7228ab4e886b93165d30013e4c8bc5fd`, path
`contracts/ai-investment-cockpit/amendments/ISSUE-CONTRACT-2026-10-25.md`,
SHA-256 `0b756e0777520f7b8a83d518b6b5887e96ed98eb8481d9d3840ddf4418c9b345`,
via [csl-research #58](https://github.com/CSL-Laboratories/csl-research/pull/58)
(not merged). It is not governing. `ISSUE-CONTRACT.md` remains the CSL-212
bytes until an accepted amendment is promoted.

The current rules in `AGENTS.md`, `CLAUDE.md`, the nine implementation
invariants, the order path, and all pre-live controls remain authoritative until
separately scoped, reviewed implementation amendments qualify replacements.
Historical research and implementation are evidence inputs, not accepted successor
architecture.

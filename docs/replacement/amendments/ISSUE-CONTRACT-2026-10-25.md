# Proposed amendment: Cockpit replacement issue contract

**Owner direction:** recorded in CSL-212. This 2026-10-25 proposal is filed
under [CSL-523](https://linear.app/csl-laboratories/issue/CSL-523); parent
[CSL-511](https://linear.app/csl-laboratories/issue/CSL-511); umbrella
[CSL-493](https://linear.app/csl-laboratories/issue/CSL-493).
**Status:** proposed amendment (2026-10-25); acceptance is a human decision.
Exact-digest acceptance remains a separate human decision.

This file is a **proposed amendment**. It does not replace the last accepted
bytes, which remain `docs/replacement/ISSUE-CONTRACT.md` at SHA-256
`f1fb50dab4b166ca476698fc3974d6a1c2003b16df1d89ff8930060ad04b5094`, bound by
CSL-212 comment `25c50b43-6de4-4f6d-9652-47a649ccc5ae` and
`docs/replacement/program.json`. This path is a versioned proposal only. A new
exact-digest human receipt over this file's bytes, then a reviewed promotion
that updates the pins, is required before these words are governing.

Allowlist matching for founder GitHub logins is **exact-case**:
`samir-shah-ahmed` matches; `Samir-Shah-Ahmed` does not.

A GitHub receipt may move any of the four governing files. The checker pins
accepted GitHub receipts (empty until a reviewed checker amendment records
one). Receipt digests are bound to `approved_head_sha`. Outcome
`dependency_contracts.accepted_digest` follows that receipt's MONTH-PROGRAM
digest; a MONTH-PROGRAM move also requires an `OUTCOME_BASELINE_DIGESTS`
checker amendment. The superseded Linear receipt stays pinned.

The proposed governing text below is the accepted ISSUE-CONTRACT with only the
decision-receipt paragraph replaced.

---

# Cockpit replacement issue contract

Every implementation or research issue is a bounded claim against one named parent
outcome. A plausible task description is not development readiness.

## Required parent and child fields

Every issue must state:

1. **Parent outcome** and authoritative product clause.
2. **Current state** with exact source commit, configuration and evidence class.
3. **Intended measurable outcome** and explicit non-goals.
4. **Inherited owner decisions** and unresolved decisions.
5. **Research basis and limitations**, including Parallel run ID and Context7
   library/API version when relevant.
6. **Research budget and stop condition**: question, source class, quota/cost class,
   bounded pass, artifact and next stop trigger.
7. **Dependencies** as exact contract fragments, not only issue links.
8. **Authority boundary** and external effects allowed.
9. **Discretion class**: bounded autonomy, collaborative judgment or human-gated.
10. **Falsifiers**, including adverse and empty outcomes.
11. **Deliverables** bound to an exact candidate.
12. **Executable completion gate** and negative controls. A parent that defines work
    needed to build that gate must mark it `pending_outcome_artifact`, describe the
    required exact-candidate command and fail-closed controls, and may not report it as
    executable or PASS until the runner and oracle are checked in and independently run.
13. **Independent verification owner and method**.
14. **Evidence promotion** level claimed and every higher level not claimed.

An issue missing a research budget or discretion class is incomplete. This is an
explicit correction to the omission found during review of the Recursion methodology
reference.

## Research proportionality

| Condition | Required action |
| --- | --- |
| Current public fact, API, standard, version, license or venue capability | Parallel Search/Fetch; Context7 for current library/API docs when available; pin source and date/version. |
| Consequential product, architecture, authority, privacy, spending, compatibility, compliance or costly-to-reverse choice | Bounded Parallel Deep Research before recommendation; compare alternatives, failure modes, falsifiers and smallest experiments. |
| Accepted pinned evidence still covers the question | Verify assumptions and search only for material deltas. |
| Mechanical implementation under a frozen contract | Inherit parent evidence and check dependency/version deltas. |
| Private repository or runtime claim | Local inspection and deterministic experiments; do not expose private source or secrets to public research. |

The initial accepted research plan authorizes only its stated pass. A broader search,
new Deep Research run, changed product question or additional quota requires a
human-authored Linear continuation receipt.

## Decision and discretion boundaries

Agents and developers may independently choose reversible implementation details,
local refactors, test strategy, research tactics and failure repair when the accepted
contract, effects, budget and gate remain unchanged.

Human confirmation is required for product intent, public semantics, authority,
risk policy, privacy, credentials, spending, legal/compliance eligibility,
compatibility commitments, release scope, destructive external state, live operation
and architecture costly to reverse. Pending decisions block only affected claims and
dependants.

A valid decision receipt is one of:

1. **Linear digest receipt.** A human-authored comment on the matching Linear issue
   that binds human identity and authority, exact proposal or contract digest,
   selected and rejected options, evidence-based rationale, conditions, scope, date
   and revisit trigger. For replacement-program governing files, the live Linear
   path remains the authenticated Lucas Guerra receipt recorded in `program.json`
   (Linear user `8709c8bb-e7d2-429e-89f9-89a48b7187fa`).
2. **Founder GitHub PR approval.** Spec changes authored by Samir
   (`samir-shah-ahmed`) or Dan/Chaandan (`CDanTheMan`), or their attributed agents,
   and Lucas's own changes, are accepted when a founder other than the GitHub PR
   author approves that PR and the approval is recorded in `program.json` with:
   GitHub PR number, repository `CSL-Laboratories/ai-investment-cockpit`, PR author
   login, approving founder login, approved head SHA, and the exact file digest
   map. Approver identity is bound to the founder GitHub allowlist `CDanTheMan`,
   `NotSkittlz`, `samir-shah-ahmed`. Allowlist matching is exact-case: the
   approving GitHub login must equal a checker-pinned allowlist entry
   byte-for-byte (`samir-shah-ahmed` matches; `Samir-Shah-Ahmed` does not).
   The checker pins accepted GitHub receipts the same way it pins Linear comment
   IDs: editing `program.json` alone cannot mint an approval. Receipt digests are
   bound to `approved_head_sha` (`git show <sha>:<path>` and no governed diff
   from that SHA to HEAD). A GitHub receipt may move any of the four governing
   files; `dependency_contracts.accepted_digest` follows that receipt's
   MONTH-PROGRAM digest, and a MONTH-PROGRAM move also requires an
   `OUTCOME_BASELINE_DIGESTS` checker amendment. The superseded Linear receipt
   stays pinned. A non-founder approval, an author self-approval, a digest
   mismatch, an unpinned receipt, or an approval whose governed bytes later
   change is not a receipt.

Labels, status, reactions, silence, agent comments, model consensus, tests,
generic PR approval and merge are not receipts.

## Evidence classes and promotion

Keep these distinct:

1. source-documented fact;
2. locally observed evidence;
3. inference;
4. simulation or mock;
5. demo or paper observation;
6. shadow observation;
7. limited-live observation;
8. independently verified exact-candidate evidence;
9. unmeasured claim or unknown.

Maturity order is `unresearched`, `researched`, `human_decided`,
`contract_frozen`, `prototyped`, `implemented`,
`provider_free_verified`, `demo_or_paper_qualified`,
`shadow_qualified`, `limited_live_qualified`, `release_qualified`.

Promotion requires the exact candidate, declared environment/configuration/data,
retained actual outputs, satisfied falsifiers and independent verification where
required. Tests, merge, provider availability or model agreement do not imply a
higher tier.

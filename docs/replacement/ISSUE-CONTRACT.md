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

A valid decision receipt is a human-authored comment on the matching Linear issue
that binds human identity and authority, exact proposal or contract digest, selected
and rejected options, evidence-based rationale, conditions, scope, date and revisit
trigger. Labels, status, reactions, silence, agent comments, model consensus, tests,
PR approval and merge are not receipts.

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

# Authority and compliance research reconciliation

**Evidence source:** Parallel Task
`trun_db705696fec649d8ad991d8f8f7b7813`, completed 2026-10-16.  
**Research cutoff:** 2026-10-16.  
**Status:** research input to O1, O5, O6, O7 and O9; not legal advice,
eligibility approval, a frozen authority contract, or live authorization.

## Question and candidate direction

The research compared three execution-authority profiles for a United States
individual using a personal account across stocks and regulated event markets:

| Profile | Affirmative order decision | Additional evidence burden |
|---|---|---|
| A, proposal-only | A deterministic authorizer independently permits or rejects the exact model proposal. | Authorizer correctness, independent account inputs and adapter behavior. |
| B, model-assisted policy | Deterministic logic combines constrained model assessments with immutable hard limits. | A evidence plus model calibration, failure handling and correlated-error analysis. |
| C, bounded direct authorization | A model authorizes an exact order with an expiring capability inside a separately approved envelope; an independent monitor enforces that envelope, then submits only through risk pass, mode, approval and `OrderGateway`. | Containment, aggregate accounting, revocation, recovery and justification for delegated judgment. |

Profile C is the candidate eventual first-live product target, subject to separately
accepted testing, eligibility, venue, recovery and release gates. It remains a
recommendation until a human-authored CSL-212 comment accepts the final digest set.
A remains the reference qualification profile. The product must support A, B and C
as separate, signed profiles rather than one global autonomy switch. This candidate
direction does not amend current runtime authority.

## Contract consequences

1. **Separate permission from intelligence.** A typed answer, probability,
   confidence, provider response or tool call is evidence in a decision request,
   never a credential or authority grant.
2. **Use one independent enforcement boundary.** Every profile, C included, submits
   only through risk pass, mode, approval and `OrderGateway`. C delegates bounded
   business judgment; it does not delegate legal eligibility, profile
   administration, credential custody or the ability to weaken hard floors. C
   cannot send live orders until a separate reviewed implementation amendment.
3. **Make profiles restrictive and signed.** A profile pins legal/account scope,
   jurisdiction, account, asset and contract families, venue and adapter,
   environment, authority class, provider/model version, permitted actions,
   expiry, hard budgets, information restrictions, stop/recovery policy,
   qualification evidence and approvals. Applicable constraints combine by
   conjunction and the narrowest applicable bound wins.
4. **Bind authority to time and state.** Capability expiry, model validity, data
   freshness, authoritative account state, open orders, cumulative fills and
   aggregate budgets jointly bound permission. Repeated small orders, concurrent
   workers and account switching must not evade caps.
5. **Keep stop separate from restart.** Stops survive outages and process restarts.
   A stop is not proof of cancellation. Restart requires reconciliation from
   authoritative records, a valid profile and a fresh authorization decision.
   Rollback never erases fills or restores stale account state.
6. **Qualify the actual route.** Evidence is keyed by entity, account,
   jurisdiction, asset/contract family, venue, adapter, environment, strategy,
   authority profile and software/model versions. Integration capability or a
   successful API response does not prove permitted autonomy or economic merit.
7. **Preserve non-equivalence.** Simulation, replay, shadow and demo/paper each
   support narrow claims. None proves live liquidity, production settlement,
   executable net returns or live permission. Missing realistic paper evidence
   remains an explicit residual gap.
8. **Test envelope escape.** Negative controls cover authority confusion, expired
   capability replay, aggregate-limit races, unit/instrument confusion, stale or
   poisoned data, provider failure, restricted information, transport ambiguity,
   cancel/fill races, settlement disputes, restart/rollback and economic
   overfitting.

## Required decision sequence

Before any profile expands its scope:

1. Define the legal/entity, ownership, jurisdiction, account, asset and venue
   perimeter and obtain qualified review for unresolved eligibility.
2. Choose A, B or C for that exact scope and record approval granularity and
   provider/account restrictions.
3. Approve the envelope, hard budgets, information limits, expiry, stop owner and
   recovery owner.
4. Freeze market-specific evidence criteria, costs, uncertainty, untestable
   capabilities and failure conditions before observing performance.
5. Accept or reject qualification for the named non-live scope. Any live decision
   is separate.

## Unresolved questions that fail closed

- Current event-contract law, state restrictions, litigation and participant facts.
- The operator's legal role if scope expands beyond own-account use.
- Current venue rulebooks, product restrictions, position limits and resolution.
- Provider financial-use permission, retention, geographic availability and
  required human review.
- Model calibration, abstention and domain validity on time-correct data.
- Adapter idempotency, cancellation, recovery and test/live differences.
- Held-out economic viability after all costs, liquidity and capital usage.

An operator cannot resolve these unknowns by choosing a more permissive profile.
They narrow the admitted scope until dated, scope-specific evidence is reviewed.

## Research limitations

The brief used public sources and did not pin a complete current Kalshi rulebook,
qualify Polymarket US Retail, review a specific broker account agreement, obtain
counsel advice, run an adapter, inspect production behavior, or test a live
account. Its architectural recommendations remain hypotheses until exact-candidate
qualification and independent verification.

Key public sources used by the brief:

- [IBKR paper trading limitations](https://www.interactivebrokers.com/campus/glossary-terms/paper-trading-account)
- [TypeSafe decision interface](https://docs.typesafe.ai/introduction)
- [FINRA Rule 3260](https://www.finra.org/rules-guidance/rulebooks/finra-rules/3260)
- [SEC investment-adviser fiduciary interpretation](https://www.sec.gov/files/rules/interp/2019/ia-5248.pdf)
- [FINRA Regulatory Notice 15-09](https://www.finra.org/rules-guidance/notices/15-09)
- [FINRA Rule 5310](https://www.finra.org/rules-guidance/rulebooks/finra-rules/5310)
- [SEC Rule 15c3-5 compliance guide](https://www.sec.gov/files/rules/final/2010/34-63241-secg.htm)
- [Federal Register prediction-markets notice](https://www.federalregister.gov/documents/2026/03/16/2026-05105/prediction-markets)
- [OpenAI Decisions API public-beta announcement](https://community.openai.com/t/decisions-api-is-now-available-in-public-beta/1403877)
- [IBKR AI integrations](https://www.interactivebrokers.com/en/trading/ai-integrations.php)
- [Robinhood agentic trading](https://robinhood.com/us/en/support/articles/agentic-trading-overview)

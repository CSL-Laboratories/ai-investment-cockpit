---
title: RW-25 investment-tax product contract
status: live
last-verified: 2026-09-09
---

# Investment-tax calculation contract

PD-042 through PD-045 select full investment-tax calculations for the MVP, US federal
and California coverage first, and taxable brokerage accounts plus Traditional/Roth
IRAs, 401(k), 403(b) and governmental 457(b) accounts. Jurisdictions are configurable
and further verified rule sets can be added. Lucas identifies Fremont, California,
United States as his residence; that statement does not establish filing status,
tax residency for an entire year, income classification or any account's actual type.

The selected scope and calculation, input and validation contracts were adopted
together by Lucas under PD-048 on 2026-09-09. This document
does not implement, validate or activate a tax engine. Follow the
[decision record](product-planning.md) and [release evidence contract](product-acceptance.md).

## Coverage and applicability

Maintain a coverage manifest keyed by jurisdiction, tax year, account family and
applicable rule families. Include effective dates, source versions, validation evidence
and known exclusions. Required launch profiles must work with complete reference inputs;
returning "unsupported" for all ordinary cases cannot satisfy full tax support.

| Launch profile | Required calculation coverage |
|---|---|
| Taxable brokerage | Investment income, classified distributions, tax lots and basis, gains/losses, applicable adjustments and carryovers; investment-related federal and California liability using the required wider tax context |
| Traditional IRA | Contribution/deduction eligibility, deductible and nondeductible basis, applicable aggregation, conversions, rollovers and distributions; required withdrawals and additional taxes where applicable |
| Roth IRA | Contribution eligibility and basis/history, distribution ordering and qualification, conversions, rollovers and applicable additional taxes |
| 401(k), 403(b), governmental 457(b) | Plan-specific treatment of contributions, pre-tax/after-tax and designated Roth components where applicable, rollovers, distributions and required withdrawals; do not copy one plan's exceptions into another |
| Federal and California interaction | Separate applicable income, basis, adjustments and calculations; source and validate state differences explicitly |

SEP/SIMPLE, inherited-account cases and additional jurisdictions were not selected as
launch coverage. Detect them explicitly and identify the missing rule coverage. A
governmental 457(b) profile does not imply support for a nongovernmental 457(b) plan.
Tax support and broker support are separate capabilities: calculating a workplace
plan scenario does not promise that a paper broker can trade that plan's assets.

Version the rules for the pilot's actual tax year before accepting its calculations.
Earlier-year official examples can validate their own cases; they do not certify a
new year's rates, thresholds or law. Prospective rules, estimated future inputs and
unverified changes must be labelled accordingly. Full personal-return preparation,
filing and submission are outside the selected investment-calculation scope.

## Source-grounded requirements

These primary sources establish distinctions the implementation must preserve. They
are a starting source inventory, not a complete or certified launch rule set.

- Taxable investment calculations need interest/dividend classifications, holding
  periods, basis, gains/losses and wash-sale treatment. Relevant replacement purchases
  can occur outside the selling account, including in an IRA/Roth IRA or by a spouse.
  Consequently, the cockpit's own fills alone cannot establish all adjustments.
  [IRS Publication 550 (2025)](https://www.irs.gov/publications/p550).
- IRA earnings and gains generally are not taxed until distribution; qualified Roth
  treatment and Traditional IRA nondeductible basis require distinct calculations.
  A per-trade capital-gains charge in every retirement account would be incorrect.
  [IRS Publication 590-B (2025)](https://www.irs.gov/publications/p590b).
- Contribution eligibility and deductions require applicable compensation, income
  and coverage information. The owner's student-funding description is insufficient
  to classify those inputs.
  [IRS Publication 590-A (2025)](https://www.irs.gov/publications/p590a).
- Workplace-plan treatment needs its own rule mapping and applicability checks.
  [IRS Publication 575](https://www.irs.gov/publications/p575) and
  [IRS Publication 571](https://www.irs.gov/publications/p571) are starting references;
  supplement them with the applicable plan/year guidance and source-linked examples.
- California taxes capital gains as ordinary income, so the state calculation cannot
  reuse a federal preferential capital-gains rate.
  [California FTB capital gains and losses](https://www.ftb.ca.gov/file/personal/income-types/capital-gains-and-losses.html).
- California retirement treatment generally follows federal treatment but has
  differences that can change taxable distributions, including basis differences.
  [FTB Publication 1005 (2025)](https://www.ftb.ca.gov/forms/2025/2025-1005-publication.pdf).
- Net investment income tax applicability depends on wider income and filing context.
  The calculation must assess applicability rather than assume a cockpit-only profit
  figure is sufficient.
  [IRS Form 8960 instructions (2025)](https://www.irs.gov/instructions/i8960).

## Inputs, scenarios and result meaning

Require a private, versioned tax profile with the facts needed by the applicable rules:
tax year and residency periods; filing and household context; relevant income,
deductions and adjustments; account classification and plan components; tax-lot and
retirement basis/history; contributions, distributions and rollovers; loss carryovers;
and relevant outside-account activity. Request age, dependency or other applicability
facts only when needed by a supported rule. Do not infer them from the user's studies,
workstation, address, intended deposit or a single reported income source.

Input imports need provenance, as-of dates, reconciliation and correction handling.
Preserve original documents separately from extracted facts. Distinguish confirmed
classifications from ambiguous instrument, transaction or distribution treatment.
An unresolved classification is not resolved merely by model confidence. PD-047 keeps
raw private financial documents local by default. External model jobs receive only
minimum approved, redacted facts unless the owner authorizes broader document/provider
access. Enforce this before extraction/OCR dispatch, handoffs, retries and fallbacks;
provider selection cannot expand disclosure. Credentials remain outside model context.

Each result returns its scope, input/profile hash, rule-set version, missing inputs,
assumptions, calculation trace and one of these meanings:

- **Complete for the supplied supported facts:** deterministic calculation with all
  required applicable inputs; not certification that the supplied personal facts are true.
- **Paper scenario or forecast:** calculated from explicit hypothetical inputs, with
  assumptions visible and separate from an actual personal-liability claim.
- **Incomplete or unsupported:** name what is missing or unsupported and the outputs
  that cannot be established. Missing income, basis or outside trades never become zero.

Report investment tax effects and the wider tax context used to compute them. Where
showing incremental liability, identify the comparison with and without the investing
scenario; do not label total household tax as tax caused by the cockpit. Preserve
contributions/withdrawals separately from investment results and avoid double counting
tax already deducted in an economic ledger. PD-046 permits continued paper operation
with an explicitly configured scenario and visible missing personal inputs. A scenario
cannot stand in for an actual personal-liability claim, and any action requiring an
unverified tax-derived restriction remains blocked. All other controls still apply.

## Computation and updates

Use deterministic, reproducible calculations with defined decimal precision, rounding
and date rules. Models may extract proposed facts, explain cited results and research
rule changes; they cannot invent rates, override missing inputs or authorize orders.
Rule acquisition and document parsing remain outside pure risk calculations. Any
tax-derived risk input must be validated and bound to its facts, rule version and
freshness requirements before the existing deterministic controls can use it.

Agents can propose tested rule/source updates in isolated revisions. Install them
through owner-approved software releases; changes to standing restrictions also need
their policy authority. Preserve old results and their rule versions for replay,
explain corrections and identify affected reports or holds after an update.

## Acceptance evidence

Build independently sourced reference cases for every launch profile and applicable
rule family, including ordinary cases, boundaries, cross-account interactions,
federal/state differences and missing/unsupported inputs. Expected values must derive
from identified authoritative examples or an independently qualified calculation
method, not a second copy of the implementation or agreement between model outputs.

Trace every declared supported rule to its source, applicability test and reference
result. Cover basis corrections, gain/loss netting, wash-sale adjustments, relevant
retirement basis and distribution differences, contribution eligibility, rounding,
year transitions and corrected imports. Freeze reference versions and tolerances
before scoring; investigate discrepancies rather than loosen tolerances afterward.

Exercise the real intake → normalized facts → tax calculation → economic report path,
plus any tax-dependent refusal through the shared application controls. Synthetic
reference cases can qualify calculations for accounts the owner does not actually
hold; they cannot qualify an untested broker adapter. Report coverage and gaps per
account, jurisdiction and year. No actual tax returns, account credentials or private
financial documents belong in Git fixtures, Discord broadcasts or evaluation logs.

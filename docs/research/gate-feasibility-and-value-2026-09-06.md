# Feasibility and value measurement — 2026-09-06

The premise remains worthwhile as a human-gated awareness and discipline tool. Its financial
edge and time savings are hypotheses. The current evidence supports continuing read-only
observation and bounded repairs; it does not support increasing autonomy.

The [offline scorer](../runbooks/research-value-score.md) now implements separate extraction,
paired time and cost-adjusted strategy arithmetic with ten synthetic calculator checks.
Its [current output](../evidence/research-value-status-2026-09-06.json) correctly reports all three
as unmeasured because independently collected study inputs were not supplied. It grants no gate.

## What the existing observations establish

The checked-in September 4 natural-head screen covered 192 discovery names: 50 had no current
SEC mapping, 112 had an unsupported absolute head and 30 had a supported head. None passed
selection eligibility or the current-target date gate. The September 3 screen had 191 names,
33 supported heads, one eligible source set (BHF) and no date-ready target. These are dated
snapshots, not an independently audited population of all cash mergers or a forecast of arrivals.
The screen reads at most 12 supported 2026 documents per issuer; its zero is not proof that every
older filing lacks extractable terms. Full source coverage is required before an authority claim.

The code requires 30 unique eligible identities on the same selection date, not 30 concurrent
orders. Under the current supported-head, cash-only and source-coverage rules, attainability has
not been demonstrated. Simply repeating yesterday's parser over unchanged bytes cannot repair
the shortfall. The 30-row minimum is unchanged by this work.

Recommendation: use filing-triggered awareness intake with a bounded daily discovery/poll-health
backstop. Cache raw bytes by accession/document hash and rerun semantics when the supported
head or declared source set changes, or when a reviewed parser revision warrants a one-time replay.
Measure unchanged-source rescans separately. Preserve SEC 429 HALT, completeness, supported-head
and freshness rules; an event trigger is not permission to trust a stale baseline. Keep operator
attention for genuinely changed evidence rather than repeat manual market-morning screens.
This recommendation does not modify the running soak or its scheduler.

A rolling prospective research cohort could test whether enough eligible deals arrive over a
fixed observation window. It would require a separately reviewed selection contract with each
entry sealed before its outcome, every exclusion retained, explicit right-censoring and a held-out
evaluation period. It cannot be quietly inserted into today's same-date W-24 manifest or used
to count the same issuer repeatedly. Decide that redesign only after measuring actual arrivals.

## Precision and recall are separate measurements

Freeze a source population by discovery window and issuer/form strata before inspecting parser
predictions. Hash the manifest and all raw filings. Include rejected documents and ambiguous
language, not just extracted rows. A reviewer who did not implement the parser labels cash amount,
target security, consideration mix, lifecycle, expected date and outside date from exact source
spans without seeing predictions. A second reviewer adjudicates disagreements while preserving
both original labels. The independent label set, parser revision and source hashes must travel
with every score.

For each field report true positives, false positives, false negatives and true negatives,
plus ambiguous/unlabelled counts and coverage. A refused but independently extractable fact is a
false negative. An emitted wrong value is a false positive and a missed correct value. Precision
is TP/(TP+FP); recall is TP/(TP+FN). A zero denominator is **unmeasured**, never 100%.
Report exact-value and complete-record accuracy separately. Keep explicit date refusals and
cross-document/security misattribution attacks as a distinct adversarial set. Tuned regression
fixtures are development evidence; they cannot also serve as the independent held-out population.

The old RW-11 scorer evaluates emitted text-delta rows; it cannot establish merger extraction
recall because missed fields need their own independent denominator. Do not rename that score.
No independently labelled merger population was supplied in this session, so neither held-out
precision nor recall is claimed. Human-source labelling remains a real evidence dependency.

## Prospective value experiment

Before collecting outcomes, seal a 20-market-day paired awareness study. On randomly assigned,
balanced days compare the cockpit with the same operator's ordinary feed/watchlist routine;
use matched packets or disjoint days to avoid learning an answer twice. Log active review minutes,
correction minutes, operational maintenance, important missed events, duplicate alerts, source
errors and response latency. Preserve zero-event days and aborted sessions. State the independently
judged definition of an important event first. Summarize medians, paired differences and uncertainty;
do not treat model latency or process uptime as operator time saved.

For each day capture subscription allocation and incremental cash spend separately, data/hosting/
witness costs, and displaced coding capacity from vendor-limit contention. Report net hours saved
after maintenance. Value those hours at an operator-chosen rate with sensitivity at zero; keep
sunk development effort visible but separate. The older ROI document's 8–18 hours/month is an
estimate, not a measurement and not a current verified subscription-price quote.

For strategy evaluation, prospectively freeze the universe, capital schedule, entry/exit rules,
cost model, and horizons before seeing outcomes. Compare the same eligible, timestamped population
against (1) no-trade cash with actual available cash yield, (2) a broad equity total-return benchmark
with identical cash-flow dates, and (3) a simple deterministic cash-merger rule with identical
eligibility/execution assumptions. All benchmark data are research inputs, never execution-watchlist
seeds. Report net return, drawdown, turnover, capital-days, tail loss and uncertainty. Subtract
spread, fees, adverse execution, latency/market-impact assumptions and allocated operating cost;
stress these costs and show capacity. Keep open/unresolved and failed deals in the denominator.
Pair comparisons by date and issuer; report dependence rather than treating every alert as an
independent trial.

Alpaca documents that paper simulation omits several real execution effects, so paper P&L alone
cannot validate live capacity or profitability ([Alpaca paper-trading documentation](https://docs.alpaca.markets/us/docs/paper-trading)).
No eligible prospective strategy cohort, signed broker outcome population or paired time study
was provided here. Current measured strategy advantage and net time savings are therefore
**unmeasured**. The next decision is whether to fund that bounded measurement, not whether to
expand autonomy. No empirical PASS is manufactured from offline tests.

Source records: [September 3](../evidence/w24-natural-head-rescreen-2026-09-03.md),
[September 4](../evidence/w24-natural-head-rescreen-2026-09-04.md),
[W-24 contract](../PLAN.md), [historical ROI estimates](upside-and-roi.md).

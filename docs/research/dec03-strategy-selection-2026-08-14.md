---
title: DEC-03 strategy selection and deterministic paper-edge boundary
type: decision
status: live
last-verified: 2026-08-14
---

# DEC-03 strategy selection

## Status

The operator moved `DEC-03` from post-MVP deferral into active paper-MVP scope and explicitly
selected the constrained cash-only, long-target merger-arbitrage research slice on 2026-08-14.
The decision is **closed**. The selection permits `W-23` implementation under this document's
contract; it does not authorize paper mode, create an expected edge, or enable live/autonomous
trading.

`DEC-03` was the first missing strategy input for new opening paper Orders. W-23 now implements
the selected provider on its dedicated branch, but production has no empirical calibration or
eligible reviewed target, so risk still returns `needs_human` and mints no `Order`. Other
readiness gates remain open
(cross-platform CI, RW-14/RW-15 evidence, and two W-22 market days), but none can substitute for
the absent deterministic edge source.

## Candidate comparison

| Candidate | Fit with the cockpit today | What is missing | Decision |
|---|---|---|---|
| Cash-only small merger arbitrage, long target only | Best architectural fit. EDGAR ingestion, raw evidence, filing triage, quotes, cost verification, human approval, and paper reconciliation already exist. No short/borrow path is needed. | Dynamic deal discovery; schema-gated deal-term extraction; point-in-time completed/failed outcome data; deterministic completion/failure-loss calibration; operator-approved strategy symbols. Binary deal-break loss remains material. | **Recommended first research slice**, under the restrictions below. |
| Closed-end-fund discount reversion | Long-only and compatible with the existing 30-day horizon. The observable price/NAV gap is structurally meaningful. | A trustworthy point-in-time NAV/distribution/corporate-action feed and survivorship-safe history. SEC N-PORT is useful research data but is public quarterly and is not a fresh daily NAV authority source. Persistent discounts also mean “below NAV” alone is not expected return. | Defer until a daily NAV data contract is selected and measured. |
| Stay strategy-agnostic | Preserves the current safety posture and permits awareness/reliability evidence. | It deliberately supplies no edge and therefore cannot exercise a real opening paper Order. | Safe fallback if the data/calibration bar cannot be met. |
| Mega-cap technical/news signal on the bring-up watchlist | Operational feeds are already healthy. | No documented, point-in-time expected-return model. A convenient watchlist is not evidence of alpha. | Rejected for `W-23`. |

Primary-source checks support the distinction. The SEC's current
[Form N-PORT data sets](https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets)
contain public portfolio information from monthly reports but are distributed as quarterly data
sets. Investor.gov also notes that closed-end funds are not required to calculate NAV daily and
that a discount may persist ([NAV definition](https://www.investor.gov/introduction-investing/investing-basics/glossary/net-asset-value),
[closed-end fund bulletin](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/investor-bulletin-publicly-traded-closed-end-funds)).
For deals, SEC rules identify Schedule TO and Schedule 14D-9 as the tender-offer statement and
target recommendation statement, while Form 8-K Item 1.01 covers entry into a material
definitive agreement ([SEC forms and schedules](https://www.sec.gov/about/divisions-offices/division-corporation-finance/rules-regulations-schedules),
[Form 8-K](https://www.sec.gov/info/edgar/forms/form8-k.pdf)). Those filings establish terms,
not completion probability or expected return.

The merger-arbitrage risk is not hidden by the word “arbitrage.” Mitchell and Pulvino's
historical study found a nonlinear downside resembling uncovered index-put exposure during
severe market declines, even after considering transaction costs
([paper record](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=268144)). That is why raw
deal spread cannot be treated as trusted edge.

## Selected strategy: constrained cash merger arbitrage

The accepted first strategy version is deliberately narrow:

- US-listed common-equity targets after a signed public definitive agreement or commenced
  tender offer;
- fixed cash consideration only; buy/long the target only;
- no acquirer short, options, leverage, CVR, earnout, mixed/stock consideration, competing-bid
  speculation, rumor/pre-announcement trade, or live capital;
- expected resolution within the existing `long` horizon of 30 calendar days; longer deals may
  be observed in shadow but cannot emit `edge_estimate.v1`;
- minimum quote, ADV, price, freshness, and source-completeness gates; and
- the discovered symbol must be separately admitted to the operator-approved strategy
  watchlist. Discovery cannot silently enlarge the risk allowlist.

This is a paper research strategy, not a profitability claim. If no deal clears every gate, the
correct output is no proposal and no order.

W-23's initial implementation is narrower than this decision ceiling: it admits signed
definitive agreements on Nasdaq/NYSE/NYSE American common equity. Tender offers remain
shadow-only until Schedule TO/14D-9 ingestion is separately implemented and reviewed.

## `W-23` implementation contract

`W-23` was claimed after the operator made the `DEC-03` selection. It is a fresh, separately
reviewed order-path package with these stages.

### 1. Point-in-time evidence before authority

Build an immutable, versioned deal record from SEC filings and market observations. At minimum
it binds target symbol/CIK, cash consideration, announcement and current filing accessions,
conditions, expiry/outside date, amendment/termination state, quote timestamp, and raw source
references. Build a survivorship-safe labelled history of completed and failed eligible deals;
delisted and failed targets cannot disappear from the sample.

The fact-extraction audit must meet the existing RW-11 bar (at least 95% held-out field
precision) on the exact deal fields used by eligibility. A model may extract candidate facts,
but deterministic validation against the cited filing decides whether they are usable.

### 2. Conservative deterministic calculation

For an eligible deal, compute a versioned estimate from observed and calibrated inputs:

`gross_bps = 10,000 * [p_close * (cash_offer / current_ask - 1) +
p_fail * (break_price / current_ask - 1) +
p_open * (horizon_price / current_ask - 1)]`

The mutually exclusive 30-day probabilities (`p_close + p_fail + p_open = 1`), failure price,
and still-open horizon price must come from a frozen point-in-time calibration cohort and pinned
method, never from proposal text or a model forecast. W-23 pins that method to the 95% Wilson
lower completion probability, empirical allocation of the remaining failure/open probability,
and the worst observed failure/open price ratios. The still-open state is explicit because a
tender expiry or expected closing date can be extended; silently treating every surviving deal
as completed would overstate edge. Use conservative out-of-sample estimates (lower confidence
for completion and adverse failure/open-price treatment), then store the complete input record
and calibration version behind an immutable `evidence_ref`. The existing W-21 gate still
requires gross edge to be at least three times all-in modeled cost.

### 3. Wire the same provider at both authority checks

Add one injected provider seam that returns `EdgeEstimate | None`. It must be called:

1. during `draft_proposal`, after symbol/side/horizon are known; and
2. in `PaperExecutionService._fresh_risk`, immediately before operator approval, using the fixed
   persisted order quantity and current quote/evidence.

Any exception, missing evidence, stale quote, changed offer, amendment, termination, symbol or
side mismatch, horizon mismatch, unknown calibration version, or non-finite output returns no
edge and no Order. Status must name the selected source/version and report unavailable when it
cannot compute; it must not merely say the check is wired.

### 4. Attack and regression gates

The package needs tests that fail if either provider call is removed and adversarial cases for
forged raw references, stale/replayed estimates, model-added expected-return fields, proposal
symbol/side/horizon substitution, quote movement between draft and approval, deal amendments or
termination, restart/idempotency, calibration tampering, and unauthorized strategy-watchlist
expansion. Run the full Windows suite and Ruff, then the Ubuntu/Windows/macOS CI matrix.

### 5. Paper activation remains separate

After `W-23` passes its gates, the operator must still deliberately enter audited `paper` mode
and manually approve each proposed trade. Risk → mode → approval → `OrderGateway` remains the
only path. `review_live`, `auto_veto`, `auto_full`, broker credentials in a model runtime, and
model-authored returns remain out of scope.

## Operator decision record

On 2026-08-14 the operator explicitly accepted the constrained cash-merger recommendation.
`DEC-03` is closed and `W-23` is claimed on `work/w23-cash-merger-edge`. This decision does not
claim calibration evidence, activate paper mode, or relax any risk/mode/approval/gateway gate.
The provisioning and activation contract is [[../runbooks/w23-cash-merger-edge]].

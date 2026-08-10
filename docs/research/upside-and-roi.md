---
title: Upside & ROI analysis — what this system realistically returns
type: analysis
status: reference
last-verified: 2026-07-02
related: [README, research-report, research-supplement, build-scaffold-ultracode]
---

# Upside & ROI: an honest accounting

Personal-use analysis, not financial advice. Figures marked **[est.]** are
reasoned estimates, not verified research; everything cited to the research
docs passed 3-vote adversarial verification.

The honest headline: **the reliable ROI is time, discipline, and reusable
infrastructure. Trading alpha is an experiment with capped downside — priced
here as an option, not as expected return.** Any analysis that leads with
"the AI will beat the market" is selling something; the verified research
gives specific reasons for skepticism (§3).

---

## 1. The cost stack

| Item | Cost | Notes |
|---|---|---|
| AI subscription | **$0 marginal** (already owned) / $200/mo standalone | The Max 20x plan is already paid for dev work; the copilot's real cost is *usage-window contention* with interactive coding, mitigated by the batching + budget design. If bought solely for this: $200/mo. |
| Market data + news | **$0** | Alpaca news WS + Finnhub free tier (verified, [[research-supplement]] §2) |
| Broker | **$0** | Alpaca paper is free; Robinhood Agentic has no platform fee (trading costs = spread/execution as usual) |
| Infrastructure | **~$0** | Existing Windows workstation; optional ~$5-15/mo VPS later |
| Build time | **~5-7 weeks part-time [est.]** | Per the scaffold calendar; the largest real cost |
| Risk capital | **Bounded by design** | The Agentic account is funded only with a loss-tolerable amount; that funding IS the max trading downside (verified funds-level sandbox) |

Total recurring cash cost if the subscription is already owned: **≈ $0/month.**
That is the quiet headline — the marginal-cost profile of this build is close
to zero, which changes what "worth it" means.

## 2. Return stream A — time (reliable, quantifiable)

A serious manual morning routine — overnight news sweep, futures/positions
check, earnings & macro calendar, watchlist deltas — runs 30-60 min/day
**[est.]**. The brew compresses it to a 5-10 min read. Intraday, the triage
loop replaces ad-hoc "check the market" interruptions (which cost context-
switching on top of the minutes).

- Time recovered: **8-18 hrs/month [est.]**
- Valued at a solo developer's opportunity cost ($50-100/hr [est.]):
  **$400-1,800/month equivalent**
- Against $0-200/mo running cost: the time stream alone clears the bar within
  the first month of Phase-1 operation, and pays back the ~5-7-week build in
  roughly **3-9 months [est.]** even valuing build hours at the same rate.

This stream requires only that Phase 1 works — no trading, no alpha, no live
account. It is the reason the phased plan front-loads the brew.

## 3. Return stream B — trading alpha (speculative; treat as a capped option)

What the verified research actually supports:

- **TradingAgents'** reported gains are credibly disputed for look-ahead bias
  (repo issue #805; arXiv 2601.13770). *(2-1 verified, caveated)*
- **LiveTradeBench** (arXiv 2511.03628) finds static-benchmark winners
  **underperform when trading live**.
- The 60k-star **ai-hedge-fund** deliberately places no trades and disclaims
  itself as educational.
- No credible, verified evidence surfaced that LLM agents beat market
  benchmarks net of costs.

So the rational prior is: **expected alpha ≈ 0, possibly negative**, with wide
uncertainty. The system prices this correctly as an *option*:

- **Premium paid:** the segregated Agentic account balance (only money you'd
  tolerate losing — max downside is that balance, enforced by the verified
  funds-level sandbox plus the risk engine's daily-loss and notional caps).
- **Payoff if it works:** unbounded upside on a small base, plus the knowledge
  it works — which justifies scaling *only after* measured proof.
- **Exercise discipline:** the performance gate (≥4 weeks, ≥30 proposals,
  ≥80% approval concordance, drawdown inside limits) and a benchmark harness —
  every strategy result is reported **relative to a SPY buy-and-hold of the
  same capital**, same period. If the system can't beat the benchmark it must
  say so in its own scorecard; that honesty is built into the design, not left
  to the operator's mood.

What CAN honestly be expected from this stream even at zero alpha: it will
tell you the truth about whether the approach works, cheaply, on a few hundred
dollars — instead of the expensive way.

## 4. Return stream C — discipline & risk reduction (real, hard to price)

Behavioral drag — overtrading, panic exits, un-documented theses, position
creep — is a well-documented cost of discretionary retail investing **[est.,
directionally robust]**. Structural features that attack it:

- Every position carries a written thesis + **invalidation condition** decided
  before entry, not rationalized after.
- Deterministic caps (notional, concentration, daily loss) that no amount of
  in-the-moment conviction can override without an audited limits change.
- The audit trail makes self-review possible: hit rate, veto quality, and
  approval concordance are measured, not remembered.

If applied to an existing self-directed portfolio, avoiding even one impulsive
mistake per year likely exceeds the system's entire cash cost **[est.]** — but
this stream is honest only if the operator actually routes decisions through it.

## 5. Return stream D — infrastructure & skills (certain, reusable)

- The **agent-runner harness** (subprocess model calls, schema-gated outputs,
  budget/backpressure, session interrogation) is a general-purpose asset —
  directly reusable for any future automation on the same subscription.
- The Discord-control-plane pattern (approvals, autonomy ladder, breakers) is
  a portfolio-grade reference architecture for human-gated agent systems —
  a category that is getting hot, not cold.
- Working fluency with MCP brokerage surfaces (Robinhood Agentic launched
  ~5 weeks ago) is early-adopter knowledge with career surface area.

## 6. Scenario table (3-year view, subscription already owned) [est.]

| Scenario | Assumptions | Outcome |
|---|---|---|
| **Pessimistic** | Alpha ≤ 0; brew used ~daily | Time stream still nets $400+/mo equivalent; trading experiment costs its capped account; harness reused elsewhere. Net positive but unglamorous. |
| **Base** | Alpha ≈ 0; discipline stream engaged on the main portfolio | Time + at least one avoided behavioral mistake/yr; system pays for itself several times over in equivalent value; alpha question answered with data. |
| **Optimistic** | Small persistent edge survives the benchmark harness | All of the above, plus measured scaling of the Agentic account under the gate; the scorecard — not optimism — sets the pace. |

Note what's absent: a scenario where returns are projected from backtests. The
verified research is why.

## 7. Risk ledger (what could make ROI negative)

- **Usage-policy drift:** the Anthropic SDK-credit pause (2026-06-15) shows
  subscription economics can change on two weeks' notice; a re-ban of headless
  subscription use would force API pay-as-you-go (real $/mo). Mitigation:
  runner is vendor-portable; costs stay visible in `#system`.
- **Robinhood beta churn:** scope/rate limits/GA terms are moving (two refuted
  claims prove the ground shifts). Mitigation: Alpaca path is fully functional
  without it.
- **A bug that trades:** the invariants (single order path, deterministic
  gates, kill switch, breakers) exist for exactly this; the paper soak and
  deliberate breaker-trip tests are non-negotiable before live mode.
- **Sunk-time overrun:** the scaffold's WP structure keeps Phase 1 shippable
  in weeks; if the project stalls, stopping after Phase 1 still keeps the
  entire time stream.
- **Overtrust ratchet:** the ladder only moves up through measured gates and
  auto-disarms daily — by design, "the models feel good lately" is not an
  unlock condition.

## 8. Verdict

Fund the build. The investment thesis for the *system* is strong even under
skeptical assumptions about the thesis-generation of the system: near-zero
marginal cash cost, a time stream that clears payback in months, certain
infrastructure reuse, and a correctly-priced, strictly-capped experiment on
the one question everyone else answers with marketing. The system's own
scorecard — benchmark-relative, audit-backed — decides whether the alpha
stream ever earns a larger allocation.

---
title: Gap-fill supplement — market data, PDT/regulatory, AI-provider policy
type: research-report
status: reference
last-verified: 2026-07-02
related: [README, research-report, cockpit-review-and-integrated-plan]
---

# Supplement: market data, regulation, and provider policy

**Method:** the main run's verification budget ran out before any market-data
(angle 4) or regulatory (angle 5) claims were checked. This pass extracted those
claims from the first run's journal and gave each the same 3-lens adversarial
verification (primary-source accuracy, staleness through July 2026, overstatement).
**Result: 12/12 confirmed, 0 refuted** (37 agents). Corrections noted inline.

---

## 1. The headline: the pattern-day-trader rule is gone (3-0)

FINRA's Rule 4210 amendments ([Regulatory Notice 26-10](https://www.finra.org/rules-guidance/notices/26-10),
SEC-approved 2026-04-14) **eliminated the PDT designation and the $25,000
minimum-equity requirement, effective June 4, 2026.** Verifier refinements:

- **Replacement, not deregulation:** new intraday margin standards (Rule 4210
  (a)(17)-(19), (d)(2), (g)(1)(J)-(K)) require firms to monitor intraday margin
  deficits — real-time blocking or end-of-day margin calls — with 90-day account
  restrictions for repeat violations. The Reg-T $2,000 margin minimum and
  cash-account settlement / good-faith-violation rules still apply.
- **The 18-month phase-in (to 2027-10-20) mostly doesn't bite:** Robinhood
  (2026-06-04), Schwab (06-08), Alpaca (06-04), E*TRADE (06-09), and Webull (06-04)
  have already adopted the new framework and no longer enforce day-trade counts or
  the $25k minimum. Laggards (e.g., Fidelity, unannounced) and stricter house
  rules remain possible — check the specific broker.

**Design consequence:** short-term vs. long-term trading in a small account is now a
strategy decision, not a compliance workaround. The risk engine should model
*intraday margin deficit*, not day-trade counts.

## 2. Morning-brew data stack: $0/month is genuinely achievable

- **Alpaca real-time news WebSocket** (Benzinga-powered), free with an API key:
  `wss://stream.data.alpaca.markets/v1beta1/news` (sandbox variant available).
  One wildcard message — `{"action":"subscribe","news":["*"]}` — subscribes to the
  entire news firehose, no ticker enumeration. Payloads carry headline, summary,
  author, timestamps, content, symbols, source. *(Both claims 3-0 against live
  docs; single-provider (Benzinga) feed; requires API-key auth; still `v1beta1`
  but no deprecation notice anywhere.)*
  [docs](https://docs.alpaca.markets/us/docs/streaming-real-time-news)
- **Finnhub free tier — better than initially claimed:** `/news` REST endpoint
  (categories: general/forex/crypto/merger; `minId` cursor for incremental polling)
  carries no premium flag *(3-0)*; free tier is 60 calls/min + 30/sec burst
  *(2-1, corrected)* — and the corrections found free **US quote data is
  real-time, not 20-minute delayed** (only LSE is 15-min delayed), with paid tiers
  starting at **$49.99/mo** (not $80). [docs](https://finnhub.io/docs/api/market-news)
- **Alpaca as the paper-trading broker** got independent confirmation *(3-0)*:
  BrokerChooser's 2026 hands-on testing (accounts opened, keys generated, paper and
  live orders run) ranks Alpaca the **#1 US algo-trading broker** — 200 req/min,
  API-identical paper account, 5-6+ years history, Python/JS/C#/Go SDKs.

**Recommended stack:** Alpaca news WS (event stream) + Finnhub (quotes/polling) +
broker MCP (portfolio state) = $0, leaving the full $200/mo for the AI subscription.

## 3. AI-provider policy: the human-approval gate is also the compliance answer

- **OpenAI** *(3-0)*: [Usage Policies](https://openai.com/policies/usage-policies/)
  prohibit automating **high-stakes decisions** in enumerated sensitive areas —
  "financial activities and credit" is one — without human review. "High-stakes" is
  undefined (read conservatively). A verifier also surfaced that OpenAI's companion
  policy for *ChatGPT Agent* goes further: users should **not automate stock
  trading or other investment transactions at all** on that surface. Codex is a
  different product, but it signals posture — keep Codex on research/coding duties,
  off the order path.
- **Anthropic** *(2-1, scope-corrected)*: the
  [Usage Policy](https://www.anthropic.com/news/usage-policy-update) (effective
  2025-09-15, unamended as of 2026-07) mandates human-in-the-loop review by a
  qualified professional + AI disclosure for high-risk financial use cases —
  **but only when outputs are consumer-facing** (advice/recommendations/decisions
  directly affecting individuals or consumers); B2B, internal, and
  **own-account** use is explicitly out of scope. For a personal copilot, the
  approval gate is prudent-not-mandated on the Claude side.

Either way: the gate the architecture already requires keeps the system comfortably
inside both vendors' policies and out of enforcement ambiguity.

## 4. Robinhood policy nuance (3-0, conclusion updated)

Robinhood's standing
[third-party-connections policy](https://robinhood.com/us/en/support/articles/third-party-connections/)
still prohibits connecting trading APIs to a regular account without written
authorization — the old community workaround (robin_stocks-style unofficial
clients) **remains a ToS violation**. But as of 2026-05-27 the conclusion flipped:
**opting into Agentic Trading is itself the authorization.** The sanctioned path
(Agentic account + MCP) and the prohibited path (unofficial APIs on regular
accounts) are now cleanly separated. (Crypto has had an official public API since
2024.)

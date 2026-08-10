---
title: Solo-dev AI investing copilot — main deep-research report
type: research-report
status: reference
last-verified: 2026-07-02
related: [README, research-supplement, cockpit-review-and-integrated-plan]
---

# Feasibility: solo-dev, subscription-powered AI investing copilot

**Method:** multi-agent deep-research harness, run 2026-07-02. Five search angles →
24 sources fetched → 120 claims extracted → top 25 adversarially verified with
3 independent refuter votes each (2/3 refutes kill a claim). Result: 21 confirmed,
4 refuted, 0 unverified. 106 agents.

**Verdict: FEASIBLE.** Every load-bearing piece of the plan is real and sanctioned,
with one economic caveat on the Claude side and one design responsibility that lands
on the developer (the approval gate).

---

## 1. Robinhood's MCP server is real, live, and documents this exact use case

- **Agentic Trading shipped 2026-05-27** (beta, phased rollout) — a live MCP server
  at `https://agent.robinhood.com/mcp/trading`. The support docs publish per-client
  setup commands, e.g.
  `claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading`.
  *(3-0; [newsroom](https://robinhood.com/us/en/newsroom/robinhood-is-now-open-to-agents/),
  [support article](https://robinhood.com/us/en/support/articles/agentic-trading-overview/),
  [product page](https://robinhood.com/us/en/agentic-trading/), TechCrunch 2026-05-27;
  pages verified live 2026-07-02.)*
- **Officially documented clients:** Claude Code, Claude Desktop, ChatGPT, Codex,
  Cursor, Grok, plus any MCP-capable platform. Connecting a subscription-authed
  agent is a documented path on Robinhood's side. *(3-0.)*
- **Blast-radius containment is native:** agents can only *trade* inside a dedicated
  "Agentic" account funded explicitly by the user, and cannot move money in or out.
  But segregation is **funds-level only** — a connected agent gets read access to
  ALL Robinhood accounts, and that portfolio data flows to whichever AI provider
  runs the agent. Privacy consideration, not a blocker. *(3-0.)*

## 2. Human approval is NOT enforced — the developer must build it

Robinhood's own terms: *"trades may be executed by an AI agent without your direct
input on each transaction"*; *"You assume all risk for orders placed by your AI
agent"*; *"Robinhood does not control, supervise, monitor, recommend, or audit
these AI agents."* A verifier specifically hunted for a "require manual approvals"
setting — it exists only for the Agentic Credit Card, not trading. The
`review_equity_order` MCP tool is advisory; nothing forces it before
`place_equity_order`. Post-trade notifications and a disconnect button are the only
platform guardrails. *(3-0.)*

**Design consequence:** the pre-trade approval gate (proposal → push notification →
explicit confirm → only then invoke the order tool) is the one safety layer nobody
provides. It must live in the app, hard-coded — not prompt-based.

## 3. Subscription-vs-API — the premise holds, with one gotcha each way

### Claude Max 20x ($200/mo)
Anthropic's help docs explicitly name *"third-party apps that authenticate with your
Claude subscription through the Agent SDK"* as a covered usage category —
subscription-authed headless apps are a **sanctioned pattern**, not a ToS violation
(this superseded the Feb 2026 third-party-OAuth ban). **The gotcha:** the announced
June 15 scheme granting Max 20x a dedicated $200/month Agent SDK credit (exempting
headless `claude -p`/SDK usage from plan limits) was **paused on June 15, 2026**.
Today headless usage draws from normal 20x limits, and the policy is explicitly in
flux. *(3-0;
[support.claude.com article 15036540](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan),
verified live 2026-07-02. Key sentence: "Claude Agent SDK, `claude -p`, and
third-party app usage still draw from your subscription's usage limits.")*

### ChatGPT Pro ($200/mo)
Codex is included in every tier; ChatGPT-account sign-in is officially supported,
including a documented **headless device-auth path** (`codex login --device-auth`),
governed by the consumer ToU with no explicit headless prohibition in the Codex plan
docs. **The gotchas:** throughput is an opaque shared "agentic usage limit"
published only as wide ranges (Pro ≈ 300–1600 GPT-5.5 messages per 5-hour window;
big tasks consume far more per message — GitHub issue #19571 reports a Pro window
depleting in ~20 minutes); the sanction covers OpenAI's **own Codex clients**, not
extracting the OAuth token from `~/.codex/auth.json` into an unrelated app; and the
ToU bans programmatic output extraction and limit circumvention — a gray zone for a
24/7 loop. OpenAI recommends API keys for CI. *(3-0;
[help.openai.com](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan),
[developers.openai.com/codex/auth](https://developers.openai.com/codex/auth),
[/codex/pricing](https://developers.openai.com/codex/pricing).)*

**Net:** the premise works on either vendor **if** the copilot runs through the
vendor's own client/SDK surface (Claude Code CLI / Agent SDK, Codex CLI) rather than
a raw harvested token. Budget as if all usage counts against normal plan limits.

## 4. The model hierarchy has precedent — the escalation trigger is custom work

- **TradingAgents** (TauricResearch, ~90k stars, Apache-2.0, v0.3.0 June 2026)
  ships the two-tier split in code: `deep_think_llm = "gpt-5.5"` for
  research/portfolio-manager roles, `quick_think_llm = "gpt-5.4-mini"` for
  analysts/trader/risk debaters, plus a firm-like role decomposition
  (fundamentals/sentiment/news/technical analysts → bull-vs-bear debate → trader →
  risk management). **Caveats (medium confidence, 2-1):** the tiering is *static
  role assignment* — no code path lets the quick model dynamically escalate to the
  deep one; and its backtest gains are credibly disputed for look-ahead bias (repo
  issue #805, arXiv 2601.13770; LiveTradeBench arXiv 2511.03628 finds
  static-benchmark winners underperform live). Architectural precedent only.
- **RouteLLM** (LMSYS/UC Berkeley/Anyscale) quantifies why the hierarchy pays:
  trained cheap-vs-strong routers cut costs **up to 85% while retaining 95% of
  GPT-4-level performance** on MT-Bench (45% on MMLU, 35% on GSM8K). *(3-0 on the
  numbers.)* But "it's a maintained framework" was **refuted 0-3** — treat it as a
  validated pattern to reimplement, not a dependency.
- **ai-hedge-fund** (virattt, 60.7k stars, actively developed) — the flagship
  open-source AI fund — **deliberately does not place trades** ("the system does not
  actually make any trades"). Existence proof that the most popular project in the
  space stops at the same line the copilot should gate. *(2-1; note the stated
  reason is an educational/liability disclaimer, not a HITL endorsement, and
  derivative projects + Alpaca's own MCP server do wire full automation.)*

## 5. Refuted claims (failed 0-3 — do not rely on these)

| Refuted claim | Why it matters |
|---|---|
| "Agentic Trading is equities-only (no options/crypto)" | Agentic crypto already expanding — re-check asset scope at build time |
| "Agentic Trading is waitlisted + desktop-only setup" | Availability moving fast — re-check at build time |
| "ai-hedge-fund is a hierarchical *router* precedent" | Cite it only for the execution-gap point |
| "RouteLLM is a *maintained* framework" | Reimplement the pattern; don't depend on the repo |

## 6. Open questions

1. Will Anthropic un-pause the Agent SDK credit, and in what form? (Directly
   determines whether a continuous triage loop is economical on subscription auth
   vs. needing API pay-as-you-go.)
2. Robinhood MCP operational parameters post-beta: rate limits, auth/session
   lifetimes for long-running headless agents, options/crypto/futures timeline, GA.
3. Real sustained throughput of a 20x plan under a 24/7 triage workload — and
   whether either vendor treats an always-on financial agent as fair use **in
   enforcement practice**, not just on paper.

## 7. Sources (primary unless noted)

- robinhood.com newsroom / support / product pages (Agentic Trading) · TechCrunch 2026-05-27 (secondary)
- support.claude.com article 15036540 (Agent SDK with Claude plan) · thenewstack.io 2026-06-16 (secondary, pause coverage)
- help.openai.com article 11369540 (Codex with ChatGPT plan) · developers.openai.com/codex/{auth,pricing} · github.com/openai/codex issue #19571
- github.com/TauricResearch/TradingAgents · github.com/virattt/ai-hedge-fund · github.com/lm-sys/RouteLLM · arxiv.org/abs/2406.18665 · arxiv.org/abs/2412.20138
- github.com/OpenBB-finance/OpenBB

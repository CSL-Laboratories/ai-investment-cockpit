# Candidate: Cockpit successor product and economic contract

**Owner direction:** recorded in CSL-212. Proposed 2026-10-24 amendment under
[CSL-511](https://linear.app/csl-laboratories/issue/CSL-511).
**Status:** amendment proposed (2026-10-24); acceptance is a human decision by Lucas.
Exact-digest acceptance remains a separate human decision.

This file is a **proposed amendment** to the 2026-10-16 human-accepted successor
contract. It does not replace that receipt. The last accepted bytes remain
`docs/replacement/SUCCESSOR-PRODUCT.md` at SHA-256
`6e5f0d0cad102077fd058aac4970ac0eb31fd6f6e952f948c43edaa9fe0c919e`, bound by
CSL-212 comment `25c50b43-6de4-4f6d-9652-47a649ccc5ae` and
[`program.json`](program.json). A new exact-digest human receipt is required
before this text is governing. Lucas's 2026-10-24 answers win over the 2026-10-16
wording where they conflict; unchanged clauses are retained.

## Product outcome

Build an **open-source research instrument** that operators can test. By
2026-11-11 it investigates functionality, the best model combinations for trading
and yield, and which skills or strategies optimize yield. Results are published
to market the software. The window ends in a **verdict on whether this category
of software succeeds or fails**. Testing is closed (private benchmarks and secret
variables); the software is open. (Lucas C1 / 10:44; C7.)

It covers **crypto, stocks and prediction markets**. (Lucas 10:25 #3; H-Q3.)
The first coherent event-market slice remains **Kalshi demo**; that still fits.
(Lucas C-Q8; Kalshi demo environment docs, retrieved 2026-10-25.) Polymarket US
Retail, legacy live paths and tax calculation code are **capability to carry
forward**, not new Nov 11 invention. (Lucas 10:30; C-Q14 resolved 10:49.)

The instrument measures risk-adjusted net yield subject to explicit safety,
compliance, liquidity, drawdown, uncertainty, reliability, latency, cost and
capital constraints. It does not promise alpha. Empty, negative, rejected and
inconclusive results are valid outcomes and must remain visible. Net-yield PASS
is not an 11 Nov gate. Paper dashboard arithmetic for this window: **$2,000
paper versus SPY plus cash**. (Lucas C-Q14 / C12.) The "~$210/mo pays for
itself" narrative is dropped. (Lucas C-Q15.)

The first stock, crypto and event-market universes must be selected by accepted
child contracts (or by measured access under CP2/CP5) rather than inferred from
historical implementation. Lean toward **no paid data**. Lucas may buy an
**optional Alpaca key** if it helps yields; that is his purchase, not CSL
pay-per-token spend. (Lucas C-Q9 / C8.)

## Required product shape

- A point-in-time evidence and replay plane retains raw inputs separately from
  summaries, with source identity, retrieval time, transformations and provenance.
  Applicable collection, licensing, retention, redistribution, confidentiality and
  account terms must be established before authenticated or continuing acquisition;
  demo or read-only access is not blanket data permission. Operator and research
  data belonging to the three founders stays **off GitHub**, in a private
  three-person collaboration system. (Lucas C-Q10 / C9; SP1.)
- Instrument, venue, portfolio, collateral, resolution, cash-flow, fee and
  accounting semantics are explicit and reconcile across restarts and external
  truth. **Tax treatment is out of Nov 11 scope**; legacy tax code is
  carry-forward source only, not a gate. (Lucas C-Q14, resolved 10:49.)
- Strategy research compares explicit baselines on frozen populations, separates
  tuning from evaluation, and accounts for costs, uncertainty and capital use. It
  investigates which skills or strategies actually optimize yield, including
  claimed yields on public social posts, without treating unreproduced claims as
  evidence. (Lucas C1; CR1.)
- Decision routes are provider-neutral experiments. Pairings **sync to the
  user's preference with any combination**. BYOK keys live in the **OS keyring**.
  First providers: **OpenRouter, OpenCode Go and OpenCode Zen**; more
  compatibility ships later. CSL adds **no pay-per-token keys of its own**.
  (Lucas 10:30 Q11; C-Q21; N-3, 11:19 PM PT.) Installed-route qualification still
  earns roles; no provider name is an architecture decision or authority grant.
  A provider response, typed decision or confidence value is an input, never
  permission.
- Authority is expressed as audited compliance profiles. Proposal-only,
  model-assisted and bounded direct authorization remain distinct profiles, not a
  bypass switch. **Live trading in a policy-compliant form is a research
  question, not decided.** Turning live on needs Lucas's explicit go. (Lucas
  C-Q12 / C10; N-1/N-2.) The 2026-10-16 candidate eventual first-live profile
  (bounded direct authorization after separately accepted eligibility, replay,
  simulation, demo or paper, shadow, limited-live, recovery and compliance gates)
  is not activated by this amendment.
  [The completed authority research](AUTHORITY-COMPLIANCE-RESEARCH.md) still makes A
  proposal-only the reference qualification profile and C bounded direct
  authorization an owner-directed candidate future target pending a human-authored
  exact-digest receipt. All profiles share one independent enforcement and
  evidence layer. Profile C is not authorized by this contract and cannot create a
  second order path: any future implementation still uses the single risk-pass,
  mode, approval and `OrderGateway` path unless those binding invariants are
  separately amended and reviewed.
- A **GUI on Linux, Windows and macOS** plus a scriptable **CLI** use one
  versioned application/service API and the same authority checks. Linux-first
  for the sprint; all three OS GUIs are in scope. A TUI is optional and must be
  justified by measured operator value. No surface owns a privileged order path.
  (Lucas C-Q5 / C-Q19; H-Q6.) **No Discord integration at all.** The product is
  standalone software needing only API keys, servers, subscriptions and brokers;
  engine and infrastructure run on our own code. (Lucas C-Q6 / C5.)
- An **operator MCP** may inspect and stop. It must not place orders or expose
  broker tools to a model. A tool named like `place_order` / `place_equity_order`
  fails the candidate. (Lucas C-Q20, 10:30; contrast Robinhood Agentic Trading
  MCP vendor copy, retrieved 2026-10-25.)
- The product is **agent-installable and agent-operable**: a coding agent can
  complete setup and drive inspect/stop without the user designing the install.
  Config syncs with the user's existing providers rather than minting CSL-owned
  secrets. (Lucas seven-point bar, 10:30.)
- Qualification binds evidence to the exact candidate, configuration, data
  manifest, venue profile and environment. Release is a human decision after
  independent verification, not a consequence of CI.

Qualification is capability-scoped. Stock, crypto, Kalshi demo, later Polymarket
US Retail, provider, interface and platform cells retain separate evidence and
unknowns. A PASS for one capability or environment cannot unlock another.

## Nov 11 target

The 2026-10-16 one-month "thin vertical slice" is **replaced** as the 11 Nov cut
line. 11 Nov success is a **replayable research instrument plus operator product**
that operators can test, with published results and a draft category verdict,
hitting **all seven of Lucas's bars** or naming the exact deferral. CLI-only is a
bar-4 fail. Breadth is added when it does not weaken provenance, authority,
recovery or evaluation. (Lucas C-Q18; seven bars 10:30; H-Q3.)

The schedule is a prioritization constraint, not evidence of safety,
profitability, legal eligibility, release readiness or live authority. Lower
maturity is an honest result. After 11 Nov, stretch is a **sealed forward-test
window**, not a rewrite. (Lucas C-Q17 / C15.)

**Not an 11 Nov gate:** SWE-bench or other benchmaxxing; net-yield PASS; live
PASS; language rewrite; tax calculations; Discord; CSL-owned pay-per-token keys.
(Lucas H-Q14; C-Q12; C-Q14; C-Q6; C-Q21.)

## Lucas's seven product bars (11 Nov)

A candidate is 11 Nov-complete only if it hits all seven, or names the exact
deferral. (Lucas 10:30.)

1. **Researched, not slop** — cited evidence, frozen manifests, unknowns visible.
2. **Enterprise and developer quality** — single-tenant, keyring secrets,
   hash-chain audit, fail-closed, schema contracts, CLI+API, tests.
3. **Hyper-optimized** — measured performance; missed SLOs are labeled unmeasured,
   not faked.
4. **A real GUI** — a person clicks, types, inspects, stops, exports. Not a
   screenshot of a CLI. Not Discord stubs.
5. **Agent-installable and operable** — doctor → provision → up → open GUI; the
   same agent can inspect and stop.
6. **Config syncs with the user's existing providers** — reuse logins and OS
   keyring names; BYOK as above; no mandatory new CSL pay-per-token key.
7. **Loved features done right** — competitor hooks (ask, citations, grid,
   dashboard, agent connect, install, portfolio, replay) shipped correctly;
   copying a model-held `place_order` tool is incorrect.

## User and scope boundaries

First users: **Lucas, Chaandan and Samir as United States individuals**, each on
their own personal account. (Lucas C-Q2 / C2.) Team, institutional, multi-tenant
and delegated-role workflows remain deferred. Extension points may be documented,
but first-release code and data models must not carry speculative team machinery.
Our data stays off GitHub; the three share a private collaboration system.
(Lucas C-Q10 / C9.)

In scope for this amendment: crypto, US equities and prediction markets; Kalshi
demo first; broad evidence ingestion with free sources preferred; provider-neutral
decision experiments with preference-synced pairings; GUI on Linux, Windows and
macOS plus CLI; agent install/operate; inspect/stop operator MCP; staged
qualification; measured economics; published results with closed benchmarks; a
category verdict; bounded live-trading **research** (not activation).

Not selected by this contract: implementation language, database, framework,
stock or crypto broker, live venue account, capital allocation, numeric risk
limits, tax treatment, deployment topology or production credential custody.
Those require bounded evidence and human receipts where consequential.

## Current-authority preservation

This successor direction supersedes the September product target for replacement
planning. The 2026-10-16 CSL-212 receipt remains the last accepted digest until a
new exact-digest receipt is posted. This amendment does not retroactively
invalidate historical evidence, nor does it silently replace current runtime
rules. Until separate amendments are accepted **and implemented**:

- every order still follows risk-pass, mode, approval and `OrderGateway`;
- auto decision routing remains pinned as currently documented;
- model outputs remain schema-validated;
- secrets remain in the OS credential store;
- `app/risk/` remains deterministic and free of model, I/O and subprocess logic;
- the client-side human gate and hash-chained audit remain;
- stale, limited or hung operation fails closed to `watch_only`;
- raw inputs remain separate from summaries;
- the current post-PDT risk model remains in force;
- agents do not enable live trading or add live credentials;
- Discord remains in the current runtime only as unamended implementation, not as
  successor product scope.

Future authority-profile amendments must separately bind the exact legal/account
perimeter, venue and contract family, adapter/environment, provider/model version,
permitted actions, expiry, cumulative budgets, information restrictions, stop and
recovery owners, evidence dossier and human receipt. Stops survive restart; restart
requires authoritative reconciliation and fresh authorization. Unknown eligibility,
execution state or provider permission fails closed rather than selecting a more
permissive profile.

## Acceptance boundary

Acceptance of this document establishes product direction only. It does not prove
implementation, provider or venue qualification, profitable operation, safety,
compliance, release readiness, or permission to trade live.

This working-tree text is **not accepted** until Lucas posts an exact-digest
human receipt over it. `program.json` and the replacement-program digest gate
must keep pointing at the 2026-10-16 accepted bytes until that receipt exists.
Updating those pins without a human receipt would falsely claim acceptance.

## Research basis (candidate-only)

This amendment is grounded as follows. It is not a research-vault record; the
csl-research repository returned HTTP 404 from this session (access denial; not
routed around). Material below is **candidate-only** except where it cites the
already-accepted CSL-212 digest receipt.

| Claim in this amendment | Source | Date |
|---|---|---|
| Open-source research instrument; publish results; category verdict; closed testing | Lucas C1 / C7 (frontier map Q table) | 2026-10-24 10:44 PT |
| Crypto + stocks + prediction markets | Lucas 10:25 #3; H-Q3 | 2026-10-24 |
| GUI on Linux, Windows, macOS + CLI; Linux-first sprint | Lucas C-Q5/C-Q19; H-Q6 | 2026-10-24 10:30 PT |
| First users Lucas, Chaandan, Samir as US individuals | Lucas C-Q2 | 2026-10-24 |
| Data off GitHub; private three-person collaboration | Lucas C-Q10 / C9 | 2026-10-24 |
| No Discord; standalone (keys, servers, subscriptions, brokers) | Lucas C-Q6 / C5 | 2026-10-24 10:44 PT |
| Taxes out of Nov 11; legacy tax code carry-forward only | Lucas C-Q14, resolved 10:49 | 2026-10-24 |
| Lean no paid data; optional Alpaca key Lucas may buy | Lucas C-Q9 / C8 | 2026-10-24 |
| Live trading is research, not decided; Lucas's explicit go to arm | Lucas C-Q12 / C10 | 2026-10-24 |
| Any pairing; BYOK in OS keyring; OpenRouter, OpenCode Go, OpenCode Zen first; CSL adds no pay-per-token keys | Lucas 10:30 Q11; N-3 11:19 PM PT; C-Q21 | 2026-10-24 |
| Kalshi demo first still fits | Lucas C-Q8; [Kalshi demo env](https://docs.kalshi.com/getting_started/demo_env) | retrieved 2026-10-25 |
| Operator MCP inspect/stop only | Lucas C-Q20 | 2026-10-24 10:30 PT |
| Seven bars; no SWE-bench | Lucas 10:30; H-Q14 | 2026-10-24 |
| $2,000 paper vs SPY+cash; $210/mo narrative dropped | Lucas C-Q14, C-Q15 | 2026-10-24 |
| Legacy live, Polymarket, tax code = carry-forward | Lucas 10:30; conflicts table | 2026-10-24 |
| Last accepted successor digest | CSL-212 comment `25c50b43-…`; `program.json` | 2026-10-16 |
| Provider-neutral routes; Profile A reference; no second order path | 2026-10-16 accepted SUCCESSOR-PRODUCT.md (retained) | 2026-10-16 |
| OpenRouter BYOK exists as a user-held key product | [OpenRouter BYOK](https://openrouter.ai/docs/guides/overview/auth/byok) **vendor docs** | retrieved 2026-10-25 |
| OpenCode Go / Zen are optional user API-key providers | [OpenCode Go](https://opencode.ai/docs/go/), [Zen](https://opencode.ai/docs/zen/) **vendor docs** | retrieved 2026-10-25 |
| Retail agentic brokers expose model-held place-order tools; Cockpit must not copy that | [Robinhood: Trading with your agent](https://robinhood.com/us/en/support/articles/trading-with-your-agent/) **vendor copy** | retrieved 2026-10-25 |
| Real-capital prediction-market agents can lose money (prior, not a forecast) | Prediction Arena [arXiv:2604.07355](https://arxiv.org/html/2604.07355) **paper result** | cited in frontier map 2026-10-25 |

Frontier map (Grok Bot, preparing research for Lucas; not a decision): attached
`frontier-map-cockpit.md` and canvas `frontier.canvas.tsx`, written 2026-10-25,
CP0 parent plus conflicts table. Publishing that brief did not mark this
amendment decided.

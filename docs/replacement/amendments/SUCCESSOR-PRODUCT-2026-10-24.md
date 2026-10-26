# Proposed amendment: Cockpit successor product and economic contract

**Owner direction:** recorded in CSL-212. This 2026-10-24 proposal is filed
under [CSL-511](https://linear.app/csl-laboratories/issue/CSL-511); umbrella
[CSL-493](https://linear.app/csl-laboratories/issue/CSL-493).
**Status:** proposed amendment (2026-10-24); this amendment is Lucas-authored,
so Lucas's exact-digest receipt accepts it. Exact-digest acceptance remains a
separate human decision.

> **Superseded in part by [SUCCESSOR-PRODUCT-2026-10-26.md](SUCCESSOR-PRODUCT-2026-10-26.md)** (v2, proposed; accepted only by an exact-digest receipt from Lucas). This v1 file is otherwise unchanged. The `program.json` pins stay on the 2026-10-16 accepted bytes until that receipt and a reviewed promotion.

This file is a **proposed amendment**. It does not replace the last accepted
bytes, which remain `docs/replacement/SUCCESSOR-PRODUCT.md`
at SHA-256
`6e5f0d0cad102077fd058aac4970ac0eb31fd6f6e952f948c43edaa9fe0c919e`, bound by
CSL-212 comment `25c50b43-6de4-4f6d-9652-47a649ccc5ae` and
`docs/replacement/program.json`. This path is a versioned proposal only. A new
exact-digest human receipt over this file's bytes, then a reviewed promotion
that updates the pins, is required before these words are governing. Lucas's
2026-10-24 answers win over the 2026-10-16 wording where they conflict;
unchanged clauses are retained.

Lucas's answers were given in his private chat with Grok Bot Liaison on
2026-10-24 (times PT) and are not in Linear comments. Claims below that rest on
those answers are **relayed by Grok Bot Liaison from Lucas's 2026-10-24 chat
answers (times PT)**. Checkable pointers:
[CSL-511](https://linear.app/csl-laboratories/issue/CSL-511),
umbrella [CSL-493](https://linear.app/csl-laboratories/issue/CSL-493), and the
frontier Page
<https://cursor.com/canvas/store-94f1dcae-bdc6-4db1-ac26-f52f26b24460/551b01dc-6417-49dd-842c-23cf35afff08>.
This comment is not an acceptance receipt.

## Research and open questions

The Cockpit spec is **dynamic**: it changes with the issues and the research
that is published. (Relayed by Grok Bot Liaison from Lucas's 2026-10-24 chat
answers, 11:57 PM PT.) Research for this amendment lives in the vault, not in
this repository:

- repository: `CSL-Laboratories/csl-research`
- commit: `250fdbbe2964e4889096a487e2dfb6d7794046a6`
- path: `submissions/20261025-cockpit-csl511-cp0-successor-research-01/`
- claim: CP0 successor-product research (Lucas 2026-10-24 answers and the
  parent register) is recorded as this vault submission, not as cockpit bytes.
- limitation: candidate evidence only; vault PR
  https://github.com/CSL-Laboratories/csl-research/pull/41 is pending review
  and is not human product acceptance.

Samir and Dan (or their agents, attributed) may change the product spec as
research is synthesized. A spec change they author is accepted when a founder
other than the author (Lucas, Samir `samir-shah-ahmed`, or Dan/Chaandan
`CDanTheMan`) approves that versioned amendment PR; that approval counts as
acceptance. (Relayed by Grok Bot Liaison from Lucas's 2026-10-24 chat
answers, 11:58 PM PT.) That other-founder-approval path is only proposed; it
applies only to future Samir/Dan-authored amendments, and it takes effect
only once it is wired into `program.json`,
`tools/check_replacement_program.py`, and `AGENTS.md`. This Lucas-authored
amendment is accepted only by Lucas's exact-digest receipt. A reviewed
promotion then moves the pins.

## Product outcome

Build an **open-source research instrument** that operators can test. By
2026-11-11 it investigates functionality, the best model combinations for trading
and yield, and which skills or strategies optimize yield. Results are published
to market the software. The window ends in a **verdict on whether this category
of software succeeds or fails**. Testing is closed (private benchmarks and secret
variables); the software is open. (Relayed by Grok Bot Liaison from Lucas's
2026-10-24 chat answers, 10:44 PT: research-instrument outcome and category
verdict.)

It covers **crypto, stocks and prediction markets**. (Relayed by Grok Bot Liaison
from Lucas's 2026-10-24 chat answers, 10:25 PT: asset scope includes crypto.)
The first coherent event-market slice remains **Kalshi demo**; that still fits.
(Relayed by Grok Bot Liaison from Lucas's 2026-10-24 chat answers: Kalshi demo
first is kept; Kalshi demo environment docs, retrieved 2026-10-25.) Polymarket US
Retail, legacy live paths and tax calculation code are **capability to carry
forward**, not new Nov 11 invention. (Relayed by Grok Bot Liaison from Lucas's
2026-10-24 chat answers, 10:30 PT: carry-forward capability, not new invention.)

The instrument measures risk-adjusted net yield subject to explicit safety,
compliance, liquidity, drawdown, uncertainty, reliability, latency, cost and
capital constraints. It does not promise alpha. Empty, negative, rejected and
inconclusive results are valid outcomes and must remain visible. Net-yield PASS
is not an 11 Nov gate. Paper dashboard arithmetic for this window: **$2,000
paper versus SPY plus cash**. (Relayed by Grok Bot Liaison from Lucas's
2026-10-24 chat answers: paper-dashboard base of $2,000 versus SPY plus cash.
This is a separate answer from tax treatment.) The "~$210/mo pays for itself"
narrative is dropped. (Relayed by Grok Bot Liaison from Lucas's 2026-10-24 chat
answers: drop the $210/mo pays-for-itself narrative.)

The first stock, crypto and event-market universes must be selected by accepted
child contracts (or by measured access under later child work) rather than
inferred from historical implementation. Lean toward **no paid data**. Lucas may
buy an **optional Alpaca key** if it helps yields; that is his purchase, not CSL
pay-per-token spend. (Relayed by Grok Bot Liaison from Lucas's 2026-10-24 chat
answers: prefer unpaid data; optional Alpaca key is Lucas's purchase.)

## Required product shape

- A point-in-time evidence and replay plane retains raw inputs separately from
  summaries, with source identity, retrieval time, transformations and provenance.
  Applicable collection, licensing, retention, redistribution, confidentiality and
  account terms must be established before authenticated or continuing acquisition;
  demo or read-only access is not blanket data permission. Operator and research
  data belonging to the three founders stays **off GitHub**, in a private
  three-person collaboration system. (Relayed by Grok Bot Liaison from Lucas's
  2026-10-24 chat answers: founder data stays off GitHub.)
- Instrument, venue, portfolio, collateral, resolution, cash-flow, fee and
  accounting semantics are explicit and reconcile across restarts and external
  truth. **Tax treatment is out of Nov 11 scope**; legacy tax code is
  carry-forward source only, not a gate. (Relayed by Grok Bot Liaison from
  Lucas's 2026-10-24 chat answers, 10:49 PT: taxes out of Nov 11 scope. This is
  a separate answer from the $2,000 paper-dashboard base.)
- Strategy research compares explicit baselines on frozen populations, separates
  tuning from evaluation, and accounts for costs, uncertainty and capital use. It
  investigates which skills or strategies actually optimize yield, including
  claimed yields on public social posts, without treating unreproduced claims as
  evidence. (Relayed by Grok Bot Liaison from Lucas's 2026-10-24 chat answers:
  yield research includes claimed public strategies, reproduced or not counted.)
- Decision routes are provider-neutral experiments. Pairings **sync to the
  user's preference with any combination**. BYOK keys live in the **OS keyring**.
  First providers: **OpenRouter, OpenCode Go and OpenCode Zen**; more
  compatibility ships later. CSL adds **no pay-per-token keys of its own**.
  (Relayed by Grok Bot Liaison from Lucas's 2026-10-24 chat answers, 10:30 PT and
  11:19 PM PT: preference-synced pairings, OS-keyring BYOK, named first
  providers, no CSL pay-per-token keys.) Installed-route qualification still
  earns roles; no provider name is an architecture decision or authority grant.
  A provider response, typed decision or confidence value is an input, never
  permission.
- Authority is expressed as audited compliance profiles. Proposal-only,
  model-assisted and bounded direct authorization remain distinct profiles, not a
  bypass switch. **Live trading in a policy-compliant form is a research
  question, not decided.** Turning live on needs Lucas's explicit go. (Relayed
  by Grok Bot Liaison from Lucas's 2026-10-24 chat answers: live trading is
  research, not an activation.) The 2026-10-16 candidate eventual first-live
  profile (bounded direct authorization after separately accepted eligibility,
  replay, simulation, demo or paper, shadow, limited-live, recovery and
  compliance gates) is not activated by this amendment.
  The completed authority research
  (`docs/replacement/AUTHORITY-COMPLIANCE-RESEARCH.md`) still
  makes A proposal-only the reference qualification profile and C bounded direct
  authorization an owner-directed candidate future target pending a
  human-authored exact-digest receipt. All profiles share one independent
  enforcement and evidence layer. Profile C is not authorized by this contract
  and cannot create a second order path: any future implementation still uses
  the single risk-pass, mode, approval and `OrderGateway` path unless those
  binding invariants are separately amended and reviewed.
- A **GUI on Linux, Windows and macOS** plus a scriptable **CLI** use one
  versioned application/service API and the same authority checks. Linux-first
  for the sprint; all three OS GUIs are in scope. A TUI is optional and must be
  justified by measured operator value. No surface owns a privileged order path.
  (Relayed by Grok Bot Liaison from Lucas's 2026-10-24 chat answers, 10:30 PT:
  GUI on three operating systems plus CLI; Linux-first for the sprint.)
  **No Discord integration at all.** The product is standalone software needing
  only API keys, servers, subscriptions and brokers; engine and infrastructure
  run on our own code. (Relayed by Grok Bot Liaison from Lucas's 2026-10-24 chat
  answers, 10:44 PT: no Discord; standalone software.)
- An **operator MCP** may inspect and stop. It must not place orders or expose
  broker tools to a model. A tool named like `place_order` / `place_equity_order`
  fails the candidate. (Relayed by Grok Bot Liaison from Lucas's 2026-10-24 chat
  answers, 10:30 PT: operator MCP is inspect/stop only; contrast Robinhood
  Agentic Trading MCP vendor copy, retrieved 2026-10-25.)
- The product is **agent-installable and agent-operable**: a coding agent can
  complete setup and drive inspect/stop without the user designing the install.
  Config syncs with the user's existing providers rather than minting CSL-owned
  secrets. (Relayed by Grok Bot Liaison from Lucas's 2026-10-24 chat answers,
  10:30 PT: seven product bars, including agent install/operate and provider
  config sync.)
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
recovery or evaluation. (Relayed by Grok Bot Liaison from Lucas's 2026-10-24
chat answers, 10:30 PT: seven bars are the 11 Nov completeness test.)

The schedule is a prioritization constraint, not evidence of safety,
profitability, legal eligibility, release readiness or live authority. Lower
maturity is an honest result. After 11 Nov, stretch is a **sealed forward-test
window**, not a rewrite. (Relayed by Grok Bot Liaison from Lucas's 2026-10-24
chat answers: post-11-Nov stretch is a sealed forward test.)

**Not an 11 Nov gate:** SWE-bench or other benchmaxxing; net-yield PASS; live
PASS; language rewrite; tax calculations; Discord; CSL-owned pay-per-token keys.
(Relayed by Grok Bot Liaison from Lucas's 2026-10-24 chat answers: those items
are explicitly not 11 Nov gates.)

## Lucas's seven product bars (11 Nov)

A candidate is 11 Nov-complete only if it hits all seven, or names the exact
deferral. (Relayed by Grok Bot Liaison from Lucas's 2026-10-24 chat answers,
10:30 PT.)

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
their own personal account. (Relayed by Grok Bot Liaison from Lucas's 2026-10-24
chat answers: first users are the three founders as US individuals.) Team,
institutional, multi-tenant and delegated-role workflows remain deferred.
Extension points may be documented, but first-release code and data models must
not carry speculative team machinery. Our data stays off GitHub; the three share
a private collaboration system. (Relayed by Grok Bot Liaison from Lucas's
2026-10-24 chat answers: private three-person collaboration, data off GitHub.)

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

This file is **not accepted** until Lucas posts an exact-digest human receipt
over it. This amendment is Lucas-authored, so that receipt is the acceptance
path. The other-founder-approval path is only proposed; it applies only to
future Samir/Dan-authored amendments, and it takes effect only once it is
wired into `program.json`, `tools/check_replacement_program.py`, and
`AGENTS.md`. `program.json` and the replacement-program digest gate must keep
pointing at the 2026-10-16 accepted bytes of
`docs/replacement/SUCCESSOR-PRODUCT.md` until that
receipt exists and a reviewed promotion updates the pins. Updating those pins
without a human receipt would falsely claim acceptance.

## Research basis (candidate-only)

Vault record (candidate evidence; not human product acceptance):

- repository: `CSL-Laboratories/csl-research`
- commit: `250fdbbe2964e4889096a487e2dfb6d7794046a6`
- path: `submissions/20261025-cockpit-csl511-cp0-successor-research-01/`
- claim: CP0 successor-product research (Lucas 2026-10-24 answers and the
  parent register) is recorded as this vault submission, not as cockpit bytes.
- limitation: candidate evidence only; vault PR
  https://github.com/CSL-Laboratories/csl-research/pull/41 is pending review.

Already-accepted CSL-212 digest receipt `25c50b43-6de4-4f6d-9652-47a649ccc5ae`
still pins `docs/replacement/SUCCESSOR-PRODUCT.md`. Lucas's product answers
remain **relayed by Grok Bot Liaison from Lucas's 2026-10-24 chat answers
(times PT)**; they are not Linear comments. Pointers:
[CSL-511](https://linear.app/csl-laboratories/issue/CSL-511),
[CSL-493](https://linear.app/csl-laboratories/issue/CSL-493),
frontier Page
<https://cursor.com/canvas/store-94f1dcae-bdc6-4db1-ac26-f52f26b24460/551b01dc-6417-49dd-842c-23cf35afff08>.
The frontier Page is Grok Bot preparing research for Lucas; it is not a
decision.

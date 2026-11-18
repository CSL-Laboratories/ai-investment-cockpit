<!-- o1-candidate: maturity=researched qualification_claimed=false contract_frozen_claimed=false live_authority_granted=false -->

# O1 candidate: successor product and economic contract

**Issue:** [CSL-213](https://linear.app/csl-laboratories/issue/CSL-213)
**Parent:** [CSL-212](https://linear.app/csl-laboratories/issue/CSL-212)
**Maturity claimed:** `researched`
**Not claimed:** `human_decided`, `contract_frozen`, `prototyped`, `implemented`, `provider_free_verified`, `demo_or_paper_qualified`, `shadow_qualified`, `limited_live_qualified`, `release_qualified`, or any PASS, qualification, profitability, or live authority.
**Registry:** `docs/replacement/program.json` stays `pending_outcome_artifact` for O1. This packet does not edit it.

This is a researched candidate. Individual decision rows below record human comments where those comments exist. Row status is not outcome maturity. ISSUE-CONTRACT requires a valid decision receipt to bind an exact proposal or contract digest. Samir's comments select structure and do not name a digest of these files, so they are not an exact-digest acceptance of this packet.

## Exact candidate command

The outcome completion gate is still unchecked. CSL-213 and `program.json` require an exact-candidate runner, retained oracle, and negative controls before O1 can claim completion. This packet is the document candidate only. It does not check in that runner.

The registry command below checks the accepted CSL-212 registry. It does not complete O1, and it does not read this directory:

```text
python -X utf8 -B tools/check_replacement_program.py
```

Exit 0 means the accepted registry still matches its receipts and integrity baseline. The stdout line `replacement-program: PASS` is that registry result. It is not an O1 qualification.

`digest-candidate.json` is the byte identity of this packet, excluding itself. A later human receipt on CSL-213 can bind that file. This author run is not the independent non-implementer rerun.

How the digest is computed:

1. Each `files[].sha256` is the SHA-256 of that file's raw bytes. Paths are relative to `docs/replacement/outcomes/O1`.
2. `files_bundle_sha256` is the SHA-256 of the UTF-8 bytes of the `files` array, serialized as JSON with sorted keys inside each entry, no whitespace, and the array kept in its listed order: Python `json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.

To reproduce both from the repo root (it prints the bundle hash, then `ok` if every file hash matches):

```
python3 -c "import json,hashlib,pathlib;b=pathlib.Path('docs/replacement/outcomes/O1');d=json.loads((b/'digest-candidate.json').read_text('utf-8'));print(hashlib.sha256(json.dumps(d['files'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')).hexdigest());print('ok' if all(hashlib.sha256((b/f['path']).read_bytes()).hexdigest()==f['sha256'] for f in d['files']) else 'MISMATCH')"
```

The first line must equal `files_bundle_sha256`.

## What this candidate consumes

O1 has no outcome dependencies. It consumes the accepted CSL-212 documents at the digests in `program.json`:

| Document | SHA-256 |
| --- | --- |
| `docs/replacement/SUCCESSOR-PRODUCT.md` | `6e5f0d0cad102077fd058aac4970ac0eb31fd6f6e952f948c43edaa9fe0c919e` |
| `docs/replacement/ISSUE-CONTRACT.md` | `f1fb50dab4b166ca476698fc3974d6a1c2003b16df1d89ff8930060ad04b5094` |
| `docs/replacement/MONTH-PROGRAM.md` | `f28b75244a4eef5e9d3095349d6088bcd08e09547ab8e0fbc03ba52a119f3001` |
| `docs/replacement/SCOPE-AUDIT.md` | `d23dcc769e743d199ae4142f0e20ad5feebfe310c71d9f57fdd9d647c230db8c` |

Authoritative clause: `docs/replacement/MONTH-PROGRAM.md#o1`. Acceptance comment: `25c50b43-6de4-4f6d-9652-47a649ccc5ae` (Lucas Guerra, 2026-10-16T05:45:25.133Z). Authority research `trun_db705696fec649d8ad991d8f8f7b7813` is input only, digest `b023b4d7f12e6b764dd466ac61fb0de61e7ec2962670201ab98bd33fd45008d6`.

Open O2–O5 and O8 packets, and the merged O7 candidate, bind that month-program fragment. They do not bind this directory. A later packet that wants a clause from these files needs its own versioned amendment and receipt.

## Issue-contract fields

| Field | Where it is recorded |
| --- | --- |
| Parent and authoritative clause | CSL-212, `MONTH-PROGRAM.md#o1` |
| Current state | Drafted against `eb92d76c089dcc2308f06edfba5a1dce074dbe7a`. Issue baseline remains `73ab78d9d69494c757cec875855121b1d29320a4`. Evidence class of this packet: researched. |
| Intended outcome | First user, markets, measurable value, month slice, exclusions, and evidence semantics below. |
| Non-goals | `non-goals.json` |
| Inherited decisions and unresolved rows | This file and `unresolved-decisions.json` |
| Evidence, sources, limits | Source labels below. No new Deep Research. |
| Research budget and stop | `one_bounded_reconciliation_pass`, `human_gated`. Stop when this candidate, the matrix, and the owner queue are reviewable. |
| Context7 | No library, framework, broker, or provider is selected, so no library pin applies. |
| Dependencies | None. |
| Authority boundary and discretion | Nine invariants and paper-first runtime remain binding. Discretion `human_gated`. |
| Falsifiers and invalidation | Falsifiers below. A row changes only through a later human comment. Revert this packet to roll it back. |
| Deliverables and completion gate | Contract, capability matrix, non-goal ledger, unknown ledger, decision ledger, digest candidate. Completion runner remains absent. Gate status `pending_outcome_artifact`. |
| Independent verification | A non-implementer reruns the registry command, recomputes `digest-candidate.json`, and compares any later decision with the human Linear comment. Self-verification is not sufficient. |
| Promotion | Claimed: `researched`. Higher tiers are not claimed. |

## First user

One United States individual using a personal account. Team, institutional, multi-tenant, and delegated-role workflows stay deferred.

Source: accepted `SUCCESSOR-PRODUCT.md`, "User and scope boundaries", receipt `25c50b43-6de4-4f6d-9652-47a649ccc5ae`. CSL-213 records the same inherited decision. This packet does not re-decide it.

## Markets

Stocks and regulated prediction markets stay in scope. The first event-market slice remains Kalshi demo. Polymarket US Retail remains a second adapter only after that slice is stable. Equity options stay deferred (D8).

The first stock slice is US-listed equities and ETFs, cash-funded, long-only, on the existing paper path (D9). That slice names an instrument population and a funding constraint. It does not select a broker, a production venue, a capital amount, or a named strategy family.

Source for the inherited market order: accepted `SUCCESSOR-PRODUCT.md`, same receipt. Source for D8 and D9: Samir, CSL-213 comment `cda1f329-b9d2-4fcc-823e-a12aa33d4237` (2026-10-18T08:57:35.660Z).

## Measurable value

The product optimizes measured risk-adjusted net yield under explicit safety, compliance, liquidity, drawdown, uncertainty, reliability, latency, cost, and capital constraints. It does not promise alpha. Empty, negative, rejected, and inconclusive results stay visible.

Samir selected the primary metric (D1): net yield versus a matched-cash baseline after transaction, latency, data, and model costs, reported per capital-day. The CSL-96 cost-coverage ratio is secondary. Gross P&L, win rate, and cost coverage as the primary metric are rejected.

Benchmarks (D2): matched cash, plus a passive equity index total-return reference for the stock leg. Event-market legs use matched cash only. Which index is open (`D2-index`). Per-strategy benchmarks stay out because per-strategy scorecards are on hold.

Horizon (D3): declared short and long buckets. Each bucket has its own population, split, and embargo, frozen before any results.

Loss and drawdown (D5): a fraction of declared notional. The fraction stays `owner_unset`. Promotion of an economic claim stays blocked while it is unset. No numeric risk limit is selected. Numeric risk limits remain in `app/risk/`, which this packet does not touch.

Statistical bars (D6): deflated Sharpe, probability of backtest overfitting, the uncertainty estimator, and embargo length stay `owner_unset`. Literature conventions are a candidate list only and are not selected bars.

Taxes (D7): pre-tax net yield is primary. Tax impact is `unknown`.

Capital base (D4) is open for Lucas. No notional amount appears in this packet.

Machine-readable copy: `economic-metrics.json`.

## Month slice

The accepted successor product names a thin vertical slice: retained market evidence, replayable strategy evaluation, explicit accounting, one decision-route comparison, Kalshi demo interaction through current-safe boundaries, and useful GUI and CLI inspection. That list is a prioritization constraint. It is not evidence of safety, profitability, legal eligibility, release readiness, or live authority.

`capability-matrix.json` lists those journeys and the later or optional cells. Every row has `r1_status` `undecided`. D11, the R1 boundary, is open for Lucas. CSL-237 consumes a later decision; this packet does not make the cut.

Lucas's comment `d358e47e-df1a-400d-869f-1368d9b3a8c7` (2026-10-16) says Oct 20 is the economics decision checkpoint, not successor-contract acceptance. Devin relays `4d7258dc-64bd-4cb9-a698-12b0979fe23e` and `5c8ffad7-f11f-4e60-b471-4ab96728777d` record Oct 20 integrated behavior, Oct 30 freeze, and Nov 12 qualification as planning handoff targets. Those relays are not human-authored contract acceptances. Devin relay `d84e36df-9d7d-4995-9aaa-387b7e9da9fa` records Lucas's direction to aim high, and says the small demo journey is not the product ceiling. This matrix keeps the named journeys visible and does not shrink them into a release claim.

## Capability matrix

`capability-matrix.json` has one row per journey per capability cell: stock, Kalshi demo, later Polymarket US Retail, provider, interface, and platform. A cell does not unlock another cell.

Each row carries the D13 live carry-over field. The field states that a future order would reach bounded-direct authority only through risk-pass, then mode, then approval, then `OrderGateway`. Paper is interim. The field authorizes nothing. O6 owns the authority design. D10 leaves the Kalshi demo credential path versus replay or fixtures open, so the Kalshi interaction row does not select a credential.

## Non-goals

`non-goals.json` records CSL-213's explicit non-goals and the later exclusions. In particular: no language, store, broker, provider, numeric risk limit, production venue, or capital selection; no claim that the month schedule proves safety or profitability; no team or multi-tenant scope; equity options deferred pending a versioned amendment of `SUCCESSOR-PRODUCT.md`; per-strategy scorecards on hold.

## Unknowns

`unknowns.json` keeps legal, venue, provider, adapter, tax, entitlement, capital, index, Kalshi credential path, R1 membership, and real-account import unresolved. Authority research does not promote them.

## Decision ledger

`unresolved-decisions.json` is the machine-readable queue. Owner of O1, as stated by Samir: Samir. At drafting, CSL-213's Linear assignee field was empty and the status was Triage. This packet records the comment and does not change Linear.

Samir's ownership comment `d2e957f0-e4a0-49d6-8bbd-95d03350209d` (2026-10-18T08:57:19.771Z), in reply to Lucas `d85b8c7d-6803-41d4-8c4c-bbeb13bc0f6e`, sets the research budget to `one_bounded_reconciliation_pass` and the discretion class to `human_gated`. Tier-1 rows stay with Lucas.

| Id | Status | Owner | Selected | Source |
| --- | --- | --- | --- | --- |
| D0 | `human_decided` | Samir | Samir owns O1. Budget `one_bounded_reconciliation_pass`. Discretion `human_gated`. | `d2e957f0`, `cda1f329` |
| D1 | `human_decided` | Samir | Net yield vs matched cash after transaction, latency, data, and model costs, per capital-day. CSL-96 cost coverage is secondary. | `cda1f329` |
| D2 | `human_decided` | Samir | Matched cash, plus a passive equity index total-return reference on the stock leg. Event legs use matched cash only. | `cda1f329` |
| D2-index | `open` | any founder | None. Which index is a later sub-decision. | `cda1f329` |
| D3 | `human_decided` | Samir | Hybrid short and long buckets, each frozen before results. | `cda1f329` |
| D4 | `open` | Lucas | None. Tier-1 capital. Suggested default, not selected: a declared notional parameter left `owner_unset`. | left open by `cda1f329` |
| D5 | `human_decided` | Samir | Fraction of declared notional. Value `owner_unset`. Promotion blocked while unset. | `cda1f329` |
| D6 | `human_decided` | Samir | DSR, PBO, uncertainty estimator, and embargo length stay `owner_unset`. Literature is a candidate list only. | `cda1f329` |
| D7 | `human_decided` | Samir | Pre-tax net yield is primary. Tax impact is `unknown`. | `cda1f329` |
| D8 | `human_decided` | Samir | Equity options deferred, pending a versioned amendment. | `cda1f329` |
| D9 | `human_decided` | Samir | US-listed equities and ETFs, cash-funded, long-only, existing paper path. | `cda1f329` |
| D10 | `open` | Lucas | None. Tier-1 credentials. Kalshi demo credential path versus replay or fixtures. | left open by `cda1f329` |
| D11 | `open` | Lucas | None. Tier-1 release scope. R1 boundary. Suggested default, not selected: list every journey and mark each in-R1, unmet, blocked, or no-claim. | left open by `cda1f329` |
| D12 | `human_decided` | Samir | ISSUE-CONTRACT maturity ladder and evidence classes, per capability cell. | `cda1f329` |
| D13 | `human_decided` | Samir | Live carry-over is a required field only. It authorizes nothing. | `cda1f329` |
| D2b | `open` | Lucas | None. Real-account import remains open. This id is the relay's name, not a child of D2. | attributed relay `509ec246` |

Revisit trigger for D0–D13, from Samir's comment: the O1 packet review, or a downstream outcome (O3 or O4) showing a decision cannot be met.

Comment `509ec246-73de-487c-a305-163970e69da3` is an attributed relay. Its body says Codex recorded Lucas Guerra's confirmation and that Lucas did not personally type that comment. It confirms the D2a development-versus-operating split and leaves A4 product choices, D2b, and the Kalshi credential rescope open. It does not accept this artifact. Devin comments on CSL-213, including `eab2d8c3` (decider rule) and `f79ca7d5` (owner proposal), are agent text. They are not receipts.

Inherited, and not re-decided here: full replacement; US individual and personal account; stocks plus prediction markets; Kalshi demo first; Polymarket US Retail second only if stable; GUI plus scriptable CLI; TUI optional; paper, demo, and shadow as qualification stages; bounded direct authorization only after separate gates; no team or multi-tenant scope this month. Source: CSL-213 issue text and the accepted CSL-212 documents.

## Evidence and release semantics

D12 adopts the ISSUE-CONTRACT maturity ladder and the nine evidence classes as they are, applied per capability cell. This packet claims `researched` only. Labels, status, agent comments, tests, PR approval, and merge are not receipts.

Profile A remains the qualification reference. Profile C remains a candidate future target. A provider response is not permission. The sole order route remains risk-pass, then mode, then approval, then `OrderGateway`.

## Falsifiers

- A historical or proposed feature is called accepted scope without a human decision that says so.
- Yield is optimized without costs, risk, uncertainty, or capital usage.
- The month target, a checkpoint date, or a merged candidate is treated as release evidence.
- A numeric threshold, capital amount, broker, provider, production venue, or risk limit appears as if this packet selected it.
- D4, D10, or D11 is marked decided.
- One capability cell's evidence is treated as a pass for another cell.
- The D13 field is read as live authority.

## Authority boundary

Agents may draft this reversible documentation inside the accepted CSL-212 contract. This packet grants no credentials, capital, live trading, merge, or weakening of the nine invariants. `app/risk/`, `OrderGateway`, and `.github/` are unchanged. Paper-first remains in force. Auto decision routing stays pinned as currently documented. Secrets stay in the OS credential store.

## Evidence limits

The matrix rows are classifications, not measured journeys. No market data, demo session, or paper run is attached. Economic thresholds are `owner_unset` or open. Authority research is not legal advice, eligibility, provider permission, or economic qualification.

Layout follows the researched O7 packet on main and the open O2, O3, O4, O5, and O8 packets. Those packets are conventions for shape. They are not accepted inputs to O1.

## Smallest next experiments

No new Deep Research. The next human steps are Lucas on D4, D10, and D11; a founder naming the equity index before any stock-leg index comparison; and a digest-bound CSL-213 comment before any row is treated as an exact-digest acceptance. Downstream packets keep consuming `MONTH-PROGRAM.md#o1` until that receipt exists.

## Files

| Path | Role |
| --- | --- |
| `CONTRACT.md` | This candidate. |
| `capability-matrix.json` | Journey rows, R1 left undecided, live carry-over field on every row. |
| `economic-metrics.json` | D1–D7 measures. Numeric bars unset. D4 capital open. |
| `non-goals.json` | Non-goal ledger. |
| `unknowns.json` | Unknown ledger. |
| `unresolved-decisions.json` | D0–D13, `D2-index`, and `D2b`, with source labels. |
| `gates.json` | Packet-level non-claims. |
| `digest-candidate.json` | SHA-256 of the files above, excluding itself, plus the bundle hash (recipe under the registry command above). |

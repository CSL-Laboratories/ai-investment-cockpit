---
title: Remaining work — session plan for the W-series tail (sequencing, drift repair, paste-ready prompts)
type: implementation-plan
status: active
last-verified: 2026-07-16
related: [production-work-packages, production-playbook, security-hardening-backlog, path-to-production, build-scaffold-ultracode, ultracode-build-playbook, enhancements-and-additions]
---

# Finishing the AI Investment Cockpit — session plan

## Context

WP0–WP9 is complete. What remains is the **W-series**: production property-closure
packages, one per session, each gated by a *fresh adversarial red-team* rather than a green
suite. W10 (audit spine) and W11 (capability boundary) are done; W12 is done but unmerged.

**Every remaining package already has a paste-ready §6 kickoff** in
`docs/production-playbook.md` (W13 `:715`, W14 `:738`, W15 `:757`, W16 `:783`, W17 `:813`,
W18 `:842`, W19 `:875`, W20 `:899`). **So this plan is not prompt-authoring — it is
sequencing, plus repairing drift that would silently break those prompts.**

Three things prompted it:

1. **Sequencing.** W13/W14/W15/W16 are all unblocked *right now*, and the two longest-lead
   items are operator provisioning tasks gating W17 that could have started weeks ago.
2. **A second operator.** Lucas's friend wants to run the cockpit on Codex.
3. **EDGAR breadth.** W20 proposes building EDGAR ingest that `edgar-morning-brew` already
   ships, proven.

**Outcome:** an ordered session list carrying the cockpit to paper-complete, then
live-enabled, without re-treading closed properties or weakening a gated invariant.

> **Standing rule, every session:** never imply SEC-11 is closed (mechanism done at
> `ea7e0fc`; **property OPEN** pending the Discord witness). Agents do **not** push —
> Lucas merges via GitHub Desktop.

---

## Part 1 — Drift repairs (do these FIRST; they break sessions silently)

Session 0 below applies all of these. Each was verified this session.

### 1.1 Stale backlog line-refs — systemic, **+180 to +220 lines**

`security-hardening-backlog.md` is now 978 lines; the W10/W11/W12 closure notes pushed every
entry down. **Every** kickoff's backlog refs are wrong:

| Entry | Cited | Actual | Entry | Cited | Actual |
|---|---|---|---|---|---|
| SEC-03 | 142-182 | **173** | SEC-19 | 567-587 | **790** |
| SEC-05 | 201-216 | **324** | SEC-20 | 680-698 | **903** |
| SEC-06 | 218-232 | **363** | SEC-21 | 589-625 | **812** |
| SEC-09 | 315-347 | **471** | SEC-22 | 627-642 | **850** |
| SEC-15 | 489-508 | **712** | SEC-24 | 663-678 | **886** |
| SEC-16 | 510-526 | **733** | OPS-01 | 707-712 | row 101 / **930** |
| SEC-17 | 528-539 | **751** | OPS-03 | 719-720 | row 103 / **942** |

Not cosmetic: a verify agent this session **read the wrong entry** (line 528 now lands inside
SEC-11's WP5 sharpening). Cheapest durable fix: add one line to each kickoff —
*"backlog line numbers have drifted; locate entries by their `### SEC-xx` heading, not by line."*

### 1.2 Stale test baseline — the dangerous one

Every W13–W16 kickoff ACCEPT says *"Full suite green (>=586 passed / 3 skipped)"*
(`playbook:728`, `production-work-packages.md:703`). True lineage: **586** (WP9) → **619**
(W10) → **662** (W11) → **753 passed / 3 skipped, 756 collected** (W12, verified).

A session reading `>=586` as its bar **can ship a tree that lost ~170 regressions and still
declare the mechanical gate green** — playbook anti-pattern #6. Patch the number in every
remaining kickoff to whatever tree it branches from.

### 1.3 The W17 `st=` gap — highest-risk item on the tail

The G11 round-2 carry-forward *"W17 MUST construct the production gateway with `st=`"* appears
at `production-work-packages.md:469-470`, `production-playbook.md:653-654`, and the G11 gate
row `:583` — **and nowhere in the W17 kickoff** (`playbook:813-840`).

`brokers/gateway.py:117` defaults **`st=None`**. So the insecure construction is the *default*
and the *silent* one: omitting `st=` re-opens the forged-ladder HMAC-substitution bypass
**with a green suite and clean ruff**. Patch the W17 kickoff, or inject it as a standing
precondition.

### 1.4 W16 knob-name asymmetry — reproduces the bug W16 exists to fix

`gate.py:62-67` reads six knobs via `getattr`; `config/settings.py` defines **none**. Five map
name-for-name; **`gate_max_drawdown` reads into the dataclass field `max_drawdown_limit`**.

A session that adds `gate_max_drawdown_limit` (mirroring the dataclass) gets **no error, no
test failure**, and the getattr silently keeps `Decimal("0.20")` forever. Exact names:
`gate_min_weeks_lower`, `gate_min_adjudicated`, `gate_min_approval_rate`, **`gate_max_drawdown`**,
`gate_min_weeks_auto_veto`, `gate_max_veto_rate`.

### 1.5 §4 script-numbering collision

Two playbooks number §4 differently, and each kickoff **mixes both without saying so** —
italic headers use production numbering, bodies use build numbering:

| | `production-playbook.md` §4 | `ultracode-build-playbook.md` §4 |
|---|---|---|
| 4a | Audit-spine red-team `:229` | Schema-first fan-out `:116` |
| 4b | **Capability-boundary red-team** `:332` | **Parallel module build (worktree)** `:253` |
| 4c | Loop-until-green `:423` | Adversarial review harness `:325` |
| 4d | Parallel module build (worktree) `:496` | Loop-until-green `:443` |

W13/W15/W16 bodies say *"playbook script 4b"* with **no doc named**. A cold session opening
`production-playbook.md` §4b gets the red-team when the body meant the worktree build.
Disambiguate before pasting.

### 1.6 Other repairs

- **W12 has no `✅ DONE` banner.** W10 (`:615`) and W11 (`:647`) have one. `fce3349` touched
  `production-work-packages.md` + `security-hardening-backlog.md` but **not the playbook**. A
  cold session would re-run a finished package and miss its carry-forwards.
- **W13's `main.py` refs are stale** — kickoff cites *"compose 29-42, run() 45-52"*; actual
  `compose` `:119-144`, `run()` `:147`. Line 29 is now W10's `_build_anchor_store`; 45-52 is
  W11's `run_startup_security_checks`. W13's whole (d) sub-item is factoring `run()`.
- **W13's "literal TODO" doesn't exist.** No TODO token in `app/pyproject.toml`. There *is* a
  comment: *"Pin + hash-lock before install"*. Unpinned deps + absent lockfile are both real.
  Restate as "remove the pin-before-install comment".
- **W14/SEC-17 is mis-scoped as "model-emitted".** `proposals.py:248-250` already reads
  HMAC-verified parquet via `resolve_raw`. The real residual is narrow: the excerpt is
  **field-keyed** (`item.get("headline")`) rather than a byte-offset substring. Mis-scoping
  invites rebuilding verification that ships (anti-pattern #5).
- **W14/SEC-16: `canary_breach` already ships** (`order_state_machine.py:11-19,50,52`;
  `schemas/models/order.py:37`). Real delta = out-of-band registry + interception hook +
  rotation.
- **W20's `{halt,filing}` anchors are stale** — FILE-ANCHOR-THEATER class. `budget.py:158` is
  `try_consume`; frozenset is `:27`, membership test `:234`. `triage_loop.py:46` is
  `_BURST_THRESHOLD = 4`; frozenset `:47`, use `:199`.

---

## Part 2 — Session order

**One W per session.** The human checkpoint between packages is a safety feature, not
overhead (playbook §7.1).

### Session 0 — Baseline + drift repair *(docs only, no property work)*

**Decide the baseline first — this is a real fork:** W12 (`fce3349`) is unmerged; the DAG
lists it in *no* `depends_on` row for W13–W16, so they're startable now. But a session
branching from `main` **silently loses W12's ~90 regressions** (756 → 662), and its "suite
green" gate then measures a tree W17 (which *does* depend on W12) will never see.

> **Trap:** remote `origin/w12-untrusted-mcp-oob-kill-atomic-audit` is a near-name collision
> that points at **`fddcc05` (the W11 merge)**, not W12. Its existence is not evidence W12 is
> pushed.

**Recommended:** merge W12 to main, re-run a fresh G12 round per §7.1, then branch everything
from main.

Then apply Part 1: W12 banner, baseline numbers, backlog-ref note, W17 `st=`, W16 knob names,
script-numbering disambiguation, W13 `main.py` refs, W20 anchors.

**Also commit the red-team scripts.** They live only in ephemeral session scratchpads.
`w10_redteam.js` is **already lost**; `w11_redteam.js` and `w12-g12-redteam.js` survive but are
one temp-cleanup from gone — while the playbook says *"reuse it as the template for every P0
W-gate."* Commit to `tools/redteam/` and repoint §4a/§4b.

### The unblocked mid-tier — W13, W14, W15, W16

All four depend only on W10 (done). **They are not interchangeable in dependency strength:**

- **W14's edge is weakest** — W10 is *"GATE-ORDERING only, and ONLY for SEC-16 + SEC-19; the
  five live-residual deliverables can land INDEPENDENTLY"* (`:810-812`). 5 of 7 lanes have no
  real W10 dep. **Most parallelizable** (7 disjoint files → 4d worktree fan-out).
- **W16's edge is HARD** (`:986-989`) and **its gate is theater without the witness** (`:984`:
  *"on the InMemory/File anchor it is theater"*).

> **File contention:** W14 (lane f), W15, and W16(c) **all touch `config/settings.py`**. Do not
> run them concurrently in one tree without a barrier merge.

| # | Session | Why here | Gate |
|---|---|---|---|
| 1 | **W20** | Cheapest high-value: no hard dep, Inv-1 untouched, **medium** not xhigh. Now much smaller (Part 3). | G20 medium |
| 2 | **W14** | 7 disjoint lanes; 4d fan-out; unblocks W18/W19 via declared edges | G14 xhigh |
| 3 | **W13** | Supply chain + OPS-01/03; operationalizes `run()` | G13 |
| 4 | **W15** | Heaviest mid-tier; **pair with the operator's SEC-05 account work** — W15/OPS-06 owns host provisioning | G15 |
| 5 | **W16** | Run **after** the witness lands, else the gate proof is theater | G16 |

### The tail — W17 → W18 → W19

Thin on build, heavy on wire-and-prove, and **gated on both** the code set *and* the two
operator provisioning acts. W17 needs W10+W11+W12+W13+W15+W16.

- **W17** — live enablement. **Must construct the gateway with `st=`** (Part 1.3).
- **W18** — `auto_veto`. SEC-15 is the open **BUILD**; SEC-01/14/23 are **WIRE+PROVE only**
  (mechanism exists in `bridge/veto.py`; replace the `_TrueCtx` placeholder at `veto.py:262-267`).
- **W19** — `auto_full`.

> **W16, W18, and W19 are structurally un-certifiable until the Discord witness is
> provisioned** (ladder-gate rule, `production-work-packages.md:72-73`).

---

## Part 3 — W20 revised: EDGAR via the brew *(decided: read SQLite read-only)*

**The brew retires ~2/3 of W20.** It already ships (a) EDGAR-direct filings
(`app/edgar/submissions_poll.py`), (b) XBRL fundamentals (`app/edgar/companyfacts.py` +
`xbrl_fact`), **and** the CIK→ticker resolution W20 names as a hard precondition
(`app/edgar/cik_map.py`). It ships **zero FINRA** — so **W20(c) FINRA stays net-new, exactly as
specced**. Split: (a)+(b) collapse to an adapter; (c) unchanged.

**Why not build our own poller:** W20's ACCEPT requires a rate-denied poll *"returns 0, never
bursts"* (`playbook:926`). The brew's `cockpit-port-map.md` calls that exact cockpit behaviour
**"exactly the anti-pattern"** — a throttled EDGAR masquerades as a quiet filing day. The brew
replaced it with `SecFairAccessHalt` (403/429 → halt + alert, zero retries). Cloning
`finnhub_poll.py` would reproduce a known-bad behaviour on the one feed whose silence is
indistinguishable from good news.

**Two premises I gave you were wrong — corrected:**
- **No namespace collision.** The cockpit has **no `app/__init__.py`**; `app/` is a bare source
  root (`pythonpath = ["."]`). It imports `from ingest.normalize`, never `from app.ingest` —
  grep for `^from app\.` returns zero.
- **Inv-8 is not a wall.** It's a lint over three LLM-drafted `BriefDraft` prose fields
  (`compliance_lint.py:15-19`), binding what the *brew emits*, never a consumer. The
  recommended seam never touches prose — the Delta/Citation layer is explicitly LLM-free.

**The seam:** new `app/ingest/edgar_brew.py` with an injectable `fetch(cursor, now) -> list[dict]`
closure — matching the `FinnhubPoller` template W20 already mandates — whose production closure
opens the brew's SQLite **read-only** and joins `filing` + `company` + `delta`. **Zero brew
changes; its 14-day soak is undisturbed.** ~50 lines: a SELECT, a field map, one
`to_market_event` call per row. The seam plugs in **upstream of everything** — `persist_events`,
parquet write-once, audit binding, dedup, untrusted wrapping all come free, unchanged.

**Must-honour details:**
- Emit canonical **`"filing"`**, never `"filing:8-K"` (exact-membership frozenset). Brew stores
  `form_type` separately → map into `raw.form`.
- Add `"sec:edgar"` to `ALLOWED_SOURCES` (`normalize.py:18`) — the single shared-code edit.
- Use **`resolve_all`** (per-citation `ResolveResult`), **not** `claim_gate.evaluate` — the
  latter is an all-or-nothing *brief-level* verdict that collides with the cockpit's Inv-7
  never-drop per-event discipline.
- **SEC-13 boundary — call out in G20:** `content_hash` hashes whatever dict is passed as
  `raw=`, so a brew record binds the chain to *the brew's record*, not the SEC bytes. The chain
  is cockpit `content_hash` → brew Delta/Citation byte-anchor → brew `RawStore` SHA-256 → SEC
  bytes. Real, but **one link longer than W20 assumed, and the added link is in another repo's
  trust domain.**
- Sentiment stays **deferred**. Inv-1 untouched. Data flows **one way only**.
- Operator-set/TBD: SEC fair-access rate + exact User-Agent format (re-verify at build); the
  materiality threshold selecting which brew deltas become events (brew defers tuning to its soak).
- Moving target: **SR-FINRA-2026-012, SEC action date 2026-08-14** (~1 month out) may change
  short-interest cadence — check before W20(c).

---

## Part 4 — The Codex program *(decided: fund it)*

**The blocker is not Invariant 2.** Inv 2 is verbatim: *"`auto_veto`/`auto_full` refuse to arm
if any decision-stage job is configured for a non-Claude runtime (**OpenAI policy constraint —
verified**)"* — grammatically **auto-scoped**, and gated at `auto`. Both enforcement points are
already auto-scoped (`mode_ladder.py:89`, `:244`), and **two regression tests assert Codex is
allowed in review_live** (`test_core.py:147`, `test_g5_acceptance.py:349`). Codex-in-review is
ratified design, not a loophole.

**The blocker is SEC-06 (tool starvation), gated at `live`** — which binds review_live.

**Phase 1 — the spike (no code; it can kill the program).**
1. **Settle the ToS.** *"(OpenAI policy constraint — verified)"* has **no linked text, no date,
   no quoted clause** anywhere in the repo. If it bans automated financial **decisions**, Codex
   proposing trades in review_live violates it *even with a human clicking approve*, and no code
   makes that safe. If it bans automated **execution**, auto-scoping is faithful. **The repo
   cannot settle this.**
2. **Find codex's zero-capability mode.** Claude answers SEC-06 with `--allowedTools none` — an
   **empty capability set**. Codex's `--sandbox read-only` still grants **FS read** — and
   SEC-06's own attack paragraph names `codex exec` and `cat ~/.codex/auth.json` **as the
   attack**. Closing SEC-06 for codex requires a true zero-capability configuration — **or
   proving none exists, and the package dies.**
3. Determine the real **JSONL envelope** and **flag vocabulary** from the installed CLI.

**Phase 2 — W21 (human-gated Codex).** Only if Phase 1 clears.
- **Do not naively delete `runner.py:52`.** `tool_inspector.assert_all_decision_specs_tool_starved`
  derives its **entire** codex refusal by catching that exact `ValueError`
  (`tool_inspector.py:120-125`). Removing it collapses the SEC-06 auto-arm gate from two
  independent checks to one — and the test that would catch it
  (`test_tool_inspector.py:109 test_codex_decision_spec_refused`) is exactly what a naive diff
  deletes as "obsolete". **This is the G11 defect class: expect the fix to introduce it; re-run
  FRESH.**
- **Invert `inspect_argv` to a per-runtime ALLOWLIST** so an unknown flag fails closed.
  "Deny-by-default" is a misnomer to not inherit: the mechanism is deny-by-**enumeration** over
  *Claude's* vocabulary — allow-by-default against an unknown CLI grammar.
- **Build the auto-ceiling — it does not exist.** There is no mode ceiling in `mode_ladder.py`.
  "Hard-capped at human-gated modes" is currently an aspiration, not a control. It must be
  durable, chain-anchored, un-flippable by a config edit or DB writer, fail-closed, and
  red-teamed.
- Codex runtime adapter (JSONL envelope + codex-shaped health detection replacing the
  claude-only `is_error`/`subtype` check at `runner.py:157`).
- Validate `runtime` at the schema edge (`app.py:105`) — today unvalidated, safe only by the
  accident of exact-match semantics.
- Provision the codex bearer under the low-priv account — **collides with SEC-05 activation**,
  already a W17 precondition.
- **A fresh multi-round G21 against a codex decision argv.** The G11 rounds **do not transfer** —
  they red-teamed a vocabulary codex does not speak. This is a first run, not a re-run.

**Phase 3 — Codex in auto.** Structurally last: **the auto rungs don't exist yet even for
Claude** (W18 builds `auto_veto`, W19 `auto_full`). Requires amending Inv 2 → after W19 + a
clean Phase-1 ToS answer.

> The ladder enum is `read_only, paper, review_live, auto_veto, auto_full`
> (`mode_ladder.py:37-39`). **There is no `review_paper`** — I used that name earlier; it does
> not exist.

---

## Part 5 — Operator track: START NOW, in parallel

**Neither is a coding session, both gate W17, and no amount of W13–W16 progress discharges
them.** They are the real long pole.

1. **SEC-11 — Discord `#audit-log` witness.** Four acts: create a private `#audit-log` channel;
   set its ACL to **deny the bot manage-messages** (so the bot cannot erase its own witness);
   set `COCKPIT_AUDIT_LOG_CHANNEL_ID`; put `discord-bot-token` in keyring. Until then
   `anchor_store.latest()` is `None` → **every consult fails closed; auto is unreachable and
   W16's gate proof is theater.**
2. **SEC-05 — low-priv account.** Standard-user Windows account **not** in Administrators;
   per-file NTFS deny-read ACLs on the bearer stores; a `CreateProcessWithLogonW` launcher
   (`transport.py:_logon_launch` raises `NotImplementedError`) + credential. **Pair with W15**
   (OPS-06 owns host provisioning).

**Wall-clock, not code:** the **2-week paper soak** and **≥4-week auto windows**. W16 delivers
the *instruments*, not the history.

**Untracked, dated:** `market_calendar.py` codes NYSE **2025 and 2026 only**. Today is
2026-07-16, so coverage expires in ~5 months and W15's staleness gauge will warn almost
immediately. Fail-safe (falls back to weekday/hours; never reports a real holiday as open), so
WARN not block — but a **2027 refresh is an operator task with a real date** and is tracked
nowhere.

**Before real capital (invariant P-C):** re-verify Anthropic SDK-credit status, Robinhood
Agentic scope/OAuth TTLs, and **per-broker post-PDT house-margin rules** against **fresh primary
sources**. Per-broker margin was **not** re-run on the 2026-07-06 refresh. A green paper soak
never satisfies P-C.

---

## Part 6 — What "done" means

- **Paper-complete** = W13+W14+W15+W16+W20 land, properties closed, suite green. **No real
  capital.** Reachable through coding sessions alone.
- **Live-enabled** = the above **+ both activations + the soak wall-clock + W17 + P-C
  re-verification**. **Not reachable by coding sessions alone.**

Do not mistake the first for the second.

---

## Verification

Per session, per playbook §1 (mechanical is necessary, never sufficient):

1. **Mechanical.** `cd app && python -m pytest -q` — green against **the branched-from
   baseline** (753/3 if from W12, not 586), `ruff` clean, named regressions present.
2. **Adversarial (the actual gate).** Run the red-team against the **un-merged worktree
   branch** — never the main checkout (anti-pattern #8: it reviews an empty tree and falsely
   passes). An absent target = **GATE FAIL** ("target not built"), never a trivial pass.
3. **Re-run FRESH.** New agents, never the fixer certifying their own fix. Converge to
   0 `must_fix`. **One reproducible bypass = the property is OPEN**; vote counts rank
   confidence, they never overrule a single real repro.
4. **Regression.** Pin the repro under a name that preserves the attack.

Template shape by package: one property × N consult sites → §4a pipeline · one property × N
angles → `w11_redteam.js` flat-parallel · N properties → `w12-g12-redteam.js` per-property.

**Expect fixes to introduce defects** — two G11 findings were exactly that, caught only by the
next fresh round.

---

# Part 7 — Paste-ready session prompts

Three prompts are authored below (Session 0, W20-revised, W21 spike) because no kickoff exists
for them. **W13–W19 already have kickoffs** — for those, paste the **patch preamble** (§7.4)
followed by the existing kickoff text.

---

## 7.1 — SESSION 0: baseline + drift repair *(docs/tooling only, no property work)*

```text
ultracode, xhigh. You are running SESSION 0 of the AI Investment Cockpit W-series: BASELINE +
DRIFT REPAIR. This is NOT a property-closure package and has no G-gate. It is docs/tooling
hygiene that every later session depends on. Repo root = cwd; source under app/, docs under docs/.

Do NOT push. Never imply SEC-11 is closed (mechanism done at ea7e0fc; the PROPERTY is OPEN
pending the Discord #audit-log witness). Never weaken an invariant. Ground every claim in a doc
line or a code file:symbol — verify current state against code, do not assume.

READ FIRST, in this order:
  1. docs/production-playbook.md §1 (gate philosophy), §2 (bootstrap read-order), §7.1 (one W
     per session) and §7.2 (the 10 anti-patterns).
  2. docs/production-work-packages.md — the DAG (~lines 267-302) and the W12 entry.
  3. git: `git log --oneline main..w12-untrusted-kill-atomic-audit`, `git branch -a`,
     `git branch --contains fce3349 -a`.
  4. cd app && python -m pytest -q --collect-only | tail -3   (establish the TRUE baseline)

TASK A — BASELINE DECISION (do this first; report before proceeding).
  W12 (fce3349) is DONE but UNMERGED and unpushed. The DAG lists W12 in NO depends_on row for
  W13/W14/W15/W16, so they are startable now — BUT a session branching from `main` silently
  loses W12's ~90 regressions (756 -> 662), and its "suite green" gate then measures a tree that
  W17 (which DOES depend on W12) will never see.
  TRAP: remote `origin/w12-untrusted-mcp-oob-kill-atomic-audit` is a NEAR-NAME COLLISION that
  points at fddcc05 (the W11 merge), NOT at W12. Its existence is not evidence W12 is pushed.
  RECOMMEND merging W12 to main, then branching everything from main. Report the recommendation
  and STOP for the operator's decision before Task B.

TASK B — DRIFT REPAIRS. Author ONLY docs/ + tools/. Do not touch app/ source.
  (a) W12 DONE banner. W10 (playbook:615) and W11 (playbook:647) carry
      "✅ DONE — do NOT re-run this kickoff"; W12 (playbook:687-713) does NOT. fce3349 touched
      production-work-packages.md + security-hardening-backlog.md but NOT the playbook. Add the
      banner in the same shape, carrying W12's corrections forward (notably: SEC-02
      confidentiality is best-effort until the real payload schema at W17).
  (b) BASELINE NUMBERS. Every W13-W16 kickoff ACCEPT says "Full suite green (>=586 passed / 3
      skipped)" (playbook:728; production-work-packages.md:703; W10 Accept :431). TRUE lineage:
      586 (WP9) -> 619 (W10) -> 662 (W11) -> 753 passed / 3 skipped (756 collected) (W12).
      A session reading ">=586" can ship a tree that LOST ~170 regressions and still declare the
      mechanical gate green (anti-pattern #6). Update every remaining kickoff to the real number.
  (c) BACKLOG LINE-REF DRIFT. security-hardening-backlog.md is now 978 lines; the W10/W11/W12
      closure notes pushed every entry down by ~+180 to +220. Verified cited -> ACTUAL:
      SEC-03 142-182 -> 173 | SEC-05 201-216 -> 324 | SEC-06 218-232 -> 363 |
      SEC-09 315-347 -> 471 | SEC-15 489-508 -> 712 | SEC-16 510-526 -> 733 |
      SEC-17 528-539 -> 751 | SEC-19 567-587 -> 790 | SEC-20 680-698 -> 903 |
      SEC-21 589-625 -> 812 | SEC-22 627-642 -> 850 | SEC-24 663-678 -> 886 |
      OPS-01 707-712 -> row 101 / detail 930 | OPS-03 719-720 -> row 103 / detail 942.
      Line 528 now lands INSIDE SEC-11's WP5 sharpening — a verify agent already read the wrong
      entry because of this. EITHER patch every ref OR add one line to each kickoff: "backlog
      line numbers have drifted — locate entries by their `### SEC-xx` heading, not by line."
      Prefer the heading-based note (durable; refs will drift again).
  (d) W17 `st=` GAP — the highest-risk item on the tail. The G11 carry-forward "W17 MUST
      construct the production gateway with st=" appears at production-work-packages.md:469-470,
      production-playbook.md:653-654, and the G11 gate row :583 — and NOWHERE in the W17 kickoff
      (playbook:813-840). gateway.py:117 defaults st=None, so the INSECURE construction is the
      DEFAULT and the SILENT one: omitting st= re-opens the G11 round-2 forged-ladder HMAC-
      substitution bypass with a GREEN SUITE and CLEAN RUFF. Add it to the W17 kickoff's WHAT TO
      BUILD and its DO NOT.
  (e) W16 KNOB NAMES. gate.py:62-67 reads six knobs via getattr; config/settings.py defines NONE.
      Five map name-for-name onto the dataclass field, but `gate_max_drawdown` reads into the
      field `max_drawdown_limit`. A session that adds `gate_max_drawdown_limit` (mirroring the
      dataclass) gets NO error, NO test failure, and the getattr silently keeps Decimal("0.20")
      FOREVER — reproducing the exact bug W16 exists to fix. Record the six EXACT getattr names
      in the W16 kickoff: gate_min_weeks_lower, gate_min_adjudicated, gate_min_approval_rate,
      gate_max_drawdown, gate_min_weeks_auto_veto, gate_max_veto_rate.
  (f) §4 SCRIPT-NUMBERING COLLISION. production-playbook.md §4: 4a=audit-spine red-team (:229),
      4b=capability-boundary red-team (:332), 4c=loop-until-green (:423), 4d=parallel worktree
      build (:496). ultracode-build-playbook.md §4: 4a=schema-first (:116), 4b=parallel worktree
      build (:253), 4c=adversarial review (:325), 4d=loop-until-green (:443). Each kickoff's
      ITALIC HEADER uses PRODUCTION numbering while its BODY uses BUILD numbering, without saying
      so. W13/W15/W16 bodies say "playbook script 4b" with NO doc named — a cold session opening
      production-playbook §4b gets the red-team when the body meant the worktree build. Qualify
      every script reference with its doc name.
  (g) W13 STALE CODE REFS. Kickoff (playbook:726) cites "app/main.py (compose 29-42, run() 45-52
      # pragma: no cover)". ACTUAL: _hmac_key :17, _build_anchor_store :30,
      run_startup_security_checks :53, build_decision_transport :80, _no_broker_history :91,
      _startup_reconcile :99, compose :119-144, run() :147. Line 29-42 is now W10's anchor store;
      45-52 is W11's security checks. W13's whole (d) sub-item is factoring run(). Also W14's
      kickoff cites "budget.py::try_consume (112-137)" -> try_consume is now :158. Re-anchor.
      NOTE the contrast: W13's storage/engine.py refs are EXACT — drift is confined to files the
      shipped W's touched.
  (h) W13 "literal TODO" IS WRONG. `grep -n TODO app/pyproject.toml` returns NOTHING. The kickoff
      ACCEPT says "pyproject.toml pins exact versions (TODO removed)" (playbook:728) and its
      read-order says "unpinned deps + TODO lines 6-23" (:726). What EXISTS at :6-7 is a comment:
      "# Pin + hash-lock before install — supply-chain hardening". The unpinned deps (:8-23) and
      the ABSENT lockfile are both REAL, so W13(c) stands. Restate as "remove the
      pin-before-install comment" so a literal-minded session does not hunt a token that is not there.
  (i) W20 STALE ANCHORS — the FILE-ANCHOR-THEATER class G10 caught at W10. Cited at
      production-work-packages.md:1379-1381 (and repeated at :261 and playbook:592):
      budget.py:158 + triage_loop.py:46,194. ACTUAL: budget.py:158 is `try_consume`; the
      frozenset _HARD_EVENT_TYPES is budget.py:27 and the deterministic_urgency membership test
      is budget.py:234. triage_loop.py:46 is `_BURST_THRESHOLD = 4`; the frozenset is :47 and the
      pre-filter use is :199. The CLAIMS are substantively true — only the anchors drifted.
  (j) W14/SEC-17 MIS-SCOPE. The spec calls proposals.py:248-250 "model-emitted provenance". The
      anchor is exact but the class is WRONG: the `_excerpt` closure ALREADY reads HMAC-verified
      raw parquet via resolve_raw (events.py:252-265, verify_entry-checked EVENT_INGEST_BATCH +
      verify_snapshot). The spec's own wording is "model-ADJACENT" (production-work-packages.md:752).
      The real residual is NARROW: the excerpt is FIELD-KEYED (item.get("headline")) rather than a
      byte-offset substring over the verified snapshot. Mis-scoping invites rebuilding
      verification that already ships (anti-pattern #5). Correct the wording.
  (k) W14/SEC-16: `canary_breach` ALREADY SHIPS — order_state_machine.py:11-19 (forced safety
      terminal reachable from any non-terminal), :50 (terminal set), :52; schemas/models/order.py:37.
      W14's real delta is the out-of-band registry + interception hook + per-run rotation +
      batch-seeding + the PII variant. Add an anti-rebuild fence.
  (l) OPS-04 DATED FUSE. app/ingest/market_calendar.py codes NYSE 2025 and 2026 ONLY and its
      docstring says they MUST be refreshed each year. Today is 2026-07-16 — coverage expires in
      ~5 months and W15's staleness gauge will warn almost immediately. The fallback is fail-SAFE
      (weekday/hours rules; never reports a real holiday as open beyond coded years), so this is
      a WARN not a block — but a 2027 NYSE refresh is an OPERATOR task with a real date and is
      tracked nowhere. Add it to the operator track.

TASK C — RED-TEAM SCRIPT DURABILITY. The reusable G-gate red-teams live ONLY in ephemeral
  session scratchpads. w10_redteam.js is ALREADY LOST. w11_redteam.js and w12-g12-redteam.js
  survive but are one temp-cleanup from gone — while playbook:621 says "reuse it as the template
  for every P0 W-gate". Recover what survives, commit to tools/redteam/, and repoint
  production-playbook.md §4a/§4b at the committed paths. If a script cannot be recovered, say so
  plainly and leave §4a/§4b prose as the canonical source — do NOT fabricate a reconstruction.

ACCEPT: (a) W12 kickoff carries a DONE banner with its carry-forwards; (b) no remaining kickoff
cites a stale baseline; (c) backlog refs are heading-anchored or corrected; (d) the W17 kickoff
carries st=; (e) the W16 kickoff records the six exact getattr names incl. the gate_max_drawdown
asymmetry; (f) every script ref names its doc; (g)-(k) corrected; (l) the 2027 calendar refresh
is on the operator track; (Task C) surviving red-teams are committed and §4a/§4b repointed.
Mechanical: ruff clean; suite UNCHANGED at the true baseline (this session changes no app/ code —
if the suite count moves, you touched source you should not have).

DO NOT: touch app/ source; start any W-package; re-run W10/W11/W12; close or imply-close SEC-11;
invent line numbers (verify each against the file); fabricate a lost red-team script; push commits.
Ground every claim in a doc line or code file:symbol.
```

---

## 7.2 — W20 (REVISED): EDGAR evidence feed via edgar-morning-brew

> Replaces the existing W20 kickoff at `production-playbook.md:899-936`. The original's security
> thesis is intact and excellent — keep it. What changes: the cockpit **consumes the brew**
> instead of building its own EDGAR poller. `edgar-morning-brew` is **READ-ONLY** here.

```text
ultracode, high. You are closing PRODUCTION work package W20 (REVISED) — "Data breadth:
first-party regulatory feeds, EDGAR via edgar-morning-brew" — for the Local AI Investment
Cockpit (repo root = cwd; source under app/, docs under docs/). READ-ONLY, PAPER-SAFE,
order-path-untouched (Inv 1). It does NOT close a register id — its register-relevant effect is
UPGRADING the SEC-21/SEC-15 corroboration SUBSTRATE from a news-vendor "filing" TAG to a
first-party SEC accession-number trust root. Never imply SEC-11 is closed. Do NOT push.

This is a property-closure package but NOT a security-critical order-path closure: a MEDIUM
adversarial pass on the G20 repros suffices. Do NOT run a heavy xhigh red-team to add an ingest
source.

LEAD WITH THE SECURITY ANGLE (unchanged from the original W20): today the ONLY thing that stamps
event_type="filing" is a news vendor's TAG — no live feed emits it; the fixture builds it by hand
(app/tests/workflows/fixtures_market_day.py). Yet deterministic_urgency treats
event_type in {"halt","filing"} as auto-corroboration and the triage pre-filter keeps it
regardless of holdings/watchlist. A FIRST-PARTY EDGAR feed makes "filing" a first-party assertion
anchored to an SEC accession number. That is the POINT — not merely more inputs.

RE-ANCHOR BEFORE YOU GREP (the docs' anchors drifted — FILE-ANCHOR-THEATER class):
  _HARD_EVENT_TYPES frozenset = app/workflows/budget.py:27 (NOT :158 — that is try_consume);
  the deterministic_urgency membership test = budget.py:234;
  app/workflows/triage_loop.py:46 is `_BURST_THRESHOLD = 4` — the frozenset is :47, use at :199.

READ IN THIS ORDER (verify current state against code; do not assume):
  1. docs/production-work-packages.md -> the W20 entry + §3.3 (data-breadth note) + the §4 DAG
     parallel track + the 9 invariants (Inv 3/7/8 in scope; Inv 1 UNTOUCHED).
  2. app/ingest/normalize.py IN FULL — ALLOWED_SOURCES (:18, currently
     {"alpaca:benzinga","finnhub","calendar"}; to_market_event RAISES on a non-allowlisted
     source, :67-68); to_market_event returns a 9-KEY DICT (:70-80), not the ORM model;
     escape_untrusted (:28-36); content_hash (:60-62); _TICKER validation (:21,39-49).
  3. app/ingest/finnhub_poll.py IN FULL — the injectable-fetch poller TEMPLATE whose SHAPE you
     clone (injectable fetch :18; per-item try/except :37-42; cursor advances only from EMITTED
     items :45-48). You are cloning the SHAPE, not the fetching.
  4. app/workflows/events.py — persist_events (:94) write-once parquet raw_ref +
     EVENT_INGEST_BATCH binding — REUSE UNCHANGED.
  5. app/main.py compose() — where the production fetch closure is constructed and injected
     (mirror FinnhubPoller(fetch=...)); keep I/O OUT of the pure ingest module.
  6. READ-ONLY, the sibling repo C:\Users\drfoo\GitHub\edgar-morning-brew:
     app/schemas/models/{filing,delta,citation,company}.py (the consumable rows),
     app/storage/raw_store.py (:161-177 resolve() — read-once + constant-time re-hash),
     app/cite/resolver.py (:160-181 resolve_all -> per-citation ResolveResult),
     app/cite/digest.py (:39-51 DigestResult; :300-307 render_digest),
     docs/cockpit-port-map.md, CLAUDE.md.

FACTS ESTABLISHED BY A GROUNDING PASS — do not re-litigate, but DO verify:
  * The brew ALREADY ships W20 (a) EDGAR-direct filings (app/edgar/submissions_poll.py), (b) XBRL
    fundamentals (app/edgar/companyfacts.py + the xbrl_fact table), AND the CIK->ticker resolution
    W20 names as a hard precondition (app/edgar/cik_map.py). It ships ZERO FINRA.
  * There is NO namespace collision: the cockpit has NO app/__init__.py — app/ is a bare source
    ROOT (app/pyproject.toml sets pythonpath=["."]) and imports `from ingest.normalize import ...`,
    never `from app.ingest`. A grep for '^from app\.' across the cockpit returns ZERO.
  * The brew's Inv-8 advice-lexicon is a lint over three LLM-drafted BriefDraft PROSE fields
    (compliance_lint.py:15-19). It binds what the BREW emits, never a consumer, and the seam below
    never touches prose — the Delta/Citation layer is explicitly LLM-free (digest.py:1-14).
  * DigestResult.text is a WALL for machine consumption (a width-78 terminal render, 400-char
    truncation at digest.py:186-196). The data/out/runs/*.txt artifacts carry FOUR fixed strings
    and zero data. The ONLY machine-readable surface is the brew's SQLite ROWS.
  * app/edgar/fetch.py is stdlib-only with zero sibling imports, and SecFetcher.__init__ takes
    user_agent directly — so nothing here drags in EDGAR_SEC_USER_AGENT (get_settings() DOES raise
    without it, settings.py:38-41, but reading rows never constructs Settings).

WHAT TO BUILD (the delta only — REUSE, do not reinvent):
  (a) app/ingest/edgar_brew.py — an injectable `fetch(cursor, now) -> list[dict]` closure matching
      the FinnhubPoller template. Its PRODUCTION closure opens the brew's SQLite READ-ONLY
      (sqlite3 file:...?mode=ro URI) and joins filing + company + delta. ~50 lines: a SELECT, a
      field map, one to_market_event(...) call per row. NO TokenBucket, NO User-Agent, NO
      fair-access risk, NO cursor poison-guard — the brew owns all of it behind its own invariants.
      Emit source="sec:edgar", article_id=<accession>, event_type="filing".
      CRITICAL: emit the CANONICAL bare string "filing" — NEVER "filing:8-K". The {halt,filing}
      check is EXACT-MEMBERSHIP. The brew stores form_type as its own column
      (brew app/schemas/models/filing.py:84) — map it into raw.form.
  (b) app/ingest/finra_short_interest.py — NET-NEW, unchanged from the original W20 spec. FINRA
      consolidated short-interest + Reg-SHO, keyed (settlementDate+symbol),
      event_type="short_interest"/"reg_sho_threshold". ENRICHMENT/CONTEXT ONLY — bi-monthly,
      ~2wk stale, NEVER real-time, NEVER drives SEC-21 urgency. This one DOES need the
      TokenBucket + declared User-Agent + poison guards (clone finnhub_poll.py properly).
  (c) Modify app/ingest/normalize.py — add "sec:edgar","finra" to ALLOWED_SOURCES (the single
      shared-code edit); app/ingest/__init__.py (export); app/config/settings.py (brew DB path +
      per-source rate + FINRA User-Agent contact); app/main.py (inject the production closures).
      Add app/tests/ingest/test_edgar_brew.py + test_finra_short_interest.py.
  XBRL FUNDAMENTALS: the brew already computes them (xbrl_fact). If exposing them is in reach via
  the same read-only join, do it as event_type="fundamentals" (never urgency/corroboration,
  accn on each datapoint). If it is not clean, DEFER it and say so — do not half-build it.
  SENTIMENT IS EXPLICITLY DEFERRED — do NOT build it (StockTwits closed to new devs; X/Reddit
  cost+terms; social posts have NO verifiable provenance key — the opposite of the accession thesis).

USE resolve_all, NOT claim_gate.evaluate. The brew's claim_gate is an ALL-OR-NOTHING BRIEF-LEVEL
verdict ("a single uncited delta ... makes the entire brief non-approvable", claim_gate.py:166-171)
— the WRONG granularity, and it collides with the cockpit's Inv-7 never-drop per-event deferral.
resolve_all (resolver.py:160-181) returns a per-citation ResolveResult with a distinct failure reason.

THE SEC-13 BINDING BOUNDARY — call this out EXPLICITLY in G20, do not let it pass silently:
content_hash (normalize.py:60-62) hashes whatever dict is passed as raw=. A brew-fed adapter
passing a brew record as raw binds the cockpit's audit chain to THE BREW'S RECORD, not to the SEC
bytes. This is acceptable ONLY because the brew's raw store is itself hash-bound and TOCTOU-safe
(raw_store.py:161-177 — resolve() reads once, re-hashes with hmac.compare_digest, raises
RawStoreError on any post-ingest edit). The real chain is:
  cockpit content_hash -> brew Delta/Citation byte-anchor -> brew RawStore SHA-256 -> fetched SEC bytes.
That is a REAL chain, but it is ONE LINK LONGER than W20 assumed, and the added link lives in
ANOTHER REPO'S TRUST DOMAIN. Document it in the W20 entry as a bounded, named residual.

STALENESS / DEGRADATION — inherit the brew's distinction, do NOT flatten it. The brew deliberately
separates four run outcomes and a DEGRADED run must NEVER render as "nothing material". A quiet
filing day (no new deltas) and a brew that DEGRADED (SecFairAccessHalt on 403/429) are NOT the
same event and must not produce the same cockpit state. Note WHY this matters: the ORIGINAL W20
ACCEPT required a rate-denied poll to "return 0, never burst" — the brew's cockpit-port-map.md
names that exact cockpit behaviour "EXACTLY THE ANTI-PATTERN", because a throttled EDGAR then
masquerades as a quiet day. Do not reintroduce it. The cockpit's E-08 treats staleness as a HARD
risk fail — decide and document how brew-staleness surfaces, and fail closed.

RE-VERIFY EXTERNAL FACTS (knowledge cutoff ~Jan 2026; these are MOVING TARGETS — cite official
primary sources, invent nothing):
  * FINRA Rule 4560 https://www.finra.org/rules-guidance/rulebooks/finra-rules/4560 ; equity short
    interest https://www.finra.org/finra-data/browse-catalog/equity-short-interest ; Query API +
    OAuth2 https://developer.finra.org/products/query-api
  * PENDING SR-FINRA-2026-012 https://www.sec.gov/rules-regulations/self-regulatory-organization-rulemaking/sr-finra-2026-012
    — SEC action date 2026-08-14, ~1 month out; MAY CHANGE short-interest cadence. Check it.
  * DEFER TRACE/bond data (out of equity scope).
Numbers that are OPERATOR-SET / TBD — do NOT invent: the SEC fair-access rate + exact required
User-Agent format; the MATERIALITY THRESHOLD selecting which brew deltas become cockpit events
(the brew defers materiality tuning to its own soak — digest.py:253-256).

ACCEPT (G20): new sources normalize through the EXISTING to_market_event (no new
normalizer/escaper/dedup/limiter); "sec:edgar"/"finra" allowlisted; filing free-text inherits
escape_untrusted (SEC-02) + content_hash binding (SEC-13/Inv 8) for free; EDGAR emits canonical
event_type="filing" with form in raw.form; persist_events gives write-once parquet raw_ref +
EVENT_INGEST_BATCH unchanged; the brew is opened READ-ONLY and NEVER written; FINRA never exceeds
the re-verified fair-access rate and ALWAYS sends the declared User-Agent; short-interest is
enrichment-only; NO new event reaches the order path; the SEC-13 one-link-longer boundary is
documented; full suite green at the TRUE baseline (753 passed / 3 skipped if branched from W12 —
NOT the stale ">=586" the docs cite) + ruff clean; the repros land as regressions.

GATE G20 (MEDIUM adversarial — reproduce then prove fail-closed):
  (1) A SPOOFED/news-tagged "filing" (no valid accession, or a vendor tag) CANNOT manufacture
      corroboration — provenance is the SEC accession carried from the brew's hash-bound raw store
      + deduped. For AUTO it stays untrusted until W10 binds event_type into the chain.
  (2) A TAMPERED brew row (edited delta text / swapped accession / a raw blob edited post-ingest)
      is CAUGHT — resolve_all's re-read + re-hash fails closed and the event is refused, not
      silently ingested. This is the load-bearing repro for the cross-repo trust link.
  (3) A brew that DEGRADED (SecFairAccessHalt) is NEVER indistinguishable from a quiet filing day.
  (4) A malformed/oversized/poison brew row (absurd accession, giant free-text, future-dated
      cursor jump) fails closed WITHOUT crashing ingest or poisoning the cursor; an accession-less
      record gets a synthetic content-hash key and is NEVER silently dropped (Inv 7).
  (5) Untrusted filing free-text is escape_untrusted-wrapped before ANY model input (SEC-02).
  (6) NO new event reaches the order path (Inv 1 untouched — the same grep W20 already mandates:
      the adapter imports only sqlite3/SQLAlchemy + ingest.normalize).

INVARIANTS IN SCOPE: 3 (schema-validated ingest), 7 (never-drop), 8 (raw separate from summaries).
Inv 1 UNTOUCHED — this package must never reach the order path. Inv 5 unchanged (NO short-interest
check inside risk/engine.py — that is a FOLLOW-ON, invariant-sensitive, order-path-gate package).

ORCHESTRATION: two disjoint lanes (edgar_brew / finra) via the PARALLEL WORKTREE BUILD script —
NOTE the numbering collision: that is production-playbook.md §4d (:496) == ultracode-build-playbook.md
§4b (:253). Barrier-merge the shared normalize.py/__init__.py/settings.py/main.py edits, then
loop-until-green (production-playbook.md §4c :423 == build-playbook §4d :443).

DEPENDS ON: no hard W-dep. SOFT edge to W10 for the AUTO-mode corroboration trust-root use ONLY
(until W10 binds event_type into the signed chain, a first-party "filing" must NOT unlock AUTO
deep-budget/rungs). Runs alongside W13-W16; NOT gated behind W19.

DO NOT: WRITE to edgar-morning-brew in ANY way — it is READ-ONLY here and is mid-soak (open its DB
with mode=ro); vendor/copy the brew's fetch.py/raw_store.py into the cockpit (the brew's own rule
is "pattern-source, never copy" — and consuming its DATA is the whole point of this revision);
build a cockpit-side EDGAR poller (the brew owns EDGAR fetching, its fair-access limiter, and its
error taxonomy — a second process hitting sec.gov independently risks a fair-access breach and its
SecFairAccessHalt); reach the order path / gateway / gates a-h; write a new
normalizer/escaper/dedup/limiter; call claim_gate.evaluate (use resolve_all); trust a NEWS-TAGGED
filing as first-party; emit "filing:8-K" as event_type; add a short-interest check inside
risk/engine.py; bind event_type into the chain (that is W10); build sentiment (DEFERRED); close or
imply-close SEC-11 / SEC-21 / SEC-15; invent endpoints, rate-limits, thresholds, or dates (cite the
official URLs; say operator-set/TBD); trust the docs' stale line anchors (re-anchor per the block
above). Do not push commits. Ground every claim in a doc line or code file:symbol.
```

---

## 7.3 — W21 PHASE 1: the Codex spike *(no code — it can kill the program)*

```text
ultracode, xhigh. You are running the W21 PHASE-1 SPIKE for the Local AI Investment Cockpit — a
DECISION SPIKE, not a build. Its ONLY job is to answer three questions that determine whether the
"run the cockpit's decision stage on Codex" program is viable AT ALL. Write NO production code.
Repo root = cwd. Do NOT push. Never imply SEC-11 is closed. Never weaken an invariant.

WHY THIS EXISTS. A second operator wants to run the cockpit with `codex` as the DECISION engine
instead of `claude`. A grounding pass established these facts — verify them, then answer the three
questions:
  * Invariant 2 is verbatim (docs/build-scaffold-ultracode.md:69-70): "auto_veto/auto_full refuse
    to arm if any decision-stage job is configured for a non-Claude runtime (OpenAI policy
    constraint — verified)." It is grammatically AUTO-SCOPED and its backlog Gate is `auto`. It is
    NOT a blanket "decision stages must be Claude".
  * Both enforcement points are ALREADY auto-scoped: mode_ladder.py:89
    (`if _as_mode(mode) in _AUTO_MODES and producing_runtime != "claude"`) and :244 (inside
    arm_auto_mode). TWO REGRESSION TESTS ASSERT CODEX IS ALLOWED IN review_live —
    tests/orchestrator/test_core.py:147 and tests/orchestrator/test_g5_acceptance.py:349. So
    codex-in-review is RATIFIED DESIGN, not a loophole.
  * The REAL blocker is SEC-06 (tool starvation), whose backlog Gate is `live` — which BINDS
    review_live. Not Invariant 2. Do not re-frame this as an Inv-2 question.
  * The ladder enum is read_only, paper, review_live, auto_veto, auto_full (mode_ladder.py:37-39).
    There is NO `review_paper`.

QUESTION 1 — THE ToS QUESTION (answer FIRST; a wrong answer here makes everything else waste).
  Invariant 2's rationale is "(OpenAI policy constraint — verified)" — but that verification is
  reproduced NOWHERE in the repo: no linked policy text, no date, no quoted clause. Read OpenAI's
  ACTUAL current usage policy / terms from PRIMARY SOURCES (openai.com, not memory, not this
  repo, not your training data — treat this as a MOVING TARGET under invariant P-C).
  Determine, and QUOTE THE CLAUSE:
    * Does the constraint bar automated EXECUTION of trades? -> then auto-scoping is FAITHFUL and
      the program proceeds.
    * Or does it bar automated FINANCIAL DECISIONS / advice generally? -> then Codex proposing
      trades in review_live VIOLATES IT EVEN WITH A HUMAN CLICKING APPROVE, and NO amount of code
      makes it safe. THE PROGRAM DIES HERE. Say so plainly.
  The repo CANNOT settle this. If the policy is genuinely ambiguous, SAY IT IS AMBIGUOUS — do not
  resolve ambiguity in the direction that lets the program proceed. Report the clause verbatim
  with its URL and retrieval date.

QUESTION 2 — DOES CODEX HAVE A TRUE ZERO-CAPABILITY MODE?
  Read docs/security-hardening-backlog.md SEC-06 END TO END (locate it by its `### SEC-06`
  HEADING — the doc's line numbers have drifted ~+180; it is near :363, NOT the cited 218-232).
  SEC-06's attack paragraph EXPLICITLY names `codex exec` and EXPLICITLY names
  `cat ~/.codex/auth.json` as the attack, and names FS-read + egress as the exfil precondition.
  The Claude decision path answers SEC-06 with `--allowedTools none` (runner.py:47) — an EMPTY
  CAPABILITY SET. Codex's current decision-forbidden branch pins `--sandbox read-only`
  (runner.py:56) — which still GRANTS FS READ. A write-restricted sandbox is NOT an empty
  capability set; it is precisely the control SEC-06 says is INSUFFICIENT.
  From the INSTALLED codex CLI (`codex --help`, `codex exec --help`, and its docs), determine:
    * Is there a configuration granting ZERO capabilities — no FS read, no shell, no network?
    * If YES: name the exact flags and how they are verified from the ARGV (not from config
      files a compromised process could edit).
    * If NO: say so. SEC-06 CANNOT BE CLOSED FOR CODEX and the program dies. That is a valid,
      valuable answer — do NOT invent a mitigation to keep it alive.

QUESTION 3 — THE ADAPTER SURFACE (only if 1 and 2 clear).
  From the installed CLI, establish and DOCUMENT (do not guess):
    (a) The real `codex exec --json` OUTPUT ENVELOPE. runner.py::_parse_output is a Claude-CLI
        envelope parser (session_id/result/is_error/subtype). If codex emits a JSONL EVENT STREAM
        rather than one JSON object, json.loads raises and _parse_output returns (None,None,None)
        -> the decision path is NON-FUNCTIONAL today, not merely unproven. Capture a real sample.
    (b) Codex's FULL capability-granting FLAG VOCABULARY — every flag that grants a tool, MCP
        server, network, shell, or loosens permissions. tool_inspector.py's three denylists
        (:23-33) are CLAUDE-CLI-flag-shaped and would not catch any of them.
    (c) Whether detect.py::looks_like_limit / _LIMIT_PATTERNS are claude-shaped, and what codex's
        real limit/health-fault strings are.
    (d) Codex's bearer store location + shape under a low-priv account (account_guard.py:57-61
        walks DIRECT-SID ACEs ONLY — group-granted reads are missed; and note default_store_paths
        resolves Path.home() in the ORCHESTRATOR's process, so those are the OPERATOR's stores the
        agent account must be DENIED — a codex deployment needs its own bearer under the low-priv
        account's OWN home).

DELIVERABLE: a written report at docs/plans/w21-codex-spike.md answering all three, with the ToS
clause quoted + URL + retrieval date, real captured CLI output, and a GO / NO-GO recommendation.
If NO-GO, say which question killed it and why — that is the most valuable outcome this spike can
produce. If GO, the report becomes the W21 kickoff's factual base.

CARRY THESE FORWARD INTO ANY W21 DESIGN (found by a grounding pass; they are the traps):
  * DO NOT naively delete runner.py:52's blanket raise. tool_inspector.py:120-125's
    assert_all_decision_specs_tool_starved derives its ENTIRE codex refusal by CATCHING that exact
    ValueError. Deleting it silently collapses the SEC-06 auto-arm gate from TWO independent checks
    to ONE (only mode_ladder.py:244 survives) — and the test that would catch the loss
    (tests/agent_runner/test_tool_inspector.py:109 test_codex_decision_spec_refused) is exactly what
    a naive diff deletes as "obsolete". This is the G11 defect class: EXPECT the fix to introduce
    it and re-run FRESH.
  * "deny-by-default" is a MISNOMER not to inherit. tool_inspector.py:1,:51 and the SEC-06 closure
    text call inspect_argv "deny-by-default"; the MECHANISM is deny-by-ENUMERATION over three
    hardcoded flag sets. Sound over a KNOWN vocabulary; ALLOW-by-default against an UNKNOWN CLI
    grammar. Codex is an unknown grammar. The fail-closed design is a PER-RUNTIME ALLOWLIST where
    an unrecognized flag REFUSES.
  * THE AUTO-CEILING DOES NOT EXIST. There is NO mode ceiling anywhere in mode_ladder.py.
    "Hard-capped at human-gated modes" is an ASPIRATION, not a control. Any W21 must BUILD a
    durable, chain-anchored, deployment-level ceiling that arm_auto_mode consults, that CANNOT be
    flipped by a config edit or a DB writer, that fails closed — and that is itself red-teamed.
  * A codex decision deployment WIDENS nearly every LIVE-gated property (SEC-02/03/04/05/06/07/09/13)
    while SEC-11 is still OPEN. Consider whether it belongs AFTER the Discord witness lands.
  * SEC-05 activation (low-priv account + CreateProcessWithLogonW launcher) is ALREADY a W17
    precondition — a codex bearer under that account COLLIDES with existing sequencing.
  * G11's three clean rounds DO NOT TRANSFER. They red-teamed a flag vocabulary codex does not
    speak. A G21 against a codex decision argv is a FIRST RUN, not a re-run.
  * Codex in AUTO is structurally LAST regardless: the auto rungs do not exist yet EVEN FOR CLAUDE
    (W18 builds auto_veto, W19 builds auto_full). It also requires AMENDING Invariant 2. Do not
    design it now.

DO NOT: write production code; modify app/ source; delete runner.py:52; resolve the ToS question
from memory or from this repo (primary sources only); invent a codex flag, envelope, or sandbox
mode you did not observe from the installed CLI; keep the program alive by inventing a mitigation
for a missing zero-capability mode; close or imply-close SEC-11 or SEC-06; push commits. Ground
every claim in a doc line, a code file:symbol, or a quoted primary source with its URL.
```

---

## 7.4 — Patch preamble for W13 / W14 / W15 / W16 / W17 / W18 / W19

**These kickoffs already exist.** Paste this preamble, then the existing kickoff text verbatim
from `docs/production-playbook.md` (W13 `:715` · W14 `:738` · W15 `:757` · W16 `:783` ·
W17 `:813` · W18 `:842` · W19 `:875`).

*(If Session 0 has already run, the repairs are in the docs and this preamble is redundant —
skip it.)*

```text
BEFORE YOU READ THE KICKOFF BELOW — corrections that override it where they conflict. Each was
verified against the tree; the kickoff text predates them.

1. BASELINE. The kickoff's ACCEPT cites "Full suite green (>=586 passed / 3 skipped)". THAT
   NUMBER IS STALE. Lineage: 586 (WP9) -> 619 (W10) -> 662 (W11) -> 753 passed / 3 skipped
   (756 collected) (W12). Your bar is the count of the tree you BRANCHED FROM — establish it with
   `cd app && python -m pytest -q` BEFORE you change anything. Shipping a tree that lost
   regressions while citing ">=586" as green is playbook anti-pattern #6.

2. BACKLOG LINE NUMBERS HAVE DRIFTED ~+180 to +220 (the W10/W11/W12 closure notes were appended;
   the file is now 978 lines). Locate every SEC-xx / OPS-xx entry by its `### SEC-xx` HEADING,
   NEVER by the cited line range. Line 528 (cited for SEC-17) now lands inside SEC-11's WP5
   sharpening — reading it gets you the WRONG ENTRY.

3. §4 SCRIPT NUMBERING IS AMBIGUOUS — the two playbooks number §4 DIFFERENTLY and this kickoff's
   header and body each use a different one WITHOUT saying so:
     production-playbook.md §4:      4a=audit-spine red-team, 4b=capability-boundary red-team,
                                     4c=loop-until-green, 4d=parallel worktree build
     ultracode-build-playbook.md §4: 4a=schema-first, 4b=parallel worktree build,
                                     4c=adversarial review, 4d=loop-until-green
   When the body says "playbook script 4b" with no doc named, it means the PARALLEL WORKTREE BUILD
   (build-playbook §4b == production-playbook §4d). Confirm which script you actually want by its
   NAME, never its number.

4. CODE LINE-REFS IN THE READ-ORDER MAY BE STALE for any file W10/W11/W12 touched (main.py,
   budget.py, gateway.py, audit.py, halt.py, killswitch.py). Verified drifts: main.py — compose is
   :119-144 and run() is :147 (NOT 29-42/45-52; line 29 is now W10's _build_anchor_store and 45-52
   is W11's run_startup_security_checks). budget.py — try_consume is :158 (NOT 112-137);
   _HARD_EVENT_TYPES is :27; the deterministic_urgency membership test is :234. triage_loop.py —
   :46 is _BURST_THRESHOLD; the frozenset is :47 and the pre-filter use is :199. Re-anchor by
   SYMBOL before trusting any line number. storage/engine.py refs ARE exact.

5. SEC-11 IS OPEN. Its MECHANISM is done (ea7e0fc) and G10 held a fresh round-2, but the PROPERTY
   is OPEN pending the Discord #audit-log witness. The git-commit witness is a MIRROR
   (out_of_domain=False), NOT a runtime trust source; FileAnchorStore is same-trust-domain theater.
   A git-only deployment has GitAuditLogAnchorStore.latest()=None, so every consult FAILS CLOSED —
   auto is unreachable BY DESIGN. Test the REFUSAL REASON, never a successful arm. Never imply
   SEC-11 is closed.

PACKAGE-SPECIFIC — apply only the one matching the kickoff below:

  W13: There is NO literal "TODO" token in app/pyproject.toml — do not hunt for one. What exists
       at :6-7 is a comment "# Pin + hash-lock before install". The UNPINNED deps (:8-23) and the
       ABSENT lockfile are both REAL — that is the work. W13(d) factors run(); see correction 4
       for its true location.

  W14: Your 7 lanes ARE disjoint (4d worktree fan-out is safe) — BUT lane (f) touches
       app/config/settings.py, which W15 and W16(c) ALSO touch. Do not run W14 concurrently with
       W15/W16 in the same tree without a barrier merge.
       SEC-17 IS MIS-SCOPED IN THE SPEC as "model-emitted provenance". proposals.py:248-250
       ALREADY reads HMAC-verified raw parquet via resolve_raw (events.py:252-265 — a
       verify_entry-checked EVENT_INGEST_BATCH + verify_snapshot). The spec's own wording is
       "model-ADJACENT". The REAL residual is narrow: the excerpt is FIELD-KEYED
       (item.get("headline")) rather than a byte-offset substring over the verified snapshot. Do
       NOT rebuild verification that already ships (anti-pattern #5).
       SEC-16: the `canary_breach` order state ALREADY SHIPS (order_state_machine.py:11-19, :50,
       :52; schemas/models/order.py:37). Your delta is the out-of-band registry + interception
       hook + per-run rotation + batch-seeding + the PII variant. Do NOT re-spec the state machine.
       Your W10 edge is GATE-ORDERING ONLY and only for SEC-16 + SEC-19 — the five live-residual
       lanes are genuinely independent.

  W15: You are the heaviest mid-tier package and you have SHARED-FILE CONTENTION (/status in
       orchestrator/app.py, config/settings.py) — barrier-merge required.
       OPS-04: HARDEN AND SURFACE the EXISTING app/ingest/market_calendar.py — do NOT re-author
       it. Note its coded holiday/early-close sets are NYSE 2025 AND 2026 ONLY and today is
       2026-07-16: the staleness gauge you build will begin WARNING within ~5 months. The fallback
       is fail-SAFE (weekday/hours; never reports a real holiday as open beyond coded years) so
       this is a WARN not a block — but flag the 2027 refresh as a dated OPERATOR task.
       OPS-06 owns the HOST-PROVISIONING half of SEC-05 (W11 owns the runtime ACL assertion) —
       this session pairs with the operator's low-priv account work.

  W16: THE SIX getattr KNOB NAMES ARE EXACTLY: gate_min_weeks_lower, gate_min_adjudicated,
       gate_min_approval_rate, gate_max_drawdown, gate_min_weeks_auto_veto, gate_max_veto_rate
       (gate.py:62-67). FIVE map name-for-name onto the dataclass field — BUT `gate_max_drawdown`
       READS INTO THE FIELD `max_drawdown_limit`. If you add a Settings field named
       `gate_max_drawdown_limit` (mirroring the dataclass) you get NO error, NO test failure, and
       the getattr SILENTLY keeps Decimal("0.20") FOREVER — reproducing the exact bug W16 exists
       to fix. Add a test that a non-default value actually REACHES compute_gate.
       Your W10 edge is HARD, and your gate is THEATER on an InMemory/File anchor: if the Discord
       witness is not provisioned, say so and prove the FAIL-CLOSED path — do not manufacture a
       green gate proof.
       beats_benchmark is an HONESTY REPORT, not an unlock bound — do NOT add a "must beat SPY"
       gate condition.

  W17: **MANDATORY, MISSING FROM YOUR KICKOFF, HIGHEST-RISK ITEM ON THE TAIL:** you MUST construct
       the production OrderGateway with `st=` (the composed state). gateway.py:117 defaults
       st=None, so the INSECURE construction is the DEFAULT and the SILENT one — omitting it
       re-opens the G11 round-2 forged-ladder HMAC-substitution bypass WITH A GREEN SUITE AND
       CLEAN RUFF. `st=` pins gate (e)'s trust root (the HMAC key + DB) at construction so a caller
       holding only the gateway cannot substitute an attacker-keyed chain. Source:
       production-work-packages.md:469-470, production-playbook.md:653-654, G11 gate row :583.
       Also: both W17 PRECONDITIONS are OPERATOR PROVISIONING, not code — the SEC-11 Discord
       #audit-log witness and the SEC-05 low-priv account + CreateProcessWithLogonW launcher
       (transport.py::_logon_launch raises NotImplementedError). If either is unprovisioned, W17
       CANNOT complete — report that rather than working around it.
       Known pre-W17 residual to honour: chain-anchor producing_runtime (gate (f) trusts a caller
       literal today).

  W18: The BUILD-vs-WIRE split is load-bearing. SEC-15 is the open BUILD (reconciled-holdings trust
       root + /limits ceremony + watchlist-human-only). SEC-01/SEC-14/SEC-23 are WIRE+PROVE ONLY —
       the mechanism ALREADY EXISTS in bridge/veto.py (WP6/WP8). Replace the `_TrueCtx` placeholder
       at veto.py:262-267 with the real workflows/grounding.py detector. Do NOT re-author veto.py.
       The /limits cool-down duration and modal threshold are OPERATOR-SET — do not invent them.

  W19: E-10's divergence band is NOT a published number — leave it operator-set. E-04 is
       arm-blocking. You depend on W18 and W14.
```

---

## 7.5 — Recommended run order

| # | Paste | Notes |
|---|---|---|
| 0 | **§7.1 Session 0** | Decide the W12 baseline; apply all drift repairs; commit the red-teams. Do this **first**. |
| — | *(operator, starts now, parallel)* | Discord `#audit-log` witness · low-priv account. **Not coding sessions. Both gate W17.** |
| 1 | **§7.2 W20-revised** | Cheapest high-value; medium gate; no order-path risk |
| 2 | §7.4 preamble + **W14** | 7 disjoint lanes; unblocks W18/W19 |
| 3 | §7.4 preamble + **W13** | Supply chain; operationalizes `run()` |
| 4 | §7.4 preamble + **W15** | Pair with the operator's SEC-05 account work |
| 5 | §7.4 preamble + **W16** | **After** the witness lands, else the gate proof is theater |
| — | **§7.3 W21 spike** | Any time. **NO-GO is a valid, valuable outcome.** |
| 6 | §7.4 preamble + **W17** | Needs W10+W11+W12+W13+W15+W16 **and both activations** |
| 7 | §7.4 preamble + **W18** | `auto_veto` |
| 8 | §7.4 preamble + **W19** | `auto_full` |

> One W per session. The operator merges the worktree, clears `must_fix`, and re-runs a FRESH
> adversarial round **between** packages. That checkpoint is a safety feature, not overhead.

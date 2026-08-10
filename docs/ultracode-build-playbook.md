---
title: Ultracode build playbook — orchestrating the cockpit build with many Opus 4.8 agents
type: playbook
status: reference
last-verified: 2026-08-09
related: [build-scaffold-ultracode, cockpit-review-and-integrated-plan, draft-discord-control-plane, security-architecture, prompt-injection-defense, security-hardening-backlog, enhancements-and-additions, upside-and-roi, research-report, research-supplement]
---

> **[REFERENCE]** Build-session orchestration for a finished run. Kept because 4 files in `app/` cite its adversarial-review pattern, which is still the house rule for any order-path change.

> **BUILD COMPLETE — 2026-07-08.** All nine Workflow phases in §3 have run; the
> gates G0–G9 in §5 are green and WP0→WP9 are merged to `main` (WP9 = commit
> `9f6ff44`; suite 586 passed / 3 skipped, ruff clean). The mandatory 4c
> adversarial passes on the four security-critical WPs (WP4 risk, WP5 mode
> ladder, WP6 approval path, WP7 order path) completed. This playbook is
> retained as the build-time record; the orchestration procedure, the §5 gate
> table, the §4 scripts, and the §9 anti-patterns are preserved verbatim.
>
> **PAPER-FIRST — no live money.** Live-mode work still requires the pre-live
> gates and the 2-week paper soak called for in [[build-scaffold-ultracode]]
> ("Build order & session plan"). The SEC-11 audit-spine "delete-then-heal" hole
> referenced through §6 (`security-hardening-backlog.md`) remains the OPEN P0
> pre-live blocker — it is **NOT** closed by this build.

# Ultracode build playbook

The operational manual for **building** the Local AI Investment Cockpit by running
many Opus 4.8 agents in parallel via the Workflow tool ("ultracode"). This is the
meta-layer above [[build-scaffold-ultracode]]: the scaffold defines *what* the work
packages are (WP0–WP9, the 9 invariants, the repo layout, the acceptance tests);
this doc defines *how to drive them* — Workflow-call by Workflow-call, with the
human in the loop between phases. It does **not** re-derive the work packages or
re-litigate the settled stack. It orchestrates them.

Read order for a build operator: this file → [[build-scaffold-ultracode]] →
[[cockpit-review-and-integrated-plan]] + [[draft-discord-control-plane]] (the spec) →
[[security-architecture]] (the enforcement model) → [[research-report]] /
[[research-supplement]] (the constraint source). Never re-decide anything settled in
those files inside a build agent.

---

## 1. Purpose & thesis — why multi-agent orchestration fits *this* build

Three properties of the cockpit make ultracode a good fit, and one property makes it
dangerous if run naively.

| Property of the build | Why fan-out helps |
|---|---|
| **Modules are loosely coupled by design.** `schemas/`, `ingest/`, `risk/`, `storage/`, `bridge/`, `brokers/`, `workflows/` talk only through the WP1a data contracts. | Independent modules fan out cleanly — one agent per schema, one agent per connector — with the schemas as the merge contract. WP3∥WP4 and WP6∥WP7 are genuinely parallel. |
| **The security-critical code is small and adversarial-review-shaped.** The order path is `risk-pass → mode-check → approval/veto → broker` and nothing else (Invariant 1). | A fan-out of *independent* skeptics who each never wrote the code is exactly the review this needs: many minds trying to construct one bypass beats one author re-reading their own logic. |
| **The whole design is schema-first + verification-gated** — LLM output is untrusted until `jsonschema`-validated (Invariant 3); risk is pure functions with no LLM authority (Invariant 5). | Gated phases prevent the LLM-authority creep the invariants forbid: an agent can *propose* code, but a deterministic gate — not another agent's say-so — decides whether the phase advances. |
| **Honest cost caveat: build agents burn plan limits too.** | This is a *dev-time* cost, separate from the runtime economics in [[upside-and-roi]]. Every fan-out spends the same Max-20x window the runtime copilot will later contend for. Batch, cap concurrency, and run the heavy adversarial passes **only** on the security-critical WPs. Gate accordingly — do not fan out 40 agents to write a `pyproject.toml`. |

The thesis in one line: **parallelize the independent modules, serialize the
decision path, and let deterministic gates — not agents — decide when to advance.**

## 2. Agent role taxonomy for a build session

| Role | Job | Runs in parallel with | Runs serially after |
|---|---|---|---|
| **Implementer** | Writes one module or one schema + its tests. | Other implementers on *disjoint* modules (worktree-isolated if they mutate files concurrently). | The schema phase it depends on. |
| **Test-author** | Writes the WP's acceptance tests from the scaffold's `Accept:` line. Often the same agent as the implementer, but a *separate* test-author is mandatory for `risk/` and the approval path (never let the implementer be the sole author of the tests that guard their own code). | Implementers. | — |
| **Adversary / red-team** | Tries to **construct** an input/race that violates an invariant (esp. Invariant 1). Never fixes; only reports a repro. | Other skeptics attacking the same module from different angles. | The module being attacked must exist first. |
| **Integrator** | Merges worktree branches, resolves cross-module field drift, wires imports. Barrier role. | Nothing — it needs all prior results. | The parallel implementers. |
| **Doc/spec-keeper** | Keeps `OBSERVER.md`-style specs and this vault in sync; flags any deviation from the settled spec or the invariants. | Anything. | — |
| **Reviewer** | Adjudicates skeptic claims (reproduce or refute), casts the majority verdict, produces `must_fix`. | Other reviewers on other modules. | The red-team fan-out. |

**Parallel vs. serial rule of thumb:** implementers and red-teamers fan out;
integrators and reviewers are barriers (they need *all* prior results at once);
doc/spec-keepers run whenever. The risk engine and the approval path get a
**separate** test-author and a **mandatory** adversary pass — the implementer never
self-approves either (see §9).

## 3. WP → workflow-phase mapping

The scaffold's build order is:

```text
WP0 → WP1a → WP1b → WP2 → {WP3, WP4} → WP5 → {WP6, WP7} → WP8 → WP9
```

Run **one Workflow call per phase** so the operator reviews the phase gate and
merges worktrees before the next call. Never chain two phases into one Workflow run —
the human-in-the-loop checkpoint between phases is a safety feature, not overhead.

| Run | Phase | Fan-out shape | Gate that must pass before the next run (see §5) |
|---|---|---|---|
| 1 | **WP0** bootstrap | 1 implementer (small) | G0: app imports; keyring round-trip; secrets-in-repo grep = 0 |
| 2 | **WP1a** schemas | fan-out: 1 agent/schema → **barrier** consistency-merge | G1a: round-trips green; cross-schema fields agree; migration runs on Windows |
| 3 | **WP1b** agent runner | 1–2 implementers + 1 test-author | G1b: mocked-subprocess tests (malformed JSON → quarantine, repair-once, timeout, limit-hit → breaker) all green |
| 4 | **WP2** storage + audit | 1 implementer + 1 test-author | G2: hash-chain verifies; tamper detected; parquet snapshot lossless |
| 5 | **WP3 ∥ WP4** | 2 worktree-isolated implementers **+ mandatory adversary pass on WP4** | G3 + G4: ingest replay/dedup/rate-limit; risk property test + Invariant-1-precondition adversarial verdict clean |
| 6 | **WP5** orchestrator | 1–2 implementers + test-author + **adversary pass on the mode ladder** | G5: illegal transitions rejected; gates enforced; breaker → `review_live`; auto refuses non-Claude decision stage |
| 7 | **WP6 ∥ WP7** | 2 worktree-isolated implementers **+ mandatory adversary pass on the approval path (WP6) and order path (WP7)** | G6 + G7: forged/foreign-user + veto-race tests; **Invariant-1 call-site grep test**; `review_equity_order` precedes `place_equity_order` |
| 8 | **WP8** workflows + prompts | 1–2 implementers + test-author | G8: fixture end-to-end brief + correctly-gated escalations; budget-exhaustion → watch-only |
| 9 | **WP9** system harness | loop-until-green over the full suite | G9 (global): full suite green; secrets-in-logs grep; deliberate breaker trip; usage-limit mid-loop; forged interaction |

Between runs the operator: (1) reads the phase report, (2) resolves any `must_fix`
from the adversary pass — which runs against the WP's **un-merged worktree branch**,
not the main checkout (script 4c takes the module + worktree path via `args`; the gate
decides the merge), (3) merges the worktree branches only if the gate passed, and only
then (4) starts the next Workflow call.

## 4. Runnable Workflow scripts

**Harness contract** (get this exactly right). Each script is an ES module that
exports a **pure-literal** `meta` and a default `async` entry. The runner injects
ambient globals: `agent`, `parallel`, `pipeline`, `phase`, `log`, `args`, `budget`.
`agent(prompt, opts)` returns the agent's final text, or the validated object when
`opts.schema` is set, or `null` if the agent is skipped/dies — always
`.filter(Boolean)`. `Date.now()` / `Math.random()` / argless `new Date()` **throw** —
vary by index, stamp wall-clock time *after* return. Concurrency is capped at
`~min(16, cores-2)`; excess queues. Prefer `pipeline()`; use a `parallel()` barrier
only when a stage needs *all* prior results at once (consistency merge, majority
vote). Plain JavaScript only.

### 4a. Schema-first fan-out (WP1a)

One agent per data contract authors the JSON Schema + (for stored rows) the
SQLAlchemy model + a round-trip test. A **barrier** merge then checks that fields
shared across schemas (`proposal_id`, `symbol`, `timestamp`, `session_id`,
`prev_hash`) agree on type and nullability — the one thing a per-schema agent cannot
see alone. Protects Invariants 3 and 8.

```js
export const meta = {
  name: "wp1a-schema-fanout",
  description: "WP1a: one agent per agent-output/ORM contract authors a JSON Schema + SQLAlchemy model + round-trip test; a barrier merge checks cross-schema field consistency before the gate.",
  phases: [
    { title: "author-schemas", detail: "One agent per schema: JSON Schema + (if stored) SQLAlchemy model + round-trip test." },
    { title: "consistency-merge", detail: "Barrier: shared fields must agree on type + nullability across every schema." }
  ]
};

const SCHEMA_TARGETS = [
  { name: "TradeProposal",       stored: false },
  { name: "TriageResult",        stored: false },
  { name: "MorningBrief",        stored: false },
  { name: "ThesisUpdate",        stored: false },
  { name: "InterrogationAnswer", stored: false },
  { name: "CompiledRule",        stored: false },
  { name: "MarketEvent",         stored: true },
  { name: "PortfolioSnapshot",   stored: true },
  { name: "AgentJob",            stored: true },
  { name: "RiskDecision",        stored: true },
  { name: "ApprovalRecord",      stored: true },
  { name: "AuditEntry",          stored: true },
  { name: "AutomationRule",      stored: true },
  { name: "ModeState",           stored: true },
  { name: "BreakerState",        stored: true },
  { name: "UsageWindow",         stored: true }
];
// All 16 WP1a contracts (6 agent-output + 10 stored ORM models) per build-scaffold
// WP1a; the §7 WP1a kickoff list is authoritative. A GroundingCheck schema is added
// by the prompt-injection grounding verifier (see [[prompt-injection-defense]] §7).

const authorSchema = {
  type: "object", additionalProperties: false,
  required: ["schema_name", "json_schema_path", "sqlalchemy_model_path", "roundtrip_test_path", "fields", "roundtrip_passed"],
  properties: {
    schema_name: { type: "string" },
    json_schema_path: { type: "string" },
    sqlalchemy_model_path: { type: ["string", "null"] },
    roundtrip_test_path: { type: "string" },
    roundtrip_passed: { type: "boolean" },
    fields: {
      type: "array",
      items: {
        type: "object", additionalProperties: false,
        required: ["name", "json_type", "nullable", "source_doc"],
        properties: {
          name: { type: "string" },
          json_type: { type: "string" },
          nullable: { type: "boolean" },
          source_doc: { type: "string" }
        }
      }
    },
    notes: { type: "string" }
  }
};

const mergeSchema = {
  type: "object", additionalProperties: false,
  required: ["consistent", "conflicts", "shared_fields"],
  properties: {
    consistent: { type: "boolean" },
    shared_fields: { type: "array", items: { type: "string" } },
    conflicts: {
      type: "array",
      items: {
        type: "object", additionalProperties: false,
        required: ["field", "schemas", "issue"],
        properties: {
          field: { type: "string" },
          schemas: { type: "array", items: { type: "string" } },
          issue: { type: "string" }
        }
      }
    },
    recommendation: { type: "string" }
  }
};

export default async function () {
  phase("author-schemas");
  const results = (await parallel(
    SCHEMA_TARGETS.map((t) => () =>
      agent(
        [
          "ultracode WP1a. Author exactly ONE data contract for the Local AI Investment Cockpit.",
          `Target: ${t.name} (${t.stored ? "stored ORM row" : "agent-output, not persisted"}).`,
          "Fields are FIXED by cockpit-plan-original.md 4 + draft-discord-control-plane.md + build-scaffold-ultracode.md WP1a. Do NOT invent fields; cite each to its source doc.",
          `Write a strict JSON Schema at app/schemas/${t.name}.schema.json (additionalProperties:false; every field required or explicitly nullable).`,
          t.stored
            ? "Append a SQLAlchemy model to app/schemas/models.py (do NOT rewrite other models)."
            : "Return null for sqlalchemy_model_path (this contract is validated in-flight, not stored).",
          `Write a pytest round-trip at app/tests/schemas/test_${t.name.toLowerCase()}_roundtrip.py: build a valid instance, dump to JSON, re-validate, and (if an ORM model exists) round-trip through SQLite.`,
          "Run the round-trip and report roundtrip_passed honestly (the barrier re-checks). Invariants in scope: 3 (schema-validated I/O), 8 (raw vs summary separation). Do NOT push commits."
        ].join("\n"),
        { label: `schema:${t.name}`, phase: "author-schemas", schema: authorSchema, effort: "high", model: "opus" }
      )
    )
  )).filter(Boolean);

  log(`authored ${results.length}/${SCHEMA_TARGETS.length} schemas; ${results.filter(r => r.roundtrip_passed).length} round-trips green`);

  phase("consistency-merge");
  const digest = results.map(r => ({
    schema: r.schema_name,
    fields: r.fields.map(f => `${f.name}:${f.json_type}${f.nullable ? "?" : ""}`)
  }));

  const merge = await agent(
    [
      "ultracode WP1a BARRIER merge. Below is every field every schema agent authored.",
      "Any field name in more than one schema MUST agree on JSON type and nullability. High-risk shared keys: proposal_id, symbol, timestamp, session_id, prev_hash, account.",
      "RiskDecision, ApprovalRecord, and AuditEntry must all key on the SAME proposal_id type. Return consistent:false with a conflict list if ANY disagreement exists — the WP1a gate blocks on this.",
      "FIELD DIGEST:", JSON.stringify(digest, null, 2)
    ].join("\n"),
    { label: "schema-consistency", phase: "consistency-merge", schema: mergeSchema, effort: "high", model: "opus" }
  );

  if (!merge || !merge.consistent) {
    log("GATE FAIL — cross-schema field conflicts; do NOT advance to WP1b:");
    (merge?.conflicts || []).forEach(c => log(`  ${c.field} @ ${c.schemas.join(",")}: ${c.issue}`));
  } else {
    log(`GATE PASS — ${merge.shared_fields.length} shared fields agree across ${results.length} schemas`);
  }
  return { authored: results, merge };
}
```

### 4b. Parallel module build with worktree isolation (WP3 & WP4)

Two modules built **concurrently**, each agent in its own git worktree so parallel
file mutations never collide. Each returns a structured build report the phase gate
re-verifies. WP4 additionally reports `llm_calls_in_module`, which **must be 0**
(Invariant 5). The same shape drives WP6 ∥ WP7 — swap `MODULES`.

```js
export const meta = {
  name: "wp3-wp4-parallel-build",
  description: "Build WP3 (market ingestor) and WP4 (risk engine) concurrently, each agent in its own git worktree so parallel file writes never conflict; each returns a structured build report for the gate.",
  phases: [
    { title: "parallel-build", detail: "WP3 and WP4 built simultaneously in isolated worktrees; reports feed the gate." }
  ]
};

const buildReportSchema = {
  type: "object", additionalProperties: false,
  required: ["work_package", "module_path", "files_written", "tests_added", "tests_passing", "invariants_touched", "worktree_branch", "summary"],
  properties: {
    work_package: { type: "string" },
    module_path: { type: "string" },
    files_written: { type: "array", items: { type: "string" } },
    tests_added: { type: "array", items: { type: "string" } },
    tests_passing: { type: "boolean" },
    invariants_touched: { type: "array", items: { type: "integer" } },
    llm_calls_in_module: { type: "integer" },
    worktree_branch: { type: "string" },
    open_questions: { type: "array", items: { type: "string" } },
    summary: { type: "string" }
  }
};

const MODULES = [
  {
    wp: "WP3", module: "app/ingest/", invariants: [7, 8],
    brief: "Build alpaca_news_ws.py (wss://stream.data.alpaca.markets/v1beta1/news; wildcard {\"action\":\"subscribe\",\"news\":[\"*\"]}; reconnect w/ backoff; dedup by article id), finnhub_poll.py (token bucket capped 60/min + minId cursor), calendars.py (earnings/macro). Normalize to MarketEvent with raw_ref into parquet — raw inputs stored SEPARATELY from summaries (Inv 8). NEVER drop events: queue + expire (Inv 7). Accept: replay-fixture tests, reconnect test, rate-limit-never-exceeded load test, dedup verified."
  },
  {
    wp: "WP4", module: "app/risk/", invariants: [1, 5, 9],
    brief: "Build the risk engine as DETERMINISTIC PURE FUNCTIONS ONLY — no LLM call anywhere in risk/ (Inv 5); report llm_calls_in_module:0 or the gate fails. Rules: symbol allowlist, max notional, max positions, concentration, daily realized-loss limit, buying power, quote staleness, spread/slippage, intraday-margin-deficit estimate + Reg-T $2k min + settlement (Inv 9 — NO day-trade-count logic; PDT eliminated 2026-06-04), live-mode lock, custom AutomationRule eval. Return RiskDecision with per-check results. Accept: exhaustive per-rule unit tests incl. boundary values; property test that NO input yields an approved decision with a failed hard check."
  }
];

export default async function () {
  phase("parallel-build");
  const reports = (await parallel(
    MODULES.map(m => () =>
      agent(
        [
          `ultracode ${m.wp}. Build ${m.module} for the Local AI Investment Cockpit. All-Python stack, Windows host.`,
          `Spec: build-scaffold-ultracode.md ${m.wp}, cockpit-review-and-integrated-plan.md, research-supplement.md. Schemas already exist under schemas/ — import them, do NOT redefine.`,
          m.brief,
          `Invariants in scope: ${m.invariants.join(", ")}.`,
          "You are in your OWN git worktree — commit on your branch, but DO NOT push. Run your module's tests and report honestly; the phase gate re-runs them, so a false 'passing' will be caught."
        ].join("\n"),
        { label: m.wp, phase: "parallel-build", schema: buildReportSchema, effort: "high", model: "opus", isolation: "worktree" }
      )
    )
  )).filter(Boolean);

  for (const r of reports) {
    log(`${r.work_package} @ ${r.worktree_branch}: ${r.files_written.length} files, ${r.tests_added.length} tests, claims_passing=${r.tests_passing}, llm_in_module=${r.llm_calls_in_module ?? "n/a"}`);
    if (r.work_package === "WP4" && (r.llm_calls_in_module ?? 0) !== 0) {
      log("  GATE RISK — WP4 reports LLM calls inside risk/; Invariant 5 violated. BLOCK acceptance.");
    }
  }
  log("Worktrees are staged, NOT pushed. Human merges after G3/G4 pass (see 5).");
  return { reports };
}
```

### 4c. Adversarial review harness for the order path

A `pipeline` over the four security-critical modules. Per module, stage 1 fans out
independent skeptics (a `parallel` barrier within the stage) who each try to
**construct** an input that reaches order placement without *(risk-pass AND
mode-check AND approval)* — i.e. break **Invariant 1**. Stage 2 casts a majority-vote
verdict. One reproducible bypass fails the gate; the vote count only ranks
confidence. Run this on WP4, WP5, WP6, and WP7.

```js
export const meta = {
  name: "order-path-adversarial-review",
  description: "Per security-critical module, fan out independent skeptics who each try to reach order placement past (risk-pass AND mode-check AND approval) — break Invariant 1 — then a majority-vote verdict per module.",
  phases: [
    { title: "red-team", detail: "N independent skeptics per module attempt to reach place_order past the gates." },
    { title: "verdict", detail: "Adjudicate the breach claims; one reproducible bypass fails the gate." }
  ]
};

const SKEPTICS_PER_MODULE = 5;

const attemptSchema = {
  type: "object", additionalProperties: false,
  required: ["module", "reached_order_placement", "missing_gate", "attack_path", "severity"],
  properties: {
    module: { type: "string" },
    reached_order_placement: { type: "boolean" },
    missing_gate: { type: "string", enum: ["risk", "mode", "approval", "none"] },
    attack_path: { type: "string" },
    repro_input: { type: "string" },
    call_sites: { type: "array", items: { type: "string" } },
    severity: { type: "string", enum: ["none", "low", "medium", "high", "critical"] }
  }
};

const verdictSchema = {
  type: "object", additionalProperties: false,
  required: ["module", "invariant_1_holds", "votes_violated", "votes_total", "must_fix"],
  properties: {
    module: { type: "string" },
    invariant_1_holds: { type: "boolean" },
    votes_violated: { type: "integer" },
    votes_total: { type: "integer" },
    consensus: { type: "string" },
    must_fix: { type: "array", items: { type: "string" } }
  }
};

// Full catalog; each GATE runs 4c on ONLY the module built at that phase (see §3/§7).
const ALL_TARGETS = [
  { module: "risk/engine.py",                  focus: "Can a RiskDecision read as approved while a hard check failed? Can custom AutomationRule eval flip a hard fail to pass?" },
  { module: "orchestrator/mode_ladder.py",     focus: "Can an illegal transition or a stale gate leave the box in an auto mode that executes with no live human window? Can auto_veto/auto_full arm with a non-Claude decision stage (Inv 2)?" },
  { module: "bridge/approval.py",              focus: "Can a foreign Discord user, a replayed interaction id, or a veto-window race produce an ApprovalRecord that authorizes an order? Does a veto landing at T-1s lose to execution at T+0?" },
  { module: "brokers/order_state_machine.py",  focus: "Is there ANY import of brokers/ that reaches place_equity_order/submit_order without passing risk-pass -> mode -> approval? Is review_equity_order ever skipped before place_equity_order?" }
];

const SELECTED = (args && Array.isArray(args.modules) && args.modules.length)
  ? args.modules
  : ALL_TARGETS.map(t => t.module);
const WORKTREE = (args && args.worktree_path) ? args.worktree_path : "investment-copilot/app";
// Never attack an un-built module: a gate invocation passes { modules:[...], worktree_path }
// for the WP built THIS phase, and 4c reviews that un-merged worktree branch. An absent
// target source file is a GATE FAIL ("target not built"), never a trivial PASS.
const TARGETS = ALL_TARGETS.filter(t => SELECTED.includes(t.module));

export default async function () {
  const verdicts = await pipeline(
    TARGETS,

    // stage 1 — fan out skeptics (parallel barrier within the stage)
    async (target) => {
      phase("red-team");
      const angles = ["schema-forgery", "state-race", "call-site-bypass", "mode/gate-staleness", "breaker-timing"];
      const attempts = (await parallel(
        Array.from({ length: SKEPTICS_PER_MODULE }, (_unused, i) => () =>
          agent(
            [
              `ultracode RED TEAM skeptic #${i + 1} of ${SKEPTICS_PER_MODULE}. Target: ${target.module}.`,
              "Your ONLY goal: CONSTRUCT a concrete input, call sequence, or race that reaches order placement WITHOUT all three of (risk-engine pass) AND (mode check) AND (approval/veto). That is Invariant 1; a bypass is a build-stopping bug.",
              `Attack focus: ${target.focus}`,
              `Attack ANGLE (must differ from other skeptics): ${angles[i % angles.length]}.`,
              `Read the real code under ${WORKTREE} (the WP's UN-MERGED worktree — NOT the main checkout, where this module may not exist yet). If the target source file is absent or empty, STOP and report reached_order_placement=false, attack_path="TARGET NOT BUILT", severity="critical" (the verdict turns this into a GATE FAIL, never a pass). Otherwise trace every call site that can reach brokers/. Set reached_order_placement=true ONLY with a concrete repro_input and the missing_gate. Do NOT fix anything. Do NOT push commits.`
            ].join("\n"),
            { label: `skeptic:${target.module}#${i + 1}`, phase: "red-team", schema: attemptSchema, effort: "high", model: "opus" }
          )
        )
      )).filter(Boolean);
      return { target, attempts };
    },

    // stage 2 — majority-vote verdict
    async ({ target, attempts }) => {
      phase("verdict");
      const breaches = attempts.filter(a => a.reached_order_placement);
      const digest = attempts.map((a, i) => ({ skeptic: i + 1, reached: a.reached_order_placement, missing_gate: a.missing_gate, path: a.attack_path, severity: a.severity }));
      const verdict = await agent(
        [
          `ultracode VERDICT for ${target.module}. ${attempts.length} independent skeptics attacked Invariant 1; ${breaches.length} claim a bypass.`,
          "Reproduce or refute EACH breach claim against the real code. invariant_1_holds=false if ANY breach reproduces — one real bypass fails the gate; the vote count only ranks confidence, it does not overrule a reproducible bypass. If ANY skeptic reported 'TARGET NOT BUILT' (absent/empty source), set invariant_1_holds=false: a gate must NEVER certify un-built order-path code.",
          "List concrete must_fix items with the exact call site. Do NOT fix; report.",
          "ATTEMPT DIGEST:", JSON.stringify(digest, null, 2)
        ].join("\n"),
        { label: `verdict:${target.module}`, phase: "verdict", schema: verdictSchema, effort: "xhigh", model: "opus" }
      );
      if (verdict) log(`${target.module}: Invariant-1 holds=${verdict.invariant_1_holds} (${verdict.votes_violated}/${verdict.votes_total} skeptics broke it)`);
      return verdict;
    }
  );

  const clean = verdicts.filter(Boolean);
  const broken = clean.filter(v => !v.invariant_1_holds);
  log(broken.length === 0
    ? `GATE PASS — Invariant 1 holds across all ${clean.length} security-critical modules`
    : `GATE FAIL — Invariant 1 broken in: ${broken.map(v => v.module).join(", ")}. BLOCK acceptance; hand must_fix to the WP owner.`);
  return { verdicts: clean };
}
```

### 4d. Loop-until-green

A bounded loop that runs the pytest suite, fans out one fixer per failing test, and
repeats until green or an iteration/budget cap. Crucially, the *loop itself* re-runs
the suite each iteration — a fixer's "fixed" claim is never trusted; the next
iteration's suite run is the source of truth (§9). A fixer that can only pass by
weakening a test escalates to a human instead.

```js
export const meta = {
  name: "loop-until-green",
  description: "Bounded loop: run the pytest suite, fan out one fixer agent per failing test, re-run, repeat until green or an iteration/budget cap; logs every failure it could not fix.",
  phases: [
    { title: "converge", detail: "Iterate run-suite -> fan-out-fixers until green or capped; the suite re-run is the source of truth." }
  ]
};

const MAX_ITERS = 6;
const MAX_FIXERS_PER_ITER = 12;

const runSchema = {
  type: "object", additionalProperties: false,
  required: ["green", "failures"],
  properties: {
    green: { type: "boolean" },
    summary: { type: "string" },
    failures: {
      type: "array",
      items: {
        type: "object", additionalProperties: false,
        required: ["test_id", "file", "message"],
        properties: { test_id: { type: "string" }, file: { type: "string" }, message: { type: "string" } }
      }
    }
  }
};

const fixSchema = {
  type: "object", additionalProperties: false,
  required: ["test_id", "fixed", "files_changed", "changed_a_test"],
  properties: {
    test_id: { type: "string" },
    fixed: { type: "boolean" },
    files_changed: { type: "array", items: { type: "string" } },
    changed_a_test: { type: "boolean" },
    root_cause: { type: "string" },
    note: { type: "string" }
  }
};

async function runSuite(iter) {
  return agent(
    [
      "ultracode. Run the cockpit test suite: `python -m pytest -q app/tests` on Windows.",
      "green=true ONLY if it exits 0 with zero failures/errors. Otherwise list each failing test_id, file, and assertion message.",
      `Do NOT edit code. Do NOT weaken/skip tests. Report only. (suite run ${iter})`
    ].join("\n"),
    { label: `suite#${iter}`, phase: "converge", schema: runSchema, effort: "medium", model: "opus" }
  );
}

export default async function () {
  phase("converge");
  const dropped = [];

  for (let iter = 1; iter <= MAX_ITERS; iter++) {
    if (budget.remaining() <= 0) { log(`STOP — budget exhausted at iter ${iter}; suite NOT green.`); break; }

    const run = await runSuite(iter);
    if (!run) { log(`iter ${iter}: suite runner died; retry next iter`); continue; }
    if (run.green) { log(`GREEN at iter ${iter}. ${run.summary || ""}`); return { green: true, iters: iter, dropped }; }

    log(`iter ${iter}: ${run.failures.length} failing tests`);
    const batch = run.failures.slice(0, MAX_FIXERS_PER_ITER); // cap concurrency

    const fixes = (await parallel(
      batch.map(f => () =>
        agent(
          [
            `ultracode FIXER. Make this ONE failing test pass by fixing the CODE, not the test: ${f.test_id}`,
            `File: ${f.file}`,
            `Failure: ${f.message}`,
            "Find the root cause under app/ and fix it. You may NOT weaken, skip, xfail, or delete the test, and you may NOT touch the invariants or the risk engine's hard checks to make a test pass. If the only way to pass is to change a test, set changed_a_test=true and fixed=false and explain — that needs a human. Do NOT push commits."
          ].join("\n"),
          { label: `fix:${f.test_id}`, phase: "converge", schema: fixSchema, effort: "high", model: "opus" }
        )
      )
    )).filter(Boolean);

    fixes.filter(x => x.changed_a_test).forEach(x => log(`  WARN — fixer wanted to change a test for ${x.test_id}; ESCALATE to human (invariant integrity).`));
    const stuck = batch.filter(f => !fixes.find(x => x.test_id === f.test_id && x.fixed && !x.changed_a_test));
    stuck.forEach(f => dropped.push({ iter, test_id: f.test_id, reason: "no fixer produced a clean passing code change" }));
    log(`iter ${iter}: attempted ${batch.length}, ${fixes.filter(x => x.fixed && !x.changed_a_test).length} clean-fixed, ${stuck.length} stuck`);
  }

  log(`NOT GREEN after cap. Dropped ${dropped.length}:`);
  dropped.forEach(d => log(`  iter ${d.iter} ${d.test_id}: ${d.reason}`));
  return { green: false, dropped };
}
```

## 5. Verification gates between phases

Each gate is a **mechanical** pass/fail the operator (or the loop-until-green run)
verifies before the next Workflow call. The gate — not an agent's claim — advances
the build. Every gate names the invariant(s) it protects.

| Gate | Runs after | Mechanical check | Protects |
|---|---|---|---|
| **G0** | WP0 | `pytest` imports the app; keyring round-trips a test secret; a repo-wide grep for secret patterns returns **zero** hits. | Inv 4 |
| **G1a** | WP1a | Every schema round-trip test green; the barrier consistency-merge returns `consistent:true`; `create_all`/alembic migration runs on Windows. | Inv 3, 8 |
| **G1b** | WP1b | Mocked-subprocess tests: malformed JSON → **quarantine** (all green), one repair attempt then `needs_human_review`, timeout path, limit-hit parse → **breaker opens**. | Inv 3, 7 |
| **G2** | WP2 | Hash-chain verifies end-to-end; a deliberately mutated row is **detected**; parquet snapshot read-back is lossless. | Inv 6, 8 |
| **G3** | WP3 | Replay-fixture tests pass; reconnect test passes; the Finnhub token bucket is **never** observed to exceed 60/min under load; dedup verified; `raw_ref` points at a separate parquet blob. | Inv 7, 8 |
| **G4** | WP4 | Property test: **no input** yields an approved `RiskDecision` with a failed hard check; static check that `risk/` imports no agent-runner/LLM module (`llm_calls_in_module == 0`); script 4c verdict clean on `risk/engine.py`; post-PDT model asserts **no** day-trade-count field exists. | Inv 1, 5, 9 |
| **G5** | WP5 | State-machine tests reject illegal transitions; performance gate refuses an unlock below threshold; every breaker trip downgrades to `review_live`; `auto_veto`/`auto_full` **refuse to arm** when any decision-stage job is configured for a non-Claude runtime; script 4c verdict clean on `mode_ladder.py`. | Inv 2, 7 |
| **G6** | WP6 | Foreign-user interaction **rejected**; replayed interaction id is idempotent; approval expiry on stale quote voids execution; veto-window race: veto at T-1s wins and execution at T+0 re-checks mode+breakers atomically; script 4c verdict clean on `approval.py`. | Inv 1, 6 |
| **G7** | WP7 | **Invariant-1 call-site grep test**: the only path that reaches `place_*_order`/`submit_order` passes through risk→mode→approval; order state machine rejects illegal transitions; **each state transition writes a hash-chained `AuditEntry` before returning success**; `review_equity_order` provably precedes `place_equity_order`; script 4c verdict clean on `order_state_machine.py`. | Inv 1, 6 |
| **G8** | WP8 | Fixture end-to-end: a recorded market day yields a `MorningBrief` + correctly-gated escalations; deep-model daily budget enforced; **usage-window exhaustion degrades to watch-only, drops no events**. | Inv 7 |
| **G9** | WP9 (global) | Full suite green (via 4d); **secrets-in-logs grep = 0**; a deliberate breaker trip halts before the next order; a mid-loop usage-limit hit enters watch-only; a forged interaction is rejected. | all |

## 6. How security is enforced *during* the build

- **Mandatory adversarial pass on WP4, WP5, WP6, WP7 before acceptance.** Script 4c
  runs on each **against that WP's un-merged worktree branch** (one module per gate;
  never the whole set on the main checkout — that would review an empty tree and
  falsely pass); a single reproducible Invariant-1 bypass, or an absent target file,
  blocks the phase. These four WPs are the only ones that carry the order path, the
  mode ladder, or the approval gate — the heavy passes run *only* here (cost
  discipline, §8). The passes target the enforcement model in [[security-architecture]]
  and the untrusted-input boundary in [[prompt-injection-defense]]; the concrete
  bypasses they must be able to find — capability-boundary escape (SEC-03), submit-time
  TOCTOU (SEC-07), runtime-pin-at-execution (SEC-08), out-of-band kill (SEC-04) — are
  enumerated in [[security-hardening-backlog]]; wire them in as 4c attack angles and as
  tests.
- **Invariant-grep tests are CI, not vibes.** The Invariant-1 call-site test
  (order placement reachable only via the one path), the secrets-in-repo/logs greps,
  and the `risk/`-imports-no-LLM static check are ordinary pytest cases that run in
  every `loop-until-green`. A build agent cannot argue past them.
- **Feature-detect CLI flags; the runner stays authoritative.** `--json-schema` and
  `--effort` are **UNVERIFIED** flags ([[cockpit-review-and-integrated-plan]] §1) —
  WP1b feature-detects them via `claude --help` at startup, but the runner's own
  `jsonschema` validation (repair-once-then-quarantine, Invariant 3) is the reliable
  enforcement layer **regardless** of whether a native flag exists. Build agents must
  never assume a flag they did not detect.
- **Stack + host discipline.** Every build agent respects the settled all-Python
  stack (FastAPI / discord.py / APScheduler / SQLAlchemy→SQLite / DuckDB-Parquet /
  `jsonschema` / `keyring`) and the Windows host. `claude -p` / `codex exec` are
  **subprocesses, not SDKs** — no Node dependency. Codex stays off the decision path
  in code (Invariant 2; OpenAI policy).
- **No pushing.** Build agents commit to their worktree branch and stop. Lucas
  reviews and merges via GitHub Desktop. `git push` is never invoked by an agent.

## 7. Per-WP kickoff prompts

Paste-ready, one per WP session. Each extends the scaffold's single kickoff prompt,
naming the WP's acceptance test and the invariants in scope. All assume the standard
preamble: *read `build-scaffold-ultracode.md`, then
`cockpit-review-and-integrated-plan.md` + `draft-discord-control-plane.md`; build in
`app/`; schema-first; do not deviate from the invariants without
flagging; do not push commits.*

> **WP0** ultracode. Bootstrap the app: `pyproject.toml`, pydantic-settings (mode
> defaults `read_only`), structured logging, `core/secrets.py` (keyring wrapper),
> `core/ids.py`, `core/clock.py`, `models.yaml` (alias→model map). **Accept:** app
> imports cleanly; secrets round-trip via keyring; a repo grep finds no plaintext
> secret. **Invariants in scope: 4.**

> **WP1a** ultracode. Author the JSON Schemas (`TriageResult`, `MorningBrief`,
> `TradeProposal`, `ThesisUpdate`, `InterrogationAnswer`, `CompiledRule`) and
> SQLAlchemy models (`MarketEvent`, `PortfolioSnapshot`, `AgentJob`, `RiskDecision`,
> `ApprovalRecord`, `AuditEntry`, `AutomationRule`, `ModeState`, `BreakerState`,
> `UsageWindow`). Fields per cockpit-plan-original §4 + draft 2; cite each. **Accept:**
> round-trip tests; migration runs on Windows; shared fields agree across schemas.
> **Invariants in scope: 3, 8.** (Drive with script 4a.)

> **WP1b** ultracode. Build the agent runner — the keystone. `run_agent(spec) →
> AgentResult` around `claude -p --output-format json --model <alias> --max-turns N
> --allowedTools <list>` and `codex exec --json`; capture `session_id`; validate
> against the spec schema; repair-then-quarantine; timeout+retry; per-vendor
> `UsageWindow` accounting with limit-hit → breaker; `continue_session`. Feature-detect
> `--json-schema`/`--effort` at startup — runner validation stays authoritative.
> **Accept:** mocked-subprocess tests for happy path, malformed JSON, repair success,
> quarantine, timeout, limit-hit; one env-flagged live smoke test. **Invariants in
> scope: 3, 7, 4.**

> **WP2** ultracode. Build storage + audit: repository layer over the models;
> append-only `AuditEntry` where each row stores `prev_hash`; parquet snapshot writer
> keyed by date/symbol. **Accept:** hash-chain verifies; a mutated row is detected;
> snapshot read-back is lossless. **Invariants in scope: 6, 8.**

> **WP3** ultracode (worktree). Build the market ingestor: Alpaca news WS (wildcard
> subscribe, reconnect+backoff, dedup by article id), Finnhub poller (token bucket ≤
> 60/min + `minId` cursor), earnings/macro calendar; normalize to `MarketEvent` with
> `raw_ref` into parquet. **Accept:** replay-fixture tests; reconnect test;
> rate-limit never exceeded under load; dedup verified. **Invariants in scope: 7, 8.**
> (Drive with script 4b alongside WP4.)

> **WP4** ultracode (worktree; adversarially reviewed). Build the risk engine as pure
> deterministic functions — no LLM in `risk/`. Rules: allowlist, max notional, max
> positions, concentration, daily realized-loss, buying power, quote staleness,
> spread/slippage, intraday-margin-deficit + Reg-T $2k min + settlement (no
> day-trade-count logic), live-mode lock, custom `AutomationRule` eval → `RiskDecision`
> with per-check results. **Accept:** exhaustive per-rule unit tests incl. boundaries;
> property test — no input yields an approved decision with a failed hard check.
> **Invariants in scope: 1, 5, 9.** (Then run script 4c on `risk/engine.py`.)

> **WP5** ultracode (mode ladder adversarially reviewed). Build the FastAPI
> orchestrator: the draft-2 endpoint table; mode-ladder state machine with the
> performance-gate calculator; circuit breakers (loss, consecutive losses,
> schema-failure rate, stale data, broker errors, usage exhaustion) → auto-drop to
> `review_live`; APScheduler jobs (pre-market brew, intraday loop w/ market-hours
> guard, EOD review, daily auto-mode disarm). **Accept:** illegal transitions
> rejected; gates enforced; breaker trips downgrade; auto modes refuse a non-Claude
> decision stage; scheduler fires on a simulated clock. **Invariants in scope: 2, 7,
> 1.** (Then run script 4c on `mode_ladder.py`.)

> **WP6** ultracode (worktree; approval path adversarially reviewed). Build the
> Discord bridge: channel bootstrap; brief/alert/proposal embeds; Approve/Reject/
> Modify/Interrogate buttons with operator-ID allowlist + modal confirm above a
> notional threshold; slash commands; thread replies → `continue_session`; `/ask` →
> fresh-context triage call; `#positions` edit-in-place; `#audit-log` batching;
> Discord-outage fallback (desktop notify + queue). **Accept:** foreign-user
> rejection; idempotent retries; approval expiry; veto-window race (veto at T-1s wins;
> execution re-checks mode+breakers atomically). **Invariants in scope: 1, 6, 4.**
> (Drive with script 4b alongside WP7; then script 4c on `approval.py`.)

> **WP7** ultracode (worktree; order path adversarially reviewed). Build broker
> connectors: `alpaca_paper.py` (REST orders/positions/fills → `PortfolioSnapshot`);
> order state machine (proposed→approved→submitted→filled/cancelled/expired);
> `robinhood_mcp.py` stub calling `review_equity_order` before `place_equity_order`
> even though the platform does not force it. **Accept:** paper round-trip vs Alpaca
> sandbox; state-machine tests; **the Invariant-1 call-site test**. **Invariants in
> scope: 1.** (Then script 4c on `order_state_machine.py`.)

> **WP8** ultracode. Build workflows + prompts: `morning_brew` (ingest window → one
> batched triage call → one deep `MorningBrief` from classified packets → Discord);
> `triage_loop` (deterministic pre-filters → batched triage scoring → deep escalation
> above threshold within the daily deep budget, default 5); `proposals` (deep model
> drafts `TradeProposal` with thesis/invalidation/sizing/source refs). Versioned
> prompt templates in `workflows/prompts/`. **Accept:** fixture end-to-end — recorded
> market day → brief + correctly-gated escalations; budget exhaustion degrades
> correctly. **Invariants in scope: 7, 3.**

> **WP9** ultracode. Wire the full test list into CI-able pytest: one-market-day dry
> run from fixtures (no broker creds); 2-week paper replay runner; deliberate
> breaker-trip test; usage-limit-hit mid-loop test; forged-interaction test;
> secrets-in-logs grep test. **Accept:** all green via `loop-until-green`. **Invariants
> in scope: all.** (Drive with script 4d.)

## 8. Cost & throughput discipline for the build itself

Build agents burn the same Max-20x window the runtime copilot will later contend
for — a **dev-time** cost, distinct from the runtime economics in [[upside-and-roi]].
Discipline:

- **Batch, don't drip.** One fan-out per phase; don't spawn a fresh agent per file
  when one agent can author a coherent module. WP0 is one small agent, not a swarm.
- **Cap concurrency deliberately.** The harness caps at `~min(16, cores-2)`; the
  scripts additionally slice (`MAX_FIXERS_PER_ITER = 12`, `SKEPTICS_PER_MODULE = 5`)
  so a single phase can't monopolize the window. Excess queues rather than piling on.
- **Prefer `pipeline` over `parallel`.** Use a `parallel` barrier *only* where a
  stage genuinely needs all prior results at once: the WP1a consistency merge and the
  4c majority vote. Everywhere else, `pipeline` lets item A reach stage 3 while item B
  is still in stage 1 — no idle barrier burning the window.
- **Heavy passes only where they pay.** The `xhigh` adversarial verdict runs on the
  four security-critical WPs, not on `ingest/` or `storage/`. Reserve `effort:
  "xhigh"` for the order-path verdicts; `high` for implementers; `medium` for the
  suite runner.
- **Watch the window.** `budget.remaining()` guards the loop; when the vendor window
  is thin, run fewer phases per day rather than degrading review quality.
- **The human checkpoint between phases is the throughput regulator.** One Workflow
  call per phase means the operator merges worktrees and clears `must_fix` before the
  next spend — no runaway multi-phase burn, and no un-reviewed security code advancing.

## 9. Anti-patterns (do not do these)

- **Let one agent both write and self-approve the risk engine.** The WP4 test-author
  and the 4c adversary must be *different* agents from the implementer. An author
  re-reading their own hard checks is not an adversarial review.
- **Collapse the schema-first ordering.** Building `ingest/` or `risk/` before WP1a's
  contracts are merged forces a later rewrite and silently drifts field types across
  modules — the exact failure the consistency-merge barrier exists to catch.
- **Skip worktree isolation on parallel file writers.** Two agents editing
  overlapping files without `isolation:"worktree"` corrupt each other's diffs. WP3∥WP4
  and WP6∥WP7 mutate files concurrently → worktrees are mandatory, not optional.
- **Trust an agent's "tests pass" claim without the gate re-running them.** Build
  reports carry a `tests_passing` flag *and* the phase gate independently re-runs the
  suite (4d). The claim is a hint; the re-run is the truth.
- **Let a fixer weaken a test to reach green.** `loop-until-green` forbids editing,
  skipping, or `xfail`-ing tests and forces `changed_a_test=true, fixed=false` to
  escalate — a green suite bought by deleting the Invariant-1 test is worse than a red
  one.
- **Run the heavy adversarial pass on every WP.** It's expensive and pointless on
  non-security modules; run 4c only on WP4/WP5/WP6/WP7.
- **Chain multiple phases into one Workflow call.** That skips the human merge +
  `must_fix` checkpoint and lets un-reviewed order-path code advance.
- **Push commits from an agent.** Ever. Worktree branch + stop; Lucas merges.

---

*Spec: [[build-scaffold-ultracode]] (the WPs) · [[cockpit-review-and-integrated-plan]]
(settled decisions, CLI-vs-SDK) · [[draft-discord-control-plane]] (control plane +
autonomy ladder) · [[security-architecture]] (enforcement model) ·
[[prompt-injection-defense]] (untrusted-input boundary) · [[enhancements-and-additions]]
(post-build roadmap). Constraint source: [[research-report]], [[research-supplement]].*

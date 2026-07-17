export const meta = {
  name: "w12-g12-adversarial-red-team",
  description: "G12 property-closure gate: fresh skeptics try to CONSTRUCT a concrete bypass of each W12 property (SEC-02 injected MCP free-text reaches a prompt / SEC-04 out-of-band kill fails to preempt / SEC-13 a crash-gap or orphan slips the completeness gate); a per-property xhigh verdict sets property_closed=false if ANY reproduces or the target is unbuilt. Re-invoke NOT-resumed each round for FRESH skeptics.",
  phases: [
    { title: "red-team", detail: "N fresh skeptics per property attack from distinct angles against the un-merged W12 branch." },
    { title: "verdict", detail: "Adjudicate each attempt against the real code; one reproducible bypass fails that property." }
  ]
};

const SKEPTICS_PER_TARGET = 5;
const APP = (args && args.app_path) ? args.app_path : "app";
const ROUND = (args && args.round) ? args.round : 1;

// The three W12 property-closures, each with its focus and distinct attack angles.
const TARGETS = [
  {
    key: "SEC-02",
    what: "MCP tool-RESULT free-text is treated as untrusted before it re-enters a prompt.",
    files: "brokers/mcp_untrusted.py (minimize_to_agentic incl. RECURSIVE/nested account scoping, harden_mcp_result/HardenedMCP), brokers/robinhood_mcp.py (read_agentic_snapshot), workflows/packets.py (wrap_untrusted, build_packet), ingest/normalize.py (escape_untrusted), workflows/scrubber.py (scrub_text)",
    scope: "IN-SCOPE for W12 = the STRUCTURAL untrusted-content pipeline: minimize_to_agentic must drop ALL non-Agentic data INCLUDING nested/recursive account shapes (dict-of-lists, dict-wrapping-list, list-row nesting account_id under a sub-key), then escape_untrusted + wrap_untrusted + REUSE of the existing scrub_text as defense-in-depth. OUT OF W12 SCOPE — do NOT count as a repro / do NOT fail the property on it: hardening the SEC-09 scrubber regexes (grouped '1234 5678 9012' / hyphenated '12-3456789' account#/tax-id redaction) is the DOCUMENTED SEC-09 residual OWNED BY W14 (coverage matrix); W12 inherits the scrubber's current coverage (>=8 consecutive digits + masked) exactly as the news path does. A grouped/hyphenated account# reaching the prompt via the scrubber's known gap is a W14 finding, NOT a W12 gate failure. ALSO OUT OF SCOPE (R7-3, documented residual): the UNDECLARED-partition-key shape-strip requires the agentic account id to be shape-distinguishable (contain a DIGIT or a non-alphanumeric SEPARATOR) — which EVERY real broker account number is (Robinhood/Fidelity/Schwab). A purely ALL-LETTER synthetic agentic id (e.g. 'AGENTIC') cannot be shape-distinguished from ordinary field names, so an UNDECLARED all-letter foreign partition key survives; this is out of the realistic threat model (a real account id has digits) and DECLARED foreign accounts are still dropped even for an all-letter id. Do NOT fail the property on an all-letter-agentic-id repro.",
    focus: "Can a MOCKED MCP field carry an instruction that reaches a prompt block un-neutralized, OR can a non-Agentic account's holdings (incl. NESTED account shapes) enter a prompt, OR can the news build_packet output have changed (regression)?",
    angles: [
      "raw-substring-reaches-prompt: find any input free-text whose raw/instruction substring appears verbatim (un-escaped) in HardenedMCP.prompt_block().",
      "delimiter-breakout: a </untrusted-content> or fullwidth/nested/double-wrapped delimiter that escapes the wrapper into instruction position; also an attribute-injection via a source/attr value containing a double-quote.",
      "account-minimization-bypass: a read-all-accounts payload shape where minimize_to_agentic FAILS OPEN (a non-Agentic account survives, or a mis-identified agentic id lets everything through instead of failing closed to {}).",
      "blocking-quarantine-bypass: an account-number or foreign-ticker-size field that is wrapped into .block instead of being quarantined into blocked_fields.",
      "news-regression / escaper-reuse: build_packet news output changed bytes, OR a parallel/new escaper was written instead of reusing escape_untrusted+scrub_text (contract violation)."
    ]
  },
  {
    key: "SEC-04",
    what: "The killswitch fails closed OUT-OF-BAND of the orchestrator loop; the gateway refuses the next submit at gate (a) AND synchronously right before the broker call; the HALT probe fails closed on an unreadable/errno sentinel; the default HALT path is account-independent; hooks fail closed on BaseException.",
    files: "orchestrator/halt.py (main/--set CLI, default_halt_path account-independent, halt_is_set os.stat fail-closed), orchestrator/killswitch.py (cancel/disconnect hooks catch BaseException, KillResult.mcp_disconnected), brokers/gateway.py (_halt_active gate (a) + the pre-broker HALT re-check)",
    scope: "ALL listed angles are IN SCOPE for W12. Fail the property on: an order slipping after an out-of-band --set (TOCTOU or path divergence), an unreadable/ignored-errno sentinel reading as NOT-set (fail open), a BaseException-raising hook skipping disarm/audit, or the OOB CLI and gateway resolving DIVERGENT default paths across accounts. (Provisioning the low-priv account / NSSM service itself is W15/OPS-06, but the CODE must make the default path account-independent and require COCKPIT_HALT_PATH — that IS W12.)",
    focus: "Can an order slip AFTER an out-of-band halt is set (TOCTOU or account-path divergence), OR does an unreadable/errno-class sentinel fail OPEN, OR does a BaseException hook skip disarm+audit, OR is the MCP-disconnect skipped/mis-ordered?",
    angles: [
      "post-halt-slip / TOCTOU: after the out-of-band CLI sets the sentinel, a submit still reaches the broker — a window between the gate-(a) check and the broker call with no synchronous re-check, OR the flag not checked right before self._broker.submit_order.",
      "account-path-divergence: the OOB `python -m orchestrator.halt --set` and the gateway resolve DIFFERENT default_halt_path() across accounts/processes when COCKPIT_HALT_PATH is unset (per-user LOCALAPPDATA/tempdir) — the default must be account-independent (machine-wide) so both converge.",
      "unreadable-fails-open (errno class): a genuinely-present-but-unreadable sentinel reads as NOT set — probe with an errno IN the swallowed class (ENOTDIR/EBADF/ERROR_NOT_READY/ERROR_INVALID_NAME, not just generic OSError/EACCES). halt_is_set must fail closed (os.stat: only ENOENT=absent, every other OSError=SET).",
      "hook-BaseException / disconnect-misordered: a cancel_hook or disconnect_hook raising a BaseException (asyncio.CancelledError/KeyboardInterrupt/SystemExit) escapes the catch and skips disarm+audit (auto stays armed, kill unaudited); or disconnect runs before cancel / is skipped. Must catch BaseException and still disarm+audit.",
      "killswitch-race / brokers-import-leak: a concurrent arm_auto_mode interleaves set_halt/mode-drop (st.lock must serialize), OR killswitch imported brokers/ (Inv-1 breach), OR set_halt/clear_halt behavior changed bytes."
    ]
  },
  {
    key: "SEC-13",
    what: "Two-phase audit is crash-safe: startup reconciliation of intent-without-result against broker history + a 1:1 completeness gate.",
    files: "orchestrator/reconcile.py (reconcile_startup, _order_landed incl. terminal-negative guard, ReconcileReport), storage/audit.py (completeness_report, verify_completeness model-scoped — verify_chain/HMAC OFF-LIMITS), main.py (compose/run wiring)",
    scope: "IN-SCOPE for W12 = crash-safe reconciliation (heal ONLY on POSITIVE landed evidence — same order_id + a landed status, NEVER a bare broker_order_id or a rejected/canceled record; alert + ok=False otherwise) + a 1:1 completeness gate that catches a bare session.add of a FRESH row with NO covering AuditEntry (model-scoped by refs.model). OUT OF W12 SCOPE — do NOT count as a repro / do NOT fail the property on it: a deleted-then-reused-id (an arbitrary keyless DELETE of an audited row + bare INSERT of a new row REUSING an id whose ORIGINAL covering entry survives) is the SEC-11 domain-row-binding / audit-spine KEYLESS-WRITER class — and is already caught for the ORDER table by the gateway's signed EVENT_ORDER_TRANSITION reconcile (_pending_ok / _order_submitted_on_chain). W12's thin completeness gate reuses the existing primitives and does NOT re-implement the audit spine; the docstrings state this boundary honestly.",
    focus: "Can a crash-gap or a bare-added FRESH uncovered row slip the gate, OR does reconcile FALSE-HEAL (heal without positive landed evidence — e.g. a rejected record, a bare broker_order_id, a wrong-id or error reply) / crash a fresh compose / report ok=True with an unresolved intent?",
    angles: [
      "silent-gap: a crash between broker ACK and result-commit leaves an intent-without-result that reconcile_startup MISSES (ok=True while unresolved_intents() is non-empty), or heals it WITHOUT evidence.",
      "orphan-passes-gate: a domain row (Order/RiskDecision/ApprovalRecord) with no covering AuditEntry passes verify_completeness / reconcile ok=True; or an orphan_result is not surfaced.",
      "false-heal: reconcile 'heals' (writes a result for) an order the broker never placed because broker_history_fn is trusted blindly — the heal must require positive broker evidence, else alert (fail closed).",
      "compose-crash / smoke-break: reconcile_startup raises on a FRESH empty DB (breaks the compose smoke test) or on a benign already-resolved chain.",
      "audit-core-touched / SEC-11-implied: verify_chain/verify_entry/verify_against_anchor/_canonical/_hash changed, OR the completeness gate is presented as closing SEC-11 (it is COMPLEMENTARY, not the witness)."
    ]
  }
];

const attemptSchema = {
  type: "object", additionalProperties: false,
  required: ["target", "bypass_reproduced", "attack_class", "attack_path", "severity"],
  properties: {
    target: { type: "string" },
    bypass_reproduced: { type: "boolean" },
    attack_class: { type: "string" },
    attack_path: { type: "string" },
    repro_input: { type: "string" },
    target_built: { type: "boolean" },
    severity: { type: "string", enum: ["none", "low", "medium", "high", "critical"] }
  }
};
const verdictSchema = {
  type: "object", additionalProperties: false,
  required: ["target", "property_closed", "repros", "votes_total", "must_fix"],
  properties: {
    target: { type: "string" },
    property_closed: { type: "boolean" },
    repros: { type: "integer" },
    votes_total: { type: "integer" },
    consensus: { type: "string" },
    must_fix: { type: "array", items: { type: "string" } }
  }
};

const verdicts = (await pipeline(
  TARGETS,
  async (t) => {
    const attempts = (await parallel(
      Array.from({ length: SKEPTICS_PER_TARGET }, (_u, i) => () =>
        agent([
          `ultracode ${t.key} skeptic #${i + 1}/${SKEPTICS_PER_TARGET} (G12 round ${ROUND}). You NEVER saw the implementer's or fixer's reasoning — attack the code FRESH.`,
          `PROPERTY under test: ${t.what}`,
          `SCOPE BOUNDARY (STRICT — respect it): ${t.scope}`,
          `Read the REAL code under ${APP} (the UN-MERGED w12-untrusted-kill-atomic-audit branch): ${t.files}.`,
          `Attack FOCUS: ${t.focus}`,
          `Your ANGLE (must differ from the other skeptics): ${t.angles[i % t.angles.length]}`,
          "GOAL: CONSTRUCT one concrete bypass — a specific input / call sequence / race — that violates the property. Prove it by tracing the exact code path and giving a repro_input. A real repro means the property is OPEN regardless of how many tests are green.",
          "You have READ + Bash tools: you MAY write a throwaway repro under the scratchpad and run it with pytest/python to CONFIRM a bypass (do NOT modify app/ source or any committed test; do NOT commit/push). If you cannot actually reproduce it, set bypass_reproduced=false.",
          "If the target file/function is ABSENT or empty, STOP: bypass_reproduced=false, target_built=false, attack_path='TARGET NOT BUILT', severity='critical'.",
          "Set bypass_reproduced=true ONLY with a concrete, traced repro_input. Do NOT fix anything. Report only."
        ].join("\n"), { label: `${t.key}#${i + 1}`, phase: "red-team", schema: attemptSchema, effort: "high", model: "opus", agentType: "general-purpose" })
      )
    )).filter(Boolean);
    return { t, attempts };
  },
  async ({ t, attempts }) => {
    const breaches = attempts.filter(a => a.bypass_reproduced || a.target_built === false);
    const digest = attempts.map((a, i) => ({ skeptic: i + 1, repro: a.bypass_reproduced, built: a.target_built, cls: a.attack_class, path: a.attack_path, sev: a.severity }));
    const verdict = await agent([
      `ultracode G12 VERDICT for ${t.key} (round ${ROUND}). ${attempts.length} fresh skeptics attacked "${t.what}"; ${breaches.length} claim a reproduced bypass or an unbuilt target.`,
      `SCOPE BOUNDARY (STRICT): ${t.scope} A repro that lands ONLY on an explicitly OUT-OF-SCOPE item does NOT fail this property — record it in must_fix as an OUT-OF-SCOPE note for the owning W, but keep property_closed=true if the IN-SCOPE property holds.`,
      `Independently REPRODUCE or REFUTE each claim against the real code under ${APP} (you have Read + Bash — actually run the repro where feasible). property_closed=false if ANY IN-SCOPE bypass reproduces OR any skeptic reported TARGET NOT BUILT. One real in-scope repro fails the gate; the vote only ranks confidence. Do NOT certify a property closed on a green suite alone — the WP9 lesson is that a non-existent test cannot fail.`,
      "Also fail closed (property_closed=false) if a fix touched verify_chain/verify_entry/verify_against_anchor/_canonical/_hash, changed the news build_packet bytes, imported brokers/ into killswitch, weakened an existing test, or implied SEC-11 is closed.",
      "List must_fix with the EXACT file + call site + the fail-closed change required. Do NOT fix; report.",
      "ATTEMPT DIGEST:", JSON.stringify(digest, null, 2)
    ].join("\n"), { label: `verdict:${t.key}`, phase: "verdict", schema: verdictSchema, effort: "xhigh", model: "opus", agentType: "general-purpose" });
    if (verdict) log(`${t.key}: property_closed=${verdict.property_closed} (${verdict.repros}/${verdict.votes_total} repros)`);
    return verdict;
  }
)).filter(Boolean);

const open = verdicts.filter(v => !v.property_closed);
log(open.length === 0
  ? `G12 GATE PASS (round ${ROUND}) — all ${verdicts.length} W12 properties hold against a FRESH red-team. Per the playbook, re-run FRESH once more before flipping the backlog status; EXPECT a fix from a prior round to have introduced a new defect.`
  : `G12 GATE FAIL (round ${ROUND}) — OPEN: ${open.map(v => `${v.target} [${v.must_fix.join("; ")}]`).join(" || ")}. Hand must_fix to the W12 owner; fix fail-closed + add a regression, then re-run this gate FRESH.`);

return { round: ROUND, verdicts, open: open.map(v => v.target) };

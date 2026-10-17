"""WP8 — the single agent-run choke point (contract Owner B 'agent_calls.py').

Implements docs/build-scaffold-ultracode.md WP8 (agent-call provenance) and the SEC items
it concentrates: SEC-06 (tool starvation — every decision-stage spec is built here with
``allowed_tools=[]``), SEC-08/E-06 (provenance — one ``AgentJob`` row per run with the
resolved model pin, prompt template ref + hash, input refs, and schema id, persisted via
``record_state_change`` so the row is covered by the HMAC chain, Inv 6), SEC-23 (the
usage-window generation is stamped into ``input_refs["_generation"]`` at mint time so
stale analyses are refusable via ``assert_fresh_analysis``), and Inv 7 (usage exhaustion:
a ``limit_hit``/``timeout`` result enters L8 watch-only EXACTLY ONCE — mode unchanged,
no event dropped; callers see the status and degrade).

Every WP8 workflow that (transitively) runs an agent threads ``transport=None`` through
to here so tests inject a FakeTransport (never a real subprocess).
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from agent_runner.result import ERROR, NEEDS_HUMAN, OK, AgentResult
from agent_runner.runner import run_agent, worst_case_repair_overhead
from agent_runner.spec import AgentSpec
from config.models import (
    configured_decision_runtime,
    job_class_for,
    pinned_model_for,
    resolve_decision_claude,
    runtime_allowed_for_mode,
    runtime_for,
)
from core.ids import new_id
from orchestrator.breakers import BreakerEngine
from orchestrator.mode_ladder import ModeLadder
from schemas.models.agent_job import AgentJob
from workflows.constants import EVENT_AGENT_JOB
from workflows.context_limit import context_token_cap, estimate_tokens
from workflows.cost_log import parse_vendor_usage, quote_cost, sum_quotes
from workflows.jev import blocked_jev_attempt, evaluate_decision, jev_weight
from workflows.schema_loader import load_schema, schema_path

# AgentResult.status -> AgentJob.status (anything else is a vendor/limit failure).
_STATUS_MAP = {OK: "done", NEEDS_HUMAN: "quarantined"}


def _quote_raw(pinned: str, raw: str, estimated_input: int):
    vendor = parse_vendor_usage(raw or "")
    if vendor is not None:
        return quote_cost(
            pinned, input_tokens=vendor[0], output_tokens=vendor[1],
            input_token_source="vendor",
        )
    return quote_cost(
        pinned, input_tokens=estimated_input, output_tokens=None,
        input_token_source="estimate",
    )


def _provisioned_secret_values() -> list[str]:
    """BUG-092 (Inv 4): the runner's argv/prompt secret check iterates ``secret_values or
    []`` — and production hardcoded ``None``, so the check inspected NOTHING and a future
    refactor interpolating a credential into a prompt would have passed silently. Feed it
    the PROVISIONED keyring values. Read per call, uncached: a handful of sub-ms local
    credential-store reads per decision call, and a rotated secret is honoured
    immediately. ``reveal()`` scope: the plaintext exists only to be compared against the
    argv/prompt inside ``_assert_no_secret``."""
    from config.settings import _KEYRING_SECRET_NAMES  # test_env_file_guard pins this list
    from core.secrets import get_secret

    values: list[str] = []
    for name in _KEYRING_SECRET_NAMES:
        secret = get_secret(name)
        if secret is None:
            continue
        value = secret.reveal()
        if value:
            values.append(value)
    return values


def _jev_eligible(selected: str, job_class: str, mode, weight, settings, packet) -> bool:
    """Jev is a paper/read-only decision route, never a live or triage route."""
    if job_class != "decision" or packet is None:
        return False
    mode_value = str(getattr(mode, "value", mode)).strip().lower()
    if mode_value not in {"read_only", "paper"}:
        return False
    if selected == "jev":
        return True
    if selected != "auto":
        return False
    number = jev_weight(weight)
    if number is None:
        return False
    ceiling = Decimal(int(getattr(settings, "jev_max_weight", 70)))
    return number <= ceiling


def _jev_window_moved(state, admitted_generation: int) -> bool:
    """True when this Jev call no longer belongs to the generation it was admitted under.

    A sibling 429, or a 503 with Retry-After, can commit a new exhausted generation
    while this HTTP call is in flight. Stamping that new generation would make the
    overlapping success look current.
    """
    if isinstance(admitted_generation, bool) or not isinstance(admitted_generation, int):
        return True
    if BreakerEngine(state).is_watch_only("jev"):
        return True
    return state.usage.window("jev").generation != admitted_generation


def call_agent(state, *, alias: str, prompt_ref: str, prompt_hash: str, prompt: str,
               schema_name: str, input_refs: dict, transport=None, timeout_s: float = 120.0,
               max_turns: int = 6, decision_weight=None, jev_packet=None,
               jev_symbol=None, jev_client=None):
    """Run one tool-starved decision-stage agent and persist its AgentJob row.

    Returns ``(AgentResult, AgentJob)``. On ``limit_hit``/``timeout`` the L8 watch-only
    path (``BreakerEngine.enter_watch_only``) is entered exactly once per exhaustion —
    guarded by the PRE-CALL watch-only state, because the runner itself already trips the
    usage window before we observe the result. Callers must inspect ``result.status`` and
    degrade (defer events, never drop them — Inv 7)."""
    selected_runtime = configured_decision_runtime(state.settings)
    resolved_runtime = runtime_for(alias, selected_runtime)
    mode = ModeLadder(state).current()
    if not runtime_allowed_for_mode(resolved_runtime, mode):
        raise RuntimeError(
            f"decision runtime {resolved_runtime!r} is not allowed in mode {mode.value!r}; "
            "Codex and Jev are limited to human-gated read_only/paper work"
        )
    # Jev is HTTP, not a CLI. Fallback and every non-decision job stay on Claude.
    cli_runtime = "claude" if resolved_runtime == "jev" else resolved_runtime
    job_class = job_class_for(alias, cli_runtime)
    model_id = None
    pinned = pinned_model_for(alias, cli_runtime)
    # Trading decisions on Claude use the allowlisted Haiku id (4.5 fallback,
    # 5.5 when configured). Auto/live already refused a non-Claude runtime above.
    if job_class == "decision" and cli_runtime == "claude":
        model_id, pinned = resolve_decision_claude(
            getattr(state.settings, "decision_model", pinned)
        )
    jev_attempt = None
    jev_generation = None
    try:
        jev_eligible = _jev_eligible(
            selected_runtime, job_class, mode, decision_weight, state.settings, jev_packet,
        )
    except Exception:  # noqa: BLE001 - a bad weight must fall back, not skip Claude
        jev_eligible = False
        jev_attempt = blocked_jev_attempt(state.settings, "jev_error")
    if jev_eligible:
        try:
            if BreakerEngine(state).is_watch_only("jev"):
                jev_attempt = blocked_jev_attempt(state.settings, "jev_vendor_limit")
            else:
                jev_generation = state.usage.window("jev").generation
                jev_attempt = evaluate_decision(
                    state.settings, jev_packet, symbol=jev_symbol,
                    schema=load_schema(schema_name), client=jev_client,
                    secret_values=_provisioned_secret_values(),
                )
                if jev_attempt.vendor_limited:
                    BreakerEngine(state).enter_watch_only(
                        "jev", "jev vendor limit", actor="agent_calls", state="exhausted",
                    )
                elif jev_attempt.accepted and _jev_window_moved(state, jev_generation):
                    # Keep the billed usage. Drop the proposal so Claude still runs.
                    jev_attempt = replace(
                        jev_attempt, accepted=False, reason="jev_stale_attempt", proposal=None,
                    )
                if jev_attempt.dispatched:
                    state.usage.record("jev")
        except Exception:  # noqa: BLE001 - Jev must not skip the Claude fallback
            dispatched = jev_attempt is not None and jev_attempt.dispatched
            if dispatched:
                # HTTP already happened. A later step (watch-only admission, the
                # usage record) must not drop the billed usd and token fields.
                jev_attempt = replace(
                    jev_attempt, accepted=False, reason="jev_error", proposal=None,
                )
                state.usage.record("jev")
            else:
                # Preflight never reached HTTP, so this attempt bills nothing.
                jev_attempt = blocked_jev_attempt(state.settings, "jev_error")
    jev_accepted = jev_attempt is not None and jev_attempt.accepted
    cap = context_token_cap(state.settings)
    if jev_accepted:
        # Jev already trimmed to its own caps and does not run a CLI repair.
        estimated_input = jev_attempt.estimated_input
        context_limited = False
    else:
        estimated_input = estimate_tokens(prompt)
        # Reserve headroom for `_repair`, which appends up to 500 *characters* of
        # rejection text (UTF-8, so 4 bytes each) plus a fixed instruction.
        context_limited = (
            estimated_input + estimate_tokens(worst_case_repair_overhead()) > cap
        )
    engine = BreakerEngine(state)
    # SEC-23: stamp the generation the producing runtime is minted under.
    runtime = "jev" if jev_accepted else cli_runtime
    if jev_accepted:
        pinned = jev_attempt.model_pinned
        # The generation captured before HTTP, not a reread. A limit that commits
        # after this point leaves the job stale instead of current.
        generation = jev_generation
    else:
        generation = state.usage.window(runtime).generation
    job_id = new_id("job")
    pinned_cwd = getattr(state.settings, "decision_agent_cwd", None)
    started = state.clock.now()

    def admit_health_fault(vendor, health_state, error):
        engine.enter_watch_only(
            vendor,
            f"{alias} agent health fault: {error}",
            actor="agent_calls",
            state=health_state,
        )

    if jev_accepted:
        # Typed answers already schema-validated. Do not spawn a CLI.
        result = AgentResult(status=OK, validated=jev_attempt.proposal, raw=jev_attempt.raw)
    elif context_limited:
        # Our cap, not a vendor limit: do not spawn and do not open watch-only.
        result = AgentResult(status=ERROR, error="context_limit")
    else:
        # W11: the runner inspects the ACTUAL argv (tools/MCP), asserts the pinned cwd,
        # and — SEC-05 — refuses to spawn a decision job under the orchestrator's
        # privileged token when a low-priv account is configured. All fail-closed.
        spec = AgentSpec(
            job_id=job_id,
            runtime=cli_runtime,
            model_alias=alias,
            prompt=prompt,
            schema=load_schema(schema_name),
            schema_path=schema_path(schema_name),
            max_turns=max_turns,
            allowed_tools=[],            # SEC-06: decision stages are tool-starved
            timeout_s=timeout_s,
            decision_stage=True,
            cwd=pinned_cwd,              # SEC-06 (W11): pin the decision-agent sandbox cwd
            model_id=model_id,
        )
        result = run_agent(spec, transport=transport, usage=state.usage,
                           secret_values=_provisioned_secret_values(),  # BUG-092: Inv 4
                           pinned_cwd=pinned_cwd,
                           decision_agent_account=getattr(
                               state.settings, "decision_agent_account", None),
                           health_fault=admit_health_fault)
    completed = state.clock.now()

    refs = dict(input_refs or {})
    # Only this function may stamp the route. A caller-supplied value must not
    # survive into the signed job when no Jev attempt ran.
    refs.pop("decision_route", None)
    refs["_generation"] = generation
    if jev_accepted:
        refs["decision_route"] = "jev"
        refs["raw_refs"] = list(jev_attempt.sent_refs)
        prior = refs.get("context_dropped", 0)
        if type(prior) is int and prior >= 0:
            refs["context_dropped"] = prior + jev_attempt.dropped
    elif jev_attempt is not None:
        refs["decision_route"] = "jev_fallback"
    dropped_raw = refs.get("context_dropped", 0)
    context_dropped = dropped_raw if type(dropped_raw) is int and dropped_raw >= 0 else 0
    sent = not context_limited and result.dispatched
    if sent:
        # Price each subprocess call on its own rate tier, then add. Summing tokens
        # first would push two short Haiku 5.5 calls over the 100k long-context line.
        # A Jev success has one raw body (its usage block) and no repair.
        # A repair appends the validation reason; when vendor usage is missing, price
        # that second call with the reserved worst-case suffix, not the first prompt.
        first_estimate = estimated_input
        repair_estimate = estimated_input + estimate_tokens(worst_case_repair_overhead())
        if result.prior_raw:
            quotes = [
                _quote_raw(pinned, result.prior_raw, first_estimate),
                _quote_raw(pinned, result.raw, repair_estimate),
            ]
        else:
            quotes = [_quote_raw(pinned, result.raw, first_estimate)]
        quote = sum_quotes(quotes)
    else:
        quote = quote_cost(
            pinned, input_tokens=estimated_input, output_tokens=None,
            input_token_source="estimate",
        )
    jev_extra = None
    if jev_attempt is not None and not jev_accepted and jev_attempt.usd is not None:
        jev_extra = Decimal(jev_attempt.usd)
    if not sent:
        usage_estimate = jev_extra if jev_extra is not None else Decimal("0")
        cost = quote.as_payload()
        cost["usd_scope"] = "not_sent" if jev_extra is None else "jev_only"
        cost["billed"] = jev_extra is not None
        cost["refused"] = "context_limit" if context_limited else (result.error or "not_sent")
    else:
        if quote.usd is None:
            usage_estimate = jev_extra
        elif jev_extra is not None:
            usage_estimate = Decimal(quote.usd) + jev_extra
        else:
            usage_estimate = Decimal(quote.usd)
        cost = quote.as_payload()
        cost["billed"] = True
    if jev_extra is not None:
        cost["jev_usd"] = format(jev_extra.quantize(Decimal("0.00000001")), "f")
    if not sent and jev_extra is None:
        usage_estimate = Decimal("0")
        cost["usd"] = "0.00000000"
    elif usage_estimate is not None:
        cost["usd"] = format(usage_estimate.quantize(Decimal("0.00000001")), "f")
    if jev_accepted and jev_attempt.response_model not in {None, pinned}:
        cost["response_model"] = jev_attempt.response_model
    cost["context_token_cap"] = cap
    cost["context_dropped"] = context_dropped
    job_status = _STATUS_MAP.get(result.status, "failed")
    job_prompt_ref = prompt_ref
    job_prompt_hash = prompt_hash
    if jev_accepted and jev_attempt.request_hash:
        job_prompt_ref = "jev.decision_questions.v1"
        job_prompt_hash = jev_attempt.request_hash
    job = AgentJob(
        id=job_id,
        runtime=runtime,
        model=alias,
        model_pinned=pinned,
        prompt_template=job_prompt_ref,
        prompt_hash=job_prompt_hash,
        input_refs=refs,
        schema_id=schema_name,
        status=job_status,
        usage_estimate=usage_estimate,
        session_id=result.session_id,
        started_at=started,
        completed_at=completed,
        created_at=started,
    )
    payload = {
        "status": job_status, "model": alias, "runtime": runtime,
        "model_pinned": pinned, "job_class": job_class,
        "prompt_template": job_prompt_ref, "schema_id": schema_name,
        "decision_runtime": selected_runtime,
        "result_status": result.status, "generation": generation,
        "cost": cost,
        # Fixed internal tokens only; never model/feed text. This makes timeout and
        # quarantine causes observable without reading a CLI session transcript.
        "result_error": result.error,
        "repaired": bool(getattr(result, "repaired", False)),
    }
    if "decision_route" in refs:
        payload["decision_route"] = refs["decision_route"]
    if jev_attempt is not None and not jev_accepted:
        payload["jev_attempt"] = jev_attempt.audit_payload()
    state.audit.record_state_change(
        job, EVENT_AGENT_JOB, actor="agent_calls", payload=payload,
    )

    return result, job

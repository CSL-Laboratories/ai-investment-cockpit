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

from agent_runner.result import LIMIT_HIT, NEEDS_HUMAN, OK, TIMEOUT
from agent_runner.runner import run_agent
from agent_runner.spec import AgentSpec
from config.models import (
    configured_decision_runtime,
    pinned_model_for,
    runtime_allowed_for_mode,
    runtime_for,
)
from core.ids import new_id
from orchestrator.breakers import BreakerEngine
from orchestrator.mode_ladder import ModeLadder
from schemas.models.agent_job import AgentJob
from workflows.constants import EVENT_AGENT_JOB
from workflows.schema_loader import load_schema, schema_path

# AgentResult.status -> AgentJob.status (anything else is a vendor/limit failure).
_STATUS_MAP = {OK: "done", NEEDS_HUMAN: "quarantined"}


def call_agent(state, *, alias: str, prompt_ref: str, prompt_hash: str, prompt: str,
               schema_name: str, input_refs: dict, transport=None, timeout_s: float = 120.0,
               max_turns: int = 6):
    """Run one tool-starved decision-stage agent and persist its AgentJob row.

    Returns ``(AgentResult, AgentJob)``. On ``limit_hit``/``timeout`` the L8 watch-only
    path (``BreakerEngine.enter_watch_only``) is entered exactly once per exhaustion —
    guarded by the PRE-CALL watch-only state, because the runner itself already trips the
    usage window before we observe the result. Callers must inspect ``result.status`` and
    degrade (defer events, never drop them — Inv 7)."""
    selected_runtime = configured_decision_runtime(state.settings)
    runtime = runtime_for(alias, selected_runtime)
    mode = ModeLadder(state).current()
    if not runtime_allowed_for_mode(runtime, mode):
        raise RuntimeError(
            f"decision runtime {runtime!r} is not allowed in mode {mode.value!r}; "
            "Codex is limited to human-gated read_only/paper work"
        )
    engine = BreakerEngine(state)
    was_watch_only = engine.is_watch_only(runtime)
    # SEC-23: stamp the generation the analysis is minted under (pre-call window).
    generation = state.usage.window(runtime).generation
    job_id = new_id("job")
    pinned_cwd = getattr(state.settings, "decision_agent_cwd", None)
    spec = AgentSpec(
        job_id=job_id,
        runtime=runtime,
        model_alias=alias,
        prompt=prompt,
        schema=load_schema(schema_name),
        schema_path=schema_path(schema_name),
        max_turns=max_turns,
        allowed_tools=[],            # SEC-06: decision stages are tool-starved
        timeout_s=timeout_s,
        decision_stage=True,
        cwd=pinned_cwd,              # SEC-06 (W11): pin the decision-agent sandbox cwd
    )
    started = state.clock.now()
    # W11: the runner inspects the ACTUAL argv (tools/MCP), asserts the pinned cwd, and — SEC-05 —
    # refuses to spawn a decision job under the orchestrator's privileged token when a low-priv
    # account is configured (the transport must launch the child under it). All fail-closed.
    result = run_agent(spec, transport=transport, usage=state.usage, secret_values=None,
                       pinned_cwd=pinned_cwd,
                       decision_agent_account=getattr(state.settings, "decision_agent_account", None))
    completed = state.clock.now()

    refs = dict(input_refs or {})
    refs["_generation"] = generation
    pinned = pinned_model_for(alias, runtime)
    job_status = _STATUS_MAP.get(result.status, "failed")
    job = AgentJob(
        id=job_id,
        runtime=runtime,
        model=alias,
        model_pinned=pinned,
        prompt_template=prompt_ref,
        prompt_hash=prompt_hash,
        input_refs=refs,
        schema_id=schema_name,
        status=job_status,
        session_id=result.session_id,
        started_at=started,
        completed_at=completed,
        created_at=started,
    )
    state.audit.record_state_change(
        job, EVENT_AGENT_JOB, actor="agent_calls",
        payload={
            "status": job_status, "model": alias, "runtime": runtime,
            "prompt_template": prompt_ref, "schema_id": schema_name,
            "result_status": result.status, "generation": generation,
            # Fixed internal tokens only; never model/feed text. This makes timeout and
            # quarantine causes observable without reading a CLI session transcript.
            "result_error": result.error,
            "repaired": bool(getattr(result, "repaired", False)),
        },
    )

    if result.status in (LIMIT_HIT, TIMEOUT) and not was_watch_only:
        # L8 (Inv 7 / SEC-23): degrade to watch-only ONCE — mode is NOT changed, events
        # are deferred by the calling workflow, never dropped.
        engine.enter_watch_only(
            runtime, f"{alias} agent {result.status}: {result.error or 'usage exhausted'}"
        )
    return result, job

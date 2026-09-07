"""WP8 — SEC-18 fresh-context interrogation (contract Owner B 'interrogation.py').

Implements docs/security-hardening-backlog.md SEC-18 and
docs/draft-discord-control-plane.md (Interrogate): an approve/veto-informing question is
answered by a BRAND-NEW agent context — NEVER ``--resume`` of the session that produced
the proposal (a compromised session would happily repeat its own lie). The fresh context
is rebuilt ONLY from the verified raw store (``resolve_raw`` over the record's
source_refs / input_refs, SEC-13) plus the validated STORED output — both wrapped as
untrusted content. The answer is schema-validated (InterrogationAnswer), forced to
``pattern="fresh"`` / ``session_id=None``, scrubbed (SEC-09; a blocking hit quarantines
the answer text), and audited as ``EVENT_INTERROGATION``. It renders BESIDE the
deterministic RiskDecision (``risk_decision_ref``) — never as sole ground truth (SEC-17).
"""
from __future__ import annotations

import json

from agent_runner.result import OK
from ingest.normalize import escape_untrusted
from schemas.models.agent_job import AgentJob
from schemas.models.proposal_record import ProposalRecord
from workflows.agent_calls import call_agent
from workflows.constants import EVENT_INTERROGATION
from workflows.events import resolve_raw
from workflows.grounding import extract_slice
from workflows.packets import wrap_untrusted
from workflows.prompts import load_prompt
from workflows.scrubber import scrub_output_fields


def _load_target(state, proposal_id: str | None, job_id: str | None):
    """Load the stored ProposalRecord / AgentJob the question is about. Returns
    (source_refs, stored_output, risk_decision_ref, subject_symbol)."""
    if proposal_id is not None:
        with state.session_factory() as s:
            record = s.get(ProposalRecord, proposal_id)
            if record is not None:
                s.expunge(record)
        if record is None:
            raise ValueError(f"no ProposalRecord {proposal_id!r}")
        payload = dict(record.payload or {})
        return (list(payload.get("source_refs") or []), payload,
                record.risk_decision_id, record.symbol)
    if job_id is not None:
        with state.session_factory() as s:
            job = s.get(AgentJob, job_id)
            if job is not None:
                s.expunge(job)
        if job is None:
            raise ValueError(f"no AgentJob {job_id!r}")
        refs = list((job.input_refs or {}).get("raw_refs") or [])
        stored = {"job_id": job.id, "model": job.model, "runtime": job.runtime,
                  "prompt_template": job.prompt_template, "schema_id": job.schema_id,
                  "status": job.status}
        return refs, stored, None, None
    raise ValueError("fresh_interrogate needs a proposal_id or a job_id")


def fresh_interrogate(state, *, question: str, proposal_id: str | None = None,
                      job_id: str | None = None, transport=None, parquet_root=None,
                      clock=None) -> dict:
    """Answer ``question`` about a stored proposal/job in a FRESH context (SEC-18).

    The AgentSpec built by ``call_agent`` never carries a session_id, so the argv can
    never contain ``--resume`` (asserted by G8). Returns the validated, scrubbed
    InterrogationAnswer dict."""
    clock = clock if clock is not None else state.clock
    refs, stored_output, rd_ref, symbol = _load_target(state, proposal_id, job_id)

    # Rebuild context ONLY from the VERIFIED raw store + the validated stored output.
    raw_items: list[dict] = []
    unresolved: list[str] = []
    for ref in refs:
        try:
            raw_items.append(resolve_raw(parquet_root, ref, state=state))
        except Exception:
            unresolved.append(str(ref))   # noted in context; never trusted prose
    context_parts = [extract_slice(raw_items)] if raw_items else []
    if unresolved:
        # BUG-144: the unresolvable-refs line used to be concatenated RAW into the
        # prompt, outside any <untrusted-content> wrapper — a stale/foreign ref carrying
        # prompt-shaped bytes reached the model unwrapped on the SEC-18 fresh-context
        # path. Wrap it like every other untrusted input (refs are identifier-shaped
        # today via normalize, but the wrapper must not depend on that staying true).
        context_parts.append(wrap_untrusted(
            "unresolvable_refs=" + escape_untrusted(",".join(sorted(unresolved))),
            source="interrogation-unresolved-refs",
        ))
    stored_json = json.dumps(stored_output, sort_keys=True, default=str)
    context_parts.append(
        '<untrusted-content source="stored-validated-output">\n'
        f"{escape_untrusted(stored_json)}\n"
        "</untrusted-content>"
    )
    context_block = "\n\n".join(context_parts)

    tmpl = load_prompt("interrogation_fresh")
    prompt = tmpl.render(context_block=context_block, question=str(question))
    result, job = call_agent(
        state, alias="triage", prompt_ref=tmpl.ref, prompt_hash=tmpl.sha256, prompt=prompt,
        schema_name="InterrogationAnswer",
        input_refs={"proposal_id": proposal_id, "job_id": job_id, "raw_refs": refs},
        transport=transport,
    )
    if result.status != OK or not isinstance(result.validated, dict):
        raise RuntimeError(f"fresh interrogation failed: {result.status} ({result.error})")

    answer = dict(result.validated)
    # SEC-18: force the fresh-context contract regardless of what the model claimed.
    answer["pattern"] = "fresh"
    answer["session_id"] = None
    answer["generated_at"] = clock.now().isoformat()
    if rd_ref:
        answer["risk_decision_ref"] = rd_ref

    subject = frozenset({symbol}) if symbol else frozenset()
    answer, blocked = scrub_output_fields(state, answer, ("answer",),
                                          subject_symbols=subject, target="interrogation")
    if blocked:
        # SEC-09: a blocking hit quarantines the text — never post leaked positions.
        answer["answer"] = "[quarantined: outbound scrub blocked this answer (SEC-09)]"

    payload: dict = {"pattern": "fresh", "job_ref": job.id}
    if proposal_id is not None:
        payload["proposal_id"] = proposal_id
    if job_id is not None:
        payload["job_id"] = job_id
    state.audit.append(EVENT_INTERROGATION, actor="interrogation",
                       refs={"agent_job": job.id}, payload=payload)
    return answer

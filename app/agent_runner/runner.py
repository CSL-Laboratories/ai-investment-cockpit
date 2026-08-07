"""WP1b — the agent runner.

run_agent(spec) builds a ``claude -p`` / ``codex exec`` argv, runs it through the
transport, and enforces the safety envelope:
  * SEC-06  — decision-stage jobs run tool-starved (empty allowedTools);
  * Inv 4   — no secret value may appear in the argv or prompt;
  * Inv 7 / SEC-23 — timeout / non-zero exit / limit signal / empty output are
              fail-closed health faults that trip the vendor breaker (never yield output);
  * Inv 3   — output is schema-validated; one repair attempt, then quarantine.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace

import jsonschema

from agent_runner.account_guard import CredentialGuardError
from agent_runner.detect import looks_like_limit
from agent_runner.result import AgentResult, LIMIT_HIT, NEEDS_HUMAN, OK, TIMEOUT
from agent_runner.spec import AgentSpec
from agent_runner.tool_inspector import assert_decision_cwd, inspect_argv
from agent_runner.transport import SubprocessTransport
from agent_runner.usage import UsageTracker
from config.models import load_model_map


def _cli_model(alias: str) -> str:
    return load_model_map().get(alias, {}).get("cli", alias)


def build_argv(spec: AgentSpec, resume: bool = False) -> list[str]:
    # SEC-06: a decision-stage job MUST run with an empty tool set.
    if spec.decision_stage and spec.allowed_tools:
        raise ValueError(
            f"SEC-06: decision-stage job {spec.job_id!r} must have empty allowed_tools, "
            f"got {spec.allowed_tools!r}"
        )
    if spec.runtime == "claude":
        argv = [
            "claude", "-p", "--output-format", "json",
            "--model", _cli_model(spec.model_alias), "--max-turns", str(spec.max_turns),
        ]
        if resume and spec.session_id:
            argv += ["--resume", spec.session_id]
        # explicit, minimal allowedTools; empty set -> the 'none' sentinel
        argv += ["--allowedTools", ",".join(spec.allowed_tools) if spec.allowed_tools else "none"]
        return argv
    if spec.runtime == "codex":
        # Inv 2 / SEC-06: codex is research/coding only — NEVER a decision stage.
        if spec.decision_stage:
            raise ValueError("SEC-06/Inv2: codex may never run a decision-stage job")
        # Codex uses its own sandbox mechanism (not --allowedTools); pin read-only. Put the
        # option before ``resume`` because the current Codex CLI treats ``resume`` as a
        # subcommand and rejects ``--sandbox`` after it. The final ``-`` is load-bearing:
        # codex exec reads the prompt from stdin, which keeps prompts out of argv and gives
        # Claude/Codex the same secret-boundary posture.
        argv = ["codex", "exec", "--sandbox", "read-only"]
        if resume and spec.session_id:
            argv += ["resume", spec.session_id]
        argv += [
            "--model", _cli_model(spec.model_alias),
            "--json", "-",
        ]
        return argv
    raise ValueError(f"unknown runtime {spec.runtime!r}")


def _assert_no_secret(argv: list[str], prompt: str, secret_values) -> None:
    # Inv 4: a secret value must never appear in the argv or the prompt.
    for s in secret_values or []:
        if s and (s in prompt or any(s in a for a in argv)):
            raise ValueError("Inv 4: a secret value appeared in the agent argv/prompt")


def _no_nonfinite(_constant):
    # Inv 3 / SEC-23: reject NaN/Infinity so poison numbers can't validate as "number".
    raise ValueError(f"non-finite JSON constant {_constant!r}")


_FENCE_RE = re.compile(
    r"\A```[A-Za-z0-9_+-]*[ \t]*\r?\n(?P<body>.*?)\r?\n?```\s*\Z",
    re.S,
)


def _strip_code_fence(text: str) -> str:
    """Unwrap only a fence spanning the entire value; leave all other text unchanged."""
    stripped = (text or "").strip()
    match = _FENCE_RE.match(stripped)
    return match.group("body").strip() if match else stripped


def _loads(text: str):
    return json.loads(_strip_code_fence(text), parse_constant=_no_nonfinite)


def _decode_json_value(value):
    """Decode a JSON string returned inside a vendor envelope, preserving plain text."""
    if not isinstance(value, str):
        return value
    try:
        return _loads(value)
    except ValueError:
        return value


def _parse_codex_output(stdout: str, fallback_session_id: str | None = None):
    """Parse Codex's documented JSONL event stream.

    ``codex exec --json`` emits lifecycle events rather than Claude's single JSON envelope. The
    final assistant answer is an ``item.completed`` event whose item type is ``agent_message``;
    its text is normally the JSON object the runner validates. Keep the adapter here so the rest
    of the runner retains one schema/repair/quarantine contract for both runtimes.
    """
    events = []
    malformed_line = False
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line, parse_constant=_no_nonfinite)
        except ValueError:
            # JSONL is the promised protocol. A malformed line means the stream is not safely
            # attributable to Codex, so fail closed rather than silently dropping output.
            malformed_line = True
            continue
        if isinstance(event, dict):
            events.append(event)

    if not events or malformed_line:
        return None, fallback_session_id, None

    session_id = fallback_session_id
    body = None
    has_error = False
    for event in events:
        session_id = (
            event.get("thread_id")
            or event.get("session_id")
            or event.get("threadId")
            or event.get("sessionId")
            or session_id
        )
        event_type = str(event.get("type", "")).lower()
        event_status = str(event.get("status", "")).lower()
        if (event_type in {"error", "turn.failed"} or event.get("is_error")
                or event_status in {"error", "failed"}):
            has_error = True

        item = event.get("item")
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", "")).lower()
        if item_type in {"error", "turn.failed"} or item.get("is_error"):
            has_error = True
        if item_type == "agent_message" and "text" in item:
            # Keep the last completed agent message: intermediate messages are allowed in a
            # JSONL stream, while the final one is the answer subject to schema validation.
            body = _decode_json_value(item.get("text"))

    envelope = {"runtime": "codex", "is_error": has_error}
    if has_error:
        # Reuse the runner's existing vendor-error health-fault branch without pretending Codex
        # emits Claude's ``subtype`` field.
        envelope["subtype"] = "error_codex_event"
    return envelope, session_id, body


def _parse_output(stdout: str, runtime: str = "claude", fallback_session_id: str | None = None):
    """Return (envelope_or_None, session_id, body). None envelope => unparseable/empty."""
    s = (stdout or "").strip()
    if not s:
        return None, fallback_session_id, None
    if runtime == "codex":
        return _parse_codex_output(s, fallback_session_id=fallback_session_id)
    try:
        env = _loads(s)
    except ValueError:
        return None, fallback_session_id, None
    session_id, body = fallback_session_id, env
    if isinstance(env, dict):
        session_id = (env.get("session_id") or env.get("sessionId") or session_id)
        if "result" in env:
            body = env["result"]
            if isinstance(body, str):
                body = _decode_json_value(body)
    return env, session_id, body


def _validate(body, schema) -> str | None:
    try:
        jsonschema.validate(body, schema)
        return None
    except jsonschema.ValidationError as e:
        return str(e.message)


def run_agent(spec: AgentSpec, transport=None, usage=None, secret_values=None,
              resume: bool = False, pinned_cwd: str | None = None,
              decision_agent_account: str | None = None) -> AgentResult:
    transport = transport or SubprocessTransport()
    usage = usage or UsageTracker()

    # Inv 3: a decision-stage job must declare a schema (no unvalidated OK path).
    if spec.decision_stage and spec.schema is None:
        raise ValueError("Inv 3: a decision-stage job requires a schema")

    # SEC-05 (G11 round-1 closure): a decision-stage subprocess must NOT inherit the orchestrator's
    # privileged token — otherwise the account_guard ACL check is theater (the guard verifies the
    # low-priv account cannot read the bearer stores, but nothing spawns the child under it). When a
    # low-priv account is configured, the transport MUST launch the child under THAT account; a
    # transport that runs as the current user (no matching ``account``) is REFUSED, fail closed.
    if spec.decision_stage and decision_agent_account:
        if getattr(transport, "account", None) != decision_agent_account:
            raise CredentialGuardError(
                f"SEC-05: decision-stage job {spec.job_id!r} would spawn under the orchestrator's "
                f"privileged token; account {decision_agent_account!r} is configured but the "
                "transport does not launch the child under it. Refusing to spawn (fail closed)."
            )
    # Inv 7 / SEC-23: while the vendor breaker is open, refuse (watch-only) — never
    # dispatch a decision job to a known-degraded vendor. (WP5 resets the window.)
    if usage.is_open(spec.runtime):
        w = usage.window(spec.runtime)
        return AgentResult(status=LIMIT_HIT, error="breaker_open", usage_state=w.state)

    argv = build_argv(spec, resume=resume)
    if spec.decision_stage:
        # SEC-06: deny-by-default on the ACTUAL argv (tools / MCP) + pin the decision-agent cwd.
        inspect_argv(argv, decision_stage=True)
        assert_decision_cwd(spec.cwd, pinned_cwd=pinned_cwd)
    _assert_no_secret(argv, spec.prompt, secret_values)

    proc = transport.run(argv, spec.prompt, spec.timeout_s, spec.cwd)

    if getattr(proc, "timed_out", False):
        w = usage.trip(spec.runtime, "exhausted")            # a timeout opens the breaker
        return AgentResult(status=TIMEOUT, raw=proc.stdout, error="timeout", usage_state=w.state)

    # Do not scan Codex's JSONL assistant messages for limit words: a valid research answer can
    # discuss a rate limit without the vendor being rate-limited. Exit status and stderr are the
    # transport-level health signals; structured Codex failure events are checked after parsing.
    health_text = proc.stderr or ""
    if proc.returncode != 0 or looks_like_limit(health_text):
        w = usage.trip(spec.runtime, "exhausted")
        return AgentResult(status=LIMIT_HIT, raw=proc.stdout, error="limit_or_error", usage_state=w.state)

    if not (proc.stdout or "").strip():
        # SEC-23: an empty/truncated decision response is a health fault -> fail closed.
        w = usage.trip(spec.runtime, "degraded")
        return AgentResult(status=LIMIT_HIT, raw=proc.stdout, error="empty_output", usage_state=w.state)

    parsed, session_id, body = _parse_output(
        proc.stdout, runtime=spec.runtime, fallback_session_id=spec.session_id
    )

    # A claude JSON envelope can flag an errored/truncated turn (is_error / subtype
    # error_*) while still exiting 0 — treat it as a health fault (SEC-23).
    if isinstance(parsed, dict) and (parsed.get("is_error") or str(parsed.get("subtype", "")).startswith("error")):
        w = usage.trip(spec.runtime, "degraded")
        return AgentResult(status=LIMIT_HIT, raw=proc.stdout, error="vendor_error_envelope", usage_state=w.state)

    usage.record(spec.runtime)

    if spec.schema is None:
        if parsed is None:
            return AgentResult(status=NEEDS_HUMAN, raw=proc.stdout, error="unparseable_output")
        return AgentResult(
            status=OK, validated=body if isinstance(body, dict) else None,
            raw=proc.stdout, session_id=session_id,
        )

    reason = "output was not valid JSON" if parsed is None else _validate(body, spec.schema)
    if reason is None:
        return AgentResult(status=OK, validated=body, raw=proc.stdout, session_id=session_id)

    # one repair attempt, then quarantine (Inv 3)
    return _repair(spec, reason, transport, usage, session_id, secret_values, pinned_cwd)


def _repair(spec: AgentSpec, reason, transport, usage, session_id, secret_values,
            pinned_cwd: str | None = None) -> AgentResult:
    repair_prompt = (
        spec.prompt
        + "\n\nYour previous output was rejected: " + str(reason)[:500]
        + "\nReturn ONLY corrected JSON that matches the required schema; no prose."
    )
    rspec = replace(spec, prompt=repair_prompt, session_id=session_id or spec.session_id)
    argv = build_argv(rspec, resume=bool(session_id))
    if rspec.decision_stage:
        inspect_argv(argv, decision_stage=True)
        assert_decision_cwd(rspec.cwd, pinned_cwd=pinned_cwd)
    _assert_no_secret(argv, rspec.prompt, secret_values)
    proc = transport.run(argv, rspec.prompt, rspec.timeout_s, rspec.cwd)

    health_text = proc.stderr or ""
    if (getattr(proc, "timed_out", False) or proc.returncode != 0
            or looks_like_limit(health_text)):
        w = usage.trip(spec.runtime, "exhausted")
        return AgentResult(status=NEEDS_HUMAN, raw=proc.stdout, repaired=True,
                           error="repair_failed", usage_state=w.state)

    if not (proc.stdout or "").strip():
        # SEC-23: the repair path is fail-closed on a degraded/empty response too.
        w = usage.trip(spec.runtime, "degraded")
        return AgentResult(status=NEEDS_HUMAN, raw=proc.stdout, repaired=True,
                           error="repair_empty_output", usage_state=w.state)

    usage.record(spec.runtime)
    parsed, sid2, body2 = _parse_output(
        proc.stdout, runtime=spec.runtime, fallback_session_id=rspec.session_id
    )
    if isinstance(parsed, dict) and (parsed.get("is_error") or str(parsed.get("subtype", "")).startswith("error")):
        w = usage.trip(spec.runtime, "degraded")
        return AgentResult(status=NEEDS_HUMAN, raw=proc.stdout, repaired=True,
                           error="repair_vendor_error", usage_state=w.state)
    if parsed is not None and _validate(body2, spec.schema) is None:
        return AgentResult(
            status=OK, validated=body2, raw=proc.stdout,
            session_id=sid2 or session_id, repaired=True,
        )
    return AgentResult(
        status=NEEDS_HUMAN, raw=proc.stdout, session_id=sid2 or session_id,
        repaired=True, error="repair_still_invalid",
    )


def continue_session(spec: AgentSpec, message: str, transport=None, usage=None,
                     secret_values=None) -> AgentResult:
    """Resume ``spec.session_id`` with a new operator message (Interrogate / thread reply)."""
    rspec = replace(spec, prompt=message)
    return run_agent(rspec, transport=transport, usage=usage, secret_values=secret_values, resume=True)

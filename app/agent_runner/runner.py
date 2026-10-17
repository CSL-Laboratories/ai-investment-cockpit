"""WP1b — the agent runner.

run_agent(spec) builds a ``claude -p`` / ``codex exec`` argv, runs it through the
transport, and enforces the safety envelope:
  * SEC-06  — decision-stage jobs run with an inspected capability-denial profile;
  * Inv 4   — no secret value may appear in the argv or prompt;
  * Inv 7 / SEC-23 — timeout / non-zero exit / limit signal / empty output are
              fail-closed health faults that trip the vendor breaker (never yield output);
  * Inv 3   — output is schema-validated; one repair attempt, then quarantine.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import replace

import jsonschema

from agent_runner.account_guard import CredentialGuardError
from agent_runner.detect import looks_like_limit
from agent_runner.result import (
    LIMIT_HIT, NEEDS_HUMAN, OK, TIMEOUT, AgentResult, mark_agent_dispatched,
)
from agent_runner.spec import AgentSpec
from agent_runner.tool_inspector import assert_decision_cwd, inspect_argv
from agent_runner.transport import SubprocessTransport
from agent_runner.usage import UsageTracker
from config.models import cli_model_for

log = logging.getLogger(__name__)

# Bound on a schema-rejection reason carried into a log line. jsonschema echoes the offending
# INSTANCE into its error text, and that instance is model output derived from untrusted feed
# text — so the reason is bounded and stays local-log only, never an operator-facing surface.
_REASON_TAIL = 300

# `_repair` slices the rejection to this many *characters* (not bytes) and appends a
# fixed instruction. ``estimate_tokens`` counts UTF-8 bytes, so callers that reserve
# headroom must use ``worst_case_repair_overhead`` (4 bytes per reserved character).
_REPAIR_REASON_CHARS = 500
_REPAIR_PREFIX = "\n\nYour previous output was rejected: "
_REPAIR_SUFFIX = "\nReturn ONLY corrected JSON that matches the required schema; no prose."
_WORST_CASE_REASON_CHAR = "\U0001F600"  # 4-byte UTF-8; matches the character slice


def repair_prompt_for(prompt: str, reason) -> str:
    return prompt + _REPAIR_PREFIX + str(reason)[:_REPAIR_REASON_CHARS] + _REPAIR_SUFFIX


def worst_case_repair_overhead() -> str:
    """The longest suffix `_repair` can append, for context-cap headroom."""
    return _REPAIR_PREFIX + (_WORST_CASE_REASON_CHAR * _REPAIR_REASON_CHARS) + _REPAIR_SUFFIX


# Codex exposes agentic tools as feature flags rather than Claude's ``--allowedTools none``.
# This is the exact deny profile verified against the installed CLI. ``--ignore-user-config``
# also prevents a user's plugins/MCPs from silently reappearing in a cockpit decision job.
CODEX_DECISION_DISABLED_FEATURES = (
    "shell_tool",
    "apps",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "computer_use",
    "image_generation",
    "multi_agent",
    "multi_agent_v2",
    "standalone_web_search",
    "tool_call_mcp_elicitation",
    "skill_search",
    "skill_mcp_dependency_install",
    "code_mode_host",
    "unified_exec",
)


def _cli_model(alias: str, runtime: str) -> str:
    return cli_model_for(alias, runtime)


def _cli_model_arg(spec: AgentSpec) -> str:
    if spec.model_id:
        return spec.model_id
    return _cli_model(spec.model_alias, spec.runtime)


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
            "--model", _cli_model_arg(spec),
            "--max-turns", str(spec.max_turns),
        ]
        if spec.decision_stage:
            if resume:
                raise ValueError("SEC-06: an ephemeral Claude decision job cannot resume")
            # BUG-115: allowedTools is pre-approval, not removal of available tools.
            # Safe/restricted modes preserve subscription auth; --bare does not.
            # Unknown switches on an older CLI fail closed through the exit-code gate.
            argv += [
                "--tools", "", "--disallowedTools", "*",
                "--strict-mcp-config", "--safe-mode", "--restricted",
                "--setting-sources", "", "--disable-slash-commands",
                "--no-session-persistence", "--no-chrome",
            ]
        elif resume and spec.session_id:
            argv += ["--resume", spec.session_id]
        # explicit, minimal allowedTools; empty set -> the 'none' sentinel
        argv += ["--allowedTools", ",".join(spec.allowed_tools) if spec.allowed_tools else "none"]
        return argv
    if spec.runtime == "codex":
        # Codex has no ``--allowedTools none``. Decision jobs therefore use a stronger,
        # mechanically inspected profile: read-only sandbox; ignore all user config/rules;
        # ephemeral state; web explicitly disabled; and every acting/read/egress feature disabled.
        # ``--strict-config`` makes a renamed/removed safety switch fail closed after CLI upgrades.
        # Repair is a fresh turn because ephemeral sessions cannot be resumed safely.
        argv = ["codex", "exec", "--sandbox", "read-only"]
        if spec.decision_stage:
            if resume:
                raise ValueError("SEC-06: an ephemeral Codex decision job cannot resume")
            argv += [
                "--ignore-user-config",
                "--ignore-rules",
                "--ephemeral",
                "--strict-config",
                "-c", 'web_search="disabled"',
            ]
            for feature in CODEX_DECISION_DISABLED_FEATURES:
                argv += ["--disable", feature]
        elif resume and spec.session_id:
            argv += ["resume", spec.session_id]
        argv += [
            "--model", _cli_model_arg(spec),
        ]
        if spec.decision_stage and spec.schema_path:
            argv += ["--output-schema", spec.schema_path]
        argv += ["--json", "-"]
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


def _scrub_prompt_echo(stderr: str, prompt: str | None) -> str:
    """BUG-063 (SEC-25): a CLI can echo its prompt back on stderr on a parse error
    (transport.py keeps the stderr tail for diagnostics and says exactly this), and the
    prompt carries untrusted feed text — so the unattributed stderr scan let a headline's
    words ("capacity", "too long") trip the vendor breaker from the one channel the
    docstring claimed was excluded. A stderr line that appears VERBATIM inside our own
    prompt is an echo of untrusted input, not vendor speech: drop it before scanning.
    Lines shorter than 12 chars are kept so generic fragments ('Error:') cannot vanish
    merely because the prompt happens to contain them. If a feed engineers a real vendor
    phrase into a headline, suppressing its echo is the INTENDED fail direction — feed
    text is data, never control; a genuine vendor limit still lands via the non-zero
    exit, raw stdout text, or an error envelope."""
    if not prompt or not stderr:
        return stderr or ""
    kept = []
    for line in stderr.splitlines():
        s = line.strip()
        if len(s) >= 12 and s in prompt:
            continue
        kept.append(line)
    return "\n".join(kept)


def _limit_signal(stdout: str, stderr: str, prompt: str | None = None) -> bool:
    """Is there a VENDOR limit/health signal here? Scans the CLI's OWN channels, NEVER the
    model's payload.

    THE MODEL'S OUTPUT IS DERIVED FROM UNTRUSTED FEED TEXT, so it must not be able to decide
    vendor health. ``detect.py``'s patterns include ``\\bcapacity\\b``, ``quota``,
    ``overloaded`` and ``too long`` — ordinary market vocabulary. Found live: a triage batch
    containing "British Airways-owner IAG trims capacity outlook" came back with "capacity" in
    the model's own rationale, so a SUCCESSFUL 187s run was reported as a usage limit, the
    breaker tripped, and the whole cockpit degraded to watch-only. Every news day mentioning
    production capacity would have done the same.

    It is also an injection vector, which is what makes this structural rather than cosmetic: a
    headline reading "usage limit reached" would let any feed force the cockpit into watch-only.
    Feed text is DATA, never control (M2/M3) — and a breaker is control.

    NEITHER PRIOR VERSION WAS CORRECT AGAINST BOTH RUNTIMES.

    * main narrowed the scan to stderr only. That closed the injection hole but went blind to a
      real channel: a vendor limit printed as raw text on stdout with exit 0 no longer produced
      LIMIT_HIT. It fell through to "output was not valid JSON", burned a repair round, and
      returned NEEDS_HUMAN — so the breaker never tripped and the next job hammered an
      exhausted vendor.
    * rw01 scanned stdout when ``json.loads`` of the WHOLE buffer failed. That is correct for
      Claude's single envelope and wrong for Codex, which emits JSONL: the whole-buffer parse
      always fails, so it fell back to scanning the model's own ``agent_message`` text and
      reopened SEC-25 for that runtime. rw01 predates Codex support and could not have known.

    Both promised protocols are recognised here, so the excluded channel is exactly the one the
    vendor cannot speak through, on either runtime. Real signals are unaffected: a limit arrives
    as a non-zero exit (checked by the caller), as text on stderr, as raw non-JSON text on
    stdout, or as an ``is_error``/``subtype: error_*`` envelope (checked after parsing).
    """
    if looks_like_limit(_scrub_prompt_echo(stderr or "", prompt)):    # BUG-063
        return True
    text = (stdout or "").strip()
    if not text:
        return False
    try:
        json.loads(text, parse_constant=_no_nonfinite)
        return False                     # claude: one valid envelope — the model's channel
    except ValueError:
        pass
    lines = [ln for ln in (line.strip() for line in text.splitlines()) if ln]
    unparsed = []
    for line in lines:
        try:
            json.loads(line, parse_constant=_no_nonfinite)
        except ValueError:
            unparsed.append(line)
    if not unparsed:
        return False                     # codex: fully JSONL — the model's channel
    if len(unparsed) < len(lines):
        # BUG-064: a MIXED stream (JSONL plus a stray non-JSON line — a truncated tail, a
        # vendor banner) used to downgrade the WHOLE buffer to "not a model payload" and
        # scan the model's agent_message text with it. Only the lines that are NOT the
        # promised protocol are the CLI's own speech; scan exactly those.
        return looks_like_limit("\n".join(unparsed))
    return looks_like_limit(text)        # nothing parsed: pure CLI text


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


def _checked_response(spec, proc, usage, *, repaired: bool = False, health_fault=None):
    """One health gate for initial and repair responses (BUG-147 / CLN-01).

    A health fault always has a status that call_agent persists as watch-only. Schema
    rejection stays separate: it can quarantine output without opening a vendor window.
    Return the decoded response alongside any fault so the caller never parses it twice.
    """
    parsed, session_id, body = None, spec.session_id, None
    status, state = LIMIT_HIT, "exhausted"
    if getattr(proc, "timed_out", False):
        status, error = TIMEOUT, "timeout"
    elif proc.returncode != 0 or _limit_signal(
        proc.stdout or "", proc.stderr or "", spec.prompt
    ):
        # SEC-25: only the CLI's own channels determine vendor health.
        error = "limit_or_error"
    elif not (proc.stdout or "").strip():
        error, state = "empty_output", "degraded"
    else:
        parsed, session_id, body = _parse_output(
            proc.stdout, runtime=spec.runtime, fallback_session_id=spec.session_id
        )
        if not (isinstance(parsed, dict) and (
            parsed.get("is_error") or str(parsed.get("subtype", "")).startswith("error")
        )):
            usage.record(spec.runtime)
            return None, parsed, session_id, body
        error, state = "vendor_error_envelope", "degraded"
    if repaired:
        # Preserve the existing fixed audit tokens; health status is shared across rounds.
        error = "repair_" + ("vendor_error" if error == "vendor_error_envelope" else error)
    if health_fault is None:
        # Dependency-free runner tests retain the local facade. Production injects the
        # signed transition callback below; no production health mutation uses this path.
        window = usage.trip(spec.runtime, state)
    else:
        health_fault(spec.runtime, state, error)
        window = usage.window(spec.runtime)
    fault = AgentResult(status=status, raw=proc.stdout, error=error,
                        usage_state=window.state, repaired=repaired)
    return fault, parsed, session_id, body


def run_agent(spec: AgentSpec, transport=None, usage=None, secret_values=None,
              resume: bool = False, pinned_cwd: str | None = None,
              decision_agent_account: str | None = None, health_fault=None) -> AgentResult:
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
        return AgentResult(status=LIMIT_HIT, error="breaker_open", usage_state=w.state,
                           dispatched=False)

    argv = build_argv(spec, resume=resume)
    if spec.decision_stage:
        # SEC-06: deny-by-default on the ACTUAL argv (tools / MCP) + pin the decision-agent cwd.
        inspect_argv(argv, decision_stage=True)
        assert_decision_cwd(spec.cwd, pinned_cwd=pinned_cwd)
    _assert_no_secret(argv, spec.prompt, secret_values)

    mark_agent_dispatched()
    proc = transport.run(argv, spec.prompt, spec.timeout_s, spec.cwd)
    fault, parsed, session_id, body = _checked_response(
        spec, proc, usage, health_fault=health_fault
    )
    if fault is not None:
        return fault

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

    # Say WHY the repair round is happening. Without this a quarantine is a status code with
    # no cause attached, and the only way to learn what the schema objected to was to reproduce
    # the run. Bounded and local-log only — see _REASON_TAIL.
    log.warning("job %s: schema %s rejected the output, attempting repair: %.*s",
                spec.job_id, spec.schema_path or "<inline>", _REASON_TAIL, str(reason))

    # one repair attempt, then quarantine (Inv 3)
    repaired = _repair(
        spec, reason, transport, usage, session_id, secret_values, pinned_cwd,
        health_fault=health_fault,
    )
    repaired.prior_raw = proc.stdout  # the first call was billed too
    return repaired


def _repair(spec: AgentSpec, reason, transport, usage, session_id, secret_values,
            pinned_cwd: str | None = None, health_fault=None) -> AgentResult:
    repair_prompt = repair_prompt_for(spec.prompt, reason)
    # Decision sessions are intentionally ephemeral (SEC-24 / BUG-115). Repair gets the complete
    # original prompt plus validation error, so it can be a fresh turn without weakening the
    # one-repair contract. Non-decision jobs retain their existing resume behavior.
    ephemeral = spec.decision_stage
    rspec = replace(
        spec,
        prompt=repair_prompt,
        session_id=None if ephemeral else (session_id or spec.session_id),
    )
    argv = build_argv(rspec, resume=bool(session_id) and not ephemeral)
    if rspec.decision_stage:
        inspect_argv(argv, decision_stage=True)
        assert_decision_cwd(rspec.cwd, pinned_cwd=pinned_cwd)
    _assert_no_secret(argv, rspec.prompt, secret_values)
    mark_agent_dispatched()
    proc = transport.run(argv, rspec.prompt, rspec.timeout_s, rspec.cwd)
    fault, parsed, sid2, body2 = _checked_response(
        rspec, proc, usage, repaired=True, health_fault=health_fault
    )
    if fault is not None:
        return fault
    reason2 = "output was not valid JSON" if parsed is None else _validate(body2, spec.schema)
    if reason2 is None:
        return AgentResult(
            status=OK, validated=body2, raw=proc.stdout,
            session_id=sid2 or session_id, repaired=True,
        )
    # The second rejection is the interesting one: the model was TOLD what was wrong and still
    # produced something the schema refuses. Quarantine is correct, but the reason is the only
    # evidence of whether the prompt or the schema is at fault.
    log.warning("job %s: the repaired output was still rejected, quarantining: %.*s",
                spec.job_id, _REASON_TAIL, str(reason2))
    return AgentResult(
        status=NEEDS_HUMAN, raw=proc.stdout, session_id=sid2 or session_id,
        repaired=True, error="repair_still_invalid",
    )


def continue_session(spec: AgentSpec, message: str, transport=None, usage=None,
                     secret_values=None) -> AgentResult:
    """Resume ``spec.session_id`` with a new operator message (Interrogate / thread reply)."""
    rspec = replace(spec, prompt=message)
    return run_agent(rspec, transport=transport, usage=usage, secret_values=secret_values, resume=True)

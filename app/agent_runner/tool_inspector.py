"""W11 (c) — deny-by-default tool/MCP-arg inspector on the ACTUAL constructed argv (SEC-06).

``build_argv`` already refuses a decision-stage *spec* with a non-empty ``allowed_tools`` (the
spec-level SEC-06 gate). This module is the missing MECHANICAL layer the backlog calls for
(SEC-06, L218-232): it inspects the ACTUAL subprocess arg list right before spawn and refuses,
DENY-BY-DEFAULT, any decision-stage argv that grants a read/shell/network tool or attaches an
MCP server — and pins+asserts the decision agent's cwd. It also backs the auto-arm refusal:
``arm_auto_mode`` will not arm ``auto_veto``/``auto_full`` if ANY decision-stage job resolves to a
tool/MCP-bearing argv.

Why an argv inspector when the spec is already gated: exfiltration needs only FS-read + egress,
neither forbidden by a write-only sandbox test (backlog SEC-06). A tool or ``--mcp-config`` flag
that reached the argv by any path other than ``spec.allowed_tools`` — a mutated spec, a changed
builder, an injected flag — is caught HERE, on the bytes that will actually launch the process.
"""
from __future__ import annotations

import re

# Any MCP flag turns a tool-starved decision agent into one holding a live capability (the very
# thing SEC-03/P-B forbids on the decision path). Matched case-insensitively as a whole token,
# covering --mcp, --mcp-config, --mcp-server, --mcpServers, --mcpConfig, and the = forms.
_MCP_FLAG = re.compile(r"^--mcp(-|s|c|$|=)", re.I)

# Flags that grant tools / loosen the permission sandbox on a decision agent (deny-by-default).
_ALLOWED_TOOLS_FLAGS = {"--allowedtools", "--allowed-tools", "--tools", "--add-dir", "--adddir"}
_PERMISSION_LOOSENERS = {"--dangerously-skip-permissions", "--permission-mode",
                         "--permission-prompt-tool",
                         # --settings can carry an inline JSON payload granting tools/MCP/permissions
                         # ({"mcpServers":…} / {"permissions":{"allow":["Bash"]}}); --*system-prompt
                         # injects instructions into a decision agent. All forbidden deny-by-default.
                         "--settings", "--settingsfile", "--settings-file",
                         "--append-system-prompt", "--system-prompt"}

# The empty/sentinel values ``build_argv`` emits for a tool-starved agent (``--allowedTools none``).
_EMPTY_TOOL_SENTINELS = {"", "none", "[]"}


class ToolStarvationError(Exception):
    """A decision-stage agent's actual argv (or cwd) violates the SEC-06 deny-by-default
    tool/MCP starvation. Raised to FAIL CLOSED — the job must not run; an auto mode must not arm."""


def _tool_entries(value: str) -> list[str]:
    """Split an ``--allowedTools`` value into non-sentinel tool names (comma- or space-separated)."""
    parts = re.split(r"[,\s]+", value.strip())
    return [p for p in parts if p and p.lower() not in _EMPTY_TOOL_SENTINELS]


def inspect_argv(argv, *, decision_stage: bool = True) -> None:
    """Deny-by-default inspection of the ACTUAL argv. Raises ``ToolStarvationError`` when a
    decision-stage argv grants any tool, attaches an MCP server, or loosens permissions. A
    non-decision argv (e.g. a codex research job in its own read-only sandbox) is not tool-starved
    here, but an MCP attachment is refused on ANY cockpit-spawned agent."""
    tokens = [str(t) for t in argv]
    for i, tok in enumerate(tokens):
        low = tok.lower()
        flag = low.split("=", 1)[0]

        if _MCP_FLAG.match(low):
            raise ToolStarvationError(
                f"MCP flag {tok!r} is forbidden on a cockpit-spawned agent argv (SEC-03/SEC-06): "
                "a decision agent must hold ZERO MCP servers"
            )
        if not decision_stage:
            continue

        if flag in _PERMISSION_LOOSENERS:
            raise ToolStarvationError(
                f"permission-loosening flag {tok!r} is forbidden on a decision-stage argv (SEC-06)"
            )
        if flag in _ALLOWED_TOOLS_FLAGS:
            # value may be inline (--allowedTools=Bash) or the next token (--allowedTools Bash)
            value = tok.split("=", 1)[1] if "=" in tok else (tokens[i + 1] if i + 1 < len(tokens) else "")
            granted = _tool_entries(value)
            if granted:
                raise ToolStarvationError(
                    f"decision-stage agent must be tool-starved (SEC-06); argv grants {granted}"
                )


def assert_decision_cwd(cwd, *, pinned_cwd) -> None:
    """Pin + assert the decision agent's working directory. When a pinned cwd is configured, a
    decision job whose cwd does not match it is REFUSED (fail closed); when none is configured
    (dev default) this is a no-op."""
    if pinned_cwd is None:
        return
    if cwd != pinned_cwd:
        raise ToolStarvationError(
            f"decision-stage cwd {cwd!r} is not the pinned decision-agent cwd {pinned_cwd!r} (SEC-06)"
        )


def default_decision_stage_specs():
    """Representative specs for the canonical decision-stage aliases (triage, deep) — the argv the
    deployment WOULD launch. ``arm_auto_mode`` inspects these when the caller passes no explicit
    specs, so a wiring that made a decision alias non-Claude (Inv 2) or tool-bearing (SEC-06) is
    caught at arm time, not at first run."""
    from agent_runner.spec import AgentSpec
    from config.models import runtime_for

    aliases = ("triage", "deep")
    return [
        AgentSpec(job_id=f"arm-check:{a}", runtime=runtime_for(a), model_alias=a, prompt="",
                  schema={}, allowed_tools=[], decision_stage=True)
        for a in aliases
    ]


def assert_all_decision_specs_tool_starved(specs) -> None:
    """For each decision-stage spec, build its ACTUAL argv and inspect it deny-by-default. Any
    spec that cannot build a safe argv (a codex decision stage, a tool-bearing spec) or whose argv
    grants tools/MCP raises ``ToolStarvationError`` — the single exception the auto-arm gate reads.
    """
    from agent_runner.runner import build_argv   # lazy: avoids agent_runner import cycle

    for spec in specs or []:
        if not getattr(spec, "decision_stage", False):
            continue
        try:
            argv = build_argv(spec)
        except ValueError as exc:                 # spec-level SEC-06 / Inv-2 refusal
            raise ToolStarvationError(
                f"decision-stage spec {getattr(spec, 'job_id', '?')!r} builds an unsafe argv: {exc}"
            ) from exc
        inspect_argv(argv, decision_stage=True)

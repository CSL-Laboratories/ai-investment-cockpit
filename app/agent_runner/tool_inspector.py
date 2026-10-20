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
from pathlib import Path

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

# A Codex decision job is allowed only through this exact capability-denial profile. The feature
# names come from ``codex features list`` and are emitted centrally by runner.build_argv.
_CODEX_REQUIRED_FLAGS = {
    "--ignore-user-config", "--ignore-rules", "--ephemeral", "--strict-config", "--json",
}
_CODEX_FORBIDDEN_FLAGS = {
    "--enable", "--profile", "-p", "--image", "-i", "--add-dir", "--oss",
    "--local-provider", "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust", "--output-last-message", "-o",
}


class ToolStarvationError(Exception):
    """A decision-stage agent's actual argv (or cwd) violates the SEC-06 deny-by-default
    tool/MCP starvation. Raised to FAIL CLOSED — the job must not run; an auto mode must not arm."""


def _flag_value_at(tokens: list[str], i: int) -> str | None:
    """Value for the flag token at ``tokens[i]``: the inline ``=`` part when present, else
    the following token (``None`` when the flag is the last token)."""
    _, sep, inline = tokens[i].partition("=")
    if sep:
        return inline
    return tokens[i + 1] if i + 1 < len(tokens) else None


def _flag_value(tokens: list[str], *names: str) -> str | None:
    for i, tok in enumerate(tokens):
        if tok.partition("=")[0].lower() not in names:
            continue
        return _flag_value_at(tokens, i)
    return None


def _inspect_codex_decision_argv(tokens: list[str]) -> None:
    """Prove the Codex argv is tool/MCP/egress-starved, not merely read-only.

    Codex loads user plugins, MCPs and feature configuration by default. Therefore a read-only
    sandbox alone is insufficient: it stops writes but could still permit reads/egress. This
    inspector requires the builder's ignored-config + disabled-feature profile byte-for-byte.
    """
    from agent_runner.runner import CODEX_DECISION_DISABLED_FEATURES

    low_tokens = [t.lower() for t in tokens]
    if len(tokens) < 3 or Path(tokens[0]).name.lower() != "codex" or tokens[1] != "exec":
        raise ToolStarvationError("Codex decision argv must start with `codex exec` (SEC-06)")
    missing = sorted(flag for flag in _CODEX_REQUIRED_FLAGS if flag not in low_tokens)
    if missing:
        raise ToolStarvationError(f"Codex decision argv is missing required safety flags {missing}")
    if tokens[-1] != "-":
        raise ToolStarvationError("Codex decision prompt must be read from stdin (`-`), not argv")
    if "resume" in low_tokens:
        raise ToolStarvationError("Codex decision jobs must be ephemeral and cannot resume")
    sandbox = _flag_value(tokens, "--sandbox", "-s")
    if sandbox != "read-only":
        raise ToolStarvationError(
            f"Codex decision sandbox must be read-only, got {sandbox!r} (SEC-06)"
        )
    for tok in tokens:
        flag = tok.lower().split("=", 1)[0]
        if flag in _CODEX_FORBIDDEN_FLAGS:
            raise ToolStarvationError(
                f"Codex decision argv contains capability-granting flag {tok!r} (SEC-06)"
            )

    # The only config override allowed is the exact deny value for built-in web search.
    config_values = [
        _flag_value_at(tokens, i) or ""
        for i, tok in enumerate(tokens)
        if tok.partition("=")[0].lower() in {"-c", "--config"}
    ]
    if config_values != ['web_search="disabled"']:
        raise ToolStarvationError(
            "Codex decision argv must carry only `-c web_search=\"disabled\"` (SEC-06)"
        )

    disabled = {
        tokens[i + 1]
        for i, tok in enumerate(tokens[:-1])
        if tok.lower() == "--disable"
    }
    missing_features = sorted(set(CODEX_DECISION_DISABLED_FEATURES) - disabled)
    if missing_features:
        raise ToolStarvationError(
            f"Codex decision argv leaves capability features enabled: {missing_features}"
        )
    # BUG-119: checking only the first sandbox value and a subset of switches does not
    # prove the actual argv closed. Reject duplicates, aliases and unknown future flags.
    seen = set()
    seen_features = set()
    value_flags = {"--sandbox", "-c", "--model", "--output-schema", "--disable"}
    i = 2
    while i < len(tokens) - 1:
        flag = tokens[i]
        if flag not in _CODEX_REQUIRED_FLAGS | value_flags:
            raise ToolStarvationError(f"unknown Codex decision flag {flag!r} (SEC-06)")
        if flag != "--disable" and flag in seen:
            raise ToolStarvationError(f"duplicate Codex decision flag {flag!r} (SEC-06)")
        seen.add(flag)
        i += 1
        if flag in value_flags:
            if i >= len(tokens) - 1:
                raise ToolStarvationError(f"Codex decision flag {flag!r} lacks a value")
            value = tokens[i]
            i += 1
            if not value or value.startswith("-"):
                raise ToolStarvationError(f"invalid Codex decision value for {flag!r}")
            if flag == "--disable":
                if value not in CODEX_DECISION_DISABLED_FEATURES or value in seen_features:
                    raise ToolStarvationError("unknown/duplicate Codex capability denial")
                seen_features.add(value)
    if not {"--sandbox", "-c", "--model"} <= seen:
        raise ToolStarvationError("Codex decision profile is incomplete (SEC-06)")


def _tool_entries(value: str) -> list[str]:
    """Split an ``--allowedTools`` value into non-sentinel tool names (comma- or space-separated)."""
    parts = re.split(r"[,\s]+", value.strip())
    return [p for p in parts if p and p.lower() not in _EMPTY_TOOL_SENTINELS]


def _inspect_claude_decision_argv(tokens: list[str]) -> None:
    """Require the complete closed grammar, including explicit removal of built-ins.

    No positional prompts, resumed sessions, duplicate overrides or future switches can
    silently broaden this profile. Values are individual argv elements, never shell text.
    """
    switches = {
        "-p", "--strict-mcp-config", "--safe-mode", "--restricted",
        "--disable-slash-commands", "--no-session-persistence", "--no-chrome",
    }
    values = {
        "--output-format": "json", "--tools": "", "--disallowedTools": "*",
        "--setting-sources": "", "--allowedTools": "none",
        "--model": None, "--max-turns": None,
    }
    seen = set()
    i = 1
    while i < len(tokens):
        flag = tokens[i]
        if flag in seen or flag not in switches | values.keys():
            raise ToolStarvationError("Claude decision argv is not tool-starved: unknown/duplicate flag")
        seen.add(flag)
        i += 1
        if flag in values:
            if i == len(tokens):
                raise ToolStarvationError("Claude tool-starved profile has a missing value")
            value = tokens[i]
            i += 1
            expected = values[flag]
            if expected is not None and value != expected:
                raise ToolStarvationError(f"Claude tool-starved profile requires {flag}={expected!r}")
            if flag == "--model" and (not value or value.startswith("-")):
                raise ToolStarvationError("Claude tool-starved profile requires a model")
            if flag == "--max-turns" and (not value.isdecimal() or int(value) < 1):
                raise ToolStarvationError("Claude tool-starved profile requires a positive turn bound")
    if seen != switches | values.keys():
        raise ToolStarvationError("Claude tool-starved profile is missing required safety flags")


def inspect_argv(argv, *, decision_stage: bool = True) -> None:
    """Deny-by-default inspection of the ACTUAL argv. Raises ``ToolStarvationError`` when a
    decision-stage argv grants any tool, attaches an MCP server, or loosens permissions. A
    non-decision argv (e.g. a codex research job in its own read-only sandbox) is not tool-starved
    here, but an MCP attachment is refused on ANY cockpit-spawned agent."""
    tokens = [str(t) for t in argv]
    if decision_stage and tokens and Path(tokens[0]).name.lower() == "codex":
        _inspect_codex_decision_argv(tokens)
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
            value = _flag_value_at(tokens, i) or ""
            granted = _tool_entries(value)
            if granted:
                raise ToolStarvationError(
                    f"decision-stage agent must be tool-starved (SEC-06); argv grants {granted}"
                )
    if decision_stage:
        runtime = Path(tokens[0]).name.lower() if tokens else ""
        if runtime == "claude":
            _inspect_claude_decision_argv(tokens)
        elif runtime != "codex":
            raise ToolStarvationError("unknown decision runtime cannot prove tool starvation")


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


def default_decision_stage_specs(settings=None):
    """Representative specs for the decision-stage aliases — the argv the deployment WOULD
    launch. ``arm_auto_mode`` inspects these when the caller passes no explicit specs, so a
    wiring that made a decision alias non-Claude (Inv 2) or tool-bearing (SEC-06) is caught
    at arm time, not at first run."""
    from agent_runner.spec import AgentSpec
    from config.models import DECISION_STAGE_ALIASES, configured_decision_runtime, runtime_for

    aliases = DECISION_STAGE_ALIASES
    selected = configured_decision_runtime(settings) if settings is not None else None
    return [
        AgentSpec(job_id=f"arm-check:{a}",
                  runtime=(runtime_for(a) if selected is None else runtime_for(a, selected)),
                  model_alias=a, prompt="",
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

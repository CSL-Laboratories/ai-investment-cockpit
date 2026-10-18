"""W11 (c) — deny-by-default tool/MCP-arg inspector on the actual argv (SEC-06).

Pins: a tool-starved decision argv passes; any granted tool, MCP attachment, permission-loosener,
or unpinned cwd is refused; and ``arm_auto_mode`` refuses to arm an auto rung when any
decision-stage job resolves to a tool-bearing argv.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runner.runner import build_argv, run_agent
from agent_runner.spec import AgentSpec
from agent_runner.tool_inspector import (
    ToolStarvationError,
    _flag_value_at,
    assert_all_decision_specs_tool_starved,
    assert_decision_cwd,
    default_decision_stage_specs,
    inspect_argv,
)
from agent_runner.usage import UsageTracker
from config.settings import Mode, Settings
from core.clock import FrozenClock
from orchestrator.gate import GateResult
from orchestrator.mode_ladder import ModeLadder
from orchestrator.state import OrchestratorState
from storage.engine import create_all, make_engine, make_session_factory


def _decision_spec(**kw):
    base = dict(job_id="j1", runtime="claude", model_alias="triage", prompt="classify",
                schema={}, decision_stage=True)
    base.update(kw)
    return AgentSpec(**base)


# ---- inspect_argv: deny-by-default -----------------------------------------------------
def test_starved_decision_argv_passes():
    inspect_argv(build_argv(_decision_spec()), decision_stage=True)   # no raise


@pytest.mark.parametrize("flag,width", [
    ("--tools", 2), ("--disallowedTools", 2), ("--strict-mcp-config", 1),
    ("--safe-mode", 1), ("--restricted", 1), ("--setting-sources", 2),
    ("--no-session-persistence", 1), ("--disable-slash-commands", 1), ("--no-chrome", 1),
])
def test_claude_missing_required_denial_refuses(flag, width):
    argv = build_argv(_decision_spec())
    index = argv.index(flag)
    del argv[index:index + width]
    with pytest.raises(ToolStarvationError):
        inspect_argv(argv)


@pytest.mark.parametrize("extra", [
    ["--tools", ""], ["--setting-sources", "user"], ["--plugin-dir", "/tmp/plugin"],
    ["--chrome"], ["--resume", "saved"], ["--future-tool"], ["positional prompt"],
])
def test_claude_extra_flags_and_prompts_refuse(extra):
    with pytest.raises(ToolStarvationError):
        inspect_argv(build_argv(_decision_spec()) + extra)


def test_permission_allowlist_alone_never_proves_tool_removal():
    with pytest.raises(ToolStarvationError):
        inspect_argv(["claude", "-p", "--allowedTools", "none"])


def test_granted_tool_next_token_refused():
    with pytest.raises(ToolStarvationError, match="tool-starved"):
        inspect_argv(["claude", "-p", "--allowedTools", "Bash"], decision_stage=True)


def test_granted_tool_inline_refused():
    with pytest.raises(ToolStarvationError, match="tool-starved"):
        inspect_argv(["claude", "-p", "--allowedTools=Read,WebFetch"], decision_stage=True)


@pytest.mark.parametrize("flag", ["--mcp-config", "--mcp-server", "--mcpServers", "--mcp", "--mcp=x"])
def test_mcp_flag_refused_even_on_nondecision_argv(flag):
    with pytest.raises(ToolStarvationError, match="MCP"):
        inspect_argv(["codex", "exec", flag, "srv.json"], decision_stage=False)


@pytest.mark.parametrize("flag", ["--dangerously-skip-permissions", "--permission-mode"])
def test_permission_looseners_refused(flag):
    with pytest.raises(ToolStarvationError):
        inspect_argv(["claude", "-p", flag, "acceptEdits"], decision_stage=True)


@pytest.mark.parametrize("flag", ["--settings", "--settings-file", "--append-system-prompt",
                                  "--system-prompt"])
def test_settings_and_system_prompt_flags_refused(flag):
    """G11 round-2 deny-list gap: --settings carries an inline JSON payload that can grant
    tools/MCP/permissions; --*system-prompt injects instructions. Both refused deny-by-default."""
    with pytest.raises(ToolStarvationError):
        inspect_argv(["claude", "-p", flag, '{"permissions":{"allow":["Bash"]}}'],
                     decision_stage=True)


def test_nondecision_codex_readonly_argv_allowed():
    inspect_argv(["codex", "exec", "--sandbox", "read-only", "--json"], decision_stage=False)


# ---- _flag_value_at --------------------------------------------------------------------
@pytest.mark.parametrize("tokens,i,expected", [
    (["--flag=v"], 0, "v"),               # inline nonempty value
    (["--flag", "v", "--other"], 0, "v"),  # next-token value
    (["--flag="], 0, ""),                 # inline empty value (explicit, not missing)
    (["--flag"], 0, None),                # terminal flag: no value
    (["--flag=a=b"], 0, "a=b"),           # inline value keeps later '=' chars
])
def test_flag_value_at(tokens, i, expected):
    assert _flag_value_at(tokens, i) == expected


# ---- cwd pin ---------------------------------------------------------------------------
def test_assert_decision_cwd():
    assert_decision_cwd(None, pinned_cwd=None)                 # dev: not enforced
    assert_decision_cwd("C:/sandbox", pinned_cwd="C:/sandbox")  # match ok
    with pytest.raises(ToolStarvationError):
        assert_decision_cwd("C:/elsewhere", pinned_cwd="C:/sandbox")


def test_runner_refuses_decision_job_in_wrong_cwd():
    """The runner pins the decision-agent cwd: a spec whose cwd != the pinned dir fails closed
    before the subprocess is spawned."""
    class DeadTransport:
        def run(self, *a, **k):
            raise AssertionError("must not spawn")

    with pytest.raises(ToolStarvationError):
        run_agent(_decision_spec(cwd="C:/evil"), transport=DeadTransport(),
                  usage=UsageTracker(), pinned_cwd="C:/sandbox")


# ---- spec-set gate (feeds arm) ---------------------------------------------------------
def test_default_decision_specs_are_starved():
    assert_all_decision_specs_tool_starved(default_decision_stage_specs())   # no raise


def test_tool_bearing_decision_spec_refused():
    with pytest.raises(ToolStarvationError):
        assert_all_decision_specs_tool_starved([_decision_spec(allowed_tools=["Bash"])])


def test_hardened_codex_decision_spec_is_accepted():
    assert_all_decision_specs_tool_starved(
        [_decision_spec(runtime="codex", model_alias="triage")]
    )


def test_codex_decision_argv_missing_required_disable_is_refused():
    from agent_runner.runner import build_argv

    argv = build_argv(_decision_spec(runtime="codex", model_alias="triage"))
    i = argv.index("shell_tool")
    del argv[i - 1:i + 1]
    with pytest.raises(ToolStarvationError, match="shell_tool"):
        inspect_argv(argv, decision_stage=True)


def test_codex_decision_argv_cannot_enable_web_or_resume():
    from agent_runner.runner import build_argv

    base = build_argv(_decision_spec(runtime="codex", model_alias="triage"))
    web = list(base)
    web[web.index('web_search="disabled"')] = 'web_search="live"'
    with pytest.raises(ToolStarvationError, match="web_search"):
        inspect_argv(web, decision_stage=True)
    with pytest.raises(ToolStarvationError, match="resume"):
        inspect_argv([*base[:-1], "resume", "thread-x", "-"], decision_stage=True)


# ---- arm-time gate ---------------------------------------------------------------------
def _state(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'ti.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(sf, hmac_key=b"ti-key", settings=Settings(),
                                   clock=FrozenClock(datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)))


def test_arm_refuses_when_a_decision_job_carries_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("COCKPIT_HALT_PATH", str(tmp_path / "cockpit-HALT"))
    st = _state(tmp_path)
    ladder = ModeLadder(st)
    ladder.set_mode(Mode.paper, actor="op")
    ladder.set_mode(Mode.review_live, actor="op")
    gate = GateResult(unlockable=True, target="auto_veto")

    # a decision job mutated to carry a tool -> arm REFUSES with the SEC-06 reason; nothing armed.
    bad = _decision_spec(job_id="triage", allowed_tools=["Bash"])
    res = ladder.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                               gate_result=gate, operator="op", decision_stage_specs=[bad])
    assert not res.armed
    assert any("SEC-06" in r for r in res.reasons)
    assert ladder.current() is Mode.review_live

    # the DEFAULT (tool-starved) decision specs still arm cleanly — the gate is the discriminator.
    ok = ladder.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                              gate_result=gate, operator="op")
    assert ok.armed and ladder.current() is Mode.auto_veto

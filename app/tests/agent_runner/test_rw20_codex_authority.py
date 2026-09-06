"""Independent BUG-119 attacks on actual argv inspection; no runtime is launched."""
from __future__ import annotations

import pytest

from agent_runner import runner
from agent_runner.runner import build_argv, run_agent
from agent_runner.spec import AgentSpec
from agent_runner.tool_inspector import ToolStarvationError, inspect_argv
from agent_runner.usage import UsageTracker


def _spec(**kwargs):
    return AgentSpec(
        job_id="rw20-isolated-argv", runtime="codex", model_alias="triage",
        prompt="isolated test input", schema={}, decision_stage=True, **kwargs,
    )


@pytest.mark.parametrize("extra", [
    ["--sandbox", "danger-full-access"],
    ["-s", "danger-full-access"],
    ["--sandbox=danger-full-access"],
    ["--sandbox", "read-only"],
    ["--model", "replacement-model"],
    ["--json"],
    ["--ignore-rules"],
    ["--future-tool"],
    ["--future-tool=enabled"],
    ["--plugin-dir", "isolated-untrusted-plugin"],
    ["positional prompt"],
    ["--disable", "unknown_future_feature"],
    ["--disable", "shell_tool"],
])
def test_codex_duplicate_or_unknown_argv_never_establishes_authority(extra):
    argv = build_argv(_spec())
    inspect_argv(argv)  # control: the unchanged production profile is still accepted
    argv[-1:-1] = extra
    with pytest.raises(ToolStarvationError):
        inspect_argv(argv)


@pytest.mark.parametrize("field", ["--model", "--output-schema"])
def test_codex_missing_value_cannot_consume_a_following_safety_switch(field):
    argv = build_argv(_spec(schema_path="isolated-schema.json"))
    del argv[argv.index(field) + 1]
    with pytest.raises(ToolStarvationError):
        inspect_argv(argv)


def test_codex_duplicate_schema_cannot_replace_the_validated_schema():
    argv = build_argv(_spec(schema_path="isolated-schema.json"))
    inspect_argv(argv)
    argv[-1:-1] = ["--output-schema", "replacement-schema.json"]
    with pytest.raises(ToolStarvationError):
        inspect_argv(argv)


def test_codex_actual_argv_mutation_is_refused_before_transport(monkeypatch):
    argv = build_argv(_spec())
    argv[-1:-1] = ["--sandbox", "danger-full-access"]
    monkeypatch.setattr(runner, "build_argv", lambda *_args, **_kwargs: argv)

    class NoSpawnTransport:
        calls = 0

        def run(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("invalid actual argv reached the transport")

    transport = NoSpawnTransport()
    with pytest.raises(ToolStarvationError):
        run_agent(_spec(), transport=transport, usage=UsageTracker())
    assert transport.calls == 0

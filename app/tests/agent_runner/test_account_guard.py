"""W11 (b) — decision-agent credential starvation (SEC-05 / Inv 4).

Pins the fail-closed logic without a second real Windows account: the effective-read predicate is
injected. When the separate account is configured and ANY bearer store is readable by it, the
startup guard raises; when none is readable it passes 'enforced'; with no account configured the
control is reported inactive rather than crashing the app (dev).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent_runner.account_guard import (
    CredentialGuardError,
    assert_credential_starved,
    default_store_paths,
)
from agent_runner.result import (
    OK, agent_dispatched, restore_agent_dispatch_watch, watch_agent_dispatch,
)
from agent_runner.runner import run_agent
from agent_runner.spec import AgentSpec
from agent_runner.transport import LowPrivSubprocessTransport, ProcResult, SubprocessTransport
from agent_runner.usage import UsageTracker

STORES = ["/x/.codex/auth.json", "/x/.claude", "/x/.claude.json"]
_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["x"],
           "properties": {"x": {"type": "integer"}}}


def _dspec(**kw):
    base = dict(job_id="triage", runtime="claude", model_alias="triage", prompt="hi",
                schema=_SCHEMA, decision_stage=True)
    base.update(kw)
    return AgentSpec(**base)


def test_not_enforced_without_a_configured_account():
    status = assert_credential_starved(SimpleNamespace(decision_agent_account=None),
                                       store_paths=STORES, account_can_read=lambda p: True)
    assert status.enforced is False and status.readable == []


def test_fails_closed_when_the_account_can_read_a_store():
    settings = SimpleNamespace(decision_agent_account="cockpit-agent")
    # the codex bearer is still readable by the agent account -> fail closed
    with pytest.raises(CredentialGuardError, match="cockpit-agent"):
        assert_credential_starved(settings, store_paths=STORES,
                                  account_can_read=lambda p: p.endswith("auth.json"))


def test_passes_enforced_when_no_store_is_readable():
    settings = SimpleNamespace(decision_agent_account="cockpit-agent")
    status = assert_credential_starved(settings, store_paths=STORES,
                                       account_can_read=lambda p: False)
    assert status.enforced is True and status.account == "cockpit-agent" and status.readable == []


def test_default_store_paths_cover_the_three_bearers():
    paths = " ".join(default_store_paths()).replace("\\", "/").lower()
    assert ".codex/auth.json" in paths
    assert ".claude" in paths


# ---- SEC-05 spawn-under-account (G11 round-1 closure): the ACL check is theater unless the child
# actually runs under the low-priv account. The runner fails closed when it does not. ----------
def test_runner_fails_closed_when_the_configured_account_is_not_applied_at_spawn():
    """A configured low-priv account whose token is NOT applied at spawn => the decision subprocess
    inherits the orchestrator's privileged token (the confirmed G11 breach). The runner REFUSES."""
    class DeadTransport(SubprocessTransport):
        def run(self, *a, **k):
            raise AssertionError("must not spawn")

    with pytest.raises(CredentialGuardError, match="cockpit-agent"):
        run_agent(_dspec(), transport=DeadTransport(), usage=UsageTracker(),
                  decision_agent_account="cockpit-agent")


def test_account_capable_transport_runs_the_decision_job_under_the_account():
    """With an account-launching transport whose ``account`` matches, the runner proceeds and the
    child is launched UNDER the low-priv account (the spawn seam records it)."""
    launched = {}

    def fake_launch(account, argv, input_text, timeout_s, cwd):
        launched["account"] = account
        return ProcResult(0, json.dumps({"result": json.dumps({"x": 1})}), "", False)

    transport = LowPrivSubprocessTransport("cockpit-agent", launch=fake_launch)
    r = run_agent(_dspec(), transport=transport, usage=UsageTracker(),
                  decision_agent_account="cockpit-agent")
    assert launched["account"] == "cockpit-agent" and r.status == OK


def test_low_priv_on_spawn_marks_only_after_the_child_exists():
    """A launcher that starts the child and then raises has used the unit.

    The same callback must stay unset when the launcher rejects before spawn,
    including the unprovisioned CreateProcessWithLogonW stub.
    """
    def started_then_failed(account, argv, input_text, timeout_s, cwd, on_spawn=None):
        assert account == "cockpit-agent"
        on_spawn()
        raise TimeoutError("child started then the pipe failed")

    def rejected_before_spawn(account, argv, input_text, timeout_s, cwd, on_spawn=None):
        raise NotImplementedError("rejected before spawn")

    started = LowPrivSubprocessTransport("cockpit-agent", launch=started_then_failed)
    token = watch_agent_dispatch()
    try:
        with pytest.raises(TimeoutError, match="pipe failed"):
            started.run(["claude"], "prompt", 1.0, None)
        assert agent_dispatched() is True
    finally:
        restore_agent_dispatch_watch(token)

    rejected = LowPrivSubprocessTransport("cockpit-agent", launch=rejected_before_spawn)
    token = watch_agent_dispatch()
    try:
        with pytest.raises(NotImplementedError, match="rejected before spawn"):
            rejected.run(["claude"], "prompt", 1.0, None)
        assert agent_dispatched() is False
    finally:
        restore_agent_dispatch_watch(token)

    unprovisioned = LowPrivSubprocessTransport("cockpit-agent")
    token = watch_agent_dispatch()
    try:
        with pytest.raises(NotImplementedError, match="not provisioned"):
            unprovisioned.run(["claude"], "prompt", 1.0, None)
        assert agent_dispatched() is False
    finally:
        restore_agent_dispatch_watch(token)


def test_no_account_configured_spawns_normally_dev_posture():
    """Dev posture: no account configured => the runner does not require a low-priv transport."""
    captured = {}

    class FakeTransport:
        def run(self, argv, input_text, timeout_s, cwd):
            captured["ran"] = True
            return ProcResult(0, json.dumps({"result": json.dumps({"x": 1})}), "", False)

    r = run_agent(_dspec(), transport=FakeTransport(), usage=UsageTracker(),
                  decision_agent_account=None)
    assert captured.get("ran") and r.status == OK

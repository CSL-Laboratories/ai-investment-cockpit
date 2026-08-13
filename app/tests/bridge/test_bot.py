"""WP6 — the discord.py adapter imports + guards without discord.py present."""
from __future__ import annotations

import ast
import inspect

from bridge import bot
from bridge.approval import Rejection
from brokers.capability import TokenRegistry

from .conftest import FOREIGN, OPERATOR, make_order, make_risk


class _User:
    def __init__(self, uid):
        self.id = uid


class _Interaction:
    def __init__(self, uid, iid="i-1"):
        self.user = _User(uid)
        self.id = iid
        self.message = None


def test_module_imports_regardless_of_discord():
    # The core adapter symbols exist even if discord.py is absent.
    assert hasattr(bot, "ProposalControls")
    assert hasattr(bot, "build_bot")
    assert isinstance(bot.bot_available(), bool)
    assert set(bot.SLASH_COMMANDS) >= {"mode", "killswitch", "brief", "status", "limits"}


def test_controls_forward_to_core_and_verify_operator(st):
    reg = TokenRegistry()
    risk = make_risk()
    controls = bot.ProposalControls(
        st, reg,
        resolve_order=lambda pid: make_order(),
        resolve_risk=lambda pid: risk,
    )
    # foreign user is refused at the adapter before touching the core
    out = controls.on_decision(_Interaction(FOREIGN), "prop-1", "approve")
    assert isinstance(out, Rejection)
    assert reg.live_count() == 0

    # operator approve flows through to the core + mints a token
    ok = controls.on_decision(_Interaction(OPERATOR, "i-2"), "prop-1", "approve")
    assert not isinstance(ok, Rejection)
    assert reg.live_count() == 1


def test_build_bot_raises_when_discord_absent():
    if bot.bot_available():
        import pytest
        pytest.skip("discord.py is installed in this environment")
    import pytest
    with pytest.raises(RuntimeError):
        bot.build_bot(None, TokenRegistry())


def test_quant_simulation_is_offloaded_from_the_discord_event_loop():
    tree = ast.parse(inspect.getsource(bot.build_bot))
    quant = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_quant"
    )
    calls = [node.func for node in ast.walk(quant) if isinstance(node, ast.Call)]
    assert any(
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "asyncio"
        and func.attr == "to_thread"
        for func in calls
    )

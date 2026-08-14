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


def test_paper_commands_authenticate_and_forward_to_the_single_service(st):
    class Paper:
        def __init__(self):
            self.calls = []

        def preview(self, order_id):
            self.calls.append(("preview", order_id))
            return {"order_id": order_id, "state": "proposed"}

        def decide(self, order_id, **kwargs):
            self.calls.append(("decide", order_id, kwargs))
            return {"order_id": order_id, "status": kwargs["decision"]}

    paper = Paper()
    commands = bot.PaperCommands(st, paper=paper)
    assert commands.handle(
        _Interaction(FOREIGN), action="approve", order_id="ord-1", confirm_symbol="AAPL"
    ) == "Not authorized."
    assert paper.calls == []

    preview = commands.handle(
        _Interaction(OPERATOR, "discord-preview"), action="preview", order_id="ord-1"
    )
    assert '"state": "proposed"' in preview
    approved = commands.handle(
        _Interaction(OPERATOR, "discord-approve"),
        action="approve",
        order_id="ord-1",
        confirm_symbol="AAPL",
    )
    assert '"status": "approve"' in approved
    _, _, forwarded = paper.calls[-1]
    assert forwarded["surface"] == "discord"
    assert forwarded["request_id"] == "discord-approve"


def test_question_commands_authenticate_and_reach_fresh_service(st):
    calls = []

    def ask(state, **kwargs):
        calls.append((state, kwargs))
        return {"pattern": "fresh", "answer": "grounded"}

    commands = bot.QuestionCommands(st, ask=ask)
    assert commands.handle(
        _Interaction(FOREIGN), question="Why?", proposal_id="prop-1"
    ) == "Not authorized."
    assert calls == []
    answer = commands.handle(
        _Interaction(OPERATOR), question="Why?", proposal_id="prop-1"
    )
    assert '"pattern": "fresh"' in answer
    assert calls[0][1] == {"question": "Why?", "proposal_id": "prop-1"}


def test_paper_decision_is_offloaded_from_the_discord_event_loop():
    tree = ast.parse(inspect.getsource(bot.build_bot))
    paper = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_paper"
    )
    calls = [node.func for node in ast.walk(paper) if isinstance(node, ast.Call)]
    assert any(
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "asyncio"
        and func.attr == "to_thread"
        for func in calls
    )


def test_fresh_question_is_offloaded_from_the_discord_event_loop():
    tree = ast.parse(inspect.getsource(bot.build_bot))
    ask = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_ask"
    )
    calls = [node.func for node in ast.walk(ask) if isinstance(node, ast.Call)]
    assert any(
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "asyncio"
        and func.attr == "to_thread"
        for func in calls
    )

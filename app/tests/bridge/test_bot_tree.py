"""WP6/QT-09 — construct the real Discord command tree without login or network.

discord.py is required by the validation profile: a missing dependency fails collection.
The absent-dependency fallback is exercised separately in an isolated subprocess by
``test_bot.py``, so every full matrix cell covers both paths.
"""
from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from bridge.bot import SLASH_COMMANDS, _WIRED_COMMANDS, build_bot


@pytest.fixture
def st():
    return SimpleNamespace(settings=SimpleNamespace(discord_operator_ids="1"))


@pytest.fixture
def tree(st):
    _client, tree = build_bot(st, token_registry=None)
    return tree


def test_the_command_tree_actually_builds(tree):
    """The regression this file exists for. Before the closure fix this raised TypeError."""
    assert isinstance(tree, discord.app_commands.CommandTree)


def test_every_advertised_command_is_registered(tree):
    """``SLASH_COMMANDS`` is the contract; a name listed there but absent from the tree is a
    command an operator will type and get 'unknown application command' from."""
    registered = {c.name for c in tree.get_commands()}
    assert registered == set(SLASH_COMMANDS)


def test_each_command_is_registered_exactly_once(tree):
    names = [c.name for c in tree.get_commands()]
    assert len(names) == len(set(names)), f"duplicate registration: {names}"


def test_a_wired_command_did_not_also_get_a_stub(tree):
    """Both registered, and the two would race to answer the same interaction — which one
    won would depend on registration order. The real body must be the only one."""
    quant = next(c for c in tree.get_commands() if c.name == "quant")
    assert {p.name for p in quant.parameters} >= {"action", "strategy", "events", "seed"}, (
        "the registered /quant is the acknowledge-only stub, not the real body"
    )
    assert _WIRED_COMMANDS <= set(SLASH_COMMANDS)
    paper = next(c for c in tree.get_commands() if c.name == "paper")
    assert {p.name for p in paper.parameters} == {"action", "order_id", "confirm_symbol"}
    ask = next(c for c in tree.get_commands() if c.name == "ask")
    assert {p.name for p in ask.parameters} == {"proposal_id", "question"}


def test_the_quant_parameters_are_optional_so_a_bare_invocation_works(tree):
    """An operator typing `/quant` with no arguments must get the status, not a validation
    error from Discord before the handler is even reached."""
    quant = next(c for c in tree.get_commands() if c.name == "quant")
    assert all(not p.required for p in quant.parameters)


def test_the_stub_commands_expose_no_options(tree):
    """A stub that accidentally advertised a parameter would prompt the operator for input
    it then ignores."""
    for command in tree.get_commands():
        if command.name in _WIRED_COMMANDS:
            continue
        assert not command.parameters, f"/{command.name} should take no options yet"

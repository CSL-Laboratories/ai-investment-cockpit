"""QT-09 — the quant engine on the Discord operator surface.

Two things are being pinned, and the first matters far more than the second.

SEC-17: THE EXECUTION SECTION IS A RISK FACT AND MUST NOT COME FROM THE MODEL
------------------------------------------------------------------------------
The operator decides from what they SEE. The whole reason ``render_risk_section`` reads only
the deterministic ``RiskDecision`` is that a prompt-injected agent must not be able to paint
a benign picture over a dangerous order. Execution quality is exactly that kind of fact —
"cost 1bp, plenty of depth" is a sentence that would get an order approved — so it is read
from the ``RiskDecision`` row and from nowhere else, and the adversarial test below plants a
flattering verdict on the proposal to prove it is ignored.

THE COMMAND SURFACE IS READ-ONLY AND CANNOT REACH A VENUE
-----------------------------------------------------------
``/quant`` runs ``Runner`` over ``SyntheticFeed``. There is no route and no handler that
mutates cockpit state or touches ``AutoLoop``. The strongest thing a compromised operator
account achieves through this surface is making the box do arithmetic.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from bridge.bot import QuantCommands
from bridge.embeds import render_execution_section, render_proposal_embed
from bridge.quant_view import (
    DISCORD_MESSAGE_LIMIT,
    clamp,
    render_execution_line,
)
from orchestrator.quant_api import (
    MAX_SIM_EVENTS,
    STRATEGIES,
    SimRequest,
    quant_status,
    run_sim,
)

VERDICT = {
    "symbol": "AAPL", "side": "buy", "qty": 10, "verdict": "uneconomic",
    "expected_slippage_bps": "2.00", "expected_fee_bps": "0.32",
    "expected_cost_bps": "2.32", "participation_rate": "3.3333",
    "fillable_now": 3, "queue_ahead_shares": 800, "touch_ticks": 10002,
    "reasons": ["order is 3.3333x ALL displayed depth on the side it must consume"],
    "book_seq": 7, "computed_at_ns": 0,
}


def _risk(*, decision="rejected", verdict=VERDICT, failed=("execution_cost_sane",)):
    return SimpleNamespace(
        decision=decision, checks=[{"name": "execution_cost_sane"}, {"name": "spread"}],
        failed_checks=list(failed), warnings=[], max_allowed_notional=Decimal("1000"),
        human_required=False, execution_verdict=verdict,
    )


def _proposal(**kw):
    base = {"proposal_id": "p1", "symbol": "AAPL", "side": "buy", "asset_type": "equity",
            "order_type": "limit", "quantity": 10, "thesis": "t", "invalidation": "i",
            "source_refs": []}
    base.update(kw)
    return base


# ---- SEC-17: the execution section is deterministic ------------------------------------------


def test_the_execution_section_renders_from_the_risk_decision():
    section = render_execution_section(_risk())
    assert section["evaluated"] is True
    assert section["verdict"] == "uneconomic"
    assert section["expected_cost_bps"] == "2.32"
    assert section["fully_fillable"] is False
    assert section["queue_ahead_shares"] == 800


def test_a_verdict_PLANTED_ON_THE_PROPOSAL_IS_IGNORED():
    """THE adversarial test. A hijacked model emits a TradeProposal; if the embed would read
    an execution verdict off that object, the model could write "cost 0.01bps, fully
    fillable" over an order that is neither, and the operator would approve on it.

    The proposal here carries a flattering verdict. The RiskDecision carries the real one.
    The operator must see the real one.
    """
    hostile = _proposal(execution_verdict={
        "verdict": "ok", "expected_cost_bps": "0.01", "fillable_now": 999_999,
        "qty": 10, "reasons": ["totally fine, ship it"],
    })
    embed = render_proposal_embed(hostile, _risk())

    assert embed["execution"]["verdict"] == "uneconomic"
    assert embed["execution"]["expected_cost_bps"] == "2.32"
    assert embed["execution"]["fully_fillable"] is False
    assert "totally fine, ship it" not in str(embed["execution"])


def test_an_unevaluated_decision_says_so_rather_than_implying_it_is_fine():
    """Null is the normal state today — no book feed exists. It must read as 'we did not
    look', never as 'we looked and it was fine'."""
    section = render_execution_section(_risk(verdict=None))
    assert section["evaluated"] is False
    assert "not evaluated" in section["summary"]
    assert "expected_cost_bps" not in section


def test_a_risk_decision_with_no_such_attribute_at_all_degrades_cleanly():
    """An older persisted row, or any object that predates the column. Must not crash the
    render — an embed that raises leaves the operator with no proposal at all."""
    legacy = SimpleNamespace(decision="approved", checks=[], failed_checks=[], warnings=[],
                             max_allowed_notional=None, human_required=False)
    assert render_execution_section(legacy)["evaluated"] is False
    assert render_proposal_embed(_proposal(), legacy)["execution"]["evaluated"] is False


def test_the_embed_still_carries_its_existing_sections():
    """The execution block is ADDITIVE. A new section that displaced the risk section would
    be a regression wearing a feature's name."""
    embed = render_proposal_embed(_proposal(), _risk())
    assert embed["risk"]["decision"] == "rejected"
    assert "execution_cost_sane" in embed["risk"]["failed_checks"]
    assert embed["order"]["symbol"] == "AAPL"
    assert embed["model_thesis"] == "t"


# ---- the operator-facing text ---------------------------------------------------------------------


def test_the_execution_line_leads_with_the_verdict():
    line = render_execution_line(render_execution_section(_risk()))
    assert "UNECONOMIC" in line
    assert "2.32bps" in line
    assert "only 3 shares" in line


def test_a_healthy_execution_line_does_not_cry_wolf():
    """CONTROL: a renderer that warned on everything would pass the test above and be useless."""
    healthy = dict(VERDICT, verdict="ok", fillable_now=10, participation_rate="0.0125",
                   expected_cost_bps="1.32", reasons=[])
    line = render_execution_line(render_execution_section(_risk(verdict=healthy)))
    assert "OK" in line
    assert "⚠" not in line


def test_an_unevaluated_execution_line_is_neutral_not_green():
    line = render_execution_line(render_execution_section(_risk(verdict=None)))
    assert "not evaluated" in line
    assert "🟢" not in line


def test_the_execution_line_never_implies_a_fill_probability():
    """The engine produces no fill probability for a resting order, and the operator surface
    must not invent language that implies one."""
    line = render_execution_line(render_execution_section(_risk()))
    assert "shares ahead" in line
    for banned in ("probability", "chance", "% fill", "likely to fill"):
        assert banned not in line.lower()


def test_money_is_quantized_for_display_but_never_through_a_float():
    """Full precision is correct and unreadable: a borrow accrual over fractional seconds
    legitimately yields 27 decimal places. Display rounds; it must round in Decimal, because
    float() at the last step reintroduces binary rounding on the exact figure the operator
    will quote back."""
    from bridge.quant_view import money

    assert money("-18.60520016950453079076293760") == "-18.61"
    assert money("7.5") == "7.50"
    assert money(None) == "—"
    # The classic float-rounding tell: 2.675 -> 2.67 via binary float, 2.68 via Decimal.
    assert money("2.675") == "2.68"


def test_money_passes_through_anything_it_cannot_parse():
    """A renderer that dies on an unexpected field leaves the operator with no tearsheet —
    strictly worse than one odd-looking line."""
    from bridge.quant_view import money

    assert money("not-a-number") == "not-a-number"


def test_the_tearsheet_shows_readable_money(st, monkeypatch):
    monkeypatch.setattr("bridge.bot.is_authorized", lambda uid, settings: True)
    out = QuantCommands(st).handle(_Interaction(), action="sim", events=1_000)
    net_line = next(line for line in out.splitlines() if line.startswith("net "))
    assert len(net_line.split()[-1].split(".")[-1]) == 2, f"cents, not 27 places: {net_line}"


def test_messages_are_clamped_to_the_discord_limit():
    """Over the cap Discord rejects the WHOLE send and the operator sees nothing — which
    during an incident is indistinguishable from a wedged engine."""
    out = clamp("x" * (DISCORD_MESSAGE_LIMIT + 500))
    assert len(out) <= DISCORD_MESSAGE_LIMIT
    assert out.endswith("[truncated]")


def test_clamping_marks_the_truncation_rather_than_silently_cutting():
    assert clamp("short message") == "short message"          # untouched when it fits


# ---- the API surface --------------------------------------------------------------------------------


def test_status_reports_the_containment_posture_from_the_running_process():
    """An operator asking what the engine may do should get an answer from the process, not
    from a document that may have drifted from it."""
    status = quant_status(None)
    assert status["containment"]["quant_may_import_brokers"] is False
    assert "SimVenue" in status["containment"]["tier_c_venue"]
    assert status["book_feed"] == "not_wired"
    assert status["verification"]["require_quant_verdict"] is False


def test_a_simulation_returns_a_tearsheet_and_is_deterministic():
    first = run_sim(SimRequest(events=2_000, seed=11))
    second = run_sim(SimRequest(events=2_000, seed=11))
    assert first["attribution"] == second["attribution"]
    assert first["events"] == 2_000
    assert first["attribution"]["fills"] >= 0


def test_a_simulation_is_json_native_because_it_crosses_an_http_boundary():
    import json

    payload = run_sim(SimRequest(events=1_000))
    assert json.loads(json.dumps(payload)) == payload


def test_every_tearsheet_carries_the_synthetic_caveat():
    """A tearsheet pasted into a channel outlives the conversation that produced it."""
    assert "SYNTHETIC DATA" in run_sim(SimRequest(events=500))["caveat"]


def test_an_event_count_over_the_cap_is_refused_with_the_ceiling_named():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        run_sim(SimRequest(events=MAX_SIM_EVENTS + 1))
    assert "killswitch" in str(exc.value.detail), "the cap must explain WHY it exists"


def test_an_unknown_strategy_is_refused_rather_than_silently_defaulted():
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        run_sim(SimRequest(strategy="martingale"))


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_every_advertised_strategy_actually_runs(strategy):
    """A status endpoint listing a strategy the sim cannot run is worse than not listing it."""
    assert run_sim(SimRequest(strategy=strategy, events=1_000))["events"] == 1_000


# ---- the slash command ------------------------------------------------------------------------------------


class _Interaction:
    def __init__(self, uid="1"):
        self.user = SimpleNamespace(id=uid)
        self.id = "i1"


def _st(allowed="1"):
    return SimpleNamespace(settings=SimpleNamespace(discord_operator_ids=allowed))


def test_an_unauthorized_user_gets_nothing_back(st):
    """The operator check runs before any work, on every action."""
    commands = QuantCommands(st)
    assert commands.handle(_Interaction(uid="999999"), action="status") == QuantCommands.UNAUTHORIZED
    assert commands.handle(_Interaction(uid="999999"), action="sim") == QuantCommands.UNAUTHORIZED


def test_an_authorized_operator_gets_the_status(st, monkeypatch):
    monkeypatch.setattr("bridge.bot.is_authorized", lambda uid, settings: True)
    out = QuantCommands(st).handle(_Interaction(), action="status")
    assert "Quant engine" in out
    assert "Containment" in out
    assert len(out) <= DISCORD_MESSAGE_LIMIT


def test_an_authorized_operator_can_run_a_simulation(st, monkeypatch):
    monkeypatch.setattr("bridge.bot.is_authorized", lambda uid, settings: True)
    out = QuantCommands(st).handle(_Interaction(), action="sim", events=1_000)
    assert "Sim — market_maker" in out
    assert "spread capture" in out
    assert "SYNTHETIC DATA" in out
    assert len(out) <= DISCORD_MESSAGE_LIMIT


def test_a_bad_event_count_comes_back_as_TEXT_not_an_exception(st, monkeypatch):
    """A handler that raises surfaces to the operator as silence — the interaction times out,
    which during an incident is indistinguishable from a wedged engine."""
    monkeypatch.setattr("bridge.bot.is_authorized", lambda uid, settings: True)
    out = QuantCommands(st).handle(_Interaction(), action="sim", events=MAX_SIM_EVENTS + 1)
    assert out.startswith("⚠")
    assert "killswitch" in out


def test_an_unknown_action_is_explained_rather_than_ignored(st, monkeypatch):
    monkeypatch.setattr("bridge.bot.is_authorized", lambda uid, settings: True)
    out = QuantCommands(st).handle(_Interaction(), action="liquidate_everything")
    assert "Unknown /quant action" in out


def test_an_unexpected_failure_still_returns_text(st, monkeypatch):
    """Belt and braces for the same reason: no exception may escape into the Discord handler."""
    monkeypatch.setattr("bridge.bot.is_authorized", lambda uid, settings: True)
    monkeypatch.setattr("orchestrator.quant_api.quant_status",
                        lambda _st: (_ for _ in ()).throw(RuntimeError("boom")))
    out = QuantCommands(st).handle(_Interaction(), action="status")
    assert out.startswith("⚠")
    assert "boom" in out


def test_the_quant_command_is_registered_and_is_not_also_a_stub():
    """Both registered, and the stub would race the real body for the same interaction —
    which one answered would depend on registration order."""
    from bridge.bot import _WIRED_COMMANDS, SLASH_COMMANDS

    assert "quant" in SLASH_COMMANDS
    assert "quant" in _WIRED_COMMANDS


def test_the_quant_surface_imports_nothing_from_brokers():
    """orchestrator/app.py's header rule, and QT-07's containment. A new operator-facing
    surface is exactly where a broker reach would slip in."""
    import ast
    import pathlib

    from bridge import quant_view
    from orchestrator import quant_api

    for module in (quant_api, quant_view):
        tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                roots.add(node.module.split(".")[0])
        assert "brokers" not in roots, module.__name__


def test_the_quant_surface_never_reaches_the_autonomous_loop():
    """``/quant sim`` drives Runner (research), never AutoLoop (Tier C). An operator command
    that could start an unattended trading loop would be a different thing entirely.

    Checked over IMPORTED NAMES, not raw source text. A text search fails here for a reason
    worth stating: this module's docstring names ``AutoLoop`` in order to say it is not used,
    so grepping would flag the very comment that documents the property. An import is a
    structural fact; prose about an import is not.
    """
    import ast
    import pathlib

    from orchestrator import quant_api

    tree = ast.parse(pathlib.Path(quant_api.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(a.name for a in node.names)
            if node.module:
                imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)

    assert not any("execution" in name for name in imported), (
        f"the operator surface must not import quant.execution (Tier C): {imported}"
    )
    for banned in ("AutoLoop", "SimVenue", "Guards"):
        assert banned not in imported, f"the operator surface must not import {banned}"

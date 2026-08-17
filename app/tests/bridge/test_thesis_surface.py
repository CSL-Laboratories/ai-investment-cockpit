"""MEM-01 — the operator half: seeing standing theses and retiring one.

A thesis memory nobody can see and nobody can close is a library, not a control. These tests
pin the half that turns a stored ``invalidation`` field into discipline:

  * THE OPERATOR IS THE ONLY THING THAT CAN CLOSE A THESIS, and the acting id is recorded.
  * CLOSING REQUIRES A STATED REASON. A thesis retired with no reason leaves no record of
    why, which is the only part worth keeping.
  * THE INVALIDATED COUNT IS SURFACED — a system that reports only its live positions cannot
    distinguish discipline from a run of luck.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bridge.bot import ThesisCommands
from bridge.thesis_view import render_embed_theses, render_open_theses
from workflows.theses import open_theses, open_thesis
from workflows.theses import views as thesis_views


OPERATOR = "424242424242424242"
STRANGER = "919191919191919191"
"""Real Discord SNOWFLAKE shapes. ``normalize_operator_ids`` rejects anything else (17-19
digits), and ``is_authorized`` denies everyone on malformed input — so a short fake id like
"1" does not merely fail to authorize, it silently makes every test exercise the refusal
path. The allowlist also holds AT MOST ONE id by design (_OPERATOR_IDS_MAX_ENTRIES)."""


class _Interaction:
    def __init__(self, uid=OPERATOR):
        self.user = SimpleNamespace(id=uid)
        self.id = "i1"


def _open(st, clock, **kw):
    p = {"proposal_id": "prop-1", "symbol": "AAPL", "side": "buy",
         "thesis": "demand accelerating", "invalidation": "a close below support",
         "source_refs": []}
    p.update(kw)
    return open_thesis(st, clock, proposal=p, proposal_id=p["proposal_id"])


@pytest.fixture(autouse=True)
def authed(monkeypatch):
    """Allowlist the operator for REAL, via the env the allowlist actually reads.

    Two details, both learned the hard way:

    * It deliberately does NOT monkeypatch ``is_authorized``. Doing so patched only
      ``bridge.bot``'s import of it and left ``workflows.theses``' own import untouched — so
      these tests exercised the adapter's gate while the core's gate (added in the adversarial
      round) silently refused. Setting the allowlist for real exercises BOTH.
    * AUTOUSE, so it runs before the ``st`` fixture constructs ``Settings``. That ordering is
      load-bearing: ``authorized_operator_ids`` treats the PRESENCE of a settings snapshot as
      authoritative even when empty, precisely so "a startup deny-all snapshot never becomes
      permissive because somebody mutates os.environ later in the process". Setting the env
      after Settings is built is therefore a no-op by design. Mirrors
      ``tests/system/test_paper_replay.py``.
    """
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR)


# ---- authorization -----------------------------------------------------------------------


def test_an_unauthorized_user_cannot_list_or_close(st):
    cmd = ThesisCommands(st)
    assert cmd.handle(_Interaction(STRANGER), action="list") == ThesisCommands.UNAUTHORIZED
    assert cmd.handle(_Interaction(STRANGER), action="invalidate",
                      thesis_id="x", reason="y") == ThesisCommands.UNAUTHORIZED


def test_closing_records_the_ACTING_operator_not_a_generic_actor(st, clock, authed):
    """'system' or 'model' would make the invalidation condition advisory. A specific human
    is on the record for the judgement call."""
    record = _open(st, clock)
    out = ThesisCommands(st).handle(_Interaction(OPERATOR), action="invalidate",
                                    thesis_id=record.id, reason="closed below support")
    assert "invalidated" in out
    assert OPERATOR in out

    with st.session_factory() as s:
        from schemas.models.thesis_record import ThesisRecord
        row = s.get(ThesisRecord, record.id)
        assert row.closed_by == OPERATOR
        assert row.closed_reason == "closed below support"


# ---- the loop actually closes --------------------------------------------------------------


def test_listing_shows_the_invalidation_the_operator_is_being_asked_about(st, clock, authed):
    _open(st, clock)
    out = ThesisCommands(st).handle(_Interaction(), action="list")
    assert "Standing theses" in out
    assert "AAPL" in out
    assert "close below support" in out, "the question being asked must be visible"


def test_an_empty_book_still_reports_the_invalidated_count(st, authed):
    out = ThesisCommands(st).handle(_Interaction(), action="list")
    assert "None open" in out
    assert "INVALIDATED" in out


def test_closing_without_a_reason_is_refused(st, clock, authed):
    record = _open(st, clock)
    out = ThesisCommands(st).handle(_Interaction(), action="invalidate",
                                    thesis_id=record.id, reason="")
    assert out.startswith("⚠")
    assert "reason" in out
    assert open_theses(st), "the thesis must still be open after a refused close"


def test_closing_without_an_id_points_at_list(st, authed):
    out = ThesisCommands(st).handle(_Interaction(), action="invalidate", reason="r")
    assert "action:list" in out


def test_an_unknown_action_is_explained(st, authed):
    assert "Unknown /thesis action" in ThesisCommands(st).handle(_Interaction(), action="delete_all")


def test_a_thesis_error_comes_back_as_TEXT_not_an_exception(st, authed):
    """A handler that raises surfaces to the operator as silence — during an incident that is
    indistinguishable from a wedged system."""
    out = ThesisCommands(st).handle(_Interaction(), action="close",
                                    thesis_id="nope", reason="r")
    assert out.startswith("⚠")
    assert "no thesis" in out


# ---- rendering ---------------------------------------------------------------------------------


def test_the_render_fits_a_discord_message(st, clock, authed):
    for i in range(30):
        _open(st, clock, proposal_id=f"p{i}", symbol=f"SYM{i}",
              thesis="x" * 400, invalidation="y" * 400)
    out = render_open_theses(thesis_views(open_theses(st)), invalidated=3)
    assert len(out) <= 2000


def test_the_embed_line_warns_about_an_existing_position_thesis():
    line = render_embed_theses([{"side": "buy", "symbol": "AAPL",
                                 "invalidation": "a close below support"}])
    assert "existing BUY thesis on AAPL" in line
    assert "close below support" in line


def test_no_standing_thesis_renders_nothing_rather_than_an_empty_banner():
    assert render_embed_theses([]) == ""


@pytest.mark.parametrize("action,expected", [
    ("invalidate", "invalidated"),
    ("close", "closed"),
    ("supersede", "superseded"),
])
def test_EVERY_verb_maps_to_a_valid_stored_status(st, clock, authed, action, expected):
    """REGRESSION. The operator verbs are imperative and the stored statuses are past-tense.
    Passing the action through unchanged made `close` and `supersede` fail while `invalidate`
    worked — a partial mapping, and the shape of bug a single-case test misses."""
    from schemas.models.thesis_record import ThesisRecord

    record = _open(st, clock, proposal_id=f"p-{action}")
    out = ThesisCommands(st).handle(_Interaction(), action=action,
                                    thesis_id=record.id, reason="operator judgement")
    assert not out.startswith("⚠"), out
    with st.session_factory() as s:
        assert s.get(ThesisRecord, record.id).status == expected

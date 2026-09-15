"""RW-01 — the ``deliver(channel, payload) -> bool`` seam and its Discord adapter.

Pins the properties the pipeline depends on: an unmapped channel FAILS CLOSED rather than
posting somewhere else, a transport failure is REPORTED as undelivered rather than raised or
silently smoothed into a success, the message is clamped under Discord's ceiling, and no token
ever reaches a log line.
"""
from __future__ import annotations

import logging

from bridge.deliver import (
    CHANNEL_ALERTS,
    CHANNEL_BRIEF,
    MAX_CONTENT,
    build_deliver,
    channel_ids_from_settings,
    render_brief,
    render_payload,
)
from config.settings import Settings

BRIEF = {
    "date": "2026-07-07",
    "degraded": False,
    "brief": {
        "market_regime": "risk-on, breadth improving",
        "risk_flags": ["concentration in AAPL"],
        "watch_items": ["MSFT into earnings"],
        "source_refs": ["parquet://2026-07-07/events-x#0", "parquet://2026-07-07/events-x#1"],
    },
}


def _recorder():
    posts = []
    return posts, (lambda cid, content: posts.append((cid, content)))


# ---------------------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------------------
def test_delivers_to_the_mapped_channel_id():
    posts, post_fn = _recorder()
    deliver = build_deliver({CHANNEL_BRIEF: 4242}, post_fn)

    assert deliver(CHANNEL_BRIEF, BRIEF) is True
    assert len(posts) == 1
    channel_id, content = posts[0]
    assert channel_id == 4242
    assert "Morning brief — 2026-07-07" in content


def test_unmapped_channel_fails_closed_and_posts_nowhere(caplog):
    """An unconfigured channel must NOT be redirected into whichever channel is configured, and
    must NOT report a delivery that did not happen."""
    posts, post_fn = _recorder()
    deliver = build_deliver({CHANNEL_BRIEF: 4242}, post_fn)

    with caplog.at_level(logging.WARNING, logger="cockpit.deliver"):
        assert deliver(CHANNEL_ALERTS, {"alert": "halt"}) is False
    assert posts == [], "an unmapped channel leaked into a mapped one"
    assert "no channel id configured" in caplog.text


def test_transport_failure_is_reported_as_undelivered_never_raised(caplog):
    def boom(_cid, _content):
        raise OSError("discord unreachable")

    deliver = build_deliver({CHANNEL_BRIEF: 1}, boom)
    with caplog.at_level(logging.WARNING, logger="cockpit.deliver"):
        assert deliver(CHANNEL_BRIEF, BRIEF) is False
    assert "delivery to #brief failed" in caplog.text


def test_failure_log_carries_the_exception_type_not_its_body(caplog):
    """A urllib error repr can carry the request URL; the log records the TYPE only, so no
    token-bearing string can ever ride out on this path (Inv 4)."""
    def boom(_cid, _content):
        raise OSError("https://discord.com/api/v10/channels/1/messages?token=NOT-A-REAL-TOKEN")

    deliver = build_deliver({CHANNEL_BRIEF: 1}, boom)
    with caplog.at_level(logging.WARNING, logger="cockpit.deliver"):
        deliver(CHANNEL_BRIEF, BRIEF)
    assert "NOT-A-REAL-TOKEN" not in caplog.text
    assert "OSError" in caplog.text


# ---------------------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------------------
def test_brief_renders_the_prose_fields_in_a_deterministic_order():
    text = render_brief(BRIEF)
    assert text.index("Regime") < text.index("Risk flags") < text.index("Watch")
    assert "risk-on, breadth improving" in text
    assert "• concentration in AAPL" in text
    assert "2 source ref(s)" in text


def test_degraded_brief_is_marked_as_such():
    assert "degraded" in render_brief({**BRIEF, "degraded": True})
    assert "degraded" not in render_brief(BRIEF)


def test_long_content_is_clamped_under_the_discord_ceiling():
    posts, post_fn = _recorder()
    huge = {**BRIEF, "brief": {**BRIEF["brief"], "market_regime": "x" * 5000}}
    build_deliver({CHANNEL_BRIEF: 1}, post_fn)(CHANNEL_BRIEF, huge)

    _cid, content = posts[0]
    assert len(content) <= MAX_CONTENT
    assert "truncated" in content, "a truncated brief must say so"


def test_research_update_renders_on_brief_channel():
    text = render_payload(CHANNEL_BRIEF, {
        "kind": "research_update",
        "as_of": "2026-09-12",
        "significance": "Latest GET-only board is empty.",
        "opposing_evidence": "32 supported heads, cash 0.",
        "next_trigger": "continue_get_only_natural_head_screens",
        "qualify_count": 0,
    })
    assert "Empty-run research update — 2026-09-12" in text
    assert "32 supported heads" in text
    assert "Morning brief" not in text


# ---------------------------------------------------------------------------------------
# settings -> channel map
# ---------------------------------------------------------------------------------------
def test_channel_map_omits_unset_ids():
    assert channel_ids_from_settings(Settings()) == {}
    mapped = channel_ids_from_settings(Settings(brief_channel_id=99))
    assert mapped == {CHANNEL_BRIEF: 99}, "only the configured channel may be mapped"

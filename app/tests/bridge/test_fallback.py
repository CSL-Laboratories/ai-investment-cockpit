"""WP6 — Discord-outage fallback (SEC-23)."""
from __future__ import annotations

from datetime import timedelta

from bridge.fallback import DiscordOutageFallback, _NullNotifier

from .conftest import NOW, make_order, make_risk


def test_enqueue_notifies_and_is_idempotent(st):
    notifier = _NullNotifier()
    fb = DiscordOutageFallback(st, notifier=notifier, max_age_seconds=600)
    risk = make_risk()
    a = fb.enqueue(proposal_id="prop-1", risk_decision=risk, resolved_order=make_order(),
                   interaction_id="i-1")
    b = fb.enqueue(proposal_id="prop-1", risk_decision=risk, resolved_order=make_order(),
                   interaction_id="i-1")
    assert a is b                                  # idempotent (interaction retry)
    assert a.notified is True
    assert notifier.calls == ["prop-1"]            # notified exactly once


def test_stale_queued_proposal_expires_and_never_executes(st):
    fb = DiscordOutageFallback(st, notifier=_NullNotifier(), max_age_seconds=300)
    fb.enqueue(proposal_id="prop-1", risk_decision=make_risk(evaluated_at=NOW),
               resolved_order=make_order(), interaction_id="i-1")
    # still fresh
    assert len(fb.pending(NOW)) == 1
    # past staleness -> dropped, not surfaced (and there is NO execute path here at all)
    late = NOW + timedelta(seconds=301)
    dropped = fb.expire_stale(late)
    assert [d.proposal_id for d in dropped] == ["prop-1"]
    assert fb.pending(late) == []


def test_no_notifier_is_safe(st):
    fb = DiscordOutageFallback(st, notifier=None)
    qp = fb.enqueue(proposal_id="prop-1", risk_decision=make_risk(), resolved_order=make_order())
    assert qp.notified is False                    # no hard desktop dependency
    assert fb.get("prop-1") is qp

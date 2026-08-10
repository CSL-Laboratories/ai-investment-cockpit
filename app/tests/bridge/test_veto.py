"""WP6 — the auto_veto veto-window (Inv 1/6; SEC-01/23)."""
from __future__ import annotations

from sqlalchemy import select

from bridge.veto import AutoVetoCoordinator
from brokers.capability import TokenRegistry
from config.settings import Mode
from schemas.models.audit_entry import AuditEntry
from schemas.models.breaker_state import BreakerState

from .conftest import FOREIGN, OPERATOR, make_order, make_risk


class _Mono:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


class _FakeLadder:
    def __init__(self, mode):
        self._mode = mode

    def current(self):
        return self._mode


def _audit_decisions(st):
    with st.session_factory() as s:
        rows = s.execute(select(AuditEntry)).scalars().all()
    return [(r.payload or {}).get("decision") for r in rows if (r.payload or {}).get("decision")]


def _coord(st, mono, *, mode=Mode.auto_veto):
    reg = TokenRegistry()
    coord = AutoVetoCoordinator(
        st, reg, window_seconds=300.0, monotonic=mono,
        mode_ladder=_FakeLadder(mode),
    )
    return coord, reg


# ---- clean elapsed window mints a token -----------------------------------------------
def test_clean_window_elapsed_mints_token(st):
    mono = _Mono(0.0)
    coord, reg = _coord(st, mono)
    risk = make_risk()
    coord.schedule(proposal_id="prop-1", risk_decision=risk, resolved_order=make_order())
    coord.confirm_delivery("prop-1")
    mono.t = 300.0
    res = coord.resolve("prop-1")
    assert res.executed is True
    assert res.reason == "elapsed_clean"
    assert reg.live_count() == 1
    assert "approve" in _audit_decisions(st)   # the auto_veto equivalent of an approval


# ---- (v) veto race: veto at T-1s WINS, T+0 execution sees it atomically ---------------
def test_veto_at_t_minus_1_wins_over_execution(st):
    mono = _Mono(0.0)
    coord, reg = _coord(st, mono)
    risk = make_risk()
    coord.schedule(proposal_id="prop-1", risk_decision=risk, resolved_order=make_order())
    coord.confirm_delivery("prop-1")
    mono.t = 299.0
    out = coord.veto("prop-1", actor_user_id=OPERATOR)     # veto lands at T-1s
    assert out.vetoed is True
    mono.t = 300.0
    res = coord.resolve("prop-1")                          # execution at T+0
    assert res.executed is False
    assert res.reason == "vetoed"
    assert reg.live_count() == 0                           # no token minted
    assert "veto" in _audit_decisions(st)


def test_foreign_user_cannot_veto_and_is_audited(st):
    mono = _Mono(0.0)
    coord, _ = _coord(st, mono)
    coord.schedule(proposal_id="prop-1", risk_decision=make_risk(), resolved_order=make_order())
    out = coord.veto("prop-1", actor_user_id=FOREIGN)
    assert out.vetoed is False
    assert out.reason == "unauthorized"
    with st.session_factory() as s:
        events = [r.event_type for r in s.execute(select(AuditEntry)).scalars().all()]
    assert "unauthorized_actor" in events


# ---- SEC-23: undelivered => no auto-exec on silence -----------------------------------
def test_undelivered_never_auto_executes(st):
    mono = _Mono(0.0)
    coord, reg = _coord(st, mono)
    coord.schedule(proposal_id="prop-1", risk_decision=make_risk(), resolved_order=make_order())
    mono.t = 100000.0                                       # long past any window
    res = coord.resolve("prop-1")
    assert res.executed is False
    assert res.reason == "undelivered"
    assert reg.live_count() == 0


def test_window_still_open_does_not_execute(st):
    mono = _Mono(0.0)
    coord, reg = _coord(st, mono)
    coord.schedule(proposal_id="prop-1", risk_decision=make_risk(), resolved_order=make_order())
    coord.confirm_delivery("prop-1")
    mono.t = 299.0
    res = coord.resolve("prop-1")
    assert res.executed is False
    assert res.reason == "window_open"
    assert reg.live_count() == 0


# ---- SEC-23: a backward clock jump trips the injection breaker + refuses ---------------
def test_backward_clock_jump_trips_breaker(st):
    mono = _Mono(0.0)
    reg = TokenRegistry()
    # real BreakerEngine + real ModeLadder so the trip actually records + drops the mode.
    coord = AutoVetoCoordinator(st, reg, window_seconds=300.0, monotonic=mono)
    risk = make_risk()
    coord.schedule(proposal_id="prop-1", risk_decision=risk, resolved_order=make_order())
    coord.confirm_delivery("prop-1")           # sets last_monotonic = 0
    mono.t = -5.0                               # a rewound/forged clock
    res = coord.resolve("prop-1")
    assert res.executed is False
    assert res.reason == "clock_backwards"
    assert reg.live_count() == 0
    with st.session_factory() as s:
        trips = s.execute(select(BreakerState)).scalars().all()
    assert any(b.tripped for b in trips)        # the injection breaker recorded a trip


# ---- SEC-23 (round-5): a rewound clock at CONFIRM must not start the veto window -------
def test_rewound_clock_at_confirm_does_not_start_window(st):
    # A backward monotonic reading at confirm time must NOT stamp a stale-low delivery
    # instant. Otherwise the process-wide watermark (which refuses to rewind) lets a later
    # forward-clock resolve() see a fully-elapsed window and mint an auto-exec token, with
    # the veto window collapsed to ~0 and WITHOUT tripping the clock/injection breaker.
    mono = _Mono(1000.0)
    coord, reg = _coord(st, mono)
    # A prior ticket establishes the process-wide monotonic watermark at 1000.
    coord.schedule(proposal_id="warm", risk_decision=make_risk(), resolved_order=make_order())
    coord.confirm_delivery("warm")                          # _last_monotonic = 1000

    # The attack ticket: the monotonic source is REWOUND at confirm time.
    coord.schedule(proposal_id="prop-1", risk_decision=make_risk(), resolved_order=make_order())
    mono.t = 0.0                                            # forged/rewound clock at CONFIRM
    ticket = coord.confirm_delivery("prop-1")
    # Fail closed: the rewound reading must not cleanly start the countdown from t=0.
    assert not (ticket.delivered and ticket.delivered_at_monotonic == 0.0)

    # Clock "restored" forward to the true instant — only ~0s actually elapsed.
    mono.t = 1000.0
    res = coord.resolve("prop-1")
    assert res.executed is False
    assert res.reason in {"undelivered", "clock_backwards"}
    assert reg.live_count() == 0


# ---- mode dropped out of auto_veto cancels the auto-exec -------------------------------
def test_mode_no_longer_auto_veto_cancels(st):
    mono = _Mono(0.0)
    coord, reg = _coord(st, mono, mode=Mode.review_live)
    coord.schedule(proposal_id="prop-1", risk_decision=make_risk(), resolved_order=make_order())
    coord.confirm_delivery("prop-1")
    mono.t = 300.0
    res = coord.resolve("prop-1")
    assert res.executed is False
    assert res.reason == "mode_changed"
    assert reg.live_count() == 0


# ---- BUG-080: settled tickets are bounded ----------------------------------------------
def test_settled_tickets_are_pruned_beyond_the_bound(coordinator_factory=None):
    from decimal import Decimal

    from bridge.veto import AutoVetoCoordinator

    # A minimal stand-in state: only .lock is touched by schedule/_prune_settled.
    import threading

    class _St:
        lock = threading.RLock()

    class _Rd:
        id = "rd-1"

    coord = AutoVetoCoordinator(_St(), None)
    coord._MAX_SETTLED_TICKETS = 5
    order = {"symbol": "AAPL", "side": "buy", "qty": "1", "order_type": "limit",
             "limit_price": "100", "tif": "day"}
    for i in range(30):
        t = coord.schedule(proposal_id=f"p-{i}", risk_decision=_Rd(), resolved_order=order)
        t.resolved = True                     # settle immediately
    assert len(coord._tickets) <= 5 + 1, "settled tickets must be bounded"
    # live tickets are never pruned
    live = coord.schedule(proposal_id="p-live", risk_decision=_Rd(), resolved_order=order)
    assert live.proposal_id in {t.proposal_id for t in coord._tickets.values()}

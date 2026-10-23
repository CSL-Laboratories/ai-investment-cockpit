"""CSL-218 negative controls for paper and dry-run authority profiles.

Live, expired, revoked, model-granted, split, raced, restart, and replay
cases must fail closed. A profile reservation is not broker authority.
"""
from __future__ import annotations

import inspect
import json
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from brokers.alpaca_paper import AlpacaPaperBroker
from brokers.authority_profiles import (
    ADMITTED_ENVIRONMENTS,
    CLAIM_LEASE,
    FUTURE_PROFILE_CLASS,
    NAMED_REFUSED_ENVIRONMENTS,
    RUNTIME_PROFILE_CLASSES,
    AuthorityProfileStore,
    ClaimTicket,
    ProfileRefused,
    _record_landed,
    _settle_completed,
    handoff_to_gateway,
    load_profile,
    load_profile_file,
    model_output_grants_authority,
    reconcile_handoff_to_gateway,
)
from brokers.gateway import GateOutcome, OrderGateway
from brokers.paper_gateway import PaperOrderGateway
from config.settings import Mode
from core.clock import Clock, FrozenClock
from tests.brokers.conftest import build_scenario

APP = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
EXPIRES = "2026-10-18T12:00:00+00:00"
GRANT = {
    "authority": "grant",
    "confidence": 1,
    "environment": "live",
    "allowed_actions": ["submit_paper"],
    "profile_class": "C",
}


def _document(**overrides):
    base = {
        "schema_version": 1,
        "profile_id": "profile-b",
        "profile_class": "B",
        "account_id": "acct-1",
        "account_kind": "individual_personal",
        "jurisdiction": "US",
        "venue": "alpaca",
        "venue_family": "us_equity",
        "adapter": "alpaca_paper",
        "environment": "paper",
        "provider": "claude",
        "model_version": "claude-pinned",
        "allowed_actions": ["propose", "submit_paper"],
        "expires_at": EXPIRES,
        "notional_cap": "100.00",
        "order_count_cap": 10,
        "max_order_notional": "40.00",
        "stop_owner": "stop-owner",
        "recovery_owner": "recovery-owner",
        "evidence_refs": ["csl-218-qualification-reference"],
    }
    base.update(overrides)
    return base


def _dry_run(**overrides):
    base = _document(
        profile_id="profile-b-dry",
        environment="dry_run",
        venue="dry_run",
        adapter="dry_run",
        allowed_actions=["propose", "submit_dry_run"],
    )
    base.update(overrides)
    return base


def _store(tmp_path, name="profiles.sqlite", *, clock=None):
    if clock is None:
        return AuthorityProfileStore(tmp_path / name)
    return AuthorityProfileStore(tmp_path / name, clock=clock)


_CAPS: dict[tuple[str, str], object] = {}


def _install(store, document=None, *, now=NOW):
    document = document or _document()
    profile, capability = store.install(document, actor=document["stop_owner"], now=now)
    _CAPS[(str(store.path), profile.profile_id)] = capability
    return profile


def _order_gateway_claim_fence_landed() -> bool:
    return "claim_fence" in inspect.signature(OrderGateway.submit).parameters


_NEEDS_GATEWAY_FENCE = pytest.mark.skipif(
    not _order_gateway_claim_fence_landed(),
    reason="in-lock claim fence lands with the CSL-218 gateway split",
)


def _refuse(fn) -> str:
    with pytest.raises(ProfileRefused) as caught:
        fn()
    return caught.value.code


def _paper_submit_kwargs(scenario, **overrides):
    # These fixtures create analysis in generation zero. Keep that lineage
    # fixed: a subsequent blackout must not refresh it when building kwargs.
    kwargs = scenario.submit_kwargs(analysis_vendor="claude", analysis_generation=0)
    kwargs.update(overrides)
    return kwargs


# Positive calls in this module declare the profile's own paper bindings. The
# store still refuses an omitted or mismatched binding; those tests pass
# enforce_bindings=False and the raw values.
_AUTHORIZE = AuthorityProfileStore.authorize
_PAPER_BINDINGS = {
    "provider": "claude",
    "model_version": "claude-pinned",
    "adapter": "alpaca_paper",
    "venue": "alpaca",
    "venue_family": "us_equity",
}
_DRY_BINDINGS = {
    "provider": "claude",
    "model_version": "claude-pinned",
    "adapter": "dry_run",
    "venue": "dry_run",
    "venue_family": "us_equity",
}
_VALID_MODEL = {"provider": "claude", "model_version": "claude-pinned", "confidence": 1}


def _use_capability(self, profile_id, kwargs):
    if kwargs.pop("enforce_capability", True) and kwargs.get("capability") is None:
        kwargs["capability"] = _CAPS[(str(self.path), profile_id)]


def _filling_authorize(self, profile_id, **kwargs):
    if kwargs.pop("enforce_bindings", True):
        for key, value in _PAPER_BINDINGS.items():
            kwargs.setdefault(key, value)
    _use_capability(self, profile_id, kwargs)
    return _AUTHORIZE(self, profile_id, **kwargs)


def _filling_stop(method):
    def wrapped(self, profile_id, **kwargs):
        _use_capability(self, profile_id, kwargs)
        return method(self, profile_id, **kwargs)
    return wrapped


AuthorityProfileStore.authorize = _filling_authorize
AuthorityProfileStore.revoke = _filling_stop(AuthorityProfileStore.revoke)
AuthorityProfileStore.kill = _filling_stop(AuthorityProfileStore.kill)
AuthorityProfileStore.hold = _filling_stop(AuthorityProfileStore.hold)
AuthorityProfileStore.release_stop = _filling_stop(AuthorityProfileStore.release_stop)

_RECONCILE = AuthorityProfileStore._reconcile_handoff
_REAL_STORE_INIT = AuthorityProfileStore.__init__
_CLOCK_BOX = {"clock": None}


def _test_store_init(self, path, *, clock=None):
    if clock is None:
        shared = _CLOCK_BOX["clock"]
        if shared is None:
            clock = FrozenClock(NOW)
        elif _CLOCK_BOX.get("share"):
            clock = shared
        else:
            # A private clock. Mutating one store must not move the next.
            clock = FrozenClock(shared.now())
    _REAL_STORE_INIT(self, path, clock=clock)


AuthorityProfileStore.__init__ = _test_store_init


@pytest.fixture(autouse=True)
def _bind_profile_clock(request):
    """Scenario tests share the fixture clock. Other tests freeze at NOW."""
    if "st" in request.fixturenames or "clock" in request.fixturenames:
        _CLOCK_BOX["clock"] = request.getfixturevalue("clock")
        _CLOCK_BOX["share"] = True
    else:
        _CLOCK_BOX["clock"] = FrozenClock(NOW)
        _CLOCK_BOX["share"] = False
    yield
    _CLOCK_BOX["clock"] = None
    _CLOCK_BOX["share"] = False


def _filling_reconcile(self, client_order_id, **kwargs):
    """Tests that already hold the stop capability keep using it for recovery."""
    if kwargs.pop("enforce_capability", True) and kwargs.get("capability") is None:
        with self._txn() as conn:
            row = conn.execute(
                "SELECT profile_id FROM reservations WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
        if row is not None:
            kwargs["capability"] = _CAPS[(str(self.path), row["profile_id"])]
    return _RECONCILE(self, client_order_id, **kwargs)


AuthorityProfileStore._reconcile_handoff = _filling_reconcile


def _handoff_row(path, client_order_id):
    import sqlite3

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT handoff_state, handoff_done, kill, claimed_at, claim_generation FROM reservations "
        "LEFT JOIN controls USING (account_id, venue, environment) "
        "WHERE client_order_id = ?",
        (client_order_id,),
    ).fetchone()
    conn.close()
    return row


@pytest.mark.parametrize(
    "environment",
    ["live", "LIVE", "limited_live", "production", "shadow", "demo", "review_live", "dry-run", "paper ", ""],
)
def test_live_and_unknown_environments_are_refused(environment):
    assert "live" not in ADMITTED_ENVIRONMENTS
    assert ADMITTED_ENVIRONMENTS == frozenset({"paper", "dry_run"})
    assert NAMED_REFUSED_ENVIRONMENTS.isdisjoint(ADMITTED_ENVIRONMENTS)
    code = _refuse(lambda: load_profile(_document(environment=environment), now=NOW))
    assert code == "environment"


def test_none_environment_and_profile_c_have_no_runtime():
    assert FUTURE_PROFILE_CLASS not in RUNTIME_PROFILE_CLASSES
    assert _refuse(lambda: load_profile(_document(environment=None), now=NOW)) == "environment"
    assert _refuse(lambda: load_profile(_document(profile_class="C"), now=NOW)) == "profile_class"
    assert _refuse(lambda: load_profile(_document(profile_class="D"), now=NOW)) == "profile_class"


def test_profile_a_is_proposal_only_and_b_cannot_cross_environments():
    proposal = load_profile(
        _document(profile_id="profile-a", profile_class="A", allowed_actions=["propose"]),
        now=NOW,
    )
    assert proposal.profile_class == "A"
    assert proposal.allowed_actions == frozenset({"propose"})
    assert _refuse(
        lambda: load_profile(
            _document(profile_class="A", allowed_actions=["propose", "submit_paper"]), now=NOW
        )
    ) == "allowed_actions"
    assert _refuse(
        lambda: load_profile(_document(allowed_actions=["submit_dry_run"]), now=NOW)
    ) == "allowed_actions"
    assert _refuse(
        lambda: load_profile(_dry_run(allowed_actions=["submit_paper"]), now=NOW)
    ) == "allowed_actions"


def test_unknown_field_expired_document_and_unauthenticated_actor_are_refused(tmp_path):
    widened = _document()
    widened["enable_live"] = True
    assert _refuse(lambda: load_profile(widened, now=NOW)) == "unknown_field"
    assert _refuse(
        lambda: load_profile(_document(expires_at="2026-10-17T12:00:00+00:00"), now=NOW)
    ) == "expired"
    assert _refuse(
        lambda: load_profile(_document(expires_at="2026-10-25T12:00:01+00:00"), now=NOW)
    ) == "expiry_too_long"
    store = _store(tmp_path)
    assert _refuse(lambda: store.install(_document(), actor="model", now=NOW)) == "unauthenticated"
    assert _refuse(lambda: AuthorityProfileStore(Path("profiles.sqlite"))) == "store_path"


def test_expired_and_revoked_profiles_are_refused(tmp_path):
    store = _store(tmp_path)
    _install(store)
    later = NOW + timedelta(days=2)
    store._clock._at = later
    assert _refuse(
        lambda: store.authorize(
            "profile-b", action="propose", client_order_id="c-expired",
            notional=Decimal("10.00"), actor="stop-owner", now=NOW,
        )
    ) == "expired"
    fresh = _store(tmp_path, "revoked.sqlite")
    _install(fresh)
    fresh.revoke("profile-b", actor="stop-owner", now=NOW, reason="operator revoke")
    assert _refuse(
        lambda: fresh.authorize(
            "profile-b", action="propose", client_order_id="c-revoked",
            notional=Decimal("10.00"), actor="stop-owner", now=NOW,
        )
    ) == "revoked"
    assert fresh.consumption("profile-b") == (Decimal("0.00"), 0)


def test_model_or_confidence_output_cannot_grant_authority(tmp_path):
    assert model_output_grants_authority(GRANT) is False
    store = _store(tmp_path)
    _install(store)
    later = NOW + timedelta(days=2)
    store._clock._at = later
    kwargs = dict(
        profile_id="profile-b", action="submit_paper", client_order_id="c-model",
        notional=Decimal("10.00"), actor="stop-owner", now=NOW,
    )
    plain = _refuse(lambda: store.authorize(**kwargs))
    valid = _refuse(lambda: store.authorize(**kwargs, model_output=_VALID_MODEL))
    assert plain == valid == "expired"
    assert store.consumption("profile-b") == (Decimal("0.00"), 0)


def test_split_cumulative_caps_are_refused(tmp_path):
    store = _store(tmp_path)
    _install(store, _document(order_count_cap=2))
    store.authorize(
        "profile-b", action="propose", client_order_id="c-1",
        notional=Decimal("40.00"), actor="stop-owner", now=NOW,
    )
    store.authorize(
        "profile-b", action="propose", client_order_id="c-2",
        notional=Decimal("40.00"), actor="stop-owner", now=NOW,
    )
    assert _refuse(
        lambda: store.authorize(
            "profile-b", action="propose", client_order_id="c-3",
            notional=Decimal("40.00"), actor="stop-owner", now=NOW,
        )
    ) == "aggregate_notional"
    assert _refuse(
        lambda: store.authorize(
            "profile-b", action="propose", client_order_id="c-4",
            notional=Decimal("20.00"), actor="stop-owner", now=NOW,
        )
    ) == "aggregate_count"
    assert _refuse(
        lambda: store.authorize(
            "profile-b", action="propose", client_order_id="c-5",
            notional=Decimal("40.01"), actor="stop-owner", now=NOW,
        )
    ) == "order_notional"
    assert store.consumption("profile-b") == (Decimal("80.00"), 2)
    assert _refuse(lambda: _install(store, _document(profile_id="profile-b-2"))) == "perimeter_occupied"


def test_reinstall_cannot_raise_a_consumed_ceiling(tmp_path):
    store = _store(tmp_path)
    _install(store)
    store.authorize(
        "profile-b", action="propose", client_order_id="c-spent-1",
        notional=Decimal("40.00"), actor="stop-owner", now=NOW,
    )
    store.authorize(
        "profile-b", action="propose", client_order_id="c-spent-2",
        notional=Decimal("40.00"), actor="stop-owner", now=NOW,
    )
    store.revoke("profile-b", actor="stop-owner", now=NOW, reason="rotate")
    wider = _document(
        profile_id="profile-b-2",
        notional_cap="10000.00",
        max_order_notional="10000.00",
        order_count_cap=100,
    )
    _install(store, wider)
    assert _refuse(
        lambda: store.authorize(
            "profile-b-2", action="propose", client_order_id="c-wider",
            notional=Decimal("30.00"), actor="stop-owner", now=NOW,
        )
    ) == "aggregate_notional"
    assert store.consumption("profile-b-2") == (Decimal("80.00"), 2)


def test_raced_cumulative_caps_are_refused(tmp_path):
    path = tmp_path / "race.sqlite"
    writer = AuthorityProfileStore(path)
    _install(writer, _document(max_order_notional="60.00"))
    reader = AuthorityProfileStore(path)
    barrier = threading.Barrier(2)
    results = []

    def attempt(store, client_order_id):
        barrier.wait()
        try:
            store.authorize(
                "profile-b", action="propose", client_order_id=client_order_id,
                notional=Decimal("60.00"), actor="stop-owner", now=NOW,
            )
            results.append("ok")
        except ProfileRefused as exc:
            results.append(exc.code)

    threads = [
        threading.Thread(target=attempt, args=(writer, "race-1")),
        threading.Thread(target=attempt, args=(reader, "race-2")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["aggregate_notional", "ok"]
    assert writer.consumption("profile-b") == (Decimal("60.00"), 1)


def test_kill_and_hold_persist_across_restart(tmp_path):
    path = tmp_path / "stop.sqlite"
    store = AuthorityProfileStore(path)
    _install(store)
    store.kill("profile-b", actor="stop-owner", now=NOW, reason="operator kill")
    assert _refuse(
        lambda: store.release_stop(
            "profile-b", actor="recovery-owner", now=NOW, reconciled=True
        )
    ) == "unauthenticated"
    assert _refuse(
        lambda: store.release_stop("profile-b", actor="stop-owner", now=NOW, reconciled=False)
    ) == "not_reconciled"
    reopened = AuthorityProfileStore(path)
    assert _refuse(
        lambda: reopened.authorize(
            "profile-b", action="propose", client_order_id="c-killed",
            notional=Decimal("10.00"), actor="stop-owner", now=NOW,
        )
    ) == "kill_active"
    assert _refuse(
        lambda: reopened.install(_document(profile_id="profile-b-2"), actor="stop-owner", now=NOW)
    ) == "kill_active"
    reopened.release_stop("profile-b", actor="stop-owner", now=NOW, reconciled=True)
    held = AuthorityProfileStore(path)
    held.hold("profile-b", actor="stop-owner", now=NOW, reason="operator hold")
    resumed = AuthorityProfileStore(path)
    assert _refuse(
        lambda: resumed.authorize(
            "profile-b", action="propose", client_order_id="c-held",
            notional=Decimal("10.00"), actor="stop-owner", now=NOW,
        )
    ) == "hold_active"


def test_client_order_ids_are_idempotent_across_restart(tmp_path):
    path = tmp_path / "idem.sqlite"
    store = AuthorityProfileStore(path)
    _install(store)
    first = store.authorize(
        "profile-b", action="submit_paper", client_order_id="client-1",
        notional=Decimal("25.00"), actor="stop-owner", now=NOW,
    )
    assert first.replayed is False
    again = store.authorize(
        "profile-b", action="submit_paper", client_order_id="client-1",
        notional=Decimal("25.00"), actor="stop-owner", now=NOW,
    )
    assert again.replayed is True
    assert again.client_order_id == first.client_order_id
    reopened = AuthorityProfileStore(path)
    replay = reopened.authorize(
        "profile-b", action="submit_paper", client_order_id="client-1",
        notional="25.00", actor="stop-owner", now=NOW,
    )
    assert replay.replayed is True
    assert reopened.consumption("profile-b") == (Decimal("25.00"), 1)
    assert _refuse(
        lambda: reopened.authorize(
            "profile-b", action="submit_paper", client_order_id="client-1",
            notional=Decimal("25.01"), actor="stop-owner", now=NOW,
        )
    ) == "client_order_conflict"


def test_null_order_ref_binds_once_on_replay(tmp_path):
    store = _store(tmp_path)
    _install(store)
    store.authorize(
        "profile-b", action="propose", client_order_id="client-1",
        notional=Decimal("25.00"), actor="stop-owner", now=NOW,
    )
    bound = store.authorize(
        "profile-b", action="propose", client_order_id="client-1",
        notional=Decimal("25.00"), actor="stop-owner", now=NOW, order_ref="ord-1",
    )
    assert bound.replayed is True
    assert _refuse(
        lambda: store.authorize(
            "profile-b", action="propose", client_order_id="client-1",
            notional=Decimal("25.00"), actor="stop-owner", now=NOW, order_ref="ord-2",
        )
    ) == "client_order_conflict"


def test_legacy_store_with_any_rows_refuses(tmp_path):
    """A pre-current store holding rows in ANY table refuses to open: its rows
    predate the install-minted stop capability and durable claim ownership, and
    no unauthenticated capability may be minted or accepted. The store must be
    recreated or its profiles re-installed via the authenticated install path.
    """
    import sqlite3

    inserts = {
        "controls": (
            "INSERT INTO controls (account_id, venue, environment) "
            "VALUES ('a', 'v', 'e')"
        ),
        "consumption": (
            "INSERT INTO consumption (account_id, venue, environment, reserved_notional,"
            " reserved_count, ceiling_notional, ceiling_count, ceiling_order)"
            " VALUES ('a', 'v', 'e', '0', 0, '0', 0, '0')"
        ),
        "reservations": (
            "INSERT INTO reservations (client_order_id, profile_id, account_id, venue,"
            " environment, action, notional, created_at)"
            " VALUES ('c', 'p', 'a', 'v', 'e', 'propose', '0', 't')"
        ),
    }
    for name, sql in inserts.items():
        path = tmp_path / f"legacy-{name}.sqlite"
        AuthorityProfileStore(path)
        raw = sqlite3.connect(path)
        raw.execute(sql)
        raw.execute("PRAGMA user_version = 2")
        raw.commit()
        raw.close()
        assert _refuse(lambda p=path: AuthorityProfileStore(p)) == "legacy_store", name

    # Profiles are covered by a real install: any profile row refuses the open.
    path = tmp_path / "legacy-profiles.sqlite"
    store = AuthorityProfileStore(path)
    _install(store)
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 2")
    raw.commit()
    raw.close()
    assert _refuse(lambda: AuthorityProfileStore(path)) == "legacy_store"


def test_empty_legacy_store_upgrades_in_place(tmp_path):
    """An empty pre-current store may be upgraded in place; the opened store
    then behaves exactly like a fresh one (install and authorize work)."""
    import sqlite3

    path = tmp_path / "empty-legacy.sqlite"
    AuthorityProfileStore(path)
    raw = sqlite3.connect(path)
    for statement in (
        "ALTER TABLE reservations DROP COLUMN order_ref",
        "ALTER TABLE reservations DROP COLUMN claim_generation",
        "ALTER TABLE reservations DROP COLUMN reconciled",
        "ALTER TABLE reservations DROP COLUMN claimed_at",
        "ALTER TABLE profiles DROP COLUMN stop_capability_hash",
        "PRAGMA user_version = 1",
    ):
        raw.execute(statement)
    raw.commit()
    raw.close()
    upgraded = AuthorityProfileStore(path)
    _install(upgraded)
    assert upgraded.authorize(
        "profile-b", action="propose", client_order_id="c-1",
        notional=Decimal("10.00"), actor="stop-owner", now=NOW,
    ).replayed is False


def test_current_store_missing_a_column_fails_closed(tmp_path):
    """A store at the current version that is missing a schema column is
    corruption, not a migration: open refuses rather than patching it."""
    import sqlite3

    path = tmp_path / "corrupt.sqlite"
    AuthorityProfileStore(path)
    raw = sqlite3.connect(path)
    raw.execute("ALTER TABLE reservations DROP COLUMN claim_generation")
    raw.commit()
    raw.close()
    assert _refuse(lambda: AuthorityProfileStore(path)) == "store"


def test_null_capability_hash_is_never_authenticated(tmp_path):
    """A NULL or empty stored stop_capability_hash refuses every presented
    capability: there is no silent migration that mints or accepts one."""
    import sqlite3

    path = tmp_path / "nullcap.sqlite"
    store = AuthorityProfileStore(path)
    _install(store)
    for value in (None, ""):
        raw = sqlite3.connect(path)
        if value is None:
            raw.execute("UPDATE profiles SET stop_capability_hash = NULL")
        else:
            raw.execute("UPDATE profiles SET stop_capability_hash = ''")
        raw.commit()
        raw.close()
        assert _refuse(
            lambda: store.kill("profile-b", actor="stop-owner", now=NOW, reason="k")
        ) == "unauthenticated", value
        assert _refuse(
            lambda: store.release_stop(
                "profile-b", actor="stop-owner", now=NOW, reconciled=True
            )
        ) == "unauthenticated", value


def test_profile_file_refuses_non_json(tmp_path):
    yaml_path = tmp_path / "profile.yaml"
    yaml_path.write_text("environment: live\n", encoding="utf-8")
    assert _refuse(lambda: load_profile_file(yaml_path)) == "profile_file"
    json_path = tmp_path / "profile.json"
    json_path.write_text(json.dumps(_document(environment="live")), encoding="utf-8")
    store = _store(tmp_path)
    assert _refuse(
        lambda: store.install(load_profile_file(json_path), actor="stop-owner", now=NOW)
    ) == "environment"


class _ProbeBroker:
    def __init__(self):
        self.calls = 0

    def submit_order(self, canonical):
        self.calls += 1
        return {
            "broker_order_id": "probe-1",
            "status": "accepted",
            "filled_qty": Decimal("0"),
            "avg_fill_price": None,
        }


class _CountingPaper(AlpacaPaperBroker):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def submit_order(self, canonical):
        self.calls += 1
        return super().submit_order(canonical)


def _sends(broker) -> int:
    """Orders the admitted paper client accepted.

    Profile transport calls ``AlpacaPaperBroker.submit_order`` captured at
    init, so a subclass ``calls`` counter stays at zero when the order lands.
    """
    client = getattr(broker, "_client", None)
    submitted = getattr(client, "submitted", None)
    if submitted is not None:
        return len(submitted)
    return int(getattr(broker, "calls", 0))


def _arm(gateway, store, client_order_id, generation):
    """Arm the gateway's one fence. ``submit`` must not receive another callable."""
    gateway.arm_profile_claim(store, client_order_id, generation)


def _use_fence(gateway, fn):
    """Run ``fn`` as the bound fence. The callable object stays the one from init."""
    gateway.arm_profile_fence(fn)


def test_paper_handoff_still_goes_through_the_gateway(st, tmp_path):
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway, mint=False)
    store = _store(tmp_path)
    now = scenario.clock.now()
    _install(
        store,
        _document(
            profile_id="profile-paper",
            account_id="paper",
            notional_cap="5000.00",
            max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    refused = handoff_to_gateway(
        store, gateway, profile_id="profile-paper", action="submit_paper",
        client_order_id="gw-refused", notional=Decimal(str(scenario.order.notional)),
        actor="stop-owner", now=now, submit_args=(scenario.order,),
        submit_kwargs=_paper_submit_kwargs(scenario), model_output=_VALID_MODEL,
        **_PAPER_BINDINGS,
    )
    assert refused.reached_gateway is True
    assert refused.outcome.ok is False
    assert _sends(broker) == 0
    failed = _handoff_row(tmp_path / "profiles.sqlite", "gw-refused")
    assert (failed["handoff_state"], failed["handoff_done"]) == ("failed", 0)
    replay = handoff_to_gateway(
        store, gateway, profile_id="profile-paper", action="submit_paper",
        client_order_id="gw-refused", notional=Decimal(str(scenario.order.notional)),
        actor="stop-owner", now=now, submit_args=(scenario.order,),
        submit_kwargs=_paper_submit_kwargs(scenario), model_output=_VALID_MODEL,
        **_PAPER_BINDINGS,
    )
    assert replay.reached_gateway is True
    assert replay.outcome.ok is False
    assert _sends(broker) == 0


def test_live_transport_and_dry_run_do_not_reach_a_broker(st, tmp_path):
    broker = _ProbeBroker()
    live = OrderGateway(broker, st=st, execution_scope="live")
    store = _store(tmp_path, clock=FrozenClock(NOW))
    _install(
        store,
        _document(account_id="paper", notional_cap="5000.00", max_order_notional="5000.00"),
    )
    assert _refuse(
        lambda: handoff_to_gateway(
            store, live, profile_id="profile-b", action="submit_paper",
            client_order_id="live-1", notional=Decimal("10.00"),
            actor="stop-owner", now=NOW, model_output=_VALID_MODEL,
            **_PAPER_BINDINGS,
        )
    ) == "transport"
    assert _sends(broker) == 0
    dry = _store(tmp_path, "dry.sqlite", clock=FrozenClock(NOW))
    _install(dry, _dry_run())

    class _Exploding:
        execution_scope = "paper"

        def submit(self, *args, **kwargs):
            raise AssertionError("dry-run handoff called the gateway")

    handoff = handoff_to_gateway(
        dry, _Exploding(), profile_id="profile-b-dry", action="submit_dry_run",
        client_order_id="dry-1", notional=Decimal("10.00"),
        actor="stop-owner", now=NOW, model_output=_VALID_MODEL,
        **_DRY_BINDINGS,
    )
    assert handoff.reached_gateway is False
    assert dry.consumption("profile-b-dry") == (Decimal("10.00"), 1)


def test_duck_typed_gateway_is_refused_before_reservation(st, tmp_path):
    store = _store(tmp_path, clock=FrozenClock(NOW))
    _install(store, _document(account_id="paper", notional_cap="5000.00", max_order_notional="5000.00"))

    class _Duck:
        execution_scope = "paper"

        def __init__(self):
            self.calls = 0

        def submit(self, *args, **kwargs):
            self.calls += 1
            return "bypassed"

    duck = _Duck()
    assert _refuse(
        lambda: handoff_to_gateway(
            store, duck, profile_id="profile-b", action="submit_paper",
            client_order_id="duck-1", notional=Decimal("10.00"),
            actor="stop-owner", now=NOW, **_PAPER_BINDINGS,
        )
    ) == "transport"
    assert duck.calls == 0
    probe = _ProbeBroker()
    forged = OrderGateway(probe, st=st, execution_scope="paper")
    assert forged._execution_scope == "paper"
    assert _refuse(
        lambda: handoff_to_gateway(
            store, forged, profile_id="profile-b", action="submit_paper",
            client_order_id="probe-1", notional=Decimal("10.00"),
            actor="stop-owner", now=NOW, **_PAPER_BINDINGS,
        )
    ) == "transport"
    assert probe.calls == 0
    pinned = PaperOrderGateway(_CountingPaper(), st=st)
    visible = pinned._broker
    pinned._broker = probe
    # Assignment does not redirect the transport bound at init.
    assert pinned._broker is visible
    assert _refuse(
        lambda: handoff_to_gateway(
            store, pinned, profile_id="profile-b", action="submit_paper",
            client_order_id="swapped-1", notional=Decimal("10.00"),
            actor="stop-owner", now=NOW, **_PAPER_BINDINGS,
        )
    ) == "order_required"
    assert probe.calls == 0
    assert store.consumption("profile-b") == (Decimal("0.00"), 0)


def test_mismatched_bindings_are_refused_before_reservation(tmp_path):
    store = _store(tmp_path)
    _install(store)
    base = dict(
        profile_id="profile-b", action="propose", notional=Decimal("10.00"),
        actor="stop-owner", now=NOW, enforce_bindings=False, **_PAPER_BINDINGS,
    )
    cases = (
        ("provider", "codex", "binding_provider"),
        ("model_version", "other-model", "binding_model"),
        ("adapter", "dry_run", "binding_adapter"),
        ("venue", "dry_run", "binding_venue"),
        ("venue_family", "event", "binding_venue_family"),
    )
    for field, value, code in cases:
        kwargs = dict(base)
        kwargs[field] = value
        kwargs["client_order_id"] = "bind-" + field
        assert _refuse(lambda kwargs=kwargs: store.authorize(**kwargs)) == code
    omitted = dict(base)
    del omitted["provider"]
    omitted["client_order_id"] = "bind-omitted"
    assert _refuse(lambda: store.authorize(**omitted)) == "binding_provider"
    assert store.consumption("profile-b") == (Decimal("0.00"), 0)


def test_revoked_profile_cannot_clear_an_active_stop(tmp_path):
    path = tmp_path / "rotate.sqlite"
    store = AuthorityProfileStore(path)
    _install(store, _document(profile_id="p1", stop_owner="owner-1", recovery_owner="recovery-1"))
    store.revoke("p1", actor="owner-1", now=NOW, reason="rotate")
    _install(store, _document(profile_id="p2", stop_owner="owner-2", recovery_owner="recovery-2"))
    store.kill("p2", actor="owner-2", now=NOW, reason="operator kill")
    assert _refuse(
        lambda: store.release_stop("p1", actor="owner-1", now=NOW, reconciled=True)
    ) == "revoked"
    assert _refuse(
        lambda: store.authorize(
            "p2", action="propose", client_order_id="c-still-killed",
            notional=Decimal("10.00"), actor="owner-2", now=NOW,
        )
    ) == "kill_active"
    import sqlite3
    conn = sqlite3.connect(path)
    kill, hold = conn.execute("SELECT kill, hold FROM controls").fetchone()
    conn.close()
    assert (kill, hold) == (1, 0)


def test_claim_rechecks_revocation_and_expiry(tmp_path):
    path = tmp_path / "claim.sqlite"
    store = AuthorityProfileStore(path)
    _install(store)
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="c-revoked",
        notional=Decimal("10.00"), actor="stop-owner", now=NOW,
    )
    store.revoke("profile-b", actor="stop-owner", now=NOW, reason="operator revoke")
    assert store.claim_handoff("c-revoked", now=NOW, awaiting_submit=True) == "revoked"
    row = _handoff_row(path, "c-revoked")
    assert (row["handoff_state"], row["handoff_done"]) == ("pending", 0)

    expiring = AuthorityProfileStore(tmp_path / "expiry.sqlite")
    _install(expiring)
    expiring.authorize(
        "profile-b", action="submit_paper", client_order_id="c-expired",
        notional=Decimal("10.00"), actor="stop-owner", now=NOW,
    )
    later = NOW + timedelta(days=2)
    expiring._clock._at = later
    assert expiring.claim_handoff("c-expired", now=NOW, awaiting_submit=True) == "expired"
    expired_row = _handoff_row(tmp_path / "expiry.sqlite", "c-expired")
    assert (expired_row["handoff_state"], expired_row["handoff_done"]) == ("pending", 0)


def test_missing_controls_row_fails_closed(tmp_path):
    import sqlite3

    path = tmp_path / "missing-controls.sqlite"
    store = AuthorityProfileStore(path)
    _install(store)
    raw = sqlite3.connect(path)
    raw.execute("DELETE FROM controls")
    raw.commit()
    raw.close()
    reopened = AuthorityProfileStore(path)
    assert _refuse(
        lambda: reopened.authorize(
            "profile-b", action="propose", client_order_id="c-open",
            notional=Decimal("10.00"), actor="stop-owner", now=NOW,
        )
    ) == "store"
    assert reopened.consumption("profile-b") == (Decimal("0.00"), 0)

    reserved = tmp_path / "reserved-controls.sqlite"
    held = AuthorityProfileStore(reserved)
    _install(held)
    held.authorize(
        "profile-b", action="submit_paper", client_order_id="c-held",
        notional=Decimal("10.00"), actor="stop-owner", now=NOW,
    )
    raw = sqlite3.connect(reserved)
    raw.execute("DELETE FROM controls")
    raw.commit()
    raw.close()
    reopened = AuthorityProfileStore(reserved)
    assert _refuse(lambda: reopened.claim_handoff("c-held", now=NOW, awaiting_submit=True)) == "store"
    row = _handoff_row(reserved, "c-held")
    assert (row["handoff_state"], row["handoff_done"]) == ("pending", 0)


def test_invalid_model_output_is_refused_before_reservation(tmp_path):
    store = _store(tmp_path)
    _install(store)
    assert _refuse(
        lambda: store.authorize(
            "profile-b", action="propose", client_order_id="c-grant",
            notional=Decimal("10.00"), actor="stop-owner", now=NOW, model_output=GRANT,
        )
    ) == "model_output"
    assert _refuse(
        lambda: store.authorize(
            "profile-b", action="propose", client_order_id="c-list",
            notional=Decimal("10.00"), actor="stop-owner", now=NOW, model_output=["grant"],
        )
    ) == "model_output"
    assert store.consumption("profile-b") == (Decimal("0.00"), 0)


def test_stop_owner_string_is_not_a_capability(tmp_path):
    import sqlite3

    store = _store(tmp_path)
    _install(store)
    refused = dict(
        capability="stop-owner", enforce_capability=False,
    )
    assert _refuse(
        lambda: store.kill(
            "profile-b", actor="stop-owner", now=NOW, reason="operator kill", **refused,
        )
    ) == "unauthenticated"
    assert _refuse(
        lambda: store.release_stop(
            "profile-b", actor="stop-owner", now=NOW, reconciled=True, **refused,
        )
    ) == "unauthenticated"
    assert _refuse(
        lambda: store.authorize(
            "profile-b", action="propose", client_order_id="c-string",
            notional=Decimal("10.00"), actor="stop-owner", now=NOW, **refused,
        )
    ) == "unauthenticated"
    conn = sqlite3.connect(store.path)
    kill = conn.execute("SELECT kill FROM controls").fetchone()[0]
    conn.close()
    assert kill == 0
    assert store.consumption("profile-b") == (Decimal("0.00"), 0)


def test_caller_set_execution_scope_is_not_paper_authority(st, tmp_path):
    broker = _CountingPaper()
    gateway = OrderGateway(broker, st=st, execution_scope="paper")
    assert gateway._execution_scope == "paper"
    assert type(gateway) is OrderGateway
    store = _store(tmp_path, clock=FrozenClock(NOW))
    _install(
        store,
        _document(account_id="paper", notional_cap="5000.00", max_order_notional="5000.00"),
    )
    assert _refuse(
        lambda: handoff_to_gateway(
            store, gateway, profile_id="profile-b", action="submit_paper",
            client_order_id="scope-1", notional=Decimal("10.00"),
            actor="stop-owner", now=NOW, **_PAPER_BINDINGS,
        )
    ) == "transport"
    assert _sends(broker) == 0
    assert store.consumption("profile-b") == (Decimal("0.00"), 0)


def test_failed_submit_keeps_the_client_order_reusable(st, tmp_path, monkeypatch):
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    calls = {"n": 0}
    real_submit = PaperOrderGateway.submit

    def _counting(self, order, **kwargs):
        calls["n"] += 1
        return real_submit(self, order, **kwargs)

    monkeypatch.setattr(PaperOrderGateway, "submit", _counting)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway, mint=False)
    path = tmp_path / "failed.sqlite"
    store = AuthorityProfileStore(path)
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper",
            notional_cap="5000.00",
            max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    call = dict(
        profile_id="profile-b", action="submit_paper", client_order_id="fail-1",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner", now=now,
        submit_args=(scenario.order,), submit_kwargs=_paper_submit_kwargs(scenario),
        model_output=_VALID_MODEL, **_PAPER_BINDINGS,
    )
    first = handoff_to_gateway(store, gateway, **call)
    assert first.reached_gateway is True
    assert first.outcome.ok is False
    assert calls["n"] == 1
    row = _handoff_row(path, "fail-1")
    assert (row["handoff_state"], row["handoff_done"]) == ("failed", 0)
    assert store.reconcile_failed_handoff("fail-1") == "failed"
    row = _handoff_row(path, "fail-1")
    assert (row["handoff_state"], row["handoff_done"]) == ("failed", 0)
    second = handoff_to_gateway(store, gateway, **call)
    assert second.reached_gateway is True
    assert calls["n"] == 2
    assert _sends(broker) == 0
    assert store.consumption("profile-b") == (Decimal(str(scenario.order.notional)), 1)


def test_owned_in_progress_claim_is_refused_until_reconciled(st, tmp_path, monkeypatch):
    """A second claim on an owned in_progress handoff is refused: two retries can
    never both enter ``gateway.submit``. The abandoned claim is re-owned only
    through ``reconcile_handoff_to_gateway``, which first checks the gateway's
    durable order identity (``order_ref`` = order id, submitted as
    ``cockpit-<order.id>`` on the signed transition chain) so a double submit
    is impossible.
    """
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    calls = {"n": 0}
    real_submit = PaperOrderGateway.submit

    def _flaky(self, order, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("crash before submit returned")
        return real_submit(self, order, **kwargs)

    monkeypatch.setattr(PaperOrderGateway, "submit", _flaky)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway, mint=True)
    path = tmp_path / "crash.sqlite"
    store = AuthorityProfileStore(path)
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper",
            notional_cap="5000.00",
            max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    call = dict(
        profile_id="profile-b", action="submit_paper", client_order_id="crash-1",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner", now=now,
        submit_args=(scenario.order,), submit_kwargs=_paper_submit_kwargs(scenario),
        model_output=_VALID_MODEL, **_PAPER_BINDINGS,
    )
    with pytest.raises(RuntimeError):
        handoff_to_gateway(store, gateway, **call)
    assert calls["n"] == 1
    row = _handoff_row(path, "crash-1")
    assert (row["handoff_state"], row["handoff_done"]) == ("in_progress", 0)
    # A concurrent or repeated claim is refused and never reaches the gateway.
    assert _refuse(lambda: handoff_to_gateway(store, gateway, **call)) == "handoff_in_progress"
    assert calls["n"] == 1
    assert _sends(broker) == 0
    # The claim lease has not expired yet: a reconcile this soon after the
    # crash is refused outright, even though the chain shows nothing landed.
    assert _refuse(
        lambda: reconcile_handoff_to_gateway(
            store, gateway, client_order_id="crash-1",
            submit_args=(scenario.order,), submit_kwargs=_paper_submit_kwargs(scenario),
            now=now, actor="recovery-owner",
        )
    ) == "claim_leased"
    assert calls["n"] == 1
    assert _sends(broker) == 0
    row = _handoff_row(path, "crash-1")
    assert (row["handoff_state"], row["handoff_done"]) == ("in_progress", 0)
    # Once the lease expires, the reconcile path sees no 'submitted' transition
    # on the signed chain, re-owns the claim under a new generation, and
    # submits exactly once.
    later = now + CLAIM_LEASE + timedelta(seconds=1)
    scenario.clock._at = later
    reconciled = reconcile_handoff_to_gateway(
        store, gateway, client_order_id="crash-1",
        submit_args=(scenario.order,), submit_kwargs=_paper_submit_kwargs(scenario),
        now=later, actor="recovery-owner",
    )
    assert calls["n"] == 2
    assert _sends(broker) == 1
    assert reconciled.reached_gateway is True
    assert reconciled.outcome.ok is True
    row = _handoff_row(path, "crash-1")
    assert (row["handoff_state"], row["handoff_done"]) == ("completed", 1)
    assert store.consumption("profile-b") == (Decimal(str(scenario.order.notional)), 1)


def test_reconcile_completes_a_landed_submit_without_a_second_one(st, tmp_path):
    """Crash after the broker ack: the signed chain already shows the order
    'submitted', so reconcile records the handoff completed instead of
    resubmitting — the durable identity is the only re-ownership check."""
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway, mint=True)
    path = tmp_path / "landed.sqlite"
    store = AuthorityProfileStore(path)
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper",
            notional_cap="5000.00",
            max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="landed-1",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner",
        now=now, order_ref=str(scenario.order.id),
    )
    ticket = store.claim_handoff("landed-1", now=now, awaiting_submit=True)
    assert isinstance(ticket, ClaimTicket)
    _arm(gateway, store, "landed-1", ticket.generation)
    outcome = gateway.submit(scenario.order, **_paper_submit_kwargs(scenario))
    assert outcome.ok is True
    assert _sends(broker) == 1
    # Crash between submit returning and complete_handoff: still in_progress.
    result = reconcile_handoff_to_gateway(
        store, gateway, client_order_id="landed-1",
        submit_args=(scenario.order,), submit_kwargs=_paper_submit_kwargs(scenario),
        now=now, actor="recovery-owner",
    )
    assert result.reached_gateway is False
    assert _sends(broker) == 1
    row = _handoff_row(path, "landed-1")
    assert (row["handoff_state"], row["handoff_done"]) == ("completed", 1)


def test_stale_claim_generation_is_a_noop_refusal(st, tmp_path):
    """Fencing: every claim gets a strictly increasing generation and the
    row-side write rejects any other token in the same atomic UPDATE. A stale
    caller's write is a refusal, never a downgrade of a completed row."""
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway, mint=True)
    path = tmp_path / "stale.sqlite"
    store = AuthorityProfileStore(path)
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper",
            notional_cap="5000.00",
            max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="stale-1",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner",
        now=now, order_ref=str(scenario.order.id),
    )
    ticket = store.claim_handoff("stale-1", now=now, awaiting_submit=True)
    assert isinstance(ticket, ClaimTicket)
    assert ticket.generation == 1
    # A wrong generation is refused even while the row is still in progress.
    assert _refuse(
        lambda: store.complete_handoff("stale-1", generation=ticket.generation + 1)
    ) == "stale_claim"
    assert _refuse(
        lambda: store.fail_handoff("stale-1", generation=ticket.generation + 1)
    ) == "stale_claim"
    _arm(gateway, store, "stale-1", ticket.generation)
    outcome = gateway.submit(scenario.order, **_paper_submit_kwargs(scenario))
    assert outcome.ok is True
    store.complete_handoff(
        "stale-1", generation=ticket.generation, gateway=gateway, ladder=scenario.ladder
    )
    # A stale owner's write after completion is refused; the row is kept.
    assert _refuse(
        lambda: store.fail_handoff("stale-1", generation=ticket.generation)
    ) == "stale_claim"
    row = _handoff_row(path, "stale-1")
    assert (row["handoff_state"], row["handoff_done"]) == ("completed", 1)


@pytest.mark.parametrize("recovery", [False, True], ids=["fresh", "expired-claim"])
def test_handoff_ignores_instance_submit_override(st, tmp_path, monkeypatch, recovery):
    """4302176363: an exact gateway type still cannot supply its own submitter."""
    from types import SimpleNamespace

    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    store = _store(tmp_path)
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    replacements = []

    def forged_submit(*args, **kwargs):
        replacements.append((args, kwargs))
        return GateOutcome(True, "submitted", "fabricated success")

    monkeypatch.setattr(gateway, "submit", forged_submit)
    # The order is otherwise valid, but this approval has no persisted or
    # signed authority. The actual gateway must refuse at gate (e).
    invalid_approval = SimpleNamespace(id="unpersisted-approval")
    kwargs = _paper_submit_kwargs(scenario, approval=invalid_approval)
    notional = Decimal(str(scenario.order.notional))
    if recovery:
        store.authorize(
            "profile-b", action="submit_paper", client_order_id="override-1",
            notional=notional, actor="stop-owner", now=now, order_ref=str(scenario.order.id),
        )
        store.claim_handoff("override-1", now=now, awaiting_submit=True)
        # The store clock, not the caller argument, decides whether the lease expired.
        scenario.clock._at = now + CLAIM_LEASE + timedelta(seconds=1)
        result = reconcile_handoff_to_gateway(
            store, gateway, client_order_id="override-1", submit_args=(scenario.order,),
            submit_kwargs=kwargs, now=scenario.clock.now(),
            actor="recovery-owner",
        )
    else:
        result = handoff_to_gateway(
            store, gateway, profile_id="profile-b", action="submit_paper",
            client_order_id="override-1", notional=notional, actor="stop-owner", now=now,
            submit_args=(scenario.order,), submit_kwargs=kwargs, **_PAPER_BINDINGS,
        )
    assert replacements == []
    assert result.reached_gateway is True
    assert result.outcome.ok is False
    assert result.outcome.stage == "approval"
    assert _sends(broker) == 0
    row = _handoff_row(store.path, "override-1")
    assert (row["handoff_state"], row["handoff_done"]) == ("failed", 0)


@pytest.mark.parametrize("disabled", [False, None, 0, 1, "true"])
def test_paper_gateway_refuses_disabling_analysis_generation(st, disabled):
    """4302211771: only explicit True or the safe default is admitted."""
    with pytest.raises(ValueError, match="requires analysis generation"):
        PaperOrderGateway(_CountingPaper(), st=st, require_analysis_generation=disabled)


@pytest.mark.parametrize("lineage", ["missing", "missing-after-blackout", "pre-blackout"])
def test_default_paper_gateway_requires_fresh_lineage(st, tmp_path, lineage):
    """4302211771: default construction cannot silently skip the freshness check."""
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    kwargs = _paper_submit_kwargs(scenario)
    if lineage != "missing":
        scenario.breaker.enter_watch_only("claude", "synthetic stale-data blackout")
        scenario.breaker.recover_from_watch_only("claude", Mode.paper)
        scenario.ladder.set_mode(Mode.paper, actor="op")
        assert scenario.breaker.watch_only_reason() is None
        assert scenario.breaker.current_generation("claude") > kwargs["analysis_generation"]
    if lineage.startswith("missing"):
        del kwargs["analysis_vendor"]
        del kwargs["analysis_generation"]
    store = _store(tmp_path)
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    result = handoff_to_gateway(
        store, gateway, profile_id="profile-b", action="submit_paper",
        client_order_id="lineage-1", notional=Decimal(str(scenario.order.notional)),
        actor="stop-owner", now=now, submit_args=(scenario.order,), submit_kwargs=kwargs,
        **_PAPER_BINDINGS,
    )
    assert result.outcome.ok is False
    assert result.outcome.stage == "analysis_freshness"
    assert _sends(broker) == 0
    row = _handoff_row(store.path, "lineage-1")
    assert (row["handoff_state"], row["handoff_done"]) == ("failed", 0)


@pytest.mark.parametrize(
    "evidence",
    ["missing-gateway", "missing-ladder", "no-transition", "forged-lookup",
     "other-order", "invalid-signature", "unavailable-chain"],
)
@pytest.mark.parametrize("path", ["completion", "settlement"])
def test_claim_ticket_cannot_complete_without_signed_submission(
    st, tmp_path, monkeypatch, evidence, path,
):
    """4302211775: a valid claim ticket cannot suppress an unsubmitted order."""
    from sqlalchemy import select

    from schemas.models.audit_entry import AuditEntry

    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    store = _store(tmp_path)
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    order_ref = str(scenario.order.id)
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="completion-1",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner", now=now,
        order_ref=order_ref + "-other" if evidence == "other-order" else order_ref,
    )
    ticket = store.claim_handoff("completion-1", now=now, awaiting_submit=True)
    assert isinstance(ticket, ClaimTicket)
    if evidence == "invalid-signature":
        _arm(gateway, store, "completion-1", ticket.generation)
        assert gateway.submit(
            scenario.order, **_paper_submit_kwargs(scenario),
        ).ok is True
    elif evidence == "other-order":
        # The permit is the reservation's order. A different order must not send.
        _arm(gateway, store, "completion-1", ticket.generation)
        refused = gateway.submit(
            scenario.order, **_paper_submit_kwargs(scenario),
        )
        assert (refused.ok, refused.stage) == (False, "claim_fence")
        assert "different order" in refused.reason
    if evidence == "invalid-signature":
        with st.session_factory() as session:
            entries = session.execute(select(AuditEntry)).scalars().all()
            submitted = [
                entry for entry in entries
                if (entry.refs or {}).get("row_id") == order_ref
                and (entry.payload or {}).get("to") == "submitted"
            ]
            assert len(submitted) == 1
            submitted[0].entry_hash = "0" * 64
            session.commit()
    if evidence == "forged-lookup":
        monkeypatch.setattr(gateway, "_order_submitted_on_chain", lambda *_args: True)
        monkeypatch.setattr(gateway, "_resolve_state", lambda *_args: st)
    if evidence == "unavailable-chain":
        def unavailable(*_args, **_kwargs):
            raise RuntimeError("signed chain unavailable")

        monkeypatch.setattr(st.audit, "verify_session", unavailable)
    kwargs = {}
    if evidence != "missing-gateway":
        kwargs["gateway"] = gateway
    if evidence not in {"missing-gateway", "missing-ladder"}:
        kwargs["ladder"] = scenario.ladder

    def complete():
        if path == "completion":
            return store.complete_handoff("completion-1", generation=ticket.generation, **kwargs)
        return _settle_completed(
            store, kwargs.get("gateway"), "completion-1", order_ref, ticket.generation,
            {"ladder": kwargs.get("ladder")}, now,
        )

    # A tampered submitted entry fails the whole chain. That is not a clean
    # "not submitted" lookup, so completion refuses chain_unreadable.
    expected = (
        "chain_unreadable"
        if evidence in {"invalid-signature", "unavailable-chain"}
        else "gateway_identity"
    )
    assert _refuse(complete) == expected
    row = _handoff_row(store.path, "completion-1")
    assert (row["handoff_state"], row["handoff_done"]) == ("in_progress", 0)
    # Completion refusal must not turn a future handoff into a completed replay.
    assert _refuse(
        lambda: store.claim_handoff("completion-1", now=now, awaiting_submit=True)
    ) == "handoff_in_progress"
    assert _sends(broker) == (1 if evidence == "invalid-signature" else 0)


def test_release_stop_derives_reconcile_from_store_state(st, tmp_path):
    """``reconciled=True`` is only an assertion of intent. The store derives the
    real reconcile state: a reservation pending, in progress, or failed without
    a gateway-identity reconcile refuses the release (fail closed), even for
    the stop owner presenting a valid capability.
    """
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway, mint=True)
    path = tmp_path / "release.sqlite"
    store = AuthorityProfileStore(path)
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper",
            notional_cap="5000.00",
            max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    call = dict(
        profile_id="profile-b", action="submit_paper",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner",
        now=now, order_ref=str(scenario.order.id),
    )

    def reconcile(client_order_id):
        return reconcile_handoff_to_gateway(
            store, gateway, client_order_id=client_order_id,
            submit_args=(scenario.order,), submit_kwargs=_paper_submit_kwargs(scenario),
            now=now, actor="recovery-owner",
        )

    # pending reservation blocks the release
    store.authorize(client_order_id="rel-pending", **call)
    store.kill("profile-b", actor="stop-owner", now=now, reason="operator kill")
    assert _refuse(
        lambda: store.release_stop(
            "profile-b", actor="stop-owner", now=now, reconciled=True
        )
    ) == "not_reconciled"
    assert _refuse(lambda: reconcile("rel-pending")) == "failed"
    store.release_stop("profile-b", actor="stop-owner", now=now, reconciled=True)

    # abandoned in_progress claim blocks the release until reconciled
    store.authorize(client_order_id="rel-inprog", **call)
    ticket = store.claim_handoff("rel-inprog", now=now, awaiting_submit=True)
    assert isinstance(ticket, ClaimTicket)
    store.kill("profile-b", actor="stop-owner", now=now, reason="operator kill")
    assert _refuse(
        lambda: store.release_stop(
            "profile-b", actor="stop-owner", now=now, reconciled=True
        )
    ) == "not_reconciled"
    # An unexpired in_progress lease is still an active submit. Kill must not
    # mark it reconciled, and release stays blocked.
    assert _refuse(lambda: reconcile("rel-inprog")) == "claim_leased"
    assert _refuse(
        lambda: store.release_stop(
            "profile-b", actor="stop-owner", now=now, reconciled=True
        )
    ) == "not_reconciled"
    assert int(_handoff_row(path, "rel-inprog")["handoff_done"]) == 0
    later = now + CLAIM_LEASE + timedelta(seconds=1)
    scenario.clock._at = later
    assert _refuse(
        lambda: reconcile_handoff_to_gateway(
            store, gateway, client_order_id="rel-inprog",
            submit_args=(scenario.order,), submit_kwargs=_paper_submit_kwargs(scenario),
            now=later, actor="recovery-owner",
        )
    ) == "failed"
    store.release_stop("profile-b", actor="stop-owner", now=later, reconciled=True)

    # failed-and-unreconciled blocks the release the same way
    store.authorize(client_order_id="rel-failed", **call)
    ticket = store.claim_handoff("rel-failed", now=now, awaiting_submit=True)
    store.fail_handoff("rel-failed", generation=ticket.generation)
    store.kill("profile-b", actor="stop-owner", now=now, reason="operator kill")
    assert _refuse(
        lambda: store.release_stop(
            "profile-b", actor="stop-owner", now=now, reconciled=True
        )
    ) == "not_reconciled"
    assert _refuse(lambda: reconcile("rel-failed")) == "failed"
    store.release_stop("profile-b", actor="stop-owner", now=now, reconciled=True)


def test_stopped_proposal_completes_after_reconcile_and_release(tmp_path):
    store = _store(tmp_path, "proposal.sqlite")
    _install(store)
    store.authorize(
        "profile-b", action="propose", client_order_id="prop-1",
        notional=Decimal("10.00"), actor="stop-owner", now=NOW,
    )
    store.hold("profile-b", actor="stop-owner", now=NOW, reason="operator hold")
    assert store.reconcile_non_gateway_handoff(
        "prop-1", now=NOW, actor="recovery-owner"
    ) == "failed"
    store.release_stop("profile-b", actor="stop-owner", now=NOW, reconciled=True)
    assert store.claim_handoff("prop-1", now=NOW, awaiting_submit=False) == "completed"
    assert store.claim_handoff("prop-1", now=NOW, awaiting_submit=False) == "replay"


@pytest.mark.parametrize("state", ["pending", "in_progress", "failed"])
@pytest.mark.parametrize("path", ["public", "private", "record_landed"])
def test_direct_caller_cannot_forge_a_submitted_verdict(st, tmp_path, monkeypatch, state, path):
    """Codex 4302176370: every reconcile path must consult the signed chain.

    A real submitted order cannot become failed/reconciled using a forged
    verdict, missing gateway evidence, or a failing chain lookup. The stop
    remains until the chain-derived completion has actually been recorded.
    """
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway, mint=True)
    store = _store(tmp_path)
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    order_ref = str(scenario.order.id)
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="forge-1",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner",
        now=now, order_ref=order_ref,
    )
    if state != "pending":
        ticket = store.claim_handoff("forge-1", now=now, awaiting_submit=True)
        assert isinstance(ticket, ClaimTicket)
        if state == "failed":
            store.fail_handoff("forge-1", generation=ticket.generation)
    if state == "in_progress":
        _arm(gateway, store, "forge-1", ticket.generation)
        seeded = gateway.submit(scenario.order, **_paper_submit_kwargs(scenario))
    else:
        # Pending and failed rows are not a live claim. Seed the signed chain
        # through a separate in-progress claim so PaperOrderGateway.submit still
        # receives a callable fence. Completing that seed leaves forge-1 in the
        # state under test.
        store.authorize(
            "profile-b", action="submit_paper", client_order_id="seed-1",
            notional=Decimal(str(scenario.order.notional)), actor="stop-owner",
            now=now, order_ref=order_ref,
        )
        seed = store.claim_handoff("seed-1", now=now, awaiting_submit=True)
        assert isinstance(seed, ClaimTicket)
        _arm(gateway, store, "seed-1", seed.generation)
        seeded = gateway.submit(scenario.order, **_paper_submit_kwargs(scenario))
        assert seeded.ok is True
        store.complete_handoff(
            "seed-1", generation=seed.generation, gateway=gateway, ladder=scenario.ladder,
        )
    assert seeded.ok is True
    assert gateway._order_submitted_on_chain(st, order_ref) is True
    store.kill("profile-b", actor="stop-owner", now=now, reason="operator kill")

    def assert_unresolved():
        with store._txn() as conn:
            row = conn.execute(
                "SELECT handoff_state, handoff_done, reconciled FROM reservations "
                "WHERE client_order_id = 'forge-1'"
            ).fetchone()
        assert tuple(row) == (state, 0, 0)
        assert _refuse(
            lambda: store.release_stop("profile-b", actor="stop-owner", now=now, reconciled=True)
        ) == "not_reconciled"
        assert _handoff_row(store.path, "forge-1")["kill"] == 1
        assert _sends(broker) == 1

    kwargs = _paper_submit_kwargs(scenario)
    reconcile_paths = {
        "public": lambda: reconcile_handoff_to_gateway(
            store, gateway, client_order_id="forge-1", submit_args=(scenario.order,),
            submit_kwargs=kwargs, now=now, actor="recovery-owner",
        ),
        # Omitting the caller's order_ref must still check the stored identity.
        "private": lambda: store._reconcile_handoff(
            "forge-1", order_ref=None, gateway=gateway, ladder=kwargs["ladder"], now=now,
            actor="recovery-owner",
        ),
        "record_landed": lambda: _record_landed(
            store, gateway, "forge-1", order_ref, kwargs, now,
        ),
    }
    for forged_verdict in (False, True):
        with pytest.raises(TypeError):
            store._reconcile_handoff(
                "forge-1", order_ref=order_ref, order_submitted=forged_verdict, now=now,
            )
        assert_unresolved()
    assert _refuse(
        lambda: store.reconcile_non_gateway_handoff(
            "forge-1", now=now, actor="recovery-owner"
        )
    ) == "store"
    assert_unresolved()
    for supplied_ref in (None, order_ref):
        assert _refuse(
            lambda: store._reconcile_handoff(
                "forge-1", order_ref=supplied_ref, now=now, actor="recovery-owner"
            )
        ) == "gateway_identity"
        assert_unresolved()
    assert _refuse(
        lambda: store._reconcile_handoff(
            "forge-1", order_ref=order_ref, gateway=gateway, now=now,
            actor="recovery-owner",
        )
    ) == "gateway_identity"
    assert_unresolved()
    if state == "failed":
        assert store.reconcile_failed_handoff("forge-1") == "failed"
        assert_unresolved()

    def unavailable_chain(*_args, **_kwargs):
        raise RuntimeError("signed chain unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(st.audit, "verify_session", unavailable_chain)
        for reconcile in reconcile_paths.values():
            assert _refuse(reconcile) == "chain_unreadable"
            assert_unresolved()

    # With genuine chain evidence every entry point records completed, never
    # failed, and never submits again. Reconcile itself leaves the stop set.
    reconcile_paths[path]()
    with store._txn() as conn:
        row = conn.execute(
            "SELECT handoff_state, handoff_done, reconciled FROM reservations "
            "WHERE client_order_id = 'forge-1'"
        ).fetchone()
    assert tuple(row) == ("completed", 1, 1)
    assert _handoff_row(store.path, "forge-1")["kill"] == 1
    assert _sends(broker) == 1
    store.release_stop("profile-b", actor="stop-owner", now=now, reconciled=True)
    assert _handoff_row(store.path, "forge-1")["kill"] == 0


def test_unbound_reservation_refuses_any_order_identity_at_reconcile(tmp_path):
    """Devin finding (4302172801): a reservation with no bound ``order_ref``
    must not accept an arbitrary order identity at reconcile time. Before the
    fix, an unbound row silently accepted whatever ``order_ref`` the caller
    presented and completed against it -- letting a caller bind (and
    complete, or fail) a reservation to an order it was never reserved for.
    """
    store = _store(tmp_path)
    _install(store)
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="unbound-1",
        notional=Decimal("10.00"), actor="stop-owner", now=NOW,
    )
    ticket = store.claim_handoff("unbound-1", now=NOW, awaiting_submit=True)
    assert isinstance(ticket, ClaimTicket)
    assert _refuse(
        lambda: store._reconcile_handoff(
            "unbound-1", order_ref="someone-elses-order", now=NOW, actor="recovery-owner"
        )
    ) == "client_order_conflict"
    row = _handoff_row(store.path, "unbound-1")
    assert (row["handoff_state"], row["handoff_done"]) == ("in_progress", 0)


def test_reconcile_rechecks_account_and_notional_against_the_reservation(st, tmp_path):
    """Codex finding (4302176367) / Devin (4302172827): recovery must run the
    SAME account/notional/provider/action checks a fresh handoff runs. Before
    the fix, ``reconcile_handoff_to_gateway`` never validated the recovery
    order at all -- a recovery call presenting a forged order (same id, wrong
    account or notional) would sail through to the gateway unexamined.
    """
    import types

    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway, mint=True)
    path = tmp_path / "forged.sqlite"
    store = AuthorityProfileStore(path)
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="forged-1",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner",
        now=now, order_ref=str(scenario.order.id),
    )
    ticket = store.claim_handoff("forged-1", now=now, awaiting_submit=True)
    assert isinstance(ticket, ClaimTicket)
    forged_account = types.SimpleNamespace(
        id=scenario.order.id, account="attacker-account", notional=scenario.order.notional,
    )
    assert _refuse(
        lambda: reconcile_handoff_to_gateway(
            store, gateway, client_order_id="forged-1",
            submit_args=(forged_account,), submit_kwargs=_paper_submit_kwargs(scenario),
            now=now, actor="recovery-owner",
        )
    ) == "account_mismatch"
    assert _sends(broker) == 0
    row = _handoff_row(path, "forged-1")
    assert (row["handoff_state"], row["handoff_done"]) == ("in_progress", 0)

    store.authorize(
        "profile-b", action="submit_paper", client_order_id="forged-2",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner",
        now=now, order_ref=str(scenario.order.id) + "-2",
    )
    store.claim_handoff("forged-2", now=now, awaiting_submit=True)
    forged_notional = types.SimpleNamespace(
        id=str(scenario.order.id) + "-2", account=scenario.order.account, notional=Decimal("1.00"),
    )
    assert _refuse(
        lambda: reconcile_handoff_to_gateway(
            store, gateway, client_order_id="forged-2",
            submit_args=(forged_notional,), submit_kwargs=_paper_submit_kwargs(scenario),
            now=now, actor="recovery-owner",
        )
    ) == "notional_mismatch"
    assert _sends(broker) == 0


def test_concurrent_reconcile_lease_prevents_a_double_send(st, tmp_path, monkeypatch):
    """Add a lease on in-flight claims (CSL reviewer item 4): an in_progress
    submit cannot be taken over and sent twice. Before the fix, a reconciler
    could re-own an abandoned claim immediately, with no regard for whether
    the original owner (or another concurrent reconciler) might still be
    mid-flight -- two reconcilers racing the same crashed claim could both
    mint a generation and both reach the gateway. Two reconcilers here must
    produce exactly one gateway send.
    """
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    calls = {"n": 0}
    real_submit = PaperOrderGateway.submit

    def _flaky(self, order, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("crash before submit returned")
        return real_submit(self, order, **kwargs)

    monkeypatch.setattr(PaperOrderGateway, "submit", _flaky)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway, mint=True)
    path = tmp_path / "lease-race.sqlite"
    store = AuthorityProfileStore(path)
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="lease-1",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner",
        now=now, order_ref=str(scenario.order.id),
    )
    ticket = store.claim_handoff("lease-1", now=now, awaiting_submit=True)
    assert isinstance(ticket, ClaimTicket)

    def reconcile(now_):
        scenario.clock._at = now_
        return reconcile_handoff_to_gateway(
            store, gateway, client_order_id="lease-1",
            submit_args=(scenario.order,), submit_kwargs=_paper_submit_kwargs(scenario),
            now=now_, actor="recovery-owner",
        )

    # The lease has expired: reconciler A re-owns the claim (generation 2) and
    # crashes again before its own submit returns.
    later = now + CLAIM_LEASE + timedelta(seconds=1)
    with pytest.raises(RuntimeError):
        reconcile(later)
    assert calls["n"] == 1
    assert _sends(broker) == 0
    row = _handoff_row(path, "lease-1")
    assert (row["handoff_state"], row["handoff_done"]) == ("in_progress", 0)

    # Reconciler B races in immediately after, at the same instant: the claim
    # it would be taking over is fresh (claimed moments ago by A), so the
    # lease refuses it before it can ever reach the gateway.
    assert _refuse(lambda: reconcile(later)) == "claim_leased"
    assert calls["n"] == 1
    assert _sends(broker) == 0

    # Only once A's own re-own lease has also expired does a third attempt
    # succeed -- and it sends to the gateway exactly once.
    even_later = later + CLAIM_LEASE + timedelta(seconds=1)
    result = reconcile(even_later)
    assert calls["n"] == 2
    assert _sends(broker) == 1
    assert result.outcome.ok is True
    row = _handoff_row(path, "lease-1")
    assert (row["handoff_state"], row["handoff_done"]) == ("completed", 1)


def test_expired_or_revoked_stop_owner_stays_blocked_pending_csl_280(tmp_path):
    """Documents the current fail-closed behavior pending founder decision CSL-280.

    A profile that kills its perimeter and is then revoked or expired cannot
    clear the stop, and install refuses the stopped perimeter: the perimeter
    stays blocked until founders decide the un-bricking rule. This test must
    keep passing unless that decision changes it (see
    docs/replacement/O6-PAPER-DRY-RUN-PROFILES.md, live-blocker ledger).
    """
    live = _store(tmp_path, "live.sqlite")
    _install(live)
    live.kill("profile-b", actor="stop-owner", now=NOW, reason="operator kill")
    live.release_stop("profile-b", actor="stop-owner", now=NOW, reconciled=True)

    revoked = _store(tmp_path, "revoked.sqlite")
    _install(revoked)
    revoked.kill("profile-b", actor="stop-owner", now=NOW, reason="operator kill")
    revoked.revoke("profile-b", actor="stop-owner", now=NOW, reason="operator revoke")
    assert _refuse(
        lambda: revoked.release_stop(
            "profile-b", actor="stop-owner", now=NOW, reconciled=True
        )
    ) == "revoked"
    assert _refuse(
        lambda: revoked.install(
            _document(profile_id="profile-b-2"), actor="stop-owner", now=NOW
        )
    ) == "kill_active"

    expired = _store(tmp_path, "expired.sqlite")
    _install(expired)
    expired.kill("profile-b", actor="stop-owner", now=NOW, reason="operator kill")
    after_expiry = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
    expired._clock._at = after_expiry
    assert _refuse(
        lambda: expired.release_stop(
            "profile-b", actor="stop-owner", now=after_expiry, reconciled=True
        )
    ) == "expired"
    assert _refuse(
        lambda: expired.install(
            _document(profile_id="profile-b-2", expires_at="2026-10-20T12:00:00+00:00"),
            actor="stop-owner", now=after_expiry,
        )
    ) == "kill_active"


def test_profile_module_has_no_live_flag_or_credential_path():
    text = (APP / "brokers" / "authority_profiles.py").read_text(encoding="utf-8")
    contract = (APP.parent / "docs" / "replacement" / "O6-PAPER-DRY-RUN-PROFILES.md").read_text(
        encoding="utf-8"
    )
    forbidden = (
        "enable_live",
        "live_credentials",
        "ALPACA_LIVE",
        "APCA_API",
        "keyring",
        "admit_live",
        "set_halt",
        "clear_halt",
    )
    for token in forbidden:
        assert token not in text
        assert token not in contract
    assert "live" not in ADMITTED_ENVIRONMENTS


def test_first_profile_installs_on_fresh_store_and_oversized_confidence_refuses(tmp_path):
    store = _store(tmp_path)
    _install(store)
    huge = {"provider": "claude", "model_version": "claude-pinned", "confidence": 10**400}
    code = _refuse(
        lambda: handoff_to_gateway(
            store, None, profile_id="profile-b", action="propose",
            client_order_id="c-huge", notional="10.00", actor="stop-owner",
            now=NOW, model_output=huge,
        )
    )
    assert code == "model_output"


def test_claim_fence_never_rewinds_lease_on_lagging_clock(tmp_path):
    path = tmp_path / "monotonic.sqlite"
    store = AuthorityProfileStore(path)
    _install(store)
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="mono-1",
        notional=Decimal("10.00"), actor="stop-owner", now=NOW, order_ref="ord-mono",
    )
    claim_time = NOW + timedelta(seconds=90)
    store._clock._at = claim_time
    ticket = store.claim_handoff("mono-1", now=NOW, awaiting_submit=True)
    assert isinstance(ticket, ClaimTicket)
    import sqlite3

    def claimed_at():
        conn = sqlite3.connect(path)
        try:
            return conn.execute(
                "SELECT claimed_at FROM reservations WHERE client_order_id = ?",
                ("mono-1",),
            ).fetchone()[0]
        finally:
            conn.close()

    before = claimed_at()
    assert before == claim_time.isoformat()
    store._clock._at = NOW
    with pytest.raises(ProfileRefused) as exc:
        store._confirm_claim_before_broker_call("mono-1", ticket.generation, now=claim_time)
    assert exc.value.code == "stale_claim"
    assert claimed_at() == before


def test_claim_fence_refreshes_lease_with_current_clock(tmp_path):
    """The fence reads the clock when it fires, so claimed_at advances."""
    from brokers import authority_profiles as ap

    store = _store(tmp_path)
    _install(store)
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="clock-1",
        notional="10.00", actor="stop-owner", now=NOW, order_ref="order-1",
    )
    ticket = store.claim_handoff("clock-1", now=NOW, awaiting_submit=True)

    class _Clock:
        def now(self):
            return NOW + timedelta(seconds=80)

    fired = NOW + timedelta(seconds=80)
    store._clock._at = fired
    store._confirm_claim_before_broker_call("clock-1", ticket.generation, now=NOW)
    assert _handoff_row(store.path, "clock-1")["claimed_at"] == fired.isoformat()
    assert _refuse(
        lambda: ap._fence_now(object(), {"clock": _Clock()})()
    ) == "clock"


def _claimed_paper_scenario(st, tmp_path, *, bind_approval=True):
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(
        st, mode=Mode.paper, broker=broker, gateway=gateway, bind_approval=bind_approval,
    )
    store = _store(tmp_path)
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="fence-1",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner", now=now,
        order_ref=str(scenario.order.id),
    )
    ticket = store.claim_handoff("fence-1", now=now, awaiting_submit=True)
    assert isinstance(ticket, ClaimTicket)
    return scenario, store, ticket, broker


def test_halt_set_inside_claim_fence_refuses_and_covers_intent(st, tmp_path):
    """4303448111: HALT arriving during a successful lease CAS wins before transport."""
    from orchestrator.halt import default_halt_path, set_halt

    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    fenced = []

    def fence():
        permit = store._confirm_claim_before_broker_call(
            "fence-1", ticket.generation, now=scenario.clock.now(),
        )
        set_halt(default_halt_path(), reason="HALT during claim fence")
        fenced.append(True)
        return permit

    _use_fence(scenario.gateway, fence)
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert fenced  # the fence also re-runs right before transport
    assert (outcome.ok, outcome.stage) == (False, "halt")
    assert _sends(broker) == 0
    assert st.audit.unresolved_intents() == []
    assert st.audit.verify_chain().ok


@pytest.mark.parametrize(
    "elapsed",
    [timedelta(microseconds=-1), CLAIM_LEASE,
     CLAIM_LEASE + timedelta(microseconds=1), timedelta(seconds=171)],
    ids=["clock-regressed", "lease-boundary", "past-boundary", "171-seconds"],
)
def test_claim_fence_cannot_renew_an_expired_or_future_lease(st, tmp_path, elapsed):
    """5558807159: a delayed owner must not revive its own dead lease."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    before = dict(_handoff_row(store.path, "fence-1"))
    fresh = scenario.clock.now()
    fence_time = fresh + elapsed
    store._clock._at = fence_time

    def fence():
        return store._confirm_claim_before_broker_call(
            "fence-1", ticket.generation, now=fresh,
        )

    _use_fence(scenario.gateway, fence)
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert (outcome.ok, outcome.stage) == (False, "claim_fence")
    assert "stale" in outcome.reason
    assert dict(_handoff_row(store.path, "fence-1")) == before
    assert _sends(broker) == 0
    assert st.audit.unresolved_intents() == []
    assert st.audit.verify_chain().ok


def test_running_owner_fenced_out_after_expired_lease_takeover(st, tmp_path):
    """5558807159: B reclaims at t+171 while A is still running; only B sends."""
    scenario, store_a, ticket_a, broker = _claimed_paper_scenario(st, tmp_path)
    store_b = AuthorityProfileStore(store_a.path)
    initial = scenario.clock.now()
    tickets_b = []

    def delayed_owner_a_fence():
        # A has passed the gateway gates but has not entered broker I/O. A
        # separate store connection models the reconciler taking its lease.
        scenario.clock._at = initial + timedelta(seconds=171)
        ticket_b = store_b._reconcile_handoff(
            "fence-1", order_ref=str(scenario.order.id), now=scenario.clock.now(),
            actor="recovery-owner",
            gateway=scenario.gateway, ladder=scenario.ladder,
        )
        assert isinstance(ticket_b, ClaimTicket)
        assert ticket_b.generation == ticket_a.generation + 1
        tickets_b.append(ticket_b)
        store_a._confirm_claim_before_broker_call(
            "fence-1", ticket_a.generation, now=scenario.clock.now(),
        )

    _use_fence(scenario.gateway, delayed_owner_a_fence)
    outcome_a = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert (outcome_a.ok, outcome_a.stage) == (False, "claim_fence")
    assert _sends(broker) == 0
    assert len(tickets_b) == 1
    row_b = dict(_handoff_row(store_b.path, "fence-1"))
    assert row_b["claim_generation"] == tickets_b[0].generation
    assert row_b["claimed_at"] == scenario.clock.now().isoformat()

    # A's consumed nonce stays burned. B needs a fresh capability through the
    # real gateway, and completion still requires the signed submitted event.
    scenario.gateway.mint_submit_token(
        nonce="takeover-b", proposal_id=scenario.order.proposal_id,
        risk_decision_id=scenario.risk_decision.id, order_tuple_hash=scenario.tuple_hash,
    )
    _arm(scenario.gateway, store_b, "fence-1", tickets_b[0].generation)
    outcome_b = scenario.gateway.submit(
        scenario.order,
        **_paper_submit_kwargs(scenario, token_nonce="takeover-b"),
    )
    assert outcome_b.ok is True
    store_b.complete_handoff(
        "fence-1", generation=tickets_b[0].generation,
        gateway=scenario.gateway, ladder=scenario.ladder,
    )
    assert _sends(broker) == 1
    assert _handoff_row(store_b.path, "fence-1")["handoff_state"] == "completed"
    assert st.audit.unresolved_intents() == []
    assert st.audit.verify_chain().ok


@_NEEDS_GATEWAY_FENCE
def test_owner_fenced_out_when_takeover_happens_after_final_admission(st, tmp_path):
    """4303610145: the fence re-runs right before transport, after admission."""
    scenario, store_a, ticket_a, broker = _claimed_paper_scenario(st, tmp_path)
    store_b = AuthorityProfileStore(store_a.path)
    initial = scenario.clock.now()
    calls = []

    def fence():
        calls.append(True)
        if len(calls) == 2:
            # Blocking work between the first fence and transport outlives the lease.
            scenario.clock._at = initial + timedelta(seconds=171)
            store_b._reconcile_handoff(
                "fence-1", order_ref=str(scenario.order.id), now=scenario.clock.now(),
                actor="recovery-owner",
                gateway=scenario.gateway, ladder=scenario.ladder,
            )
        return store_a._confirm_claim_before_broker_call(
            "fence-1", ticket_a.generation, now=scenario.clock.now(),
        )

    _use_fence(scenario.gateway, fence)
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert len(calls) == 2
    assert (outcome.ok, outcome.stage) == (False, "claim_fence")
    assert _sends(broker) == 0
    assert st.audit.unresolved_intents() == []
    assert st.audit.verify_chain().ok


@pytest.mark.parametrize("failure", ["raises", "naive", "not-datetime", "missing"])
def test_claim_fence_clock_failure_refuses_without_writing_lease(st, tmp_path, failure):
    """5558807159: a bad current clock cannot persist the old handoff timestamp."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    before = dict(_handoff_row(store.path, "fence-1"))

    class BrokenClock:
        def now(self):
            if failure == "raises":
                raise RuntimeError("clock unavailable")
            if failure == "naive":
                return scenario.clock.now().replace(tzinfo=None)
            return "not a timestamp"

    def fence():
        # The caller clock stays valid. Only the store clock is authority.
        if failure == "missing":
            store._clock = None
        else:
            store._clock = BrokenClock()
        return store._confirm_claim_before_broker_call(
            "fence-1", ticket.generation, now=scenario.clock.now(),
        )

    _use_fence(scenario.gateway, fence)
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert (outcome.ok, outcome.stage) == (False, "claim_fence")
    assert "clock" in outcome.reason
    assert dict(_handoff_row(store.path, "fence-1")) == before
    assert _sends(broker) == 0
    assert st.audit.unresolved_intents() == []
    assert st.audit.verify_chain().ok


@_NEEDS_GATEWAY_FENCE
@pytest.mark.parametrize("recovery", [False, True], ids=["handoff", "reconcile"])
def test_handoff_clock_failure_cannot_fall_back_to_handoff_time(
    st, tmp_path, monkeypatch, recovery,
):
    """5558807159: both production entry points refuse a clock that fails at the fence."""
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    store = _store(tmp_path)
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    # The post-fence refresh and the store both read this clock, not the caller clock.
    broken = []
    raise_once = {"left": 0}
    original_now = scenario.clock.now

    def raising_now():
        if raise_once["left"]:
            raise_once["left"] = 0
            raise RuntimeError("clock failed after the final audit read")
        return original_now()

    monkeypatch.setattr(scenario.clock, "now", raising_now)
    real_audit_check = gateway._final_audit_authority

    def audit_check(st_arg):
        result = real_audit_check(st_arg)
        broken.append(True)
        raise_once["left"] = 1
        return result

    monkeypatch.setattr(gateway, "_final_audit_authority", audit_check)
    kwargs = _paper_submit_kwargs(scenario)
    notional = Decimal(str(scenario.order.notional))
    if recovery:
        store.authorize(
            "profile-b", action="submit_paper", client_order_id="clock-failure-1",
            notional=notional, actor="stop-owner", now=now, order_ref=str(scenario.order.id),
        )
        store.claim_handoff("clock-failure-1", now=now, awaiting_submit=True)
        scenario.clock._at = now + CLAIM_LEASE + timedelta(seconds=1)
        expected_claimed = original_now().isoformat()
        result = reconcile_handoff_to_gateway(
            store, gateway, client_order_id="clock-failure-1",
            submit_args=(scenario.order,), submit_kwargs=kwargs, now=original_now(),
            actor="recovery-owner",
        )
    else:
        expected_claimed = now.isoformat()
        result = handoff_to_gateway(
            store, gateway, profile_id="profile-b", action="submit_paper",
            client_order_id="clock-failure-1", notional=notional, actor="stop-owner", now=now,
            submit_args=(scenario.order,), submit_kwargs=kwargs, **_PAPER_BINDINGS,
        )
    assert broken == [True]
    assert (result.outcome.ok, result.outcome.stage) == (False, "claim_fence")
    assert "clock" in result.outcome.reason
    assert _sends(broker) == 0
    row = _handoff_row(store.path, "clock-failure-1")
    assert row["claimed_at"] == expected_claimed
    assert (row["handoff_state"], row["handoff_done"]) == ("failed", 0)
    assert st.audit.unresolved_intents() == []
    assert st.audit.verify_chain().ok


@pytest.mark.parametrize("mutation", ["paper-broker", "other-adapter", "broker-type", "scope"])
def test_claim_fence_cannot_swap_transport_or_paper_scope(st, tmp_path, mutation):
    """4303485166: a paper-scope change is refused. Broker assignment is not transport."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    swapped = _ProbeBroker() if mutation == "other-adapter" else _CountingPaper()
    visible = scenario.gateway._broker

    class OtherPaper(_CountingPaper):
        pass

    def fence():
        permit = store._confirm_claim_before_broker_call(
            "fence-1", ticket.generation, now=scenario.clock.now(),
        )
        if mutation == "scope":
            scenario.gateway._execution_scope = "live"
        elif mutation == "broker-type":
            broker.__class__ = OtherPaper
        else:
            scenario.gateway._broker = swapped
        return permit

    _use_fence(scenario.gateway, fence)
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert scenario.gateway._broker is visible
    if mutation == "scope":
        assert (outcome.ok, outcome.stage) == (False, "transport_scope")
        assert _sends(broker) == swapped.calls == 0
    else:
        # The object bound at init still carries the order. The substitute does not.
        assert outcome.ok is True
        assert _sends(broker) == 1
        assert swapped.calls == 0
        assert broker.calls == 0
    assert st.audit.unresolved_intents() == []
    assert st.audit.verify_chain().ok


def test_unreadable_chain_after_refused_submit_stays_in_progress(st, tmp_path, monkeypatch):
    """An unreadable chain after a non-ok submit is not "not submitted".

    The row stays in progress, the client id is not reusable, and the
    exception propagates. ``fail_handoff`` must not run.
    """
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway, mint=False)
    path = tmp_path / "unread.sqlite"
    store = AuthorityProfileStore(path)
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )

    def _unreadable(*_args, **_kwargs):
        raise ProfileRefused("chain_unreadable", "signed chain did not verify")

    monkeypatch.setattr("brokers.authority_profiles._gateway_submitted", _unreadable)
    call = dict(
        profile_id="profile-b", action="submit_paper", client_order_id="unread-1",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner", now=now,
        submit_args=(scenario.order,), submit_kwargs=_paper_submit_kwargs(scenario),
        model_output=_VALID_MODEL, **_PAPER_BINDINGS,
    )
    with pytest.raises(ProfileRefused) as exc:
        handoff_to_gateway(store, gateway, **call)
    assert exc.value.code == "chain_unreadable"
    row = _handoff_row(path, "unread-1")
    assert (row["handoff_state"], row["handoff_done"]) == ("in_progress", 0)
    assert _refuse(lambda: handoff_to_gateway(store, gateway, **call)) == "handoff_in_progress"


def test_truncated_chain_is_not_a_not_submitted_verdict(st):
    """Deleting a signed tail must not let a negative lookup return False."""
    from sqlalchemy import delete, select

    from brokers.authority_profiles import _gateway_submitted
    from schemas.models.audit_entry import AuditEntry

    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway, mint=False)
    with st.session_factory() as session, session.begin():
        last = session.execute(
            select(AuditEntry).order_by(AuditEntry.seq.desc()).limit(1)
        ).scalar_one()
        session.execute(delete(AuditEntry).where(AuditEntry.id == last.id))
    assert _refuse(
        lambda: _gateway_submitted(gateway, "missing-order", scenario.ladder)
    ) == "chain_unreadable"


def test_configured_anchor_must_be_present_before_a_negative_lookup(st):
    from types import SimpleNamespace

    from brokers.authority_profiles import _gateway_submitted

    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway, mint=False)
    st.anchor_store = SimpleNamespace(latest=lambda: None)
    assert _refuse(
        lambda: _gateway_submitted(gateway, "missing-order", scenario.ladder)
    ) == "chain_unreadable"


def test_current_store_missing_a_ledger_table_is_not_recreated(tmp_path):
    import sqlite3

    from brokers.authority_profiles import STORE_USER_VERSION

    path = tmp_path / "damaged.sqlite"
    store = AuthorityProfileStore(path)
    _install(store)
    store._conn.close()
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE reservations")
    conn.commit()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == STORE_USER_VERSION
    assert _refuse(lambda: AuthorityProfileStore(path)) == "store"
    conn = sqlite3.connect(path)
    names = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    conn.close()
    assert "reservations" not in names


def test_historical_perimeter_does_not_reseed_missing_consumption(tmp_path):
    store = _store(tmp_path)
    _install(store)
    store.revoke("profile-b", actor="stop-owner", now=NOW, reason="operator revoke")
    with store._txn() as conn:
        conn.execute("DELETE FROM consumption")
    assert _refuse(
        lambda: _install(store, _document(profile_id="profile-c"))
    ) == "store"
    with store._txn() as conn:
        remaining = conn.execute("SELECT COUNT(*) FROM consumption").fetchone()[0]
    assert remaining == 0


def test_reconcile_refuses_an_actor_who_is_not_the_recovery_owner(st, tmp_path):
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway, mint=False)
    path = tmp_path / "recovery.sqlite"
    store = AuthorityProfileStore(path)
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="rec-1",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner", now=now,
        order_ref=str(scenario.order.id),
    )
    store.claim_handoff("rec-1", now=now, awaiting_submit=True)
    later = now + CLAIM_LEASE + timedelta(seconds=1)
    scenario.clock._at = later
    assert _refuse(
        lambda: _RECONCILE(
            store, "rec-1", order_ref=str(scenario.order.id), now=later,
            actor="recovery-owner", gateway=gateway, ladder=scenario.ladder,
        )
    ) == "unauthenticated"
    assert _refuse(
        lambda: store.reconcile_non_gateway_handoff("rec-1", now=later, actor="stop-owner")
    ) == "store"
    row = _handoff_row(path, "rec-1")
    assert (row["handoff_state"], row["handoff_done"]) == ("in_progress", 0)
    assert _refuse(
        lambda: store.reconcile_non_gateway_handoff(
            "rec-1", now=later, actor="recovery-owner"
        )
    ) == "store"


def test_stop_cannot_reconcile_an_unexpired_in_progress_submit(st, tmp_path):
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway, mint=False)
    path = tmp_path / "active-stop.sqlite"
    store = AuthorityProfileStore(path)
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    order_ref = str(scenario.order.id)
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="active-1",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner", now=now,
        order_ref=order_ref,
    )
    store.claim_handoff("active-1", now=now, awaiting_submit=True)
    store.kill("profile-b", actor="stop-owner", now=now, reason="operator kill")
    assert _refuse(
        lambda: store._reconcile_handoff(
            "active-1", order_ref=order_ref, now=now, actor="recovery-owner",
            gateway=gateway, ladder=scenario.ladder,
        )
    ) == "claim_leased"
    row = _handoff_row(path, "active-1")
    assert (row["handoff_state"], row["handoff_done"]) == ("in_progress", 0)


def test_reconcile_refuses_a_null_order_ref_on_submit_paper(tmp_path):
    store = _store(tmp_path)
    _install(store)
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="null-ref",
        notional=Decimal("10.00"), actor="stop-owner", now=NOW,
    )
    ticket = store.claim_handoff("null-ref", now=NOW, awaiting_submit=True)
    assert isinstance(ticket, ClaimTicket)
    assert _refuse(
        lambda: store._reconcile_handoff(
            "null-ref", order_ref=None, now=NOW, actor="recovery-owner",
        )
    ) == "order_ref"
    row = _handoff_row(store.path, "null-ref")
    assert (row["handoff_state"], row["handoff_done"]) == ("in_progress", 0)


def _reservation_flags(path, client_order_id):
    import sqlite3

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT handoff_state, handoff_done, reconciled, claim_generation "
        "FROM reservations WHERE client_order_id = ?",
        (client_order_id,),
    ).fetchone()
    conn.close()
    return row


def test_store_clock_is_read_only_after_both_locks(tmp_path):
    """A caller clock, and any sample taken before the SQLite lock, is not authority."""
    box = []

    class LockClock:
        def __init__(self):
            self.samples = []
            self.fence = False

        def now(self):
            if not box:
                return NOW
            store = box[0]
            locked = store._lock.locked() and store._conn.in_transaction
            if self.fence:
                self.samples.append(locked)
            if self.fence and locked:
                return NOW + timedelta(seconds=50)
            return NOW

    clock = LockClock()
    store = AuthorityProfileStore(tmp_path / "lockclock.sqlite", clock=clock)
    box.append(store)
    _install(store)
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="lock-1",
        notional=Decimal("10.00"), actor="stop-owner", now=NOW, order_ref="ord-lock",
    )
    ticket = store.claim_handoff("lock-1", now=NOW, awaiting_submit=True)
    clock.fence = True
    store._confirm_claim_before_broker_call("lock-1", ticket.generation, now=NOW)
    assert clock.samples
    assert all(clock.samples)
    assert _handoff_row(store.path, "lock-1")["claimed_at"] == (
        NOW + timedelta(seconds=50)
    ).isoformat()


def test_caller_now_cannot_take_over_a_live_lease(st, tmp_path):
    """A future ``now`` argument must not expire a lease the store clock still holds."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    lied = scenario.clock.now() + CLAIM_LEASE + timedelta(seconds=1)
    assert _refuse(
        lambda: _RECONCILE(
            store, "fence-1", order_ref=str(scenario.order.id), now=lied,
            actor="recovery-owner", gateway=scenario.gateway, ladder=scenario.ladder,
            capability=_CAPS[(str(store.path), "profile-b")],
        )
    ) == "claim_leased"
    assert _sends(broker) == 0
    assert _handoff_row(store.path, "fence-1")["claim_generation"] == ticket.generation


def test_recovery_uses_the_stop_capability_not_the_owner_name(st, tmp_path):
    """4356344782 / 4303448120: the recovery-owner string is not a credential."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    order_ref = str(scenario.order.id)
    now = scenario.clock.now()
    assert _refuse(
        lambda: _RECONCILE(
            store, "fence-1", order_ref=order_ref, now=now, actor="recovery-owner",
            gateway=scenario.gateway, ladder=scenario.ladder,
        )
    ) == "unauthenticated"
    assert _refuse(
        lambda: _RECONCILE(
            store, "fence-1", order_ref=order_ref,
            now=now + CLAIM_LEASE + timedelta(seconds=1),
            actor="not-the-recovery-owner", gateway=scenario.gateway,
            ladder=scenario.ladder, capability=_CAPS[(str(store.path), "profile-b")],
        )
    ) == "claim_leased"
    assert _sends(broker) == 0
    assert _handoff_row(store.path, "fence-1")["claim_generation"] == ticket.generation


def test_submission_lookup_and_chain_check_share_one_audit_snapshot(st, monkeypatch):
    """4356354255: a row appended between two sessions must not verify a stale negative."""
    from sqlalchemy import delete, select

    from brokers.authority_profiles import _gateway_submitted
    from schemas.models.audit_entry import AuditEntry

    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(
        st, mode=Mode.paper, broker=broker, gateway=gateway, mint=False,
    )
    opens = {"n": 0}
    real = st.audit._sf

    def counting():
        opens["n"] += 1
        if opens["n"] >= 2:
            with real() as session, session.begin():
                last = session.execute(
                    select(AuditEntry).order_by(AuditEntry.seq.desc()).limit(1)
                ).scalar_one()
                session.execute(delete(AuditEntry).where(AuditEntry.id == last.id))
        return real()

    monkeypatch.setattr(st.audit, "_sf", counting)
    monkeypatch.setattr(st, "session_factory", counting)
    assert _gateway_submitted(gateway, "not-on-chain", scenario.ladder) is False
    assert opens["n"] == 1


@pytest.mark.parametrize("stop", ["revoke", "kill", "hold", "expired"])
def test_revocation_during_profile_handoff_does_not_reach_the_broker(st, tmp_path, stop):
    """The final fence re-checks authority after risk_recheck and before transport."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)

    def risk_recheck(_positions):
        if stop == "expired":
            scenario.clock._at = datetime(2026, 7, 8, 14, 0, 1, tzinfo=timezone.utc)
        elif stop == "revoke":
            store.revoke(
                "profile-b", actor="stop-owner", now=scenario.clock.now(),
                reason="operator revoke",
            )
        elif stop == "kill":
            store.kill(
                "profile-b", actor="stop-owner", now=scenario.clock.now(),
                reason="operator kill",
            )
        else:
            store.hold(
                "profile-b", actor="stop-owner", now=scenario.clock.now(),
                reason="operator hold",
            )
        return {"decision": "approved", "failed_checks": []}

    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    outcome = scenario.gateway.submit(
        scenario.order,
        **_paper_submit_kwargs(scenario, risk_recheck=risk_recheck),
    )
    assert outcome.ok is False
    assert _sends(broker) == 0
    assert broker._client.submitted == []


def test_lost_claim_generation_during_risk_recheck_does_not_reach_the_broker(st, tmp_path):
    """4356344702: a takeover between the first fence and transport must not send."""
    scenario, store_a, ticket_a, broker = _claimed_paper_scenario(st, tmp_path)
    store_b = AuthorityProfileStore(store_a.path)
    initial = scenario.clock.now()

    def risk_recheck(_positions):
        scenario.clock._at = initial + timedelta(seconds=171)
        ticket_b = store_b._reconcile_handoff(
            "fence-1", order_ref=str(scenario.order.id), now=scenario.clock.now(),
            actor="recovery-owner", gateway=scenario.gateway, ladder=scenario.ladder,
        )
        assert isinstance(ticket_b, ClaimTicket)
        assert ticket_b.generation == ticket_a.generation + 1
        return {"decision": "approved", "failed_checks": []}

    _arm(scenario.gateway, store_a, "fence-1", ticket_a.generation)
    outcome = scenario.gateway.submit(
        scenario.order,
        **_paper_submit_kwargs(scenario, risk_recheck=risk_recheck),
    )
    assert outcome.ok is False
    assert _sends(broker) == 0
    assert broker._client.submitted == []
    assert _handoff_row(store_a.path, "fence-1")["claim_generation"] == (
        ticket_a.generation + 1
    )


def test_ambiguous_accept_then_timeout_is_checked_with_the_broker(st, tmp_path):
    """An accept followed by a timeout must not release the profile stop."""
    broker = _CountingPaper()
    # The fault sits on the client. Profile transport calls
    # AlpacaPaperBroker.submit_order, so a subclass override never runs.
    real_submit = broker._client.submit_order

    def ack_then_timeout(canonical):
        real_submit(canonical)
        raise TimeoutError("response lost after accept")

    broker._client.submit_order = ack_then_timeout
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    store = _store(tmp_path)
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    notional = Decimal(str(scenario.order.notional))
    handoff_to_gateway(
        store, gateway, profile_id="profile-b", action="submit_paper",
        client_order_id="ambiguous-1", notional=notional, actor="stop-owner",
        now=now, submit_args=(scenario.order,),
        submit_kwargs=_paper_submit_kwargs(scenario), **_PAPER_BINDINGS,
    )
    flags = _reservation_flags(store.path, "ambiguous-1")
    assert (flags["handoff_state"], int(flags["handoff_done"]), int(flags["reconciled"])) == (
        "completed", 1, 1,
    )
    store.release_stop(
        "profile-b", actor="stop-owner", now=now, reconciled=True,
    )
    assert len(broker._client.submitted) == 1


def test_unreadable_broker_status_does_not_release_the_profile_stop(st, tmp_path, monkeypatch):
    """A status read that is not a clean miss stays unknown and keeps the stop."""
    def unreadable(*_args, **_kwargs):
        raise RuntimeError("broker status unread")

    monkeypatch.setattr(AlpacaPaperBroker, "find_order", unreadable)
    broker = _CountingPaper()

    def timeout_no_ack(_canonical):
        raise TimeoutError("response lost before ack")

    broker._client.submit_order = timeout_no_ack
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    store = _store(tmp_path)
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    with pytest.raises(ProfileRefused) as exc:
        handoff_to_gateway(
            store, gateway, profile_id="profile-b", action="submit_paper",
            client_order_id="unknown-1", notional=Decimal(str(scenario.order.notional)),
            actor="stop-owner", now=now, submit_args=(scenario.order,),
            submit_kwargs=_paper_submit_kwargs(scenario), **_PAPER_BINDINGS,
        )
    assert exc.value.code == "broker_unknown"
    flags = _reservation_flags(store.path, "unknown-1")
    assert (flags["handoff_state"], flags["reconciled"]) == ("in_progress", 0)
    assert _refuse(
        lambda: store._reconcile_handoff(
            "unknown-1", order_ref=str(scenario.order.id), now=now,
            actor="recovery-owner", gateway=gateway, ladder=scenario.ladder,
        )
    ) == "broker_unknown"
    assert _reservation_flags(store.path, "unknown-1")["reconciled"] == 0


def test_direct_paper_submit_requires_a_validated_claim(st, tmp_path):
    scenario, _store, _ticket, broker = _claimed_paper_scenario(st, tmp_path)
    missing = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert (missing.ok, missing.stage) == (False, "claim_fence")
    assert _sends(broker) == 0
    # The unarmed submit reaches capability consumption on the split gateway
    # before the fence refuses. Each later refusal needs its own token.
    _use_fence(scenario.gateway, lambda: None)
    scenario.gateway.mint_submit_token(
        nonce="noop", proposal_id=scenario.order.proposal_id,
        risk_decision_id=scenario.risk_decision.id, order_tuple_hash=scenario.tuple_hash,
    )
    noop = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario, token_nonce="noop"),
    )
    assert (noop.ok, noop.stage) == (False, "claim_fence")
    assert "no admission" in noop.reason
    assert _sends(broker) == 0
    scenario.gateway.mint_submit_token(
        nonce="foreign", proposal_id=scenario.order.proposal_id,
        risk_decision_id=scenario.risk_decision.id, order_tuple_hash=scenario.tuple_hash,
    )
    foreign = scenario.gateway.submit(
        scenario.order,
        claim_fence=lambda: None,
        **_paper_submit_kwargs(scenario, token_nonce="foreign"),
    )
    assert (foreign.ok, foreign.stage) == (False, "claim_fence")
    assert "bound at setup" in foreign.reason
    assert broker._client.submitted == []


def test_instance_precheck_cannot_skip_the_risk_gate(st, tmp_path):
    """4303742543: assigning ``_precheck`` must not skip risk, mode, or approval."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    scenario.gateway._precheck = lambda *_args, **_kwargs: None

    def rejected(_positions):
        return {"decision": "rejected", "failed_checks": ["hard"]}

    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    outcome = scenario.gateway.submit(
        scenario.order,
        **_paper_submit_kwargs(scenario, risk_recheck=rejected),
    )
    assert (outcome.ok, outcome.stage) == (False, "risk")
    assert _sends(broker) == 0


def test_instance_submit_order_cannot_replace_the_admitted_adapter(st, tmp_path):
    """4303615310: an instance ``submit_order`` must not carry the paper order."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    called = []

    def evil(canonical):
        called.append(canonical)
        raise AssertionError("replaced submit_order")

    broker.submit_order = evil
    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert called == []
    assert outcome.ok is True
    assert _sends(broker) == 1
    assert len(broker._client.submitted) == 1
    assert broker.calls == 0


def test_risk_recheck_cannot_replace_the_pinned_broker(st, tmp_path):
    """A callback inside submit cannot point transport at another broker."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    swapped = _ProbeBroker()
    visible = scenario.gateway._broker

    def risk_recheck(_positions):
        scenario.gateway._broker = swapped
        assert scenario.gateway._broker is visible
        return {"decision": "approved", "failed_checks": []}

    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    outcome = scenario.gateway.submit(
        scenario.order,
        **_paper_submit_kwargs(scenario, risk_recheck=risk_recheck),
    )
    assert swapped.calls == 0
    assert outcome.ok is True
    assert _sends(broker) == 1
    assert scenario.gateway._broker is visible
    assert st.audit.unresolved_intents() == []


def test_confirm_claim_before_broker_call_does_not_submit(st, tmp_path, monkeypatch):
    """The store fence renews the lease. It must not enter gateway.submit."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)

    def refused(*_args, **_kwargs):
        raise AssertionError("claim confirmation must not submit")

    monkeypatch.setattr(PaperOrderGateway, "submit", refused)
    monkeypatch.setattr(OrderGateway, "submit", refused)
    store._confirm_claim_before_broker_call(
        "fence-1", ticket.generation, now=scenario.clock.now(),
    )
    assert _sends(broker) == 0


def test_profile_transport_ignores_a_subclass_submit_override(st, tmp_path):
    """Profile transport is AlpacaPaperBroker.submit_order, captured before the fence."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)

    class OverridePaper(_CountingPaper):
        def submit_order(self, canonical):
            raise AssertionError("subclass submit_order")

    broker.__class__ = OverridePaper
    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert outcome.ok is True
    assert broker.calls == 0
    assert len(broker._client.submitted) == 1


def test_fence_rechecks_risk_after_it_returns(st, tmp_path):
    """A risk re-run that flips inside the fence is refused at stage risk."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    state = {"decision": "approved"}

    def recheck(_positions):
        return {"decision": state["decision"], "failed_checks": []}

    def fence():
        permit = store._confirm_claim_before_broker_call(
            "fence-1", ticket.generation, now=scenario.clock.now(),
        )
        state["decision"] = "rejected"
        return permit

    _use_fence(scenario.gateway, fence)
    outcome = scenario.gateway.submit(
        scenario.order,
        **_paper_submit_kwargs(scenario, risk_recheck=recheck),
    )
    assert (outcome.ok, outcome.stage) == (False, "risk")
    assert _sends(broker) == 0
    assert st.audit.unresolved_intents() == []


def test_fence_rechecks_feed_staleness_after_it_returns(st, tmp_path):
    """A feed that goes stale inside the fence is refused at stage market_data."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)

    class Guard:
        def __init__(self):
            self.reason = None

        def stale_reason(self, symbol, *, now, limit_s):
            del now, limit_s
            assert symbol == scenario.order.symbol
            return self.reason

    guard = Guard()
    scenario.st.market_data = guard

    def fence():
        permit = store._confirm_claim_before_broker_call(
            "fence-1", ticket.generation, now=scenario.clock.now(),
        )
        guard.reason = "market quote is stale"
        return permit

    _use_fence(scenario.gateway, fence)
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert (outcome.ok, outcome.stage) == (False, "market_data")
    assert "stale" in outcome.reason
    assert _sends(broker) == 0
    assert st.audit.unresolved_intents() == []


def test_fence_rechecks_approval_ttl_after_it_returns(st, tmp_path):
    """An approval that expires inside the fence is refused at stage approval."""
    from schemas.models.risk_decision import RiskDecision

    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    scenario.gateway._ttl_override = 900
    opened = scenario.clock.now()
    with st.session_factory() as session, session.begin():
        row = session.get(RiskDecision, scenario.risk_decision.id)
        row.evaluated_at = opened - timedelta(seconds=890)

    def fence():
        permit = store._confirm_claim_before_broker_call(
            "fence-1", ticket.generation, now=opened,
        )
        scenario.clock._at = opened + timedelta(seconds=20)
        return permit

    _use_fence(scenario.gateway, fence)
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert outcome.ok is False
    assert outcome.stage == "approval"
    assert "expired" in outcome.reason
    assert _sends(broker) == 0
    assert st.audit.unresolved_intents() == []


@_NEEDS_GATEWAY_FENCE
def test_nested_submit_inside_claim_fence_is_reentrant(st, tmp_path):
    """A submit started from inside the fence is stage reentrant_submit."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    scenario.gateway.mint_submit_token(
        nonce="nested",
        proposal_id=scenario.order.proposal_id,
        risk_decision_id=scenario.risk_decision.id,
        order_tuple_hash=scenario.tuple_hash,
    )
    nested = {}

    def fence():
        permit = store._confirm_claim_before_broker_call(
            "fence-1", ticket.generation, now=scenario.clock.now(),
        )
        nested["outcome"] = scenario.gateway.submit(
            scenario.order,
            **_paper_submit_kwargs(scenario, token_nonce="nested"),
        )
        return permit

    _use_fence(scenario.gateway, fence)
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert nested["outcome"].ok is False
    assert nested["outcome"].stage == "reentrant_submit"
    assert outcome.ok is True
    assert _sends(broker) == 1
    assert scenario.gateway.live_token_count() == 1
    assert st.audit.unresolved_intents() == []


def test_admission_lock_acquire_times_out_at_five_seconds(st, tmp_path):
    """Admission-lock acquisition gives up after 5 seconds at stage admission_lock."""
    import threading

    try:
        from brokers.gateway import _ADMISSION_WAIT_BUDGET_S
    except ImportError:
        _ADMISSION_WAIT_BUDGET_S = 5.0

    class _BudgetLock:
        def __init__(self):
            self._lock = threading.Lock()
            self.timeouts: list = []

        def acquire(self, blocking=True, timeout=-1):
            self.timeouts.append(timeout)
            if timeout is not None and timeout >= 0:
                return False
            return self._lock.acquire(blocking, timeout)

        def release(self):
            self._lock.release()

        def __enter__(self):
            if not self.acquire():
                raise RuntimeError("admission lock context manager acquired nothing")
            return self

        def __exit__(self, *_args):
            self.release()
            return False

    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    lock = _BudgetLock()
    scenario.st.safety_admission_lock = lock
    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert _ADMISSION_WAIT_BUDGET_S == 5.0
    assert (outcome.ok, outcome.stage) == (False, "admission_lock")
    assert "lock" in outcome.reason
    assert _ADMISSION_WAIT_BUDGET_S in lock.timeouts
    assert _sends(broker) == 0
    assert st.audit.unresolved_intents() == []


@_NEEDS_GATEWAY_FENCE
def test_claim_fence_base_exception_records_the_intent_and_propagates(st, tmp_path):
    """KeyboardInterrupt from the fence covers the open intent, then propagates."""
    from sqlalchemy import select

    from schemas.models.audit_entry import AuditEntry

    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)

    def fence():
        store._confirm_claim_before_broker_call(
            "fence-1", ticket.generation, now=scenario.clock.now(),
        )
        raise KeyboardInterrupt("fence interrupted")

    _use_fence(scenario.gateway, fence)
    with pytest.raises(KeyboardInterrupt, match="fence interrupted"):
        scenario.gateway.submit(
            scenario.order, **_paper_submit_kwargs(scenario),
        )
    assert _sends(broker) == 0
    assert st.audit.unresolved_intents() == []
    with st.session_factory() as session:
        entries = session.execute(select(AuditEntry)).scalars().all()
    covered = [
        entry for entry in entries
        if (entry.payload or {}).get("safety_reason") == "claim_fence"
        and "fence interrupted" in str((entry.payload or {}).get("aborted", ""))
    ]
    assert covered
    assert st.audit.verify_chain().ok


def test_store_clock_must_be_the_state_clock(st, tmp_path):
    """The post-fence refresh reads ``st.clock``. A different store clock is refused."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    other = AuthorityProfileStore(store.path, clock=FrozenClock(scenario.clock.now()))
    assert _refuse(
        lambda: scenario.gateway.arm_profile_claim(other, "fence-1", ticket.generation)
    ) == "clock"
    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    store._clock = FrozenClock(scenario.clock.now())
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert (outcome.ok, outcome.stage) == (False, "claim_fence")
    assert "clock" in outcome.reason
    assert _sends(broker) == 0
    assert st.audit.unresolved_intents() == []


def test_bound_fence_attaches_remaining_when_the_lease_is_under_five_seconds(st, tmp_path, monkeypatch):
    """A renewed lease shorter than the admission budget is reported on the permit."""
    from brokers.authority_profiles import TransportPermit

    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    scenario.gateway._profile_fence.bind_order(scenario.order)
    scenario.gateway._profile_fence._local.producing_runtime = "claude"
    full = scenario.gateway._profile_fence()
    assert type(full) is TransportPermit
    assert full.remaining_s is None
    monkeypatch.setattr(
        "brokers.authority_profiles.CLAIM_LEASE", timedelta(seconds=4),
    )
    short = scenario.gateway._profile_fence()
    assert type(short) is TransportPermit
    assert short.remaining_s is not None
    assert 0 <= short.remaining_s < 5
    assert _sends(broker) == 0


def test_second_gateway_on_the_same_state_is_reentrant(st, tmp_path):
    """In-flight tracking lives on the shared state, so a second gateway is refused."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    other_broker = _CountingPaper()
    other = PaperOrderGateway(other_broker, st=st)
    _arm(other, store, "fence-1", ticket.generation)
    other.mint_submit_token(
        nonce="other",
        proposal_id=scenario.order.proposal_id,
        risk_decision_id=scenario.risk_decision.id,
        order_tuple_hash=scenario.tuple_hash,
    )
    nested = {}

    def fence():
        permit = store._confirm_claim_before_broker_call(
            "fence-1", ticket.generation, now=scenario.clock.now(),
        )
        nested["outcome"] = other.submit(
            scenario.order,
            **_paper_submit_kwargs(scenario, token_nonce="other"),
        )
        return permit

    _use_fence(scenario.gateway, fence)
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert nested["outcome"].ok is False
    assert nested["outcome"].stage == "reentrant_submit"
    assert outcome.ok is True
    assert _sends(broker) == 1
    assert _sends(other_broker) == 0
    assert other.live_token_count() == 1
    assert st.audit.unresolved_intents() == []


def test_post_fence_refresh_uses_the_state_clock(st, tmp_path):
    """A frozen caller clock cannot hide an approval that aged on ``st.clock``."""
    from schemas.models.risk_decision import RiskDecision

    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    scenario.gateway._ttl_override = 900
    opened = scenario.clock.now()
    with st.session_factory() as session, session.begin():
        row = session.get(RiskDecision, scenario.risk_decision.id)
        row.evaluated_at = opened - timedelta(seconds=890)
    frozen = FrozenClock(opened)

    def fence():
        permit = store._confirm_claim_before_broker_call(
            "fence-1", ticket.generation, now=opened,
        )
        scenario.clock._at = opened + timedelta(seconds=40)
        return permit

    _use_fence(scenario.gateway, fence)
    outcome = scenario.gateway.submit(
        scenario.order,
        **_paper_submit_kwargs(scenario, clock=frozen),
    )
    assert outcome.ok is False
    assert outcome.stage == "approval"
    assert "expired" in outcome.reason
    assert _sends(broker) == 0
    assert st.audit.unresolved_intents() == []


def test_reentrant_submit_is_refused_before_any_fence(st, tmp_path):
    """A nested submit returns before the fence, the token, or a second send."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    scenario.gateway.mint_submit_token(
        nonce="nested",
        proposal_id=scenario.order.proposal_id,
        risk_decision_id=scenario.risk_decision.id,
        order_tuple_hash=scenario.tuple_hash,
    )
    nested = {}
    entered = {"n": 0}

    def fence():
        entered["n"] += 1
        permit = store._confirm_claim_before_broker_call(
            "fence-1", ticket.generation, now=scenario.clock.now(),
        )
        nested["outcome"] = scenario.gateway.submit(
            scenario.order,
            **_paper_submit_kwargs(scenario, token_nonce="nested"),
        )
        return permit

    _use_fence(scenario.gateway, fence)
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert entered["n"] >= 1
    assert nested["outcome"].ok is False
    assert nested["outcome"].stage == "reentrant_submit"
    assert "before the capability token" in nested["outcome"].reason
    assert outcome.ok is True
    assert _sends(broker) == 1
    assert scenario.gateway.live_token_count() == 1
    assert st.audit.unresolved_intents() == []


@pytest.mark.parametrize(
    "stop",
    ["revoke", "kill", "hold", "expired", "takeover", "other-order"],
)
def test_permit_consumption_rechecks_the_live_claim(st, tmp_path, stop):
    """4356858716 / 4356865633: a cached nonce is not authority for a later order."""
    from brokers.authority_profiles import accept_transport_permit

    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    order_ref = str(scenario.order.id)
    permit = store._confirm_claim_before_broker_call(
        "fence-1", ticket.generation, now=scenario.clock.now(),
    )
    if stop == "revoke":
        store.revoke(
            "profile-b", actor="stop-owner", now=scenario.clock.now(),
            reason="operator revoke",
        )
        code = "revoked"
    elif stop == "kill":
        store.kill(
            "profile-b", actor="stop-owner", now=scenario.clock.now(),
            reason="operator kill",
        )
        code = "kill_active"
    elif stop == "hold":
        store.hold(
            "profile-b", actor="stop-owner", now=scenario.clock.now(),
            reason="operator hold",
        )
        code = "hold_active"
    elif stop == "expired":
        scenario.clock._at = datetime(2026, 7, 8, 14, 0, 1, tzinfo=timezone.utc)
        code = "expired"
    elif stop == "takeover":
        scenario.clock._at = scenario.clock.now() + CLAIM_LEASE + timedelta(seconds=1)
        taken = store._reconcile_handoff(
            "fence-1", order_ref=order_ref, now=scenario.clock.now(),
            actor="recovery-owner", gateway=scenario.gateway, ladder=scenario.ladder,
        )
        assert isinstance(taken, ClaimTicket)
        assert taken.generation == ticket.generation + 1
        code = "stale_claim"
    else:
        assert _refuse(
            lambda: accept_transport_permit(permit, order_ref="other-order")
        ) == "client_order_conflict"
        assert permit.nonce in store._live_permits
        binding = accept_transport_permit(
            permit, order_ref=order_ref, client=broker._client,
            order=scenario.order, producing_runtime="claude",
        )
        assert binding[3] == order_ref
        assert store._admitted_clients[order_ref] is broker._client
        assert permit.nonce not in store._live_permits
        assert _refuse(
            lambda: accept_transport_permit(permit, order_ref=order_ref)
        ) == "claim_required"
        assert _sends(broker) == 0
        return
    assert _refuse(
        lambda: accept_transport_permit(permit, order_ref=order_ref)
    ) == code
    assert permit.nonce in store._live_permits
    assert _sends(broker) == 0


def test_permit_bound_to_another_order_cannot_submit(st, tmp_path):
    """4356865633: submitting order A with order B's permit does not send."""
    scenario, store, _ticket, broker = _claimed_paper_scenario(st, tmp_path)
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="other-order-1",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner",
        now=scenario.clock.now(), order_ref="not-this-order",
    )
    other = store.claim_handoff(
        "other-order-1", now=scenario.clock.now(), awaiting_submit=True,
    )
    assert isinstance(other, ClaimTicket)
    foreign = store._confirm_claim_before_broker_call(
        "other-order-1", other.generation, now=scenario.clock.now(),
    )
    _use_fence(scenario.gateway, lambda: foreign)
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert (outcome.ok, outcome.stage) == (False, "claim_fence")
    assert "different order" in outcome.reason
    assert foreign.nonce in store._live_permits
    assert _sends(broker) == 0
    assert st.audit.unresolved_intents() == []


def test_cached_permit_cannot_submit_after_revoke(st, tmp_path):
    """The bound fence consumes the permit, including on the split gateway."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    revoked = store._confirm_claim_before_broker_call(
        "fence-1", ticket.generation, now=scenario.clock.now(),
    )
    store.revoke(
        "profile-b", actor="stop-owner", now=scenario.clock.now(),
        reason="operator revoke",
    )
    _use_fence(scenario.gateway, lambda: revoked)
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert (outcome.ok, outcome.stage) == (False, "claim_fence")
    assert "no longer valid" in outcome.reason
    assert revoked.nonce in store._live_permits
    assert _sends(broker) == 0
    assert st.audit.unresolved_intents() == []


def test_pending_reconcile_does_not_query_the_broker(st, tmp_path, monkeypatch):
    """4356858487: a pending reservation is failed from the chain, not the broker."""
    calls = []

    def boom(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("pending reconcile must not ask the broker")

    monkeypatch.setattr(AlpacaPaperBroker, "find_order", boom)
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway, mint=False)
    store = _store(tmp_path)
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    order_ref = str(scenario.order.id)
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="pending-1",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner", now=now,
        order_ref=order_ref,
    )
    verdict = store._reconcile_handoff(
        "pending-1", order_ref=order_ref, now=now, actor="recovery-owner",
        gateway=gateway, ladder=scenario.ladder,
    )
    assert verdict == "failed"
    assert calls == []
    flags = _reservation_flags(store.path, "pending-1")
    assert (flags["handoff_state"], flags["reconciled"]) == ("failed", 1)


def test_broker_status_during_reconcile_does_not_hold_the_store_lock(st, tmp_path, monkeypatch):
    """4356858162: kill proceeds while a reconcile is waiting on the broker."""
    scenario, store, _ticket, _broker = _claimed_paper_scenario(st, tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def blocking_find(self, *, broker_order_id=None, client_order_id=None):
        del self, broker_order_id, client_order_id
        entered.set()
        if not release.wait(2):
            raise AssertionError("broker lookup was not released")
        return None

    monkeypatch.setattr(AlpacaPaperBroker, "find_order", blocking_find)
    result = {}

    def reconcile():
        try:
            store._reconcile_handoff(
                "fence-1", order_ref=str(scenario.order.id), now=scenario.clock.now(),
                actor="recovery-owner", gateway=scenario.gateway, ladder=scenario.ladder,
            )
        except ProfileRefused as exc:
            result["code"] = exc.code

    worker = threading.Thread(target=reconcile)
    killed = threading.Event()

    def do_kill():
        store.kill(
            "profile-b", actor="stop-owner", now=scenario.clock.now(),
            reason="operator kill",
        )
        killed.set()

    killer = threading.Thread(target=do_kill)
    worker.start()
    try:
        assert entered.wait(2)
        killer.start()
        assert killed.wait(2), "kill blocked while broker lookup held the store lock"
    finally:
        release.set()
        worker.join(3)
        if killer.ident is not None:
            killer.join(3)
    assert not worker.is_alive()
    assert result["code"] == "claim_leased"
    assert killed.is_set()
    assert _handoff_row(store.path, "fence-1")["handoff_state"] == "in_progress"


def test_substituted_broker_client_cannot_prove_an_order_absent(st, tmp_path):
    """4356858842: recovery cannot clear a stop by swapping in an empty client."""
    from brokers.authority_profiles import _broker_order_visibility

    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    order_ref = str(scenario.order.id)
    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    scenario.gateway._profile_fence.bind_order(scenario.order)
    scenario.gateway._profile_fence._local.producing_runtime = "claude"
    scenario.gateway._profile_fence()
    broker._client.submit_order({"client_order_id": f"cockpit-{order_ref}"[:128]})
    assert _broker_order_visibility(
        scenario.gateway, order_ref, store=store,
    ) == "present"
    other = PaperOrderGateway(_CountingPaper(), st=st)
    assert _broker_order_visibility(other, order_ref, store=store) == "unknown"
    assert _refuse(
        lambda: store._reconcile_handoff(
            "fence-1", order_ref=order_ref, now=scenario.clock.now(),
            actor="recovery-owner", gateway=other, ladder=scenario.ladder,
        )
    ) == "broker_unknown"
    flags = _reservation_flags(store.path, "fence-1")
    assert (flags["handoff_state"], flags["reconciled"]) == ("in_progress", 0)
    assert _sends(broker) == 1


@pytest.mark.parametrize("visibility", ["present", "absent", "unknown"])
def test_order_state_refusal_asks_the_broker(st, tmp_path, monkeypatch, visibility):
    """4357032681: order_state is checked with the broker before fail_handoff."""
    from brokers.authority_profiles import _settle_failed

    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    order_ref = str(scenario.order.id)
    if visibility == "present":
        broker._client.submit_order({"client_order_id": f"cockpit-{order_ref}"[:128]})
        st.audit.intent(
            "order_submit",
            refs={
                "order_id": order_ref,
                "approval_id": scenario.approval.id,
                "risk_decision_id": scenario.risk_decision.id,
            },
            payload={"symbol": "AAPL", "side": "buy"},
        )
    elif visibility == "unknown":
        def unreadable(*_args, **_kwargs):
            raise RuntimeError("broker status unread")

        monkeypatch.setattr(AlpacaPaperBroker, "find_order", unreadable)
    outcome = GateOutcome(False, "order_state", "order state is not a clean miss")
    kwargs = _paper_submit_kwargs(scenario)
    now = scenario.clock.now()
    if visibility == "absent":
        assert _settle_failed(
            store, scenario.gateway, "fence-1", order_ref, ticket.generation,
            kwargs, now, outcome=outcome,
        ) is False
        assert _handoff_row(store.path, "fence-1")["handoff_state"] == "failed"
        return
    if visibility == "present":
        assert _settle_failed(
            store, scenario.gateway, "fence-1", order_ref, ticket.generation,
            kwargs, now, outcome=outcome,
        ) is True
        flags = _reservation_flags(store.path, "fence-1")
        assert (flags["handoff_state"], int(flags["reconciled"])) == ("completed", 1)
        return
    assert _refuse(
        lambda: _settle_failed(
            store, scenario.gateway, "fence-1", order_ref, ticket.generation,
            kwargs, now, outcome=outcome,
        )
    ) == "broker_unknown"
    assert _handoff_row(store.path, "fence-1")["handoff_state"] == "in_progress"


def test_risk_recheck_cannot_swap_broker_submit_order(st, tmp_path):
    """A risk callback cannot replace the transport send, including after a revoke."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    calls = []

    def evil(canonical):
        calls.append(canonical)
        return {
            "broker_order_id": "evil",
            "status": "accepted",
            "filled_qty": Decimal("0"),
            "avg_fill_price": None,
        }

    def risk_recheck(_positions):
        scenario.gateway._broker.submit_order = evil
        store.revoke(
            "profile-b", actor="stop-owner", now=scenario.clock.now(),
            reason="operator revoke",
        )
        return {"decision": "approved", "failed_checks": []}

    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    outcome = scenario.gateway.submit(
        scenario.order,
        **_paper_submit_kwargs(scenario, risk_recheck=risk_recheck),
    )
    assert calls == []
    assert outcome.ok is False
    assert _sends(broker) == 0
    assert broker._client.submitted == []


@pytest.mark.parametrize("change", ["halt", "watch_only", "stale_feed", "approval", "risk"])
def test_safety_change_during_final_authority_check_does_not_send(st, tmp_path, change):
    """HALT, watch-only, a stale feed, approval expiry, or a risk flip during the final check."""
    from orchestrator.halt import default_halt_path, set_halt
    from schemas.models.risk_decision import RiskDecision

    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    state = {"decision": "approved"}
    opened = scenario.clock.now()
    if change == "approval":
        scenario.gateway._ttl_override = 900
        with st.session_factory() as session, session.begin():
            row = session.get(RiskDecision, scenario.risk_decision.id)
            row.evaluated_at = opened - timedelta(seconds=890)

    def risk_recheck(_positions):
        return {"decision": state["decision"], "failed_checks": []}

    real = store._recheck_transport_authority

    def during_authority(*args, **kwargs):
        real(*args, **kwargs)
        if change == "halt":
            set_halt(default_halt_path(), reason="HALT during final authority")
        elif change == "watch_only":
            scenario.breaker.enter_watch_only("claude", "watch-only during final authority")
        elif change == "stale_feed":
            class Guard:
                def stale_reason(self, symbol, *, now, limit_s):
                    del symbol, now, limit_s
                    return "market quote is stale"

            scenario.st.market_data = Guard()
        elif change == "approval":
            scenario.clock._at = opened + timedelta(seconds=40)
        else:
            state["decision"] = "rejected"

    store._recheck_transport_authority = during_authority
    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    outcome = scenario.gateway.submit(
        scenario.order,
        **_paper_submit_kwargs(scenario, risk_recheck=risk_recheck),
    )
    assert outcome.ok is False
    assert _sends(broker) == 0
    if change == "approval":
        assert "expired" in outcome.reason
    elif change == "halt":
        assert "HALT" in outcome.reason
    elif change == "stale_feed":
        assert "stale" in outcome.reason


@pytest.mark.parametrize("binding", ["notional", "account", "provider"])
def test_permit_bindings_cover_account_notional_and_provider(st, tmp_path, binding, monkeypatch):
    """A one-dollar reservation, a foreign account, or another provider does not send."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    kwargs = _paper_submit_kwargs(scenario)
    if binding == "notional":
        with store._txn() as conn:
            conn.execute(
                "UPDATE reservations SET notional = ? WHERE client_order_id = ?",
                ("1.00", "fence-1"),
            )
    elif binding == "account":
        scenario.order.account = "other-account"
    else:
        monkeypatch.setattr(scenario.breaker, "assert_fresh_analysis", lambda *_a, **_k: None)
        kwargs["producing_runtime"] = "codex"
        kwargs["analysis_vendor"] = "codex"
    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    outcome = scenario.gateway.submit(scenario.order, **kwargs)
    assert outcome.ok is False
    assert _sends(broker) == 0
    if binding == "notional":
        assert "notional" in outcome.reason
    elif binding == "account":
        assert "account" in outcome.reason
    else:
        assert "provider" in outcome.reason or "runtime" in outcome.reason


def test_submit_and_reconcile_use_the_same_client(st, tmp_path):
    """Replacing the paper client cannot redirect the send or the absence proof."""
    from brokers.alpaca_paper import FakeAlpaca
    from brokers.authority_profiles import _broker_order_visibility

    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    original = broker._client
    other = FakeAlpaca()
    order_ref = str(scenario.order.id)

    def risk_recheck(_positions):
        broker._client = other
        return {"decision": "approved", "failed_checks": []}

    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    outcome = scenario.gateway.submit(
        scenario.order,
        **_paper_submit_kwargs(scenario, risk_recheck=risk_recheck),
    )
    assert outcome.ok is True
    assert other.submitted == []
    assert len(original.submitted) == 1
    assert _broker_order_visibility(
        scenario.gateway, order_ref, store=store,
    ) == "present"


def test_restarted_client_cannot_prove_the_order_absent(st, tmp_path):
    """A new process with an empty client cannot clear a stop from the same database."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    order_ref = str(scenario.order.id)
    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    scenario.gateway._profile_fence.bind_order(scenario.order)
    scenario.gateway._profile_fence._local.producing_runtime = "claude"
    scenario.gateway._profile_fence()
    broker._client.submit_order({"client_order_id": f"cockpit-{order_ref}"[:128]})
    scenario.clock._at = scenario.clock.now() + CLAIM_LEASE + timedelta(seconds=1)
    restarted = AuthorityProfileStore(store.path)
    empty = PaperOrderGateway(_CountingPaper(), st=st)
    assert _refuse(
        lambda: restarted._reconcile_handoff(
            "fence-1", order_ref=order_ref, now=scenario.clock.now(),
            actor="recovery-owner", gateway=empty, ladder=scenario.ladder,
        )
    ) == "broker_unknown"
    flags = _reservation_flags(store.path, "fence-1")
    assert (flags["handoff_state"], flags["reconciled"]) == ("in_progress", 0)
    assert _refuse(
        lambda: restarted.release_stop(
            "profile-b", actor="stop-owner", now=scenario.clock.now(), reconciled=True,
        )
    ) == "not_reconciled"


def test_concurrent_arm_cannot_redirect_the_submission(st, tmp_path):
    """A handoff that arms during a blocked fence cannot lend its generation."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    fence = scenario.gateway._profile_fence
    started = threading.Event()
    release = threading.Event()
    original = fence._order_ref

    def blocking():
        started.set()
        assert release.wait(3)
        return original()

    fence._order_ref = blocking
    result = {}

    def submit_stale():
        _arm(scenario.gateway, store, "fence-1", ticket.generation)
        try:
            result["outcome"] = scenario.gateway.submit(
                scenario.order, **_paper_submit_kwargs(scenario),
            )
        except Exception as exc:
            result["error"] = repr(exc)

    worker = threading.Thread(target=submit_stale)
    worker.start()
    try:
        assert started.wait(3)
        scenario.clock._at = scenario.clock.now() + CLAIM_LEASE + timedelta(seconds=1)
        store_b = AuthorityProfileStore(store.path)
        ticket_b = store_b._reconcile_handoff(
            "fence-1", order_ref=str(scenario.order.id), now=scenario.clock.now(),
            actor="recovery-owner", gateway=scenario.gateway, ladder=scenario.ladder,
        )
        assert isinstance(ticket_b, ClaimTicket)
        assert ticket_b.generation == ticket.generation + 1
        scenario.gateway.arm_profile_claim(store_b, "fence-1", ticket_b.generation)
        release.set()
        worker.join(5)
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert "error" not in result
    assert result["outcome"].ok is False
    assert _sends(broker) == 0


def test_permit_issue_waits_for_the_store_lock(st, tmp_path):
    """Permit-map publication cannot race a consume that holds the store lock."""
    scenario, store, ticket, _broker = _claimed_paper_scenario(st, tmp_path)
    started = threading.Event()
    finished = threading.Event()

    def issue():
        started.set()
        store._issue_transport_permit(
            "fence-1", ticket.generation, str(scenario.order.id),
        )
        finished.set()

    store._lock.acquire()
    worker = threading.Thread(target=issue)
    try:
        worker.start()
        assert started.wait(1)
        assert finished.wait(0.2) is False
    finally:
        store._lock.release()
    assert finished.wait(2)
    worker.join(2)
    assert not worker.is_alive()


def test_future_claimed_at_is_a_stale_claim(st, tmp_path):
    """A claim timestamp ahead of the store clock is not a live lease."""
    from brokers.authority_profiles import accept_transport_permit

    scenario, store, ticket, _broker = _claimed_paper_scenario(st, tmp_path)
    order_ref = str(scenario.order.id)
    permit = store._confirm_claim_before_broker_call(
        "fence-1", ticket.generation, now=scenario.clock.now(),
    )
    scenario.clock._at = scenario.clock.now() - timedelta(seconds=5)
    assert _refuse(
        lambda: accept_transport_permit(permit, order_ref=order_ref)
    ) == "stale_claim"
    assert permit.nonce in store._live_permits


def test_accept_then_timeout_completes_when_the_broker_has_the_order(st, tmp_path):
    """4356858340: broker presence completes an accept-then-timeout reservation."""
    broker = _CountingPaper()
    real_submit = broker._client.submit_order

    def ack_then_timeout(canonical):
        real_submit(canonical)
        raise TimeoutError("response lost after accept")

    broker._client.submit_order = ack_then_timeout
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(
        st, mode=Mode.paper, broker=broker, gateway=gateway, bind_approval=False,
    )
    assert scenario.order.approval_id is None
    store = _store(tmp_path, "present.sqlite")
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    notional = Decimal(str(scenario.order.notional))
    handoff_to_gateway(
        store, gateway, profile_id="profile-b", action="submit_paper",
        client_order_id="present-1", notional=notional, actor="stop-owner",
        now=now, submit_args=(scenario.order,),
        submit_kwargs=_paper_submit_kwargs(scenario), **_PAPER_BINDINGS,
    )
    flags = _reservation_flags(store.path, "present-1")
    assert (flags["handoff_state"], int(flags["handoff_done"]), int(flags["reconciled"])) == (
        "completed", 1, 1,
    )
    store.release_stop(
        "profile-b", actor="stop-owner", now=now, reconciled=True,
    )
    assert len(broker._client.submitted) == 1


def test_distinct_default_clocks_do_not_strand_a_claim(st, tmp_path, monkeypatch):
    """A default store at the state time arms. The claim is not left in progress."""
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    monkeypatch.setattr(Clock, "now", lambda self: scenario.clock.now())
    store = AuthorityProfileStore.__new__(AuthorityProfileStore)
    _REAL_STORE_INIT(store, tmp_path / "default-clocks.sqlite")
    assert type(store._clock) is Clock
    assert store._clock is not st.clock
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    result = handoff_to_gateway(
        store, gateway, profile_id="profile-b", action="submit_paper",
        client_order_id="clock-default", notional=Decimal(str(scenario.order.notional)),
        actor="stop-owner", now=now, submit_args=(scenario.order,),
        submit_kwargs=_paper_submit_kwargs(scenario), **_PAPER_BINDINGS,
    )
    assert getattr(result.outcome, "ok", None) is True
    assert _sends(broker) == 1
    row = _handoff_row(store.path, "clock-default")
    assert row["handoff_state"] == "completed"
    assert store._clock is st.clock


def test_version_four_store_migrates_the_transport_column(tmp_path):
    """4358444883: a populated version-4 store gains the transport column and opens."""
    import sqlite3

    from brokers.authority_profiles import STORE_USER_VERSION

    path = tmp_path / "v4.sqlite"
    store = AuthorityProfileStore(path)
    _install(store)
    store.authorize(
        "profile-b", action="propose", client_order_id="v4-row",
        notional=Decimal("10.00"), actor="stop-owner", now=NOW,
    )
    raw = sqlite3.connect(path)
    columns = [row[1] for row in raw.execute("PRAGMA table_info(reservations)")]
    for name in ("transport_client_hash", "transport_binding_hash"):
        if name in columns:
            raw.execute(f"ALTER TABLE reservations DROP COLUMN {name}")
    raw.execute("PRAGMA user_version = 4")
    raw.commit()
    raw.close()
    reopened = AuthorityProfileStore(path)
    raw = sqlite3.connect(path)
    version = raw.execute("PRAGMA user_version").fetchone()[0]
    names = {row[1] for row in raw.execute("PRAGMA table_info(reservations)")}
    kept = raw.execute(
        "SELECT COUNT(*) FROM reservations WHERE client_order_id = 'v4-row'"
    ).fetchone()[0]
    raw.close()
    assert version == STORE_USER_VERSION == 5
    assert "transport_client_hash" in names
    assert kept == 1
    assert reopened.consumption("profile-b") == (Decimal("10.00"), 1)


def test_restart_with_the_same_broker_key_can_reconcile(st, tmp_path):
    """4358445004: a restarted client with the same broker key reconciles; a different key does not."""
    from brokers.alpaca_paper import FakeAlpaca

    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    broker._client.key_id = "paper-key-1"
    broker._client._headers = {
        "APCA-API-KEY-ID": "paper-key-1",
        "APCA-API-SECRET-KEY": "paper-secret-1",
    }
    order_ref = str(scenario.order.id)
    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    scenario.gateway._profile_fence.bind_order(scenario.order)
    scenario.gateway._profile_fence._local.producing_runtime = "claude"
    scenario.gateway._profile_fence()
    cockpit_id = f"cockpit-{order_ref}"[:128]
    broker._client.submit_order({"client_order_id": cockpit_id})
    st.audit.intent(
        "order_submit",
        refs={
            "order_id": order_ref,
            "approval_id": scenario.approval.id,
            "risk_decision_id": scenario.risk_decision.id,
        },
        payload={"symbol": "AAPL", "side": "buy"},
    )
    scenario.clock._at = scenario.clock.now() + CLAIM_LEASE + timedelta(seconds=1)
    restarted = AuthorityProfileStore(store.path)
    same = FakeAlpaca()
    same.key_id = "paper-key-1"
    same._headers = {
        "APCA-API-KEY-ID": "paper-key-1",
        "APCA-API-SECRET-KEY": "paper-secret-1",
    }
    same._orders_by_client[cockpit_id] = {"broker_order_id": "alp-kept", "status": "accepted"}
    same_gateway = PaperOrderGateway(AlpacaPaperBroker(client=same), st=st)
    verdict = restarted._reconcile_handoff(
        "fence-1", order_ref=order_ref, now=scenario.clock.now(),
        actor="recovery-owner", gateway=same_gateway, ladder=scenario.ladder,
    )
    assert verdict == "completed"
    flags = _reservation_flags(store.path, "fence-1")
    assert (flags["handoff_state"], int(flags["reconciled"])) == ("completed", 1)
    restarted.release_stop(
        "profile-b", actor="stop-owner", now=scenario.clock.now(), reconciled=True,
    )


def test_restart_without_the_broker_key_stays_fail_closed(st, tmp_path):
    """A restarted client that cannot prove the broker key cannot clear the stop."""
    from brokers.alpaca_paper import FakeAlpaca

    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    broker._client.key_id = "paper-key-1"
    order_ref = str(scenario.order.id)
    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    scenario.gateway._profile_fence.bind_order(scenario.order)
    scenario.gateway._profile_fence._local.producing_runtime = "claude"
    scenario.gateway._profile_fence()
    scenario.clock._at = scenario.clock.now() + CLAIM_LEASE + timedelta(seconds=1)
    restarted = AuthorityProfileStore(store.path)
    other = FakeAlpaca()
    other.key_id = "other-key"
    other_gateway = PaperOrderGateway(AlpacaPaperBroker(client=other), st=st)
    assert _refuse(
        lambda: restarted._reconcile_handoff(
            "fence-1", order_ref=order_ref, now=scenario.clock.now(),
            actor="recovery-owner", gateway=other_gateway, ladder=scenario.ladder,
        )
    ) == "broker_unknown"
    assert _reservation_flags(store.path, "fence-1")["reconciled"] == 0
    assert _refuse(
        lambda: restarted.release_stop(
            "profile-b", actor="stop-owner", now=scenario.clock.now(), reconciled=True,
        )
    ) == "not_reconciled"


def test_admission_timeout_closes_only_its_own_intent(st, tmp_path):
    """4358445109: a lock timeout must not resolve an earlier order's open intent."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    prior = st.audit.intent(
        "order_submit",
        refs={"order_id": "earlier-order"},
        payload={"symbol": "MSFT", "side": "buy"},
    )
    lock = threading.Lock()
    lock.acquire()

    class _Held:
        def acquire(self, blocking=True, timeout=-1):
            if timeout is None or timeout < 0:
                return lock.acquire(blocking, timeout)
            return False

        def release(self):
            if lock.locked():
                lock.release()

        def __enter__(self):
            if not self.acquire():
                raise RuntimeError("held")
            return self

        def __exit__(self, *_args):
            self.release()
            return False

    scenario.st.safety_admission_lock = _Held()
    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert (outcome.ok, outcome.stage) == (False, "admission_lock")
    assert _sends(broker) == 0
    assert st.audit.unresolved_intents() == [prior.seq]
    lock.release()


def test_timed_lock_swap_stays_under_the_state_lock(st, tmp_path, monkeypatch):
    """4358445259: two paper submits must not leave the timed admission wrapper installed."""
    from brokers.paper_gateway import _TimedAdmissionLock

    scenario, store, ticket, _broker = _claimed_paper_scenario(st, tmp_path)
    original = scenario.st.safety_admission_lock
    other = PaperOrderGateway(_CountingPaper(), st=st)
    now = scenario.clock.now()
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="fence-2",
        notional=Decimal("10.00"), actor="stop-owner", now=now, order_ref="order-b",
    )
    ticket_b = store.claim_handoff("fence-2", now=now, awaiting_submit=True)
    assert isinstance(ticket_b, ClaimTicket)
    entered = []
    release = threading.Event()

    def blocking_submit(self, order, **kwargs):
        del self, order, kwargs
        entered.append(threading.get_ident())
        assert release.wait(3)
        return GateOutcome(False, "broker_error", "stopped before transport")

    monkeypatch.setattr(OrderGateway, "submit", blocking_submit)
    order_b = type("OrderB", (), {})()
    order_b.id = "order-b"
    order_b.account = "paper"
    order_b.symbol = "AAPL"
    order_b.side = "buy"
    order_b.qty = "1"
    order_b.order_type = "limit"
    order_b.limit_price = "10"
    order_b.tif = "day"
    order_b.notional = Decimal("10.00")
    errors = []
    outcomes = []

    def run(gateway, order, client_id, generation):
        try:
            _arm(gateway, store, client_id, generation)
            outcomes.append(gateway.submit(order, **_paper_submit_kwargs(scenario)))
        except Exception as exc:
            errors.append(repr(exc))

    first = threading.Thread(
        target=run, args=(scenario.gateway, scenario.order, "fence-1", ticket.generation),
    )
    second = threading.Thread(
        target=run, args=(other, order_b, "fence-2", ticket_b.generation),
    )
    first.start()
    deadline = threading.Event()
    try:
        for _ in range(50):
            if entered:
                break
            deadline.wait(0.05)
        assert entered, f"first submit did not reach the parent: {outcomes!r} {errors!r}"
        second.start()
        for _ in range(50):
            if len(entered) >= 2:
                break
            deadline.wait(0.05)
        assert len(entered) >= 2, f"second submit did not reach the parent: {outcomes!r} {errors!r}"
    finally:
        release.set()
        first.join(5)
        if second.ident is not None:
            second.join(5)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert not isinstance(scenario.st.safety_admission_lock, _TimedAdmissionLock)
    assert scenario.st.safety_admission_lock is original


def test_two_reservations_for_one_order_cannot_submit(st, tmp_path):
    """4357424253: a second live reservation for the same order cannot carry the send."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    fence = scenario.gateway._profile_fence
    started = threading.Event()
    release = threading.Event()
    original = fence._order_ref

    def blocking():
        started.set()
        assert release.wait(3)
        return original()

    fence._order_ref = blocking
    result = {}

    def submit_stale():
        _arm(scenario.gateway, store, "fence-1", ticket.generation)
        try:
            result["outcome"] = scenario.gateway.submit(
                scenario.order, **_paper_submit_kwargs(scenario),
            )
        except Exception as exc:
            result["error"] = repr(exc)

    worker = threading.Thread(target=submit_stale)
    worker.start()
    try:
        assert started.wait(3)
        now = scenario.clock.now()
        store.authorize(
            "profile-b", action="submit_paper", client_order_id="fence-2",
            notional=Decimal(str(scenario.order.notional)), actor="stop-owner",
            now=now, order_ref=str(scenario.order.id),
        )
        with pytest.raises(ProfileRefused) as caught:
            store.claim_handoff("fence-2", now=now, awaiting_submit=True)
        assert caught.value.code == "client_order_conflict"
        release.set()
        worker.join(5)
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert "error" not in result
    assert result["outcome"].ok is True
    assert _sends(broker) == 1
    assert _reservation_flags(store.path, "fence-2")["handoff_state"] == "pending"


def _order_state(st, order_id):
    from schemas.models.order import Order

    with st.session_factory() as session:
        row = session.get(Order, order_id)
        return None if row is None else row.state


@pytest.mark.parametrize(
    "change",
    ["revoke", "kill", "hold", "halt", "watch_only", "stale_feed", "lease", "approval"],
)
def test_final_risk_recheck_cannot_admit_a_revoked_or_unsafe_send(st, tmp_path, change):
    """A revoke, stop, HALT, mode, stale feed, lease, or approval expiry inside the last risk callback."""
    from orchestrator.halt import default_halt_path, set_halt
    from schemas.models.risk_decision import RiskDecision

    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    opened = scenario.clock.now()
    if change == "approval":
        scenario.gateway._ttl_override = 90
        with st.session_factory() as session, session.begin():
            row = session.get(RiskDecision, scenario.risk_decision.id)
            row.evaluated_at = opened
    calls = {"n": 0}

    def risk_recheck(_positions):
        calls["n"] += 1
        if calls["n"] < 2:
            return {"decision": "approved", "failed_checks": []}
        if change == "revoke":
            store.revoke(
                "profile-b", actor="stop-owner", now=scenario.clock.now(),
                reason="revoke during final risk recheck",
            )
        elif change == "kill":
            store.kill(
                "profile-b", actor="stop-owner", now=scenario.clock.now(),
                reason="kill during final risk recheck",
            )
        elif change == "hold":
            store.hold(
                "profile-b", actor="stop-owner", now=scenario.clock.now(),
                reason="hold during final risk recheck",
            )
        elif change == "halt":
            set_halt(default_halt_path(), reason="HALT during final risk recheck")
        elif change == "watch_only":
            scenario.breaker.enter_watch_only("claude", "watch-only during final risk recheck")
        elif change == "stale_feed":
            class Guard:
                def stale_reason(self, symbol, *, now, limit_s):
                    del symbol, now, limit_s
                    return "market quote is stale"

            scenario.st.market_data = Guard()
        else:
            scenario.clock._at = opened + timedelta(seconds=91)
        return {"decision": "approved", "failed_checks": []}

    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario, risk_recheck=risk_recheck),
    )
    assert calls["n"] >= 2
    assert outcome.ok is False
    assert _sends(broker) == 0


def test_reserved_notional_is_qty_times_limit_price(st, tmp_path):
    """A detached $1 notional must not reserve or send a 100 x $100 order under a $40 cap."""
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(
        st, mode=Mode.paper, broker=broker, gateway=gateway,
        qty="100", limit_price="100", notional="1.00",
    )
    scenario.order.notional = Decimal("1.00")
    store = _store(tmp_path, "tuple-notional.sqlite")
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="40.00", max_order_notional="40.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    code = _refuse(
        lambda: handoff_to_gateway(
            store, gateway, profile_id="profile-b", action="submit_paper",
            client_order_id="tuple-1", notional=Decimal("1.00"), actor="stop-owner",
            now=now, submit_args=(scenario.order,),
            submit_kwargs=_paper_submit_kwargs(scenario), **_PAPER_BINDINGS,
        )
    )
    assert code in {"notional_mismatch", "order_notional"}
    assert _sends(broker) == 0
    assert _handoff_row(store.path, "tuple-1") is None


def test_live_claim_uses_tuple_notional_not_the_detached_attribute(st, tmp_path):
    """Matching the detached notional to a $1 reservation still does not send a larger tuple."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    scenario.order.notional = Decimal("1.00")
    with store._txn() as conn:
        conn.execute(
            "UPDATE reservations SET notional = ? WHERE client_order_id = ?",
            ("1.00", "fence-1"),
        )
    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    outcome = scenario.gateway.submit(scenario.order, **_paper_submit_kwargs(scenario))
    assert outcome.ok is False
    assert "notional" in outcome.reason
    assert _sends(broker) == 0


def test_copied_key_id_cannot_authenticate_recovery(st, tmp_path):
    """A copied key id, without the secret, cannot prove absence or fabricate presence."""
    from brokers.alpaca_paper import FakeAlpaca

    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    broker._client.key_id = "paper-key-1"
    broker._client._headers = {
        "APCA-API-KEY-ID": "paper-key-1",
        "APCA-API-SECRET-KEY": "paper-secret-1",
    }
    order_ref = str(scenario.order.id)
    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    scenario.gateway._profile_fence.bind_order(scenario.order)
    scenario.gateway._profile_fence._local.producing_runtime = "claude"
    scenario.gateway._profile_fence()
    cockpit_id = f"cockpit-{order_ref}"[:128]
    broker._client.submit_order({"client_order_id": cockpit_id})
    scenario.clock._at = scenario.clock.now() + CLAIM_LEASE + timedelta(seconds=1)
    restarted = AuthorityProfileStore(store.path)
    copied = FakeAlpaca()
    copied.key_id = "paper-key-1"
    copied._headers = {"APCA-API-KEY-ID": "paper-key-1"}
    copied_gateway = PaperOrderGateway(AlpacaPaperBroker(client=copied), st=st)
    assert _refuse(
        lambda: restarted._reconcile_handoff(
            "fence-1", order_ref=order_ref, now=scenario.clock.now(),
            actor="recovery-owner", gateway=copied_gateway, ladder=scenario.ladder,
        )
    ) == "broker_unknown"
    assert _reservation_flags(store.path, "fence-1")["reconciled"] == 0
    assert _refuse(
        lambda: restarted.release_stop(
            "profile-b", actor="stop-owner", now=scenario.clock.now(), reconciled=True,
        )
    ) == "not_reconciled"
    fabricated = FakeAlpaca()
    fabricated.key_id = "paper-key-1"
    fabricated._headers = {"APCA-API-KEY-ID": "paper-key-1"}
    fabricated._orders_by_client[cockpit_id] = {
        "broker_order_id": "alp-forged", "status": "accepted",
    }
    forged_gateway = PaperOrderGateway(AlpacaPaperBroker(client=fabricated), st=st)
    assert _refuse(
        lambda: restarted._reconcile_handoff(
            "fence-1", order_ref=order_ref, now=scenario.clock.now(),
            actor="recovery-owner", gateway=forged_gateway, ladder=scenario.ladder,
        )
    ) == "broker_unknown"
    assert _reservation_flags(store.path, "fence-1")["handoff_state"] == "in_progress"


def test_migrated_inflight_row_without_a_binding_stays_unknown(st, tmp_path):
    """A version-4 in-flight row with no binding hash is not absent for an empty client."""
    import sqlite3

    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    order_ref = str(scenario.order.id)
    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    scenario.gateway._profile_fence.bind_order(scenario.order)
    scenario.gateway._profile_fence._local.producing_runtime = "claude"
    scenario.gateway._profile_fence()
    broker._client.submit_order({"client_order_id": f"cockpit-{order_ref}"[:128]})
    store.kill(
        "profile-b", actor="stop-owner", now=scenario.clock.now(), reason="operator kill",
    )
    scenario.clock._at = scenario.clock.now() + CLAIM_LEASE + timedelta(seconds=1)
    raw = sqlite3.connect(store.path)
    columns = [row[1] for row in raw.execute("PRAGMA table_info(reservations)")]
    for name in ("transport_client_hash", "transport_binding_hash"):
        if name in columns:
            raw.execute(f"ALTER TABLE reservations DROP COLUMN {name}")
    raw.execute("PRAGMA user_version = 4")
    raw.commit()
    raw.close()
    restarted = AuthorityProfileStore(store.path)
    empty = PaperOrderGateway(_CountingPaper(), st=st)
    assert _refuse(
        lambda: restarted._reconcile_handoff(
            "fence-1", order_ref=order_ref, now=scenario.clock.now(),
            actor="recovery-owner", gateway=empty, ladder=scenario.ladder,
        )
    ) == "broker_unknown"
    assert _reservation_flags(store.path, "fence-1")["reconciled"] == 0
    assert _refuse(
        lambda: restarted.release_stop(
            "profile-b", actor="stop-owner", now=scenario.clock.now(), reconciled=True,
        )
    ) == "not_reconciled"
    from brokers.alpaca_paper import FakeAlpaca
    from brokers.authority_profiles import bind_startup_transport

    cockpit_id = f"cockpit-{order_ref}"[:128]
    restored = FakeAlpaca()
    restored.key_id = "paper-key-1"
    restored._headers = {
        "APCA-API-KEY-ID": "paper-key-1",
        "APCA-API-SECRET-KEY": "paper-secret-1",
    }
    restored._orders_by_client[cockpit_id] = dict(broker._client._orders_by_client[cockpit_id])
    stranger = PaperOrderGateway(AlpacaPaperBroker(client=restored), st=st)
    assert _refuse(
        lambda: restarted._reconcile_handoff(
            "fence-1", order_ref=order_ref, now=scenario.clock.now(),
            actor="recovery-owner", gateway=stranger, ladder=scenario.ladder,
        )
    ) == "broker_unknown"
    bind_startup_transport(restarted, restored)
    st.audit.intent(
        "order_submit",
        refs={
            "order_id": order_ref,
            "approval_id": scenario.approval.id,
            "risk_decision_id": scenario.risk_decision.id,
        },
        payload={"symbol": "AAPL", "side": "buy"},
    )
    assert restarted._reconcile_handoff(
        "fence-1", order_ref=order_ref, now=scenario.clock.now(),
        actor="recovery-owner", gateway=stranger, ladder=scenario.ladder,
    ) == "completed"


def test_gateway_clock_cannot_revive_an_expired_profile(st, tmp_path, monkeypatch):
    """The clock is fixed at composition. An older gateway clock cannot outvote an expired store."""
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    times = {"now": scenario.clock.now()}
    monkeypatch.setattr(Clock, "now", lambda self: times["now"])
    store = AuthorityProfileStore.__new__(AuthorityProfileStore)
    _REAL_STORE_INIT(store, tmp_path / "composed-clock.sqlite")
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=times["now"],
    )
    times["now"] = scenario.clock.now() + timedelta(days=2)
    code = _refuse(
        lambda: handoff_to_gateway(
            store, gateway, profile_id="profile-b", action="submit_paper",
            client_order_id="expired-clock", notional=Decimal(str(scenario.order.notional)),
            actor="stop-owner", now=times["now"], submit_args=(scenario.order,),
            submit_kwargs=_paper_submit_kwargs(scenario), **_PAPER_BINDINGS,
        )
    )
    assert code == "expired"
    assert _sends(broker) == 0
    row = _handoff_row(store.path, "expired-clock")
    assert row is None or row["handoff_state"] != "in_progress"


def test_admission_timeout_leaves_a_concurrent_intent_open(st, tmp_path):
    """A lock timeout closes this submission's intent and no other intent opened during the wait."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    prior = st.audit.intent(
        "order_submit",
        refs={"order_id": "earlier-order"},
        payload={"symbol": "MSFT", "side": "buy"},
    )
    concurrent = {}
    lock = threading.Lock()
    lock.acquire()

    class _Held:
        def acquire(self, blocking=True, timeout=-1):
            if timeout is None or timeout < 0:
                return lock.acquire(blocking, timeout)
            concurrent["entry"] = st.audit.intent(
                "order_submit",
                refs={"order_id": "concurrent-other"},
                payload={"symbol": "IBM", "side": "buy"},
            )
            return False

        def release(self):
            if lock.locked():
                lock.release()

        def __enter__(self):
            if not self.acquire():
                raise RuntimeError("held")
            return self

        def __exit__(self, *_args):
            self.release()
            return False

    scenario.st.safety_admission_lock = _Held()
    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert (outcome.ok, outcome.stage) == (False, "admission_lock")
    assert _sends(broker) == 0
    open_seqs = st.audit.unresolved_intents()
    assert prior.seq in open_seqs
    assert concurrent["entry"].seq in open_seqs
    order_seqs = [
        row.seq for row in st.audit._rows()
        if (row.refs or {}).get("order_id") == scenario.order.id
        and (row.payload or {}).get("_phase") == "intent"
    ]
    assert order_seqs
    assert order_seqs[0] not in open_seqs
    lock.release()


def test_callback_cannot_redirect_request_credentials_or_endpoint(st, tmp_path):
    """Replacing the captured client's request, headers, or endpoint does not redirect the send."""
    from brokers.alpaca_paper import PAPER_BASE_URL

    seen = []

    class _Http:
        def __init__(self):
            self._headers = {
                "APCA-API-KEY-ID": "key-1",
                "APCA-API-SECRET-KEY": "secret-1",
            }
            self._endpoint = PAPER_BASE_URL
            self.submitted = []

        def _request(self, method, base_url, path, *, json=None, params=None):
            del method, path, params
            seen.append((base_url, dict(self._headers)))
            return {
                "id": "alp-frozen",
                "status": "accepted",
                "filled_qty": "0",
                "filled_avg_price": None,
                "client_order_id": None if json is None else json.get("client_order_id"),
            }

        def submit_order(self, canonical):
            body = self._request("POST", self._endpoint, "/v2/orders", json=canonical)
            ack = {
                "broker_order_id": body["id"],
                "status": "accepted",
                "filled_qty": Decimal("0"),
                "avg_fill_price": None,
            }
            self.submitted.append(ack)
            return ack

    client = _Http()
    broker = AlpacaPaperBroker(client=client)
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    store = _store(tmp_path, "frozen-http.sqlite")
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="frozen-1",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner", now=now,
        order_ref=str(scenario.order.id),
    )
    ticket = store.claim_handoff("frozen-1", now=now, awaiting_submit=True)
    assert isinstance(ticket, ClaimTicket)

    def substitute(method, base_url, path, *, json=None, params=None):
        del method, path, params, json
        seen.append(("substitute", base_url, dict(client._headers)))
        return {
            "id": "evil",
            "status": "accepted",
            "filled_qty": "0",
            "filled_avg_price": None,
        }

    def risk_recheck(_positions):
        client._request = substitute
        client._headers["APCA-API-SECRET-KEY"] = "replaced-secret"
        client._headers["APCA-API-KEY-ID"] = "replaced-key"
        client._endpoint = "https://evil.example"
        return {"decision": "approved", "failed_checks": []}

    _arm(gateway, store, "frozen-1", ticket.generation)
    outcome = gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario, risk_recheck=risk_recheck),
    )
    assert outcome.ok is True
    assert not any(item[0] == "substitute" for item in seen)
    assert seen
    assert all(
        item[0] == PAPER_BASE_URL and item[1]["APCA-API-SECRET-KEY"] == "secret-1"
        for item in seen
    )
    assert _order_state(st, scenario.order.id) == "submitted"


def test_presence_recovery_resolves_intent_and_order_before_complete(st, tmp_path):
    """Broker presence closes this order's submit intent and moves it to submitted first."""
    broker = _CountingPaper()
    real_submit = broker._client.submit_order

    def ack_then_timeout(canonical):
        real_submit(canonical)
        raise TimeoutError("response lost after accept")

    broker._client.submit_order = ack_then_timeout
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(
        st, mode=Mode.paper, broker=broker, gateway=gateway, bind_approval=False,
    )
    assert scenario.order.approval_id is None
    real_result = st.audit.result

    def skip_abort(intent_entry, op, refs=None, payload=None):
        if isinstance(payload, dict) and payload.get("aborted"):
            return intent_entry
        return real_result(intent_entry, op, refs=refs, payload=payload)

    st.audit.result = skip_abort
    store = _store(tmp_path, "present-intent.sqlite")
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    handoff_to_gateway(
        store, gateway, profile_id="profile-b", action="submit_paper",
        client_order_id="present-intent", notional=Decimal(str(scenario.order.notional)),
        actor="stop-owner", now=now, submit_args=(scenario.order,),
        submit_kwargs=_paper_submit_kwargs(scenario), **_PAPER_BINDINGS,
    )
    flags = _reservation_flags(store.path, "present-intent")
    assert (flags["handoff_state"], int(flags["reconciled"])) == ("completed", 1)
    assert st.audit.unresolved_intents() == []
    assert _order_state(st, scenario.order.id) == "submitted"


def test_second_claim_does_not_take_a_live_order(st, tmp_path):
    """One reservation owns an order. The loser stays pending or failed, not in progress."""
    scenario, store, _ticket, _broker = _claimed_paper_scenario(st, tmp_path)
    now = scenario.clock.now()
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="fence-2",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner", now=now,
        order_ref=str(scenario.order.id),
    )
    code = _refuse(
        lambda: store.claim_handoff("fence-2", now=now, awaiting_submit=True)
    )
    assert code == "client_order_conflict"
    flags = _reservation_flags(store.path, "fence-2")
    assert flags["handoff_state"] in {"pending", "failed"}
    winner = _reservation_flags(store.path, "fence-1")
    assert winner["handoff_state"] == "in_progress"


def test_failed_arm_restores_reconciled(st, tmp_path):
    """An arm refusal puts the prior reconciled flag back on the reservation."""
    from brokers.authority_profiles import _arm_or_release

    scenario, store, ticket, _broker = _claimed_paper_scenario(st, tmp_path)
    store.fail_handoff("fence-1", generation=ticket.generation)
    with store._txn() as conn:
        conn.execute(
            "UPDATE reservations SET reconciled = 1 WHERE client_order_id = ?",
            ("fence-1",),
        )
    retry = store.claim_handoff("fence-1", now=scenario.clock.now(), awaiting_submit=True)
    assert isinstance(retry, ClaimTicket)
    assert int(_reservation_flags(store.path, "fence-1")["reconciled"]) == 0
    store._clock = FrozenClock(scenario.clock.now())
    assert _refuse(
        lambda: _arm_or_release(scenario.gateway, store, "fence-1", retry)
    ) == "clock"
    flags = _reservation_flags(store.path, "fence-1")
    assert flags["handoff_state"] == "failed"
    assert int(flags["reconciled"]) == 1


def test_httpx_request_hook_cannot_redirect_the_secret(st, tmp_path, monkeypatch):
    """A request hook added during risk_recheck must not carry the secret off the paper host."""
    import httpx

    from brokers.alpaca_paper import PAPER_BASE_URL, AlpacaPaperHTTPClient

    caller_seen = []
    seen = []

    def handler(request):
        caller_seen.append((str(request.url), request.headers.get("APCA-API-SECRET-KEY")))
        return _http_accept("alp-caller")

    def owned_handle(self, request):
        del self
        seen.append((str(request.url), request.headers.get("APCA-API-SECRET-KEY")))
        return _http_accept("alp-owned")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", owned_handle)
    http = httpx.Client(transport=httpx.MockTransport(handler), timeout=2.0)
    client = AlpacaPaperHTTPClient("key-1", "secret-1", http_client=http)
    broker = AlpacaPaperBroker(client=client)
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    store = _store(tmp_path, "owned-http.sqlite")
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="owned-1",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner", now=now,
        order_ref=str(scenario.order.id),
    )
    ticket = store.claim_handoff("owned-1", now=now, awaiting_submit=True)
    assert isinstance(ticket, ClaimTicket)

    def risk_recheck(_positions):
        def rewrite(request):
            request.url = httpx.URL("https://substitute.invalid/v2/orders")

        client._client.event_hooks["request"].append(rewrite)
        return {"decision": "approved", "failed_checks": []}

    _arm(gateway, store, "owned-1", ticket.generation)
    try:
        outcome = gateway.submit(
            scenario.order, **_paper_submit_kwargs(scenario, risk_recheck=risk_recheck),
        )
    finally:
        http.close()
    assert outcome.ok is True
    assert caller_seen == []
    assert seen
    assert not any("substitute.invalid" in url for url, _secret in seen)
    assert all(
        url.startswith(PAPER_BASE_URL) and secret == "secret-1" for url, secret in seen
    )
    assert b"secret-1" not in Path(store.path).read_bytes()


def test_presence_recovery_consumes_the_approval(st, tmp_path):
    """Accept-then-timeout recovery burns the approval, so a fresh capability cannot resend."""
    from brokers.order_state_machine import create_order
    from schemas.models.approval_record import ApprovalRecord
    from schemas.models.order import Order

    broker = _CountingPaper()
    real_submit = broker._client.submit_order

    def ack_then_timeout(canonical):
        real_submit(canonical)
        raise TimeoutError("response lost after accept")

    broker._client.submit_order = ack_then_timeout
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(
        st, mode=Mode.paper, broker=broker, gateway=gateway, bind_approval=False,
    )
    assert scenario.order.approval_id is None
    store = _store(tmp_path, "consume-approval.sqlite")
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    notional = Decimal(str(scenario.order.notional))
    handoff_to_gateway(
        store, gateway, profile_id="profile-b", action="submit_paper",
        client_order_id="consume-1", notional=notional, actor="stop-owner",
        now=now, submit_args=(scenario.order,),
        submit_kwargs=_paper_submit_kwargs(scenario), **_PAPER_BINDINGS,
    )
    with st.session_factory() as session:
        approval = session.get(ApprovalRecord, scenario.approval.id)
        consumed = None if approval is None else bool(approval.single_use_consumed)
        landed = session.get(Order, scenario.order.id)
        landed_approval = None if landed is None else landed.approval_id
    assert consumed is True
    assert landed_approval == scenario.approval.id
    assert gateway._consumed_on_chain(st, scenario.approval.id) is True
    replay = create_order(
        st, proposal_id=scenario.order.proposal_id,
        risk_decision_id=scenario.risk_decision.id, approval_id=scenario.approval.id,
        account="paper", symbol="AAPL", side="buy", qty=scenario.order.qty,
        order_type="limit", limit_price=scenario.order.limit_price, tif="day",
        notional=notional, order_tuple_hash=scenario.tuple_hash, actor="claude",
    )
    gateway.mint_submit_token(
        nonce="n-replay", proposal_id=replay.proposal_id,
        risk_decision_id=scenario.risk_decision.id, order_tuple_hash=scenario.tuple_hash,
    )
    second = handoff_to_gateway(
        store, gateway, profile_id="profile-b", action="submit_paper",
        client_order_id="consume-2", notional=notional, actor="stop-owner",
        now=now, submit_args=(replay,),
        submit_kwargs=_paper_submit_kwargs(scenario, token_nonce="n-replay"),
        **_PAPER_BINDINGS,
    )
    assert getattr(second.outcome, "ok", None) is not True
    assert getattr(second.outcome, "stage", None) == "approval"
    assert _sends(broker) == 1


def test_tuple_exposure_refuses_a_ten_thousand_dollar_order_signed_at_one_dollar(st, tmp_path):
    """A $10,000 limit signed at notional $1 must not pass a $40 exposure cap."""
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st, pending_exposure_cap=Decimal("40"))
    scenario = build_scenario(
        st, mode=Mode.paper, broker=broker, gateway=gateway,
        qty="100", limit_price="100", notional="1.00",
    )
    store = _store(tmp_path, "exposure-paper.sqlite")
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="10000.00", max_order_notional="10000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    result = handoff_to_gateway(
        store, gateway, profile_id="profile-b", action="submit_paper",
        client_order_id="exposure-1", notional=Decimal("10000.00"), actor="stop-owner",
        now=now, submit_args=(scenario.order,),
        submit_kwargs=_paper_submit_kwargs(scenario), **_PAPER_BINDINGS,
    )
    assert getattr(result.outcome, "ok", None) is False
    assert getattr(result.outcome, "stage", None) == "pending_exposure"
    assert _sends(broker) == 0


def test_build_paper_execution_refuses_understated_notional_without_gateway_edits(st):
    """The paper composition at main.py must apply qty x limit_price on this tree."""
    import ast

    from brokers.capability import TokenRegistry
    from brokers.paper_gateway import PaperExecutionGateway

    source = (APP / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "build_paper_execution"
    )
    constructed = [
        node.func.id for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "OrderGateway" not in constructed
    assert "PaperExecutionGateway" in constructed
    broker = _CountingPaper()
    gateway = PaperExecutionGateway(
        broker, st=st, token_registry=TokenRegistry(),
        pending_exposure_cap=Decimal("40"),
        approval_ttl_seconds=900,
        execution_scope="paper",
        require_analysis_generation=True,
    )
    scenario = build_scenario(
        st, mode=Mode.paper, broker=broker, gateway=gateway,
        qty="100", limit_price="100", notional="1.00",
    )
    outcome = gateway.submit(scenario.order, **_paper_submit_kwargs(scenario))
    assert outcome.ok is False
    assert outcome.stage == "pending_exposure"
    assert _sends(broker) == 0


def test_admission_timeout_closes_only_the_submission_intent_token(st, tmp_path):
    """A same-order intent opened by a callback stays open. Only this submission closes."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    prior = st.audit.intent(
        "order_submit",
        refs={"order_id": "earlier-order"},
        payload={"symbol": "MSFT", "side": "buy"},
    )
    opened = {}

    def risk_recheck(_positions):
        opened["callback"] = st.audit.intent(
            "order_submit",
            refs={"order_id": scenario.order.id},
            payload={"symbol": "AAPL", "side": "buy"},
        )
        return {"decision": "approved", "failed_checks": []}

    class _Held:
        def acquire(self, blocking=True, timeout=-1):
            if timeout is None or timeout < 0:
                return False
            opened["same_order"] = st.audit.intent(
                "order_submit",
                refs={
                    "order_id": scenario.order.id,
                    "proposal_id": scenario.order.proposal_id,
                },
                payload={"symbol": "AAPL", "side": "buy"},
            )
            return False

        def release(self):
            return None

        def __enter__(self):
            if not self.acquire():
                raise RuntimeError("held")
            return self

        def __exit__(self, *_args):
            self.release()
            return False

    scenario.st.safety_admission_lock = _Held()
    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario, risk_recheck=risk_recheck),
    )
    assert (outcome.ok, outcome.stage) == (False, "admission_lock")
    assert _sends(broker) == 0
    open_seqs = set(st.audit.unresolved_intents())
    assert prior.seq in open_seqs
    assert opened["callback"].seq in open_seqs
    assert opened["same_order"].seq in open_seqs
    submission = [
        row.seq for row in st.audit._rows()
        if (row.payload or {}).get("_phase") == "intent"
        and (row.payload or {}).get("_op") == "order_submit"
        and (row.refs or {}).get("approval_id") == scenario.approval.id
    ]
    assert len(submission) == 1
    assert submission[0] not in open_seqs


class _TouchQuote:
    """A trusted quote slot. ``stale`` set means the observation is not fresh."""

    def __init__(self, bid, ask, *, stale=None):
        self.max_age_s = 120.0
        self._bid = bid
        self._ask = ask
        self._stale = stale

    def quote(self, symbol):
        return {"symbol": symbol, "bid": self._bid, "ask": self._ask}

    def stale_reason(self, symbol, *, now, limit_s):
        del symbol, now, limit_s
        return self._stale


def _market_buy(st, broker, gateway, *, notional="1.00"):
    return build_scenario(
        st, mode=Mode.paper, broker=broker, gateway=gateway,
        qty="10", order_type="market", limit_price=None, notional=notional,
    )


def test_market_order_within_the_cap_sends_from_the_fresh_ask(st, tmp_path):
    """4359331640: qty x fresh ask x buffer sends. The detached notional is not the size."""
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st, pending_exposure_cap=Decimal("40"))
    scenario = _market_buy(st, broker, gateway)
    st.market_data = _TouchQuote("1.40", "1.50")
    store = _store(tmp_path, "market-send.sqlite")
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    # 10 x 1.50 ask x 1.05 buffer. order.notional stays 1.00.
    reserved = Decimal("15.75")
    result = handoff_to_gateway(
        store, gateway, profile_id="profile-b", action="submit_paper",
        client_order_id="market-send", notional=reserved, actor="stop-owner",
        now=now, submit_args=(scenario.order,),
        submit_kwargs=_paper_submit_kwargs(scenario), **_PAPER_BINDINGS,
    )
    assert Decimal(str(scenario.order.notional)) == Decimal("1.00")
    assert getattr(result.outcome, "ok", None) is True
    assert _sends(broker) == 1


def test_understated_market_reservation_refuses(st, tmp_path):
    """A reservation at the detached notional, or at the raw touch, is not the buffered size."""
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = _market_buy(st, broker, gateway)
    st.market_data = _TouchQuote("1.40", "1.50")
    store = _store(tmp_path, "market-under.sqlite")
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    with pytest.raises(ProfileRefused) as caught:
        handoff_to_gateway(
            store, gateway, profile_id="profile-b", action="submit_paper",
            client_order_id="market-under", notional=Decimal("1.00"), actor="stop-owner",
            now=now, submit_args=(scenario.order,),
            submit_kwargs=_paper_submit_kwargs(scenario), **_PAPER_BINDINGS,
        )
    assert caught.value.code == "notional_mismatch"
    assert "limit price" not in str(caught.value).lower()
    assert _sends(broker) == 0
    assert _handoff_row(store.path, "market-under") is None


def test_stale_market_quote_refuses(st, tmp_path):
    """No fresh touch: a market handoff refuses before a send."""
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = _market_buy(st, broker, gateway)
    st.market_data = _TouchQuote("1.40", "1.50", stale="market quote for AAPL is stale")
    store = _store(tmp_path, "market-stale.sqlite")
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    code = _refuse(
        lambda: handoff_to_gateway(
            store, gateway, profile_id="profile-b", action="submit_paper",
            client_order_id="market-stale", notional=Decimal("15.75"), actor="stop-owner",
            now=now, submit_args=(scenario.order,),
            submit_kwargs=_paper_submit_kwargs(scenario), **_PAPER_BINDINGS,
        )
    )
    assert code == "stale_quote"
    assert _sends(broker) == 0


def test_market_exposure_over_the_cap_refuses_despite_a_one_dollar_notional(st, tmp_path):
    """The gateway cap sees qty x buffered ask, not order.notional."""
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st, pending_exposure_cap=Decimal("40"))
    scenario = _market_buy(st, broker, gateway)
    st.market_data = _TouchQuote("9.90", "10.00")
    store = _store(tmp_path, "market-cap.sqlite")
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    result = handoff_to_gateway(
        store, gateway, profile_id="profile-b", action="submit_paper",
        client_order_id="market-cap", notional=Decimal("105.00"), actor="stop-owner",
        now=now, submit_args=(scenario.order,),
        submit_kwargs=_paper_submit_kwargs(scenario), **_PAPER_BINDINGS,
    )
    assert Decimal(str(scenario.order.notional)) == Decimal("1.00")
    assert getattr(result.outcome, "ok", None) is False
    assert getattr(result.outcome, "stage", None) == "pending_exposure"
    assert _sends(broker) == 0


def test_default_store_arms_on_the_state_clock(st, tmp_path, monkeypatch):
    """4359331775: a store built without a clock can arm when that does not rewind time."""
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    monkeypatch.setattr(Clock, "now", lambda self: scenario.clock.now())
    store = AuthorityProfileStore.__new__(AuthorityProfileStore)
    _REAL_STORE_INIT(store, tmp_path / "default-arm.sqlite")
    assert type(store._clock) is Clock
    assert store._clock is not st.clock
    assert store._clock_default is True
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    result = handoff_to_gateway(
        store, gateway, profile_id="profile-b", action="submit_paper",
        client_order_id="default-arm", notional=Decimal(str(scenario.order.notional)),
        actor="stop-owner", now=now, submit_args=(scenario.order,),
        submit_kwargs=_paper_submit_kwargs(scenario), **_PAPER_BINDINGS,
    )
    assert getattr(result.outcome, "ok", None) is True
    assert _sends(broker) == 1
    assert store._clock is st.clock


def test_explicit_distinct_clock_still_refuses_without_stranding(st, tmp_path, monkeypatch):
    """An explicit clock that is not the state clock is not replaced and does not stay claimed."""
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    monkeypatch.setattr(Clock, "now", lambda self: scenario.clock.now())
    explicit = FrozenClock(scenario.clock.now())
    store = AuthorityProfileStore.__new__(AuthorityProfileStore)
    _REAL_STORE_INIT(store, tmp_path / "explicit-clock.sqlite", clock=explicit)
    assert store._clock is explicit
    assert store._clock_default is False
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    call = dict(
        profile_id="profile-b", action="submit_paper", client_order_id="explicit-clock",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner", now=now,
        submit_args=(scenario.order,), submit_kwargs=_paper_submit_kwargs(scenario),
        **_PAPER_BINDINGS,
    )
    assert _refuse(lambda: handoff_to_gateway(store, gateway, **call)) == "clock"
    assert _sends(broker) == 0
    row = _handoff_row(store.path, "explicit-clock")
    assert row is None or row["handoff_state"] != "in_progress"
    assert store._clock is explicit
    assert _refuse(lambda: handoff_to_gateway(store, gateway, **call)) == "clock"


def test_recovery_requires_a_signed_submit_intent(st, tmp_path):
    """4359332076: broker presence alone must not adopt the order."""
    from brokers.alpaca_paper import FakeAlpaca

    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    broker._client.key_id = "paper-key-1"
    broker._client._headers = {
        "APCA-API-KEY-ID": "paper-key-1",
        "APCA-API-SECRET-KEY": "paper-secret-1",
    }
    order_ref = str(scenario.order.id)
    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    scenario.gateway._profile_fence.bind_order(scenario.order)
    scenario.gateway._profile_fence._local.producing_runtime = "claude"
    scenario.gateway._profile_fence()
    cockpit_id = f"cockpit-{order_ref}"[:128]
    broker._client.submit_order({"client_order_id": cockpit_id})
    scenario.clock._at = scenario.clock.now() + CLAIM_LEASE + timedelta(seconds=1)
    restarted = AuthorityProfileStore(store.path)
    same = FakeAlpaca()
    same.key_id = "paper-key-1"
    same._headers = {
        "APCA-API-KEY-ID": "paper-key-1",
        "APCA-API-SECRET-KEY": "paper-secret-1",
    }
    same._orders_by_client[cockpit_id] = {"broker_order_id": "alp-kept", "status": "accepted"}
    same_gateway = PaperOrderGateway(AlpacaPaperBroker(client=same), st=st)
    code = _refuse(
        lambda: restarted._reconcile_handoff(
            "fence-1", order_ref=order_ref, now=scenario.clock.now(),
            actor="recovery-owner", gateway=same_gateway, ladder=scenario.ladder,
        )
    )
    assert code == "intent"
    assert _order_state(st, order_ref) != "submitted"
    flags = _reservation_flags(store.path, "fence-1")
    assert flags["handoff_state"] != "completed"
    assert int(flags["reconciled"]) == 0


def test_damaged_version_four_store_is_not_rebuilt(tmp_path):
    """4359332370: a version-4 store missing a ledger table fails closed."""
    import sqlite3

    path = tmp_path / "damaged-v4.sqlite"
    store = AuthorityProfileStore(path)
    _install(store)
    store.authorize(
        "profile-b", action="propose", client_order_id="v4-damaged",
        notional=Decimal("10.00"), actor="stop-owner", now=NOW,
    )
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE reservations")
    raw.execute("PRAGMA user_version = 4")
    raw.commit()
    raw.close()
    with pytest.raises(ProfileRefused) as caught:
        AuthorityProfileStore(path)
    assert caught.value.code == "store"
    raw = sqlite3.connect(path)
    version = raw.execute("PRAGMA user_version").fetchone()[0]
    names = {
        row[0]
        for row in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    raw.close()
    assert version == 4
    assert "reservations" not in names


def test_owned_transport_is_not_the_caller_object(st, tmp_path, monkeypatch):
    """Mutating the caller's transport after init cannot redirect the secret."""
    import httpx

    from brokers.alpaca_paper import PAPER_BASE_URL, AlpacaPaperHTTPClient

    seen = []

    def handler(request):
        seen.append(("caller", str(request.url), request.headers.get("APCA-API-SECRET-KEY")))
        return _http_accept("alp-caller-transport")

    def owned_handle(self, request):
        del self
        seen.append(("owned", str(request.url), request.headers.get("APCA-API-SECRET-KEY")))
        return _http_accept("alp-owned-transport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", owned_handle)
    http = httpx.Client(transport=httpx.MockTransport(handler), timeout=2.0)
    client = AlpacaPaperHTTPClient("key-1", "secret-1", http_client=http)
    broker = AlpacaPaperBroker(client=client)
    gateway = PaperOrderGateway(broker, st=st)
    owned_transport = gateway._owned_http._transport
    assert owned_transport is not http._transport
    assert getattr(owned_transport, "_inner", None) is not http._transport

    def evil(request):
        seen.append(("evil", str(request.url), request.headers.get("APCA-API-SECRET-KEY")))
        return httpx.Response(
            201,
            json={
                "id": "alp-evil",
                "status": "accepted",
                "filled_qty": "0",
                "filled_avg_price": None,
                "client_order_id": None,
            },
        )

    http._transport.handler = evil
    scenario = build_scenario(st, mode=Mode.paper, broker=broker, gateway=gateway)
    store = _store(tmp_path, "owned-transport.sqlite")
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="owned-transport",
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner", now=now,
        order_ref=str(scenario.order.id),
    )
    ticket = store.claim_handoff("owned-transport", now=now, awaiting_submit=True)
    _arm(gateway, store, "owned-transport", ticket.generation)
    try:
        outcome = gateway.submit(scenario.order, **_paper_submit_kwargs(scenario))
    finally:
        http.close()
    assert outcome.ok is True
    assert seen
    assert not any(kind == "evil" for kind, _url, _secret in seen)
    assert all(
        kind == "owned" and url.startswith(PAPER_BASE_URL) and secret == "secret-1"
        for kind, url, secret in seen
    )
    assert b"secret-1" not in Path(store.path).read_bytes()


def test_submission_token_does_not_depend_on_the_gateway_submit_frame(st):
    """The timeout closer stamps this submission's intent without reading gateway.py."""
    from brokers.paper_gateway import _arm_submission_token, _close_submission_intent

    scenario = build_scenario(st, mode=Mode.paper)
    order = scenario.order
    callback = st.audit.intent(
        "order_submit",
        refs={"order_id": order.id},
        payload={"symbol": "AAPL", "side": "buy"},
    )
    _arm_submission_token(st, "submission-token", order)
    parent = st.audit.intent(
        "order_submit",
        refs={
            "order_id": order.id,
            "proposal_id": order.proposal_id,
            "risk_decision_id": order.risk_decision_id,
            "approval_id": order.approval_id,
            "order_tuple_hash": order.order_tuple_hash,
        },
        payload={"symbol": order.symbol, "side": order.side, "qty": str(order.qty)},
    )
    _close_submission_intent(st, order, set(), "admission lock", "submission-token")
    open_seqs = set(st.audit.unresolved_intents())
    assert callback.seq in open_seqs
    assert parent.seq not in open_seqs


def _http_accept(order_id="alp-owned"):
    import httpx

    return httpx.Response(
        201,
        json={
            "id": order_id,
            "status": "accepted",
            "filled_qty": "0",
            "filled_avg_price": None,
            "client_order_id": None,
        },
    )


def _armed_http_submit(st, tmp_path, client, gateway, name):
    scenario = build_scenario(st, mode=Mode.paper, broker=gateway._broker, gateway=gateway)
    store = _store(tmp_path, f"{name}.sqlite")
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    store.authorize(
        "profile-b", action="submit_paper", client_order_id=name,
        notional=Decimal(str(scenario.order.notional)), actor="stop-owner", now=now,
        order_ref=str(scenario.order.id),
    )
    ticket = store.claim_handoff(name, now=now, awaiting_submit=True)
    _arm(gateway, store, name, ticket.generation)
    return scenario, store


def test_caller_transport_handler_never_sees_the_secret(st, tmp_path, monkeypatch):
    """4359992101: an owned session must not call the caller's transport handler."""
    import httpx

    from brokers.alpaca_paper import PAPER_BASE_URL, AlpacaPaperHTTPClient

    caller_seen = []

    def handler(request):
        caller_seen.append(request.headers.get("APCA-API-SECRET-KEY"))
        return _http_accept("alp-caller-handler")

    def owned_handle(self, request):
        del self
        assert str(request.url).startswith(PAPER_BASE_URL)
        return _http_accept("alp-owned-pool")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", owned_handle)
    http = httpx.Client(transport=httpx.MockTransport(handler), timeout=2.0)
    client = AlpacaPaperHTTPClient("key-1", "secret-1", http_client=http)
    broker = AlpacaPaperBroker(client=client)
    gateway = PaperOrderGateway(broker, st=st)
    scenario, store = _armed_http_submit(st, tmp_path, client, gateway, "handler-secret")
    try:
        outcome = gateway.submit(scenario.order, **_paper_submit_kwargs(scenario))
    finally:
        http.close()
    assert caller_seen == []
    assert outcome.ok is True
    assert b"secret-1" not in Path(store.path).read_bytes()


def test_owned_transport_failure_does_not_use_the_caller(st, tmp_path, monkeypatch):
    """4359992101: a failed owned transport refuses. It does not fall back to the caller."""
    import httpx

    from brokers.alpaca_paper import AlpacaPaperHTTPClient

    caller_seen = []

    def handler(request):
        caller_seen.append(request.headers.get("APCA-API-SECRET-KEY"))
        return _http_accept("alp-fallback")

    http = httpx.Client(transport=httpx.MockTransport(handler), timeout=2.0)

    def boom(self, *args, **kwargs):
        del self, args, kwargs
        raise RuntimeError("owned pool unavailable")

    monkeypatch.setattr(httpx.HTTPTransport, "__init__", boom)
    client = AlpacaPaperHTTPClient("key-1", "secret-1", http_client=http)
    broker = AlpacaPaperBroker(client=client)
    gateway = PaperOrderGateway(broker, st=st)
    scenario, store = _armed_http_submit(st, tmp_path, client, gateway, "transport-fail")
    try:
        outcome = gateway.submit(scenario.order, **_paper_submit_kwargs(scenario))
    finally:
        http.close()
    assert outcome.ok is False
    assert "transport" in outcome.reason
    assert caller_seen == []
    assert b"secret-1" not in Path(store.path).read_bytes()


def test_market_buy_submits_a_protective_limit_at_the_buffered_ask(st, tmp_path):
    """The 5% buffer is also the fill cap. A market buy is sent as that limit."""
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st, pending_exposure_cap=Decimal("40"))
    scenario = _market_buy(st, broker, gateway)
    birth_hash = scenario.order.order_tuple_hash
    st.market_data = _TouchQuote("1.40", "1.50")
    store = _store(tmp_path, "market-collar.sqlite")
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    result = handoff_to_gateway(
        store, gateway, profile_id="profile-b", action="submit_paper",
        client_order_id="market-collar", notional=Decimal("15.75"), actor="stop-owner",
        now=now, submit_args=(scenario.order,),
        submit_kwargs=_paper_submit_kwargs(scenario), **_PAPER_BINDINGS,
    )
    assert getattr(result.outcome, "ok", None) is True
    assert _sends(broker) == 1
    sent = broker._client.submitted[-1]
    assert sent["order_type"] == "limit"
    assert Decimal(str(sent["limit_price"])) == Decimal("1.57")
    assert scenario.order.order_type == "market"
    assert scenario.order.limit_price is None
    assert scenario.order.order_tuple_hash == birth_hash
    assert Decimal(str(scenario.order.notional)) == Decimal("1.00")


def test_market_sell_submits_a_protective_limit_at_the_buffered_bid(st, tmp_path):
    """A market sell is sent as a limit at the buffered bid, not as an uncapped market."""
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st, pending_exposure_cap=Decimal("40"))
    scenario = build_scenario(
        st, mode=Mode.paper, broker=broker, gateway=gateway, side="sell",
        qty="10", order_type="market", limit_price=None, notional="1.00",
    )
    st.market_data = _TouchQuote("1.40", "1.50")
    store = _store(tmp_path, "market-sell-collar.sqlite")
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    result = handoff_to_gateway(
        store, gateway, profile_id="profile-b", action="submit_paper",
        client_order_id="market-sell-collar", notional=Decimal("14.70"), actor="stop-owner",
        now=now, submit_args=(scenario.order,),
        submit_kwargs=_paper_submit_kwargs(scenario), **_PAPER_BINDINGS,
    )
    assert getattr(result.outcome, "ok", None) is True
    sent = broker._client.submitted[-1]
    assert sent["order_type"] == "limit"
    assert sent["side"] == "sell"
    assert Decimal(str(sent["limit_price"])) == Decimal("1.47")
    assert scenario.order.order_type == "market"


def test_quote_change_between_reserve_and_send_refuses(st, tmp_path):
    """A quote that moves after the reservation does not send at the old exposure."""
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st, pending_exposure_cap=Decimal("40"))
    scenario = _market_buy(st, broker, gateway)
    quote = _TouchQuote("1.40", "1.50")
    st.market_data = quote
    store = _store(tmp_path, "market-quote-move.sqlite")
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )

    def risk_recheck(_positions):
        quote._ask = "2.00"
        return {"decision": "approved", "failed_checks": []}

    result = handoff_to_gateway(
        store, gateway, profile_id="profile-b", action="submit_paper",
        client_order_id="market-quote-move", notional=Decimal("15.75"), actor="stop-owner",
        now=now, submit_args=(scenario.order,),
        submit_kwargs=_paper_submit_kwargs(scenario, risk_recheck=risk_recheck),
        **_PAPER_BINDINGS,
    )
    assert _sends(broker) == 0
    assert getattr(result.outcome, "ok", None) is False
    assert "notional" in str(getattr(result.outcome, "reason", "")).lower()
    assert scenario.order.order_type == "market"
    assert broker._client.submitted == []


def test_recovery_ignores_an_unsigned_order_row_approval(st, tmp_path):
    """4360006561: a row approval planted after birth is not the approval recovery burns."""
    from core.ids import new_id
    from orchestrator.state import EVENT_ADJUDICATION
    from schemas.models.approval_record import ApprovalRecord
    from schemas.models.order import Order

    broker = _CountingPaper()
    real_submit = broker._client.submit_order

    def ack_then_timeout(canonical):
        real_submit(canonical)
        raise TimeoutError("response lost after accept")

    broker._client.submit_order = ack_then_timeout
    gateway = PaperOrderGateway(broker, st=st)
    scenario = build_scenario(
        st, mode=Mode.paper, broker=broker, gateway=gateway, bind_approval=False,
    )
    assert scenario.order.approval_id is None
    other = ApprovalRecord(
        id=new_id("appr"), proposal_id=scenario.order.proposal_id,
        risk_decision_id=scenario.risk_decision.id, actor_user_id="lucas",
        user_decision="approve", surface="discord",
        order_tuple_hash=scenario.tuple_hash, single_use_consumed=False,
        decided_at=scenario.clock.now(), created_at=scenario.clock.now(),
    )
    st.audit.record_state_change(
        other, EVENT_ADJUDICATION, actor="lucas",
        payload={
            "decision": "approve", "proposal_id": other.proposal_id,
            "risk_decision_id": other.risk_decision_id,
            "order_tuple_hash": other.order_tuple_hash,
        },
    )
    with st.session_factory() as session:
        row = session.get(Order, scenario.order.id)
        row.approval_id = other.id
        session.commit()
    store = _store(tmp_path, "row-approval.sqlite")
    now = scenario.clock.now()
    _install(
        store,
        _document(
            account_id="paper", notional_cap="5000.00", max_order_notional="5000.00",
            expires_at="2026-07-08T14:00:00+00:00",
        ),
        now=now,
    )
    with pytest.raises(ProfileRefused):
        handoff_to_gateway(
            store, gateway, profile_id="profile-b", action="submit_paper",
            client_order_id="row-approval", notional=Decimal(str(scenario.order.notional)),
            actor="stop-owner", now=now, submit_args=(scenario.order,),
            submit_kwargs=_paper_submit_kwargs(scenario), **_PAPER_BINDINGS,
        )
    with st.session_factory() as session:
        planted = session.get(ApprovalRecord, other.id)
        signed = session.get(ApprovalRecord, scenario.approval.id)
        planted_consumed = None if planted is None else bool(planted.single_use_consumed)
        signed_consumed = None if signed is None else bool(signed.single_use_consumed)
    assert planted_consumed is False
    assert signed_consumed is False
    assert _order_state(st, scenario.order.id) != "submitted"
    flags = _reservation_flags(store.path, "row-approval")
    assert flags["handoff_state"] != "completed"


def test_null_order_approval_still_stamps_the_submission_intent(st, tmp_path):
    """Production births leave approval_id null. The timeout still closes that submit intent."""
    scenario, store, ticket, broker = _claimed_paper_scenario(
        st, tmp_path, bind_approval=False,
    )
    assert scenario.order.approval_id is None

    class _Held:
        def acquire(self, blocking=True, timeout=-1):
            del blocking
            if timeout is None or timeout < 0:
                return False
            return False

        def release(self):
            return None

        def __enter__(self):
            if not self.acquire():
                raise RuntimeError("held")
            return self

        def __exit__(self, *_args):
            self.release()
            return False

    scenario.st.safety_admission_lock = _Held()
    _arm(scenario.gateway, store, "fence-1", ticket.generation)
    outcome = scenario.gateway.submit(
        scenario.order, **_paper_submit_kwargs(scenario),
    )
    assert (outcome.ok, outcome.stage) == (False, "admission_lock")
    assert _sends(broker) == 0
    open_seqs = set(st.audit.unresolved_intents())
    submission = [
        row.seq for row in st.audit._rows()
        if (row.payload or {}).get("_phase") == "intent"
        and (row.payload or {}).get("_op") == "order_submit"
        and (row.refs or {}).get("approval_id") == scenario.approval.id
    ]
    assert len(submission) == 1
    assert submission[0] not in open_seqs

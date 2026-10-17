"""CSL-218 negative controls for paper and dry-run authority profiles.

Live, expired, revoked, model-granted, split, raced, restart, and replay
cases must fail closed. A profile reservation is not broker authority.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from brokers.alpaca_paper import AlpacaPaperBroker
from brokers.authority_profiles import (
    ADMITTED_ENVIRONMENTS,
    FUTURE_PROFILE_CLASS,
    NAMED_REFUSED_ENVIRONMENTS,
    RUNTIME_PROFILE_CLASSES,
    AuthorityProfileStore,
    ClaimTicket,
    ProfileRefused,
    handoff_to_gateway,
    load_profile,
    load_profile_file,
    model_output_grants_authority,
    reconcile_handoff_to_gateway,
)
from brokers.gateway import OrderGateway, PaperOrderGateway
from config.settings import Mode
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


def _store(tmp_path, name="profiles.sqlite"):
    return AuthorityProfileStore(tmp_path / name)


_CAPS: dict[tuple[str, str], object] = {}


def _install(store, document=None, *, now=NOW):
    document = document or _document()
    profile, capability = store.install(document, actor=document["stop_owner"], now=now)
    _CAPS[(str(store.path), profile.profile_id)] = capability
    return profile


def _refuse(fn) -> str:
    with pytest.raises(ProfileRefused) as caught:
        fn()
    return caught.value.code


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


def _handoff_row(path, client_order_id):
    import sqlite3

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT handoff_state, handoff_done, kill FROM reservations "
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
    assert _refuse(
        lambda: store.authorize(
            "profile-b", action="propose", client_order_id="c-expired",
            notional=Decimal("10.00"), actor="stop-owner", now=later,
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
    kwargs = dict(
        profile_id="profile-b", action="submit_paper", client_order_id="c-model",
        notional=Decimal("10.00"), actor="stop-owner", now=later,
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
        submit_kwargs=scenario.submit_kwargs(), model_output=_VALID_MODEL,
        **_PAPER_BINDINGS,
    )
    assert refused.reached_gateway is True
    assert refused.outcome.ok is False
    assert broker.calls == 0
    failed = _handoff_row(tmp_path / "profiles.sqlite", "gw-refused")
    assert (failed["handoff_state"], failed["handoff_done"]) == ("failed", 0)
    replay = handoff_to_gateway(
        store, gateway, profile_id="profile-paper", action="submit_paper",
        client_order_id="gw-refused", notional=Decimal(str(scenario.order.notional)),
        actor="stop-owner", now=now, submit_args=(scenario.order,),
        submit_kwargs=scenario.submit_kwargs(), model_output=_VALID_MODEL,
        **_PAPER_BINDINGS,
    )
    assert replay.reached_gateway is True
    assert replay.outcome.ok is False
    assert broker.calls == 0


def test_live_transport_and_dry_run_do_not_reach_a_broker(st, tmp_path):
    broker = _ProbeBroker()
    live = OrderGateway(broker, st=st, execution_scope="live")
    store = _store(tmp_path)
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
    assert broker.calls == 0
    dry = _store(tmp_path, "dry.sqlite")
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
    store = _store(tmp_path)
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
    assert forged.execution_scope == "paper"
    assert _refuse(
        lambda: handoff_to_gateway(
            store, forged, profile_id="profile-b", action="submit_paper",
            client_order_id="probe-1", notional=Decimal("10.00"),
            actor="stop-owner", now=NOW, **_PAPER_BINDINGS,
        )
    ) == "transport"
    assert probe.calls == 0
    pinned = PaperOrderGateway(_CountingPaper(), st=st)
    pinned._broker = probe
    assert _refuse(
        lambda: handoff_to_gateway(
            store, pinned, profile_id="profile-b", action="submit_paper",
            client_order_id="swapped-1", notional=Decimal("10.00"),
            actor="stop-owner", now=NOW, **_PAPER_BINDINGS,
        )
    ) == "binding_adapter"
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
    assert expiring.claim_handoff("c-expired", now=later, awaiting_submit=True) == "expired"
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
    assert gateway.execution_scope == "paper"
    assert type(gateway) is OrderGateway
    store = _store(tmp_path)
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
    assert broker.calls == 0
    assert store.consumption("profile-b") == (Decimal("0.00"), 0)


def test_failed_submit_keeps_the_client_order_reusable(st, tmp_path):
    broker = _CountingPaper()
    gateway = PaperOrderGateway(broker, st=st)
    calls = {"n": 0}
    real_submit = gateway.submit

    def _counting(order, **kwargs):
        calls["n"] += 1
        return real_submit(order, **kwargs)

    gateway.submit = _counting
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
        submit_args=(scenario.order,), submit_kwargs=scenario.submit_kwargs(),
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
    assert broker.calls == 0
    assert store.consumption("profile-b") == (Decimal(str(scenario.order.notional)), 1)


def test_owned_in_progress_claim_is_refused_until_reconciled(st, tmp_path):
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
    real_submit = gateway.submit

    def _flaky(order, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("crash before submit returned")
        return real_submit(order, **kwargs)

    gateway.submit = _flaky
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
        submit_args=(scenario.order,), submit_kwargs=scenario.submit_kwargs(),
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
    assert broker.calls == 0
    # The reconcile path sees no 'submitted' transition on the signed chain,
    # re-owns the claim under a new generation, and submits exactly once.
    reconciled = reconcile_handoff_to_gateway(
        store, gateway, client_order_id="crash-1",
        submit_args=(scenario.order,), submit_kwargs=scenario.submit_kwargs(),
        now=now,
    )
    assert calls["n"] == 2
    assert broker.calls == 1
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
    outcome = gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert outcome.ok is True
    assert broker.calls == 1
    # Crash between submit returning and complete_handoff: still in_progress.
    result = reconcile_handoff_to_gateway(
        store, gateway, client_order_id="landed-1",
        submit_args=(scenario.order,), submit_kwargs=scenario.submit_kwargs(),
        now=now,
    )
    assert result.reached_gateway is False
    assert broker.calls == 1
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
    outcome = gateway.submit(scenario.order, **scenario.submit_kwargs())
    assert outcome.ok is True
    store.complete_handoff("stale-1", generation=ticket.generation)
    # A stale owner's write after completion is refused; the row is kept.
    assert _refuse(
        lambda: store.fail_handoff("stale-1", generation=ticket.generation)
    ) == "stale_claim"
    row = _handoff_row(path, "stale-1")
    assert (row["handoff_state"], row["handoff_done"]) == ("completed", 1)


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
            submit_args=(scenario.order,), submit_kwargs=scenario.submit_kwargs(),
            now=now,
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
    # kill is active so the dead claim cannot be re-owned: it resolves as
    # failed+reconciled, which consumes only its own slice and unblocks release.
    assert _refuse(lambda: reconcile("rel-inprog")) == "failed"
    store.release_stop("profile-b", actor="stop-owner", now=now, reconciled=True)

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

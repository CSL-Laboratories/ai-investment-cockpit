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
    assert broker.calls == 0
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
    assert gateway._execution_scope == "paper"
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
    assert broker.calls == 0
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
    assert broker.calls == 0
    # The claim lease has not expired yet: a reconcile this soon after the
    # crash is refused outright, even though the chain shows nothing landed.
    assert _refuse(
        lambda: reconcile_handoff_to_gateway(
            store, gateway, client_order_id="crash-1",
            submit_args=(scenario.order,), submit_kwargs=_paper_submit_kwargs(scenario),
            now=now,
        )
    ) == "claim_leased"
    assert calls["n"] == 1
    assert broker.calls == 0
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
        now=later,
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
    outcome = gateway.submit(scenario.order, **_paper_submit_kwargs(scenario))
    assert outcome.ok is True
    assert broker.calls == 1
    # Crash between submit returning and complete_handoff: still in_progress.
    result = reconcile_handoff_to_gateway(
        store, gateway, client_order_id="landed-1",
        submit_args=(scenario.order,), submit_kwargs=_paper_submit_kwargs(scenario),
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


@pytest.mark.parametrize(
    "recovery",
    [
        pytest.param(False, id="fresh"),
        pytest.param(True, id="expired-claim", marks=_NEEDS_GATEWAY_FENCE),
    ],
)
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
        result = reconcile_handoff_to_gateway(
            store, gateway, client_order_id="override-1", submit_args=(scenario.order,),
            submit_kwargs=kwargs, now=now + CLAIM_LEASE + timedelta(seconds=1),
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
    assert broker.calls == 0
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
    assert broker.calls == 0
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
    if evidence in {"other-order", "invalid-signature"}:
        assert gateway.submit(scenario.order, **_paper_submit_kwargs(scenario)).ok is True
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
        def unavailable(*_args):
            raise RuntimeError("signed chain unavailable")

        monkeypatch.setattr(OrderGateway, "_order_submitted_on_chain", unavailable)
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

    assert _refuse(complete) == "gateway_identity"
    row = _handoff_row(store.path, "completion-1")
    assert (row["handoff_state"], row["handoff_done"]) == ("in_progress", 0)
    # Completion refusal must not turn a future handoff into a completed replay.
    assert _refuse(
        lambda: store.claim_handoff("completion-1", now=now, awaiting_submit=True)
    ) == "handoff_in_progress"
    assert broker.calls == (1 if evidence in {"other-order", "invalid-signature"} else 0)


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


def test_stopped_proposal_completes_after_reconcile_and_release(tmp_path):
    store = _store(tmp_path, "proposal.sqlite")
    _install(store)
    store.authorize(
        "profile-b", action="propose", client_order_id="prop-1",
        notional=Decimal("10.00"), actor="stop-owner", now=NOW,
    )
    store.hold("profile-b", actor="stop-owner", now=NOW, reason="operator hold")
    assert store.reconcile_non_gateway_handoff("prop-1", now=NOW) == "failed"
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
    assert gateway.submit(scenario.order, **_paper_submit_kwargs(scenario)).ok is True
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
        assert broker.calls == 1

    kwargs = _paper_submit_kwargs(scenario)
    reconcile_paths = {
        "public": lambda: reconcile_handoff_to_gateway(
            store, gateway, client_order_id="forge-1", submit_args=(scenario.order,),
            submit_kwargs=kwargs, now=now,
        ),
        # Omitting the caller's order_ref must still check the stored identity.
        "private": lambda: store._reconcile_handoff(
            "forge-1", order_ref=None, gateway=gateway, ladder=kwargs["ladder"], now=now,
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
        lambda: store.reconcile_non_gateway_handoff("forge-1", now=now)
    ) == "store"
    assert_unresolved()
    for supplied_ref in (None, order_ref):
        assert _refuse(
            lambda: store._reconcile_handoff("forge-1", order_ref=supplied_ref, now=now)
        ) == "gateway_identity"
        assert_unresolved()
    assert _refuse(
        lambda: store._reconcile_handoff(
            "forge-1", order_ref=order_ref, gateway=gateway, now=now,
        )
    ) == "gateway_identity"
    assert_unresolved()
    if state == "failed":
        assert store.reconcile_failed_handoff("forge-1") == "failed"
        assert_unresolved()

    def unavailable_chain(*_args):
        raise RuntimeError("signed chain unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(OrderGateway, "_order_submitted_on_chain", unavailable_chain)
        for reconcile in reconcile_paths.values():
            assert _refuse(reconcile) == "gateway_identity"
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
    assert broker.calls == 1
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
            "unbound-1", order_ref="someone-elses-order", now=NOW
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
            now=now,
        )
    ) == "account_mismatch"
    assert broker.calls == 0
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
            now=now,
        )
    ) == "notional_mismatch"
    assert broker.calls == 0


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
            now=now_,
        )

    # The lease has expired: reconciler A re-owns the claim (generation 2) and
    # crashes again before its own submit returns.
    later = now + CLAIM_LEASE + timedelta(seconds=1)
    with pytest.raises(RuntimeError):
        reconcile(later)
    assert calls["n"] == 1
    assert broker.calls == 0
    row = _handoff_row(path, "lease-1")
    assert (row["handoff_state"], row["handoff_done"]) == ("in_progress", 0)

    # Reconciler B races in immediately after, at the same instant: the claim
    # it would be taking over is fresh (claimed moments ago by A), so the
    # lease refuses it before it can ever reach the gateway.
    assert _refuse(lambda: reconcile(later)) == "claim_leased"
    assert calls["n"] == 1
    assert broker.calls == 0

    # Only once A's own re-own lease has also expired does a third attempt
    # succeed -- and it sends to the gateway exactly once.
    even_later = later + CLAIM_LEASE + timedelta(seconds=1)
    result = reconcile(even_later)
    assert calls["n"] == 2
    assert broker.calls == 1
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
    ticket = store.claim_handoff("mono-1", now=claim_time, awaiting_submit=True)
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
    with pytest.raises(ProfileRefused) as exc:
        store._confirm_claim_before_broker_call("mono-1", ticket.generation, now=NOW)
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

    fired = ap._fence_now(object(), {"clock": _Clock()})()
    assert fired == NOW + timedelta(seconds=80)
    store._confirm_claim_before_broker_call("clock-1", ticket.generation, now=fired)
    assert _handoff_row(store.path, "clock-1")["claimed_at"] == fired.isoformat()
    assert _refuse(
        lambda: ap._fence_now(object(), {})()
    ) == "clock"


def _claimed_paper_scenario(st, tmp_path):
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
        store._confirm_claim_before_broker_call(
            "fence-1", ticket.generation, now=scenario.clock.now(),
        )
        set_halt(default_halt_path(), reason="HALT during claim fence")
        fenced.append(True)

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **_paper_submit_kwargs(scenario),
    )
    assert fenced  # the fence also re-runs right before transport
    assert (outcome.ok, outcome.stage) == (False, "halt")
    assert broker.calls == 0
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
    fence_time = scenario.clock.now() + elapsed

    def fence():
        store._confirm_claim_before_broker_call("fence-1", ticket.generation, now=fence_time)

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **_paper_submit_kwargs(scenario),
    )
    assert (outcome.ok, outcome.stage) == (False, "claim_fence")
    assert "stale" in outcome.reason
    assert dict(_handoff_row(store.path, "fence-1")) == before
    assert broker.calls == 0
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
            gateway=scenario.gateway, ladder=scenario.ladder,
        )
        assert isinstance(ticket_b, ClaimTicket)
        assert ticket_b.generation == ticket_a.generation + 1
        tickets_b.append(ticket_b)
        store_a._confirm_claim_before_broker_call(
            "fence-1", ticket_a.generation, now=scenario.clock.now(),
        )

    outcome_a = scenario.gateway.submit(
        scenario.order, claim_fence=delayed_owner_a_fence, **_paper_submit_kwargs(scenario),
    )
    assert (outcome_a.ok, outcome_a.stage) == (False, "claim_fence")
    assert broker.calls == 0
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
    outcome_b = scenario.gateway.submit(
        scenario.order,
        claim_fence=lambda: store_b._confirm_claim_before_broker_call(
            "fence-1", tickets_b[0].generation, now=scenario.clock.now(),
        ),
        **_paper_submit_kwargs(scenario, token_nonce="takeover-b"),
    )
    assert outcome_b.ok is True
    store_b.complete_handoff(
        "fence-1", generation=tickets_b[0].generation,
        gateway=scenario.gateway, ladder=scenario.ladder,
    )
    assert broker.calls == 1
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
                gateway=scenario.gateway, ladder=scenario.ladder,
            )
        store_a._confirm_claim_before_broker_call(
            "fence-1", ticket_a.generation, now=scenario.clock.now(),
        )

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **_paper_submit_kwargs(scenario),
    )
    assert len(calls) == 2
    assert (outcome.ok, outcome.stage) == (False, "claim_fence")
    assert broker.calls == 0
    assert st.audit.unresolved_intents() == []
    assert st.audit.verify_chain().ok


@pytest.mark.parametrize("failure", ["raises", "naive", "not-datetime", "missing"])
def test_claim_fence_clock_failure_refuses_without_writing_lease(st, tmp_path, failure):
    """5558807159: a bad current clock cannot persist the old handoff timestamp."""
    from brokers import authority_profiles as ap

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
        # Use the same composition as both production handoff entry points.
        # An unavailable state clock must also refuse, without a fallback.
        gateway = object() if failure == "missing" else scenario.gateway
        clock_kwargs = {} if failure == "missing" else {"clock": BrokenClock()}
        store._confirm_claim_before_broker_call(
            "fence-1", ticket.generation, now=ap._fence_now(gateway, clock_kwargs)(),
        )

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **_paper_submit_kwargs(scenario),
    )
    assert (outcome.ok, outcome.stage) == (False, "claim_fence")
    assert "clock" in outcome.reason
    assert dict(_handoff_row(store.path, "fence-1")) == before
    assert broker.calls == 0
    assert st.audit.unresolved_intents() == []
    assert st.audit.verify_chain().ok


@pytest.mark.parametrize("recovery", [False, True], ids=["handoff", "reconcile"])
@_NEEDS_GATEWAY_FENCE
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
    broken = []

    class FenceClock:
        def now(self):
            if broken:
                raise RuntimeError("clock failed after the final audit read")
            return scenario.clock.now()

    real_audit_check = gateway._final_audit_authority

    def audit_check(st_arg):
        result = real_audit_check(st_arg)
        broken.append(True)
        return result

    monkeypatch.setattr(gateway, "_final_audit_authority", audit_check)
    kwargs = _paper_submit_kwargs(scenario, clock=FenceClock())
    notional = Decimal(str(scenario.order.notional))
    if recovery:
        store.authorize(
            "profile-b", action="submit_paper", client_order_id="clock-failure-1",
            notional=notional, actor="stop-owner", now=now, order_ref=str(scenario.order.id),
        )
        store.claim_handoff("clock-failure-1", now=now, awaiting_submit=True)
        scenario.clock._at = now + CLAIM_LEASE + timedelta(seconds=1)
        result = reconcile_handoff_to_gateway(
            store, gateway, client_order_id="clock-failure-1",
            submit_args=(scenario.order,), submit_kwargs=kwargs, now=scenario.clock.now(),
        )
    else:
        result = handoff_to_gateway(
            store, gateway, profile_id="profile-b", action="submit_paper",
            client_order_id="clock-failure-1", notional=notional, actor="stop-owner", now=now,
            submit_args=(scenario.order,), submit_kwargs=kwargs, **_PAPER_BINDINGS,
        )
    assert broken == [True]
    assert (result.outcome.ok, result.outcome.stage) == (False, "claim_fence")
    assert "clock" in result.outcome.reason
    assert broker.calls == 0
    row = _handoff_row(store.path, "clock-failure-1")
    assert row["claimed_at"] == scenario.clock.now().isoformat()
    assert (row["handoff_state"], row["handoff_done"]) == ("failed", 0)
    assert st.audit.unresolved_intents() == []
    assert st.audit.verify_chain().ok


@pytest.mark.parametrize("mutation", ["paper-broker", "other-adapter", "broker-type", "scope"])
@_NEEDS_GATEWAY_FENCE
def test_claim_fence_cannot_swap_transport_or_paper_scope(st, tmp_path, mutation):
    """4303485166: callback mutations never reach either the old or new transport."""
    scenario, store, ticket, broker = _claimed_paper_scenario(st, tmp_path)
    swapped = _ProbeBroker() if mutation == "other-adapter" else _CountingPaper()

    class OtherPaper(_CountingPaper):
        pass

    def fence():
        store._confirm_claim_before_broker_call(
            "fence-1", ticket.generation, now=scenario.clock.now(),
        )
        if mutation == "scope":
            scenario.gateway._execution_scope = "live"
        elif mutation == "broker-type":
            broker.__class__ = OtherPaper
        else:
            scenario.gateway._broker = swapped

    outcome = scenario.gateway.submit(
        scenario.order, claim_fence=fence, **_paper_submit_kwargs(scenario),
    )
    assert (outcome.ok, outcome.stage) == (False, "transport_scope")
    assert broker.calls == swapped.calls == 0
    assert st.audit.unresolved_intents() == []
    assert st.audit.verify_chain().ok

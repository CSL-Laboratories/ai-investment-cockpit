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

from brokers.authority_profiles import (
    ADMITTED_ENVIRONMENTS,
    FUTURE_PROFILE_CLASS,
    NAMED_REFUSED_ENVIRONMENTS,
    RUNTIME_PROFILE_CLASSES,
    AuthorityProfileStore,
    ProfileRefused,
    handoff_to_gateway,
    load_profile,
    load_profile_file,
    model_output_grants_authority,
)
from brokers.gateway import OrderGateway
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


def _install(store, document=None, *, now=NOW):
    return store.install(document or _document(), actor="stop-owner", now=now)


def _refuse(fn) -> str:
    with pytest.raises(ProfileRefused) as caught:
        fn()
    return caught.value.code


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
    boosted = _refuse(lambda: store.authorize(**kwargs, model_output=GRANT))
    assert plain == boosted == "expired"
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


def test_legacy_store_without_order_ref_migrates_and_binds_once(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.sqlite"
    store = AuthorityProfileStore(path)
    _install(store)
    store.authorize(
        "profile-b", action="submit_paper", client_order_id="client-1",
        notional=Decimal("25.00"), actor="stop-owner", now=NOW,
    )
    raw = sqlite3.connect(path)
    raw.execute("ALTER TABLE reservations DROP COLUMN order_ref")
    raw.commit()
    raw.close()
    reopened = AuthorityProfileStore(path)
    second = reopened.authorize(
        "profile-b", action="submit_paper", client_order_id="client-2",
        notional=Decimal("10.00"), actor="stop-owner", now=NOW,
    )
    assert second.replayed is False
    bound = reopened.authorize(
        "profile-b", action="submit_paper", client_order_id="client-1",
        notional=Decimal("25.00"), actor="stop-owner", now=NOW, order_ref="ord-1",
    )
    assert bound.replayed is True
    assert _refuse(
        lambda: reopened.authorize(
            "profile-b", action="submit_paper", client_order_id="client-1",
            notional=Decimal("25.00"), actor="stop-owner", now=NOW, order_ref="ord-2",
        )
    ) == "client_order_conflict"


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


def test_paper_handoff_still_goes_through_the_gateway(st, tmp_path):
    broker = _ProbeBroker()
    gateway = OrderGateway(broker, st=st, execution_scope="paper")
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
        submit_kwargs=scenario.submit_kwargs(), model_output=GRANT,
    )
    assert refused.reached_gateway is True
    assert refused.outcome.ok is False
    assert broker.calls == 0
    replay = handoff_to_gateway(
        store, gateway, profile_id="profile-paper", action="submit_paper",
        client_order_id="gw-refused", notional=Decimal(str(scenario.order.notional)),
        actor="stop-owner", now=now, submit_args=(scenario.order,),
        submit_kwargs=scenario.submit_kwargs(), model_output=GRANT,
    )
    assert replay.replayed is True
    assert replay.reached_gateway is False
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
            actor="stop-owner", now=NOW, model_output=GRANT,
        )
    ) == "transport_scope"
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
        actor="stop-owner", now=NOW, model_output=GRANT,
    )
    assert handoff.reached_gateway is False
    assert dry.consumption("profile-b-dry") == (Decimal("10.00"), 1)


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

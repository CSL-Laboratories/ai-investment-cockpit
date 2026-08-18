"""G8 — composition-root + API wiring tests (Owner B).

Enforces the WP8 'main.py' and 'orchestrator/app.py wiring' contract of
docs/build-scaffold-ultracode.md WP8 and SEC-05/Inv 4 (the audit HMAC key is keyring
get-or-create, never env/config): compose() builds (state, app, services); the brief
endpoints answer through the injected service registry and stay 501 without it (existing
WP5 tests unchanged); GET /proposals reads persisted ProposalRecord rows. Hermetic:
InMemoryKeyring (root conftest), tmp SQLite, FrozenClock; fastapi/httpx importorskip'd
matching the house pattern.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402

from core.clock import FrozenClock  # noqa: E402
from core.secrets import get_secret  # noqa: E402
from main import compose  # noqa: E402
from schemas.models.proposal_record import ProposalRecord  # noqa: E402
from tests.workflows.conftest import NOW  # noqa: E402
from workflows.services import build_services  # noqa: E402


def test_compose_builds_state_app_and_services(tmp_path):
    state, app, services = compose(db_url=f"sqlite:///{(tmp_path / 'c.db').as_posix()}",
                                   parquet_root=str(tmp_path / "raw"))
    assert isinstance(app, FastAPI)
    assert set(services) == {"brew", "intraday", "eod", "ask"}
    assert state.audit.verify_chain().ok
    assert app.state.services is services
    # RW-01: the ingest runner is composed too, and hangs off app.state beside the others (the
    # return shape stays a 3-tuple so every existing caller keeps working). The env-driven
    # feeds are unconfigured here, so they must be ABSENT — present-and-silent is the state
    # this package exists to make unreachable. (Keyring-driven feeds are asserted in
    # tests/system/test_rw01_market_day.py, which isolates its own keyring.)
    assert app.state.ingest is not None
    assert "sec:edgar" not in app.state.ingest.polls
    assert "finra" not in app.state.ingest.polls
    assert app.state.ingest.status()["feeds"].keys() == app.state.ingest.polls.keys() | \
        app.state.ingest.streams.keys()
    # Inv 4 / SEC-05: the HMAC key was minted INTO keyring (never env/config)
    secret = get_secret("audit-hmac-key")
    assert secret is not None

    # get-or-create: a second compose reuses the same key (chains stay verifiable)
    state2, _, _ = compose(db_url=f"sqlite:///{(tmp_path / 'c2.db').as_posix()}",
                           parquet_root=str(tmp_path / "raw2"))
    assert get_secret("audit-hmac-key").reveal() == secret.reveal()
    state.audit.append("compose_smoke", actor="test")
    assert state.audit.verify_chain().ok


def test_compose_wires_injected_paper_client_and_static_watchlist_behind_gateway(
    tmp_path, monkeypatch
):
    from brokers.alpaca_paper import FakeAlpaca
    import workflows.services as service_module

    class PaperClient(FakeAlpaca):
        def latest_quote(self, _symbol):
            return {
                "price": "150", "age_s": 0, "spread_pct": "0.1",
                "bid": "149.99", "ask": "150.01", "bid_size": 100, "ask_size": 120,
                "ts": NOW.isoformat(), "feed": "iex",
            }

        def daily_bars(self, _symbol, *, limit=60):
            assert limit == 60
            return [
                {"t": "2026-07-02T20:00:00Z", "c": "148", "v": 1_000_000},
                {"t": "2026-07-03T20:00:00Z", "c": "149", "v": 1_100_000},
                {"t": "2026-07-06T20:00:00Z", "c": "150", "v": 1_200_000},
            ]

    captured = {}

    def capture_triage(_state, _clock, **kwargs):
        captured["watchlist"] = kwargs["watchlist"]
        return {"status": "captured"}

    monkeypatch.setattr(service_module, "triage_loop", capture_triage)
    monkeypatch.setenv("COCKPIT_WATCHLIST", "INTC,AAPL,SPCX,GOOGL,WDC")
    monkeypatch.setenv("COCKPIT_MERGER_WATCHLIST", "AAPL")
    state, app, services = compose(
        db_url=f"sqlite:///{(tmp_path / 'paper.db').as_posix()}",
        parquet_root=str(tmp_path / "raw-paper"),
        paper_client=PaperClient(),
        clock=FrozenClock(NOW),
    )
    assert app.state.paper is not None
    assert app.state.paper.account()["account"] == "alpaca-paper"
    expected = frozenset({"INTC", "AAPL", "SPCX", "GOOGL", "WDC"})
    assert app.state.paper._watchlist() == expected  # noqa: SLF001 - composition contract
    assert state.edge_provider is app.state.paper._edge_provider  # noqa: SLF001
    assert state.edge_provider.strategy_watchlist == frozenset({"AAPL"})
    from strategies.merger_semantics import CashMergerSemanticVerifier

    assert isinstance(state.edge_provider.semantic_verifier, CashMergerSemanticVerifier)
    assert state.edge_provider.semantic_verifier.witness is None  # absent audit channel => closed
    assert app.state.watchlist == expected
    assert app.state.market_data.poll_quotes()["failed"] == []
    assert app.state.market_data.refresh_daily_bars()["failed"] == []
    assert app.state.quant_snapshots is app.state.market_data.snapshot_source
    assert any(getattr(route, "path", None) == "/ws/quant" for route in app.routes)
    from orchestrator.quant_api import quant_status

    status = quant_status(state)
    assert status["book_feed"] == "alpaca_iex_l1"
    assert status["market_data"]["faulted"] == []
    assert status["verification"]["impact_coefficient_y"] == "1.0"
    assert status["verification"]["max_adv_participation"] == "0.10"
    assert status["verification"]["net_cost_multiple"] == "2.0"
    assert app.state.market_data.verify_params.max_staleness_ns == 120_000_000_000
    from orchestrator.scheduler import build_scheduler

    jobs = {
        job.id: job
        for job in build_scheduler(
            state,
            state.clock,
            services=services,
            ingest=app.state.ingest,
            market_data=app.state.market_data,
            paper=app.state.paper,
        ).get_jobs()
    }
    assert "paper_order_reconcile" in jobs
    assert "paper_performance" in jobs
    assert jobs["paper_order_reconcile"].func().ok
    assert jobs["paper_performance"].func()["ok"]

    # Reassigning the legacy-mutable Settings object cannot widen the frozen production set.
    state.settings.watchlist = "NVDA"
    services["intraday"](state, state.clock)
    assert captured["watchlist"] == expected
    assert app.state.paper._watchlist() == expected  # noqa: SLF001
    assert app.state.watchlist == expected

    # Fresh deterministic risk consumes that same set: an in-universe symbol passes the
    # allowlist check, while the attempted post-compose widening remains rejected.
    def proposal(symbol):
        return {
            "proposal_id": f"p-{symbol}", "symbol": symbol, "side": "buy",
            "asset_type": "equity", "quantity": "1", "notional": "150",
            "order_type": "limit", "time_horizon": "short", "sizing_basis": "capped",
        }

    from config.settings import Mode
    from orchestrator.mode_ladder import ModeLadder

    ModeLadder(state).set_mode(Mode.paper, actor="op")
    inside, _ = app.state.paper._fresh_risk(proposal("INTC"), "INTC")  # noqa: SLF001
    inside_checks = {row["name"]: row["passed"] for row in inside["checks"]}
    assert inside_checks["symbol_allowlist"] is True
    # W-21/W-23: approval-time risk re-runs the ADV cost and deterministic edge path. No
    # immutable merger evidence is provisioned here, so the fresh check is fail-closed.
    assert inside["decision"] == "needs_human"
    assert inside_checks["net_edge_available"] is False
    from risk.engine import evaluate
    from workflows.proposals import build_risk_context

    outside = evaluate(
        proposal("NVDA"),
        build_risk_context(
            state, quote=app.state.market_data.quote("INTC"), watchlist=expected
        ),
    )
    outside_checks = {row["name"]: row["passed"] for row in outside["checks"]}
    assert outside_checks["symbol_allowlist"] is False
    assert state.audit.verify_chain().ok


def test_compose_wires_durable_git_witness_not_file_anchor(tmp_path, monkeypatch):
    """SEC-11 (W10, hardened by the G10 red-team): compose() wires a NON-NULL
    GitAuditLogAnchorStore (NEVER FileAnchorStore). In the default (Discord-deferred) deployment
    its ONLY backend is the same-trust-domain GitCommitWitness mirror, so the store has NO
    genuine out-of-domain witness -> latest() is None -> an auto unlock FAILS CLOSED. A git-only
    (file-anchor-in-effect) wiring must never satisfy the SEC-11 prerequisite."""
    from bridge.anchor import FileAnchorStore, GitAuditLogAnchorStore
    from config.settings import Mode
    from orchestrator.gate import compute_gate

    monkeypatch.setenv("COCKPIT_AUDIT_HEAD_FILE", str(tmp_path / "audit-head.json"))
    state, _, _ = compose(db_url=f"sqlite:///{(tmp_path / 'w.db').as_posix()}",
                          parquet_root=str(tmp_path / "raw"))
    assert state.anchor_store is not None
    assert isinstance(state.anchor_store, GitAuditLogAnchorStore)
    assert not isinstance(state.anchor_store, FileAnchorStore)
    assert state.anchor_store.has_out_of_domain_witness is False   # git-only: no genuine witness
    assert state.anchor_store.latest() is None                     # same-domain mirror is not trusted

    # BUG-051: production keeps the monotonic local mirror but must never stage/commit it onto
    # whichever development branch happens to be checked out. Any subprocess call here is a
    # regression to the old auto-commit behavior.
    def forbid_git(*args, **kwargs):
        raise AssertionError(f"runtime attempted to mutate git: {args!r} {kwargs!r}")

    monkeypatch.setattr("bridge.witness.subprocess.run", forbid_git)
    state.anchor_store.append(1, "a" * 64)
    assert (tmp_path / "audit-head.json").exists()
    assert state.anchor_store.latest() is None                     # local mirror still untrusted
    res = compute_gate(state, Mode.auto_veto)
    assert not res.unlockable and res.metrics.get("anchor_ok") is False   # fail closed


def test_merger_semantic_witness_uses_existing_keyring_token_and_audit_channel():
    from bridge.witness import DiscordSemanticWitness
    from config.settings import Settings
    from core.secrets import set_secret
    from main import _build_merger_semantic_witness

    set_secret("discord_bot_token", "test-token-never-logged")
    witness = _build_merger_semantic_witness(
        Settings(audit_log_channel_id=123456789, _env_file=None)
    )

    assert isinstance(witness, DiscordSemanticWitness)


def test_administrator_discord_channel_is_a_mirror_not_sec11_authority():
    from config.settings import Settings
    from core.secrets import set_secret
    from main import _build_anchor_store

    set_secret("discord_bot_token", "test-token-never-logged")
    store = _build_anchor_store(Settings(audit_log_channel_id=123456789, _env_file=None))

    assert store.has_out_of_domain_witness is False
    assert store.latest() is None


def test_partial_worm_configuration_creates_no_trusted_adapter():
    from config.settings import Settings
    from core.secrets import set_secret
    from main import _build_anchor_store

    set_secret("witness_s3_access_key_id", "AKIAEXPLICITONLY")
    store = _build_anchor_store(
        Settings(
            witness_s3_bucket="cockpit-worm-example",
            witness_s3_region="us-west-2",
            _env_file=None,
        )
    )

    assert store.has_out_of_domain_witness is False
    assert store.latest() is None


def test_complete_worm_configuration_uses_only_explicit_keyring_credentials(monkeypatch):
    import bridge.witness as witness_module
    from config.settings import Settings
    from core.secrets import set_secret
    from main import _build_anchor_store

    captured = {}

    class Trusted:
        out_of_domain = True

        def read_latest(self):
            return None

        def publish(self, _seq, _head_hash):
            return None

    def fake_factory(**kwargs):
        captured.update(kwargs)
        return Trusted()

    monkeypatch.setattr(witness_module, "s3_object_lock_witness", fake_factory)
    set_secret("witness_s3_access_key_id", "AKIAEXPLICITONLY")
    set_secret("witness_s3_secret_access_key", "explicit-secret")
    store = _build_anchor_store(
        Settings(
            witness_s3_bucket="cockpit-worm-example",
            witness_s3_region="us-west-2",
            witness_s3_expected_bucket_owner="123456789012",
            _env_file=None,
        )
    )

    assert store.has_out_of_domain_witness is True
    assert captured["access_key_id"] == "AKIAEXPLICITONLY"
    assert captured["secret_access_key"] == "explicit-secret"


def test_brief_endpoints_answer_through_services_and_proposals_read_rows(st, clock):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from orchestrator.app import create_app
    from orchestrator.auth import API_KEY_HEADER, ApiKeyAuth

    key = "wp8-test-key-123456"
    auth = {API_KEY_HEADER: key}
    services = build_services(st)
    app = create_app(st, auth=ApiKeyAuth(key_provider=lambda: key), services=services)
    client = TestClient(app)

    # wired: /brief/today is a 404 (no brief yet), NOT a 501; /brief/run invokes brew
    assert client.get("/brief/today", headers=auth).status_code == 404
    run = client.post("/brief/run", headers=auth)
    assert run.status_code == 200 and run.json()["status"] == "no_events"

    # unwired app keeps the honest 501s (existing WP5 tests unchanged)
    bare = TestClient(create_app(st, auth=ApiKeyAuth(key_provider=lambda: key)))
    assert bare.get("/brief/today", headers=auth).status_code == 501
    assert bare.post("/brief/run", headers=auth).status_code == 501
    assert bare.post("/automations", headers=auth).status_code == 501

    # GET /proposals reads ProposalRecord rows (id, symbol, side, status, flagged, created_at)
    assert client.get("/proposals", headers=auth).json() == {"proposals": []}
    record = ProposalRecord(id="prop-api", symbol="AAPL", side="buy", status="draft",
                            payload={"proposal_id": "prop-api"}, job_id=None,
                            risk_decision_id=None, order_id=None, flagged=False,
                            created_at=NOW, updated_at=NOW)
    st.audit.record_state_change(record, "trade_proposal", actor="test")
    body = client.get("/proposals", headers=auth).json()
    assert len(body["proposals"]) == 1
    row = body["proposals"][0]
    assert row["id"] == "prop-api" and row["symbol"] == "AAPL"
    assert row["side"] == "buy" and row["status"] == "draft" and row["flagged"] is False
    assert row["created_at"] is not None


def test_compose_fails_closed_on_credential_guard_violation(tmp_path, monkeypatch):
    """W11 (SEC-05): compose() INVOKES the credential-starvation guard before building the graph —
    a decision-agent account that can read a bearer store fails the whole compose closed."""
    from agent_runner import account_guard
    from agent_runner.account_guard import CredentialGuardError

    def boom(settings):
        raise CredentialGuardError("decision-agent account can read ~/.codex/auth.json")

    monkeypatch.setattr(account_guard, "assert_credential_starved", boom)
    with pytest.raises(CredentialGuardError):
        compose(db_url=f"sqlite:///{(tmp_path / 'cg.db').as_posix()}",
                parquet_root=str(tmp_path / "raw"))


def test_compose_fails_closed_when_a_decision_job_is_not_tool_starved(tmp_path, monkeypatch):
    """W11 (SEC-06): compose() INVOKES the tool inspector on the canonical decision specs — a
    decision job resolving to a tool-bearing argv fails the whole compose closed."""
    from agent_runner import tool_inspector
    from agent_runner.spec import AgentSpec

    tool_bearing = AgentSpec(job_id="triage", runtime="claude", model_alias="triage", prompt="",
                             schema={}, allowed_tools=["Bash"], decision_stage=True)
    monkeypatch.setattr(
        tool_inspector, "default_decision_stage_specs", lambda _settings=None: [tool_bearing]
    )
    with pytest.raises(tool_inspector.ToolStarvationError):
        compose(db_url=f"sqlite:///{(tmp_path / 'ts.db').as_posix()}",
                parquet_root=str(tmp_path / "raw"))


def test_build_decision_transport_selects_low_priv_when_account_configured():
    """W11 (SEC-05): compose wires an account-LAUNCHING transport when a low-priv account is
    configured (so decision agents spawn under it), else the plain subprocess transport (dev)."""
    from types import SimpleNamespace

    from agent_runner.transport import LowPrivSubprocessTransport, SubprocessTransport
    from main import build_decision_transport

    dev = build_decision_transport(SimpleNamespace(decision_agent_account=None))
    assert isinstance(dev, SubprocessTransport)

    prod = build_decision_transport(SimpleNamespace(decision_agent_account="cockpit-agent"))
    assert isinstance(prod, LowPrivSubprocessTransport) and prod.account == "cockpit-agent"

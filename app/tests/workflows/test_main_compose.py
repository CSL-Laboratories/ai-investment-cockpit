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

from core.secrets import get_secret  # noqa: E402
from main import compose  # noqa: E402
from schemas.models.proposal_record import ProposalRecord  # noqa: E402
from tests.workflows.conftest import NOW  # noqa: E402
from workflows.services import build_services  # noqa: E402


def test_compose_builds_state_app_and_services(tmp_path):
    state, app, services = compose(db_url=f"sqlite:///{(tmp_path / 'c.db').as_posix()}",
                                   parquet_root=str(tmp_path / "raw"))
    assert isinstance(app, FastAPI)
    assert set(services) == {"brew", "intraday", "eod"}
    assert state.audit.verify_chain().ok
    assert app.state.services is services
    # Inv 4 / SEC-05: the HMAC key was minted INTO keyring (never env/config)
    secret = get_secret("audit-hmac-key")
    assert secret is not None

    # get-or-create: a second compose reuses the same key (chains stay verifiable)
    state2, _, _ = compose(db_url=f"sqlite:///{(tmp_path / 'c2.db').as_posix()}",
                           parquet_root=str(tmp_path / "raw2"))
    assert get_secret("audit-hmac-key").reveal() == secret.reveal()
    state.audit.append("compose_smoke", actor="test")
    assert state.audit.verify_chain().ok


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

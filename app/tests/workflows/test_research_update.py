"""RW-35 empty-run research update through the real composition. No LLM."""

from __future__ import annotations

import hashlib
from pathlib import Path

from feasibility.inspect import CASES_PATH, RW27_FREEZE_SHA256, RW28_EVALUATION_SHA256
from feasibility.protocol import PROTOCOL_PATH
from feasibility.screens import RW29_CASES_SHA256, RW30_PROTOCOL_SHA256, load_screens
from feasibility.validate import FREEZE_PATH
from research.empty_run import compose_empty_run_update
from workflows.constants import EVENT_RESEARCH_UPDATE
from workflows.research_update import empty_run_update
from workflows.services import build_services

APP = Path(__file__).resolve().parents[2]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_empty_run_update_explains_empty_board_and_keeps_gates():
    payload = compose_empty_run_update()
    assert payload["kind"] == "research_update"
    assert payload["empirical_pass_claimed"] is False
    assert payload["qualify_count"] == 0
    assert payload["meets_cohort_gate"] is False
    assert payload["cohort_minimum"] == 30
    assert payload["latest_screen_id"] == "w24_2026_09_12"
    assert "pool 197" in payload["significance"]
    assert "supported 32" in payload["significance"]
    assert "cash 0" in payload["significance"]
    assert payload["next_trigger"] == "continue_get_only_natural_head_screens"
    classes = payload["failure_classes"]
    for name in (
        "acquisition", "extraction", "reasoning", "eligibility", "delivery", "scheduling",
    ):
        assert classes[name] >= 1
    kinds = {item["kind"] for item in payload["cases"]}
    assert {"empty", "rejected", "useful", "unverified"} <= kinds
    assert _digest(FREEZE_PATH) == RW27_FREEZE_SHA256
    assert _digest(CASES_PATH) == RW29_CASES_SHA256
    assert _digest(PROTOCOL_PATH) == RW30_PROTOCOL_SHA256
    screens = load_screens()
    assert screens["qualify_count"] == 0
    evaluation = APP / "feasibility" / "evaluation.v1.json"
    assert _digest(evaluation) == RW28_EVALUATION_SHA256


def test_research_update_service_delivers_and_audits(st, clock):
    posts = []

    def deliver(channel, payload):
        posts.append((channel, payload))
        return True

    services = build_services(st, deliver=deliver)
    assert "research_update" in services
    result = services["research_update"](st, clock)
    assert result["delivered"] is True
    assert result["empirical_pass_claimed"] is False
    assert posts[0][0] == "#brief"
    assert posts[0][1]["kind"] == "research_update"
    from sqlalchemy import select

    from schemas.models.audit_entry import AuditEntry

    with st.session_factory() as session:
        rows = session.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_RESEARCH_UPDATE)
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload["qualify_count"] == 0
    assert rows[0].payload["empirical_pass_claimed"] is False


def test_removing_research_update_wiring_is_visible():
    source = Path(build_services.__code__.co_filename).read_text(encoding="utf-8")
    assert "empty_run_update" in source
    assert '"research_update": research_update' in source
    assert empty_run_update is not None

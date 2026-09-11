"""RW-27 — frozen slice A shortlist and fail-closed contract validation."""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import jsonschema
import pytest

from feasibility.validate import (
    FORBIDDEN_IDS,
    FREEZE_PATH,
    FROZEN_IDS,
    FeasibilityError,
    QualifyBlocked,
    load_freeze,
    validate_freeze,
)

APP = Path(__file__).resolve().parents[2]
FEASIBILITY = APP / "feasibility"


def test_checked_in_freeze_loads() -> None:
    payload = load_freeze()
    assert payload["claim_id"] == "RW-27"
    assert payload["empirical_pass_claimed"] is False
    assert payload["metrics_schema_version"] is None
    assert tuple(item["id"] for item in payload["shortlist"]) == FROZEN_IDS
    assert not FORBIDDEN_IDS.intersection(FROZEN_IDS)
    assert all(item["verdict"] == "unverified" for item in payload["results"])


def test_qualify_blocked_while_metrics_unset() -> None:
    payload = copy.deepcopy(load_freeze())
    payload["results"][0]["verdict"] = "qualify"
    payload["results"][0]["reason"] = "attempted qualify with unset metrics"
    with pytest.raises(QualifyBlocked):
        validate_freeze(payload)


@pytest.mark.parametrize("verdict", ["watch", "reject"])
def test_watch_and_reject_blocked_while_metrics_unset(verdict: str) -> None:
    payload = copy.deepcopy(load_freeze())
    payload["results"][1]["verdict"] = verdict
    payload["results"][1]["reason"] = "attempted decisive verdict with unset metrics"
    with pytest.raises(QualifyBlocked):
        validate_freeze(payload)


def test_extra_candidate_rejected() -> None:
    payload = copy.deepcopy(load_freeze())
    extra = copy.deepcopy(payload["shortlist"][0])
    extra["id"] = "cash_merger"
    payload["shortlist"].append(extra)
    with pytest.raises(jsonschema.ValidationError):
        validate_freeze(payload)


def test_market_maker_id_rejected_by_schema() -> None:
    payload = copy.deepcopy(load_freeze())
    payload["shortlist"][4]["id"] = "market_maker"
    with pytest.raises(jsonschema.ValidationError):
        validate_freeze(payload)


def test_pass_claimed_rejected() -> None:
    payload = copy.deepcopy(load_freeze())
    payload["empirical_pass_claimed"] = True
    with pytest.raises(jsonschema.ValidationError):
        validate_freeze(payload)


def test_unknown_field_rejected() -> None:
    payload = copy.deepcopy(load_freeze())
    payload["activate_now"] = True
    with pytest.raises(jsonschema.ValidationError):
        validate_freeze(payload)


def test_source_paths_exist() -> None:
    root = APP.parent
    missing: list[str] = []
    for item in load_freeze()["shortlist"]:
        for rel in item["source_paths"]:
            target = root / rel
            if not target.is_file() and not target.is_dir():
                missing.append(rel)
    assert not missing, missing


def test_result_order_must_match_shortlist() -> None:
    payload = copy.deepcopy(load_freeze())
    payload["results"][0], payload["results"][1] = (
        payload["results"][1],
        payload["results"][0],
    )
    with pytest.raises(FeasibilityError, match="same order"):
        validate_freeze(payload)


def test_freeze_file_is_canonical_bytes() -> None:
    raw = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    validate_freeze(raw)


_FORBIDDEN_IMPORT = (
    "risk",
    "brokers",
    "agent_runner",
    "subprocess",
    "orchestrator.mode_ladder",
)


def test_feasibility_package_has_no_order_or_llm_imports() -> None:
    offenders: list[str] = []
    for path in FEASIBILITY.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if any(name == item or name.startswith(f"{item}.") for item in _FORBIDDEN_IMPORT):
                    offenders.append(f"{path.name}:{name}")
    assert not offenders, offenders

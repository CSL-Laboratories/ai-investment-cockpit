"""The O8 registry binds reproducible test selections, never production paths."""
from __future__ import annotations

import copy
import json

import pytest

from tests.test_o8_phase_a import CHECK_O8, _synthetic_comparison


@pytest.mark.parametrize("locator", [
    "app/risk/engine.py::evaluate",
    "app/tests/test_invariants.py",
    "app/tests/",
    "app/tests/missing.py::test_missing",
    "app/tests/test_invariants.py::test_missing",
    "app/tests/test_invariants.py::_order_path_offenders",
    "app/tests/test_invariants.py::test_invariant_1_order_calls_only_in_brokers::extra",
    "app/tests/../tests/test_invariants.py::test_invariant_1_order_calls_only_in_brokers",
    "https://example.test/test_cases.py::test_case",
    "C:/tests/test_cases.py::test_case",
    "app\\tests\\test_invariants.py::test_invariant_1_order_calls_only_in_brokers",
    "app/tests/test_invariants.py::test_missing\x00",
    None,
])
def test_floor_locator_rejects_non_test_or_missing_references(locator):
    assert not CHECK_O8._test_locator_exists(locator)


def test_every_floor_has_resolvable_cases_and_keeps_its_flags():
    packet = CHECK_O8._load_json(CHECK_O8.PACKET_PATH)
    assert set(CHECK_O8.FLOOR_LOCATORS) == set(CHECK_O8.FLOOR_IDS)
    for floor in packet["hard_floors"]:
        assert floor["enforced_by"] == "tests"
        assert floor["benchmark_eligible"] is False
        assert CHECK_O8._floor_locators_valid(floor)
        assert len(floor["test_locators"]) == len(set(floor["test_locators"]))


def _mutate(floor, kind):
    if kind == "production":
        floor["test_locators"] = ["app/risk/engine.py"]
    elif kind == "missing_case":
        floor["test_locators"][0] += "_removed"
    elif kind == "partial":
        floor["test_locators"].pop()
    elif kind == "duplicate":
        floor["test_locators"].append(floor["test_locators"][0])
    elif kind == "unrelated_test":
        floor["test_locators"] = ["app/tests/test_wp0.py::test_mode_defaults_read_only"]
    else:
        floor["test_locator"] = "app/tests/test_invariants.py"
        del floor["test_locators"]


@pytest.mark.parametrize("kind", [
    "production", "missing_case", "partial", "duplicate", "unrelated_test", "legacy",
])
def test_floor_selection_is_pinned_in_candidate_packet_and_attached_floors(kind):
    packet = CHECK_O8._load_json(CHECK_O8.PACKET_PATH)
    candidate = copy.deepcopy(packet["candidate"])
    _mutate(candidate["floors"][0], kind)
    assert "candidate missing floors" in CHECK_O8.candidate_reasons(candidate)
    _mutate(packet["hard_floors"][0], kind)
    with pytest.raises(CHECK_O8.O8Error, match="floor locator drift"):
        CHECK_O8.validate_packet(packet)
    benchmark = _synthetic_comparison()
    _mutate(benchmark["floors"][0], kind)
    assert "floor locator drift: invariant_1_single_order_path" in CHECK_O8.benchmark_reasons(benchmark)


@pytest.mark.parametrize("kind", [
    "production", "missing_case", "partial", "duplicate", "unrelated_test", "legacy",
])
def test_floor_receipt_requires_the_complete_registered_selection(monkeypatch, kind):
    benchmark = _synthetic_comparison()
    receipt = json.loads(CHECK_O8._retained_bytes(benchmark["floor_results"]))
    assert CHECK_O8._floor_results_bound(benchmark)
    _mutate(receipt["results"][0], kind)
    read = CHECK_O8._retained_bytes
    monkeypatch.setattr(CHECK_O8, "_retained_bytes", lambda ref: (
        json.dumps(receipt).encode() if ref == benchmark["floor_results"] else read(ref)
    ))
    assert not CHECK_O8._floor_results_bound(benchmark)


def test_registered_but_deleted_function_fails_every_validation_path(monkeypatch):
    packet = CHECK_O8._load_json(CHECK_O8.PACKET_PATH)
    benchmark = _synthetic_comparison()
    target = CHECK_O8.FLOOR_LOCATORS[CHECK_O8.FLOOR_IDS[0]][0]
    exists = CHECK_O8._test_locator_exists
    monkeypatch.setattr(CHECK_O8, "_test_locator_exists", lambda value: (
        False if value == target else exists(value)
    ))
    assert "candidate missing floors" in CHECK_O8.candidate_reasons(packet["candidate"])
    with pytest.raises(CHECK_O8.O8Error, match="missing floor test case"):
        CHECK_O8.validate_packet(packet)
    assert f"missing floor test case: {target}" in CHECK_O8.benchmark_reasons(benchmark)
    assert not CHECK_O8._floor_results_bound(benchmark)
    assert CHECK_O8.floor_qualification_gaps(benchmark) == ["hung_orchestrator_detection"]


def _serve_receipt(monkeypatch, benchmark, receipt):
    read = CHECK_O8._retained_bytes
    monkeypatch.setattr(CHECK_O8, "_retained_bytes", lambda ref: (
        json.dumps(receipt).encode() if ref == benchmark["floor_results"] else read(ref)
    ))


@pytest.mark.parametrize("floor_id", CHECK_O8.FLOOR_IDS)
def test_every_floor_row_is_structurally_bound(monkeypatch, floor_id):
    # Binding stays checkable for every floor even though qualification is incomplete.
    benchmark = _synthetic_comparison()
    receipt = json.loads(CHECK_O8._retained_bytes(benchmark["floor_results"]))
    assert CHECK_O8._floor_results_bound(benchmark)
    row = next(item for item in receipt["results"] if item["id"] == floor_id)
    assert row["outcome"] == "pass"
    assert row["test_locators"] == CHECK_O8.FLOOR_LOCATORS[floor_id]
    row["test_locators"] = row["test_locators"][:-1] or ["app/tests/test_wp0.py::test_mode_defaults_read_only"]
    _serve_receipt(monkeypatch, benchmark, receipt)
    assert not CHECK_O8._floor_results_bound(benchmark)


def test_published_registry_names_no_hung_orchestrator_selector():
    requirement = CHECK_O8.FLOOR_COVERAGE_REQUIREMENTS["hung_orchestrator_detection"]
    assert requirement == {"floors": ("invariant_7_fail_closed", "fail_closed"), "selector": None}
    assert CHECK_O8.FLOOR_LOCATORS["invariant_7_fail_closed"] == CHECK_O8.FLOOR_LOCATORS["fail_closed"]


# Test-only registration mechanics. This name is deliberately not a test function, so the
# real source lookup rejects it; it is never a hung-orchestrator detector or a PASS.
SYNTHETIC_SELECTOR = "app/tests/test_o8_floor_registry.py::test_synthetic_registration_placeholder_only"
HUNG_FLOORS = ("invariant_7_fail_closed", "fail_closed")


@pytest.mark.parametrize(
    ("case", "registry_floors", "receipt_floors", "required", "source_exists", "bound", "qualifies"),
    [
        ("registered_everywhere", HUNG_FLOORS, HUNG_FLOORS, True, True, True, True),
        ("invariant_7_list_only", HUNG_FLOORS[:1], HUNG_FLOORS[:1], True, True, True, False),
        ("fail_closed_list_only", HUNG_FLOORS[1:], HUNG_FLOORS[1:], True, True, True, False),
        ("receipt_lacks_selector", HUNG_FLOORS, (), True, True, False, False),
        ("receipt_one_row_only", HUNG_FLOORS, HUNG_FLOORS[:1], True, True, False, False),
        ("source_function_missing", HUNG_FLOORS, HUNG_FLOORS, True, False, False, False),
        ("listed_but_not_required", HUNG_FLOORS, HUNG_FLOORS, False, True, True, False),
    ],
)
def test_future_selector_registration_mechanics(
    monkeypatch, case, registry_floors, receipt_floors, required, source_exists, bound, qualifies,
):
    benchmark = _synthetic_comparison()
    receipt = json.loads(CHECK_O8._retained_bytes(benchmark["floor_results"]))
    for row in receipt["results"]:
        if row["id"] in receipt_floors:
            row["test_locators"].append(SYNTHETIC_SELECTOR)
    _serve_receipt(monkeypatch, benchmark, receipt)
    registry = copy.deepcopy(CHECK_O8.FLOOR_LOCATORS)
    for floor_id in registry_floors:
        registry[floor_id].append(SYNTHETIC_SELECTOR)
    monkeypatch.setattr(CHECK_O8, "FLOOR_LOCATORS", registry)
    requirements = copy.deepcopy(CHECK_O8.FLOOR_COVERAGE_REQUIREMENTS)
    if required:
        requirements["hung_orchestrator_detection"]["selector"] = SYNTHETIC_SELECTOR
    monkeypatch.setattr(CHECK_O8, "FLOOR_COVERAGE_REQUIREMENTS", requirements)
    assert not CHECK_O8._test_locator_exists(SYNTHETIC_SELECTOR)
    if source_exists:
        exists = CHECK_O8._test_locator_exists
        monkeypatch.setattr(CHECK_O8, "_test_locator_exists", lambda value: (
            value == SYNTHETIC_SELECTOR or exists(value)
        ))
    assert CHECK_O8._floor_results_bound(benchmark) is bound
    gaps = CHECK_O8.floor_qualification_gaps(benchmark)
    assert gaps == ([] if qualifies else ["hung_orchestrator_detection"])
    qualification = [
        reason for reason in CHECK_O8.benchmark_reasons(benchmark)
        if reason.startswith(CHECK_O8.FLOOR_QUALIFICATION_INCOMPLETE)
    ]
    assert bool(qualification) is not qualifies


def test_source_lookup_does_not_execute_test_modules(tmp_path, monkeypatch):
    test = tmp_path / "app/tests/test_probe.py"
    test.parent.mkdir(parents=True)
    test.write_text("raise RuntimeError('must not import')\ndef test_probe():\n    assert True\n")
    monkeypatch.setattr(CHECK_O8, "ROOT", tmp_path)
    assert CHECK_O8._test_locator_exists("app/tests/test_probe.py::test_probe")
    test.write_text("def broken syntax\n")
    assert not CHECK_O8._test_locator_exists("app/tests/test_probe.py::test_probe")


def test_historical_synthetic_receipt_does_not_certify_the_amended_registry(monkeypatch):
    benchmark = _synthetic_comparison()
    old = (CHECK_O8.O8 / "fixtures/validator-only/floor-results.json").read_bytes()
    monkeypatch.setattr(CHECK_O8, "_retained_bytes", lambda _ref: old)
    assert not CHECK_O8._floor_results_bound(benchmark)

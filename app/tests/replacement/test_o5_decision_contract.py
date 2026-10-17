"""Provider-free CSL-217 O5 contract validator.

Positives must be admitted at maturity researched. Negative controls must be
refused with their designed codes. The test prints those refusals so CI logs
show them failing as designed. It does not call a provider or the network.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "tools" / "o5_decision_contract.py"
SCHEMA = ROOT / "docs/replacement/outcomes/O5/schemas/o5-wire.schema.json"
SUITE = ROOT / "docs/replacement/outcomes/O5/fixtures/task-suite.json"

REQUIRED_NEGATIVES = {
    "mock_reported_as_installed": "mock_is_not_installed_route",
    "fallback_widens_authority": "fallback_widens_authority",
    "fallback_widens_budget": "fallback_widens_budget",
    "fallback_widens_disclosure": "fallback_widens_disclosure",
    "cheaper_despite_worse_correctness": "worse_correctness_accepted",
    "faster_despite_worse_grounding": "worse_grounding_accepted",
    "cheaper_despite_worse_calibration": "worse_calibration_accepted",
    "provider_rewrites_protocol": "provider_result_rewrites_protocol",
}

FORBIDDEN_IMPORTS = frozenset({
    "httpx",
    "urllib",
    "socket",
    "requests",
    "openai",
    "anthropic",
    "subprocess",
    "boto3",
    "websockets",
    "discord",
})


def _load():
    spec = importlib.util.spec_from_file_location("o5_decision_contract", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves string annotations through sys.modules[cls.__module__].
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_candidate_command_fails_negative_controls_as_designed(capfd) -> None:
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", "tools/o5_decision_contract.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    with capfd.disabled():
        print(result.stdout, end="")
    assert result.returncode == 0, result.stderr
    assert "maturity_claimed: researched" in result.stdout
    assert "qualification_claimed: false" in result.stdout
    assert "empirical_pass_claimed: false" in result.stdout
    assert "contract_frozen_claimed: false" in result.stdout
    assert "O5 candidate: FAIL" not in result.stdout
    assert "UNEXPECTEDLY admitted" not in result.stdout
    for negative_id, code in REQUIRED_NEGATIVES.items():
        line = f"O5 negative control failed as designed: {negative_id} — {code}:"
        assert line in result.stdout
    module = _load()
    report = module.evaluate(ROOT)
    assert report.ok
    assert report.maturity == "researched"
    assert report.qualification_claimed is False
    assert report.empirical_pass_claimed is False
    assert report.contract_frozen_claimed is False
    seen = {item.id: item for item in report.negatives}
    for negative_id, code in REQUIRED_NEGATIVES.items():
        item = seen[negative_id]
        assert item.admitted is False
        assert item.code == code
        assert item.expected_code == code


def test_relabeling_a_frozen_task_fails_closed() -> None:
    module = _load()
    suite = json.loads(SUITE.read_text(encoding="utf-8"))
    suite["cases"][0]["expected"]["value_correctness"] = True
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))["$defs"]["task_suite"]
    context = module.Context(
        root=ROOT,
        suite_digest="relabel",
        suite=suite,
        cases={},
        route_ids=set(),
        negative_ids=set(suite["required_negative_ids"]),
    )
    admission = module._admit_document(suite, schema, context)
    assert admission.admitted is False
    assert admission.code == "suite_oracle_mismatch"


def _context(module, suite: dict) -> object:
    return module.Context(
        root=ROOT,
        suite_digest="relabel",
        suite=suite,
        cases={case["id"]: case for case in suite["cases"]},
        route_ids={"current.decision"},
        negative_ids=set(suite["required_negative_ids"]),
    )


def test_json_equality_keeps_containers_and_bools() -> None:
    module = _load()
    assert module._json_equal({}, {"x": {}}) is False
    assert module._json_equal({"x": {}}, {"x": {}}) is True
    assert module._json_equal({"x": 1}, {"x": True}) is False
    assert module._json_equal({"x": 1}, {"x": 1}) is True
    assert module._json_equal([], {}) is False


def test_grounding_rejects_bool_and_number_aliases() -> None:
    module = _load()
    case = {
        "dispatched_refs": ["ref-1"],
        "sources": {"ref-1": {"fields": {"n": 1, "box": {}}}},
        "output": {"source_refs": ["ref-1"], "claims": {"n": True}},
    }
    assert module._score_grounding(case) is False
    case["output"]["claims"] = {"box": {"x": {}}}
    assert module._score_grounding(case) is False
    case["output"]["claims"] = {"n": 1}
    assert module._score_grounding(case) is True


def test_conflicting_citations_are_not_grounded() -> None:
    module = _load()
    case = {
        "dispatched_refs": ["a", "b"],
        "sources": {
            "a": {"fields": {"name": "composer"}},
            "b": {"fields": {"name": "conductor"}},
        },
        "output": {"source_refs": ["a", "b"], "claims": {"name": "conductor"}},
    }
    assert module._score_grounding(case) is False
    case["output"]["source_refs"] = ["b", "a"]
    assert module._score_grounding(case) is False
    case["sources"]["b"]["fields"]["name"] = "composer"
    case["output"]["claims"] = {"name": "composer"}
    assert module._score_grounding(case) is True
    case["output"]["source_refs"] = ["a", "b"]
    assert module._score_grounding(case) is True


def test_higher_tier_evidence_is_not_admitted() -> None:
    module = _load()
    suite = json.loads(SUITE.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))["$defs"]["qualification_record"]
    document = json.loads(
        (ROOT / "docs/replacement/outcomes/O5/fixtures/positive/qualification-current-decision.json").read_text(
            encoding="utf-8"
        )
    )
    context = _context(module, suite)
    admitted = module._admit_document(document, schema, context)
    assert admitted.admitted is True
    for evidence_class in ("demo_or_paper", "shadow", "limited_live"):
        higher = dict(document)
        higher["evidence_class"] = evidence_class
        refused = module._admit_document(higher, schema, context)
        assert refused.admitted is False
        assert refused.code == "installed_route_not_qualified"


def test_result_rejects_unsupported_evidence() -> None:
    module = _load()
    suite = json.loads(SUITE.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))["$defs"]["decision_result"]
    document = json.loads(
        (ROOT / "docs/replacement/outcomes/O5/fixtures/positive/decision-result.json").read_text(encoding="utf-8")
    )
    context = _context(module, suite)
    context.suite_digest = document["suite_digest"]
    admitted = module._admit_document(document, schema, context)
    assert admitted.admitted is True
    for evidence_class in ("demo_or_paper", "shadow", "limited_live", "independently_verified"):
        higher = dict(document)
        higher["evidence_class"] = evidence_class
        refused = module._admit_document(higher, schema, context)
        assert refused.admitted is False
        assert refused.code == "installed_route_not_qualified"


def test_grounding_and_refusal_results_fail_closed() -> None:
    module = _load()
    suite = json.loads(SUITE.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))["$defs"]["decision_result"]
    base = json.loads(
        (ROOT / "docs/replacement/outcomes/O5/fixtures/positive/decision-result.json").read_text(encoding="utf-8")
    )
    context = _context(module, suite)
    context.suite_digest = base["suite_digest"]
    matched = dict(base)
    matched["task_id"] = "task.ungrounded_claim"
    matched["grounding_ok"] = False
    admitted = module._admit_document(matched, schema, context)
    assert admitted.admitted is True
    flipped = dict(base)
    flipped["task_id"] = "task.ungrounded_claim"
    flipped["grounding_ok"] = True
    refused = module._admit_document(flipped, schema, context)
    assert refused.admitted is False
    assert refused.code == "result_contradicts_suite"
    order = dict(base)
    order["task_id"] = "task.refuse_order"
    order_result = module._admit_document(order, schema, context)
    assert order_result.admitted is False
    assert order_result.code == "result_contradicts_suite"


def test_refusal_code_must_follow_the_requested_action() -> None:
    module = _load()
    suite = json.loads(SUITE.read_text(encoding="utf-8"))
    for case in suite["cases"]:
        if case["id"] == "task.refuse_order":
            case["refusal_code"] = "credential_refused"
            case["expected"]["refusal_code"] = "credential_refused"
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))["$defs"]["task_suite"]
    admission = module._admit_document(suite, schema, _context(module, suite))
    assert admission.admitted is False
    assert admission.code == "suite_oracle_mismatch"


def test_measure_report_requires_refusal() -> None:
    module = _load()
    suite = json.loads(SUITE.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))["$defs"]["measure_report"]
    document = json.loads(
        (ROOT / "docs/replacement/outcomes/O5/fixtures/measures.json").read_text(encoding="utf-8")
    )
    admitted = module._admit_document(document, schema, _context(module, suite))
    assert admitted.admitted is True
    dropped = dict(document)
    dropped["measures"] = [item for item in document["measures"] if item["id"] != "refusal"]
    refused = module._admit_document(dropped, schema, _context(module, suite))
    assert refused.admitted is False
    assert refused.code == "protocol_pin_mismatch"


def test_validator_imports_no_provider_or_network() -> None:
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    assert imported.isdisjoint(FORBIDDEN_IMPORTS)

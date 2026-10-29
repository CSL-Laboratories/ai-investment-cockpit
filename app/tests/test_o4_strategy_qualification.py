"""CSL-216 O4 candidate packet. Provider-free. No network and no credentials.

The negative controls are supposed to fail validation. This test passes only
when each frozen control fails for its designed reason, and it prints those
failures so the CI log shows them.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import runpy
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
O4 = ROOT / "docs" / "replacement" / "outcomes" / "O4"
COMMAND = ("python", "-X", "utf8", "-B", "docs/replacement/outcomes/O4/validate.py")
ACCEPTED = {
    "docs/replacement/SUCCESSOR-PRODUCT.md":
        "6e5f0d0cad102077fd058aac4970ac0eb31fd6f6e952f948c43edaa9fe0c919e",
    "docs/replacement/ISSUE-CONTRACT.md":
        "f1fb50dab4b166ca476698fc3974d6a1c2003b16df1d89ff8930060ad04b5094",
    "docs/replacement/MONTH-PROGRAM.md":
        "f28b75244a4eef5e9d3095349d6088bcd08e09547ab8e0fbc03ba52a119f3001",
    "docs/replacement/SCOPE-AUDIT.md":
        "d23dcc769e743d199ae4142f0e20ad5feebfe310c71d9f57fdd9d647c230db8c",
}
FAILURES = (
    "O4 negative control FAIL overlapping_populations:",
    "O4 negative control FAIL dropped_costs:",
    "O4 negative control FAIL dropped_empty_outcome:",
    "O4 negative control FAIL parameter_variants_as_distinct_strategies:",
    "O4 negative control FAIL decision_owner_role:",
    "O4 negative control FAIL leakage_controls_satisfied:",
    "O4 negative control FAIL variant_configuration:",
    "O4 negative control FAIL unbound_population:",
    "O4 negative control FAIL metric_registry:",
    "O4 negative control FAIL measured_costs_on_unrun_experiment:",
    "O4 negative control FAIL promotion_stage_authorized:",
    "O4 negative control FAIL promotion_gates:",
    "O4 negative control FAIL missing_promotion_rung:",
    "O4 negative control FAIL promotion_gates:",
    "O4 negative control FAIL hypothesis_costs:",
    "O4 negative control FAIL hypothesis_costs:",
    "O4 negative control FAIL hypothesis_costs:",
    "O4 negative control FAIL hypothesis_costs:",
    "O4 negative control FAIL hypothesis_costs:",
    "O4 negative control FAIL hypothesis_costs:",
    "O4 negative control FAIL hypothesis_costs:",
    "O4 negative control FAIL hypothesis_costs:",
    "O4 negative control FAIL hypothesis_costs:",
    "O4 negative control FAIL hypothesis_costs:",
    "O4 negative control FAIL hypothesis_costs:",
    "O4 negative control FAIL hypothesis_costs:",
    "O4 negative control FAIL hypothesis_costs:",
    "O4 negative control FAIL hypothesis_costs:",
    "O4 negative control FAIL hypothesis_costs:",
    "O4 negative control FAIL hypothesis_costs:",
    "O4 negative control FAIL hypothesis_costs:",
    "O4 negative control FAIL hypothesis_costs:",
    "O4 negative control FAIL hypothesis_costs:",
)
CHECKED = (
    "O4 candidate packet checked at maturity researched; "
    "qualify_count=0; promotion_authorized=false"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_o4_validate():
    spec = importlib.util.spec_from_file_location("o4_validate", O4 / "validate.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# Phrases the hypothesis must contain, one per COST_KEYS entry, in that order.
HYPOTHESIS_COST_PHRASES = {
    "transaction": "transaction",
    "latency": "latency",
    "data": "data",
    "model": "model",
    "venue_fees": "venue fees",
    "spread_slippage": "spread/slippage",
    "funding_collateral": "funding/collateral",
    "tax_accounting": "tax/accounting",
}


def test_o4_negative_controls_fail_as_designed(capsys) -> None:
    completed = subprocess.run(
        (sys.executable, *COMMAND[1:]),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    with capsys.disabled():
        sys.stdout.write(completed.stdout)
        sys.stdout.flush()
    assert completed.returncode == 0, completed.stderr
    lines = completed.stdout.splitlines()
    assert len(lines) == len(FAILURES) + 1
    for line, prefix in zip(lines, FAILURES):
        assert line.startswith(prefix)
    assert lines[-1] == CHECKED
    assert "PASS" not in completed.stdout
    assert "contract_frozen" not in completed.stdout
    source = (O4 / "validate.py").read_text(encoding="utf-8")
    for banned in ("import socket", "import urllib", "import httpx", "import requests"):
        assert banned not in source


def test_o4_requires_every_nonlive_rung_in_order() -> None:
    assess = runpy.run_path(str(O4 / "validate.py"))["assess"]
    packet = json.loads((O4 / "fixtures/candidate-packet.json").read_text(encoding="utf-8"))
    schema = json.loads((O4 / "schemas/promotion-protocol.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    stages = packet["promotion_protocol"]["stages"]
    assert [stage["id"] for stage in stages] == ["replay", "simulation", "demo", "shadow", "paper"]
    assert assess(packet) == []
    assert validator.is_valid(packet["promotion_protocol"])
    for index in range(len(stages)):
        missing = deepcopy(packet)
        del missing["promotion_protocol"]["stages"][index]
        assert not validator.is_valid(missing["promotion_protocol"])
        assert [failure.code for failure in assess(missing)] == ["missing_promotion_rung"]

        if index == 0:
            continue
        skipped = deepcopy(packet)
        skipped["promotion_protocol"]["stages"][index]["requires_prior_stage"] = None
        assert [failure.code for failure in assess(skipped)] == ["schema"]
        reordered = deepcopy(packet)
        rungs = reordered["promotion_protocol"]["stages"]
        rungs[index - 1], rungs[index] = rungs[index], rungs[index - 1]
        assert not validator.is_valid(reordered["promotion_protocol"])
        assert all(failure.code == "schema" for failure in assess(reordered))


def test_o4_enforces_gates_and_refuses_authority_at_every_rung() -> None:
    assess = runpy.run_path(str(O4 / "validate.py"))["assess"]
    packet = json.loads((O4 / "fixtures/candidate-packet.json").read_text(encoding="utf-8"))
    for index, stage in enumerate(packet["promotion_protocol"]["stages"]):
        authorized = deepcopy(packet)
        authorized["promotion_protocol"]["stages"][index]["authorized"] = True
        assert [failure.code for failure in assess(authorized)] == ["promotion_stage_authorized"]
        for gate, field in (
            ("entry_gates", "criteria"),
            ("entry_gates", "required_evidence"),
            ("exit_gates", "criteria"),
        ):
            for position in range(len(stage[gate][field])):
                weakened = deepcopy(packet)
                values = weakened["promotion_protocol"]["stages"][index][gate][field]
                # Keep schema shape valid to exercise the semantic gate checker.
                values[position] = "irrelevant_substitute"
                assert [failure.code for failure in assess(weakened)] == ["promotion_gates"]
    for field in ("authorized", "limited_live_authorized", "broader_live_authorized"):
        live = deepcopy(packet)
        live["promotion_protocol"]["live"][field] = True
        assert [failure.code for failure in assess(live)] == ["schema"]


def test_o4_protocol_keeps_research_and_owner_decisions() -> None:
    protocol = (O4 / "PROTOCOL.md").read_text(encoding="utf-8")
    assert "## Research record" in protocol
    assert "## Research (Parallel)" not in protocol
    assert "## Unresolved decisions (owner: roles)" in protocol
    assert "CSL-Laboratories/csl-research" in protocol
    assert "aab0b90c130948434a752611d66da32390e7186c" in protocol
    assert "submissions/20261029-cockpit-csl216-o4-research-prose-01/record.json" in protocol
    assert "candidate-only, unreviewed submission (csl-research#261 not yet merged)" in protocol
    assert "search_a81ed0bcb8b97e64d4f958ef59dde38a" not in protocol
    assert "https://doi.org/10.1016/j.knosys.2024.112477" not in protocol
    assert "https://github.com/eslazarev/purged-cross-validation" not in protocol
    for url in (
        "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551",
        "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253",
    ):
        assert url in protocol
    for decision in ("Spend", "Credentials", "Tax/legal", "Architecture", "Release", "Live", "Thresholds"):
        assert f"| {decision} |" in protocol
    assert "**Maturity claimed:** `researched`." in protocol
    packet = json.loads((O4 / "fixtures" / "candidate-packet.json").read_text(encoding="utf-8"))
    assert packet["maturity"] == "researched"
    assert packet["qualification_claimed"] is False
    assert packet["contract_frozen_claimed"] is False
    assert packet["empirical_pass_claimed"] is False
    assert [item["status"] for item in packet["unresolved_decisions"]] == ["undecided"] * 7
    owners = {item["id"]: item["owner"] for item in packet["unresolved_decisions"]}
    assert owners["release"] == "release owner"
    assert owners["spend"] == owners["credentials"] == "reserved authority"
    assert set(owners.values()) == {"owner", "release owner", "reserved authority"}
    registry_ids = [item["id"] for item in packet["metric_registry"]["measures"]]
    assert "net_yield" in registry_ids and "capital_use" in registry_ids
    for variant in packet["strategy_contract"]["parameter_variants"]:
        assert variant["configuration"]["legs"]
        assert len(variant["configuration_sha256"]) == 64


def test_o4_requires_declared_costs_and_cash_benchmark() -> None:
    packet = json.loads((O4 / "fixtures/candidate-packet.json").read_text(encoding="utf-8"))
    schema = json.loads((O4 / "schemas/experiment.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    experiment = packet["experiment"]
    assert validator.is_valid(experiment)
    unmeasured = {"amount": None, "status": "retained_unmeasured", "unit": "owner_unset"}
    for key in ("venue_fees", "spread_slippage", "funding_collateral", "tax_accounting"):
        assert experiment["costs"][key] == unmeasured
    baseline = experiment["matched_baseline"]
    for key in ("instrument", "rate_source", "as_of", "compounding", "horizon", "capital_flow_schedule"):
        assert baseline[key] is None
    assert baseline["value"] is None

    missing_fee = deepcopy(experiment)
    del missing_fee["costs"]["venue_fees"]
    assert not validator.is_valid(missing_fee)

    missing_rate = deepcopy(experiment)
    del missing_rate["matched_baseline"]["rate_source"]
    assert not validator.is_valid(missing_rate)


def test_o4_hypothesis_names_every_retained_cost() -> None:
    validate = _load_o4_validate()
    packet = json.loads((O4 / "fixtures/candidate-packet.json").read_text(encoding="utf-8"))
    assert [item.code for item in validate.assess(packet)] == []
    for section in ("experiment", "strategy_contract"):
        text = packet[section]["hypothesis"]
        for key, phrase in HYPOTHESIS_COST_PHRASES.items():
            assert phrase in text
            broken = deepcopy(packet)
            broken[section]["hypothesis"] = text.replace(phrase, "omitted", 1)
            found = validate.assess(broken)
            assert [item.code for item in found] == ["hypothesis_costs"]
            assert f"{section}.{key}" in found[0].message
        excluded = deepcopy(packet)
        excluded[section]["hypothesis"] = text + " Venue fees are excluded."
        found = validate.assess(excluded)
        assert [item.code for item in found] == ["hypothesis_costs"]
        assert f"{section}.exclusion:exclud" in found[0].message
    devin_example = (
        "Beats cash after transaction, latency, data and model costs; "
        "venue fees, spread/slippage, funding/collateral and tax/accounting are excluded."
    )
    both = deepcopy(packet)
    both["experiment"]["hypothesis"] = devin_example
    both["strategy_contract"]["hypothesis"] = devin_example
    found = validate.assess(both)
    assert [item.code for item in found] == ["hypothesis_costs"]
    assert "experiment.inclusion" in found[0].message
    assert "experiment.exclusion:exclud" in found[0].message


def test_o4_accepts_equivalent_net_cost_wording() -> None:
    validate = _load_o4_validate()
    packet = json.loads((O4 / "fixtures/candidate-packet.json").read_text(encoding="utf-8"))
    equivalents = (
        "Beats cash net of all eight retained costs (transaction, latency, data, "
        "model, venue fees, spread/slippage, funding/collateral, and tax/accounting).",
        "Beats cash net of all eight retained costs (tax/accounting, funding/collateral, "
        "spread/slippage, venue-fee, model, data, latency, and transaction).",
        "Beats cash without look-ahead bias, net of transaction, latency, data, "
        "model, venue fees, spread/slippage, funding/collateral, and tax/accounting.",
    )
    for hypothesis in equivalents:
        candidate = deepcopy(packet)
        candidate["experiment"]["hypothesis"] = hypothesis
        candidate["strategy_contract"]["hypothesis"] = hypothesis
        assert validate.assess(candidate) == []
    base = packet["experiment"]["hypothesis"]
    repeated = deepcopy(packet)
    repeated["experiment"]["hypothesis"] = base + " Data costs remain included."
    repeated["strategy_contract"]["hypothesis"] = base + " Data costs remain included."
    assert validate.assess(repeated) == []
    unrelated = deepcopy(packet)
    suffix = " The experiment runs without look-ahead bias; no costs are excluded."
    unrelated["experiment"]["hypothesis"] = base + suffix
    unrelated["strategy_contract"]["hypothesis"] = base + suffix
    assert validate.assess(unrelated) == []
    for suffix in (
        " It does not ignore model costs.",
        " It does not exclude venue fees.",
        " They do not omit latency.",
        " It never ignores model costs.",
        " It never excluded data.",
        " It is never excluding data.",
        " Venue fees are not waived.",
        " Venue fees are never waived.",
        " It never drops data costs.",
        " Data costs are not counted twice.",
    ):
        candidate = deepcopy(packet)
        candidate["experiment"]["hypothesis"] = _NET_CLAUSE + suffix
        candidate["strategy_contract"]["hypothesis"] = _NET_CLAUSE + suffix
        assert validate.assess(candidate) == [], suffix
    bounded = (
        _NET_CLAUSE[:-1]
        + ", without look-ahead bias, verified using dated observations from the historical data."
    )
    aside = (
        _NET_CLAUSE[:-1]
        + ", without, for example, look-ahead bias in the historical data."
    )
    candidate = deepcopy(packet)
    candidate["experiment"]["hypothesis"] = bounded
    candidate["strategy_contract"]["hypothesis"] = bounded
    assert validate.assess(candidate) == []
    candidate = deepcopy(packet)
    candidate["experiment"]["hypothesis"] = aside
    candidate["strategy_contract"]["hypothesis"] = aside
    assert validate.assess(candidate) == []


_EIGHT_COSTS = (
    "transaction, latency, data, model, venue fees, spread/slippage, "
    "funding/collateral, and tax/accounting"
)
_NET_CLAUSE = f"Beats cash net of {_EIGHT_COSTS}."
SPLIT_NET_CASES = (
    (
        "hypothesis-net-partial.json",
        "Beats cash net of transaction and latency. Model, data, venue fees, "
        "spread/slippage, funding/collateral, tax/accounting are reported separately.",
    ),
    (
        "hypothesis-net-of-nothing.json",
        f"Beats cash gross. We also list {_EIGHT_COSTS}. Results are net of nothing.",
    ),
    (
        "hypothesis-before-costs.json",
        f"Beats cash before {_EIGHT_COSTS}. Net of fees reported elsewhere.",
    ),
    (
        "hypothesis-set-aside.json",
        f"Beats cash net of {_EIGHT_COSTS}, with data costs set aside.",
    ),
)
VERB_EXCLUSION_CASES = (
    (
        "hypothesis-excludes-venue-fees.json",
        _NET_CLAUSE + " The strategy excludes venue fees.",
    ),
    (
        "hypothesis-ignores-model.json",
        _NET_CLAUSE + " It ignores model costs.",
    ),
    (
        "hypothesis-except-after-hours.json",
        _NET_CLAUSE[:-1] + ", except after-hours transaction fees.",
    ),
    (
        "hypothesis-except-for-example.json",
        _NET_CLAUSE[:-1] + ", except, for example, transaction fees.",
    ),
    (
        "hypothesis-excluding-notably.json",
        _NET_CLAUSE[:-1] + ", excluding, notably, data.",
    ),
    (
        "hypothesis-no-longer-includes.json",
        _NET_CLAUSE + " It no longer includes venue fees.",
    ),
    (
        "hypothesis-drops-data.json",
        _NET_CLAUSE + " It drops data costs.",
    ),
    (
        "hypothesis-removes-model.json",
        _NET_CLAUSE + " It removes model costs.",
    ),
    (
        "hypothesis-waived-venue-fees.json",
        _NET_CLAUSE + " Venue fees are waived.",
    ),
    (
        "hypothesis-not-counted.json",
        _NET_CLAUSE + " Data costs are not counted.",
    ),
    (
        "hypothesis-save-for-data.json",
        _NET_CLAUSE[:-1] + ", save for data.",
    ),
    (
        "hypothesis-does-not-include-any.json",
        _NET_CLAUSE + " It does not include any venue fees.",
    ),
    (
        "hypothesis-does-not-include-the-data.json",
        _NET_CLAUSE + " It does not include the data costs.",
    ),
    (
        "hypothesis-not-counted-in-yield.json",
        _NET_CLAUSE + " Data costs are not counted in net yield.",
    ),
)
_VERB_INFLECTIONS = (
    _NET_CLAUSE + " The strategy will exclude latency.",
    _NET_CLAUSE + " It is excluding data.",
    _NET_CLAUSE + " It ignored model costs.",
    _NET_CLAUSE + " It omits data costs.",
    _NET_CLAUSE + " It is omitting data.",
)


def test_o4_rejects_a_net_claim_split_from_its_costs() -> None:
    validate = _load_o4_validate()
    packet = json.loads((O4 / "fixtures/candidate-packet.json").read_text(encoding="utf-8"))
    for filename, text in (*SPLIT_NET_CASES, *VERB_EXCLUSION_CASES):
        candidate = deepcopy(packet)
        candidate["experiment"]["hypothesis"] = text
        candidate["strategy_contract"]["hypothesis"] = text
        found = validate.assess(candidate)
        assert [item.code for item in found] == ["hypothesis_costs"]
        frozen = json.loads((O4 / "fixtures" / "negative" / filename).read_text(encoding="utf-8"))
        assert frozen["experiment"]["hypothesis"] == text
        assert frozen["strategy_contract"]["hypothesis"] == text
    for text in _VERB_INFLECTIONS:
        candidate = deepcopy(packet)
        candidate["experiment"]["hypothesis"] = text
        candidate["strategy_contract"]["hypothesis"] = text
        found = validate.assess(candidate)
        assert [item.code for item in found] == ["hypothesis_costs"]


def test_o4_rejects_measured_costs_on_unrun_experiment() -> None:
    validate = _load_o4_validate()
    packet = json.loads((O4 / "fixtures/candidate-packet.json").read_text(encoding="utf-8"))
    one = deepcopy(packet)
    one["experiment"]["costs"]["transaction"] = {
        "amount": "0.01",
        "status": "retained_measured",
        "unit": "owner_unset",
    }
    found = validate.assess(one)
    assert [item.code for item in found] == ["measured_costs_on_unrun_experiment"]
    assert "transaction" in found[0].message
    several = deepcopy(packet)
    for key in ("transaction", "latency", "data", "model"):
        several["experiment"]["costs"][key] = {
            "amount": "1.0",
            "status": "retained_measured",
            "unit": "owner_unset",
        }
    found = validate.assess(several)
    assert [item.code for item in found] == ["measured_costs_on_unrun_experiment"]


def test_o4_leaves_accepted_csl212_digests_unchanged() -> None:
    program = json.loads((ROOT / "docs" / "replacement" / "program.json").read_text(encoding="utf-8"))
    recorded = {item["path"]: item["sha256"] for item in program["contracts"]}
    assert recorded == ACCEPTED
    for path, digest in ACCEPTED.items():
        assert _sha256(ROOT / path) == digest
    outcome = next(item for item in program["outcomes"] if item["key"] == "O4")
    assert outcome["evidence"]["claimed"] == "researched"
    assert outcome["completion_gate_status"] == "pending_outcome_artifact"
    assert program["authority"]["live_authority_granted"] is False

"""Provider-free checker for the CSL-216 O4 candidate packet.

This is a candidate artifact checker. It does not trade, does not read
credentials, and does not use the network. A clean run means the checked-in
packet is internally consistent at maturity ``researched``. It is not an
outcome completion, not independent verification, and not qualification.
"""

from __future__ import annotations

import hashlib
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import jsonschema

ROOT = Path(__file__).resolve().parents[4]
O4 = ROOT / "docs" / "replacement" / "outcomes" / "O4"
CANDIDATE_COMMAND = "python -X utf8 -B docs/replacement/outcomes/O4/validate.py"

STRATEGY_ID = "xm_us_equity_kalshi_demo"
EXPERIMENT_ID = "xm-us-equity-kalshi-demo-001"
SEARCH_IDS = (
    "search_a81ed0bcb8b97e64d4f958ef59dde38a",
    "search_73f6ee6002538aa601e083ab7169dee4",
)
CITATION_URLS = (
    "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551",
    "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253",
    "https://doi.org/10.1016/j.knosys.2024.112477",
    "https://github.com/eslazarev/purged-cross-validation",
)
NOT_CLAIMED = (
    "human_decided",
    "contract_frozen",
    "prototyped",
    "implemented",
    "provider_free_verified",
    "demo_or_paper_qualified",
    "shadow_qualified",
    "limited_live_qualified",
    "release_qualified",
)
DECISION_IDS = (
    "spend",
    "credentials",
    "tax_legal",
    "architecture",
    "release",
    "live",
    "thresholds",
)
DECISION_OWNER_ROLES = {
    "spend": "reserved authority",
    "credentials": "reserved authority",
    "tax_legal": "owner",
    "architecture": "owner",
    "release": "release owner",
    "live": "owner",
    "thresholds": "owner",
}
COST_KEYS = (
    "transaction",
    "latency",
    "data",
    "model",
    "venue_fees",
    "spread_slippage",
    "funding_collateral",
    "tax_accounting",
)
METRIC_IDS = (
    "deflated_sharpe_ratio",
    "probability_of_backtest_overfitting",
    "drawdown",
    "uncertainty",
    "net_yield",
    "capital_use",
)
METRIC_INPUTS = {
    "deflated_sharpe_ratio": (
        "selected_sharpe",
        "trial_count",
        "trial_sharpe_variance",
        "skewness",
        "excess_kurtosis",
        "observation_count",
    ),
    "probability_of_backtest_overfitting": (
        "variant_return_matrix",
        "cscv_splits",
        "in_sample_winner_out_of_sample_rank",
    ),
    "drawdown": ("peak_to_trough_net_loss", "drawdown_duration"),
    "uncertainty": ("estimator_owner_unset", "net_result_series"),
    "net_yield": (
        "net_result_series",
        "matched_cash_baseline_series",
        "retained_cost_amounts",
    ),
    "capital_use": ("capital_employed_series", "peak_capital_employed"),
}
METRIC_CITATIONS = {
    "deflated_sharpe_ratio": CITATION_URLS[0],
    "probability_of_backtest_overfitting": CITATION_URLS[1],
}
ACCEPTED_CONTRACTS = {
    "docs/replacement/SUCCESSOR-PRODUCT.md":
        "6e5f0d0cad102077fd058aac4970ac0eb31fd6f6e952f948c43edaa9fe0c919e",
    "docs/replacement/ISSUE-CONTRACT.md":
        "f1fb50dab4b166ca476698fc3974d6a1c2003b16df1d89ff8930060ad04b5094",
    "docs/replacement/MONTH-PROGRAM.md":
        "f28b75244a4eef5e9d3095349d6088bcd08e09547ab8e0fbc03ba52a119f3001",
    "docs/replacement/SCOPE-AUDIT.md":
        "d23dcc769e743d199ae4142f0e20ad5feebfe310c71d9f57fdd9d647c230db8c",
}
SECTION_SCHEMAS = {
    "strategy_contract": "strategy-contract.schema.json",
    "experiment": "experiment.schema.json",
    "metric_registry": "metric-registry.schema.json",
    "promotion_protocol": "promotion-protocol.schema.json",
    "candidate_queue": "candidate-queue.schema.json",
    "recorded_empty_outcome": "recorded-empty-outcome.schema.json",
    "research": "research.schema.json",
}
CONTROL_ORDER = (
    "overlapping_populations",
    "dropped_costs",
    "dropped_empty_outcome",
    "parameter_variants_as_distinct_strategies",
    "decision_owner_role",
    "leakage_controls_satisfied",
    "variant_configuration",
    "unbound_population",
    "metric_registry",
    "measured_costs_on_unrun_experiment",
    "promotion_stage_authorized",
    "promotion_gates",
    "missing_promotion_rung",
    "promotion_gates",
    "hypothesis_costs",
)
REQUIRED_STAGE_GATES = {
    "replay": {
        "entry_criteria": (
            "packet_and_manifest_digests_pinned_to_separately_accepted_preresult_receipt",
            "independent_verifier_rerun_authorized",
            "unit_property_checks_recorded",
        ),
        "entry_evidence": ("preresult_receipt", "verifier_rerun_record", "unit_property_test_record"),
        "exit_criteria": ("replay_output_recorded_and_matched_to_receipt_digests",),
    },
    "simulation": {
        "entry_criteria": (
            "replay_exit_gates_met",
            "simulation_model_costs_thresholds_and_coverage_pinned_before_results",
        ),
        "entry_evidence": ("replay_output_record", "preresult_receipt"),
        "exit_criteria": (
            "simulation_output_recorded_with_all_costs",
            "simulation_meets_preresult_thresholds_and_coverage",
        ),
    },
    "demo": {
        "entry_criteria": (
            "simulation_exit_gates_met",
            "official_demo_venue_profile_and_nonlive_scope_accepted_before_results",
            "demo_thresholds_and_coverage_pinned_before_results",
        ),
        "entry_evidence": (
            "simulation_output_record",
            "nonlive_scope_receipt",
            "venue_conformance_record",
            "preresult_receipt",
        ),
        "exit_criteria": (
            "demo_output_recorded",
            "demo_reconciliation_and_recovery_meet_preresult_thresholds_and_coverage",
        ),
    },
    "shadow": {
        "entry_criteria": (
            "demo_exit_gates_met",
            "live_data_acquisition_scope_accepted_before_observation",
            "observation_only_no_order_authority",
        ),
        "entry_evidence": ("demo_output_record", "data_scope_receipt", "preresult_receipt"),
        "exit_criteria": (
            "shadow_output_recorded_with_data_gaps_latency_and_costs",
            "shadow_meets_preresult_thresholds_and_coverage",
        ),
    },
    "paper": {
        "entry_criteria": (
            "shadow_exit_gates_met",
            "paper_or_venue_equivalent_execution_scope_accepted_before_results",
            "current_risk_mode_approval_and_gateway_path_preserved",
        ),
        "entry_evidence": ("shadow_output_record", "nonlive_scope_receipt", "preresult_receipt"),
        "exit_criteria": (
            "paper_or_venue_equivalent_execution_output_recorded",
            "paper_reconciliation_recovery_and_economics_meet_preresult_thresholds_and_coverage",
            "outcome_routed_to_release_owner_for_decision",
        ),
    },
}


class QualificationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class Failure:
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(obj: Any) -> str:
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_json(path: Path) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        keys = [key for key, _ in items]
        if len(keys) != len(set(keys)):
            raise QualificationError("duplicate_key", f"duplicate object key in {path}")
        return dict(items)

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    except QualificationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QualificationError("invalid_json", f"{path}: {exc}") from exc


def _inside(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise QualificationError("path", f"{path} escapes {root}")
    return resolved


def _repo_path(repo: Path, relative: str) -> Path:
    parts = Path(relative).parts
    if not relative or Path(relative).is_absolute() or ".." in parts:
        raise QualificationError("path", f"refusing evidence path {relative}")
    return _inside(repo, repo / relative)


def _schema_failures(instance: Any, schema: dict[str, Any], name: str) -> list[Failure]:
    validator = jsonschema.Draft202012Validator(schema)
    failures: list[Failure] = []
    for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.path)):
        failures.append(Failure("schema", f"{name}: {error.message}"))
    return failures


def _section_failures(packet: dict[str, Any]) -> list[Failure]:
    failures: list[Failure] = []
    envelope = _load_json(O4 / "schemas" / "envelope.schema.json")
    failures.extend(_schema_failures(packet, envelope, "envelope"))
    for field, filename in SECTION_SCHEMAS.items():
        schema = _load_json(O4 / "schemas" / filename)
        failures.extend(_schema_failures(packet.get(field), schema, field))
    return failures


def _fail_if(failures: list[Failure], condition: bool, code: str, message: str) -> None:
    if condition:
        failures.append(Failure(code, message))


def _semantic_failures(packet: dict[str, Any], repo: Path) -> list[Failure]:
    failures: list[Failure] = []
    _claims(packet, failures)
    _populations(packet, failures)
    _leakage(packet, failures)
    _costs(packet, failures)
    _hypotheses(packet, failures)
    _variants(packet, failures)
    _empty_outcome(packet, repo, failures)
    _metrics(packet, failures)
    _queue(packet, repo, failures)
    _decisions(packet, failures)
    _promotion(packet, failures)
    _contracts(packet, repo, failures)
    return failures


def _claims(packet: dict[str, Any], failures: list[Failure]) -> None:
    _fail_if(
        failures,
        packet.get("maturity") != "researched" or tuple(packet.get("not_claimed", ())) != NOT_CLAIMED,
        "maturity_overclaim",
        "maturity must stay researched and the higher rungs must stay unclaimed",
    )
    flags = (
        packet.get("empirical_pass_claimed"),
        packet.get("contract_frozen_claimed"),
        packet.get("qualification_claimed"),
        packet.get("live_authority_granted"),
        packet.get("credentials_present"),
        packet.get("data_acquisition_authorized"),
    )
    _fail_if(failures, any(flag is not False for flag in flags), "maturity_overclaim", "a forbidden claim is set")


def _populations(packet: dict[str, Any], failures: list[Failure]) -> None:
    population = packet["experiment"]["population"]
    tuning = population["tuning_member_ids"]
    evaluation = population["evaluation_member_ids"]
    overlap = sorted(set(tuning) & set(evaluation))
    if overlap:
        failures.append(
            Failure(
                "overlapping_populations",
                "tuning and evaluation populations intersect at " + ", ".join(overlap),
            )
        )
    leaked = [item for item in [*tuning, *evaluation] if not str(item).startswith("illustrative:")]
    _fail_if(
        failures,
        bool(leaked) or population.get("member_ids_are_acquired_observations") is not False,
        "acquired_population",
        "this design has no acquired population",
    )
    members = {
        "tuning_member_ids": tuning,
        "evaluation_member_ids": evaluation,
    }
    recorded = population.get("selection_manifest", {}).get("members_sha256")
    _fail_if(
        failures,
        recorded != _canonical_sha256(members),
        "unbound_population",
        "population member ids do not match the recorded selection-manifest digest",
    )


def _leakage(packet: dict[str, Any], failures: list[Failure]) -> None:
    controls = packet["experiment"]["leakage_controls"]
    satisfied = [
        name
        for name in ("purge", "embargo")
        if controls[name].get("status") != "not_run" or controls[name].get("evidence") is not None
    ]
    _fail_if(
        failures,
        bool(satisfied),
        "leakage_controls_satisfied",
        "an unrun leakage control reads as satisfied; a not_run control cannot satisfy qualification: "
        + ", ".join(satisfied),
    )


def _cost_ok(cost: dict[str, Any]) -> bool:
    status = cost.get("status")
    amount = cost.get("amount")
    if status == "retained_unmeasured":
        return amount is None
    if status == "retained_measured":
        try:
            return isinstance(amount, str) and Decimal(amount).is_finite()
        except InvalidOperation:
            return False
    return False


def _costs(packet: dict[str, Any], failures: list[Failure]) -> None:
    costs = packet["experiment"]["costs"]
    bad = [key for key in COST_KEYS if key not in costs or not _cost_ok(costs[key])]
    if bad:
        failures.append(Failure("dropped_costs", "required cost is dropped or missing: " + ", ".join(bad)))
    measured = [key for key in COST_KEYS if costs.get(key, {}).get("status") == "retained_measured"]
    _fail_if(
        failures,
        packet["experiment"].get("status") == "not_run" and bool(measured),
        "measured_costs_on_unrun_experiment",
        "an unrun experiment cannot carry measured costs: " + ", ".join(measured),
    )


# Equivalent names for one retained cost, longest first. Hyphens and slashes
# fold to spaces, so "venue-fee" and "spread/slippage" match the phrase forms.
_COST_PHRASES: dict[str, tuple[str, ...]] = {
    "transaction": ("transaction",),
    "latency": ("latency",),
    "data": ("data",),
    "model": ("model",),
    "venue_fees": ("venue fees", "venue fee"),
    "spread_slippage": ("spread and slippage", "spread slippage"),
    "funding_collateral": ("funding and collateral", "funding collateral"),
    "tax_accounting": ("tax and accounting", "tax accounting"),
}
# A net-of claim. Any order of the eight names satisfies it. "after" plus every
# cost in one clause is the other accepted form.
_NET_PHRASES = (
    "net of",
    "after all eight retained costs",
    "after the eight retained costs",
    "after all retained costs",
)
# These deny an exclusion. They are not themselves exclusions.
_NEGATED_EXCLUSIONS = (
    "no costs are excluded",
    "no cost is excluded",
    "costs are not excluded",
    "are not excluded",
    "is not excluded",
    "not excluded",
    "none are excluded",
    "none is excluded",
    "nothing is excluded",
    "without excluding",
    "no exclusion",
    "not omitted",
    "nothing is omitted",
    "not ignored",
)
# A marker that drops the next named cost ("without venue fees", "except data").
_FORWARD_MARKERS = (
    "does not include",
    "do not include",
    "not including",
    "other than",
    "apart from",
    "aside from",
    "not retained",
    "not count",
    "not apply",
    "not part",
    "leave out",
    "left out",
    "excluding",
    "except",
    "without",
    "omitting",
    "ignoring",
)
# Past-participle predicates ("are excluded"). Gerunds ("excluding") stay forward markers.
_PREDICATE_TOKENS = {"excluded": "exclud", "omitted": "omitt", "ignored": "ignor"}
_FORWARD_WINDOW = 8


def _fold_hypothesis(text: str) -> str:
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text.casefold()).split())


def _hypothesis_clauses(text: str) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    for char in text:
        if char in ".;!?\n":
            chunks.append("".join(current))
            current = []
        else:
            current.append(char)
    chunks.append("".join(current))
    clauses: list[str] = []
    for chunk in chunks:
        lower = chunk.casefold()
        start = 0
        while True:
            at = lower.find(" but ", start)
            if at < 0:
                clauses.append(chunk[start:])
                break
            clauses.append(chunk[start:at])
            start = at + len(" but ")
    return [part for part in clauses if part.strip()]


def _phrase_index() -> list[tuple[tuple[str, ...], str]]:
    ranked: list[tuple[int, int, tuple[str, ...], str]] = []
    for key, phrases in _COST_PHRASES.items():
        for phrase in phrases:
            parts = tuple(phrase.split())
            ranked.append((len(parts), len(phrase), parts, key))
    ranked.sort(key=lambda item: (-item[0], -item[1]))
    return [(parts, key) for _, _, parts, key in ranked]


def _cost_spans(tokens: list[str]) -> list[tuple[str, int, int]]:
    spans: list[tuple[str, int, int]] = []
    index = 0
    phrases = _phrase_index()
    while index < len(tokens):
        matched = False
        for parts, key in phrases:
            width = len(parts)
            if tokens[index : index + width] == list(parts):
                spans.append((key, index, index + width))
                index += width
                matched = True
                break
        if not matched:
            index += 1
    return spans


def _strip_negated_exclusions(folded: str) -> str:
    padded = f" {folded} "
    for snippet in sorted(_NEGATED_EXCLUSIONS, key=len, reverse=True):
        padded = padded.replace(f" {snippet} ", " ")
    return " ".join(padded.split())


def _predicate_stem(folded: str) -> str | None:
    for token in _strip_negated_exclusions(folded).split():
        label = _PREDICATE_TOKENS.get(token)
        if label is not None:
            return label
    return None


def _marker_label(marker: str) -> str:
    head = marker.split()[0]
    if head.startswith("exclud"):
        return "exclud"
    if head.startswith("omitt"):
        return "omitt"
    if head.startswith("ignor"):
        return "ignor"
    return marker


def _forward_excluded(tokens: list[str]) -> list[tuple[str, str]]:
    scanned = _strip_negated_exclusions(" ".join(tokens)).split()
    found: list[tuple[str, str]] = []
    markers = sorted(_FORWARD_MARKERS, key=len, reverse=True)
    index = 0
    while index < len(scanned):
        matched: str | None = None
        for marker in markers:
            parts = marker.split()
            if scanned[index : index + len(parts)] == parts:
                matched = marker
                break
        if matched is None:
            index += 1
            continue
        width = len(matched.split())
        window = scanned[index + width : index + width + _FORWARD_WINDOW]
        for key, _start, _end in _cost_spans(window):
            found.append((key, _marker_label(matched)))
        index += width
    return found


def _hypothesis_defects(name: str, text: object) -> list[str]:
    if not isinstance(text, str):
        return [f"{name}.{key}" for key in COST_KEYS]
    included: set[str] = set()
    excluded: list[tuple[str, str]] = []
    clause_folds: list[str] = []
    for clause in _hypothesis_clauses(text):
        folded = _fold_hypothesis(clause)
        if not folded:
            continue
        clause_folds.append(folded)
        tokens = folded.split()
        spans = _cost_spans(tokens)
        keys = {key for key, _start, _end in spans}
        stem = _predicate_stem(folded)
        if stem is not None and keys:
            for key in keys:
                excluded.append((key, stem))
            continue
        forward = _forward_excluded(tokens)
        excluded_keys = {key for key, _label in forward}
        excluded.extend(forward)
        included.update(key for key in keys if key not in excluded_keys)
    folded_all = " ".join(clause_folds)
    affirms = any(phrase in folded_all for phrase in _NET_PHRASES) or any(
        "after" in clause.split() and set(COST_KEYS) <= {key for key, _start, _end in _cost_spans(clause.split())}
        for clause in clause_folds
    )
    defects: list[str] = []
    if not affirms:
        defects.append(f"{name}.inclusion")
    for key in COST_KEYS:
        if key not in included:
            defects.append(f"{name}.{key}")
    seen: set[str] = set()
    for _key, label in excluded:
        if label not in seen:
            defects.append(f"{name}.exclusion:{label}")
            seen.add(label)
    return defects


def _hypotheses(packet: dict[str, Any], failures: list[Failure]) -> None:
    sections = (
        ("experiment", packet.get("experiment")),
        ("strategy_contract", packet.get("strategy_contract")),
    )
    defects: list[str] = []
    for name, section in sections:
        text = section.get("hypothesis") if isinstance(section, dict) else None
        defects.extend(_hypothesis_defects(name, text))
    _fail_if(
        failures,
        bool(defects),
        "hypothesis_costs",
        "hypothesis does not retain every cost: " + ", ".join(defects),
    )


def _variants(packet: dict[str, Any], failures: list[Failure]) -> None:
    contract = packet["strategy_contract"]
    count = len(contract["parameter_variants"])
    distinct = contract["counted_as_distinct_strategies"] is not False or contract["distinct_strategy_count"] != 1
    if distinct:
        failures.append(
            Failure(
                "parameter_variants_as_distinct_strategies",
                f"{count} parameter variants are counted as distinct strategies",
            )
        )
        return
    _fail_if(
        failures,
        contract["multiple_testing_trial_count"] != count,
        "trial_count",
        "multiple-testing trial count must include every parameter variant",
    )
    family = contract["strategy_id"]
    mismatched = [
        item["variant_id"]
        for item in contract["parameter_variants"]
        if item["family_id"] != family or item["counts_as_distinct_strategy"] is not False
    ]
    _fail_if(
        failures,
        bool(mismatched),
        "parameter_variants_as_distinct_strategies",
        "a parameter variant is marked as its own strategy: " + ", ".join(mismatched),
    )
    rebound = [
        item["variant_id"]
        for item in contract["parameter_variants"]
        if item.get("configuration_sha256") != _canonical_sha256(item.get("configuration"))
    ]
    _fail_if(
        failures,
        bool(rebound),
        "variant_configuration",
        "a parameter variant's configuration digest does not match its recorded configuration: "
        + ", ".join(rebound),
    )


def _empty_outcome(packet: dict[str, Any], repo: Path, failures: list[Failure]) -> None:
    recorded = packet["recorded_empty_outcome"]
    dropped = (
        recorded.get("retained") is not True
        or recorded.get("qualify_count") != 0
        or recorded.get("empirical_pass_claimed") is not False
    )
    if dropped:
        failures.append(Failure("dropped_empty_outcome", "recorded zero-qualify outcome is not retained"))
        return
    screens = _load_json(_repo_path(repo, recorded["screens_path"]))
    evaluation = _load_json(_repo_path(repo, recorded["evaluation_path"]))
    screens_hash = _sha256(_repo_path(repo, recorded["screens_path"]))
    evaluation_hash = _sha256(_repo_path(repo, recorded["evaluation_path"]))
    latest = next((item for item in screens["screens"] if item["id"] == recorded["latest_screen_id"]), None)
    intact = (
        screens_hash == recorded["screens_sha256"]
        and evaluation_hash == recorded["evaluation_sha256"]
        and screens.get("qualify_count") == 0
        and evaluation.get("qualify_count") == 0
        and evaluation.get("empirical_pass_claimed") is False
        and screens.get("latest_screen_id") == recorded["latest_screen_id"]
        and latest is not None
        and latest.get("gate_result") == recorded["latest_gate_result"]
        and latest.get("counts") == recorded["latest_counts"]
        and latest.get("meets_cohort_gate") is False
    )
    _fail_if(failures, not intact, "evidence_pin", "recorded empty screen or evaluation pin does not match the file")


def _metrics(packet: dict[str, Any], failures: list[Failure]) -> None:
    measures = packet["metric_registry"]["measures"]
    by_id = {item["id"]: item for item in measures}
    _fail_if(
        failures,
        tuple(item["id"] for item in measures) != METRIC_IDS or set(by_id) != set(METRIC_IDS),
        "metric_registry",
        "metric registry must keep deflated Sharpe, backtest-overfitting probability, drawdown, and uncertainty",
    )
    for metric_id, url in METRIC_CITATIONS.items():
        measure = by_id.get(metric_id)
        if measure is None or measure.get("citation") != url or measure.get("computed") is not False:
            failures.append(Failure("metric_registry", f"{metric_id} is missing its citation or is marked computed"))
    for metric_id, required in METRIC_INPUTS.items():
        measure = by_id.get(metric_id)
        if measure is not None and set(measure.get("inputs_retained", ())) != set(required):
            failures.append(Failure("metric_registry", f"{metric_id} does not retain its required inputs"))
    citations = {item["url"] for item in packet["research"]["citations"]}
    searches = tuple(packet["research"]["parallel_search_ids"])
    references = tuple(packet["metric_registry"]["methodology_references"])
    _fail_if(
        failures,
        citations != set(CITATION_URLS) or searches != SEARCH_IDS or references != CITATION_URLS,
        "research_citations",
        "research citations must be the embedded Parallel results",
    )


def _queue(packet: dict[str, Any], repo: Path, failures: list[Failure]) -> None:
    recorded = packet["recorded_empty_outcome"]
    if recorded.get("retained") is not True:
        return
    evaluation = _load_json(_repo_path(repo, recorded["evaluation_path"]))
    entries = packet["candidate_queue"]["entries"]
    historical = [item for item in entries if item["source"] == "rw28_evaluation"]
    design = [item for item in entries if item["source"] == "o4_design"]
    expected = [
        (item["candidate_id"], item["verdict"], item["blocking_rule"])
        for item in evaluation["results"]
    ]
    actual = [(item["strategy_id"], item["verdict"], item["blocking_rule"]) for item in historical]
    variant_ids = {item["variant_id"] for item in packet["strategy_contract"]["parameter_variants"]}
    design_ok = (
        len(design) == 1
        and design[0]["strategy_id"] == STRATEGY_ID
        and design[0]["verdict"] == "not_run"
        and design[0]["blocking_rule"] == "design_only_not_measured"
        and design[0]["qualified"] is False
        and design[0]["activation_authorized"] is False
    )
    _fail_if(
        failures,
        actual != expected or not design_ok or any(item["qualified"] is not False for item in entries),
        "candidate_queue",
        "candidate queue must retain the zero-qualify screen verdicts and the unrun design",
    )
    _fail_if(
        failures,
        any(item["strategy_id"] in variant_ids for item in entries),
        "parameter_variants_as_distinct_strategies",
        "a parameter variant is queued as its own strategy",
    )


def _decisions(packet: dict[str, Any], failures: list[Failure]) -> None:
    rows = packet["unresolved_decisions"]
    ids = tuple(item["id"] for item in rows)
    decided = [
        item["id"]
        for item in rows
        if item.get("status") != "undecided" or item.get("selected_option") is not None
    ]
    misrouted = [
        item["id"]
        for item in rows
        if item.get("owner") != DECISION_OWNER_ROLES.get(item["id"])
    ]
    _fail_if(
        failures,
        ids != DECISION_IDS or bool(decided),
        "human_decision_taken",
        "an owner decision is missing or has been selected: " + ", ".join(decided or list(ids)),
    )
    _fail_if(
        failures,
        bool(misrouted),
        "decision_owner_role",
        "an unresolved decision routes to a person instead of its role: " + ", ".join(misrouted),
    )


def _promotion(packet: dict[str, Any], failures: list[Failure]) -> None:
    protocol = packet["promotion_protocol"]
    authorized = [stage["id"] for stage in protocol["stages"] if stage.get("authorized") is not False]
    _fail_if(
        failures,
        bool(authorized),
        "promotion_stage_authorized",
        "a promotion stage is marked authorized: " + ", ".join(authorized),
    )
    missing = []
    for stage in protocol["stages"]:
        required = REQUIRED_STAGE_GATES.get(stage["id"])
        if required is None:
            continue
        entry = stage.get("entry_gates", {})
        exit_ = stage.get("exit_gates", {})
        if (
            not set(required["entry_criteria"]) <= set(entry.get("criteria", ()))
            or not set(required["entry_evidence"]) <= set(entry.get("required_evidence", ()))
            or not set(required["exit_criteria"]) <= set(exit_.get("criteria", ()))
        ):
            missing.append(stage["id"])
    _fail_if(
        failures,
        bool(missing),
        "promotion_gates",
        "a promotion stage is missing a required gate criterion or evidence: " + ", ".join(missing),
    )


def _contracts(packet: dict[str, Any], repo: Path, failures: list[Failure]) -> None:
    program = _load_json(repo / "docs" / "replacement" / "program.json")
    o4 = next(item for item in program["outcomes"] if item["key"] == "O4")
    month = ACCEPTED_CONTRACTS["docs/replacement/MONTH-PROGRAM.md"]
    expected_dependencies = (
        ("O1", "docs/replacement/MONTH-PROGRAM.md#o1"),
        ("O2", "docs/replacement/MONTH-PROGRAM.md#o2"),
        ("O3", "docs/replacement/MONTH-PROGRAM.md#o3"),
    )
    dependencies = tuple((item["outcome"], item["fragment"]) for item in packet["dependencies"])
    dependency_ok = dependencies == expected_dependencies and all(
        item["accepted_digest"] == month and item["status"] == "accepted_human_digest_receipt"
        for item in packet["dependencies"]
    )
    digests = packet["accepted_contract_digests"]
    digest_ok = digests == ACCEPTED_CONTRACTS and {
        item["path"]: item["sha256"] for item in program["contracts"]
    } == ACCEPTED_CONTRACTS
    file_ok = all(_sha256(repo / path) == digest for path, digest in ACCEPTED_CONTRACTS.items())
    registry_ok = (
        program.get("source_commit") == packet.get("registry_source_commit")
        and program["authority"]["live_authority_granted"] is False
        and o4["evidence"]["claimed"] == "researched"
        and o4["completion_gate_status"] == "pending_outcome_artifact"
        and "contract_frozen" in o4["evidence"]["not_claimed"]
    )
    _fail_if(
        failures,
        not (digest_ok and file_ok and registry_ok and dependency_ok),
        "accepted_contract_drift",
        "accepted CSL-212 documents or the O4 registry maturity changed",
    )


def assess(packet: dict[str, Any], repo: Path | None = None) -> list[Failure]:
    repo = ROOT if repo is None else repo
    protocol = packet.get("promotion_protocol")
    stages = protocol.get("stages") if isinstance(protocol, dict) else None
    if isinstance(stages, list) and all(isinstance(stage, dict) for stage in stages):
        missing = [name for name in REQUIRED_STAGE_GATES if not any(stage.get("id") == name for stage in stages)]
        if missing:
            return [Failure("missing_promotion_rung", "required non-live promotion rungs missing: " + ", ".join(missing))]
    schema_errors = _section_failures(packet)
    if schema_errors:
        return schema_errors
    return _semantic_failures(packet, repo)


def _manifest_paths(manifest: dict[str, Any]) -> list[dict[str, str]]:
    rows = list(manifest["files"])
    if [row["path"] for row in rows] != sorted(row["path"] for row in rows):
        raise QualificationError("manifest", "manifest files must be sorted by path")
    return rows


def _check_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("maturity_claimed") != "researched":
        raise QualificationError("maturity_overclaim", "manifest maturity is not researched")
    if (
        manifest.get("qualification_claimed") is not False
        or manifest.get("empirical_pass_claimed") is not False
        or manifest.get("contract_frozen_claimed") is not False
    ):
        raise QualificationError(
            "maturity_overclaim", "manifest claims qualification, an empirical pass, or a frozen contract"
        )
    expected = [item["expect"] for item in manifest["negative_controls"]]
    if tuple(expected) != CONTROL_ORDER:
        raise QualificationError("negative_control", "negative-control manifest order changed")
    rows = _manifest_paths(manifest)
    paths = [row["path"] for row in rows]
    required = {
        "PROTOCOL.md",
        "validate.py",
        manifest["candidate_packet"],
        *(f"schemas/{name}" for name in SECTION_SCHEMAS.values()),
        "schemas/envelope.schema.json",
        *(item["path"] for item in manifest["negative_controls"]),
    }
    if len(set(paths)) != len(paths) or set(paths) != required:
        raise QualificationError("manifest", "manifest files must list each pinned artifact exactly once")
    for row in rows:
        path = _inside(O4, O4 / row["path"])
        if _sha256(path) != row["sha256"]:
            raise QualificationError("manifest", f"frozen digest mismatch for {row['path']}")


def run(repo: Path | None = None) -> list[str]:
    repo = ROOT if repo is None else repo
    manifest = _load_json(O4 / "manifest.json")
    _check_manifest(manifest)
    packet = _load_json(O4 / manifest["candidate_packet"])
    failures = assess(packet, repo)
    if failures:
        detail = "; ".join(f"{item.code}: {item.message}" for item in failures)
        raise QualificationError("candidate_packet", detail)
    lines: list[str] = []
    for item in manifest["negative_controls"]:
        negative = _load_json(_inside(O4, O4 / item["path"]))
        found = assess(negative, repo)
        codes = [failure.code for failure in found]
        if codes != [item["expect"]]:
            raise QualificationError(
                "negative_control",
                f"{item['path']} failed as {codes}, expected {item['expect']}",
            )
        lines.append(f"O4 negative control FAIL {found[0].code}: {found[0].message}")
    lines.append(
        "O4 candidate packet checked at maturity researched; qualify_count=0; promotion_authorized=false"
    )
    return lines


def main() -> int:
    try:
        for line in run(ROOT):
            print(line)
    except QualificationError as exc:
        print(f"O4 candidate packet rejected {exc.code}: {exc.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

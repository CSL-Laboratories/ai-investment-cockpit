"""Offline, hash-pinned diagnosis of held filings; no production or authority writes.

The process-local regex ablations isolate causes. They are deliberately NOT a
proposed parser, a safety-reviewed repair, or a selection/precision measurement.
Run from the repository root; redirect stdout to retain the JSON observations.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "app"))

from strategies import merger_semantics as m


def checked(path: Path, digest: str) -> bytes:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError(f"Pinned input changed: {path}")
    return raw


def dates(text: str) -> dict:
    result = {}
    for field, pattern in (
        ("expected_resolution_date", m._EXPECTED_DATE),
        ("outside_date", m._OUTSIDE_DATE),
    ):
        item = {"raw_matches": pattern.findall(text), "date": None}
        try:
            value = m._unique_match(pattern, text, field=field)
            item["date"] = m._source_date(value, field=field).isoformat()
        except m.SemanticEvidenceUnavailable as exc:
            item["refusal"] = str(exc)
        result[field] = item
    return result


def observe(text: str, *, amended: bool = False) -> dict:
    result = {
        "cash_raw": m._CASH.findall(text),
        "cash_asserted": [x.group(1) for x in m._assertion_matches(m._CASH, text)],
        "stock_matches": [x.group(0) for x in m._STOCK_CONSIDERATION.finditer(text)],
        "not_definitive_matches": [x.group(0) for x in m._NOT_DEFINITIVE.finditer(text)],
        "dates": dates(text),
        "label": None,
        "cash_strategy_eligible": False,
    }
    try:
        result["label"] = m._derive_eligibility(text, amended=amended)
        m._require_strategy_eligible(result["label"])
        result["cash_strategy_eligible"] = True
    except m.SemanticEvidenceUnavailable as exc:
        result["refusal"] = str(exc)
    return result


def relevant_spans(text: str, symbol: str) -> list[dict]:
    amount = {"SAFT": "105.00", "FSEA": "17.25", "SLP": "18.50", "CCO": "2.43"}[symbol]
    result = []
    for match in re.finditer(r"(?:[^.!?]|\.(?=\d))+", text):
        sentence = match.group().strip()
        cash = bool(re.search(r"\$\s*" + re.escape(amount) + r"\b", sentence))
        calendar = bool(re.search(
            r"\b(?:outside|end|termination)\s+date\b|"
            r"(?:merger|closing).{0,50}expected|"
            r"merger.{0,80}(?:not\s+consummated|not\s+been\s+consummated)", sentence,
        ))
        if cash or calendar:
            result.append({
                "kind": "cash" if cash else "calendar_context",
                "normalized_start": match.start(),
                "normalized_end": match.end(),
                "text": match.group(),
            })
    return result


def main() -> None:
    pin = json.loads((HERE / "inputs.json").read_text())
    evidence = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(pin["evidence_root"])
    for source in pin["code"]:
        checked(REPO / source["path"], source["sha256"])
    for source in pin["metadata"]:
        checked(evidence / source["path"], source["sha256"])
    texts = {}
    observations = []
    for source in pin["filings"]:
        raw = checked(evidence / source["path"], source["sha256"])
        text = m._text(raw)
        texts[source["accession"]] = text
        observation = observe(text)
        # This dated survey pins refusals, not a hypothetical future repair.
        assert all(x["date"] is None for x in observation["dates"].values())
        observations.append({
            "symbol": source["symbol"], "accession": source["accession"],
            "normalized_sha256": hashlib.sha256(text.encode()).hexdigest(),
            **observation,
            "source_spans": relevant_spans(text, source["symbol"]),
        })

    original_cash = m._CASH
    original_stock = m._STOCK_CONSIDERATION
    old_tail = (
        r"(?:in\s+cash\s+per\s+(?:each\s+)?share|"
        r"per\s+(?:each\s+)?share(?:\s+|,\s*)(?:net\s+)?in\s+cash)"
    )
    assert original_cash.pattern.endswith(old_tail)
    tail_only = re.compile(
        original_cash.pattern[:-len(old_tail)]
        + r"(?:in\s+cash(?=\s*[,.;])|" + old_tail[3:]
    )
    head = original_cash.pattern[:original_cash.pattern.index(r"(?:(?:will|shall)")]
    cco_only = re.compile(
        head + r"(?:will|shall)\s+be\s+automatically\s+cancell?ed,\s+extinguished\s+and\s+"
        r"converted\s+into\s+the\s+right\s+to\s+receive\s+cash\s+in\s+an\s+amount\s+"
        r"equal\s+to\s+\$\s*(\d+(?:\.\d+)?)(?=\s*,\s*without\s+interest)"
    )
    without_bare_and = re.compile(original_stock.pattern.replace(
        "(?:receive|plus|and)", "(?:receive|plus)", 1,
    ))
    ablations = []
    bait_prefix = "The company entered into an agreement and plan of merger. Nasdaq. "
    shortcut_probes = {
        "cash_tail_only": bait_prefix + (
            "Each share of common stock will be converted into the right to receive "
            "a distribution; employee options receive $17.25 in cash, without interest."
        ),
        "stock_without_bare_and": bait_prefix + (
            "Each share of common stock will receive $105 in cash per share, without interest "
            "and subject to applicable withholding taxes, paid after the transfer agent "
            "validates the holder's account, and 0.5 shares of parent common stock."
        ),
    }
    shortcut_baselines = {
        name: observe(m._text(text.encode())) for name, text in shortcut_probes.items()
    }
    assert all(not x["cash_strategy_eligible"] for x in shortcut_baselines.values())
    try:
        for name, cash, stock in (
            ("cash_tail_only", tail_only, original_stock),
            ("cco_observed_conversion_only", cco_only, original_stock),
            ("stock_without_bare_and", original_cash, without_bare_and),
        ):
            m._CASH, m._STOCK_CONSIDERATION = cash, stock
            records = []
            for source in pin["filings"]:
                text = texts[source["accession"]]
                item = observe(text)
                if not item["cash_asserted"]:
                    continue
                record = {"symbol": source["symbol"], "accession": source["accession"], **item}
                head_info = pin["last_screen_heads"][source["symbol"]]
                supported = head_info.get("supported_2026", [])
                accessions = [x["accession"] for x in supported]
                if source["accession"] in accessions:
                    ordered = list(reversed(accessions[:accessions.index(source["accession"]) + 1]))
                    combined = m._combined_source_text([texts[x] for x in ordered])
                    amended = any(m._MERGER_AMENDMENT.search(texts[x]) for x in ordered)
                    record["unsigned_source_set_diagnostic"] = {
                        "source_accessions": ordered,
                        **observe(combined, amended=amended),
                    }
                records.append(record)
            ablation = {
                "name": name, "production_safe": False,
                "cash_regex": cash.pattern, "stock_regex": stock.pattern,
                "filings_with_asserted_cash": records,
            }
            if name in shortcut_probes:
                result = observe(m._text(shortcut_probes[name].encode()))
                assert result["cash_strategy_eligible"]
                ablation["known_unsafe_synthetic_counterexample"] = {
                    "text": shortcut_probes[name], "main_refuses": shortcut_baselines[name],
                    "ablation_unsafely_accepts": result,
                }
            ablations.append(ablation)
    finally:
        m._CASH, m._STOCK_CONSIDERATION = original_cash, original_stock

    # Existing BHF quote plus independent synthetic controls; never source authority.
    excerpt_path = REPO / pin["existing_excerpts"]["path"]
    excerpts = json.loads(checked(excerpt_path, pin["existing_excerpts"]["sha256"]))
    bhf = next(x["text"] for x in excerpts["entries"] if x["id"] == "bhf_conditional_year_only")
    controls = {"existing_bhf_quote": bhf}
    controls.update({
        "synthetic_year": "The merger is expected to close in 2026.",
        "synthetic_quarter": "The merger is expected to close by the end of the third quarter of 2026.",
        "synthetic_not_closed_by": "If the merger has not closed by September 6, 2026, it may terminate.",
        "synthetic_extension": "The merger agreement will be automatically extended to December 6, 2026.",
        "synthetic_negation": "It is not true that expected closing date is September 6, 2026.",
        "synthetic_conflicting_dates": (
            "Expected closing date: September 6, 2026. Expected closing date: December 6, 2026. "
            "Outside date: December 6, 2026. Outside date: January 6, 2027."
        ),
        "synthetic_may_modal": "The merger may be expected to close by May 1, 2026.",
    })
    refuse_checks = []
    for name, text in controls.items():
        result = dates(m._text(text.encode()))
        assert all(x["date"] is None for x in result.values())
        refuse_checks.append({"name": name, "text": text, "result": result})
    positive = dates("expected closing date: may 1, 2026. outside date: may 2, 2026.")
    assert positive["expected_resolution_date"]["date"] == "2026-05-01"
    assert positive["outside_date"]["date"] == "2026-05-02"
    print(json.dumps({
        "scope": "Dated held-filing research; unsigned; no selection authority",
        "base_main": pin["main_commit"],
        "summary": {
            "filings": len(observations),
            "filings_with_asserted_cash": sum(bool(x["cash_asserted"]) for x in observations),
            "cash_strategy_eligible_filings": sum(x["cash_strategy_eligible"] for x in observations),
            "exact_date_ready_filings": 0,
            "grammar_branch_justified_under_requested_date_condition": False,
        },
        "observations": observations, "diagnostic_ablations": ablations,
        "refusal_controls": refuse_checks, "positive_may_control": positive,
    }, indent=2) + "\n", end="")


if __name__ == "__main__":
    main()

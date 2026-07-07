"""Invariant gates, wired from WP0 so every later WP lands behind them.

These are static first-line checks. The runtime capability-boundary enforcement (an
``OrderGateway`` + capability token + tracer) is docs/security-hardening-backlog SEC-03,
added in WP7 — this call-site grep is the necessary-not-sufficient first line.
"""
from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1]  # app/


def _py_files(root: Path):
    return [p for p in root.rglob("*.py") if ".git" not in p.parts]


# ---- Invariant 1: single order path (static call-site gate; docs security-architecture §4 L1)
_ORDER_CALL = re.compile(r"\b(place_equity_order|place_order|submit_order|place_crypto_order)\s*\(")


def test_invariant_1_order_calls_only_in_brokers():
    offenders = []
    for p in _py_files(APP):
        rel = p.relative_to(APP)
        if rel.parts and rel.parts[0] in ("brokers", "tests"):
            continue
        if _ORDER_CALL.search(p.read_text(encoding="utf-8")):
            offenders.append(str(rel))
    assert not offenders, f"Invariant 1: order placement called outside brokers/: {offenders}"


# ---- Invariant 5: no LLM/subprocess inside risk/ (docs security-architecture §4 L2)
_LLM_IMPORT = re.compile(r"^\s*(?:import|from)\s+(?:subprocess|agent_runner|\S*claude|\S*codex)\b", re.M)


def test_invariant_5_risk_engine_has_no_llm():
    risk = APP / "risk"
    offenders = [
        str(p.relative_to(APP))
        for p in _py_files(risk)
        if _LLM_IMPORT.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"Invariant 5: LLM/subprocess import inside risk/: {offenders}"


# ---- Invariant 4: no plaintext secret literals in code/config (docs SEC-09; secrets-grep gate)
_SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:secret|token|password|api[_-]?key)\b\s*[:=]\s*[\"'][A-Za-z0-9_\-]{16,}[\"']"),
    re.compile(r"\b[MNO][A-Za-z0-9_\-]{23}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{27}\b"),  # discord bot token
]


def test_invariant_4_no_plaintext_secrets_in_app():
    hits = []
    for ext in ("*.py", "*.yaml", "*.yml", "*.toml", "*.json"):
        for p in APP.rglob(ext):
            if "tests" in p.relative_to(APP).parts:  # fixtures may carry sample tokens
                continue
            text = p.read_text(encoding="utf-8")
            for pat in _SECRET_PATTERNS:
                if pat.search(text):
                    hits.append(f"{p.relative_to(APP)} :: /{pat.pattern[:28]}/")
    assert not hits, f"Invariant 4: possible plaintext secret(s): {hits}"

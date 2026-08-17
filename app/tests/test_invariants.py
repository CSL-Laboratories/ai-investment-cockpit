"""Invariant gates, wired from WP0 so every later WP lands behind them.

These are static first-line checks. The runtime capability-boundary enforcement (an
``OrderGateway`` + capability token + tracer) is docs/security-hardening-backlog SEC-03,
added in WP7 — this call-site grep is the necessary-not-sufficient first line.
"""
from __future__ import annotations

import ast
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


# ---- QT-07: the quant engine's containment (docs/quant-engine.md; quant/execution/__init__.py)
#
# Tier C places orders with NO human approval and NO risk gate, at market-making frequency.
# That is only safe because its venue is an in-process simulator and there is no code path
# from a strategy to a broker adapter. THIS GATE IS THAT GUARANTEE.
#
# AST-based, not a regex: an import is a structural fact, and a text search would be fooled
# by a comment and defeated by `importlib.import_module("brok" + "ers.gateway")`. The AST
# catches every static import form; the runtime `isinstance` assertion in AutoLoop covers
# what static analysis cannot see.
_FORBIDDEN_QUANT_IMPORTS = ("brokers",)


def _imported_roots(tree: ast.AST):
    """Every top-level module name this file imports, by any static form."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level:          # a relative import cannot reach a sibling top-level pkg
                continue
            if node.module:
                yield node.module.split(".")[0]


_DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__", "load_module", "exec_module"})


def _dynamic_import_calls(tree: ast.AST):
    """Every call to a dynamic-import mechanism, by bare or attribute name.

    The static-import scan below CANNOT see these. ``importlib.import_module("brokers")`` is
    an ``ast.Call``, not an ``ast.Import``, so a gate that only walks import nodes misses it
    entirely — and the module name is an expression that may be computed at runtime
    (``"brok" + "ers"``, a config lookup, a reversed string), so no amount of cleverness makes
    the ARGUMENT statically decidable.

    The only sound control is therefore to ban the MECHANISM inside this package rather than
    try to evaluate its argument. Nothing under ``quant/`` has any legitimate need for a
    dynamic import; the cost of the ban is zero and it closes the hole completely.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        if name in _DYNAMIC_IMPORTERS:
            yield name


def test_quant_engine_never_imports_the_broker_package():
    quant = APP / "quant"
    if not quant.exists():
        return
    offenders = []
    for path in _py_files(quant):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for root in _imported_roots(tree):
            if root in _FORBIDDEN_QUANT_IMPORTS:
                offenders.append(f"{path.relative_to(APP)} imports {root}")
    assert not offenders, (
        "QT-07 containment: nothing under quant/ may import brokers/. Tier C's autonomous "
        "loop is unapproved and ungated; it is contained by being unable to reach a broker "
        f"at all. Offenders: {offenders}"
    )


def test_quant_engine_uses_no_dynamic_import_mechanism_at_all():
    """The hole the static gate above cannot see, closed by banning the mechanism.

    Found by adversarially probing the gate rather than by reading it: every dynamic form —
    ``importlib.import_module("brokers")``, ``__import__("brokers")``, string-concatenated
    names — sailed straight through, while the documentation claimed otherwise. A security
    control described as stronger than it is, is worse than one described accurately.
    """
    quant = APP / "quant"
    if not quant.exists():
        return
    offenders = []
    for path in _py_files(quant):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name in _dynamic_import_calls(tree):
            offenders.append(f"{path.relative_to(APP)} calls {name}()")
    assert not offenders, (
        "QT-07 containment: quant/ may not use a dynamic import mechanism. The module name "
        "would be a runtime expression, so the static brokers/ gate cannot see through it. "
        f"Offenders: {offenders}"
    )


def test_the_dynamic_import_gate_detects_every_form_that_defeated_the_static_one():
    """CONTROL, written from the actual probe output. Each of these was MISSED by the
    static import scan; all must be caught here."""
    for source in (
        'importlib.import_module("brokers")',
        'importlib.import_module("brok" + "ers")',
        '__import__("brokers")',
        'getattr(__import__("importlib"), "import_module")("brokers")',
        'spec.loader.exec_module(mod)',
    ):
        assert list(_dynamic_import_calls(ast.parse(source))), f"gate MISSES: {source}"

    # ...and does not fire on ordinary code that merely mentions a module.
    for source in ("import quant.book", "from quant.oms import OMS", "x = module_name"):
        assert not list(_dynamic_import_calls(ast.parse(source))), source


def test_the_quant_containment_gate_actually_detects_a_violation(tmp_path):
    """CONTROL. A gate that cannot fail proves nothing, and this one carries the entire
    safety argument for Tier C. Prove it fires on each import form it must catch."""
    for source in (
        "import brokers",
        "import brokers.gateway",
        "from brokers import gateway",
        "from brokers.gateway import OrderGateway",
    ):
        roots = list(_imported_roots(ast.parse(source)))
        assert "brokers" in roots, f"the containment gate would MISS: {source!r}"

    # ...and does not fire on innocent imports that merely mention the word.
    for source in ("import quant.book", "from quant.oms import OMS", "import brokerage_utils"):
        assert "brokers" not in list(_imported_roots(ast.parse(source))), source


# ---- QT-06: quant/verify.py is imported BY risk/, so it inherits Invariant 5's purity.
_IMPURE_IN_VERIFY = re.compile(
    r"^\s*(?:import|from)\s+(?:numpy|pandas|scipy|requests|httpx|urllib|socket|subprocess|"
    r"pathlib|os|sqlalchemy|pyarrow|\S*claude|\S*codex)\b",
    re.M,
)


def test_the_execution_verifier_stays_pure_because_risk_imports_it():
    """``risk/engine.py`` consumes a QuantVerdict, and Invariant 5 requires the risk engine to
    be pure deterministic functions — no LLM, no I/O, no subprocess. A numpy or network
    import in the verifier would propagate straight through that boundary."""
    verify = APP / "quant" / "verify.py"
    if not verify.exists():
        return
    text = verify.read_text(encoding="utf-8")
    offenders = _IMPURE_IN_VERIFY.findall(text)
    assert not offenders, (
        f"Invariant 5: quant/verify.py must stay import-clean (risk/ imports it): {offenders}"
    )


def test_the_risk_engine_itself_did_not_acquire_an_impure_import():
    """The other half of the same boundary: adding the QT-06 checks must not have pulled
    anything heavy into risk/ either."""
    offenders = [
        str(p.relative_to(APP))
        for p in _py_files(APP / "risk")
        if _IMPURE_IN_VERIFY.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"Invariant 5: impure import inside risk/: {offenders}"

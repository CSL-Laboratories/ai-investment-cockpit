"""Invariant gates, wired from WP0 so every later WP lands behind them.

These are static first-line checks. The runtime capability-boundary enforcement (an
``OrderGateway`` + capability token + tracer) is docs/security-hardening-backlog SEC-03,
added in WP7 — this call-site grep is the necessary-not-sufficient first line.
"""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

APP = Path(__file__).resolve().parents[1]  # app/
ROOT = APP.parent


def _py_files(root: Path):
    return [p for p in root.rglob("*.py") if ".git" not in p.parts]


# ---- Invariant 1: single order path (static call-site gate; docs security-architecture §4 L1)
_ORDER_CALL = re.compile(r"\b(place_equity_order|place_order|submit_order|place_crypto_order)\s*\(")


def _order_path_offenders(app: Path, tools: Path) -> list[str]:
    """Call sites outside ``brokers/`` (and ``app/tests/``) — also scan repo ``tools/``."""
    offenders: list[str] = []
    for p in _py_files(app):
        rel = p.relative_to(app)
        if rel.parts and rel.parts[0] in ("brokers", "tests"):
            continue
        if _ORDER_CALL.search(p.read_text(encoding="utf-8")):
            offenders.append(str(rel))
    if tools.is_dir():
        root = app.parent
        for p in _py_files(tools):
            if _ORDER_CALL.search(p.read_text(encoding="utf-8")):
                offenders.append(str(p.relative_to(root)))
    return offenders


def test_invariant_1_order_calls_only_in_brokers():
    assert (ROOT / "tools").is_dir(), "Invariant 1 tools/ scan requires the repo tools/ directory"
    offenders = _order_path_offenders(APP, ROOT / "tools")
    assert not offenders, f"Invariant 1: order placement called outside brokers/: {offenders}"


def test_the_order_path_gate_actually_detects_a_tools_violation(tmp_path):
    """CONTROL. Prove Inv 1 fires when an order call lands under repo-level tools/."""
    app = tmp_path / "app"
    tools = tmp_path / "tools"
    app.mkdir()
    tools.mkdir()
    (tools / "rogue_order.py").write_text("place_order(symbol='SPY')\n", encoding="utf-8")

    offenders = [o.replace("\\", "/") for o in _order_path_offenders(app, tools)]
    assert offenders == ["tools/rogue_order.py"]


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


def test_invariant_4_no_plaintext_secrets_in_app_or_operator_tools():
    hits = []
    for base in (APP, ROOT / "tools"):
        for ext in ("*.py", "*.ps1", "*.js", "*.yaml", "*.yml", "*.toml", "*.json"):
            for p in base.rglob(ext):
                if base == APP and "tests" in p.relative_to(APP).parts:
                    continue  # fixtures may carry sample tokens
                text = p.read_text(encoding="utf-8")
                for pat in _SECRET_PATTERNS:
                    if pat.search(text):
                        hits.append(f"{p.relative_to(ROOT)} :: /{pat.pattern[:28]}/")
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


# ---- QT-06: quant/verify.py feeds risk/, so its whole local import closure inherits Inv 5.
_IMPURE_VERIFY_ROOTS = frozenset({
    "agent_runner", "anthropic", "httpx", "langchain", "numpy", "openai", "os", "pandas",
    "pathlib", "pyarrow", "requests", "scipy", "socket", "sqlalchemy", "subprocess", "urllib",
})


def _module_path(root: Path, parts: tuple[str, ...]) -> Path | None:
    """Resolve a static module name inside ``root`` without importing it."""
    if not parts:
        return None
    module = root.joinpath(*parts)
    source = module.with_suffix(".py")
    if source.is_file():
        return source
    package = module / "__init__.py"
    return package if package.is_file() else None


def _local_import_targets(path: Path, root: Path, tree: ast.AST) -> set[Path]:
    """Return local modules that a file can reach through ordinary static imports."""
    package = path.relative_to(root).with_suffix("").parts[:-1]
    if path.name == "__init__.py":
        package = path.relative_to(root).parent.parts

    targets: set[Path] = set()
    for node in ast.walk(tree):
        names: list[tuple[str, ...]] = []
        if isinstance(node, ast.Import):
            names.extend(tuple(alias.name.split(".")) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = len(package) - (node.level - 1)
                if keep < 0:
                    continue
                base = package[:keep]
            else:
                base = ()
            if node.module:
                base += tuple(node.module.split("."))
            names.append(base)
            names.extend(base + tuple(alias.name.split(".")) for alias in node.names)

        for parts in names:
            target = _module_path(root, parts)
            if target is not None:
                targets.add(target)
            # Importing ``quant.book.lob`` executes both package initializers first. They are
            # part of the runtime closure even though the import statement names only ``lob``.
            for end in range(1, len(parts)):
                initializer = root.joinpath(*parts[:end], "__init__.py")
                if initializer.is_file():
                    targets.add(initializer)
    return targets


def _transitive_impure_imports(entry: Path, root: Path) -> list[str]:
    """Walk local static imports and report every forbidden root with its importing file."""
    offenders: list[str] = []
    pending = [entry]
    visited: set[Path] = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for imported in _imported_roots(tree):
            if imported in _IMPURE_VERIFY_ROOTS or "claude" in imported or "codex" in imported:
                offenders.append(f"{path.relative_to(root)} imports {imported}")
        pending.extend(_local_import_targets(path, root, tree) - visited)
    return sorted(offenders)


def test_the_execution_verifier_stays_pure_because_risk_imports_it():
    """``risk/engine.py`` consumes a QuantVerdict, and Invariant 5 requires the risk engine to
    be pure deterministic functions — no LLM, no I/O, no subprocess. A numpy or network
    import in the verifier would propagate straight through that boundary."""
    verify = APP / "quant" / "verify.py"
    if not verify.exists():
        return
    offenders = _transitive_impure_imports(verify, APP)
    assert not offenders, (
        "Invariant 5: quant/verify.py and its local import closure must stay pure because "
        f"the verifier feeds risk decisions: {offenders}"
    )


def test_the_execution_verifier_gate_detects_a_transitive_violation(tmp_path):
    """BUG-049 control: an impure grandchild must fail even when the entry file is clean."""
    package = tmp_path / "quant"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "verify.py").write_text("from quant.helper import VALUE\n", encoding="utf-8")
    (package / "helper.py").write_text("from quant.deep import VALUE\n", encoding="utf-8")
    (package / "deep.py").write_text("import httpx\nVALUE = 1\n", encoding="utf-8")

    offenders = _transitive_impure_imports(package / "verify.py", tmp_path)
    assert [offender.replace("\\", "/") for offender in offenders] == [
        "quant/deep.py imports httpx"
    ]


def test_the_execution_verifier_gate_checks_imported_package_initializers(tmp_path):
    """Python executes every parent ``__init__`` before a deeply imported module."""
    package = tmp_path / "quant"
    book = package / "book"
    book.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "verify.py").write_text(
        "from quant.book.lob import BookSnapshot\n", encoding="utf-8"
    )
    (book / "__init__.py").write_text("import anthropic\n", encoding="utf-8")
    (book / "lob.py").write_text("class BookSnapshot: pass\n", encoding="utf-8")

    offenders = _transitive_impure_imports(package / "verify.py", tmp_path)
    assert [offender.replace("\\", "/") for offender in offenders] == [
        "quant/book/__init__.py imports anthropic"
    ]


def test_the_risk_engine_itself_did_not_acquire_an_impure_import():
    """The other half of the same boundary: adding the QT-06 checks must not have pulled
    anything heavy into risk/ either."""
    offenders = []
    for path in _py_files(APP / "risk"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        roots = set(_imported_roots(tree))
        if impure := sorted(
            root for root in roots
            if root in _IMPURE_VERIFY_ROOTS or "claude" in root or "codex" in root
        ):
            offenders.append(f"{path.relative_to(APP)} imports {', '.join(impure)}")
    assert not offenders, f"Invariant 5: impure import inside risk/: {offenders}"


def test_runtime_artifact_directories_are_ignored_at_any_depth():
    """The process runs from ``app/``, so root-only patterns leave app/data trackable."""
    gitignore = Path(__file__).resolve().parents[2] / ".gitignore"
    rules = {
        line.strip()
        for line in gitignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "data/" in rules and "/data/" not in rules
    assert "replays/" in rules and "/replays/" not in rules


def test_machine_written_audit_head_is_not_tracked():
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--error-unmatch", "app/data/audit-head.json"],
        check=False,
        capture_output=True,
    )
    assert tracked.returncode != 0, "runtime audit head must not be tracked on a source branch"

"""The spine is not an order path and owns no route machinery.

Imports are an allow-list per production module, so a new capability (threads,
sockets, subprocesses, the legacy ``app`` tree, brokers, risk) must be added
here on purpose and reviewed. Identifiers that name order placement are refused
anywhere in production code. The detector is proved on bad snippets first.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
BASE_ALLOWED = frozenset({
    "__future__", "copy", "dataclasses", "datetime", "hashlib", "json", "math", "pathlib",
    "re", "typing", "collections", "jsonschema", "successor", "cockpit_spine",
})
EXTRA_ALLOWED: dict[str, frozenset[str]] = {
    "projections.py": frozenset({"types"}),
    "regen_projections.py": frozenset({"sys"}),
    "cli.py": frozenset({"argparse", "errno", "fcntl", "msvcrt", "secrets", "sys"}),
    "__main__.py": frozenset({"sys"}),
    "web.py": frozenset({"html", "http", "secrets", "threading", "urllib"}),
    "recorded.py": frozenset({"contextlib", "io"}),
}
FORBIDDEN_IDENTIFIER_WORDS = ("broker", "gateway", "submit_order", "place_order", "execute_order",
                              "send_order", "killswitch", "arm_auto")


def import_roots(source: str) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                roots.add("<relative>")
            elif node.module:
                roots.add(node.module.split(".")[0])
    return roots


def bad_identifiers(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        names: list[str] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.arg):
            names.append(node.arg)
        elif isinstance(node, ast.alias):
            names.append(node.name)
        for name in names:
            if any(word in name.lower() for word in FORBIDDEN_IDENTIFIER_WORDS):
                found.append(name)
    return found


def production_modules() -> list[Path]:
    return sorted(p for p in PACKAGE.glob("*.py") if p.name != "__init__.py") + [
        PACKAGE / "__init__.py"]


class DetectorTests(unittest.TestCase):
    def test_import_detector_sees_the_forbidden_roots(self) -> None:
        for snippet, root in (
            ("import subprocess", "subprocess"), ("from app.brokers import x", "app"),
            ("import socket", "socket"), ("from threading import Thread", "threading"),
            ("from risk import gate", "risk"), ("from . import x", "<relative>"),
        ):
            self.assertIn(root, import_roots(snippet), snippet)

    def test_identifier_detector_sees_order_placement_names(self) -> None:
        for snippet in ("def place_order(): pass", "broker = 1", "x.OrderGateway()",
                        "def f(submit_order): pass", "class BrokerClient: pass"):
            self.assertTrue(bad_identifiers(snippet), snippet)
        self.assertEqual([], bad_identifiers("def approve(): return 1"))


class BoundaryTests(unittest.TestCase):
    def test_modules_exist(self) -> None:
        self.assertGreaterEqual(len(production_modules()), 5)

    def test_imports_stay_on_the_allow_list(self) -> None:
        for path in production_modules():
            allowed = BASE_ALLOWED | EXTRA_ALLOWED.get(path.name, frozenset())
            extra = import_roots(path.read_text(encoding="utf-8")) - allowed
            self.assertEqual(set(), extra, f"{path.name} imports {sorted(extra)}")

    def test_no_identifier_names_order_placement(self) -> None:
        for path in production_modules():
            self.assertEqual([], bad_identifiers(path.read_text(encoding="utf-8")), path.name)

    def test_the_spine_reaches_the_route_only_through_the_consumer_package(self) -> None:
        for path in production_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module \
                        and node.module.startswith("successor.route_consumer"):
                    self.assertNotIn("tests", node.module, path.name)
                    self.assertNotIn("contract", node.module.split(".")[2:], path.name)


if __name__ == "__main__":
    unittest.main(verbosity=2)

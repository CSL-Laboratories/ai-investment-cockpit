"""Cockpit defines NO session, queue, lease or route-selection code (WP12 acceptance b).

Three independent checks over every Python file in this tree:

1. AST: no class, function, parameter, assignment target, import alias or
   global that is named like session/queue/lease/fencing/lock machinery or like
   route selection (select, choose, pick, rank, balance, fail over, fall back).
   Test method names (``test_*``) describe behavior and are exempt from the
   name check; their bodies are still scanned.
2. Imports: production modules import only the standard library pieces they
   need, ``jsonschema`` and this package; nothing that provides threads,
   queues, sockets, subprocesses, schedulers or the order path.
3. Token grep: in production modules, no identifier token (strings and
   comments excluded) contains a forbidden word.

The detector is proved against bad snippets first, so a green run cannot be a
detector that matches nothing.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parents[2]
PRODUCTION = TREE / "route_consumer"
MACHINERY_WORDS = frozenset({
    "session", "sessions", "queue", "queues", "queued", "enqueue", "dequeue", "requeue",
    "lease", "leases", "leased", "fence", "fences", "fencing", "lock", "locks", "locked",
    "mutex", "semaphore", "scheduler", "failover", "fallback", "backoff", "retry", "retries",
    "heartbeat", "ownership", "owner",
})
ROUTE_VERBS = frozenset({
    "select", "selector", "selection", "choose", "chooser", "choice", "pick", "picker",
    "rank", "ranker", "ranked", "prefer", "preferred", "balance", "balancer", "router",
    "routing", "candidates", "candidate", "dispatch", "dispatcher",
})
FORBIDDEN_IMPORT_ROOTS = frozenset({
    "queue", "sched", "threading", "_thread", "multiprocessing", "asyncio", "concurrent",
    "subprocess", "socket", "selectors", "heapq", "signal", "fcntl", "sqlite3", "http",
    "urllib", "requests", "httpx", "app", "brokers", "risk", "orchestrator", "scripts",
})
TEST_ONLY_IMPORTS = frozenset({"subprocess"})
PRODUCTION_IMPORT_ALLOWED = frozenset({
    "__future__", "copy", "dataclasses", "hashlib", "json", "pathlib", "types", "typing",
    "functools", "jsonschema", "successor",
})
_SPLIT = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def words(identifier: str) -> list[str]:
    return [part.lower() for part in _SPLIT.findall(identifier.replace("_", " ").replace(" ", "_"))
            if part != "_"] or [identifier.lower()]


def identifier_problem(identifier: str) -> str | None:
    tokens = words(identifier)
    joined = identifier.lower().replace("_", "")
    for token in tokens:
        if token in MACHINERY_WORDS:
            return f"machinery word {token!r}"
    if "route" in tokens or "routes" in tokens:
        for token in tokens:
            if token in ROUTE_VERBS:
                return f"route-selection word {token!r}"
    for verb in ("selectroute", "chooseroute", "pickroute", "routeselect", "failover", "fallback"):
        if verb in joined:
            return f"route-selection name {verb!r}"
    return None


def definition_names(tree: ast.AST) -> list[tuple[str, int]]:
    names: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("test_"):
                names.append((node.name, node.lineno))
            args = node.args
            for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
                if arg is not None:
                    names.append((arg.arg, arg.lineno))
        elif isinstance(node, ast.ClassDef):
            names.append((node.name, node.lineno))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.append((node.id, node.lineno))
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            names.append((node.attr, node.lineno))
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.extend((name, node.lineno) for name in node.names)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.extend((alias.asname or alias.name.split(".")[-1], node.lineno)
                         for alias in node.names)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append((node.target.id, node.lineno))
    return names


def imported_roots(tree: ast.AST) -> list[tuple[str, int]]:
    roots: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.extend((alias.name.split(".")[0], node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.append((node.module.split(".")[0], node.lineno))
    return roots


def scan_names(source: str) -> list[str]:
    problems = []
    for name, line in definition_names(ast.parse(source)):
        problem = identifier_problem(name)
        if problem:
            problems.append(f"line {line}: {name}: {problem}")
    return problems


def scan_imports(source: str, *, production: bool) -> list[str]:
    problems = []
    for root, line in imported_roots(ast.parse(source)):
        if root in FORBIDDEN_IMPORT_ROOTS and (production or root not in TEST_ONLY_IMPORTS):
            problems.append(f"line {line}: import {root}")
        elif production and root not in PRODUCTION_IMPORT_ALLOWED:
            problems.append(f"line {line}: import {root} is not on the production allow-list")
    return problems


def scan_tokens(source: str) -> list[str]:
    problems = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NAME:
            problem = identifier_problem(token.string)
            if problem:
                problems.append(f"line {token.start[0]}: {token.string}: {problem}")
    return problems


def is_production(path: Path) -> bool:
    if PRODUCTION not in path.parents:
        return True
    return "tests" not in path.relative_to(PRODUCTION).parts


def python_files() -> list[Path]:
    return sorted(path for path in TREE.rglob("*.py"))


BAD_SNIPPETS = {
    "session class": "class SessionStore:\n    pass\n",
    "session function": "def open_session():\n    return 1\n",
    "queue attribute": "class A:\n    def __init__(self):\n        self.pending_queue = []\n",
    "lease variable": "lease_until = 0\n",
    "lock parameter": "def f(lock):\n    return lock\n",
    "fencing": "fencing_token = 3\n",
    "route selection": "def select_route(routes):\n    return routes[0]\n",
    "route chooser": "class RouteChooser:\n    pass\n",
    "pick route camel": "def pickRoute():\n    pass\n",
    "route ranking": "ranked_routes = []\n",
    "failover": "def failover():\n    pass\n",
    "fallback route": "fallback = None\n",
    "retry loop": "def retry_after():\n    pass\n",
    "heartbeat": "heartbeat_at = 0\n",
    "import alias": "import json as session\n",
    "global": "def f():\n    global the_queue\n",
}
BAD_IMPORTS = {
    "queue": "import queue\n",
    "threading": "from threading import Lock\n",
    "subprocess": "import subprocess\n",
    "asyncio": "import asyncio\n",
    "scheduler": "import sched\n",
    "order path": "from app.brokers import gateway\n",
    "risk": "from risk import limits\n",
    "recursion scripts": "from scripts.v3 import route_contract\n",
}


class DetectorProofTests(unittest.TestCase):
    def test_detector_flags_every_bad_snippet(self) -> None:
        for label, source in BAD_SNIPPETS.items():
            with self.subTest(label):
                self.assertTrue(scan_names(source), label)
                self.assertTrue(scan_tokens(source), label)

    def test_detector_flags_every_forbidden_import(self) -> None:
        for label, source in BAD_IMPORTS.items():
            with self.subTest(label):
                self.assertTrue(scan_imports(source, production=True), label)

    def test_detector_allows_the_contract_vocabulary_this_tree_needs(self) -> None:
        clean = (
            "route_id = 'route-a'\n"
            "route_descriptor_digest = 'x'\n"
            "class RouteBinding:\n    engine_id: str\n"
            "def build_request_record(request_id, binding, profile):\n    return {}\n"
            "reply_record = {'session': request_id}\n"
        )
        self.assertEqual([], scan_names(clean))
        self.assertEqual([], scan_tokens(clean))

    def test_string_and_comment_mentions_are_not_definitions(self) -> None:
        source = '"""no queue, no lease, no session"""\n# no failover\nx = "session"\n'
        self.assertEqual([], scan_names(source))
        self.assertEqual([], scan_tokens(source))

    def test_tokenizer_splits_snake_and_camel_case(self) -> None:
        self.assertEqual(["route", "selector"], words("RouteSelector"))
        self.assertEqual(["pick", "route"], words("pick_route"))
        self.assertEqual(["http", "session"], words("HTTPSession"))


class CockpitDefinesNoRouteMachineryTests(unittest.TestCase):
    def test_the_tree_is_found_and_nonempty(self) -> None:
        names = {path.name for path in python_files()}
        self.assertTrue({"consumer.py", "profile.py", "records.py", "pin.py"} <= names)
        self.assertGreaterEqual(len(python_files()), 12)

    def test_ast_no_forbidden_definitions_anywhere_in_the_tree(self) -> None:
        for path in python_files():
            with self.subTest(path.relative_to(TREE).as_posix()):
                self.assertEqual([], scan_names(path.read_text(encoding="utf-8")))

    def test_no_forbidden_imports_anywhere_in_the_tree(self) -> None:
        for path in python_files():
            with self.subTest(path.relative_to(TREE).as_posix()):
                source = path.read_text(encoding="utf-8")
                self.assertEqual([], scan_imports(source, production=is_production(path)))

    def test_token_grep_of_production_modules(self) -> None:
        production = [path for path in python_files() if is_production(path)]
        self.assertGreaterEqual(len(production), 6)
        for path in production:
            with self.subTest(path.relative_to(TREE).as_posix()):
                self.assertEqual([], scan_tokens(path.read_text(encoding="utf-8")))

    def test_production_has_exactly_one_route_binding_and_no_route_collection(self) -> None:
        source = (PRODUCTION / "consumer.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        init = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and node.name == "__init__")
        annotations = [ast.unparse(arg.annotation) for arg in init.args.kwonlyargs if arg.annotation]
        self.assertEqual(["RouteBinding"], annotations)
        for node in ast.walk(tree):
            if isinstance(node, ast.arg) and node.annotation is not None:
                text = ast.unparse(node.annotation)
                self.assertNotRegex(text, r"(list|List|Sequence|Iterable|dict|Mapping)\[.*Route")

    def test_no_loops_around_the_port_in_the_consumer(self) -> None:
        tree = ast.parse((PRODUCTION / "consumer.py").read_text(encoding="utf-8"))
        loops = [node for node in ast.walk(tree) if isinstance(node, (ast.For, ast.While, ast.AsyncFor))]
        self.assertEqual([], loops, "a loop could retry, wait or walk a list of routes")

    def test_no_order_path_or_model_runtime_names_in_production(self) -> None:
        pattern = re.compile(r"OrderGateway|order_gateway|broker|keyring|subprocess|Popen|os\.environ")
        for path in python_files():
            if is_production(path):
                code = "".join(
                    token.string + " " for token in tokenize.generate_tokens(
                        io.StringIO(path.read_text(encoding="utf-8")).readline)
                    if token.type in (tokenize.NAME, tokenize.OP)
                )
                self.assertIsNone(pattern.search(code), path.name)


if __name__ == "__main__":
    unittest.main(verbosity=2)

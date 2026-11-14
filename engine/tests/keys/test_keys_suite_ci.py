"""The key-store suite in CI's existing step (CSL-59).

CI runs ``python -X utf8 -B -m pytest -q -p no:cacheprovider engine/tests``, which never reaches
``engine/cockpit/keys/tests``. Importing the suite's test classes here makes that same step collect and
run all of them, with no workflow edit and no copied test. Suggested by the Codex (GPT-6) read of #326.

``SuiteCoverageTests`` compares the tests pytest will run from this file with what unittest discovery of
the suite folder runs, as a developer would, and fails unless every runnable unittest test there (any test
method, whatever its class is called, in any test module or test package, or a module that fails to import)
is there exactly once. Under pytest it reads the running session's own items, after every collection hook
(including a ``conftest.py``) has run, so a test pytest leaves out (``__test__ = False`` on a class or a
method, a class not imported here, an item a plugin drops) fails it, and so does a class exposed here twice.
In a run narrowed by one of these selection options (node ids, ``-k``, ``-m``, ``--deselect``, ``--lf``,
``--sw``), and under plain unittest, it runs pytest's collection of this file in a child process instead,
which sees what ``--collect-only`` sees; a run narrowed any other way, such as a plugin's own selection
option, fails the check. It cannot see a test a plugin skips at run time, a plugin that drops this check
itself, or a method that is no longer a test at all (one renamed from ``test_...``); without pytest it is
skipped (CI always has it).

A second check fails if a test module there defines a test class twice, or a test method twice in one
class, since the later definition replaces the earlier one before either runner sees it. Definitions inside
blocks of the same scope (``if``, ``for``, ``while``, ``with``, ``try``, ``match``) count. Alternatives that
cannot both run count once: the branches of one ``if``/``elif``/``else`` or ``match``, and the ordinary
``except`` arms and ``else`` of one ``try``. A ``try`` body and its ``finally`` count on top of whichever of
those runs, and ``except*`` arms add up, since several can run. Rebinding a name by assignment or import is
not checked. The suite is stdlib ``unittest``; a pytest-style test function there
would not be covered.
"""
from __future__ import annotations

import ast
import collections
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from typing import Any

import cockpit.keys.tests as _suite
from cockpit.keys.tests.test_b13_chain import DoctorLineTests, MixedChainTests  # noqa: F401
from cockpit.keys.tests.test_b13_real_library import RealChainerTests  # noqa: F401
from cockpit.keys.tests.test_b14_config_home import FreshAccountTests  # noqa: F401
from cockpit.keys.tests.test_b23_strict_input import KeyFileTests, KeyNameTests  # noqa: F401
from cockpit.keys.tests.test_b24_enrolment import EnrolmentTests, InFlightTests, ParserTests  # noqa: F401
from cockpit.keys.tests.test_b24_keychain_record import (  # noqa: F401
    AcceptTests, GenuineKeychainTests, NoRecordTests, RecordDoctorTests, RecordTests,
)
from cockpit.keys.tests.test_b29_not_a_regular_file import NotARegularFileTests  # noqa: F401
from cockpit.keys.tests.test_detect import DetectTests, DoctorTests, StoreForHostTests  # noqa: F401
from cockpit.keys.tests.test_r6_finding_7 import FreshHostTests  # noqa: F401
from cockpit.keys.tests.test_store import KeyringHostTests, NoKeychainTests, StaticTests  # noqa: F401

try:
    import pytest
except ImportError:  # CI's runner; a plain unittest run without it uses neither pytest path
    pytest = None

_SUITE_DIR = os.path.dirname(os.path.abspath(_suite.__file__))
_ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_SUITE_DIR)))
_REPO_DIR = os.path.dirname(_ENGINE_DIR)

# Run in a child interpreter: pytest's own collection of this file, written out in unittest's id form
# (``module.Class.method``, from the collected class itself, so an alias counts as the class it names).
# Classes defined in this file are not part of the suite. A non-unittest item keeps its pytest id.
_COLLECT = """
import json, sys
import pytest

class Items:
    def __init__(self):
        self.ids = []

    def pytest_collection_finish(self, session):
        for item in session.items:
            cls = getattr(item, "cls", None)
            if cls is None:
                self.ids.append(item.nodeid)
            elif cls.__module__ != item.module.__name__:
                self.ids.append(f"{cls.__module__}.{cls.__qualname__}.{item.name}")

items = Items()
code = pytest.main(["--collect-only", "-q", "-p", "no:cacheprovider", sys.argv[1]], plugins=[items])
with open(sys.argv[2], "w", encoding="utf-8") as out:
    json.dump(items.ids, out)
sys.exit(int(code))
"""


def _ids(suite: unittest.TestSuite) -> collections.Counter[str]:
    found: collections.Counter[str] = collections.Counter()
    for item in suite:
        found += _ids(item) if isinstance(item, unittest.TestSuite) else collections.Counter([item.id()])
    return found


def _session_ids(session: Any) -> collections.Counter[str]:
    """The running session's items from this file, as ``_COLLECT`` writes them."""
    this = sys.modules[__name__]
    found: collections.Counter[str] = collections.Counter()
    for item in session.items:
        if getattr(item, "module", None) is not this:
            continue
        cls = getattr(item, "cls", None)
        if cls is None:
            found[item.nodeid] += 1
        elif cls.__module__ != __name__:
            found[f"{cls.__module__}.{cls.__qualname__}.{item.name}"] += 1
    return found


def _narrowed(config: Any) -> bool:
    """Whether this pytest run was asked for only some tests, so its items cannot be the whole file."""
    option = config.option
    return bool(getattr(option, "keyword", "") or getattr(option, "markexpr", "") or getattr(option, "deselect", None)
                or getattr(option, "lf", False) or getattr(option, "stepwise", False)
                or any("::" in str(arg) for arg in config.args))


def _child_ids(test: unittest.TestCase) -> collections.Counter[str]:
    this_file = os.path.relpath(os.path.abspath(__file__), _REPO_DIR).replace(os.sep, "/")
    with tempfile.TemporaryDirectory() as scratch:
        out = os.path.join(scratch, "items.json")
        run = subprocess.run([sys.executable, "-X", "utf8", "-B", "-c", _COLLECT, this_file, out],
                             cwd=_REPO_DIR, capture_output=True, text=True, timeout=600)
        test.assertEqual(run.returncode, 0, f"pytest could not collect {this_file}:\n{run.stdout[-3000:]}{run.stderr[-3000:]}")
        with open(out, encoding="utf-8") as items:
            return collections.Counter(json.load(items))


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _blocks(node: ast.AST) -> Iterator[list[ast.stmt]]:
    """The statement lists of a compound statement that run in its own scope."""
    for field in ("body", "orelse", "finalbody"):
        block = getattr(node, field, None)
        if isinstance(block, list):
            yield block
    for part in (*getattr(node, "handlers", ()), *getattr(node, "cases", ())):
        yield part.body


def _defined(body: list[ast.stmt], kinds: tuple[type, ...], prefix: str = "") -> collections.Counter[str]:
    """How often each name is bound by a definition in this scope, blocks included. Alternatives that
    cannot both run count once across them: the branches of one ``if`` or ``match``, and the ordinary
    ``except`` arms and ``else`` of one ``try``. A ``try`` body and its ``finally`` add to whichever runs,
    and the arms of ``except*`` add up, since several of them can run."""
    found: collections.Counter[str] = collections.Counter()
    for node in body:
        if isinstance(node, kinds) and node.name.startswith(prefix):  # type: ignore[attr-defined]
            found[node.name] += 1  # type: ignore[attr-defined]
        elif isinstance(node, ast.If):
            found += _defined(node.body, kinds, prefix) | _defined(node.orelse, kinds, prefix)
        elif isinstance(node, ast.Match):
            branches: collections.Counter[str] = collections.Counter()
            for case in node.cases:
                branches |= _defined(case.body, kinds, prefix)
            found += branches
        elif isinstance(node, (ast.Try, ast.TryStar)):
            arms: collections.Counter[str] = collections.Counter()
            for handler in node.handlers:
                arm = _defined(handler.body, kinds, prefix)
                arms = arms + arm if isinstance(node, ast.TryStar) else arms | arm
            found += (_defined(node.body, kinds, prefix) + (arms | _defined(node.orelse, kinds, prefix))
                      + _defined(node.finalbody, kinds, prefix))
        elif not isinstance(node, _SCOPES):
            for block in _blocks(node):
                found += _defined(block, kinds, prefix)
    return found


def _classes(body: list[ast.stmt]) -> Iterator[ast.ClassDef]:
    """Every class defined in this scope, blocks included."""
    for node in body:
        if isinstance(node, ast.ClassDef):
            yield node
        elif not isinstance(node, _SCOPES):
            for block in _blocks(node):
                yield from _classes(block)


class SuiteCoverageTests(unittest.TestCase):
    _session: Any = None  # the running pytest session, when pytest runs this test

    if pytest is not None:
        @pytest.fixture(autouse=True)
        def _running_session(self, request: Any) -> None:  # called by pytest only; unittest never does
            self._session = request.session

    def test_ci_collects_every_runnable_test_of_the_key_store_suite_exactly_once(self) -> None:
        if pytest is None:
            self.skipTest("pytest is not installed with this interpreter (CI's step runs this check)")
        discovered = _ids(unittest.TestLoader().discover(_SUITE_DIR, top_level_dir=_ENGINE_DIR))
        self.assertTrue(discovered)
        if self._session is not None and not _narrowed(self._session.config):
            collected, source = _session_ids(self._session), "this pytest run"
        else:
            collected, source = _child_ids(self), "pytest's collection in a child process"
        missing = sorted((discovered - collected).elements())
        extra = sorted((collected - discovered).elements())
        self.assertEqual((missing, extra), ([], []), f"(not in {source}, in {source} beyond unittest's count)")

    def test_no_test_class_or_test_method_of_the_suite_is_defined_twice(self) -> None:
        # A second definition of the same name replaces the first before either runner sees it.
        twice = []
        for folder, _, files in os.walk(_SUITE_DIR):
            for name in sorted(files):
                if not (name.startswith("test") and name.endswith(".py")):
                    continue
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8") as source:
                    tree = ast.parse(source.read(), path)
                where = os.path.relpath(path, _SUITE_DIR)
                twice += [f"{where}: class {n}" for n, c in _defined(tree.body, (ast.ClassDef,)).items() if c > 1]
                for cls in _classes(tree.body):
                    twice += [f"{where}: {cls.name}.{n}" for n, c in _defined(cls.body, _DEFS, "test").items() if c > 1]
        self.assertEqual(twice, [])


if __name__ == "__main__":
    unittest.main()

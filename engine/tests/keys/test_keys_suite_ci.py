"""The key-store suite in CI's existing step (CSL-59).

CI runs ``python -X utf8 -B -m pytest -q -p no:cacheprovider engine/tests``, which never reaches
``engine/cockpit/keys/tests``. Importing the suite's test classes here makes that same step collect and
run all of them, with no workflow edit and no copied test. Suggested by the Codex (GPT-6) read of #326.

``SuiteCoverageTests`` compares what pytest really collects from this file with what unittest discovery of
the suite folder runs, as a developer would, and fails unless every runnable unittest test there (any test
method, whatever its class is called, in any test module or test package, or a module that fails to import)
is collected here exactly once. So a test pytest leaves out (``__test__ = False`` on a class or a method,
a class not imported here) fails it, and so does a class exposed here twice. It also fails if a test module
there defines a test class twice, or a test method twice in one class, since the later definition replaces
the earlier one before either runner sees it. It cannot see a method that is no longer a test at all (one
renamed from ``test_...``). It runs pytest's collection in a child process with this interpreter and is
skipped where pytest is not installed; CI always has it. The suite is stdlib ``unittest``; a pytest-style
test function there would not be covered.
"""
from __future__ import annotations

import ast
import collections
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

import cockpit.keys.tests as _suite
from cockpit.keys.tests.test_b13_chain import DoctorLineTests, MixedChainTests  # noqa: F401
from cockpit.keys.tests.test_b13_real_library import RealChainerTests  # noqa: F401
from cockpit.keys.tests.test_b14_config_home import FreshAccountTests  # noqa: F401
from cockpit.keys.tests.test_detect import DetectTests, DoctorTests, StoreForHostTests  # noqa: F401
from cockpit.keys.tests.test_r6_finding_7 import FreshHostTests  # noqa: F401
from cockpit.keys.tests.test_store import KeyringHostTests, NoKeychainTests, StaticTests  # noqa: F401

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


class SuiteCoverageTests(unittest.TestCase):
    def test_ci_collects_every_runnable_test_of_the_key_store_suite_exactly_once(self) -> None:
        if importlib.util.find_spec("pytest") is None:
            self.skipTest("pytest is not installed with this interpreter (CI's step runs this check)")
        discovered = _ids(unittest.TestLoader().discover(_SUITE_DIR, top_level_dir=_ENGINE_DIR))
        self.assertTrue(discovered)
        this_file = os.path.relpath(os.path.abspath(__file__), _REPO_DIR).replace(os.sep, "/")
        with tempfile.TemporaryDirectory() as scratch:
            out = os.path.join(scratch, "items.json")
            run = subprocess.run([sys.executable, "-X", "utf8", "-B", "-c", _COLLECT, this_file, out],
                                 cwd=_REPO_DIR, capture_output=True, text=True, timeout=600)
            self.assertEqual(run.returncode, 0, f"pytest could not collect {this_file}:\n{run.stdout[-3000:]}{run.stderr[-3000:]}")
            with open(out, encoding="utf-8") as items:
                collected = collections.Counter(json.load(items))
        missing = sorted((discovered - collected).elements())
        extra = sorted((collected - discovered).elements())
        self.assertEqual((missing, extra), ([], []), "(not collected by CI, collected by CI beyond unittest's count)")

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
                classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
                where = os.path.relpath(path, _SUITE_DIR)
                twice += [f"{where}: class {n}" for n, c in collections.Counter(c.name for c in classes).items() if c > 1]
                for cls in classes:
                    methods = collections.Counter(node.name for node in cls.body
                                                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                                                  and node.name.startswith("test"))
                    twice += [f"{where}: {cls.name}.{n}" for n, c in methods.items() if c > 1]
        self.assertEqual(twice, [])


if __name__ == "__main__":
    unittest.main()

"""The key-store suite in CI's existing step (CSL-59).

CI runs ``python -X utf8 -B -m pytest -q -p no:cacheprovider engine/tests``, which never reaches
``engine/cockpit/keys/tests``. Importing the suite's test classes here makes that same step collect and
run all of them, with no workflow edit and no copied test. Suggested by the Codex (GPT-6) read of #326.

``SuiteCoverageTests`` runs unittest discovery on the suite folder, as a developer would, and fails if it
finds a runnable test (any test method, whatever its class is called, in any test module or test package
there, or a module that fails to import) that this file does not collect. A helper class with no test
methods produces no test and so needs no import. The suite is stdlib ``unittest``; a pytest-style test
function there would not be covered.
"""
from __future__ import annotations

import os
import sys
import unittest

import cockpit.keys.tests as _suite
from cockpit.keys.tests.test_b13_chain import DoctorLineTests, MixedChainTests  # noqa: F401
from cockpit.keys.tests.test_b13_real_library import RealChainerTests  # noqa: F401
from cockpit.keys.tests.test_b14_config_home import FreshAccountTests  # noqa: F401
from cockpit.keys.tests.test_detect import DetectTests, DoctorTests, StoreForHostTests  # noqa: F401
from cockpit.keys.tests.test_r6_finding_7 import FreshHostTests  # noqa: F401
from cockpit.keys.tests.test_store import KeyringHostTests, NoKeychainTests, StaticTests  # noqa: F401


def _cases(suite: unittest.TestSuite) -> set[tuple[type, str]]:
    found: set[tuple[type, str]] = set()
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            found |= _cases(item)
        else:
            found.add((type(item), getattr(item, "_testMethodName", item.id())))
    return found


class SuiteCoverageTests(unittest.TestCase):
    def test_every_runnable_test_of_the_key_store_suite_is_collected_here(self) -> None:
        suite_dir = os.path.dirname(os.path.abspath(_suite.__file__))
        top_dir = os.path.dirname(os.path.dirname(os.path.dirname(suite_dir)))  # the engine folder
        discovered = _cases(unittest.TestLoader().discover(suite_dir, top_level_dir=top_dir))
        collected = {case for case in _cases(unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
                     if case[0] is not SuiteCoverageTests}
        self.assertTrue(discovered)
        missing = sorted(f"{kind.__module__}.{kind.__qualname__}.{name}" for kind, name in discovered - collected)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()

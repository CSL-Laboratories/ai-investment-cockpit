"""The key-store suite in CI's existing step (CSL-59).

CI runs ``python -X utf8 -B -m pytest -q -p no:cacheprovider engine/tests``, which never reaches
``engine/cockpit/keys/tests``. Importing the suite's test classes here makes that same step collect and
run all of them, with no workflow edit and no copied test. ``SuiteCoverageTests`` fails if a test class
is added to the suite and not imported here. Suggested by the Codex (GPT-6) read of #326.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
import unittest

import cockpit.keys.tests as _suite
from cockpit.keys.tests.test_b13_chain import DoctorLineTests, MixedChainTests  # noqa: F401
from cockpit.keys.tests.test_b13_real_library import RealChainerTests  # noqa: F401
from cockpit.keys.tests.test_b14_config_home import FreshAccountTests  # noqa: F401
from cockpit.keys.tests.test_detect import DetectTests, DoctorTests, StoreForHostTests  # noqa: F401
from cockpit.keys.tests.test_r6_finding_7 import FreshHostTests  # noqa: F401
from cockpit.keys.tests.test_store import KeyringHostTests, NoKeychainTests, StaticTests  # noqa: F401


class SuiteCoverageTests(unittest.TestCase):
    def test_every_test_class_of_the_key_store_suite_is_collected_here(self) -> None:
        here = {obj for obj in globals().values() if inspect.isclass(obj) and issubclass(obj, unittest.TestCase)}
        missing = []
        for info in pkgutil.iter_modules(_suite.__path__):
            if not info.name.startswith("test"):
                continue
            module = importlib.import_module(f"{_suite.__name__}.{info.name}")
            for name, obj in vars(module).items():
                if (inspect.isclass(obj) and issubclass(obj, unittest.TestCase) and obj.__module__ == module.__name__
                        and not name.startswith("_") and obj not in here):
                    missing.append(f"{module.__name__}.{name}")
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()

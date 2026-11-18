"""Adversarial: the e2e suite may skip only while the core's plugin_security package is absent.

test_e2e_recursion_core.py skips on a ModuleNotFoundError whose name is exactly
``recursion_core`` or ``recursion_core.plugin_security``, with one exact reason. A broken
plugin_security (an ImportError inside it, a missing dependency or submodule, a missing name)
must raise at import so the run reports an error, never a skip.
"""

from __future__ import annotations

import importlib
import importlib.util
import itertools
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path

E2E = "tests.paper.test_e2e_recursion_core"
E2E_PATH = Path(__file__).with_name("test_e2e_recursion_core.py")
REASON = ("awaiting recursion #328 (recursion_core.plugin_security not importable here; "
          "pinned core aa34e9a7 has none)")
NAMES = frozenset({"recursion_core", "recursion_core.plugin_security"})
_COUNTER = itertools.count()


def _load_fresh() -> types.ModuleType:
    """Execute a fresh copy of the e2e module under a throwaway name (never the real one)."""
    spec = importlib.util.spec_from_file_location("_e2e_fresh_copy_%d" % next(_COUNTER), E2E_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _drop_core() -> None:
    for name in [n for n in sys.modules if n == "recursion_core" or n.startswith("recursion_core.")]:
        del sys.modules[name]


class E2EImportErrorSkipTests(unittest.TestCase):
    def setUp(self) -> None:
        self._modules, self._path = dict(sys.modules), list(sys.path)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        for name in [n for n in sys.modules if n not in self._modules]:
            del sys.modules[name]
        sys.modules.update(self._modules)
        sys.path[:] = self._path
        importlib.invalidate_caches()

    @contextmanager
    def _without_core(self):
        _drop_core()
        try:
            yield
        finally:
            self._restore()

    def test_skip_reason_is_exact_and_only_for_the_named_modules(self) -> None:
        module = importlib.import_module(E2E)
        self.assertEqual(module.AWAITING_328, REASON)
        self.assertEqual(module.SKIPPABLE_MISSING, NAMES)
        self.assertIsInstance(module.SKIPPABLE_MISSING, frozenset)
        self.assertIn(module.CORE_SKIP, (None, REASON))
        self.assertFalse(hasattr(module, "CORE"))
        if module.CORE_SKIP is not None:  # the skip must be honest for this environment
            with self.assertRaises(ModuleNotFoundError) as ctx:
                importlib.import_module("recursion_core.plugin_security")
            self.assertIn(ctx.exception.name, NAMES)

    def test_e2e_suite_skips_only_with_the_exact_reason(self) -> None:
        module = importlib.import_module(E2E)
        result = unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromModule(module).run(result)
        self.assertEqual(result.testsRun, 5)
        self.assertEqual((result.failures, result.errors), ([], []))
        reasons = [reason for _, reason in result.skipped]
        if module.CORE_SKIP is None:
            self.assertEqual(reasons, [])
        else:
            self.assertEqual(reasons, [REASON] * 5)

    def test_absent_core_is_the_only_skip(self) -> None:
        with self.subTest(missing="recursion_core"), self._without_core():
            sys.modules["recursion_core"] = None
            self.assertEqual(_load_fresh().CORE_SKIP, REASON)
        with self.subTest(missing="recursion_core.plugin_security"), self._without_core():
            stub = types.ModuleType("recursion_core")
            stub.__path__ = []
            sys.modules["recursion_core"] = stub
            sys.modules["recursion_core.plugin_security"] = None
            self.assertEqual(_load_fresh().CORE_SKIP, REASON)

    def _load_with_plugin_security(self, source: str) -> BaseException:
        """Load the e2e module against a temp core whose plugin_security/__init__.py is ``source``."""
        with tempfile.TemporaryDirectory(prefix="cockpit-broken-core-") as root, self._without_core():
            package = Path(root, "recursion_core", "plugin_security")
            package.mkdir(parents=True)
            Path(root, "recursion_core", "__init__.py").write_text("", encoding="utf-8")
            (package / "__init__.py").write_text(source, encoding="utf-8")
            sys.path.insert(0, root)
            importlib.invalidate_caches()
            with self.assertRaises(ImportError) as ctx:
                _load_fresh()
            return ctx.exception

    def test_broken_plugin_security_fails_instead_of_skipping(self) -> None:
        with self.subTest("ImportError raised inside plugin_security"):
            exc = self._load_with_plugin_security('raise ImportError("broken inside plugin_security")\n')
            self.assertNotIsInstance(exc, ModuleNotFoundError)
            self.assertIn("broken inside plugin_security", str(exc))
        other_names = {
            "import recursion_core.plugin_security.signing_missing_xyz\n":
                "recursion_core.plugin_security.signing_missing_xyz",
            'raise ModuleNotFoundError("No module named \'cryptography\'", name="cryptography")\n':
                "cryptography",
        }
        for source, name in other_names.items():
            with self.subTest("ModuleNotFoundError for another name", name=name):
                exc = self._load_with_plugin_security(source)
                self.assertIsInstance(exc, ModuleNotFoundError)
                self.assertEqual(exc.name, name)
                self.assertNotIn(exc.name, NAMES)
        with self.subTest("plugin_security imports but lacks the required names"):
            exc = self._load_with_plugin_security("")
            self.assertNotIsInstance(exc, ModuleNotFoundError)
            self.assertIn("ApprovalGate", str(exc))


if __name__ == "__main__":
    unittest.main()

"""CSL-42 / CSL-287 — the cockpit_quant loader must fail loudly, never fall back silently.

These subprocess tests mock ``importlib.util.find_spec`` and ``__import__`` and
never touch a real wheel, so they live outside ``test_costs_rust_parity.py`` and
its module-level ``skipif``: the required Linux gate runs them whether or not the
extension is installed, and the rust-book job still exercises the real wheel.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path


def _python(script: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    app = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = app + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=app,
        env=env,
        check=False,
    )


def test_broken_costs_extension_import_is_not_swallowed():
    script = textwrap.dedent(
        """
        import builtins
        import importlib.util

        real_find = importlib.util.find_spec

        def find_spec(name, package=None):
            if name == "cockpit_quant":
                return real_find("json")
            return real_find(name, package)

        importlib.util.find_spec = find_spec
        real_import = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name == "cockpit_quant":
                raise ImportError("broken extension")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = guarded
        import quant.costs
        """
    )
    ran = _python(script)
    assert ran.returncode != 0
    assert "broken extension" in ran.stderr


def test_partial_costs_extension_is_not_a_silent_python_fallback():
    script = textwrap.dedent(
        """
        import builtins
        import importlib.util
        import types

        real_find = importlib.util.find_spec

        def find_spec(name, package=None):
            if name == "cockpit_quant":
                return real_find("json")
            return real_find(name, package)

        importlib.util.find_spec = find_spec
        real_import = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name == "cockpit_quant":
                return types.ModuleType("cockpit_quant")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = guarded
        import quant.costs
        """
    )
    ran = _python(script)
    assert ran.returncode != 0
    assert "fee_for" in ran.stderr


def test_partial_pnl_extension_is_not_a_silent_python_fallback():
    script = textwrap.dedent(
        """
        import builtins
        import importlib.util
        import types

        real_find = importlib.util.find_spec

        def find_spec(name, package=None):
            if name == "cockpit_quant":
                return real_find("json")
            return real_find(name, package)

        importlib.util.find_spec = find_spec
        real_import = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name == "cockpit_quant":
                module = types.ModuleType("cockpit_quant")
                for attr in ("fee_for", "borrow_cost", "cost_bps", "fee_for_many"):
                    setattr(module, attr, lambda *a, **k: None)
                return module
            return real_import(name, *args, **kwargs)

        builtins.__import__ = guarded
        import quant.pnl
        """
    )
    ran = _python(script)
    assert ran.returncode != 0, ran.stderr
    assert "PnlCore" in ran.stderr


def test_missing_costs_extension_stays_on_python():
    script = textwrap.dedent(
        """
        import importlib.util

        real_find = importlib.util.find_spec

        def find_spec(name, package=None):
            if name == "cockpit_quant":
                return None
            return real_find(name, package)

        importlib.util.find_spec = find_spec
        import quant.costs as costs
        import quant.pnl as pnl
        assert costs.COSTS_BACKEND == "python", costs.COSTS_BACKEND
        assert pnl.PNL_BACKEND == "python", pnl.PNL_BACKEND
        """
    )
    ran = _python(script)
    assert ran.returncode == 0, ran.stderr

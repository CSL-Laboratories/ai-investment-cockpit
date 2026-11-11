"""Import the repo's ``engine/`` Cockpit packages from ``app/`` (item 5 wiring).

Lucas, Nov 10, 8:49pm PST: "Unfreeze app/ only for the paper panel and owner-only key
integration". Lucas, Nov 11, 12:52am PST: "Lift the #318 hold and have new app/ code wire in
#318 and #319". This module exists only so that the app-side paper panel (#318,
``cockpit.gui``) and owner-only keys (#319, ``cockpit.keys``) can be imported; it imports
nothing else from ``engine/``.

The ``engine/`` directory is appended to ``sys.path``, never prepended, so the app's own
top-level packages (``core``, ``orchestrator``, ``tests`` ...) always win. After import the
module's file must sit inside this checkout's ``engine/cockpit/``; a ``cockpit`` package
found anywhere else (an installed distribution, a stray directory) is refused, so the app
never runs an engine it did not ship with. Paper only: PRODUCT.md 5.2 / CP0-v2 (pin
d84cdd26, CSLR-D-012).
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

ENGINE_DIR = Path(__file__).resolve().parents[2] / "engine"
ALLOWED = ("cockpit.gui", "cockpit.keys")


class EngineImportRefused(ImportError):
    """The requested engine module is not allowed here or did not come from ``ENGINE_DIR``."""


def import_engine(name: str) -> ModuleType:
    """Import ``name`` (``cockpit.gui...`` or ``cockpit.keys...``) from this checkout's engine."""
    if type(name) is not str or not any(name == a or name.startswith(a + ".") for a in ALLOWED):
        raise EngineImportRefused(f"engine module {name!r} is outside the paper panel and keys")
    package = ENGINE_DIR / "cockpit"
    if not (package / "__init__.py").is_file():
        raise EngineImportRefused(f"engine tree missing at {ENGINE_DIR}")
    entry = str(ENGINE_DIR)
    if entry not in sys.path:
        sys.path.append(entry)
    module = importlib.import_module(name)
    for loaded in (sys.modules.get("cockpit"), module):
        origin = getattr(loaded, "__file__", None)
        if origin is None or not Path(origin).resolve().is_relative_to(package.resolve()):
            raise EngineImportRefused(f"{getattr(loaded, '__name__', name)!r} was not loaded from {package}")
    return module

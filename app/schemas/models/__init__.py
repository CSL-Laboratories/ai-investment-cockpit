"""WP1a — SQLAlchemy models package. Each model lives in its own module
(so parallel authoring never writes a shared file). Import a specific model
module to register its table on Base.metadata."""
from __future__ import annotations

import importlib
import pkgutil

from .base import Base


def load_all() -> None:
    """Import every model module so Base.metadata sees all tables (create_all / migrations)."""
    for _m in pkgutil.iter_modules(__path__):
        if _m.name != "base":
            importlib.import_module(f"{__name__}.{_m.name}")


__all__ = ["Base", "load_all"]

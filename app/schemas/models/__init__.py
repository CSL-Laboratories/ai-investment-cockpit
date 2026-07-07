"""WP1a — SQLAlchemy models package. Each model lives in its own module
(so parallel authoring never writes a shared file). Import a specific model
module to register its table on Base.metadata."""
from __future__ import annotations

from .base import Base

__all__ = ["Base"]

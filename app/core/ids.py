"""WP0 — id generation (opaque, collision-resistant, prefix-taggable)."""
from __future__ import annotations

import uuid


def new_id(prefix: str = "") -> str:
    h = uuid.uuid4().hex
    return f"{prefix}_{h}" if prefix else h

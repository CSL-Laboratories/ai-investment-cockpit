"""WP0 — model alias map loader (``config/models.yaml``).

Aliases (``triage``/``deep``) for daily use; pinned IDs recorded for reproducible
research and audit lineage (docs/enhancements-and-additions E-06). Codex is research/
coding only — NEVER a decision or order stage (Invariant 2; docs/security-architecture
§4 L5).
"""
from __future__ import annotations

from pathlib import Path

import yaml

_MODELS_YAML = Path(__file__).with_name("models.yaml")


def load_model_map() -> dict:
    with _MODELS_YAML.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["aliases"]


def runtime_for(alias: str) -> str:
    return load_model_map()[alias]["runtime"]

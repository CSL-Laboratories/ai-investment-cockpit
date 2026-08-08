"""WP0 + CXP-05a — provider-aware model alias map.

``triage`` and ``deep`` remain stable job-role names.  Each can resolve to a Claude or Codex
model without changing workflow code, while the exact resolved runtime/model pin is still
recorded in audit lineage.  Provider selection never changes order authority: paper execution
is human-gated and live/auto retains its separate Claude-only pin.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_MODELS_YAML = Path(__file__).with_name("models.yaml")


def load_model_map() -> dict:
    with _MODELS_YAML.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["aliases"]


def _runtime_value(runtime) -> str | None:
    if runtime is None:
        return None
    return str(getattr(runtime, "value", runtime)).strip().lower()


def resolve_model(alias: str, runtime=None) -> dict:
    """Resolve ``alias`` for ``runtime`` while preserving the legacy default mapping.

    The base fields remain Claude so historical callers and auto-mode policy tests keep their
    conservative default.  A provider override must be explicit in YAML; missing overrides fail
    closed instead of silently falling back to another provider.
    """
    entry = dict(load_model_map()[alias])
    selected = _runtime_value(runtime) or str(entry["runtime"])
    if selected == entry["runtime"]:
        return {k: v for k, v in entry.items() if k != "providers"}
    override = (entry.get("providers") or {}).get(selected)
    if not isinstance(override, dict):
        raise ValueError(f"model alias {alias!r} has no {selected!r} provider mapping")
    return {"runtime": selected, **override}


def runtime_for(alias: str, selected_runtime=None) -> str:
    return str(resolve_model(alias, selected_runtime)["runtime"])


def cli_model_for(alias: str, runtime=None) -> str:
    return str(resolve_model(alias, runtime).get("cli") or alias)


def pinned_model_for(alias: str, runtime=None) -> str:
    resolved = resolve_model(alias, runtime)
    return str(resolved.get("pinned") or resolved.get("cli") or alias)


def configured_decision_runtime(settings) -> str:
    """Return the validated provider selected by ``Settings`` as a plain audit string."""
    return _runtime_value(getattr(settings, "decision_runtime", "claude")) or "claude"


def runtime_allowed_for_mode(runtime: str, mode) -> bool:
    """Codex is a human-gated read-only/paper provider, never a live/auto provider."""
    mode_value = str(getattr(mode, "value", mode)).strip().lower()
    return runtime != "codex" or mode_value in {"read_only", "paper"}

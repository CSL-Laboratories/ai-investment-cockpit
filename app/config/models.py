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

# CSL-98: Haiku 5.5 bills the entire request at 5x once the prompt exceeds this
# many input tokens. The workflow cap stays strictly below it.
HAIKU_55_LONG_CONTEXT_TOKENS = 100_000
DEFAULT_CONTEXT_TOKEN_CAP = 90_000

# Trading-decision Claude ids. The CLI value maps to the audit pin (E-06).
# Haiku 4.5 is the fallback; Haiku 5.5 is opt-in when that id is on the CLI.
DECISION_MODEL_PINS = {
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5-20251001",
    "claude-haiku-5-5": "claude-haiku-5-5",
}
DEFAULT_DECISION_MODEL = "claude-haiku-4-5"

# Aliases whose runtime must be Claude before an auto rung can arm (Inv 2).
# ``summary`` is awareness-only and is not in this list.
DECISION_STAGE_ALIASES = ("triage", "deep", "decision")


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
    job_class = entry.get("job_class")
    if selected == entry["runtime"]:
        resolved = {k: v for k, v in entry.items() if k != "providers"}
    else:
        override = (entry.get("providers") or {}).get(selected)
        if not isinstance(override, dict):
            raise ValueError(f"model alias {alias!r} has no {selected!r} provider mapping")
        resolved = {"runtime": selected, **override}
        if job_class is not None:
            resolved["job_class"] = job_class
    return resolved


def runtime_for(alias: str, selected_runtime=None) -> str:
    return str(resolve_model(alias, selected_runtime)["runtime"])


def cli_model_for(alias: str, runtime=None) -> str:
    return str(resolve_model(alias, runtime).get("cli") or alias)


def pinned_model_for(alias: str, runtime=None) -> str:
    resolved = resolve_model(alias, runtime)
    return str(resolved.get("pinned") or resolved.get("cli") or alias)


def job_class_for(alias: str, runtime=None) -> str:
    """``decision`` is a trading proposal. Summary and classification are awareness."""
    resolved = resolve_model(alias, runtime)
    return str(resolved.get("job_class") or alias)


def resolve_decision_claude(model_id: str) -> tuple[str, str]:
    """Return ``(cli_model, pinned_model)`` for an allowlisted Claude decision id.

    Anything else fails closed. Opus and non-Claude ids are not decision models.
    """
    key = str(model_id or "").strip()
    pinned = DECISION_MODEL_PINS.get(key)
    if pinned is None:
        allowed = ", ".join(sorted(DECISION_MODEL_PINS))
        raise ValueError(
            f"decision model {model_id!r} is not an allowlisted Claude Haiku id ({allowed})"
        )
    return key, pinned


def configured_decision_runtime(settings) -> str:
    """Return the validated provider selected by ``Settings`` as a plain audit string."""
    return _runtime_value(getattr(settings, "decision_runtime", "claude")) or "claude"


def runtime_allowed_for_mode(runtime: str, mode) -> bool:
    """Codex is a human-gated read-only/paper provider, never a live/auto provider."""
    mode_value = str(getattr(mode, "value", mode)).strip().lower()
    return runtime != "codex" or mode_value in {"read_only", "paper"}

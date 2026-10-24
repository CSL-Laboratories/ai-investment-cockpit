"""Append-only ledger of strategy configs, parameter sets, and prompt versions.

DSR's trial count is the number of distinct config hashes, not the number of times the same
config was re-run. Repeats are still stored so the file shows the history.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sidecar.chainlog import ChainLog
from sidecar.jsonutil import sha256_canonical

KINDS = frozenset({"strategy_config", "parameter_set", "prompt_version"})


class TrialLedger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._log = ChainLog(self.path)

    def append(
        self,
        *,
        kind: str,
        name: str,
        config: dict[str, Any],
        recorded_at: datetime | None = None,
    ) -> dict:
        if kind not in KINDS:
            raise ValueError(f"trial kind must be one of {sorted(KINDS)}")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("trial name must be a non-empty string")
        if not isinstance(config, dict):
            raise ValueError("trial config must be an object")
        moment = recorded_at or datetime.now(timezone.utc)
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("recorded_at must be timezone-aware")
        return self._log.append(
            {
                "kind": kind,
                "name": name.strip(),
                "config_hash": sha256_canonical(config),
                "config": config,
                "recorded_at": moment.astimezone(timezone.utc).isoformat(),
            }
        )

    def entries(self) -> list[dict]:
        return self._log.entries()

    def distinct_config_count(self) -> int:
        return len({entry["config_hash"] for entry in self.entries()})

    def head_hash(self) -> str | None:
        rows = self.entries()
        if not rows:
            return None
        return rows[-1]["entry_hash"]

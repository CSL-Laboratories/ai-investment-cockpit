"""Decision profile: what the Cockpit asks of a route, and where Invariant 2 sits.

Invariant 2 (CLAUDE.md): the decision path is pinned to Claude in auto modes.
AGENTS.md "Codex boundary" lets Codex run schema-gated analysis jobs only in
``read_only`` and human-approved ``paper`` mode; ``review_live``, ``auto_veto``
and ``auto_full`` stay outside it. This profile restates that text and nothing
more. It is NOT an amendment: no provider-neutral change to Invariant 2 exists
or is assumed here (CSL-515 question CR3 and the O5 candidate #181 are open).

Workload ids are neutral tokens. The Cockpit job each one stands for lives only
in this table and is never placed in a route request.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from successor.route_consumer.pin import PACKAGE_DIR

SCHEMA_DIR = PACKAGE_DIR / "output_schemas"

MODES = ("read_only", "paper", "review_live", "auto_veto", "auto_full")
CODEX_ELIGIBLE_MODES = frozenset({"read_only", "paper"})
PINNED_ENGINE = "claude"
KNOWN_ENGINES = ("claude", "codex")
FAIL_CLOSED_TO = "watch_only"


class ProfileRefusal(Exception):
    """A request the profile refuses before any route is contacted."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def engines_allowed(mode: str) -> tuple[str, ...]:
    if mode not in MODES:
        raise ProfileRefusal("unknown_mode")
    if mode in CODEX_ELIGIBLE_MODES:
        return KNOWN_ENGINES
    return (PINNED_ENGINE,)


def canonical_digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WorkloadProfile:
    workload_id: str
    cockpit_job: str
    output_schema_name: str
    output_schema_digest: str
    max_turns: int = 6
    timeout_s: float = 120.0
    privacy_class: str = "private"
    max_output_bytes: int = 262_144
    authority: str = "analysis-only"


def _profile(workload_id: str, job: str, schema: str, digest: str) -> WorkloadProfile:
    return WorkloadProfile(workload_id, job, schema, digest)


_PROFILES = (
    _profile(
        "workload-d01", "briefing", "MorningBrief",
        "sha256:c3b2b7999d697b53d8a36cf56fe6405b863f7a0d41205c7ccc1aaef037931450",
    ),
    _profile(
        "workload-d02", "triage", "TriageResult",
        "sha256:e00b598d6432ef15d202f5dde0ff65c87998d48c200703874316e02537c5a78d",
    ),
    _profile(
        "workload-d03", "grounding", "GroundingCheck",
        "sha256:b2352f81ffce8f70fec6b1b6bbd34f9f3762287304146693e93f20a0a989fab4",
    ),
    _profile(
        "workload-d04", "proposal-drafting", "TradeProposal",
        "sha256:aa0975a5a2f83be69b99db1431c459815010c07aecd7305da4037031f72cda06",
    ),
    _profile(
        "workload-d05", "interrogation", "InterrogationAnswer",
        "sha256:35ce8cbb3b8d56b53c943321bb37eef1b8e3a0657f11ed9a0b59a8f0a14524e4",
    ),
)

DECISION_PROFILE = MappingProxyType({item.workload_id: item for item in _PROFILES})


def load_output_schema(profile: WorkloadProfile) -> dict[str, Any]:
    """The vendored output schema for ``profile``, proved against its digest."""

    path: Path = SCHEMA_DIR / f"{profile.output_schema_name}.schema.json"
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ProfileRefusal("output_schema_unreadable") from None
    check_output_schema(profile, schema)
    return schema


def check_output_schema(profile: WorkloadProfile, schema: dict[str, Any]) -> None:
    if canonical_digest(schema) != profile.output_schema_digest:
        raise ProfileRefusal("output_schema_digest_mismatch")

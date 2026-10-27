"""Build ``RouteExecutionRequest@0`` records. The prompt is digested, never stored.

The request grants no tools, sends the prompt on stdin only, and asks for one
JSON output with one repair attempt and no retained raw output. Those are
constants of the zero-tools profile, not parameters.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from successor.route_consumer.profile import WorkloadProfile, canonical_digest


@dataclass(frozen=True)
class RouteBinding:
    """The route a request is addressed to. The route layer supplies it.

    Cockpit holds exactly one binding per consumer and never picks between
    routes; which route that is stays Recursion's decision.
    """

    route_id: str
    engine_id: str
    route_descriptor_digest: str
    process_requirement_id: str
    cli_model: str
    tenant: str
    credential_provider: str
    credential_name: str


def prompt_digest(prompt: str) -> str:
    return "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def build_request_record(
    *,
    request_id: str,
    binding: RouteBinding,
    profile: WorkloadProfile,
    prompt: str,
    output_schema: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": "RouteExecutionRequest@0",
        "id": request_id,
        "route_id": binding.route_id,
        "engine_id": binding.engine_id,
        "route_descriptor_digest": binding.route_descriptor_digest,
        "process_requirement_id": binding.process_requirement_id,
        "workload_id": profile.workload_id,
        "session": {
            "type": "SessionRef@0",
            "tenant": binding.tenant,
            "route": binding.route_id,
            "session": request_id,
        },
        "credential_ref": {
            "type": "CredentialRef@0",
            "tenant_id": binding.tenant,
            "provider": binding.credential_provider,
            "name": binding.credential_name,
        },
        "cli_model": binding.cli_model,
        "tool_profile": "zero-tools",
        "allowed_tools": [],
        "prompt_channel": "stdin",
        "prompt_digest": prompt_digest(prompt),
        "max_turns": profile.max_turns,
        "timeout_ms": int(profile.timeout_s * 1000),
        "cwd_ref": None,
        "privacy_class": profile.privacy_class,
        "output": {
            "type": "OutputContract@0",
            "format": "json",
            "schema_digest": canonical_digest(output_schema),
            "schema_path_ref": None,
            "repair_attempts": 1,
            "max_output_bytes": profile.max_output_bytes,
            "raw_output_retained": False,
        },
    }

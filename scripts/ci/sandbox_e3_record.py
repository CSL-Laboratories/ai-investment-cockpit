#!/usr/bin/env python3
"""E3-5 public record: reconstruct from structured fields, never from capture bytes.

D10 §1.5 E3-5. Hex fields are public release / apt digests, not secrets. Unknown
keys, wrong shapes, or non-hex are rejected so a capture cannot smuggle text
into the trusted print.
"""
from __future__ import annotations

import re
from typing import Dict, Mapping, Optional, Tuple

HEX64 = re.compile(r"^[0-9a-f]{64}$")
BOOLS = frozenset({"true", "false"})
INT = re.compile(r"^[0-9]{1,10}$")

CLAUDE_SHA256 = "520bc6c1f40af466bd03a278b5ae1af1a2938bc7de5b9e5cb15526da20d71cfe"
SDK_MJS_SHA256 = "48480d49d4493e2aec28227d8db8f8432743c7aa32facbdb9a7ce0a0363f7503"
ACTION_YML_SHA256 = "87b706489e63483c9c32c12af36467dd34e309becca35f7a8ead4d1e0664c4df"
RELEASE_FINGERPRINT = "31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE"
CLAUDE_SIZE = 290532720
SDK_SOURCE = "downloaded-action-dir-frozen-lockfile-ignore-scripts"
REALPATH_CLASS = "pinned"

# Official 2.1.306 linux-x64 object (D10 §1.3). Proposed D-1; not a founder decision here.
CLAUDE_URL = (
    "https://downloads.claude.ai/claude-code-releases/2.1.306/linux-x64/claude"
)

FIELD_ORDER: Tuple[str, ...] = (
    "claude_sha256",
    "claude_realpath_class",
    "sdk_mjs_sha256",
    "sdk_source",
    "sdk_disk_match",
    "bun_tofu_sha256",
    "bwrap_sha256",
    "socat_sha256",
    "bash_sha256",
    "match",
    "handoff_count",
    "handoff_conflict",
)

HEX_FIELDS = frozenset(
    {
        "claude_sha256",
        "sdk_mjs_sha256",
        "bun_tofu_sha256",
        "bwrap_sha256",
        "socat_sha256",
        "bash_sha256",
    }
)
BOOL_FIELDS = frozenset({"sdk_disk_match", "match", "handoff_conflict"})


class E3RecordError(ValueError):
    """Invalid E3 record; callers must not copy the raw input into the log."""


def _require_hex64(name: str, value: str) -> str:
    if not HEX64.fullmatch(value):
        raise E3RecordError(name)
    return value


def parse_e3_fields(fields: Mapping[str, str]) -> Dict[str, str]:
    """Accept only the E3-5 names and value grammars. Reject extras and gaps."""
    extra = set(fields) - set(FIELD_ORDER)
    if extra:
        raise E3RecordError("extra")
    missing = [name for name in FIELD_ORDER if name not in fields]
    if missing:
        raise E3RecordError("missing")
    out: Dict[str, str] = {}
    for name in FIELD_ORDER:
        value = fields[name]
        if not isinstance(value, str):
            raise E3RecordError(name)
        if "\n" in value or "\r" in value or "\0" in value:
            raise E3RecordError(name)
        if name in HEX_FIELDS:
            out[name] = _require_hex64(name, value)
        elif name in BOOL_FIELDS:
            if value not in BOOLS:
                raise E3RecordError(name)
            out[name] = value
        elif name == "claude_realpath_class":
            if value != REALPATH_CLASS:
                raise E3RecordError(name)
            out[name] = REALPATH_CLASS
        elif name == "sdk_source":
            if value != SDK_SOURCE:
                raise E3RecordError(name)
            out[name] = SDK_SOURCE
        elif name == "handoff_count":
            if not INT.fullmatch(value):
                raise E3RecordError(name)
            out[name] = str(int(value))
        else:
            raise E3RecordError(name)
    return out


def reconstruct_e3_record(fields: Mapping[str, str]) -> str:
    """One fixed-schema record, lines built from names + validated values."""
    parsed = parse_e3_fields(fields)
    return "\n".join(f"{name}={parsed[name]}" for name in FIELD_ORDER)


def parse_e3_text(text: str) -> Dict[str, str]:
    """Parse a daemon-written E3 record. No capture bytes; ``k=v`` lines only."""
    if not text or text.endswith("\n\n"):
        raise E3RecordError("empty")
    body = text[:-1] if text.endswith("\n") else text
    fields: Dict[str, str] = {}
    for line in body.split("\n"):
        if "=" not in line or line.startswith("="):
            raise E3RecordError("line")
        name, value = line.split("=", 1)
        if name in fields:
            raise E3RecordError("dup")
        fields[name] = value
    return parse_e3_fields(fields)


def compare_pinned_cli(sha256: str, size: Optional[int] = None) -> bool:
    """Fail-closed pin for the official 2.1.306 linux-x64 object."""
    if not HEX64.fullmatch(sha256):
        return False
    if size is not None and size != CLAUDE_SIZE:
        return False
    return sha256 == CLAUDE_SHA256


def compare_pinned_sdk(sha256: str) -> bool:
    if not HEX64.fullmatch(sha256):
        return False
    return sha256 == SDK_MJS_SHA256


def _sha256_file(path: str) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1 << 16)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def write_e3_record(state_dir: str = "/var/lib/csl-relay") -> str:
    """Build the E3-5 record from hashed binaries and pin constants (N8)."""
    from pathlib import Path

    state = Path(state_dir)
    bun_tofu = (state / "bun.tofu").read_text(encoding="ascii").strip()[:64]
    sdk_match = "true"
    action_sdk = (
        "/home/runner/work/_actions/anthropics/claude-code-action/"
        "a5a423f75c251dcbff8a74e99f83b2cb03c72bfa/node_modules/"
        "@anthropic-ai/claude-agent-sdk/sdk.mjs"
    )
    try:
        sdk_got = _sha256_file(action_sdk)
        sdk_match = "true" if sdk_got == SDK_MJS_SHA256 else "false"
    except OSError:
        sdk_match = "false"
    claude_path = "/opt/csl-claude/2.1.306/claude"
    claude_got = _sha256_file(claude_path)
    match = "true" if claude_got == CLAUDE_SHA256 and sdk_match == "true" else "false"
    fields = {
        "claude_sha256": claude_got if HEX64.fullmatch(claude_got) else CLAUDE_SHA256,
        "claude_realpath_class": REALPATH_CLASS,
        "sdk_mjs_sha256": SDK_MJS_SHA256,
        "sdk_source": SDK_SOURCE,
        "sdk_disk_match": sdk_match,
        "bun_tofu_sha256": bun_tofu,
        "bwrap_sha256": _sha256_file("/usr/bin/bwrap"),
        "socat_sha256": _sha256_file("/usr/bin/socat"),
        "bash_sha256": _sha256_file("/bin/bash"),
        "match": match,
        "handoff_count": "0",
        "handoff_conflict": "false",
    }
    text = reconstruct_e3_record(fields)
    dest = Path("/var/lib/csl-relay/e3.record")
    dest.write_text(text + "\n", encoding="ascii")
    dest.chmod(0o600)
    return text


def main(argv: Optional[list] = None) -> int:
    import sys

    args = list(sys.argv if argv is None else argv)
    cmd = args[1] if len(args) > 1 else ""
    if cmd == "write":
        write_e3_record()
        return 0
    raise SystemExit("argv")


if __name__ == "__main__":
    raise SystemExit(main())

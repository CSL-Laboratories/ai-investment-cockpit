#!/usr/bin/env python3
"""Report Claude Code tool-permission denials from the execution file.

Prints only tool names and a redacted, truncated input summary. Never prints
tool results, messages, or environment. Always exits 0.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

MAX_INPUT = 200
SECRET_RE = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}"
    r"|[A-Za-z0-9+/_-]{40,})"
)


def redact(text: str) -> str:
    text = SECRET_RE.sub("[redacted]", text)
    text = " ".join(text.split())
    return text if len(text) <= MAX_INPUT else text[:MAX_INPUT] + "..."


def summarize_input(tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        for key in ("command", "file_path", "path", "pattern", "url"):
            if key in tool_input:
                return redact(str(tool_input[key]))
        return redact(",".join(sorted(str(k) for k in tool_input)))
    return redact(str(tool_input or ""))


def find_denials(data: Any) -> list[dict[str, Any]]:
    items = data if isinstance(data, list) else [data]
    for item in reversed(items):
        if isinstance(item, dict) and item.get("type") == "result":
            denials = item.get("permission_denials") or []
            return [d for d in denials if isinstance(d, dict)]
    return []


def report(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return ["claude-denials: execution file unreadable"]
    denials = find_denials(data)
    lines = [f"claude-denials: count={len(denials)}"]
    for d in denials:
        lines.append(
            f"claude-denials: tool={redact(str(d.get('tool_name') or '?'))} "
            f"input={summarize_input(d.get('tool_input'))}"
        )
    return lines


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    lines = report(path) if path else ["claude-denials: no execution file"]
    for line in lines:
        print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("### Claude tool-permission denials\n\n")
            handle.write("\n".join(f"- `{line}`" for line in lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

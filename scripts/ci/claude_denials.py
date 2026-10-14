#!/usr/bin/env python3
"""Report Claude Code tool-permission denials from the execution file.

Prints only tool names and a coarse input shape: at most the first few
words of a command, where every word other than the program name, a fixed
list of known gate/git words, shell operators and repo paths is replaced
by [arg]. Arguments, URLs, flag values and
anything credential-like are therefore never echoed. Never prints tool
results, messages, or environment. Always exits 0.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

MAX_WORDS = 6
SAFE_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._/-]{0,39}$|^-{1,2}[A-Za-z][A-Za-z0-9-]{0,24}$")
SHELL_OPS = frozenset({"&&", "||", "|", ";", ">", ">>", "<"})
SAFE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,39}$")


KNOWN_WORDS = frozenset(
    {
        ".", "-B", "-X", "-m", "-q", "--force", "add", "api", "app", "app/tests", "cd",
        "check", "commit", "diff", "gh", "git", "log", "origin", "pr", "push", "pytest",
        "python", "python3", "rev-parse", "ruff", "status", "utf8", "view",
    }
)
REPO_PATH_RE = re.compile(r"^(?:app|tools|scripts|docs|experiments|\.github)/[A-Za-z0-9._/-]{1,80}$")


def _safe_word(index: int, word: str) -> bool:
    if word in SHELL_OPS or word in KNOWN_WORDS or REPO_PATH_RE.fullmatch(word):
        return True
    # The program name (first word) may be any plain identifier.
    return index == 0 and bool(SAFE_WORD_RE.fullmatch(word)) and not word.startswith("-")


def shape(text: str) -> str:
    """Keep the program name, known words, operators and repo paths; mask the rest."""
    words = str(text).split()
    out = [w if _safe_word(i, w) else "[arg]" for i, w in enumerate(words[:MAX_WORDS])]
    if len(words) > MAX_WORDS:
        out.append(f"...(+{len(words) - MAX_WORDS} words)")
    return " ".join(out)


def safe_name(value: Any) -> str:
    name = str(value or "?")
    return name if SAFE_NAME_RE.fullmatch(name) else "[tool]"


def summarize_input(tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        for key in ("command", "file_path", "path", "pattern"):
            if key in tool_input:
                return shape(tool_input[key])
        return "keys=" + ",".join(sorted(safe_name(k) for k in tool_input))
    return shape(tool_input or "")


def find_denials(data: Any) -> list[dict[str, Any]]:
    """Collect permission_denials from every result record."""
    items = data if isinstance(data, list) else [data]
    found: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict) and item.get("type") == "result":
            denials = item.get("permission_denials") or []
            found.extend(d for d in denials if isinstance(d, dict))
    return found


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
            f"claude-denials: tool={safe_name(d.get('tool_name'))} "
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
            # Plain fenced block; backticks and markup cannot escape it.
            body = "\n".join(line.replace("`", "'") for line in lines)
            handle.write("```text\n" + body + "\n```\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

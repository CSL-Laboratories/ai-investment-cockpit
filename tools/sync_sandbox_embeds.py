#!/usr/bin/env python3
"""Rewrite quoted heredocs in claude.yml from scripts/ci sources (CSL-474)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
YML = ROOT / ".github" / "workflows" / "claude.yml"
SCRIPTS = ROOT / "scripts" / "ci"

TAGS = {
    "CSL_E3_ENVIRON": SCRIPTS / "sandbox_e3_environ.py",
    "CSL_E3_RECORD": SCRIPTS / "sandbox_e3_record.py",
    "CSL_E3_BIND": SCRIPTS / "sandbox_e3_bind.py",
    "CSL_E3_BOOTSTRAP": SCRIPTS / "sandbox_e3_bootstrap.sh",
    "CSL_E3_CLI_PIN": SCRIPTS / "sandbox_e3_cli_pin.sh",
    "CSL_C_COMMON_H": SCRIPTS / "sandbox_common.h",
    "CSL_C_COMMON_C": SCRIPTS / "sandbox_common.c",
    "CSL_C_FILTER_H": SCRIPTS / "sandbox_filter.h",
    "CSL_C_FILTER_C": SCRIPTS / "sandbox_filter.c",
    "CSL_C_ARGV_H": SCRIPTS / "sandbox_argv.h",
    "CSL_C_ARGV_C": SCRIPTS / "sandbox_argv.c",
    "CSL_C_LAUNCH": SCRIPTS / "sandbox_launch.c",
    "CSL_C_CAPTURE": SCRIPTS / "sandbox_capture.c",
    "CSL_C_RELAY": SCRIPTS / "sandbox_relay.c",
    "CSL_C_PIN": SCRIPTS / "sandbox_c.pin",
}

C_ORDER = (
    ("CSL_C_COMMON_H", "/opt/csl-claude/src/sandbox_common.h"),
    ("CSL_C_COMMON_C", "/opt/csl-claude/src/sandbox_common.c"),
    ("CSL_C_FILTER_H", "/opt/csl-claude/src/sandbox_filter.h"),
    ("CSL_C_FILTER_C", "/opt/csl-claude/src/sandbox_filter.c"),
    ("CSL_C_ARGV_H", "/opt/csl-claude/src/sandbox_argv.h"),
    ("CSL_C_ARGV_C", "/opt/csl-claude/src/sandbox_argv.c"),
    ("CSL_C_LAUNCH", "/opt/csl-claude/src/sandbox_launch.c"),
    ("CSL_C_CAPTURE", "/opt/csl-claude/src/sandbox_capture.c"),
    ("CSL_C_RELAY", "/opt/csl-claude/src/sandbox_relay.c"),
    ("CSL_C_PIN", "/opt/csl-claude/lib/sandbox_c.pin"),
    ("CSL_E3_CLI_PIN", "/opt/csl-claude/lib/sandbox_e3_cli_pin.sh"),
)

INDENT = "          "


def indent_body(source: str) -> str:
    lines = source.rstrip("\n").split("\n")
    return "\n".join(INDENT + line if line else INDENT.rstrip() for line in lines)


def replace_or_note(text: str, tag: str, source: str) -> str:
    start = f"<<'{tag}'\n"
    if start not in text:
        return text
    pattern = re.compile(
        rf"(<<'{re.escape(tag)}'\n)(.*?)(\n[ \t]*{re.escape(tag)}\n)",
        re.S,
    )
    body = indent_body(source)

    def repl(match: re.Match[str]) -> str:
        # group(3) already starts with the newline before TAG. Do not add
        # another newline or the written file gains a trailing blank line (N3).
        return match.group(1) + body + match.group(3)

    return pattern.sub(repl, text)


def cats_block() -> str:
    chunks = ["          mkdir -p /opt/csl-claude/src /opt/csl-relay/bin"]
    for tag, dest in C_ORDER:
        chunks.append(f"          cat > {dest} <<'{tag}'")
        chunks.append(indent_body(TAGS[tag].read_text(encoding="utf-8")))
        chunks.append(f"          {tag}")
    return "\n".join(chunks) + "\n"


def insert_c_cats(text: str) -> str:
    if "<<'CSL_C_LAUNCH'" in text:
        return text
    marker = "          cat > /opt/csl-claude/lib/sandbox_e3_bootstrap.sh <<'CSL_E3_BOOTSTRAP'\n"
    if marker not in text:
        raise SystemExit("bootstrap marker missing")
    return text.replace(marker, cats_block() + marker)


OUTBOX = ROOT / "scripts" / "ci" / "claude_fixer_outbox.py"
SHELL = "/usr/bin/sudo -n /bin/bash --noprofile --norc -e -o pipefail {0}"
PY_ORDER = (
    ("CSL_E3_ENVIRON", "/opt/csl-claude/lib/sandbox_e3_environ.py"),
    ("CSL_E3_RECORD", "/opt/csl-claude/lib/sandbox_e3_record.py"),
    ("CSL_E3_BIND", "/opt/csl-claude/lib/sandbox_e3_bind.py"),
)
C_A = C_ORDER[:6]
C_B = C_ORDER[6:]


def cat_group(pairs) -> str:
    chunks = []
    for tag, dest in pairs:
        chunks.append(f"          cat > {dest} <<'{tag}'")
        chunks.append(indent_body(TAGS[tag].read_text(encoding="utf-8")))
        chunks.append(f"          {tag}")
    return "\n".join(chunks) + "\n"


def make_step(name: str, env_block: str, body: str) -> str:
    return (
        f"      - name: {name}\n"
        f"        shell: {SHELL}\n"
        f"{env_block}"
        f"        run: |\n"
        f"          set -euo pipefail\n"
        f"{body}"
    )


def _generated_0b_steps(env: str) -> str:
    helper = (
        "          mkdir -p /opt/csl-claude/lib /opt/csl-claude/src "
        "/opt/csl-relay/bin /opt/csl-bun /var/lib/csl-relay\n"
        "          # sudo env_reset drops $RUNNER_TEMP; use the compiled-in path.\n"
        "          mkdir -p /home/runner/work/_temp/claude-fixer-guard\n"
        "          cat > /home/runner/work/_temp/claude-fixer-guard/model-api "
        "<<'CLAUDE_FIXER_OUTBOX'\n"
        f"{indent_body(OUTBOX.read_text(encoding='utf-8'))}\n"
        "          CLAUDE_FIXER_OUTBOX\n"
    )
    py = "          mkdir -p /opt/csl-claude/lib\n" + cat_group(PY_ORDER)
    ca = "          mkdir -p /opt/csl-claude/src\n" + cat_group(C_A)
    cb = (
        "          mkdir -p /opt/csl-claude/src /opt/csl-claude/lib "
        "/opt/csl-relay/bin\n" + cat_group(C_B)
    )
    boot = (
        "          mkdir -p /opt/csl-claude/lib\n"
        "          cat > /opt/csl-claude/lib/sandbox_e3_bootstrap.sh "
        "<<'CSL_E3_BOOTSTRAP'\n"
        f"{indent_body(TAGS['CSL_E3_BOOTSTRAP'].read_text(encoding='utf-8'))}\n"
        "          CSL_E3_BOOTSTRAP\n"
        "          /bin/bash --noprofile --norc -e -o pipefail "
        "/opt/csl-claude/lib/sandbox_e3_bootstrap.sh\n"
    )
    return "\n".join([
        make_step("E3-0-early write helper", env, helper),
        make_step("E3-0-early write python helpers", env, py),
        make_step("E3-0-early write C sources A", env, ca),
        make_step("E3-0-early write C sources B", env, cb),
        make_step("E3-0-early root bootstrap", env, boot),
    ]) + "\n"


def split_0b_into_steps(text: str) -> str:
    """Keep each run: under actionlint's 60 KiB pipe limit."""
    if "E3-0-early write helper" in text:
        return text
    marker = "      - name: E3-0-early root bootstrap\n"
    checkout = "      - uses: actions/checkout@"
    n = 0
    out = []
    pos = 0
    while True:
        start = text.find(marker, pos)
        if start < 0:
            out.append(text[pos:])
            break
        out.append(text[pos:start])
        env_at = text.find("        env:\n", start)
        run_at = text.find("        run: |\n", start)
        end = text.find(checkout, start)
        if env_at < 0 or run_at < 0 or end < 0 or not (start < env_at < run_at < end):
            raise SystemExit("0b markers out of order")
        env = text[env_at:run_at]
        out.append(_generated_0b_steps(env))
        pos = end
        n += 1
    if n != 2:
        raise SystemExit(f"expected 2 0b steps to split, got {n}")
    return "".join(out)


def main() -> None:
    text = YML.read_text(encoding="utf-8")
    text = insert_c_cats(text)
    text = split_0b_into_steps(text)
    for tag, path in TAGS.items():
        if f"<<'{tag}'" in text:
            text = replace_or_note(text, tag, path.read_text(encoding="utf-8"))
    if "<<'CLAUDE_FIXER_OUTBOX'" in text:
        text = replace_or_note(text, "CLAUDE_FIXER_OUTBOX", OUTBOX.read_text(encoding="utf-8"))
    YML.write_text(text, encoding="utf-8")
    print("synced", YML)


if __name__ == "__main__":
    main()

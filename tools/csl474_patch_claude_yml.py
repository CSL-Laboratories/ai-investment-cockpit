#!/usr/bin/env python3
"""Structural CSL-474 D10 edits to claude.yml (E3). Idempotent-ish."""
from __future__ import annotations

import re
from pathlib import Path

YML = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "claude.yml"
OUTBOX_SRC = Path(__file__).resolve().parents[1] / "scripts" / "ci" / "claude_fixer_outbox.py"
INDENT = "          "


def indent_body(source: str) -> str:
    lines = source.rstrip("\n").split("\n")
    return "\n".join(INDENT + line if line else INDENT.rstrip() for line in lines)


def drop_setup_python_and_pip(text: str) -> str:
    pattern = re.compile(
        r"\n      - uses: actions/setup-python@v5\n"
        r"        with:\n"
        r'          python-version: "3.12"\n'
        r"      - name: Install acceptance-gate deps\n"
        r"        working-directory: \$\{\{ runner\.temp \}\}\n"
        r"        run: python -P -m pip install[^\n]+\n",
    )
    new, n = pattern.subn("\n", text)
    print("dropped setup-python/pip", n)
    return new


def helper_cat_block() -> str:
    body = indent_body(OUTBOX_SRC.read_text(encoding="utf-8"))
    return (
        "          mkdir -p /home/runner/work/_temp/claude-fixer-guard\n"
        "          cat > /home/runner/work/_temp/claude-fixer-guard/model-api <<'CLAUDE_FIXER_OUTBOX'\n"
        f"{body}\n"
        "          CLAUDE_FIXER_OUTBOX\n"
    )


def insert_helper_into_0b(text: str) -> str:
    if text.count("<<'CLAUDE_FIXER_OUTBOX'") >= 4:
        return text
    marker = "          mkdir -p /opt/csl-claude/lib /opt/csl-bun /var/lib/csl-relay\n"
    if marker not in text:
        raise SystemExit("0b mkdir marker missing")
    # mention 0b only first; fixer copy happens later
    return text.replace(marker, marker + helper_cat_block(), 1)


def expand_0b_env_mention(text: str) -> str:
    old = """        env:
          BASH_ENV: ""
          LD_PRELOAD: ""
          LD_LIBRARY_PATH: ""
        run: |
          set -euo pipefail
          mkdir -p /opt/csl-claude/lib /opt/csl-bun /var/lib/csl-relay
"""
    new = """        env:
          BASH_ENV: ""
          LD_PRELOAD: ""
          LD_LIBRARY_PATH: ""
          REPO: ${{ github.repository }}
          PR_NUMBER: ${{ needs.resolve-pr.outputs.pr_number }}
          HEAD_SHA: ${{ needs.mention-prepare.outputs.head_sha }}
          HEAD_REF: ${{ needs.mention-prepare.outputs.head_ref }}
          WRITER_MODE: mention
          GH_TOKEN: ${{ github.token }}
          GITHUB_EVENT_NAME: ${{ github.event_name }}
        run: |
          set -euo pipefail
          mkdir -p /opt/csl-claude/lib /opt/csl-bun /var/lib/csl-relay
"""
    if old not in text:
        print("mention 0b env already patched or marker moved")
        return text
    return text.replace(old, new, 1)


VERIFY_OUTBOX = """        run: |
          set -euo pipefail
          test -x "$RUNNER_TEMP/claude-fixer-guard/model-api"
          test "$(stat -c %U "$RUNNER_TEMP/claude-fixer-guard/model-api")" = root
"""


def slim_outbox_steps(text: str) -> str:
    """Replace helper-write outbox steps with a root-ownership check."""
    pattern = re.compile(
        r"(      - name: Install model API outbox helper\n"
        r"        id: outbox\n"
        r"        env:\n"
        r".*?\n)"
        r"        run: \|\n"
        r"          set -euo pipefail\n"
        r"          mkdir -p \"\$RUNNER_TEMP/claude-fixer-guard\"\n"
        r"          cat > \"\$RUNNER_TEMP/claude-fixer-guard/model-api\" <<'CLAUDE_FIXER_OUTBOX'\n"
        r".*?\n"
        r"          CLAUDE_FIXER_OUTBOX\n"
        r".*?"
        r"          test \"\$\(stat -c %U \"\$RUNNER_TEMP/claude-fixer-guard/model-api\"\)\" = root\n",
        re.S,
    )

    def repl(m: re.Match[str]) -> str:
        return m.group(1) + VERIFY_OUTBOX

    new, n = pattern.subn(repl, text)
    print("slimmed outbox steps", n)
    return new


def slim_fixer_attempt_outbox(text: str) -> str:
    pattern = re.compile(
        r"(      - name: \"Fixer attempt \$\{\{ needs\.fixer-prepare\.outputs\.attempt_key \}\}\"\n"
        r"        id: outbox\n"
        r"        env:\n"
        r".*?\n)"
        r"        run: \|\n"
        r"          set -euo pipefail\n"
        r"          mkdir -p \"\$RUNNER_TEMP/claude-fixer-guard\"\n"
        r"          cat > \"\$RUNNER_TEMP/claude-fixer-guard/model-api\" <<'CLAUDE_FIXER_OUTBOX'\n"
        r".*?\n"
        r"          CLAUDE_FIXER_OUTBOX\n"
        r".*?"
        r"          test \"\$\(stat -c %U \"\$RUNNER_TEMP/claude-fixer-guard/model-api\"\)\" = root\n",
        re.S,
    )

    def repl(m: re.Match[str]) -> str:
        return m.group(1) + VERIFY_OUTBOX

    new, n = pattern.subn(repl, text)
    print("slimmed fixer outbox", n)
    return new


def drop_cli_install_steps(text: str) -> str:
    pattern = re.compile(
        r"\n      - name: Install hash-pinned Claude Code CLI\n"
        r"        run: \|\n"
        r"          set -euo pipefail\n"
        r".*?"
        r"          leftover=\"\$\(find \"\$DEST_DIR\" -mindepth 1 -maxdepth 1 ! -name claude -print\)\"\n"
        r"          if \[ -n \"\$leftover\" \]; then\n"
        r"            echo \"install dir contains extra files\" >&2\n"
        r"            exit 1\n"
        r"          fi\n",
        re.S,
    )
    new, n = pattern.subn("\n", text)
    print("dropped CLI install steps", n)
    return new


def slim_sandbox_steps(text: str) -> str:
    """0b folded apt/sysctl. Keep a no-sudo assertion step so names stay stable."""
    old = """      - name: Install the subprocess sandbox for Claude's commands
        run: |
          # CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1 (on the Claude step) removes the OAuth
          # token, runtime and GitHub tokens from every command Claude runs, and on
          # Linux needs bubblewrap so those commands cannot read the CLI's own
          # environment either. Without bubblewrap the CLI refuses to start, so a
          # failed install fails closed: the model step does not succeed and the
          # publish job publishes nothing.
          for i in 1 2 3; do
            sudo apt-get update -qq && sudo apt-get install -y --no-install-recommends bubblewrap socat && break
            echo "apt-get attempt $i failed, retrying..."
            sleep 5
          done
          command -v bwrap
          # Ubuntu 24.04+ restricts unprivileged user namespaces through AppArmor.
          if [ -f /proc/sys/kernel/apparmor_restrict_unprivileged_userns ]; then
            sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
          fi
"""
    new = """      - name: Install the subprocess sandbox for Claude's commands
        run: |
          # Folded into E3-0-early 0b (apt + apparmor after sweep). After sudoers
          # this step cannot sudo; assert the 0b install.
          command -v bwrap
          command -v socat
"""
    count = text.count(old)
    print("sandbox blocks matched", count)
    return text.replace(old, new)


def extract_mention_prefix(text: str) -> str:
    start = text.index("      - name: E3-0-early setup-bun\n")
    end = text.index("      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262\n")
    # first checkout after mention 0b
    return text[start:end]


def insert_fixer_prefix(text: str) -> str:
    if "devin-review-fix:" in text and "E3-0-early setup-bun" in text.split("devin-review-fix:", 1)[1][:8000]:
        print("fixer already has 0a")
        return text
    prefix = extract_mention_prefix(text)
    prefix = prefix.replace("needs.mention-prepare.outputs.head_sha", "needs.fixer-prepare.outputs.head_sha")
    prefix = prefix.replace("needs.mention-prepare.outputs.head_ref", "needs.fixer-prepare.outputs.head_ref")
    prefix = prefix.replace("WRITER_MODE: mention", "WRITER_MODE: fixer")
    if "RECEIPT_CONTEXT:" not in prefix:
        prefix = prefix.replace(
            "GITHUB_EVENT_NAME: ${{ github.event_name }}",
            "GITHUB_EVENT_NAME: ${{ github.event_name }}\n"
            "          RECEIPT_CONTEXT: ${{ needs.fixer-prepare.outputs.receipt_context }}",
        )
    marker = "    steps:\n      - uses: actions/checkout@v4\n"
    # fixer still has unpinned checkout
    if marker not in text:
        marker = "    steps:\n      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262\n"
        if text.split("devin-review-fix:", 1)[1].find("E3-0-early setup-bun") != -1:
            print("fixer prefix present")
            return text
    # Only replace the fixer job's first checkout: after devin-review-fix
    head, rest = text.split("  devin-review-fix:", 1)
    if "      - name: E3-0-early setup-bun\n" in rest.split("    steps:", 1)[1][:2000]:
        print("fixer prefix already in job")
        return text
    rest = rest.replace(
        "    steps:\n      - uses: actions/checkout@v4\n",
        "    steps:\n" + prefix + "      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262\n",
        1,
    )
    print("inserted fixer 0a/0b")
    return head + "  devin-review-fix:" + rest


def pin_remaining_checkout_v4(text: str) -> str:
    # Only inside claude.yml model jobs we care about; leave other files.
    # Fixer checkout should already be pinned by insert. Replace leftover v4 in this file's model jobs?
    n = text.count("uses: actions/checkout@v4")
    print("remaining checkout@v4", n)
    return text


def main() -> None:
    text = YML.read_text(encoding="utf-8")
    text = expand_0b_env_mention(text)
    text = insert_helper_into_0b(text)
    text = drop_setup_python_and_pip(text)
    text = slim_sandbox_steps(text)
    text = drop_cli_install_steps(text)
    text = slim_outbox_steps(text)
    text = slim_fixer_attempt_outbox(text)
    text = insert_fixer_prefix(text)
    text = pin_remaining_checkout_v4(text)
    YML.write_text(text, encoding="utf-8")
    print("patched", YML, "bytes", len(text))


if __name__ == "__main__":
    main()

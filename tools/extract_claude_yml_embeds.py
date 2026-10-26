#!/usr/bin/env python3
"""Replace inlined claude.yml sources with copies from github.workflow_sha.

CSL-474 / CSL-377: GitHub will not start jobs from a workflow file larger than
500 KB (512,000 bytes). The E3 merge inlined C sources and helpers twice and
pushed claude.yml to 746 KB. Trusted jobs copy versioned files from
scripts/ci/ at github.workflow_sha (never the PR head).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
YML = ROOT / ".github" / "workflows" / "claude.yml"
CHECKOUT_PIN = "11d5960a326750d5838078e36cf38b85af677262"
TRUSTED_DIR = ".csl-trusted-workflow"
TRUSTED_SCRIPTS = f"{TRUSTED_DIR}/scripts/ci"

CHECKOUT_STEP = f"""      - name: Checkout trusted workflow commit
        uses: actions/checkout@{CHECKOUT_PIN}
        with:
          ref: ${{{{ github.workflow_sha }}}}
          path: {TRUSTED_DIR}
          persist-credentials: false
"""

TRUSTED_SETUP = f"""          TRUSTED="${{{{ github.workspace }}}}/{TRUSTED_SCRIPTS}"
          test -d "$TRUSTED"
          test ! -L "$TRUSTED"
          test ! -L "${{{{ github.workspace }}}}/{TRUSTED_DIR}"
          test "$(git -C "${{{{ github.workspace }}}}/{TRUSTED_DIR}" rev-parse HEAD)" = "${{{{ github.workflow_sha }}}}"
"""

CAT_FILES = {
    "CLAUDE_FIXER_OUTBOX": ("claude_fixer_outbox.py", "0755"),
    "CSL_E3_ENVIRON": ("sandbox_e3_environ.py", "0444"),
    "CSL_E3_RECORD": ("sandbox_e3_record.py", "0444"),
    "CSL_E3_BIND": ("sandbox_e3_bind.py", "0444"),
    "CSL_C_COMMON_H": ("sandbox_common.h", "0444"),
    "CSL_C_COMMON_C": ("sandbox_common.c", "0444"),
    "CSL_C_FILTER_H": ("sandbox_filter.h", "0444"),
    "CSL_C_FILTER_C": ("sandbox_filter.c", "0444"),
    "CSL_C_ARGV_H": ("sandbox_argv.h", "0444"),
    "CSL_C_ARGV_C": ("sandbox_argv.c", "0444"),
    "CSL_C_LAUNCH": ("sandbox_launch.c", "0444"),
    "CSL_C_CAPTURE": ("sandbox_capture.c", "0444"),
    "CSL_C_RELAY": ("sandbox_relay.c", "0444"),
    "CSL_C_PIN": ("sandbox_c.pin", "0444"),
    "CSL_E3_CLI_PIN": ("sandbox_e3_cli_pin.sh", "0755"),
    "CSL_E3_BOOTSTRAP": ("sandbox_e3_bootstrap.sh", "0755"),
    "CLAUDE_FIXER_RECEIPTS": ("claude_fixer_receipts.py", "0444"),
    "CLAUDE_FIXER_PUBLISH": ("claude_fixer_publish.py", "0444"),
}

STDIN_FILES = {
    "CLAUDE_FIXER_PREFLIGHT": "claude_fixer_preflight.py",
    "CLAUDE_PR_FIXER_GATE": "claude_pr_fixer_gate.py",
    "CLAUDE_DENIALS_REPORT": "claude_denials.py",
}

INSERT_BEFORE = (
    "      - name: Evaluate event path\n",
    "      - name: Refresh authorized human request under PR lock\n",
    "      - name: E3-0-early setup-bun\n",
    "      - name: Download untrusted model output\n",
    "      - name: Recheck current head and unresolved Devin findings\n",
)


def replace_cat_heredocs(text: str) -> str:
    for tag, (filename, mode) in CAT_FILES.items():
        pattern = re.compile(
            rf"cat > ([^\n]+) <<'{tag}'\n.*?\n[ \t]*{tag}\n",
            re.DOTALL,
        )

        def repl(match: re.Match[str], filename=filename, mode=mode) -> str:
            dest = match.group(1)
            return f'install -m {mode} "$TRUSTED/{filename}" {dest}\n'

        text, n = pattern.subn(repl, text)
        if n == 0:
            raise SystemExit(f"missing cat heredoc {tag}")
    return text


def replace_stdin_python(text: str) -> str:
    # "$interpreter" -I - "$EXECUTION_FILE" <<'CLAUDE_DENIALS_REPORT' ... TAG
    denials = re.compile(
        r'"\$interpreter" -I - "\$EXECUTION_FILE" <<\'CLAUDE_DENIALS_REPORT\'\n'
        r".*?\n[ \t]*CLAUDE_DENIALS_REPORT\n",
        re.DOTALL,
    )
    text, n = denials.subn(
        '"$interpreter" -I "$TRUSTED/claude_denials.py" "$EXECUTION_FILE"\n',
        text,
    )
    if n != 2:
        raise SystemExit(f"expected 2 denials stdin heredocs, got {n}")

    for tag, filename in STDIN_FILES.items():
        if tag == "CLAUDE_DENIALS_REPORT":
            continue
        pattern = re.compile(
            rf"python3 - <<'{tag}'\n.*?\n[ \t]*{tag}\n",
            re.DOTALL,
        )

        def repl(match: re.Match[str], filename=filename) -> str:
            return f'python3 -I "$TRUSTED/{filename}"\n'

        text, n = pattern.subn(repl, text)
        if n == 0:
            raise SystemExit(f"missing stdin heredoc {tag}")
    return text


def inject_trusted_setup(text: str) -> str:
    """Add TRUSTED= and fail-closed checks to every run script that uses $TRUSTED."""
    step_re = re.compile(r"(^      - name: .+\n(?:        .+\n)*?        run: \|\n)(.*?)(?=^      - |\Z)", re.MULTILINE | re.DOTALL)

    def repl(match: re.Match[str]) -> str:
        header, body = match.group(1), match.group(2)
        if "$TRUSTED/" not in body or "TRUSTED=" in body:
            return match.group(0)
        if "          set -euo pipefail\n" in body:
            body = body.replace(
                "          set -euo pipefail\n",
                "          set -euo pipefail\n" + TRUSTED_SETUP,
                1,
            )
        else:
            body = "          set -euo pipefail\n" + TRUSTED_SETUP + body
        return header + body

    return step_re.sub(repl, text)


def insert_checkouts(text: str) -> str:
    if text.count("Checkout trusted workflow commit") >= 7:
        return text
    n = 0
    for marker in INSERT_BEFORE:
        # E3-0-early setup-bun appears twice (mention + fixer).
        start = 0
        while True:
            idx = text.find(marker, start)
            if idx < 0:
                break
            before = text[:idx]
            # Skip if this job already has the trusted checkout immediately above.
            window = before[-400:]
            if "Checkout trusted workflow commit" in window:
                start = idx + len(marker)
                continue
            text = text[:idx] + CHECKOUT_STEP + text[idx:]
            n += 1
            start = idx + len(CHECKOUT_STEP) + len(marker)
    if n != 7:
        raise SystemExit(f"expected 7 trusted checkouts inserted, got {n}")
    return text


def rewrite_comments(text: str) -> str:
    text = text.replace(
        "    # Trusted and model-free (CSL-377): no checkout and no repository code. It\n"
        "    # runs only the workflow-embedded publisher, which mints the Claude App\n"
        "    # token itself, validates the untrusted artifact, pushes one ordinary\n"
        "    # fast-forward, posts the validated outbox and records the terminal audit.\n"
        "    # It also runs after a failed model job so the audit is never skipped.\n",
        "    # Trusted and model-free (CSL-377): checkout of github.workflow_sha only\n"
        "    # (never the PR head). Copies publisher/receipts from that tree, mints the\n"
        "    # Claude App token, validates the untrusted artifact, pushes one ordinary\n"
        "    # fast-forward, posts the validated outbox and records the terminal audit.\n"
        "    # It also runs after a failed model job so the audit is never skipped.\n",
    )
    text = text.replace(
        "    # Trusted and model-free (CSL-377): the locked human-request, pause and\n"
        "    # commit-cap preflight. Reads only; no checkout and no PR code.\n",
        "    # Trusted and model-free (CSL-377): the locked human-request, pause and\n"
        "    # commit-cap preflight. Checkout of github.workflow_sha only; no PR code.\n",
    )
    text = text.replace(
        "    # workflow markers. No checkout and no PR code.\n",
        "    # workflow markers. Checkout of github.workflow_sha only; no PR code.\n",
    )
    text = text.replace(
        "      # A separate step: actionlint 1.7.7 hangs on any run script of 64 KiB or more,\n"
        "      # and the receipt helper plus the publisher together exceed that.\n",
        "      # Receipt helper and publisher come from github.workflow_sha, not PR HEAD.\n",
    )
    return text


def main() -> None:
    text = YML.read_text(encoding="utf-8")
    if "Checkout trusted workflow commit" in text and "<<'CSL_C_LAUNCH'" not in text:
        print("already extracted", YML)
        return
    text = replace_cat_heredocs(text)
    text = replace_stdin_python(text)
    text = inject_trusted_setup(text)
    text = insert_checkouts(text)
    text = rewrite_comments(text)
    if "<<'CSL_C_" in text or "<<'CLAUDE_FIXER_PUBLISH'" in text:
        raise SystemExit("extraction left sandbox/publisher heredocs")
    YML.write_text(text, encoding="utf-8")
    print("extracted", YML, "bytes", YML.stat().st_size)


if __name__ == "__main__":
    main()

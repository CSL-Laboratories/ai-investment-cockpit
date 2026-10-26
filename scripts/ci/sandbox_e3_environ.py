#!/usr/bin/env python3
"""J1 three-set environ check with K1 PATH and K2/L1/L2 X-only names (CSL-450 D10).

Classification order is frozen DESIGN L259, unchanged by the L1/L2 addendum:
(1) exact Set-2 name, (2) Set-3 prefix or CI or PATH, (3) on X only, K2 names
PWD / SHLVL / _ / OLDPWD, (4) Set-1 Worker pair. Fail closed on any other name.

L1 (addendum): on X, OLDPWD is absent or equal to the Worker's OLDPWD.
L2 (addendum): PWD / SHLVL / _ may appear on B as Set-1 Worker pairs; on X the
K2 value rules apply. The check is unchanged; only the wording was.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Sequence, Tuple

# D10 §1.5 H1.2 Set 2: 81 names from action.yml:288–392 at pin a5a423f7.
SET2_NAMES: Tuple[str, ...] = (
    "MODE",
    "PROMPT",
    "TRIGGER_PHRASE",
    "ASSIGNEE_TRIGGER",
    "LABEL_TRIGGER",
    "BASE_BRANCH",
    "BRANCH_PREFIX",
    "BRANCH_NAME_TEMPLATE",
    "OVERRIDE_GITHUB_TOKEN",
    "ALLOWED_BOTS",
    "ALLOWED_NON_WRITE_USERS",
    "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB",
    "CLAUDE_CODE_SCRIPT_CAPS",
    "INCLUDE_COMMENTS_BY_ACTOR",
    "EXCLUDE_COMMENTS_BY_ACTOR",
    "GITHUB_RUN_ID",
    "USE_STICKY_COMMENT",
    "CLASSIFY_INLINE_COMMENTS",
    "DEFAULT_WORKFLOW_TOKEN",
    "USE_COMMIT_SIGNING",
    "SSH_SIGNING_KEY",
    "BOT_ID",
    "BOT_NAME",
    "TRACK_PROGRESS",
    "INCLUDE_FIX_LINKS",
    "ADDITIONAL_PERMISSIONS",
    "CLAUDE_ARGS",
    "ALL_INPUTS",
    "INPUT_PROMPT_FILE",
    "INPUT_SETTINGS",
    "INPUT_EXPERIMENTAL_SLASH_COMMANDS_DIR",
    "INPUT_PATH_TO_CLAUDE_CODE_EXECUTABLE",
    "INPUT_PATH_TO_BUN_EXECUTABLE",
    "INPUT_SHOW_FULL_OUTPUT",
    "DISPLAY_REPORT",
    "INPUT_PLUGINS",
    "INPUT_PLUGIN_MARKETPLACES",
    "PATH_TO_CLAUDE_CODE_EXECUTABLE",
    "NODE_VERSION",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_FEDERATION_RULE_ID",
    "ANTHROPIC_ORGANIZATION_ID",
    "ANTHROPIC_SERVICE_ACCOUNT_ID",
    "ANTHROPIC_WORKSPACE_ID",
    "ANTHROPIC_OIDC_AUDIENCE",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_CUSTOM_HEADERS",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "AWS_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "CLOUD_ML_REGION",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "ANTHROPIC_VERTEX_BASE_URL",
    "VERTEX_REGION_CLAUDE_3_5_HAIKU",
    "VERTEX_REGION_CLAUDE_3_5_SONNET",
    "VERTEX_REGION_CLAUDE_3_7_SONNET",
    "ANTHROPIC_FOUNDRY_RESOURCE",
    "ANTHROPIC_FOUNDRY_BASE_URL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "MCP_TIMEOUT",
    "MCP_TOOL_TIMEOUT",
    "MAX_MCP_OUTPUT_TOKENS",
    "CLAUDE_CODE_ENABLE_TELEMETRY",
    "OTEL_METRICS_EXPORTER",
    "OTEL_LOGS_EXPORTER",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_METRIC_EXPORT_INTERVAL",
    "OTEL_LOGS_EXPORT_INTERVAL",
    "OTEL_RESOURCE_ATTRIBUTES",
)
SET2 = frozenset(SET2_NAMES)

SET3_PREFIXES: Tuple[str, ...] = (
    "GITHUB_",
    "RUNNER_",
    "ACTIONS_",
    "INPUT_",
)
SET3_EXACT = frozenset({"CI", "PATH"})
K2_NAMES = frozenset({"PWD", "SHLVL", "_", "OLDPWD"})

BUN_DIR = "/opt/csl-bun"
BUN_HOME_BIN = "/home/runner/.bun/bin"
LAUNCHER = "/opt/csl-claude/launch"
BUN_EXE = "/opt/csl-bun/bun"
# Action PATH wrapper (hosted 40359709793). Capture tofu still hashes BUN_EXE.
BUN_ACTION_DIR = "/opt/csl-bun-action"
BUN_ACTION = BUN_ACTION_DIR + "/bun"
SYS_BIN = "/usr/bin"
BIN_DIR = "/bin"

# Set-2 value rules (D10 H1.2).
SET2_VALUE_RULES = {
    "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1",
    "PATH_TO_CLAUDE_CODE_EXECUTABLE": LAUNCHER,
    "INPUT_PATH_TO_CLAUDE_CODE_EXECUTABLE": LAUNCHER,
    "INPUT_PATH_TO_BUN_EXECUTABLE": BUN_ACTION,
    "ANTHROPIC_BASE_URL": "",
    "ANTHROPIC_CUSTOM_HEADERS": "",
    "GOOGLE_APPLICATION_CREDENTIALS": "",
}

FORBIDDEN_ANY = frozenset(
    {"NODE_OPTIONS", "BASH_ENV", "ENV", "LD_PRELOAD", "LD_AUDIT"}
)

# D10:261: LD_LIBRARY_PATH unset or empty under preferred I5 (no setup-python
# before uses:). BUN_* only names recorded from the root-owned bun at 0a.

# Exact Run-step shell line (D10 H5 / K2 fixture): Worker execs bash with these.
RUN_STEP_BASH_ARGV: Tuple[str, ...] = (
    "--noprofile",
    "--norc",
    "-e",
    "-o",
    "pipefail",
)


@dataclass(frozen=True)
class EnvironVerdict:
    ok: bool
    reason: str = ""


def prepend_path(base: str, lines: Sequence[str]) -> str:
    """Runner PrependPath: RemoveAll(x == line); Add(line); then reverse-join.

    De-dupe is only inside PrependPath. The suffix is the unmodified Worker PATH
    (K1). Newest-first after reverse.
    """
    prepend: list[str] = []
    for line in lines:
        prepend = [x for x in prepend if x != line]
        prepend.append(line)
    head = ":".join(reversed(prepend))
    if not base:
        return head
    if not head:
        return base
    return head + ":" + base


def expected_job_path(worker_path: str) -> str:
    """K1 exact expected PATH for B and X.

    Writers before the Run step, in job order: setup-bun ~/.bun/bin, custom bun
    wrapper dir (dirname of path_to_bun_executable), then /usr/bin and /bin.
    Newest-first:
    ``/bin:/usr/bin:/opt/csl-bun-action:/home/runner/.bun/bin:<Worker PATH>``.
    The tofu ELF stays at BUN_EXE; that directory is not this PATH entry.
    """
    return prepend_path(
        worker_path,
        (BUN_HOME_BIN, BUN_ACTION_DIR, SYS_BIN, BIN_DIR),
    )


def classify_name(name: str, *, on_x: bool) -> str:
    """First matching rule. Returns set1 / set2 / set3 / k2 / reject."""
    if name in SET2:
        return "set2"
    if name in SET3_EXACT or name.startswith(SET3_PREFIXES):
        return "set3"
    if on_x and name in K2_NAMES:
        return "k2"
    if not on_x and name in K2_NAMES:
        # L2: on B these are Set-1 pairs if the Worker carried them (rule 4).
        return "set1"
    return "set1"


def _decimal_shlvl(value: str) -> Optional[int]:
    if not value or not value.isdigit():
        return None
    if value != str(int(value)):
        return None
    return int(value)


def check_k2_x(
    x_env: Mapping[str, str],
    b_env: Mapping[str, str],
    worker_env: Mapping[str, str],
    workspace: str,
) -> Optional[str]:
    """K2 value rules on X, with L1 OLDPWD amendment."""
    if x_env.get("PWD") != workspace:
        return "pwd"
    b_shlvl = b_env.get("SHLVL")
    expected = 1 if b_shlvl is None else None
    if b_shlvl is not None:
        parsed = _decimal_shlvl(b_shlvl)
        if parsed is None:
            return "b_shlvl"
        expected = parsed + 1
    got = _decimal_shlvl(x_env.get("SHLVL", ""))
    if got is None or got != expected:
        return "shlvl"
    if x_env.get("_") != BUN_EXE:
        return "underscore"
    # L1: absent, or equal to the Worker's OLDPWD.
    if "OLDPWD" in x_env:
        if "OLDPWD" not in worker_env or x_env["OLDPWD"] != worker_env["OLDPWD"]:
            return "oldpwd"
    return None


def check_ld_library_path(proc_env: Mapping[str, str]) -> Optional[str]:
    """Preferred I5: unset or empty. Non-empty fails every honest run."""
    if "LD_LIBRARY_PATH" not in proc_env:
        return None
    if proc_env["LD_LIBRARY_PATH"] != "":
        return "LD_LIBRARY_PATH"
    return None


def check_bun_names(
    proc_env: Mapping[str, str], recorded_bun: Mapping[str, str]
) -> Optional[str]:
    """BUN_* only names recorded from the root-owned bun at 0a."""
    for name, value in proc_env.items():
        if not name.startswith("BUN_"):
            continue
        if name not in recorded_bun or recorded_bun[name] != value:
            return name
    return None


def check_environ(
    *,
    proc_env: Mapping[str, str],
    worker_env: Mapping[str, str],
    b_env: Mapping[str, str],
    on_x: bool,
    workspace: str,
    worker_path: str,
    recorded_bun: Optional[Mapping[str, str]] = None,
) -> EnvironVerdict:
    """Allowlist ``proc_env`` (X or B) against the three sets plus K1/K2."""
    for name in FORBIDDEN_ANY:
        if name in proc_env and proc_env[name] != "":
            return EnvironVerdict(False, name)
    reason = check_ld_library_path(proc_env)
    if reason:
        return EnvironVerdict(False, reason)
    bun = recorded_bun or {}
    reason = check_bun_names(proc_env, bun)
    if reason:
        return EnvironVerdict(False, reason)
    expected_path = expected_job_path(worker_path)
    if proc_env.get("PATH") != expected_path:
        return EnvironVerdict(False, "path")

    for name, value in proc_env.items():
        if name == "LD_LIBRARY_PATH":
            continue
        if name.startswith("BUN_"):
            continue
        kind = classify_name(name, on_x=on_x)
        if kind == "set2":
            if name in SET2_VALUE_RULES and value != SET2_VALUE_RULES[name]:
                return EnvironVerdict(False, name)
            continue
        if kind == "set3":
            continue
        if kind == "k2":
            continue
        # set1: Worker pair equality, except names already in set2/set3/k2.
        if name not in worker_env or worker_env[name] != value:
            return EnvironVerdict(False, name)

    if on_x:
        reason = check_k2_x(proc_env, b_env, worker_env, workspace)
        if reason:
            return EnvironVerdict(False, reason)

    # Set-1 names the Worker had must still be present unless classified away.
    for name, value in worker_env.items():
        kind = classify_name(name, on_x=on_x)
        if kind != "set1":
            continue
        if proc_env.get(name) != value:
            return EnvironVerdict(False, name)

    return EnvironVerdict(True)


def parse_environ_bytes(data: bytes) -> Dict[str, str]:
    """NUL-separated ``/proc/*/environ`` dump."""
    out: Dict[str, str] = {}
    for item in data.split(b"\0"):
        if not item:
            continue
        if b"=" not in item:
            raise ValueError("environ")
        key, value = item.split(b"=", 1)
        name = key.decode("ascii")
        out[name] = value.decode("utf-8", "strict")
    return out


def run_step_argv(script_path: str, bash: str = "/bin/bash") -> Tuple[str, ...]:
    """Exact Run-step argv[0..6] shape (H5), for the K2 fixture test."""
    return (bash, *RUN_STEP_BASH_ARGV, script_path)


def main(argv: Sequence[str]) -> int:
    """Hosted 0b/observation entry. Constants only; no env path overrides."""
    import json
    import os
    from pathlib import Path

    cmd = argv[1] if len(argv) > 1 else ""
    state = Path("/var/lib/csl-relay")
    if cmd == "check":
        raise SystemExit("worker.environ is not written (N9); use check-ld + C F1")
    if cmd == "check-ld":
        proc = {k: v for k, v in os.environ.items()}
        reason = check_ld_library_path(proc)
        if reason:
            raise SystemExit(reason)
        for name in ("SRT_DEBUG", "DEBUG_CLAUDE_AGENT_SDK", "ACTIONS_STEP_DEBUG"):
            if name in proc:
                raise SystemExit(name)
        bun = {}
        bun_path = state / "bun.env"
        if bun_path.is_file():
            bun = json.loads(bun_path.read_text(encoding="ascii"))
        reason = check_bun_names(
            {k: v for k, v in proc.items() if k.startswith("BUN_")}, bun
        )
        if reason:
            raise SystemExit(reason)
        return 0
    if cmd == "record-bun":
        recorded = {k: v for k, v in os.environ.items() if k.startswith("BUN_")}
        state.mkdir(parents=True, exist_ok=True)
        (state / "bun.env").write_text(json.dumps(recorded, sort_keys=True), encoding="ascii")
        names = "".join(f"{k}\n" for k in sorted(recorded))
        names_path = state / "bun.names"
        names_path.write_text(names, encoding="ascii")
        names_path.chmod(0o600)
        return 0
    raise SystemExit("argv")


if __name__ == "__main__":
    import os
    import sys

    raise SystemExit(main(sys.argv))

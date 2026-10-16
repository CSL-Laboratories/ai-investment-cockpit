#!/usr/bin/env python3
"""Workflow-owned, single-attempt pre-push guard and non-success terminal audit.

Installed outside the PR tree from the workflow's embedded, tested source.
No fetch, retry, history repair, new credential, or handled-result receipt.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
REF = re.compile(r"(?!.*\.\.)[A-Za-z0-9._/-]+\Z")


def command(args, **kwargs):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=30, **kwargs).strip()


def api(path):
    return json.loads(command(["gh", "api", path]))


def validate(config):
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", config["repo"])
            or not re.fullmatch(r"[1-9][0-9]*", config["number"])
            or not SHA.fullmatch(config["base"])
            or not REF.fullmatch(config["ref"])
            or config["ref"] in {"main", "master", "HEAD"}):
        raise ValueError("invalid guard context")


def current_pr(config, read=api):
    pr = read(f"repos/{config['repo']}/pulls/{config['number']}")
    head = pr.get("head") or {}
    if (pr.get("state") != "open" or head.get("ref") != config["ref"]
            or (head.get("repo") or {}).get("full_name") != config["repo"]
            or not SHA.fullmatch(str(head.get("sha") or ""))):
        raise ValueError("PR identity changed")
    return head["sha"]


def guard(config, updates, remote, state_path, read=api, git=command):
    """One ordinary push at most; consume the attempt even on a rejected check."""
    validate(config)
    # Exclusive creation prevents retry after either a hook refusal or a later
    # server rejection. A fresh authorized workflow run gets a fresh directory.
    with state_path.open("x", encoding="utf-8") as handle:
        handle.write("{}")
    rows = [line.split() for line in updates.splitlines() if line.strip()]
    if remote != "origin" or len(rows) != 1 or len(rows[0]) != 4:
        raise ValueError("unexpected push target")
    local_ref, candidate, remote_ref, advertised = rows[0]
    if (local_ref != "HEAD" or remote_ref != "refs/heads/" + config["ref"]
            or not SHA.fullmatch(candidate) or candidate == config["base"]
            or advertised != config["base"]):
        raise ValueError("remote moved or invalid update")
    if git(["git", "rev-parse", "HEAD"]) != candidate:
        raise ValueError("local head changed")
    if git(["git", "merge-base", config["base"], candidate]) != config["base"]:
        raise ValueError("fix does not descend from checkout")
    # The API check is deliberately last. Git's ordinary server-side rejection
    # remains authoritative if an external writer wins the race after this read.
    if current_pr(config, read) != config["base"]:
        raise ValueError("remote moved")
    state_path.write_text(json.dumps({"candidate": candidate}), encoding="utf-8")
    print(f"Guard checked candidate {candidate}; push is not yet confirmed. "
          "Use the full commit ID only after successful push and live-head verification.")


def execution_reason(path, outcome):
    """Read only coarse result fields; never echo model text or command inputs."""
    if outcome != "success":
        return "model-step-failed-or-timed-out"
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else [data]
        results = [r for r in items if isinstance(r, dict) and r.get("type") == "result"]
        if not results:
            return "execution-result-unavailable"
        for result in results:
            if result.get("permission_denials"):
                return "command-denied"
            turns = result.get("num_turns")
            if type(turns) is not int or turns < 0:
                return "execution-result-unavailable"
            if turns > 30 or result.get("subtype") == "error_max_turns":
                return "turn-limit"
            if result.get("is_error") or result.get("subtype") != "success":
                return "model-error"
        return "model-completed"
    except (OSError, ValueError, TypeError):
        return "execution-result-unavailable"


def terminal(config, state_path, execution, outcome, read=api):
    validate(config)
    reason = execution_reason(execution, outcome)
    pushed = "unverified"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        candidate = state.get("candidate", "")
        live = current_pr(config, read)
        if SHA.fullmatch(candidate) and candidate != config["base"]:
            base_compare = read(f"repos/{config['repo']}/compare/{config['base']}...{candidate}")
            live_compare = read(f"repos/{config['repo']}/compare/{candidate}...{live}")
            if base_compare.get("status") == "ahead" and live_compare.get("status") in {"ahead", "identical"}:
                pushed = candidate
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        pass  # Unknown is not successful, and never a needs-human result receipt.
    return (f"Claude fixer terminal audit: {reason}. Checkout `{config['base']}`. "
            f"Guarded candidate reachable on the live PR: `{pushed}`.\n\n"
            "This audit is not an addressed or needs-human finding receipt. "
            "If no verified per-root fix receipt exists, work remains pending. "
            "A fresh authorized run must recheck genuine unresolved, current findings "
            "and existing author permissions, three-attempt budget and eight-commit guard. "
            "Do not reopen resolved findings or retry this checkout. No fetch, rebase or merge recovery. "
            "The PR author should relay any remaining blocker to its Linear issue.")


def main():
    directory = Path(__file__).resolve().parent
    config_path, state_path = directory / "context.json", directory / "attempt.json"
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "install":
        config = {key: os.environ[env] for key, env in (
            ("repo", "REPO"), ("number", "PR_NUMBER"),
            ("base", "HEAD_SHA"), ("ref", "HEAD_REF"))}
        validate(config)
        config_path.write_text(json.dumps(config), encoding="utf-8")
        Path(__file__).chmod(0o700)
        command(["git", "config", "--local", "core.hooksPath", str(directory)])
        return 0
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if mode == "terminal":
        body = terminal(config, state_path, os.environ.get("EXECUTION_FILE", ""), os.environ.get("MODEL_OUTCOME", ""))
        # Summary survives a denied/unavailable comment API. Do not log its error
        # payload or silently claim the remote handback was posted.
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(body + "\n")
        print(body)
        try:
            command(["gh", "api", "--method", "POST",
                     f"repos/{config['repo']}/issues/{config['number']}/comments", "--input", "-"],
                    input=json.dumps({"body": body}))
        except (OSError, subprocess.SubprocessError):
            print("::error::Terminal audit saved to job summary; PR handback could not be confirmed.")
            return 1
        return 0
    try:
        guard(config, sys.stdin.read(), mode, state_path)
    except (OSError, ValueError, subprocess.SubprocessError):
        print("Claude fixer push stopped: stale/invalid head, unavailable read, or attempt already used. "
              "Stop this run; no fetch, rebase, merge or second push.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

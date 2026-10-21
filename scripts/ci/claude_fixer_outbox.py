#!/usr/bin/env python3
"""Model-side GitHub helper for the untrusted Claude model job (CSL-377).

Installed outside the PR tree from the workflow's embedded, tested source.
Reads call fixed same-PR endpoints with the job's read-only token. Claude Code's
subprocess scrub removes GH_TOKEN from the commands Claude runs, so the trusted
install step also stores that read-only token next to this helper. Writes are
never sent from this job: `comment`, `reply`, `label` and `receipt` append a
validated record to `.claude-fixer-out/outbox.jsonl`, which the trusted publish
job revalidates and posts only after it has checked the live PR (and, for
receipts, its own verified push). Nothing here is a security boundary: the
model job runs PR code, so the publisher treats the outbox as untrusted.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
REF = re.compile(r"(?!.*\.\.)[A-Za-z0-9._/-]+\Z")
MODES = frozenset({"mention", "fixer", "conflict"})
HANDOFF_LABEL = "needs-decision"
MAX_RECORDS = 50
MAX_BODY = 65000
OUTBOX = Path(".claude-fixer-out") / "outbox.jsonl"
READ_TOKEN = "read-token"


def command(args, **kwargs):
    kwargs.setdefault("timeout", 30)
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, **kwargs).strip()


def validate(config):
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", config["repo"])
            or not re.fullmatch(r"[1-9][0-9]*", config["number"])
            or not SHA.fullmatch(config["base"])
            or not REF.fullmatch(config["ref"])
            or config["ref"] in {"main", "master", "HEAD"}
            or config.get("mode") not in MODES):
        raise ValueError("invalid helper context")


def read_body(body_path):
    # Do not let a comment-file read bypass the model's denied .env/secret reads.
    if not re.fullmatch(r"\.claude-fixer-replies/[A-Za-z0-9_-]+\.txt", body_path):
        raise ValueError("invalid comment file")
    workspace = Path.cwd().resolve()
    directory = workspace / ".claude-fixer-replies"
    file = workspace / body_path
    if directory.is_symlink() or file.is_symlink() or file.resolve().parent != directory:
        raise ValueError("comment file escapes reply directory")
    # Nonblocking open plus descriptor type check rejects FIFOs before reading,
    # including a file replaced between the earlier path check and open.
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(file, flags), "r", encoding="utf-8") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("comment file is not regular")
        body = handle.read(MAX_BODY + 1)
    # The publisher rejects HTML comments so model text cannot carry markers.
    if not body.strip() or len(body) > MAX_BODY or "<!--" in body or "\x00" in body:
        raise ValueError("invalid comment body")
    return body


def append(record):
    workspace = Path.cwd().resolve()
    directory = workspace / OUTBOX.parent
    if directory.is_symlink():
        raise ValueError("outbox directory is a symlink")
    directory.mkdir(exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(workspace / OUTBOX, flags, 0o600), "r+", encoding="utf-8") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("outbox is not regular")
        existing = [line for line in handle.read().split("\n") if line.strip()]
        if len(existing) >= MAX_RECORDS:
            raise ValueError("outbox is full")
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    return "Queued for the workflow publisher; it posts only after its own live-PR checks."


def model_api(config, args, run=command):
    """Fixed same-PR reads and queued writes. No model-supplied endpoint, method or flags."""
    validate(config)
    root = f"repos/{config['repo']}"
    pull = f"{root}/pulls/{config['number']}"
    reads = {"read": pull, "comments": f"{root}/issues/{config['number']}/comments",
             "review-comments": f"{pull}/comments", "reviews": f"{pull}/reviews"}
    if args == ["threads"]:
        owner, name = config["repo"].split("/")
        # Fixed read-only query. The root maps REST review comments to a current
        # thread; pagination covers all thread states without accepting a query.
        query = """query($owner:String!,$name:String!,$number:Int!,$endCursor:String){
          repository(owner:$owner,name:$name){pullRequest(number:$number){
            reviewThreads(first:100,after:$endCursor){pageInfo{hasNextPage endCursor}
              nodes{id isResolved isOutdated path line comments(first:1){nodes{databaseId body url}}}}}}}"""
        return run(["gh", "api", "graphql", "--paginate", "-f", "query=" + query,
                    "-f", "owner=" + owner, "-f", "name=" + name, "-F", "number=" + config["number"]])
    if len(args) == 1 and args[0] in reads:
        paging = [] if args[0] == "read" else ["--paginate"]
        return run(["gh", "api", "--method", "GET", reads[args[0]], *paging])
    if args == ["label"] and config["mode"] == "conflict":
        # Only the conflict job may queue the one human-handoff label.
        return append({"op": "label", "label": HANDOFF_LABEL})
    if len(args) == 3 and args[0] == "receipt" and config["mode"] == "fixer" \
            and re.fullmatch(r"[1-9][0-9]{0,19}", args[1]) and args[2] in {"addressed", "needs-human"}:
        if int(args[1]) not in config.get("roots", []):
            raise ValueError("receipt root is not an eligible finding")
        return append({"op": "receipt", "root": int(args[1]), "status": args[2]})
    if len(args) == 2 and args[0] == "comment":
        return append({"op": "comment", "body": read_body(args[1])})
    if len(args) == 3 and args[0] == "reply" and re.fullmatch(r"[1-9][0-9]{0,19}", args[1]):
        return append({"op": "reply", "comment_id": int(args[1]), "body": read_body(args[2])})
    raise ValueError("unsupported PR API operation")


def main():
    directory = Path(__file__).resolve().parent
    config_path = directory / "context.json"
    if sys.argv[1:] == ["install"]:
        config = {key: os.environ[env] for key, env in (
            ("repo", "REPO"), ("number", "PR_NUMBER"), ("base", "HEAD_SHA"),
            ("ref", "HEAD_REF"), ("mode", "WRITER_MODE"))}
        validate(config)
        receipts = json.loads(os.environ.get("RECEIPT_CONTEXT") or "[]")
        if not isinstance(receipts, list) or (receipts and config["mode"] != "fixer"):
            raise ValueError("invalid receipt context")
        config["roots"] = [item["root"] for item in receipts if type(item.get("root")) is int]
        config_path.write_text(json.dumps(config), encoding="utf-8")
        token = os.environ.get("GH_TOKEN", "")
        if token:
            # The job's read-only token; it is also in the checkout's origin URL.
            with os.fdopen(os.open(directory / READ_TOKEN, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
                handle.write(token)
        Path(__file__).chmod(0o700)
        return 0
    try:
        if len(sys.argv) < 3 or sys.argv[1] != "--":
            raise ValueError("invalid model-api invocation")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if not os.environ.get("GH_TOKEN") and (directory / READ_TOKEN).is_file():
            os.environ["GH_TOKEN"] = (directory / READ_TOKEN).read_text(encoding="utf-8").strip()
        print(model_api(config, sys.argv[2:]))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("PR API operation failed or is not permitted.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

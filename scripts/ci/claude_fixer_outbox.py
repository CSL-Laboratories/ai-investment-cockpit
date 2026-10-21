#!/usr/bin/env python3
"""Model-side helper for the untrusted Claude model job (CSL-377).

Installed outside the PR tree from the workflow's embedded, tested source, then
made root-owned and read-only by the install step, and the only Bash command the
model may run. Git operations are fixed argv lists with no model-supplied
options: `status`, `diff`, `diff-stat`, `log`, `head`, `commit MSGFILE` (stages
every change except the helper directories, commits the validated message
without hooks and prints the full new HEAD) and, in the conflict job only,
`conflicts` and `merge-abort`. Git runs without a shell, hooks, fsmonitor or
pager. GitHub reads call fixed same-PR endpoints with the job's read-only token.
Claude Code's subprocess scrub removes GH_TOKEN from the commands Claude runs, so
the trusted install step also stores that read-only token next to this helper.
Writes are never sent from this job: `comment`, `reply`, `label` and `receipt`
append a validated record to `.claude-fixer-out/outbox.jsonl`, which the trusted
publish job revalidates and posts only after it has checked the live PR (and,
for receipts, its own verified push). Nothing here is a security boundary for
publishing: the publisher treats the bundle and outbox as untrusted.
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
# Every git call: no shell, hooks, fsmonitor, pager or signing program, no user or
# system config (command() sets GIT_CONFIG_GLOBAL/NOSYSTEM, as the publisher does),
# and no model-supplied options.
GIT = ("git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.pager=cat",
       "-c", "commit.gpgsign=false")
NO_DRIVERS = ("--no-ext-diff", "--no-textconv")
GIT_READS = {"status": ("status", "--short"), "diff": ("diff", *NO_DRIVERS), "diff-stat": ("diff", "--stat", *NO_DRIVERS),
             "log": ("log", "--oneline", "-20"), "head": ("log", "-1", "--stat")}
# Git's default; a `conflict-marker-size` attribute changes it per path.
MARKER_SIZE = 7
MAX_MARKER_SIZE = 1000
UNMERGED = ("diff", "--name-only", "-z", "--diff-filter=U")
CONFLICT_GIT = {"conflicts": ("diff", "--name-only", "--diff-filter=U"), "merge-abort": ("merge", "--abort")}
STAGE = ("add", "-A", "--", ".")
# Not `:(exclude)` pathspecs: the model job lists these in .git/info/exclude, and `git add`
# exits 1 when a pathspec names an ignored path (CSL-377 canary, run 39569528483).
UNSTAGE = ("reset", "-q", "--", ".claude-fixer-out", ".claude-fixer-replies")
# claude-code-action's restoreConfigFromBase (SENSITIVE_PATHS) replaces these with the base branch's
# versions for PR events and leaves them unstaged; "callers that stage files must exclude these".
# The conflict job is not a PR event, and there a base-side change to them is part of the merge.
ACTION_RESTORED = (".claude", ".mcp.json", ".claude.json", ".gitmodules", ".ripgreprc", "CLAUDE.md",
                   "CLAUDE.local.md", ".husky")
UNTRACKED = ("ls-files", "-z", "--others", "--exclude-standard")
MODIFIED = ("ls-files", "-z", "--modified")
# The protected names Claude Code's sandbox masks when they do not exist (Claude Code 2.1.30x).
SANDBOX_MASKED = frozenset({".gitconfig", ".gitmodules", ".bashrc", ".bash_profile", ".zshrc", ".zprofile",
                            ".profile", ".ripgreprc", ".mcp.json", ".vscode", ".idea"})


def command(args, **kwargs):
    kwargs.setdefault("timeout", 30)
    if args[0] == "git":
        # A user-level config could name diff drivers, clean filters or gpg.program.
        kwargs.setdefault("env", {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    # rstrip only: leading spaces are meaningful in `git status --short`.
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, **kwargs).rstrip()


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


def git(run, *args):
    return run([*GIT, *args])


def marker_sizes(run, names):
    """Each path's possible conflict-marker sizes: git's default plus the attribute's value.

    Read from the working tree, HEAD and MERGE_HEAD, so neither side's `.gitattributes`,
    nor a later edit of the working tree's, hides the markers git wrote.
    """
    sizes = {name: {MARKER_SIZE} for name in names}
    for source in ((), ("--source=HEAD",), ("--source=MERGE_HEAD",)):
        fields = git(run, "check-attr", "-z", *source, "conflict-marker-size", "working-tree-encoding",
                     "--", *names).split("\0")
        for path, attr, value in zip(fields[0::3], fields[1::3], fields[2::3]):
            if attr == "working-tree-encoding":
                # Markers in a re-encoded file (UTF-16, say) are not UTF-8 text this check can read.
                if value not in {"unspecified", "unset"}:
                    raise ValueError("unmerged path has a working-tree-encoding")
                continue
            # As git's atoi(): leading digits; zero, negative or none means the default.
            match = re.match(r"\s*([+-]?[0-9]+)", value)
            size = int(match.group(1)) if match else 0
            if size > MAX_MARKER_SIZE:
                raise ValueError("unsupported conflict-marker-size")
            if size > 0:
                sizes[path].add(size)
    return sizes


def conflict_marker(sizes):
    # `<`, `|` and `>` runs from the smallest size up (a recursive merge nests longer ones);
    # the bare `=` separator at exactly a size the merge could have used.
    low = f"{{{min(sizes)},}}"
    separators = "|".join(f"={{{size}}}" for size in sorted(sizes))
    return re.compile(rf"^(?:(?:<{low}|\|{low}|>{low})(?: |$)|(?:{separators})$)", re.MULTILINE)


class MaskedPath(ValueError):
    """A tracked path the sandbox masked: named to the model, unlike other refusals."""


def sandbox_masks(run):
    """Exclude pathspecs for the sandbox's masks among untracked paths.

    Claude Code's sandbox read-only binds /dev/null over protected paths that do not exist
    (.bashrc, .gitconfig, .idea, ...). Git lists them as untracked, then `git add` refuses the
    device and stages nothing, so they are left out. While another sandbox is still active,
    the CLI leaves those mount points behind as empty regular files; they are left out too.
    Only missing paths are masked, so a tracked path never is: one that became a device was
    deleted, and that deletion cannot be staged, so the commit is refused instead.
    """
    workspace = Path.cwd()

    def status(name):
        try:
            return os.lstat(workspace / name)
        except FileNotFoundError:
            return None  # a deletion: `add -A` stages it
        # Any other OSError propagates and the commit fails closed.

    def regular(info):
        return stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)

    for name in filter(None, git(run, *MODIFIED).split("\0")):
        info = status(name)
        # A directory here is a real change (a file replaced by a directory, a moved gitlink).
        if info is not None and not (regular(info) or stat.S_ISDIR(info.st_mode)):
            raise MaskedPath("a tracked path is masked by the sandbox (deleted?), so it cannot be committed")
    masks = []
    for name in filter(None, git(run, *UNTRACKED).split("\0")):
        info = status(name)
        if info is None:
            continue
        leftover = name in SANDBOX_MASKED and stat.S_ISREG(info.st_mode) and not info.st_size
        if leftover or not regular(info):
            masks.append(":(exclude,literal)" + name)
    return masks


def commit(body_path, run, mode):
    # The message is validated like a comment body and passed as one argv element.
    message = read_body(body_path)
    if mode == "conflict":
        # `git add` would mark a still-conflicted file resolved, markers and all.
        workspace = Path.cwd().resolve()
        # NUL-separated, so unusual names are not C-quoted.
        present = []
        for name in filter(None, git(run, *UNMERGED).split("\0")):
            file = workspace / name
            if not file.exists() and not file.is_symlink():
                continue  # resolved by deleting the file; `add -A` stages the removal
            if file.is_symlink() or not file.is_file():
                raise ValueError("unmerged path is not a regular file")
            present.append(name)
        # Attributes only for files that remain: a deletion has no markers to read.
        sizes = marker_sizes(run, present) if present else {}
        for name in present:
            if conflict_marker(sizes[name]).search((workspace / name).read_text(encoding="utf-8", errors="replace")):
                raise ValueError("conflict markers remain")
    git(run, *STAGE, *sandbox_masks(run))
    # The helper's own files stay out of the commit whether or not they are ignored, and so do
    # the action's base-branch restores in the PR-event jobs.
    git(run, *UNSTAGE, *(ACTION_RESTORED if mode != "conflict" else ()))
    # In the conflict job this completes the in-progress merge.
    git(run, "commit", "--no-verify", "-q", "-m", message)
    return git(run, "rev-parse", "HEAD")


def model_api(config, args, run=command):
    """Fixed git operations, same-PR reads and queued writes. No model-supplied endpoint, method or flags."""
    validate(config)
    if len(args) == 1 and args[0] in GIT_READS:
        return git(run, *GIT_READS[args[0]])
    if len(args) == 1 and args[0] in CONFLICT_GIT and config["mode"] == "conflict":
        return git(run, *CONFLICT_GIT[args[0]])
    if len(args) == 2 and args[0] == "commit":
        return commit(args[1], run, config["mode"])
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
    raise ValueError("unsupported helper operation")


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
            # The job's read-only token, revoked when this job ends. The workflow keeps it
            # out of .git/config; the publisher refuses token-shaped strings in the candidate
            # and outbox.
            with os.fdopen(os.open(directory / READ_TOKEN, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
                handle.write(token)
        # The install step then root-owns this directory and sets 0755 (directory,
        # helper) and 0644 (context, token): the runner user can run and read them
        # but cannot change them.
        Path(__file__).chmod(0o700)
        return 0
    try:
        if len(sys.argv) < 3 or sys.argv[1] != "--":
            raise ValueError("invalid model-api invocation")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if not os.environ.get("GH_TOKEN") and (directory / READ_TOKEN).is_file():
            os.environ["GH_TOKEN"] = (directory / READ_TOKEN).read_text(encoding="utf-8").strip()
        print(model_api(config, sys.argv[2:]))
    except MaskedPath as error:
        print(f"Helper operation failed: {error}.", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        # Name the failed step, never its output.
        cmd = list(error.cmd) if isinstance(error.cmd, (list, tuple)) else []
        step = f"git {cmd[len(GIT)]}" if cmd[:len(GIT)] == list(GIT) and len(cmd) > len(GIT) else "a command"
        print(f"Helper operation failed: {step} exited {error.returncode}.", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("Helper operation failed or is not permitted.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

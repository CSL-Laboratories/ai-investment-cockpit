#!/usr/bin/env python3
"""Trusted, model-free publisher for the Claude PR writers (CSL-377).

Runs in the workflow's publish job, never in the model job, and never runs
repository code. Its input is the model job's artifact, which is UNTRUSTED:
a Git bundle of new local commits, a JSONL outbox of queued GitHub writes and
a coarse model result. This one process:

1. re-reads the live PR (open, same repository, recorded branch and head, no
   human pause) and enforces one publish attempt;
2. mints the Claude GitHub App token exactly like claude-code-action (GitHub
   OIDC, audience claude-code-github-action, Anthropic token exchange), masks
   it, never prints it and revokes it in a finally block;
3. fetches the recorded head into a fresh bare repository, verifies the bundle
   and validates every new commit: ancestry, linear history (or the one
   conflict merge), commit/size bounds, committer identity, file modes and
   protected paths; then pushes one ordinary fast-forward (no force);
4. posts the validated outbox as claude[bot] and builds per-root receipts
   itself, so model text can never author a machine marker;
5. records the workflow-owned terminal audit with the job's GitHub token.

The live-head checks are not an atomic lock. Git's ordinary non-fast-forward
rejection stays the final race protection against external writers.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
REF = re.compile(r"(?!.*\.\.)[A-Za-z0-9._/-]+\Z")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
NUMBER_RE = re.compile(r"[1-9][0-9]{0,9}\Z")
MODES = frozenset({"mention", "fixer", "conflict"})
PAUSE_LABELS = frozenset({"claude:pause", "hold"})
HANDOFF_LABEL = "needs-decision"
# The model job pins this identity (claude-code-action bot_id/bot_name and the
# job's git config). Any other committer, e.g. GIT_COMMITTER_NAME=Alice from a
# prefixed command, is refused: it would forge a human commit and reset the
# eight-commit streak.
CLAUDE_COMMITTER = ("claude[bot]", "209825114+claude[bot]@users.noreply.github.com")
MAX_COMMITS = 20
MAX_PATHS = 300
MAX_DIFF_BYTES = 5_000_000
MAX_BUNDLE_BYTES = 25_000_000
MAX_RECORDS = 50
MAX_BODY = 65000
MAX_OUTBOX_BYTES = MAX_RECORDS * (MAX_BODY * 4 + 512)
MAX_RESULT_BYTES = 1_000_000
OIDC_AUDIENCE = "claude-code-github-action"
EXCHANGE_URL = "https://api.anthropic.com/api/github/github-app-token-exchange"
GITHUB_API = "https://api.github.com"
GITHUB_HOST = "https://github.com"
WORKFLOW_VALIDATION_CODES = frozenset({"workflow_not_found_on_default_branch"})
RECORD_KEYS = {"comment": {"op", "body"}, "reply": {"op", "comment_id", "body"},
               "label": {"op", "label"}, "receipt": {"op", "root", "status"}}
# Exact or basename matches anywhere in the tree (casefolded, fail closed).
PROTECTED_FILES = frozenset({"tools/ci_gate.py", "tools/required_ci_gate.py", "tools/test_claude_pr_fixer.py"})
PROTECTED_NAMES = frozenset({"codeowners", "agents.md", "claude.md", ".mcp.json"})
PROTECTED_DIRS = frozenset({".claude", ".claude-fixer-out", ".claude-fixer-replies"})
# GitHub and Anthropic token shapes. The model job can read its own read-only job token
# (the helper's read-token); a copy in the candidate or outbox is refused, not redacted.
CREDENTIAL = re.compile(r"gh[opsuvr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,}|sk-ant-[A-Za-z0-9_-]{20,}")


class Refused(Exception):
    """A fixed, model-free reason. Never carries model text, paths or secrets."""


class LiveStateRefused(Refused):
    """The live PR moved, closed or is paused: stop every remaining write."""


class WorkflowValidationSkip(Exception):
    """The token exchange refused this workflow version (expected before merge)."""


class AuditReadError(RuntimeError):
    """Read failures must not be confused with observation validation."""


def command(args, **kwargs):
    kwargs.setdefault("timeout", 30)
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, **kwargs).strip()


def api(path, timeout=30):
    """GET with the job's GitHub token (GH_TOKEN); never the App token."""
    return json.loads(command(["gh", "api", path], timeout=timeout))


# ---------------------------------------------------------------------------
# HTTP and the Claude App token


class HttpError(Exception):
    def __init__(self, status, data=None):
        super().__init__(f"HTTP {status}")
        self.status, self.data = status, data


def http_json(method, url, token=None, body=None, timeout=20):
    """Minimal JSON client. Error text never includes headers or the token."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "claude-fixer-publish"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if url.startswith(GITHUB_API):
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(2_000_000)
            return response.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as error:
        try:
            parsed = json.loads(error.read(200_000) or b"null")
        except ValueError:
            parsed = None
        raise HttpError(error.code, parsed) from None
    except (urllib.error.URLError, OSError, ValueError):
        raise HttpError(0) from None


def is_workflow_validation(status, data):
    data = data if isinstance(data, dict) else {}
    error = data.get("error") if isinstance(data.get("error"), dict) else {}
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    if details.get("error_code") in WORKFLOW_VALIDATION_CODES:
        return True
    if status != 401:
        return False
    return any("workflow validation failed" in str(message or "").lower()
               for message in (data.get("message"), error.get("message")))


def mint_app_token(env, http=http_json, out=print, sleep=time.sleep):
    """OIDC -> Claude App token, as claude-code-action src/github/token.ts does."""
    request_url = env.get("ACTIONS_ID_TOKEN_REQUEST_URL") or ""
    request_token = env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN") or ""
    if not request_url.startswith("https://") or not request_token:
        raise Refused("OIDC token unavailable (id-token: write missing)")
    separator = "&" if "?" in request_url else "?"
    oidc = None
    for attempt in range(3):
        try:
            _status, data = http("GET", f"{request_url}{separator}audience={OIDC_AUDIENCE}", token=request_token)
            oidc = (data or {}).get("value") if isinstance(data, dict) else None
            if isinstance(oidc, str) and oidc:
                break
        except HttpError:
            pass
        oidc = None
        if attempt < 2:
            sleep(2 ** attempt)
    if not oidc:
        raise Refused("OIDC token request failed")
    out(f"::add-mask::{oidc}")
    for attempt in range(3):
        try:
            _status, data = http("POST", EXCHANGE_URL, token=oidc)
        except HttpError as error:
            if is_workflow_validation(error.status, error.data):
                raise WorkflowValidationSkip() from None
            if error.status and error.status < 500 and error.status != 429:
                raise Refused(f"App token exchange refused (HTTP {error.status})") from None
            if attempt < 2:
                sleep(2 ** attempt)
            continue
        token = (data or {}).get("token") or (data or {}).get("app_token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_.~-]{20,512}", token):
            raise Refused("App token missing from exchange response")
        out(f"::add-mask::{token}")
        return token
    raise Refused("App token exchange failed")


def revoke_app_token(token, http=http_json):
    try:
        http("DELETE", f"{GITHUB_API}/installation/token", token=token, timeout=10)
        return True
    except HttpError:
        return False


# ---------------------------------------------------------------------------
# Configuration and live PR state


def load_config(env):
    config = {"repo": env.get("REPO", ""), "number": env.get("PR_NUMBER", ""),
              "ref": env.get("HEAD_REF", ""), "base": env.get("HEAD_SHA", ""),
              "mode": env.get("WRITER_MODE", ""), "merge_base": env.get("BASE_SHA", ""),
              "base_ref": env.get("BASE_REF", "")}
    validate(config)
    if config["mode"] not in MODES:
        raise ValueError("invalid writer mode")
    if config["mode"] == "conflict":
        if not SHA.fullmatch(config["merge_base"]) or not REF.fullmatch(config["base_ref"]) \
                or config["merge_base"] == config["base"]:
            raise ValueError("invalid conflict base")
    else:
        config["merge_base"] = config["base_ref"] = ""
    receipts = json.loads(env.get("RECEIPT_CONTEXT") or "[]")
    if not isinstance(receipts, list) or len(receipts) > MAX_RECORDS or (receipts and config["mode"] != "fixer"):
        raise ValueError("invalid receipt context")
    for item in receipts:
        if (not isinstance(item, dict) or type(item.get("root")) is not int or item["root"] <= 0
                or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("fingerprint") or ""))
                or item.get("base") != config["base"]):
            raise ValueError("invalid receipt context")
    if len({item["root"] for item in receipts}) != len(receipts):
        raise ValueError("duplicate receipt context")
    config["receipts"] = receipts
    return config


def validate(config):
    if (not REPO_RE.fullmatch(config.get("repo", ""))
            or not NUMBER_RE.fullmatch(config.get("number", ""))
            or not SHA.fullmatch(config.get("base", ""))
            or not REF.fullmatch(config.get("ref", ""))
            or config["ref"] in {"main", "master", "HEAD"}):
        raise ValueError("invalid publish context")


def pause_refusal(pr, env):
    if env.get("CLAUDE_FIXER_PAUSE") == "true":
        return "paused by repo variable CLAUDE_FIXER_PAUSE"
    labels = pr.get("labels")
    # Unreadable labels fail closed: a hold must not be missed.
    if not isinstance(labels, list) or not all(isinstance(label, dict) for label in labels):
        return "PR labels unreadable"
    if PAUSE_LABELS & {str(label.get("name") or "").lower() for label in labels}:
        return "paused by a claude:pause or hold label"
    return ""


def live_head(config, env, read, allowed):
    """Live PR identity and pause, as the pre-push guard and model-api checked."""
    pr = read(f"repos/{config['repo']}/pulls/{config['number']}")
    if not isinstance(pr, dict):
        raise LiveStateRefused("PR read failed")
    head = pr.get("head") or {}
    if (pr.get("state") != "open" or head.get("ref") != config["ref"]
            or (head.get("repo") or {}).get("full_name") != config["repo"]
            or not SHA.fullmatch(str(head.get("sha") or ""))):
        raise LiveStateRefused("PR identity changed")
    reason = pause_refusal(pr, env)
    if reason:
        raise LiveStateRefused(reason)
    if head["sha"] not in allowed:
        raise LiveStateRefused("PR head moved since the recorded checkout")
    return head["sha"]


def current_pr(config, read=api):
    pr = read(f"repos/{config['repo']}/pulls/{config['number']}")
    head = pr.get("head") or {}
    if (pr.get("state") != "open" or head.get("ref") != config["ref"]
            or (head.get("repo") or {}).get("full_name") != config["repo"]
            or not SHA.fullmatch(str(head.get("sha") or ""))):
        raise ValueError("PR identity changed")
    return head["sha"]


# ---------------------------------------------------------------------------
# Untrusted artifact files


def read_regular(path, limit):
    """Read a regular, non-symlink file without blocking on FIFOs."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise Refused("artifact file is not a bounded regular file")
        return handle.read(limit + 1)


def artifact_file(directory, name):
    if not directory:
        return None
    path = Path(directory) / name
    try:
        if path.is_symlink() or not path.exists():
            return None
        return path if path.is_file() else None
    except OSError:
        return None


def no_duplicate_keys(pairs):
    keys = [key for key, _value in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def valid_body(body):
    return (isinstance(body, str) and body.strip() and len(body) <= MAX_BODY
            and "<!--" not in body and "\x00" not in body)


def load_outbox(raw, mode, roots):
    """Validate the whole outbox or reject it: queued writes, never markers."""
    if raw is None:
        return []
    if len(raw) > MAX_OUTBOX_BYTES:
        raise Refused("outbox too large")
    try:
        lines = [line for line in raw.decode("utf-8").split("\n") if line.strip()]
    except UnicodeDecodeError:
        raise Refused("outbox is not UTF-8") from None
    if len(lines) > MAX_RECORDS:
        raise Refused("outbox has too many records")
    records, receipt_roots = [], set()
    for line in lines:
        try:
            record = json.loads(line, object_pairs_hook=no_duplicate_keys)
        except (ValueError, RecursionError):
            raise Refused("outbox record is not valid JSON") from None
        if not isinstance(record, dict) or not isinstance(record.get("op"), str) \
                or record["op"] not in RECORD_KEYS \
                or set(record) != RECORD_KEYS[record["op"]]:
            raise Refused("outbox record has an unknown operation or fields")
        op = record["op"]
        if op in {"comment", "reply"} and not valid_body(record["body"]):
            raise Refused("outbox body empty, oversized or contains an HTML comment")
        if op in {"comment", "reply"} and CREDENTIAL.search(record["body"]):
            raise Refused("outbox body contains a credential-shaped string")
        if op == "reply" and (type(record["comment_id"]) is not int or record["comment_id"] <= 0):
            raise Refused("outbox reply target is invalid")
        if op == "label" and (mode != "conflict" or record["label"] != HANDOFF_LABEL):
            raise Refused("outbox label not permitted for this writer")
        if op == "receipt":
            root = record["root"]
            if (mode != "fixer" or type(root) is not int or root not in roots
                    or record["status"] not in ("addressed", "needs-human") or root in receipt_roots):
                raise Refused("outbox receipt not permitted for this root")
            receipt_roots.add(root)
        records.append(record)
    return records


# ---------------------------------------------------------------------------
# Fresh-repository candidate validation


def is_protected(path):
    """Workflow, credential, agent-instruction and gate paths; casefolded."""
    folded = path.casefold()
    parts = folded.split("/")
    if any(part.startswith(".git") or part.startswith(".env") or part in PROTECTED_DIRS for part in parts):
        return True
    if parts[-1] in PROTECTED_NAMES:
        return True
    return folded.startswith("scripts/ci/") or folded in PROTECTED_FILES


class FreshRepo:
    """A bare repository isolated from system/global config, hooks and fsmonitor."""

    def __init__(self, root, runner=subprocess.run):
        self.dir = Path(root) / "candidate.git"
        self.home = Path(root) / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.runner = runner
        keep = ("PATH", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
                "SSL_CERT_FILE", "SSL_CERT_DIR", "GIT_SSL_CAINFO", "SYSTEMROOT", "TMPDIR")
        self.env = {key: os.environ[key] for key in keep if key in os.environ}
        self.env.update(HOME=str(self.home), LANG="C", LC_ALL="C", GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
        self.run_git(["init", "-q", "--bare", str(self.dir)], bare=False)

    def run_git(self, args, token=None, input=None, ok=(0,), bare=True, timeout=180, fsck=False):
        env = dict(self.env)
        if token is not None:
            # Header via environment-scoped config: not argv, not on disk.
            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0=f"http.{GITHUB_HOST}/.extraheader",
                       GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {basic}")
        prefix = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
                  "-c", "credential.helper="]
        if fsck:
            # Before Git 2.46 a bundle fetch ignores these, so check_objects() also runs a full fsck.
            prefix += ["-c", "fetch.fsckObjects=true", "-c", "transfer.fsckObjects=true"]
        if bare:
            prefix += ["--git-dir", str(self.dir)]
        result = self.runner(prefix + list(args), env=env, input=input, capture_output=True, timeout=timeout)
        if result.returncode not in ok:
            raise Refused("git operation failed: " + str(args[0]))
        stdout = result.stdout
        return (stdout, result.returncode) if ok != (0,) else stdout

    def text(self, *args, **kwargs):
        return self.run_git(list(args), **kwargs).decode("utf-8", "strict").strip()


def diff_entries(raw):
    """Parse `git diff-tree -r -z --raw --no-renames` output."""
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise Refused("unexpected diff output")
    entries = []
    for meta, path in zip(fields[0::2], fields[1::2]):
        parts = meta.decode("ascii", "strict").lstrip(":").split(" ")
        if len(parts) != 5:
            raise Refused("unexpected diff output")
        try:
            name = path.decode("utf-8", "strict")
        except UnicodeDecodeError:
            raise Refused("changed path is not UTF-8") from None
        if not name or any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
            raise Refused("changed path has control characters")
        if any(part in {"", ".", ".."} for part in name.split("/")):
            raise Refused("changed path has an empty, '.' or '..' component")
        entries.append({"old_mode": parts[0], "new_mode": parts[1], "old": parts[2], "new": parts[3],
                        "status": parts[4], "path": name})
    return entries


def check_entries(entries):
    for entry in entries:
        if is_protected(entry["path"]):
            raise Refused("candidate changes a protected path")
        status, old, new = entry["status"], entry["old_mode"], entry["new_mode"]
        if status == "D":
            continue
        if status not in {"A", "M"}:
            raise Refused("candidate changes a file type")
        # Only regular files; an executable bit only where it already existed.
        if new != "100644" and not (new == "100755" and status == "M" and old == "100755"):
            raise Refused("candidate adds a symlink, submodule or executable mode")


def bounded_size(repo, entries):
    blobs = sorted({e["new"] for e in entries if e["status"] != "D"})
    if len({e["path"] for e in entries}) > MAX_PATHS:
        raise Refused("candidate changes too many paths")
    if not blobs:
        return
    out = repo.run_git(["cat-file", "--batch-check=%(objecttype) %(objectsize)"], input=("\n".join(blobs) + "\n").encode())
    total = 0
    for line in out.decode().splitlines():
        kind, _, size = line.partition(" ")
        if kind != "blob" or not size.isdigit():
            raise Refused("candidate object is not a blob")
        total += int(size)
    if total > MAX_DIFF_BYTES:
        raise Refused("candidate diff too large")


def credentials(data):
    # Latin-1 maps every byte to one character, so ASCII tokens match in any blob.
    return set(CREDENTIAL.findall(data.decode("latin-1")))


def check_credentials(repo, entries, commits):
    """Refuse a commit message, path or blob of any new commit that adds a token-shaped string."""
    for commit in commits:
        if credentials(repo.run_git(["cat-file", "commit", commit])):
            raise Refused("candidate commit message contains a credential-shaped string")
    for entry in entries:
        if entry["status"] == "D":
            continue
        if CREDENTIAL.search(entry["path"]):
            raise Refused("candidate adds a credential-shaped string")
        old = set()
        if entry["status"] == "M":
            size = repo.text("cat-file", "-s", entry["old"])
            if not size.isdigit() or int(size) > MAX_DIFF_BYTES:
                raise Refused("candidate diff too large")
            old = credentials(repo.run_git(["cat-file", "blob", entry["old"]]))
        if credentials(repo.run_git(["cat-file", "blob", entry["new"]])) - old:
            raise Refused("candidate adds a credential-shaped string")


def check_objects(repo):
    """fsck every object, including the bundle's, whatever the runner's Git version.

    `.`, `..` and `.git` tree entries are errors here, not warnings; duplicate entries already are.
    """
    severities = []
    for msg in ("hasDot", "hasDotdot", "hasDotgit"):
        severities += ["-c", f"fsck.{msg}=error"]
    try:
        repo.run_git(severities + ["fsck", "--no-dangling", "--no-progress"])
    except Refused:
        raise Refused("candidate objects fail git fsck") from None


def check_committers(repo, commits):
    for commit in commits:
        name, _, email = repo.text("show", "-s", "--format=%cn%x00%ce", commit).partition("\0")
        if (name, email) != CLAUDE_COMMITTER:
            raise Refused("candidate commit has a non-Claude committer")


def validate_candidate(repo, config, bundle):
    """Return the validated candidate SHA, or raise Refused. No push here."""
    head, merge_base = config["base"], config["merge_base"]
    repo.run_git(["bundle", "verify", str(bundle)])
    heads = repo.text("bundle", "list-heads", str(bundle)).splitlines()
    if len(heads) != 1 or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})? HEAD", heads[0]):
        raise Refused("bundle must carry exactly one HEAD")
    try:
        # Git 2.46+ fscks bundle objects here; older Git leaves that to check_objects().
        repo.run_git(["fetch", "-q", "--no-tags", str(bundle), "HEAD:refs/claude/candidate"], fsck=True)
    except Refused:
        raise Refused("candidate bundle refused at fetch (fsck or corrupt pack)") from None
    check_objects(repo)
    candidate = repo.text("rev-parse", "--verify", "refs/claude/candidate^{commit}")
    if not SHA.fullmatch(candidate) or candidate in {head, merge_base}:
        raise Refused("bundle adds no new commit")
    excludes = ["^" + head] + (["^" + merge_base] if merge_base else [])
    rows = [line.split() for line in repo.text("rev-list", "--parents", candidate, *excludes).splitlines()]
    if not rows or len(rows) > MAX_COMMITS:
        raise Refused("candidate commit count out of bounds")
    commits = [row[0] for row in rows]
    if config["mode"] == "conflict":
        # Exactly one new commit: the merge of the recorded base into the head.
        if rows != [[candidate, head, merge_base]]:
            raise Refused("conflict candidate must be one merge of the recorded head and base")
        check_committers(repo, commits)
        tree_out, code = repo.run_git(["merge-tree", "--write-tree", "--name-only", "-z", head, merge_base], ok=(0, 1))
        fields = tree_out.split(b"\0")
        auto_tree = fields[0].decode("ascii", "strict")
        conflicted = []
        for field in fields[1:]:
            if not field:
                break
            conflicted.append(field.decode("utf-8", "replace"))
        if not SHA.fullmatch(auto_tree) or (code == 1 and not conflicted):
            raise Refused("merge-tree result unavailable")
        if any(is_protected(path) for path in conflicted):
            raise Refused("merge conflicts in a protected path need a human")
        entries = diff_entries(repo.run_git(["diff-tree", "-r", "-z", "--raw", "--no-renames", auto_tree, candidate + "^{tree}"]))
        check_entries(entries)
        bounded_size(repo, entries)
        check_credentials(repo, entries, commits)
        return candidate
    entries = []
    for row in rows:
        # Linear: every new commit has exactly one parent and descends from head.
        if len(row) != 2:
            raise Refused("candidate history is not linear")
        is_ancestor = repo.run_git(["merge-base", "--is-ancestor", head, row[0]], ok=(0, 1))[1]
        if is_ancestor != 0:
            raise Refused("candidate does not descend from the recorded head")
        entries += diff_entries(repo.run_git(["diff-tree", "-r", "-z", "--raw", "--no-renames", "--no-commit-id", row[1], row[0]]))
    check_committers(repo, commits)
    check_entries(entries)
    bounded_size(repo, entries)
    check_credentials(repo, entries, commits)
    return candidate


def fetch_recorded(repo, config, token):
    url = f"{GITHUB_HOST}/{config['repo']}.git"
    repo.run_git(["fetch", "-q", "--no-tags", url, f"+refs/heads/{config['ref']}:refs/claude/head"], token=token)
    if repo.text("rev-parse", "--verify", "refs/claude/head^{commit}") != config["base"]:
        raise Refused("PR branch moved since the recorded checkout")
    if config["mode"] == "conflict":
        repo.run_git(["fetch", "-q", "--no-tags", url, f"+refs/heads/{config['base_ref']}:refs/claude/base"], token=token)
        # A merge against a base that has since moved would leave the PR conflicted; a fresh scan retries.
        if repo.text("rev-parse", "--verify", "refs/claude/base^{commit}") != config["merge_base"]:
            raise Refused("base branch moved since the recorded checkout")


def push_candidate(repo, config, candidate, token):
    url = f"{GITHUB_HOST}/{config['repo']}.git"
    # Ordinary push: no force, no lease. A moved remote is rejected.
    repo.run_git(["push", "-q", "--no-verify", url, f"{candidate}:refs/heads/{config['ref']}"], token=token)


# ---------------------------------------------------------------------------
# Outbox publication


def reply_target_ok(config, comment_id, read):
    target = read(f"repos/{config['repo']}/pulls/comments/{comment_id}")
    pull = f"https://api.github.com/repos/{config['repo']}/pulls/{config['number']}"
    return (isinstance(target, dict) and type(target.get("id")) is int and target["id"] == comment_id
            and target.get("in_reply_to_id") is None
            and str(target.get("pull_request_url") or "").casefold() == pull.casefold())


def receipt_body(consumer, config, item, status, commit):
    data = {"root": item["root"], "fingerprint": item["fingerprint"], "status": status, "base": config["base"]}
    if status == "addressed":
        data["commit"] = commit
        text = f"Workflow-verified result: addressed by pushed commit `{commit}`."
    else:
        text = "Workflow-recorded result: Claude handed this finding to a human."
    return consumer.marker("result", data) + "\n" + text


def publish_records(config, env, records, pushed, write, read, consumer):
    """Post validated records in order as claude[bot]. Returns (posted, refused)."""
    roots = {item["root"]: item for item in config["receipts"]}
    allowed = {pushed} if pushed else {config["base"]}
    root = f"repos/{config['repo']}"
    posted = refused = 0
    for record in records:
        try:
            live_head(config, env, read, allowed)
            op = record["op"]
            if op == "comment":
                write("POST", f"{root}/issues/{config['number']}/comments", {"body": record["body"]})
            elif op == "label":
                write("POST", f"{root}/issues/{config['number']}/labels", {"labels": [HANDOFF_LABEL]})
            else:
                target = record["comment_id"] if op == "reply" else record["root"]
                if not reply_target_ok(config, target, read):
                    raise Refused("reply target is not a root on this PR")
                if op == "reply":
                    body = record["body"]
                else:
                    if consumer is None or (record["status"] == "addressed" and not pushed):
                        raise Refused("addressed receipt needs a verified push")
                    body = receipt_body(consumer, config, roots[target], record["status"], pushed)
                write("POST", f"{root}/pulls/{config['number']}/comments/{target}/replies", {"body": body})
            posted += 1
        except LiveStateRefused:
            refused = len(records) - posted
            break
        except Refused:
            refused += 1
        except (HttpError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
            refused += 1
    return posted, refused


# ---------------------------------------------------------------------------
# Workflow-owned terminal audit (moved from the CSL-201 pre-push guard)


def bounded_reader():
    deadline = time.monotonic() + 70
    calls = 0
    def read(path):
        nonlocal calls
        remaining = deadline - time.monotonic()
        calls += 1
        if remaining <= 0 or calls > 40:
            raise AuditReadError("terminal read budget exhausted")
        try:
            return api(path, timeout=min(10, remaining))
        except ValueError as error:
            raise AuditReadError("invalid terminal API response") from error
    return read


def receipt_audit(config, read, consumer):
    items = config.get("receipts", [])
    if isinstance(items, list) and not items:
        return "Per-root receipt audit: not applicable (no captured receipt context)."
    try:
        if consumer is None or not isinstance(items, list) or len(items) > 50:
            raise ValueError("receipt context unavailable or over limit")
        roots = set()
        for item in items:
            rid = item.get("root")
            if (type(rid) is not int or rid <= 0 or rid in roots
                    or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("fingerprint") or ""))
                    or item.get("base") != config["base"]):
                raise ValueError("invalid captured receipt context")
            roots.add(rid)
        head = current_pr(config, read)
        comments = []
        for page in range(1, 6):
            batch = read(f"repos/{config['repo']}/pulls/{config['number']}/comments?per_page=100&page={page}")
            if not isinstance(batch, list) or any(not isinstance(c, dict) for c in batch):
                raise ValueError("invalid comment snapshot")
            comments.extend(batch)
            if len(batch) < 100:
                break
        else:
            raise ValueError("comment page limit reached")
        ids = [c.get("id") for c in comments]
        if any(type(cid) is not int or cid <= 0 for cid in ids) or len(set(ids)) != len(ids):
            raise ValueError("inconsistent comment snapshot")
        cache = {}
        def cached(path):
            if path not in cache:
                cache[path] = read(path)
            return cache[path]
        def compare(base, tip):
            try:
                return cached(f"repos/{config['repo']}/compare/{base}...{tip}")["status"]
            except subprocess.CalledProcessError as error:
                if re.search(r"\(HTTP (?:404|422)\)", str(error.stderr or "")):
                    return "invalid"
                raise
        def permission(login):
            return cached(f"repos/{config['repo']}/collaborators/{login}/permission")["permission"]
        reports = consumer.terminal_receipts(items, comments, head, compare, permission)
        if current_pr(config, read) != head:
            raise ValueError("head changed during receipt audit")
        details = "; ".join(f"{r['root']}={r['status']}" for r in reports)
        return f"Per-root receipt audit at `{head}`: {details}."
    except (AuditReadError, OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        return "Per-root receipt audit: unverified (read, context, snapshot or bounded-budget failure)."


def execution_reason(path, outcome):
    """Read only coarse result fields; never echo model text or command inputs."""
    fallback = "execution-result-unavailable" if outcome == "success" else "model-step-failed-or-timed-out"
    try:
        if not path:
            return fallback
        data = json.loads(read_regular(path, MAX_RESULT_BYTES).decode("utf-8"))
        items = data if isinstance(data, list) else [data]
        results = [r for r in items if isinstance(r, dict) and r.get("type") == "result"]
        if not results:
            return fallback
        for result in results:
            turns = result.get("num_turns")
            if (type(turns) is int and turns > 30) or result.get("subtype") == "error_max_turns":
                return "turn-limit"
            if result.get("permission_denials"):
                return "command-denied"
            if type(turns) is not int or turns < 0:
                return fallback
            if result.get("is_error") or result.get("subtype") != "success":
                return "model-error"
        return "model-completed" if outcome == "success" else fallback
    except (OSError, ValueError, TypeError, RecursionError, Refused):
        return fallback


def terminal(config, state_path, execution, outcome, read=api, consumer=None, notes=""):
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
    except (AuditReadError, OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        pass  # Unknown is not successful, and never a needs-human result receipt.
    return (f"Claude fixer terminal audit: {reason}. Checkout `{config['base']}`. "
            f"Guarded candidate reachable on the live PR: `{pushed}`.\n\n"
            + (f"Publisher: {notes}\n\n" if notes else "")
            + receipt_audit(config, read, consumer) + "\n\n"
            "This audit is not an addressed or needs-human finding receipt. "
            "Receipt evidence is a current snapshot for captured roots and may predate this run; "
            "it does not prove this run fixed them or establish current eligibility or READY. "
            "A fresh authorized run must recheck genuine unresolved, current findings "
            "and existing author permissions, three-attempt budget and eight-commit guard. "
            "Do not reopen resolved findings or retry this checkout. No fetch, rebase or merge recovery. "
            "The PR author should relay any remaining blocker to its Linear issue.")


# ---------------------------------------------------------------------------
# Entry point


def load_consumer(directory):
    try:
        spec = importlib.util.spec_from_file_location("workflow_receipts", Path(directory) / "receipts.py")
        consumer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(consumer)
        return consumer
    except (OSError, ImportError, ValueError, AttributeError, SyntaxError, TypeError):
        return None


MODEL_FAILURES = {"failure", "cancelled", "skipped"}


def publish(config, env, state_path, artifact, deps):
    """Validate and publish. Returns (pushed SHA or "", fixed note strings)."""
    notes = []
    if env.get("PREPARED_RUN_ATTEMPT", "") != env.get("GITHUB_RUN_ATTEMPT", ""):
        raise Refused("run attempt differs from the prepared reservation")
    # One publish attempt per prepared run: exclusive creation, like the guard.
    with state_path.open("x", encoding="utf-8") as handle:
        handle.write("{}")
    # Only a model step that succeeded publishes: no partial commits, no queued claims.
    outcome = env.get("MODEL_OUTCOME", "")
    if outcome != "success":
        raise Refused(f"model step did not succeed ({outcome if outcome in MODEL_FAILURES else 'unknown'})")
    bundle = artifact_file(artifact, "candidate.bundle")
    # A failed `git bundle create` must not look like a run with no commits.
    package = env.get("PACKAGE_STATUS", "")
    if package not in {"bundled", "unchanged"} or (package == "bundled") != (bundle is not None):
        raise Refused("model output packaging failed or is inconsistent")
    outbox = artifact_file(artifact, "outbox.jsonl")
    try:
        roots = {item["root"] for item in config["receipts"]}
        records = load_outbox(read_regular(outbox, MAX_OUTBOX_BYTES) if outbox else None, config["mode"], roots)
    except (Refused, OSError) as error:
        records = []
        notes.append("outbox rejected (" + (str(error) if isinstance(error, Refused) else "unreadable") + ")")
    if bundle is None and not records:
        notes.append("no candidate commits and no queued writes")
        return "", notes
    read, out = deps["read"], deps["out"]
    live_head(config, env, read, {config["base"]})
    try:
        token = deps["mint"](env)
    except WorkflowValidationSkip:
        out("::notice::Claude App token exchange reported workflow validation; nothing published. "
            "This is expected until this workflow version is on the default branch.")
        notes.append("App token unavailable (workflow validation); nothing published")
        return "", notes
    pushed = ""
    try:
        if bundle is not None:
            if bundle.stat().st_size > MAX_BUNDLE_BYTES:
                raise Refused("bundle too large")
            with tempfile.TemporaryDirectory(dir=env.get("RUNNER_TEMP") or None) as tmp:
                repo = deps["repo"](tmp)
                fetch_recorded(repo, config, env.get("GH_TOKEN") or None)
                candidate = validate_candidate(repo, config, bundle)
                state_path.write_text(json.dumps({"candidate": candidate}), encoding="utf-8")
                live_head(config, env, read, {config["base"]})
                push_candidate(repo, config, candidate, token)
            for attempt in range(3):
                try:
                    if live_head(config, env, read, {candidate}) == candidate:
                        pushed = candidate
                        break
                except Refused:
                    if attempt == 2:
                        raise
                    deps["sleep"](2)
            notes.append(f"pushed candidate `{pushed}` as one ordinary fast-forward")
        def write(method, path, body):
            return deps["http"](method, f"{GITHUB_API}/{path}", token=token, body=body)
        posted, refused = publish_records(config, env, records, pushed, write, read, deps["consumer"])
        notes.append(f"{posted} queued write(s) posted, {refused} refused")
    except Refused as error:
        notes.append(f"refused: {error}; queued writes dropped")
    finally:
        if not deps["revoke"](token):
            out("::warning::Claude App token revocation could not be confirmed.")
    return pushed, notes


def main(env=None, deps=None):
    env = dict(os.environ if env is None else env)
    directory = Path(__file__).resolve().parent
    deps = {"read": api, "out": print, "mint": lambda e: mint_app_token(e), "http": http_json,
            "revoke": revoke_app_token, "repo": FreshRepo, "sleep": time.sleep,
            "consumer": load_consumer(directory), "post": None, **(deps or {})}
    out = deps["out"]
    config = load_config(env)
    state_path = Path(env.get("PUBLISH_STATE_DIR") or directory) / "attempt.json"
    artifact = env.get("ARTIFACT_DIR", "")
    notes = []
    code = 0
    try:
        _pushed, notes = publish(config, env, state_path, artifact, deps)
    except Refused as error:
        notes = [f"refused: {error}; nothing published"]
    except FileExistsError:
        notes = ["publish attempt already used; nothing published"]
    except Exception as error:  # noqa: BLE001 - always audit; never echo details.
        notes = [f"publisher error ({type(error).__name__}); nothing further published"]
        code = 1
    for note in notes:
        out(f"::notice::Claude publisher: {note}")
    result = artifact_file(artifact, "result.json")
    body = terminal(config, state_path, str(result) if result else "", env.get("MODEL_OUTCOME", ""),
                    read=bounded_reader() if deps["read"] is api else deps["read"],
                    consumer=deps["consumer"], notes="; ".join(notes))
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(body + "\n")
    out(body)
    try:
        if deps["post"] is not None:
            deps["post"](config, body)
        else:
            command(["gh", "api", "--method", "POST",
                     f"repos/{config['repo']}/issues/{config['number']}/comments", "--input", "-"],
                    input=json.dumps({"body": body}))
    except (OSError, subprocess.SubprocessError):
        out("::error::Terminal audit saved to job summary; PR handback could not be confirmed.")
        return 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())

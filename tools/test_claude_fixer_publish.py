#!/usr/bin/env python3
"""CSL-377 split writers: trusted publisher, model outbox and workflow wiring.

Offline only. Git validations use real temporary repositories; the token
exchange and GitHub API are fakes. No live/provider qualification.

    python -X utf8 -B tools/test_claude_fixer_publish.py
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


p = load("claude_fixer_publish", "scripts/ci/claude_fixer_publish.py")
o = load("claude_fixer_outbox", "scripts/ci/claude_fixer_outbox.py")
receipts = load("claude_fixer_receipts", "scripts/ci/claude_fixer_receipts.py")
WORKFLOWS = {name: ROOT / ".github/workflows" / name
             for name in ("claude.yml", "conflict-scan.yml", "claude-conflict-writer.yml")}
REPO = "acme/example"
CLAUDE = p.CLAUDE_COMMITTER
TOKEN = "ghs_" + "S3cr3tAppToken" * 3
OIDC = "eyJ" + "oidcSecretValue" * 3


def git_env(committer=CLAUDE, author=None):
    author = author or committer
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME=author[0], GIT_AUTHOR_EMAIL=author[1],
               GIT_COMMITTER_NAME=committer[0], GIT_COMMITTER_EMAIL=committer[1])
    return env


class Work:
    """A working clone where the 'model' makes commits."""

    def __init__(self, path):
        self.path = Path(path)

    def git(self, *args, committer=CLAUDE, author=None, check=True, input=None):
        result = subprocess.run(["git", "-C", str(self.path), *args], env=git_env(committer, author),
                                capture_output=True, text=True, input=input)
        if check and result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout.strip()

    def write(self, name, content="x\n", mode=None):
        file = self.path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)
        if mode:
            file.chmod(mode)
        return file

    def commit(self, files=None, message="change", committer=CLAUDE, author=None, delete=()):
        for name, content in (files or {}).items():
            self.write(name, content)
        for name in delete:
            self.git("rm", "-q", name)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message, committer=committer, author=author)
        return self.git("rev-parse", "HEAD")


class GitCase(unittest.TestCase):
    """Source repo (the 'origin'), a model clone, and a fresh validator repo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.src = Work(root / "src")
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.src.path)], env=git_env(), check=True)
        self.src.write("app/a.py", "a = 1\n")
        self.src.write("app/b.py", "b = 1\n")
        self.src.write("scripts/run.sh", "#!/bin/sh\n", mode=0o755)
        self.src.write(".github/workflows/ci.yml", "on: push\n")
        self.src.write("AGENTS.md", "rules\n")
        self.head = self.src.commit(message="initial", committer=("Alice", "alice@example.com"))
        self.base = ""
        self.model = Work(root / "model")
        subprocess.run(["git", "clone", "-q", str(self.src.path), str(self.model.path)], env=git_env(), check=True)
        self.model.git("checkout", "-q", "-B", "fix", self.head)
        self.root = root

    def config(self, mode="fixer"):
        return {"repo": REPO, "number": "7", "ref": "fix", "base": self.head, "mode": mode,
                "merge_base": self.base if mode == "conflict" else "", "base_ref": "main" if mode == "conflict" else "",
                "receipts": []}

    def bundle(self, *extra_excludes, name="candidate.bundle"):
        path = self.root / name
        self.model.git("bundle", "create", str(path), "HEAD", "^" + self.head, *[f"^{e}" for e in extra_excludes])
        return path

    def validate(self, mode="fixer", bundle=None):
        repo = p.FreshRepo(tempfile.mkdtemp(dir=self.root))
        refs = [self.head] + ([self.base] if self.base else [])
        repo.run_git(["fetch", "-q", str(self.src.path), *refs])
        if self.base:
            repo.run_git(["fetch", "-q", str(self.src.path), "+refs/heads/main:refs/claude/base"])
        config = self.config(mode)
        return p.validate_candidate(repo, config, bundle or self.bundle(*( [self.base] if mode == "conflict" else [])))

    def refused(self, pattern, mode="fixer", bundle=None):
        with self.assertRaisesRegex(p.Refused, pattern):
            self.validate(mode, bundle)


class ProtectedPathTests(unittest.TestCase):
    def test_every_protected_pattern_including_nested_git_components(self):
        for path in (".github/workflows/ci.yml", ".github/CODEOWNERS", ".GitHub/x", ".gitignore", ".gitmodules",
                     "app/.gitattributes", "app/.git/config", "docs/.github/x.yml", "a/b/.gitkeep",
                     ".claude/settings.json", "app/.claude/commands/x.md", ".claude-fixer-out/outbox.jsonl",
                     ".claude-fixer-replies/a.txt", "scripts/ci/x.py", "SCRIPTS/CI/x.py", "scripts/ci/nested/y.py",
                     "tools/ci_gate.py", "tools/required_ci_gate.py", "tools/test_claude_pr_fixer.py",
                     "CODEOWNERS", "docs/CODEOWNERS", "AGENTS.md", "app/AGENTS.md", "CLAUDE.md", "claude.md",
                     ".env", ".env.local", "app/.env", ".envrc", ".mcp.json", "app/.mcp.json"):
            with self.subTest(path=path):
                self.assertTrue(p.is_protected(path))
        for path in ("app/a.py", "tools/test_other.py", "docs/agents.md.bak", "scripts/run.sh", "app/environment.py",
                     "github/x", "tools/ci_gate.py.md", "app/claude/x.py", "docs/scripts/ci/x.py", "mcp.json"):
            with self.subTest(path=path):
                self.assertFalse(p.is_protected(path))


class LinearValidationTests(GitCase):
    def test_valid_linear_commits_pass(self):
        self.model.commit({"app/a.py": "a = 2\n"})
        last = self.model.commit({"app/c.py": "c = 1\n"}, delete=["app/b.py"])
        self.assertEqual(self.validate(), last)

    def test_protected_paths_are_refused_including_deletes_and_reverted_intermediates(self):
        for change in ({".github/workflows/ci.yml": "on: pull_request_target\n"}, {"AGENTS.md": "new rules\n"},
                       {"scripts/ci/new.py": "x\n"}, {".env": "SECRET=1\n"}, {"app/.gitattributes": "* -text\n"},
                       {"docs/CODEOWNERS": "* @x\n"}, {".claude/settings.json": "{}\n"}):
            with self.subTest(change=change):
                self.model.git("reset", "-q", "--hard", self.head)
                self.model.commit(change)
                self.refused("protected path")
        self.model.git("reset", "-q", "--hard", self.head)
        self.model.commit(delete=["AGENTS.md"])
        self.refused("protected path")
        # Net diff empty, but the pushed history would still carry it.
        self.model.git("reset", "-q", "--hard", self.head)
        self.model.commit({"AGENTS.md": "evil\n"})
        self.model.commit({"AGENTS.md": "rules\n"})
        self.refused("protected path")

    def test_symlink_submodule_and_mode_changes_are_refused(self):
        self.model.git("reset", "-q", "--hard", self.head)
        os.symlink("../etc/passwd", self.model.path / "app/link")
        self.model.commit()
        self.refused("symlink, submodule or executable")
        self.model.git("reset", "-q", "--hard", self.head)
        self.model.git("update-index", "--add", "--cacheinfo", f"160000,{self.head},app/sub")
        self.model.git("commit", "-q", "-m", "submodule")
        self.refused("symlink, submodule or executable")
        self.model.git("reset", "-q", "--hard", self.head)
        self.model.write("app/tool.sh", "#!/bin/sh\n", mode=0o755)
        self.model.commit()
        self.refused("symlink, submodule or executable")
        self.model.git("reset", "-q", "--hard", self.head)
        (self.model.path / "app/a.py").chmod(0o755)
        self.model.commit()
        self.refused("symlink, submodule or executable")

    def test_existing_executable_may_change_or_lose_its_bit(self):
        self.model.write("scripts/run.sh", "#!/bin/sh\necho hi\n", mode=0o755)
        self.model.commit()
        self.assertTrue(self.validate())
        (self.model.path / "scripts/run.sh").chmod(0o644)
        last = self.model.commit()
        self.assertEqual(self.validate(), last)

    def test_ancestry_and_linear_history(self):
        # Orphan history never descends from the recorded head.
        self.model.git("checkout", "-q", "--orphan", "other")
        self.model.commit({"app/z.py": "z\n"})
        self.refused("linear|descend")
        # Merge commits are refused for fixer/mention writers.
        self.model.git("checkout", "-q", "-B", "fix", self.head)
        self.model.git("checkout", "-q", "-b", "side")
        side = self.model.commit({"app/s.py": "s\n"})
        self.model.git("checkout", "-q", "fix")
        self.model.commit({"app/f.py": "f\n"})
        self.model.git("merge", "-q", "--no-ff", "-m", "merge", side)
        self.refused("not linear")
        # Linear, but built on an older commit: it never descends from the head.
        self.src.git("checkout", "-q", "-b", "older", self.head)
        self.head = self.src.commit({"app/b.py": "b = 9\n"})
        self.model.git("fetch", "-q", "origin", "older")
        self.model.git("checkout", "-q", "-B", "fix", self.head + "~1")
        self.model.commit({"app/f.py": "f\n"})
        self.refused("descend from the recorded head")
        # A bundle without a new commit is not a candidate.
        self.model.git("reset", "-q", "--hard", self.head)
        self.model.git("commit", "-q", "--allow-empty", "-m", "noop")
        self.assertTrue(self.validate())

    def test_commit_count_size_and_path_bounds(self):
        for i in range(p.MAX_COMMITS + 1):
            self.model.commit({"app/a.py": f"a = {i}\n"})
        self.refused("commit count")
        self.model.git("reset", "-q", "--hard", self.head)
        self.model.commit({"app/big.py": "x" * 2000})
        with patch.object(p, "MAX_DIFF_BYTES", 1000):
            self.refused("too large")
        with patch.object(p, "MAX_PATHS", 1):
            self.model.commit({"app/c.py": "c\n"})
            self.refused("too many paths")

    def test_committer_must_be_claude(self):
        # GIT_COMMITTER_NAME=Alice would forge a human commit and reset the streak.
        self.model.commit({"app/a.py": "a = 2\n"}, committer=("Alice", "alice@example.com"))
        self.refused("non-Claude committer")
        for committer in (("claude[bot]", "alice@example.com"), ("Claude", CLAUDE[1]),
                          ("claude[bot]", "41898282+claude[bot]@users.noreply.github.com")):
            with self.subTest(committer=committer):
                self.model.git("reset", "-q", "--hard", self.head)
                self.model.commit({"app/a.py": "a = 3\n"})
                self.model.commit({"app/b.py": "b = 3\n"}, committer=committer)
                self.refused("non-Claude committer")
        # An --author override alone is allowed: the streak counts committers.
        self.model.git("reset", "-q", "--hard", self.head)
        last = self.model.commit({"app/a.py": "a = 4\n"}, author=("Alice", "alice@example.com"))
        self.assertEqual(self.validate(), last)

    def test_bundle_shape_is_checked(self):
        self.model.commit({"app/a.py": "a = 2\n"})
        two = self.root / "two.bundle"
        self.model.git("bundle", "create", str(two), "HEAD", "fix", "^" + self.head)
        self.refused("exactly one HEAD", bundle=two)
        corrupt = self.root / "corrupt.bundle"
        corrupt.write_bytes(b"# v2 git bundle\nnot a bundle")
        self.refused("git operation failed|exactly one HEAD", bundle=corrupt)
        # Prerequisites the validator does not have fail verification.
        self.model.commit({"app/b.py": "b = 2\n"})
        middle = self.model.git("rev-parse", "HEAD~1")
        late = self.root / "late.bundle"
        self.model.git("bundle", "create", str(late), "HEAD", "^" + middle)
        repo = p.FreshRepo(tempfile.mkdtemp(dir=self.root))
        repo.run_git(["fetch", "-q", str(self.src.path), self.head])
        with self.assertRaisesRegex(p.Refused, "git operation failed"):
            p.validate_candidate(repo, self.config(), late)


class TreeEntryTests(GitCase):
    """Hand-built trees that `git mktree` would not write.

    Git 2.46+ refuses them at the bundle fetch, older Git in check_objects(); the reason is the same.
    """

    def raw_tree(self, entries):
        body = b"".join(mode + b" " + name + b"\0" + bytes.fromhex(sha) for mode, name, sha in entries)
        raw = self.root / "raw-tree"
        raw.write_bytes(body)
        return self.model.git("hash-object", "-t", "tree", "-w", "--literally", str(raw))

    def commit_tree(self, entries):
        blob = self.model.git("hash-object", "-w", "--stdin", input="x\n")
        inner = self.raw_tree([(b"100644", b"x", blob)])
        root = self.raw_tree([(b"40000", name, inner) if kind == "tree" else (b"100644", name, blob)
                              for kind, name in entries])
        commit = self.model.git("commit-tree", root, "-p", self.head, "-m", "crafted")
        self.model.git("update-ref", "HEAD", commit)

    def test_dot_dotdot_and_duplicate_entries_are_refused(self):
        for entries in ([("tree", b".."), ("blob", b"z")], [("tree", b"."), ("blob", b"z")],
                        [("tree", b".git"), ("blob", b"z")], [("tree", b".GIT"), ("blob", b"z")],
                        [("blob", b"z"), ("blob", b"z")]):
            with self.subTest(entries=entries):
                self.model.git("update-ref", "HEAD", self.head)
                self.commit_tree(entries)
                # Older Git refuses in check_objects(), Git 2.46+ at the fsck-enabled fetch.
                self.refused(r"^(candidate objects fail git fsck|candidate bundle refused at fetch \(fsck or corrupt pack\))$")

    def test_check_objects_refuses_crafted_trees_without_a_fetch(self):
        # Objects written straight into the validator repo, so check_objects() runs on every Git version.
        for name in (b"..", b".", b".git", b".GIT", b"z"):
            with self.subTest(name=name):
                repo = p.FreshRepo(tempfile.mkdtemp(dir=self.root))
                blob_file = self.root / "blob"
                blob_file.write_text("x\n")
                blob = repo.text("hash-object", "-w", str(blob_file))
                second = (b"40000", b"z", self.raw_inner(repo, blob)) if name != b"z" else (b"100644", b"z", blob)
                first = (b"40000", name, self.raw_inner(repo, blob)) if name != b"z" else (b"100644", b"z", blob)
                raw = self.root / "root-tree"
                raw.write_bytes(b"".join(m + b" " + n + b"\0" + bytes.fromhex(sha) for m, n, sha in (first, second)))
                repo.run_git(["hash-object", "-t", "tree", "-w", "--literally", str(raw)])
                with self.assertRaisesRegex(p.Refused, "^candidate objects fail git fsck$"):
                    p.check_objects(repo)

    def test_fsck_refusal_at_the_bundle_fetch_keeps_the_fsck_reason(self):
        # Git 2.46+ rejects a bad tree at the fsck-enabled bundle fetch, before check_objects().
        self.model.commit({"app/a.py": "a = 2\n"})
        bundle = self.bundle()
        repo = p.FreshRepo(tempfile.mkdtemp(dir=self.root))
        repo.run_git(["fetch", "-q", str(self.src.path), self.head])
        real = repo.run_git
        def run_git(args, **kwargs):
            if args[0] == "fetch" and kwargs.get("fsck"):
                raise p.Refused("git operation failed: fetch")
            return real(args, **kwargs)
        repo.run_git = run_git
        with self.assertRaisesRegex(p.Refused, r"^candidate bundle refused at fetch \(fsck or corrupt pack\)$"):
            p.validate_candidate(repo, self.config(), bundle)

    def test_check_objects_accepts_ordinary_history(self):
        repo = p.FreshRepo(tempfile.mkdtemp(dir=self.root))
        repo.run_git(["fetch", "-q", str(self.src.path), self.head])
        p.check_objects(repo)

    def raw_inner(self, repo, blob):
        raw = self.root / "inner-tree"
        raw.write_bytes(b"100644 x\0" + bytes.fromhex(blob))
        return repo.text("hash-object", "-t", "tree", "-w", "--literally", str(raw))

    def test_diff_paths_with_dot_or_empty_components_are_refused(self):
        for path in (b"../x", b"./x", b"a/../x", b"a//x", b"a/.", b"a/"):
            with self.subTest(path=path):
                raw = b":000000 100644 " + b"0" * 40 + b" " + b"1" * 40 + b" A\0" + path + b"\0"
                with self.assertRaisesRegex(p.Refused, "component"):
                    p.diff_entries(raw)


class RecordedFetchAndPushTests(GitCase):
    """fetch_recorded and push_candidate against a real file:// remote."""

    def setUp(self):
        super().setUp()
        self.host = self.root / "host"
        self.remote = self.host / f"{REPO}.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(self.src.path), str(self.remote)], env=git_env(), check=True)
        self.remote_git("update-ref", "refs/heads/fix", self.head)
        patcher = patch.object(p, "GITHUB_HOST", "file://" + str(self.host))
        patcher.start()
        self.addCleanup(patcher.stop)

    def remote_git(self, *args):
        return subprocess.run(["git", "--git-dir", str(self.remote), *args], env=git_env(),
                              capture_output=True, text=True, check=True).stdout.strip()

    def side_commit(self):
        """A commit on the remote only, as if another writer pushed."""
        tree = self.remote_git("rev-parse", self.head + "^{tree}")
        return self.remote_git("commit-tree", tree, "-p", self.head, "-m", "other writer")

    def candidate(self, config):
        repo = p.FreshRepo(tempfile.mkdtemp(dir=self.root))
        p.fetch_recorded(repo, config, TOKEN)
        return repo, p.validate_candidate(repo, config, self.bundle())

    def test_happy_path_pushes_exactly_the_candidate_without_leaving_the_token_on_disk(self):
        commit = self.model.commit({"app/a.py": "a = 2\n"})
        config = self.config()
        repo, candidate = self.candidate(config)
        self.assertEqual(candidate, commit)
        p.push_candidate(repo, config, candidate, TOKEN)
        self.assertEqual(self.remote_git("rev-parse", "refs/heads/fix"), commit)
        import base64
        encoded = base64.b64encode(f"x-access-token:{TOKEN}".encode())
        for base in (repo.dir, repo.home, self.remote):
            for file in Path(base).rglob("*"):
                if file.is_file():
                    data = file.read_bytes()
                    self.assertNotIn(TOKEN.encode(), data, file)
                    self.assertNotIn(encoded, data, file)

    def test_moved_branch_is_refused_before_validation(self):
        self.model.commit({"app/a.py": "a = 2\n"})
        self.remote_git("update-ref", "refs/heads/fix", self.side_commit())
        repo = p.FreshRepo(tempfile.mkdtemp(dir=self.root))
        with self.assertRaisesRegex(p.Refused, "moved since the recorded checkout"):
            p.fetch_recorded(repo, self.config(), TOKEN)

    def test_push_after_a_remote_move_is_rejected_without_force(self):
        self.model.commit({"app/a.py": "a = 2\n"})
        config = self.config()
        repo, candidate = self.candidate(config)
        other = self.side_commit()
        self.remote_git("update-ref", "refs/heads/fix", other)
        with self.assertRaisesRegex(p.Refused, "git operation failed: push"):
            p.push_candidate(repo, config, candidate, TOKEN)
        self.assertEqual(self.remote_git("rev-parse", "refs/heads/fix"), other)

    def test_conflict_base_not_on_the_base_branch_is_refused(self):
        side = self.side_commit()
        self.remote_git("update-ref", "refs/heads/fix", side)
        config = dict(self.config("conflict"), base=side, merge_base=side)
        repo = p.FreshRepo(tempfile.mkdtemp(dir=self.root))
        with self.assertRaisesRegex(p.Refused, "not on the base branch"):
            p.fetch_recorded(repo, config, TOKEN)


class ConflictValidationTests(GitCase):
    def setUp(self):
        super().setUp()
        self.src.git("checkout", "-q", "-b", "fix")
        self.src.git("checkout", "-q", "main")
        self.base = self.src.commit({"app/a.py": "a = 'base'\n", "app/b.py": "b = 'base'\n"})
        self.src.git("checkout", "-q", "fix")
        self.head = self.src.commit({"app/a.py": "a = 'head'\n"})
        self.src.git("checkout", "-q", "main")
        self.model.git("fetch", "-q", "origin", "fix", "main")
        self.model.git("checkout", "-q", "-B", "fix", self.head)

    def merge(self, resolution=None, extra=None):
        self.model.git("merge", "--no-ff", "--no-commit", self.base, check=False)
        for name, content in {"app/a.py": "a = 'both'\n", **(resolution or {}), **(extra or {})}.items():
            self.model.write(name, content)
        self.model.git("add", "-A")
        self.model.git("commit", "-q", "-m", "merge base")
        return self.model.git("rev-parse", "HEAD")

    def test_one_merge_of_recorded_head_and_base_passes(self):
        merge = self.merge()
        self.assertEqual(self.validate("conflict"), merge)

    def test_merge_differs_from_auto_merge_only_outside_protected_paths(self):
        self.merge(extra={"app/new.py": "n\n"})
        self.assertTrue(self.validate("conflict"))
        self.model.git("reset", "-q", "--hard", self.head)
        self.merge(extra={".github/workflows/ci.yml": "on: pull_request_target\n"})
        self.refused("protected path", "conflict")

    def test_base_side_protected_changes_merge_cleanly(self):
        self.src.git("checkout", "-q", "main")
        self.base = self.src.commit({".github/workflows/ci.yml": "on: [push]\n"})
        self.model.git("fetch", "-q", "origin", "main")
        merge = self.merge()
        self.assertEqual(self.validate("conflict"), merge)

    def test_protected_conflict_needs_a_human(self):
        self.src.git("checkout", "-q", "fix")
        self.head = self.src.commit({"AGENTS.md": "head rules\n"})
        self.src.git("checkout", "-q", "main")
        self.base = self.src.commit({"AGENTS.md": "base rules\n"})
        self.model.git("fetch", "-q", "origin", "fix", "main")
        self.model.git("checkout", "-q", "-B", "fix", self.head)
        self.merge(resolution={"AGENTS.md": "head rules\n"})
        self.refused("protected path need a human", "conflict")

    def test_parent_rules(self):
        # A linear commit is not the conflict merge.
        self.model.commit({"app/a.py": "a = 'x'\n"})
        self.refused("one merge", "conflict")
        # Merge plus an extra commit: exactly one new commit is allowed.
        self.model.git("reset", "-q", "--hard", self.head)
        self.merge()
        self.model.commit({"app/c.py": "c\n"})
        self.refused("one merge", "conflict")
        # Reversed parents (base merged with head) are refused.
        self.model.git("checkout", "-q", "-B", "fix", self.base)
        self.model.git("merge", "--no-ff", "--no-commit", self.head, check=False)
        self.model.write("app/a.py", "a = 'both'\n")
        self.model.git("add", "-A")
        self.model.git("commit", "-q", "-m", "reverse")
        self.refused("one merge", "conflict")

    def test_human_committed_merge_is_refused(self):
        self.model.git("merge", "--no-ff", "--no-commit", self.base, check=False)
        self.model.write("app/a.py", "a = 'both'\n")
        self.model.git("add", "-A")
        self.model.git("commit", "-q", "-m", "merge", committer=("Alice", "alice@example.com"))
        self.refused("non-Claude committer", "conflict")


class ExecutionReasonTests(unittest.TestCase):
    def test_deeply_nested_result_falls_back_instead_of_suppressing_the_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            path.write_text("[" * 200_000 + "]" * 200_000)
            self.assertEqual(p.execution_reason(str(path), "success"), "execution-result-unavailable")
            self.assertEqual(p.execution_reason(str(path), "failure"), "model-step-failed-or-timed-out")


class OutboxSchemaTests(unittest.TestCase):
    def raw(self, *records):
        return ("\n".join(r if isinstance(r, str) else json.dumps(r) for r in records) + "\n").encode()

    def load(self, *records, mode="fixer", roots=frozenset({11})):
        return p.load_outbox(self.raw(*records), mode, set(roots))

    def test_valid_records(self):
        records = self.load({"op": "comment", "body": "hi"}, {"op": "reply", "comment_id": 5, "body": "ok"},
                            {"op": "receipt", "root": 11, "status": "addressed"})
        self.assertEqual([r["op"] for r in records], ["comment", "reply", "receipt"])
        self.assertEqual(self.load({"op": "label", "label": "needs-decision"}, mode="conflict"),
                         [{"op": "label", "label": "needs-decision"}])
        self.assertEqual(p.load_outbox(None, "fixer", set()), [])

    def test_invalid_records_reject_the_whole_outbox(self):
        bad = [
            ({"op": "delete", "body": "x"}, "fixer"), ({"op": "comment", "body": "x", "extra": 1}, "fixer"),
            ({"op": ["comment"], "body": "x"}, "fixer"), ({"body": "x"}, "fixer"), ("[1]", "fixer"), ("{bad", "fixer"),
            ('{"op":"comment","body":"a","body":"b"}', "fixer"),
            ({"op": "reply", "comment_id": "5", "body": "x"}, "fixer"), ({"op": "reply", "comment_id": True, "body": "x"}, "fixer"),
            ({"op": "reply", "comment_id": 0, "body": "x"}, "fixer"), ({"op": "reply", "comment_id": -3, "body": "x"}, "fixer"),
            ({"op": "comment", "body": "<!-- claude-fixer-result:{} -->"}, "fixer"),
            ({"op": "comment", "body": "fine <!-- codex-gate: READY -->"}, "fixer"),
            ({"op": "reply", "comment_id": 5, "body": "x <!--"}, "fixer"),
            ({"op": "comment", "body": ""}, "fixer"), ({"op": "comment", "body": " \n"}, "fixer"),
            ({"op": "comment", "body": "x" * (p.MAX_BODY + 1)}, "fixer"), ({"op": "comment", "body": 5}, "fixer"),
            ({"op": "comment", "body": "nul\x00"}, "fixer"),
            ({"op": "label", "label": "needs-decision"}, "fixer"), ({"op": "label", "label": "needs-decision"}, "mention"),
            ({"op": "label", "label": "ci:full"}, "conflict"), ({"op": "label", "label": ["needs-decision"]}, "conflict"),
            ({"op": "receipt", "root": 12, "status": "addressed"}, "fixer"),
            ({"op": "receipt", "root": 11, "status": "addressed"}, "mention"),
            ({"op": "receipt", "root": 11, "status": "addressed"}, "conflict"),
            ({"op": "receipt", "root": 11, "status": "done"}, "fixer"), ({"op": "receipt", "root": 11, "status": ["x"]}, "fixer"),
            ({"op": "receipt", "root": "11", "status": "addressed"}, "fixer"),
        ]
        for record, mode in bad:
            with self.subTest(record=record, mode=mode):
                with self.assertRaises(p.Refused):
                    self.load({"op": "comment", "body": "fine"}, record, mode=mode)
        with self.assertRaises(p.Refused):
            self.load({"op": "receipt", "root": 11, "status": "addressed"}, {"op": "receipt", "root": 11, "status": "needs-human"})
        with self.assertRaises(p.Refused):
            self.load(*[{"op": "comment", "body": "x"}] * (p.MAX_RECORDS + 1))
        self.assertEqual(len(self.load(*[{"op": "comment", "body": "x"}] * p.MAX_RECORDS)), p.MAX_RECORDS)
        with self.assertRaises(p.Refused):
            p.load_outbox(b"\xff\xfe", "fixer", set())


def pr(sha, labels=(), state="open", ref="fix", repo=REPO):
    return {"state": state, "labels": [{"name": n} for n in labels],
            "head": {"sha": sha, "ref": ref, "repo": {"full_name": repo}}}


A, B, C = "a" * 40, "b" * 40, "c" * 40
FP = "f" * 64


class Harness:
    """Fake GitHub/exchange around publish.main()."""

    def __init__(self, test, *, mode="fixer", records=(), bundle=False, roots=(11,)):
        self.test = test
        self.tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.artifact = self.dir / "artifact"
        self.artifact.mkdir()
        if records:
            (self.artifact / "outbox.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
        if bundle:
            (self.artifact / "candidate.bundle").write_bytes(b"bundle")
        (self.artifact / "result.json").write_text(json.dumps([{"type": "result", "subtype": "success", "num_turns": 3}]))
        self.state = self.dir / "state"
        self.state.mkdir()
        context = [{"root": r, "fingerprint": FP, "thread": "t", "base": A} for r in roots] if mode == "fixer" else []
        self.env = {"REPO": REPO, "PR_NUMBER": "7", "HEAD_REF": "fix", "HEAD_SHA": A, "WRITER_MODE": mode,
                    "BASE_SHA": C if mode == "conflict" else "", "BASE_REF": "main" if mode == "conflict" else "",
                    "RECEIPT_CONTEXT": json.dumps(context), "ARTIFACT_DIR": str(self.artifact),
                    "PUBLISH_STATE_DIR": str(self.state), "MODEL_OUTCOME": "success",
                    "PREPARED_RUN_ATTEMPT": "1", "GITHUB_RUN_ATTEMPT": "1", "GH_TOKEN": "gh-job-token"}
        self.heads = [A]
        self.labels = []
        self.writes, self.revoked, self.minted, self.pushed, self.audits, self.lines = [], [], 0, [], [], []
        self.targets = {5: {"id": 5, "in_reply_to_id": None,
                            "pull_request_url": f"https://api.github.com/repos/{REPO}/pulls/7"},
                        11: {"id": 11, "in_reply_to_id": None,
                             "pull_request_url": f"https://api.github.com/repos/{REPO}/pulls/7"}}
        self.mint_error = None
        self.candidate = B
        self.validate_error = None

    def read(self, path):
        if "/pulls/comments/" in path:
            target = self.targets.get(int(path.rsplit("/", 1)[1]))
            if target is None:
                raise subprocess.CalledProcessError(1, "gh", stderr="(HTTP 404)")
            return target
        if "/compare/" in path:
            return {"status": "ahead"}
        if "/comments?" in path:
            return []
        head = self.heads[0] if len(self.heads) == 1 else self.heads.pop(0)
        return pr(head, self.labels)

    def mint(self, env):
        self.minted += 1
        if self.mint_error:
            raise self.mint_error
        return TOKEN

    def http(self, method, url, token=None, body=None, timeout=20):
        self.writes.append((method, url, token, body))
        return 201, {}

    def revoke(self, token):
        self.revoked.append(token)
        return True

    def run(self):
        deps = {"read": self.read, "out": self.lines.append, "mint": self.mint, "http": self.http,
                "revoke": self.revoke, "repo": lambda tmp: object(), "sleep": lambda _s: None,
                "consumer": receipts, "post": lambda config, body: self.audits.append(body)}

        def validate(repo, config, bundle):
            if self.validate_error:
                raise self.validate_error
            return self.candidate

        def push(repo, config, candidate, token):
            self.pushed.append((candidate, token))
            self.heads = [self.candidate]

        with patch.object(p, "fetch_recorded", lambda *a: None), patch.object(p, "validate_candidate", validate), \
                patch.object(p, "push_candidate", push):
            return p.main(self.env, deps)


class PublishFlowTests(unittest.TestCase):
    def test_comment_reply_and_addressed_receipt_after_verified_push(self):
        h = Harness(self, bundle=True, records=[{"op": "reply", "comment_id": 11, "body": "fixed in sha"},
                                                {"op": "receipt", "root": 11, "status": "addressed"},
                                                {"op": "comment", "body": "summary"}])
        self.assertEqual(h.run(), 0)
        self.assertEqual(h.pushed, [(B, TOKEN)])
        self.assertEqual([w[1].rsplit("/repos/", 1)[1] for w in h.writes],
                         [f"{REPO}/pulls/7/comments/11/replies", f"{REPO}/pulls/7/comments/11/replies",
                          f"{REPO}/issues/7/comments"])
        self.assertTrue(all(w[2] == TOKEN and w[0] == "POST" for w in h.writes))
        receipt = h.writes[1][3]["body"]
        parsed = receipts.records(receipt, "result")
        self.assertEqual(parsed, [{"root": 11, "fingerprint": FP, "status": "addressed", "base": A, "commit": B}])
        # The existing consumer accepts it as claude[bot]'s reply on the root.
        item = {"root": 11, "fingerprint": FP, "thread": "t"}
        comments = [{"id": 900, "in_reply_to_id": 11, "body": receipt, "created_at": "2026-10-20T00:00:00Z",
                     "user": {"login": "claude[bot]", "type": "Bot", "id": receipts.CLAUDE_ID}}]
        compare = lambda base, tip: "identical" if base == tip else "ahead"
        self.assertEqual(receipts.pending_findings([item], comments, B, compare, lambda _l: "none"), [])
        self.assertEqual(h.revoked, [TOKEN])
        self.assertIn(f"pushed candidate `{B}`", h.audits[0])
        self.assertIn("3 queued write(s) posted, 0 refused", h.audits[0])
        self.assertNotIn(TOKEN, h.audits[0])

    def test_needs_human_without_push_and_addressed_without_push_is_refused(self):
        h = Harness(self, records=[{"op": "receipt", "root": 11, "status": "needs-human"}])
        h.run()
        body = h.writes[0][3]["body"]
        self.assertEqual(receipts.records(body, "result"),
                         [{"root": 11, "fingerprint": FP, "status": "needs-human", "base": A}])
        h = Harness(self, records=[{"op": "receipt", "root": 11, "status": "addressed"}, {"op": "comment", "body": "x"}])
        h.run()
        self.assertEqual(len(h.writes), 1)
        self.assertNotIn("receipt", json.dumps(h.writes))
        self.assertIn("1 queued write(s) posted, 1 refused", h.audits[0])

    def test_receipt_for_a_non_selected_root_rejects_the_outbox(self):
        h = Harness(self, bundle=True, records=[{"op": "receipt", "root": 99, "status": "addressed"},
                                                {"op": "comment", "body": "x"}])
        h.run()
        self.assertEqual(h.writes, [])
        self.assertIn("outbox rejected", h.audits[0])
        self.assertEqual(h.pushed, [(B, TOKEN)])

    def test_marker_injection_in_body_rejects_everything_queued(self):
        h = Harness(self, records=[{"op": "comment", "body": "ok"},
                                   {"op": "comment", "body": '<!-- claude-fixer-result:{"root":11} -->'}])
        h.run()
        self.assertEqual(h.writes, [])
        self.assertEqual(h.minted, 0)

    def test_pause_moved_head_and_unreadable_labels_refuse_before_minting(self):
        for labels, var, heads in ((["Claude:Pause"], "", [A]), (["HOLD"], "", [A]), ([], "true", [A]),
                                   ([], "", [C]), (None, "", [A])):
            with self.subTest(labels=labels, var=var, heads=heads):
                h = Harness(self, bundle=True, records=[{"op": "comment", "body": "x"}])
                h.env["CLAUDE_FIXER_PAUSE"] = var
                h.heads = heads
                if labels is None:
                    h.read = lambda path, h=h: {**pr(A), "labels": None} if path.endswith("/pulls/7") else Harness.read(h, path)
                else:
                    h.labels = labels
                h.run()
                self.assertEqual((h.minted, h.pushed, h.writes), (0, [], []))
                self.assertIn("nothing published", h.audits[0])

    def test_head_moving_after_mint_blocks_push_and_revokes(self):
        h = Harness(self, bundle=True, records=[{"op": "comment", "body": "x"}])
        h.heads = [A, C, C]  # first check passes, the pre-push recheck sees a moved head
        h.run()
        self.assertEqual(h.pushed, [])
        self.assertEqual(h.writes, [])
        self.assertEqual(h.revoked, [TOKEN])
        self.assertIn("queued writes dropped", h.audits[0])

    def test_pause_between_records_stops_the_rest(self):
        h = Harness(self, records=[{"op": "comment", "body": "1"}, {"op": "comment", "body": "2"},
                                   {"op": "comment", "body": "3"}])
        original = h.read
        calls = {"n": 0}
        def read(path):
            if path.endswith("/pulls/7"):
                calls["n"] += 1
                if calls["n"] >= 3:
                    return pr(A, ["hold"])
            return original(path)
        h.read = read
        h.run()
        self.assertEqual(len(h.writes), 1)
        self.assertIn("1 queued write(s) posted, 2 refused", h.audits[0])

    def test_reply_target_must_be_a_root_on_this_pr(self):
        h = Harness(self, records=[{"op": "reply", "comment_id": 6, "body": "x"},
                                   {"op": "reply", "comment_id": 5, "body": "y"}])
        h.targets[7] = {"id": 7, "in_reply_to_id": 5, "pull_request_url": f"https://api.github.com/repos/{REPO}/pulls/7"}
        h.targets[8] = {"id": 8, "in_reply_to_id": None, "pull_request_url": f"https://api.github.com/repos/{REPO}/pulls/8"}
        h.run()
        self.assertEqual([w[1].rsplit("/comments/", 1)[1] for w in h.writes], ["5/replies"])
        for target in (7, 8):
            h = Harness(self, records=[{"op": "reply", "comment_id": target, "body": "x"}])
            h.targets.update({7: {"id": 7, "in_reply_to_id": 5, "pull_request_url": f"https://api.github.com/repos/{REPO}/pulls/7"},
                              8: {"id": 8, "in_reply_to_id": None, "pull_request_url": f"https://api.github.com/repos/{REPO}/pulls/8"}})
            h.run()
            self.assertEqual(h.writes, [])

    def test_refused_candidate_drops_queued_writes(self):
        h = Harness(self, bundle=True, records=[{"op": "comment", "body": "pushed!"}])
        h.validate_error = p.Refused("candidate changes a protected path")
        h.run()
        self.assertEqual((h.pushed, h.writes, h.revoked), ([], [], [TOKEN]))
        self.assertIn("refused: candidate changes a protected path; queued writes dropped", h.audits[0])

    def test_label_only_in_conflict_job(self):
        h = Harness(self, mode="conflict", roots=(), records=[{"op": "label", "label": "needs-decision"}])
        h.run()
        self.assertEqual(h.writes[0][1], f"{p.GITHUB_API}/repos/{REPO}/issues/7/labels")
        self.assertEqual(h.writes[0][3], {"labels": ["needs-decision"]})

    def test_single_attempt_and_prepared_run_attempt(self):
        h = Harness(self, records=[{"op": "comment", "body": "x"}])
        (h.state / "attempt.json").write_text("{}")
        h.run()
        self.assertEqual((h.minted, h.writes), (0, []))
        self.assertIn("publish attempt already used", h.audits[0])
        h = Harness(self, records=[{"op": "comment", "body": "x"}])
        h.env["GITHUB_RUN_ATTEMPT"] = "2"
        h.run()
        self.assertEqual((h.minted, h.writes), (0, []))
        self.assertIn("run attempt differs", h.audits[0])

    def test_unexpected_error_still_revokes_and_audits(self):
        h = Harness(self, bundle=True, records=[{"op": "comment", "body": "x"}])
        h.validate_error = RuntimeError(TOKEN)  # Even a token-bearing message is never echoed.
        self.assertEqual(h.run(), 1)
        self.assertEqual(h.revoked, [TOKEN])
        self.assertIn("publisher error (RuntimeError)", h.audits[0])
        self.assertNotIn(TOKEN, "\n".join(h.audits + h.lines))

    def test_workflow_validation_skip_publishes_nothing_but_audits(self):
        h = Harness(self, bundle=True, records=[{"op": "comment", "body": "x"}])
        h.mint_error = p.WorkflowValidationSkip()
        self.assertEqual(h.run(), 0)
        self.assertEqual((h.pushed, h.writes), ([], []))
        self.assertTrue(any("workflow validation" in line for line in h.lines))
        self.assertIn("App token unavailable (workflow validation)", h.audits[0])

    def test_missing_artifact_audits_model_failure_without_minting(self):
        h = Harness(self)
        h.env["ARTIFACT_DIR"] = str(h.dir / "missing")
        h.env["MODEL_OUTCOME"] = "failure"
        self.assertEqual(h.run(), 0)
        self.assertEqual(h.minted, 0)
        self.assertIn("model-step-failed-or-timed-out", h.audits[0])
        self.assertIn("no candidate commits and no queued writes", h.audits[0])

    def test_audit_post_failure_keeps_summary_and_fails(self):
        h = Harness(self)
        summary = h.dir / "summary"
        h.env["GITHUB_STEP_SUMMARY"] = str(summary)
        def fail(config, body):
            raise subprocess.CalledProcessError(1, "gh")
        with patch.object(p, "fetch_recorded"):
            deps = {"read": h.read, "out": h.lines.append, "mint": h.mint, "http": h.http, "revoke": h.revoke,
                    "repo": None, "sleep": None, "consumer": receipts, "post": fail}
            self.assertEqual(p.main(h.env, deps), 1)
        self.assertIn("terminal audit", summary.read_text())


class FakeHttp:
    def __init__(self, exchange):
        self.calls = []
        self.exchange = list(exchange)

    def __call__(self, method, url, token=None, body=None, timeout=20):
        self.calls.append((method, url, token))
        if url.startswith("https://oidc.example"):
            return 200, {"value": OIDC}
        result = self.exchange.pop(0)
        if isinstance(result, Exception):
            raise result
        return 200, result


class TokenTests(unittest.TestCase):
    ENV = {"ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.example/token?api-version=2.0",
           "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token"}

    def test_exchange_matches_the_action_and_masks_both_tokens(self):
        lines = []
        http = FakeHttp([{"token": TOKEN}])
        self.assertEqual(p.mint_app_token(self.ENV, http, lines.append, lambda _s: None), TOKEN)
        self.assertEqual(http.calls[0], ("GET", "https://oidc.example/token?api-version=2.0&audience=claude-code-github-action",
                                         "request-token"))
        self.assertEqual(http.calls[1], ("POST", p.EXCHANGE_URL, OIDC))
        self.assertEqual(lines, [f"::add-mask::{OIDC}", f"::add-mask::{TOKEN}"])
        http = FakeHttp([{"app_token": TOKEN}])
        self.assertEqual(p.mint_app_token(self.ENV, http, lambda _l: None, lambda _s: None), TOKEN)

    def test_workflow_validation_errors_skip_and_other_errors_fail_closed(self):
        for error in (p.HttpError(401, {"message": "Workflow validation failed: not on default branch"}),
                      p.HttpError(401, {"error": {"message": "workflow validation failed"}}),
                      p.HttpError(403, {"error": {"details": {"error_code": "workflow_not_found_on_default_branch"}}})):
            with self.subTest(error=error.data), self.assertRaises(p.WorkflowValidationSkip):
                p.mint_app_token(self.ENV, FakeHttp([error]), lambda _l: None, lambda _s: None)
        with self.assertRaisesRegex(p.Refused, "HTTP 403"):
            p.mint_app_token(self.ENV, FakeHttp([p.HttpError(403, {"message": "forbidden"})]), lambda _l: None, lambda _s: None)
        http = FakeHttp([p.HttpError(502), p.HttpError(0), {"token": TOKEN}])
        self.assertEqual(p.mint_app_token(self.ENV, http, lambda _l: None, lambda _s: None), TOKEN)
        with self.assertRaisesRegex(p.Refused, "exchange failed"):
            p.mint_app_token(self.ENV, FakeHttp([p.HttpError(500)] * 3), lambda _l: None, lambda _s: None)
        for bad in ({}, {"token": ""}, {"token": "has space " * 3}, ["x"]):
            with self.subTest(bad=bad), self.assertRaises(p.Refused):
                p.mint_app_token(self.ENV, FakeHttp([bad]), lambda _l: None, lambda _s: None)
        with self.assertRaisesRegex(p.Refused, "id-token"):
            p.mint_app_token({}, FakeHttp([]), lambda _l: None, lambda _s: None)

    def test_token_is_only_ever_printed_inside_mask_commands(self):
        h = Harness(self, records=[{"op": "comment", "body": "x"}], bundle=True)
        h.mint = lambda env: p.mint_app_token(self.ENV, FakeHttp([{"token": TOKEN}]), h.lines.append, lambda _s: None)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            h.run()
        everything = "\n".join(h.lines + h.audits) + stdout.getvalue() + stderr.getvalue()
        for secret in (TOKEN, OIDC):
            self.assertEqual([line for line in everything.splitlines() if secret in line], [f"::add-mask::{secret}"])
        self.assertEqual(h.revoked, [TOKEN])

    def test_git_auth_header_stays_out_of_argv_and_disk(self):
        seen = []
        def runner(args, env=None, **kwargs):
            seen.append((args, env))
            return subprocess.CompletedProcess(args, 0, b"", b"")
        with tempfile.TemporaryDirectory() as tmp:
            repo = p.FreshRepo(tmp, runner=runner)
            config = {"repo": REPO, "ref": "fix", "base": A}
            p.push_candidate(repo, config, B, TOKEN)
        args, env = seen[-1]
        self.assertNotIn(TOKEN, " ".join(args))
        self.assertNotIn("--force", args)
        self.assertNotIn("-f", args)
        self.assertEqual(args[-2:], [f"https://github.com/{REPO}.git", f"{B}:refs/heads/fix"])
        self.assertIn("core.hooksPath=" + os.devnull, args)
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
        import base64
        self.assertEqual(env["GIT_CONFIG_VALUE_0"],
                         "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode())
        self.assertNotIn("GIT_CONFIG_VALUE_0", seen[0][1])  # git init carries no credential

    def test_revoke_uses_the_installation_token_endpoint(self):
        http = FakeHttp([{}])
        http.calls.clear()
        def call(method, url, token=None, body=None, timeout=20):
            http.calls.append((method, url, token))
            return 204, None
        self.assertTrue(p.revoke_app_token(TOKEN, call))
        self.assertEqual(http.calls, [("DELETE", "https://api.github.com/installation/token", TOKEN)])
        def fail(*a, **k):
            raise p.HttpError(500)
        self.assertFalse(p.revoke_app_token(TOKEN, fail))


class OutboxHelperTests(unittest.TestCase):
    CONFIG = {"repo": REPO, "number": "7", "base": A, "ref": "fix", "mode": "fixer", "roots": [11]}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, self.cwd)
        Path(".claude-fixer-replies").mkdir()
        Path(".claude-fixer-replies/r.txt").write_text("reply text")

    def outbox(self):
        return [json.loads(line) for line in Path(".claude-fixer-out/outbox.jsonl").read_text().splitlines()]

    def test_writes_are_queued_never_sent(self):
        run = unittest.mock.Mock(side_effect=AssertionError("no API write from the model job"))
        o.model_api(self.CONFIG, ["comment", ".claude-fixer-replies/r.txt"], run=run)
        o.model_api(self.CONFIG, ["reply", "5", ".claude-fixer-replies/r.txt"], run=run)
        o.model_api(self.CONFIG, ["receipt", "11", "addressed"], run=run)
        o.model_api(self.CONFIG | {"mode": "conflict"}, ["label"], run=run)
        self.assertEqual(self.outbox(), [{"op": "comment", "body": "reply text"},
                                         {"op": "reply", "comment_id": 5, "body": "reply text"},
                                         {"op": "receipt", "root": 11, "status": "addressed"},
                                         {"op": "label", "label": "needs-decision"}])
        run.assert_not_called()
        # The publisher accepts exactly what the helper queues.
        raw = Path(".claude-fixer-out/outbox.jsonl").read_bytes()
        with self.assertRaises(p.Refused):  # the label is conflict-only
            p.load_outbox(raw, "fixer", {11})
        self.assertEqual(len(p.load_outbox(raw.rsplit(b"\n", 2)[0] + b"\n", "fixer", {11})), 3)

    def test_reads_use_fixed_endpoints(self):
        calls = []
        run = lambda args: calls.append(args) or "{}"
        for op in ("read", "comments", "review-comments", "reviews", "threads"):
            o.model_api(self.CONFIG, [op], run=run)
        self.assertEqual(calls[0], ["gh", "api", "--method", "GET", f"repos/{REPO}/pulls/7"])
        self.assertTrue(all(c[:2] == ["gh", "api"] for c in calls))
        self.assertNotIn("POST", json.dumps(calls))

    def test_refusals(self):
        for config, args in ((self.CONFIG, ["label"]), (self.CONFIG | {"mode": "mention"}, ["receipt", "11", "addressed"]),
                             (self.CONFIG, ["receipt", "12", "addressed"]), (self.CONFIG, ["receipt", "11", "done"]),
                             (self.CONFIG, ["comment", "../r.txt"]), (self.CONFIG, ["comment", ".env"]),
                             (self.CONFIG, ["reply", "x", ".claude-fixer-replies/r.txt"]),
                             (self.CONFIG, ["post", "repos/x"]), (self.CONFIG, ["read", "--method", "POST"])):
            with self.subTest(args=args), self.assertRaises(ValueError):
                o.model_api(config, args, run=lambda a: "")
        for body in ("", "<!-- claude-fixer-result:{} -->", "x" * (o.MAX_BODY + 1)):
            Path(".claude-fixer-replies/b.txt").write_text(body)
            with self.subTest(body=body[:20]), self.assertRaises(ValueError):
                o.model_api(self.CONFIG, ["comment", ".claude-fixer-replies/b.txt"])
        os.mkfifo(".claude-fixer-replies/fifo.txt")
        with self.assertRaises(ValueError):
            o.model_api(self.CONFIG, ["comment", ".claude-fixer-replies/fifo.txt"])
        os.symlink("/etc/hostname", ".claude-fixer-replies/link.txt")
        with self.assertRaises(ValueError):
            o.model_api(self.CONFIG, ["comment", ".claude-fixer-replies/link.txt"])
        for _ in range(o.MAX_RECORDS):
            o.model_api(self.CONFIG, ["comment", ".claude-fixer-replies/r.txt"])
        with self.assertRaises(ValueError):
            o.model_api(self.CONFIG, ["comment", ".claude-fixer-replies/r.txt"])

    def test_outbox_symlink_is_refused(self):
        Path(".claude-fixer-out").mkdir()
        os.symlink("/tmp/elsewhere", ".claude-fixer-out/outbox.jsonl")
        with self.assertRaises(OSError):
            o.model_api(self.CONFIG, ["comment", ".claude-fixer-replies/r.txt"])


def jobs(name):
    return yaml.load(WORKFLOWS[name].read_text(encoding="utf-8"), Loader=yaml.BaseLoader)["jobs"]


PIPELINES = {
    "claude.yml": [("mention-prepare", "claude-mention", "mention-publish", "claude-${{ needs.resolve-pr.outputs.pr_number }}"),
                   ("fixer-prepare", "devin-review-fix", "fixer-publish", "claude-${{ needs.resolve-pr.outputs.pr_number }}")],
    "claude-conflict-writer.yml": [("prepare", "model", "publish", "claude-${{ inputs.pr_number }}")],
}


class WorkflowWiringTests(unittest.TestCase):
    def test_conflict_writer_devin_race_check_reads_author_and_committer(self):
        text = (ROOT / ".github/workflows/claude-conflict-writer.yml").read_text(encoding="utf-8")
        line = next(l for l in text.splitlines() if l.strip().startswith('head_author="$(gh api'))
        for field in ("(.author.login", "(.commit.author.name", "(.committer.login", "(.commit.committer.name"):
            self.assertIn(field, line)

    def pipelines(self):
        for name, triples in PIPELINES.items():
            all_jobs = jobs(name)
            for prepare, model, publish, group in triples:
                yield name, all_jobs[prepare], all_jobs[model], all_jobs[publish], group, (prepare, model, publish)

    def test_model_jobs_are_read_only_without_credentials_or_push(self):
        for name, _prep, model, _pub, _group, ids in self.pipelines():
            with self.subTest(workflow=name, job=ids[1]):
                self.assertEqual(model["permissions"], {"contents": "read", "pull-requests": "read", "issues": "read"})
                checkout = [s for s in model["steps"] if s.get("uses", "").startswith("actions/checkout@")]
                self.assertEqual(len(checkout), 1)
                self.assertEqual(checkout[0]["with"]["persist-credentials"], "false")
                self.assertEqual(checkout[0]["with"]["ref"], f"${{{{ needs.{ids[0]}.outputs.head_sha }}}}")
                action = [s for s in model["steps"] if s.get("uses", "").startswith("anthropics/claude-code-action@")]
                self.assertEqual(len(action), 1)
                inputs = action[0]["with"]
                self.assertEqual(inputs["github_token"], "${{ github.token }}")
                self.assertEqual(inputs["classify_inline_comments"], "false")  # CSL-383
                self.assertEqual((inputs["bot_id"], inputs["bot_name"]), ("209825114", "claude[bot]"))
                allow = inputs["claude_args"].split("--allowedTools", 1)[1].split("--disallowedTools", 1)[0]
                self.assertNotIn("git push", allow)
                self.assertNotIn("Bash(gh api", allow)
                self.assertIn("Bash(python3 ${{ runner.temp }}/claude-fixer-guard/model-api --:*)", allow)
                for rule in ("Bash(git push:*)", "Bash(git config:*)", "Bash(gh api:*)"):
                    self.assertIn(rule, inputs["claude_args"].split("--disallowedTools", 1)[1])
                deny = json.loads(inputs["settings"])["permissions"]["deny"]
                for rule in ("Edit(.git/**)", "Write(.git/**)", "Edit(/${{ runner.temp }}/claude-fixer-guard/**)",
                             "Write(/${{ runner.temp }}/claude-fixer-guard/**)"):
                    self.assertIn(rule, deny)
                self.assertFalse([r for r in deny if ".claude-fixer-out" in r])  # the outbox stays writable
                text = yaml.dump(model)
                self.assertNotIn("id-token", text)
                self.assertNotIn("secrets.GITHUB", text)
                upload = [s for s in model["steps"] if s.get("uses", "").startswith("actions/upload-artifact@")]
                self.assertEqual(len(upload), 1)
                self.assertEqual(upload[0]["if"], "always()")
                self.assertEqual(upload[0]["with"]["retention-days"], "1")
                package = [s for s in model["steps"] if s.get("name") == "Package untrusted model output"][0]
                self.assertEqual(package["if"], "always()")
                self.assertIn('bundle create "$out/candidate.bundle" HEAD "^$HEAD_SHA"', package["run"])
                self.assertNotIn('cp "$EXECUTION_FILE"', package["run"])  # never upload the transcript
                self.assertEqual(model["outputs"], {"claude_outcome": "${{ steps.claude.outcome }}"})

    def test_publish_jobs_mint_and_never_run_repository_code(self):
        for name, _prep, _model, publish, _group, ids in self.pipelines():
            with self.subTest(workflow=name, job=ids[2]):
                self.assertEqual(publish["permissions"], {"contents": "read", "pull-requests": "write",
                                                          "issues": "write", "id-token": "write"})
                self.assertEqual(set(publish["needs"]) - {"resolve-pr"}, {ids[0], ids[1]})
                condition = " ".join(publish["if"].split())
                self.assertTrue(condition.startswith("!cancelled() &&"), condition)
                self.assertIn(f"needs.{ids[0]}.outputs.skip == 'false'", condition)
                self.assertIn(f"needs.{ids[0]}.outputs.run_attempt == github.run_attempt", condition)
                self.assertNotIn(f"needs.{ids[1]}.result == 'success'", condition)
                uses = [s.get("uses", "") for s in publish["steps"]]
                self.assertEqual(uses, ["actions/download-artifact@v4", ""])
                self.assertEqual(publish["steps"][0]["continue-on-error"], "true")
                run = publish["steps"][1]["run"]
                # Outside the embedded, parity-checked sources: no repository code.
                outside = re.sub(r"<<'([A-Z_]+)'\n.*?\n\1(?:\n|$)", "<<heredoc\n", run, flags=re.DOTALL)
                self.assertEqual(outside.count("<<heredoc"), 2)
                rest = {**publish, "steps": [publish["steps"][0], {**publish["steps"][1], "run": outside}]}
                for forbidden in ("pip install", "pytest", "python tools/", "python3 tools/", "uses: ./",
                                  "actions/checkout", "scripts/ci/", "git clone", "-e .", "GITHUB_WORKSPACE"):
                    self.assertNotIn(forbidden, yaml.dump(rest))
                commands = [line.strip() for line in run.split("\n")
                            if line.strip().startswith(("python", "cat >", "mkdir"))]
                self.assertEqual(commands, ['mkdir -p "$RUNNER_TEMP/claude-fixer-publish"',
                                            "cat > \"$RUNNER_TEMP/claude-fixer-publish/receipts.py\" <<'CLAUDE_FIXER_RECEIPTS'",
                                            "cat > \"$RUNNER_TEMP/claude-fixer-publish/publish.py\" <<'CLAUDE_FIXER_PUBLISH'",
                                            'python3 -I "$RUNNER_TEMP/claude-fixer-publish/publish.py"'])
                env = publish["steps"][1]["env"]
                self.assertEqual(env["GH_TOKEN"], "${{ github.token }}")
                self.assertEqual(env["CLAUDE_FIXER_PAUSE"], "${{ vars.CLAUDE_FIXER_PAUSE }}")
                self.assertEqual(env["HEAD_SHA"], f"${{{{ needs.{ids[0]}.outputs.head_sha }}}}")
                self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", yaml.dump(publish))

    def test_prepare_jobs_are_model_free_and_hold_no_id_token(self):
        for name, prepare, _model, _pub, _group, ids in self.pipelines():
            with self.subTest(workflow=name, job=ids[0]):
                text = yaml.dump(prepare)
                for forbidden in ("id-token", "actions/checkout", "claude-code-action", "pip install", "pytest"):
                    self.assertNotIn(forbidden, text)
                self.assertNotIn("contents: write", text)
                self.assertIn("run_attempt", prepare["outputs"])
                self.assertIn("|| 'true'", prepare["outputs"]["skip"])

    def test_all_three_jobs_share_the_writer_lock(self):
        for name, prepare, model, publish, group, ids in self.pipelines():
            for job_id, job in zip(ids, (prepare, model, publish)):
                with self.subTest(workflow=name, job=job_id):
                    self.assertEqual(job["concurrency"], {"group": group, "queue": "max", "cancel-in-progress": "false"})
        caller = jobs("conflict-scan.yml")["conflict-resolve"]
        self.assertNotIn("concurrency", caller)  # a caller lock would deadlock its inner jobs
        self.assertEqual(caller["uses"], "./.github/workflows/claude-conflict-writer.yml")
        self.assertEqual(caller["strategy"]["max-parallel"], "1")
        self.assertEqual(caller["secrets"], {"CLAUDE_CODE_OAUTH_TOKEN": "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"})

    def test_model_jobs_wait_for_a_fresh_prepare(self):
        for name, _prep, model, _pub, _group, ids in self.pipelines():
            condition = " ".join(model["if"].split())
            self.assertIn(f"needs.{ids[0]}.result == 'success'", condition)
            self.assertIn(f"needs.{ids[0]}.outputs.skip == 'false'", condition)
            self.assertIn(f"needs.{ids[0]}.outputs.run_attempt == github.run_attempt", condition)

    def test_every_embedded_script_matches_its_source(self):
        sources = {"CLAUDE_FIXER_PUBLISH": "claude_fixer_publish.py", "CLAUDE_FIXER_OUTBOX": "claude_fixer_outbox.py",
                   "CLAUDE_FIXER_RECEIPTS": "claude_fixer_receipts.py", "CLAUDE_FIXER_PREFLIGHT": "claude_fixer_preflight.py",
                   "CLAUDE_DENIALS_REPORT": "claude_denials.py", "CLAUDE_PR_FIXER_GATE": "claude_pr_fixer_gate.py",
                   "CLAUDE_CONFLICT_SCAN": "claude_conflict_scan.py"}
        expected = {("claude.yml", "CLAUDE_FIXER_PUBLISH"): 2, ("claude.yml", "CLAUDE_FIXER_OUTBOX"): 2,
                    ("claude.yml", "CLAUDE_FIXER_RECEIPTS"): 3, ("claude.yml", "CLAUDE_FIXER_PREFLIGHT"): 2,
                    ("claude.yml", "CLAUDE_DENIALS_REPORT"): 2, ("claude.yml", "CLAUDE_PR_FIXER_GATE"): 1,
                    ("claude-conflict-writer.yml", "CLAUDE_FIXER_PUBLISH"): 1,
                    ("claude-conflict-writer.yml", "CLAUDE_FIXER_OUTBOX"): 1,
                    ("claude-conflict-writer.yml", "CLAUDE_FIXER_RECEIPTS"): 1,
                    ("claude-conflict-writer.yml", "CLAUDE_DENIALS_REPORT"): 1,
                    ("conflict-scan.yml", "CLAUDE_CONFLICT_SCAN"): 1}
        for name, path in WORKFLOWS.items():
            text = path.read_text(encoding="utf-8")
            for tag, source in sources.items():
                chunks = text.split(f"<<'{tag}'\n")[1:]
                with self.subTest(workflow=name, tag=tag):
                    self.assertEqual(len(chunks), expected.get((name, tag), 0))
                    want = (ROOT / "scripts/ci" / source).read_text(encoding="utf-8").rstrip()
                    for chunk in chunks:
                        self.assertEqual(textwrap.dedent(chunk.split(f"          {tag}\n", 1)[0]).rstrip(), want)
            self.assertNotIn("CLAUDE_FIXER_GUARD", text)
            self.assertNotIn("claude-fixer-guard/pre-push", text)
            self.assertNotIn("core.hooksPath\", str(directory)", text)

    def test_conflict_merge_is_bundled_against_head_and_base(self):
        model = jobs("claude-conflict-writer.yml")["model"]
        package = [s for s in model["steps"] if s.get("name") == "Package untrusted model output"][0]
        self.assertIn('HEAD "^$HEAD_SHA" "^$BASE_SHA"', package["run"])
        merge = [s for s in model["steps"] if s.get("name", "").startswith("Start merge")][0]
        self.assertIn('git merge --no-ff --no-commit "$BASE_SHA"', merge["run"])
        publish = jobs("claude-conflict-writer.yml")["publish"]["steps"][1]["env"]
        self.assertEqual(publish["BASE_SHA"], "${{ needs.prepare.outputs.base_sha }}")
        self.assertEqual(publish["WRITER_MODE"], "conflict")
        self.assertEqual(jobs("claude.yml")["fixer-publish"]["steps"][1]["env"]["RECEIPT_CONTEXT"],
                         "${{ needs.fixer-prepare.outputs.receipt_context }}")
        self.assertNotIn("RECEIPT_CONTEXT", jobs("claude.yml")["mention-publish"]["steps"][1]["env"])

    def test_committer_identity_is_pinned_where_the_publisher_expects_it(self):
        for name, _prep, model, _pub, _group, ids in self.pipelines():
            attach = [s for s in model["steps"] if s.get("name") == "Attach verified PR branch at event SHA"][0]["run"]
            self.assertIn(f'git config user.name "{CLAUDE[0]}"', attach)
            self.assertIn(f'git config user.email "{CLAUDE[1]}"', attach)
            self.assertIn(".claude-fixer-out/", attach)


if __name__ == "__main__":
    unittest.main(verbosity=1)

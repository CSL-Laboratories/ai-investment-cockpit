#!/usr/bin/env python3
"""Terminal handback and the model-side API helper; no live/provider qualification.

CSL-377 removed the in-job pre-push guard: the model job holds no push
credential and the trusted publish job (scripts/ci/claude_fixer_publish.py)
validates and pushes. The terminal audit moved into that publisher, and the
fixed same-PR API facade became the queue-only outbox helper
(scripts/ci/claude_fixer_outbox.py). Publish validation tests live in
tools/test_claude_fixer_publish.py.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/ci/claude_fixer_publish.py"
OUTBOX = ROOT / "scripts/ci/claude_fixer_outbox.py"
spec = importlib.util.spec_from_file_location("publish", SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
spec = importlib.util.spec_from_file_location("outbox", OUTBOX)
ob = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ob)
A, B, C = "a" * 40, "b" * 40, "c" * 40
CONFIG = {"repo": "acme/example", "number": "7", "base": A, "ref": "fix"}
HELPER = CONFIG | {"mode": "mention", "roots": []}


def pr(sha=A, labels=()):
    return {"state": "open", "labels": [{"name": name} for name in labels],
            "head": {"sha": sha, "ref": "fix", "repo": {"full_name": "acme/example"}}}


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.state = self.path / "attempt.json"
        self.execution = self.path / "execution.json"
        self.execution.write_text(json.dumps([{"type": "result", "subtype": "success", "num_turns": 12}]))

    def record_candidate(self, candidate=B):
        # The publisher records the validated candidate before its single push.
        self.state.write_text(json.dumps({"candidate": candidate}))

    def queued(self):
        file = self.path / ".claude-fixer-out/outbox.jsonl"
        return [json.loads(line) for line in file.read_text().splitlines()] if file.exists() else []

    def test_pause_never_blocks_audit_reads(self):
        self.assertEqual(m.current_pr(CONFIG, lambda _: pr(labels=["hold"])), A)
        self.assertEqual(m.current_pr(CONFIG, lambda _: pr() | {"labels": None}), A)

    def test_wrong_target_closed_fork_and_missing_identity_are_rejected(self):
        for bad in [pr() | {"state": "closed"}, {"state": "open", "head": {}},
                    pr() | {"head": pr()["head"] | {"ref": "other"}},
                    pr() | {"head": pr()["head"] | {"repo": {"full_name": "fork/example"}}}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                m.current_pr(CONFIG, lambda _, bad=bad: bad)
        for ref in ("main", "master", "HEAD", "../bad", "fix\nother"):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                m.validate(CONFIG | {"ref": ref})
            with self.subTest(helper=ref), self.assertRaises(ValueError):
                ob.validate(HELPER | {"ref": ref})

    def test_pr_api_facade_rejects_ref_mutations_methods_and_extra_flags(self):
        run = Mock()
        for args in (["PATCH", "repos/acme/example/git/refs/heads/fix"],
                     ["DELETE", "repos/acme/example/git/refs/heads/fix"],
                     ["POST", "repos/acme/example/git/refs"], ["graphql"], ["threads", "mutation"],
                     ["read", "--method", "PATCH"], ["comment", "body", "--method", "DELETE"],
                     ["reply", "../git/refs", "body"], ["reply", "1?force=true", "body"],
                     ["reply", "1", "body", "extra"], ["comment", ".env"],
                     ["comment", "../secret"], ["comment", "/tmp/secret"], [],
                     ["receipt", "1", "addressed"], ["push"]):
            with self.subTest(args=args), patch.object(ob.Path, "cwd", return_value=self.path), \
                    self.assertRaises(ValueError):
                ob.model_api(HELPER, args, run=run)
        run.assert_not_called()
        self.assertEqual(self.queued(), [])

    def test_pr_api_facade_constructs_only_current_pr_reads_and_queues_writes(self):
        run = Mock(return_value="{}")
        for operation, endpoint in (("read", "pulls/7"), ("comments", "issues/7/comments"),
                                    ("review-comments", "pulls/7/comments"), ("reviews", "pulls/7/reviews")):
            ob.model_api(HELPER, [operation], run=run)
            self.assertEqual(run.call_args.args[0][:5], ["gh", "api", "--method", "GET", "repos/acme/example/" + endpoint])
        ob.model_api(HELPER, ["threads"], run=run)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["gh", "api", "graphql", "--paginate"])
        self.assertIn("isResolved isOutdated", argv[5])
        self.assertNotIn("mutation", argv[5])
        self.assertEqual(argv[-6:], ["-f", "owner=acme", "-f", "name=example", "-F", "number=7"])
        calls = run.call_count
        (self.path / ".claude-fixer-replies").mkdir()
        body = self.path / ".claude-fixer-replies/body.txt"
        body.write_text('Literal --method PATCH and $(secret) are comment data.', encoding="utf-8")
        for args in (["comment", ".claude-fixer-replies/body.txt"], ["reply", "123", ".claude-fixer-replies/body.txt"]):
            with patch.object(ob.Path, "cwd", return_value=self.path):
                ob.model_api(HELPER, args, run=run)
        # CSL-377: no write leaves the model job; both are queued for publish.
        self.assertEqual(run.call_count, calls)
        self.assertEqual(self.queued(), [{"op": "comment", "body": body.read_text()},
                                         {"op": "reply", "comment_id": 123, "body": body.read_text()}])

    def test_handoff_label_is_opt_in_fixed_and_same_pr_only(self):
        run = Mock(return_value="{}")
        conflict = HELPER | {"mode": "conflict"}
        for config, args in ((HELPER, ["label"]), (HELPER | {"mode": "fixer"}, ["label"]),
                             (conflict, ["label", "ci:full"]), (conflict, ["label", "needs-decision"]),
                             (conflict, ["labels"])):
            with self.subTest(mode=config["mode"], args=args), patch.object(ob.Path, "cwd", return_value=self.path), \
                    self.assertRaises(ValueError):
                ob.model_api(config, args, run=run)
        with patch.object(ob.Path, "cwd", return_value=self.path):
            ob.model_api(conflict, ["label"], run=run)
        run.assert_not_called()
        self.assertEqual(self.queued(), [{"op": "label", "label": "needs-decision"}])

    def test_install_accepts_only_known_modes_and_fixer_receipt_context(self):
        env = {"REPO": CONFIG["repo"], "PR_NUMBER": "7", "HEAD_SHA": A, "HEAD_REF": "fix"}
        context = json.dumps([{"root": 11, "fingerprint": "f" * 64, "base": A}])
        for mode, receipts, ok in (("mention", "", True), ("conflict", "", True), ("fixer", context, True),
                                   ("mention", context, False), ("label", "", False), ("", "", False)):
            with self.subTest(mode=mode, receipts=bool(receipts)), tempfile.TemporaryDirectory() as tmp:
                script = Path(tmp) / "model-api"
                script.write_text("fixture")
                with patch.object(ob, "__file__", str(script)), patch.object(sys, "argv", ["model-api", "install"]), \
                        patch.dict(os.environ, env | {"WRITER_MODE": mode, "RECEIPT_CONTEXT": receipts}):
                    if ok:
                        self.assertEqual(ob.main(), 0)
                        saved = json.loads((Path(tmp) / "context.json").read_text())
                        self.assertEqual((saved["mode"], saved["roots"]), (mode, [11] if receipts else []))
                    else:
                        with self.assertRaises(ValueError):
                            ob.main()
                        self.assertFalse((Path(tmp) / "context.json").exists())

    def test_reply_refuses_foreign_missing_malformed_or_non_root_targets(self):
        # The publisher binds each reply (and receipt) to a root on this PR.
        valid = {"id": 123, "pull_request_url": "https://api.github.com/repos/acme/example/pulls/7"}
        self.assertTrue(m.reply_target_ok(CONFIG, 123, lambda _p: valid))
        for target in ([], {}, valid | {"id": 124}, valid | {"id": True},
                       valid | {"in_reply_to_id": 1}, valid | {"pull_request_url": valid["pull_request_url"] + "0"},
                       valid | {"pull_request_url": "https://api.github.com/repos/other/repo/pulls/7"}):
            with self.subTest(target=target):
                paths = []
                self.assertFalse(m.reply_target_ok(CONFIG, 123, lambda path, target=target, paths=paths: paths.append(path) or target))
                self.assertEqual(paths, ["repos/acme/example/pulls/comments/123"])

    def test_malformed_api_entry_does_not_queue_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "context.json").write_text(json.dumps(HELPER))
            for args in ([], ["--"], ["read"], ["install", "extra"]):
                with self.subTest(args=args), patch.object(ob, "__file__", str(Path(tmp) / "model-api")), \
                        patch.object(sys, "argv", ["model-api", *args]), \
                        patch.object(ob.Path, "cwd", return_value=self.path), patch("builtins.print"):
                    self.assertEqual(ob.main(), 1)
                    self.assertEqual(self.queued(), [])

    @unittest.skipIf(os.name == "nt", "POSIX FIFO/symlink fixtures")
    def test_reply_file_fifo_and_symlink_are_rejected_before_read(self):
        directory = self.path / ".claude-fixer-replies"
        directory.mkdir()
        fifo = directory / "fifo.txt"
        os.mkfifo(fifo)
        secret = self.path / ".env"
        secret.write_text("fixture-only")
        (directory / "link.txt").symlink_to(secret)
        for name in ("fifo", "link"):
            with patch.object(ob.Path, "cwd", return_value=self.path), \
                    patch.object(ob.Path, "read_text", side_effect=AssertionError("must reject before read")), \
                    self.assertRaises(ValueError):
                ob.model_api(HELPER, ["comment", f".claude-fixer-replies/{name}.txt"], run=Mock())
        self.assertEqual(self.queued(), [])

    def test_no_false_handled_receipt_for_unpushed_or_unknown_candidate(self):
        self.record_candidate()
        for compare in ("behind", "diverged", None):
            body = m.terminal(CONFIG, self.state, str(self.execution), "success",
                              read=lambda path, compare=compare: pr(C) if "/pulls/" in path else {"status": compare})
            self.assertIn("`unverified`", body)
            self.assertNotIn("claude-fixer-result:", body)
            self.assertNotIn('"status":"addressed"', body)

    def test_reachable_candidate_audit_does_not_claim_findings_handled(self):
        self.record_candidate()
        body = m.terminal(CONFIG, self.state, str(self.execution), "success",
                          read=lambda path: pr(B) if "/pulls/" in path else {"status": "ahead"})
        self.assertIn(f"`{B}`", body)
        self.assertIn("not an addressed", body)
        self.assertNotIn("claude-fixer-result:", body)

    def test_denied_command_turn_exhaustion_and_missing_output_have_terminal_audit(self):
        for record, expected in [({"permission_denials": [{"tool_input": "PRIVATE"}]}, "command-denied"),
                                 ({"num_turns": 38}, "turn-limit"),
                                 ({"subtype": "error_max_turns"}, "turn-limit"),
                                 ({"is_error": True}, "model-error")]:
            data = {"type": "result", "subtype": "success", "num_turns": 12} | record
            self.execution.write_text(json.dumps([data]))
            for outcome in ("failure", "cancelled", "success"):
                self.assertEqual(m.execution_reason(str(self.execution), outcome), expected)
            body = m.terminal(CONFIG, self.state, str(self.execution), "failure")
            self.assertIn(expected, body)
            self.assertNotIn("PRIVATE", body)
            self.assertIn("three-attempt", body)
        self.assertEqual(m.execution_reason("", "failure"), "model-step-failed-or-timed-out")
        self.assertEqual(m.execution_reason("", "success"), "execution-result-unavailable")

    @unittest.skipIf(os.name == "nt", "POSIX FIFO fixture")
    def test_untrusted_result_fifo_or_symlink_is_unavailable(self):
        fifo = self.path / "result.fifo"
        os.mkfifo(fifo)
        link = self.path / "result.link"
        link.symlink_to(self.execution)
        for path in (fifo, link):
            self.assertEqual(m.execution_reason(str(path), "success"), "execution-result-unavailable")

    def test_turn_exhaustion_has_priority_over_recovered_denial(self):
        self.execution.write_text(json.dumps({"type": "result", "num_turns": 38,
                                             "permission_denials": ["private"]}))
        self.assertEqual(m.execution_reason(str(self.execution), "failure"), "turn-limit")

    def test_comment_denial_keeps_summary_and_returns_failure(self):
        summary = self.path / "summary"
        env = {"REPO": CONFIG["repo"], "PR_NUMBER": "7", "HEAD_REF": "fix", "HEAD_SHA": A, "WRITER_MODE": "mention",
               "PUBLISH_STATE_DIR": str(self.path), "ARTIFACT_DIR": str(self.path / "missing"),
               "GITHUB_STEP_SUMMARY": str(summary), "MODEL_OUTCOME": "failure"}
        def post(_config, _body):
            raise subprocess.CalledProcessError(1, "gh")
        deps = {"read": lambda _p: pr(), "out": lambda _l: None, "post": post, "consumer": None}
        self.assertEqual(m.main(env, deps), 1)
        self.assertIn("model-step-failed", summary.read_text())


class WorkflowTests(unittest.TestCase):
    def test_workflow_embeds_trusted_publisher_and_terminal_after_model_failure(self):
        text = (ROOT / ".github/workflows/claude.yml").read_text(encoding="utf-8")
        chunks = text.split("<<'CLAUDE_FIXER_PUBLISH'\n")[1:]
        self.assertEqual(len(chunks), 2)
        for chunk in chunks:
            embedded = textwrap.dedent(chunk.split("          CLAUDE_FIXER_PUBLISH", 1)[0]).rstrip()
            self.assertEqual(embedded, SOURCE.read_text(encoding="utf-8").rstrip())
        self.assertNotIn("CLAUDE_FIXER_GUARD", text)
        # The publish job runs after a failed model job; only workflow cancellation stops it.
        self.assertEqual(text.count("\n      !cancelled() &&\n"), 2)
        self.assertEqual(text.count("timeout-minutes: 20"), 2)
        self.assertEqual(text.count("--max-turns 30"), 2)
        for body in text.split("      - id: claude\n")[1:]:
            allow = body.split("--allowedTools", 1)[1].split("--disallowedTools", 1)[0]
            self.assertNotIn("Bash(gh api", allow)
            self.assertIn("Bash(python3 ${{ runner.temp }}/claude-fixer-guard/model-api --:*)", allow)
            self.assertIn("Bash(gh api:*)", body.split("--disallowedTools", 1)[1])
            for command in ("fetch", "pull", "rebase", "merge", "config", "push", "remote"):
                self.assertNotIn(f"Bash(git {command}", allow)
            for bypass in ("Bash(git -", "--no-verify", "--force", "--dry-run"):
                self.assertNotIn(bypass, "\n".join(re.findall(r"Bash\(git [^)]*\)", allow)))
            self.assertIn("you have no push access and must not try", body)
            self.assertIn("only after it has pushed your commits", body)
            self.assertIn("has no Linear, Context7 or Parallel tools", body)


class TerminalReceiptIntegrationTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("receipt_fixtures", ROOT / "tools/test_claude_fixer_receipts.py")
        self.fixtures = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.fixtures)
        self.consumer = self.fixtures.m
        self.roots = self.fixtures.root_comments([self.fixtures.thread()])
        items = self.fixtures.findings([self.fixtures.thread()])
        if hasattr(self.fixtures, "prepare"):
            items = self.fixtures.prepare(items)
        self.item = {**items[0], "base": A}
        self.config = CONFIG | {"receipts": [self.item]}
        self.receipt = {**self.item, "status": "addressed", "commit": B}
        self.comments = self.roots + [self.reply(self.receipt)]

    def reply(self, receipt):
        return self.fixtures.reply(receipt) | {"id": 900}

    def read(self, path):
        if "/compare/" in path:
            return {"status": "identical" if path.endswith(f"{B}...{B}") else "ahead"}
        if "/comments?" in path:
            return self.comments
        if "/collaborators/" in path:
            return {"permission": "write"}
        return pr(B)

    def audit(self, read=None):
        return m.receipt_audit(self.config, read or self.read, self.consumer)

    def test_full_sha_producer_shape_reconciles_but_short_output_does_not(self):
        self.assertIn("1=verified-addressed", self.audit())
        self.comments = self.roots + [self.reply(self.receipt | {"commit": "d5194ed"})]
        self.assertIn("1=invalid-or-superseded", self.audit())
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "attempt.json"
            state.write_text(json.dumps({"candidate": B}))
            body = m.terminal(self.config, state, "", "failure", self.read, self.consumer)
        self.assertIn(f"live PR: `{B}`", body)
        self.assertIn("1=invalid-or-superseded", body)
        self.assertNotIn("claude-fixer-result:", body)

    def test_missing_invalid_forged_and_unpushed_receipts_are_not_verified(self):
        self.comments = self.roots
        self.assertIn("1=missing", self.audit())
        for change in ({"commit": A}, {"commit": "z" * 40}, {"base": C}, {"fingerprint": "0" * 64}):
            with self.subTest(change=change):
                self.comments = self.roots + [self.reply(self.receipt | change)]
                # A no-op compare must never be reported as ahead.
                def read(path):
                    if "/compare/" in path and path.endswith(f"{A}...{A}"):
                        return {"status": "identical"}
                    return self.read(path)
                self.assertNotIn("=verified-", self.audit(read))
        for user in (self.fixtures.actor("github-actions[bot]", self.consumer.ACTIONS_ID),
                     self.fixtures.actor("claude[bot]", 1)):
            self.comments = self.roots + [self.reply(self.receipt) | {"user": user}]
            self.assertIn("1=invalid-or-superseded", self.audit())
        self.comments = self.roots + [self.reply(self.receipt)]
        self.assertIn("1=invalid-or-superseded", self.audit(
            lambda p: {"status": "diverged"} if "/compare/" in p else self.read(p)))

    def test_handoff_is_reported_separately_and_root_edits_are_not_verified(self):
        self.comments = self.roots + [self.reply(self.receipt | {"status": "needs-human"})]
        self.assertIn("1=verified-needs-human", self.audit())
        self.comments[0] = self.comments[0] | {"body": "changed"}
        self.assertIn("1=root-changed-or-unverified", self.audit())

    def test_meaningful_followup_invalidates_captured_receipt(self):
        self.comments.append({"id": 901, "in_reply_to_id": 1,
                              "user": {"id": 12, "login": "writer", "type": "User"},
                              "created_at": "2026-10-16T02:00:00Z", "body": "This still fails."})
        self.assertNotIn("=verified-", self.audit())

    def test_read_denial_page_cap_duplicate_snapshot_and_head_race_stay_unverified(self):
        def denied(_):
            raise subprocess.CalledProcessError(1, "gh")
        self.assertIn("audit: unverified", self.audit(denied))
        reads = []
        def pages(path):
            reads.append(path)
            return [{}] * 100 if "/comments?" in path else self.read(path)
        self.assertIn("audit: unverified", self.audit(pages))
        self.assertEqual(sum("/comments?" in p for p in reads), 5)
        self.comments += self.comments
        self.assertIn("audit: unverified", self.audit())
        self.comments = self.roots + [self.reply(self.receipt)]
        heads = iter([B, C])
        def race(path):
            return pr(next(heads)) if path.endswith("/pulls/7") else self.read(path)
        self.assertIn("audit: unverified", self.audit(race))

    def test_invalid_compare_objects_differ_from_access_failure(self):
        for code, expected in ((404, "1=invalid-or-superseded"), (422, "1=invalid-or-superseded"),
                               (403, "audit: unverified"), (500, "audit: unverified")):
            def read(path, code=code):
                if "/compare/" in path:
                    raise subprocess.CalledProcessError(1, "gh", stderr=f"(HTTP {code})")
                return self.read(path)
            self.assertIn(expected, self.audit(read))

    def test_v3_generation_and_observation_remain_required(self):
        if "v" not in self.item:
            return
        for key in ("v", "generation", "observation"):
            self.comments = self.roots + [self.reply(self.receipt | {key: "wrong"})]
            self.assertIn("1=invalid-or-superseded", self.audit())

    def test_mixed_roots_and_complete_pagination_report_each_root(self):
        threads = [self.fixtures.thread(root=2)]
        items = self.fixtures.findings(threads)
        if hasattr(self.fixtures, "prepare"):
            items = self.fixtures.prepare(items)
        self.config["receipts"].append({**items[0], "base": A})
        self.comments += self.fixtures.root_comments(threads)
        def paged(path):
            if "/comments?" in path and path.endswith("page=1"):
                return [{"id": 1000 + i, "body": "unrelated"} for i in range(100)]
            return self.read(path)
        body = self.audit(paged)
        self.assertIn("1=verified-addressed", body)
        self.assertIn("2=missing", body)

    def test_terminal_read_count_and_wall_budget_are_enforced(self):
        with patch.object(m.time, "monotonic", return_value=0), patch.object(m, "api", return_value={}):
            read = m.bounded_reader()
            for _ in range(40):
                read("fixture")
            with self.assertRaisesRegex(m.AuditReadError, "budget"):
                read("fixture")
        with patch.object(m.time, "monotonic", side_effect=[0, 71]), patch.object(m, "api") as api:
            read = m.bounded_reader()
            with self.assertRaisesRegex(m.AuditReadError, "budget"):
                read("fixture")
            api.assert_not_called()

    def test_reader_decode_and_budget_errors_are_global_unverified(self):
        def exhausted(_):
            raise m.AuditReadError("budget")
        self.assertIn("audit: unverified", self.audit(exhausted))
        with patch.object(m, "api", side_effect=ValueError("bad JSON")), self.assertRaises(m.AuditReadError):
            m.bounded_reader()("fixture")

    def test_install_captures_context_and_workflow_uses_trusted_consumer(self):
        text = (ROOT / ".github/workflows/claude.yml").read_text(encoding="utf-8")
        self.assertIn('cat > "$RUNNER_TEMP/claude-fixer-guard/receipts.py"', text)
        self.assertIn('python3 "$RUNNER_TEMP/claude-fixer-guard/receipts.py"', text)
        self.assertIn('cat > "$RUNNER_TEMP/claude-fixer-publish/receipts.py"', text)
        # The model helper (allowed roots) and the publisher (receipt fields) both bind prepare's context.
        # E3 copies the fixer 0b env into each prefix step, then the outbox verify
        # step and the publisher: 5 0b + outbox + publish.
        self.assertEqual(text.count("RECEIPT_CONTEXT: ${{ needs.fixer-prepare.outputs.receipt_context }}"), 7)
        env = {"REPO": CONFIG["repo"], "PR_NUMBER": "7", "HEAD_SHA": A, "HEAD_REF": "fix", "WRITER_MODE": "fixer",
               "RECEIPT_CONTEXT": json.dumps([self.item])}
        self.assertEqual(m.load_config(env)["receipts"], [self.item])
        for bad in ([self.item | {"base": B}], [self.item, self.item], [self.item | {"root": True}], {"x": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                m.load_config(env | {"RECEIPT_CONTEXT": json.dumps(bad)})
        with self.assertRaises(ValueError):
            m.load_config(env | {"WRITER_MODE": "mention"})

    def test_terminal_entry_point_posts_reconciliation_without_handled_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "receipts.py").write_text((ROOT / "scripts/ci/claude_fixer_receipts.py").read_text())
            (path / "attempt.json").write_text(json.dumps({"candidate": B}))
            env = {"REPO": CONFIG["repo"], "PR_NUMBER": "7", "HEAD_REF": "fix", "HEAD_SHA": A, "WRITER_MODE": "fixer",
                   "RECEIPT_CONTEXT": json.dumps([self.item]), "PUBLISH_STATE_DIR": str(path / "fresh"),
                   "ARTIFACT_DIR": str(path / "missing"), "GITHUB_STEP_SUMMARY": str(path / "summary"),
                   "MODEL_OUTCOME": "failure"}
            (path / "fresh").mkdir()
            (path / "fresh/attempt.json").write_text(json.dumps({"candidate": B}))
            posted = []
            with patch.object(m, "__file__", str(path / "publish.py")), \
                    patch.object(m, "api", side_effect=lambda p, timeout=30: self.read(p)), \
                    patch.object(m, "command", side_effect=lambda args, **kw: posted.append(kw["input"]) or ""), \
                    patch("builtins.print"):
                self.assertEqual(m.main(env), 0)
            body = json.loads(posted[0])["body"]
            self.assertIn("1=verified-addressed", body)
            self.assertNotIn("claude-fixer-result:", body)
            self.assertIn("publish attempt already used", body)
            self.assertEqual((path / "summary").read_text().strip(), body.strip())


if __name__ == "__main__":
    unittest.main()

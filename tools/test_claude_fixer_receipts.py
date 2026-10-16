#!/usr/bin/env python3
"""Provider-free controls for explicit fixer receipts and bounded attempts."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("receipts", ROOT / "scripts/ci/claude_fixer_receipts.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
A, B, C = "a" * 40, "b" * 40, "c" * 40


def thread(body='<!-- devin-review-comment {"kind":"bug"} -->\nBug', root=1):
    return {"id": "thread1", "isResolved": False, "isOutdated": False,
            "comments": {"nodes": [{"databaseId": root, "body": body,
                "author": {"login": "devin-ai-integration", "__typename": "Bot"}}]}}


def root_comments(threads):
    return [{"id": t["comments"]["nodes"][0]["databaseId"],
             "body": t["comments"]["nodes"][0]["body"],
             "user": actor("devin-ai-integration[bot]", m.DEVIN_ID)} for t in threads]


def findings(threads):
    return m.findings(threads, root_comments(threads))


def actor(login="claude[bot]", identity=m.CLAUDE_ID):
    return {"login": login, "type": "Bot", "id": identity}


def reply(data, user=None):
    return {"in_reply_to_id": 1, "created_at": "2026-10-16T01:00:00Z",
            "user": user or actor(), "body": m.marker("result", data)}


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.items = findings([thread()])
        self.receipt = {"root": 1, "fingerprint": self.items[0]["fingerprint"],
                        "status": "addressed", "base": A, "commit": B}

    def pending(self, comments, compare=lambda base, tip: "ahead", manual=False):
        return m.pending_findings(self.items, comments, C, compare, lambda user: "write", manual)

    def test_same_second_correction_uses_monotonic_comment_id(self):
        receipt = reply(self.receipt | {"status": "needs-human"}) | {"id": 100}
        follow = {"id": 101, "in_reply_to_id": 1, "created_at": receipt["created_at"],
                  "user": actor("devin-ai-integration[bot]", m.DEVIN_ID), "body": "Still fails"}
        self.assertTrue(self.pending([receipt, follow]))
        self.assertEqual(self.pending([receipt, follow | {"id": 99}]), [])
        self.assertEqual(self.pending([receipt, follow | {"id": 100}]), [])

    def test_inline_indented_receipts_and_quoted_writer_correction(self):
        for body in ("Fixed. " + m.marker("result", self.receipt), "  " + m.marker("result", self.receipt)):
            self.assertEqual(self.pending([reply(self.receipt) | {"body": body}]), [])
        terminal = reply(self.receipt | {"status": "needs-human"})
        correction = {"in_reply_to_id": 1, "created_at": "2026-10-16T02:00:00Z",
                      "user": {"login": "writer", "type": "User", "id": 12},
                      "body": "> " + terminal["body"] + "\nThis still fails with empty input."}
        self.assertTrue(self.pending([terminal, correction]))
        quote_only = correction | {"body": "> " + terminal["body"]}
        self.assertEqual(self.pending([terminal, quote_only]), [])
        self.assertEqual(m.pending_findings(self.items, [terminal, correction], C,
            lambda b,t: "ahead", lambda u: "read"), [])

    def test_root_requires_rest_numeric_bot_identity(self):
        roots = root_comments([thread()])
        self.assertTrue(m.findings([thread()], roots))
        self.assertEqual(m.findings([thread()], []), [])
        for change in ({"id": 9}, {"type": "User"}, {"login": "fake[bot]"}):
            fake = [{**roots[0], "user": roots[0]["user"] | change}]
            self.assertEqual(m.findings([thread()], fake), [])
        self.assertEqual(m.findings([thread()], [roots[0] | {"body": "changed"}]), [])
        for snapshot in ([], [roots[0] | {"body": "changed"}]):
            with self.assertRaises(ValueError):
                m.findings([thread()], snapshot, strict_snapshot=True)

    def test_late_older_root_survives_prior_push(self):
        old = thread(root=1)
        late = thread(root=2)
        resolved, outdated = thread(root=3), thread(root=4)
        resolved["isResolved"], outdated["isOutdated"] = True, True
        items = findings([old, late, resolved, outdated])
        pending = m.pending_findings(items, [reply(self.receipt)], B,
            lambda base, tip: "identical" if base == tip else "ahead", lambda user: "write")
        self.assertEqual([item["root"] for item in pending], [2])

    def test_metadata_fails_closed(self):
        for body in ("plain trusted text", '<!-- devin-review-comment broken -->',
                     '<!-- devin-review-comment {"kind":"analysis"} -->',
                     '<!-- devin-review-comment {"kind":"bug"} -->' * 2):
            self.assertEqual(findings([thread(body)]), [])
        for flag in ("isResolved", "isOutdated"):
            value = thread()
            value[flag] = True
            self.assertEqual(findings([value]), [])

    def test_addressed_requires_identity_root_fingerprint_and_ancestry(self):
        self.assertEqual(self.pending([reply(self.receipt)]), [])
        for change in ({"root": True}, {"root": 2}, {"fingerprint": "f" * 64}, {"commit": A}, {"commit": "bad"}):
            self.assertTrue(self.pending([reply(self.receipt | change)],
                compare=lambda base, tip: "identical" if base == tip else "ahead"))
        for user in (actor(identity=1), actor("other[bot]"), actor() | {"type": "User"}):
            self.assertTrue(self.pending([reply(self.receipt, user)]))
        self.assertTrue(self.pending([reply(self.receipt)], compare=lambda b, t: "diverged"))
        with self.assertRaises(OSError):
            self.pending([reply(self.receipt)], compare=lambda b, t: (_ for _ in ()).throw(OSError("API unavailable")))

    def test_prose_failure_no_push_and_changed_findings_retry(self):
        for body in ("Fixed in " + B, "Tests failed; no push", "Needs a human", ""):
            self.assertTrue(self.pending([reply(self.receipt) | {"body": body}]))
        self.assertTrue(self.pending([reply(self.receipt | {"status": "failed"})]))
        self.items = findings([thread('<!-- devin-review-comment {"kind":"bug"} -->\nChanged bug')])
        self.assertTrue(self.pending([reply(self.receipt)]))

    def test_explicit_handoff_manual_retry_and_substantive_followup(self):
        receipt = reply(self.receipt | {"status": "needs-human"})
        self.assertEqual(self.pending([receipt]), [])
        self.assertTrue(self.pending([receipt], manual=True))
        follow = {"in_reply_to_id": 1, "created_at": "2026-10-16T02:00:00Z",
                  "user": actor("devin-ai-integration[bot]", m.DEVIN_ID), "body": "This still fails for empty input"}
        pending = self.pending([receipt, follow])
        self.assertTrue(pending)
        self.assertIn("generation", pending[0])
        structured = follow | {"body": '<!-- devin-review-comment {"kind":"bug"} -->\nStill broken'}
        self.assertTrue(self.pending([receipt, structured]))
        analysis = follow | {"body": '<!-- devin-review-comment {"kind":"analysis"} -->\nStill reviewing'}
        self.assertEqual(self.pending([receipt, analysis]), [])
        noise = follow | {"user": actor("other[bot]", 1)}
        self.assertEqual(self.pending([receipt, noise]), [])


class AttemptTests(unittest.TestCase):
    def setUp(self):
        self.items = findings([thread()])
        self.key = m.attempt_key(A, self.items)

    def attempts(self, count=3, user=None):
        return [{"user": user or actor("github-actions[bot]", m.ACTIONS_ID),
                 "body": m.marker("attempt", self.key | {"run": str(i + 1), "attempt": "1"})}
                for i in range(count)]

    def test_forged_duplicate_and_changed_key_controls(self):
        self.assertEqual(m.attempt_count(self.attempts(), self.key), 3)
        self.assertEqual(m.attempt_count(self.attempts() * 2, self.key), 3)
        self.assertEqual(m.attempt_count(self.attempts(user=actor("github-actions[bot]", 1)), self.key), 0)
        self.assertEqual(m.attempt_count(self.attempts(), m.attempt_key(B, self.items)), 0)
        self.assertEqual(m.attempt_count(self.attempts(), m.attempt_key(A, findings([thread(root=2)]))), 0)

    def runtime(self, count, manual=False, post_error=False, loop_count="0", clean=False, compare_error=None):
        threads = {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [] if clean else [thread()], "pageInfo": {"hasNextPage": False}}}}}}
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {
            "REPO": "o/r", "PR_NUMBER": "1", "FIXER_HEAD_SHA": A,
            "LOOP_COMMIT_COUNT": loop_count, "MANUAL_RETRY": "true" if manual else "false", "GITHUB_OUTPUT": str(Path(d) / "out"),
            "GITHUB_RUN_ID": "100", "GITHUB_RUN_ATTEMPT": "1"}), \
                patch.object(m.subprocess, "check_output", return_value=json.dumps(threads)), \
                patch.object(m, "pages", side_effect=[root_comments([thread()]) + ([reply({"root": 1, "fingerprint": self.items[0]["fingerprint"], "base": A, "commit": B, "status": "addressed"})] if compare_error else []), self.attempts(count)]), \
                patch.object(m, "api", side_effect=compare_error), \
                patch.object(m, "revoke_owned_ci"), \
                patch.object(m, "post", side_effect=OSError("write failed") if post_error else None) as post:
            if post_error:
                with self.assertRaises(OSError):
                    m.main()
                self.assertFalse((Path(d) / "out").exists())
                return "", post.call_count
            m.main()
            return (Path(d) / "out").read_text(), post.call_count

    def test_clean_findings_before_commit_cap_and_manual_cannot_bypass(self):
        output, posts = self.runtime(0, loop_count="8", clean=True)
        self.assertIn("no-pending-findings", output)
        self.assertEqual(posts, 0)
        for manual in (False, True):
            output, posts = self.runtime(0, loop_count="8", manual=manual)
            self.assertIn("loop-commit-limit", output)
            self.assertEqual(posts, 1)

    def test_bad_receipt_compare_does_not_poison_manual_or_automatic_runs(self):
        import subprocess
        for status in (404, 422):
            for manual in (False, True):
                error = subprocess.CalledProcessError(1, "gh", stderr=f"gh: invalid (HTTP {status})")
                output, _ = self.runtime(0, manual=manual, compare_error=error)
                self.assertIn("skip=false", output)
        for status in (401, 403, 429, 500):
            with self.assertRaises(subprocess.CalledProcessError):
                self.runtime(0, compare_error=subprocess.CalledProcessError(1, "gh", stderr=f"gh: error (HTTP {status})"))
        with self.assertRaises(OSError):
            self.runtime(0, compare_error=OSError("network failure"))

    def test_third_attempt_runs_fourth_pauses_manual_retries(self):
        output, posts = self.runtime(2)
        self.assertIn("skip=false", output)
        self.assertEqual(posts, 1)
        output, posts = self.runtime(3)
        self.assertIn("skip=true", output)
        self.assertIn("attempt-limit", output)
        output, posts = self.runtime(3, manual=True)
        self.assertIn("skip=false", output)
        self.assertEqual(posts, 0)

    def test_failed_reservation_never_authorizes_model(self):
        self.runtime(0, post_error=True)

    def test_paginated_comments_and_partial_failure(self):
        with patch.object(m, "api", side_effect=[[{}] * 100, [{"id": 101}]]):
            self.assertEqual(len(m.pages("repos/o/r/pulls/1/comments")), 101)
        with patch.object(m, "api", side_effect=[[{}] * 100, OSError("page2 failed")]):
            with self.assertRaises(OSError):
                m.pages("repos/o/r/pulls/1/comments")


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.items = findings([thread()])
        self.pause = {"id": 10, "user": actor("github-actions[bot]", m.ACTIONS_ID),
                      "body": m.marker("paused", m.attempt_key(A, self.items))}
        self.pr = {"state": "open", "draft": False, "head": {"sha": A, "repo": {"full_name": "o/r"}}}

    def test_active_pause_changed_findings_resolved_handoff_and_forgery(self):
        with patch.object(m, "live_state", return_value=(self.items, self.items)) as live:
            self.assertTrue(m.readiness_blocked("o/r", "1", A, [self.pause], lambda p: self.pr))
            self.assertFalse(m.readiness_blocked("o/r", "1", A, [self.pause | {"user": actor("github-actions[bot]", 1)}], lambda p: self.pr))
            live.return_value = (findings([thread(root=2)]), findings([thread(root=2)]))
            self.assertFalse(m.readiness_blocked("o/r", "1", A, [self.pause], lambda p: self.pr))
            live.return_value = ([], [])
            self.assertFalse(m.readiness_blocked("o/r", "1", A, [self.pause], lambda p: self.pr))
            live.return_value = ([], self.items)
            self.assertTrue(m.readiness_blocked("o/r", "1", A, [self.pause], lambda p: self.pr))
        self.assertTrue(m.readiness_blocked("o/r", "1", A, [], lambda p: self.pr | {"state": "closed"}))
        with self.assertRaises(OSError):
            m.readiness_blocked("o/r", "1", A, [], lambda p: (_ for _ in ()).throw(OSError("transport")))

    def test_third_attempt_exhaustion_blocks_before_pause_notice(self):
        attempts = [{"id": i+1, "user": actor("github-actions[bot]", m.ACTIONS_ID),
                     "body": m.marker("attempt", {**m.attempt_key(A, self.items), "run": str(i+1), "attempt": "1"})} for i in range(3)]
        self.assertEqual(m.latest_pause_id(attempts, A), 3)
        self.assertEqual(m.latest_pause_id(attempts + [attempts[0] | {"id": 20}], A), 3)
        self.assertEqual(m.latest_pause_id(attempts[:2], A), 0)
        with patch.object(m, "live_state", return_value=(self.items, self.items)):
            self.assertTrue(m.readiness_blocked("o/r", "1", A, attempts, lambda p: self.pr))
            self.assertFalse(m.readiness_blocked("o/r", "1", A, attempts[:2], lambda p: self.pr))

    def test_exact_label_event_ownership_preserves_human_override(self):
        event = {"id": 7, "event": "labeled", "label": {"name": "ci:full"}, "actor": {"login": "writer"}}
        posts = []
        self.assertTrue(m.record_ci_ownership("o/r", "1", A, 0, "writer", lambda p: [event], lambda r,n,b: posts.append(b)))
        owned = {"id": 11, "user": actor("github-actions[bot]", m.ACTIONS_ID), "body": posts[0]}
        deletes = []
        read = lambda p: [owned] if p.endswith("comments") else [event]
        self.assertTrue(m.revoke_owned_ci("o/r", "1", A, read, lambda r,n: deletes.append(n), lambda *args: None))
        read = lambda p: [owned] if p.endswith("comments") else [event, event | {"id": 8, "actor": {"login": "human"}}]
        self.assertFalse(m.revoke_owned_ci("o/r", "1", A, read, lambda r,n: deletes.append(n), lambda *args: None))
        self.assertEqual(deletes, ["1"])
        self.assertFalse(m.record_ci_ownership("o/r", "1", A, 7, "writer", lambda p: [event], lambda r,n,b: posts.append(b)))
        with self.assertRaises(ValueError):
            m.record_ci_ownership("o/r", "1", A, 0, "writer", lambda p: [event | {"actor": {"login": "human"}}], lambda *args: None)
        with self.assertRaises(OSError):
            m.revoke_owned_ci("o/r", "1", A, lambda p: (_ for _ in ()).throw(OSError("page failure")))

    def test_ready_check_requires_trusted_exact_pause_generation(self):
        checked = {"id": 12, "user": actor("github-actions[bot]", m.ACTIONS_ID),
                   "body": m.marker("ci-checked", {"head": A, "pause": 10})}
        self.assertEqual(m.latest_ready_check([checked], A, 10), 12)
        self.assertEqual(m.latest_ready_check([checked], A, 13), 0)
        self.assertEqual(m.latest_ready_check([checked | {"user": actor("github-actions[bot]", 1)}], A, 10), 0)


if __name__ == "__main__":
    unittest.main()

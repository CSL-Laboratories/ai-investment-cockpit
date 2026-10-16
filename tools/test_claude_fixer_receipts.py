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


def actor(login="claude[bot]", identity=m.CLAUDE_ID):
    return {"login": login, "type": "Bot", "id": identity}


def reply(data, user=None):
    return {"in_reply_to_id": 1, "created_at": "2026-10-16T01:00:00Z",
            "user": user or actor(), "body": m.marker("result", data)}


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.items = m.findings([thread()])
        self.receipt = {"root": 1, "fingerprint": self.items[0]["fingerprint"],
                        "status": "addressed", "base": A, "commit": B}

    def pending(self, comments, compare=lambda base, tip: "ahead", manual=False):
        return m.pending_findings(self.items, comments, C, compare, lambda user: "write", manual)

    def test_late_older_root_survives_prior_push(self):
        old = thread(root=1)
        late = thread(root=2)
        resolved, outdated = thread(root=3), thread(root=4)
        resolved["isResolved"], outdated["isOutdated"] = True, True
        items = m.findings([old, late, resolved, outdated])
        pending = m.pending_findings(items, [reply(self.receipt)], B,
            lambda base, tip: "identical" if base == tip else "ahead", lambda user: "write")
        self.assertEqual([item["root"] for item in pending], [2])

    def test_metadata_fails_closed(self):
        for body in ("plain trusted text", '<!-- devin-review-comment broken -->',
                     '<!-- devin-review-comment {"kind":"analysis"} -->',
                     '<!-- devin-review-comment {"kind":"bug"} -->' * 2):
            self.assertEqual(m.findings([thread(body)]), [])
        for flag in ("isResolved", "isOutdated"):
            value = thread()
            value[flag] = True
            self.assertEqual(m.findings([value]), [])

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
        self.items = m.findings([thread('<!-- devin-review-comment {"kind":"bug"} -->\nChanged bug')])
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
        noise = follow | {"user": actor("other[bot]", 1)}
        self.assertEqual(self.pending([receipt, noise]), [])


class AttemptTests(unittest.TestCase):
    def setUp(self):
        self.items = m.findings([thread()])
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
        self.assertEqual(m.attempt_count(self.attempts(), m.attempt_key(A, m.findings([thread(root=2)]))), 0)

    def runtime(self, count, manual=False, post_error=False):
        threads = {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [thread()], "pageInfo": {"hasNextPage": False}}}}}}
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {
            "REPO": "o/r", "PR_NUMBER": "1", "FIXER_HEAD_SHA": A,
            "MANUAL_RETRY": "true" if manual else "false", "GITHUB_OUTPUT": str(Path(d) / "out"),
            "GITHUB_RUN_ID": "100", "GITHUB_RUN_ATTEMPT": "1"}), \
                patch.object(m.subprocess, "check_output", return_value=json.dumps(threads)), \
                patch.object(m, "pages", side_effect=[[], self.attempts(count)]), \
                patch.object(m, "post", side_effect=OSError("write failed") if post_error else None) as post:
            if post_error:
                with self.assertRaises(OSError):
                    m.main()
                self.assertFalse((Path(d) / "out").exists())
                return "", post.call_count
            m.main()
            return (Path(d) / "out").read_text(), post.call_count

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


if __name__ == "__main__":
    unittest.main()

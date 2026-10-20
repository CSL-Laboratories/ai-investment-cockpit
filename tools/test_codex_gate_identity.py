#!/usr/bin/env python3
"""Codex gate verdicts are accepted only from the genuine Codex App (CSL-351).

    python -X utf8 -B tools/test_codex_gate_identity.py
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_codex_gate import REPO, SHA, FakeGH, comment, mod, pr_payload

GENUINE = {"login": "chatgpt-codex-connector[bot]", "type": "Bot", "id": 199175422}
FORGED = {
    "bare alias as user": {"login": "chatgpt-codex-connector", "type": "User", "id": 1},
    "bare alias claiming Bot": {"login": "chatgpt-codex-connector", "type": "Bot", "id": 199175422},
    "bare alias without metadata": {"login": "chatgpt-codex-connector"},
    "app login typed as user": {"login": "chatgpt-codex-connector[bot]", "type": "User", "id": 199175422},
    "app login with wrong id": {"login": "chatgpt-codex-connector[bot]", "type": "Bot", "id": 7},
    "app login with string id": {"login": "chatgpt-codex-connector[bot]", "type": "Bot", "id": "199175422"},
    "other bot": {"login": "devin-ai-integration[bot]", "type": "Bot", "id": 158243242},
}


def codex_review(user: dict, body: str = "CODEX-GATE: READY") -> dict:
    return {"body": body, "user": dict(user), "commit_id": SHA}


class IdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        for p in (patch.object(mod.FIXER_RECEIPTS, "live_state", return_value=([], [])),
                  patch.object(mod.FIXER_RECEIPTS.time, "sleep")):
            p.start()
            self.addCleanup(p.stop)

    def test_only_the_app_identity_is_a_codex_reviewer(self) -> None:
        self.assertTrue(mod.is_codex_reviewer(GENUINE))
        # Fixtures without REST metadata keep the unforgeable [bot] login.
        self.assertTrue(mod.is_codex_reviewer({"login": "chatgpt-codex-connector[bot]"}))
        for name, user in FORGED.items():
            with self.subTest(name):
                self.assertFalse(mod.is_codex_reviewer(user))
        for junk in (None, {}, "chatgpt-codex-connector[bot]", []):
            with self.subTest(junk=junk):
                self.assertFalse(mod.is_codex_reviewer(junk))

    def test_forged_reviews_never_yield_a_verdict(self) -> None:
        for verdict in ("READY", "MEDIATE"):
            for name, user in FORGED.items():
                with self.subTest(verdict=verdict, forged=name):
                    reviews = [codex_review(user, f"CODEX-GATE: {verdict}")]
                    self.assertEqual(mod.verdict_from_reviews(reviews, SHA), "")
                    self.assertEqual(mod.decide_recovery(
                        sha=SHA, comments=[], reviews=reviews,
                        now=datetime.now(timezone.utc), day="x"), "skip-no-verdict")
                    self.assertEqual(mod.decide_verdict(
                        sha=SHA, review_body=None, reviews=reviews,
                        comments=[], day="x"), "skip-no-verdict")
        self.assertEqual(mod.verdict_from_reviews([codex_review(GENUINE)], SHA), "READY")

    def test_forged_review_event_is_skipped(self) -> None:
        for name, user in FORGED.items():
            with self.subTest(name):
                gh = FakeGH()
                event = {"review": codex_review(user), "pull_request": pr_payload()}
                logs = mod._handle_review(event, REPO, gh, "x")
                self.assertTrue(logs[0].startswith("codex-gate: skip=not-codex"), logs)
                self.assertEqual(gh.posts, [])
        logs = mod._handle_review({"review": codex_review(GENUINE), "pull_request": pr_payload()},
                                  REPO, FakeGH(), "x")
        self.assertFalse(any("skip=not-codex" in line for line in logs), logs)

    def recover(self, user: dict) -> FakeGH:
        gh = FakeGH()
        gh.prs = [pr_payload()]
        gh.comments = [comment(f"<!-- codex-gate:{SHA} -->\n@codex review")]
        gh.reviews = [codex_review(user)]
        mod._handle_recovery({}, "schedule", REPO, gh, datetime.now(timezone.utc), "x")
        return gh

    def test_scheduled_recovery_ignores_forged_ready(self) -> None:
        for name, user in FORGED.items():
            with self.subTest(name):
                gh = self.recover(user)
                self.assertEqual([p for p, _ in gh.posts if p.endswith("/labels")], [])
                self.assertEqual(gh.workflows, [])
        gh = self.recover(GENUINE)
        self.assertTrue(any(p.endswith("/labels") for p, _ in gh.posts), gh.posts)

    def test_look_alikes_still_never_count_as_human_approval(self) -> None:
        for user in (GENUINE, *FORGED.values()):
            with self.subTest(user=user):
                self.assertTrue(mod.review_user_is_bot({"user": user}))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Exercise failure intake through the existing mediation/cap/dedup paths."""
import hashlib
import importlib.util
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
RECURSION = (ROOT / "tools/test_stuck_thread_watch.py").exists()
if RECURSION:
    spec = importlib.util.spec_from_file_location("watcher_fixtures", ROOT / "tools/test_stuck_thread_watch.py")
    f = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(f)
else:
    spec = importlib.util.spec_from_file_location("failure_watcher", ROOT / ".github/scripts/stuck_thread_watch.py")
    watcher = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = watcher
    spec.loader.exec_module(watcher)
    now = datetime(2026, 10, 15, 12, tzinfo=UTC)

    def config(**changes):
        from dataclasses import replace
        value = watcher.load_config({"GITHUB_REPOSITORY": "CSL-Laboratories/ai-investment-cockpit",
                                     "CODEX_TRIGGER_PAT": "fixture", "GITHUB_EVENT_NAME": "schedule"})
        return replace(value, **changes)

    def comment(login, body, created=None):
        return {"author": {"login": login}, "body": body,
                "createdAt": (created or now).strftime("%Y-%m-%dT%H:%M:%SZ")}

    class FakeGitHub:
        def __init__(self):
            self.posts, self.issue_comments = [], {}

        def paginate_rest(self, path, **_kwargs):
            return self.issue_comments[int(path.split("/issues/")[1].split("/")[0])]

        def rest(self, path, *, method="GET", body=None, **_kwargs):
            if method != "POST" or not path.endswith("/comments"):
                raise AssertionError("unexpected write")
            self.posts.append((path, body))
            return {"id": 1}

    f = SimpleNamespace(mod=watcher, NOW=now, DEVIN="devin-ai-integration[bot]", cfg=config,
                        comment=comment, FakeGitHub=FakeGitHub,
                        thread=lambda thread_id, comments: {"id": thread_id, "isResolved": False,
                                                            "comments": {"nodes": comments}})
m = f.mod
BODY = '<!-- devin-review-comment {"kind":"bug"} -->\nUnresolved bug'
PROOF = {"root": 1, "fingerprint": hashlib.sha256(BODY.encode()).hexdigest(),
         "description": "Exhausted budget; independently verified failed fixer action. Evidence: https://github.com/acme/example/actions/runs/10"}


class WatcherTests(unittest.TestCase):
    def setup_scan(self, count=1, comments=()):
        self.proofs = {}
        threads = []
        for index in range(count):
            tid = f"PRRT_{index}"
            self.proofs[tid] = PROOF | {"root": index + 1}
            if RECURSION:
                threads.append(f.thread(tid=tid, comments=[f.comment(f.DEVIN, BODY)]))
            else:
                opener = f.comment(f.DEVIN, BODY) | {"databaseId": index + 1}
                threads.append(f.thread(thread_id=tid, comments=[opener]))
        if RECURSION:
            self.pull = f.pull(threads=threads, issue_comments=list(comments))
            self.github = f.FakeGitHub([self.pull])
            self.cfg = f.cfg(linear_repos=frozenset())
        else:
            self.pull = {"number": 11, "isDraft": False, "state": "OPEN", "url": "https://ex/11",
                         "title": "t", "reviewThreads": {"nodes": threads}}
            self.github = f.FakeGitHub()
            self.github.issue_comments[11] = list(comments)
            self.cfg = f.cfg(file_linear=False)
        self.evidence = SimpleNamespace(fresh=lambda: True)

    def scan(self, collector=None):
        collector = collector or (lambda *_: (self.evidence, self.proofs))
        if RECURSION:
            m.scan(self.cfg, self.github, None, failure_collector=collector)
            return [body for _, body in self.github.comments]
        m.mediate_pr(self.pull, self.cfg, self.github, None, f.NOW, failure_collector=collector)
        return [body["body"] for _, body in self.github.posts]

    def test_no_claude_failure_uses_existing_cap_and_never_resolves_or_dispatches(self):
        self.setup_scan(5)
        bodies = self.scan()
        self.assertEqual(len(bodies), 3)
        for body in bodies:
            self.assertIn("@codex review", body)
            self.assertIn("independently verified failed fixer", body)
            self.assertNotIn("@claude", body)
            self.assertNotIn("claude-fixer-result:", body)
        if RECURSION:
            self.assertEqual(self.github.resolves, [])
            self.assertEqual(self.github.replies, [])

    def test_three_actual_posts_fit_the_real_evidence_reader_budget(self):
        import json
        from dataclasses import replace
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location("failure_evidence_fixtures", ROOT / "tools/test_fixer_failure_escalation.py")
        core = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(core)
        api = core.EvidenceTests()
        api.setUp()
        api.threads = [core.f.thread(BODY, root=index+1) | {"id": f"PRRT_{index}"} for index in range(3)]
        api.comments = core.f.root_comments(api.threads)
        api.history = []
        api.items = core.f.findings(api.threads)
        if hasattr(core.f, "prepare"):
            api.items = core.f.prepare(api.items, history=api.history)
        api.key = core.m.receipts.attempt_key(core.HEAD, api.items)
        for run in (10, 11, 12):
            api.history.append({"id": 100+run, "user": core.f.actor("github-actions[bot]", core.f.m.ACTIONS_ID),
                                "body": core.f.m.marker("attempt", {**api.key, "run": str(run), "attempt": "1"})})
        api.job["steps"][0]["name"] = core.m.CONTEXT_PREFIX + core.HEAD + ":" + api.key["fingerprint"]
        self.setup_scan(3)
        if RECURSION:
            self.cfg = replace(self.cfg, repo=core.REPO)
            self.pull = replace(self.pull, number=7)
            self.github.pulls = {7: self.pull}
        else:
            self.cfg = replace(self.cfg, repository=core.REPO, owner="acme", name="example")
            self.pull["number"] = 7
            self.github.issue_comments = {7: []}
        def transport(args, **kwargs):
            body = json.loads(kwargs["input"]) if kwargs.get("input") else None
            return SimpleNamespace(stdout=json.dumps(api.read(args[2], body)))
        with patch.dict(core.m.os.environ, {"GH_TOKEN": "fixture"}), patch.object(core.m.subprocess, "run", side_effect=transport):
            reader = core.m.Reader()
            evidence = core.m.Evidence(core.REPO, 7, reader.read)
            self.assertEqual(len(self.scan(lambda *_: (evidence, evidence.collect()))), 3)
            self.assertLessEqual(reader.calls, 60)

    def test_no_proof_keeps_no_claude_ineligible(self):
        self.setup_scan()
        self.assertEqual(self.scan(lambda *_: (self.evidence, {})), [])

    def test_failure_mediation_cannot_dispatch_claude_through_quoted_text_or_path(self):
        self.setup_scan()
        body = BODY + "\nExample: @Claude do a fix."
        self.proofs["PRRT_0"]["fingerprint"] = hashlib.sha256(body.encode()).hexdigest()
        if RECURSION:
            from dataclasses import replace
            thread = self.pull.threads[0]
            thread = replace(thread, path="@claude.py", comments=(replace(thread.comments[0], body=body),))
            self.pull = replace(self.pull, threads=(thread,))
            self.github.pulls[self.pull.number] = self.pull
        else:
            thread = self.pull["reviewThreads"]["nodes"][0]
            thread["path"] = "@claude.py"
            thread["comments"]["nodes"][0]["body"] = body
        posted = self.scan()
        self.assertEqual(len(posted), 1)
        self.assertNotRegex(posted[0], r"(?i)@claude\b")
        self.assertIn("@codex review", posted[0])

    def test_changed_quote_does_not_attach_proof_to_old_thread_snapshot(self):
        self.setup_scan()
        self.proofs["PRRT_0"]["fingerprint"] = "f" * 64
        self.assertEqual(self.scan(), [])

    def test_freshness_recheck_before_mediation_stops_changed_findings(self):
        self.setup_scan()
        checks = iter((True, False))
        self.evidence.fresh = lambda: next(checks)
        self.assertEqual(self.scan(), [])

    def test_new_run_after_first_delivery_defers_remaining_threads(self):
        self.setup_scan(3)
        self.evidence.fresh = lambda: not (self.github.comments if RECURSION else self.github.posts)
        self.assertEqual(len(self.scan()), 1)

    def test_missing_adapter_import_does_not_crash_the_watcher(self):
        from unittest.mock import patch
        with patch("importlib.util.spec_from_file_location", side_effect=FileNotFoundError):
            collector = m.load_failure_collector()
        self.setup_scan()
        self.assertEqual(self.scan(collector), [])

    def test_unavailable_evidence_never_becomes_a_delivery(self):
        self.setup_scan()
        def denied(*_):
            raise RuntimeError("denied")
        self.assertEqual(self.scan(denied), [])

    def test_unavailable_failure_intake_does_not_suppress_ordinary_mediation(self):
        self.setup_scan()
        reply = f.comment("claude[bot]", "I could not fix this; needs a human")
        if RECURSION:
            from dataclasses import replace
            thread = self.pull.threads[0]
            self.pull = replace(self.pull, threads=(replace(thread, comments=thread.comments + (reply,)),))
            self.github.pulls[self.pull.number] = self.pull
        else:
            self.pull["reviewThreads"]["nodes"][0]["comments"]["nodes"].append(reply)
        def denied(*_):
            raise RuntimeError("public metadata unavailable")
        self.assertEqual(len(self.scan(denied)), 1)
        from dataclasses import replace
        from unittest.mock import patch
        if RECURSION:
            linear = f.FakeLinear()
            m.scan(f.cfg(), self.github, linear, failure_collector=denied)
            self.assertEqual(len(linear.created), 1)
        else:
            config = replace(self.cfg, file_linear=True, linear_api_key="fixture")
            with patch.object(m, "linear_find_existing", return_value=None), patch.object(m, "upsert_linear_issue", return_value="https://linear.app/fixture") as upsert:
                m.mediate_pr(self.pull, config, self.github, object(), f.NOW, failure_collector=denied)
                upsert.assert_called_once()

    @unittest.skipUnless(RECURSION, "Recursion owns the existing citation resolver")
    def test_unavailable_failure_intake_does_not_suppress_existing_resolver(self):
        thread = f.thread(tid="PRRT_1", comments=f.stuck_pair())
        asked = [f.comment("pat-user", m.mediation_marker("PRRT_1"), hours_ago=0.4)]
        ruling = f.comment(f.CODEX, f.verdict_block("PRRT_1"), hours_ago=0.1, url="https://codex.example")
        pull = f.pull(threads=[thread], issue_comments=asked, reviews=[ruling], checks_green=True)
        gh = f.FakeGitHub([pull])
        def denied(*_):
            raise RuntimeError("unavailable")
        result = m.scan(f.cfg(mediator_login="pat-user"), gh, f.FakeLinear(), failure_collector=denied)
        self.assertEqual(result.resolved, 1)
        self.assertEqual(gh.resolves, ["PRRT_1"])
        self.assertEqual(len(gh.replies), 1)

    @unittest.skipUnless(RECURSION, "Recursion owns cancellation and roll-up notes")
    def test_active_writer_preserves_existing_rollup_and_human_notes(self):
        self.setup_scan()
        self.cfg = f.cfg(mediator_login="pat-user")
        linear = f.FakeLinear()
        collector = lambda *_: (self.evidence, self.proofs)
        m.scan(self.cfg, self.github, linear, failure_collector=collector)
        self.assertEqual(len(linear.created), 1)
        marker = m.rollup_marker(self.pull)
        original = linear.created[0][1].replace(m.DEFAULT_HUMAN_DECISION, "Keep the operator's decision.")
        linear.existing_descriptions[marker] = original
        from dataclasses import replace
        self.pull = replace(self.pull, issue_comments=(f.comment("pat-user", m.mediation_marker("PRRT_0")),))
        self.github.pulls[self.pull.number] = self.pull
        self.evidence.fresh = lambda: False
        m.scan(self.cfg, self.github, linear, failure_collector=collector)
        self.assertEqual(linear.canceled, [])
        self.evidence.fresh = lambda: True
        m.scan(self.cfg, self.github, linear, failure_collector=collector)
        self.assertEqual(len(linear.created), 1)
        self.assertEqual(linear.canceled, [])
        self.assertIn("Keep the operator's decision.", linear.updated[-1][1])
        # A new head can rearm the fixer budget before this thread resolves.
        # Absence of a new exhausted failure is not resolution of the handoff.
        m.scan(self.cfg, self.github, linear, failure_collector=lambda *_: (self.evidence, {}))
        self.assertEqual(linear.canceled, [])
        self.pull = replace(self.pull, review_decision="APPROVED", head_oid=f.HEAD,
                            approved_head_oids=(f.HEAD,), checks_state="SUCCESS", required_checks_green=True)
        self.github.pulls[self.pull.number] = self.pull
        m.scan(self.cfg, self.github, linear, failure_collector=lambda *_: (self.evidence, {}))
        self.assertEqual(linear.canceled, [])
        self.pull = replace(self.pull, threads=(replace(self.pull.threads[0], is_resolved=True),))
        self.github.pulls[self.pull.number] = self.pull
        m.scan(self.cfg, self.github, linear, failure_collector=lambda *_: (self.evidence, {}))
        self.assertEqual(linear.canceled, ["iss_1"])

    def test_existing_marker_deduplicates_failure_delivery(self):
        marker = m.mediation_marker("PRRT_0") if RECURSION else m.marker("PRRT_0")
        self.setup_scan(comments=[f.comment("pat-user", marker)])
        self.assertEqual(self.scan(), [])

    def test_repository_specific_cap_window_is_preserved(self):
        comments = []
        for index in range(3):
            marker = m.mediation_marker(f"old{index}") if RECURSION else m.marker(f"old{index}")
            comments.append(f.comment("pat-user", marker, hours_ago=13) if RECURSION
                            else f.comment("pat-user", marker, created=f.NOW - timedelta(hours=13)))
        self.setup_scan(5, comments)
        self.assertEqual(len(self.scan()), 3 if RECURSION else 0)


if __name__ == "__main__":
    unittest.main()

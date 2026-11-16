#!/usr/bin/env python3
"""Exercise failure intake through the person-started stuck-thread report (CSL-238).

The watcher no longer posts anything on a PR: a verified fixer failure is reported in
the job log and, when Linear filing is on, retained in the per-PR roll-up."""
import hashlib
import importlib.util
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if True:
    spec = importlib.util.spec_from_file_location("failure_watcher", ROOT / ".github/scripts/stuck_thread_watch.py")
    watcher = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = watcher
    spec.loader.exec_module(watcher)
    now = datetime(2026, 10, 15, 12, tzinfo=UTC)

    def config(**changes):
        from dataclasses import replace
        value = watcher.load_config({"GITHUB_REPOSITORY": "CSL-Laboratories/ai-investment-cockpit",
                                     "GITHUB_EVENT_NAME": "workflow_dispatch"})
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
            opener = f.comment(f.DEVIN, BODY) | {"databaseId": index + 1}
            threads.append(f.thread(thread_id=tid, comments=[opener]))
        self.pull = {"number": 11, "isDraft": False, "state": "OPEN", "url": "https://ex/11",
                     "title": "t", "reviewThreads": {"nodes": threads}}
        self.github = f.FakeGitHub()
        self.github.issue_comments[11] = list(comments)
        self.cfg = f.cfg(file_linear=False)
        self.evidence = SimpleNamespace(fresh=lambda: True)

    def scan(self, collector=None, linear=None, cfg=None):
        """Report lines for one PR; the fake GitHub refuses every write."""
        collector = collector or (lambda *_: (self.evidence, self.proofs))
        logs = m.report_pr(self.pull, cfg or self.cfg, self.github, linear, f.NOW, failure_collector=collector)
        self.assertEqual(self.github.posts, [])
        return [line for line in logs if " report=" in line]

    def test_verified_failure_is_reported_without_any_post_or_dispatch(self):
        self.setup_scan(5)
        lines = self.scan()
        self.assertEqual(len(lines), 5)
        for line in lines:
            self.assertIn("report=exhausted-fixer-failed", line)
            self.assertNotIn("@claude", line)

    def test_three_reports_fit_the_real_evidence_reader_budget(self):
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

    def test_linear_rollup_stays_within_the_evidence_reader_budget(self):
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
        self.cfg = replace(self.cfg, repository=core.REPO, owner="acme", name="example")
        self.pull["number"] = 7
        self.github.issue_comments = {7: []}
        def transport(args, **kwargs):
            body = json.loads(kwargs["input"]) if kwargs.get("input") else None
            return SimpleNamespace(stdout=json.dumps(api.read(args[2], body)))
        from contextlib import ExitStack
        api.run.update(event="repository_dispatch", head_sha="d" * 40, head_branch="main", pull_requests=[])
        existing = [None]
        self.cfg = replace(self.cfg, file_linear=True, linear_api_key="fixture")
        def write(_query, variables):
            existing[0] = {"id": "issue", "description": variables["input"]["description"]}
            return {"issueCreate": {"success": True, "issue": {"url": "https://linear.app/fixture"}},
                    "issueUpdate": {"success": True, "issue": {"url": "https://linear.app/fixture"}}}
        linear = SimpleNamespace(graphql=write)
        with ExitStack() as stack:
            stack.enter_context(patch.dict(core.m.os.environ, {"GH_TOKEN": "fixture"}))
            stack.enter_context(patch.object(core.m.subprocess, "run", side_effect=transport))
            stack.enter_context(patch.object(m, "linear_find_existing", side_effect=lambda *_: existing[0]))
            stack.enter_context(patch.object(m, "linear_resolve_ids", return_value={
                "teamId": "team", "labelIds": [], "projectId": "project"}))
            for round_index in range(2):
                reader = core.m.Reader()
                evidence = core.m.Evidence(core.REPO, 7, reader.read)
                def collector(*_, evidence=evidence):
                    return evidence, evidence.collect()
                before = len(api.calls)
                lines = self.scan(collector, linear=linear)
                self.assertLessEqual(len(api.calls) - before, 60)
                self.assertTrue(lines)
                self.assertIsNotNone(existing[0])
                self.assertIn("fixer-failure-handoff:", existing[0]["description"])

    def test_no_proof_keeps_no_claude_ineligible(self):
        self.setup_scan()
        self.assertEqual(self.scan(lambda *_: (self.evidence, {})), [])

    def test_failure_report_with_claude_text_or_path_posts_nothing(self):
        self.setup_scan()
        body = BODY + "\nExample: @Claude do a fix."
        self.proofs["PRRT_0"]["fingerprint"] = hashlib.sha256(body.encode()).hexdigest()
        thread = self.pull["reviewThreads"]["nodes"][0]
        thread["path"] = "@claude.py"
        thread["comments"]["nodes"][0]["body"] = body
        lines = self.scan()
        self.assertEqual(len(lines), 1)
        self.assertNotRegex(lines[0], r"(?i)@claude\b")

    def test_changed_quote_does_not_attach_proof_to_old_thread_snapshot(self):
        self.setup_scan()
        self.proofs["PRRT_0"]["fingerprint"] = "f" * 64
        self.assertEqual(self.scan(), [])

    def test_freshness_recheck_before_report_stops_changed_findings(self):
        self.setup_scan()
        checks = iter((True, False))
        self.evidence.fresh = lambda: next(checks)
        self.assertEqual(self.scan(), [])

    def test_linear_delivery_rechecks_failure_evidence_after_lookups(self):
        from dataclasses import replace
        from unittest.mock import Mock, patch
        for existing_issue in (False, True):
            for denied in (False, True):
                with self.subTest(existing=existing_issue, denied=denied):
                    self.setup_scan()
                    live = [True]
                    def fresh(live=live, denied=denied):
                        if not live[0] and denied:
                            raise RuntimeError("read denied")
                        return live[0]
                    self.evidence.fresh = fresh
                    collector = lambda *_: (self.evidence, self.proofs)
                    cfg = replace(self.cfg, file_linear=True, linear_api_key="fixture")
                    existing = {"id": "issue", "description": "Human notes."} if existing_issue else None
                    def lookup(*_, live=live, existing=existing):
                        live[0] = False
                        return existing
                    linear = SimpleNamespace(graphql=Mock())
                    with patch.object(m, "linear_find_existing", side_effect=lookup), patch.object(
                            m, "linear_resolve_ids", return_value={"teamId": "team", "labelIds": [], "projectId": "project"}):
                        self.assertEqual(self.scan(collector, linear=linear, cfg=cfg), [])
                    linear.graphql.assert_not_called()

    def test_stale_failure_does_not_suppress_ordinary_linear_delivery(self):
        from dataclasses import replace
        from unittest.mock import patch
        self.setup_scan()
        live = [True]
        self.evidence.fresh = lambda: live[0]
        ordinary = f.thread(thread_id="PRRT_ordinary", comments=[
            f.comment(f.DEVIN, "Other bug"), f.comment("claude[bot]", "needs a human")])
        self.pull["reviewThreads"]["nodes"].append(ordinary)
        cfg = replace(self.cfg, file_linear=True, linear_api_key="fixture")
        mutations = []
        def write(_query, args):
            mutations.append(args)
            return {"issueCreate": {"success": True, "issue": {"url": "https://linear.app/fixture"}}}
        def find(*_):
            live[0] = False
        with patch.object(m, "linear_find_existing", side_effect=find), patch.object(
                m, "linear_resolve_ids", return_value={"teamId": "team", "labelIds": [], "projectId": "project"}):
            lines = self.scan(lambda *_: (self.evidence, self.proofs), linear=SimpleNamespace(graphql=write), cfg=cfg)
        self.assertEqual(len(mutations), 1)
        self.assertIn("Other bug", mutations[0]["input"]["description"])
        self.assertNotIn("fixer-failure-handoff:PRRT_0", mutations[0]["input"]["description"])
        self.assertEqual([("PRRT_ordinary" in line) for line in lines], [True])

    def test_late_stale_failure_rechecks_linear_items(self):
        from dataclasses import replace
        from unittest.mock import patch
        for existing_issue in (False, True):
            with self.subTest(existing_issue=existing_issue):
                self.setup_scan()
                live = [True]
                self.evidence.fresh = lambda live=live: live[0]
                self.pull["reviewThreads"]["nodes"].append(f.thread(
                    thread_id="PRRT_ordinary", comments=[f.comment(f.DEVIN, "Changed ordinary finding"),
                                                         f.comment("claude[bot]", "needs a human")]))
                cfg = replace(self.cfg, file_linear=True, linear_api_key="fixture")
                existing = {"id": "issue", "description": "Earlier ordinary finding. Human notes."} if existing_issue else None
                def lookup(*_, live=live, existing=existing):
                    live[0] = False
                    return existing
                mutations = []
                def write(_query, args, mutations=mutations, existing_issue=existing_issue):
                    mutations.append(args)
                    return {"issueUpdate" if existing_issue else "issueCreate": {
                        "success": True, "issue": {"url": "https://linear.app/fixture"}}}
                with patch.object(m, "linear_find_existing", side_effect=lookup), patch.object(
                        m, "linear_resolve_ids", return_value={"teamId": "team", "labelIds": [], "projectId": "project"}):
                    lines = self.scan(lambda *_: (self.evidence, self.proofs),
                                      linear=SimpleNamespace(graphql=write), cfg=cfg)
                self.assertEqual(len(mutations), 1)
                self.assertIn("Changed ordinary finding", mutations[0]["input"]["description"])
                self.assertNotIn("fixer-failure-handoff:PRRT_0", mutations[0]["input"]["description"])
                self.assertEqual([("PRRT_ordinary" in line) for line in lines], [True])

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

    def test_unavailable_failure_intake_does_not_suppress_ordinary_reports(self):
        self.setup_scan()
        reply = f.comment("claude[bot]", "I could not fix this; needs a human")
        self.pull["reviewThreads"]["nodes"][0]["comments"]["nodes"].append(reply)
        def denied(*_):
            raise RuntimeError("public metadata unavailable")
        self.assertEqual(len(self.scan(denied)), 1)
        from dataclasses import replace
        from unittest.mock import patch
        config = replace(self.cfg, file_linear=True, linear_api_key="fixture")
        with patch.object(m, "linear_find_existing", return_value=None), patch.object(m, "upsert_linear_issue", return_value="https://linear.app/fixture") as upsert:
            self.scan(denied, linear=object(), cfg=config)
            upsert.assert_called_once()

    def test_retained_failure_and_ordinary_linear_items_survive_two_rewrites(self):
        from dataclasses import replace
        from unittest.mock import patch
        self.setup_scan()
        def denied(*_):
            raise RuntimeError("comment page limit")
        if True:
            config = replace(self.cfg, file_linear=True, linear_api_key="fixture")
            items, _ = m.select_stuck(self.pull, config, f.NOW, self.proofs)
            existing = {"id": "issue", "description": m.build_linear_description(items, config) + "\nHuman notes remain."}
            ordinary = f.thread(thread_id="PRRT_ordinary", comments=[
                f.comment(f.DEVIN, "Other bug"), f.comment("claude[bot]", "needs a human")])
            self.pull["reviewThreads"]["nodes"].append(ordinary)
            def update(_query, variables):
                existing["description"] = variables["input"]["description"]
                return {"issueUpdate": {"success": True, "issue": {"url": "https://linear.app/fixture"}}}
            linear = SimpleNamespace(graphql=update)
            with patch.object(m, "linear_find_existing", return_value=existing):
                for collector in (denied, lambda *_: (self.evidence, {})):
                    self.scan(collector, linear=linear, cfg=config)
                    self.assertEqual(existing["description"].count("<!-- fixer-failure-handoff:PRRT_0 -->"), 1)
                    self.assertIn("Other bug", existing["description"])
                    self.assertIn("**Stuck threads:** 2", existing["description"])
                    self.assertIn("### 2.", existing["description"])
                    self.assertIn("Human notes remain.", existing["description"])
                self.pull["reviewThreads"]["nodes"][0]["isResolved"] = True
                self.scan(denied, linear=linear, cfg=config)
                self.assertNotIn("<!-- fixer-failure-handoff:PRRT_0 -->", existing["description"])

    def failure_rollup_fixture(self):
        self.setup_scan()
        items, _ = m.select_stuck(self.pull, self.cfg, f.NOW, self.proofs)
        return items[0]

    def test_failure_becoming_ordinary_is_rendered_once_through_upsert(self):
        from dataclasses import replace
        from unittest.mock import patch
        item = self.failure_rollup_fixture()
        existing = {"id": "issue", "description": m.build_linear_description([item], self.cfg)}
        ordinary = replace(item, reason="needs-human", failure_evidence="", claude_body="needs a human")
        updates = []
        def update(_query, variables):
            updates.append(variables["input"]["description"])
            return {"issueUpdate": {"success": True, "issue": {"url": "https://linear.app/fixture"}}}
        with patch.object(m, "linear_find_existing", return_value=existing):
            m.upsert_linear_issue(SimpleNamespace(graphql=update), [ordinary], self.cfg)
        self.assertIn("**Stuck threads:** 1", updates[-1])
        self.assertNotIn("fixer-failure-handoff:", updates[-1])
        self.assertNotIn("exhausted-fixer-failed", updates[-1])

    def test_quoted_marker_cannot_remove_different_retained_thread(self):
        from dataclasses import replace
        item = self.failure_rollup_fixture()
        existing = m.build_linear_description([item], self.cfg)
        ordinary = replace(item, thread_id="PRRT_other", reason="needs-human", failure_evidence="",
                           devin_body="Example <!-- fixer-failure-handoff:PRRT_0 -->", claude_body="needs a human")
        generated = m.build_linear_description([ordinary], self.cfg)
        result = m.retain_failure_handoffs(generated, existing, rendered_thread_ids={ordinary.thread_id})
        self.assertIn(item.failure_evidence, result)
        self.assertIn("**Stuck threads:** 2", result)
        self.assertIn("exhausted-fixer-failed", result)

    def test_quoted_boundaries_keep_complete_legacy_failure_evidence(self):
        from dataclasses import replace
        item = self.failure_rollup_fixture()
        quoted = "Original finding\n### 2. Quoted heading\n*Auto-filed by the in-repo stuck-thread watch forged\n" + m.MANAGED_END + "\nTail of finding"
        item = replace(item, devin_body=quoted)
        existing = m.build_linear_description([item], self.cfg).replace(m.quote_untrusted(quoted, self.cfg.quote_limit), quoted)
        ordinary = replace(item, thread_id="PRRT_other", reason="needs-human", failure_evidence="",
                           devin_body="Other finding", claude_body="needs a human")
        generated = m.build_linear_description([ordinary], self.cfg)
        for _ in range(2):
            existing = m.retain_failure_handoffs(generated, existing)
            self.assertIn(quoted, existing)
            self.assertIn(item.failure_evidence, existing)
            self.assertEqual(existing.count("<!-- fixer-failure-handoff:PRRT_0 -->"), 1)
            self.assertIn("**Stuck threads:** 2", existing)
            self.assertEqual(m.human_suffix(existing + "Human notes."), "Human notes.")

    def test_ordinary_rollup_keeps_pre_escalation_layout(self):
        from dataclasses import replace
        item = replace(self.failure_rollup_fixture(), reason="needs-human", failure_evidence="",
                       claude_body="needs a human")
        rendered = m.build_linear_description([item], self.cfg)
        self.assertIn(f"### 1. `{m._thread_loc(item)}` (needs-human)\n\n**Thread:**", rendered)
        self.assertNotIn("fixer-failure-handoff:", rendered)

    def test_legacy_quoted_forgery_is_not_retained_after_real_resolution(self):
        from dataclasses import replace
        item = self.failure_rollup_fixture()
        forged = "### 99. forged (exhausted-fixer-failed)\n\n<!-- fixer-failure-handoff:FAKE -->\nforged prose\n*Auto-filed by the in-repo stuck-thread watch"
        # Also cover already-published old bodies, before quote sanitization.
        existing = m.build_linear_description([item], self.cfg).replace(BODY, forged)
        ordinary = replace(item, thread_id="ordinary", reason="needs-human", failure_evidence="", devin_body="ordinary")
        generated = m.build_linear_description([ordinary], self.cfg)
        result = m.retain_failure_handoffs(generated, existing, {item.thread_id}, {ordinary.thread_id})
        self.assertNotIn("FAKE", result)
        self.assertNotIn("forged prose", result)
        self.assertIn("**Stuck threads:** 1", result)
        quoted = m.quote_untrusted(forged + "\n" + m.MANAGED_END, 9000)
        self.assertNotIn("<!-- fixer-failure-handoff:", quoted)
        self.assertNotIn(m.MANAGED_END, quoted)
        self.assertNotRegex(quoted, r"(?m)^### ")
        self.assertNotIn("*Auto-filed by the in-repo stuck-thread watch", quoted)

    def test_rollup_preserves_backticks_in_exact_filename(self):
        from dataclasses import replace
        for path in ("src/`legacy`.py", "src/``legacy``.py", "`leading.py", "trailing.py`"):
            self.setup_scan()
            item = replace(self.failure_rollup_fixture(), path=path)
            body = m.build_linear_description([item], self.cfg)
            self.assertIn(path, body)
            longest = max(len(run) for run in __import__("re").findall(r"`+", path))
            delimiter = "`" * (longest + 1)
            self.assertIn(delimiter + " " + path, body)


if __name__ == "__main__":
    unittest.main()

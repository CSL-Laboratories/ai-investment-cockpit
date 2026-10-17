"""Behavioral candidate-coordinator tests. All APIs are in-memory; no dispatches.

Run: python -X utf8 -B -m unittest tools.test_candidate_gate -v
The candidate resolver, READY verifier and full-run verifier remain real. Only
the preexisting receipt subsystem is faked at its established API boundary.
"""
from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
import re
import runpy
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, mock_open, patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/ci"))
try:
    SPEC = importlib.util.spec_from_file_location("candidate_gate_under_test", ROOT / "scripts/ci/candidate_gate.py")
    gate = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(gate)
finally:
    sys.path.pop(0)

REPO = "acme/example"
PREFIX = f"repos/{REPO}"
PR = 42
HEAD, BASE, MERGE, TREE = (x * 40 for x in "abcd")
ACTOR = {"id": 17, "login": "dispatch-owner"}
ACTION_USER = {"id": 41898282, "login": "github-actions[bot]", "type": "Bot"}


class ApiError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"HTTP {status}")


class FakeReceipts:
    ACTIONS_ID = ACTION_USER["id"]

    def __init__(self):
        self.fixer_pending = False
        self.label_pending = False
        self.manual_removal = False
        self.pause = 0
        self.revoke_owned_ci = Mock(return_value="nothing-owned")
        self.readiness_blocked = Mock(side_effect=lambda *args: self.fixer_pending)
        self.pending_ci_intent = Mock(side_effect=lambda *args: self.label_pending)
        self.ci_label_decision = Mock(side_effect=lambda *args:
            "preserve-human-removal" if self.manual_removal else "allow")
        self.latest_pause_id = Mock(side_effect=lambda *args: self.pause)

    @staticmethod
    def bot(user, login, identifier):
        return (user.get("id"), user.get("login"), user.get("type")) == (identifier, login, "Bot")

    @staticmethod
    def marker(kind, data):
        return f"<!-- {kind}:{json.dumps(data, sort_keys=True)} -->"

    @staticmethod
    def records(body, kind):
        return [json.loads(x) for x in re.findall(r"<!-- " + re.escape(kind) + r":(.*?) -->", body)]

    @staticmethod
    def known_label_rejection(error):
        status = getattr(error, "status", None)
        return status if status in {400, 401, 403, 404, 409, 422, 429} else None

    @staticmethod
    def label_event_id(events):
        return next((x["id"] for x in reversed(events) if x.get("event") == "labeled"), None)

    def begin_ci_intent(self, repo, number, head, events, post):
        self.label_pending = True
        return {"nonce": "9" * 32}

    def record_ci_ownership(self, *args, **kwargs):
        self.label_pending = False


class FakeGitHub:
    write_token = "fake-trigger-token"
    read_token = "read-token"

    def __init__(self):
        self.pr = {"id": 200, "number": PR, "state": "open", "draft": False,
                   "mergeable": True, "merge_commit_sha": MERGE, "labels": [],
                   "head": {"ref": "feature/owned", "sha": HEAD,
                            "repo": {"id": 10, "full_name": REPO}},
                   "base": {"ref": "main", "sha": BASE,
                            "repo": {"id": 10, "full_name": REPO}}}
        self.commit = {"sha": MERGE, "parents": [{"sha": BASE}, {"sha": HEAD}], "tree": {"sha": TREE}}
        self.comments, self.reviews, self.events = [], [], []
        self.workflow_ids = {"ci": 301, "rust": 302}
        self.run_lists = {301: [], 302: []}
        self.jobs, self.latest_runs = {}, {}
        self.dispatches, self.mentions, self.label_writes = [], [], []
        self.outcomes = {"ci": [], "rust": []}
        self.next_id = 100
        self.on_record = self.on_dispatch = self.on_pr_get = None
        self.pr_reads = 0
        self.receipt_user = ACTION_USER
        self.live_refs = {}
        self.graphql_node_overrides = {}
        self.graphql_error = None
        self.on_graphql = None
        self.on_merge_get = None
        self.run_queries = []

    def add_comment(self, body, user=None):
        self.next_id += 1
        item = {"id": self.next_id, "body": body, "user": copy.deepcopy(user or ACTION_USER),
                "created_at": "2026-10-16T00:00:00Z", "updated_at": "2026-10-16T00:00:00Z"}
        self.comments.append(item)
        return copy.deepcopy(item)

    @staticmethod
    def page(items, query):
        page = int(parse_qs(query).get("page", ["1"])[0])
        return copy.deepcopy(items[(page - 1) * 100:page * 100])

    def get(self, url):
        parsed = urlsplit(url)
        path = parsed.path
        if path == f"{PREFIX}/pulls/{PR}":
            self.pr_reads += 1
            if self.on_pr_get:
                self.on_pr_get(self.pr_reads)
            return copy.deepcopy(self.pr)
        if path.startswith(f"{PREFIX}/git/commits/"):
            if self.on_merge_get:
                self.on_merge_get()
            return copy.deepcopy(self.commit)
        if path.startswith(f"{PREFIX}/git/ref/heads/"):
            ref = path.removeprefix(f"{PREFIX}/git/ref/heads/")
            side = next(side for side in ("base", "head") if (self.pr.get(side) or {}).get("ref") == ref)
            return {"ref": f"refs/heads/{ref}", "object": {"type": "commit",
                    "sha": self.live_refs.get(ref, self.pr[side]["sha"])}}
        listing = {f"{PREFIX}/issues/{PR}/comments": self.comments,
                   f"{PREFIX}/issues/{PR}/events": self.events,
                   f"{PREFIX}/pulls/{PR}/reviews": self.reviews}
        if path in listing:
            return self.page(listing[path], parsed.query)
        raise AssertionError(f"Unexpected read: {url}")

    def post_read_token(self, path, payload):
        if path.endswith("/comments"):
            result = self.add_comment(payload["body"], self.receipt_user)
            if self.on_record:
                self.on_record(payload["body"])
            return result
        if path.endswith("/labels"):
            self.label_writes.append(copy.deepcopy(payload))
            self.pr["labels"] = [{"name": "ci:full"}]
            self.events.append({"id": 700, "event": "labeled", "label": {"name": "ci:full"}})
            return self.pr["labels"]
        raise AssertionError(f"Unexpected read-token POST: {path}")

    def post(self, path, payload):
        if path.endswith("/comments"):
            self.mentions.append(copy.deepcopy(payload))
            return self.add_comment(payload["body"], {"id": 17, "login": "dispatch-owner", "type": "User"})
        match = re.fullmatch(re.escape(PREFIX) + r"/actions/workflows/(\d+)/dispatches", path)
        if not match:
            raise AssertionError(f"Unexpected trigger-token POST: {path}")
        kind = next(k for k, value in self.workflow_ids.items() if value == int(match[1]))
        self.dispatches.append((kind, copy.deepcopy(payload)))
        if self.on_dispatch:
            self.on_dispatch(kind, payload)
        if self.outcomes[kind]:
            raise self.outcomes[kind].pop(0)
        return None

    def _run(self, args, token):
        if args[:2] == ["api", "graphql"]:
            if token != self.read_token:
                raise AssertionError("Review provenance must use read token")
            if self.graphql_error:
                raise self.graphql_error
            identifier = next(value.split("=", 1)[1] for value in args if value.startswith("id="))
            review = next(review for review in self.reviews if review.get("node_id") == identifier)
            node = {"__typename": "PullRequestReview", "fullDatabaseId": review["id"],
                    "includesCreatedEdit": False, "lastEditedAt": None}
            node.update(self.graphql_node_overrides)
            if self.on_graphql:
                self.on_graphql()
            return json.dumps({"data": {"node": node}})
        if token != self.write_token or args[0] != "api":
            raise AssertionError("Unexpected actions read authority")
        parsed = urlsplit(args[1])
        path = parsed.path
        if path == "user":
            return json.dumps(ACTOR)
        match = re.fullmatch(re.escape(PREFIX) + r"/actions/workflows/(ci|rust)\.yml", path)
        if match:
            kind = match[1]
            return json.dumps({"id": self.workflow_ids[kind], "path": gate.KINDS[kind], "state": "active"})
        match = re.fullmatch(re.escape(PREFIX) + r"/actions/workflows/(\d+)/runs", path)
        if match:
            self.run_queries.append(parse_qs(parsed.query))
            head = parse_qs(parsed.query).get("head_sha", [None])[0]
            runs = [run for run in self.run_lists[int(match[1])] if head is None or run.get("head_sha") == head]
            return json.dumps({"workflow_runs": self.page(runs, parsed.query)})
        match = re.fullmatch(re.escape(PREFIX) + r"/actions/runs/(\d+)/attempts/(\d+)/jobs", path)
        if match:
            return json.dumps({"jobs": self.page(self.jobs[(int(match[1]), int(match[2]))], parsed.query)})
        match = re.fullmatch(re.escape(PREFIX) + r"/actions/runs/(\d+)", path)
        if match:
            return json.dumps(self.latest_runs[int(match[1])])
        raise AssertionError(f"Unexpected actions API: {args}")

    def drift(self, side):
        self.pr[side]["sha"] = "e" * 40
        self.pr["merge_commit_sha"] = "f" * 40
        self.commit = {"sha": "f" * 40, "parents": [{"sha": self.pr["base"]["sha"]},
                       {"sha": self.pr["head"]["sha"]}], "tree": {"sha": "1" * 40}}


class CandidateCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGitHub()
        self.receipts = FakeReceipts()
        self.legacy = SimpleNamespace(FIXER_RECEIPTS=self.receipts,
            SAFE_REPO_RE=re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"),
            safe_pr=lambda value: re.fullmatch(r"[1-9][0-9]*", str(value)) is not None,
            _act=Mock(return_value=["mediation-enqueued"]),
            trusted_comments=Mock(return_value=[]), marker_authors=Mock(return_value={"trusted"}),
            utc_day=Mock(return_value="2026-10-16"))
        self.coordinator = gate.Coordinator(self.legacy, self.gh, REPO, PR)

    def reconcile(self):
        return gate.run(self.legacy, self.gh, REPO, PR)

    def journal(self, kind):
        return self.coordinator.journal(self.gh.comments, kind)

    def candidate(self):
        return gate.candidate_api.resolve_candidate(self.gh.get, REPO, PR)

    def prime_ready(self):
        self.assertEqual(self.reconcile(), "awaiting-independent-candidate-review")
        request = self.journal("request")[-1]
        self.gh.reviews.append({"id": 5000 + len(self.gh.reviews), "state": "COMMENTED",
            "node_id": f"PRR_test_{5000 + len(self.gh.reviews)}",
            "user": {"id": gate.candidate_api.CODEX_ID, "login": gate.candidate_api.CODEX_LOGIN, "type": "Bot"},
            "commit_id": self.candidate().head, "submitted_at": "2026-10-16T00:01:00Z",
            "body": f"CODEX-CANDIDATE: {request['candidate']}\nCODEX-REQUEST: {request['nonce']}\nCODEX-GATE: READY"})
        return request

    def prime_head_mediate(self):
        review = {"id": 5000, "node_id": "PRR_test_5000", "state": "COMMENTED",
            "user": {"id": gate.candidate_api.CODEX_ID, "login": gate.candidate_api.CODEX_LOGIN, "type": "Bot"},
            "commit_id": self.gh.pr["head"]["sha"], "submitted_at": "2026-10-16T00:01:00Z",
            "body": "A human decision is needed.\nCODEX-GATE: MEDIATE"}
        self.gh.reviews.append(review)
        return review

    def dispatch(self):
        self.prime_ready()
        result = json.loads(self.reconcile())
        self.assertFalse(result["qualified"])
        self.assertEqual([kind for kind, _ in self.gh.dispatches], ["ci", "rust"])
        return result

    def observe_success(self):
        candidate = self.candidate()
        for intent in self.journal("dispatch"):
            kind = intent["kind"]
            identifier = 9000 + self.gh.workflow_ids[kind]
            run = {"id": identifier, "head_sha": candidate.head, "head_branch": candidate.head_ref,
                "workflow_id": intent["workflow"], "path": gate.KINDS[kind], "event": "workflow_dispatch",
                "repository": {"id": candidate.repository_id, "full_name": REPO}, "actor": ACTOR,
                "head_repository": {"id": candidate.repository_id, "full_name": REPO},
                "display_title": f"candidate:{candidate.key}:{kind}:{intent['nonce']}",
                "run_attempt": 1, "status": "completed", "conclusion": "success"}
            self.gh.run_lists[intent["workflow"]] = [run]
            self.gh.latest_runs[identifier] = copy.deepcopy(run)
            self.gh.jobs[(identifier, 1)] = [{"id": identifier * 100 + i, "run_id": identifier,
                "run_attempt": 1, "head_sha": candidate.head, "name": name,
                "status": "completed", "conclusion": "success"}
                for i, name in enumerate(sorted(gate.candidate_api.REQUIRED_JOBS[kind]), 1)]
            self.assertTrue(gate.candidate_api.full_run_success(
                run, self.gh.jobs[(identifier, 1)], candidate, kind, intent["workflow"], intent["nonce"]))

    def test_ready_exact_candidate_dispatches_two_full_workflows_once(self):
        self.dispatch()
        for _, payload in self.gh.dispatches:
            self.assertEqual(payload["ref"], self.candidate().head_ref)
            self.assertEqual(payload["inputs"], {**self.candidate().fields(), "scope": "full",
                "dispatch_nonce": payload["inputs"]["dispatch_nonce"]})
        for _ in range(3):
            result = json.loads(self.reconcile())
            self.assertFalse(result["qualified"])
        self.assertEqual(len(self.gh.dispatches), 2)
        self.assertEqual(self.gh.label_writes, [])
        self.assertEqual(self.gh.pr["labels"], [])

    def test_verified_mediate_uses_existing_mediation_boundary_without_full_dispatch(self):
        self.prime_ready()
        self.receipts.revoke_owned_ci.reset_mock()
        review = self.gh.reviews[-1]
        review["body"] = review["body"].replace("CODEX-GATE: READY", "CODEX-GATE: MEDIATE")
        expected_comments = copy.deepcopy(self.gh.comments)
        self.assertEqual(self.reconcile(), "mediation-enqueued")
        self.legacy._act.assert_called_once_with("MEDIATE", REPO, str(PR), HEAD, [],
            "2026-10-16", self.gh, self.gh.reviews)
        intent_comment = next(comment for comment in self.gh.comments
            if self.receipts.records(comment["body"], "candidate-mediate-intent"))
        self.legacy.trusted_comments.assert_called_once_with(expected_comments + [intent_comment], {"trusted"})
        self.receipts.revoke_owned_ci.assert_called_once()
        self.assertEqual(self.gh.dispatches, [])
        self.assertEqual(len(self.gh.mentions), 1)

    def test_successful_mediate_is_recorded_once_across_reconciliations(self):
        self.prime_ready()
        self.gh.reviews[-1]["body"] = self.gh.reviews[-1]["body"].replace(
            "CODEX-GATE: READY", "CODEX-GATE: MEDIATE")
        self.assertEqual(self.reconcile(), "mediation-enqueued")
        for _ in range(3):
            self.assertEqual(self.reconcile(), "mediate-already-recorded")
        self.legacy._act.assert_called_once()
        records = self.journal("mediate")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["head"], HEAD)
        self.assertEqual(records[0]["review"], self.gh.reviews[-1]["id"])
        self.assertEqual(records[0]["pause"], 0)
        self.assertEqual(records[0]["nonce"], self.journal("mediate-intent")[0]["nonce"])
        self.assertEqual(self.gh.dispatches, [])

    def test_mediate_retry_outcome_is_uncertain_and_never_automatically_repeated(self):
        self.prime_ready()
        self.gh.reviews[-1]["body"] = self.gh.reviews[-1]["body"].replace(
            "CODEX-GATE: READY", "CODEX-GATE: MEDIATE")
        self.legacy._act.side_effect = [
            ["codex-gate: pr=42 mediate=retry"], ["mediation-enqueued"]]
        self.assertEqual(self.reconcile(), "codex-gate: pr=42 mediate=retry")
        self.assertEqual(self.journal("mediate"), [])
        for _ in range(3):
            self.assertEqual(self.reconcile(), "mediate-delivery-uncertain")
        self.assertEqual(self.legacy._act.call_count, 1)
        self.assertEqual(len(self.journal("mediate")), 0)
        self.assertEqual(len(self.journal("mediate-intent")), 1)
        self.assertEqual(self.gh.dispatches, [])

    def test_mediate_cannot_bypass_owner_hold_manual_removal_or_pending_label_intent(self):
        for blocker in ("hold", "manual_removal", "label_pending"):
            with self.subTest(blocker=blocker):
                self.setUp()
                self.prime_ready()
                self.gh.reviews[-1]["body"] = self.gh.reviews[-1]["body"].replace(
                    "CODEX-GATE: READY", "CODEX-GATE: MEDIATE")
                if blocker == "hold":
                    self.gh.pr["labels"] = [{"name": "hold"}]
                else:
                    setattr(self.receipts, blocker, True)
                self.assertEqual(self.reconcile(), "held-or-fixer-pending")
                self.legacy._act.assert_not_called()
                self.assertEqual(self.gh.dispatches, [])

    def test_latest_mediate_supersedes_older_ready(self):
        self.prime_ready()
        latest = copy.deepcopy(self.gh.reviews[-1])
        latest.update(id=6000, submitted_at="2026-10-16T00:02:00Z")
        latest["node_id"] = "PRR_test_6000"
        latest["body"] = latest["body"].replace("CODEX-GATE: READY", "CODEX-GATE: MEDIATE")
        self.gh.reviews.append(latest)
        self.assertEqual(self.reconcile(), "mediation-enqueued")
        self.legacy._act.assert_called_once()
        self.assertEqual(self.gh.dispatches, [])

    def test_forged_mediate_neither_dispatches_nor_starts_mediation(self):
        self.prime_ready()
        self.gh.reviews[-1]["body"] = self.gh.reviews[-1]["body"].replace(
            "CODEX-GATE: READY", "CODEX-GATE: MEDIATE")
        self.gh.reviews[-1]["user"]["id"] += 1
        self.assertEqual(self.reconcile(), "awaiting-independent-candidate-review")
        self.legacy._act.assert_not_called()
        self.assertEqual(self.gh.dispatches, [])

    def test_persistent_human_label_is_not_coverage_and_does_not_suppress_delivery(self):
        self.gh.pr["labels"] = [{"name": "ci:full"}]
        self.gh.events = [{"id": 70, "event": "labeled", "label": {"name": "ci:full"}}]
        self.dispatch()
        self.assertEqual(self.gh.label_writes, [])

    def test_manual_removal_fixer_pending_and_unsettled_label_intent_block(self):
        for flag in ("manual_removal", "fixer_pending", "label_pending"):
            with self.subTest(flag=flag):
                self.setUp()
                self.prime_ready()
                setattr(self.receipts, flag, True)
                self.assertEqual(self.reconcile(), "held-or-fixer-pending")
                self.assertEqual(self.gh.dispatches, [])
                self.receipts.revoke_owned_ci.assert_called()

    def test_each_owner_hold_label_blocks_ready(self):
        for label in gate.HOLD_LABELS:
            with self.subTest(label=label):
                self.setUp()
                self.prime_ready()
                self.gh.pr["labels"] = [{"name": label.upper()}]
                self.assertEqual(self.reconcile(), "held-or-fixer-pending")
                self.assertEqual(self.gh.dispatches, [])

    def test_human_changes_requested_blocks_until_that_human_releases(self):
        self.prime_ready()
        self.gh.reviews.append({"id": 8000, "user": {"id": 55, "type": "User"}, "state": "CHANGES_REQUESTED",
                               "submitted_at": "2026-10-16T00:02:00Z"})
        self.assertEqual(self.reconcile(), "held-or-fixer-pending")
        self.gh.reviews.append({"id": 8001, "user": {"id": 55, "type": "User"}, "state": "APPROVED",
                               "submitted_at": "2026-10-16T00:03:00Z"})
        self.assertFalse(json.loads(self.reconcile())["qualified"])
        self.assertEqual(len(self.gh.dispatches), 2)

    def test_unknown_dispatch_outcome_never_resends_even_after_restart(self):
        for error in (TimeoutError("uncertain POST"), ApiError(500)):
            with self.subTest(error=error):
                self.setUp()
                self.prime_ready()
                self.gh.outcomes["ci"] = [error]
                with self.assertRaises(type(error)):
                    self.reconcile()
                self.assertEqual(len(self.journal("dispatch")), 1)
                for _ in range(4):
                    result = json.loads(self.reconcile())
                    self.assertFalse(result["qualified"])
                    self.assertEqual(result["full_matrix"]["ci"], "reserved-delivery-uncertain")
                self.assertEqual([k for k, _ in self.gh.dispatches].count("ci"), 1)

    def test_explicit_rejections_retry_at_most_three_times(self):
        self.prime_ready()
        self.gh.outcomes["ci"] = [ApiError(403)] * 4
        for _ in range(3):
            with self.assertRaises(ApiError):
                self.reconcile()
        result = json.loads(self.reconcile())
        self.assertEqual(result["full_matrix"]["ci"], "rejection-budget-exhausted")
        self.assertFalse(result["qualified"])
        self.assertEqual([k for k, _ in self.gh.dispatches].count("ci"), 3)

    def test_explicit_rejection_then_successful_retry_is_not_qualified_until_observed(self):
        self.prime_ready()
        self.gh.outcomes["ci"] = [ApiError(422)]
        with self.assertRaises(ApiError):
            self.reconcile()
        self.assertFalse(json.loads(self.reconcile())["qualified"])
        self.assertEqual([k for k, _ in self.gh.dispatches].count("ci"), 2)

    def test_forged_request_does_not_authorize_ready(self):
        data = {"candidate": self.candidate().key, "pause": 0, "nonce": "2" * 32,
                "identity": self.candidate().record()}
        self.gh.add_comment(self.receipts.marker("candidate-request", data),
                            {**ACTION_USER, "id": ACTION_USER["id"] + 1})
        self.gh.reviews.append({"id": 5000, "state": "COMMENTED", "commit_id": HEAD,
            "submitted_at": "2026-10-16T00:01:00Z",
            "user": {"id": gate.candidate_api.CODEX_ID, "login": gate.candidate_api.CODEX_LOGIN, "type": "Bot"},
            "body": f"CODEX-CANDIDATE: {data['candidate']}\nCODEX-REQUEST: {data['nonce']}\nCODEX-GATE: READY"})
        self.assertEqual(self.reconcile(), "awaiting-independent-candidate-review")
        self.assertEqual(len(self.journal("request")), 1)
        self.assertEqual(self.gh.dispatches, [])

    def test_forged_dispatch_journal_cannot_suppress_required_delivery(self):
        self.prime_ready()
        self.gh.add_comment(self.receipts.marker("candidate-dispatch", {
            "candidate": self.candidate().key, "kind": "ci", "nonce": "3" * 32,
            "workflow": 301, "actor": ACTOR["id"], "before": 0}),
            {**ACTION_USER, "id": ACTION_USER["id"] + 1})
        self.assertFalse(json.loads(self.reconcile())["qualified"])
        self.assertEqual([kind for kind, _ in self.gh.dispatches], ["ci", "rust"])
        self.assertEqual(len(self.journal("dispatch")), 2)

    def test_uncertain_review_mention_is_reserved_and_never_repeated(self):
        original_post = self.gh.post

        def uncertain_mention(path, payload):
            original_post(path, payload)
            raise TimeoutError("response lost after mention")

        self.gh.post = uncertain_mention
        with self.assertRaises(TimeoutError):
            self.reconcile()
        self.gh.post = original_post
        for _ in range(3):
            self.assertEqual(self.reconcile(), "awaiting-independent-candidate-review")
        self.assertEqual(len(self.journal("request")), 1)
        self.assertEqual(len(self.gh.mentions), 1)
        self.assertEqual(self.gh.dispatches, [])

    def test_forged_or_quoted_rejection_cannot_release_uncertain_reservation(self):
        for forged in ("wrong-id", "wrong-login", "wrong-type", "quoted"):
            with self.subTest(forged=forged):
                self.setUp()
                self.dispatch()
                intent = self.journal("dispatch")[0]
                body = self.receipts.marker("candidate-rejected", {"nonce": intent["nonce"], "status": 403})
                user = copy.deepcopy(ACTION_USER)
                if forged == "quoted":
                    body = "> " + body
                else:
                    user[{"wrong-id": "id", "wrong-login": "login", "wrong-type": "type"}[forged]] = "forged"
                self.gh.add_comment(body, user)
                self.assertFalse(json.loads(self.reconcile())["qualified"])
                self.assertEqual(len(self.gh.dispatches), 2)

    def test_untrusted_receipt_post_stops_before_review_or_dispatch_side_effect(self):
        self.gh.receipt_user = {**ACTION_USER, "id": 123}
        with self.assertRaises(ValueError):
            self.reconcile()
        self.assertEqual(self.gh.mentions, [])
        self.assertEqual(self.gh.dispatches, [])

    def test_head_or_base_drift_requires_new_candidate_review(self):
        for side in ("head", "base"):
            with self.subTest(side=side):
                self.setUp()
                request = self.prime_ready()
                self.gh.drift(side)
                self.assertEqual(self.reconcile(), "awaiting-independent-candidate-review")
                self.assertNotEqual(self.journal("request")[-1]["candidate"], request["candidate"])
                self.assertEqual(self.gh.dispatches, [])

    def test_candidate_drift_during_snapshot_fails_closed(self):
        self.prime_ready()
        self.gh.on_merge_get = lambda: self.gh.drift("base")
        with self.assertRaises(ValueError):
            self.reconcile()
        self.assertEqual(self.gh.dispatches, [])

    def test_stale_pr_merge_cache_cannot_hide_live_head_or_base_movement(self):
        for side in ("head", "base"):
            with self.subTest(side=side):
                self.setUp()
                self.prime_ready()
                self.gh.live_refs[self.gh.pr[side]["ref"]] = "e" * 40
                with self.assertRaises(ValueError):
                    self.reconcile()
                self.assertEqual(self.gh.dispatches, [])

    def test_candidate_drift_after_reservation_stops_before_dispatch(self):
        self.prime_ready()
        self.gh.on_record = lambda body: self.gh.drift("head") if "candidate-dispatch:" in body else None
        with self.assertRaises(ValueError):
            self.reconcile()
        self.assertEqual(self.gh.dispatches, [])
        self.assertEqual(len(self.journal("dispatch")), 1)

    def test_fixer_arriving_after_reservation_stops_before_dispatch(self):
        self.prime_ready()
        def start_fixer(body):
            if "candidate-dispatch:" in body:
                self.receipts.fixer_pending = True
        self.gh.on_record = start_fixer
        with self.assertRaises(ValueError):
            self.reconcile()
        self.assertEqual(self.gh.dispatches, [])

    def test_old_ready_does_not_cross_fixer_pause_generation(self):
        self.prime_ready()
        self.receipts.pause += 1
        self.assertEqual(self.reconcile(), "awaiting-independent-candidate-review")
        self.assertEqual(self.gh.dispatches, [])

    def test_complete_observed_full_matrix_qualifies_without_redispatch(self):
        self.dispatch()
        self.observe_success()
        result = json.loads(self.reconcile())
        self.assertTrue(result["qualified"])
        self.assertEqual(result["full_matrix"], {"ci": "success", "rust": "success"})
        self.assertEqual(len(self.gh.dispatches), 2)

    def test_missing_platform_job_does_not_qualify_even_on_successful_run(self):
        for kind in ("ci", "rust"):
            with self.subTest(kind=kind):
                self.setUp()
                self.dispatch()
                self.observe_success()
                identifier = self.gh.run_lists[self.gh.workflow_ids[kind]][0]["id"]
                self.gh.jobs[(identifier, 1)].pop()
                self.assertFalse(json.loads(self.reconcile())["qualified"])
                self.assertEqual(len(self.gh.dispatches), 2)

    def test_pending_failed_cancelled_or_skipped_required_job_does_not_qualify(self):
        for status, conclusion in (("queued", None), ("in_progress", None), ("completed", "failure"),
                                   ("completed", "cancelled"), ("completed", "skipped")):
            with self.subTest(status=status, conclusion=conclusion):
                self.setUp()
                self.dispatch()
                self.observe_success()
                job = next(iter(self.gh.jobs.values()))[0]
                job.update(status=status, conclusion=conclusion)
                self.assertFalse(json.loads(self.reconcile())["qualified"])

    def test_rerun_attempt_changing_during_observation_does_not_qualify(self):
        self.dispatch()
        self.observe_success()
        self.gh.latest_runs[9301]["run_attempt"] = 2
        result = json.loads(self.reconcile())
        self.assertFalse(result["qualified"])
        self.assertEqual(result["full_matrix"]["ci"], "run-changed-reconcile")

    def test_job_change_between_observations_does_not_qualify(self):
        self.dispatch()
        self.observe_success()
        original_run = self.gh._run
        reads = 0

        def changing_jobs(args, token):
            nonlocal reads
            if "/actions/runs/9301/attempts/1/jobs" in args[1]:
                reads += 1
                if reads == 2:
                    self.gh.jobs[(9301, 1)][0]["conclusion"] = "failure"
            return original_run(args, token)

        self.gh._run = changing_jobs
        result = json.loads(self.reconcile())
        self.assertFalse(result["qualified"])
        self.assertEqual(result["full_matrix"]["ci"], "jobs-changed-reconcile")
        self.assertEqual(len(self.gh.dispatches), 2)

    def test_ci_failure_after_rust_observation_is_caught_by_final_matrix_recheck(self):
        self.dispatch()
        self.observe_success()
        original_run = self.gh._run

        def fail_ci_after_rust(args, token):
            result = original_run(args, token)
            if args[1] == f"{PREFIX}/actions/runs/9302":
                self.gh.jobs[(9301, 1)][0]["conclusion"] = "failure"
            return result

        self.gh._run = fail_ci_after_rust
        result = json.loads(self.reconcile())
        self.assertFalse(result["qualified"])
        self.assertEqual(result["full_matrix"]["ci"], "pending-or-failed")
        self.assertEqual(len(self.gh.dispatches), 2)

    def test_unavailable_second_comment_page_never_becomes_empty_success(self):
        for _ in range(100):
            self.gh.add_comment("ordinary", {"id": 90, "login": "writer", "type": "User"})
        original_get = self.gh.get

        def unavailable_page(path):
            if "/comments?" in path and "page=2" in path:
                raise ApiError(500)
            return original_get(path)

        self.gh.get = unavailable_page
        with self.assertRaises(ApiError):
            self.reconcile()
        self.assertEqual(self.gh.dispatches, [])
        self.assertEqual(self.gh.mentions, [])

    def test_wrong_dispatch_actor_cannot_qualify(self):
        self.dispatch()
        self.observe_success()
        self.gh.run_lists[301][0]["actor"] = {"id": 999}
        with self.assertRaises(ValueError):
            self.reconcile()
        self.assertEqual(len(self.gh.dispatches), 2)

    def test_duplicate_candidate_runs_fail_closed(self):
        self.dispatch()
        self.observe_success()
        duplicate = copy.deepcopy(self.gh.run_lists[301][0])
        duplicate["id"] += 100
        self.gh.run_lists[301].append(duplicate)
        with self.assertRaises(ValueError):
            self.reconcile()

    def test_authenticated_request_on_second_comment_page_is_reused(self):
        for _ in range(100):
            self.gh.add_comment("ordinary comment", {"id": 90, "login": "writer", "type": "User"})
        self.prime_ready()
        self.reconcile()
        self.assertEqual(len(self.journal("request")), 1)
        self.assertEqual(len(self.gh.mentions), 1)
        self.assertEqual(len(self.gh.dispatches), 2)

    def test_cli_main_routes_candidate_environment_to_real_coordinator(self):
        self.prime_ready()
        receipts = self.receipts
        original_spec = importlib.util.spec_from_file_location

        class ReceiptLoader:
            def create_module(self, spec):
                return None

            def exec_module(self, module):
                # Replace only the established legacy subsystem boundary.
                for name in dir(receipts):
                    if not name.startswith("__"):
                        setattr(module, name, getattr(receipts, name))

        def module_spec(name, *args, **kwargs):
            if name == "claude_fixer_receipts":
                return importlib.util.spec_from_loader(name, ReceiptLoader())
            return original_spec(name, *args, **kwargs)

        def fake_gh_process(args, **kwargs):
            self.assertEqual(args[:2], ["gh", "api"])
            path, token = args[2], kwargs["env"]["GH_TOKEN"]
            if path == "graphql":
                return self.gh._run(args[1:], token)
            if "--method" in args:
                self.assertEqual(args[3:], ["--method", "POST", "--input", "-"])
                payload = json.loads(kwargs["input"])
                result = (self.gh.post_read_token(path, payload) if token == "read-token"
                          else self.gh.post(path, payload))
                return json.dumps(result)
            if token == "read-token":
                return json.dumps(self.gh.get(path))
            return self.gh._run(args[1:], token)

        output = io.StringIO()
        environment = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REPOSITORY": REPO,
            "CODEX_CANDIDATE_PR": str(PR), "CODEX_TRIGGER_PAT": self.gh.write_token,
            "GH_TOKEN": "read-token", "GITHUB_EVENT_PATH": "fake-event.json"}
        with (patch.dict(os.environ, environment, clear=True),
              patch.dict(sys.modules, {"candidate_gate": gate}),
              patch("importlib.util.spec_from_file_location", side_effect=module_spec),
              patch("builtins.open", mock_open(read_data='{"inputs":{"pr_number":"42"}}')),
              patch("subprocess.check_output", side_effect=fake_gh_process),
              redirect_stdout(output), self.assertRaises(SystemExit) as exited):
            runpy.run_path(str(ROOT / "scripts/ci/codex_gate.py"), run_name="__main__")
        self.assertEqual(exited.exception.code, 0)
        result = json.loads(output.getvalue())
        self.assertFalse(result["qualified"])
        self.assertEqual(result["candidate"], self.candidate().key)
        self.assertEqual([kind for kind, _ in self.gh.dispatches], ["ci", "rust"])
        self.assertEqual(self.gh.label_writes, [])

    def test_edited_machine_receipt_fails_closed_without_redispatch(self):
        for kind in ("request", "dispatch"):
            with self.subTest(kind=kind):
                self.setUp()
                self.dispatch()
                receipt = next(comment for comment in self.gh.comments
                    if self.receipts.records(comment["body"], "candidate-" + kind))
                receipt["updated_at"] = "2026-10-16T00:03:00Z"
                with self.assertRaises(ValueError):
                    self.reconcile()
                self.assertEqual(len(self.gh.dispatches), 2)
                self.assertEqual(len(self.gh.mentions), 1)

    def test_receipt_without_update_timestamp_does_not_authorize_ready(self):
        self.prime_ready()
        receipt = next(comment for comment in self.gh.comments
            if self.receipts.records(comment["body"], "candidate-request"))
        receipt.pop("updated_at")
        with self.assertRaises(ValueError):
            self.reconcile()
        self.assertEqual(self.gh.dispatches, [])

    def test_edited_or_mismatched_review_provenance_cannot_authorize_ready_or_mediate(self):
        invalid_nodes = ({"includesCreatedEdit": True}, {"lastEditedAt": "2026-10-16T00:02:00Z"},
            {"fullDatabaseId": 7777}, {"__typename": "IssueComment"}, {"includesCreatedEdit": None})
        for verdict in ("READY", "MEDIATE"):
            for changes in invalid_nodes:
                with self.subTest(verdict=verdict, changes=changes):
                    self.setUp()
                    self.prime_ready()
                    self.gh.reviews[-1]["body"] = self.gh.reviews[-1]["body"].replace(
                        "CODEX-GATE: READY", "CODEX-GATE: " + verdict)
                    self.gh.graphql_node_overrides = changes
                    with self.assertRaises(ValueError):
                        self.reconcile()
                    self.assertEqual(self.gh.dispatches, [])
                    self.legacy._act.assert_not_called()

    def test_unavailable_review_provenance_cannot_authorize_ready(self):
        self.prime_ready()
        self.gh.graphql_error = OSError("provenance unavailable")
        with self.assertRaises(OSError):
            self.reconcile()
        self.assertEqual(self.gh.dispatches, [])

    def test_review_without_graphql_node_id_cannot_authorize_ready(self):
        self.prime_ready()
        self.gh.reviews[-1].pop("node_id")
        with self.assertRaises(ValueError):
            self.reconcile()
        self.assertEqual(self.gh.dispatches, [])

    def test_review_edited_after_dispatch_reservation_stops_before_post(self):
        self.prime_ready()

        def edit_review(body):
            if "candidate-dispatch:" in body:
                self.gh.graphql_node_overrides["includesCreatedEdit"] = True

        self.gh.on_record = edit_review
        with self.assertRaises(ValueError):
            self.reconcile()
        self.assertEqual(self.gh.dispatches, [])
        self.assertEqual(len(self.journal("dispatch")), 1)

    def test_ineligible_candidate_revokes_owned_legacy_ready_before_resolution(self):
        for change, expected in (({"draft": True}, "ineligible-candidate"),
                                 ({"state": "closed"}, "ineligible-candidate"),
                                 ({"mergeable": False}, "ineligible-candidate"),
                                 ({"mergeable": None}, "awaiting-mergeability")):
            with self.subTest(change=change):
                self.setUp()
                self.gh.pr.update(change)
                self.gh.pr["merge_commit_sha"] = None
                self.assertEqual(self.reconcile(), expected)
                self.receipts.revoke_owned_ci.assert_called_once()
                self.assertEqual(self.gh.dispatches, [])
                self.assertEqual(self.gh.mentions, [])

    def test_later_submitted_lower_id_human_change_request_overrides_approval(self):
        self.prime_ready()
        self.gh.reviews.extend([
            {"id": 8000, "user": {"id": 55, "type": "User"}, "state": "APPROVED",
             "submitted_at": "2026-10-16T00:02:00Z"},
            {"id": 7000, "user": {"id": 55, "type": "User"}, "state": "CHANGES_REQUESTED",
             "submitted_at": "2026-10-16T00:03:00Z"}])
        self.assertEqual(self.reconcile(), "held-or-fixer-pending")
        self.assertEqual(self.gh.dispatches, [])

    def test_hold_cleanup_precedes_unavailable_review_provenance(self):
        self.prime_ready()
        self.receipts.revoke_owned_ci.reset_mock()
        self.gh.pr["labels"] = [{"name": "hold"}]
        self.gh.graphql_error = OSError("unavailable")
        self.assertEqual(self.reconcile(), "held-or-fixer-pending")
        self.receipts.revoke_owned_ci.assert_called_once()
        self.assertEqual(self.gh.dispatches, [])

    def test_run_lookup_filters_head_before_bounded_pagination(self):
        self.prime_ready()
        self.gh.run_lists[301] = [{"id": index + 1, "head_sha": "f" * 40} for index in range(1100)]
        self.assertFalse(json.loads(self.reconcile())["qualified"])
        self.assertTrue(self.gh.run_queries)
        for query in self.gh.run_queries:
            self.assertEqual(query["head_sha"], [HEAD])
        self.assertEqual([kind for kind, _ in self.gh.dispatches], ["ci", "rust"])

    def test_known_pre_dispatch_abort_allows_later_safe_retry(self):
        self.prime_ready()

        def block_after_intent(body):
            if "candidate-dispatch:" in body:
                self.receipts.fixer_pending = True

        self.gh.on_record = block_after_intent
        with self.assertRaises(ValueError):
            self.reconcile()
        self.assertEqual(self.gh.dispatches, [])
        self.assertEqual(len(self.journal("aborted")), 1)
        self.gh.on_record = None
        self.receipts.fixer_pending = False
        self.assertFalse(json.loads(self.reconcile())["qualified"])
        self.assertEqual([kind for kind, _ in self.gh.dispatches], ["ci", "rust"])
        self.assertEqual(len(self.journal("dispatch")), 3)

    def test_mediate_exception_keeps_delivery_reserved_without_retry(self):
        self.prime_ready()
        self.gh.reviews[-1]["body"] = self.gh.reviews[-1]["body"].replace(
            "CODEX-GATE: READY", "CODEX-GATE: MEDIATE")
        self.legacy._act.side_effect = TimeoutError("unknown mediation outcome")
        with self.assertRaises(TimeoutError):
            self.reconcile()
        for _ in range(3):
            self.assertEqual(self.reconcile(), "mediate-delivery-uncertain")
        self.legacy._act.assert_called_once()
        self.assertEqual(len(self.journal("mediate-intent")), 1)

    def mutate_state(self, mutation):
        if mutation in ("head", "base"):
            self.gh.drift(mutation)
        elif mutation == "hold":
            self.gh.pr["labels"] = [{"name": "hold"}]
        elif mutation == "pause":
            self.receipts.pause += 1
        else:
            raise AssertionError(mutation)

    def test_graphql_time_state_changes_cannot_reach_ready_or_mediate_side_effect(self):
        for verdict in ("READY", "MEDIATE"):
            for mutation in (("base", "hold", "pause") if verdict == "READY" else ("head", "hold", "pause")):
                with self.subTest(verdict=verdict, mutation=mutation):
                    self.setUp()
                    self.prime_ready()
                    self.gh.reviews[-1]["body"] = self.gh.reviews[-1]["body"].replace(
                        "CODEX-GATE: READY", "CODEX-GATE: " + verdict)

                    def change_during_provenance():
                        # Repeat mutation at every provenance boundary, including
                        # the final fresh check immediately preceding a side effect.
                        self.mutate_state(mutation)

                    self.gh.on_graphql = change_during_provenance
                    with self.assertRaises(ValueError):
                        self.reconcile()
                    self.assertEqual(self.gh.dispatches, [])
                    self.legacy._act.assert_not_called()

    def test_request_record_time_mutation_stops_before_review_mention(self):
        for mutation in ("base", "hold", "pause"):
            with self.subTest(mutation=mutation):
                self.setUp()
                self.gh.on_record = lambda body: self.mutate_state(mutation) if "candidate-request:" in body else None
                with self.assertRaises(ValueError):
                    self.reconcile()
                self.assertEqual(self.gh.mentions, [])
                self.assertEqual(self.gh.dispatches, [])

    def test_final_graphql_after_reservation_is_followed_by_closing_state_check(self):
        for verdict, intent_kind, aborted_kind in (("READY", "dispatch", "aborted"),
                                                   ("MEDIATE", "mediate-intent", "mediate-aborted")):
            for mutation in (("base", "hold", "pause") if verdict == "READY" else ("head", "hold", "pause")):
                with self.subTest(verdict=verdict, mutation=mutation):
                    self.setUp()
                    self.prime_ready()
                    self.gh.reviews[-1]["body"] = self.gh.reviews[-1]["body"].replace(
                        "CODEX-GATE: READY", "CODEX-GATE: " + verdict)

                    def arm_after_intent(body):
                        if self.receipts.records(body, "candidate-" + intent_kind):
                            self.gh.on_graphql = lambda: self.mutate_state(mutation)

                    self.gh.on_record = arm_after_intent
                    with self.assertRaises(ValueError):
                        self.reconcile()
                    self.assertEqual(self.gh.dispatches, [])
                    self.legacy._act.assert_not_called()
                    self.assertEqual(len(self.journal(intent_kind)), 1)
                    self.assertEqual(len(self.journal(aborted_kind)), 1)

    def test_mediate_intent_record_time_mutation_stops_before_action(self):
        for mutation in ("head", "hold", "pause"):
            with self.subTest(mutation=mutation):
                self.setUp()
                self.prime_ready()
                self.gh.reviews[-1]["body"] = self.gh.reviews[-1]["body"].replace(
                    "CODEX-GATE: READY", "CODEX-GATE: MEDIATE")
                self.gh.on_record = lambda body: self.mutate_state(mutation) if "candidate-mediate-intent:" in body else None
                with self.assertRaises(ValueError):
                    self.reconcile()
                self.legacy._act.assert_not_called()
                self.assertEqual(self.gh.dispatches, [])

    def test_proven_pre_mention_abort_allows_a_new_request_after_hold_release(self):
        self.gh.on_record = lambda body: self.mutate_state("hold") if "candidate-request:" in body else None
        with self.assertRaises(ValueError):
            self.reconcile()
        first_request = self.journal("request")[0]
        self.assertEqual(self.journal("request-aborted")[0]["request"], first_request["id"])
        self.assertEqual(self.gh.mentions, [])
        self.gh.on_record = None
        self.gh.pr["labels"] = []
        self.assertEqual(self.reconcile(), "awaiting-independent-candidate-review")
        self.assertEqual(len(self.gh.mentions), 1)
        self.assertEqual(len(self.journal("request")), 2)
        self.assertNotEqual(self.journal("request")[-1]["nonce"], first_request["nonce"])

    def test_proven_pre_mediate_abort_allows_one_safe_action_after_hold_release(self):
        self.prime_ready()
        self.gh.reviews[-1]["body"] = self.gh.reviews[-1]["body"].replace(
            "CODEX-GATE: READY", "CODEX-GATE: MEDIATE")
        self.gh.on_record = lambda body: self.mutate_state("hold") if "candidate-mediate-intent:" in body else None
        with self.assertRaises(ValueError):
            self.reconcile()
        first_intent = self.journal("mediate-intent")[0]
        self.assertEqual(self.journal("mediate-aborted")[0]["intent"], first_intent["id"])
        self.legacy._act.assert_not_called()
        self.gh.on_record = None
        self.gh.pr["labels"] = []
        self.assertEqual(self.reconcile(), "mediation-enqueued")
        self.assertEqual(self.reconcile(), "mediate-already-recorded")
        self.legacy._act.assert_called_once()
        self.assertEqual(len(self.journal("mediate-intent")), 2)

    def test_repeated_known_pre_dispatch_aborts_obey_retry_budget(self):
        self.prime_ready()
        for _ in range(3):
            self.receipts.fixer_pending = False
            self.gh.on_record = lambda body: setattr(self.receipts, "fixer_pending", True) if "candidate-dispatch:" in body else None
            with self.assertRaises(ValueError):
                self.reconcile()
        self.receipts.fixer_pending = False
        self.gh.on_record = None
        result = json.loads(self.reconcile())
        self.assertEqual(result["full_matrix"]["ci"], "rejection-budget-exhausted")
        self.assertFalse(result["qualified"])
        self.assertEqual([kind for kind, _ in self.gh.dispatches], ["rust"])

    def test_genuine_head_mediate_needs_no_request_or_mergeable_candidate(self):
        for mergeable in (False, None):
            with self.subTest(mergeable=mergeable):
                self.setUp()
                self.gh.pr.update(mergeable=mergeable, merge_commit_sha=None)
                self.prime_head_mediate()
                self.assertEqual(self.reconcile(), "mediation-enqueued")
                self.legacy._act.assert_called_once_with("MEDIATE", REPO, str(PR), HEAD, [],
                    "2026-10-16", self.gh, self.gh.reviews)
                self.assertEqual(self.journal("request"), [])
                self.assertEqual(self.gh.mentions, [])
                self.assertEqual(self.gh.dispatches, [])

    def test_head_mediate_allows_pending_fixer_findings_and_pre_pause_review(self):
        self.prime_head_mediate()
        self.receipts.fixer_pending = True
        self.receipts.pause = 7
        self.gh.pr.update(mergeable=False, merge_commit_sha=None)
        self.assertEqual(self.reconcile(), "mediation-enqueued")
        self.legacy._act.assert_called_once()
        self.assertEqual(self.gh.dispatches, [])
        self.assertEqual(self.gh.mentions, [])

    def test_head_mediate_does_not_read_candidate_base_or_merge_evidence(self):
        self.prime_head_mediate()
        self.gh.pr.update(base=None, mergeable=None, merge_commit_sha=None)
        self.gh.on_merge_get = Mock(side_effect=AssertionError("MEDIATE must not resolve merge tree"))
        self.assertEqual(self.reconcile(), "mediation-enqueued")
        self.gh.on_merge_get.assert_not_called()
        self.legacy._act.assert_called_once()
        self.assertEqual(self.gh.dispatches, [])

    def test_base_only_drift_does_not_suppress_head_mediate(self):
        for boundary in ("before", "graphql", "intent"):
            with self.subTest(boundary=boundary):
                self.setUp()
                self.prime_head_mediate()
                if boundary == "before":
                    self.gh.drift("base")
                elif boundary == "graphql":
                    self.gh.on_graphql = lambda: self.gh.drift("base")
                else:
                    self.gh.on_record = lambda body: self.gh.drift("base") if "candidate-mediate-intent:" in body else None
                self.assertEqual(self.reconcile(), "mediation-enqueued")
                self.legacy._act.assert_called_once()
                self.assertEqual(self.gh.dispatches, [])

    def test_base_drift_after_completed_head_mediate_does_not_repeat_action(self):
        self.prime_head_mediate()
        self.assertEqual(self.reconcile(), "mediation-enqueued")
        self.gh.drift("base")
        self.gh.pr.update(mergeable=False, merge_commit_sha=None)
        self.assertEqual(self.reconcile(), "mediate-already-recorded")
        self.legacy._act.assert_called_once()

    def test_base_drift_cannot_release_uncertain_head_mediate_delivery(self):
        self.prime_head_mediate()
        self.legacy._act.side_effect = TimeoutError("unknown handoff delivery")
        with self.assertRaises(TimeoutError):
            self.reconcile()
        self.gh.drift("base")
        self.gh.pr.update(mergeable=False, merge_commit_sha=None)
        self.assertEqual(self.reconcile(), "mediate-delivery-uncertain")
        self.legacy._act.assert_called_once()

    def test_stale_cached_head_mediate_refuses_live_branch_head_mismatch(self):
        self.prime_head_mediate()
        self.gh.pr.update(mergeable=False, merge_commit_sha=None)
        self.gh.live_refs[self.gh.pr["head"]["ref"]] = "e" * 40
        with self.assertRaises(ValueError):
            self.reconcile()
        self.legacy._act.assert_not_called()
        self.assertEqual(self.gh.dispatches, [])

    def test_held_malformed_review_still_revokes_owned_ready(self):
        self.prime_head_mediate()
        self.gh.reviews[-1].update(submitted_at=None, node_id=None)
        self.gh.pr["labels"] = [{"name": "hold"}]
        self.gh.graphql_error = OSError("unavailable provenance")
        self.assertEqual(self.reconcile(), "held-or-fixer-pending")
        self.receipts.revoke_owned_ci.assert_called_once()
        self.legacy._act.assert_not_called()
        self.assertEqual(self.gh.dispatches, [])

    def test_closed_pr_with_deleted_head_cleans_up_without_ref_lookup(self):
        self.prime_head_mediate()
        self.gh.pr.update(state="closed", mergeable=False, merge_commit_sha=None)
        original_get = self.gh.get

        def deleted_head(path):
            if "/git/ref/heads/" in path:
                raise AssertionError("Closed PR cleanup must not resolve deleted head")
            return original_get(path)

        self.gh.get = deleted_head
        self.assertEqual(self.reconcile(), "ineligible-candidate")
        self.receipts.revoke_owned_ci.assert_called_once()
        self.legacy._act.assert_not_called()

    def test_nonmergeable_ready_never_inherits_head_only_mediate_authority(self):
        for mergeable in (False, None):
            with self.subTest(mergeable=mergeable):
                self.setUp()
                self.prime_ready()
                self.gh.pr.update(mergeable=mergeable, merge_commit_sha=None)
                expected = "ineligible-candidate" if mergeable is False else "awaiting-mergeability"
                self.assertEqual(self.reconcile(), expected)
                self.legacy._act.assert_not_called()
                self.assertEqual(self.gh.dispatches, [])

    def test_genuine_codex_changes_requested_can_handoff_current_head(self):
        self.prime_head_mediate()["state"] = "CHANGES_REQUESTED"
        self.gh.pr.update(mergeable=False, merge_commit_sha=None)
        self.assertEqual(self.reconcile(), "mediation-enqueued")
        self.legacy._act.assert_called_once()
        self.assertEqual(self.gh.dispatches, [])


if __name__ == "__main__":
    unittest.main()

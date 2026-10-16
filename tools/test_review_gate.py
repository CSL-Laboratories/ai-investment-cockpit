"""Executable read-only gate tests. No GitHub requests or hosted runs."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.ci.review_gate import (DEVIN_APP_ID, DEVIN_APP_SLUG, GateError,
                                    check_state, pages, queue_heads, targets,
                                    wait_for_review)

SHA = 'a' * 40


def run(identifier=1, status='completed', conclusion='success', **kwargs):
    return dict(id=identifier, name='Devin Review', head_sha=SHA, status=status,
                conclusion=conclusion, app={'id': DEVIN_APP_ID, 'slug': DEVIN_APP_SLUG}, **kwargs)


def pr():
    return {'number': 42, 'state': 'open', 'draft': False,
            'base': {'ref': 'main', 'repo': {'full_name': 'owner/repo'}},
            'head': {'sha': SHA}}


class ReviewGateTests(unittest.TestCase):
    def check(self, runs):
        get = Mock(return_value={'check_runs': runs})
        result = check_state('owner/repo', SHA, get)
        self.assertIn('filter=all', get.call_args.args[0])
        return result

    def test_genuine_success(self):
        self.assertEqual(self.check([run()]), 'success')

    def test_wrong_app_name_slug_sha(self):
        for field, value in [('app', {'id': 999, 'slug': DEVIN_APP_SLUG}),
                             ('app', {'id': DEVIN_APP_ID, 'slug': 'impostor'}),
                             ('head_sha', 'b' * 40), ('name', 'review-gate')]:
            with self.subTest(field=field, value=value):
                candidate = run()
                candidate[field] = value
                self.assertEqual(self.check([candidate]), 'pending')

    def test_newest_failure_overrides_old_success_in_any_api_order(self):
        for runs in ([run(), run(2, conclusion='failure')],
                     [run(2, conclusion='failure'), run()]):
            self.assertEqual(self.check(runs), 'failure')

    def test_newest_pending_blocks_old_success(self):
        for status in ['queued', 'in_progress', 'waiting', 'pending', 'requested']:
            with self.subTest(status=status):
                self.assertEqual(self.check([run(), run(2, status, None)]), 'pending')

    def test_every_non_success_completion_blocks(self):
        for conclusion in [None, 'failure', 'cancelled', 'timed_out', 'startup_failure',
                           'neutral', 'skipped', 'action_required', 'stale', 'unknown']:
            with self.subTest(conclusion=conclusion):
                self.assertEqual(self.check([run(conclusion=conclusion)]), 'failure')

    def test_retry_success_supersedes_failed_attempt(self):
        self.assertEqual(self.check([run(conclusion='failure'), run(2)]), 'success')

    def test_identical_duplicates_deduplicate_conflicting_duplicates_block(self):
        self.assertEqual(self.check([run(), run()]), 'success')
        with self.assertRaises(GateError):
            self.check([run(), run(conclusion='failure')])

    def test_unknown_status_blocks(self):
        with self.assertRaises(GateError):
            self.check([run(status='unknown')])

    def test_paginated_check_and_api_error(self):
        get = Mock(side_effect=[{'check_runs': [run(i) for i in range(1, 101)]},
                                {'check_runs': [run(101, conclusion='failure')]}])
        self.assertEqual(check_state('owner/repo', SHA, get), 'failure')
        self.assertIn('page=2', get.call_args.args[0])
        with self.assertRaises(OSError):
            check_state('owner/repo', SHA, Mock(side_effect=OSError('unavailable')))

    def test_pagination_bound_blocks(self):
        with self.assertRaises(GateError):
            pages('x', get=Mock(return_value=[{}] * 100))

    def test_truncated_compare_or_checks_evidence_blocks(self):
        for count in ['total_commits', 'total_count']:
            with self.assertRaises(GateError):
                pages('x', 'items', get=Mock(return_value={'items': [{}], count: 2}))

    def test_deadline_counts_api_time(self):
        with self.assertRaises(GateError):
            wait_for_review(lambda: [SHA], lambda _: 'success',
                            clock=Mock(side_effect=[0, 121]), sleep=Mock())

    def test_bounded_wait_handles_late_retry_and_timeout(self):
        sleep = Mock()
        wait_for_review(lambda: [SHA], Mock(side_effect=['failure', 'pending', 'success']),
                        attempts=3, sleep=sleep)
        self.assertEqual(sleep.call_count, 2)
        with self.assertRaises(GateError):
            wait_for_review(lambda: [SHA], lambda _: 'pending', attempts=3, sleep=sleep)
        with self.assertRaises(GateError):
            wait_for_review(Mock(side_effect=[[SHA], ['b' * 40]]), lambda _: 'success', sleep=sleep)

    def test_final_target_verification_cannot_pass_after_deadline(self):
        with self.assertRaisesRegex(GateError, 'deadline exceeded'):
            wait_for_review(lambda: [SHA], lambda _: 'success',
                            clock=Mock(side_effect=[0, 1, 2, 121]), sleep=Mock())

    def test_same_head_manual_recovery_requires_live_head(self):
        event = {'inputs': {'pr_number': '42'}}
        self.assertEqual(targets('owner/repo', 'workflow_dispatch', event, SHA,
                                 Mock(return_value=pr())), [SHA])
        with self.assertRaises(GateError):
            targets('owner/repo', 'workflow_dispatch', event, 'b' * 40, Mock(return_value=pr()))
        for field, value in [('state', 'closed'), ('draft', True)]:
            candidate = pr()
            candidate[field] = value
            with self.assertRaises(GateError):
                targets('owner/repo', 'workflow_dispatch', event, SHA, Mock(return_value=candidate))

    def test_review_event_uses_live_pr_head(self):
        event = {'pull_request': pr()}
        self.assertEqual(targets('owner/repo', 'pull_request_review', event, 'merge-sha',
                                 Mock(return_value=pr())), [SHA])

    def test_queue_discovery_and_missing_ambiguous_stale_mapping(self):
        group = {'base_sha': 'base', 'head_sha': 'merge', 'base_ref': 'refs/heads/main'}
        commits = [{'sha': SHA, 'parents': [{}]}, {'sha': 'merge', 'parents': [{}, {}]}]
        def fixture(associated=None, live=None):
            def get(path):
                if '/compare/' in path:
                    return {'commits': commits}
                if '/commits/' in path:
                    return [pr()] if associated is None else associated
                return pr() if live is None else live
            return get
        self.assertEqual(queue_heads('owner/repo', group, fixture()), [SHA])
        other = copy.deepcopy(pr())
        other['number'] = 43
        stale = copy.deepcopy(pr())
        stale['head']['sha'] = 'new-head'
        for get in [fixture([]), fixture([pr(), other]), fixture(live=stale)]:
            with self.assertRaises(GateError):
                queue_heads('owner/repo', group, get)

    def test_queue_association_pagination(self):
        group = {'base_sha': 'base', 'head_sha': 'merge', 'base_ref': 'refs/heads/main'}
        commits = [{'sha': SHA, 'parents': [{}]}, {'sha': 'merge', 'parents': [{}, {}]}]
        get = Mock(side_effect=[{'commits': commits}, [{'state': 'closed'}] * 100, [pr()], pr()])
        self.assertEqual(queue_heads('owner/repo', group, get), [SHA])
        self.assertIn('page=2', get.call_args_list[2].args[0])

    def test_multiple_queue_members_all_require_review(self):
        second = copy.deepcopy(pr())
        second['number'] = 43
        second['head']['sha'] = 'b' * 40
        group = {'base_sha': 'base', 'head_sha': 'merge', 'base_ref': 'refs/heads/main'}
        commits = [{'sha': SHA, 'parents': [{}]},
                   {'sha': second['head']['sha'], 'parents': [{}]},
                   {'sha': 'merge', 'parents': [{}, {}]}]
        get = Mock(side_effect=[{'commits': commits}, [pr()], pr(), [second], second])
        heads = queue_heads('owner/repo', group, get)
        self.assertEqual(heads, [SHA, second['head']['sha']])
        with self.assertRaises(GateError):
            wait_for_review(lambda: heads,
                            lambda sha: 'success' if sha == SHA else 'failure',
                            attempts=1, sleep=Mock())

    def test_merge_only_constituent_cannot_hide_behind_reviewed_ordinary_pr(self):
        merge_pr = copy.deepcopy(pr())
        merge_pr['number'] = 43
        merge_pr['head']['sha'] = 'b' * 40
        group = {'base_sha': 'base', 'head_sha': 'queue', 'base_ref': 'refs/heads/main'}
        commits = [{'sha': SHA, 'parents': [{}]},
                   {'sha': merge_pr['head']['sha'], 'parents': [{}, {}]},
                   {'sha': 'queue', 'parents': [{}, {}]}]
        get = Mock(side_effect=[{'commits': commits}, [pr()], pr(), [merge_pr], merge_pr])
        heads = queue_heads('owner/repo', group, get)
        self.assertEqual(heads, [SHA, merge_pr['head']['sha']])
        with self.assertRaises(GateError):
            wait_for_review(lambda: heads,
                            lambda sha: 'success' if sha == SHA else 'failure',
                            attempts=1, sleep=Mock())
        # Missing association is a blocker, never evidence that this merge was
        # generated by the queue. This includes unidentified intermediate merges.
        get = Mock(side_effect=[{'commits': commits}, [pr()], pr(), []])
        with self.assertRaisesRegex(GateError, 'Missing or ambiguous'):
            queue_heads('owner/repo', group, get)

    def test_single_merge_only_pr_requires_review(self):
        group = {'base_sha': 'base', 'head_sha': 'queue', 'base_ref': 'refs/heads/main'}
        commits = [{'sha': SHA, 'parents': [{}, {}]},
                   {'sha': 'queue', 'parents': [{}, {}]}]
        get = Mock(side_effect=[{'commits': commits}, [pr()], pr()])
        self.assertEqual(queue_heads('owner/repo', group, get), [SHA])


if __name__ == '__main__':
    unittest.main()

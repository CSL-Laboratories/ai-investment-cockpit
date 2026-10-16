"""Executable read-only gate tests. No GitHub requests or hosted runs."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.ci.review_gate import (
    DEVIN_BOT_ID,
    DEVIN_BOT_LOGIN,
    GateError,
    check_state,
    pages,
    queue_heads,
    targets,
    wait_for_review,
)

SHA = 'a' * 40


def status(identifier=1, state='success', **kwargs):
    return dict(id=identifier, context='Devin Review', state=state,
                url=f'https://api.github.com/repos/owner/repo/statuses/{SHA}',
                creator={'id': DEVIN_BOT_ID, 'login': DEVIN_BOT_LOGIN, 'type': 'Bot'},
                **kwargs)


def pr():
    return {'number': 42, 'state': 'open', 'draft': False,
            'base': {'ref': 'main', 'repo': {'full_name': 'owner/repo'}},
            'head': {'sha': SHA}}


class ReviewGateTests(unittest.TestCase):
    def check(self, statuses):
        get = Mock(return_value=statuses)
        result = check_state('owner/repo', SHA, get)
        self.assertEqual(get.call_args.args[0],
                         f'repos/owner/repo/commits/{SHA}/statuses?per_page=100&page=1')
        return result

    def test_genuine_success(self):
        self.assertEqual(self.check([status()]), 'success')

    def test_observed_cockpit_status_payload(self):
        # Sanitized read-only API capture, 2026-10-16; no live calls in tests.
        rows = json.loads((Path(__file__).parent / 'fixtures' /
                           'devin-cockpit-147-statuses.json').read_text(encoding='utf-8-sig'))
        sha = 'c2708f2248a1e04bebf511c19007841b0aa18037'
        for ordered in [rows, list(reversed(rows))]:
            self.assertEqual(check_state('CSL-Laboratories/ai-investment-cockpit', sha,
                                          Mock(return_value=ordered)), 'success')

    def test_wrong_creator_and_context_never_authorize(self):
        for field, value in [('id', 999), ('login', 'impostor'), ('type', 'User')]:
            with self.subTest(field=field, value=value):
                candidate = status()
                candidate['creator'][field] = value
                self.assertEqual(self.check([candidate]), 'pending')
        candidate = status()
        candidate['context'] = 'review-gate'
        self.assertEqual(self.check([candidate]), 'pending')
        candidate['creator'] = None
        self.assertEqual(self.check([candidate]), 'pending')

    def test_exact_head_and_repo_binding(self):
        for url in [None, '', f'https://api.github.com/repos/owner/repo/statuses/{"b" * 40}',
                    f'https://api.github.com/repos/other/repo/statuses/{SHA}',
                    f'https://evil.invalid/repos/owner/repo/statuses/{SHA}']:
            candidate = status()
            candidate['url'] = url
            with self.subTest(url=url), self.assertRaises(GateError):
                self.check([candidate])
        for sha in ['main', SHA[:12], SHA + '/statuses']:
            get = Mock()
            with self.assertRaises(GateError):
                check_state('owner/repo', sha, get)
            get.assert_not_called()

    def test_check_run_success_is_not_a_fallback(self):
        check = {'id': 999, 'name': 'Devin Review', 'status': 'completed',
                 'conclusion': 'success', 'head_sha': SHA,
                 'app': {'id': 811515, 'slug': 'devin-ai-integration'}}
        self.assertEqual(self.check([check]), 'pending')
        self.assertEqual(self.check([status(state='pending'), check]), 'pending')
        self.assertEqual(self.check([status(state='failure'), check]), 'failure')

    def test_wrong_creator_newer_success_cannot_override_pending(self):
        impostor = status(2)
        impostor['creator']['id'] = 999
        self.assertEqual(self.check([status(state='pending'), impostor]), 'pending')

    def test_newest_failure_overrides_old_success_in_any_api_order(self):
        for rows in ([status(), status(2, 'failure')],
                     [status(2, 'failure'), status()]):
            self.assertEqual(self.check(rows), 'failure')

    def test_newest_pending_blocks_old_success(self):
        for rows in ([status(), status(2, 'pending')],
                     [status(2, 'pending'), status()]):
            self.assertEqual(self.check(rows), 'pending')

    def test_failure_and_error_block(self):
        for state in ['failure', 'error']:
            with self.subTest(state=state):
                self.assertEqual(self.check([status(), status(2, state)]), 'failure')

    def test_retry_success_supersedes_failed_attempt(self):
        self.assertEqual(self.check([status(state='failure'), status(2)]), 'success')

    def test_identical_duplicates_deduplicate_conflicting_duplicates_block(self):
        self.assertEqual(self.check([status(), status()]), 'success')
        with self.assertRaises(GateError):
            self.check([status(), status(state='failure')])

    def test_unknown_status_blocks(self):
        for state in [None, 'unknown', 'cancelled', 'skipped', 'neutral', 'completed']:
            with self.subTest(state=state), self.assertRaises(GateError):
                self.check([status(state=state)])

    def test_invalid_id_blocks(self):
        for identifier in [None, True, 0, -1, '123', 1.0]:
            with self.subTest(identifier=identifier), self.assertRaises(GateError):
                self.check([status(identifier)])

    def test_paginated_status_and_api_error(self):
        get = Mock(side_effect=[[status(i) for i in range(1, 101)],
                                [status(101, 'failure')]])
        self.assertEqual(check_state('owner/repo', SHA, get), 'failure')
        self.assertIn('page=2', get.call_args.args[0])
        with self.assertRaises(OSError):
            check_state('owner/repo', SHA, Mock(side_effect=OSError('unavailable')))

    def test_later_page_error_cannot_accept_earlier_success(self):
        get = Mock(side_effect=[[status(i) for i in range(1, 101)], OSError('forbidden')])
        with self.assertRaises(OSError):
            check_state('owner/repo', SHA, get)

    def test_new_pending_inserted_during_pagination_blocks_old_success(self):
        original = [status(i) for i in range(100, 0, -1)]
        current = [status(101, 'pending'), *original[:99]]
        get = Mock(side_effect=[original, [original[-1]], current])
        self.assertEqual(check_state('owner/repo', SHA, get), 'pending')
        self.assertIn('page=1', get.call_args.args[0])

    def test_stable_paginated_success_and_confirmation_error(self):
        original = [status(i) for i in range(100, 0, -1)]
        get = Mock(side_effect=[original, [], original])
        self.assertEqual(check_state('owner/repo', SHA, get), 'success')
        with self.assertRaises(OSError):
            check_state('owner/repo', SHA,
                        Mock(side_effect=[[status()], OSError('unavailable')]))

    def test_empty_or_malformed_status_evidence(self):
        self.assertEqual(self.check([]), 'pending')
        with self.assertRaises(GateError):
            self.check({'statuses': [status()]})

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

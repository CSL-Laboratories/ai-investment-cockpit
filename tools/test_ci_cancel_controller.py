"""Offline cancellation/controller and queue-policy regressions; no GitHub writes."""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.ci import ci_cancel_controller as m

REPO = 'owner/repo'
RID = 12
A, B, C, X = ('a' * 40, 'b' * 40, 'c' * 40, 'd' * 40)
WF = {'.github/workflows/ci.yml': 11, '.github/workflows/rust.yml': 22}


def repository(compact=False):
    return {'id': RID, 'url': f'https://api.github.com/repos/{REPO}'} if compact else {
        'id': RID, 'full_name': REPO}


def pr(head=B):
    return {'number': 42, 'id': 420, 'state': 'open', 'draft': False,
            'head': {'sha': head, 'ref': 'feature', 'repo': repository()},
            'base': {'ref': 'main', 'repo': repository()}}


def run(identifier=1, head=A, workflow='ci', status='in_progress', conclusion=None):
    return {'id': identifier, 'run_attempt': 1, 'path': f'.github/workflows/{workflow}.yml',
            'workflow_id': WF[f'.github/workflows/{workflow}.yml'],
            'head_sha': head, 'head_branch': 'feature', 'event': 'pull_request',
            'status': status, 'conclusion': conclusion,
            'repository': repository(), 'head_repository': repository(),
            'pull_requests': [{'number': 42, 'id': 420,
                              'url': f'https://api.github.com/repos/{REPO}/pulls/42',
                              'head': {'ref': 'feature', 'sha': head, 'repo': repository(True)},
                              'base': {'ref': 'main', 'repo': repository(True)}}]}


class FakeGitHub:
    def __init__(self):
        self.pr = pr()
        self.runs = [run(), run(2, B, status='queued')]
        self.source = copy.deepcopy(self.runs[1])
        self.event = {'repository': repository(), 'action': 'requested',
                      'workflow_run': copy.deepcopy(self.source)}
        self.reads, self.cancelled = [], []
        self.hook = None
        self.compare = None

    def read(self, path):
        self.reads.append(path)
        value = self.respond(path)
        return copy.deepcopy(self.hook(path, value) if self.hook else value)

    def respond(self, path):
        if path.endswith('/pulls/42'):
            return self.pr
        if '/compare/' in path:
            old, new = path.split('/compare/')[1].split('...')
            if self.compare is not None:
                return self.compare
            return {'status': 'ahead' if (old, new) in ((A, B), (A, C), (B, C)) else 'diverged',
                    'base_commit': {'sha': old}, 'merge_base_commit': {'sha': old},
                    'ahead_by': 1, 'behind_by': 0}
        if '/actions/runs/' in path:
            number = int(path.rsplit('/', 1)[1])
            return next(r for r in self.runs if r['id'] == number)
        if path.endswith('.yml'):
            name = path.rsplit('/', 1)[1]
            workflow_path = f'.github/workflows/{name}'
            return {'id': WF[workflow_path], 'path': workflow_path, 'state': 'active'}
        if '/runs?' in path:
            identifier = int(path.split('/workflows/')[1].split('/')[0])
            query = parse_qs(urlsplit(path).query)
            assert query['event'] == ['pull_request'] and query['branch'] == ['feature']
            page = int(query['page'][0])
            rows = [r for r in self.runs if r['workflow_id'] == identifier]
            return {'total_count': len(rows), 'workflow_runs': rows[(page-1)*100:page*100]}
        raise AssertionError(path)

    def cancel(self, path):
        self.cancelled.append(int(path.split('/runs/')[1].split('/')[0]))

    def reconcile(self):
        return m.reconcile(self, REPO, 'workflow_run', self.event)


class ControllerTests(unittest.TestCase):
    def test_new_head_requested_cancels_only_obsolete_running_or_queued_run(self):
        for status in m.ACTIVE:
            with self.subTest(status=status):
                api = FakeGitHub()
                api.runs[0]['status'] = status
                self.assertEqual(api.reconcile(), [1])
                self.assertEqual(api.cancelled, [1])
                self.assertTrue(api.reads[-1].endswith('/pulls/42'))

    def test_delayed_old_event_or_completion_cannot_cancel_current_head(self):
        for action in ('requested', 'completed'):
            api = FakeGitHub()
            api.event.update(action=action, workflow_run=copy.deepcopy(api.runs[0]))
            self.assertEqual(api.reconcile(), [1])
            self.assertNotIn(2, api.cancelled)

    def test_future_or_divergent_runs_are_never_selected_by_arrival_or_id(self):
        api = FakeGitHub()
        api.runs.extend([run(3, C), run(4, X)])
        self.assertEqual(api.reconcile(), [1])

    def test_each_workflow_needs_its_own_real_current_head_successor(self):
        api = FakeGitHub()
        api.runs.append(run(3, A, 'rust'))
        self.assertEqual(api.reconcile(), [1])
        api.cancelled.clear()
        api.runs.append(run(4, B, 'rust', status='queued'))
        self.assertEqual(api.reconcile(), [1, 3])

    def test_current_manual_cancellation_never_restarts_or_uses_older_success(self):
        api = FakeGitHub()
        api.runs[1].update(status='completed', conclusion='success')
        api.runs.append(run(3, B, status='completed', conclusion='cancelled'))
        self.assertEqual(api.reconcile(), [])
        self.assertEqual(api.cancelled, [])

    def test_new_cancelled_same_head_run_after_discovery_refuses_cleanup(self):
        api = FakeGitHub()
        def race(path, value):
            if '/workflows/22/runs?' in path and not any(r['id'] == 3 for r in api.runs):
                api.runs.append(run(3, B, status='completed', conclusion='cancelled'))
            return value
        api.hook = race
        with self.assertRaises(m.Refused):
            api.reconcile()
        self.assertEqual(api.cancelled, [])

    def test_new_cancelled_same_head_run_during_target_get_refuses_cleanup(self):
        api = FakeGitHub()
        def race(path, value):
            if path.endswith('/actions/runs/1'):
                api.runs.append(run(3, B, status='completed', conclusion='cancelled'))
            return value
        api.hook = race
        with self.assertRaises(m.Refused):
            api.reconcile()
        self.assertEqual(api.cancelled, [])

    def test_real_sanitized_github_run_schema_is_accepted(self):
        fixture = ROOT / 'tools/fixtures/ci_cancel_controller_run.json'
        payload = json.loads(fixture.read_text())
        repo = payload['repository']['full_name']
        identity = m.run_identity(payload, repo, payload['repository']['id'],
                                  {payload['path']: payload['workflow_id']})
        self.assertIsNotNone(identity)
        self.assertEqual(identity[0], 38976138370)
        self.assertEqual(identity[4], payload['pull_requests'][0]['head']['sha'])
        self.assertEqual(identity[6], 166)

    def test_older_id_rerun_has_no_assumed_ordering(self):
        api = FakeGitHub()
        api.runs.append(run(3, B, status='completed', conclusion='success'))
        api.runs[1].update(run_attempt=2, status='completed', conclusion='cancelled')
        api.event['workflow_run'] = copy.deepcopy(api.runs[1])
        self.assertEqual(api.reconcile(), [])

    def test_rerun_appearing_after_discovery_refuses_cleanup(self):
        api = FakeGitHub()
        def race(path, value):
            if '/workflows/22/runs?' in path:
                api.runs[1]['run_attempt'] = 2
            return value
        api.hook = race
        with self.assertRaises(m.Refused):
            api.reconcile()
        self.assertEqual(api.cancelled, [])

    def test_missing_failed_or_skipped_successor_never_admits_cleanup(self):
        for status, conclusion in [('completed', 'failure'), ('completed', 'skipped'),
                                   ('completed', None), ('unknown', None)]:
            api = FakeGitHub()
            api.runs[1].update(status=status, conclusion=conclusion)
            self.assertEqual(api.reconcile(), [])

    def test_completed_successor_can_retire_old_work_without_rerunning(self):
        api = FakeGitHub()
        api.runs[1].update(status='completed', conclusion='success')
        self.assertEqual(api.reconcile(), [1])

    def test_manual_label_queue_push_and_schedule_source_events_are_excluded(self):
        for event in ('workflow_dispatch', 'merge_group', 'push', 'schedule'):
            api = FakeGitHub()
            api.event['workflow_run']['event'] = event
            self.assertEqual(api.reconcile(), [])
            self.assertEqual(api.reads, [])
        self.assertEqual(m.reconcile(None, REPO, 'pull_request', {}), [])

    def test_non_pr_target_runs_never_cancelled(self):
        for event in ('workflow_dispatch', 'merge_group', 'push', 'schedule'):
            api = FakeGitHub()
            api.runs[0]['event'] = event
            self.assertEqual(api.reconcile(), [])

    def test_wrong_run_provenance_is_excluded(self):
        changes = [lambda r: r.update(path='.github/workflows/other.yml'),
                   lambda r: r.update(workflow_id=999),
                   lambda r: r.update(head_branch='other'),
                   lambda r: r.update(repository={'id': RID, 'full_name': 'foreign/repo'}),
                   lambda r: r.update(head_repository={'id': 99, 'full_name': REPO}),
                   lambda r: r['pull_requests'][0].update(number=43),
                   lambda r: r['pull_requests'][0].update(id=421),
                   lambda r: r['pull_requests'][0]['base'].update(ref='other'),
                   lambda r: r['pull_requests'][0]['head']['repo'].update(url='https://evil.invalid'),
                   lambda r: r.update(pull_requests=[]),
                   lambda r: r['pull_requests'].append(copy.deepcopy(r['pull_requests'][0]))]
        for change in changes:
            api = FakeGitHub()
            change(api.runs[0])
            with self.subTest(change=change):
                self.assertEqual(api.reconcile(), [])

    def test_forged_or_changed_source_provenance_refuses_all_cancellation(self):
        for field, value in [('workflow_id', 99), ('run_attempt', 2), ('head_sha', X),
                             ('pull_requests', []), ('repository', {'id': 99, 'full_name': REPO})]:
            api = FakeGitHub()
            api.event['workflow_run'][field] = value
            with self.subTest(field=field), self.assertRaises(m.Refused):
                api.reconcile()
            self.assertEqual(api.cancelled, [])

    def test_draft_closed_fork_and_retarget_refuse_until_live_ready(self):
        for change in [lambda p: p.update(draft=True), lambda p: p.update(state='closed'),
                       lambda p: p['head']['repo'].update(id=99),
                       lambda p: p['head'].update(ref='different'),
                       lambda p: p['base'].update(ref='different')]:
            api = FakeGitHub()
            change(api.pr)
            with self.assertRaises(m.Refused):
                api.reconcile()
            self.assertEqual(api.cancelled, [])
        self.assertEqual(FakeGitHub().reconcile(), [1])

    def test_live_head_advance_or_divergence_before_cancel_aborts(self):
        for new in (C, X, A):
            api = FakeGitHub()
            count = 0
            def race(path, value, new=new):
                nonlocal count
                if path.endswith('/pulls/42'):
                    count += 1
                    if count == 2:
                        return pr(new)
                return value
            api.hook = race
            with self.assertRaises(m.Refused):
                api.reconcile()
            self.assertEqual(api.cancelled, [])

    def test_fast_forward_after_last_read_still_only_targets_an_ancestor(self):
        api = FakeGitHub()
        def cancel(path):
            api.pr = pr(C)  # Not atomic; A remains a strict ancestor on fast-forward.
            api.cancelled.append(int(path.split('/runs/')[1].split('/')[0]))
        api.cancel = cancel
        self.assertEqual(api.reconcile(), [1])
        self.assertEqual(api.runs[0]['head_sha'], A)

    def test_rerun_identity_or_live_witness_cancel_before_write_is_preserved(self):
        for target_change in (True, False):
            api = FakeGitHub()
            count = 0
            def race(path, value, target_change=target_change):
                nonlocal count
                if '/actions/runs/' in path:
                    count += 1
                    if target_change and count == 2:
                        value['run_attempt'] = 2
                    elif not target_change and count == 3:
                        value.update(status='completed', conclusion='cancelled')
                return value
            api.hook = race
            if target_change:
                with self.assertRaises(m.Refused):
                    api.reconcile()
            else:
                self.assertEqual(api.reconcile(), [])
            self.assertEqual(api.cancelled, [])

    def test_strict_ancestry_required_not_timestamp_or_sha_inequality(self):
        for status in ('identical', 'behind', 'diverged'):
            api = FakeGitHub()
            api.compare = {'status': status}
            self.assertEqual(api.reconcile(), [])
        for evidence in ({}, {'status': 'ahead'},
                         {'status': 'ahead', 'base_commit': {'sha': A},
                          'merge_base_commit': {'sha': X}, 'ahead_by': 1, 'behind_by': 0}):
            api = FakeGitHub()
            api.compare = evidence
            with self.assertRaises(m.Refused):
                api.reconcile()
            self.assertEqual(api.cancelled, [])

    def test_pagination_complete_before_mutation(self):
        api = FakeGitHub()
        api.runs = [run(i, A, status='completed') for i in range(3, 103)] + api.runs
        self.assertEqual(api.reconcile(), [1])
        self.assertTrue(any('page=2' in path for path in api.reads))

    def test_duplicate_incomplete_changing_or_capped_pagination_refuses(self):
        for mutation in ('duplicate', 'incomplete', 'capped', 'changing'):
            api = FakeGitHub()
            calls = 0
            def corrupt(path, value, mutation=mutation, api=api):
                nonlocal calls
                if '/runs?' in path:
                    calls += 1
                    if mutation == 'duplicate':
                        value['workflow_runs'] = [api.runs[0], api.runs[0]]
                    elif mutation == 'incomplete':
                        value['total_count'] = 3
                    elif mutation == 'capped':
                        value['total_count'] = 1000
                    elif calls == 2:
                        value['workflow_runs'][0]['status'] = 'completed'
                return value
            api.hook = corrupt
            with self.subTest(mutation=mutation), self.assertRaises(m.Refused):
                api.reconcile()
            self.assertEqual(api.cancelled, [])

    def test_late_second_workflow_read_error_cannot_leave_partial_plan_applied(self):
        api = FakeGitHub()
        def failure(path, value):
            if '/workflows/22/runs?' in path:
                raise OSError('unavailable')
            return value
        api.hook = failure
        with self.assertRaises(OSError):
            api.reconcile()
        self.assertEqual(api.cancelled, [])

    def test_each_prewrite_api_failure_refuses_without_cancelling(self):
        baseline = FakeGitHub()
        baseline.reconcile()
        for fail_at in range(1, len(baseline.reads)+1):
            api = FakeGitHub()
            def failure(path, value, api=api, fail_at=fail_at):
                if len(api.reads) == fail_at:
                    raise OSError('unavailable')
                return value
            api.hook = failure
            with self.subTest(fail_at=fail_at), self.assertRaises(OSError):
                api.reconcile()
            self.assertEqual(api.cancelled, [])

    def test_uncertain_cancel_is_not_retried_or_reported_complete(self):
        api = FakeGitHub()
        api.cancel = Mock(side_effect=TimeoutError('unknown write outcome'))
        with self.assertRaises(TimeoutError):
            api.reconcile()
        self.assertEqual(api.cancel.call_count, 1)

    def test_transport_budget_and_timeout_are_bounded(self):
        api = m.GitHub(clock=Mock(side_effect=[0, 121]))
        with patch.object(m.subprocess, 'run') as transport, self.assertRaises(m.Refused):
            api.read('fixture')
        transport.assert_not_called()
        api = m.GitHub(clock=lambda: 0)
        api.calls = 160
        with self.assertRaises(m.Refused):
            api.read('fixture')
        api = m.GitHub(clock=lambda: 0)
        with patch.object(m.subprocess, 'run', return_value=Mock(stdout='{}')) as transport:
            api.read('repos/owner/repo/pulls/42')
        self.assertEqual(transport.call_args.kwargs['timeout'], 10)
        self.assertTrue(transport.call_args.kwargs['check'])


class QueuePolicyTests(unittest.TestCase):
    def document(self, name):
        return yaml.load((ROOT / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)

    def test_ordinary_only_policy_and_shared_identity(self):
        from test_ci_dispatch_scope import concurrency_group, evaluate
        for name in ('ci.yml', 'rust.yml'):
            doc = self.document(name)
            for event in ('pull_request', 'workflow_dispatch', 'push', 'schedule', 'merge_group'):
                context = {'github.event_name': event}
                self.assertEqual(evaluate(doc['concurrency']['cancel-in-progress'], **context),
                                 event != 'pull_request')
                self.assertEqual(evaluate(doc['concurrency']['queue'], **context),
                                 'max' if event == 'pull_request' else 'single')
            self.assertEqual(concurrency_group(doc), concurrency_group(doc, **{
                'github.sha': B, 'github.event.pull_request.head.sha': B}))

    def test_delayed_A_admission_does_not_displace_B_active_or_pending(self):
        from test_ci_dispatch_scope import evaluate
        for name in ('ci.yml', 'rust.yml'):
            policy = self.document(name)['concurrency']
            for arrivals in (['B', 'A'], ['X', 'B', 'A']):
                running, pending, canceled = None, [], []
                for item in arrivals:
                    if running is None:
                        running = item
                    elif evaluate(policy['cancel-in-progress']):
                        canceled.append(running)
                        running = item
                    else:
                        if evaluate(policy['queue']) == 'single':
                            canceled.extend(pending)
                            pending.clear()
                        pending.append(item)
                self.assertNotIn('B', canceled)
                self.assertIn('B', [running, *pending])

    def test_trusted_controller_bootstrap_and_permissions(self):
        doc = self.document('ci-label-retrigger.yml')
        self.assertEqual(doc['on']['workflow_run'], {'workflows': ['ci', 'rust'],
                                                    'types': ['requested', 'completed']})
        self.assertEqual(doc['permissions'], {'actions': 'write', 'contents': 'read',
                                             'pull-requests': 'read'})
        self.assertNotIn('pull_request_target', doc['on'])
        job = doc['jobs']['cancel-ancestors']
        checkout, execute = job['steps']
        self.assertEqual(checkout['with']['ref'], '${{ github.event.repository.default_branch }}')
        self.assertEqual(checkout['with']['persist-credentials'], 'false')
        self.assertEqual(checkout['with']['sparse-checkout'], 'scripts/ci/ci_cancel_controller.py')
        self.assertIn('github.event_name == \'workflow_run\'', job['if'])
        self.assertIn('github.event_name == \'pull_request\'', doc['jobs']['retrigger']['if'])
        self.assertNotIn('actions/download-artifact', json.dumps(doc))
        self.assertIn('if [ ! -f scripts/ci/ci_cancel_controller.py ]; then', execute['run'])
        self.assertNotIn('gh run rerun', execute['run'])

    def test_bootstrap_missing_policy_executes_no_other_command(self):
        script = self.document('ci-label-retrigger.yml')['jobs']['cancel-ancestors']['steps'][1]['run']
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(['bash', '-c', script], cwd=tmp, capture_output=True,
                                    text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('shared PR queues remain intact', result.stdout)

    def test_controller_event_groups_do_not_collide_with_labels_or_other_runs(self):
        from test_ci_dispatch_scope import concurrency_group
        doc = self.document('ci-label-retrigger.yml')
        label = concurrency_group(doc)
        first = concurrency_group(doc, **{'github.event_name': 'workflow_run', 'github.run_id': 10})
        second = concurrency_group(doc, **{'github.event_name': 'workflow_run', 'github.run_id': 11})
        self.assertEqual(len({label, first, second}), 3)

    def test_queue_lint_exception_is_exact_and_limited_to_named_paths(self):
        exe = shutil.which('actionlint')
        if not exe:
            self.skipTest('actionlint not on PATH')
        config = (ROOT / '.github/actionlint.yaml').read_text()
        template = '''name: queue test
on: pull_request
concurrency:
  group: fixture
  cancel-in-progress: false
  queue: max
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.git').mkdir()
            directory = root / '.github/workflows'
            directory.mkdir(parents=True)
            (root / '.github/actionlint.yaml').write_text(config)
            for name, body, allowed in [('ci.yml', template, True), ('rust.yml', template, True),
                                        ('other.yml', template, False),
                                        ('ci.yml', template.replace('queue: max', 'queues: max'), False),
                                        ('rust.yml', template.replace('echo ok', 'echo $unbound'), False)]:
                path = directory / name
                path.write_text(body)
                result = subprocess.run([exe, str(path)], cwd=root, capture_output=True, check=False)
                self.assertEqual(result.returncode == 0, allowed, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()

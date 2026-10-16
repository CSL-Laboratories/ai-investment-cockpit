"""Execute settle event predicates and the actual required-gate shell offline."""
import itertools
import os
import subprocess
import unittest
from unittest.mock import patch

from test_ci_dispatch_scope import evaluate, workflow


class SettleTests(unittest.TestCase):
    def setUp(self):
        self.ci = workflow('ci.yml')
        self.rust = workflow('rust.yml')
        self.work = [(self.ci, job) for job in ('linux', 'windows', 'macos', 'benchmarks')]
        self.work.append((self.rust, 'rust-book'))

    def test_event_table_and_settle_outcomes(self):
        for event, action, draft in [
            ('pull_request', 'synchronize', False),
            ('pull_request', 'opened', False),
            ('pull_request', 'reopened', False),
            ('pull_request', 'ready_for_review', False),
            ('pull_request', 'synchronize', True),
            ('workflow_dispatch', '', False),  # manual and validated label reruns
            ('schedule', '', False),
            ('push', '', False),
            ('merge_group', 'checks_requested', False),
        ]:
            context = {'github.event_name': event, 'github.event.action': action,
                       'github.event.pull_request.draft': draft,
                       'github.event.pull_request.labels.*.name': ['ci:full'],
                       'github.ref': 'refs/heads/main', 'inputs.scope': 'full'}
            expected_wait = event == 'pull_request' and action == 'synchronize' and not draft
            for doc in (self.ci, self.rust):
                self.assertEqual(bool(evaluate(doc['jobs']['settle']['if'], **context)), expected_wait)
            for doc, job in self.work:
                condition = doc['jobs'][job]['if']
                self.assertIn('settle', doc['jobs'][job]['needs'])
                eligible = bool(evaluate(condition, **context, **{'needs.settle.result': 'success'}))
                for result in ('success', 'failure', 'cancelled', 'skipped', '', 'pending'):
                    with self.subTest(event=event, action=action, draft=draft, job=job, result=result):
                        self.assertEqual(bool(evaluate(condition, **context, **{
                            'needs.settle.result': result})),
                            eligible and (result == 'success' or (result == 'skipped' and not expected_wait)))
                for result in ('failure', 'skipped', 'cancelled', ''):
                    self.assertFalse(evaluate(condition, **context, **{'needs.filter.result': result}))
                # Exercise cancellation itself, not just a dependency's result.
                with patch('test_ci_dispatch_scope.eval', wraps=eval) as evaluator:
                    evaluate(condition, **context)
                    expression, environment = evaluator.call_args.args
                environment['cancelled'] = lambda: True
                self.assertFalse(eval(expression, environment))

    def test_required_gate_executable_result_table(self):
        gate = self.ci['jobs']['linux-gates']
        self.assertIn('settle', gate['needs'])
        script = gate['steps'][0]['run']
        # Execute the workflow's actual shell, including unset/unknown output cases.
        for expected, settle, run_tests, linux in itertools.product(
            ('true', 'false'), ('success', 'failure', 'cancelled', 'skipped'),
            ('true', 'false', ''), ('success', 'failure', 'cancelled', 'skipped')):
            env = {**os.environ, 'FILTER_RESULT': 'success', 'SETTLE_RESULT': settle,
                   'SETTLE_EXPECTED': expected, 'RUN_TESTS': run_tests, 'LINUX_RESULT': linux}
            result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, check=False)
            accepted = ((settle == 'success' or (settle == 'skipped' and expected == 'false'))
                        and ((run_tests == 'true' and linux == 'success')
                             or (run_tests == 'false' and linux == 'skipped')))
            with self.subTest(expected=expected, settle=settle, run_tests=run_tests, linux=linux):
                self.assertEqual(result.returncode, 0 if accepted else 1, result.stderr)

    def test_known_non_synchronize_paths_are_not_accidentally_suppressed(self):
        for event, action, expected in [
            ('pull_request', 'opened', ('linux', 'windows', 'macos', 'benchmarks')),
            ('pull_request', 'reopened', ('linux', 'windows', 'macos', 'benchmarks')),
            ('pull_request', 'ready_for_review', ('linux', 'windows', 'macos', 'benchmarks')),
            ('workflow_dispatch', '', ('linux', 'windows', 'macos')),
            ('push', '', ('linux', 'windows')),
            ('schedule', '', ('macos',)),
        ]:
            context = {'github.event_name': event, 'github.event.action': action,
                       'github.ref': 'refs/heads/main', 'inputs.scope': 'full',
                       'github.event.pull_request.labels.*.name': ['ci:full'],
                       'needs.settle.result': 'skipped',
                       'needs.filter.outputs.run-tests': 'false' if event == 'schedule' else 'true'}
            actual = tuple(job for job in ('linux', 'windows', 'macos', 'benchmarks')
                           if evaluate(self.ci['jobs'][job]['if'], **context))
            self.assertEqual(actual, expected)
            if event != 'schedule':
                self.assertTrue(evaluate(self.rust['jobs']['rust-book']['if'], **context))


if __name__ == '__main__':
    unittest.main()

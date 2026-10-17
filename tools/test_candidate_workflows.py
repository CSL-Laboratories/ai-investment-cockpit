"""Execute candidate workflow decisions without dispatching hosted jobs."""
import itertools
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.test_ci_dispatch_scope import concurrency_group, evaluate, workflow  # noqa: E402 - direct-script entry

FIELDS = {'pr_number': '166', 'pr_head_sha': 'a' * 40, 'pr_base_sha': 'b' * 40,
          'merge_sha': 'c' * 40, 'merge_tree_sha': 'd' * 40,
          'candidate_id': 'e' * 64, 'dispatch_nonce': 'f' * 32}
CONTEXT = {'github.event_name': 'workflow_dispatch', 'inputs.scope': 'full',
           'github.event.pull_request.base.sha': 'b' * 40,
           **{f'inputs.{key}': value for key, value in FIELDS.items()}}


def step_env(step, context=None):
    result = {}
    for key, value in step.get('env', {}).items():
        if value.startswith('${{'):
            value = evaluate(value, **(context or CONTEXT), **{
                'github.token': 'fixture-read-only-token'})
            value = str(value).lower() if isinstance(value, bool) else str(value)
        result[key] = value
    return result


class CandidateWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.ci, self.rust = workflow('ci.yml'), workflow('rust.yml')

    def test_candidate_groups_and_delivery_names_do_not_collide(self):
        for name, doc in [('ci', self.ci), ('rust', self.rust)]:
            self.assertEqual(evaluate(doc['run-name'], **CONTEXT),
                             f"candidate:{FIELDS['candidate_id']}:{name}:{FIELDS['dispatch_nonce']}")
            self.assertFalse(evaluate(doc['concurrency']['cancel-in-progress'], **CONTEXT))
            self.assertEqual(evaluate(doc['concurrency']['queue'], **CONTEXT), 'max')
            candidate = concurrency_group(doc, **CONTEXT)
            other = {**CONTEXT, 'inputs.candidate_id': '1' * 64}
            self.assertNotEqual(candidate, concurrency_group(doc, **other))
            retry = {**CONTEXT, 'inputs.dispatch_nonce': '2' * 32}
            self.assertEqual(candidate, concurrency_group(doc, **retry))
            self.assertNotEqual(evaluate(doc['run-name'], **CONTEXT),
                                evaluate(doc['run-name'], **retry))
            label = {**CONTEXT, **{f'inputs.{k}': '' for k in
                                  ('candidate_id', 'pr_base_sha', 'merge_tree_sha', 'dispatch_nonce')}}
            self.assertNotEqual(candidate, concurrency_group(doc, **label))
            self.assertTrue(evaluate(doc['concurrency']['cancel-in-progress'], **label))
            self.assertEqual(evaluate(doc['concurrency']['queue'], **label), 'single')
            self.assertEqual(evaluate(doc['concurrency']['queue'], **{
                'github.event_name': 'workflow_dispatch'}), 'single')
            self.assertEqual(evaluate(doc['concurrency']['queue']), 'max')
            for event in ('pull_request', 'push', 'schedule', 'merge_group'):
                self.assertNotEqual(candidate, concurrency_group(doc, **{'github.event_name': event}))

    def test_partial_candidate_fields_enter_validation_and_cannot_cancel_labels(self):
        new_fields = ('candidate_id', 'pr_base_sha', 'merge_tree_sha', 'dispatch_nonce')
        for doc in (self.ci, self.rust):
            step = next(s for s in doc['jobs']['filter']['steps']
                        if s.get('name') == 'Validate exact full candidate')
            legacy = {'github.event_name': 'workflow_dispatch', 'inputs.pr_number': '166'}
            self.assertFalse(evaluate(step['if'], **legacy))
            for key in new_fields:
                context = {**legacy, f'inputs.{key}': FIELDS[key]}
                with self.subTest(field=key):
                    self.assertTrue(evaluate(step['if'], **context))
                    self.assertFalse(evaluate(doc['concurrency']['cancel-in-progress'], **context))
                    self.assertEqual(evaluate(doc['concurrency']['queue'], **context), 'max')
                    self.assertNotEqual(concurrency_group(doc, **context),
                                        concurrency_group(doc, **legacy))

    def test_delayed_duplicate_delivery_keeps_current_pending_candidate(self):
        for doc in (self.ci, self.rust):
            policy = doc['concurrency']
            # All three deliveries share the candidate digest. The middle
            # approved delivery must survive a delayed duplicate's admission.
            running, pending, canceled = None, [], []
            for delivery in ('active', 'approved-pending', 'delayed-duplicate'):
                if running is None:
                    running = delivery
                elif evaluate(policy['cancel-in-progress'], **CONTEXT):
                    canceled.append(running)
                    running = delivery
                else:
                    if evaluate(policy['queue'], **CONTEXT) == 'single':
                        canceled.extend(pending)
                        pending.clear()
                    pending.append(delivery)
            self.assertEqual(canceled, [])
            self.assertEqual(running, 'active')
            self.assertEqual(pending, ['approved-pending', 'delayed-duplicate'])

    def test_ordinary_candidate_manual_and_label_deliveries_cannot_cancel_each_other(self):
        candidate_fields = ('candidate_id', 'pr_base_sha', 'merge_tree_sha', 'dispatch_nonce')
        common = {'github.event.pull_request.number': 166,
                  'github.ref': 'refs/heads/feature', 'inputs.scope': 'full'}
        ordinary = {**common, 'github.event_name': 'pull_request'}
        manual = {**common, 'github.event_name': 'workflow_dispatch'}
        label = {**manual, 'inputs.pr_number': '166', 'inputs.pr_head_sha': FIELDS['pr_head_sha'],
                 'inputs.merge_sha': FIELDS['merge_sha']}
        for doc in (self.ci, self.rust):
            # Every nonempty subset of candidate-specific fields must isolate
            # even a malformed delivery before its validation step can execute.
            for present in itertools.product((False, True), repeat=len(candidate_fields)):
                if not any(present):
                    continue
                for legacy in (manual, label):
                    candidate = {**legacy, **{f'inputs.{field}': FIELDS[field]
                                             for field, supplied in zip(candidate_fields, present)
                                             if supplied}}
                    policy = doc['concurrency']
                    self.assertFalse(evaluate(policy['cancel-in-progress'], **candidate))
                    self.assertEqual(evaluate(policy['queue'], **candidate), 'max')
                    deliveries = [(name, concurrency_group(doc, **context),
                                   evaluate(policy['cancel-in-progress'], **context))
                                  for name, context in [('ordinary', ordinary), ('candidate', candidate),
                                                        ('manual', manual), ('label', label)]]
                    for arrivals in itertools.permutations(deliveries):
                        running, pending, canceled = {}, {}, []
                        for name, group, cancel in arrivals:
                            if group not in running:
                                running[group] = name
                            elif cancel:
                                canceled.append(running[group])
                                running[group] = name
                            else:
                                pending.setdefault(group, []).append(name)
                        self.assertEqual(canceled, [], arrivals)
                        self.assertEqual(set(running.values()), {'ordinary', 'candidate', 'manual', 'label'})
                        self.assertEqual(pending, {})

    def test_existing_required_check_names_remain_exact(self):
        self.assertEqual(self.ci['jobs']['linux-gates']['name'], 'linux-gates')
        self.assertEqual(self.ci['jobs']['queue-gates']['name'], 'queue-gates')
        self.assertEqual(self.rust['jobs']['rust-gates']['name'], 'rust-gates')
        self.assertEqual(self.ci['jobs']['candidate-ci-gates']['name'], 'candidate-ci-gates')

    def test_live_validation_runs_after_each_exact_checkout_before_test_installation(self):
        for doc, jobs in [(self.ci, ('filter', 'linux', 'windows', 'macos')),
                          (self.rust, ('filter', 'rust-book'))]:
            for job in jobs:
                steps = doc['jobs'][job]['steps']
                checkout, validation = steps[:2]
                self.assertEqual(checkout['uses'], 'actions/checkout@v4')
                self.assertEqual(evaluate(checkout['with']['ref'], **CONTEXT), FIELDS['merge_sha'])
                self.assertEqual(checkout['with']['fetch-depth'], '2')
                self.assertEqual(validation['name'], 'Validate exact full candidate')
                self.assertTrue(evaluate(validation['if'], **CONTEXT))
                self.assertEqual(step_env(validation)['DISPATCH_NONCE'], FIELDS['dispatch_nonce'])
                self.assertEqual(step_env(validation)['PR_BASE_SHA'], FIELDS['pr_base_sha'])
                self.assertEqual(step_env(validation)['MERGE_TREE_SHA'], FIELDS['merge_tree_sha'])

    def test_actual_validation_step_forwards_tuple_and_stops_on_refusal_or_wrong_scope(self):
        # A subprocess fixture isolates workflow wiring from the helper's separate
        # real-Git/API tests. A refusal must propagate before any following work.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = root / 'scripts/ci/merge_candidate.py'
            helper.parent.mkdir(parents=True)
            helper.write_text("import json, os, sys\n"
                              "assert sys.argv[1:] == ['validate-dispatch']\n"
                              "print(json.dumps({k: os.environ[k] for k in "
                              "['PR_NUMBER','PR_HEAD_SHA','PR_BASE_SHA','MERGE_SHA',"
                              "'MERGE_TREE_SHA','CANDIDATE_ID','DISPATCH_NONCE']}))\n"
                              "raise SystemExit(int(os.environ['REFUSAL']))\n")
            for doc in (self.ci, self.rust):
                step = doc['jobs']['filter']['steps'][1]
                for scope, refusal in [('full', 0), ('linux', 0), ('', 0), ('full', 1)]:
                    result = subprocess.run(['bash', '-c', step['run']], cwd=root,
                                            env={**os.environ, **step_env(step),
                                                 'CANDIDATE_SCOPE': scope, 'REFUSAL': str(refusal)},
                                            capture_output=True, text=True, check=False)
                    self.assertEqual(result.returncode == 0, scope == 'full' and refusal == 0,
                                     result.stderr)
                    if scope == 'full':
                        self.assertEqual(json.loads(result.stdout),
                                         {key.upper(): value for key, value in FIELDS.items()})
                    else:
                        self.assertEqual(result.stdout, '')

    def test_docs_candidate_executes_all_five_python_and_rust_cells(self):
        for files in ('docs/example.md', '', 'app/main.py'):
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / 'output'
                decide = next(s for s in self.ci['jobs']['filter']['steps'] if s.get('id') == 'decide')
                result = subprocess.run(['bash', '-c', decide['run']], capture_output=True, check=False,
                                        env={**os.environ, **step_env(decide),
                                             'DIFF_FILES': files, 'GITHUB_OUTPUT': str(output)})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('run-tests=true', output.read_text())
                for job in ('linux', 'windows', 'macos'):
                    self.assertTrue(evaluate(self.ci['jobs'][job]['if'], **{
                        **CONTEXT, 'needs.settle.result': 'skipped'}))
                self.assertEqual(self.ci['jobs']['linux']['strategy']['matrix']['python-version'],
                                 ['3.11', '3.12'])
                self.assertEqual(self.ci['jobs']['windows']['strategy']['matrix']['python-version'],
                                 ['3.11', '3.12'])
                gate = next(s for s in self.rust['jobs']['filter']['steps'] if s.get('id') == 'gate')
                output.unlink()
                result = subprocess.run(['bash', '-c', gate['run']], capture_output=True, check=False,
                                        env={**os.environ, **step_env(gate),
                                             'DIFF_FILES': files, 'GITHUB_OUTPUT': str(output)})
                self.assertEqual(result.returncode, 0, result.stderr)
                cells = json.loads(output.read_text().splitlines()[1])['include']
                self.assertEqual([(c['os-name'], c['python-version']) for c in cells],
                                 [('linux', '3.11'), ('linux', '3.12'),
                                  ('windows', '3.11'), ('windows', '3.12'), ('macos', '3.12')])

    def test_full_aggregate_rejects_every_partial_platform_result(self):
        gate = self.ci['jobs']['candidate-ci-gates']
        self.assertTrue(evaluate(gate['if'], **CONTEXT))
        self.assertFalse(evaluate(gate['if']))
        self.assertEqual(gate['needs'], ['filter', 'settle', 'linux', 'windows', 'macos'])
        baseline = {'FILTER_RESULT': 'success', 'SETTLE_RESULT': 'skipped',
                    'RUN_TESTS': 'true', 'CANDIDATE_SCOPE': 'full'}
        for states in itertools.product(('success', 'failure', 'skipped', 'cancelled'), repeat=3):
            env = {**os.environ, **baseline, **dict(zip(
                ('LINUX_RESULT', 'WINDOWS_RESULT', 'MACOS_RESULT'), states))}
            result = subprocess.run(['bash', '-c', gate['steps'][0]['run']], env=env,
                                    capture_output=True, check=False)
            self.assertEqual(result.returncode == 0, states == ('success',) * 3)
        for key, value in [('FILTER_RESULT', 'failure'), ('SETTLE_RESULT', 'failure'),
                           ('SETTLE_RESULT', 'cancelled'), ('SETTLE_RESULT', 'success'),
                           ('RUN_TESTS', 'false'), ('RUN_TESTS', ''), ('CANDIDATE_SCOPE', 'linux')]:
            env = {**os.environ, **baseline, 'LINUX_RESULT': 'success',
                   'WINDOWS_RESULT': 'success', 'MACOS_RESULT': 'success', key: value}
            result = subprocess.run(['bash', '-c', gate['steps'][0]['run']], env=env,
                                    capture_output=True, check=False)
            self.assertNotEqual(result.returncode, 0)
        reject = gate['steps'][1]
        self.assertEqual(reject['if'], 'cancelled()')
        self.assertNotEqual(subprocess.run(['bash', '-c', reject['run']], check=False).returncode, 0)


if __name__ == '__main__':
    unittest.main()

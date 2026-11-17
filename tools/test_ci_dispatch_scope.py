"""Offline workflow regression tests; no GitHub calls or hosted jobs.

Run: python -m unittest discover -s tools -p test_ci_dispatch_scope.py -v
Requires PyYAML (already part of the repository validation dependencies).
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / '.github' / 'workflows'


def workflow(name):
    # BaseLoader keeps the YAML 1.1 word "on" as a string.
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def evaluate(expression, **overrides):
    """Evaluate this bounded set of workflow boolean expressions, failing unknowns.

    This is a test adapter, not a general GitHub expression interpreter.
    """
    values = {
        'github.event_name': 'pull_request',
        'github.event.before': 'e' * 40,
        'github.event.action': 'synchronize',
        'github.workflow': 'ci',
        'github.run_id': 12345,
        'github.sha': 'a' * 40,
        'github.ref': 'refs/pull/149/merge',
        'github.repository': 'owner/repo',
        'github.event.pull_request.head.repo.full_name': 'owner/repo',
        'github.event.pull_request.head.sha': 'a' * 40,
        'github.event.pull_request.draft': False,
        'github.event.pull_request.labels.*.name': [],
        'github.event.label.name': '',
        'github.event.pull_request.number': 149,
        'needs.filter.result': 'success',
        'needs.settle.result': 'success',
        'github.event.merge_group.head_ref': 'refs/heads/feature',
        'github.event.merge_group.base_sha': 'c' * 40,
        'github.event.merge_group.head_sha': 'd' * 40,
        'inputs.scope': '',
        'inputs.pr_number': '',
        'inputs.pr_head_sha': '',
        'inputs.merge_sha': '',
        'inputs.pr_base_sha': '',
        'inputs.merge_tree_sha': '',
        'inputs.candidate_id': '',
        'inputs.dispatch_nonce': '',
        'needs.filter.outputs.run-tests': 'true',
        'needs.filter.outputs.run-macos': 'true',
        'needs.filter.outputs.has-cells': 'true',
    }
    values.update(overrides)
    expression = expression.strip().removeprefix('${{').removesuffix('}}').strip()
    tokens = '|'.join(re.escape(k) for k in sorted(values, key=len, reverse=True))
    expression = re.sub(tokens, lambda m: repr(values[m.group()]), expression)
    expression = re.sub(r'!cancelled\(\)', ' (not cancelled()) ', expression)
    expression = expression.replace('&&', ' and ').replace('||', ' or ')
    expression = re.sub(r'\btrue\b', 'True', expression)
    expression = re.sub(r'\bfalse\b', 'False', expression)
    # Expressions under test only use literals, comparisons, boolean operators,
    # contains, and indexed string format. No arbitrary external input reaches
    # this evaluation. format fixtures use only GitHub's supported {N} form.
    return eval(expression, {'__builtins__': {}, 'contains': lambda a, b: b in a,
                             'format': lambda template, *args: template.format(*args),
                             'always': lambda: True, 'cancelled': lambda: False})


def concurrency_group(doc, **context):
    """Render the checked-in group expression for a synthetic event."""
    return re.sub(r'\$\{\{(.*?)\}\}',
                  lambda match: str(evaluate(match[1], **context)),
                  doc['concurrency']['group'])


class DispatchScopeTests(unittest.TestCase):
    def setUp(self):
        self.ci = workflow('ci.yml')
        self.rust = workflow('rust.yml')
        self.label = workflow('ci-label-retrigger.yml')
        self.step = self.label['jobs']['retrigger']['steps'][0]

    def test_merge_group_trigger_and_isolated_concurrency(self):
        self.assertEqual(self.ci['on']['merge_group']['types'], ['checks_requested'])
        self.assertEqual(self.rust['on']['merge_group']['types'], ['checks_requested'])
        self.assertIn('MG_BASE', next(step['env'] for step in self.ci['jobs']['filter']['steps']
                                      if step.get('id') == 'decide'))
        self.assertNotIn("github.event_name != 'push'", self.ci['jobs']['linux']['if'])
        self.assertIn("github.event_name == 'merge_group'", self.ci['jobs']['windows']['if'])
        self.assertIn("github.event_name == 'push' && github.ref == 'refs/heads/main'",
                         self.ci['jobs']['windows']['if'])
        for cells in ('true', 'false', ''):
            self.assertEqual(bool(evaluate(self.rust['jobs']['rust-book']['if'], **{
                'needs.filter.outputs.has-cells': cells})), cells == 'true')
        def group(doc, **context):
            return re.sub(r'\$\{\{(.*?)\}\}',
                          lambda match: str(evaluate(match[1], **context)),
                          doc['concurrency']['group'])
        for doc in (self.ci, self.rust):
            pr = group(doc)
            queued = group(doc, **{'github.event_name': 'merge_group',
                                   'github.event.pull_request.number': '',
                                   'github.ref': 'refs/heads/feature',
                                   'github.event.merge_group.head_ref': 'refs/heads/feature'})
            self.assertNotEqual(pr, queued)
            self.assertIn('queue', queued)
            self.assertIn('refs/heads/feature', queued)

    def test_dispatch_defaults_and_existing_events(self):
        for doc in (self.ci, self.rust):
            with self.subTest(workflow=doc['name']):
                spec = doc['on']['workflow_dispatch']['inputs']['scope']
                self.assertEqual(spec['default'], 'full')
                self.assertEqual(spec['options'], ['full', 'linux'])
                self.assertEqual(doc['on']['pull_request']['types'],
                                 ['opened', 'synchronize', 'reopened', 'ready_for_review'])
        self.assertEqual(self.label['on']['pull_request']['types'], ['labeled'])
        self.assertNotIn('linux-gates', self.label['jobs'])
        self.assertTrue(any('tools/test_ci_dispatch_scope.py' in step.get('run', '')
                            for step in self.ci['jobs']['linux']['steps']))

    def test_label_eligibility_and_scope(self):
        guard = self.label['jobs']['retrigger']['if']
        scope = self.step['env']['CI_SCOPE']
        for label, labels, draft, repo, eligible, expected in [
            ('enhancement', ['enhancement'], False, 'owner/repo', True, 'linux'),
            ('enhancement', ['enhancement', 'ci:full'], False, 'owner/repo', True, 'full'),
            ('ci:full', [], False, 'owner/repo', True, 'full'),
            ('documentation', ['ci:full'], False, 'owner/repo', False, None),
            ('enhancement', [], True, 'owner/repo', False, None),
            ('ci:full', [], False, 'fork/repo', False, None),
        ]:
            with self.subTest(label=label, labels=labels, draft=draft, repo=repo):
                context = {'github.event.label.name': label,
                           'github.event.pull_request.labels.*.name': labels,
                           'github.event.pull_request.draft': draft,
                           'github.event.pull_request.head.repo.full_name': repo}
                self.assertEqual(evaluate(guard, **context), eligible)
                if eligible:
                    self.assertEqual(evaluate(scope, **context), expected)

    def test_platform_conditions_and_actual_rust_matrix(self):
        rust_step = next(step for step in self.rust['jobs']['filter']['steps']
                         if step.get('id') == 'gate')
        queue_env = next(step for step in self.ci['jobs']['queue-gates']['steps']
                         if step.get('name') == 'Require all mandatory platform cells')['env']
        # CSL-238: a non-draft same-repository PR runs every cell without a label;
        # a draft runs no matrix at all (filter is skipped, whatever the labels) and a fork PR still needs
        # ci:full; the rows below evaluate the platform conditions only, as before.
        for event, scope, labels, draft, repo, win, mac, rust_win, rust_mac in [
            ('workflow_dispatch', '', [], False, 'owner/repo', True, True, True, True),
            ('workflow_dispatch', 'full', [], False, 'owner/repo', True, True, True, True),
            ('workflow_dispatch', 'linux', [], False, 'owner/repo', False, False, False, False),
            ('pull_request', '', [], False, 'owner/repo', True, True, True, True),
            ('pull_request', '', ['enhancement'], False, 'owner/repo', True, True, True, True),
            ('pull_request', '', [], True, 'owner/repo', False, False, False, False),
            ('pull_request', '', ['enhancement'], True, 'owner/repo', False, False, False, False),
            ('pull_request', '', [], False, 'fork/repo', False, False, False, False),
            ('pull_request', '', ['enhancement'], False, 'fork/repo', False, False, False, False),
            ('pull_request', '', ['ci:full'], False, 'owner/repo', True, True, True, True),
            ('pull_request', '', ['ci:full'], True, 'owner/repo', True, True, True, True),
            ('pull_request', '', ['ci:full'], False, 'fork/repo', True, True, True, True),
            ('push', '', [], False, 'owner/repo', True, False, True, False),
            ('merge_group', '', [], False, 'owner/repo', True, False, True, False),
            ('schedule', '', [], False, 'owner/repo', False, True, False, False),
        ]:
            with self.subTest(event=event, scope=scope, labels=labels, draft=draft, repo=repo):
                context = {'github.event_name': event, 'inputs.scope': scope,
                           'github.ref': 'refs/heads/main',
                           'github.event.pull_request.draft': draft,
                           'github.event.pull_request.head.repo.full_name': repo,
                           'github.event.pull_request.labels.*.name': labels}
                self.assertEqual(bool(evaluate(self.ci['jobs']['windows']['if'], **context)), win)
                # R5 (Fable round 1): queue-gates must expect Windows exactly when the windows job runs.
                self.assertEqual(bool(evaluate(queue_env['WINDOWS_REQUIRED'], **context)), win)
                self.assertEqual(bool(evaluate(self.ci['jobs']['macos']['if'], **context)), mac)
                self.assertEqual(bool(evaluate(self.ci['jobs']['linux']['if'], **context)), True)
                env = {key: str(bool(evaluate(expr, **context))).lower()
                       for key, expr in rust_step['env'].items()}
                self.assertEqual(env['RUN_WINDOWS'], str(rust_win).lower())
                self.assertEqual(env['RUN_MACOS'], str(rust_mac).lower())
                self.assertEqual(env['SKIP_CELLS'], 'false')
                self.assertEqual(env['CHECK_PATHS'], str(event != 'workflow_dispatch').lower())
                run_env = {**os.environ, **env}
                if event != 'workflow_dispatch':
                    run_env['DIFF_FILES'] = 'rust/src/lib.rs'
                with tempfile.TemporaryDirectory() as tmp:
                    output = Path(tmp) / 'output'
                    subprocess.run(['bash', '-eu', '-c', rust_step['run']], check=True,
                                   env={**run_env, 'GITHUB_OUTPUT': str(output)})
                    matrix = json.loads(output.read_text().splitlines()[1])['include']
                self.assertEqual([c['os-name'] for c in matrix],
                                     ['linux', 'linux'] + ['windows'] * (2 * rust_win)
                                     + ['macos'] * rust_mac)
        # Dropping the same-repository clause (a mutant) must differ from the windows job on a fork pull request.
        mutant = queue_env['WINDOWS_REQUIRED'].replace(
            " && github.event.pull_request.head.repo.full_name == github.repository", '')
        self.assertNotEqual(mutant, queue_env['WINDOWS_REQUIRED'])
        fork = {'github.event_name': 'pull_request', 'github.event.pull_request.draft': False,
                'github.event.pull_request.head.repo.full_name': 'fork/repo',
                'github.event.pull_request.labels.*.name': []}
        self.assertNotEqual(bool(evaluate(mutant, **fork)), bool(evaluate(queue_env['WINDOWS_REQUIRED'], **fork)))
        self.assertFalse(evaluate(self.ci['jobs']['macos']['if'], **{
            'github.event_name': 'schedule', 'needs.filter.outputs.run-macos': 'false'}))
        rust_step = next(step for step in self.rust['jobs']['filter']['steps']
                         if step.get('id') == 'gate')
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'output'
            subprocess.run(['bash', '-eu', '-c', rust_step['run']], check=True,
                           env={**os.environ, 'SKIP_CELLS': 'false', 'CHECK_PATHS': 'true',
                                'RUN_WINDOWS': 'true', 'RUN_MACOS': 'false',
                                'DIFF_FILES': 'docs/example.md', 'GITHUB_OUTPUT': str(output)})
            self.assertEqual(json.loads(output.read_text().splitlines()[1])['include'], [])
            self.assertIn('has-cells=false', output.read_text())

    def test_settle_debounce_gates_test_cells_only_on_pr_synchronize(self):
        for doc, billed in ((self.ci, ('linux', 'windows', 'benchmarks')),
                            (self.rust, ('rust-book',))):
            settle = doc['jobs']['settle']
            self.assertEqual(settle['runs-on'], 'ubuntu-latest')
            self.assertEqual(settle['steps'][0]['run'], 'sleep 120')
            self.assertTrue(evaluate(settle['if'], **{
                'github.event_name': 'pull_request',
                'github.event.action': 'synchronize',
                'github.event.pull_request.draft': False}))
            for event, action, draft in [
                ('pull_request', 'opened', False),
                ('pull_request', 'reopened', False),
                ('pull_request', 'ready_for_review', False),
                ('pull_request', 'synchronize', True),
                ('push', 'synchronize', False),
                ('workflow_dispatch', 'synchronize', False),
                ('schedule', 'synchronize', False),
            ]:
                self.assertFalse(evaluate(settle['if'], **{
                    'github.event_name': event, 'github.event.action': action,
                    'github.event.pull_request.draft': draft}))
            for job in billed:
                self.assertEqual(doc['jobs'][job]['needs'], ['filter', 'settle'])
                self.assertIn('always() && !cancelled()',
                              ' '.join(doc['jobs'][job]['if'].split()))
        self.assertEqual(self.ci['jobs']['filter'].get('needs'), None)
        self.assertIn('settle', self.ci['jobs']['macos']['needs'])
        self.assertEqual(self.ci['jobs']['linux-gates']['needs'], ['filter', 'settle', 'linux'])
        self.assertNotIn('settle', self.rust['jobs']['filter'].get('needs', []))

    def test_linux_gate_fails_closed(self):
        gate = self.ci['jobs']['linux-gates']
        self.assertEqual(gate['if'], "always() && github.event_name != 'schedule'")
        self.assertEqual(self.ci['jobs']['linux']['strategy']['matrix']['python-version'], ['3.11', '3.12'])
        for filter_result in ('success', 'failure', 'skipped', 'cancelled'):
            for linux_result in ('success', 'failure', 'skipped', 'cancelled'):
                with self.subTest(filter=filter_result, linux=linux_result):
                    result = subprocess.run(['bash', '-c', gate['steps'][0]['run']],
                                            capture_output=True, check=False, env={**os.environ,
                                            'FILTER_RESULT': filter_result, 'LINUX_RESULT': linux_result,
                                            'SETTLE_RESULT': 'success', 'SETTLE_EXPECTED': 'true',
                                            'RUN_TESTS': 'true'})
                    self.assertEqual(result.returncode == 0,
                                     filter_result == 'success' and linux_result == 'success')

    def test_dispatch_runs_linux_and_drafts_keep_existing_guard(self):
        for doc in (self.ci, self.rust):
            self.assertFalse(evaluate(doc['jobs']['filter']['if'], **{
                'github.event.pull_request.draft': True}))
        decide = next(step for step in self.ci['jobs']['filter']['steps']
                      if step.get('id') == 'decide')
        for scope in ('', 'full', 'linux'):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / 'output'
                subprocess.run(['bash', '-eu', '-c', decide['run']], check=True,
                               capture_output=True, env={**os.environ,
                               'EVENT_NAME': 'workflow_dispatch', 'CI_SCOPE': scope,
                               'GITHUB_OUTPUT': str(output)})
                self.assertIn('run-tests=true', output.read_text().splitlines())
                self.assertTrue(evaluate(self.ci['jobs']['linux']['if'], **{
                    'needs.filter.outputs.run-tests': 'true'}))

    def test_actual_dispatch_shell_argv_and_rust_path_filter(self):
        # Stub gh locally. This executes the workflow shell, never the real CLI.
        for scope in ('linux', 'full'):
            for changed, expected in [('docs/example.md', ['ci.yml']),
                                      ('rust/crate/src/lib.rs', ['ci.yml', 'rust.yml']),
                                      ('.github/workflows/ci.yml', ['ci.yml', 'rust.yml']),
                                      ('.github/actions/run-gates/action.yml', ['ci.yml', 'rust.yml']),
                                      ('.github/actionlint.yaml', ['ci.yml', 'rust.yml']),
                                      ('tools/required_ci_gate.py', ['ci.yml', 'rust.yml']),
                                      ('tools/test_queue_gates.py', ['ci.yml', 'rust.yml']),
                                      ('tools/test_queue_gates.py.bak', ['ci.yml']),
                                      ('app/quant/pnl.py.bak', ['ci.yml']),
                                      # Native signals/normalization coverage added on main.
                                      ('app/quant/signals.py', ['ci.yml', 'rust.yml']),
                                      ('app/ingest/normalize.py', ['ci.yml', 'rust.yml']),
                                      ('app/tests/quant/test_signals.py', ['ci.yml', 'rust.yml']),
                                      ('app/tests/quant/test_signals_rust_parity.py', ['ci.yml', 'rust.yml']),
                                      ('app/tests/ingest/test_normalize.py', ['ci.yml', 'rust.yml']),
                                      ('app/tests/ingest/test_normalize_rust_parity.py', ['ci.yml', 'rust.yml']),
                                      ('app/quant/signals.py.bak', ['ci.yml']),
                                      ('app/ingest/normalize_edgar.py', ['ci.yml']),
                                      ('app/ingest/edgar.py', ['ci.yml']),
                                      ('app/tests/ingest/test_edgar_brew.py', ['ci.yml'])]:
                with self.subTest(scope=scope, changed=changed), tempfile.TemporaryDirectory() as tmp:
                    tmp = Path(tmp)
                    gh = tmp / 'gh'
                    gh.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
                                  'if sys.argv[1] == "api": print(os.environ["CHANGED"])\n'
                                  'else:\n with open(os.environ["CALLS"], "a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n')
                    gh.chmod(0o700)
                    env = {**os.environ, 'PATH': str(tmp) + os.pathsep + os.environ['PATH'],
                           'CI_SCOPE': scope, 'HEAD_REF': 'cursor/csl-202', 'PR_NUMBER': '149',
                           'PR_HEAD_SHA': 'a' * 40, 'MERGE_SHA': 'b' * 40,
                           'GITHUB_REPOSITORY': 'owner/repo', 'CHANGED': changed, 'CALLS': str(tmp/'calls')}
                    subprocess.run(['bash', '-c', self.step['run']], check=True, env=env)
                    calls = [json.loads(line) for line in (tmp/'calls').read_text().splitlines()]
                    self.assertEqual(calls, [['workflow', 'run', name, '--repo', 'owner/repo',
                                             '--ref', 'cursor/csl-202', '-f', 'scope='+scope,
                                             '-f', 'pr_number=149', '-f', 'pr_head_sha='+'a'*40,
                                             '-f', 'merge_sha='+'b'*40]
                                            for name in expected])
                    (tmp/'calls').unlink()
                    rejected = subprocess.run(['bash', '-c', self.step['run']], env={**env, 'HEAD_REF': 'main'},
                                              capture_output=True, check=False)
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertFalse((tmp/'calls').exists())

    def test_label_regex_and_rust_filter_route_native_paths_consistently(self):
        # Both CI-owned selectors must agree on every Rust-owned path, including
        # the native signals/normalization coverage added on main. Each side is
        # executed as checked in: the dispatcher's grep via bash, the rust.yml
        # matrix generator via its embedded Python.
        regex = re.search(r"grep -Eq '(\^\(.*?\))'", self.step['run']).group(1)
        gate = next(step for step in self.rust['jobs']['filter']['steps']
                    if step.get('id') == 'gate')
        generator = gate['run'].split("<<'PY'", 1)[1].rsplit('PY', 1)[0]

        def label_selects(path):
            return subprocess.run(['bash', '-c', 'printf "%s\\n" "$1" | grep -Eq "$2"', '_', path, regex],
                                  check=False).returncode == 0

        def rust_selects(path):
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / 'output'
                subprocess.run([sys.executable, '-c', generator], check=True, env={
                    **os.environ, 'SKIP_CELLS': 'false', 'CHECK_PATHS': 'true',
                    'RUN_WINDOWS': 'false', 'RUN_MACOS': 'false', 'DIFF_FILES': path,
                    'GITHUB_OUTPUT': str(output)})
                return 'has-cells=true' in output.read_text()

        selected = ['rust/cockpit-signals/src/lib.rs', 'rust/cockpit-ingest/src/normalize.rs',
                    'rust/bench_signals.py', 'rust/bench_normalize.py',
                    'app/quant/book/_rust_backend.py', 'app/quant/costs.py', 'app/quant/pnl.py',
                    'app/quant/signals.py', 'app/ingest/normalize.py',
                    'app/tests/quant/book/test_book_batch.py', 'app/tests/quant/test_pnl.py',
                    'app/tests/quant/test_costs_rust_parity.py', 'app/tests/quant/test_signals.py',
                    'app/tests/quant/test_signals_rust_parity.py', 'app/tests/quant/test_w21_cost_model.py',
                    'app/tests/ingest/test_normalize.py', 'app/tests/ingest/test_normalize_rust_parity.py',
                    'app/ingest/dedup.py', 'app/ingest/ratelimit.py',
                    'app/tests/ingest/test_admission_rust_parity.py',
                    '.github/workflows/rust.yml', 'tools/required_ci_gate.py', 'tools/test_queue_gates.py']
        ignored = ['docs/ci-queue-rollout.md', 'app/ingest/edgar.py', 'app/ingest/normalize_edgar.py',
                   'app/quant/signals.py.bak', 'app/quant/strategies/market_maker.py',
                   'app/tests/ingest/test_edgar_brew.py', 'app/tests/quant/test_signals_extra.py',
                   'app/workflows/morning_brew.py', 'tools/test_ci_dispatch_scope.py']
        for path in selected + ignored:
            with self.subTest(path=path):
                expected = path in selected
                self.assertEqual(label_selects(path), expected)
                self.assertEqual(rust_selects(path), expected)
        # Every selected Python source/test path is exercised by rust-book against the
        # strictly required extension, and the main benchmarks all run.
        pytest_step = next(step for step in self.rust['jobs']['rust-book']['steps']
                           if step.get('name') == 'Python book tests against the extension')
        self.assertEqual(pytest_step['env']['COCKPIT_REQUIRE_RUST'], '1')
        for test_path in ('tests/quant/test_signals.py', 'tests/quant/test_signals_rust_parity.py',
                          'tests/ingest/test_normalize.py', 'tests/ingest/test_normalize_rust_parity.py',
                          'tests/quant/book', 'tests/quant/test_pnl.py',
                          'tests/quant/test_costs_rust_parity.py', 'tests/quant/test_w21_cost_model.py',
                          'tests/ingest/test_admission_rust_parity.py'):
            self.assertIn(f' {test_path}', pytest_step['run'])
        bench_step = next(step for step in self.rust['jobs']['rust-book']['steps']
                          if step.get('name') == 'Benchmark Rust against the Python reference')
        self.assertEqual(bench_step['run'].split(),
                         ['python', 'rust/bench_compare.py', 'python', 'rust/bench_costs.py',
                          'python', 'rust/bench_normalize.py', 'python', 'rust/bench_signals.py',
                          'python', 'rust/bench_admission.py'])
        for script in ('bench_compare.py', 'bench_costs.py', 'bench_normalize.py', 'bench_signals.py',
                       'bench_admission.py'):
            self.assertTrue((ROOT / 'rust' / script).is_file(), script)

    def test_actual_rust_merge_tree_filter_with_diverged_base(self):
        step = next(s for s in self.rust['jobs']['filter']['steps'] if s.get('id') == 'gate')
        # Run the actual workflow shell and Python matrix generator against real
        # commits. No DIFF_FILES override or mocked git can hide the regression.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def git(*args):
                return subprocess.check_output(['git', '-c', 'user.name=CI Test',
                    '-c', 'user.email=ci@example.invalid', '-c', 'commit.gpgsign=false',
                    '-c', 'core.hooksPath=/dev/null', *args], cwd=root,
                    text=True, stderr=subprocess.DEVNULL).strip()

            def commit(path, content, message):
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding='utf-8')
                git('add', path)
                git('commit', '-m', message)
                return git('rev-parse', 'HEAD')

            git('init', '-b', 'main')
            ancestor = commit('README.md', 'base\n', 'base')
            git('checkout', '-b', 'docs-pr')
            pr_head = commit('docs/example.md', 'docs only\n', 'docs')
            git('checkout', 'main')
            base = commit('rust/base-only.rs', '// base-only Rust\n', 'base Rust')
            git('merge', '--no-ff', 'docs-pr', '-m', 'immutable docs merge candidate')
            candidate = git('rev-parse', 'HEAD')
            git('remote', 'add', 'origin', str(root))
            self.assertIn('rust/base-only.rs', git('diff', '--name-only', base, pr_head))
            self.assertEqual(git('diff', '--name-only', base, candidate), 'docs/example.md')

            def cells(event='pull_request', **overrides):
                output = root / 'matrix-output'
                output.write_text('', encoding='utf-8')
                # Isolate event identity from any ambient real Actions SHA.
                env = {key: value for key, value in os.environ.items()
                       if key not in {'DIFF_FILES', 'EVENT_NAME', 'EXPECTED_PR_HEAD',
                                      'MERGE_CANDIDATE', 'DIFF_BASE', 'DIFF_HEAD'}}
                env.update({'CHECK_PATHS': 'true', 'SKIP_CELLS': 'false',
                            'RUN_WINDOWS': 'true', 'RUN_MACOS': 'false',
                            'EVENT_NAME': event, 'EXPECTED_PR_HEAD': pr_head,
                            'MERGE_CANDIDATE': candidate, 'DIFF_BASE': base,
                            'DIFF_HEAD': candidate, 'GITHUB_OUTPUT': str(output),
                            **overrides})
                result = subprocess.run(['bash', '-eu', '-c', step['run']], cwd=root,
                                        env=env, text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(output.read_text().splitlines()[1])['include']

            self.assertEqual(cells(), [])
            docs_base, docs_candidate = base, candidate
            for overrides in [{'MERGE_CANDIDATE': pr_head},
                              {'EXPECTED_PR_HEAD': base}, {'MERGE_CANDIDATE': ''}]:
                self.assertEqual(len(cells(**overrides)), 4)
            git('checkout', 'docs-pr')
            self.assertEqual(len(cells(MERGE_CANDIDATE=pr_head)), 4)
            pr_head = commit('rust/pr-owned.rs', '// PR Rust\n', 'PR Rust')
            git('checkout', 'main')
            base = git('rev-parse', 'HEAD')
            git('merge', '--no-ff', 'docs-pr', '-m', 'immutable code merge candidate')
            candidate = git('rev-parse', 'HEAD')
            self.assertEqual(len(cells()), 4)
            # Fetching explicit push/queue ranges can shallow the local fixture;
            # finish constructing all merge candidates before these actual fetches.
            for event in ['push', 'merge_group']:
                self.assertEqual(cells(event, DIFF_BASE=docs_base, DIFF_HEAD=docs_candidate), [])
                self.assertEqual(len(cells(event, DIFF_BASE=ancestor, DIFF_HEAD=docs_base)), 4)
                self.assertEqual(len(cells(event, DIFF_BASE='0' * 40)), 4)

    def test_pr_concurrency_queues_old_heads_but_isolates_drafts(self):
        for doc in (self.ci, self.rust):
            self.assertFalse(evaluate(doc['concurrency']['cancel-in-progress']))
            self.assertEqual(evaluate(doc['concurrency']['queue']), 'max')
            normal = concurrency_group(doc)
            # The action and resolved merge/head SHA must not partition normal
            # PR work: opened -> synchronize -> ready all supersede old work.
            for action in ('opened', 'synchronize', 'reopened', 'ready_for_review'):
                for head in ('a' * 40, 'b' * 40):
                    with self.subTest(workflow=doc['name'], action=action, head=head):
                        self.assertEqual(normal, concurrency_group(doc, **{
                            'github.event.action': action, 'github.sha': head,
                            'github.event.pull_request.head.sha': head}))
            draft = concurrency_group(doc, **{'github.event.pull_request.draft': True})
            self.assertNotEqual(normal, draft)
            self.assertEqual(draft, concurrency_group(doc, **{
                'github.event.pull_request.draft': True,
                'github.event.pull_request.head.sha': 'b' * 40}))
            self.assertNotEqual(normal, concurrency_group(doc, **{
                'github.event.pull_request.number': 150}))

    def test_out_of_order_label_dispatch_cannot_cancel_current_work(self):
        def dispatched(label, head, resolved_head):
            # Evaluate the real dispatcher scope expression. A delayed A event
            # can resolve --ref to B while still carrying expected head A.
            scope = evaluate(self.step['env']['CI_SCOPE'], **{
                'github.event.label.name': label,
                'github.event.pull_request.labels.*.name': [label]})
            return {'github.event_name': 'workflow_dispatch',
                    'github.event.pull_request.number': '',
                    'github.event.pull_request.head.sha': '',
                    'github.ref': 'refs/heads/feature', 'github.sha': resolved_head,
                    'inputs.pr_number': '149', 'inputs.pr_head_sha': head,
                    'inputs.scope': scope}

        a, b = 'a' * 40, 'b' * 40
        for doc in (self.ci, self.rust):
            for delayed_head in (a, b):
                with self.subTest(workflow=doc['name'], delayed_head=delayed_head):
                    # Model admission before filter validation: cancellation is
                    # based only on the actual rendered concurrency key.
                    active = {}
                    cancelled = []
                    arrivals = [('ordinary-B', {'github.sha': b,
                                                'github.event.pull_request.head.sha': b}),
                                ('full-B', dispatched('ci:full', b, b)),
                                ('delayed-enhancement', dispatched('enhancement', delayed_head, b))]
                    for name, context in arrivals:
                        key = concurrency_group(doc, **context)
                        if key in active:
                            cancelled.append(active[key])
                        active[key] = name
                    self.assertEqual(cancelled, [])
                    self.assertEqual(set(active.values()), {name for name, _ in arrivals})
            # Same expected-head/scope retries coalesce, even when --ref moved;
            # different expected heads remain independent until validation.
            for label in ('ci:full', 'enhancement'):
                self.assertEqual(concurrency_group(doc, **dispatched(label, a, a)),
                                 concurrency_group(doc, **dispatched(label, a, b)))
                self.assertNotEqual(concurrency_group(doc, **dispatched(label, a, b)),
                                    concurrency_group(doc, **dispatched(label, b, b)))
        for label in ('ci:full', 'enhancement'):
            old = {'github.event.pull_request.head.sha': a, 'github.event.label.name': label}
            new = {**old, 'github.event.pull_request.head.sha': b}
            self.assertNotEqual(concurrency_group(self.label, **old),
                                concurrency_group(self.label, **new))

    def test_manual_nightly_and_queue_concurrency_isolation(self):
        for doc in (self.ci, self.rust):
            # Intentionally use the same ref to prove event-kind isolation,
            # rather than merely relying on differing ordinary ref strings.
            base = {'github.event.pull_request.number': '',
                    'github.event.pull_request.head.sha': '',
                    'github.ref': 'refs/heads/main'}
            events = [('push', ''), ('workflow_dispatch', 'full'),
                      ('workflow_dispatch', 'linux'), ('merge_group', '')]
            if doc is self.ci:
                events.append(('schedule', ''))
            keys = [concurrency_group(doc, **{**base, 'github.event_name': event,
                                             'inputs.scope': scope})
                    for event, scope in events]
            self.assertEqual(len(keys), len(set(keys)), keys)
            manual_default = concurrency_group(doc, **{**base,
                'github.event_name': 'workflow_dispatch'})
            self.assertEqual(manual_default, keys[1])
            queue = {**base, 'github.event_name': 'merge_group'}
            self.assertNotEqual(concurrency_group(doc, **queue),
                                concurrency_group(doc, **{**queue,
                                    'github.ref': 'refs/heads/gh-readonly-queue/main/pr-150'}))

    def test_merge_context_reaches_every_matrix_checkout(self):
        for doc, jobs in ((self.ci, ('filter', 'linux', 'windows', 'macos')),
                          (self.rust, ('filter', 'rust-book'))):
            inputs = doc['on']['workflow_dispatch']['inputs']
            for key in ('pr_number', 'pr_head_sha', 'merge_sha'):
                self.assertEqual(inputs[key]['default'], '')
            candidate = {'github.event_name': 'workflow_dispatch', 'inputs.scope': 'full',
                         'inputs.candidate_id': 'e' * 64, 'inputs.dispatch_nonce': 'f' * 32,
                         'inputs.pr_base_sha': 'b' * 40, 'inputs.merge_tree_sha': 'd' * 40,
                         'inputs.merge_sha': 'c' * 40}
            for job in jobs:
                checkouts = [step for step in doc['jobs'][job]['steps']
                             if step.get('uses') == 'actions/checkout@v4']
                # Exactly one conditional trusted-policy checkout (github.sha on
                # the default branch) precedes the single merge-context checkout.
                policy, merge = checkouts
                self.assertEqual(len(checkouts), 2)
                self.assertEqual(policy['with']['ref'], '${{ github.sha }}')
                self.assertEqual(policy['with']['fetch-depth'], '1')
                self.assertEqual(policy['with']['persist-credentials'], 'false')
                self.assertFalse(evaluate(policy['if']))
                self.assertFalse(evaluate(policy['if'], **{'github.event_name': 'workflow_dispatch',
                                                           'inputs.pr_number': '149',
                                                           'inputs.merge_sha': 'b' * 40}))
                self.assertTrue(evaluate(policy['if'], **candidate))
                self.assertNotIn('if', merge)
                ref = merge['with']['ref']
                self.assertEqual(evaluate(ref), 'a' * 40)
                self.assertEqual(evaluate(ref, **{'inputs.merge_sha': 'b' * 40}), 'b' * 40)
                self.assertEqual(evaluate(ref, **candidate), 'c' * 40)
                # CSL-544: linux/windows/macos run the replacement-program
                # checker via run-gates/pytest and need full history so
                # git show <approved_sha>:<path> can resolve. filter and
                # rust-book do not run that checker; they stay at depth 2
                # (label-dispatch HEAD^2 validation / rust book tests).
                if job in ('linux', 'windows', 'macos'):
                    self.assertEqual(merge['with']['fetch-depth'], '0')
                else:
                    self.assertEqual(merge['with']['fetch-depth'], '2')
        self.assertEqual(self.step['env']['PR_HEAD_SHA'], '${{ github.event.pull_request.head.sha }}')
        self.assertEqual(self.step['env']['MERGE_SHA'], '${{ github.sha }}')

    def test_actual_dispatch_context_validation_rejects_stale_and_partial_context(self):
        scripts = [next(step['run'] for step in doc['jobs']['filter']['steps']
                        if step.get('name') == 'Validate label dispatch context')
                   for doc in (self.ci, self.rust)]
        self.assertEqual(scripts[0], scripts[1])
        with tempfile.TemporaryDirectory() as tmp:
            def git(*args):
                return subprocess.check_output(['git', '-c', 'user.name=CI Test',
                    '-c', 'user.email=ci@example.invalid', '-c', 'commit.gpgsign=false',
                    '-c', 'core.hooksPath=/dev/null', *args], cwd=tmp,
                    text=True, stderr=subprocess.DEVNULL).strip()
            git('init', '-b', 'main')
            git('commit', '--allow-empty', '-m', 'base')
            git('checkout', '-b', 'feature')
            git('commit', '--allow-empty', '-m', 'head')
            head = git('rev-parse', 'HEAD')
            git('checkout', 'main')
            git('merge', '--no-ff', 'feature', '-m', 'merge candidate')
            merge = git('rev-parse', 'HEAD')
            valid = {'PR_NUMBER': '149', 'PR_HEAD_SHA': head,
                     'MERGE_SHA': merge, 'GITHUB_SHA': head}
            for changes, expected in [({}, True), ({'GITHUB_SHA': 'f'*40}, False),
                    ({'PR_HEAD_SHA': 'f'*40}, False), ({'MERGE_SHA': head}, False),
                    ({'PR_NUMBER': ''}, False), ({'PR_NUMBER': '149;echo unsafe'}, False),
                    ({'MERGE_SHA': ''}, False),
                    ({'PR_NUMBER': '', 'PR_HEAD_SHA': '', 'MERGE_SHA': ''}, True)]:
                with self.subTest(changes=changes):
                    result = subprocess.run(['bash', '-c', scripts[0]], cwd=tmp,
                        capture_output=True, check=False, env={**os.environ, **valid, **changes})
                    self.assertEqual(result.returncode == 0, expected, result.stderr)



# CSL-416 guarded the admission surface of the Claude writers (claude.yml,
# claude-conflict-writer.yml, conflict-scan.yml) and the CSL-474 P9 probe workflow.
# CSL-238 (Lucas, Nov 16, 2026) retired every automated reviewer and writer except
# the Devin GitHub App, so those workflows are gone. What stays is the property the
# guard protected: no workflow holds model credentials, runs a model, or can push.
MODEL_ACCESS = re.compile(r'ANTHROPIC_API_KEY|CLAUDE_CODE_OAUTH_TOKEN|anthropics/claude-code|'
                          r'CODEX_TRIGGER_PAT|OPENAI_API_KEY|GEMINI_API_KEY|GOOGLE_API_KEY|gemini-cli|run-gemini|'
                          r'OPENROUTER|DEEPSEEK|XAI_API_KEY|MISTRAL_API_KEY|actions/ai-inference|gh-models',
                          re.IGNORECASE)
HOSTED = {'ubuntu-latest', 'ubuntu-24.04', 'ubuntu-22.04', 'windows-2025', 'windows-latest', 'macos-latest'}
# The Linear PR sync (kept on the owner's word, Nov 16, 2026) reads exactly one secret, by name; no other
# workflow reads any. Names are upper case (GitHub reads them case-insensitively). Variables: none anywhere.
ADMITTED_SECRETS = {'linear-pr-sync.yml': {'LINEAR_API_KEY'}}
ADMITTED_VARS = {}
# The only spellings of the secrets or vars context that may stand anywhere in a workflow's text, outside
# YAML comments, per file. Everything else that contains the word `secrets` or `vars` (any case) is refused,
# whether or not the expression parser understood it.
ADMITTED_REFS = {name: {'secrets.' + n for n in names} for name, names in ADMITTED_SECRETS.items()}
# Token permissions above read, admitted by workflow file, by job (None = the workflow-level block) and by
# scope. Exactly one is in the tree: ci-label-retrigger.yml dispatches CI (actions). The `benchmarks` job
# of ci.yml no longer posts a pull request comment (the owner's decision, Nov 16, 2026), so no workflow
# holds issues or pull-requests write, and a comment posted through gh, the API or github-script is
# refused in every workflow. `models` counts as above read at ANY level (read is enough to call a model),
# and `read-all` / `write-all` are refused outright: every workflow names its scopes.
# `contents` and `id-token` are never admissible, whatever this table says.
ADMITTED_WRITES = {('ci-label-retrigger.yml', None): {'actions'}}
# Lines of a run script that may contain the token `push` (see below), by workflow file; each with its reason.
# The key is the line after the normalisation described below, stripped.
ADMITTED_PUSH_LINES = {
    ('ci.yml', ('echo ::error::Cannot fetch the pinned Recursion route contract commit $pin '
               '(successor/route_consumer/contract/PIN.json, source.head_sha). The pin must be reachable from '
               'CSL-Laboratories/recursion master or a protected tag; a draft PR head can disappear after a push, '
               'rebase or squash-merge. Re-pin PIN.json to a commit on recursion master.')):
        'an error message that explains why a pinned commit can vanish; it runs no git command',
    ('rust.yml', '# Push and queue events retain their explicit before/base -> head range.'):
        'a shell comment about the push event trigger, not a command',
}

# THE CAPABILITY LAYER'S ALLOWLISTS (CSL-238, the answer to DeepSeek's third read). Every entry is a decision of
# the owner (Lucas), with one line of reason. The tables pin today's tree: a new entry is a decision, not an edit.
# 1. Jobs whose EFFECTIVE token permissions (the job's own block, else the workflow's, else UNSPECIFIED) hold a
#    scope above read (`models` at any level; any whole-token string such as `write-all`) or are UNSPECIFIED.
#    (file, job) -> (the exact scopes, or UNSPECIFIED; the reason). Both jobs of ci-label-retrigger.yml inherit
#    the workflow-level `actions: write`. No job has unspecified permissions today.
UNSPECIFIED = 'UNSPECIFIED'
ADMITTED_JOB_POWER = {
    ('ci-label-retrigger.yml', 'retrigger'): (
        frozenset({'actions'}), 'dispatches ci.yml and rust.yml for a labelled pull request (gh workflow run)'),
    ('ci-label-retrigger.yml', 'cancel-ancestors'): (
        frozenset({'actions'}), 'cancels verified obsolete pull request CI runs (scripts/ci/ci_cancel_controller.py)'),
}
# 2. Secrets (other than GITHUB_TOKEN) that a workflow or a local composite action may name: (file, name) -> reason.
#    Files are the workflow's name under .github/workflows, or an action's path from the repository root.
ADMITTED_SECRET_USE = {
    ('linear-pr-sync.yml', 'LINEAR_API_KEY'):
        'the Linear pull request sync, kept on the owner\'s word; it runs on manual dispatch only today',
}
# 3. Triggers that run with the base repository's rights on a pull request's behalf: (file, event) -> reason.
DANGEROUS_TRIGGERS = ('pull_request_target', 'workflow_run')
ADMITTED_TRIGGERS = {
    ('ci-label-retrigger.yml', 'workflow_run'):
        'job cancel-ancestors cancels obsolete pull request CI runs; its policy script is read from the default branch',
}

# THE SHAPE OF THIS GUARD. Two layers. The CAPABILITY LAYER (`guard_tree_violations`, below `writer_guard_violations`)
# checks what the tree lets a job DO: the shape of .github, the token permissions, the secrets, the triggers and the
# local composite actions. The TEXT LAYER reads workflow TEXT (it never runs anything), from the strongest line to the
# weakest, each failing CLOSED (a text it cannot read to the end with certainty is refused). A command assembled at
# run time has no fixed spelling and is outside any text scan: the capability layer is what bounds it.
# 1. RAW TEXT, no expression parsing: every occurrence of the word `secrets` or `vars` (any case) in the
#    text of any YAML scalar (comments excluded; the decoded value of every string is checked as well) must
#    start one of the exact spellings in ADMITTED_REFS for that file, else the workflow is refused.
# 2. PARSED: an expression ends at the first `}}` outside a single-quoted string, as GitHub's runner reads it
#    (a doubled quote is an escaped quote); an expression that does not end is refused; every reference to
#    the secrets or vars context inside it must be in ADMITTED_SECRETS / ADMITTED_VARS. Every token
#    permission above read (`models` at any level, `read-all`, `write-all` included) must be in
#    ADMITTED_WRITES by file, job and scope; every job runs on a GitHub-hosted runner and calls no remote
#    reusable workflow. A workflow that cannot reach a secret or a write scope cannot push to this
#    repository, post to a pull request or call a model with the repository token, whatever its scripts say.
# 3. THE WORD `push`: in every run script (and github-script `script`, `args`, `entrypoint`, `command`), after
#    joining backslash-newline pairs and deleting quote characters, backslashes, backticks and carets, the
#    token `push` (any case, not part of a longer word) may not appear at all, unless that exact line is in
#    ADMITTED_PUSH_LINES. A harmless line that merely mentions the word (`echo "no push here"`, a commit
#    message, a shell comment) is refused too, on purpose: it must be admitted by name or reworded.
# 4. A command scan (git push, gh writes, an HTTP client with a write method against GitHub, label, comment and
#    contents API calls) as a last line.
# WHAT NO SCAN OF TEXT CAN SEE: a script or action fetched or assembled at run time (curl | sh, a downloaded
# file, an action whose ref moves), a script committed in the repository that a step calls
# (`bash scripts/push.sh`: the word `push` in a file name is refused, a script's body is not read), an encoded
# or built command (`base64 -d | sh`, `cmd=pu; git ${cmd}sh`, `git pu{sh,}`, `$'push'`, an alias made from
# parts, `eval`), a reusable workflow elsewhere (refused when named, but its content is not read), what a
# `fromJson` matrix yields at run time (only the producer's runner literals are read), the rights an
# organization default or a token held outside this repository gives, and what a step may do with the one
# admitted write (`gh workflow disable`, `gh run cancel`): the table admits a scope for a file by name, not a
# use of it. Triggers are checked for `pull_request_target` and `workflow_run` only (the capability layer); the
# other events that run with the base repository's rights (`issue_comment`, `pull_request_review`, `issues`,
# `repository_dispatch`) are not. GitHub's own reading of expressions (where `${{`
# ends, whether `read-all` carries `models`) was read from the runner's public source and its documentation,
# not observed in a run.
_NOT_SHELL = {'name', 'if', 'description', 'group'}
_RUN_KEYS = {'run', 'script', 'args', 'entrypoint', 'command', 'cmd'}
_PUSH_WORD = re.compile(r'(?<![A-Za-z0-9_])push(?![A-Za-z0-9_])', re.IGNORECASE)
_CONTEXT_WORD = re.compile(r'(?<![A-Za-z0-9_])(secrets|vars)(?![A-Za-z0-9_])', re.IGNORECASE)
_GH_VALUE_OPTIONS = {'-R', '--repo', '-X', '--method', '-f', '-F', '--field', '--raw-field', '-H', '--header',
                     '-q', '--jq', '--input', '-t', '--template', '--hostname', '-b', '--body', '--title'}
_CONTENT_ENDPOINT = re.compile(r'/(?:contents|git/(?:refs|commits|trees|blobs|tags)|merges?|releases)(?:/|$|\?)'
                               r'|/pulls/[^/]+/merge|/branches/[^/]+/(?:protection|rename)')
_LABEL_ENDPOINT = re.compile(r'/labels(?:/|$|\?)')
_COMMENT_ENDPOINT = re.compile(r'/(?:issues|pulls)/[^/]+/comments(?:/|$|\?)|/issues/comments/|/pulls/comments/')
_COMMENT_API = re.compile(r'createComment|updateComment|deleteComment|createReview\b')
_CONTENT_API = re.compile(r'createOrUpdateFileContents|deleteFile|git\.(?:createRef|updateRef|deleteRef|'
                          r'createCommit|createTree|createBlob|createTag)|pulls\.merge|repos\.merge|'
                          r'createRelease|uploadReleaseAsset')
_LABEL_API = re.compile(r'addLabels|setLabels|removeLabel|removeAllLabels|createLabel|updateLabel|deleteLabel')
_CONTEXT = re.compile(r'(?<![\w.-])(secrets|vars)(?![\w-])\s*(?:\.\s*([A-Za-z0-9_*-]+)|\[\s*([\'"])(.*?)\3\s*\])?',
                      re.IGNORECASE | re.DOTALL)


def _scalars(node, key=None):
    """Every string value of the parsed workflow, except names, conditions and descriptions."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _scalars(v, str(k))
    elif isinstance(node, list):
        for v in node:
            yield from _scalars(v, key)
    elif isinstance(node, str) and key not in _NOT_SHELL:
        yield node


def _all_strings(node):
    """Every string of the parsed workflow, keys and values, nothing left out."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield str(k)
            yield from _all_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _all_strings(v)
    elif isinstance(node, str):
        yield node


def _scalar_texts(text):
    """The raw text of every YAML scalar (comments are not scalars), from the scanner's own marks."""
    return [text[t.start_mark.index:t.end_mark.index] for t in yaml.scan(text) if isinstance(t, yaml.ScalarToken)]


def _context_word_refused(name, texts):
    """True when a text holds the word secrets or vars (any case) outside the admitted exact spellings."""
    allowed = ADMITTED_REFS.get(name, set())
    for t in texts:
        for m in _CONTEXT_WORD.finditer(t):
            if not any(t.startswith(ref, m.start()) and not re.match(r'[A-Za-z0-9_]', t[m.start() + len(ref):][:1] or ' ')
                       for ref in allowed):
                return True
    return False


def _expressions(text):
    """Bodies of every ${{ }} in a string, each ended at the first `}}` outside a single-quoted string (a
    doubled quote is an escaped quote); None when one does not end, which is refused."""
    bodies, i = [], 0
    while True:
        i = text.find('${{', i)
        if i < 0:
            return bodies
        j, quoted = i + 3, False
        while j < len(text):
            if text[j] == "'":
                quoted = not quoted
            elif not quoted and text.startswith('}}', j):
                break
            j += 1
        else:
            return None
        bodies.append(text[i + 3:j])
        i = j + 2


def _context_refs(node, key=None):
    """{('secrets'|'vars', NAME)} for every use of either context in any expression; '*' for a wildcard,
    a dynamic index, the whole object or `secrets: inherit`; ('unreadable', '*') for an expression that does
    not end."""
    refs = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k).lower() == 'secrets' and isinstance(v, str) and '${{' not in v:
                refs.add(('secrets', '*'))
            refs |= _context_refs(v, str(k))
    elif isinstance(node, list):
        for v in node:
            refs |= _context_refs(v, key)
    elif isinstance(node, str):
        bodies = _expressions(node)
        if bodies is None:
            refs.add(('unreadable', '*'))
            bodies = []
        if key == 'if' and not bodies and '${{' not in node:
            bodies = [node]
        for body in bodies:
            for m in _CONTEXT.finditer(body):
                refs.add((m.group(1).lower(), (m.group(2) or m.group(4) or '*').upper()))
    return refs


def _run_scripts(node, key=None):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _run_scripts(v, str(k))
    elif isinstance(node, list):
        for v in node:
            yield from _run_scripts(v, key)
    elif isinstance(node, str) and key in _RUN_KEYS:
        yield node


def _push_lines(script):
    """Lines of a script that hold the token `push` once quotes, backslashes, backticks and carets are gone."""
    flat = re.sub(r'[\"\'\\`^]', '', re.sub(r'\\\r?\n', '', script))
    return [line.strip() for line in flat.split('\n') if _PUSH_WORD.search(line)]


def _shell_segments(text, depth=0):
    """Split shell-ish text into segments of normalised words. Separators (&& || ; | & newline) count only
    outside quotes; quotes and backslashes are removed inside words (`pu""sh`, `p\\ush`, a backslash-newline
    pair joins the word); `, [ ] { } ( ) ` < >` and blanks end a word; a quoted string that holds blanks is
    read again as a command (bash -c '...')."""
    segs, words, word, inner = [], [], [], []
    quote, i, n = None, 0, len(text)

    def end_word():
        if word:
            words.append(''.join(word))
            word.clear()

    def end_segment():
        end_word()
        if words:
            segs.append(list(words))
            words.clear()

    while i < n:
        c = text[i]
        if quote:
            if c == quote:
                quote, said = None, ''.join(inner)
                inner.clear()
                if depth < 3 and re.search(r'[\s;&|]', said):
                    segs.extend(_shell_segments(said, depth + 1))
            elif c == '\\' and quote == '"' and i + 1 < n:
                i += 1
                inner.append(text[i])
                word.append(text[i])
            else:
                inner.append(c)
                word.append(c)
        elif c in '"\'':
            quote = c
        elif c == '\\' and i + 1 < n:
            i += 1
            if text[i] == '\r' and text[i + 1:i + 2] == '\n':
                i += 1
            elif text[i] != '\n':
                word.append(text[i])
        elif c in ' \t\r,[]{}()`<>':
            end_word()
        elif c in '\n;|':
            end_segment()
        elif c == '&':
            if (i and text[i - 1] in '<>') or text[i + 1:i + 2] == '>':
                end_word()
            else:
                end_segment()
        else:
            word.append(c)
        i += 1
    if quote and depth < 3 and inner:
        segs.extend(_shell_segments(''.join(inner), depth + 1))
    end_segment()
    return segs


_HTTP_CLIENTS = {'curl', 'curl.exe', 'wget', 'wget.exe', 'invoke-webrequest', 'invoke-restmethod', 'iwr', 'irm',
                 'http', 'https', 'xh'}
_HTTP_WRITE = {'PUT', 'POST', 'PATCH', 'DELETE'}
_GITHUB_WORD = re.compile(r'github|gh_token', re.IGNORECASE)
_CURL_ARG_SHORT = set('AbcCdDeEFHKmoPQrtTuUwxXyYz')
_CURL_BODY_LONG = {'--data', '--data-raw', '--data-binary', '--data-ascii', '--data-urlencode', '--form',
                   '--form-string', '--upload-file', '--json'}


def _http_write(client, rest):
    """True when one call of an HTTP client (curl, wget, Invoke-WebRequest, Invoke-RestMethod, httpie) carries a
    write method (PUT, POST, PATCH, DELETE): by an explicit method option, or by sending a body, which makes the
    client POST. A literal form only; a method or an address assembled at run time is not seen."""
    method, body, query = '', False, False
    if client in ('curl', 'curl.exe'):
        for k, w in enumerate(rest):
            nxt = rest[k + 1] if k + 1 < len(rest) else ''
            if w.startswith('--'):
                opt, _, val = w.partition('=')
                if opt == '--request':
                    method = (val or nxt).upper()
                elif opt in _CURL_BODY_LONG:
                    body = True
                elif opt == '--get':
                    query = True
            elif w.startswith('-') and len(w) > 1:
                for at, ch in enumerate(w[1:], 1):
                    if ch in _CURL_ARG_SHORT:  # the rest of the word, or the next word, is this option's value
                        if ch == 'X':
                            method = (w[at + 1:] or nxt).upper()
                        elif ch in 'dFT':
                            body = True
                        break
                    if ch == 'G':
                        query = True
        return method in _HTTP_WRITE or (not method and body and not query)
    if client in ('wget', 'wget.exe'):
        for k, w in enumerate(rest):
            opt, _, val = w.partition('=')
            if opt == '--method':
                method = (val or (rest[k + 1] if k + 1 < len(rest) else '')).upper()
            elif opt in ('--post-data', '--post-file', '--body-data', '--body-file'):
                body = True
        return method in _HTTP_WRITE or (not method and body)
    if client in ('invoke-webrequest', 'invoke-restmethod', 'iwr', 'irm'):
        for k, w in enumerate(rest):
            if w.startswith('-') and len(w) > 1:
                param, _, val = w[1:].partition(':')
                param = param.lower()
                if len(param) >= 2 and 'method'.startswith(param):
                    method = (val or (rest[k + 1] if k + 1 < len(rest) else '')).upper()
                elif param and 'body'.startswith(param) or len(param) >= 4 and 'infile'.startswith(param):
                    body = True
        return method in _HTTP_WRITE or (not method and body)
    positional = [w for w in rest if not w.startswith('-')]  # httpie: a method word, or key=value items
    return bool(positional) and (positional[0].upper() in _HTTP_WRITE or
                                 any(re.match(r'[A-Za-z0-9_.\[\]-]+:?=[^=]', w) for w in positional))


def _command_violations(text):
    """Last line: refuse a push, a write to repository contents, a write through an HTTP client or `gh api`, a
    label write, a pull request comment or a model call wherever the words stand (see the comment above for what
    this is and is not)."""
    out = set()
    text = text.replace('${IFS}', ' ').replace('$IFS', ' ')
    if _CONTENT_API.search(text):
        out.add('api writes contents')
    if _LABEL_API.search(text):
        out.add('label write')
    if _COMMENT_API.search(text):
        out.add('pull request comment')
    for words in _shell_segments(text):
        base = [w.rsplit('/', 1)[-1].lower() for w in words]
        if 'git-push' in base or 'git-send-pack' in base or 'git-receive-pack' in base:
            out.add('git push')
        git_at = next((i for i, b in enumerate(base) if b in ('git', 'git.exe')), None)
        if git_at is not None and {'push', 'send-pack'} & set(words[git_at + 1:]):
            out.add('git push')
        if 'gh' in base:
            rest = words[base.index('gh') + 1:]
            pos, skip = [], False
            for w in rest:  # the command words, minus options and the values of the options that take one
                if skip:
                    skip = False
                elif w in _GH_VALUE_OPTIONS:
                    skip = True
                elif not w.startswith('-'):
                    pos.append(w)
            if pos[:1] in (['models'], ['copilot']):
                out.add('model call')
            if pos[:2] in (['pr', 'merge'], ['pr', 'create'], ['repo', 'sync'], ['repo', 'create'],
                           ['repo', 'delete'], ['release', 'create'], ['release', 'upload'],
                           ['release', 'delete'], ['release', 'edit']):
                out.add('gh writes contents')
            if pos[:2] in (['pr', 'comment'], ['issue', 'comment']):
                out.add('pull request comment')
            if pos[:2] in (['pr', 'ready'], ['pr', 'close'], ['pr', 'reopen'], ['pr', 'review'], ['pr', 'edit']):
                out.add('gh writes a pull request')
            if pos[:2] in (['label', 'create'], ['label', 'edit'], ['label', 'delete'], ['label', 'clone']) or \
                    any(w.startswith(('--add-label', '--remove-label')) for w in rest):
                out.add('label write')
            if pos[:1] == ['api'] and len(pos) > 1:
                method = ''
                for i, w in enumerate(rest):
                    if w in ('-X', '--method') and i + 1 < len(rest):
                        method = rest[i + 1].upper()
                    elif w.startswith('--method='):
                        method = w.split('=', 1)[1].upper()
                fields = any(w in ('-f', '-F', '--field', '--raw-field', '--input') for w in rest)
                if method in ('PUT', 'POST', 'PATCH', 'DELETE') or (not method and fields):
                    if pos[1].lower() != 'graphql' or 'mutation' in ' '.join(rest).lower():
                        out.add('github api write')
                    if _CONTENT_ENDPOINT.search(pos[1]):
                        out.add('gh writes contents')
                    if _LABEL_ENDPOINT.search(pos[1]):
                        out.add('label write')
                    if _COMMENT_ENDPOINT.search(pos[1]):
                        out.add('pull request comment')
        for at, client in enumerate(base):  # an HTTP client call with a write method, with GitHub named in it
            if client in _HTTP_CLIENTS and _http_write(client, words[at + 1:]) and any(_GITHUB_WORD.search(w) for w in words):
                out.add('github api write')
    return out


def _written_scopes(doc):
    """{job or None: set of token scopes above read}; '*' for any whole-token string (`read-all`, `write-all`);
    `models` at any level."""
    found = {}
    blocks = [(None, doc.get('permissions'))] + [(n, j.get('permissions')) for n, j in (doc.get('jobs') or {}).items()]
    for job, block in blocks:
        if isinstance(block, str):
            found.setdefault(job, set()).add('*')
            continue
        for scope, level in (block or {}).items():
            if str(level).strip().lower() not in ('read', 'none') or str(scope).strip().lower() == 'models':
                found.setdefault(job, set()).add(str(scope).strip().lower())
    return found


def _runner_values(doc, job):
    runner = job.get('runs-on')
    if isinstance(runner, list):
        values = []
        for item in runner:
            sub = _runner_values(doc, {**job, 'runs-on': item})
            if sub is None:
                return None
            values += sub
        return values
    if not isinstance(runner, str):
        return None
    hit = re.fullmatch(r'\$\{\{\s*matrix\.([A-Za-z0-9_-]+)\s*\}\}', runner.strip())
    if not hit:
        return None if '${{' in runner else [runner]
    key, matrix = hit.group(1), (job.get('strategy') or {}).get('matrix')
    if isinstance(matrix, dict):
        values = matrix.get(key, [])
        values = list(values) if isinstance(values, list) else [values]
        values += [i[key] for i in matrix.get('include') or [] if isinstance(i, dict) and key in i]
        return [str(v) for v in values] or None
    producer = re.fullmatch(r'\$\{\{\s*fromJson\(\s*needs\.([A-Za-z0-9_-]+)\.outputs\.[A-Za-z0-9_-]+\s*\)\s*\}\}',
                            str(matrix or '').strip())
    if producer and producer.group(1) in (doc.get('jobs') or {}):
        literals = [v for s in _scalars(doc['jobs'][producer.group(1)])
                    for v in re.findall(r'["\']' + re.escape(key) + r'["\']\s*:\s*["\']([^"\']+)["\']', s)]
        return literals or None
    return None


def _runner_violations(doc):
    out = []
    for name, job in (doc.get('jobs') or {}).items():
        if 'uses' in job:
            if not str(job['uses']).startswith('./.github/workflows/') or str(job.get('secrets')) == 'inherit':
                out.append((name, 'reusable workflow'))
            continue
        values = _runner_values(doc, job)
        if not values or any(v not in HOSTED for v in values):
            out.append((name, 'runner'))
    return out


def writer_guard_violations(name, text):
    out = []
    if MODEL_ACCESS.search(text):
        out.append('model credential')
    if re.search(r'(?i)contents:\s*[\'"]?write|permissions:\s*[\'"]?write-all', text):
        out.append('contents write')
    if re.search(r'(?i)id-token:\s*[\'"]?write', text):
        out.append('id-token')
    try:
        doc = yaml.safe_load(text) or {}
        raw = _scalar_texts(text)
    except yaml.YAMLError:
        return out + ['unparseable']
    if not isinstance(doc, dict):
        return out + ['unparseable']
    # 1. raw text, no expression parsing
    if _context_word_refused(name, raw + list(_all_strings(doc))):
        out.append('secret')
    # 2. parsed
    refs = _context_refs(doc)
    if ('unreadable', '*') in refs:
        out.append('unreadable expression')
    if {n for c, n in refs if c == 'secrets'} != ADMITTED_SECRETS.get(name, set()) and 'secret' not in out:
        out.append('secret')
    if {n for c, n in refs if c == 'vars'} != ADMITTED_VARS.get(name, set()):
        out.append('variable')
    scopes = _written_scopes(doc)
    granted = {s for v in scopes.values() for s in v}
    if 'contents' in granted and 'contents write' not in out:
        out.append('contents write')
    if 'id-token' in granted and 'id-token' not in out:
        out.append('id-token')
    if scopes != {job: s for (file, job), s in ADMITTED_WRITES.items() if file == name}:
        out.append('permission write')
    if doc.get('permissions') is None:
        out.append('no permissions block')
    out += [why for _, why in _runner_violations(doc)]
    # 3. the word push
    for script in _run_scripts(doc):
        if any((name, line) not in ADMITTED_PUSH_LINES for line in _push_lines(script)):
            out.append('push word')
            break
    # 4. command scan
    for scalar in _scalars(doc):
        out += sorted(_command_violations(scalar) - set(out))
    return out



# ---------------------------------------------------------------------------------------------------------------
# THE CAPABILITY LAYER (CSL-238, DeepSeek round 3). It does not read what a job SAYS; it checks what a job is ABLE
# to do. A scan of shell text can never be complete (a command assembled at run time has no fixed spelling), so
# these checks are made on the parsed tree, whatever the scripts contain:
#   a. THE TREE. Every workflow lies directly under .github/workflows. A .yml or .yaml file in a subfolder of it
#      is refused. GitHub runs no workflow from a subfolder (its documentation says so; not observed in a run
#      here), so nothing there can write, and refusing the shape means nothing can hide there either. A symlink
#      under .github/workflows or .github/actions is refused too.
#   b. PERMISSIONS. Each job's effective `permissions` (its own block, else the workflow's, else UNSPECIFIED): a
#      job that holds a scope above read, or is UNSPECIFIED, must be in ADMITTED_JOB_POWER by file and job, with
#      exactly those scopes. A new job with write power, or one that drops its block, is refused.
#   c. SECRETS. Every `secrets.<NAME>` other than GITHUB_TOKEN that a workflow or a local composite action names
#      (the bare word `secrets` counts as `*`) must be in ADMITTED_SECRET_USE.
#   d. TRIGGERS. `pull_request_target` and `workflow_run` must be in ADMITTED_TRIGGERS.
#   e. LOCAL COMPOSITE ACTIONS (`uses: ./...`, and every action.yml under .github/actions). They run with the
#      job's token, so (b) bounds their power. Each is read for (c); it must lie under .github/actions and be a
#      composite (a JavaScript or Docker action's code is not read, so it is refused); its `run:` steps go to the
#      text scan as well.
# The text scan above (the word `push`, the command scan) stays as the second layer. THE LIMITS, stated: a command
# assembled at run time is outside any text scan; layers (b) to (d) are what bound it. A job whose token holds no
# write scope and which names no secret can still run what it likes inside the runner (read the repository,
# call a network host without a credential, spend minutes); a script that a step calls is not read; a third-party
# action (`uses: owner/name@ref`) is not read and runs with the job's token; the use made of an admitted write
# (`actions: write` can disable or cancel workflows) is not checked; the repository's and the organization's
# default token permission, and what a ruleset or a branch protection allows, are not visible from the tree.

def _read_text(path):
    try:
        return path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return None


def _block_power(block):
    """The scopes above read of one `permissions` value; `*` for any whole-token string (or any shape that is
    not a mapping); `models` counts at any level."""
    if not isinstance(block, dict):
        return {'*'}
    power = set()
    for scope, level in block.items():
        name = str(scope).strip().lower()
        if str(level).strip().lower() not in ('read', 'none') or name == 'models':
            power.add(name)
    return power


def _job_power(doc, job):
    """The token power a job ends up with: a set of scopes above read (empty = read only), or UNSPECIFIED."""
    if isinstance(job, dict) and job.get('permissions') is not None:
        return _block_power(job['permissions'])
    if doc.get('permissions') is not None:
        return _block_power(doc['permissions'])
    return UNSPECIFIED


def _jobs_with_power(doc):
    """{job: frozenset of scopes | UNSPECIFIED} for every job that holds a write scope or is unspecified."""
    jobs = doc.get('jobs')
    held = {}
    for name, job in (jobs.items() if isinstance(jobs, dict) else []):
        power = _job_power(doc, job)
        if power == UNSPECIFIED or power:
            held[str(name)] = power if power == UNSPECIFIED else frozenset(power)
    return held


def _events(doc):
    """The lower-case event names a workflow is triggered by. `on` is the key `True` under safe_load."""
    found = set()
    for key in ('on', True):
        value = doc.get(key)
        if isinstance(value, str):
            found.add(value.strip().lower())
        elif isinstance(value, (list, dict)):
            found |= {str(event).strip().lower() for event in value}
    return found


_SECRET_NAME = re.compile(r'(?<![A-Za-z0-9_])secrets(?![A-Za-z0-9_])\s*(?:\.\s*([A-Za-z0-9_-]+)|\[\s*([\'"])(.*?)\2\s*\])?',
                          re.IGNORECASE | re.DOTALL)


def _secret_names(texts):
    """Upper-case names after the word `secrets` in any text; `*` when the word stands without a name."""
    return {(m.group(1) or m.group(3) or '*').upper() for t in texts for m in _SECRET_NAME.finditer(t)}


def _local_action_refs(node):
    """Every `uses: ./...` of a parsed workflow or action (a step, or a job's reusable workflow)."""
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key) == 'uses' and isinstance(value, str):
                if value.strip().startswith('./'):
                    yield value.strip()
            else:
                yield from _local_action_refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _local_action_refs(value)


def action_guard_violations(key, text):
    """The text layer for a local composite action: no model access, no mention of `secrets` or `vars`, the word
    `push` only on an admitted line, and the command scan over every scalar. `key` is its path from the root."""
    out = []
    if MODEL_ACCESS.search(text):
        out.append('model credential')
    try:
        doc = yaml.safe_load(text) or {}
        raw = _scalar_texts(text)
    except yaml.YAMLError:
        return out + ['unparseable']
    if not isinstance(doc, dict):
        return out + ['unparseable']
    if _context_word_refused(key, raw + list(_all_strings(doc))):
        out.append('secret')
    for script in _run_scripts(doc):
        if any((key, line) not in ADMITTED_PUSH_LINES for line in _push_lines(script)):
            out.append('push word')
            break
    for scalar in _scalars(doc):
        out += sorted(_command_violations(scalar) - set(out))
    return out


def guard_tree_violations(root):
    """The whole guard over one repository tree: [(path, layer, what)], layers `tree`, `permissions`, `secrets`,
    `triggers`, `composite` and `text`. Empty when the tree passes."""
    root = Path(root)
    workflows, actions = root / '.github' / 'workflows', root / '.github' / 'actions'
    out, top, queue, seen = [], [], [], set()

    def rel(path):
        return path.relative_to(root).as_posix()

    def secrets_layer(key, texts):
        for name in sorted(_secret_names(texts) - {'GITHUB_TOKEN'}):
            if (key, name) not in ADMITTED_SECRET_USE:
                out.append((key, 'secrets', 'names secrets.' + name + ', which is not admitted'))

    def want(owner, ref):
        target = Path(os.path.normpath(root / ref[2:]))
        if target.suffix.lower() in ('.yml', '.yaml'):
            return  # a reusable workflow: it is a workflow file and is read as one
        if target != root and root not in target.parents:
            out.append((owner, 'composite', 'local action ' + ref + ' lies outside the repository'))
            return
        files = [target / n for n in ('action.yml', 'action.yaml') if (target / n).is_file()]
        if not files:
            out.append((owner, 'composite', 'local action ' + ref + ' has no action.yml: its code is not read'))
        if actions != target and actions not in target.parents:
            out.append((owner, 'composite', 'local action ' + ref + ' lies outside .github/actions'))
        queue.extend(files)

    # a. the tree
    for base in (workflows, actions):
        for path in sorted(base.rglob('*')) if base.is_dir() else []:
            if path.is_symlink():
                out.append((rel(path), 'tree', 'symlink under ' + rel(base)))
            elif base == workflows and path.is_file() and path.suffix.lower() in ('.yml', '.yaml'):
                if path.parent != workflows:
                    out.append((rel(path), 'tree', 'workflow in a subfolder of .github/workflows (GitHub runs none)'))
                else:
                    top.append(path)
            elif base == actions and path.is_file() and path.name.lower() in ('action.yml', 'action.yaml'):
                queue.append(path)
    # every workflow: the text layer, then permissions, secrets and triggers
    for path in top:
        text = _read_text(path)
        if text is None:
            out.append((rel(path), 'tree', 'unreadable file'))
            continue
        out += [(rel(path), 'text', kind) for kind in writer_guard_violations(path.name, text)]
        try:
            doc = yaml.safe_load(text)
            raw = _scalar_texts(text)
        except yaml.YAMLError:
            continue
        if not isinstance(doc, dict):
            continue
        for job, power in sorted(_jobs_with_power(doc).items()):
            admitted = ADMITTED_JOB_POWER.get((path.name, job))
            if admitted is None or admitted[0] != power:
                out.append((rel(path), 'permissions', 'job ' + job + ' holds ' +
                            (power if power == UNSPECIFIED else ' '.join(sorted(power))) + ', which is not admitted'))
        secrets_layer(path.name, raw + list(_all_strings(doc)))
        events = _events(doc)
        if not events:
            out.append((rel(path), 'triggers', 'no readable trigger'))
        for event in sorted(events & set(DANGEROUS_TRIGGERS)):
            if (path.name, event) not in ADMITTED_TRIGGERS:
                out.append((rel(path), 'triggers', event + ' is not admitted'))
        for ref in sorted(set(_local_action_refs(doc))):
            want(rel(path), ref)
    # e. every local composite action, once
    while queue:
        path = queue.pop(0)
        key = rel(path)
        if key in seen:
            continue
        seen.add(key)
        text = _read_text(path)
        if text is None:
            out.append((key, 'composite', 'unreadable file'))
            continue
        out += [(key, 'composite', kind) for kind in action_guard_violations(key, text)]
        try:
            doc = yaml.safe_load(text) or {}
            raw = _scalar_texts(text)
        except yaml.YAMLError:
            continue
        if not isinstance(doc, dict):
            continue
        runs = doc.get('runs')
        if not isinstance(runs, dict) or str(runs.get('using')).strip().lower() != 'composite':
            out.append((key, 'composite', 'not a composite action: its code is not read'))
        secrets_layer(key, raw + list(_all_strings(doc)))
        for ref in sorted(set(_local_action_refs(doc))):
            want(key, ref)
    return out


class NoAutomatedWriterTests(unittest.TestCase):
    def test_no_workflow_holds_model_credentials(self):
        found = {p.name for p in WORKFLOWS.glob('*.y*ml') if MODEL_ACCESS.search(p.read_text())}
        self.assertEqual(found, set())

    def test_no_workflow_can_push_or_write_contents(self):
        for path in sorted(WORKFLOWS.glob('*.y*ml')):
            text = path.read_text()
            with self.subTest(path=path.name):
                doc = yaml.safe_load(text)
                self.assertNotIn('git push', _command_violations('\n'.join(_scalars(doc))))
                granted = {s for v in _written_scopes(doc).values() for s in v}
                self.assertEqual(granted & {'contents', 'id-token', '*'}, set())

    def test_retired_writer_workflows_are_absent(self):
        for name in ('claude.yml', 'claude-conflict-writer.yml', 'conflict-scan.yml',
                     'csl-474-p9-probes.yml', 'stuck-thread-watch.yml'):
            self.assertFalse((WORKFLOWS / name).exists(), name)

    def test_linear_pr_sync_is_kept(self):
        # CSL-238, Lucas, Nov 16, 2026: "Keep in both". Five workflows are retired, not six.
        self.assertTrue((WORKFLOWS / 'linear-pr-sync.yml').exists())
        self.assertTrue((ROOT / '.github' / 'scripts' / 'linear_pr_sync.py').exists())

    def test_secrets_are_admitted_by_name_for_one_workflow(self):
        for path in sorted(WORKFLOWS.glob('*.y*ml')):
            found = set(re.findall(r'secrets\.([A-Za-z0-9_]+)', path.read_text()))
            with self.subTest(path=path.name):
                self.assertEqual(found, ADMITTED_SECRETS.get(path.name, set()))

    def test_every_workflow_passes_the_writer_guard(self):
        for path in sorted(WORKFLOWS.glob('*.y*ml')):
            with self.subTest(path=path.name):
                self.assertEqual(writer_guard_violations(path.name, path.read_text()), [])

    def test_writer_guard_turns_red_on_a_second_secret_a_model_credential_or_a_write(self):
        name = 'linear-pr-sync.yml'
        text = (WORKFLOWS / name).read_text()
        self.assertEqual(writer_guard_violations(name, text), [])
        anchor = '          LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}\n'
        self.assertIn(anchor, text)
        mutants = {
            'second secret': text.replace(anchor, anchor + '          OTHER: ${{ secrets.OTHER_KEY }}\n'),
            'model credential': text.replace(anchor, anchor + '          K: ${{ secrets.ANTHROPIC_API_KEY }}\n'),
            'model action': text.replace('actions/checkout@v4', 'anthropics/claude-code-action@v1', 1),
            'contents write': text.replace('contents: read', 'contents: write', 1),
            'id-token': text.replace('contents: read', 'id-token: write', 1),
            'git push': text.replace('run: python3', 'run: git push origin HEAD\n      - run: python3', 1),
            'self-hosted': text.replace('runs-on: ubuntu-latest', 'runs-on: self-hosted', 1),
            'git push after &&': text.replace('run: python3', 'run: make && git push\n      - run: python3', 1),
            'quoted contents write': text.replace('contents: read', 'contents: "write"', 1),
            'pull-requests write': text.replace('pull-requests: read', 'pull-requests: write', 1),
        }
        for label, mutated in mutants.items():
            with self.subTest(mutant=label):
                self.assertNotEqual(mutated, text)
                self.assertTrue(writer_guard_violations(name, mutated), label)
        # The one admitted secret is admitted for that workflow only.
        self.assertTrue(writer_guard_violations('ci.yml', text))

    def test_every_job_uses_a_github_hosted_runner(self):
        for path in sorted(WORKFLOWS.glob('*.y*ml')):
            with self.subTest(path=path.name):
                self.assertEqual(_runner_violations(yaml.safe_load(path.read_text())), [])

def _wf(run='echo ok', top='permissions:\n  contents: read\n', job='', runs_on='ubuntu-latest'):
    """A minimal new workflow (a name no table admits) whose one step runs `run`."""
    body = '\n'.join('          ' + line for line in run.strip('\n').split('\n'))
    return ('name: t\non: workflow_dispatch\n' + top + 'jobs:\n  j:\n    runs-on: ' + runs_on + '\n' + job +
            '    steps:\n      - run: |\n' + body + '\n')


def _kinds(text, name='new.yml'):
    return writer_guard_violations(name, text)


class WriterGuardWitnessTests(unittest.TestCase):
    """DeepSeek round 1 on #332: each finding as a failing-then-passing witness, written as text.

    These are strings, not files under .github/. Reader witnesses are marked F1..F4.
    """

    def test_control_the_template_is_clean(self):
        self.assertEqual(_kinds(_wf()), [])

    def test_f1_push_is_found_wherever_it_stands_in_a_run_step(self):
        forms = [
            'make && git push origin HEAD',                  # F1 witness C2b
            'git -C . push origin HEAD',                     # F1 witness C3b
            'true; git push',
            'make | git push',
            'make || git push',
            'git commit -am x\nmake\ngit push',              # a later line of a multi-line script
            'git -c user.name=x push',
            'git --no-pager push',
            'git --git-dir=.git push',
            "bash -c 'make && git push'",
            'sh -c "git push origin HEAD"',
            'bash -c "cd x; git -C y push"',
            '/usr/bin/git push',
            'git "push"',
            "git 'push'",
            'make && \\\n  git push',                       # after a line continuation
            'git${IFS}push',
            '(cd x && git push)',
            'echo $(git push)',
            'env GIT_TRACE=1 git push',
            'if true; then git push; fi',
            'git send-pack origin HEAD',
            'git-push origin',
            'git.exe push',
            'x=1 git push',
        ]
        for form in forms:
            with self.subTest(form=form):
                self.assertIn('git push', _kinds(_wf(form)))

    def test_f1_push_in_other_scalars_and_not_in_names(self):
        script = ("name: t\non: workflow_dispatch\npermissions:\n  contents: read\njobs:\n  j:\n    runs-on: ubuntu-latest\n"
                  "    steps:\n      - uses: actions/github-script@v7\n        with:\n          script: |\n"
                  "            await exec.exec('git', ['push'])\n            await exec.exec('sh -c \"git push\"')\n")
        self.assertIn('git push', _kinds(script))
        named = _wf().replace('- run: |', '- name: Wait for a follow-up git push\n        run: |')
        self.assertEqual(_kinds(named), [])

    def test_f1_benign_git_and_words_do_not_trip_the_push_check(self):
        for form in ('git status', 'git log --oneline -3', 'git fetch origin', 'git diff --name-only',
                     'echo pushed && make pushes', 'python3 push_tool.py', 'git checkout -b x'):
            with self.subTest(form=form):
                self.assertNotIn('git push', _kinds(_wf(form)))

    def test_f2_write_permissions_other_than_contents_are_refused_unless_admitted_by_name(self):
        for scope in ('pull-requests', 'issues', 'statuses', 'checks', 'actions', 'deployments', 'packages',
                      'security-events', 'pages', 'discussions', 'attestations'):
            for placement in ('workflow', 'job'):
                with self.subTest(scope=scope, placement=placement):
                    if placement == 'workflow':
                        text = _wf(top=f'permissions:\n  contents: read\n  {scope}: write\n')
                    else:
                        text = _wf(job=f'    permissions:\n      contents: read\n      {scope}: write\n')
                    self.assertIn('permission write', _kinds(text))
        # F2 witnesses A, E and A6, verbatim in shape.
        comment = _wf('gh pr comment 149 --body "looks good"', top='permissions:\n  contents: read\n  pull-requests: write\n')
        self.assertIn('permission write', _kinds(comment))
        dispatch = _wf('gh workflow run ci.yml', top='permissions:\n  contents: read\n  actions: write\n')
        self.assertIn('permission write', _kinds(dispatch))
        self.assertIn('permission write', _kinds(_wf(top='permissions: write-all\n')))

    def test_f2_a_missing_permissions_block_is_refused(self):
        text = _wf(top='')
        self.assertIn('no permissions block', _kinds(text))

    def test_f2_the_admitted_writes_are_exact_for_the_real_files(self):
        # The one workflow that holds a write keeps exactly that write; a different or an extra one is red, and
        # so is the benchmarks job's old comment permission.
        bench = '    name: benchmarks\n    permissions:\n      contents: read\n'
        for name, anchor, extra in (('ci-label-retrigger.yml', '  actions: write\n', '  statuses: write\n'),
                                    ('ci.yml', bench, '      pull-requests: write\n'),
                                    ('ci.yml', bench, '      issues: write\n      pull-requests: write\n'),
                                    ('ci.yml', bench, '      actions: write\n')):
            text = (WORKFLOWS / name).read_text()
            self.assertEqual(writer_guard_violations(name, text), [])
            with self.subTest(name=name, extra=extra.strip()):
                self.assertIn(anchor, text)
                self.assertIn('permission write', writer_guard_violations(name, text.replace(anchor, anchor + extra, 1)))
        # Admission is by file name: the same text under another name is red.
        self.assertIn('permission write', writer_guard_violations('other.yml', (WORKFLOWS / 'ci-label-retrigger.yml').read_text()))
        # Dropping an admitted write also turns the table red, so the table cannot go stale.
        text = (WORKFLOWS / 'ci-label-retrigger.yml').read_text()
        self.assertIn('permission write', writer_guard_violations('ci-label-retrigger.yml', text.replace('  actions: write\n', '  actions: read\n', 1)))

    def test_f2_labels_and_contents_through_gh_or_the_api_are_refused_even_with_an_admitted_write(self):
        forms = {
            'label write': ['gh pr edit 1 --add-label ci:full', 'gh issue edit 1 --remove-label x', 'gh label create x',
                            'gh api --method POST repos/o/r/issues/1/labels -f labels[]=x',
                            "await github.rest.issues.addLabels({})"],
            'gh writes contents': ['gh api -X PUT repos/o/r/contents/x -f message=m -f content=Zm9v',
                                   'gh api --method POST repos/o/r/git/refs -f ref=refs/heads/x -f sha=1',
                                   'gh api repos/o/r/merges -f base=main -f head=x', 'gh pr merge 1 --squash',
                                   'gh release create v1', 'gh repo sync o/r', 'gh pr create --fill'],
            'api writes contents': ['createOrUpdateFileContents({})', 'github.rest.git.updateRef({})'],
            'gh writes a pull request': ['gh pr ready 1', 'gh pr close 1', 'gh pr review 1 --approve', 'gh pr edit 1 --title x'],
        }
        for kind, lines in forms.items():
            for line in lines:
                with self.subTest(kind=kind, line=line):
                    text = _wf(line, top='permissions:\n  contents: read\n  pull-requests: write\n')
                    self.assertIn(kind, _kinds(text))
        # Reads are not a write of any kind.
        ok = _wf('gh api --paginate repos/o/r/pulls/1/files --jq .[].filename\n'
                 'gh api --paginate repos/o/r/issues/1/comments --jq .[].id\n'
                 'gh api repos/o/r/contents/x')
        self.assertEqual(_kinds(ok), [])

    def test_the_benchmarks_job_no_longer_posts_a_pull_request_comment(self):
        # CSL-238, Lucas, Nov 16, 2026: "The benchmarks job should not keep posting". The removed step, as it was.
        step = ('gh api "repos/${REPO}/issues/${PR}/comments" --paginate --jq ".[].id"\n'
                'gh api --method PATCH "repos/${REPO}/issues/comments/${existing}" --raw-field body="$(cat f)"\n'
                'gh api --method POST "repos/${REPO}/issues/${PR}/comments" --raw-field body="$(cat f)"\n')
        for line in step.splitlines()[1:]:
            with self.subTest(line=line[:60]):
                self.assertIn('pull request comment', _kinds(_wf(line)))
        for line in ('gh pr comment 1 --body x', 'gh issue comment 1 --body x', 'github.rest.issues.createComment({})',
                     'github.rest.pulls.createReview({})'):
            with self.subTest(line=line):
                self.assertIn('pull request comment', _kinds(_wf(line)))
        real = (WORKFLOWS / 'ci.yml').read_text()
        self.assertEqual(writer_guard_violations('ci.yml', real), [])
        self.assertEqual(_written_scopes(yaml.safe_load(real)), {})
        self.assertNotIn('csl-perf-bench', real)
        self.assertNotIn('Flag regressions on the pull request', real)
        # The job still runs the comparison, and putting the old comment step and permissions back is red.
        self.assertIn('benchmarks.compare', real)
        old = real.replace('    name: benchmarks\n    permissions:\n      contents: read\n',
                           '    name: benchmarks\n    permissions:\n      contents: read\n      issues: write\n'
                           '      pull-requests: write\n', 1)
        old = old.rstrip('\n') + '\n      - name: Flag regressions on the pull request\n        run: |\n' + \
            ''.join('          ' + line + '\n' for line in step.splitlines())
        kinds = writer_guard_violations('ci.yml', old)
        self.assertIn('permission write', kinds)
        self.assertIn('pull request comment', kinds)

    def test_f3_quoted_and_flow_forms_of_a_write_are_refused(self):
        for top in ('permissions:\n  contents: "write"\n', "permissions:\n  contents: 'write'\n",
                    'permissions: {contents: write}\n', 'permissions: {contents: "write"}\n',
                    'permissions:\n  contents: WRITE\n', 'permissions: "write-all"\n'):
            with self.subTest(top=top):
                self.assertIn('contents write', _kinds(_wf(top=top)))
        job_level = _wf(job='    permissions:\n      contents: "write"\n')
        self.assertIn('contents write', _kinds(job_level))
        self.assertIn('id-token', _kinds(_wf(top='permissions:\n  id-token: "write"\n')))

    def test_f4_a_matrix_runner_must_resolve_to_hosted_runners(self):
        def matrix(runs_on, strategy):
            return _wf(runs_on=runs_on, job=strategy)
        self.assertEqual(_kinds(matrix('${{ matrix.os }}', '    strategy:\n      matrix:\n        os: [ubuntu-latest, windows-2025]\n')), [])
        witnesses = {
            'F4 witness D2': '    strategy:\n      matrix:\n        os: [self-hosted, ubuntu-latest]\n',
            'include leg': '    strategy:\n      matrix:\n        os: [ubuntu-latest]\n        include:\n          - os: self-hosted\n',
            'unknown key': '    strategy:\n      matrix:\n        other: [ubuntu-latest]\n',
            'no matrix': '',
            'expression matrix, no producer': '    strategy:\n      matrix: ${{ fromJson(inputs.m) }}\n',
        }
        for label, strategy in witnesses.items():
            with self.subTest(witness=label):
                self.assertIn('runner', _kinds(matrix('${{ matrix.os }}', strategy)))
        for runs_on in ('${{ inputs.runner }}', '[self-hosted, linux]', '[ubuntu-latest, self-hosted]',
                        '{group: big, labels: x}', '${{ matrix.os }}-extra', 'self-hosted'):
            with self.subTest(runs_on=runs_on):
                self.assertIn('runner', _kinds(_wf(runs_on=runs_on)))
        self.assertIn('runner', _kinds(_wf().replace('    runs-on: ubuntu-latest\n', '')))

    def test_f4_a_generated_matrix_is_read_from_the_job_that_produces_it(self):
        def two_jobs(literals):
            return ('name: t\non: workflow_dispatch\npermissions:\n  contents: read\njobs:\n  plan:\n    runs-on: ubuntu-latest\n'
                    '    outputs:\n      matrix: ${{ steps.m.outputs.matrix }}\n    steps:\n      - id: m\n        run: |\n'
                    "          echo 'matrix=" + literals + "' >> \"$GITHUB_OUTPUT\"\n"
                    '  build:\n    needs: plan\n    runs-on: ${{ matrix.runner }}\n    strategy:\n'
                    '      matrix: ${{ fromJson(needs.plan.outputs.matrix) }}\n    steps:\n      - run: echo ok\n')
        good = '{"include": [{"runner": "ubuntu-24.04"}, {"runner": "windows-2025"}]}'
        self.assertEqual(_kinds(two_jobs(good)), [])
        self.assertIn('runner', _kinds(two_jobs('{"include": [{"runner": "ubuntu-24.04"}, {"runner": "self-hosted"}]}')))
        self.assertIn('runner', _kinds(two_jobs('{"include": []}')))

    def test_f4_a_remote_reusable_workflow_or_inherited_secrets_are_refused(self):
        def caller(uses, extra=''):
            return ('name: t\non: workflow_dispatch\npermissions:\n  contents: read\njobs:\n  call:\n    uses: ' + uses + '\n' + extra)
        self.assertIn('reusable workflow', _kinds(caller('someone/elsewhere/.github/workflows/build.yml@main', '    secrets: inherit\n')))
        self.assertIn('reusable workflow', _kinds(caller('someone/elsewhere/.github/workflows/build.yml@main')))
        self.assertIn('reusable workflow', _kinds(caller('./.github/workflows/ci.yml', '    secrets: inherit\n')))
        self.assertIn('secret', _kinds(caller('./.github/workflows/ci.yml', '    secrets: inherit\n')))
        self.assertEqual(_kinds(caller('./.github/workflows/ci.yml')), [])

    def test_secrets_and_variables_by_any_spelling_and_other_model_tools_are_refused(self):
        for step in ('env:\n          K: ${{ secrets[\'OTHER\'] }}', 'env:\n          K: ${{ toJSON(secrets) }}',
                     'env:\n          K: ${{ secrets.OTHER_KEY }}'):
            with self.subTest(step=step):
                self.assertIn('secret', _kinds(_wf().replace('- run: |', '- ' + step + '\n        run: |')))
        self.assertIn('variable', _kinds(_wf().replace('- run: |', '- env:\n          K: ${{ vars.SOME_KEY }}\n        run: |')))
        for token in ('GEMINI_API_KEY', 'google-github-actions/run-gemini-cli', 'OPENROUTER_API_KEY', 'DEEPSEEK_API_KEY'):
            with self.subTest(token=token):
                self.assertIn('model credential', _kinds(_wf('echo ' + token)))

    def test_r2_every_reference_to_the_secrets_or_vars_context_must_be_admitted_by_name(self):
        # Fable round 1, R2: the witnesses, in every spelling GitHub reads; each is red unless admitted.
        secret_forms = [
            "${{ SECRETS.OTHER_KEY }}", "${{ Secrets.OTHER_KEY }}", "${{ SECRETS['OTHER'] }}",
            "${{ secrets[\"OTHER\"] }}", "${{ TOJSON(SECRETS) }}", "${{ toJSON(secrets) }}",
            "${{ join(secrets.*, ',') }}", "${{ secrets.* }}", "${{ secrets .OTHER }}",
            "${{ secrets[format('{0}', 'x')] }}", "${{ fromJSON('1') && secrets.OTHER }}",
            "${{ secrets.GITHUB_TOKEN }}"]
        for form in secret_forms:
            with self.subTest(form=form):
                text = _wf().replace('- run: |', '- env:\n          K: ' + form + '\n        run: |')
                self.assertIn('secret', _kinds(text))
        for form in ("${{ VARS.X }}", "${{ vars['X'] }}", "${{ vars.X }}", "${{ toJSON(vars) }}", "${{ Vars .X }}"):
            with self.subTest(form=form):
                text = _wf().replace('- run: |', '- env:\n          K: ' + form + '\n        run: |')
                self.assertIn('variable', _kinds(text))
        # An `if:` is an expression without delimiters, and a job-level env or with block is read too.
        self.assertIn('secret', _kinds(_wf().replace('- run: |', "- if: secrets.OTHER != ''\n        run: |")))
        self.assertIn('secret', _kinds(_wf().replace('- run: |', "- if: ${{ SECRETS.OTHER }}\n        run: |")))
        self.assertIn('secret', _kinds(_wf(job='    env:\n      K: ${{ SECRETS.OTHER }}\n')))
        self.assertIn('variable', _kinds(_wf(job="    if: vars.X == '1'\n")))
        # The admitted workflow: its one name, in any case, is fine; a second one, in any spelling, is not.
        name = 'linear-pr-sync.yml'
        text = (WORKFLOWS / name).read_text()
        anchor = '          LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}\n'
        self.assertIn(anchor, text)
        self.assertEqual(writer_guard_violations(name, text), [])
        # The admitted spelling is exact (round 2): another case is refused by the raw-text line.
        self.assertIn('secret', writer_guard_violations(name, text.replace(anchor, anchor.replace('secrets.LINEAR_API_KEY', 'SECRETS.linear_api_key'))))
        for form in ("${{ SECRETS.OTHER_KEY }}", "${{ toJSON(SECRETS) }}", "${{ secrets['OTHER'] }}", '${{ secrets.* }}'):
            with self.subTest(admitted_plus=form):
                self.assertIn('secret', writer_guard_violations(name, text.replace(anchor, anchor + '          OTHER: ' + form + '\n')))
        self.assertIn('secret', writer_guard_violations(name, text.replace(anchor, '')))
        # Fail closed (round 2): another context that merely holds the word is refused too, on purpose.
        quiet = _wf().replace('- run: |', "- env:\n          K: ${{ needs.a.outputs.secrets }} ${{ github.event.vars.x }}\n        run: |")
        self.assertIn('secret', _kinds(quiet))

    def test_r3_the_push_scan_reads_options_quotes_and_separators_inside_quotes(self):
        forms = [
            'git -C "$(pwd)" push origin HEAD',
            'git -C $(pwd) push origin HEAD',
            'git -c http.extraheader="AUTHORIZATION: basic $(printf x | base64)" push origin HEAD',
            'git -c "alias.z=status;x" push',
            "git -c 'a=b&&c' push",
            'git 2>/dev/null push',
            'git >/dev/null push',
            'git 2>&1 push',
            'git pu""sh',
            "git p''ush",
            'git p\\ush',
            '"git" "pu""sh"',
            'python3 -c "import subprocess;subprocess.run([\'git\',\'push\'])"',
            "node -e \"require('child_process').execFileSync('git', ['push'])\"",
            'bash -c "git -C \\"$(pwd)\\" push"',
        ]
        for form in forms:
            with self.subTest(form=form):
                self.assertIn('git push', _kinds(_wf(form)))
        # M3 of the read: a new workflow that reads a PAT and pushes is refused twice over.
        pat = _wf('git -C "$(pwd)" push origin HEAD').replace('- run: |', '- env:\n          T: ${{ SECRETS.DEPLOY_PAT }}\n        run: |')
        kinds = _kinds(pat)
        self.assertIn('git push', kinds)
        self.assertIn('secret', kinds)
        # Round 2 (R9): a line that merely mentions the word is refused too, on purpose; a quoted word that is
        # not push is not.
        for form in ('git commit -m "no push here"', "git log --grep='push'"):
            with self.subTest(refused_on_purpose=form):
                self.assertIn('push word', _kinds(_wf(form)))
        self.assertNotIn('git push', _kinds(_wf('echo "git status"')))

    def test_r4_a_models_permission_or_a_model_call_is_refused(self):
        for top in ('permissions:\n  contents: read\n  models: read\n', 'permissions:\n  contents: read\n  models: write\n',
                    'permissions:\n  contents: read\n  Models: READ\n'):
            with self.subTest(top=top):
                self.assertIn('permission write', _kinds(_wf(top=top)))
        self.assertIn('permission write', _kinds(_wf(job='    permissions:\n      contents: read\n      models: read\n')))
        self.assertIn('model call', _kinds(_wf('gh models run openai/gpt-4o-mini "hello"')))
        self.assertIn('model call', _kinds(_wf('gh copilot suggest "x"')))
        self.assertIn('model credential', _kinds(_wf().replace('- run: |', '- uses: actions/ai-inference@v1\n        run: |')))
        witness = ('name: t\non: workflow_dispatch\npermissions:\n  contents: read\n  models: read\njobs:\n  j:\n'
                   '    runs-on: ubuntu-latest\n    steps:\n      - run: gh models run x "y"\n        env:\n'
                   '          GH_TOKEN: ${{ github.token }}\n')
        kinds = _kinds(witness)
        self.assertIn('permission write', kinds)
        self.assertIn('model call', kinds)

    def test_r2_to_r4_nothing_in_the_tree_holds_what_the_tables_would_admit_twice(self):
        # The tables are exact: every admitted name is used, every admitted write is held.
        used = set()
        for path in sorted(WORKFLOWS.glob('*.y*ml')):
            used |= {(path.name, n) for c, n in _context_refs(yaml.safe_load(path.read_text())) if c == 'secrets'}
        self.assertEqual(used, {(f, n) for f, names in ADMITTED_SECRETS.items() for n in names})
        self.assertEqual(ADMITTED_VARS, {})

    def test_r8_a_closing_brace_pair_inside_a_string_literal_hides_nothing(self):
        # Fable round 2, R8: GitHub ends an expression at the first `}}` outside a single-quoted string.
        secret_forms = [
            "${{ '}}' && secrets.OTHER }}",
            "${{ '}}' != '' && SECRETS.DEPLOY_PAT }}",
            "${{ format('}}{0}', SECRETS['OTHER']) }}",
            "${{ format('a}}b') && SECRETS['OTHER'] }}",
            "${{ '}}' && toJSON(secrets) }}",
            "${{ 'it''s }}' && secrets.OTHER }}",
        ]
        for form in secret_forms:
            with self.subTest(form=form):
                self.assertIn('secret', _kinds(_wf().replace('- run: |', '- env:\n          K: ' + form + '\n        run: |')))
        self.assertIn('variable', _kinds(_wf().replace('- run: |', "- env:\n          K: ${{ '}}' && vars.X }}\n        run: |")))
        self.assertIn('secret', _kinds(_wf().replace('- run: |', "- if: ${{ '}}' != '' && secrets.OTHER != '' }}\n        run: |")))
        self.assertIn('secret', _kinds(_wf("echo ${{ '}}' && secrets.OTHER }}")))
        # M3c of the read: a workflow that reads a PAT behind '}}' and pushes is refused twice over.
        pat = _wf('git pu\\\nsh "https://x:${K}@github.com/o/r" HEAD:main').replace(
            '- run: |', "- env:\n          K: ${{ '}}' != '' && SECRETS.DEPLOY_PAT }}\n        run: |")
        kinds = _kinds(pat)
        self.assertIn('secret', kinds)
        self.assertIn('push word', kinds)
        # M3g: the admitted workflow plus a second secret hidden the same way (a key name the test does not use).
        name = 'linear-pr-sync.yml'
        text = (WORKFLOWS / name).read_text()
        anchor = '          LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}\n'
        self.assertIn('secret', writer_guard_violations(
            name, text.replace(anchor, anchor + "          SECOND_KEY: ${{ '}}' != '' && SECRETS.OTHER_KEY }}\n")))

    def test_r8_an_expression_that_does_not_end_is_refused_and_the_word_alone_is_enough(self):
        for form in ("${{ 'abc }}", "${{ secrets.X", "${{ 'a' && 'b }}"):
            with self.subTest(form=form):
                text = _wf().replace('- run: |', '- env:\n          K: "' + form + '"\n        run: |')
                self.assertIn('unreadable expression', _kinds(text))
        # Raw text, no parsing at all: the word anywhere in a scalar is enough, in any case and any place.
        for step in ("- run: echo secrets", "- name: Use the Secrets here\n        run: echo ok", "- run: echo VARS",
                     '- env:\n          K: "${{ \\x73ecrets.OTHER }}"\n        run: echo ok'):
            with self.subTest(step=step):
                self.assertIn('secret', _kinds(_wf().replace('- run: |', step + '\n      - run: |')))
        # A comment is not a scalar and does not count; a longer word does not count.
        quiet = _wf().replace('- run: |', '# the secrets are not used here\n      - env:\n          K: my_secrets_dir\n        run: |')
        self.assertEqual(_kinds(quiet), [])
        # Admission is by exact spelling and the whole name.
        name = 'linear-pr-sync.yml'
        text = (WORKFLOWS / name).read_text()
        for variant in ('SECRETS.LINEAR_API_KEY', 'secrets.linear_api_key', 'secrets.LINEAR_API_KEY_2'):
            with self.subTest(variant=variant):
                self.assertIn('secret', writer_guard_violations(name, text.replace('secrets.LINEAR_API_KEY', variant, 1)))

    def test_r9_the_word_push_may_not_appear_in_a_run_script_unless_the_exact_line_is_admitted(self):
        forms = [
            'git -c "alias.z=!git push" z',                       # R9 witnesses, refused at e5a640ed
            'git config alias.p "push origin HEAD"\ngit p',
            'git "push origin"',
            'git commit -m "no push here"',                        # refused on purpose: it mentions the word
            'git pu\\\nsh origin HEAD',                            # bash joins the pair
            'git pu""sh', "git p''ush", 'git p\\ush', "git p'u's\"h\"",
            'git PUSH', 'Git Push origin', 'echo "$(git push)"',
            'make && git push origin HEAD', 'git -C "$(pwd)" push origin HEAD',
            'git 2>/dev/null push', 'git "pu"\'sh\'', 'sh -c "git push"', 'echo push',
            '# push is mentioned in a shell comment',
        ]
        for form in forms:
            with self.subTest(form=form):
                self.assertIn('push word', _kinds(_wf(form)))
        # Not the word: longer words and file names with underscores stay allowed.
        for form in ('echo pushed && make pushes', 'python3 push_tool.py', 'git status', 'git log --oneline -3'):
            with self.subTest(form=form):
                self.assertNotIn('push word', _kinds(_wf(form)))
        # github-script and other executing keys are read the same way.
        script = ("name: t\non: workflow_dispatch\npermissions:\n  contents: read\njobs:\n  j:\n    runs-on: ubuntu-latest\n"
                  "    steps:\n      - uses: actions/github-script@v7\n        with:\n          script: |\n"
                  "            await exec.exec('git', ['pu' + 'sh'])\n            await exec.exec('git push')\n")
        self.assertIn('push word', _kinds(script))
        # A backslash-newline pair joins words in the command scan too.
        self.assertIn('pull request comment', _kinds(_wf('gh pr com\\\nment 1 --body x')))

    def test_r9_admitted_push_lines_are_exact_by_file_and_none_is_stale(self):
        held = set()
        for path in sorted(WORKFLOWS.glob('*.y*ml')):
            doc = yaml.safe_load(path.read_text())
            for script in _run_scripts(doc):
                held |= {(path.name, line) for line in _push_lines(script)}
        self.assertEqual(held, set(ADMITTED_PUSH_LINES))
        for (name, line), reason in ADMITTED_PUSH_LINES.items():
            self.assertTrue(reason)
            self.assertEqual(writer_guard_violations(name, (WORKFLOWS / name).read_text()), [])
            # the line itself is fine under its own name; under another name, or grown by one word, it is refused
            self.assertNotIn('push word', writer_guard_violations(name, _wf(line)))
            self.assertIn('push word', writer_guard_violations(name, _wf(line + ' now')))
            self.assertIn('push word', writer_guard_violations('other.yml', _wf(line)))

    def test_read_all_and_write_all_are_refused_outright(self):
        for top in ('permissions: read-all\n', 'permissions: write-all\n', 'permissions: "read-all"\n'):
            with self.subTest(top=top):
                self.assertIn('permission write', _kinds(_wf(top=top)))
        self.assertIn('permission write', _kinds(_wf(job='    permissions: read-all\n')))
        witness = ('name: t\non: workflow_dispatch\npermissions: read-all\njobs:\n  j:\n    runs-on: ubuntu-latest\n'
                   '    steps:\n      - run: gh models run x "y"\n')
        kinds = _kinds(witness)
        self.assertIn('permission write', kinds)
        self.assertIn('model call', kinds)
        for path in sorted(WORKFLOWS.glob('*.y*ml')):
            self.assertNotIn('*', {s for v in _written_scopes(yaml.safe_load(path.read_text())).values() for s in v}, path.name)

    def test_every_real_workflow_still_passes_and_the_real_admissions_are_what_the_tree_holds(self):
        held = {}
        for path in sorted(WORKFLOWS.glob('*.y*ml')):
            self.assertEqual(writer_guard_violations(path.name, path.read_text()), [], path.name)
            for job, scopes in _written_scopes(yaml.safe_load(path.read_text())).items():
                held[(path.name, job)] = scopes
        self.assertEqual(held, ADMITTED_WRITES)
        for scopes in ADMITTED_WRITES.values():
            self.assertFalse({'contents', 'id-token', '*'} & scopes)


def _guard(files):
    """guard_tree_violations over a throwaway tree built from {path from the repository root: text}."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel, text in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        return guard_tree_violations(root)


def _has(found, layer, what=''):
    """True when a violation of this layer (whose text holds `what`) was reported."""
    return any(l == layer and what in w for _, l, w in found)


def _action(run='echo ok', using='composite'):
    return ('name: helper\ndescription: a local action\nruns:\n  using: ' + using + '\n  steps:\n    - shell: bash\n'
            '      run: |\n' + ''.join('        ' + line + '\n' for line in run.split('\n')))


def _caller(uses='./.github/actions/helper'):
    return _wf().replace('- run: |', '- uses: ' + uses + '\n      - run: |')


NEW = '.github/workflows/new.yml'
HELPER = '.github/actions/helper/action.yml'
# DeepSeek round 3, finding A (M-A) verbatim in shape: a complete push workflow with a PAT.
EVIL = ('name: helper\non:\n  workflow_dispatch:\npermissions:\n  contents: write\njobs:\n  publish:\n    runs-on: ubuntu-latest\n'
        '    steps:\n      - uses: actions/checkout@v4\n      - name: sync\n        env:\n          PAT: ${{ secrets.DEPLOY_PAT }}\n'
        '        run: |\n          git config user.name bot\n          git push "https://x:${PAT}@github.com/o/r.git" HEAD:main\n')
# What no text scan can refuse: the command is built at run time, encoded, or lives in a script nobody reads here.
ASSEMBLED = {
    'N10 an assembled word': 'P=pu; P=${P}sh; git $P origin HEAD',
    'N11 ANSI-C quoting': "git $'\\x70ush' origin HEAD",
    'N12 an empty expansion': 'git pu${EMPTY}sh origin HEAD',
    'N15 an encoded command': 'echo Z2l0IHB1c2ggb3JpZ2luIEhFQUQ= | base64 -d | sh',
    'N16 a committed script': 'bash scripts/sync.sh',
    'N22 character codes': 'python3 -c "import os; os.system(chr(103)+chr(105)+chr(116)+chr(32)+chr(112)+chr(117)+chr(115)+chr(104))"',
}


class CapabilityLayerTests(unittest.TestCase):
    """DeepSeek round 3 on #332: each witness as a fixture, and the layer that refuses it named in the test.

    Layers: `tree` (the shape of .github), `permissions`, `secrets`, `triggers`, `composite` (a local action's
    own checks) and `text` (the older scan, kept). A witness the text scan cannot see is shown BOUND by the
    capability layer instead: the same script, given any write power or any secret, is refused; with neither, it
    holds a read-only token and no credential, which is the stated limit.
    """

    def test_the_real_tree_passes_every_layer_and_every_table_is_exact(self):
        self.assertEqual(guard_tree_violations(ROOT), [])
        power, events, secrets, files = {}, set(), set(), sorted(WORKFLOWS.glob('*.y*ml'))
        for path in files:
            doc = yaml.safe_load(path.read_text())
            power.update({(path.name, job): scopes for job, scopes in _jobs_with_power(doc).items()})
            events |= {(path.name, event) for event in _events(doc) & set(DANGEROUS_TRIGGERS)}
            secrets |= {(path.name, n) for n in _secret_names(_scalar_texts(path.read_text())) - {'GITHUB_TOKEN'}}
        self.assertEqual(power, {key: value[0] for key, value in ADMITTED_JOB_POWER.items()})
        self.assertEqual(events, set(ADMITTED_TRIGGERS))
        self.assertEqual(secrets, set(ADMITTED_SECRET_USE))
        # no workflow lives below the top level, and the tree is the one the old tests glob
        self.assertEqual(sorted(WORKFLOWS.rglob('*.y*ml')), files)
        for table in (ADMITTED_JOB_POWER, ADMITTED_SECRET_USE, ADMITTED_TRIGGERS):
            for key, value in table.items():
                reason = value[1] if isinstance(value, tuple) else value
                self.assertTrue(reason and '\n' not in reason, key)
        # the layer's tables agree with the older ones
        self.assertEqual({(f, n) for f, names in ADMITTED_SECRETS.items() for n in names}, set(ADMITTED_SECRET_USE))
        self.assertEqual({f: set(s) for (f, _), (s, _) in ADMITTED_JOB_POWER.items()},
                         {f: set(s) for (f, _), s in ADMITTED_WRITES.items()})
        self.assertEqual(ADMITTED_VARS, {})

    # ---- A. a workflow in a subfolder (layer: tree) ------------------------------------------------------------
    def test_w_a_a_workflow_in_a_subfolder_is_refused_by_the_tree_layer(self):
        for rel, text in (('.github/workflows/sub/evil.yml', EVIL), ('.github/workflows/a/b/evil.yaml', EVIL),
                          ('.github/workflows/SUB/Evil.YML', EVIL), ('.github/workflows/sub/clean.yml', _wf())):
            with self.subTest(rel=rel):
                self.assertTrue(_has(_guard({rel: text}), 'tree', 'subfolder'))
        # the same file at the top level is refused by three other layers, so the shape rule is what is new
        top = _guard({'.github/workflows/evil.yml': EVIL})
        for layer, what in (('text', 'git push'), ('permissions', 'contents'), ('secrets', 'DEPLOY_PAT')):
            self.assertTrue(_has(top, layer, what), layer)
        self.assertFalse(_has(top, 'tree'))
        # a symlink under the two folders is refused as well
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.github' / 'workflows').mkdir(parents=True)
            (root / 'elsewhere.yml').write_text(_wf(), encoding='utf-8')
            try:
                (root / '.github' / 'workflows' / 'link.yml').symlink_to(root / 'elsewhere.yml')
            except (OSError, NotImplementedError):
                self.skipTest('no symlinks here')
            self.assertTrue(_has(guard_tree_violations(root), 'tree', 'symlink'))

    # ---- B. a composite action (layers: composite, secrets) ----------------------------------------------------
    def test_w_b_a_composite_action_is_read_and_its_run_steps_go_to_the_text_scan(self):
        pushing = _action('git push origin HEAD:main')
        self.assertTrue(_has(_guard({NEW: _caller(), HELPER: pushing}), 'composite', 'git push'))
        self.assertTrue(_has(_guard({HELPER: pushing}), 'composite', 'push word'))      # even when nothing calls it
        self.assertTrue(_has(_guard({NEW: _caller(), HELPER: _action('make && gh pr comment 1 --body x')}),
                             'composite', 'pull request comment'))
        self.assertTrue(_has(_guard({NEW: _caller(), HELPER: _action('echo secrets')}), 'composite', 'secret'))
        self.assertTrue(_has(_guard({NEW: _caller(), HELPER: _action('echo ${{ secrets.DEPLOY_PAT }}')}), 'secrets', 'DEPLOY_PAT'))
        self.assertTrue(_has(_guard({NEW: _caller(), HELPER: _action('echo ANTHROPIC_API_KEY')}), 'composite', 'model credential'))
        # a clean one passes, and so does the real one
        self.assertEqual(_guard({NEW: _caller(), HELPER: _action()}), [])

    def test_w_b_a_local_action_must_be_a_composite_under_dot_github_actions_and_must_exist(self):
        outside = _guard({NEW: _caller('./scripts/act'), 'scripts/act/action.yml': _action()})
        self.assertTrue(_has(outside, 'composite', 'outside .github/actions'))
        pushing = _guard({NEW: _caller('./scripts/act'), 'scripts/act/action.yml': _action('git push')})
        self.assertTrue(_has(pushing, 'composite', 'git push'))   # read wherever it is, and refused for the place
        node = _guard({NEW: _caller(), HELPER: _action(using='node20')})
        self.assertTrue(_has(node, 'composite', 'not a composite'))
        docker = _guard({NEW: _caller(), HELPER: _action(using='docker')})
        self.assertTrue(_has(docker, 'composite', 'not a composite'))
        self.assertTrue(_has(_guard({NEW: _caller('./.github/actions/missing')}), 'composite', 'no action.yml'))
        self.assertTrue(_has(_guard({NEW: _caller('./../elsewhere')}), 'composite', 'outside the repository'))
        # a composite that calls another is followed
        chain = _guard({NEW: _caller(), HELPER: _action().replace('    - shell: bash', '    - uses: ./other/deep\n    - shell: bash'),
                        'other/deep/action.yml': _action('git push origin HEAD')})
        self.assertTrue(_has(chain, 'composite', 'outside .github/actions'))
        self.assertTrue(_has(chain, 'composite', 'git push'))
        # a reusable workflow named by a job is a workflow file, read as one
        caller = ('name: t\non: workflow_dispatch\npermissions:\n  contents: read\njobs:\n  call:\n'
                  '    uses: ./.github/workflows/other.yml\n')
        self.assertEqual(_guard({NEW: caller, '.github/workflows/other.yml': _wf()}), [])

    # ---- C. an HTTP client with a write method (layer: text) ---------------------------------------------------
    def test_w_c_an_http_client_with_a_write_method_against_github_is_refused(self):
        for form in ('curl -sS -X PUT https://api.github.com/repos/o/r/contents/f -d @x',          # N19, the witness
                     'curl -XPUT https://api.github.com/repos/o/r/contents/f -d @x',
                     'curl -sSX PUT https://api.github.com/x', 'curl --request PUT https://api.github.com/x',
                     'curl --request=DELETE https://api.github.com/x', 'curl -d @x https://api.github.com/repos/o/r/git/refs',
                     'curl --data-binary @x https://api.github.com/x', 'curl -F f=@x https://uploads.github.com/x',
                     'curl -X POST "$GITHUB_API_URL/repos/o/r/issues/1/comments" -d @x',
                     'curl -X PATCH -H "Authorization: Bearer $GITHUB_TOKEN" https://example.com/x',
                     'wget --method=PUT --body-file=x https://api.github.com/x', 'wget --post-data=a=b https://api.github.com/x',
                     'Invoke-RestMethod -Method Put -Uri https://api.github.com/x -Body $b',
                     'iwr -Method:Post https://api.github.com/x', 'irm https://api.github.com/x -Body $b',
                     'http PUT api.github.com/repos/o/r/contents/f content=x'):
            with self.subTest(form=form):
                self.assertIn('github api write', _kinds(_wf(form)))
                self.assertTrue(_has(_guard({NEW: _wf(form)}), 'text', 'github api write'))
        for form in ('curl -fsSL -o actionlint.tgz "https://github.com/rhysd/actionlint/releases/download/v1/x.tar.gz"',
                     'curl -sS -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/repos/o/r/pulls/1',
                     'curl -G -d per_page=100 https://api.github.com/repos/o/r/pulls',
                     'curl -X GET https://api.github.com/x', 'curl -X POST https://example.com/hook -d x',
                     'wget -qO- https://api.github.com/x', 'Invoke-RestMethod -Uri https://api.github.com/x',
                     'gh api --paginate repos/o/r/pulls/1/files --jq .[].filename'):
            with self.subTest(read=form):
                self.assertNotIn('github api write', _kinds(_wf(form)))

    # ---- N18. an assembled gh api address, and N17 a literal receive-pack (layer: text) ------------------------
    def test_w_n18_gh_api_with_a_write_method_is_refused_whatever_the_endpoint(self):
        assembled = 'U=https://api.github.com/repos/o/r/cont; U=${U}ents/f; gh api -X PUT "$U" -f content=x'
        self.assertIn('github api write', _kinds(_wf(assembled)))
        for form in ('gh api -X PUT "$U" -f content=x', 'gh api --method POST repos/o/r/actions/runs/1/cancel',
                     'gh api -X DELETE repos/o/r/actions/runs/1', 'gh api repos/o/r/anything -f a=b',
                     "gh api graphql -f query='mutation { x }'"):
            with self.subTest(form=form):
                self.assertIn('github api write', _kinds(_wf(form)))
        for form in ('gh api graphql -f query=\'query { viewer { login } }\'', 'gh api -X GET repos/o/r/pulls -f per_page=100',
                     'gh api repos/o/r/pulls/1/files --paginate'):
            with self.subTest(read=form):
                self.assertNotIn('github api write', _kinds(_wf(form)))

    def test_w_n17_a_literal_git_receive_pack_over_ssh_is_refused(self):
        for form in ('ssh git@github.com "git-receive-pack \'o/r.git\'"', 'git-receive-pack o/r.git'):
            with self.subTest(form=form):
                self.assertIn('git push', _kinds(_wf(form)))

    # ---- N10, N11, N12, N15, N16, N22: assembled, encoded or hidden commands (layers: permissions, secrets) -----
    def test_w_assembled_commands_are_bound_by_the_token_and_the_secrets_not_refused_as_text(self):
        for label, form in ASSEMBLED.items():
            with self.subTest(witness=label):
                # No text scan sees it: the older layer is silent (this is the stated limit)...
                self.assertEqual(_kinds(_wf(form)), [])
                # ...and with a read-only token and no secret it holds no power to write: nothing to refuse.
                self.assertEqual(_guard({NEW: _wf(form)}), [])
                # Given any power it is refused by the capability layer, whatever it says.
                for top in ('permissions:\n  contents: write\n', 'permissions:\n  pull-requests: write\n', 'permissions: write-all\n'):
                    self.assertTrue(_has(_guard({NEW: _wf(form, top=top)}), 'permissions', 'not admitted'), top)
                self.assertTrue(_has(_guard({NEW: _wf(form, top='')}), 'permissions', 'UNSPECIFIED'))
                self.assertTrue(_has(_guard({NEW: _wf(form, job='    permissions:\n      contents: write\n')}), 'permissions', 'contents'))
                keyed = _wf(form).replace('- run: |', '- env:\n          T: ${{ secrets.DEPLOY_PAT }}\n        run: |')
                self.assertTrue(_has(_guard({NEW: keyed}), 'secrets', 'DEPLOY_PAT'))
                # (At the top level the older text layer refused all of these already, and still does.)
                self.assertTrue(_has(_guard({NEW: keyed}), 'text', 'secret'))
                self.assertTrue(_has(_guard({NEW: _wf(form, top='permissions:\n  contents: write\n')}), 'text', 'contents write'))
                # In a subfolder the shape is refused. In a composite action the job's token is what bounds it.
                self.assertTrue(_has(_guard({'.github/workflows/sub/x.yml': _wf(form)}), 'tree'))
                self.assertEqual(_guard({NEW: _caller(), HELPER: _action(form)}), [])
                writer = _caller().replace('permissions:\n  contents: read\n', 'permissions:\n  contents: write\n')
                self.assertNotEqual(writer, _caller())
                self.assertTrue(_has(_guard({NEW: writer, HELPER: _action(form)}), 'permissions', 'contents'))

    def test_the_text_scan_misses_a_third_party_action_and_a_script_it_does_not_read(self):
        # KNOWN LIMITS, pinned: a third-party action and a script a step calls are not read; the job's token bounds them.
        remote = _wf().replace('- run: |', '- uses: someone/elsewhere@v1\n      - run: |')
        self.assertEqual(_guard({NEW: remote}), [])
        self.assertEqual(_guard({NEW: _wf('bash scripts/sync.sh'), 'scripts/sync.sh': 'git push origin HEAD\n'}), [])

    # ---- the raw-text first line (layer: text, and secrets) ----------------------------------------------------
    def test_the_raw_text_line_sees_a_secret_that_a_duplicate_key_hides(self):
        text = _wf().replace('- run: |', '- env:\n          K: ${{ secrets.OTHER }}\n          K: ok\n        run: |')
        self.assertEqual(yaml.safe_load(text)['jobs']['j']['steps'][0]['env'], {'K': 'ok'})   # the parse lost it
        self.assertEqual(_context_refs(yaml.safe_load(text)), set())
        self.assertIn('secret', _kinds(text))                                                  # the raw line has it
        self.assertTrue(_has(_guard({NEW: text}), 'secrets', 'OTHER'))
        name = 'linear-pr-sync.yml'
        real = (WORKFLOWS / name).read_text()
        anchor = '          LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}\n'
        hidden = real.replace(anchor, anchor + '          K: ${{ secrets.OTHER_KEY }}\n          K: ok\n')
        self.assertIn('secret', writer_guard_violations(name, hidden))
        self.assertTrue(_has(_guard({'.github/workflows/' + name: hidden}), 'secrets', 'OTHER_KEY'))

    # ---- b. permissions ----------------------------------------------------------------------------------------
    def test_permissions_a_job_that_inherits_or_drops_its_block_must_be_admitted(self):
        name = 'ci-label-retrigger.yml'
        real = (WORKFLOWS / name).read_text()
        path = '.github/workflows/' + name
        self.assertEqual(_guard({path: real}), [])
        # A third job in the admitted file inherits `actions: write`; the older table, keyed by workflow, cannot see it.
        extra = real.rstrip('\n') + '\n\n  extra:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n'
        found = _guard({path: extra})
        self.assertTrue(_has(found, 'permissions', 'job extra holds actions'))
        self.assertEqual({layer for _, layer, _ in found}, {'permissions'})
        # Its own read-only block takes the inherited power away, as GitHub reads it.
        own = extra.replace('    steps:\n      - run: echo hi\n', '    permissions:\n      contents: read\n    steps:\n      - run: echo hi\n')
        self.assertEqual(_guard({path: own}), [])
        # A grown scope is refused (older layer too); the same text under another name is refused for both jobs.
        grown = real.replace('  actions: write\n', '  actions: write\n  statuses: write\n', 1)
        self.assertTrue(_has(_guard({path: grown}), 'permissions', 'job retrigger holds actions statuses'))
        self.assertTrue(_has(_guard({path: grown}), 'text', 'permission write'))
        other = _guard({'.github/workflows/other.yml': real})
        self.assertTrue(_has(other, 'permissions', 'job retrigger') and _has(other, 'permissions', 'job cancel-ancestors'))
        # A dropped block is UNSPECIFIED; a whole-token string and `models` are power; an empty block is none.
        self.assertTrue(_has(_guard({NEW: _wf(top='')}), 'permissions', 'UNSPECIFIED'))
        self.assertTrue(_has(_guard({NEW: _wf(top='permissions: write-all\n')}), 'permissions', '*'))
        self.assertTrue(_has(_guard({NEW: _wf(top='permissions:\n  contents: read\n  models: read\n')}), 'permissions', 'models'))
        self.assertFalse(_has(_guard({NEW: _wf(top='permissions:\n  contents: read\n', job='    permissions: {}\n')}), 'permissions'))
        self.assertFalse(_has(_guard({NEW: _wf(top='', job='    permissions:\n      contents: read\n')}), 'permissions'))
        # An admitted UNSPECIFIED job is a named decision, and only for that file and job.
        with mock.patch.dict(ADMITTED_JOB_POWER, {('new.yml', 'j'): (UNSPECIFIED, 'unspecified today; owner\'s point')}):
            self.assertFalse(_has(_guard({NEW: _wf(top='')}), 'permissions'))
            self.assertTrue(_has(_guard({'.github/workflows/o.yml': _wf(top='')}), 'permissions'))

    # ---- c. secrets --------------------------------------------------------------------------------------------
    def test_secrets_every_name_but_the_github_token_must_be_admitted_for_the_file(self):
        self.assertTrue(_has(_guard({NEW: _wf().replace('- run: |', '- env:\n          K: ${{ secrets.OTHER }}\n        run: |')}),
                             'secrets', 'OTHER'))
        for form in ("${{ secrets['OTHER'] }}", '${{ toJSON(secrets) }}', '${{ secrets.* }}', '${{ SECRETS.other }}'):
            with self.subTest(form=form):
                self.assertTrue(_has(_guard({NEW: _wf().replace('- run: |', '- env:\n          K: ' + form + '\n        run: |')}), 'secrets'))
        self.assertTrue(_has(_guard({NEW: _wf().replace('- run: |', '- name: all the secrets\n        run: |')}), 'secrets', '*'))
        inherit = _wf().replace('    runs-on: ubuntu-latest\n    steps:\n      - run: |\n          echo ok\n',
                                '    uses: ./.github/workflows/other.yml\n    secrets: inherit\n')
        self.assertTrue(_has(_guard({NEW: inherit, '.github/workflows/other.yml': _wf()}), 'secrets', '*'))
        # The admitted name is admitted for its own file only; a second one in that file is refused.
        name = 'linear-pr-sync.yml'
        real = (WORKFLOWS / name).read_text()
        anchor = '          LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}\n'
        self.assertEqual([x for x in _guard({'.github/workflows/' + name: real}) if x[1] == 'secrets'], [])
        self.assertTrue(_has(_guard({'.github/workflows/other.yml': real}), 'secrets', 'LINEAR_API_KEY'))
        second = _guard({'.github/workflows/' + name: real.replace(anchor, anchor + '          K: ${{ secrets.OTHER_KEY }}\n')})
        self.assertTrue(_has(second, 'secrets', 'OTHER_KEY'))
        self.assertFalse(_has(second, 'secrets', 'LINEAR_API_KEY'))
        # GITHUB_TOKEN is not this layer's concern (the older text scan still refuses it).
        token = _guard({NEW: _wf().replace('- run: |', '- env:\n          K: ${{ secrets.GITHUB_TOKEN }}\n        run: |')})
        self.assertFalse(_has(token, 'secrets'))
        self.assertTrue(_has(token, 'text', 'secret'))

    # ---- d. triggers -------------------------------------------------------------------------------------------
    def test_triggers_pull_request_target_and_workflow_run_must_be_admitted(self):
        for on in ('on: pull_request_target\n', 'on: [pull_request_target]\n', 'on: [push, WORKFLOW_RUN]\n',
                   'on:\n  pull_request_target:\n    types: [opened]\n',
                   'on:\n  workflow_dispatch:\n  workflow_run:\n    workflows: [ci]\n'):
            with self.subTest(on=on):
                text = _wf().replace('on: workflow_dispatch\n', on)
                self.assertNotEqual(text, _wf())
                self.assertTrue(_has(_guard({NEW: text}), 'triggers', 'not admitted'))
        self.assertFalse(_has(_guard({NEW: _wf()}), 'triggers'))
        real = (WORKFLOWS / 'ci-label-retrigger.yml').read_text()
        self.assertFalse(_has(_guard({'.github/workflows/ci-label-retrigger.yml': real}), 'triggers'))
        self.assertTrue(_has(_guard({'.github/workflows/other.yml': real}), 'triggers', 'workflow_run'))
        self.assertTrue(_has(_guard({NEW: _wf().replace('on: workflow_dispatch\n', 'on: {}\n')}), 'triggers', 'no readable trigger'))
        # an unreadable workflow is refused by the older layer; the capability layer does not guess
        self.assertTrue(_has(_guard({NEW: 'name: [unclosed\n'}), 'text', 'unparseable'))


# The linux job runs this file by path, not every tools/test_*.py. Importing
# the class makes that invocation collect the pinned-checkout regression.
_tools_dir = str(Path(__file__).resolve().parent)
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)
from test_recursion_route_pin_fetch import RecursionRoutePinFetchTests  # noqa: E402,F401

if __name__ == '__main__':
    unittest.main()


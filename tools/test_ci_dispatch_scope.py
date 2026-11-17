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
# Token permissions above read, admitted by workflow file, by job (None = the workflow-level block) and by
# scope. Exactly one is in the tree: ci-label-retrigger.yml dispatches CI (actions). The `benchmarks` job
# of ci.yml no longer posts a pull request comment (the owner's decision, Nov 16, 2026), so no workflow
# holds issues or pull-requests write, and a comment posted through gh, the API or github-script is
# refused in every workflow. `models` counts as above read at ANY level (read is enough to call a model).
# `contents` and `id-token` are never admissible, whatever this table says.
ADMITTED_WRITES = {('ci-label-retrigger.yml', None): {'actions'}}

# WHAT HOLDS, AND WHAT DOES NOT (the guard reads workflow TEXT; it never runs anything):
# Held by what is PARSED, not by spelling: (a) every reference to the `secrets` or `vars` context inside any
# expression (any case, dot, bracket, wildcard, whole-object, `secrets: inherit`, and the bare expression of
# an `if:`) must be in ADMITTED_SECRETS / ADMITTED_VARS for that workflow file; (b) every token permission
# above read, `models` at any level included, must be in ADMITTED_WRITES by file, job and scope; (c) every
# job runs on a GitHub-hosted runner and calls no remote reusable workflow. A workflow that cannot reach a
# secret (a) or a write scope (b) cannot push to this repository, post to a pull request or call a model
# with the repository token, whatever it writes in a script.
# A SECOND LINE, not a proof: the push, `gh`, API and label scan below reads each string as shell text,
# removes quotes and backslashes inside words, ignores separators inside quotes (and reads the quoted text
# again as a command), and refuses a `git` word followed by `push` or `send-pack` in the same segment, even
# inside an `echo`. It is as good as that normalisation and no better.
# WHAT NO SCAN OF TEXT CAN SEE: a script or action fetched or built at run time (curl | sh, a downloaded
# file, an action whose ref moves), a script committed in the repository that a step calls
# (`bash scripts/push.sh`), a command assembled at run time (`cmd=push; git $cmd`, an alias, `eval`,
# base64, ANSI-C quoting), what a `fromJson` matrix yields at run time (only the producer's runner
# literals are read), the rights an organization default or a token held outside this repository gives,
# and what a step may do with the one admitted write (`gh workflow disable`, `gh run cancel`): the table
# admits a scope for a file by name, not a use of it. No trigger is checked here: a `pull_request_target`
# workflow with a read token passes this guard (`test_linear_pr_sync.py` pins the sync's trigger set).
_NOT_SHELL = {'name', 'if', 'description', 'group'}
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
_EXPR = re.compile(r'\$\{\{(.*?)\}\}', re.DOTALL)
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


def _context_refs(node, key=None):
    """{('secrets'|'vars', NAME)} for every use of either context in any expression; '*' for a wildcard,
    a dynamic index, the whole object or `secrets: inherit`."""
    refs = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k).lower() == 'secrets' and isinstance(v, str) and not _EXPR.search(v):
                refs.add(('secrets', '*'))
            refs |= _context_refs(v, str(k))
    elif isinstance(node, list):
        for v in node:
            refs |= _context_refs(v, key)
    elif isinstance(node, str):
        for body in _EXPR.findall(node) or ([node] if key == 'if' else []):
            for m in _CONTEXT.finditer(body):
                refs.add((m.group(1).lower(), (m.group(2) or m.group(4) or '*').upper()))
    return refs


def _shell_segments(text, depth=0):
    """Split shell-ish text into segments of normalised words. Separators (&& || ; | & newline) count only
    outside quotes; quotes and backslashes are removed inside words (`pu""sh`, `p\\ush`); `, [ ] { } ( ) ` < >`
    and blanks end a word; a quoted string that holds blanks is read again as a command (bash -c '...')."""
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
            if text[i] == '\n':
                end_word()
            else:
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


def _command_violations(text):
    """Second line: refuse a push, a write to repository contents, a label write, a pull request comment or
    a model call wherever the words stand (see the comment above for what this is and is not)."""
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
        if 'git-push' in base or 'git-send-pack' in base:
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
                    if _CONTENT_ENDPOINT.search(pos[1]):
                        out.add('gh writes contents')
                    if _LABEL_ENDPOINT.search(pos[1]):
                        out.add('label write')
                    if _COMMENT_ENDPOINT.search(pos[1]):
                        out.add('pull request comment')
    return out


def _written_scopes(doc):
    """{job or None: set of token scopes above read}; '*' for a whole-token string; `models` at any level."""
    found = {}
    blocks = [(None, doc.get('permissions'))] + [(n, j.get('permissions')) for n, j in (doc.get('jobs') or {}).items()]
    for job, block in blocks:
        if isinstance(block, str):
            block = {'*': block}
        for scope, level in (block or {}).items():
            if str(level).strip().lower() not in ('read', 'none', 'read-all') or str(scope).strip().lower() == 'models':
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
    except yaml.YAMLError:
        return out + ['unparseable']
    if not isinstance(doc, dict):
        return out + ['unparseable']
    refs = _context_refs(doc)
    if {n for c, n in refs if c == 'secrets'} != ADMITTED_SECRETS.get(name, set()):
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
    for scalar in _scalars(doc):
        out += sorted(_command_violations(scalar) - set(out))
    out += [why for _, why in _runner_violations(doc)]
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
        self.assertEqual(writer_guard_violations(name, text.replace(anchor, anchor.replace('secrets.LINEAR_API_KEY', 'SECRETS.linear_api_key'))), [])
        for form in ("${{ SECRETS.OTHER_KEY }}", "${{ toJSON(SECRETS) }}", "${{ secrets['OTHER'] }}", '${{ secrets.* }}'):
            with self.subTest(admitted_plus=form):
                self.assertIn('secret', writer_guard_violations(name, text.replace(anchor, anchor + '          OTHER: ' + form + '\n')))
        self.assertIn('secret', writer_guard_violations(name, text.replace(anchor, '')))
        # Not a reference: another context that merely holds the word, a plain word in text.
        quiet = _wf().replace('- run: |', "- env:\n          K: ${{ needs.a.outputs.secrets }} ${{ github.event.vars.x }}\n        run: |")
        self.assertEqual(_kinds(quiet), [])

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
        # Quoted words that merely hold the word are not a push.
        for form in ('git commit -m "no push here"', 'echo "git status"', "git log --grep='push'"):
            with self.subTest(benign=form):
                self.assertNotIn('git push', _kinds(_wf(form)))

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

    def test_every_real_workflow_still_passes_and_the_real_admissions_are_what_the_tree_holds(self):
        held = {}
        for path in sorted(WORKFLOWS.glob('*.y*ml')):
            self.assertEqual(writer_guard_violations(path.name, path.read_text()), [], path.name)
            for job, scopes in _written_scopes(yaml.safe_load(path.read_text())).items():
                held[(path.name, job)] = scopes
        self.assertEqual(held, ADMITTED_WRITES)
        for scopes in ADMITTED_WRITES.values():
            self.assertFalse({'contents', 'id-token', '*'} & scopes)


# The linux job runs this file by path, not every tools/test_*.py. Importing
# the class makes that invocation collect the pinned-checkout regression.
_tools_dir = str(Path(__file__).resolve().parent)
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)
from test_recursion_route_pin_fetch import RecursionRoutePinFetchTests  # noqa: E402,F401

if __name__ == '__main__':
    unittest.main()


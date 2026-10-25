"""Offline workflow regression tests; no GitHub calls or hosted jobs.

Run: python -m unittest discover -s tools -p test_ci_dispatch_scope.py -v
Requires PyYAML (already part of the repository validation dependencies).
"""
import copy
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
        for event, scope, labels, win, mac, rust_win, rust_mac in [
            ('workflow_dispatch', '', [], True, True, True, True),
            ('workflow_dispatch', 'full', [], True, True, True, True),
            ('workflow_dispatch', 'linux', [], False, False, False, False),
            ('pull_request', '', [], False, False, False, False),
            ('pull_request', '', ['enhancement'], False, False, False, False),
            ('pull_request', '', ['ci:full'], True, True, True, True),
            ('push', '', [], True, False, True, False),
            ('merge_group', '', [], True, False, True, False),
            ('schedule', '', [], False, True, False, False),
        ]:
            with self.subTest(event=event, scope=scope, labels=labels):
                context = {'github.event_name': event, 'inputs.scope': scope,
                           'github.ref': 'refs/heads/main',
                           'github.event.pull_request.labels.*.name': labels}
                self.assertEqual(bool(evaluate(self.ci['jobs']['windows']['if'], **context)), win)
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



# CSL-416 (CSL-255 exit criterion 5): a static guard on the admission surface of the Claude
# writers. They reach CI only through the ordinary `synchronize` of their own push: no CI
# dispatch, re-run, ready-for-review or CI-label add (the only CI-label write is revoking a
# gate-owned `ci:full`); GitHub-hosted runners only; one shared per-PR lock without
# cancellation; pushes only from model-free publish jobs as one ordinary fast-forward; the
# eight-commit guard, the one-attempt publish guard and the pause. Retry behaviour itself is
# exercised by tools/test_claude_fixer_*.py and tools/test_claude_pr_fixer.py.
WRITER_FILES = ('claude.yml', 'claude-conflict-writer.yml', 'conflict-scan.yml')
# CSL-474 P9: dispatch-only measurement. Not a writer. Optional OAuth is an
# explicit workflow_dispatch input defaulting off; Lucas dispatches.
P9_PROBE_FILE = 'csl-474-p9-probes.yml'
# Roles: prep, model, publish. No job has the credential-free 'verify' role yet; CSL-409 adds one,
# and the 'verify' branches below are ready for it.
WRITER_JOBS = {
    'claude.yml': {'mention-prepare': 'prep', 'claude-mention': 'model', 'mention-publish': 'publish',
                   'fixer-prepare': 'prep', 'devin-review-fix': 'model', 'fixer-publish': 'publish'},
    'claude-conflict-writer.yml': {'prepare': 'prep', 'model': 'model', 'publish': 'publish'},
}
NON_WRITER_JOBS = {'claude.yml': {'resolve-pr', 'event-paths', 'forward-conflict-scan'},
                   'claude-conflict-writer.yml': set()}
# conflict-scan.yml's complete job inventory: the read-only scan, and the reusable-workflow caller of
# claude-conflict-writer.yml (its permissions are checked exactly below). A new job there must be added.
SCAN_JOBS = {'conflict-scan': 'scan', 'conflict-resolve': 'caller'}
# Every way to hand a job a model: the two credentials and the action. Prep and publish jobs hold none.
MODEL_ACCESS = re.compile(r'ANTHROPIC_API_KEY|CLAUDE_CODE_OAUTH_TOKEN|anthropics/claude-code-action', re.IGNORECASE)
HOSTED = {'ubuntu-latest', 'ubuntu-24.04', 'ubuntu-22.04', 'windows-2025', 'windows-latest', 'macos-latest'}
MODEL_BASH = 'Bash(python3 ${{ runner.temp }}/claude-fixer-guard/model-api --:*)'
# A push, in shell or argv form. Shell: `git`, then global options (`-C dir`, `-c k=v`,
# `--git-dir path`, `--no-pager`), then `push`, across backslash continuations. Argv: any
# `[`/`(` list or a quoted `"git"` list, then `"push"` — helpers named `run_git`, `GIT`,
# `_git`, `run`, or `cmd` all count. Quotes may be backslash-escaped. False positives are
# excluded narrowly: a bare `"push"` passed to print/logging, or a confirmed
# display list (`allowed_commands = ["push", "origin"]`). Value-taking git options
# (`--git-dir`, `--work-tree`, `--namespace`, `-C`, `-c`) consume their following token.
_Q = r'\\?["\']'
_STR = r'[rbfuRBFU]{0,2}' + _Q + r'[^"\'\n]*' + _Q
_OPT = r'[rbfuRBFU]{0,2}' + _Q + r'-[^"\'\n]*' + _Q
_EXPR = r'\*?[A-Za-z_][\w.]*(?:\([^()\n]*\)|\[[^\[\]\n]*\])*'
_SEP = r'\s*,\s*'
_TOKEN = rf'(?:{_OPT}(?:{_SEP}(?:{_STR}|{_EXPR}))?|{_EXPR})'
_GAP = r'(?:\s|\\\n)+'
_GIT_VAL = (r'(?:--(?:git-dir|work-tree|namespace|config-env|super-prefix|attr-source))'
            rf'(?:={_GAP}?\S+|{_GAP}\S+)')
_GIT_OPT = rf'(?:-[Cc]{_GAP}\S+|{_GIT_VAL}|--?[A-Za-z][\w-]*(?:=\S+)?)'
_PUSH_RAW = re.compile(
    rf'(?:[\[(]\s*|{_Q}git{_Q}{_SEP})(?:{_TOKEN}{_SEP}){{0,12}}{_Q}push{_Q}'
    rf'|\bgit(?:{_GAP}{_GIT_OPT})*{_GAP}push\b|/git/refs/'
)
_BENIGN_CALL = re.compile(r'(?:print|pprint|logging(?:\.\w+)+|logger(?:\.\w+)*|\blog)\s*\(?\s*$'
                           r'|\b\w*(?:allowed|words|display|verbs)\w*\s*[:=]\s*$', re.IGNORECASE)


class _PushMatcher:
    """Broad argv/shell push detector with narrow exclusions for prompt/log word lists."""

    def finditer(self, text):
        for match in _PUSH_RAW.finditer(text):
            if not self._benign(text, match):
                yield match

    def search(self, text):
        return next(self.finditer(text), None)

    @staticmethod
    def _benign(text, match):
        snippet = match.group(0)
        if snippet.startswith(('git', '/git/refs/')):
            return False
        # Only confirmed display/log lists are excluded. `args = ["push", "pull"]`
        # then `run_git(args)` is a push to a remote named `pull`.
        return bool(_BENIGN_CALL.search(text[max(0, match.start() - 80):match.start()]))


PUSH = _PushMatcher()
CI_LABELS = frozenset({'ci:full', 'enhancement'})
HANDOFF_CONST = re.compile(
    r"(?m)^\s*(?:HANDOFF_LABEL|MODEL_API_LABEL)\s*[=:]\s*(?:['\"]([^'\"]+)['\"]|(\S+))"
)
LABEL_ARRAY = re.compile(
    r'''(?:["']labels["']\s*:|labels\s*=)\s*\[([^\]]*)\]''', re.IGNORECASE | re.DOTALL
)
ISSUE_RESOURCE = re.compile(r'/issues/(?:\{[^}]+\}|\$[\w.]+|\d+)(?!/)')
WRITER_CALL = re.compile(
    r'(?m)^\s+uses:\s*["\']?(?:\./)?\.github/workflows/claude-conflict-writer\.ya?ml["\']?\s*(?:#.*)?$'
)
_SECRET_EXPR = re.compile(
    r'''secrets(?:\.[A-Za-z0-9_]+|\[\s*['"][A-Za-z0-9_]+['"]\s*\])'''
)
_SECRETS_CONTEXT = re.compile(r'(?<![\w.])secrets\b')
_GH_EXPR = re.compile(r'\$\{\{(.*?)\}\}', re.DOTALL)
_OAUTH_ACTION_INPUT = re.compile(
    r'''['"]?claude_code_oauth_token['"]?\s*:\s*['"]?\$\{\{\s*secrets\.CLAUDE_CODE_OAUTH_TOKEN\s*\}\}['"]?'''
)
_OAUTH_VALUE = re.compile(r'^\$\{\{\s*secrets\.CLAUDE_CODE_OAUTH_TOKEN\s*\}\}$')


class _ModelSecrets:
    """secrets.* in a model job or inherited workflow env. OAuth only as the Claude action `with` input."""

    def search(self, job, inherited=None):
        if not isinstance(job, dict):
            hit = self._in_exprs(str(job))
        else:
            scanned = copy.deepcopy(job)
            for step in scanned.get('steps') or []:
                with_block = step.get('with') or {}
                token = with_block.get('claude_code_oauth_token')
                if (str(step.get('uses') or '').startswith('anthropics/claude-code-action')
                        and token is not None
                        and _OAUTH_VALUE.fullmatch(str(token).strip())):
                    with_block.pop('claude_code_oauth_token')
            hit = self._walk(scanned)
        if hit:
            return hit
        return None if inherited is None else self._walk(inherited)

    def _walk(self, value):
        if isinstance(value, str):
            return self._in_exprs(value)
        if isinstance(value, dict):
            for item in value.values():
                hit = self._walk(item)
                if hit:
                    return hit
            return None
        if isinstance(value, (list, tuple)):
            for item in value:
                hit = self._walk(item)
                if hit:
                    return hit
            return None
        return None

    @staticmethod
    def _in_exprs(text):
        for body in _GH_EXPR.findall(text):
            hit = _ModelSecrets._unquoted_secrets(body)
            if hit:
                return hit
        return None

    @staticmethod
    def _unquoted_secrets(body):
        """secrets context in unquoted GitHub expression text. Doubled quotes are literals."""
        i, n = 0, len(body)
        while i < n:
            if body[i] in "'\"":
                quote = body[i]
                i += 1
                start = i
                while i < n:
                    if body[i] == quote:
                        if i + 1 < n and body[i + 1] == quote:
                            i += 2
                            continue
                        i += 1
                        break
                    i += 1
                else:
                    return _SECRETS_CONTEXT.search(body[start:])
            else:
                start = i
                while i < n and body[i] not in "'\"":
                    i += 1
                hit = _SECRETS_CONTEXT.search(body[start:i])
                if hit:
                    return hit
        return None


MODEL_SECRETS = _ModelSecrets()
# Named allow-list: the Claude action may receive the job-scoped token, and
# only that mapping, when every permission on the model job is `read`.
MODEL_GITHUB_TOKEN_EXCEPTION = re.compile(
    r'''['"]?github_token['"]?\s*:\s*['"]?\$\{\{\s*github\.token\s*\}\}['"]?'''
)
_GITHUB_TOKEN_VALUE = re.compile(r'^\$\{\{\s*github\.token\s*\}\}$')
_SECRET_GITHUB_TOKEN = re.compile(
    r'''['"]?github_token['"]?\s*:\s*['"]?\$\{\{\s*secrets\.'''
)
EXPECTED_LOCK = {
    'claude.yml': 'claude-${{ needs.resolve-pr.outputs.pr_number }}',
    'claude-conflict-writer.yml': 'claude-${{ inputs.pr_number }}',
}
ROLE_USES = {
    'prep': (),
    'model': ('actions/checkout@', 'actions/setup-python@', 'oven-sh/setup-bun@',
              'anthropics/claude-code-action@', 'actions/upload-artifact@'),
    'publish': ('actions/download-artifact@',),
    'verify': (),
}
MODEL_PERMS = {'contents': 'read', 'pull-requests': 'read', 'issues': 'read'}
# Inside a whole push command: force, lease, mirror or delete options (also in short-option clusters
# such as `-fq`), and a refspec that starts with `+` (forced) or `:` (delete).
FORCED_PUSH = re.compile(r'--force|--mirror|--delete|(?<![\w-])-[A-Za-z]*[fd][A-Za-z]*(?![\w-])'
                         r'|(?:^|[\s,(\[])[rbfuRBFU]{0,2}\\?["\']?[+:][^\s"\'+:]'
                         r'|["\']?force["\']?\s*[:=]\s*["\']?(?:true|True)')
# A request that writes: explicit method, or a body option that makes `gh api`/`curl` POST.
LABEL_WRITE = re.compile(r'\b(?:POST|PUT|PATCH)\b|(?<![\w-])(?:-f|-F|--field|--raw-field|--input|-d|--data[\w-]*'
                         r'|--json)(?![\w-])')
SPAN_LINES = 40


def job_keys(text):
    """Job ids at the `jobs:` indent, including quoted keys such as `"extra-job":`."""
    match = re.search(r'(?m)^jobs:\s*(?:#.*)?$', text)
    if not match:
        return set()
    rest = text[match.end():]
    stop = re.search(r'(?m)^[^\s#]', rest)
    block = rest[:stop.start()] if stop else rest
    keys = []
    for item in re.finditer(r'(?m)^  (?:["\']([^"\']+)["\']|([A-Za-z_][\w-]*)):', block):
        keys.append(item.group(1) or item.group(2))
    return set(keys)


def job_uses(job):
    return [str(step.get('uses')) for step in job.get('steps') or [] if step.get('uses')]


_THIRD_PARTY_SHA = re.compile(r'^[^@\s]+@[0-9a-f]{40}$')


def uses_sha_pinned(uses):
    """GitHub-owned actions/* may use a tag. Everything else needs a 40-hex SHA."""
    return uses.startswith('actions/') or bool(_THIRD_PARTY_SHA.fullmatch(uses))


def uses_allowed(job, role):
    allowed = ROLE_USES[role]
    return all(
        any(uses.startswith(prefix) for prefix in allowed) and uses_sha_pinned(uses)
        for uses in job_uses(job)
    )


def model_github_token_ok(job):
    """Allow only `github_token: ${{ github.token }}` when every permission is read.

    Checked on each step `with:` input, so a decoy `github.token` mention cannot
    hide a PAT or `vars.*` mapping on the Claude action.
    """
    perms = job.get('permissions') or {}
    if (not isinstance(perms, dict) or not perms
            or any(value != 'read' for value in perms.values())):
        return False
    tokens = []
    for step in job.get('steps') or []:
        token = (step.get('with') or {}).get('github_token')
        if token is not None:
            tokens.append(str(token).strip())
    return bool(tokens) and all(_GITHUB_TOKEN_VALUE.fullmatch(token) for token in tokens)


def writer_callers(directory):
    return {path.name for path in Path(directory).glob('*.y*ml')
            if WRITER_CALL.search(path.read_text(encoding='utf-8'))}


def handoff_constants(text):
    return [match.group(1) or match.group(2) for match in HANDOFF_CONST.finditer(text)]


def labels_in_span(span):
    names = []
    for match in LABEL_ARRAY.finditer(span):
        for token in re.finditer(r'HANDOFF_LABEL|[rbfuRBFU]{0,2}["\']([^"\']+)["\']', match.group(1)):
            names.append('needs-decision' if token.group(0) == 'HANDOFF_LABEL' else token.group(1))
    if re.search(r'addLabelsToLabelable', span, re.IGNORECASE):
        if 'HANDOFF_LABEL' in span:
            names.append('needs-decision')
        names.extend(token.group(1) for token in re.finditer(r'["\']([^"\']+)["\']', span)
                     if token.group(1) in CI_LABELS | {'needs-decision'})
    return names


def ci_label_add_violations(text):
    """CI-label adds the writers must not perform, including issue PATCH and GraphQL."""
    hits, seen = [], set()

    def consider(pos, kind):
        span = command_span(text, pos)
        key = (kind, span)
        if key in seen:
            return
        seen.add(key)
        names = labels_in_span(span)
        if kind == 'graphql':
            if set(names) & CI_LABELS or (
                    'HANDOFF_LABEL' not in span and 'needs-decision' not in names):
                hits.append(span)
            return
        if not LABEL_WRITE.search(span):
            return
        if kind == 'issue-patch' and not names and not re.search(r'\blabels\b', span):
            return
        if set(names) & CI_LABELS:
            hits.append(span)
            return
        if kind == 'labels-path' and 'HANDOFF_LABEL' not in span and 'needs-decision' not in names:
            hits.append(span)

    for match in re.finditer(r'/labels\b', text):
        consider(match.start(), 'labels-path')
    for match in ISSUE_RESOURCE.finditer(text):
        consider(match.start(), 'issue-patch')
    for match in re.finditer(r'addLabelsToLabelable', text, re.IGNORECASE):
        consider(match.start(), 'graphql')
    return hits


def job_text(job):
    """Raw `run` scripts and `with` values of a job's steps, unescaped."""
    parts = []
    for step in job.get('steps', []):
        parts.append(str(step.get('run', '')))
        parts.extend(str(v) for v in (step.get('with') or {}).values())
    return '\n'.join(parts)


def command_span(text, pos):
    """The whole command or call around text[pos].

    It starts at the line of the outermost bracket still open at `pos` (within SPAN_LINES lines),
    or earlier when the previous lines end in a shell backslash continuation, and ends at the first
    newline after `pos` with every bracket closed and no continuation. So a multiline python call
    or argv list, or a continued shell command, is read as one unit.
    """
    floor = 0
    for _ in range(SPAN_LINES):
        floor = text.rfind('\n', 0, floor or pos)
        if floor < 0:
            floor = 0
            break
    start = text.rfind('\n', 0, pos) + 1
    depth = 0
    for i in range(pos - 1, floor - 1, -1):
        if text[i] in ')]}':
            depth += 1
        elif text[i] in '([{':
            if depth:
                depth -= 1
            else:
                start = text.rfind('\n', 0, i) + 1
    while start > 0 and text[:start - 1].rstrip(' \t').endswith('\\'):
        start = text.rfind('\n', 0, start - 1) + 1
    depth, end, lines = 0, len(text), 0
    for i in range(start, len(text)):
        char = text[i]
        if char in '([{':
            depth += 1
        elif char in ')]}':
            depth -= 1
        elif char == '\n' and i >= pos:
            lines += 1
            if (depth <= 0 and not text[:i].rstrip(' \t').endswith('\\')) or lines > SPAN_LINES:
                end = i
                break
    return text[start:end]


def granted(text):
    # `--disallowedTools` denials (e.g. `Bash(git push:*)`) are not grants.
    return re.sub(r'--disallowedTools "[^"]*"', '', text)


class CrossLaneAdmissionTests(unittest.TestCase):
    def test_writer_workflow_set_is_complete(self):
        # A new Claude workflow must be added here (and to the contract) before it can merge.
        found = {p.name for p in WORKFLOWS.glob('*.y*ml')
                 if re.search(r'anthropics/claude-code|ANTHROPIC_API_KEY|CLAUDE_CODE_OAUTH_TOKEN', p.read_text())}
        self.assertEqual(found, set(WRITER_FILES) | {P9_PROBE_FILE})
        for name, jobs in WRITER_JOBS.items():
            parsed = set(workflow(name)['jobs'])
            self.assertEqual(parsed, set(jobs) | NON_WRITER_JOBS[name])
            self.assertEqual(job_keys((WORKFLOWS / name).read_text()), parsed)
        scan_jobs = set(workflow('conflict-scan.yml')['jobs'])
        self.assertEqual(scan_jobs, set(SCAN_JOBS))
        self.assertEqual(job_keys((WORKFLOWS / 'conflict-scan.yml').read_text()), scan_jobs)
        self.assertEqual(writer_callers(WORKFLOWS), {'conflict-scan.yml'})

    def test_writers_never_dispatch_rerun_or_ready_ci(self):
        for name in WRITER_FILES:
            text = (WORKFLOWS / name).read_text()
            with self.subTest(workflow=name):
                self.assertNotRegex(text, r'/dispatches\b|createWorkflowDispatch|reRunWorkflow|actions/github-script')
                self.assertNotRegex(text, r'/rerun|rerun-failed-jobs|gh run rerun|gh pr ready|ready_for_review"?\s*[,)]'
                                          r'|markPullRequestReadyForReview|ReadyForReview\(')
                if name != 'claude.yml':
                    self.assertEqual(re.findall(r'gh workflow run (\S+)', text), [])
        dispatching = {job for job, spec in workflow('claude.yml')['jobs'].items()
                       if 'gh workflow run' in job_text(spec)}
        self.assertEqual(dispatching, {'forward-conflict-scan'})
        self.assertEqual(set(re.findall(r'gh workflow run (\S+)', (WORKFLOWS / 'claude.yml').read_text())),
                         {'conflict-scan.yml'})

    def test_writers_never_add_ci_labels(self):
        for name in WRITER_FILES:
            text = (WORKFLOWS / name).read_text()
            with self.subTest(workflow=name):
                self.assertNotRegex(text, r'--add-label|gh pr edit|labels\[\]=')
                self.assertNotRegex(text, r'"labels":\s*\[\s*"(ci:full|enhancement)"')
                # Every label write is the handoff label; ci:full is only ever removed (DELETE of a
                # gate-owned label event). The span covers `/labels`, PATCH of the issue resource,
                # and GraphQL addLabelsToLabelable, and the complete label set — not the first name.
                self.assertEqual(ci_label_add_violations(text), [])
                constants = handoff_constants(text)
                self.assertTrue(set(constants) <= {'needs-decision'}, constants)
                if name != 'conflict-scan.yml':
                    self.assertTrue(constants)
                    self.assertEqual(set(constants), {'needs-decision'})

    def test_conflict_resolve_caller_permissions_are_exact(self):
        # The reusable-workflow caller grants its inner jobs their ceiling; it holds no lock itself.
        caller = workflow('conflict-scan.yml')['jobs']['conflict-resolve']
        self.assertEqual(caller['permissions'], {'contents': 'read', 'pull-requests': 'write',
                                                 'issues': 'write', 'id-token': 'write'})
        self.assertNotIn('concurrency', caller)
        self.assertIn("github.event_name != 'push'", caller['if'])

    def test_writer_workflows_use_github_hosted_runners_only(self):
        for name in WRITER_FILES:
            for job_name, job in workflow(name)['jobs'].items():
                with self.subTest(workflow=name, job=job_name):
                    if 'uses' in job:
                        self.assertEqual(job['uses'], './.github/workflows/claude-conflict-writer.yml')
                    else:
                        self.assertIn(job['runs-on'], HOSTED)

    def test_non_writer_jobs_cannot_push(self):
        for name, jobs in NON_WRITER_JOBS.items():
            doc = workflow(name)
            for job_name in jobs:
                job = doc['jobs'][job_name]
                perms = job.get('permissions') or {}
                with self.subTest(workflow=name, job=job_name):
                    if job_name == 'forward-conflict-scan':
                        # The one dispatch (of conflict-scan.yml, checked above) needs exactly this.
                        self.assertEqual(perms, {'actions': 'write', 'contents': 'read'})
                    else:
                        self.assertEqual(perms.get('contents'), 'read')
                        self.assertTrue(set(perms.values()) <= {'read', 'none'})
                    self.assertFalse(PUSH.search(job_text(job)))
        # Jobs without their own `permissions:` inherit the workflow default, which must be read-only.
        for name in WRITER_FILES:
            with self.subTest(workflow=name, check='default token'):
                default = workflow(name).get('permissions')
                self.assertIsInstance(default, dict)
                self.assertTrue(default)
                self.assertTrue(set(default.values()) <= {'read', 'none'})
        scan = workflow('conflict-scan.yml')['jobs']['conflict-scan']
        self.assertEqual(scan['permissions'], {'actions': 'read', 'contents': 'read', 'pull-requests': 'read'})
        self.assertFalse(PUSH.search(job_text(scan)))
        self.assertIsNone(MODEL_ACCESS.search(str(scan)))

    def test_push_matcher_and_spans(self):
        # Option-bearing and prefixed argv pushes are pushes; a prompt word list is not.
        for push in ('subprocess.run(["git", "-C", repo, "push", ...])', 'run_git(["-C", repo, "push"])',
                     'subprocess.run(prefix + ["push", "origin", "HEAD"])', 'run([*prefix, "push", "origin"])',
                     'python3 -c "run([\\"git\\", \\"-C\\", \\"$R\\", \\"push\\"])"', 'git -C "$dir" \\\n  push origin',
                     'repo.run_git(["push", "-q", "--no-verify", url, ref], token=token)',
                     'subprocess.run([GIT, "push", url])', 'git("push", url)', 'self.git("push")',
                     '_git(["push", url])', 'run(["push", url])', 'cmd(["push", "--force", url])',
                     'git_cmd = ["push", url]',
                     'subprocess.run(["git", "push", "pull", "HEAD"])',
                     'run_git(["push", "pull", "HEAD"])', 'run_git(["push", "pull"])',
                     'args = ["push", "pull"]\nrepo.run_git(args)',
                     'git_cmd = ["push", "pull"]'):
            with self.subTest(push=push):
                self.assertTrue(PUSH.search(push))
        for other in ('"check", "commit", "diff", "gh", "git", "log", "origin", "pr", "push", "pytest",',
                      'NEVER push or force-push', 'subprocess.run(["git", "-C", repo, "status"])',
                      'print("push")', 'allowed_commands = ["push", "pull"]', 'allowed_commands = ["push", "origin"]',
                      'logging.info("push")', 'logger.debug("push")'):
            with self.subTest(other=other):
                self.assertFalse(PUSH.search(other))
        for push in ('git --git-dir "$X" push origin HEAD',
                     'git --work-tree "$W" --git-dir "$G" push',
                     'git --namespace foo push origin'):
            with self.subTest(valued=push):
                self.assertTrue(PUSH.search(push))
        # The force check reads the whole wrapped command.
        for forced in ('run_git([\n    "push",\n    "--force-with-lease", url])', 'run_git(["push",\n    "-f"])',
                       'git push \\\n  --force origin HEAD', 'run_git(["push", url, f"+{sha}:refs/heads/x"])',
                       'run_git(["push", "--delete", url, ref])', 'run_git(["push", "--mirror", url])'):
            with self.subTest(forced=forced):
                self.assertRegex(command_span(forced, PUSH.search(forced).start()), FORCED_PUSH)
        ordinary = 'repo.run_git([\n    "push", "-q", "--no-verify", url,\n    f"{sha}:refs/heads/x"], token=token)'
        self.assertNotRegex(command_span(ordinary, PUSH.search(ordinary).start()), FORCED_PUSH)
        # The label check reads the whole call, so a method before the URL still counts as a write.
        post = 'check_call(\n    ["gh", "api", "--method", "POST",\n     f"repos/{r}/issues/{n}/labels",\n     "--input", f])'
        self.assertRegex(command_span(post, post.index('/labels')), LABEL_WRITE)
        delete = 'check_call(\n    ["gh", "api", "-X", "DELETE",\n     f"repos/{r}/issues/{n}/labels/ci:full"])'
        self.assertNotRegex(command_span(delete, delete.index('/labels')), LABEL_WRITE)

    def test_writers_share_one_per_pr_lock_without_cancellation(self):
        for name, jobs in WRITER_JOBS.items():
            doc = workflow(name)
            for job_name, role in jobs.items():
                if role == 'verify':
                    continue
                self.assertIn('concurrency', doc['jobs'][job_name], f'{name}:{job_name} needs the claude-<PR> lock')
                conc = doc['jobs'][job_name]['concurrency']
                with self.subTest(workflow=name, job=job_name):
                    self.assertEqual(conc['group'], EXPECTED_LOCK[name])
                    self.assertEqual(conc['cancel-in-progress'], 'false')
                    self.assertEqual(conc['queue'], 'max')

    def test_only_model_free_publish_jobs_can_push(self):
        for name, jobs in WRITER_JOBS.items():
            doc = workflow(name)
            for job_name, role in jobs.items():
                job = doc['jobs'][job_name]
                text = job_text(job)
                perms = job.get('permissions') or {}
                with self.subTest(workflow=name, job=job_name, role=role):
                    self.assertEqual(bool(PUSH.search(granted(text))), role == 'publish')
                    self.assertEqual(perms.get('id-token') == 'write', role == 'publish')
                    if role == 'verify':
                        # A credential-free verify job (CSL-409): no write scope and no secrets.
                        self.assertTrue(set(perms.items()) <= {('contents', 'read')})
                        self.assertNotIn('secrets.', text)
                    else:
                        self.assertEqual(perms.get('contents'), 'read')
                        # No writer job may write code, workflows or packages; PR and issue writes only.
                        self.assertTrue(set(perms) <= {'contents', 'pull-requests', 'issues', 'id-token'})
                    if role == 'model':
                        # Values, not only scope names: the model token stays read-only.
                        self.assertEqual(perms, MODEL_PERMS)
                        self.assertIsNone(MODEL_SECRETS.search(job, inherited=doc.get('env')))
                        self.assertTrue(model_github_token_ok(job))
                    self.assertTrue(uses_allowed(job, role), job_uses(job))
                    uses_model = any(str(s.get('uses', '')).startswith('anthropics/claude-code-action') for s in job['steps'])
                    self.assertEqual(uses_model, role == 'model')
                    if role != 'model':
                        # The whole job (env, step env, `with`, `uses`, scripts), not only run/with text.
                        self.assertIsNone(MODEL_ACCESS.search(str(job)))
                    if role == 'publish':
                        self.assertNotRegex(text, r'"\+[^"]*:refs/heads|\+\{[^}]*\}:refs/heads')
                        # The whole push command, however it is wrapped: one ordinary fast-forward.
                        for match in PUSH.finditer(granted(text)):
                            self.assertNotRegex(command_span(granted(text), match.start()), FORCED_PUSH)
                        self.assertIn('# Ordinary push: no force, no lease.', text)
                    if role == 'model':
                        allowed = re.findall(r'--allowedTools "([^"]*)"', text)
                        denied = re.findall(r'--disallowedTools "([^"]*)"', text)
                        self.assertEqual(len(allowed), 1)
                        self.assertTrue(denied and 'Bash(git push:*)' in denied[0])
                        bash = [tool for tool in allowed[0].split(',') if tool.startswith('Bash')]
                        self.assertEqual(bash, [MODEL_BASH])
                        for step in job['steps']:
                            if str(step.get('uses', '')).startswith('actions/checkout'):
                                self.assertEqual(step['with']['persist-credentials'], 'false')

    def test_retries_are_bounded_and_pausable(self):
        claude = (WORKFLOWS / 'claude.yml').read_text()
        writer = (WORKFLOWS / 'claude-conflict-writer.yml').read_text()
        scan = (WORKFLOWS / 'conflict-scan.yml').read_text()
        # Eight-commit guard: one constant per claude.yml prepare job, compared in the @claude path,
        # and the Devin path's `cap` step; no other cap value anywhere.
        self.assertEqual(re.findall(r'^\s*LOOP_CAP = (\d+)$', claude, re.MULTILINE), ['8', '8'])
        # A conflict-writer guard (CSL-419) may add one, but only at the same cap.
        self.assertTrue(set(re.findall(r'^\s*LOOP_CAP = (\d+)$', writer + scan, re.MULTILINE)) <= {'8'})
        self.assertIn('if streak >= LOOP_CAP:', job_text(workflow('claude.yml')['jobs']['mention-prepare']))
        cap = job_text(workflow('claude.yml')['jobs']['fixer-prepare'])
        self.assertRegex(cap, r'if \[ "\$total" -ge 250 \] && \[ "\$count" -lt 8 \]')
        self.assertIn('echo "loop_commit_count=$count"', cap)
        # The Devin path reserves bounded attempts; every publisher allows one publish per prepared run.
        self.assertEqual(set(re.findall(r'^\s*MAX_ATTEMPTS = (\d+)$', claude, re.MULTILINE)), {'3'})
        self.assertIn('Select pending findings and reserve bounded attempt', claude)
        for name, jobs in WRITER_JOBS.items():
            doc = workflow(name)
            for job_name, role in jobs.items():
                if role == 'publish':
                    with self.subTest(publisher=f'{name}:{job_name}'):
                        self.assertIn('publish attempt already used; nothing published', job_text(doc['jobs'][job_name]))
        # The human pause, per job: every prepare and publish job reads both labels and the repository
        # variable, every publisher refuses on them before pushing, and the scan skips paused PRs.
        pause_py = 'PAUSE_LABELS = frozenset({"claude:pause", "hold"})'
        pause_jq = 'select(. == "claude:pause" or . == "hold")'
        for name, jobs in WRITER_JOBS.items():
            doc = workflow(name)
            for job_name, role in jobs.items():
                if role not in ('prep', 'publish'):
                    continue
                job = doc['jobs'][job_name]
                text = job_text(job)
                with self.subTest(pause=f'{name}:{job_name}'):
                    self.assertTrue(pause_py in text or pause_jq in text)
                    self.assertIn('${{ vars.CLAUDE_FIXER_PAUSE }}', str(job))
                    if role == 'publish':
                        self.assertIn('reason = pause_refusal(pr, env)', text)
        scan_job = workflow('conflict-scan.yml')['jobs']['conflict-scan']
        self.assertIn(pause_py, job_text(scan_job))
        self.assertIn('${{ vars.CLAUDE_FIXER_PAUSE }}', str(scan_job))

    def test_lock_group_mutation_rejects_the_other_workflows_expression(self):
        # Before: any of the two expressions passed. After: each workflow is pinned.
        old = re.compile(r'^claude-\$\{\{ (needs\.resolve-pr\.outputs\.pr_number|inputs\.pr_number) \}\}$')
        swapped = 'claude-${{ inputs.pr_number }}'
        self.assertRegex(swapped, old)
        self.assertNotEqual(swapped, EXPECTED_LOCK['claude.yml'])
        job = copy.deepcopy(workflow('claude.yml')['jobs']['claude-mention'])
        job['concurrency']['group'] = swapped
        self.assertNotEqual(job['concurrency']['group'], EXPECTED_LOCK['claude.yml'])

    def test_label_write_mutations_cover_issue_patch_graphql_and_full_set(self):
        mixed = 'write("POST", f"{root}/issues/{n}/labels", {"labels": [HANDOFF_LABEL, "ci:full"]})'
        self.assertIn('HANDOFF_LABEL', mixed)
        self.assertTrue(ci_label_add_violations(mixed))
        patch = ('check_call(["gh", "api", "--method", "PATCH", '
                 'f"repos/{r}/issues/{n}", "--input", '
                 '\'{"labels": [HANDOFF_LABEL, "ci:full"]}\'])')
        self.assertFalse(re.search(r'/labels\b', patch))
        self.assertTrue(ci_label_add_violations(patch))
        graphql = ('gh api graphql -f query=\'mutation { addLabelsToLabelable(input: '
                   '{labelableId: $id, labelIds: ["ci:full"]}) { clientMutationId } }\'')
        self.assertFalse(re.search(r'/labels\b', graphql))
        self.assertTrue(ci_label_add_violations(graphql))
        handoff = 'write("POST", f"{root}/issues/{n}/labels", {"labels": [HANDOFF_LABEL]})'
        self.assertEqual(ci_label_add_violations(handoff), [])
        title = 'check_call(["gh", "api", "--method", "PATCH", f"repos/{r}/issues/{n}", "-f", "title=x"])'
        self.assertEqual(ci_label_add_violations(title), [])

    def test_handoff_constant_mutations_reject_empty_and_wrong_values(self):
        def old(text):
            return set(re.findall(r'HANDOFF_LABEL = "([^"]+)"', text)) - {'needs-decision'}
        self.assertEqual(old(''), set())
        self.assertEqual(handoff_constants(''), [])
        quoted = "HANDOFF_LABEL = 'needs-decision'\nMODEL_API_LABEL: needs-decision\n"
        self.assertEqual(old(quoted), set())
        self.assertEqual(set(handoff_constants(quoted)), {'needs-decision'})
        self.assertEqual(set(handoff_constants('HANDOFF_LABEL = \'ci:full\'\n')), {'ci:full'})
        self.assertEqual(set(handoff_constants('MODEL_API_LABEL: enhancement\n')), {'enhancement'})

    def test_model_job_permission_and_secret_mutations(self):
        job = copy.deepcopy(workflow('claude.yml')['jobs']['claude-mention'])
        job['permissions']['issues'] = 'write'
        self.assertTrue(set(job['permissions']) <= {'contents', 'pull-requests', 'issues', 'id-token'})
        self.assertNotEqual(job['permissions'], MODEL_PERMS)
        leaked = copy.deepcopy(workflow('claude.yml')['jobs']['claude-mention'])
        leaked['steps'][0]['env'] = {**(leaked['steps'][0].get('env') or {}),
                                     'GH_TOKEN': '${{ secrets.MY_PAT }}'}
        self.assertIsNotNone(MODEL_SECRETS.search(leaked))
        live = workflow('claude.yml')['jobs']['claude-mention']
        self.assertIsNone(MODEL_SECRETS.search(live))
        self.assertIsNone(MODEL_SECRETS.search(live, inherited=workflow('claude.yml').get('env')))
        workflow_env = {'GH_TOKEN': '${{ secrets.MY_PAT }}'}
        self.assertIsNotNone(MODEL_SECRETS.search(live, inherited=workflow_env))
        oauth_env = copy.deepcopy(live)
        oauth_env['steps'][0]['env'] = {**(oauth_env['steps'][0].get('env') or {}),
                                        'CLAUDE_CODE_OAUTH_TOKEN':
                                            '${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}'}
        self.assertIsNotNone(MODEL_SECRETS.search(oauth_env))
        gh_secret = copy.deepcopy(live)
        gh_secret['steps'][0]['env'] = {**(gh_secret['steps'][0].get('env') or {}),
                                        'GH_TOKEN': '${{ secrets.GITHUB_TOKEN }}'}
        self.assertIsNotNone(MODEL_SECRETS.search(gh_secret))
        oauth_job_env = copy.deepcopy(live)
        oauth_job_env['env'] = {**(oauth_job_env.get('env') or {}),
                                'claude_code_oauth_token':
                                    '${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}'}
        self.assertIsNone(_SECRET_EXPR.search(_OAUTH_ACTION_INPUT.sub('', str(oauth_job_env))))
        self.assertIsNotNone(MODEL_SECRETS.search(oauth_job_env))
        oauth_step_env = copy.deepcopy(live)
        for step in oauth_step_env['steps']:
            if str(step.get('uses') or '').startswith('anthropics/claude-code-action'):
                step['env'] = {**(step.get('env') or {}),
                               'claude_code_oauth_token':
                                   '${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}'}
                break
        self.assertIsNone(_SECRET_EXPR.search(_OAUTH_ACTION_INPUT.sub('', str(oauth_step_env))))
        self.assertIsNotNone(MODEL_SECRETS.search(oauth_step_env))
        if_secret = copy.deepcopy(live)
        if_secret['steps'][0]['if'] = "${{ secrets.MY_PAT != '' }}"
        self.assertIsNotNone(MODEL_SECRETS.search(if_secret))
        container = copy.deepcopy(live)
        container['container'] = {
            'image': 'x',
            'credentials': {'password': '${{ secrets.MY_PAT }}'},
        }
        self.assertIsNotNone(MODEL_SECRETS.search(container))
        indexed = copy.deepcopy(live)
        indexed.setdefault('env', {})['GH_TOKEN'] = "${{ secrets['MY_PAT'] }}"
        self.assertIsNotNone(MODEL_SECRETS.search(indexed))
        lower = copy.deepcopy(live)
        lower.setdefault('env', {})['GH_TOKEN'] = '${{ secrets.my_pat }}'
        self.assertIsNotNone(MODEL_SECRETS.search(lower))
        mixed = copy.deepcopy(live)
        mixed.setdefault('env', {})['GH_TOKEN'] = "${{ secrets['My_Pat'] }}"
        self.assertIsNotNone(MODEL_SECRETS.search(mixed))
        quoted_indexed = copy.deepcopy(live)
        run = 'echo "${{ secrets[\'MY_PAT\'] }}"'
        quoted_indexed['steps'][0]['run'] = run
        self.assertIsNone(_SECRET_EXPR.search(str({'run': run})))
        self.assertIsNotNone(MODEL_SECRETS.search(quoted_indexed))
        literal = copy.deepcopy(live)
        literal['steps'][0]['run'] = "echo 'secrets.MY_PAT'"
        self.assertIsNone(MODEL_SECRETS.search(literal))
        for dump in ('echo ${{ toJSON(secrets) }}', 'echo ${{ toJson(secrets) }}',
                     'echo ${{ join(secrets.*) }}',
                     "echo ${{ secrets[format('MY_{0}','PAT')] }}"):
            dumped = copy.deepcopy(live)
            dumped['steps'][0]['run'] = dump
            self.assertIsNotNone(MODEL_SECRETS.search(dumped), dump)
        title = copy.deepcopy(live)
        title['steps'][0]['if'] = "${{ contains(github.event.pull_request.title, 'secrets') }}"
        self.assertIsNone(MODEL_SECRETS.search(title))
        phrase = copy.deepcopy(live)
        phrase['steps'][0]['if'] = (
            "${{ contains(github.event.pull_request.title, 'update secrets handling') }}")
        self.assertIsNone(MODEL_SECRETS.search(phrase))
        doubled = copy.deepcopy(live)
        doubled['steps'][0]['if'] = (
            "${{ contains(github.event.pull_request.title, 'it''s secrets') }}")
        self.assertIsNone(MODEL_SECRETS.search(doubled))
        inputs = copy.deepcopy(live)
        inputs['steps'][0]['if'] = '${{ github.event.inputs.secrets }}'
        self.assertIsNone(MODEL_SECRETS.search(inputs))
        quoted_span = copy.deepcopy(live)
        quoted_span['steps'][0]['run'] = '''echo ${{ 'x" && secrets.MY_PAT && "y' }}'''
        self.assertIsNone(MODEL_SECRETS.search(quoted_span))
        after_literal = copy.deepcopy(live)
        after_literal['steps'][0]['run'] = "echo ${{ contains(foo, 'x') && secrets.MY_PAT }}"
        self.assertIsNotNone(MODEL_SECRETS.search(after_literal))
        after_doubled = copy.deepcopy(live)
        after_doubled['steps'][0]['run'] = (
            "echo ${{ contains(foo, 'it''s x') && secrets.MY_PAT }}")
        self.assertIsNotNone(MODEL_SECRETS.search(after_doubled))
        self.assertTrue(model_github_token_ok(live))
        self.assertFalse(model_github_token_ok(job))
        pat_input = copy.deepcopy(live)
        for step in pat_input['steps']:
            with_block = step.get('with') or {}
            if 'github_token' in with_block:
                with_block['github_token'] = '${{ secrets.MY_PAT }}'
                step['with'] = with_block
                break
        self.assertIsNotNone(_SECRET_GITHUB_TOKEN.search(str(pat_input)))
        self.assertFalse(model_github_token_ok(pat_input))
        gh_token_input = copy.deepcopy(live)
        for step in gh_token_input['steps']:
            with_block = step.get('with') or {}
            if 'github_token' in with_block:
                with_block['github_token'] = '${{ secrets.GITHUB_TOKEN }}'
                step['with'] = with_block
                break
        self.assertIsNotNone(_SECRET_GITHUB_TOKEN.search(str(gh_token_input)))
        self.assertFalse(model_github_token_ok(gh_token_input))
        vars_pat = copy.deepcopy(live)
        for step in vars_pat['steps']:
            if str(step.get('uses') or '').startswith('anthropics/claude-code-action'):
                step.setdefault('with', {})['github_token'] = '${{ vars.MY_PAT }}'
                break
        vars_pat.setdefault('steps', []).append(
            {'name': 'decoy', 'run': 'echo github_token: ${{ github.token }}'})
        self.assertIsNotNone(MODEL_GITHUB_TOKEN_EXCEPTION.search(str(vars_pat)))
        self.assertFalse(model_github_token_ok(vars_pat))
        multiline = copy.deepcopy(live)
        decoy = '${{ vars.MY_PAT }}\ngithub_token: ${{ github.token }}'
        for step in multiline['steps']:
            with_block = step.get('with') or {}
            if 'github_token' in with_block:
                with_block['github_token'] = decoy
                step['with'] = with_block
                break
        self.assertIsNotNone(MODEL_GITHUB_TOKEN_EXCEPTION.search(f'github_token: {decoy}'))
        self.assertIsNone(MODEL_SECRETS.search(multiline))
        self.assertFalse(model_github_token_ok(multiline))
        string_perms = copy.deepcopy(live)
        string_perms['permissions'] = 'read-all'
        self.assertFalse(model_github_token_ok(string_perms))

    def test_quoted_job_key_mutation_is_inventoried(self):
        text = (WORKFLOWS / 'claude.yml').read_text()
        mutated = text.replace('\n  claude-mention:',
                               '\n  "extra-job":\n    runs-on: ubuntu-latest\n  claude-mention:', 1)
        expected = set(WRITER_JOBS['claude.yml']) | NON_WRITER_JOBS['claude.yml']
        self.assertEqual(job_keys(text), expected)
        self.assertIn('extra-job', job_keys(mutated))
        self.assertIn('extra-job', yaml.load(mutated, Loader=yaml.BaseLoader)['jobs'])
        self.assertNotEqual(job_keys(mutated), expected)

    def test_reusable_writer_caller_without_model_markers_is_inventoried(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / 'quiet.yml').write_text(
                'name: quiet\non: workflow_dispatch\njobs:\n  call:\n'
                '    uses: ./.github/workflows/claude-conflict-writer.yml\n'
                '    secrets: inherit\n', encoding='utf-8')
            markers = {path.name for path in tmp.glob('*.y*ml')
                       if re.search(r'anthropics/claude-code|ANTHROPIC_API_KEY|CLAUDE_CODE_OAUTH_TOKEN',
                                    path.read_text(encoding='utf-8'))}
            self.assertEqual(markers, set())
            self.assertEqual(writer_callers(tmp), {'quiet.yml'})

    def test_unapproved_action_in_prep_or_model_is_rejected(self):
        prep = copy.deepcopy(workflow('claude.yml')['jobs']['mention-prepare'])
        prep.setdefault('steps', []).append({'uses': 'ad-m/github-push-action@v0.8.0'})
        self.assertFalse(PUSH.search(job_text(prep)))
        self.assertFalse(uses_allowed(prep, 'prep'))
        model = copy.deepcopy(workflow('claude.yml')['jobs']['claude-mention'])
        model['steps'].append({'uses': './.github/actions/publish/action.yml'})
        self.assertFalse(uses_allowed(model, 'model'))
        live = workflow('claude.yml')['jobs']['claude-mention']
        self.assertTrue(any(u.startswith('oven-sh/setup-bun@') for u in job_uses(live)))
        self.assertTrue(uses_allowed(live, 'model'))
        tagged = copy.deepcopy(live)
        for step in tagged['steps']:
            uses = str(step.get('uses') or '')
            if uses.startswith('oven-sh/setup-bun@'):
                step['uses'] = 'oven-sh/setup-bun@v1.2.3'
                break
        self.assertTrue(any(u.startswith('oven-sh/setup-bun@') for u in job_uses(tagged)))
        self.assertTrue(any(u.startswith('oven-sh/setup-bun@') for u in ROLE_USES['model']))
        self.assertFalse(uses_sha_pinned('oven-sh/setup-bun@v1.2.3'))
        self.assertFalse(uses_allowed(tagged, 'model'))
        pin = next(u for u in job_uses(live) if u.startswith('oven-sh/setup-bun@'))
        self.assertRegex(pin, r'@([0-9a-f]{40})$')
        self.assertTrue(uses_sha_pinned(pin))

    def test_runbook_names_the_github_token_dispatch_exception(self):
        runbook = next(path for path in (ROOT / 'docs').iterdir() if path.name == 'ci-runbook.md')
        section = runbook.read_text(encoding='utf-8').split('## 5.', 1)[0]
        self.assertIn('workflow_dispatch', section)
        self.assertIn('repository_dispatch', section)
        self.assertIn('forward-conflict-scan', section)
        self.assertIn('github_token: ${{ github.token }}', section)
        self.assertIn('CSL-428', section)


class P9ProbeWorkflowTests(unittest.TestCase):
    """CSL-474 P9 Probes 0/2: dispatch-only measurement, not a writer."""

    def setUp(self):
        self.path = WORKFLOWS / P9_PROBE_FILE
        self.text = self.path.read_text(encoding='utf-8')
        self.doc = workflow(P9_PROBE_FILE)

    def test_dispatch_only_least_privilege(self):
        header = self.text.split('\njobs:', 1)[0]
        self.assertIn('workflow_dispatch:', header)
        self.assertIn(
            'With enable_probe2_oauth=false (the intended dispatch), Probe 2 is',
            header,
        )
        self.assertIn('INCONCLUSIVE/non-green', header)
        self.assertIn('That is not a CLI isolation finding', header)
        self.assertIn('P9 in this form rests on Probe 0', header)
        self.assertNotIn('\npull_request:', header)
        self.assertNotIn('\npush:', header)
        self.assertNotIn('\nschedule:', header)
        self.assertNotIn('\nissue_comment:', header)
        self.assertNotIn('\nrepository_dispatch:', header)
        self.assertNotIn('\nworkflow_call:', header)
        self.assertNotIn('\nworkflow_run:', header)
        self.assertEqual(self.doc.get('permissions'), {'contents': 'read'})
        self.assertEqual(set(self.doc['jobs']), {'probe-0', 'probe-2'})
        for name, job in self.doc['jobs'].items():
            with self.subTest(job=name):
                self.assertEqual(job['runs-on'], 'ubuntu-24.04')
                self.assertEqual(job.get('permissions'), {'contents': 'read'})
                self.assertNotIn('id-token', job.get('permissions') or {})
                self.assertFalse(PUSH.search(job_text(job)))
                uses = [str(s.get('uses') or '') for s in job.get('steps') or []]
                self.assertFalse(any(u.startswith('anthropics/claude-code-action') for u in uses))

    def test_oauth_input_defaults_off_and_gates_the_secret(self):
        spec = self.doc['on']['workflow_dispatch']['inputs']['enable_probe2_oauth']
        self.assertEqual(spec['type'], 'boolean')
        self.assertIn(spec['default'], (False, 'false'))
        oauth_steps = [s for s in self.doc['jobs']['probe-2']['steps']
                       if 'secrets.CLAUDE_CODE_OAUTH_TOKEN' in str(s)]
        self.assertEqual(len(oauth_steps), 1)
        self.assertIn('enable_probe2_oauth', str(oauth_steps[0].get('if')))
        self.assertNotIn('secrets.CLAUDE_CODE_OAUTH_TOKEN', str(self.doc['jobs']['probe-0']))
        self.assertNotIn('secrets: inherit', self.text)
        hooked = [s for s in self.doc['jobs']['probe-2']['steps']
                  if s.get('name') == 'Probe 2 OAuth hooked trials without token env']
        self.assertEqual(len(hooked), 1)
        self.assertNotIn('secrets.CLAUDE_CODE_OAUTH_TOKEN', str(hooked[0]))

    def test_same_cli_pin_as_e3(self):
        pin = (ROOT / 'scripts' / 'ci' / 'claude-code-2.1.306-linux-x64.pin').read_text(encoding='utf-8')
        sha = [line.split('=', 1)[1].strip() for line in pin.splitlines() if line.startswith('sha256=')][0]
        self.assertIn('2.1.306', self.text)
        self.assertIn(sha, self.text)
        self.assertIn('a5a423f75c251dcbff8a74e99f83b2cb03c72bfa', self.text)
        self.assertIn('https://downloads.claude.ai/claude-code-releases', self.text)
        self.assertIn('${RELEASE_BASE}/${CLAUDE_VERSION}/${PLATFORM}/claude', self.text)
        self.assertNotIn('claude.ai/install', self.text)
        self.assertNotIn('anthropics/claude-code', self.text)

    def test_probe_bodies_record_host_and_p1_facts(self):
        p0 = job_text(self.doc['jobs']['probe-0'])
        self.assertIn('cgroup.controllers', p0)
        self.assertIn('cgroup.kill', p0)
        self.assertIn('cgroup.procs', p0)
        self.assertIn('apparmor_restrict_unprivileged_userns', p0)
        self.assertIn('unshare --user --pid', p0)
        self.assertIn('HOSTED_ONLY', p0)
        self.assertIn('CLASS_CGROUP_PROCS observational', p0)
        self.assertIn('} 2>/tmp/csl-p9-cgroup-procs.err', p0)
        p2 = job_text(self.doc['jobs']['probe-2'])
        self.assertIn('settings.local.json', p2)
        self.assertIn('$WORK/AGENTS.md', p2)
        self.assertIn('instructionFiles', p2)
        self.assertIn('CSL474_AGENTS_CANARY', p2)
        self.assertIn('--setting-sources user', p2)
        self.assertIn('mcp.empty', p2)

    def test_probe2_oracle_forces_tool_and_positive_control(self):
        jobs = self.doc['jobs']
        self.assertEqual(jobs['probe-0']['timeout-minutes'], '10')
        self.assertEqual(jobs['probe-2']['timeout-minutes'], '20')
        p2 = job_text(jobs['probe-2'])
        self.assertIn('--allowedTools "Bash(true)"', p2)
        self.assertIn('--max-turns 4', p2)
        self.assertIn('CSL474_AGENTS_LOADED', p2)
        self.assertIn('HOOK_CONTROL', p2)
        self.assertIn('INCONCLUSIVE', p2)
        self.assertIn('EVIDENCE_EXIT non-green', p2)
        self.assertIn('Positive control omits --setting-sources user', p2)
        self.assertIn('AGENTS.md positive control: user instructionFiles unset', p2)
        self.assertIn('canary isolation is --setting-sources user only', p2)
        self.assertIn('instructionFiles not toggled between CONTROL and ISOLATION', p2)
        self.assertIn('run_probe_invoke CONTROL ""', p2)
        self.assertIn('run_probe_invoke ISOLATION user', p2)
        self.assertIn('run_probe_invoke MCPRO user "$MCP_RO"', p2)
        self.assertIn('} 2>/tmp/csl-p9-mcp-write.err', p2)
        self.assertIn('refused-unverified-isolation', p2)
        self.assertIn('TOKEN_EXPOSURE', p2)
        self.assertNotIn("absent = pass", p2)
        self.assertIn('AUTH_BOOTSTRAP FAIL', p2)
        self.assertIn('not recording DONE', p2)
        self.assertIn('Hook-marker alone or timeout is not a completed session', p2)
        self.assertIn('CANARY_ISOLATION PASS requires completed P9PROBE2OK', p2)
        self.assertIn('[ "$MCPRO" = PASS ]', p2)
        self.assertIn('MCPRO=INCONCLUSIVE', p2)
        self.assertIn('MCPRO PASS requires user hook marker', p2)
        self.assertIn('skipping hooked OAuth trials; bootstrap did not PASS', p2)
        self.assertNotIn('session_ok', p2)
        self.assertIn('Probe 2 OAuth bootstrap in clean cwd', self.text)
        self.assertIn('Probe 2 OAuth hooked trials without token env', self.text)

if __name__ == '__main__':
    unittest.main()


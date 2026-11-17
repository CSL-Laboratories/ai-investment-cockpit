"""Offline candidate identity/full-CI regressions; no external mutations."""
import copy
import importlib
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
m = importlib.import_module('scripts.ci.merge_candidate')

REPO = 'owner/repo'
H, B, M, T, X = (c * 40 for c in 'abcde')
N = 'f' * 32
# Trusted policy = the default-branch commit; with base ref main it equals B.
POLICY = m.Policy(B, 'main')


def pr():
    return {'number': 42, 'id': 420, 'state': 'open', 'draft': False,
            'mergeable': True, 'merge_commit_sha': M,
            'head': {'sha': H, 'ref': 'feature', 'repo': {'id': 10, 'full_name': REPO}},
            'base': {'sha': B, 'ref': 'main', 'repo': {'id': 10, 'full_name': REPO}}}


def commit():
    return {'sha': M, 'parents': [{'sha': B}, {'sha': H}], 'tree': {'sha': T}}


class API:
    def __init__(self):
        self.pr, self.commit, self.calls = pr(), commit(), []
        self.second = None
        self.refs = {name: {'ref': f'refs/heads/{name}', 'object': {'type': 'commit', 'sha': tip}}
                     for name, tip in [('feature', H), ('main', B)]}
        self.second_refs = {}

    def __call__(self, path):
        self.calls.append(path)
        if path == f'repos/{REPO}':
            return {'id': 10, 'full_name': REPO, 'default_branch': 'main'}
        if path == f'repos/{REPO}/pulls/42':
            return copy.deepcopy(self.second if self.calls.count(path) > 1 and self.second else self.pr)
        prefix = f'repos/{REPO}/git/ref/heads/'
        if path.startswith(prefix):
            name = path[len(prefix):]
            value = self.second_refs.get(name, self.refs[name]) if self.calls.count(path) > 1 else self.refs[name]
            return copy.deepcopy(value)
        if path == f'repos/{REPO}/git/commits/{M}':
            return copy.deepcopy(self.commit)
        raise AssertionError(path)


def candidate():
    return m.resolve_candidate(API(), REPO, 42)


def evidence(kind='ci'):
    c = candidate()
    # Candidate runs execute on the default branch at the policy commit.
    run = {'id': 100, 'workflow_id': 11, 'repository': {'id': 10, 'full_name': REPO},
           'head_repository': {'id': 10, 'full_name': REPO},
           'event': 'workflow_dispatch', 'path': f'.github/workflows/{kind}.yml',
           'display_title': f'candidate:{c.key}:{kind}:{N}', 'head_sha': POLICY.sha,
           'head_branch': POLICY.branch, 'run_attempt': 1, 'status': 'completed', 'conclusion': 'success'}
    jobs = [{'id': i, 'run_id': 100, 'run_attempt': 1, 'head_sha': POLICY.sha, 'name': name,
             'status': 'completed', 'conclusion': 'success'}
            for i, name in enumerate(sorted(m.REQUIRED_JOBS[kind]), 1)]
    return run, jobs


class CandidateTests(unittest.TestCase):
    def test_exact_identity_and_serialized_inputs(self):
        api = API()
        c = m.resolve_candidate(api, REPO, 42)
        self.assertEqual((c.head, c.base, c.merge, c.tree), (H, B, M, T))
        self.assertEqual(c.fields(), {'pr_number': '42', 'pr_head_sha': H, 'pr_base_sha': B,
                                     'merge_sha': M, 'merge_tree_sha': T, 'candidate_id': c.key})
        self.assertEqual(len(c.key), 64)
        self.assertEqual(api.calls[-1], f'repos/{REPO}/pulls/42')
        self.assertEqual(c, m.Candidate(**c.record()))

    def test_lagging_pr_head_or_base_metadata_cannot_qualify(self):
        for name in ('feature', 'main'):
            api = API()
            api.refs[name]['object']['sha'] = X
            with self.subTest(ref=name), self.assertRaises(m.CandidateError):
                m.resolve_candidate(api, REPO, 42)
            self.assertNotIn(f'repos/{REPO}/git/commits/{M}', api.calls)

    def test_ref_movement_during_resolution_is_detected_even_if_pr_stays_stale(self):
        for name in ('feature', 'main'):
            api = API()
            api.second_refs[name] = copy.deepcopy(api.refs[name])
            api.second_refs[name]['object']['sha'] = X
            with self.subTest(ref=name), self.assertRaises(m.CandidateError):
                m.resolve_candidate(api, REPO, 42)
            self.assertIn(f'repos/{REPO}/git/commits/{M}', api.calls)

    def test_ref_names_types_and_shapes_are_strict_on_both_reads(self):
        for closing in (False, True):
            for name in ('feature', 'main'):
                malformed = [None, [], {'ref': 'refs/tags/' + name, 'object': {'type': 'commit', 'sha': H}},
                             {'ref': 'refs/heads/other', 'object': {'type': 'commit', 'sha': H}},
                             {'ref': 'refs/heads/' + name, 'object': None},
                             {'ref': 'refs/heads/' + name, 'object': []},
                             {'ref': 'refs/heads/' + name, 'object': {'type': 'tag', 'sha': H}},
                             {'ref': 'refs/heads/' + name, 'object': {'type': 'commit', 'sha': 'short'}}]
                for value in malformed:
                    api = API()
                    (api.second_refs if closing else api.refs)[name] = value
                    with self.subTest(closing=closing, ref=name, value=value), self.assertRaises(m.CandidateError):
                        m.resolve_candidate(api, REPO, 42)

    def test_branch_slashes_use_exact_ref_endpoint_and_response(self):
        api = API()
        api.pr['head']['ref'] = 'codex/topic'
        api.refs['codex/topic'] = {'ref': 'refs/heads/codex/topic', 'object': {'type': 'commit', 'sha': H}}
        self.assertEqual(m.resolve_candidate(api, REPO, 42).head_ref, 'codex/topic')
        self.assertEqual(api.calls.count(f'repos/{REPO}/git/ref/heads/codex/topic'), 2)

    def test_closing_ref_api_failure_never_returns_a_candidate(self):
        api = API()
        def get(path):
            if path.endswith('/git/ref/heads/main') and path in api.calls:
                raise OSError('ref unavailable')
            return api(path)
        with self.assertRaises(OSError):
            m.resolve_candidate(get, REPO, 42)

    def test_every_identity_component_changes_candidate_key(self):
        c = candidate()
        for key, value in c.record().items():
            with self.subTest(key=key):
                changed = value + 1 if isinstance(value, int) else value + 'x'
                self.assertNotEqual(replace(c, **{key: changed}).key, c.key)

    def test_base_head_merge_or_target_changes_during_resolution_refuse(self):
        for side, field, value in [('base', 'sha', X), ('head', 'sha', X),
                                   ('base', 'ref', 'release'), ('head', 'ref', 'other')]:
            api = API()
            api.second = pr()
            api.second[side][field] = value
            with self.subTest(side=side, field=field), self.assertRaises(m.CandidateError):
                m.resolve_candidate(api, REPO, 42)
        api = API()
        api.second = {**pr(), 'merge_commit_sha': X}
        with self.assertRaises(m.CandidateError):
            m.resolve_candidate(api, REPO, 42)

    def test_not_ready_or_malformed_pr_is_refused(self):
        for field, value in [('state', 'closed'), ('draft', True), ('mergeable', None),
                             ('mergeable', False), ('merge_commit_sha', None),
                             ('number', 43), ('id', True), ('head', None), ('base', [])]:
            api = API()
            api.pr[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(m.CandidateError):
                m.resolve_candidate(api, REPO, 42)

    def test_fork_and_reused_repo_name_are_refused(self):
        for side in ('head', 'base'):
            for field, value in [('id', 20), ('full_name', 'foreign/repo')]:
                api = API()
                api.pr[side]['repo'][field] = value
                with self.subTest(side=side, field=field), self.assertRaises(m.CandidateError):
                    m.resolve_candidate(api, REPO, 42)

    def test_exact_merge_parents_order_and_tree_are_required(self):
        bad = [{'sha': X}, {'tree': None}, {'tree': {'sha': 'short'}},
               {'parents': [{'sha': H}, {'sha': B}]}, {'parents': [{'sha': B}]},
               {'parents': [{'sha': B}, {'sha': H}, {'sha': X}]},
               {'parents': [None, {'sha': H}]}]
        for change in bad:
            api = API()
            api.commit.update(change)
            with self.subTest(change=change), self.assertRaises(m.CandidateError):
                m.resolve_candidate(api, REPO, 42)

    def test_unsafe_refs_and_protected_head_never_become_candidates(self):
        invalid = ('-option', 'feature/../main', 'feature//x', '.hidden', 'feature/.x',
                   'feature/x.lock', 'feature.', 'feature/', 'a b', 'a\nb', 'a@{b}', 'a~b')
        for side in ('head', 'base'):
            for ref in invalid + (('main', 'master', 'HEAD') if side == 'head' else ()):
                api = API()
                api.pr[side]['ref'] = ref
                with self.subTest(side=side, ref=ref), self.assertRaises(m.CandidateError):
                    m.resolve_candidate(api, REPO, 42)

    def test_lookup_error_never_produces_candidate(self):
        def fail(path):
            raise OSError('API unavailable')
        with self.assertRaises(OSError):
            m.resolve_candidate(fail, REPO, 42)

    def test_checkout_and_full_input_tuple_are_exact(self):
        c = candidate()
        checkout = {'sha': M, 'parents': [B, H], 'tree': T}
        m.validate_checkout(c, c.fields(), checkout)
        for field in c.fields():
            values = c.fields()
            values[field] = 'wrong'
            with self.subTest(field=field), self.assertRaises(m.CandidateError):
                m.validate_checkout(c, values, checkout)
        for change in [{'sha': H}, {'parents': [H, B]}, {'tree': X}]:
            with self.assertRaises(m.CandidateError):
                m.validate_checkout(c, c.fields(), {**checkout, **change})
        # The nonce is bound by main(), not by the identity tuple: fields() never
        # carries it, so a nonce-only omission cannot be what makes a tuple partial.
        self.assertNotIn('dispatch_nonce', c.fields())
        for partial in [{}, {k: v for k, v in c.fields().items() if k != 'candidate_id'},
                        {k: v for k, v in c.fields().items() if k != 'merge_tree_sha'},
                        {**c.fields(), 'extra': 'x'}, {**c.fields(), 'dispatch_nonce': N}]:
            self.assertNotEqual(partial, c.fields())
            with self.subTest(partial=sorted(partial)), self.assertRaises(m.CandidateError):
                m.validate_checkout(c, partial, checkout)


class PolicyTests(unittest.TestCase):
    def test_policy_identity_is_strict(self):
        self.assertEqual(m.Policy(B, 'main'), POLICY)
        for sha_value, branch in [('short', 'main'), (B.upper(), 'main'), (None, 'main'), (B, ''),
                                  (B, '-x'), (B, 'a b'), (B, 'feature/../main'), (B, None), (7, 'main')]:
            with self.subTest(sha=sha_value, branch=branch), self.assertRaises(m.CandidateError):
                m.Policy(sha_value, branch)

    def test_policy_and_candidate_roles_cannot_be_confused(self):
        c = candidate()
        m.policy_binding(POLICY, c)
        # A default branch that is not the PR base needs no base equality.
        m.policy_binding(m.Policy(X, 'trunk'), c)
        for policy, cand in [(m.Policy(H, 'main'), c), (m.Policy(M, 'main'), c),
                             (m.Policy(X, 'main'), c), (m.Policy(B, 'feature'), c),
                             (m.Policy(X, 'feature'), c), (None, c), (POLICY, None),
                             (POLICY, {'head': H})]:
            with self.subTest(policy=policy, candidate=type(cand)), self.assertRaises(m.CandidateError):
                m.policy_binding(policy, cand)

    def test_live_default_branch_is_strict(self):
        self.assertEqual(m.repository_default_branch(API(), REPO), 'main')
        self.assertEqual(m.repository_default_branch(API(), REPO, 10), 'main')
        for data in [None, [], {}, {'id': 10, 'full_name': 'other/repo', 'default_branch': 'main'},
                     {'id': 0, 'full_name': REPO, 'default_branch': 'main'},
                     {'id': 10, 'full_name': REPO, 'default_branch': ''},
                     {'id': 10, 'full_name': REPO, 'default_branch': '-x'},
                     {'id': 10, 'full_name': REPO, 'default_branch': None},
                     {'id': 10, 'full_name': REPO}]:
            with self.subTest(data=data), self.assertRaises(m.CandidateError):
                m.repository_default_branch(lambda path: data, REPO)
        with self.assertRaises(m.CandidateError):
            m.repository_default_branch(API(), REPO, 20)
        with self.assertRaises(OSError):
            m.repository_default_branch(lambda path: (_ for _ in ()).throw(OSError('down')), REPO)

    def witness(self, **changes):
        return {'sha': B, 'workflow_sha': B, 'policy_sha': B, 'ref': 'refs/heads/main',
                'ref_name': 'main', 'branch': 'main', **changes}

    def test_witness_binds_workflow_checkout_and_default_branch(self):
        self.assertEqual(m.policy_witness(self.witness(), 'main'), POLICY)
        for change in [{'sha': H}, {'workflow_sha': H}, {'policy_sha': H}, {'sha': X},
                       {'workflow_sha': X}, {'policy_sha': X}, {'policy_sha': None}, {'policy_sha': ''},
                       {'workflow_sha': None}, {'sha': 'short'}, {'ref': 'refs/heads/feature'},
                       {'ref': 'refs/tags/main'}, {'ref': 'main'}, {'ref': None},
                       {'ref_name': 'feature'}, {'ref_name': None}, {'branch': 'feature'}, {'branch': None}]:
            with self.subTest(change=change), self.assertRaises(m.CandidateError):
                m.policy_witness(self.witness(**change), 'main')
        for default in ['feature', '', None, '-x']:
            with self.subTest(default=default), self.assertRaises(m.CandidateError):
                m.policy_witness(self.witness(), default)
        for malformed in [None, [], 'x']:
            with self.assertRaises(m.CandidateError):
                m.policy_witness(malformed, 'main')

    def test_validator_must_be_the_staged_copy_outside_the_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = root / 'runner-temp' / 'candidate-policy' / 'merge_candidate.py'
            staged.parent.mkdir(parents=True)
            staged.write_text('# policy copy\n')
            workspace = root / 'workspace'
            (workspace / 'scripts' / 'ci').mkdir(parents=True)
            shadow = workspace / 'scripts' / 'ci' / 'merge_candidate.py'
            shadow.write_text('# candidate copy\n')
            m.trusted_location(str(staged), str(staged), str(workspace))
            m.trusted_location(str(staged), str(staged), str(workspace) + os.sep)
            cases = [(str(shadow), str(shadow), str(workspace)),  # running the candidate copy
                     (str(shadow), str(staged), str(workspace)),  # claims the staged path, runs another
                     (str(staged), str(shadow), str(workspace)),
                     (str(staged), str(staged), str(root)),  # workspace contains the staged copy
                     (str(staged), '', str(workspace)), (str(staged), str(staged), ''),
                     (str(staged), str(root / 'missing.py'), str(workspace)),
                     (str(root / 'missing.py'), str(root / 'missing.py'), str(workspace))]
            for script, expected, ws in cases:
                with self.subTest(script=script, expected=expected, workspace=ws), \
                        self.assertRaises(m.CandidateError):
                    m.trusted_location(script, expected, ws)
            link = root / 'runner-temp' / 'link.py'
            try:
                os.symlink(staged, link)
            except (OSError, NotImplementedError):
                self.skipTest('symlinks unavailable on this host')
            with self.assertRaises(m.CandidateError):
                m.trusted_location(str(link), str(link), str(workspace))
            try:
                os.symlink(staged.parent, workspace / 'shadow-dir')
            except (OSError, NotImplementedError):
                self.skipTest('symlinks unavailable on this host')
            with self.assertRaises(m.CandidateError):
                m.trusted_location(str(workspace / 'shadow-dir' / 'merge_candidate.py'),
                                   str(workspace / 'shadow-dir' / 'merge_candidate.py'), str(workspace))


class RunTests(unittest.TestCase):
    def test_every_full_python_and_rust_cell_is_required(self):
        for kind in ('ci', 'rust'):
            run, jobs = evidence(kind)
            self.assertTrue(m.full_run_success(run, jobs, candidate(), kind, 11, N, POLICY))
            for i in range(len(jobs)):
                with self.subTest(kind=kind, missing=jobs[i]['name']):
                    self.assertFalse(m.full_run_success(run, jobs[:i]+jobs[i+1:], candidate(), kind, 11, N, POLICY))

    def test_fail_cancel_skip_pending_cannot_pass_even_if_run_is_green(self):
        for status, conclusion in [('completed', 'failure'), ('completed', 'cancelled'),
                                   ('completed', 'skipped'), ('in_progress', None)]:
            run, jobs = evidence()
            jobs[0].update(status=status, conclusion=conclusion)
            self.assertFalse(m.full_run_success(run, jobs, candidate(), 'ci', 11, N, POLICY))

    def test_wrong_run_workflow_branch_candidate_or_nonce_cannot_pass(self):
        run, jobs = evidence()
        for field, value in [('head_sha', X), ('head_sha', H), ('head_sha', M), ('head_branch', 'other'),
                             ('head_branch', 'feature'), ('workflow_id', 12),
                             ('event', 'pull_request'), ('path', '.github/workflows/other.yml'),
                             ('display_title', f'candidate:{candidate().key}:ci'),
                             ('status', 'in_progress'), ('conclusion', 'cancelled'),
                             ('repository', {'id': 20, 'full_name': REPO}),
                             ('head_repository', {'id': 20, 'full_name': REPO})]:
            with self.subTest(field=field, value=value):
                self.assertFalse(m.full_run_success({**run, field: value}, jobs, candidate(), 'ci', 11, N, POLICY))
        self.assertFalse(m.full_run_success(run, jobs, candidate(), 'ci', 11, '0' * 32, POLICY))

    def test_candidate_head_run_never_qualifies_even_with_matching_title_and_jobs(self):
        # A run whose head is the candidate head executed candidate YAML and a
        # candidate validator. The old evidence shape is rejected outright.
        run, jobs = evidence()
        legacy_run = {**run, 'head_sha': H, 'head_branch': 'feature'}
        legacy_jobs = [{**job, 'head_sha': H} for job in jobs]
        self.assertFalse(m.full_run_success(legacy_run, jobs, candidate(), 'ci', 11, N, POLICY))
        self.assertFalse(m.full_run_success(legacy_run, legacy_jobs, candidate(), 'ci', 11, N, POLICY))
        with self.assertRaises(m.CandidateError):
            m.full_run_success(run, legacy_jobs, candidate(), 'ci', 11, N, POLICY)

    def test_policy_argument_is_required_and_bound_to_the_candidate(self):
        run, jobs = evidence()
        for policy in [None, 'main', {'sha': B, 'branch': 'main'}, m.Policy(H, 'main'),
                       m.Policy(M, 'main'), m.Policy(X, 'main'), m.Policy(B, 'feature')]:
            with self.subTest(policy=policy), self.assertRaises(m.CandidateError):
                m.full_run_success(run, jobs, candidate(), 'ci', 11, N, policy)
        # A valid but different policy commit simply does not match this run.
        self.assertFalse(m.full_run_success(run, jobs, candidate(), 'ci', 11, N, m.Policy(X, 'trunk')))
        self.assertFalse(m.full_run_success(run, jobs, candidate(), 'ci', 11, N, m.Policy(B, 'trunk')))

    def test_ref_qualified_workflow_path_binds_only_the_same_workflow_file(self):
        run, jobs = evidence()
        for suffix in ['@main', '@feature', '@refs/heads/feature', '@refs/pull/42/merge', '@' + H,
                       '@release-1.2_x']:
            with self.subTest(suffix=suffix):
                qualified = {**run, 'path': '.github/workflows/ci.yml' + suffix}
                self.assertTrue(m.workflow_path_matches(qualified['path'], 'ci'))
                self.assertTrue(m.full_run_success(qualified, jobs, candidate(), 'ci', 11, N, POLICY))
                # A tolerated suffix never substitutes for any other binding.
                for field, value in [('workflow_id', 12), ('head_sha', X), ('head_sha', H),
                                     ('head_branch', 'other'), ('head_branch', 'feature'),
                                     ('event', 'pull_request'), ('status', 'in_progress'),
                                     ('repository', {'id': 20, 'full_name': REPO}),
                                     ('head_repository', {'id': 10, 'full_name': 'fork/repo'}),
                                     ('display_title', f'candidate:{candidate().key}:ci:{"0" * 32}')]:
                    self.assertFalse(m.full_run_success({**qualified, field: value}, jobs, candidate(), 'ci', 11, N, POLICY))
                self.assertFalse(m.full_run_success(qualified, jobs, candidate(), 'ci', 11, '0' * 32, POLICY))
                self.assertFalse(m.full_run_success(qualified, jobs, candidate(), 'ci', 12, N, POLICY))
                self.assertFalse(m.full_run_success(qualified, jobs[1:], candidate(), 'ci', 11, N, POLICY))
        self.assertTrue(m.workflow_path_matches('.github/workflows/rust.yml@main', 'rust'))
        for path in ['.github/workflows/rust.yml', '.github/workflows/rust.yml@main',
                     '.github/workflows/ci.yml@', '.github/workflows/ci.yml@main extra',
                     '.github/workflows/ci.yml @main', '.github/workflows/ci.yml@main\n',
                     '.github/workflows/ci.yml.bak@main', '.github/workflows/ci.yaml@main',
                     'x.github/workflows/ci.yml@main', '@.github/workflows/ci.yml',
                     '.github/workflows/ci.yml@main@.github/workflows/ci.yml',
                     '.github/workflows/ci.yml@refs/heads/{x}', '.github/workflows/ci.yml@a~1',
                     '.github/workflows/ci.yml#main', '.github/workflows/ci.yml@refs/heads/x@y',
                     '.GITHUB/WORKFLOWS/CI.YML@main', '', None, 7, ['.github/workflows/ci.yml']]:
            with self.subTest(path=path):
                self.assertFalse(m.workflow_path_matches(path, 'ci'))
                self.assertFalse(m.full_run_success({**run, 'path': path}, jobs, candidate(), 'ci', 11, N, POLICY))
        self.assertFalse(m.workflow_path_matches('.github/workflows/ci.yml', 'other'))
        self.assertFalse(m.workflow_path_matches('.github/workflows/other.yml', 'other'))

    def test_rerun_cannot_borrow_prior_attempt_cells(self):
        run, jobs = evidence()
        run['run_attempt'] = 2
        with self.assertRaises(m.CandidateError):
            m.full_run_success(run, jobs, candidate(), 'ci', 11, N, POLICY)
        for job in jobs:
            job['run_attempt'] = 2
        self.assertTrue(m.full_run_success(run, jobs, candidate(), 'ci', 11, N, POLICY))

    def test_duplicate_job_names_or_foreign_run_are_refused(self):
        run, jobs = evidence()
        with self.assertRaises(m.CandidateError):
            m.full_run_success(run, jobs+[jobs[0]], candidate(), 'ci', 11, N, POLICY)
        for field, value in [('run_id', 101), ('head_sha', X), ('head_sha', H), ('run_attempt', True)]:
            changed = copy.deepcopy(jobs)
            changed[0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(m.CandidateError):
                m.full_run_success(run, changed, candidate(), 'ci', 11, N, POLICY)


class CLITests(unittest.TestCase):
    def env(self):
        # A default-branch dispatch: GitHub SHA, workflow SHA and the staged
        # policy witness all name the policy commit; the candidate is in inputs.
        return {'GITHUB_REPOSITORY': REPO, 'GITHUB_SHA': B, 'GITHUB_WORKFLOW_SHA': B,
                'GITHUB_REF': 'refs/heads/main', 'GITHUB_REF_NAME': 'main',
                'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_WORKSPACE': str(ROOT / 'app'),
                'CANDIDATE_POLICY_SHA': B, 'CANDIDATE_POLICY_BRANCH': 'main',
                'CANDIDATE_POLICY_VALIDATOR': m.__file__,
                'CANDIDATE_SCOPE': 'full', 'DISPATCH_NONCE': N,
                **{key.upper(): value for key, value in candidate().fields().items()}}

    def run_main(self, env, api=None, controls=None):
        api = api or API()
        # Control-file closure has its own executable Git tests (ControlTests);
        # here it is observed, not re-run against this repository's files.
        with patch.dict(os.environ, env, clear=True), patch.object(sys, 'argv', ['helper', 'validate-dispatch']), \
                patch.object(m, 'api', api), patch.object(m, 'verify_controls', controls or MagicMock()) as verify, \
                patch.object(m.subprocess, 'check_output', side_effect=[M, f'{B} {H}', T]) as git:
            m.main()
        return api, git, verify

    def test_cli_uses_readonly_api_and_git_before_live_revalidation(self):
        api, git, verify = self.run_main(self.env())
        self.assertEqual(git.call_count, 3)
        self.assertTrue(all(call.args[0][0] == 'git' for call in git.call_args_list))
        self.assertEqual(api.calls.count(f'repos/{REPO}/pulls/42'), 2)
        self.assertEqual(api.calls[-1], f'repos/{REPO}')
        # Controls are verified against the stage holding the running validator,
        # bound to the recorded policy SHA, before any API or candidate Git read.
        verify.assert_called_once_with(str(ROOT / 'app'), os.path.dirname(os.path.abspath(m.__file__)), B)

    def test_cli_refuses_before_api_when_controls_differ(self):
        def refuse(workspace, stage, policy_sha):
            raise m.CandidateError('Candidate modifies orchestration control file')
        api = API()
        with self.assertRaises(m.CandidateError):
            self.run_main(self.env(), api=api, controls=refuse)
        self.assertEqual(api.calls, [])

    def test_partial_or_nonfull_candidate_is_refused(self):
        for field, value in [('CANDIDATE_SCOPE', 'linux'), ('CANDIDATE_SCOPE', ''), ('DISPATCH_NONCE', ''),
                             ('DISPATCH_NONCE', '0' * 31), ('GITHUB_EVENT_NAME', 'pull_request'),
                             ('PR_NUMBER', '42/path'), ('PR_NUMBER', ''), ('PR_HEAD_SHA', ''),
                             ('PR_BASE_SHA', ''), ('PR_BASE_SHA', X), ('MERGE_SHA', ''),
                             ('MERGE_TREE_SHA', ''), ('CANDIDATE_ID', ''), ('CANDIDATE_ID', '0' * 64)]:
            with self.subTest(field=field, value=value), self.assertRaises(m.CandidateError):
                self.run_main({**self.env(), field: value})

    def test_policy_witness_disagreement_or_candidate_head_dispatch_is_refused(self):
        for field, value in [('GITHUB_SHA', H), ('GITHUB_WORKFLOW_SHA', H), ('CANDIDATE_POLICY_SHA', H),
                             ('GITHUB_SHA', X), ('GITHUB_WORKFLOW_SHA', X), ('CANDIDATE_POLICY_SHA', X),
                             ('GITHUB_SHA', ''), ('GITHUB_WORKFLOW_SHA', ''), ('CANDIDATE_POLICY_SHA', ''),
                             ('GITHUB_REF', 'refs/heads/feature'), ('GITHUB_REF', 'refs/pull/42/merge'),
                             ('GITHUB_REF', 'main'), ('GITHUB_REF', ''), ('GITHUB_REF_NAME', 'feature'),
                             ('CANDIDATE_POLICY_BRANCH', 'feature'), ('CANDIDATE_POLICY_BRANCH', '')]:
            with self.subTest(field=field, value=value), self.assertRaises(m.CandidateError):
                self.run_main({**self.env(), field: value})
        # A consistent witness that names the candidate head is a candidate-head
        # dispatch running candidate YAML: refused even though all SHAs agree.
        with self.assertRaises(m.CandidateError):
            self.run_main({**self.env(), 'GITHUB_SHA': H, 'GITHUB_WORKFLOW_SHA': H, 'CANDIDATE_POLICY_SHA': H,
                           'GITHUB_REF': 'refs/heads/feature', 'GITHUB_REF_NAME': 'feature',
                           'CANDIDATE_POLICY_BRANCH': 'feature'})

    def test_validator_location_and_live_default_branch_are_bound(self):
        for field, value in [('GITHUB_WORKSPACE', str(ROOT)), ('GITHUB_WORKSPACE', ''),
                             ('CANDIDATE_POLICY_VALIDATOR', ''),
                             ('CANDIDATE_POLICY_VALIDATOR', str(ROOT / 'tools' / 'test_merge_candidate.py'))]:
            with self.subTest(field=field, value=value), self.assertRaises(m.CandidateError):
                self.run_main({**self.env(), field: value})
        for repo_data in [{'id': 10, 'full_name': REPO, 'default_branch': 'feature'},
                          {'id': 10, 'full_name': REPO, 'default_branch': 'trunk'},
                          {'id': 20, 'full_name': REPO, 'default_branch': 'main'},
                          {'id': 10, 'full_name': 'other/repo', 'default_branch': 'main'},
                          {'id': 10, 'full_name': REPO}, {}, None]:
            api = API()

            def get(path, api=api, repo_data=repo_data):
                return copy.deepcopy(repo_data) if path == f'repos/{REPO}' else api(path)
            with self.subTest(repo=repo_data), self.assertRaises(m.CandidateError):
                self.run_main(self.env(), api=get)

        def unavailable(path):
            raise OSError('API unavailable')
        with self.assertRaises(OSError):
            self.run_main(self.env(), api=unavailable)


if __name__ == '__main__':
    unittest.main()

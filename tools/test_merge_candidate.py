"""Offline candidate identity/review/full-CI regressions; no external mutations."""
import copy
import importlib
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
m = importlib.import_module('scripts.ci.merge_candidate')

REPO = 'owner/repo'
H, B, M, T, X = (c * 40 for c in 'abcde')
N = 'f' * 32


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


def request(c=None):
    return {'id': 5, 'candidate': (c or candidate()).key, 'nonce': N,
            'created_at': '2026-10-16T23:00:00Z'}


def review(identifier=6, c=None):
    c = c or candidate()
    return {'id': identifier, 'commit_id': H, 'state': 'COMMENTED',
            'submitted_at': '2026-10-16T23:00:01Z',
            'user': {'id': m.CODEX_ID, 'login': m.CODEX_LOGIN, 'type': 'Bot'},
            'body': f'CODEX-CANDIDATE: {c.key}\nCODEX-REQUEST: {N}\nCODEX-GATE: READY'}


def evidence(kind='ci'):
    c = candidate()
    run = {'id': 100, 'workflow_id': 11, 'repository': {'id': 10, 'full_name': REPO},
           'head_repository': {'id': 10, 'full_name': REPO},
           'event': 'workflow_dispatch', 'path': f'.github/workflows/{kind}.yml',
           'display_title': f'candidate:{c.key}:{kind}:{N}', 'head_sha': H,
           'head_branch': 'feature', 'run_attempt': 1, 'status': 'completed', 'conclusion': 'success'}
    jobs = [{'id': i, 'run_id': 100, 'run_attempt': 1, 'head_sha': H, 'name': name,
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
        m.validate_checkout(c, c.fields(), H, checkout)
        for field in c.fields():
            values = c.fields()
            values[field] = 'wrong'
            with self.subTest(field=field), self.assertRaises(m.CandidateError):
                m.validate_checkout(c, values, H, checkout)
        for change in [{'sha': H}, {'parents': [H, B]}, {'tree': X}]:
            with self.assertRaises(m.CandidateError):
                m.validate_checkout(c, c.fields(), H, {**checkout, **change})
        with self.assertRaises(m.CandidateError):
            m.validate_checkout(c, c.fields(), X, checkout)


class ReviewTests(unittest.TestCase):
    def test_exact_codex_ready_is_accepted(self):
        self.assertEqual(m.ready_review([review()], candidate(), request()), review())

    def test_mediate_has_same_candidate_binding_but_cannot_qualify_ci(self):
        r = review()
        r['body'] = r['body'].replace('CODEX-GATE: READY', 'CODEX-GATE: MEDIATE')
        self.assertEqual(m.candidate_verdict([r], candidate(), request()), 'MEDIATE')
        self.assertEqual(m.candidate_review([r], candidate(), request()), r)
        self.assertIsNone(m.ready_review([r], candidate(), request()))
        self.assertEqual(m.candidate_verdict([review()], candidate(), request()), 'READY')
        r['body'] = r['body'].replace(N, '0' * 32)
        self.assertEqual(m.candidate_verdict([r], candidate(), request()), '')

    def test_forged_bot_identity_is_not_a_verdict(self):
        for field, value in [('id', 1), ('login', 'lookalike[bot]'), ('type', 'User')]:
            r = review()
            r['user'][field] = value
            self.assertIsNone(m.ready_review([r], candidate(), request()))

    def test_head_base_candidate_or_nonce_replay_is_rejected(self):
        for field, value in [('commit_id', X),
                             ('body', review()['body'].replace(N, '0' * 32)),
                             ('body', review()['body'].replace(candidate().key, '0' * 64))]:
            r = {**review(), field: value}
            self.assertIsNone(m.ready_review([r], candidate(), request()))
        changed = replace(candidate(), base=X)
        self.assertIsNone(m.ready_review([review()], changed, request(changed)))

    def test_same_second_or_older_review_cannot_acknowledge_new_request(self):
        for stamp in ('2026-10-16T23:00:00Z', '2026-10-16T22:59:59Z'):
            self.assertIsNone(m.ready_review([{**review(), 'submitted_at': stamp}], candidate(), request()))

    def test_latest_negative_pending_dismissed_or_missing_verdict_wins(self):
        for change in [{'state': 'DISMISSED'}, {'state': 'PENDING'},
                       {'body': 'Findings remain'}, {'body': 'CODEX-GATE: MEDIATE'}]:
            r = {**review(7), 'submitted_at': '2026-10-16T23:00:02Z', **change}
            self.assertIsNone(m.ready_review([r, review()], candidate(), request()))

    def test_submitted_time_handles_old_draft_review_id(self):
        later = {**review(4), 'submitted_at': '2026-10-16T23:00:02Z', 'body': 'Findings'}
        self.assertIsNone(m.ready_review([later, review()], candidate(), request()))

    def test_same_second_distinct_reviews_are_ambiguous(self):
        self.assertIsNone(m.ready_review([review(), review(7)], candidate(), request()))

    def test_duplicate_identical_records_coalesce_conflicting_records_refuse(self):
        self.assertIsNotNone(m.ready_review([review(), review()], candidate(), request()))
        with self.assertRaises(m.CandidateError):
            m.ready_review([review(), {**review(), 'body': 'Findings'}], candidate(), request())

    def test_echoes_and_final_unique_verdict_are_required(self):
        body = review()['body']
        for invalid in [body.replace('CODEX-REQUEST:', '> CODEX-REQUEST:'),
                        body + '\nMore text', body + '\nCODEX-GATE: READY',
                        body.replace('CODEX-CANDIDATE:', 'Candidate:'),
                        f'CODEX-REQUEST: {N}\n' + body,
                        '```\n' + '\n'.join(body.splitlines()[:2]) + '\n```\nCODEX-GATE: READY']:
            self.assertIsNone(m.ready_review([{**review(), 'body': invalid}], candidate(), request()))

    def test_inline_backtick_span_does_not_open_a_fence(self):
        for prefix in ['```foo```', '````foo````', '   ```foo```']:
            with self.subTest(prefix=prefix):
                r = {**review(), 'body': prefix + '\n' + review()['body']}
                self.assertEqual(m.ready_review([r], candidate(), request()), r)

    def test_shorter_or_different_fence_cannot_promote_quoted_markers(self):
        for opener, closer in [('````', '```'), ('~~~~', '~~~'), ('````', '~~~~'),
                               ('~~~~', '````'), ('````', '```` trailing text')]:
            with self.subTest(opener=opener, closer=closer):
                body = opener + '\nquoted example\n' + closer + '\n' + review()['body']
                self.assertIsNone(m.ready_review([{**review(), 'body': body}], candidate(), request()))

    def test_matching_or_longer_fence_closes_before_plain_verdict(self):
        for opener, closer in [('````python', '````'), ('````', '`````'),
                               ('~~~~text', '~~~~'), ('~~~~', '~~~~~'),
                               ('   ````python', '   ```` \t')]:
            with self.subTest(opener=opener, closer=closer):
                # The fenced duplicate must not enter the marker inventory.
                body = opener + '\n' + review()['body'] + '\n' + closer + '\n' + review()['body']
                r = {**review(), 'body': body}
                self.assertEqual(m.ready_review([r], candidate(), request()), r)

    def test_indented_code_markers_do_not_authorize_a_verdict(self):
        for indent in ['    ', '\t', ' \t', '   \t']:
            for marker_index in range(3):
                with self.subTest(indent=indent, marker_index=marker_index):
                    lines = review()['body'].splitlines()
                    lines[marker_index] = indent + lines[marker_index]
                    r = {**review(), 'body': '\n'.join(lines)}
                    self.assertIsNone(m.ready_review([r], candidate(), request()))

    def test_plain_markers_allow_up_to_three_spaces(self):
        for count in range(4):
            r = {**review(), 'body': '\n'.join(' ' * count + line for line in review()['body'].splitlines())}
            self.assertEqual(m.ready_review([r], candidate(), request()), r)

    def test_indented_fence_cannot_close_an_open_fence(self):
        for indent in ['    ', '\t']:
            body = '````\nquoted example\n' + indent + '````\n' + review()['body']
            self.assertIsNone(m.ready_review([{**review(), 'body': body}], candidate(), request()))

    def test_malformed_request_and_review_timestamp_refuse(self):
        for change in [{'id': True}, {'candidate': 'wrong'}, {'nonce': 'wrong'},
                       {'created_at': '2026-10-16T23:00:00'}]:
            with self.assertRaises(m.CandidateError):
                m.ready_review([review()], candidate(), {**request(), **change})
        with self.assertRaises(m.CandidateError):
            m.ready_review([{**review(), 'submitted_at': None}], candidate(), request())


class RunTests(unittest.TestCase):
    def test_every_full_python_and_rust_cell_is_required(self):
        for kind in ('ci', 'rust'):
            run, jobs = evidence(kind)
            self.assertTrue(m.full_run_success(run, jobs, candidate(), kind, 11, N))
            for i in range(len(jobs)):
                with self.subTest(kind=kind, missing=jobs[i]['name']):
                    self.assertFalse(m.full_run_success(run, jobs[:i]+jobs[i+1:], candidate(), kind, 11, N))

    def test_fail_cancel_skip_pending_cannot_pass_even_if_run_is_green(self):
        for status, conclusion in [('completed', 'failure'), ('completed', 'cancelled'),
                                   ('completed', 'skipped'), ('in_progress', None)]:
            run, jobs = evidence()
            jobs[0].update(status=status, conclusion=conclusion)
            self.assertFalse(m.full_run_success(run, jobs, candidate(), 'ci', 11, N))

    def test_wrong_run_workflow_branch_candidate_or_nonce_cannot_pass(self):
        run, jobs = evidence()
        for field, value in [('head_sha', X), ('head_branch', 'other'), ('workflow_id', 12),
                             ('event', 'pull_request'), ('path', '.github/workflows/other.yml'),
                             ('display_title', f'candidate:{candidate().key}:ci'),
                             ('status', 'in_progress'), ('conclusion', 'cancelled'),
                             ('repository', {'id': 20, 'full_name': REPO}),
                             ('head_repository', {'id': 20, 'full_name': REPO})]:
            with self.subTest(field=field):
                self.assertFalse(m.full_run_success({**run, field: value}, jobs, candidate(), 'ci', 11, N))
        self.assertFalse(m.full_run_success(run, jobs, candidate(), 'ci', 11, '0' * 32))

    def test_rerun_cannot_borrow_prior_attempt_cells(self):
        run, jobs = evidence()
        run['run_attempt'] = 2
        with self.assertRaises(m.CandidateError):
            m.full_run_success(run, jobs, candidate(), 'ci', 11, N)
        for job in jobs:
            job['run_attempt'] = 2
        self.assertTrue(m.full_run_success(run, jobs, candidate(), 'ci', 11, N))

    def test_duplicate_job_names_or_foreign_run_are_refused(self):
        run, jobs = evidence()
        with self.assertRaises(m.CandidateError):
            m.full_run_success(run, jobs+[jobs[0]], candidate(), 'ci', 11, N)
        for field, value in [('run_id', 101), ('head_sha', X), ('run_attempt', True)]:
            changed = copy.deepcopy(jobs)
            changed[0][field] = value
            with self.subTest(field=field), self.assertRaises(m.CandidateError):
                m.full_run_success(run, changed, candidate(), 'ci', 11, N)


class CLITests(unittest.TestCase):
    def env(self):
        return {'GITHUB_REPOSITORY': REPO, 'GITHUB_SHA': H, 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                'CANDIDATE_SCOPE': 'full', 'DISPATCH_NONCE': N,
                **{key.upper(): value for key, value in candidate().fields().items()}}

    def test_cli_uses_readonly_api_and_git_before_live_revalidation(self):
        api = API()
        with patch.dict(os.environ, self.env(), clear=True), patch.object(sys, 'argv', ['helper', 'validate-dispatch']), \
                patch.object(m, 'api', api), patch.object(m.subprocess, 'check_output', side_effect=[M, f'{B} {H}', T]) as git:
            m.main()
        self.assertEqual(git.call_count, 3)
        self.assertTrue(all(call.args[0][0] == 'git' for call in git.call_args_list))
        self.assertEqual(api.calls[-1], f'repos/{REPO}/pulls/42')

    def test_partial_or_nonfull_candidate_is_refused(self):
        for field, value in [('CANDIDATE_SCOPE', 'linux'), ('DISPATCH_NONCE', ''),
                             ('GITHUB_EVENT_NAME', 'pull_request'), ('PR_NUMBER', '42/path')]:
            with patch.dict(os.environ, {**self.env(), field: value}, clear=True), \
                    patch.object(sys, 'argv', ['helper', 'validate-dispatch']), \
                    patch.object(m.subprocess, 'check_output', side_effect=[M, f'{B} {H}', T]), \
                    self.subTest(field=field), self.assertRaises(m.CandidateError):
                m.main()


if __name__ == '__main__':
    unittest.main()

"""Exact candidate identity and evidence checks; all network operations are read-only.

Coordinator owns request/dispatch authentication, holds, writer exclusion and retry
reservations. Neither labels nor this helper's process exit constitutes readiness.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime

CODEX_ID = 199175422  # Verified from genuine Cockpit #147 reviews on 2026-10-16.
CODEX_LOGIN = 'chatgpt-codex-connector[bot]'
SHA = re.compile(r'[0-9a-f]{40}')
REPO = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
NONCE = re.compile(r'[0-9a-f]{32}')
REQUIRED_JOBS = {
    'ci': frozenset({'filter', 'linux (3.11)', 'linux (3.12)', 'windows (3.11)',
                     'windows (3.12)', 'macos', 'linux-gates', 'queue-gates', 'candidate-ci-gates'}),
    'rust': frozenset({'filter', 'rust-gates', 'rust-book (linux / py3.11)',
                       'rust-book (linux / py3.12)', 'rust-book (windows / py3.11)',
                       'rust-book (windows / py3.12)', 'rust-book (macos / py3.12)'}),
}
# Actions run metadata may qualify the workflow file with "@<ref or sha>", e.g.
# ".github/workflows/ci.yml@main" or "@refs/heads/<branch>". Only ref/SHA-safe
# characters are admitted after "@"; the file name itself must match exactly.
WORKFLOW_PATH_SUFFIX = r'(?:@[A-Za-z0-9._/-]+)?'


def workflow_path_matches(path, kind):
    """True when path names exactly this kind's workflow file, bare or @ref-qualified.

    The suffix is tolerated, never trusted: numeric workflow ID, repository ID,
    exact head/branch, actor, nonce, attempt and job completeness stay bound by
    the callers. A split on "@" would admit a different file; this does not.
    """
    if kind not in REQUIRED_JOBS or not isinstance(path, str):
        return False
    pattern = re.escape(f'.github/workflows/{kind}.yml') + WORKFLOW_PATH_SUFFIX
    return re.fullmatch(pattern, path) is not None


class CandidateError(ValueError):
    """Incomplete, stale or ambiguous evidence; no readiness assertion is allowed."""


def positive(value):
    return type(value) is int and value > 0


def sha(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def safe_ref(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9._/-]{0,199}', value):
        return False
    return '..' not in value and all(part and not part.startswith(('.', '-'))
                                    and not part.endswith(('.', '.lock')) for part in value.split('/'))


@dataclass(frozen=True)
class Candidate:
    repository: str
    repository_id: int
    number: int
    pr_id: int
    head_ref: str
    base_ref: str
    head: str
    base: str
    merge: str
    tree: str

    def record(self):
        return asdict(self)

    @property
    def key(self):
        payload = json.dumps(self.record(), sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(payload.encode()).hexdigest()

    def fields(self):
        return {'pr_number': str(self.number), 'pr_head_sha': self.head,
                'pr_base_sha': self.base, 'merge_sha': self.merge,
                'merge_tree_sha': self.tree, 'candidate_id': self.key}


def pr_identity(repository, number, pr):
    if not isinstance(pr, dict) or not REPO.fullmatch(repository):
        raise CandidateError('Malformed repository or PR')
    if (not positive(number) or not positive(pr.get('number')) or pr['number'] != number or not positive(pr.get('id'))
            or pr.get('state') != 'open' or pr.get('draft') is not False
            or pr.get('mergeable') is not True or not sha(pr.get('merge_commit_sha'))):
        raise CandidateError('PR is not an open, ready, mergeable candidate')
    sides = []
    for name in ('head', 'base'):
        side = pr.get(name)
        if not isinstance(side, dict) or not isinstance(side.get('repo'), dict):
            raise CandidateError('Missing candidate branch/repository identity')
        repo = side['repo']
        if (repo.get('full_name') != repository or not positive(repo.get('id'))
                or not sha(side.get('sha')) or not safe_ref(side.get('ref'))
                or (name == 'head' and side['ref'] in ('main', 'master', 'HEAD'))):
            raise CandidateError('Invalid same-repository candidate identity')
        sides.append((repo['id'], side['ref'], side['sha']))
    if sides[0][0] != sides[1][0]:
        raise CandidateError('Candidate repository IDs differ')
    return {'repository': repository, 'repository_id': sides[0][0], 'number': number,
            'pr_id': pr['id'], 'head_ref': sides[0][1], 'base_ref': sides[1][1],
            'head': sides[0][2], 'base': sides[1][2], 'merge': pr['merge_commit_sha']}


def verify_branch_refs(get, identity):
    """Triangulate PR metadata with the actual named head and base refs."""
    for side in ('base', 'head'):
        name = identity[f'{side}_ref']
        value = get(f'repos/{identity["repository"]}/git/ref/heads/{name}')
        if (not isinstance(value, dict) or value.get('ref') != f'refs/heads/{name}'
                or not isinstance(value.get('object'), dict)
                or value['object'].get('type') != 'commit'
                or value['object'].get('sha') != identity[side]):
            raise CandidateError(f'Live {side} ref does not match candidate PR metadata')


def resolve_candidate(get, repository, number):
    """Bind PR, live branch refs and merge tree; reread refs and PR before return.

    These reads are not atomic. Later movement invalidates readiness at the
    coordinator's next reconciliation; no cached PR merge data alone qualifies.
    """
    if not isinstance(repository, str) or not REPO.fullmatch(repository) or not positive(number):
        raise CandidateError('Invalid repository/PR lookup')
    path = f'repos/{repository}/pulls/{number}'
    identity = pr_identity(repository, number, get(path))
    verify_branch_refs(get, identity)
    commit = get(f'repos/{repository}/git/commits/{identity["merge"]}')
    if (not isinstance(commit, dict) or commit.get('sha') != identity['merge']
            or not isinstance(commit.get('parents'), list)
            or not isinstance(commit.get('tree'), dict) or not sha(commit['tree'].get('sha'))):
        raise CandidateError('Malformed merge commit evidence')
    parents = commit['parents']
    if (len(parents) != 2 or any(not isinstance(p, dict) for p in parents)
            or [p.get('sha') for p in parents] != [identity['base'], identity['head']]):
        raise CandidateError('Merge parents must be exactly current base then head')
    verify_branch_refs(get, identity)
    if pr_identity(repository, number, get(path)) != identity:
        raise CandidateError('Candidate changed during resolution')
    return Candidate(**identity, tree=commit['tree']['sha'])


def timestamp(value):
    if not isinstance(value, str):
        raise CandidateError('Missing evidence timestamp')
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise CandidateError('Invalid evidence timestamp') from error
    if stamp.tzinfo is None:
        raise CandidateError('Ambiguous evidence timezone')
    return stamp


def _latest_head_review(reviews, head):
    """Select latest genuine review first, without falling back to an older verdict."""
    if not sha(head):
        raise CandidateError('Invalid review head')
    if not isinstance(reviews, list):
        raise CandidateError('Incomplete review listing')
    trusted = {}
    for review in reviews:
        if not isinstance(review, dict):
            raise CandidateError('Malformed review')
        user = review.get('user')
        if not isinstance(user, dict) or (user.get('id'), user.get('login'), user.get('type')) != (
                CODEX_ID, CODEX_LOGIN, 'Bot'):
            continue
        if review.get('commit_id') != head:
            continue
        identifier = review.get('id')
        if not positive(identifier):
            raise CandidateError('Invalid Codex review ID')
        timestamp(review.get('submitted_at'))
        if identifier in trusted and trusted[identifier] != review:
            raise CandidateError('Conflicting Codex review records')
        trusted[identifier] = review
    if not trusted:
        return None
    # Select latest first, never search backwards for a successful older verdict.
    latest = max(trusted.values(), key=lambda r: (timestamp(r['submitted_at']), r['id']))
    if sum(timestamp(r['submitted_at']) == timestamp(latest['submitted_at'])
           for r in trusted.values()) != 1:
        return None  # Same-second independent submissions have no proven causal order.
    return latest


def _terminal_verdict(body):
    """Return plain marker lines and an exact, unquoted terminal verdict.

    The verdict must be the unique plain CODEX-GATE line, and the final nonblank
    line of the body must itself be that admitted plain line. A final copy that
    is indented code, quoted, a fence delimiter, or inside an open or closed
    fence is not a verdict, and an earlier plain line cannot lend it that status.
    """
    if not isinstance(body, str):
        return [], ''
    machine, fence, terminal = [], None, None
    for raw in body.splitlines():
        if not raw.strip():
            continue  # Trailing blank lines are legitimate; they carry no verdict.
        terminal = None  # Reset per nonblank line; only an admitted plain line sets it.
        # At most three spaces may precede a Markdown fence or plain marker.
        # A tab/four spaces denotes code, never an independent review verdict.
        indent = raw[:len(raw) - len(raw.lstrip())]
        if len(indent) > 3 or any(char != ' ' for char in indent):
            continue
        line = raw.lstrip(' ')
        delimiter = re.match(r'(`{3,}|~{3,})(.*)$', line)
        if fence is not None:
            char, length = fence
            if re.fullmatch(re.escape(char) + '{' + str(length) + r',}[ \t]*', line):
                fence = None
            continue
        if delimiter and (delimiter[1][0] != '`' or '`' not in delimiter[2]):
            fence = (delimiter[1][0], len(delimiter[1]))
        elif not line.startswith('>'):
            terminal = line.strip()
            machine.append(terminal)
    verdicts = [line for line in machine if line.startswith('CODEX-GATE:')]
    if (len(verdicts) != 1 or verdicts[0] not in ('CODEX-GATE: READY', 'CODEX-GATE: MEDIATE')
            or terminal != verdicts[0]):
        return machine, ''
    return machine, verdicts[0].split(': ', 1)[1]


def _candidate_review(reviews, candidate, request):
    """Return latest genuine candidate verdict; request provenance is caller-owned.

    Request is an authenticated coordinator record with id, candidate, nonce and
    created_at. A new nonce is required after invalidation/retry. Same-second
    request/review timestamps are ambiguous and cannot establish causality.
    """
    if (not isinstance(request, dict) or not positive(request.get('id'))
            or request.get('candidate') != candidate.key
            or not isinstance(request.get('nonce'), str) or not NONCE.fullmatch(request['nonce'])):
        raise CandidateError('Invalid candidate review request')
    requested_at = timestamp(request.get('created_at'))
    latest = _latest_head_review(reviews, candidate.head)
    if (latest is None or latest.get('state') not in ('COMMENTED', 'APPROVED')
            or timestamp(latest['submitted_at']) <= requested_at):
        return None
    machine, verdict = _terminal_verdict(latest.get('body'))
    expected = [f'CODEX-CANDIDATE: {candidate.key}', f'CODEX-REQUEST: {request["nonce"]}']
    if not verdict or any([line for line in machine if line.startswith(prefix)] != [wanted]
           for prefix, wanted in zip(('CODEX-CANDIDATE:', 'CODEX-REQUEST:'), expected, strict=True)):
        return None
    return latest


def head_mediate_review(reviews, head):
    """Validate a current-head human handoff, never READY or CI authorization.

    MEDIATE does not require a mergeable synthetic candidate or a request nonce.
    The caller verifies live PR identity, owner holds, pause generation, review
    edit provenance and side-effect reservations before acting.
    """
    latest = _latest_head_review(reviews, head)
    if (latest is None or latest.get('state') not in ('COMMENTED', 'APPROVED', 'CHANGES_REQUESTED')
            or _terminal_verdict(latest.get('body'))[1] != 'MEDIATE'):
        return None
    return latest


def candidate_review(reviews, candidate, request):
    """Return the exact validated READY/MEDIATE review for caller freshness checks."""
    return _candidate_review(reviews, candidate, request)


def candidate_verdict(reviews, candidate, request):
    """Authenticated candidate verdict string READY/MEDIATE, otherwise empty."""
    review = _candidate_review(reviews, candidate, request)
    return _terminal_verdict(review['body'])[1] if review else ''


def ready_review(reviews, candidate, request):
    """Return the exact current READY review; MEDIATE cannot qualify CI."""
    review = _candidate_review(reviews, candidate, request)
    return review if review and _terminal_verdict(review['body'])[1] == 'READY' else None


def full_run_success(run, jobs, candidate, kind, workflow_id, dispatch_nonce):
    """Validate one coordinator-owned latest run/attempt with complete job pages.

    Caller must authenticate dispatch ownership and select/revalidate the latest
    owned run/attempt. A run name is correlation data, not dispatch authority.
    """
    if (kind not in REQUIRED_JOBS or not positive(workflow_id)
            or not isinstance(dispatch_nonce, str) or not NONCE.fullmatch(dispatch_nonce)):
        raise CandidateError('Unknown candidate workflow')
    if not isinstance(run, dict) or not isinstance(jobs, list):
        raise CandidateError('Incomplete run/job evidence')
    repo = run.get('repository')
    head_repo = run.get('head_repository')
    if (not isinstance(head_repo, dict) or head_repo.get('id') != candidate.repository_id
            or head_repo.get('full_name') != candidate.repository
            or not isinstance(repo, dict) or repo.get('id') != candidate.repository_id
            or repo.get('full_name') != candidate.repository
            or run.get('event') != 'workflow_dispatch' or run.get('workflow_id') != workflow_id
            or not workflow_path_matches(run.get('path'), kind)
            or run.get('display_title') != f'candidate:{candidate.key}:{kind}:{dispatch_nonce}'
            or run.get('head_sha') != candidate.head or run.get('head_branch') != candidate.head_ref
            or not positive(run.get('id')) or not positive(run.get('run_attempt'))
            or run.get('status') != 'completed' or run.get('conclusion') != 'success'):
        return False
    found = {}
    for job in jobs:
        if (not isinstance(job, dict) or not positive(job.get('id'))
                or not positive(job.get('run_id')) or not positive(job.get('run_attempt'))
                or job.get('run_id') != run['id'] or job.get('run_attempt') != run['run_attempt']
                or job.get('head_sha') != candidate.head):
            raise CandidateError('Job does not bind to this run/attempt')
        name = job.get('name')
        if not isinstance(name, str) or name in found:
            raise CandidateError('Ambiguous job identity')
        found[name] = job
    return all(name in found and found[name].get('status') == 'completed'
               and found[name].get('conclusion') == 'success' for name in REQUIRED_JOBS[kind])


def validate_checkout(candidate, fields, workflow_sha, checkout):
    """Pure validation of supplied inputs and checked-out git object identity."""
    if fields != candidate.fields() or workflow_sha != candidate.head:
        raise CandidateError('Dispatch tuple does not match the live candidate')
    if checkout != {'sha': candidate.merge, 'parents': [candidate.base, candidate.head],
                    'tree': candidate.tree}:
        raise CandidateError('Checkout is not the exact candidate merge tree')


def api(path):
    result = subprocess.run(['gh', 'api', path], check=True, capture_output=True,
                            text=True, timeout=30)
    return json.loads(result.stdout)


def main():
    if sys.argv[1:] != ['validate-dispatch']:
        raise CandidateError('Expected validate-dispatch command')
    env = os.environ
    if env.get('CANDIDATE_SCOPE') != 'full' or env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch':
        raise CandidateError('Candidate validation requires a full dispatch')
    def git(*args):
        return subprocess.check_output(['git', *args], text=True, timeout=10).strip()
    checkout = {'sha': git('rev-parse', 'HEAD'), 'parents': git('show', '-s', '--format=%P', 'HEAD').split(),
                'tree': git('rev-parse', 'HEAD^{tree}')}
    if not NONCE.fullmatch(env.get('DISPATCH_NONCE', '')):
        raise CandidateError('Missing or invalid dispatch nonce')
    number = env.get('PR_NUMBER', '')
    if not re.fullmatch(r'[1-9][0-9]*', number):
        raise CandidateError('Invalid dispatched PR number')
    candidate = resolve_candidate(api, env['GITHUB_REPOSITORY'], int(number))
    fields = {key: env.get(key.upper(), '') for key in candidate.fields()}
    validate_checkout(candidate, fields, env.get('GITHUB_SHA'), checkout)
    print(f'Validated merge candidate {candidate.key}')


if __name__ == '__main__':
    main()

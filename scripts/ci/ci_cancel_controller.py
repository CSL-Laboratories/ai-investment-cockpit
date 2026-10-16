"""Cancel only authenticated ordinary PR runs strictly older than the live head.

Executed exclusively by the default-branch workflow_run companion. No checkout
or artifact from a PR is read, and no run is created, retried, or marked success.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode

WORKFLOWS = {'.github/workflows/ci.yml': 'ci.yml',
             '.github/workflows/rust.yml': 'rust.yml'}
ACTIVE = {'queued', 'pending', 'requested', 'waiting', 'in_progress'}
SHA = re.compile(r'[0-9a-f]{40}')


class Refused(RuntimeError):
    """Insufficient evidence; do not broaden cancellation."""


class GitHub:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.deadline = clock() + 120
        self.calls = 0

    def request(self, method, path):
        remaining = self.deadline - self.clock()
        self.calls += 1
        if remaining <= 0 or self.calls > 160:
            raise Refused('Controller read/write budget exceeded')
        result = subprocess.run(['gh', 'api', '--method', method, path], check=True,
                                capture_output=True, text=True, timeout=min(10, remaining))
        return json.loads(result.stdout) if result.stdout.strip() else None

    def read(self, path):
        return self.request('GET', path)

    def cancel(self, path):
        # No retry on an uncertain write, and no force-cancel endpoint.
        self.request('POST', path)


def positive(value):
    return type(value) is int and value > 0


def same_repo(value, repo, repo_id, compact=False):
    if not isinstance(value, dict) or value.get('id') != repo_id:
        return False
    if compact:
        return value.get('url') == f'https://api.github.com/repos/{repo}'
    return value.get('full_name') == repo


def association(run, repo, repo_id):
    """Require one exact same-repository PR association; never infer by branch."""
    prs = run.get('pull_requests')
    if not isinstance(prs, list) or len(prs) != 1:
        return None
    pr = prs[0]
    if not isinstance(pr, dict) or not positive(pr.get('number')) or not positive(pr.get('id')):
        return None
    if pr.get('url') != f"https://api.github.com/repos/{repo}/pulls/{pr['number']}":
        return None
    for side in ('head', 'base'):
        data = pr.get(side, {})
        if not isinstance(data, dict) or not same_repo(data.get('repo'), repo, repo_id, compact=True):
            return None
        if not isinstance(data.get('ref'), str) or not data['ref']:
            return None
    if pr['head']['ref'] != run.get('head_branch'):
        return None
    return pr


def run_identity(run, repo, repo_id, workflows, live=None):
    if not isinstance(run, dict):
        return None
    path = run.get('path')
    if (run.get('event') != 'pull_request' or not isinstance(path, str) or path not in workflows
            or not isinstance(run.get('status'), str)
            or run.get('workflow_id') != workflows[path]
            or not positive(run.get('id')) or not positive(run.get('run_attempt'))
            or not SHA.fullmatch(str(run.get('head_sha', '')))
            or not same_repo(run.get('repository'), repo, repo_id)
            or not same_repo(run.get('head_repository'), repo, repo_id)):
        return None
    pr = association(run, repo, repo_id)
    if (pr is None and live is not None and run.get('pull_requests') == []
            and run['head_sha'] != live[4] and run.get('head_branch') == live[2]):
        # GitHub drops the PR link from runs whose SHA is no longer the PR head.
        # Branch plus same-repository head, live PR identity and the strict
        # ancestor compare stand in; the current-head witness stays strict.
        pr = {'number': live[0], 'id': live[1], 'base': {'ref': live[3]}}
    if pr is None:
        return None
    return (run['id'], run['run_attempt'], run['workflow_id'], path, run['head_sha'],
            run['head_branch'], pr['number'], pr['id'], pr['base']['ref'])


def live_pr(api, repo, repo_id, number):
    pr = api.read(f'repos/{repo}/pulls/{number}')
    if not isinstance(pr, dict):
        raise Refused('Malformed live PR')
    head, base = pr.get('head'), pr.get('base')
    if not isinstance(head, dict) or not isinstance(base, dict):
        raise Refused('Malformed live PR head/base')
    if (pr.get('number') != number or not positive(pr.get('id'))
            or pr.get('state') != 'open' or pr.get('draft') is not False
            or not same_repo(head.get('repo'), repo, repo_id)
            or not same_repo(base.get('repo'), repo, repo_id)
            or not SHA.fullmatch(str(head.get('sha', '')))):
        raise Refused('PR is not an open, non-draft, same-repository target')
    if any(not isinstance(side.get('ref'), str) or not side['ref'] for side in (head, base)):
        raise Refused('Missing live branch identity')
    return (pr['number'], pr['id'], head['ref'], base['ref'], head['sha'])


def belongs(identity, live):
    return identity is not None and (identity[6], identity[7], identity[5], identity[8]) == live[:4]


def stable(data):
    """Identity-only view of a run page; status/timestamps legitimately change."""
    if not isinstance(data, dict) or not isinstance(data.get('workflow_runs'), list):
        return None
    return (data.get('total_count'), [
        (run.get('id'), run.get('run_attempt'), run.get('head_sha'))
        if isinstance(run, dict) else None for run in data['workflow_runs']])


def run_snapshot(api, repo, workflow_id, branch):
    """Read all matching runs before any mutation; reject capped/changing pages."""
    query = urlencode({'event': 'pull_request', 'branch': branch, 'per_page': 100})
    path = f'repos/{repo}/actions/workflows/{workflow_id}/runs?{query}'
    rows, seen, total = [], set(), None
    first = None
    for page in range(1, 11):
        data = api.read(f'{path}&page={page}')
        if not isinstance(data, dict):
            raise Refused('Malformed run page')
        batch, count = data.get('workflow_runs'), data.get('total_count')
        if not isinstance(batch, list) or type(count) is not int or not 0 <= count < 1000:
            raise Refused('Missing or capped run pagination')
        if total is None:
            total = count
            first = data
        if count != total:
            raise Refused('Run pagination changed')
        for run in batch:
            identifier = run.get('id') if isinstance(run, dict) else None
            if not positive(identifier) or identifier in seen:
                raise Refused('Invalid or duplicate run ID in pagination')
            seen.add(identifier)
        rows.extend(batch)
        if len(batch) < 100:
            if len(rows) != total:
                raise Refused('Incomplete run pagination')
            # Listing must remain stable through discovery. Per-run revalidation
            # below catches later status/attempt changes without trusting order.
            if stable(api.read(f'{path}&page=1')) != stable(first):
                raise Refused('Run snapshot changed during pagination')
            return rows
    raise Refused('Run pagination bound exceeded')


def strict_ancestor(api, repo, older, current):
    if older == current:
        return False
    data = api.read(f'repos/{repo}/compare/{older}...{current}')
    if not isinstance(data, dict):
        raise Refused('Malformed ancestry evidence')
    status = data.get('status')
    if status in ('identical', 'behind', 'diverged'):
        return False
    base, merge_base = data.get('base_commit'), data.get('merge_base_commit')
    if (status != 'ahead' or not isinstance(base, dict) or not isinstance(merge_base, dict)
            or base.get('sha') != older or merge_base.get('sha') != older
            or type(data.get('ahead_by')) is not int or data['ahead_by'] <= 0
            or data.get('behind_by') != 0):
        raise Refused('Incomplete strict-ancestor evidence')
    return True


def successor(run):
    return run.get('status') in ACTIVE or (
        run.get('status') == 'completed' and run.get('conclusion') == 'success')


def current_witness(identified, live, workflow_id):
    current = [(run, identity) for run, identity in identified
               if belongs(identity, live) and identity[2] == workflow_id
               and identity[4] == live[4]]
    # Run IDs cannot order reruns of older IDs. Decline this optimization when
    # any current-head run was rerun; never infer attempt recency by timestamps.
    if not current or any(identity[1] != 1 for _, identity in current):
        return None
    witness, identity = max(current, key=lambda item: item[1][0])
    return identity if successor(witness) else None


def reconcile(api, repo, event_name, event):
    if not isinstance(event, dict):
        raise Refused('Malformed triggering event')
    if event_name != 'workflow_run' or event.get('action') not in ('requested', 'completed'):
        return []
    payload = event.get('workflow_run', {})
    if not isinstance(payload, dict):
        raise Refused('Malformed triggering run')
    if (payload.get('event') != 'pull_request' or not isinstance(payload.get('path'), str)
            or payload['path'] not in WORKFLOWS):
        return []
    repository = event.get('repository', {})
    if not isinstance(repository, dict):
        raise Refused('Malformed triggering repository')
    repo_id = repository.get('id')
    if not positive(repo_id) or not same_repo(repository, repo, repo_id):
        raise Refused('Unverified triggering repository')
    workflows = {}
    for path, name in WORKFLOWS.items():
        workflow = api.read(f'repos/{repo}/actions/workflows/{name}')
        if (not isinstance(workflow, dict) or workflow.get('path') != path
                or not positive(workflow.get('id')) or workflow.get('state') != 'active'):
            raise Refused('Unverified active workflow identity')
        workflows[path] = workflow['id']
    triggered = run_identity(payload, repo, repo_id, workflows)
    if triggered is None:
        raise Refused('Unverified triggering run provenance')
    source = api.read(f'repos/{repo}/actions/runs/{triggered[0]}')
    if run_identity(source, repo, repo_id, workflows) != triggered:
        raise Refused('Triggering run identity changed')
    live = live_pr(api, repo, repo_id, triggered[6])
    if not belongs(triggered, live):
        raise Refused('Triggering PR/branch identity changed')
    plans = []
    # Complete both workflow snapshots and ancestry reads before any cancellation.
    for workflow_id in workflows.values():
        rows = run_snapshot(api, repo, workflow_id, live[2])
        identified = [(run, run_identity(run, repo, repo_id, workflows, live)) for run in rows]
        witness_id = current_witness(identified, live, workflow_id)
        if witness_id is None:
            continue
        for run, identity in identified:
            if (not belongs(identity, live) or identity[2] != workflow_id
                    or identity[1] != 1 or run.get('status') not in ACTIVE
                    or identity[4] == live[4]):
                continue
            if strict_ancestor(api, repo, identity[4], live[4]):
                plans.append((identity, witness_id))
    requested = []
    for identity, witness_id in plans:
        # A rerun, completed cancellation, retarget, draft transition or head
        # change invalidates the plan. All APIs remain bounded and fail closed.
        target = api.read(f'repos/{repo}/actions/runs/{identity[0]}')
        if run_identity(target, repo, repo_id, workflows, live) != identity:
            raise Refused('Target identity/attempt changed before cancellation')
        if target.get('status') not in ACTIVE:
            continue
        if not strict_ancestor(api, repo, identity[4], live[4]):
            raise Refused('Ancestry changed before cancellation')
        witness = api.read(f'repos/{repo}/actions/runs/{witness_id[0]}')
        if run_identity(witness, repo, repo_id, workflows) != witness_id:
            raise Refused('Successor identity/attempt changed before cancellation')
        if not successor(witness):
            continue
        fresh = run_snapshot(api, repo, identity[2], live[2])
        identified = [(run, run_identity(run, repo, repo_id, workflows, live)) for run in fresh]
        fresh_targets = [(run, found) for run, found in identified
                         if run['id'] == identity[0]]
        if len(fresh_targets) != 1 or fresh_targets[0][1] != identity:
            raise Refused('Target identity/attempt changed in final snapshot')
        if fresh_targets[0][0].get('status') not in ACTIVE:
            continue
        if current_witness(identified, live, identity[2]) != witness_id:
            raise Refused('Current-head successor changed before cancellation')
        if live_pr(api, repo, repo_id, live[0]) != live:
            raise Refused('Live head or PR state changed before cancellation')
        api.cancel(f'repos/{repo}/actions/runs/{identity[0]}/cancel')
        requested.append(identity[0])
        print(f'Requested cancellation of verified ancestor run {identity[0]}', flush=True)
    return requested


def main():
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
    repo = os.environ['GITHUB_REPOSITORY']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise Refused('Invalid repository name')
    reconcile(GitHub(), repo, os.environ['GITHUB_EVENT_NAME'], event)


if __name__ == '__main__':
    main()

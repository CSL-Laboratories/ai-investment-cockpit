"""Read-only Devin check gate. Fail closed on incomplete API evidence."""
import json
import os
import re
import subprocess
import time

DEVIN_APP_ID = 811515  # api.github.com/apps/devin-ai-integration, verified 2026-10-16
DEVIN_APP_SLUG = "devin-ai-integration"


class GateError(RuntimeError):
    pass


def api(path):
    result = subprocess.run(["gh", "api", path], check=True, capture_output=True,
                            text=True, timeout=30)
    return json.loads(result.stdout)


def pages(path, key=None, get=api):
    """Explicit pagination, bounded and fail closed rather than silently truncate."""
    rows = []
    for page in range(1, 51):
        data = get(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
        batch = data[key] if key else data
        if not isinstance(batch, list):
            raise GateError("Malformed API page")
        rows.extend(batch)
        if len(batch) < 100:
            total = data.get("total_commits", data.get("total_count")) if isinstance(data, dict) else None
            if total is not None and (not isinstance(total, int) or len(rows) != total):
                raise GateError("API returned incomplete or changing pagination")
            return rows
    raise GateError("API pagination bound exceeded")


def check_state(repo, sha, get=api):
    runs = pages(f"repos/{repo}/commits/{sha}/check-runs?filter=all", "check_runs", get)
    trusted = {}
    for run in runs:
        if (run.get("name") != "Devin Review" or run.get("head_sha") != sha
                or run.get("app", {}).get("id") != DEVIN_APP_ID
                or run.get("app", {}).get("slug") != DEVIN_APP_SLUG):
            continue
        identifier = run.get("id")
        if not isinstance(identifier, int) or identifier <= 0:
            raise GateError("Invalid Devin check ID")
        if identifier in trusted and trusted[identifier] != run:
            raise GateError("Conflicting duplicate Devin check ID")
        trusted[identifier] = run
    if not trusted:
        return "pending"
    # New check-run IDs supersede previous attempts; a re-request on the same ID
    # updates that run's status. Never search historical attempts for success.
    current = trusted[max(trusted)]
    if current.get("status") in ("queued", "in_progress", "waiting", "pending", "requested"):
        return "pending"
    if current.get("status") != "completed":
        raise GateError("Unknown Devin check status")
    return "success" if current.get("conclusion") == "success" else "failure"


def queue_heads(repo, group, get=api):
    base, head = group["base_sha"], group["head_sha"]
    branch = group["base_ref"].removeprefix("refs/heads/")
    commits = pages(f"repos/{repo}/compare/{base}...{head}", "commits", get)
    shas = {commit["sha"] for commit in commits}
    if not shas or head not in shas:
        raise GateError("Missing merge-group commit evidence")
    # Queue-created merge commits need no separate review. Every ordinary commit
    # must belong to an open PR whose exact live head is included in the group.
    if any(not c.get("parents") for c in commits):
        raise GateError("Missing commit ancestry")
    ordinary = [c for c in commits if len(c.get("parents", [])) == 1]
    if not ordinary:
        raise GateError("No constituent PR commits found")
    heads = set()
    for commit in ordinary:
        associated = pages(f"repos/{repo}/commits/{commit['sha']}/pulls", get=get)
        candidates = {pr["number"] for pr in associated
                      if pr.get("state") == "open" and pr.get("base", {}).get("ref") == branch
                      and pr.get("base", {}).get("repo", {}).get("full_name") == repo
                      and pr.get("head", {}).get("sha") in shas}
        if len(candidates) != 1:
            raise GateError("Missing or ambiguous constituent PR mapping")
        number = candidates.pop()
        pr = get(f"repos/{repo}/pulls/{number}")
        if (pr.get("state") != "open" or pr.get("draft") is not False
                or pr.get("base", {}).get("ref") != branch
                or pr.get("base", {}).get("repo", {}).get("full_name") != repo
                or pr.get("head", {}).get("sha") not in shas):
            raise GateError("Constituent PR changed or is not eligible")
        heads.add(pr["head"]["sha"])
    return sorted(heads)


def targets(repo, event_name, event, sha, get=api):
    if event_name == "merge_group":
        return queue_heads(repo, event["merge_group"], get)
    number = event.get("pull_request", {}).get("number") or event.get("inputs", {}).get("pr_number")
    if not re.fullmatch(r"[1-9][0-9]*", str(number)):
        raise GateError("A valid PR number is required")
    pr = get(f"repos/{repo}/pulls/{number}")
    expected = event.get("pull_request", {}).get("head", {}).get("sha") or sha
    if (pr.get("state") != "open" or pr.get("draft") is not False
            or pr.get("head", {}).get("sha") != expected):
        raise GateError("PR closed, draft, or head changed")
    return [expected]


def wait_for_review(resolve, state, attempts=9, sleep=time.sleep, clock=time.monotonic):
    deadline = clock() + 120
    initial = resolve()
    for attempt in range(attempts):
        if clock() >= deadline:
            break
        if resolve() != initial:
            raise GateError("Review targets changed during polling")
        results = [state(sha) for sha in initial]
        if results and all(result == "success" for result in results) and clock() < deadline:
            if resolve() != initial:
                raise GateError("Review targets changed before success")
            return
        # A failed attempt may be replaced by a new Devin retry during this bound.
        if attempt + 1 < attempts:
            remaining = deadline - clock()
            if remaining <= 0:
                break
            sleep(min(15, remaining))
    raise GateError("Current Devin review did not succeed within the two-minute bound; rerun after completion")


def main():
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)
    repo = os.environ["GITHUB_REPOSITORY"]
    wait_for_review(lambda: targets(repo, os.environ["GITHUB_EVENT_NAME"], event,
                                    os.environ["GITHUB_SHA"]),
                    lambda sha: check_state(repo, sha))
    print("Current authenticated Devin review succeeded for every required PR head")


if __name__ == "__main__":
    main()

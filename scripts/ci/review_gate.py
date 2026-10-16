"""Read-only Devin status gate. Fail closed on incomplete API evidence."""
import json
import os
import re
import subprocess
import time

# Verified from Cockpit #147's commit statuses on 2026-10-16. The provider
# publishes statuses, not check runs; an app metadata ID alone is not evidence.
DEVIN_BOT_ID = 158243242
DEVIN_BOT_LOGIN = "devin-ai-integration[bot]"


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
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise GateError("An exact commit SHA is required")
    path = f"repos/{repo}/commits/{sha}/statuses"
    statuses = pages(path, get=get)
    expected_url = f"https://api.github.com/repos/{repo}/statuses/{sha}"
    trusted = {}
    for status in statuses:
        creator = status.get("creator") or {}
        if (status.get("context") != "Devin Review"
                or creator.get("id") != DEVIN_BOT_ID
                or creator.get("login") != DEVIN_BOT_LOGIN
                or creator.get("type") != "Bot"):
            continue
        # Status responses do not have head_sha. Bind the API's canonical status
        # URL as well as the requested commit endpoint; never trust target_url.
        if status.get("url") != expected_url:
            raise GateError("Devin status does not bind to the requested commit")
        identifier = status.get("id")
        if type(identifier) is not int or identifier <= 0:
            raise GateError("Invalid Devin status ID")
        if identifier in trusted and trusted[identifier] != status:
            raise GateError("Conflicting duplicate Devin status ID")
        trusted[identifier] = status
    if not trusted:
        return "pending"
    # Status updates create new IDs. Select the latest authenticated publisher
    # record regardless of page/order; never search old attempts for success or
    # accept check-run success as a fallback for a missing/current failed status.
    current = trusted[max(trusted)]
    state = current.get("state")
    if state not in ("success", "pending", "failure", "error"):
        raise GateError("Unknown Devin status state")
    # The status list has no total_count and new entries can shift page offsets.
    # Confirm page one stayed stable before accepting success; otherwise let the
    # bounded poll obtain a new scan. This is not an atomic provider snapshot.
    if state == "success" and get(f"{path}?per_page=100&page=1") != statuses[:100]:
        return "pending"
    return "failure" if state == "error" else state


def queue_heads(repo, group, get=api):
    base, head = group["base_sha"], group["head_sha"]
    branch = group["base_ref"].removeprefix("refs/heads/")
    commits = pages(f"repos/{repo}/compare/{base}...{head}", "commits", get)
    shas = {commit["sha"] for commit in commits}
    if not shas or head not in shas:
        raise GateError("Missing merge-group commit evidence")
    # Only the exact synthetic merge head attested by the merge_group event is
    # exempt. Parent count cannot distinguish a queue merge from a PR-authored
    # conflict-resolution merge. Every other commit, including merge-only PR
    # heads and intermediate merges, must have unambiguous review coverage.
    if any(not c.get("parents") for c in commits):
        raise GateError("Missing commit ancestry")
    constituents = [c for c in commits
                    if c["sha"] != head or len(c["parents"]) < 2]
    if not constituents:
        raise GateError("No constituent PR commits found")
    heads = set()
    for commit in constituents:
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
            if clock() >= deadline:
                raise GateError("Review deadline exceeded during final verification")
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

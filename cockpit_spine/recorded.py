"""Check a recorded operator session without re-running it.

A reviewer who has no Recursion checkout can still prove the committed record
is whole: the chain verifies and replays under the session rules, every step's
response head is a real chain hash, every displayed number in every step is a
verified published-tier link, and approve, veto and stop each happened.
"""

from __future__ import annotations

from typing import Any

from cockpit_spine import views
from cockpit_spine.audit import AuditError, verify_chain
from cockpit_spine.projections import Registry
from cockpit_spine.session import OperatorSession
from cockpit_spine.sources import ProposalSource

REQUIRED_CODES = ("approved", "vetoed", "stopped")


class _NoSource:
    adapter_kind = "none"

    def fetch(self, **_: Any) -> Any:
        raise AssertionError("a replay never contacts a source")


def problems(record: dict[str, Any], registry: Registry) -> list[str]:
    found: list[str] = []
    if record.get("platform") != "linux":
        found.append("not recorded as a linux session")
    if record.get("live_calls") is not False:
        found.append("live_calls is not false")
    if "no verdict" not in str(record.get("kind", "")):
        found.append("record does not say it carries no verdict")
    if "deferred" not in str(record.get("label", "")):
        found.append("record does not carry the deferral label")
    if record.get("projection_registry_digest") != registry.digest:
        found.append("projection registry digest differs from the committed registry")
    chain = record.get("chain")
    try:
        head = verify_chain(chain)
    except AuditError as error:
        return found + [f"chain: {error}"]
    if record.get("head") != head:
        found.append("recorded head is not the chain head")
    source: ProposalSource = _NoSource()
    try:
        session = OperatorSession.replay(chain, source=source)
    except AuditError as error:
        return found + [f"replay: {error}"]
    states = {p["proposal_id"]: p["state"] for p in session.proposals()}
    if not {"approved", "vetoed", "abandoned"} <= set(states.values()):
        found.append("final states do not include approved, vetoed and abandoned")
    hashes = {r["hash"] for r in chain}
    codes = []
    for index, step in enumerate(record.get("steps", [])):
        response = step.get("response", {})
        ok = response.get("ok")
        if step.get("exit_code") != (0 if ok else 2):
            found.append(f"step {index}: exit code does not match the response")
        if response.get("head") not in hashes:
            found.append(f"step {index}: head is not in the chain")
        try:
            views.validate_lines([views.Line(**i) for i in response.get("lines", [])], registry)
        except (views.ViewError, TypeError) as error:
            found.append(f"step {index}: {error}")
        if ok:
            codes.append(response.get("code"))
    for needed in REQUIRED_CODES:
        if needed not in codes:
            found.append(f"no successful {needed} step")
    refusals = [s["response"]["code"] for s in record.get("steps", []) if not s["response"]["ok"]]
    if "session_stopped" not in refusals:
        found.append("no refusal after stop was recorded")
    return found

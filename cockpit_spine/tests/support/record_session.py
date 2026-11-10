"""Record one Linux operator session, driven through the CLI, against Recursion's fake driver.

Every ``propose`` builds the real WP12 ``RouteConsumer`` over Recursion's
in-process reference adapter and AT1 ``FakeRouteDriver`` (the read-only checkout
named by ``RECURSION_ROUTE_CONTRACT_CHECKOUT``, byte-verified against the pin),
with a scripted transport. No provider runs, no credential is real, nothing
leaves the machine.

The record carries no verdict and compares no models. It shows that approve,
veto and stop work, fail closed and are audited, on the path the demo will use.

The committed file is bound to the synthetic projection stand-in from
``tests/support/vault.py`` (fifteen accepted cells and five that failed closed).
Those figure links are not csl-research evidence. ``run`` takes the vault root
and the registry file explicitly and does not read ``CSL_RESEARCH_CHECKOUT``.
A different registry changes the figure links and ``projection_registry_digest``,
so it does not match the committed file and the checker reports that.

Regenerate on Linux, with the Recursion checkout and the synthetic pair (or any
other vault you mean to cite)::

    RECURSION_ROUTE_CONTRACT_CHECKOUT=<checkout> python3 -X utf8 -B -m \\
        cockpit_spine.tests.support.record_session --write \\
        --vault-root <vault> --registry <registry.json>
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from cockpit_spine import cli
from cockpit_spine.recorded import EXPECTED_KIND, EXPECTED_LABEL
from cockpit_spine.fake_source import FAIL_PROMPT, inline_payload
from cockpit_spine.projections import REPO_ROOT, load_registry
from cockpit_spine.sources import PROPOSAL_WORKLOAD, RouteProposalSource
from cockpit_spine.tests.support.scripted import counter_clock
from successor.route_consumer import DECISION_PROFILE, pin
from successor.route_consumer.profile import load_output_schema
from successor.route_consumer.tests.support import rig as rigmod
from successor.route_consumer.tests.support.bakeoff import _script
from successor.route_consumer.tests.support.recursion_bridge import recursion

RECORD_PATH = REPO_ROOT / "cockpit_spine" / "records" / "linux-operator-session-fake-driver.json"
OPERATOR = "operator-1"
LOG_NAME = "<transcript>"


class RecursionFakeDriverSource:
    """A ``ProposalSource`` whose every fetch goes consumer -> adapter -> fake driver."""

    adapter_kind = "recursion-fake-driver"

    def __init__(self) -> None:
        self._rec = recursion()

    def fetch(self, *, request_id: str, mode: str, prompt: str) -> Any:
        if prompt == FAIL_PROMPT:
            script = [{"returncode": 0, "stdout": "", "stderr": "", "timed_out": True}]
        else:
            script = _script("claude", inline_payload(request_id))
        # The pinned adapter accepts only the schema it was built with. The
        # consumer submits the proposal workload's schema, so the rig must be
        # built with that same schema (as the bakeoff rig is).
        schema = load_output_schema(DECISION_PROFILE[PROPOSAL_WORKLOAD])
        rig = rigmod.make_rig(self._rec, "claude", script=script, output_schema=schema)
        return RouteProposalSource(rig.consumer, adapter_kind=self.adapter_kind).fetch(
            request_id=request_id, mode=mode, prompt=prompt)


def _argv(*args: str) -> list[str]:
    return list(args)


def scenario(digests: dict[str, str]) -> list[list[str]]:
    return [
        _argv("open", "--session-id", "demo-1", "--mode", "paper"),
        _argv("propose", "--request-id", "r1"),
        _argv("propose", "--request-id", "r2"),
        _argv("propose", "--request-id", "r3", "--prompt", FAIL_PROMPT),
        _argv("approve", "--proposal-id", "prop-r1", "--operator", OPERATOR,
              "--confirm-digest", "sha256:" + "0" * 64),
        _argv("approve", "--proposal-id", "prop-r1", "--operator", OPERATOR,
              "--confirm-digest", digests.get("prop-r1", "")),
        _argv("veto", "--proposal-id", "prop-r2", "--operator", OPERATOR,
              "--reason", "size not justified"),
        _argv("propose", "--request-id", "r4"),
        _argv("stop", "--operator", OPERATOR, "--reason", "end of demo session"),
        _argv("propose", "--request-id", "r5"),
        _argv("approve", "--proposal-id", "prop-r4", "--operator", OPERATOR,
              "--confirm-digest", digests.get("prop-r4", "")),
        _argv("audit"),
    ]


def run(*, vault_root: Path, registry_path: Path) -> dict[str, Any]:
    """One session against ``vault_root`` and ``registry_path``. No environment fallback."""

    registry = load_registry(registry_path, vault_root=vault_root)
    source = RecursionFakeDriverSource()
    clock = counter_clock()
    steps: list[dict[str, Any]] = []
    digests: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="session-") as tmp:
        log = Path(tmp) / "transcript.jsonl"
        index = 0
        while True:
            plan = scenario(digests)
            if index >= len(plan):
                break
            argv = plan[index]
            out = io.StringIO()
            code = cli.main(
                ["--log", str(log), "--vault-root", str(vault_root), "--json", *argv],
                source_factory=lambda: source, out=out, clock=clock,
                registry_path=registry_path,
            )
            response = json.loads(out.getvalue())
            steps.append({"argv": argv, "exit_code": code, "response": response})
            lines = response["lines"]
            current = None
            for item in lines:
                if item["label"] == "proposal":
                    current = item["value"]
                elif item["label"] == "confirm digest" and current:
                    digests[current] = item["value"]
            index += 1
        chain = cli.read_log(log)
    pinned = pin.load_pin()
    return {
        "label": EXPECTED_LABEL,
        "kind": EXPECTED_KIND,
        "platform": "linux",
        "adapter_kind": source.adapter_kind,
        "live_calls": False,
        "contract": {
            "repository": pinned["source"]["repository"],
            "pull_request": pinned["source"]["pull_request"],
            "head_sha": pinned["source"]["head_sha"],
            "schema_sha256": pinned["vendored"][pin.SCHEMA_KEY]["sha256"],
        },
        "projection_registry_digest": registry.digest,
        "steps": steps,
        "chain": chain,
        "head": chain[-1]["hash"],
    }


def render(record: dict[str, Any]) -> str:
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="record_session", description=__doc__)
    parser.add_argument("--write", action="store_true", help="replace the committed record")
    parser.add_argument(
        "--vault-root", type=Path, required=True,
        help="vault the registry cites; required, CSL_RESEARCH_CHECKOUT is not read",
    )
    parser.add_argument(
        "--registry", type=Path, required=True,
        help="registry file verified against --vault-root",
    )
    args = parser.parse_args(argv)
    if args.write and not sys.platform.startswith("linux"):
        sys.stderr.write("the recorded session is a Linux session; refusing to write\n")
        return 3
    text = render(run(vault_root=args.vault_root, registry_path=args.registry))
    if args.write:
        RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)
        RECORD_PATH.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

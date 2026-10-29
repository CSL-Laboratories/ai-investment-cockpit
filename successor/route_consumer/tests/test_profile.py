"""Decision profile: neutral workload ids, the Inv 2 table, and vendored output schemas."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from successor.route_consumer import pin
from successor.route_consumer.profile import (
    CODEX_ELIGIBLE_MODES,
    DECISION_PROFILE,
    MODES,
    PINNED_ENGINE,
    ProfileRefusal,
    WorkloadProfile,
    canonical_digest,
    engines_allowed,
    load_output_schema,
)
from successor.route_consumer.records import RouteBinding, build_request_record

ROOT = Path(__file__).resolve().parents[3]
BINDING = RouteBinding(
    route_id="route-fake", engine_id="claude", route_descriptor_digest="sha256:" + "1" * 64,
    process_requirement_id="proc-req-1", cli_model="model-x", tenant="tenant-a",
    credential_provider="fake", credential_name="route-ref",
)
COCKPIT_WORDS = ("cockpit", "briefing", "triage", "grounding", "proposal", "interrogation",
                 "trade", "order", "broker", "morningbrief", "tradeproposal", "watch_only")


class ProfileTests(unittest.TestCase):
    def test_five_workloads_with_neutral_ids(self) -> None:
        self.assertEqual(5, len(DECISION_PROFILE))
        id_pattern = pin.schema_defs()["Id"]["pattern"]
        for workload_id, profile in DECISION_PROFILE.items():
            self.assertEqual(workload_id, profile.workload_id)
            self.assertRegex(workload_id, id_pattern)
            self.assertRegex(workload_id, r"^workload-d\d\d$")
            for word in COCKPIT_WORDS:
                self.assertNotIn(word, workload_id)

    def test_cockpit_job_names_never_enter_a_route_request(self) -> None:
        for profile in DECISION_PROFILE.values():
            schema = load_output_schema(profile)
            record = build_request_record(
                request_id="req-1", binding=BINDING, profile=profile, prompt="PROMPT",
                output_schema=schema,
            )
            self.assertEqual([], pin.validation_errors("RouteExecutionRequest", record))
            blob = json.dumps(record).lower()
            for word in COCKPIT_WORDS + (profile.cockpit_job, profile.output_schema_name.lower()):
                self.assertNotIn(word.lower(), blob, f"{profile.workload_id}: {word}")

    def test_output_schemas_match_their_digests(self) -> None:
        for profile in DECISION_PROFILE.values():
            schema = load_output_schema(profile)
            self.assertEqual(profile.output_schema_digest, canonical_digest(schema))
            self.assertEqual(profile.output_schema_name, schema["title"])

    def test_a_changed_output_schema_is_refused(self) -> None:
        profile = next(iter(DECISION_PROFILE.values()))
        bent = WorkloadProfile(
            profile.workload_id, profile.cockpit_job, profile.output_schema_name,
            "sha256:" + "0" * 64,
        )
        with self.assertRaises(ProfileRefusal) as caught:
            load_output_schema(bent)
        self.assertEqual("output_schema_digest_mismatch", caught.exception.code)

    def test_vendored_schemas_still_equal_the_legacy_copies_while_they_exist(self) -> None:
        legacy = ROOT / "app" / "schemas" / "json"
        if not legacy.is_dir():
            self.skipTest("legacy app/ tree is gone; the profile digests are authoritative")
        for profile in DECISION_PROFILE.values():
            name = f"{profile.output_schema_name}.schema.json"
            successor = pin.PACKAGE_DIR / "output_schemas" / name
            legacy_path = legacy / name
            if profile.output_schema_name == "InterrogationAnswer":
                fresh = json.loads(successor.read_text(encoding="utf-8"))
                old = json.loads(legacy_path.read_text(encoding="utf-8"))
                self.assertEqual(["fresh"], fresh["properties"]["pattern"]["enum"])
                self.assertEqual(["resume", "fresh"], old["properties"]["pattern"]["enum"])
                continue
            self.assertEqual(legacy_path.read_bytes(), successor.read_bytes(), name)

    def test_every_profile_is_analysis_only_zero_tools_and_bounded(self) -> None:
        for profile in DECISION_PROFILE.values():
            self.assertEqual("analysis-only", profile.authority)
            self.assertEqual("private", profile.privacy_class)
            self.assertGreaterEqual(profile.max_turns, 1)
            self.assertGreater(profile.timeout_s, 0)

    def test_interrogation_profile_schema_is_fresh_only(self) -> None:
        schema = load_output_schema(DECISION_PROFILE["workload-d05"])
        self.assertEqual(["fresh"], schema["properties"]["pattern"]["enum"])


class InvariantTwoTableTests(unittest.TestCase):
    def test_auto_modes_and_review_live_are_claude_only(self) -> None:
        for mode in ("review_live", "auto_veto", "auto_full"):
            self.assertEqual((PINNED_ENGINE,), engines_allowed(mode), mode)

    def test_codex_is_allowed_only_where_agents_md_allows_it(self) -> None:
        self.assertEqual(frozenset({"read_only", "paper"}), CODEX_ELIGIBLE_MODES)
        for mode in CODEX_ELIGIBLE_MODES:
            self.assertEqual(("claude", "codex"), engines_allowed(mode))

    def test_every_known_mode_resolves_and_unknown_modes_are_refused(self) -> None:
        for mode in MODES:
            self.assertTrue(engines_allowed(mode))
        for bad in ("", "AUTO_FULL", "live", "watch_only", None, 7):
            with self.assertRaises(ProfileRefusal, msg=repr(bad)) as caught:
                engines_allowed(bad)  # type: ignore[arg-type]
            self.assertEqual("unknown_mode", caught.exception.code)

    def test_pinned_engine_is_a_schema_engine(self) -> None:
        self.assertIn(PINNED_ENGINE, pin.schema_defs()["EngineProfile"]["enum"])
        self.assertRegex(PINNED_ENGINE, re.compile(r"^claude$"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

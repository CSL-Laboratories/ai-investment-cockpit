"""CSL-221 qualification evidence controls. Skipped cells are never accepted."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "qualification_evidence", ROOT / "tools" / "qualification_evidence.py"
)
assert SPEC and SPEC.loader
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

COMMIT = "a" * 40
TREE = "b" * 40


def codes(errors):
    return {item.split(":", 1)[0] for item in errors}


class QualificationEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matrix = MOD.load_matrix(MOD.matrix_path(ROOT))
        self.tmp = Path(tempfile.mkdtemp(prefix="qual-"))
        self.bundle = MOD.materialize_passing_bundle(
            self.tmp, self.matrix, commit=COMMIT, tree=TREE
        )
        self.root = self.tmp / "artifacts"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def validate(self, bundle=None, *, commit=COMMIT, tree=TREE, root=None):
        return MOD.validate_bundle(
            self.bundle if bundle is None else bundle,
            matrix=self.matrix,
            expected_commit=commit,
            expected_tree=tree,
            artifact_root=self.root if root is None else root,
        )

    def test_matrix_is_platform_python_tier_and_never_pre_passed(self) -> None:
        expected = {
            f"{host}/{python}/{tier}"
            for host in MOD.PLATFORMS
            for python in MOD.PYTHONS
            for tier in MOD.TIERS
        }
        self.assertEqual({cell["cell_id"] for cell in self.matrix["cells"]}, expected)
        required = MOD.required_cells(self.matrix)
        self.assertEqual({cell["tier"] for cell in required}, {MOD.EXECUTED_TIER})
        self.assertEqual(len(required), len(MOD.PLATFORMS) * len(MOD.PYTHONS))
        self.assertTrue(all(cell["pass_allowed"] for cell in required))
        self.assertTrue(all(cell["recorded_status"] == "not_run" for cell in self.matrix["cells"]))
        excluded = [cell for cell in self.matrix["cells"] if cell["tier"] != MOD.EXECUTED_TIER]
        self.assertTrue(excluded)
        self.assertTrue(all(cell["requirement"] == "excluded" for cell in excluded))
        self.assertTrue(all(cell["pass_allowed"] is False for cell in excluded))
        self.assertEqual(
            self.matrix["owners"],
            {
                "required": "CSL-221 qualification evidence",
                "excluded": "release owner",
            },
        )
        self.assertTrue(all(cell["owner"] == self.matrix["owners"]["required"] for cell in required))
        self.assertTrue(all(cell["owner"] == self.matrix["owners"]["excluded"] for cell in excluded))
        for relative in (
            "tools/qualification_evidence.py",
            "docs/evidence/csl221/qualification-matrix.json",
            "docs/evidence/csl221-unresolved-blockers.md",
            "docs/evidence/csl221-dec09-qualification-matrix-2026-10-17.md",
        ):
            self.assertNotIn("Lucas", (ROOT / relative).read_text(encoding="utf-8"), relative)
        self.assertTrue(all(record["pass_allowed"] is False for record in self.matrix["historical_records"]))
        self.assertEqual(self.matrix["does_not_replace"], "DEC-09")
        self.assertEqual(self.matrix["release_decision"], "not_made")
        for cell in self.matrix["cells"]:
            self.assertEqual(cell["runner_image"], MOD.HOSTED_RUNNER_IMAGES[cell["platform"]])

    def test_schema_matches_the_validator_keys(self) -> None:
        schema = json.loads(
            (ROOT / "docs" / "evidence" / "csl221" / "qualification-evidence.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(schema["additionalProperties"], False)
        self.assertEqual(set(schema["required"]), MOD.BUNDLE_KEYS)
        cell = schema["$defs"]["cell"]
        self.assertEqual(set(cell["required"]), MOD.CELL_KEYS)
        self.assertEqual(cell["additionalProperties"], False)
        status = cell["properties"]["status"]["enum"]
        for name in ("pass", "skipped", "unknown", "excluded"):
            self.assertIn(name, status)
        self.assertIn("never pass", schema["description"])

    def test_complete_bundle_is_accepted_only_for_its_candidate(self) -> None:
        self.assertEqual(self.validate(), [])

    def test_negative_control_wrong_commit(self) -> None:
        found = codes(self.validate(commit="c" * 40))
        self.assertIn(MOD.WRONG_COMMIT, found)
        self.assertNotEqual(self.validate(commit="c" * 40), [])

    def test_negative_control_wrong_tree(self) -> None:
        found = codes(self.validate(tree="d" * 40))
        self.assertIn(MOD.WRONG_TREE, found)

    def test_negative_control_skipped_cell(self) -> None:
        bundle = json.loads(json.dumps(self.bundle))
        bundle["cells"][0]["status"] = "skipped"
        found = codes(self.validate(bundle))
        self.assertIn(MOD.SKIPPED_CELL, found)
        self.assertNotEqual(found, set())

    def test_negative_control_unknown_and_excluded_status(self) -> None:
        for status, code in (("unknown", MOD.UNKNOWN_STATUS), ("excluded", MOD.EXCLUDED_CELL)):
            bundle = json.loads(json.dumps(self.bundle))
            bundle["cells"][0]["status"] = status
            self.assertIn(code, codes(self.validate(bundle)))

    def test_negative_control_missing_cell(self) -> None:
        bundle = json.loads(json.dumps(self.bundle))
        bundle["cells"] = bundle["cells"][1:]
        self.assertIn(MOD.MISSING_CELL, codes(self.validate(bundle)))

    def test_negative_control_missing_junit(self) -> None:
        target = self.root / self.bundle["cells"][0]["artifact_dir"] / "junit.xml"
        target.unlink()
        self.assertIn(MOD.MISSING_JUNIT, codes(self.validate()))

    def test_suite_summary_must_match_testcases(self) -> None:
        target = self.root / self.bundle["cells"][0]["artifact_dir"] / "junit.xml"
        original = target.read_bytes()
        mutated = MOD.increment_one_declared_count(original)
        self.assertIn(b'classname="qualification.fixture"', mutated)
        self.assertIn(b'name="test_provider_free"', mutated)
        self.assertEqual(original.count(b"<testcase"), mutated.count(b"<testcase"))
        with self.assertRaises(MOD.JunitSuiteError) as caught:
            MOD.parse_junit(mutated)
        self.assertIn("junit suite tests=", str(caught.exception))
        self.assertIn("does not match", str(caught.exception))
        target.write_bytes(mutated)
        errors = self.validate()
        self.assertIn(MOD.NOT_PASS, codes(errors))
        self.assertTrue(any("junit suite tests=" in item for item in errors))

    def test_junit_skip_is_not_pass(self) -> None:
        cell = json.loads(json.dumps(self.bundle["cells"][0]))
        skipped = (
            b'<?xml version="1.0" encoding="utf-8"?>'
            b"<testsuites><testsuite tests=\"1\">"
            b'<testcase classname="qualification.fixture" name="test_provider_free">'
            b'<skipped message="not this cell" /></testcase>'
            b"</testsuite></testsuites>"
        )
        parsed = MOD.parse_junit(skipped)
        cell["junit"] = parsed
        cell["artifacts"]["junit.xml"] = parsed["sha256"]
        cell["status"] = "pass"
        directory = self.root / cell["artifact_dir"]
        (directory / "junit.xml").write_bytes(skipped)
        bundle = json.loads(json.dumps(self.bundle))
        bundle["cells"][0] = cell
        self.assertIn(MOD.JUNIT_SKIPPED, codes(self.validate(bundle)))

    def test_artifact_and_dependency_digest_mismatch(self) -> None:
        bundle = json.loads(json.dumps(self.bundle))
        bundle["cells"][0]["artifacts"]["junit.xml"] = "sha256:" + "ab" * 32
        self.assertIn(MOD.ARTIFACT_DIGEST, codes(self.validate(bundle)))
        bundle = json.loads(json.dumps(self.bundle))
        bundle["cells"][0]["dependency_digest"] = "sha256:" + "cd" * 32
        self.assertIn(MOD.DEPENDENCY_DIGEST, codes(self.validate(bundle)))

    def test_excluded_tier_cannot_pass(self) -> None:
        bundle = json.loads(json.dumps(self.bundle))
        bundle["cells"].append(
            {
                "cell_id": "linux/3.12/replay",
                "status": "pass",
            }
        )
        self.assertIn(MOD.EXCLUDED_CELL, codes(self.validate(bundle)))

    def test_unknown_cell_is_rejected(self) -> None:
        bundle = json.loads(json.dumps(self.bundle))
        bundle["cells"].append({"cell_id": "hetzner/3.12/provider-free", "status": "pass"})
        self.assertIn(MOD.UNKNOWN_CELL, codes(self.validate(bundle)))

    def test_negative_controls_command_rejects_each_case(self) -> None:
        lines = MOD.run_negative_controls(self.matrix)
        text = "\n".join(lines)
        for name in (
            "wrong-sha",
            "wrong-tree",
            "skipped-cell",
            "missing-junit",
            "missing-cell",
            "suite-counts",
        ):
            self.assertIn(f"negative-control {name}: rejected", text)
        self.assertIn("junit suite tests=", text)
        self.assertIn("does not match", text)

    def test_runner_record_stores_observed_image_not_only_the_scheduled_label(self) -> None:
        spec = MOD.required_cells(self.matrix)[0]
        cell = next(item for item in self.bundle["cells"] if item["cell_id"] == spec["cell_id"])
        runner = json.loads((self.root / cell["artifact_dir"] / "runner.json").read_text(encoding="utf-8"))
        self.assertEqual(set(runner), MOD.RUNNER_KEYS)
        self.assertEqual(runner["scheduled_image"], spec["runner_image"])
        self.assertEqual(cell["runner_image"], spec["runner_image"])
        self.assertNotEqual(runner["image_os"], runner["scheduled_image"])
        for key in ("image_os", "image_version", "platform", "python_runtime", "runner_name"):
            self.assertTrue(runner[key].strip())
        self.assertIn(spec["python"], runner["python_runtime"])
        with self.assertRaises(MOD.QualificationError):
            MOD.build_cell_files(
                spec=spec,
                commit_sha=COMMIT,
                tree_sha=TREE,
                run_id="1000",
                run_attempt="1",
                job_id="2001",
                python_version=f"{spec['python']}.0",
                junit_bytes=(self.root / cell["artifact_dir"] / "junit.xml").read_bytes(),
                dependency_text="pytest==8.3.5\n",
                runner_name="runner",
                runner_os=spec["runner_os"],
                runner_arch=spec["runner_arch"],
                image_os="",
                image_version="observed-image",
                observed_platform=f"{spec['runner_os']}-{spec['runner_arch']}",
                python_runtime=f"{spec['python']}.0 ({spec['platform']})",
            )

    def test_full_run_rejects_missing_cancelled_or_skipped_cells(self) -> None:
        names = [MOD.provider_free_job_name(spec) for spec in MOD.required_cells(self.matrix)]
        self.assertEqual(len(names), 6)
        jobs = [{"name": name, "conclusion": "success"} for name in names]
        self.assertEqual(MOD.incomplete_matrix(jobs, names), [])
        missing = MOD.incomplete_matrix(jobs[:-1], names)
        self.assertTrue(any(item.startswith("missing-cell:") and names[-1] in item for item in missing))
        cancelled = json.loads(json.dumps(jobs))
        cancelled[0]["conclusion"] = "cancelled"
        skipped = json.loads(json.dumps(jobs))
        skipped[1]["conclusion"] = "skipped"
        self.assertTrue(any("conclusion cancelled" in item for item in MOD.incomplete_matrix(cancelled, names)))
        self.assertTrue(any("conclusion skipped" in item for item in MOD.incomplete_matrix(skipped, names)))

    def test_manifest_records_the_bundle_sha256(self) -> None:
        bundle_path = self.tmp / "bundle.json"
        bundle_path.write_bytes(MOD.canonical_json(self.bundle))
        destination = self.tmp / "manifest.json"
        document = MOD.write_manifest(bundle_path, destination)
        self.assertEqual(document["schema"], MOD.MANIFEST_SCHEMA)
        self.assertEqual(document["bundle_path"], "bundle.json")
        self.assertEqual(
            document["bundle_sha256"],
            hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), document)

    def test_real_bundle_negative_controls_use_that_bundle(self) -> None:
        lines = MOD.run_negative_controls(
            self.matrix,
            bundle=self.bundle,
            artifact_root=self.root,
            expected_commit=COMMIT,
            expected_tree=TREE,
        )
        self.assertIn("negative-control missing-junit: rejected", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for tools/check_product_pin.py, plus a check of the committed pin (CSL-585, CSL-796)."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("check_product_pin", ROOT / "tools" / "check_product_pin.py")
pin_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pin_check)

COMMITTED_COMMIT = "d84cdd26a4de2366f3c5d34c4aec5215f822a494"
COMMITTED_SHA256 = "e1d5fb8c02594b2ca88281113ffc14e6f3125daded2a00d45739cfb9d2ed04dd"
COMMITTED_BYTES = 467579
AWAITING = {
    "format": "csl-product-pin/v1",
    "status": "awaiting",
    "repository": "CSL-Laboratories/csl-research",
    "path": None,
    "commit": None,
    "sha256": None,
    "claim": None,
    "limitations": "No contract is pinned yet.",
}
PINNED = {
    **AWAITING,
    "status": "pinned",
    "path": "contracts/recursion/PRODUCT.md",
    "commit": "a" * 40,
    "sha256": "b" * 64,
    "claim": "Recursion product contract, candidate.",
}


class ShapeTests(unittest.TestCase):
    def test_committed_pin_is_valid(self) -> None:
        pin = json.loads(pin_check.PIN.read_text(encoding="utf-8"))
        self.assertEqual(pin_check.errors(pin), [])

    def test_committed_pin_names_expected_bytes(self) -> None:
        # CSL-796: the exact csl-research bytes this PR pins. A re-pin updates both.
        pin = json.loads(pin_check.PIN.read_text(encoding="utf-8"))
        self.assertEqual(pin["status"], "pinned")
        self.assertEqual(pin["path"], "contracts/PRODUCT.md")
        self.assertEqual(pin["commit"], COMMITTED_COMMIT)
        self.assertEqual(pin["sha256"], COMMITTED_SHA256)
        self.assertIn(f"({COMMITTED_BYTES} bytes)", pin["claim"])

    @unittest.skipUnless(os.environ.get("CSL_RESEARCH_VAULT"), "set CSL_RESEARCH_VAULT to a local csl-research clone")
    def test_committed_pin_matches_vault_blob(self) -> None:
        # Re-hash the real pinned blob from a local csl-research clone (no network).
        pin = json.loads(pin_check.PIN.read_text(encoding="utf-8"))
        vault = Path(os.environ["CSL_RESEARCH_VAULT"])
        blob = subprocess.run(
            ["git", "-C", str(vault), "show", f"{COMMITTED_COMMIT}:contracts/PRODUCT.md"],
            check=True,
            capture_output=True,
        ).stdout
        self.assertEqual(len(blob), COMMITTED_BYTES)
        self.assertEqual(hashlib.sha256(blob).hexdigest(), COMMITTED_SHA256)
        self.assertEqual(pin_check.vault_errors(pin, vault), [])

    def test_readme_pointer_matches_committed_pin(self) -> None:
        pin = json.loads(pin_check.PIN.read_text(encoding="utf-8"))
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        line = next(l for l in readme.splitlines() if "contract/PRODUCT.pin.json" in l)
        self.assertIn(pin["commit"], line)
        self.assertIn(pin["sha256"], line)
        # The pinned bytes say "Not governing"; the pointer states values only.
        self.assertNotIn("governing", line.lower().replace("not governing", ""))

    def test_awaiting_and_pinned_examples_pass(self) -> None:
        self.assertEqual(pin_check.errors(AWAITING), [])
        self.assertEqual(pin_check.errors(PINNED), [])

    def test_awaiting_rejects_partial_pin(self) -> None:
        pin = {**AWAITING, "commit": "a" * 40}
        self.assertIn("commit must be null while status is awaiting", pin_check.errors(pin))

    def test_pinned_rejects_short_commit_and_bad_digest(self) -> None:
        pin = {**PINNED, "commit": "abc1234", "sha256": "B" * 64}
        problems = pin_check.errors(pin)
        self.assertIn("commit must be a full 40-hex lowercase SHA", problems)
        self.assertIn("sha256 must be 64 lowercase hex characters", problems)

    def test_pinned_rejects_trailing_newline_in_hex_fields(self) -> None:
        problems = pin_check.errors({**PINNED, "commit": "a" * 40 + "\n", "sha256": "b" * 64 + "\n"})
        self.assertIn("commit must be a full 40-hex lowercase SHA", problems)
        self.assertIn("sha256 must be 64 lowercase hex characters", problems)

    def test_pinned_rejects_escaping_paths(self) -> None:
        for path in ("/etc/passwd", "../x.md", "a/../b.md", "a\\b.md", "./a.md"):
            with self.subTest(path=path):
                self.assertIn(
                    "path must be a relative posix path inside the repository",
                    pin_check.errors({**PINNED, "path": path}),
                )

    def test_pinned_rejects_nul_control_and_surrogate_paths(self) -> None:
        for path in (*VaultTests.BAD_PATHS, "a\nb.md", "a\x1fb.md", "a\x7fb.md", "a\udfffb.md"):
            with self.subTest(path=ascii(path)):
                self.assertIn(
                    "path must not contain NUL, control characters or lone surrogates",
                    pin_check.errors({**PINNED, "path": path}),
                )

    def test_rejects_other_repository_unknown_keys_and_status(self) -> None:
        pin = copy.deepcopy(PINNED)
        pin["repository"] = "CSL-Laboratories/recursion"
        pin["accepted"] = True
        problems = pin_check.errors(pin)
        self.assertIn("unknown keys: accepted", problems)
        self.assertTrue(any(p.startswith("repository must be") for p in problems))
        self.assertIn("status must be 'awaiting' or 'pinned'", pin_check.errors({**AWAITING, "status": "accepted"}))

    def test_rejects_blank_limitations_and_claim(self) -> None:
        self.assertIn("limitations must be a non-empty string", pin_check.errors({**AWAITING, "limitations": " "}))
        self.assertIn("claim must be a non-empty string", pin_check.errors({**PINNED, "claim": ""}))


class VaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        git = ["git", "-C", str(self.vault), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        subprocess.run([*git[:3], "init", "-q"], check=True)
        target = self.vault / "contracts" / "recursion" / "PRODUCT.md"
        target.parent.mkdir(parents=True)
        self.body = b"# Product\n"
        target.write_bytes(self.body)
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "contract"], check=True)
        self.commit = subprocess.check_output([*git[:3], "rev-parse", "HEAD"], text=True).strip()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def pin(self, digest: str) -> dict:
        return {**PINNED, "commit": self.commit, "sha256": digest}

    def test_matching_bytes_pass(self) -> None:
        digest = hashlib.sha256(self.body).hexdigest()
        self.assertEqual(pin_check.vault_errors(self.pin(digest), self.vault), [])

    def test_mismatch_and_missing_path_fail(self) -> None:
        self.assertTrue(pin_check.vault_errors(self.pin("0" * 64), self.vault)[0].startswith("sha256 mismatch"))
        missing = {**self.pin("0" * 64), "path": "contracts/recursion/NOPE.md"}
        self.assertTrue(pin_check.vault_errors(missing, self.vault)[0].startswith("cannot read"))

    BAD_PATHS = ("contracts/recursion/PRO\u0000DUCT.md", "contracts/recursion/\ud800.md")

    def test_nul_and_lone_surrogate_paths_fail_cleanly_in_process(self) -> None:
        # A JSON \u0000 or lone \ud800 in path must be rejected, not raise.
        digest = hashlib.sha256(self.body).hexdigest()
        for path in self.BAD_PATHS:
            with self.subTest(path=ascii(path)):
                pin = {**self.pin(digest), "path": path}
                try:
                    problems = pin_check.errors(pin) or pin_check.vault_errors(pin, self.vault)
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"checker raised {type(exc).__name__}: {exc}")
                self.assertTrue(problems, "bad path was accepted")

    def test_nul_and_lone_surrogate_paths_fail_cleanly_on_cli(self) -> None:
        digest = hashlib.sha256(self.body).hexdigest()
        for path in self.BAD_PATHS:
            with self.subTest(path=ascii(path)):
                pin_file = self.vault / "bad.pin.json"
                pin_file.write_text(json.dumps({**self.pin(digest), "path": path}), encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, "-B", str(ROOT / "tools" / "check_product_pin.py"),
                     "--pin", str(pin_file), "--vault", str(self.vault)],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("Traceback", result.stderr, result.stderr)
                self.assertTrue(result.stdout.startswith("FAIL: "), result.stdout + result.stderr)

    def test_awaiting_needs_no_vault(self) -> None:
        self.assertEqual(pin_check.vault_errors(AWAITING, self.vault / "absent"), [])

    def test_nul_and_surrogate_paths_fail_without_raising(self) -> None:
        for path in self.BAD_PATHS:
            with self.subTest(path=ascii(path)):
                problems = pin_check.vault_errors({**self.pin("0" * 64), "path": path}, self.vault)
                self.assertEqual(len(problems), 1)
                self.assertTrue(problems[0].startswith("cannot read"))
                problems[0].encode("utf-8")  # printable on a UTF-8 stdout


if __name__ == "__main__":
    unittest.main()

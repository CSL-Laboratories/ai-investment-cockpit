"""The vendored contract is the pinned contract, and a drifted copy fails closed."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from successor.route_consumer import pin
from successor.route_consumer.tests.support import recursion_bridge as bridge

EXPECTED_HEAD = "aa34e9a77d91c0b7d809ebc969d8111372cf1a01"
EXPECTED_BASE_HEAD = "b625072ae24c4334236f3c5f0919f1d7375fbee0"
EXPECTED_SCHEMA_SHA = "6b3e775273b70df78f7a4ec986672191165228078dd5e493a4a4e0cb18cc4820"


class VendoredPinTests(unittest.TestCase):
    def test_pin_names_the_requested_heads(self) -> None:
        source = pin.load_pin()["source"]
        self.assertEqual("CSL-Laboratories/recursion", source["repository"])
        self.assertEqual((302, EXPECTED_HEAD), (source["pull_request"], source["head_sha"]))
        self.assertEqual((301, EXPECTED_BASE_HEAD),
                         (source["stacked_on"]["pull_request"], source["stacked_on"]["head_sha"]))
        for sha in (source["head_sha"], source["stacked_on"]["head_sha"]):
            self.assertRegex(sha, r"^[0-9a-f]{40}$")
        self.assertEqual(
            EXPECTED_SCHEMA_SHA, pin.load_pin()["vendored"][pin.SCHEMA_KEY]["sha256"]
        )

    def test_vendored_bytes_match_the_pin(self) -> None:
        pin.verify_vendored()
        self.assertEqual({pin.SCHEMA_KEY, pin.VECTORS_KEY}, set(pin.load_pin()["vendored"]))

    def test_only_data_is_vendored_no_recursion_code(self) -> None:
        self.assertEqual([], sorted(p.name for p in (pin.PACKAGE_DIR / "contract").glob("*.py")))

    def test_every_manifest_entry_is_a_sha256(self) -> None:
        manifest = pin.load_pin()["subject_manifest"]
        self.assertGreaterEqual(len(manifest), 15)
        for path, digest in manifest.items():
            self.assertRegex(digest, r"^[0-9a-f]{64}$", path)

    def test_a_single_flipped_byte_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp)
            shutil.copytree(pin.PACKAGE_DIR / "contract", copy / "contract")
            target = copy / pin.SCHEMA_KEY
            data = bytearray(target.read_bytes())
            data[len(data) // 2] ^= 1
            target.write_bytes(bytes(data))
            with mock.patch.object(pin, "PACKAGE_DIR", copy):
                with self.assertRaises(pin.PinError):
                    pin.read_vendored(pin.SCHEMA_KEY)
                with self.assertRaises(pin.PinError):
                    pin.verify_vendored()

    def test_unpinned_artifacts_are_refused(self) -> None:
        with self.assertRaises(pin.PinError):
            pin.read_vendored("contract/PIN.json")

    def test_loaded_schema_defines_the_records_the_consumer_uses(self) -> None:
        defs = pin.schema_defs()
        for name in ("RouteExecutionRequest", "RouteExecutionResult", "RouteRefusal",
                     "AdapterSupport", "RouteHealthEvent", "ProcessTerminationObservation"):
            self.assertIn(name, defs)
        self.assertRegex(pin.load_schema()["title"], r"Route Contract v0")


class CheckoutPinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = bridge.require_checkout()

    def test_checkout_is_the_pinned_one(self) -> None:
        bridge.verify_checkout(self.root)

    def test_vendored_files_equal_the_source_files(self) -> None:
        for key, entry in pin.load_pin()["vendored"].items():
            self.assertEqual(
                (self.root / entry["source_path"]).read_bytes(),
                (pin.PACKAGE_DIR / key).read_bytes(),
                key,
            )

    def test_any_edited_subject_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "recursion"
            for relative in pin.load_pin()["subject_manifest"]:
                (copy / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.root / relative, copy / relative)
            for key, entry in pin.load_pin()["vendored"].items():
                (copy / entry["source_path"]).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.root / entry["source_path"], copy / entry["source_path"])
            bridge.verify_checkout(copy)
            victim = copy / "scripts/v3/route_contract/executor.py"
            victim.write_text(victim.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
            with self.assertRaises(bridge.CheckoutError):
                bridge.verify_checkout(copy)

    def test_head_matches_when_the_checkout_is_a_git_tree(self) -> None:
        if not (self.root / ".git").exists():
            self.skipTest("checkout is not a git tree; file digests above are the proof")
        head = bridge.checkout_head(self.root)
        self.assertRegex(head, r"^[0-9a-f]{40}$", "git rev-parse HEAD failed on the checkout")
        self.assertEqual(pin.pinned_head(), head)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()


class CheckoutHeadTests(unittest.TestCase):
    """verify_checkout compares the real git HEAD on a plain clone, a branch and a worktree."""

    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.repo = base / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "README").write_text("x", encoding="utf-8")
        _git(self.repo, "add", "README")
        _git(self.repo, "commit", "-q", "-m", "one")
        self.first = _git(self.repo, "rev-parse", "HEAD")
        (self.repo / "README").write_text("y", encoding="utf-8")
        _git(self.repo, "commit", "-q", "-am", "two")
        self.second = _git(self.repo, "rev-parse", "HEAD")
        _git(self.repo, "worktree", "add", "-q", "--detach", str(base / "wt"), self.first)
        self.worktree = base / "wt"

    def verify_with_pin(self, root: Path, pinned: str) -> None:
        with mock.patch.object(pin, "load_pin", return_value={
            "subject_manifest": {}, "vendored": {}, "source": {"head_sha": pinned},
        }), mock.patch.object(pin, "pinned_head", return_value=pinned):
            bridge.verify_checkout(root)

    def test_worktree_has_a_gitfile_and_head_is_read_through_git(self) -> None:
        self.assertTrue((self.worktree / ".git").is_file())
        self.assertEqual(self.first, bridge.checkout_head(self.worktree))

    def test_a_matching_head_passes_on_a_branch_checkout_and_a_worktree(self) -> None:
        self.assertTrue((self.repo / ".git").is_dir())
        self.assertTrue(_git(self.repo, "symbolic-ref", "HEAD").startswith("refs/heads/"))
        self.verify_with_pin(self.repo, self.second)
        self.verify_with_pin(self.worktree, self.first)

    def test_a_head_that_is_not_the_pin_is_refused_on_both(self) -> None:
        for root, other in ((self.repo, self.first), (self.worktree, self.second)):
            with self.assertRaises(bridge.CheckoutError) as caught:
                self.verify_with_pin(root, other)
            self.assertIn("is not the pin", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)

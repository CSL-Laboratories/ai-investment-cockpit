"""Regression tests for the checker test-inventory floor (CSL-511 WP4).

    python -X utf8 -B tools/test_checker_test_floor.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_checker_test_floor as floor

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"

DIRECT_COMMANDS = (
    "python -X utf8 -B tools/check_replacement_program.py",
    "python -X utf8 -B tools/check_o7_operator_contract.py",
    "python -X utf8 -B -m unittest -q tools.test_replacement_program",
    "python -X utf8 -B tools/test_checker_test_floor.py",
    "python -X utf8 -B tools/check_checker_test_floor.py",
)


def _tree(tmp: Path, paths: tuple[str, ...]) -> None:
    for rel in paths:
        target = tmp / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n", encoding="utf-8")


def _counts(value: int):
    return {"pytest": lambda root, f: value, "unittest": lambda root, f: value}


class FloorLogicTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _tree(self.root, floor.REQUIRED_FILES + tuple(f.path for f in floor.FLOORS))

    def run_check(self, value: int = 10_000) -> list[str]:
        return floor.check(self.root, counters=_counts(value))

    def test_complete_tree_at_or_above_floor_passes(self) -> None:
        self.assertEqual(self.run_check(), [])

    def test_deleting_the_registry_test_fails_closed(self) -> None:
        (self.root / "app/tests/test_replacement_program_registry.py").unlink()
        problems = self.run_check()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("test_replacement_program_registry.py is missing", problems[0])

    def test_deleting_the_o7_test_fails_closed(self) -> None:
        (self.root / "app/tests/test_o7_operator_contract.py").unlink()
        self.assertTrue(any("test_o7_operator_contract.py is missing" in p for p in self.run_check()))

    def test_deleting_a_checker_or_program_fails_closed(self) -> None:
        for rel in floor.REQUIRED_FILES:
            with self.subTest(rel=rel):
                path = self.root / rel
                data = path.read_bytes()
                path.unlink()
                self.assertTrue(any(f"{rel} is missing" in p for p in self.run_check()))
                path.write_bytes(data)

    def test_symlinked_test_file_fails_closed(self) -> None:
        path = self.root / "app/tests/test_o7_operator_contract.py"
        path.unlink()
        path.symlink_to(self.root / "tools/check_o7_operator_contract.py")
        self.assertTrue(any("is a symlink" in p for p in self.run_check()))

    def test_thinned_test_file_fails_closed(self) -> None:
        for entry in floor.FLOORS:
            with self.subTest(path=entry.path):
                counters = {"pytest": lambda r, f: f.minimum, "unittest": lambda r, f: f.minimum}
                counters[entry.runner] = lambda r, f, e=entry: f.minimum - 1 if f.path == e.path else f.minimum
                problems = floor.check(self.root, counters=counters)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("below its floor", problems[0])

    def test_collection_error_fails_closed(self) -> None:
        def boom(root: Path, entry: floor.Floor) -> int:
            raise RuntimeError("collection failed")

        problems = floor.check(self.root, counters={"pytest": boom, "unittest": boom})
        self.assertEqual(len(problems), len(floor.FLOORS))

    def test_unknown_runner_fails_closed(self) -> None:
        problems = floor.check(self.root, floors=(floor.Floor("tools/check_replacement_program.py", "nope", 1),))
        self.assertTrue(any("unknown runner" in p for p in problems))


class PinnedInventoryTests(unittest.TestCase):
    def test_floors_cover_both_app_tests_and_the_tools_test(self) -> None:
        paths = {f.path for f in floor.FLOORS}
        self.assertIn("app/tests/test_replacement_program_registry.py", paths)
        self.assertIn("app/tests/test_o7_operator_contract.py", paths)
        self.assertIn("tools/test_replacement_program.py", paths)
        self.assertTrue(all(f.minimum > 0 for f in floor.FLOORS))

    def test_real_tree_meets_every_floor(self) -> None:
        for entry in floor.FLOORS:
            with self.subTest(path=entry.path):
                self.assertIsNone(floor._plain_file_problem(ROOT, entry.path))
                self.assertGreaterEqual(floor.COUNTERS[entry.runner](ROOT, entry), entry.minimum)

    def test_ci_runs_the_checkers_directly_in_the_linux_job(self) -> None:
        text = CI.read_text(encoding="utf-8")
        start = text.index("\n  linux:\n")
        end = text.index("\n  linux-gates:\n")
        linux = text[start:end]
        for command in DIRECT_COMMANDS:
            self.assertIn(command, linux, command)
        self.assertIn("\n    name: linux\n", linux)
        self.assertIn("\n    name: linux-gates\n", text[end:])


if __name__ == "__main__":
    unittest.main()

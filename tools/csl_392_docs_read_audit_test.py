"""Regression tests for the CSL-392 open-audit tool, with negative controls.

    python -X utf8 -B tools/csl_392_docs_read_audit_test.py

Not named tools/test_*.py on purpose: that glob needs founder-approved allow
rules in .claude/settings.json (app/tests/test_claude_settings.py), and a new
app/tests file needs an X1 inventory row. Neither belongs in this audit PR.
Not run by CI; the linux job's command list is not edited from this issue.

The hook tests run a child Python against a throwaway git repo with the tool
copied in as sitecustomize, so they exercise the same path the runner uses.
"""
from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import csl_392_docs_read_audit as audit

TOOL = Path(audit.__file__).resolve()

# Synthetic decide patterns: run, run, skip. Kept local so these tests do not
# depend on the live filter's current contents.
PATTERNS = (r"\.py$", r"^app/|^AGENTS\.md$", r"^docs/|\.md$")

CHILD = r"""
import io, os, pathlib, subprocess, sys
for name in ("tracked", "untracked"):
    with open(f"{name}_open.md", encoding="utf-8") as handle:
        handle.read()
    with io.open(f"{name}_io.md", encoding="utf-8") as handle:
        handle.read()
    pathlib.Path(f"{name}_path.md").read_text(encoding="utf-8")
    os.close(os.open(f"{name}_os.md", os.O_RDONLY))
subprocess.run(["git", "--version"], check=True, capture_output=True)
subprocess.run([sys.executable, "-I", "-c", "pass"], check=True)
subprocess.run([sys.executable, "-c", "pass"], check=True)
"""


class HookTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.repo = base / "repo"
        self.hook_dir = base / "hook"
        self.repo.mkdir()
        self.hook_dir.mkdir()
        (self.hook_dir / "sitecustomize.py").write_bytes(TOOL.read_bytes())
        for kind in ("open", "io", "path", "os"):
            (self.repo / f"tracked_{kind}.md").write_text("t\n", encoding="utf-8")
            (self.repo / f"untracked_{kind}.md").write_text("u\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "add", *(f"tracked_{kind}.md" for kind in ("open", "io", "path", "os"))],
            cwd=self.repo,
            check=True,
        )
        self.log = base / "opens.jsonl"

    def run_child(self, *, with_log: bool) -> list[dict]:
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("CSL392_", "PYTEST_"))
        }
        env["PYTHONPATH"] = str(self.hook_dir)
        env["CSL392_REPO"] = str(self.repo)
        if with_log:
            env["CSL392_AUDIT_LOG"] = str(self.log)
        subprocess.run(
            [sys.executable, "-B", "-c", CHILD], cwd=self.repo, env=env, check=True
        )
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def test_records_every_open_family_for_tracked_files_only(self):
        rows = self.run_child(with_log=True)
        opened = {(row["path"], row["op"]) for row in rows if "path" in row}
        self.assertEqual(
            opened,
            {
                ("tracked_open.md", "open"),
                ("tracked_io.md", "io.open"),
                ("tracked_path.md", "io.open"),
                ("tracked_os.md", "os.open"),
            },
        )
        # Negative control: the untracked twins were opened the same way.
        self.assertFalse([row for row in rows if "untracked" in row.get("path", "")])

    def test_records_child_processes_and_whether_they_load_the_hook(self):
        rows = self.run_child(with_log=True)
        children = {(row["exec"], row["python"], row["hooked"]) for row in rows if "exec" in row}
        python = os.path.basename(sys.executable)
        self.assertIn(("git", False, False), children)
        self.assertIn((python, True, False), children)  # -I drops PYTHONPATH
        self.assertIn((python, True, True), children)

    def test_no_log_env_records_nothing(self):
        self.assertEqual(self.run_child(with_log=False), [])


class HookGuardTests(unittest.TestCase):
    def test_other_thread_records_while_this_thread_is_in_the_hook(self):
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            (repo / "other.md").write_text("o\n", encoding="utf-8")
            (repo / "busy.md").write_text("b\n", encoding="utf-8")
            subprocess.run(["git", "add", "other.md", "busy.md"], cwd=repo, check=True)
            log = repo / "opens.jsonl"
            saved_seen = set(audit._SEEN)
            saved_tracked = audit._TRACKED
            saved_env = {
                key: os.environ.get(key) for key in ("CSL392_AUDIT_LOG", "CSL392_REPO")
            }
            audit._SEEN.clear()
            audit._TRACKED = None
            os.environ["CSL392_AUDIT_LOG"] = str(log)
            os.environ["CSL392_REPO"] = str(repo)
            audit._hook_local.busy = True
            try:
                errors: list[BaseException] = []

                def worker():
                    try:
                        audit._record(str(repo / "other.md"), "open")
                        audit._record_exec([sys.executable, "-c", "pass"], {})
                    except BaseException as exc:  # noqa: BLE001 - fail the test, don't hang
                        errors.append(exc)

                thread = threading.Thread(target=worker)
                thread.start()
                thread.join()
                self.assertEqual(errors, [])
                audit._record(str(repo / "busy.md"), "open")
                rows = [
                    json.loads(line)
                    for line in log.read_text(encoding="utf-8").splitlines()
                ]
            finally:
                audit._hook_local.busy = False
                audit._SEEN.clear()
                audit._SEEN.update(saved_seen)
                audit._TRACKED = saved_tracked
                for key, value in saved_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
        paths = [row["path"] for row in rows if "path" in row]
        self.assertEqual(paths, ["other.md"])
        self.assertTrue(any(row.get("exec") for row in rows))


class UnittestNameTests(unittest.TestCase):
    def test_main_script_tests_are_named_by_file(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            hook = base / "hook"
            hook.mkdir()
            (hook / "sitecustomize.py").write_bytes(TOOL.read_bytes())
            subprocess.run(["git", "init", "-q"], cwd=base, check=True)
            (base / "note.md").write_text("n\n", encoding="utf-8")
            (base / "test_probe.py").write_text(
                "import unittest\n"
                "class Probe(unittest.TestCase):\n"
                "    def test_reads(self):\n"
                "        open('note.md', encoding='utf-8').close()\n"
                "unittest.main()\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "note.md", "test_probe.py"], cwd=base, check=True)
            log = base / "opens.jsonl"
            env = {k: v for k, v in os.environ.items() if not k.startswith(("CSL392_", "PYTEST_"))}
            env.update(PYTHONPATH=str(hook), CSL392_REPO=str(base), CSL392_AUDIT_LOG=str(log))
            subprocess.run(
                [sys.executable, "-B", "test_probe.py"], cwd=base, env=env, check=True,
                capture_output=True,
            )
            rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            self.assertIn(
                {"test": "test_probe.py::Probe.test_reads", "path": "note.md", "op": "open"}, rows
            )


class ClassifyTests(unittest.TestCase):
    def write_log(self, rows: list[dict]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = Path(tmp.name) / "opens.jsonl"
        log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return log

    def test_skip_pattern_path_is_a_mismatch(self):
        log = self.write_log([
            {"test": "tests/test_a.py::test_x", "path": "docs/guide.md", "op": "open"},
            {"test": "tests/test_a.py::test_x", "path": "app/x.py", "op": "open"},
            {"test": "tests/test_b.py::test_y", "path": "AGENTS.md", "op": "io.open"},
        ])
        result = audit.classify(log, PATTERNS)
        self.assertEqual([item["path"] for item in result["mismatches"]], ["docs/guide.md"])
        self.assertEqual(
            [item["path"] for item in result["opened_run_tests"]], ["AGENTS.md", "app/x.py"]
        )

    def test_run_pattern_wins_over_skip_pattern(self):
        self.assertFalse(audit.skipped_by_filter("AGENTS.md", PATTERNS))
        self.assertTrue(audit.skipped_by_filter("README.md", PATTERNS))
        self.assertFalse(audit.skipped_by_filter("LICENSE", PATTERNS))

    def test_classify_cli_exits_2_on_mismatch_and_0_when_clean(self):
        bad = self.write_log([{"test": "t", "path": "docs/guide.md", "op": "open"}])
        good = self.write_log([{"test": "t", "path": "app/x.py", "op": "open"}])
        with mock.patch.object(audit, "filter_patterns", return_value=PATTERNS), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(audit.main(["classify", str(bad)]), 2)
            self.assertEqual(audit.main(["classify", str(good)]), 0)

    def test_unhooked_children_are_reported_by_test_file(self):
        log = self.write_log([
            {"test": "tests/test_a.py::test_x", "exec": "git", "python": False, "hooked": False},
            {"test": "tools/test_b.py:12", "exec": "git", "python": False, "hooked": False},
            {"test": "tests/test_a.py::test_y", "exec": "python3", "python": True, "hooked": True},
        ])
        result = audit.classify(log, PATTERNS)
        self.assertEqual(
            result["unhooked_children"],
            [{"exec": "git", "test_files": ["tests/test_a.py", "tools/test_b.py"]}],
        )


class PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ci = audit._load_yaml(audit.CI)
        cls.action = audit._load_yaml(audit.RUN_GATES)

    def plan(self, ci=None, action=None):
        return audit.plan_linux_commands(ci or self.ci, action or self.action)

    def linux_step(self, ci: dict, name: str) -> dict:
        return next(step for step in ci["jobs"]["linux"]["steps"] if step.get("name") == name)

    def test_live_plan_covers_every_linux_python_command(self):
        commands, excluded = self.plan()
        planned = {(cwd, " ".join(argv)) for cwd, argv in commands}
        self.assertIn(("app", "python -m pytest -q"), planned)
        self.assertIn((".", "python -X utf8 -B tools/test_run_gates_action.py"), planned)
        self.assertEqual(len(excluded), len(audit.EXCLUDED))

    def test_added_test_command_is_run(self):
        ci = copy.deepcopy(self.ci)
        step = self.linux_step(ci, "CI dispatch scope regressions")
        step["run"] += "python -X utf8 -B tools/test_new_thing.py\n"
        planned = {" ".join(argv) for _, argv in self.plan(ci)[0]}
        self.assertIn("python -X utf8 -B tools/test_new_thing.py", planned)

    def test_removed_test_command_is_not_run(self):
        ci = copy.deepcopy(self.ci)
        step = self.linux_step(ci, "CI dispatch scope regressions")
        step["run"] = step["run"].replace("python -X utf8 -B tools/test_ci_settle.py\n", "")
        planned = {" ".join(argv) for _, argv in self.plan(ci)[0]}
        self.assertNotIn("python -X utf8 -B tools/test_ci_settle.py", planned)

    def test_added_unrecognised_python_command_fails(self):
        for line in (
            "python -X utf8 -B scripts/ci/new_reader.py",
            "python -m pytest -q tools | tee out.txt",
            "python -I -X utf8 -B tools/test_isolated.py",
            "pytest -q",
        ):
            with self.subTest(line=line):
                ci = copy.deepcopy(self.ci)
                step = self.linux_step(ci, "CI dispatch scope regressions")
                step["run"] += line + "\n"
                with self.assertRaises(SystemExit) as caught:
                    self.plan(ci)
                self.assertIn("uncovered", str(caught.exception))

    def test_removed_excluded_command_fails_as_stale(self):
        ci = copy.deepcopy(self.ci)
        step = self.linux_step(ci, "Install maturin for batch parity")
        step["run"] = "echo skipped\n"
        with self.assertRaises(SystemExit) as caught:
            self.plan(ci)
        self.assertIn("stale_exclusions", str(caught.exception))

    def test_run_gates_command_added_or_dropped(self):
        action = copy.deepcopy(self.action)
        action["runs"]["steps"].append({"run": "python -X utf8 -B tools/test_gate_extra.py"})
        planned = {" ".join(argv) for _, argv in self.plan(action=action)[0]}
        self.assertIn("python -X utf8 -B tools/test_gate_extra.py", planned)
        action = copy.deepcopy(self.action)
        action["runs"]["steps"] = [
            step for step in action["runs"]["steps"] if step.get("run") != "python -m pytest -q"
        ]
        with self.assertRaises(SystemExit):
            self.plan(action=action)

    def test_missing_run_gates_call_fails(self):
        ci = copy.deepcopy(self.ci)
        ci["jobs"]["linux"]["steps"] = [
            step for step in ci["jobs"]["linux"]["steps"]
            if step.get("uses") != audit.RUN_GATES_USES
        ]
        with self.assertRaises(SystemExit):
            self.plan(ci)


class RelativeLogTests(unittest.TestCase):
    def test_relative_log_path_is_shared_across_suite_directories(self):
        previous = os.getcwd()
        with tempfile.TemporaryDirectory() as raw:
            os.chdir(raw)
            try:
                relative = Path("opens.jsonl")
                commands = [
                    (".", ["python", "-X", "utf8", "-B", "tools/test_ci_docs_filter.py"]),
                    ("app", ["python", "-m", "pytest", "-q"]),
                ]

                def fake_run(command, env, cwd):
                    target = Path(env["CSL392_AUDIT_LOG"])
                    if not target.is_absolute():
                        target = Path(cwd) / target
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("a", encoding="utf-8") as handle:
                        handle.write(
                            json.dumps(
                                {"test": str(cwd), "path": "docs/guide.md", "op": "open"}
                            )
                            + "\n"
                        )
                    return 0

                with mock.patch.object(
                    audit, "plan_linux_commands", return_value=(commands, [])
                ), mock.patch.object(audit, "_run", side_effect=fake_run), mock.patch.object(
                    audit, "_python", return_value=sys.executable
                ):
                    codes, _excluded = audit.run_linux_suites(relative)
                rows = [
                    json.loads(line)
                    for line in relative.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            finally:
                os.chdir(previous)
        self.assertEqual(sorted(codes), [
            "(.) python -X utf8 -B tools/test_ci_docs_filter.py",
            "(app) python -m pytest -q",
        ])
        self.assertEqual(
            [row["test"] for row in rows],
            [str(audit.ROOT.resolve()), str((audit.ROOT / "app").resolve())],
        )
        self.assertFalse((audit.ROOT / "app" / "opens.jsonl").exists())


class MainExitTests(unittest.TestCase):
    def run_main(self, codes: dict[str, int], rows: list[dict]) -> tuple[int, dict]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = Path(tmp.name) / "opens.jsonl"
        summary = Path(tmp.name) / "summary.json"

        def fake_run(log_path: Path):
            log_path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            return codes, []

        with mock.patch.object(audit, "run_linux_suites", side_effect=fake_run), \
                mock.patch.object(audit, "filter_patterns", return_value=PATTERNS), \
                mock.patch.dict(os.environ, {"CSL392_AUDIT_SUMMARY": str(summary)}), \
                redirect_stdout(io.StringIO()):
            code = audit.main([str(log)])
        return code, json.loads(summary.read_text(encoding="utf-8"))

    def test_clean_complete_run_exits_0(self):
        code, summary = self.run_main({"a": 0, "b": 0}, [])
        self.assertEqual(code, 0)
        self.assertTrue(summary["complete"])

    def test_failed_suite_is_incomplete_not_clean(self):
        code, summary = self.run_main({"a": 0, "(app) python -m pytest -q": 2}, [])
        self.assertEqual(code, 1)
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["failed_suites"], ["(app) python -m pytest -q"])

    def test_mismatch_exits_2_even_when_incomplete(self):
        rows = [{"test": "t", "path": "docs/guide.md", "op": "open"}]
        code, summary = self.run_main({"a": 1}, rows)
        self.assertEqual(code, 2)
        self.assertFalse(summary["complete"])


if __name__ == "__main__":
    unittest.main()

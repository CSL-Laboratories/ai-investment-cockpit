"""Offline run-gates action regressions; no GitHub calls or hosted jobs.

Run: python -m unittest tools.test_run_gates_action -v
"""
import importlib.util
from pathlib import Path
import os
import stat
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ORIGINAL_RUN = subprocess.run
ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / '.github' / 'actions' / 'run-gates' / 'action.yml'
REQUIREMENTS = ROOT / '.github' / 'actions' / 'run-gates' / 'requirements.txt'

# Same direct package set as the previous unpinned pip install line, plus the
# uvicorn[standard] extra that line already requested.
DIRECT_PACKAGES = (
    'pydantic',
    'pydantic-settings',
    'keyring',
    'pyyaml',
    'jsonschema',
    'sqlalchemy',
    'pyarrow',
    'duckdb',
    'fastapi',
    'uvicorn[standard]',
    'httpx',
    'websockets',
    'apscheduler',
    'boto3',
    'discord.py',
    'pytest',
    'ruff',
)


class RunGatesActionTests(unittest.TestCase):
    def setUp(self):
        self.action = ACTION.read_text(encoding='utf-8')
        self.requirements = REQUIREMENTS.read_text(encoding='utf-8')

    def test_installs_from_pinned_requirements_and_keys_caches_on_it(self):
        self.assertIn('cache: pip', self.action)
        self.assertIn(
            'cache-dependency-path: .github/actions/run-gates/requirements.txt',
            self.action,
        )
        self.assertNotIn('app/pyproject.toml', self.action)
        self.assertNotIn('uses: actions/cache@', self.action)
        self.assertNotIn('cache-hit', self.action)
        self.assertIn(
            'python -P .github/actions/run-gates/install.py',
            self.action,
        )
        self.assertNotIn('pip install -U pip pydantic', self.action)
        self.assertIn('python -m pytest -q', self.action)
        self.assertIn('ruff check .', self.action)

    def test_requirements_pin_exactly_the_ci_package_set(self):
        pins = []
        for line in self.requirements.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            pins.append(stripped)
        names = []
        for pin in pins:
            self.assertIn('==', pin, pin)
            name, version = pin.split('==', 1)
            self.assertTrue(version and version[0].isdigit(), pin)
            names.append(name)
        self.assertEqual(names, list(DIRECT_PACKAGES))


SPEC = importlib.util.spec_from_file_location('gate_install', ACTION.with_name('install.py'))
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


class FreshEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / 'checkout'
        self.root.mkdir()
        self.runner_temp = base / 'runner-temp'
        self.runner_temp.mkdir()
        # Hermetic: ambient RUNNER_TEMP on a developer box must not leak in.
        self.env_patch = patch.dict(os.environ, {'RUNNER_TEMP': str(self.runner_temp)})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.create_patch = patch.object(INSTALLER.venv, 'EnvBuilder')
        self.builder = self.create_patch.start()
        self.addCleanup(self.create_patch.stop)
        self.run_patch = patch.object(INSTALLER.subprocess, 'run')
        self.run = self.run_patch.start()
        self.addCleanup(self.run_patch.stop)

    def test_every_invocation_recreates_even_when_download_cache_hits(self):
        INSTALLER.install(self.root)
        INSTALLER.install(self.root)
        self.assertEqual(self.builder.call_count, 2)
        for call in self.builder.call_args_list:
            self.assertEqual(call.kwargs, dict(with_pip=True, clear=True))
        self.assertEqual(self.builder.return_value.create.call_count, 2)
        self.assertEqual(len(self.run.call_args_list), 4)
        self.assertEqual(self.run.call_args_list[1].args[0][-2:], ['pip', 'check'])

    def test_install_failure_recreates_and_bypasses_corrupt_cache(self):
        self.run.side_effect = [subprocess.CalledProcessError(1, 'pip'), None, None]
        INSTALLER.install(self.root)
        self.assertEqual(self.builder.call_count, 2)
        self.assertNotIn('--no-cache-dir', self.run.call_args_list[0].args[0])
        self.assertIn('--no-cache-dir', self.run.call_args_list[1].args[0])
        self.assertEqual(self.run.call_args_list[2].args[0][-2:], ['pip', 'check'])

    def test_broken_dependency_graph_also_retries_from_empty_environment(self):
        self.run.side_effect = [None, subprocess.CalledProcessError(1, 'check'), None, None]
        INSTALLER.install(self.root)
        self.assertEqual(self.builder.call_count, 2)
        self.assertIn('--no-cache-dir', self.run.call_args_list[2].args[0])

    def test_second_failure_stops_before_gates(self):
        self.run.side_effect = subprocess.CalledProcessError(1, 'pip')
        with self.assertRaises(subprocess.CalledProcessError):
            INSTALLER.install(self.root)
        self.assertEqual(self.builder.call_count, 2)
        self.assertEqual(self.run.call_count, 2)

    def test_second_dependency_check_failure_remains_fatal(self):
        self.run.side_effect = [None, subprocess.CalledProcessError(1, 'check'),
                               None, subprocess.CalledProcessError(1, 'check')]
        with self.assertRaises(subprocess.CalledProcessError):
            INSTALLER.install(self.root)
        self.assertEqual(self.run.call_count, 4)

    def test_environment_creation_failure_never_runs_pip(self):
        self.builder.return_value.create.side_effect = OSError('cannot create venv')
        with self.assertRaises(OSError):
            INSTALLER.install(self.root)
        self.run.assert_not_called()

    def test_symlink_environment_is_not_cleared(self):
        with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFLNK)):
            with self.assertRaises(ValueError):
                INSTALLER.install(self.root)
        self.builder.assert_not_called()
        self.run.assert_not_called()

    def test_windows_reparse_point_is_rejected_on_python_311(self):
        junction = SimpleNamespace(st_mode=stat.S_IFDIR,
                                   st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        with patch.object(Path, 'lstat', return_value=junction):
            with self.assertRaises(ValueError):
                INSTALLER.install(self.root)
        self.builder.assert_not_called()
        self.run.assert_not_called()

    def test_resolved_environment_must_match_checkout_child(self):
        with patch.object(Path, 'resolve', side_effect=[self.root / 'elsewhere', self.root]):
            with self.assertRaises(ValueError):
                INSTALLER.install(self.root)
        self.builder.assert_not_called()

    def test_retry_revalidates_environment_before_clearing(self):
        self.run.side_effect = subprocess.CalledProcessError(1, 'pip')
        with patch.object(INSTALLER, 'validate_environment',
                          side_effect=[None, ValueError('became a junction')]) as validate:
            with self.assertRaises(ValueError):
                INSTALLER.install(self.root)
        self.assertEqual(validate.call_count, 2)
        self.assertEqual(self.builder.call_count, 1)

    @unittest.skipUnless(os.name == 'nt', 'Windows directory junction fixture')
    def test_real_windows_junction_preserves_target(self):
        target = self.root / 'protected'
        target.mkdir()
        sentinel = target / 'keep.txt'
        sentinel.write_text('must survive')
        ORIGINAL_RUN(['cmd.exe', '/c', 'mklink', '/J', str(self.root / '.venv'),
                      str(target)], check=True, capture_output=True)
        with self.assertRaises(ValueError):
            INSTALLER.install(self.root)
        self.builder.assert_not_called()
        self.assertEqual(sentinel.read_text(), 'must survive')

    def test_unix_interpreter_is_used_when_present(self):
        python = self.root / '.venv/bin/python'
        python.parent.mkdir(parents=True)
        python.touch()
        INSTALLER.install(self.root)
        self.assertEqual(self.run.call_args_list[0].args[0][0], str(python))

    def test_windows_interpreter_fallback(self):
        INSTALLER.install(self.root)
        self.assertEqual(self.run.call_args_list[0].args[0][0],
                         str(self.root / '.venv/Scripts/python.exe'))

    def test_pip_runs_isolated_from_checkout_cwd(self):
        # CSL-471: ci.yml linux/windows/macos cells run this installer on a PR
        # checkout. `python -m pip` with cwd=checkout would execute pip/__main__.py.
        INSTALLER.install(self.root)
        self.assertGreater(len(self.run.call_args_list), 0)
        for call in self.run.call_args_list:
            argv = call.args[0]
            self.assertEqual(argv[1:4], ['-P', '-m', 'pip'])
            self.assertEqual(Path(call.kwargs['cwd']).resolve(), self.runner_temp.resolve())
            self.assertNotEqual(Path(call.kwargs['cwd']).resolve(), self.root.resolve())

    def test_pip_cwd_falls_back_when_runner_temp_is_the_checkout(self):
        outside = Path(INSTALLER.tempfile.gettempdir()).resolve()
        try:
            outside.relative_to(self.root.resolve())
        except ValueError:
            pass
        else:
            self.skipTest('tempdir is inside the fixture checkout')
        with patch.dict(os.environ, {'RUNNER_TEMP': str(self.root)}):
            cwd = INSTALLER.pip_cwd(self.root)
        self.assertEqual(cwd.resolve(), outside)

    def test_pip_cwd_fails_closed_when_every_candidate_is_the_checkout(self):
        with patch.dict(os.environ, {'RUNNER_TEMP': str(self.root)}), \
             patch.object(INSTALLER.tempfile, 'gettempdir', return_value=str(self.root)):
            with self.assertRaisesRegex(ValueError, 'pip cwd must not be inside the checkout'):
                INSTALLER.pip_cwd(self.root)


if __name__ == '__main__':
    unittest.main()

"""Offline run-gates action regressions; no GitHub calls or hosted jobs.

Run: python -m unittest tools.test_run_gates_action -v
"""
from pathlib import Path
import unittest

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
        self.assertIn('uses: actions/cache@v4', self.action)
        self.assertIn("hashFiles('.github/actions/run-gates/requirements.txt')", self.action)
        self.assertIn(
            '"$venv_py" -m pip install -r .github/actions/run-gates/requirements.txt',
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


if __name__ == '__main__':
    unittest.main()

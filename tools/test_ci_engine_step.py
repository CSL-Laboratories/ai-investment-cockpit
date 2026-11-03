"""Offline pin for the ci.yml `engine tests` step (CSL-601); no GitHub calls.

Run: python -m unittest discover -s tools -p test_ci_engine_step.py -v
Requires PyYAML (already part of the repository validation dependencies).
"""
import glob
import re
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / '.github' / 'workflows' / 'ci.yml'
STEP = 'engine tests'


def linux_steps():
    # BaseLoader keeps the YAML 1.1 word "on" as a string.
    doc = yaml.load(CI.read_text(), Loader=yaml.BaseLoader)
    return doc['jobs']['linux']['steps']


def engine_step():
    matches = [s for s in linux_steps() if s.get('name') == STEP]
    if len(matches) != 1:
        raise AssertionError(f'expected one {STEP!r} step in job linux, found {len(matches)}')
    return matches[0]


def guard_pattern(step):
    m = re.fullmatch(r"hashFiles\('([^']+)'\) != ''", step['if'].strip())
    if not m:
        raise AssertionError(f"unexpected guard: {step['if']!r}")
    return m.group(1)


class EngineStepTest(unittest.TestCase):
    def test_step_runs_pytest_on_engine_tests(self):
        run = engine_step()['run']
        self.assertRegex(run, r'(^|\s)-m pytest(\s|$)')
        self.assertIn('engine/tests', run.split())
        self.assertNotIn('unittest', run)

    def test_step_sits_after_cockpit_spine_step(self):
        names = [s.get('name', '') for s in linux_steps()]
        spine = [i for i, s in enumerate(linux_steps()) if 'cockpit_spine' in s.get('run', '')]
        self.assertTrue(spine, 'no cockpit_spine step in job linux')
        self.assertGreater(names.index(STEP), spine[-1])

    def test_guard_recurses_like_pytest(self):
        pattern = guard_pattern(engine_step())
        self.assertTrue(pattern.startswith('engine/tests/**/'), pattern)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def hits():
                return glob.glob(pattern, root_dir=root, recursive=True)

            (root / 'engine' / 'tests').mkdir(parents=True)
            self.assertEqual(hits(), [], 'empty tree must skip the step')
            nested = root / 'engine' / 'tests' / 'unit' / 'test_nested.py'
            nested.parent.mkdir()
            nested.write_text('')
            self.assertEqual(hits(), ['engine/tests/unit/test_nested.py'])
            (root / 'engine' / 'tests' / 'test_top.py').write_text('')
            self.assertEqual(len(hits()), 2)


if __name__ == '__main__':
    unittest.main()

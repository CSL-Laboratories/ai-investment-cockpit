"""Offline review-gate workflow regressions; no GitHub calls or hosted jobs.

Run: python -m unittest tools.test_review_gate -v
Requires PyYAML (already part of the repository validation dependencies).
"""
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github' / 'workflows' / 'review-gate.yml'


class ReviewGateTests(unittest.TestCase):
    def setUp(self):
        self.doc = yaml.load(WORKFLOW.read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
        self.text = WORKFLOW.read_text(encoding='utf-8')

    def test_never_path_skipped_and_passes_merge_group(self):
        self.assertNotIn('paths', self.doc['on']['pull_request'])
        self.assertNotIn('paths', self.doc['on']['merge_group'])
        self.assertEqual(self.doc['on']['merge_group']['types'], ['checks_requested'])
        self.assertEqual(self.doc['jobs']['review-gate']['runs-on'], 'ubuntu-latest')
        self.assertEqual(self.doc['jobs']['review-gate']['name'], 'review-gate')
        self.assertIn('Devin Review', self.text)
        self.assertIn('merge_group: review-gate passes', self.text)
        self.assertIn('github.event.pull_request.head.sha', self.text)
        self.assertNotIn('paths:', self.text.split('jobs:', 1)[0])


if __name__ == '__main__':
    unittest.main()

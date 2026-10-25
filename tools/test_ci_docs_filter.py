"""Guard the ci.yml docs-only filter against skipping files the tests read (CSL-397).

The `filter` job's `decide` step marks docs-only diffs as no-test, so a required
`linux-gates` can pass with `linux` skipped. The CSL-392 audit found tests that
read files the old filter skipped, including the LLM prompts under
app/workflows/prompts/. These tests execute the checked-in `decide` script on real
pull_request diffs and fail when a test source references a tracked file the
filter would still skip.

The guard is static: it sees path literals ('docs/x.md') and quoted components
joined by / ("docs" / "x.md"). Paths built with os.path.join, f-strings, globs
or variable prefixes are not detected, so new dynamic reads of documentation
need a matching run-tests pattern by review.
"""
from __future__ import annotations

import functools
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / '.github' / 'workflows' / 'ci.yml'
RUN_GATES = ROOT / '.github' / 'actions' / 'run-gates' / 'action.yml'
SELF = Path(__file__).resolve().relative_to(ROOT).as_posix()

# Tracked files the CSL-392 audit saw tests open while the old filter skipped them.
AUDITED = (
    '.gitignore',
    '.claude/skills/proceed-or-escalate/SKILL.md',
    'AGENTS.md',
    'app/filings/PROVENANCE.md',
    'app/workflows/prompts/grounding_check.v1.md',
    'app/workflows/prompts/interrogation_fresh.v1.md',
    'app/workflows/prompts/morning_brief.v1.md',
    'app/workflows/prompts/trade_proposal.v1.md',
    'app/workflows/prompts/triage_batch.v1.md',
    'docs/PLAN.md',
    'docs/evidence/w24-natural-head-rescreen-2026-09-09.md',
    'docs/evidence/w24-natural-head-rescreen-2026-09-12.md',
    'docs/evidence/w24-natural-head-rescreen-2026-09-15-tue.md',
    'docs/evidence/w24-natural-head-rescreen-2026-09-15.md',
    'docs/replacement/AUTHORITY-COMPLIANCE-RESEARCH.md',
    'docs/replacement/ISSUE-CONTRACT.md',
    'docs/replacement/MONTH-PROGRAM.md',
    'docs/replacement/README.md',
    'docs/replacement/SCOPE-AUDIT.md',
    'docs/replacement/SUCCESSOR-PRODUCT.md',
    'docs/replacement/outcomes/O7/CONTRACT.md',
    'docs/research/gate-feasibility-and-value-2026-09-06.md',
    'docs/setup/accounts-and-keys.md',
)
# Plain documentation that no test reads keeps the free no-test path.
STILL_SKIPPED = ('docs/example.md', 'README.md', 'docs/research/notes.md', 'LICENSE')
# Reviewed references to skipped files that are not reads of the checked-in file.
# A new reference fails the guard until it is added to the run-tests pattern or here.
ALLOWED_REFERENCES = {
    ('tools/test_ci_dispatch_scope.py', 'README.md'): 'file created in a temporary git repo',
    ('tools/test_ci_dispatch_scope.py', 'docs/ci-queue-rollout.md'): 'path string fed to the Rust selector',
}


@functools.cache
def decide_step() -> dict:
    ci = yaml.load(CI.read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
    return next(step for step in ci['jobs']['filter']['steps'] if step.get('id') == 'decide')


@functools.cache
def filter_patterns() -> tuple[str, ...]:
    # The decide loop's grep -Eq patterns, in order: run, run, skip.
    return tuple(re.findall(r"grep -Eq(?: \\\n\s*)? '([^']+)'", decide_step()['run']))


def linux_job_test_sources(tracked: list[str]) -> list[str]:
    # What the required linux job runs: app pytest, plus the tools suites named in
    # ci.yml and in the run-gates composite action. This module is excluded: its
    # allow-list and fixtures name skipped files on purpose.
    text = CI.read_text(encoding='utf-8') + RUN_GATES.read_text(encoding='utf-8')
    tools = {f'tools/{name}.py' for name in re.findall(r'tools[/.](test_\w+)', text)}
    return [path for path in tracked if path.endswith('.py') and path != SELF
            and (path.startswith('app/tests/') or path in tools)]


def skipped_by_filter(path: str) -> bool:
    patterns = filter_patterns()
    run_patterns, skip_pattern = patterns[:-1], patterns[-1]
    if any(re.search(pattern, path) for pattern in run_patterns):
        return False
    return re.search(skip_pattern, path) is not None


class DecideScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.git('init', '-b', 'main')
        (cls.root / 'seed.txt').write_text('seed\n', encoding='utf-8')
        cls.git('add', 'seed.txt')
        cls.git('commit', '-m', 'base')
        cls.base = cls.git('rev-parse', 'HEAD')
        cls.git('remote', 'add', 'origin', str(cls.root))
        cls.script = decide_step()['run']

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def git(cls, *args):
        return subprocess.check_output(['git', '-c', 'user.name=CI Test',
            '-c', 'user.email=ci@example.invalid', '-c', 'commit.gpgsign=false',
            '-c', 'core.hooksPath=/dev/null', *args], cwd=cls.root,
            text=True, stderr=subprocess.DEVNULL).strip()

    def run_tests_for(self, paths) -> str:
        # Each diff is a fresh commit on top of the shared base: exactly `paths` changed.
        self.git('checkout', '-q', '--detach', self.base)
        for path in paths:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f'changed {path}\n', encoding='utf-8')
            self.git('add', path)
        self.git('commit', '-q', '-m', 'change')
        head = self.git('rev-parse', 'HEAD')
        self.assertEqual(sorted(self.git('diff', '--name-only', self.base, head).splitlines()),
                         sorted(paths))
        return self.decide(self.base, head)

    def run_tests_for_rename(self, source: str, destination: str) -> str:
        # PR base holds `source`; the PR head only renames it (identical content).
        self.git('checkout', '-q', '--detach', self.base)
        target = self.root / source
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('renamed content\n' * 20, encoding='utf-8')
        self.git('add', source)
        self.git('commit', '-q', '-m', 'add source')
        base = self.git('rev-parse', 'HEAD')
        (self.root / destination).parent.mkdir(parents=True, exist_ok=True)
        self.git('mv', source, destination)
        self.git('commit', '-q', '-m', 'rename')
        head = self.git('rev-parse', 'HEAD')
        # Git's default rename detection reports only the destination.
        self.assertEqual(self.git('diff', '--name-only', base, head), destination)
        return self.decide(base, head)

    def decide(self, base: str, head: str) -> str:
        output = self.root / '.git' / 'github-output'
        output.write_text('', encoding='utf-8')
        env = {key: value for key, value in os.environ.items()
               if key not in {'EVENT_NAME', 'PR_BASE', 'PR_HEAD', 'MG_BASE', 'MG_HEAD',
                              'PUSH_BEFORE', 'PUSH_HEAD'}}
        env.update({'EVENT_NAME': 'pull_request', 'PR_BASE': base, 'PR_HEAD': head,
                    'GITHUB_OUTPUT': str(output)})
        result = subprocess.run(['bash', '-c', self.script], cwd=self.root, env=env,
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Changed files:', result.stdout, result.stdout)
        lines = output.read_text(encoding='utf-8').splitlines()
        return next(line for line in lines if line.startswith('run-tests=')).split('=', 1)[1]

    def test_each_audited_file_alone_runs_tests(self):
        for path in AUDITED:
            with self.subTest(path=path):
                self.assertEqual(self.run_tests_for([path]), 'true')

    def test_any_file_under_app_runs_tests(self):
        for path in ('app/workflows/prompts/new_prompt.v2.md', 'app/README.md', 'app/notes.txt'):
            with self.subTest(path=path):
                self.assertEqual(self.run_tests_for([path]), 'true')

    def test_claude_md_alone_runs_tests(self):
        self.assertEqual(self.run_tests_for(['CLAUDE.md']), 'true')

    def test_nested_claude_md_alone_still_skips(self):
        # The selector is anchored to the root file; docs/ keeps the docs-only skip.
        self.assertEqual(self.run_tests_for(['docs/CLAUDE.md']), 'false')

    def test_lowercase_claude_md_alone_still_skips(self):
        # The match is case-sensitive, like the decide step's grep -E.
        self.assertEqual(self.run_tests_for(['claude.md']), 'false')

    def test_agents_md_alone_runs_tests(self):
        self.assertEqual(self.run_tests_for(['AGENTS.md']), 'true')

    def test_nested_agents_md_alone_still_skips(self):
        # The selector is anchored to the root file; docs/ keeps the docs-only skip.
        self.assertEqual(self.run_tests_for(['docs/AGENTS.md']), 'false')

    def test_lowercase_agents_md_alone_still_skips(self):
        # The match is case-sensitive, like the decide step's grep -E.
        self.assertEqual(self.run_tests_for(['agents.md']), 'false')

    def test_proceed_or_escalate_skill_alone_runs_tests(self):
        self.assertEqual(
            self.run_tests_for(['.claude/skills/proceed-or-escalate/SKILL.md']),
            'true',
        )

    def test_other_claude_skill_alone_still_skips(self):
        # Exact skill path only; sibling skills keep the docs-only skip.
        self.assertEqual(
            self.run_tests_for(['.claude/skills/other/SKILL.md']),
            'false',
        )

    def test_nested_proceed_or_escalate_skill_alone_still_skips(self):
        self.assertEqual(
            self.run_tests_for(
                ['docs/.claude/skills/proceed-or-escalate/SKILL.md']
            ),
            'false',
        )

    def test_plain_documentation_still_skips(self):
        self.assertEqual(self.run_tests_for(list(STILL_SKIPPED)), 'false')

    def test_renaming_a_test_read_file_runs_tests(self):
        self.assertEqual(self.run_tests_for_rename('docs/PLAN.md', 'docs/old-plan.md'), 'true')
        self.assertEqual(self.run_tests_for_rename('app/workflows/prompts/a.v1.md', 'docs/a.v1.md'),
                         'true')

    def test_renaming_plain_documentation_still_skips(self):
        self.assertEqual(self.run_tests_for_rename('docs/notes-a.md', 'docs/notes-b.md'), 'false')

    def test_mixed_diff_runs_tests(self):
        self.assertEqual(self.run_tests_for(['docs/example.md', 'docs/setup/accounts-and-keys.md']),
                         'true')


class TestSourceGuardTests(unittest.TestCase):
    def test_guard_scans_the_linux_job_suites(self):
        tracked = subprocess.check_output(['git', 'ls-files'], cwd=ROOT, text=True).splitlines()
        sources = linux_job_test_sources(tracked)
        for name in ('tools/test_ci_dispatch_scope.py', 'tools/test_ci_settle.py',
                     'tools/test_review_gate.py', 'tools/test_queue_gates.py',
                     'tools/test_ci_cancel_controller.py', 'tools/test_candidate_workflows.py',
                     'tools/test_run_gates_action.py', 'app/tests/test_o7_operator_contract.py'):
            self.assertIn(name, sources)
        self.assertNotIn(SELF, sources)

    def test_allowed_references_are_still_present(self):
        # Drop stale exceptions so the allow-list cannot grow silently.
        for (source, path), reason in ALLOWED_REFERENCES.items():
            with self.subTest(source=source, path=path):
                self.assertIn(path, (ROOT / source).read_text(encoding='utf-8'), reason)

    def test_python_pattern_matches_the_script(self):
        # skipped_by_filter mirrors the script; keep the two in step.
        self.assertEqual(len(filter_patterns()), 3)
        for path in AUDITED:
            self.assertFalse(skipped_by_filter(path), path)
        for path in STILL_SKIPPED:
            self.assertTrue(skipped_by_filter(path), path)
        # A real tracked document that no linux-job test reads keeps skipping.
        self.assertTrue((ROOT / 'docs/ci-queue-rollout.md').is_file())
        self.assertTrue(skipped_by_filter('docs/ci-queue-rollout.md'))

    def test_no_test_references_a_file_the_filter_skips(self):
        tracked = subprocess.check_output(['git', 'ls-files'], cwd=ROOT, text=True).splitlines()
        skipped = {path for path in tracked if skipped_by_filter(path)}
        quoted = r"""(?:'[^'\n]+'|"[^"\n]+")"""
        chain = re.compile(quoted + r'(?:\s*/\s*' + quoted + r')*')
        hits = []
        for source in linux_job_test_sources(tracked):
            text = (ROOT / source).read_text(encoding='utf-8', errors='replace')
            # Path literals ("docs/x.md") and quoted components joined by / ("docs" / "x.md").
            for match in chain.finditer(text):
                parts = re.findall(quoted, match.group(0))
                path = '/'.join(part[1:-1] for part in parts)
                if path in skipped and (source, path) not in ALLOWED_REFERENCES:
                    hits.append(f'{source} -> {path}')
        self.assertEqual(sorted(set(hits)), [], 'tests reference files the ci.yml filter skips; '
                         'add them to the run-tests pattern in the decide step')

if __name__ == '__main__':
    unittest.main()

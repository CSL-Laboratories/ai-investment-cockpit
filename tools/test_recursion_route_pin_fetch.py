"""Offline regression for the linux job's pinned Recursion checkout.

The step lives in ``.github/workflows/ci.yml``. This module parses that step
and runs its shell body against a local git repository. ``url.<base>.insteadOf``
rewrites the public remote to that repository, and an HTTP proxy on a closed
port makes a missed rewrite fail instead of contacting GitHub.

The linux job runs ``tools/test_ci_dispatch_scope.py`` by path, which imports
``RecursionRoutePinFetchTests`` so this file is collected there.

    python -X utf8 -B tools/test_recursion_route_pin_fetch.py
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / '.github' / 'workflows' / 'ci.yml'
STEP_NAME = 'Fetch the pinned Recursion route contract (read-only, outside the workspace)'
PUBLIC_REMOTE = 'https://github.com/CSL-Laboratories/recursion.git'
PIN_REL = 'successor/route_consumer/contract/PIN.json'
MISSING_SHA = '0123456789abcdef0123456789abcdef01234567'
LIED_HEAD = '0000000000000000000000000000000000000000'


def fetch_step() -> dict:
    ci = yaml.load(CI.read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
    return next(step for step in ci['jobs']['linux']['steps'] if step.get('name') == STEP_NAME)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(
        ['git', '-c', 'user.name=CI Test', '-c', 'user.email=ci@example.invalid',
         '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', *args],
        cwd=cwd, text=True, stderr=subprocess.DEVNULL,
    ).strip()


class RecursionRoutePinFetchTests(unittest.TestCase):
    def test_step_is_tokenless_bash_and_reads_only_the_pin_file(self) -> None:
        step = fetch_step()
        self.assertEqual(list(step), ['name', 'shell', 'run'])
        self.assertEqual(step['shell'], 'bash')
        body = step['run']
        self.assertIn('set -euo pipefail', body)
        self.assertEqual(body.count(PUBLIC_REMOTE), 1)
        self.assertNotIn('GITHUB_TOKEN', body)
        self.assertNotIn('secrets.', body)
        self.assertNotIn('actions/checkout', body)
        self.assertIn(f"open('{PIN_REL}', encoding='utf-8')", body)
        self.assertIn("['source']['head_sha']", body)
        self.assertIn('fetch -q --depth 1', body)
        self.assertIn('checkout -q --detach FETCH_HEAD', body)
        self.assertIn('[ "$(git -C "$dest" rev-parse HEAD)" != "$pin" ]', body)
        self.assertIn('RECURSION_ROUTE_CONTRACT_CHECKOUT=$dest', body)
        self.assertIn('::error::', body)

    def test_matching_sha_exports_the_checkout_outside_the_workspace(self) -> None:
        pin, result, checkout = self.run_fetch(self.commit_sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.env_text.strip(),
            f'RECURSION_ROUTE_CONTRACT_CHECKOUT={checkout}',
        )
        self.assertNotEqual(checkout.parent, self.workspace)
        self.assertFalse(str(checkout).startswith(str(self.workspace)))
        self.assertEqual(_git(checkout, 'rev-parse', 'HEAD'), pin)

    def test_missing_sha_fails_and_does_not_export_a_checkout(self) -> None:
        _pin, result, checkout = self.run_fetch(MISSING_SHA)
        self.assertEqual(result.returncode, 1)
        self.assertIn('::error::', result.stdout)
        self.assertIn(MISSING_SHA, result.stdout)
        self.assertNotIn('RECURSION_ROUTE_CONTRACT_CHECKOUT=', result.env_text)
        # git init runs before the fetch. A failed fetch must not leave a
        # checkout of the requested commit, and must not publish the path.
        if checkout.exists():
            head = subprocess.run(
                ['git', '-C', str(checkout), 'rev-parse', 'HEAD'],
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(head.stdout.strip(), MISSING_SHA)

    def test_head_that_is_not_the_pin_fails_closed(self) -> None:
        pin, result, checkout = self.run_fetch(self.commit_sha, lie_about_head=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('::error::', result.stdout)
        self.assertIn(pin, result.stdout)
        self.assertNotIn('RECURSION_ROUTE_CONTRACT_CHECKOUT=', result.env_text)
        # Fetch and checkout did happen; the step refused the lied HEAD.
        self.assertEqual(_git(checkout, 'rev-parse', 'HEAD'), pin)
        self.assertNotEqual(pin, LIED_HEAD)

    def setUp(self) -> None:
        if shutil.which('git') is None or shutil.which('bash') is None:
            self.skipTest('git and bash are required')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.src = root / 'src'
        self.src.mkdir()
        _git(self.src, 'init', '-q', '-b', 'main')
        (self.src / 'README').write_text('pinned\n', encoding='utf-8')
        _git(self.src, 'add', 'README')
        _git(self.src, 'commit', '-q', '-m', 'pin')
        self.commit_sha = _git(self.src, 'rev-parse', 'HEAD')
        _git(self.src, 'config', 'uploadpack.allowReachableSHA1InWant', 'true')
        self.workspace = root / 'ws'
        self.runner = root / 'runner'
        self.runner.mkdir()
        self.github_env = root / 'github_env'
        self.bin_dir = root / 'bin'
        self.bin_dir.mkdir()
        (self.bin_dir / 'python').symlink_to(sys.executable)

    def run_fetch(self, pin: str, *, lie_about_head: bool = False):
        pin_path = self.workspace / PIN_REL
        pin_path.parent.mkdir(parents=True, exist_ok=True)
        pin_path.write_text(
            json.dumps({'source': {'head_sha': pin}}) + '\n', encoding='utf-8'
        )
        self.github_env.write_text('', encoding='utf-8')
        checkout = self.runner / 'recursion-route-contract'
        if checkout.exists():
            shutil.rmtree(checkout)
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {'GITHUB_TOKEN', 'GH_TOKEN', 'GITHUB_ENV', 'RUNNER_TEMP'}
            and not key.startswith('GIT_CONFIG_')
        }
        env.update({
            'PATH': str(self.bin_dir) + os.pathsep + env.get('PATH', ''),
            'RUNNER_TEMP': str(self.runner),
            'GITHUB_ENV': str(self.github_env),
            'GIT_TERMINAL_PROMPT': '0',
            # Rewrite the script's public URL onto the fixture. A closed proxy
            # makes a fetch that still uses https fail closed, offline.
            'GIT_CONFIG_COUNT': '2',
            'GIT_CONFIG_KEY_0': f'url.{self.src}/.insteadOf',
            'GIT_CONFIG_VALUE_0': PUBLIC_REMOTE,
            'GIT_CONFIG_KEY_1': 'http.proxy',
            'GIT_CONFIG_VALUE_1': 'http://127.0.0.1:9',
        })
        if lie_about_head:
            env['PIN_FETCH_LIE_HEAD'] = '1'
            self.install_lying_git()
        completed = subprocess.run(
            ['bash', '-c', fetch_step()['run']],
            cwd=self.workspace,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        completed.env_text = self.github_env.read_text(encoding='utf-8')
        return pin, completed, checkout

    def install_lying_git(self) -> None:
        real = shutil.which('git')
        wrapper = self.bin_dir / 'git'
        wrapper.write_text(
            '#!/bin/sh\n'
            'if [ "${PIN_FETCH_LIE_HEAD:-}" = 1 ] && [ "$1" = "-C" ] '
            '&& [ "$3" = "rev-parse" ] && [ "$4" = "HEAD" ]; then\n'
            f'  echo "{LIED_HEAD}"\n'
            '  exit 0\n'
            'fi\n'
            f'exec "{real}" "$@"\n',
            encoding='utf-8',
        )
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)


if __name__ == '__main__':
    unittest.main()

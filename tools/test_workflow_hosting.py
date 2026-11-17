#!/usr/bin/env python3
"""Workflow-wide hosting, trigger and documentation guards.

Moved from tools/test_codex_gate.py when the Codex CI/fixer gate was removed
(CSL-238); the assertions are unchanged except that the gate-specific ones were
dropped and the gate workflow's checks now apply to workflow-checks.yml.
"""
from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "workflow-checks.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
RUST_WORKFLOW = ROOT / ".github" / "workflows" / "rust.yml"
PR_LINT_WORKFLOW = ROOT / ".github" / "workflows" / "pr-lint.yml"
RETRIGGER_WORKFLOW = ROOT / ".github" / "workflows" / "ci-label-retrigger.yml"
ACTIONLINT = ROOT / ".github" / "actionlint.yaml"
AGENTS = ROOT / "AGENTS.md"


class WorkflowStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.ci = CI_WORKFLOW.read_text(encoding="utf-8")
        cls.rust = RUST_WORKFLOW.read_text(encoding="utf-8")
        cls.pr_lint = PR_LINT_WORKFLOW.read_text(encoding="utf-8")
        cls.retrigger = RETRIGGER_WORKFLOW.read_text(encoding="utf-8")
        cls.actionlint = ACTIONLINT.read_text(encoding="utf-8")
        cls.agents = AGENTS.read_text(encoding="utf-8")

    def test_test_jobs_use_free_github_hosted_runners(self) -> None:
        # CSL-408: no job may run on a billed Blacksmith runner or use a Blacksmith
        # action. Comments are stripped (they name the superseded migration branch),
        # and the blacksmith-sh[bot] login in bot allow-lists is not a runner.
        configs = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        configs += [ROOT / ".github" / "actions" / "run-gates" / "action.yml",
                    ROOT / ".github" / "actionlint.yaml"]
        self.assertGreater(len(configs), 5)
        for path in configs:
            code = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                             if not line.lstrip().startswith("#"))
            hits = re.findall(r"(?i)blacksmith-(?!sh\[bot\])\S*|useblacksmith/\S*", code)
            with self.subTest(path=path.name):
                self.assertEqual(hits, [])
        self.assertIn("runs-on: windows-2025", self.ci)
        self.assertIn('"runner": "windows-2025"', self.rust)
        self.assertIn('"runner": "ubuntu-24.04"', self.rust)
        self.assertNotIn("self-hosted-runner", self.actionlint)

    def test_rust_cache_and_label_skip(self) -> None:
        self.assertIn("Swatinem/rust-cache@v2", self.rust)
        self.assertIn("workspaces: rust", self.rust)
        self.assertIn("ci:full", self.rust)
        self.assertIn("github.event.label.name == 'enhancement'", self.retrigger)

    def test_ci_label_events_do_not_start_required_check(self) -> None:
        on = self.ci.split("jobs:", 1)[0]
        self.assertIn("types: [opened, synchronize, reopened, ready_for_review]", on)
        self.assertNotIn("labeled, unlabeled", on)
        rust_on = self.rust.split("jobs:", 1)[0]
        self.assertIn("types: [opened, synchronize, reopened, ready_for_review]", rust_on)
        self.assertNotIn("labeled, unlabeled", rust_on)
        self.assertIn("github.event.pull_request.draft != true", self.ci)
        self.assertIn("&& 'draft'", self.ci)
        self.assertIn("format('label-{0}-{1}', inputs.pr_head_sha", self.ci)
        gates = self.ci.split("\n  linux-gates:\n", 1)[1].split("\n  windows:\n", 1)[0]
        header = gates.split("steps:", 1)[0]
        self.assertIn("name: linux-gates", self.ci)
        self.assertIn("always() && github.event_name != 'schedule'", header)
        self.assertNotIn("needs.filter.result != 'skipped'", header)
        self.assertIn('FILTER_RESULT" != "success"', gates)
        self.assertNotIn("required check passes.", self.ci)
        retrigger_jobs = self.retrigger.split("\njobs:\n", 1)[1]
        self.assertNotIn("linux-gates:", retrigger_jobs)
        self.assertNotIn("name: linux-gates", retrigger_jobs)
        self.assertIn("github.event.label.name == 'ci:full'", self.retrigger)
        self.assertIn("github.event.label.name == 'enhancement'", self.retrigger)
        self.assertIn("gh workflow run ci.yml", self.retrigger)
        self.assertIn("runs-on: ubuntu-latest", self.retrigger)
        retrigger_on = self.retrigger.split("jobs:", 1)[0]
        self.assertIn("types: [labeled]", retrigger_on)
        self.assertNotIn("unlabeled", retrigger_on.split("jobs:", 1)[0].split("\non:", 1)[-1])

    def test_non_test_jobs_use_github_hosted(self) -> None:
        filter_job = self.ci.split("\n  linux:\n", 1)[0]
        self.assertIn("runs-on: ubuntu-latest", filter_job)
        gates = self.ci.split("\n  linux-gates:\n", 1)[1].split("\n  windows:\n", 1)[0]
        self.assertIn("runs-on: ubuntu-latest", gates)
        self.assertIn("name: linux-gates", self.ci)
        rust_filter = self.rust.split("\n  rust-book:\n", 1)[0]
        self.assertIn("runs-on: ubuntu-latest", rust_filter)
        self.assertIn("github.event.pull_request.draft != true", self.rust)
        self.assertIn("ready_for_review", self.rust)
        self.assertIn("&& 'draft'", self.rust)
        self.assertIn("format('label-{0}-{1}', inputs.pr_head_sha", self.rust)
        linux = self.ci.split("\n  linux:\n", 1)[1].split("\n  linux-gates:\n", 1)[0]
        self.assertIn("runs-on: ubuntu-24.04", linux)

    def test_pr_lint_uses_github_hosted(self) -> None:
        self.assertIn("runs-on: ubuntu-latest", self.pr_lint)
        self.assertNotIn("blacksmith", self.pr_lint)

    def test_agents_review_research_and_evidence_rules(self) -> None:
        self.assertIn("## Code Review Rules", self.agents)
        # CSL-238 (Lucas, Nov 16): Devin is the only automated reviewer; nothing answers it.
        self.assertIn("nothing answers or writes automatically", self.agents)
        # CSL-238: no automated reviewer verdict is documented as a gate any more.
        self.assertNotIn("CODEX-GATE: READY", self.agents)
        self.assertNotIn("CODEX_TRIGGER_PAT", self.agents)
        self.assertIn("When you are the assignee", self.agents)
        self.assertIn("## Research", self.agents)
        self.assertIn("post a cited brief", self.agents)
        self.assertIn("Grok Bot, preparing research for <owner>", self.agents)
        self.assertIn("Only the designated human decision-maker", self.agents)
        self.assertIn("never marks the issue decided", self.agents)
        self.assertIn("No specific tool is required", self.agents)
        self.assertIn(
            "any deep-research source the team has", self.agents
        )
        self.assertIn("Open Deep Research", self.agents)
        self.assertIn("Claude Deep Research", self.agents)
        self.assertIn("ChatGPT Deep Research", self.agents)
        self.assertIn(
            "Each run records its tool, its run id and its cost", self.agents
        )
        self.assertIn("no-pay-per-token-API", self.agents)
        self.assertIn("This section adds no spend ceiling", self.agents)
        self.assertIn(
            "Record which source or tool produced each piece of evidence",
            self.agents,
        )
        self.assertIn("three-founder", self.agents)
        self.assertIn("a digest on the Linear issue", self.agents)
        self.assertIn("submissions/<record_id>/", self.agents)
        self.assertIn("tools/check_submission.py", self.agents)
        self.assertIn("Every new non-public doc", self.agents)
        self.assertIn("CSL-536", self.agents)
        self.assertIn("docs/research/**` is legacy", self.agents)
        self.assertIn("no separate admission is needed", self.agents)
        self.assertIn(
            "If csl-research is not accessible, stop and post a blocker",
            self.agents,
        )
        self.assertNotIn(
            "docs/evidence/**` are legacy and frozen", self.agents
        )
        self.assertNotIn(
            "This repository takes contracts, specs, fixtures and runbooks only",
            self.agents,
        )
        self.assertIn("docs/replacement/outcomes/**", self.agents)
        carve_out = (
            "Gate and operational evidence that code or a runbook in this repo already reads"
        )
        runbook = (ROOT / "docs" / "runbooks" / "w25-worm-audit-witness.md").read_text(
            encoding="utf-8"
        )
        screens = (ROOT / "app" / "feasibility" / "screens.py").read_text(
            encoding="utf-8"
        )
        writers_use_docs_evidence = (
            "docs/evidence/" in runbook and "docs/evidence/" in screens
        )
        agents_allows_docs_evidence = (
            carve_out in self.agents and "docs/evidence/" in self.agents
        )
        self.assertEqual(
            writers_use_docs_evidence,
            agents_allows_docs_evidence,
            "W-25 / feasibility docs/evidence/ writers must match the AGENTS.md carve-out",
        )
        self.assertIn(
            "this cockpit's research/dev swarm dispatch", self.agents
        )
        self.assertNotIn("solo-developer", self.agents)
        self.assertNotIn(
            "Current process: `docs/research/agent-swarm-rebase-2026-09-14.md`",
            self.agents,
        )
        self.assertNotIn(
            "in the issue or the evidence so the research can be checked",
            self.agents,
        )
        self.assertNotIn(
            "Do not assume published records, a record schema or a validator exist",
            self.agents,
        )
        self.assertNotIn(
            "Grok Bots do not research or resolve decision issues", self.agents
        )
        self.assertIn("A founder may have their own bot", self.agents)
        self.assertIn("aggregator is GitHub-hosted", self.agents)
        self.assertIn("Python 3.11+3.12 matrix it wraps runs on GitHub-hosted `ubuntu-24.04`", self.agents)
        self.assertIn("fails closed until `ready_for_review`", self.agents)
        self.assertIn("ci-label-retrigger.yml", self.agents)
        claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("AGENTS.md", claude)
        self.assertIn("Grok Bot, preparing research for <owner>", claude)
        self.assertIn("submissions/<record_id>/", claude)
        self.assertIn("Every new", claude)
        self.assertIn("non-public doc", claude)
        self.assertIn("CSL-536", claude)
        self.assertNotIn(
            "docs/evidence/**` are legacy and frozen", claude
        )
        self.assertIn(carve_out, claude)
        self.assertIn("docs/replacement/outcomes/**", claude)
        self.assertIn("docs/evidence/", claude)
        self.assertIn("three-founder", claude)
        self.assertIn("CSL-511", claude)
        self.assertIn("this cockpit's", claude)
        self.assertIn("Devin may still post research briefs", claude)
        self.assertNotIn("solo-developer", claude)
        self.assertNotIn(
            "Current process: `docs/research/agent-swarm-rebase-2026-09-14.md`",
            claude,
        )
        self.assertNotIn(
            "Grok Bots do not research or resolve those issues", claude
        )
        skill = (
            ROOT / ".claude" / "skills" / "proceed-or-escalate" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Grok Bot, preparing research for <owner>", skill)
        self.assertIn("submissions/<record_id>/", skill)
        self.assertIn("Every new non-public doc", skill)
        self.assertIn("CSL-536", skill)
        self.assertNotIn(
            "docs/evidence/**` are legacy and frozen", skill
        )
        self.assertIn(carve_out, skill)
        self.assertIn("docs/replacement/outcomes/**", skill)
        self.assertIn("docs/evidence/", skill)
        self.assertIn(
            "If csl-research is not accessible, stop and post a blocker", skill
        )
        self.assertNotIn(
            "Grok Bots do not research or resolve decision issues", skill
        )
        self.assertIn(
            "An agent working in both repos follows each repo's own rule",
            self.agents,
        )
        self.assertIn("This note changes neither rule", self.agents)
        self.assertNotIn("research-attestations/", self.agents)
        self.assertNotIn("research-attestations/", claude)
        self.assertNotIn("web research (Parallel)", claude)
        self.assertNotIn("web research (Parallel)", self.agents)

    def test_workflow_checks_job_is_read_only_and_actionlint_is_checksummed(self) -> None:
        # The checks that ran as codex-gate.yml's event-paths job (CSL-238).
        self.assertIn("runs-on: ubuntu-latest", self.text)
        self.assertNotIn("blacksmith", self.text)
        self.assertIn("contents: read", self.text)
        for write in ("issues: write", "pull-requests: write", "actions: write", "contents: write"):
            self.assertNotIn(write, self.text)
        self.assertNotIn("secrets.", self.text)
        self.assertIn("ACTIONLINT_SHA256", self.text)
        self.assertIn("sha256sum -c", self.text)
        self.assertNotIn("download-actionlint.bash", self.text)
        self.assertIn("./actionlint -color .github/workflows/*.yml", self.text)
        for test in ("tools.test_merge_candidate", "tools.test_candidate_workflows",
                     "tools.test_review_gate", "tools.test_queue_gates", "tools/test_workflow_hosting.py"):
            self.assertIn(test, self.text)

    def test_no_workflow_names_the_retired_codex_gate(self) -> None:
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("CODEX_TRIGGER_PAT", text)
                self.assertNotIn("chatgpt-codex-connector", text)
                self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", text)
                self.assertNotIn("scripts/ci/codex_gate.py", text)
        self.assertFalse((ROOT / ".github" / "workflows" / "codex-gate.yml").exists())


def _workflow_files() -> list[Path]:
    return sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))


def _all_steps():
    """(file name, job, index, step) for every step of every workflow and of the composite actions."""
    for path in _workflow_files():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job_name, job in (data.get("jobs") or {}).items():
            for index, step in enumerate(job.get("steps") or []):
                yield path.name, job_name, index, step
    for path in sorted((ROOT / ".github" / "actions").glob("*/action.y*ml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for index, step in enumerate((data.get("runs") or {}).get("steps") or []):
            yield path.parent.name + "/" + path.name, "composite", index, step


def _has_pip_module_install(run: str) -> bool:
    return bool(re.search(r"-m\s+pip\b", run) or re.search(r"""['"]-m['"],\s*['"]pip['"]""", run))


def _pip_uses_safe_path_flag(run: str) -> bool:
    """True when every pip module invocation opts out of cwd-on-sys.path (`-P`)."""
    for pattern in (r"-m\s+pip\b", r"""['"]-m['"],\s*['"]pip['"]"""):
        for match in re.finditer(pattern, run):
            if "-P" not in run[max(0, match.start() - 80):match.start()]:
                return False
    return True


class SurvivingWorkflowGuardTests(unittest.TestCase):
    """Workflow-wide guards that lived in the deleted tools/test_claude_fixer_publish.py (CSL-238) and
    still make sense for the workflows that remain (Fable round 1, R6)."""

    def test_no_pip_call_runs_with_checkout_cwd(self) -> None:
        # CSL-471: `python -m pip` from a checkout cwd can run app/pip/__main__.py as the runner.
        found = 0
        for name, job, index, step in _all_steps():
            run = re.sub(r"<<'([A-Z0-9_]+)'\n.*?\n[ \t]*\1(?:\n|$)", "<<heredoc\n", step.get("run") or "",
                         flags=re.DOTALL)
            if not _has_pip_module_install(run):
                continue
            found += 1
            wd = "".join(str(step.get("working-directory") or "").split())
            with self.subTest(workflow=name, job=job, step=index):
                self.assertTrue(_pip_uses_safe_path_flag(run), run)
                self.assertNotIn(wd, {"app", "rust", ".", "${{github.workspace}}"})
                self.assertTrue(wd == "${{runner.temp}}" or "cwd=os.environ['RUNNER_TEMP']" in run
                                or 'cwd=os.environ["RUNNER_TEMP"]' in run, run)
        self.assertGreater(found, 0)

    def test_benchmarks_requirements_come_from_the_base_tree(self) -> None:
        # CSL-471 / REVIEW-D7 I5: a PR-controlled requirements file must not choose the packages.
        jobs = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))["jobs"]
        install = next(s for s in jobs["benchmarks"]["steps"] if s.get("name") == "Install benchmark dependencies")
        run = install["run"]
        self.assertEqual(install["working-directory"], "${{ runner.temp }}")
        self.assertIn("_bench_base/app/benchmarks/requirements.txt", run)
        self.assertIn("${RUNNER_TEMP}/bench-requirements.txt", run)
        self.assertIn("python -P -m pip install -r", run)
        self.assertNotIn("${{ github.workspace }}/app/benchmarks/requirements.txt", run)

    def test_no_job_enables_the_setup_python_pip_cache(self) -> None:
        # CSL-471 REVIEW-D8 J3: setup-python's post step would save a PR-poisoned ~/.cache/pip into the
        # default-branch cache scope.
        pip_cache_line = re.compile(r"^[ \t]*cache:[ \t]*pip[ \t]*$", re.MULTILINE)
        paths = _workflow_files() + sorted((ROOT / ".github" / "actions").glob("*/action.y*ml"))
        for path in paths:
            with self.subTest(file=path.name):
                self.assertIsNone(pip_cache_line.search(path.read_text(encoding="utf-8")))
        checked = 0
        for name, job, index, step in _all_steps():
            if str(step.get("uses") or "").startswith("actions/setup-python@"):
                checked += 1
                with self.subTest(workflow=name, job=job, step=index):
                    self.assertNotEqual((step.get("with") or {}).get("cache"), "pip")
                    self.assertNotIn("cache-dependency-path", step.get("with") or {})
        self.assertGreater(checked, 0)

    def test_every_run_script_stays_below_actionlints_pipe_limit(self) -> None:
        # actionlint 1.7.7 hangs (and the event-paths job times out) on a run script of 64 KiB or more.
        for name, job, index, step in _all_steps():
            with self.subTest(workflow=name, job=job, step=index):
                self.assertLess(len((step.get("run") or "").encode("utf-8")), 60_000)

    def test_no_workflow_pipes_a_downloaded_installer_into_a_shell(self) -> None:
        for path in _workflow_files():
            text = path.read_text(encoding="utf-8")
            with self.subTest(workflow=path.name):
                self.assertNotIn("claude.ai/install", text)
                self.assertNotRegex(text, r"https?://\S*bootstrap\.sh")
                self.assertNotRegex(text, r"install\.sh\s*\|\s*(?:ba|z)?sh")
                self.assertNotRegex(text, r"(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b")


class ActionlintTests(unittest.TestCase):
    def test_actionlint_when_available(self) -> None:
        import shutil

        exe = shutil.which("actionlint")
        if not exe:
            self.skipTest("actionlint not on PATH")
        result = subprocess.run(
            [exe, "-color", str(WORKFLOW_PATH), str(CI_WORKFLOW), str(RUST_WORKFLOW)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1)

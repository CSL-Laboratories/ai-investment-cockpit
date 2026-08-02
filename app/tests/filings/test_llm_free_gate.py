"""RW-10 (GR-10) — the `no-LLM-in-filings` gate is not weakened by reading code-only.

COCKPIT-AUTHORED, not vendored. It exists because RW-10 *changed a gate*, and the standing
rule (CLAUDE.md) is that a gate may never be relaxed to make something pass. The change:
``test_wp4_structural._code_only`` strips comments and docstrings before the forbidden-token
sweep, because widening the sweep to the vendored SEC client made it fail on PROSE —
``filings/edgar/companyfacts`` documents that it uses "no LLM, no subprocess." and
``\\bsubprocess\\.`` matched the sentence's full stop.

Stripping prose is only legitimate if everything the sweep caught before, it still catches.
That is what this file asserts. Each test below is a token the gate MUST still see in real
code, plus the specific false positive it must no longer see in English.

It also pins the gate's COVERAGE: every vendored package is swept. Upstream only had to
guard parse/ + delta/; the cockpit vendored four packages plus raw_store into a repo where
``agent_runner`` is two imports away, so a package that quietly escapes the sweep is the
realistic failure mode — more likely than someone typing ``import anthropic`` into a parser.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.filings.test_wp4_structural import (
    _FORBIDDEN_TOKENS,
    _code_only,
    _core_py_files,
    CITE_DIR,
    DELTA_DIR,
    EDGAR_DIR,
    FILINGS,
    PARSE_DIR,
    RAW_STORE,
)


def _trips(source: str) -> bool:
    """Would the real gate flag this source?"""
    text = _code_only(source)
    return any(pat.search(text) for pat in _FORBIDDEN_TOKENS)


# ---------------------------------------------------------------------------------------
# STILL CAUGHT — every forbidden construct, as executable code
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("source", [
    "import subprocess\nsubprocess.run(['claude'])\n",
    "from subprocess import Popen\nPopen(['claude'])\n",
    "import subprocess\nx = subprocess.check_output(['ls'])\n",
    "import os\nos.system('claude -p hi')\n",
    "import os\nos.popen('claude')\n",
    "from agent_runner.runner import run_agent\nrun_agent(spec)\n",
    "import anthropic\nanthropic.Anthropic()\n",
    "import openai\nopenai.chat()\n",
    "from multiprocessing import Popen\n",
])
def test_real_code_still_trips_the_gate(source):
    """THE REGRESSION THAT MATTERS. If any of these stops tripping, the gate was weakened."""
    assert _trips(source), f"gate went blind to real code:\n{source}"


def test_a_used_string_literal_is_still_scanned():
    """Only DOCSTRINGS are dropped, not string literals in general.

    A statement-position string is prose. A string being *used* — assigned, passed as an
    argument — is code, and a module name hidden in one must stay visible to the sweep.

    NOTE ON WHAT THIS DOES *NOT* CLAIM: an earlier draft of this test asserted that
    ``__import__("subprocess")`` trips the gate. It does not, and it never did — no pattern
    in ``_FORBIDDEN_TOKENS`` matches that spelling, before or after the code-only change.
    Asserting it here would have invented a capability the gate has never had, which is a
    worse failure than the gap itself: a green test claiming coverage that does not exist.
    The real backstop for that spelling is ``test_core_imports_no_agent_runner_or_llm_module``
    plus the fact that nothing under app/filings/ has any reason to import dynamically.
    """
    assert _trips('name = "subprocess.run"\nrunner = name\n')
    assert _trips('handler = dict(cmd="os.system")\n')


def test_the_token_list_itself_is_not_empty():
    """A sweep over an empty pattern list would pass everything, silently."""
    assert _FORBIDDEN_TOKENS, "the forbidden-token list is empty — the gate checks nothing"


# ---------------------------------------------------------------------------------------
# NO LONGER FALSELY CAUGHT — the one behaviour that actually changed
# ---------------------------------------------------------------------------------------
def test_prose_about_not_spawning_no_longer_trips_the_gate():
    """The exact false positive that forced this change (filings/edgar/companyfacts.py)."""
    source = '"""A pure mapper over an injected fetcher, no LLM, no subprocess."""\nX = 1\n'
    assert not _trips(source)


def test_a_comment_mentioning_subprocess_does_not_trip_the_gate():
    assert not _trips("# we deliberately never call subprocess.run here\nX = 1\n")


def test_a_function_docstring_is_also_prose():
    source = 'def f():\n    """Never uses subprocess.Popen or agent_runner."""\n    return 1\n'
    assert not _trips(source)


# ---------------------------------------------------------------------------------------
# COVERAGE — the sweep must actually reach every vendored package
# ---------------------------------------------------------------------------------------
def test_every_vendored_package_is_swept():
    """A package that escapes ``_core_py_files`` is unguarded, and nothing else would say so.

    Compares the sweep's own file list against what is ON DISK under app/filings/, so adding
    a fifth vendored package without adding it to the gate fails HERE rather than silently
    shipping an unswept directory.
    """
    swept = {p.resolve() for p in _core_py_files()}
    on_disk = {
        p.resolve() for p in FILINGS.rglob("*.py")
        if "__pycache__" not in p.parts
    }
    missed = sorted(str(p.relative_to(FILINGS)) for p in on_disk - swept)
    assert missed == [], f"vendored files not covered by the no-LLM sweep: {missed}"


def test_the_sweep_is_not_vacuous():
    """Guards against the whole gate passing because it found no files to read."""
    files = list(_core_py_files())
    assert len(files) >= 15, f"only {len(files)} vendored files swept — expected the full core"
    for d in (PARSE_DIR, DELTA_DIR, EDGAR_DIR, CITE_DIR):
        assert any(Path(d) in f.parents for f in files), f"{d.name}/ contributed no files"
    assert RAW_STORE.resolve() in {f.resolve() for f in files}

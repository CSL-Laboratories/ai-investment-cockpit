"""WP8 — versioned prompt-template loader (contract Owner A 'prompts.py').

Implements the WP8 prompt-versioning requirement of docs/build-scaffold-ultracode.md
("keep prompts versioned in git") and the SEC-08/E-06 provenance hooks: every rendered
prompt carries a stable template ref (``{name}.v{version}``) and a sha256 of the template
text, which agent_calls stamps onto the AgentJob row (prompt_template / prompt_hash).

Templates live in ``app/workflows/prompts/`` as ``{name}.v{n}.md`` files using
``string.Template`` ``$placeholders``. ``load_prompt(name)`` resolves the LATEST version;
an explicit ``version=`` pins one.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from string import Template

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
_VERSIONED = re.compile(r"^(?P<name>[a-z0-9_]+)\.v(?P<version>\d+)\.md$")


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: int
    text: str
    sha256: str

    @property
    def ref(self) -> str:
        """Stable provenance ref recorded on AgentJob.prompt_template."""
        return f"{self.name}.v{self.version}"

    def render(self, **fields: str) -> str:
        """Substitute ``$placeholders`` strictly — a missing field raises (fail-closed;
        never dispatch a prompt with an unfilled slot)."""
        return Template(self.text).substitute(fields)


def _versions(name: str) -> dict[int, Path]:
    out: dict[int, Path] = {}
    for p in _PROMPT_DIR.glob(f"{name}.v*.md"):
        m = _VERSIONED.match(p.name)
        if m and m.group("name") == name:
            out[int(m.group("version"))] = p
    return out


def load_prompt(name: str, version: int | None = None) -> PromptTemplate:
    """Load a versioned template; ``version=None`` resolves the latest on disk."""
    versions = _versions(name)
    if not versions:
        raise FileNotFoundError(f"no prompt template found for {name!r} in {_PROMPT_DIR}")
    v = max(versions) if version is None else version
    if v not in versions:
        raise FileNotFoundError(f"prompt template {name}.v{v}.md not found in {_PROMPT_DIR}")
    text = versions[v].read_text(encoding="utf-8")
    return PromptTemplate(
        name=name,
        version=v,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )

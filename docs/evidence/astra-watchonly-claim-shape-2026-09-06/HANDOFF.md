# Output-location handoff

The requested external directory is readable but outside this session's writable roots.
The complete package is staged in the repository. No copy to the external destination was
attempted. This handoff does not open a production claim, PR, commit, service or soak action.

Run the following only from a session allowed to write the requested evidence directory.
It copies only this report and its companion evidence, refuses differing existing files,
and adds the one INDEX line idempotently. No repository or production state is changed.

```bash
/workspace/ai-investment-cockpit/.venv/bin/python - <<'PY'
from pathlib import Path
import shutil

repo_evidence = Path('/workspace/ai-investment-cockpit/docs/evidence')
destination = Path('/workspace/w24-evidence/astra-drain-2026-09-06')
stem = 'astra-watchonly-claim-shape-2026-09-06'
sources = [repo_evidence / (stem + '.md')]
sources.extend(sorted(p for p in (repo_evidence / stem).rglob('*') if p.is_file()))
copies = [(source, destination / source.relative_to(repo_evidence)) for source in sources]
for source, target in copies:
    if target.exists() and target.read_bytes() != source.read_bytes():
        raise SystemExit(f'Refusing differing existing evidence: {target}')
for source, target in copies:
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
note = (repo_evidence / stem / 'INDEX-note.md').read_text().strip()
index = destination / 'INDEX.md'
prior = index.read_text() if index.exists() else ''
if note not in prior:
    with index.open('a') as stream:
        stream.write(('\n' if prior and not prior.endswith('\n') else '') + note + '\n')
print(f'Copied/verified {len(copies)} evidence files; INDEX note present.')
PY
```

If another actor has already copied a different draft, this intentionally stops before
copying anything; retain both versions explicitly rather than overwriting prior evidence.
The report's absolute links to the original evidence and repository continue to resolve
after copying; links to companion files are relative to the report.

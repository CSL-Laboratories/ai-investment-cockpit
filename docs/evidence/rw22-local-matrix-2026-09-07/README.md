# RW-22 local platform evidence

The package scope, fixes and interpretation are recorded in
[the repair record](../rw22-review-remediation-2026-09-07.md).

`w311`, `w312`, `l311` and `l312` identify native Windows and Ubuntu WSL2 on
Python 3.11 and 3.12. Each cell retains the runner's attestation, source manifest,
Python version, dependency inventory, dependency check, Ruff output, pytest output
and JUnit report. `.gitattributes` preserves these files byte for byte so the
attested SHA-256 hashes remain reproducible after checkout.

`source-binding.json` records a separate reconstruction of the source archive
from Git. Its SHA-256 matches the archive used by every cell, and the working
application matches the committed application subtree.

`environment-w312-initial/` preserves a setup failure before the Windows 3.12
environment had its dependencies installed. That attempt ran no tests and is not
one of the four passing cells. The completed `w312/` evidence comes from the fresh
`w312r` run after installing the dependency versions recorded by `w311/`.

`run_cell.py` is the exact runner used for all four cells. It extracts a commit
archive into a fresh directory, runs the checks with isolated configuration and
short temporary paths, and compares the archived source before and after testing.
`collect_cell.py` copies completed evidence from the workstation's dedicated run
directories, checking each artifact hash before writing it here.

`verify_evidence.py` independently checks the recorded evidence's consistency,
including all four cell identities, pinned source hashes, successful completion,
artifact hashes and agreement between the JUnit counts and pytest summary:

```text
python verify_evidence.py .
```

Its successful JSON output is retained as `summary.json`. This is local execution
and evidence verification, not an external witness or empirical market evidence.

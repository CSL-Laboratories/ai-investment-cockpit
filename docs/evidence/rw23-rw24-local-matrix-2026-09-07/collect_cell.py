"""Copy completed local matrix evidence without changing the test run."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

cell = sys.argv[1]
if cell not in ("w311", "w312", "l311", "l312"):
    raise SystemExit("Unknown matrix cell")
run_name = sys.argv[3] if len(sys.argv) > 3 else cell
if run_name not in (cell, cell + "r"):
    raise SystemExit("Unknown matrix run")
destination = Path(sys.argv[2]).resolve() / cell
if destination.exists():
    raise SystemExit("Refuse to overwrite evidence")

def read_file(name):
    if cell.startswith("w"):
        return (Path("C:/t/r24") / run_name / name).read_bytes()
    return subprocess.check_output([
        "wsl.exe", "-d", "Cockpit-Audit-2404", "--exec", "cat",
        f"/audit/rw24/{run_name}/{name}",
    ])

attestation_bytes = read_file("attestation.json")
attestation = json.loads(attestation_bytes)
if not attestation.get("completed_utc"):
    raise SystemExit("Cell has not completed")
if attestation.get("tracked_source_changed") != []:
    raise SystemExit("Source integrity check failed")
if len(attestation.get("checks", [])) != 5 or any(
    check["returncode"] for check in attestation["checks"]
):
    raise SystemExit("Cell checks did not all pass")
files = {"attestation.json": attestation_bytes}
for name, digest in attestation["artifacts_sha256"].items():
    if Path(name).name != name:
        raise SystemExit("Unsafe evidence name")
    data = read_file(name)
    if hashlib.sha256(data).hexdigest() != digest:
        raise SystemExit("Artifact hash mismatch: " + name)
    files[name] = data
destination.mkdir(parents=True)
for name, data in files.items():
    with (destination / name).open("xb") as handle:
        handle.write(data)
print(json.dumps({"cell": cell, "destination": str(destination), "files": len(files)}))

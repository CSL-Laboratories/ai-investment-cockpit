#!/usr/bin/env bash
# Claude Code cloud-session bootstrap. Local sessions skip it.
# Toolchains (python3 >= 3.11, uv, rustup) come from the cloud environment's
# setup script. This fetches the latest refs (HEAD is not moved), prepares the
# project .venv the gates use (AGENTS.md: run ../.venv/bin/python from app/)
# with CI's pinned run-gates set when present, and prefetches Rust crates. It always exits 0 so the status line below reaches
# Claude's context; each step is marked ok or FAIL there. No secrets
# (invariant 4).
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0
set -uo pipefail
cd "$CLAUDE_PROJECT_DIR" || { echo "cockpit bootstrap: FAIL (cannot enter project dir)"; exit 0; }
export GIT_TERMINAL_PROMPT=0

fetch=ok venv=ok deps=ok rust=ok
py=.venv/bin/python
# CI's pinned install set; when present, cloud sessions use the same versions.
pins=.github/actions/run-gates/requirements.txt

git fetch --quiet origin '+refs/heads/main:refs/remotes/origin/main' || fetch=FAIL
git fetch --quiet --prune origin || fetch=FAIL

# Project virtualenv. A venv that is unusable (no working pip, e.g. half-built
# by a failed `python3 -m venv`) or older than Python 3.11 is rebuilt: first
# with the host python3, then with uv, whose --seed adds pip and which can
# fetch a Python >= 3.11 if the host's is too old.
venv_ok() {
  [ -x "$py" ] && "$py" -m pip --version >/dev/null 2>&1 &&
    "$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null
}
if ! venv_ok; then
  rm -rf .venv
  { python3 -m venv .venv 2>/dev/null && venv_ok; } ||
    { rm -rf .venv; uv venv --quiet --seed --python '>=3.11' .venv 2>/dev/null && venv_ok; } ||
    venv="FAIL(no usable python>=3.11)"
fi

# Every declared requirement and everything it depends on, including the
# packages its extras pull in (e.g. uvicorn[standard]), must be installed at
# an allowed version.
verify_deps() {
  "$py" - <<'EOF'
import os, sys, tomllib
from importlib.metadata import PackageNotFoundError, distribution
try:
    from packaging.requirements import Requirement
except ImportError:
    from pip._vendor.packaging.requirements import Requirement

def lines(path):
    with open(path) as f:
        return [l for l in (x.split("#")[0].strip() for x in f) if l]

with open("app/pyproject.toml", "rb") as f:
    reqs = tomllib.load(f)["project"]["dependencies"]
reqs += lines("app/benchmarks/requirements.txt")
if os.path.exists(".github/actions/run-gates/requirements.txt"):
    reqs += lines(".github/actions/run-gates/requirements.txt")  # CI pins

problems, seen = {}, set()

def need(req, extra=""):
    r = Requirement(req)
    if r.marker and not r.marker.evaluate({"extra": extra}):
        return
    try:
        dist = distribution(r.name)
    except PackageNotFoundError:
        problems[f"{r.name} missing"] = None
        return
    # Check every incoming constraint; `seen` only stops repeated recursion.
    if r.specifier and not r.specifier.contains(dist.version, prereleases=True):
        problems[f"{r.name} {dist.version} not {r.specifier}"] = None
    key = (r.name.lower(), frozenset(r.extras))
    if key in seen:
        return
    seen.add(key)
    # Walk the whole closure: unconditional requirements (extra "") plus
    # those of each requested extra, with platform markers evaluated.
    for ctx in ("", *r.extras):
        for sub in dist.requires or []:
            s = Requirement(sub)
            if s.marker is None or s.marker.evaluate({"extra": ctx}):
                s.marker = None
                need(str(s))

for req in reqs + ["pytest", "ruff"]:
    need(req)
if problems:
    print("deps: " + "; ".join(problems), file=sys.stderr)
sys.exit(1 if problems else 0)
EOF
}

install_deps() {
  local reqs rc=1
  local -a extra=()
  "$py" -m pip install --quiet -U pip || return 1
  reqs="$(mktemp)"
  if "$py" - > "$reqs" <<'EOF'
import tomllib
with open("app/pyproject.toml", "rb") as f:
    deps = tomllib.load(f)["project"]["dependencies"]
print("\n".join(deps + ["pytest", "ruff"]))
EOF
  then
    # One resolver run: CI's pins fix the versions of shared packages, and a
    # dependency added to pyproject.toml but not yet pinned is still installed.
    [ -f "$pins" ] && extra=(-r "$pins")
    "$py" -m pip install --quiet -r "$reqs" -r app/benchmarks/requirements.txt "${extra[@]}" && rc=0
  fi
  rm -f "$reqs"
  return $rc
}

# Reinstall unless the dependency files are unchanged AND the venv still
# verifies, so resume stays cheap. A failed check always drops the stamp.
stamp=.venv/.cloud-deps.sha256
if [ "$venv" = ok ]; then
  dep_files=(app/pyproject.toml app/benchmarks/requirements.txt)
  [ -f "$pins" ] && dep_files+=("$pins")
  want="$(cat "${dep_files[@]}" | sha256sum | cut -d' ' -f1)"
  if [ "$(cat "$stamp" 2>/dev/null)" = "$want" ] && verify_deps 2>/dev/null; then
    deps=ok
  else
    rm -f "$stamp"
    if install_deps && verify_deps; then
      echo "$want" > "$stamp"
    else
      deps=FAIL
    fi
  fi
else
  deps=skipped
fi

if [ "$venv" = ok ] && [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export VIRTUAL_ENV=\"$CLAUDE_PROJECT_DIR/.venv\"" >> "$CLAUDE_ENV_FILE"
  echo "export PATH=\"$CLAUDE_PROJECT_DIR/.venv/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi

# Rust: older rustup installs rust/rust-toolchain.toml's toolchain on first
# cargo use; newer rustup needs `rustup toolchain install`. Without rustup the
# 1.83.0 pin cannot be honored, so that is a failed step, not a skip. The
# maturin wheel build is left to tasks that need it.
if ! command -v rustup >/dev/null 2>&1; then
  rust="FAIL(no rustup)"
elif (cd rust && { rustup toolchain install >/dev/null 2>&1 || true; } && cargo fetch --quiet); then
  rust=ok
else
  rust=FAIL
fi

main="$(git rev-parse --short origin/main 2>/dev/null || echo unknown)"
[ "$fetch" = ok ] || main="$main (stale)"
rustv="$(cd rust 2>/dev/null && rustc --version 2>/dev/null || echo 'rustc unavailable')"
echo "cockpit bootstrap: fetch=$fetch venv=$venv deps=$deps rust=$rust; origin/main $main, HEAD $(git rev-parse --short HEAD 2>/dev/null || echo unknown) (not moved), $rustv"
case "$fetch $venv $deps $rust" in
  *FAIL*) echo "Setup incomplete: fix the FAIL steps above before running the gates." ;;
esac
exit 0

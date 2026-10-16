#!/usr/bin/env bash
# Claude Code cloud-session bootstrap. Local sessions skip it.
# Toolchains (python3 >= 3.11, uv, rustup) come from the cloud environment's
# setup script. This fetches the latest refs (HEAD is not moved), prepares the
# project .venv the gates use (AGENTS.md: run ../.venv/bin/python from app/),
# and prefetches Rust crates. It always exits 0 so the status line below reaches
# Claude's context; each step is marked ok or FAIL there. No secrets
# (invariant 4).
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0
set -uo pipefail
cd "$CLAUDE_PROJECT_DIR" || { echo "cockpit bootstrap: FAIL (cannot enter project dir)"; exit 0; }
export GIT_TERMINAL_PROMPT=0

fetch=ok venv=ok deps=ok rust=skipped
py=.venv/bin/python

git fetch --quiet origin '+refs/heads/main:refs/remotes/origin/main' || fetch=FAIL
git fetch --quiet --prune origin || fetch=FAIL

# Project virtualenv. A venv without a working pip (for example a half-built
# one left by a failed `python3 -m venv`) is rebuilt, falling back to uv,
# whose --seed adds pip.
if [ ! -x "$py" ] || ! "$py" -m pip --version >/dev/null 2>&1; then
  rm -rf .venv
  { python3 -m venv .venv 2>/dev/null && "$py" -m pip --version >/dev/null 2>&1; } ||
    { rm -rf .venv; uv venv --quiet --seed .venv; } || venv=FAIL
fi
if [ "$venv" = ok ] && ! "$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  venv="FAIL(python<3.11)"
fi

# Every declared requirement must be installed at an allowed version,
# including the packages its extras pull in (e.g. uvicorn[standard]).
verify_deps() {
  "$py" - <<'EOF'
import sys, tomllib
from importlib.metadata import PackageNotFoundError, distribution
try:
    from packaging.requirements import Requirement
except ImportError:
    from pip._vendor.packaging.requirements import Requirement

with open("app/pyproject.toml", "rb") as f:
    reqs = tomllib.load(f)["project"]["dependencies"]
with open("app/benchmarks/requirements.txt") as f:
    reqs += [l for l in (x.split("#")[0].strip() for x in f) if l]

problems, seen = [], set()

def need(req, extra=""):
    r = Requirement(req)
    if r.marker and not r.marker.evaluate({"extra": extra}):
        return
    key = (r.name.lower(), frozenset(r.extras))
    if key in seen:
        return
    seen.add(key)
    try:
        dist = distribution(r.name)
    except PackageNotFoundError:
        problems.append(f"{r.name} missing")
        return
    if r.specifier and not r.specifier.contains(dist.version, prereleases=True):
        problems.append(f"{r.name} {dist.version} not {r.specifier}")
    for ex in r.extras:
        for sub in dist.requires or []:
            s = Requirement(sub)
            if s.marker and s.marker.evaluate({"extra": ex}):
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
  reqs="$(mktemp)"
  if "$py" - > "$reqs" <<'EOF'
import tomllib
with open("app/pyproject.toml", "rb") as f:
    deps = tomllib.load(f)["project"]["dependencies"]
print("\n".join(deps + ["pytest", "ruff"]))
EOF
  then
    "$py" -m pip install --quiet -U pip &&
      "$py" -m pip install --quiet -r "$reqs" -r app/benchmarks/requirements.txt && rc=0
  fi
  rm -f "$reqs"
  return $rc
}

# Reinstall unless the dependency files are unchanged AND the venv still
# verifies, so resume stays cheap. A failed check always drops the stamp.
stamp=.venv/.cloud-deps.sha256
if [ "$venv" = ok ]; then
  want="$(cat app/pyproject.toml app/benchmarks/requirements.txt | sha256sum | cut -d' ' -f1)"
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
# cargo use; newer rustup needs `rustup toolchain install`. The maturin wheel
# build is left to tasks that need it.
if command -v rustup >/dev/null 2>&1; then
  if (cd rust && { rustup toolchain install >/dev/null 2>&1 || true; } && cargo fetch --quiet); then
    rust=ok
  else
    rust=FAIL
  fi
fi

main="$(git rev-parse --short origin/main 2>/dev/null || echo unknown)"
[ "$fetch" = ok ] || main="$main (stale)"
rustv="$(cd rust 2>/dev/null && rustc --version 2>/dev/null || echo 'rustc unavailable')"
echo "cockpit bootstrap: fetch=$fetch venv=$venv deps=$deps rust=$rust; origin/main $main, HEAD $(git rev-parse --short HEAD 2>/dev/null || echo unknown) (not moved), $rustv"
case "$fetch $venv $deps $rust" in
  *FAIL*) echo "Setup incomplete: fix the FAIL steps above before running the gates." ;;
esac
exit 0

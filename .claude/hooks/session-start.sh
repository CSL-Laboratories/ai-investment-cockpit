#!/usr/bin/env bash
# Claude Code cloud-session bootstrap. Local sessions skip it.
# Toolchains (rustup, maturin, ruff, pytest) come from the cloud environment's
# setup script; this installs the repo's own dependencies so they track the
# current branch. No secrets: invariant 4 keeps credentials in keyring only.
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0
set -uo pipefail
cd "$CLAUDE_PROJECT_DIR" || exit 0

git fetch --prune --quiet origin || true

# app/pyproject.toml is the source of truth for runtime deps; pytest and ruff
# match .github/actions/run-gates.
reqs="$(mktemp)"
python3 - > "$reqs" <<'EOF' || true
import tomllib
with open("app/pyproject.toml", "rb") as f:
    deps = tomllib.load(f)["project"]["dependencies"]
print("\n".join(deps + ["pytest", "ruff"]))
EOF
python3 -m pip install --quiet -r "$reqs" -r app/benchmarks/requirements.txt || true
rm -f "$reqs"

# Rust: install the toolchain pinned in rust/rust-toolchain.toml if missing,
# then prefetch crates. The maturin wheel build is left to tasks that need it.
if command -v rustup >/dev/null 2>&1; then
  (cd rust && rustup toolchain install >/dev/null 2>&1; cargo fetch --quiet) || true
fi

echo "cockpit: origin/main $(git rev-parse --short origin/main), HEAD $(git rev-parse --short HEAD), $(cd rust && rustc --version 2>/dev/null)"
exit 0

#!/bin/bash
# Action-PATH bun wrapper (CSL-377 canary, hosted 40359709793).
# E3-0-early already ran ``bun install --frozen-lockfile --production
# --ignore-scripts`` in $AP and remounted $AP bind,ro. The pinned
# claude-code-action then does ``cd $GITHUB_ACTION_PATH && bun install
# --production``, which failed in 25ms with
# ``Failed to link @anthropic-ai/sdk: EEXIST``.
#
# This script is installed at /opt/csl-bun-action/bun so
# path_to_bun_executable can point here: the action prepends dirname to
# PATH, then runs ``bun``. Intercept ``install`` only when cwd is $AP;
# verify the 0b node_modules digest and do not invoke the ELF. Any other
# invocation execs /opt/csl-bun/bun. Capture tofu hashes that ELF
# (CSL_BUN_PATH); do not replace it.
# Constants only — no CSL_* environment path overrides (L-d).
set -euo pipefail

BUN_ELF=/opt/csl-bun/bun
AP=/home/runner/work/_actions/anthropics/claude-code-action/a5a423f75c251dcbff8a74e99f83b2cb03c72bfa
PYTHON=/usr/bin/python3
BIND=/opt/csl-claude/lib/sandbox_e3_bind.py

if [ "${1:-}" = "install" ]; then
  if [ ! -d "$AP" ]; then
    echo "e3-action-bun: action dir missing" >&2
    exit 1
  fi
  cwd="$(pwd -P)"
  ap="$(cd "$AP" && pwd -P)"
  if [ "$cwd" = "$ap" ]; then
    exec "$PYTHON" -I "$BIND" check-node-modules
  fi
fi

exec "$BUN_ELF" "$@"

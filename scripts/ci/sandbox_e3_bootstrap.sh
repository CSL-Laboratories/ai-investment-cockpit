#!/bin/bash
# E3-0-early 0b body (CSL-474 D10). Already root via:
#   shell: /usr/bin/sudo -n /bin/bash --noprofile --norc -e -o pipefail {0}
# Does not install the Claude CLI (CSL-473 E3-1), does not rewrite sudoers
# (would break that pin install), does not set SRT_DEBUG, and does not start
# the fd-2 relay (E1). Apparmor sysctl stays in the later sandbox step, after
# these binds.
set -euo pipefail

AP="${CSL_ACTION_DIR:-/home/runner/work/_actions/anthropics/claude-code-action/a5a423f75c251dcbff8a74e99f83b2cb03c72bfa}"
STATE="${CSL_RELAY_STATE:-/var/lib/csl-relay}"
LIB="${CSL_E3_LIB:-/opt/csl-claude/lib}"
BUN_DST=/opt/csl-bun/bun
BUN_BIN=/home/runner/.bun/bin
PYTHON="${PYTHON3:-/usr/bin/python3}"

umask 022
mkdir -p /opt/csl-bun "$BUN_BIN" "$STATE" "$LIB"

if ! command -v bun >/dev/null 2>&1; then
  echo "e3-0b: bun missing after setup-bun" >&2
  exit 1
fi
BUN_SRC="$(command -v bun)"
install -D -m0755 -o root -g root "$BUN_SRC" "$BUN_DST"
sha256sum "$BUN_DST" | awk '{print $1}' > "$STATE/bun.tofu"
# K1: empty runner-owned ~/.bun/bin after the copy; root:root 0755.
CSL_BUN_BIN="$BUN_BIN" "$PYTHON" -I "$LIB/sandbox_e3_bind.py" empty-bun-bin

test -d "$AP" || { echo "e3-0b: pinned action dir missing" >&2; exit 1; }

# E3-3: materialize node_modules (I4) so sdk.mjs exists to hash. Ignore-scripts.
# CSL-473 does not own this; it pins the CLI object, not the action tree.
if [ -f "$AP/package.json" ]; then
  (cd "$AP" && "$BUN_DST" install --frozen-lockfile --production --ignore-scripts)
fi

"$PYTHON" -I "$LIB/sandbox_e3_bind.py" verify-pins
"$PYTHON" -I "$LIB/sandbox_e3_bind.py" check-u16

chown -R root:root "$AP"
# I4: drop group/other write on the action tree including node_modules.
chmod -R go-w "$AP"

# J2 bind order, outermost first. $AP last, then remount ro.
mount --bind /home/runner/work /home/runner/work
mount --bind /home/runner/work/_actions /home/runner/work/_actions
mount --bind /home/runner/work/_actions/anthropics /home/runner/work/_actions/anthropics
mount --bind /home/runner/work/_actions/anthropics/claude-code-action /home/runner/work/_actions/anthropics/claude-code-action
mount --bind "$AP" "$AP"
mount -o remount,bind,ro "$AP"

awk '$5=="'"$AP"'" || $5=="/home/runner/work" || $5=="/home/runner/work/_actions" || $5=="/home/runner/work/_actions/anthropics" || $5=="/home/runner/work/_actions/anthropics/claude-code-action"' /proc/self/mountinfo > "$STATE/mountinfo"
readlink /proc/self/ns/mnt > "$STATE/ns.mnt" || true
"$PYTHON" -I "$LIB/sandbox_e3_bind.py" record-inodes

# U-18 parent-shape hook: record ppid chain. Do not pick fork vs exec here.
tr '\0' ' ' < /proc/$$/cmdline > "$STATE/u18.self.cmdline" || true
if [ -r "/proc/$PPID/cmdline" ]; then
  tr '\0' ' ' < "/proc/$PPID/cmdline" > "$STATE/u18.ppid.cmdline" || true
  readlink "/proc/$PPID/exe" > "$STATE/u18.ppid.exe" || true
fi
printf 'u18_status=[A]\nsee=f-u18.md\n' > "$STATE/u18.record"

# Do not set kernel.apparmor_restrict_unprivileged_userns here (J2(d)).
# Do not rewrite /etc/sudoers (CSL-473 still needs sudo for the CLI pin).
# Do not start capture or set SRT_DEBUG.

#!/bin/bash
# E3-0-early 0b body (CSL-474 D10). Already root via:
#   shell: /usr/bin/sudo -n /bin/bash --noprofile --norc -e -o pipefail {0}
# Constants only — no CSL_* environment path overrides (L-d).
# sudo env_reset drops the workflow step env, including $RUNNER_TEMP;
# paths below are compiled in (same as CSL_RUNNER_TEMP).
# E3-1 CLI pin runs here before sudoers. Capture-start is not in sudoers.
set -euo pipefail

AP=/home/runner/work/_actions/anthropics/claude-code-action/a5a423f75c251dcbff8a74e99f83b2cb03c72bfa
SETUP_BUN_DIR=/home/runner/work/_actions/oven-sh/setup-bun/0c5077e51419868618aeaa5fe8019c62421857d6
STATE=/var/lib/csl-relay
LIB=/opt/csl-claude/lib
SRC=/opt/csl-claude/src
BUN_DST=/opt/csl-bun/bun
BUN_BIN=/home/runner/.bun/bin
PYTHON=/usr/bin/python3
GCC=/usr/bin/gcc
SUDOERS_TEXT='Defaults env_reset
Defaults secure_path="/usr/sbin:/usr/bin:/sbin:/bin"
runner ALL=(root) NOPASSWD: /opt/csl-relay/bin/relay
'

umask 022
mkdir -p /opt/csl-bun /opt/csl-claude/lib /opt/csl-claude/src /opt/csl-relay/bin \
  "$BUN_BIN" "$STATE"
chmod 0711 "$STATE"
chown root:root "$STATE"

# Walk $$ → ppid until exe basename is Runner.Worker (N2). Do not persist
# /proc/PID/environ (may contain GH_TOKEN). worker.status is 0600.
WORKER_PID=""
cur=$$
for _ in 1 2 3 4 5 6 7 8; do
  pp="$(awk -F')' '{print $2}' "/proc/$cur/stat" | awk '{print $2}')"
  [ -n "$pp" ] && [ "$pp" != "0" ] || break
  exe="$(readlink "/proc/$pp/exe" || true)"
  base="${exe##*/}"
  if [ "$base" = "Runner.Worker" ]; then
    WORKER_PID="$pp"
    break
  fi
  cur="$pp"
done
if [ -z "$WORKER_PID" ]; then
  echo "e3-0b: Runner.Worker not found in parent chain" >&2
  exit 1
fi
printf '%s\n' "$WORKER_PID" > "$STATE/worker.pid"
chmod 0600 "$STATE/worker.pid"
readlink /proc/self/ns/mnt > "$STATE/ns.mnt"
chmod 0600 "$STATE/ns.mnt"
cat /proc/"$WORKER_PID"/status > "$STATE/worker.status"
chmod 0600 "$STATE/worker.status"

# Launcher-owned empty MCP (N1). Exact bytes, root 0444.
printf '%s\n' '{"mcpServers":{}}' > /opt/csl-claude/mcp.empty.json
chown root:root /opt/csl-claude/mcp.empty.json
chmod 0444 /opt/csl-claude/mcp.empty.json

# 0b step 1: compile static binaries from workflow-embedded sources. Pin check.
test -d "$SRC" || { echo "e3-0b: C sources missing" >&2; exit 1; }
for f in sandbox_common.h sandbox_common.c sandbox_filter.h sandbox_filter.c \
         sandbox_argv.h sandbox_argv.c sandbox_launch.c sandbox_capture.c \
         sandbox_relay.c; do
  test -f "$SRC/$f" || { echo "e3-0b: missing $f" >&2; exit 1; }
done
# Runtime .pin enforcement (H1): sha256 of embedded sources vs committed pins.
test -f "$LIB/sandbox_c.pin" || { echo "e3-0b: sandbox_c.pin missing" >&2; exit 1; }
(cd "$SRC" && sha256sum -c "$LIB/sandbox_c.pin")
"$GCC" -static -O2 -Wall -Werror -o /opt/csl-claude/launch \
  "$SRC/sandbox_launch.c" "$SRC/sandbox_argv.c" "$SRC/sandbox_common.c" -I"$SRC"
"$GCC" -static -O2 -Wall -Werror -o /opt/csl-relay/bin/capture-start \
  "$SRC/sandbox_capture.c" "$SRC/sandbox_common.c" -I"$SRC"
"$GCC" -static -O2 -Wall -Werror -o /opt/csl-relay/bin/relay \
  "$SRC/sandbox_relay.c" "$SRC/sandbox_filter.c" "$SRC/sandbox_common.c" -I"$SRC"
install -o root -g root -m0755 /opt/csl-relay/bin/relay /opt/csl-relay/bin/filter
chown root:root /opt/csl-claude/launch /opt/csl-relay/bin/capture-start \
  /opt/csl-relay/bin/relay /opt/csl-relay/bin/filter
chmod 0755 /opt/csl-claude/launch /opt/csl-relay/bin/capture-start \
  /opt/csl-relay/bin/relay /opt/csl-relay/bin/filter
sha256sum /opt/csl-claude/launch | awk '{print $1}' > "$STATE/launch.sha256"
chown root:root "$STATE/launch.sha256"
chmod 0600 "$STATE/launch.sha256"
# Directory containing launch only (plus lib/src which are not in the launch dir).
test -x /opt/csl-claude/launch
test -x /opt/csl-relay/bin/relay
test -s "$STATE/launch.sha256"

# Never PATH-lookup bun: sudo secure_path is /usr/sbin:/usr/bin:/sbin:/bin
# and hides setup-bun's ~/.bun/bin. The runner-uid record step wrote the
# setup-bun bun-path output to this compiled-in file.
BUN_SRC="/home/runner/.bun/bin/bun"
BUN_PATH_FILE="/home/runner/work/_temp/csl-e3-bun-path"
if [ ! -f "$BUN_PATH_FILE" ]; then
  echo "e3-0b: bun-path file missing" >&2
  exit 1
fi
got="$(tr -d '\n' < "$BUN_PATH_FILE")"
if [ "$got" != "$BUN_SRC" ]; then
  echo "e3-0b: bun-path '$got' want $BUN_SRC" >&2
  exit 1
fi
if [ ! -x "$BUN_SRC" ]; then
  echo "e3-0b: bun missing after setup-bun at $BUN_SRC" >&2
  exit 1
fi
ver="$("$BUN_SRC" --version)"
if [ "$ver" != "1.3.14" ]; then
  echo "e3-0b: bun version '$ver' want 1.3.14" >&2
  exit 1
fi
install -D -m0755 -o root -g root "$BUN_SRC" "$BUN_DST"
sha256sum "$BUN_DST" | awk '{print $1}' > "$STATE/bun.tofu"
chmod 0644 "$STATE/bun.tofu"
"$PYTHON" -I "$LIB/sandbox_e3_environ.py" record-bun
"$PYTHON" -I "$LIB/sandbox_e3_bind.py" empty-bun-bin
"$PYTHON" -I "$LIB/sandbox_e3_bind.py" hash-setup-bun

# 0b step 2: fixer helper was written as root then installed as runner-uid
# (sudo env_reset drops REPO/PR_NUMBER/…). Root only chowns and verifies.
GUARD="/home/runner/work/_temp/claude-fixer-guard"
if [ -f "$GUARD/model-api" ]; then
  test -f "$GUARD/context.json" || { echo "e3-0b: helper context.json missing" >&2; exit 1; }
  chown -R root:root "$GUARD"
  chmod 0755 "$GUARD" "$GUARD/model-api"
  chmod 0644 "$GUARD/context.json"
  if [ -e "$GUARD/read-token" ]; then chmod 0644 "$GUARD/read-token"; fi
  test "$(stat -c %U "$GUARD/model-api")" = root
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends bubblewrap socat gcc libc6-dev
command -v bwrap
command -v socat

# 0b step 3: docker / containerd / podman / lxd stop+mask (F12).
for unit in docker.socket docker.service containerd.socket containerd.service \
            podman.socket podman.service lxd.socket snap.lxd.daemon; do
  systemctl stop "$unit" 2>/dev/null || true
  systemctl mask "$unit" 2>/dev/null || true
done

# 0b step 4: ptrace_scope=2 (fail closed if Yama missing).
test -f /proc/sys/kernel/yama/ptrace_scope
sysctl -w kernel.yama.ptrace_scope=2

# 0b step 5: nsswitch / sudo.conf (U-16).
"$PYTHON" -I "$LIB/sandbox_e3_bind.py" check-u16

# 0b step 6: drop runner from sudo/adm/lxd/docker (G10). Fail closed if still
# listed in the group file after the drop.
drop_group() {
  local g="$1"
  if getent group "$g" >/dev/null; then
    if getent group "$g" | grep -qE '[:,]runner(,|$)'; then
      gpasswd -d runner "$g"
    fi
    if getent group "$g" | grep -qE '[:,]runner(,|$)'; then
      echo "e3-0b: runner still in $g" >&2
      exit 1
    fi
  fi
}
drop_group sudo
drop_group adm
drop_group lxd
drop_group docker
id runner | tee "$STATE/id.runner" >/dev/null
for sock in /var/run/docker.sock /var/snap/lxd/common/lxd/unix.socket \
            /run/containerd/containerd.sock /run/podman/podman.sock; do
  if [ -S "$sock" ]; then
    mode=$(stat -c %a "$sock")
    case "$mode" in
      *2|*3|*6|*7) echo "e3-0b: group-writable socket $sock" >&2; exit 1 ;;
    esac
  fi
done

test -d "$AP" || { echo "e3-0b: pinned action dir missing" >&2; exit 1; }

# 0b step 8: E3-1 CLI pin then E3-3 bun install, before sudoers.
test -f "$LIB/sandbox_e3_cli_pin.sh" || { echo "e3-0b: cli_pin missing" >&2; exit 1; }
/bin/bash --noprofile --norc -e -o pipefail "$LIB/sandbox_e3_cli_pin.sh"
if [ -f "$AP/package.json" ]; then
  (cd "$AP" && "$BUN_DST" install --frozen-lockfile --production --ignore-scripts)
fi

# 0b step 9: git-tree hash, extra-file check, node_modules digest, then bind.
# hash-git-tree before $AP/bin/bun: check_git_tree does not exclude bin/, so
# pre-seeding bun first would fail the extra-file check or punch a hole in it.
command -v git >/dev/null 2>&1 || { echo "e3-0b: git missing" >&2; exit 1; }
REF="$STATE/cca.git"
git init --bare "$REF"
git -C "$REF" fetch --depth 1 https://github.com/anthropics/claude-code-action.git \
  a5a423f75c251dcbff8a74e99f83b2cb03c72bfa
git -C "$REF" ls-tree -r FETCH_HEAD > "$STATE/cca.ls-tree"
"$PYTHON" -I "$LIB/sandbox_e3_bind.py" hash-git-tree
BUNREF="$STATE/setup-bun.git"
git init --bare "$BUNREF"
git -C "$BUNREF" fetch --depth 1 https://github.com/oven-sh/setup-bun.git \
  0c5077e51419868618aeaa5fe8019c62421857d6
git -C "$BUNREF" ls-tree -r FETCH_HEAD > "$STATE/setup-bun.ls-tree"
"$PYTHON" -I "$LIB/sandbox_e3_bind.py" verify-pins
"$PYTHON" -I "$LIB/sandbox_e3_bind.py" hash-node-modules
# Pre-create the action's pin-bun destination after the extra-file check and
# before the ro remount so mkdir -p "$AP/bin" is a no-op. Touch/mkdir after
# the remount fail closed if $AP is still writable.
mkdir -p "$AP/bin"
install -D -m0755 -o root -g root "$BUN_DST" "$AP/bin/bun"
chown -R root:root "$AP"
chmod -R go-w "$AP"

# J2 bind order, outermost first. $AP last, then remount ro.
mount --bind /home/runner/work /home/runner/work
mount --bind /home/runner/work/_actions /home/runner/work/_actions
mount --bind /home/runner/work/_actions/anthropics /home/runner/work/_actions/anthropics
mount --bind /home/runner/work/_actions/anthropics/claude-code-action /home/runner/work/_actions/anthropics/claude-code-action
mount --bind "$AP" "$AP"
mount -o remount,bind,ro "$AP"
if touch "$AP/csl-e3-ro-probe" 2>/dev/null; then
  echo "e3-0b: $AP still writable (touch)" >&2
  exit 1
fi
if mkdir "$AP/csl-e3-ro-dir" 2>/dev/null; then
  echo "e3-0b: $AP still writable (mkdir)" >&2
  exit 1
fi

awk '$5=="'"$AP"'" || $5=="/home/runner/work" || $5=="/home/runner/work/_actions" || $5=="/home/runner/work/_actions/anthropics" || $5=="/home/runner/work/_actions/anthropics/claude-code-action"' /proc/self/mountinfo > "$STATE/mountinfo"
# Worker ns/mnt must equal 0b's (fail closed, H4). Compare Worker with
# this process, not Worker with an earlier snapshot of itself.
test -s "$STATE/ns.mnt"
test "$(readlink /proc/self/ns/mnt)" = "$(readlink /proc/"$WORKER_PID"/ns/mnt)"
test "$(readlink /proc/"$WORKER_PID"/ns/mnt)" = "$(cat "$STATE/ns.mnt")"
"$PYTHON" -I "$LIB/sandbox_e3_bind.py" record-inodes

# 0b step 10: ~/.claude empty, runner-owned 0700.
rm -rf /home/runner/.claude /home/runner/.claude.json
mkdir -m 0700 /home/runner/.claude
chown runner:runner /home/runner/.claude
rm -f /home/runner/.bunfig.toml /home/runner/.npmrc /home/runner/.docker/config.json
if [ -d /home/runner/.docker ] && [ -z "$(ls -A /home/runner/.docker 2>/dev/null || true)" ]; then
  rmdir /home/runner/.docker || true
fi

# 0b step 11: exact sudoers, rename-atomic, visudo -cf (F10). No NOPASSWD:ALL.
# GNU install exits 1 when src and dst are the same file; stage via mktemp.
SUDOERS_SRC="$(mktemp)"
printf '%s' "$SUDOERS_TEXT" > "$SUDOERS_SRC"
install -m0440 -o root -g root "$SUDOERS_SRC" /etc/sudoers.new
rm -f "$SUDOERS_SRC"
visudo -cf /etc/sudoers.new
# fsync via python (already root).
"$PYTHON" -I -c 'import os; fd=os.open("/etc/sudoers.new", os.O_RDONLY); os.fsync(fd); os.close(fd)'
mv -f /etc/sudoers.new /etc/sudoers
if [ -d /etc/sudoers.d ]; then
  find /etc/sudoers.d -mindepth 1 -maxdepth 1 -exec rm -rf {} +
fi

# 0b step 12: sweep runner-uid extras (record; fail closed on unknown), then arm.
ALLOW_PIDS="$$ $PPID $WORKER_PID"
ps -eo pid,user,ppid,args --no-headers | awk '$2=="runner"{print}' > "$STATE/sweep.runner" || true
# Inventory extras as veto-only records (G1(d)).
ps -eo pid,user,args --no-headers | awk '$2=="root"{print}' > "$STATE/sweep.root" || true
crontab -u runner -l > "$STATE/cron.runner" 2>/dev/null || true
printf '%s\n' "/home/runner/work/ai-investment-cockpit/ai-investment-cockpit" > "$STATE/workspace"
chmod 0644 "$STATE/workspace"

# Start capture daemon (empty argv, not in sudoers) then bind 0600.
/opt/csl-relay/bin/capture-start >/var/lib/csl-relay/daemon.log 2>&1 &
echo $! > "$STATE/daemon.pid"
sleep 0.2
test -S "$STATE/handoff.sock"
# Socket stays 0600 until F1 observation (daemon chmod 0666).
chmod 0600 "$STATE/handoff.sock"

# Apparmor sysctl AFTER the sweep (J2(d)).
if [ -f /proc/sys/kernel/apparmor_restrict_unprivileged_userns ]; then
  sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
fi

# Invoke environ/record so they are not dead (H4 / N8). C also checks bun X
# environ in F1 without writing /proc/PID/environ (N9).
"$PYTHON" -I "$LIB/sandbox_e3_bind.py" observe
"$PYTHON" -I "$LIB/sandbox_e3_environ.py" check-ld
"$PYTHON" -I "$LIB/sandbox_e3_record.py" write

# 0b step 13: negative controls as runner (T3/T7). Step fails if any succeeds.
fail_closed() {
  local desc="$1"
  shift
  if runuser -u runner -- "$@" >/dev/null 2>&1; then
    echo "e3-0b: negative control succeeded (want fail): $desc" >&2
    exit 1
  fi
}
fail_closed "sudo -n /bin/true" sudo -n /bin/true
fail_closed "sudo -n -u nobody /bin/true" sudo -n -u nobody /bin/true
fail_closed "sudo -n -u nobody relay" sudo -n -u nobody /opt/csl-relay/bin/relay
fail_closed "sudo -n -E relay" sudo -n -E /opt/csl-relay/bin/relay
fail_closed "sudo -n FOO=1 relay" sudo -n FOO=1 /opt/csl-relay/bin/relay
fail_closed "sudo -n filter" sudo -n /opt/csl-relay/bin/filter
fail_closed "sudo -n launch" sudo -n /opt/csl-claude/launch
runuser -u runner -- sudo -l > "$STATE/sudo.l"
grep -q 'NOPASSWD: ALL' "$STATE/sudo.l" && { echo "e3-0b: NOPASSWD:ALL remains" >&2; exit 1; }
grep -q SETENV "$STATE/sudo.l" && { echo "e3-0b: SETENV remains" >&2; exit 1; }
grep -q '/opt/csl-relay/bin/relay' "$STATE/sudo.l" || {
  echo "e3-0b: sudo -l missing relay" >&2
  exit 1
}

# 0b step 14: exact sudoers bytes (not a vacuous grep -qv).
printf '%s' "$SUDOERS_TEXT" > "$STATE/sudoers.want"
cmp -s /etc/sudoers "$STATE/sudoers.want" || {
  echo "e3-0b: /etc/sudoers does not match compiled-in text" >&2
  exit 1
}
grep -q 'NOPASSWD: /opt/csl-relay/bin/relay' /etc/sudoers
grep -q 'NOPASSWD: ALL' /etc/sudoers && { echo "e3-0b: NOPASSWD:ALL in sudoers" >&2; exit 1; }

# U-18 parent-shape hook: record ppid chain. Do not pick fork vs exec here.
tr '\0' ' ' < /proc/$$/cmdline > "$STATE/u18.self.cmdline" || true
if [ -r "/proc/$PPID/cmdline" ]; then
  tr '\0' ' ' < "/proc/$PPID/cmdline" > "$STATE/u18.ppid.cmdline" || true
  readlink "/proc/$PPID/exe" > "$STATE/u18.ppid.exe" || true
fi
printf 'u18_status=[A]\nsee=f-u18.md\naccepted_shapes=sudo_fork,sudo_exec\n' > "$STATE/u18.record"

# Do not set SRT_DEBUG. Capture daemon is already running.

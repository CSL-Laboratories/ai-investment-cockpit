#!/bin/bash
# E1 install: relay, launcher, FIFO, U-16/U-18 hooks. Already root.
# Does not set SRT_DEBUG. Does not download the Claude CLI (CSL-473).
set -euo pipefail
STATE="${CSL_RELAY_STATE:-/var/lib/csl-relay}"
LIB="${CSL_E3_LIB:-/opt/csl-claude/lib}"
RELAY_BIN=/opt/csl-relay/bin
LAUNCH=/opt/csl-claude/launch
PYTHON="${PYTHON3:-/usr/bin/python3}"

mkdir -p "$STATE" "$RELAY_BIN" /opt/csl-claude
# Modules the relay imports live next to it so python3 -I + sys.path insert works.
cp -f "$LIB/sandbox_debug_filter.py" "$RELAY_BIN/sandbox_debug_filter.py"
cp -f "$LIB/sandbox_e3_record.py" "$RELAY_BIN/sandbox_e3_record.py"
cp -f "$LIB/sandbox_e1_hooks.py" "$RELAY_BIN/sandbox_e1_hooks.py"
cp -f "$LIB/sandbox_relay.py" "$RELAY_BIN/relay"
cp -f "$LIB/sandbox_launcher.py" "$LAUNCH"
chmod 0755 "$RELAY_BIN/relay" "$LAUNCH"
chmod 0644 "$RELAY_BIN/sandbox_debug_filter.py" "$RELAY_BIN/sandbox_e3_record.py" \
  "$RELAY_BIN/sandbox_e1_hooks.py"
chown root:root "$RELAY_BIN/relay" "$LAUNCH" || true

FIFO="$STATE/stderr.fifo"
if [ ! -p "$FIFO" ]; then
  mkfifo -m 0600 "$FIFO"
fi
chown root:root "$FIFO" || true
chmod 0600 "$FIFO"

"$PYTHON" -I "$LIB/sandbox_e1_hooks.py"
# Do not set SRT_DEBUG, ACTIONS_STEP_DEBUG, or DEBUG_CLAUDE_AGENT_SDK here.

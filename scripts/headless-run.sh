#!/usr/bin/env bash
# Run a command inside a private headless GNOME compositor: nothing appears on the real screen.
# Uses a throwaway XDG home, app id and D-Bus session, so it never touches real settings, a running
# instance, the real tray or the shortcuts portal (whose approval dialog would open on the real screen).
#   scripts/headless-run.sh bin/ai-linux-app --screenshot /tmp/x.png --page home --state listening
set -euo pipefail
if [ -z "${AILX_PRIVATE_BUS:-}" ] && [ -z "${AILX_REAL_BUS:-}" ]; then
  export AILX_PRIVATE_BUS=1
  exec dbus-run-session --config-file="$(dirname "$(readlink -f "$0")")/headless-dbus.conf" -- "$0" "$@"
fi
if [ -n "${AILX_PRIVATE_BUS:-}" ]; then
  export GTK_A11Y="${GTK_A11Y:-none}"  # the private bus has no accessibility bus to find
fi
name="ailx-headless-$$"
tmp="$(mktemp -d)"
mutter --headless --no-x11 --wayland-display="$name" --virtual-monitor "${AILX_MONITOR:-1280x800}" >"$tmp/mutter.log" 2>&1 &
pid=$!
trap 'kill $pid 2>/dev/null; rm -rf "$tmp"' EXIT
for _ in $(seq 1 80); do [ -S "$XDG_RUNTIME_DIR/$name" ] && break; sleep 0.25; done
if [ -z "${AILX_REAL_HOME:-}" ]; then
  export XDG_CONFIG_HOME="$tmp/config" XDG_DATA_HOME="$tmp/data" XDG_STATE_HOME="$tmp/state" XDG_CACHE_HOME="$tmp/cache"
fi
export AI_LINUX_APP_ID="${AI_LINUX_APP_ID:-io.github.itsdigvijaysing.AILinux.Test$$}"
WAYLAND_DISPLAY="$name" GDK_BACKEND=wayland DISPLAY= timeout "${AILX_TIMEOUT:-30}" "$@"

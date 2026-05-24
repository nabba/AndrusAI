#!/usr/bin/env bash
# Install the host-side SMART telemetry collector as a launchd
# LaunchAgent on macOS (Gap #11, 2026-05-24). Mirrors the install
# shape of warm_spare_host.plist and gateway_watchdog.plist.
#
# Requires `smartctl` (brew install smartmontools). The collector
# tolerates its absence and writes a tool-error row, which the
# gateway monitor surfaces as a Signal alert.

set -euo pipefail

PLIST_SRC="$(cd "$(dirname "$0")" && pwd)/host_smart_collector.plist"
PLIST_DST="$HOME/Library/LaunchAgents/org.andrus.botarmy.host-smart-collector.plist"
LABEL="org.andrus.botarmy.host-smart-collector"

cmd="${1:-install}"

case "$cmd" in
  install)
    if ! command -v smartctl >/dev/null 2>&1; then
      echo "⚠ smartctl not installed."
      echo "  brew install smartmontools"
      echo "  then re-run this installer."
      echo ""
      echo "Continuing install — the LaunchAgent will record a"
      echo "tool-error row that the gateway monitor surfaces. Once"
      echo "smartctl is present, the next 04:00 pass produces real data."
    fi
    mkdir -p "$HOME/Library/LaunchAgents"
    cp "$PLIST_SRC" "$PLIST_DST"
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
    echo "✓ Installed and loaded $LABEL"
    echo "  Schedule: daily at 04:00 local time"
    echo "  Logs:     ~/BotArmy/crewai-team/workspace/healing/.host_smart.log"
    echo ""
    echo "Smoke test with one immediate pass:"
    echo "  $0 start"
    echo "Dry-run preview (no file IO):"
    echo "  python3 scripts/host_smart_collector.py --dry-run"
    ;;
  start)
    launchctl kickstart -p "gui/$(id -u)/$LABEL"
    echo "✓ Kicked off one collector pass. Tail the log:"
    echo "  tail -F ~/BotArmy/crewai-team/workspace/healing/.host_smart.log"
    ;;
  restart)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
    echo "✓ Restarted $LABEL"
    ;;
  stop)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    echo "✓ Stopped $LABEL"
    ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST_DST"
    echo "✓ Uninstalled $LABEL"
    ;;
  *)
    echo "Usage: $0 {install|start|restart|stop|uninstall}"
    exit 1
    ;;
esac

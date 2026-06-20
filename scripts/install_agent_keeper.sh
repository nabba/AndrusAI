#!/usr/bin/env bash
# Install the host-side agent-keeper as a launchd LaunchAgent.
#
# The keeper runs scripts/ensure_host_agents.sh every ~180s and re-bootstraps
# any botarmy LaunchAgent that has fallen off (unless the operator explicitly
# `launchctl disable`d it) — including the gateway watchdog. It is the outer
# backstop that closes the "who watches the watchdog" gap; the watchdog guards
# the keeper in return.
#
# Logs:  workspace/healing/.agent_keeper.log
#
# Usage:
#   ./scripts/install_agent_keeper.sh install    # link + load
#   ./scripts/install_agent_keeper.sh start      # run one sweep now (kickstart)
#   ./scripts/install_agent_keeper.sh restart    # reload the plist
#   ./scripts/install_agent_keeper.sh stop       # unload the agent
#   ./scripts/install_agent_keeper.sh uninstall  # unload + remove
#   ./scripts/install_agent_keeper.sh status     # per-agent loaded/disabled/DOWN

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PLIST_SRC="$HERE/agent_keeper.plist"
PLIST_DST="$HOME/Library/LaunchAgents/org.andrus.botarmy.agent-keeper.plist"
LABEL="org.andrus.botarmy.agent-keeper"
LOG_DIR="/Users/andrus/BotArmy/crewai-team/workspace/healing"

cmd="${1:-install}"

case "$cmd" in
  install)
    mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
    cp "$PLIST_SRC" "$PLIST_DST"
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
    echo "✓ Installed and loaded $LABEL"
    echo "  Sweeps every 180s; re-bootstraps any down botarmy LaunchAgent."
    echo "  Skips agents you have 'launchctl disable'd."
    echo "  Logs: $LOG_DIR/.agent_keeper.log"
    ;;
  start)
    launchctl kickstart "gui/$(id -u)/$LABEL"
    echo "✓ Kickstarted one sweep of $LABEL"
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
  status)
    if launchctl list | grep -q "$LABEL"; then
      launchctl list | grep "$LABEL"
    else
      echo "$LABEL not loaded."
    fi
    echo "── per-agent state ──"
    /bin/bash "$HERE/ensure_host_agents.sh" status
    ;;
  *)
    echo "Usage: $0 {install|start|restart|stop|uninstall|status}"
    exit 1
    ;;
esac

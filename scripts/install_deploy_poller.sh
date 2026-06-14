#!/usr/bin/env bash
# Install the host-side git-pull deploy poller as a launchd LaunchAgent.
#
# Pull-based auto-deploy: every ~180 s it fetches origin and, when origin/main
# is a clean fast-forward ahead of the local checkout, runs
# scripts/deploy_gateway.sh — so a merged PR redeploys the gateway. No inbound
# port, no public exposure, no GitHub-side webhook (the host reaches out;
# nothing reaches in). This is the pull-based alternative to the #133 inbound
# webhook (scripts/deploy_webhook.py) and needs no secret or Funnel.
#
# Logs:  workspace/healing/.deploy_poller.log
# Lock/state:  ~/.crewai-bridge/deploy_poller.{lock,_state.json}  (outside the repo)
#
# Usage:
#   ./scripts/install_deploy_poller.sh install     # load the LaunchAgent
#   ./scripts/install_deploy_poller.sh restart      # reload the plist
#   ./scripts/install_deploy_poller.sh stop         # unload the agent
#   ./scripts/install_deploy_poller.sh uninstall    # unload + remove the plist
#   ./scripts/install_deploy_poller.sh status       # is it loaded?
#   ./scripts/install_deploy_poller.sh run-once      # run one poll now (verify)
#   ./scripts/install_deploy_poller.sh logs          # tail the poller log

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_SRC="$REPO_ROOT/scripts/deploy_poller.plist"
PLIST_DST="$HOME/Library/LaunchAgents/org.andrus.botarmy.deploy-poller.plist"
LABEL="org.andrus.botarmy.deploy-poller"
LOG_DIR="$REPO_ROOT/workspace/healing"
LOG_FILE="$LOG_DIR/.deploy_poller.log"

cmd="${1:-install}"

case "$cmd" in
  install)
    mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
    cp "$PLIST_SRC" "$PLIST_DST"
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
    echo "✓ Installed and loaded $LABEL"
    echo "  Polls:    git fetch origin main every 180s (on the host)"
    echo "  Trigger:  origin/main fast-forward ahead → scripts/deploy_gateway.sh"
    echo "  Logs:     $LOG_FILE"
    echo "  Verify:   $0 run-once   (should print 'up to date' when in sync)"
    ;;
  restart)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
    echo "✓ Restarted $LABEL"
    ;;
  stop)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    echo "✓ Stopped $LABEL (deploys are manual again until reinstalled)"
    ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST_DST"
    echo "✓ Uninstalled $LABEL"
    ;;
  status)
    if launchctl list | grep -q "$LABEL"; then
      launchctl list | grep "$LABEL"
      echo "(PID / last_exit_code / label above; PID is usually '-' since this is a"
      echo " short-lived one-shot launchd reschedules every 180s)"
    else
      echo "Not loaded."
    fi
    ;;
  run-once)
    # Run one poll synchronously with the same env the plist sets, so the
    # verification path is identical to the scheduled path.
    echo "▶ running one poll (deploys only if origin/main is fast-forward ahead)…"
    DEPLOY_POLLER_BRANCH="${DEPLOY_POLLER_BRANCH:-main}" \
    DEPLOY_POLLER_REMOTE="${DEPLOY_POLLER_REMOTE:-origin}" \
    DEPLOY_SCRIPT="${DEPLOY_SCRIPT:-$REPO_ROOT/scripts/deploy_gateway.sh}" \
    DEPLOY_POLLER_LOG="${DEPLOY_POLLER_LOG:-$LOG_FILE}" \
    DEPLOY_POLLER_LOCK="${DEPLOY_POLLER_LOCK:-$HOME/.crewai-bridge/deploy_poller.lock}" \
    DEPLOY_POLLER_STATE="${DEPLOY_POLLER_STATE:-$HOME/.crewai-bridge/deploy_poller_state.json}" \
    PATH="/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin" \
      /usr/bin/python3 -u "$REPO_ROOT/scripts/deploy_poller.py"
    echo "✓ poll complete (see $LOG_FILE for any deploy output)"
    ;;
  logs)
    if [ -f "$LOG_FILE" ]; then tail -n 40 "$LOG_FILE"; else echo "(no log yet at $LOG_FILE)"; fi
    ;;
  *)
    echo "Usage: $0 {install|restart|stop|uninstall|status|run-once|logs}"
    exit 1
    ;;
esac

#!/usr/bin/env bash
# Install the host-side deploy webhook as a launchd LaunchAgent.
#
# Receives a GitHub webhook and, on a merge to the deploy branch (default
# main), runs scripts/deploy_gateway.sh — so a merged PR redeploys the gateway
# with no terminal. Runs on the host because the deploy is a host docker build.
#
# The HMAC secret is generated here into ~/.crewai-bridge/deploy_webhook_secret
# (gitignored, never committed); the plist references it by path. `install`
# prints the secret + the GitHub/Funnel setup steps. `secret` reprints it.
#
# Logs:  workspace/healing/.deploy_webhook.log
#
# Usage:
#   ./scripts/install_deploy_webhook.sh install     # generate secret + load
#   ./scripts/install_deploy_webhook.sh restart     # reload the plist
#   ./scripts/install_deploy_webhook.sh stop        # unload the agent
#   ./scripts/install_deploy_webhook.sh uninstall   # unload + remove (keeps secret)
#   ./scripts/install_deploy_webhook.sh status      # is it loaded?
#   ./scripts/install_deploy_webhook.sh secret      # print the HMAC secret
#   ./scripts/install_deploy_webhook.sh setup-help  # GitHub + Funnel steps

set -euo pipefail

PLIST_SRC="$(cd "$(dirname "$0")" && pwd)/deploy_webhook.plist"
PLIST_DST="$HOME/Library/LaunchAgents/org.andrus.botarmy.deploy-webhook.plist"
LABEL="org.andrus.botarmy.deploy-webhook"
LOG_DIR="/Users/andrus/BotArmy/crewai-team/workspace/healing"
SECRET_FILE="$HOME/.crewai-bridge/deploy_webhook_secret"
PORT="9200"

ensure_secret() {
  if [ ! -s "$SECRET_FILE" ]; then
    mkdir -p "$(dirname "$SECRET_FILE")"
    # 64 hex chars of CSPRNG; openssl is present on macOS.
    openssl rand -hex 32 > "$SECRET_FILE"
    chmod 600 "$SECRET_FILE"
    echo "✓ Generated new HMAC secret at $SECRET_FILE"
  fi
}

setup_help() {
  cat <<EOF

── Wire the webhook to GitHub ────────────────────────────────────────────
1. Expose the loopback listener to GitHub via Tailscale Funnel:
     tailscale funnel --bg $PORT
   (Funnel gives you an https://<host>.ts.net URL. Note it.)

2. GitHub → repo Settings → Webhooks → Add webhook:
     Payload URL:   https://<host>.ts.net/        (the Funnel URL)
     Content type:  application/json
     Secret:        (paste the value of: $0 secret)
     Events:        "Pull requests" (and/or "Pushes")
   Save. GitHub sends a 'ping' — the log should show "event='ping' → ping".

3. Merge a PR into main → the log shows "DEPLOY START" and the gateway
   rebuilds. Tail it:
     tail -F $LOG_DIR/.deploy_webhook.log

Security: every request must carry a valid X-Hub-Signature-256 HMAC over the
body (keyed by the secret above) or it's rejected 401. Only merges to the
deploy branch trigger a build. Bind stays loopback; Funnel + HMAC are the
boundary. Rotate the secret by deleting $SECRET_FILE and re-running install.
EOF
}

cmd="${1:-install}"

case "$cmd" in
  install)
    mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
    ensure_secret
    cp "$PLIST_SRC" "$PLIST_DST"
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
    echo "✓ Installed and loaded $LABEL"
    echo "  Listens:  http://127.0.0.1:$PORT  (POST = GitHub webhook, GET /healthz = liveness)"
    echo "  Trigger:  merge to main → scripts/deploy_gateway.sh"
    echo "  Secret:   $SECRET_FILE"
    echo "  Logs:     $LOG_DIR/.deploy_webhook.log"
    echo ""
    echo "  HMAC secret (paste into the GitHub webhook config):"
    echo "    $(cat "$SECRET_FILE")"
    setup_help
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
    echo "✓ Uninstalled $LABEL (secret file kept at $SECRET_FILE)"
    ;;
  status)
    if launchctl list | grep -q "$LABEL"; then
      launchctl list | grep "$LABEL"
      echo "(PID / last_exit_code / label above; PID should be non-'-' since this is a daemon)"
    else
      echo "Not loaded."
    fi
    ;;
  secret)
    if [ -s "$SECRET_FILE" ]; then cat "$SECRET_FILE"; else
      echo "No secret yet — run: $0 install" >&2; exit 1
    fi
    ;;
  setup-help)
    setup_help
    ;;
  *)
    echo "Usage: $0 {install|restart|stop|uninstall|status|secret|setup-help}"
    exit 1
    ;;
esac

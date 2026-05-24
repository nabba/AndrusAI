#!/usr/bin/env bash
# Install the external dead-man-switch script as a cron job.
#
# Gap #1 + #12 (2026-05-24). This script runs *off* the gateway —
# typically on a separate machine, a cloud cron, or the operator's
# second laptop. The point is to escape the gateway's blast radius.
#
# The companion module ``app/notify/last_resort.py`` runs *inside* the
# gateway and uses the same Twilio + SMTP creds to escalate critical
# alerts whose Signal + Web Push both failed. The two pieces compose:
# in-gateway last-resort catches "Signal is broken but the gateway is
# alive"; this external script catches "the whole gateway is dark."

set -euo pipefail

SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/external_deadman.py"
ENV_FILE="${ENV_FILE:-$HOME/.config/andrusai_deadman.env}"

cmd="${1:-help}"

show_help() {
  cat <<EOF
Usage: $0 {install|test|env-template|uninstall}

  install        Install a cron entry that runs the probe every 6 hours.
                 Reads env from $ENV_FILE.
  test           Run one probe immediately (uses your current shell env).
  env-template   Print a template for $ENV_FILE.
  uninstall      Remove the cron entry.

Configure Twilio + SMTP in $ENV_FILE before installing — see
\`$0 env-template\` for the variable set.
EOF
}

case "$cmd" in
  install)
    if [[ ! -f "$ENV_FILE" ]]; then
      echo "✗ $ENV_FILE not found."
      echo "  Run \`$0 env-template > $ENV_FILE\` and fill it in first."
      exit 1
    fi
    # Pull the existing crontab (if any), strip our managed line, add it.
    TMP_CRON="$(mktemp)"
    crontab -l 2>/dev/null | grep -v 'andrusai-deadman' > "$TMP_CRON" || true
    cat >> "$TMP_CRON" <<EOF
# andrusai-deadman — external liveness probe; managed by install_external_deadman.sh
0 */6 * * * set -a; . "$ENV_FILE"; /usr/bin/env python3 "$SCRIPT_PATH" >> "$HOME/.andrusai_deadman/run.log" 2>&1
EOF
    crontab "$TMP_CRON"
    rm -f "$TMP_CRON"
    mkdir -p "$HOME/.andrusai_deadman"
    echo "✓ Installed cron entry (every 6h)."
    echo "  Log: $HOME/.andrusai_deadman/run.log"
    echo "  State: $HOME/.andrusai_deadman/state.json"
    ;;
  test)
    if [[ -f "$ENV_FILE" ]]; then
      set -a; . "$ENV_FILE"; set +a
    fi
    /usr/bin/env python3 "$SCRIPT_PATH"
    ;;
  env-template)
    cat <<'EOF'
# AndrusAI external dead-man-switch — fill in and save to
# ~/.config/andrusai_deadman.env, then run install_external_deadman.sh install.

# Required.
export DASHBOARD_URL="https://andrusai.example.com"

# Optional tunables — defaults shown.
# export HEALTH_PATH="/health"
# export FAILURE_THRESHOLD="3"
# export HTTP_TIMEOUT_SECONDS="10"

# Twilio (one of Twilio or SMTP must be configured).
export TWILIO_ACCOUNT_SID=""
export TWILIO_AUTH_TOKEN=""
export TWILIO_FROM_NUMBER="+1XXXXXXXXXX"
export OPERATOR_PHONE_NUMBER="+358XXXXXXXX"

# SMTP (one of Twilio or SMTP must be configured).
export SMTP_HOST="smtp.example.com"
export SMTP_PORT="465"
export SMTP_USER="alerts@example.com"
export SMTP_PASSWORD=""
export SMTP_FROM="alerts@example.com"
export OPERATOR_EMAIL="andrus@example.com"
EOF
    ;;
  uninstall)
    TMP_CRON="$(mktemp)"
    crontab -l 2>/dev/null | grep -v 'andrusai-deadman' > "$TMP_CRON" || true
    crontab "$TMP_CRON"
    rm -f "$TMP_CRON"
    echo "✓ Removed cron entry."
    ;;
  *)
    show_help
    ;;
esac

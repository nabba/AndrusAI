#!/usr/bin/env bash
# Keep the BotArmy host LaunchAgents loaded — the outer backstop that keeps the
# gateway-watchdog (and every other botarmy agent) alive.
#
# Why (2026-06-19): the watchdog + signal-forwarder + 7 other host agents were
# found unloaded — they'd fallen off across reboots / diagnostic `launchctl
# bootout`s over days and nothing restored them; inbound Signal was dead ~19h.
# KeepAlive in each plist only restarts a CRASHED process — a `bootout` fully
# unloads an agent and KeepAlive cannot bring it back. macOS login auto-load is
# the reboot backstop; this keeper covers the mid-session case it doesn't, and
# crucially keeps the WATCHDOG itself loaded. The watchdog guards this keeper in
# return (see scripts/gateway_watchdog.py), so no single bootout can leave
# either of the two recovery agents dead — mutual protection.
#
# Idempotent: a silent no-op when everything is already loaded (logs only when
# it acts). Respects operator intent — an agent explicitly `launchctl disable`d
# is left alone. That is the sanctioned way to durably stop an agent; a plain
# `bootout` is treated as accidental and re-bootstrapped.
#
# Usage:
#   ensure_host_agents.sh run      # one sweep (what the agent-keeper LaunchAgent runs)
#   ensure_host_agents.sh status   # show loaded / disabled / DOWN per agent
set -u

UIDN="$(id -u)"
LA_DIR="$HOME/Library/LaunchAgents"
LAUNCHCTL="${LAUNCHCTL_BIN:-/bin/launchctl}"
SIGNAL_CLI_URL="${SIGNAL_CLI_HTTP_URL:-http://127.0.0.1:7583}"
SIGNAL_OWNER="${SIGNAL_OWNER_NUMBER:-}"
STATE_DIR="$HOME/.crewai-bridge"
ALERT_STAMP="$STATE_DIR/agent_keeper_last_alert"
ALERT_COOLDOWN="${KEEPER_ALERT_COOLDOWN_SECONDS:-3600}"

# The agents this keeper manages: everything matching the botarmy naming in the
# user's LaunchAgents dir. nullglob so an empty match yields an empty array.
shopt -s nullglob
PLISTS=( "$LA_DIR"/com.botarmy.*.plist "$LA_DIR"/org.andrus.botarmy.*.plist )

ts()  { date '+%Y-%m-%dT%H:%M:%S%z'; }
log() { printf '[keeper] %s %s\n' "$(ts)" "$*"; }

# True (0) iff the operator has explicitly disabled this label in the gui domain.
# `launchctl print-disabled gui/<uid>` prints e.g.  "label" => disabled|enabled.
is_disabled() {
  "$LAUNCHCTL" print-disabled "gui/$UIDN" 2>/dev/null \
    | grep -Eq "\"$1\"[[:space:]]*=>[[:space:]]*(disabled|true)"
}

# True (0) iff the label is currently loaded (launchctl list exits 0 if so).
is_loaded() { "$LAUNCHCTL" list "$1" >/dev/null 2>&1; }

# Best-effort Signal alert via signal-cli JSON-RPC (loopback) — independent of
# the gateway. Coarse cooldown so a flapping agent can't spam. The message is
# constructed from label names only (safe chars; no " or \ to escape).
signal_alert() {
  [ -n "$SIGNAL_OWNER" ] || return 0
  local now last
  now="$(date +%s)"; last=0
  [ -f "$ALERT_STAMP" ] && last="$(cat "$ALERT_STAMP" 2>/dev/null || echo 0)"
  [ $(( now - last )) -ge "$ALERT_COOLDOWN" ] || return 0
  curl -s -m 10 -o /dev/null -H 'Content-Type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"send\",\"params\":{\"recipient\":[\"$SIGNAL_OWNER\"],\"message\":\"$1\"}}" \
    "${SIGNAL_CLI_URL%/}/api/v1/rpc" >/dev/null 2>&1 || true
  mkdir -p "$STATE_DIR"; printf '%s' "$now" > "$ALERT_STAMP"
}

sweep() {
  local recovered="" label plist
  for plist in "${PLISTS[@]}"; do
    label="$(basename "$plist" .plist)"
    is_disabled "$label" && continue
    is_loaded "$label"   && continue
    log "Agent '$label' is NOT loaded — bootstrapping from $plist"
    if "$LAUNCHCTL" bootstrap "gui/$UIDN" "$plist" 2>/dev/null || is_loaded "$label"; then
      log "Re-bootstrapped '$label'"
      recovered="$recovered $label"
    else
      log "FAILED to bootstrap '$label'"
    fi
  done
  if [ -n "$recovered" ]; then
    signal_alert "Host agent-keeper re-loaded down LaunchAgent(s):$recovered (they had fallen off). Recovery layer restored."
  fi
}

status() {
  printf '%-46s %s\n' "AGENT" "STATE"
  local label plist st
  for plist in "${PLISTS[@]}"; do
    label="$(basename "$plist" .plist)"
    if   is_disabled "$label"; then st="disabled (operator)"
    elif is_loaded   "$label"; then st="loaded"
    else                            st="DOWN"
    fi
    printf '%-46s %s\n' "$label" "$st"
  done
}

case "${1:-run}" in
  run)    sweep ;;
  status) status ;;
  *)      echo "Usage: $0 {run|status}" >&2; exit 2 ;;
esac

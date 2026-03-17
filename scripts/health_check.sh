#!/usr/bin/env bash

echo '=== CrewAI Agent Team Health Check ==='

pass() { echo "  PASS $1"; }
fail() { echo "  FAIL $1"; ERRORS=$((ERRORS+1)); }

ERRORS=0

# Check gateway is running
curl -s http://127.0.0.1:8765/health | grep -q 'ok' && pass 'Gateway running' || fail 'Gateway not responding'

# Check Docker sandbox image
docker image inspect crewai-sandbox:latest &>/dev/null && pass 'Sandbox image built' || fail 'Sandbox image missing'

# Check ChromaDB
docker ps | grep -q chroma && pass 'ChromaDB running' || fail 'ChromaDB not running'

# Check signal-cli daemon
pgrep -f signal-cli &>/dev/null && pass 'signal-cli running' || fail 'signal-cli not running'

# Check .env has required keys
grep -q 'ANTHROPIC_API_KEY=your_' .env && fail '.env not configured' || pass '.env configured'

# Check Tailscale
tailscale status &>/dev/null && pass 'Tailscale connected' || fail 'Tailscale not connected'

echo ''
[ $ERRORS -eq 0 ] && echo 'All checks passed. System is ready.' || echo "$ERRORS check(s) failed. See above."

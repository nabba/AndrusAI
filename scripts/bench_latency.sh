#!/usr/bin/env bash
# bench_latency.sh — Day 0 baseline for the BotArmy speed upgrade.
# Probes the gateway with four canonical workloads and records wall-clock
# latency per request. Runs 5 iterations each; prints per-iteration timings
# and a crude p50/p95 per workload.
#
# Usage:
#   cd crewai-team && ./scripts/bench_latency.sh [OUTPUT_CSV]
# Default OUTPUT_CSV is workspace/benchmarks/latency_$(date +%s).csv
#
# Requires: the gateway reachable at $GATEWAY_URL (default 127.0.0.1:$GATEWAY_PORT).
# Reads GATEWAY_PORT from the environment or .env (default 8765).

set -euo pipefail

PORT="${GATEWAY_PORT:-8765}"
if [[ -z "${GATEWAY_PORT:-}" && -f .env ]]; then
  PORT="$(grep -E '^GATEWAY_PORT=' .env | head -1 | cut -d= -f2 | tr -d '[:space:]' || true)"
  PORT="${PORT:-8765}"
fi
URL="${GATEWAY_URL:-http://127.0.0.1:${PORT}/signal/inbound}"
OUT="${1:-workspace/benchmarks/latency_$(date +%s).csv}"
ITERS="${ITERS:-5}"

mkdir -p "$(dirname "$OUT")"
echo "workload,iter,seconds" > "$OUT"

BENCH_SENDER="+1bench0000"

run() {
  local label="$1" prompt="$2"
  echo "=== ${label} ==="
  local times=()
  for i in $(seq 1 "$ITERS"); do
    local body
    body=$(printf '{"sender":"%s","text":%s}' "$BENCH_SENDER" \
      "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$prompt")")
    local t
    t=$(curl -sS -o /dev/null -w "%{time_total}" \
        -X POST "$URL" \
        -H 'content-type: application/json' \
        --data-binary "$body")
    echo "  iter=${i} seconds=${t}"
    echo "${label},${i},${t}" >> "$OUT"
    times+=("$t")
  done
  # crude p50/p95
  local sorted
  sorted=$(printf '%s\n' "${times[@]}" | sort -g)
  local n=${#times[@]}
  local p50_idx=$(( (n + 1) / 2 - 1 ))
  local p95_idx=$(( (n * 95 + 99) / 100 - 1 ))
  (( p50_idx < 0 )) && p50_idx=0
  (( p95_idx < 0 )) && p95_idx=0
  (( p95_idx >= n )) && p95_idx=$(( n - 1 ))
  local p50 p95
  p50=$(echo "$sorted" | sed -n "$((p50_idx+1))p")
  p95=$(echo "$sorted" | sed -n "$((p95_idx+1))p")
  echo "  >> p50=${p50}s p95=${p95}s"
}

run "easy"     "hi"
run "easy2"    "thanks"
run "medium"   "What is Reflexion and why does it help LLMs?"
run "hard"     "Summarize last week's commits and suggest three refactors with concrete file paths."
run "creative" "Design a short, punchy ad concept for a coffee startup that roasts beans in Helsinki."

echo ""
echo "CSV written to: $OUT"
echo "Summary: awk -F, 'NR>1{sum[\$1]+=\$3;cnt[\$1]++}END{for(k in sum)printf \"%-10s mean=%.2fs (n=%d)\\n\",k,sum[k]/cnt[k],cnt[k]}' $OUT"

#!/usr/bin/env bash
# bench_histo.sh — summarize phase=X duration_ms=Y log lines emitted when
# LOG_PHASE_TIMING=1 is set. Reads from stdin (pipe from docker logs), or
# takes a file path as $1.
#
# Usage:
#   docker compose logs --tail 2000 gateway | scripts/bench_histo.sh
#   scripts/bench_histo.sh /app/workspace/logs/errors.jsonl
#
# Output: per-phase mean / p50 / p95 / max / n / total seconds, ordered by
# total consumed. Python-based for portability (macOS BSD awk lacks match+array).

set -euo pipefail

if [[ -n "${1:-}" && -f "$1" ]]; then
  exec python3 "$(dirname "$0")/_histo.py" < "$1"
else
  exec python3 "$(dirname "$0")/_histo.py"
fi

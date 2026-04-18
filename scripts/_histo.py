#!/usr/bin/env python3
"""_histo.py — parse phase=X duration_ms=Y log lines from stdin, print p50/p95 table.

Helper for bench_histo.sh. macOS BSD awk can't do regex capture groups into
arrays, so we use Python instead.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict

_PHASE = re.compile(r"phase=([a-zA-Z_]+)")
_DUR = re.compile(r"duration_ms=(\d+)")

samples: dict[str, list[int]] = defaultdict(list)
for line in sys.stdin:
    p = _PHASE.search(line)
    d = _DUR.search(line)
    if p and d:
        samples[p.group(1)].append(int(d.group(1)))

if not samples:
    print("(no phase=X duration_ms=Y lines found — run traffic with LOG_PHASE_TIMING=1 first)")
    sys.exit(0)


def pct(vals: list[int], p: float) -> int:
    if not vals:
        return 0
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, int(round((p / 100.0) * (len(s) - 1)))))
    return s[idx]


rows = []
for phase, vals in samples.items():
    n = len(vals)
    mean_ = int(sum(vals) / n)
    rows.append((phase, n, mean_, pct(vals, 50), pct(vals, 95), max(vals), sum(vals) / 1000.0))

rows.sort(key=lambda r: r[6], reverse=True)

print(f"{'phase':<20} {'n':>6} {'mean_ms':>8} {'p50_ms':>8} {'p95_ms':>8} {'max_ms':>8} {'sum_s':>8}")
print(f"{'-----':<20} {'-':>6} {'-------':>8} {'------':>8} {'------':>8} {'------':>8} {'-----':>8}")
for r in rows:
    print(f"{r[0]:<20} {r[1]:>6d} {r[2]:>8d} {r[3]:>8d} {r[4]:>8d} {r[5]:>8d} {r[6]:>8.1f}")

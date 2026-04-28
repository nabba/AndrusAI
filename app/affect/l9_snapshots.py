"""
l9_snapshots.py — Daily L9 SubIA homeostasis snapshots.

Creates one rolled-up daily record summarizing affect dynamics + viability
state. Intended for long-horizon analysis (drift, set-point trends,
seasonal patterns) without paying the cost of replaying the per-tick trace
each time.

Schedule: 04:35 EET/EEST, right after the daily reflection cycle so it
includes the freshly-written reflection report.

Output: /app/workspace/affect/l9_snapshots.jsonl — append-only, one record
per day.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_AFFECT_DIR = Path("/app/workspace/affect")
_L9_FILE = _AFFECT_DIR / "l9_snapshots.jsonl"


def write_daily_snapshot() -> dict | None:
    """Compute and append one daily L9 record. Returns the record (or None)."""
    try:
        from app.affect.calibration import load_recent_trace, latest_report
        from app.affect.welfare import read_audit
        from app.affect.viability import compute_viability_frame

        window = load_recent_trace(hours=24)
        report = latest_report()
        audits = read_audit(limit=200)
        # Filter audit to last 24h
        cutoff = datetime.now(timezone.utc).timestamp() - 24 * 3600
        recent_audits = []
        for a in audits:
            try:
                if datetime.fromisoformat(str(a.get("ts", "")).replace("Z", "+00:00")).timestamp() >= cutoff:
                    recent_audits.append(a)
            except (ValueError, AttributeError):
                continue

        # Roll-up affect stats
        if window:
            valences = [s.valence for s in window]
            arousals = [s.arousal for s in window]
            controllabilities = [s.controllability for s in window]
            mean_v = sum(valences) / len(valences)
            var_v = sum((v - mean_v) ** 2 for v in valences) / len(valences)
            attractor_counts: dict[str, int] = {}
            for s in window:
                attractor_counts[s.attractor] = attractor_counts.get(s.attractor, 0) + 1
            stats = {
                "n_steps": len(window),
                "mean_valence": round(mean_v, 4),
                "var_valence": round(var_v, 4),
                "mean_arousal": round(sum(arousals) / len(arousals), 4),
                "mean_controllability": round(sum(controllabilities) / len(controllabilities), 4),
                "positive_fraction": round(sum(1 for v in valences if v > 0) / len(valences), 4),
                "attractor_counts": attractor_counts,
            }
        else:
            stats = {"n_steps": 0}

        frame = compute_viability_frame()

        # Phase 4: snapshot the ecological signal alongside.
        ecological_snapshot: dict | None = None
        try:
            from app.affect.ecological import compute_ecological_signal
            ecological_snapshot = compute_ecological_signal().to_dict()
        except Exception:
            logger.debug("affect.l9: ecological snapshot failed", exc_info=True)

        # Phase 5: include consciousness-indicator gate snapshot.
        gate_snapshot: dict | None = None
        try:
            from app.affect.phase5_gate import evaluate_gate
            gate_snapshot = evaluate_gate().to_dict()
        except Exception:
            logger.debug("affect.l9: gate snapshot failed", exc_info=True)

        snapshot = {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "ts": datetime.now(timezone.utc).isoformat(),
            "stats_24h": stats,
            "viability_at_snapshot": frame.to_dict(),
            "welfare_breaches_24h": len(recent_audits),
            "welfare_breach_kinds": list(set(a.get("kind", "?") for a in recent_audits)),
            "reflection_date": (report or {}).get("ts", ""),
            "reflection_healthy": ((report or {}).get("healthy_dynamics") or {}).get("passes"),
            "ecological": ecological_snapshot,
            "phase5_gate": gate_snapshot,
        }

        _AFFECT_DIR.mkdir(parents=True, exist_ok=True)
        with _L9_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot, default=str) + "\n")
        logger.info(
            f"affect.l9: snapshot {snapshot['date']} — n={stats.get('n_steps', 0)} "
            f"breaches={snapshot['welfare_breaches_24h']}"
        )
        return snapshot
    except Exception:
        logger.error("affect.l9: snapshot failed", exc_info=True)
        return None


def read_l9_snapshots(days: int = 30) -> list[dict]:
    """Return most-recent N daily snapshots (newest last)."""
    if not _L9_FILE.exists():
        return []
    try:
        rows: list[dict] = []
        with _L9_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows[-days:]
    except Exception:
        logger.debug("affect.l9: read failed", exc_info=True)
        return []

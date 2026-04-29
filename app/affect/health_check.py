"""
health_check.py — Narrative-Self pipeline diagnostic.

One-shot inspector that scans /app/workspace/affect/ and the experiential
KB to verify the salience/episodes/chapter pipeline is producing healthy
output over a configurable window. No LLM calls — pure data inspection.

Output: /app/workspace/affect/health_checks/YYYY-MM-DD.md  (markdown report)

Self-Improver permissions: read-only on this module. Health checks are
infrastructure-level diagnostics; allowing the self-improver to edit the
checks would let it silently rewrite what counts as "healthy" output from
its own narrative-self pipeline.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

from app.paths import (  # noqa: E402  workspace-aware paths
    AFFECT_ROOT as _AFFECT_DIR,
    AFFECT_HEALTH_CHECKS_DIR as _HEALTH_DIR,
    AFFECT_SALIENCE as _SALIENCE_FILE,
    AFFECT_IDENTITY_CLAIMS as _IDENTITY_FILE,
)
_AUDIT_FILE = _AFFECT_DIR / "chapters_audit.jsonl"  # narrative-self chapters audit (local helper)


# ── Public entry ────────────────────────────────────────────────────────────


def run_health_check(window_days: int = 14, sample_chapters: int = 3) -> dict:
    """Run all diagnostics and write a markdown report. Returns the report dict."""
    flags: list[str] = []
    report: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "window_days": window_days,
        "checks": {
            "chapters": _check_chapters(window_days, flags),
            "identity_claims": _check_identity_claims(window_days, flags),
            "sampled_chapters": _sample_chapters(sample_chapters),
            "episodes_salience": _check_episodes_and_salience(window_days, flags),
            "drift_signals": _check_drift_signals(window_days, flags),
        },
        "flags": flags,
    }
    path = _write_markdown_report(report)
    report["report_path"] = str(path) if path else None
    logger.info(
        "affect.health_check: complete — %d flag(s); report=%s",
        len(flags), report["report_path"],
    )
    return report


# ── Diagnostics ─────────────────────────────────────────────────────────────


def _check_chapters(window_days: int, flags: list[str]) -> dict:
    """Cadence: expect ≈window_days chapters; flag deficit > 2 or audit/KB mismatch."""
    audit_count = 0
    severe_days: list[str] = []
    if _AUDIT_FILE.exists():
        cutoff = _iso_cutoff(window_days)
        try:
            with _AUDIT_FILE.open("r", encoding="utf-8") as f:
                for line in f:
                    row = _parse_jsonl_line(line)
                    if row is None:
                        continue
                    ts = row.get("ts", "")
                    if ts < cutoff:
                        continue
                    audit_count += 1
                    if row.get("drift_signal") == "severe":
                        severe_days.append(ts[:10])
        except Exception:
            logger.debug("health_check: audit read failed", exc_info=True)

    kb_count = _count_kb_entries("chapter", window_days)

    expected_min = max(0, window_days - 2)
    if audit_count < expected_min:
        flags.append(
            f"chapters: only {audit_count} in {window_days}d "
            f"(expected ≥{expected_min}) — pipeline may be skipping days"
        )
    if audit_count != kb_count:
        flags.append(
            f"chapters: audit log ({audit_count}) ≠ KB entries ({kb_count}) — "
            "possible storage failure"
        )

    return {
        "audit_count": audit_count,
        "kb_count": kb_count,
        "expected_min": expected_min,
        "severe_drift_days": severe_days,
    }


def _check_identity_claims(window_days: int, flags: list[str]) -> dict:
    """FIFO turnover (no stagnation, no churn) + cap respect."""
    try:
        from app.affect.narrative import MAX_IDENTITY_CLAIMS
    except Exception:
        MAX_IDENTITY_CLAIMS = 5

    if not _IDENTITY_FILE.exists():
        flags.append("identity_claims: file missing — no claims have been ratified yet")
        return {"present": False}

    try:
        claims = json.loads(_IDENTITY_FILE.read_text(encoding="utf-8"))
    except Exception:
        flags.append("identity_claims: JSON parse failed")
        return {"present": True, "parse_error": True}

    if not isinstance(claims, list):
        flags.append("identity_claims: not a list")
        return {"present": True, "malformed": True}

    n = len(claims)
    if n > MAX_IDENTITY_CLAIMS:
        flags.append(
            f"identity_claims: cap exceeded ({n} > {MAX_IDENTITY_CLAIMS}) — "
            "FIFO eviction may be broken"
        )

    # Turnover: examine claims_accepted across audit-log days.
    daily_sets: list[dict] = []
    if _AUDIT_FILE.exists():
        cutoff = _iso_cutoff(window_days)
        try:
            with _AUDIT_FILE.open("r", encoding="utf-8") as f:
                for line in f:
                    row = _parse_jsonl_line(line)
                    if row is None or row.get("ts", "") < cutoff:
                        continue
                    daily_sets.append({
                        "date": row.get("ts", "")[:10],
                        "claims": frozenset(row.get("claims_accepted", []) or []),
                    })
        except Exception:
            logger.debug("health_check: audit read for claims failed", exc_info=True)

    rotations = sum(
        1 for i in range(1, len(daily_sets))
        if daily_sets[i]["claims"] != daily_sets[i - 1]["claims"]
    )
    n_days = len(daily_sets)

    if n_days >= 7 and rotations == 0:
        flags.append(
            f"identity_claims: NO rotation in {n_days} days (stagnation)"
        )
    if n_days >= 7 and rotations >= n_days - 1:
        flags.append(
            f"identity_claims: rotation every chapter ({rotations}/{n_days}) — churn"
        )

    return {
        "present": True,
        "current_count": n,
        "current_claims": [c.get("text") for c in claims if isinstance(c, dict)],
        "rotation_events": rotations,
        "n_days_with_data": n_days,
    }


def _sample_chapters(k: int) -> list[dict]:
    """Pull k most recent chapters for manual qualitative review."""
    try:
        from app.experiential.vectorstore import get_store
        store = get_store()
        col = store._collection
        if col.count() == 0:
            return []
        data = col.get(
            where={"entry_type": "chapter"},
            include=["documents", "metadatas"],
        )
        ids = data.get("ids") or []
        docs = data.get("documents") or []
        metas = data.get("metadatas") or []
        rows: list[dict] = []
        for i, _id in enumerate(ids):
            rows.append({
                "id": _id,
                "text": docs[i] if i < len(docs) else "",
                "metadata": metas[i] if i < len(metas) else {},
            })
        rows.sort(key=lambda r: r["metadata"].get("created_at", ""), reverse=True)
        return rows[:k]
    except Exception:
        logger.debug("health_check: chapter sample failed", exc_info=True)
        return []


def _check_episodes_and_salience(window_days: int, flags: list[str]) -> dict:
    """Volume sanity: ≥1 episode/day average, salience ≥ episodes."""
    salience_count = 0
    if _SALIENCE_FILE.exists():
        cutoff = _iso_cutoff(window_days)
        try:
            with _SALIENCE_FILE.open("r", encoding="utf-8") as f:
                for line in f:
                    row = _parse_jsonl_line(line)
                    if row is None:
                        continue
                    if row.get("ts", "") >= cutoff:
                        salience_count += 1
        except Exception:
            logger.debug("health_check: salience read failed", exc_info=True)

    episode_kb_count = _count_kb_entries("episode", window_days)
    avg_per_day = episode_kb_count / max(1, window_days)

    if avg_per_day < 1:
        flags.append(
            f"episodes: only {episode_kb_count} in {window_days}d "
            f"(avg {avg_per_day:.1f}/d) — synthesis may be silently failing"
        )
    elif avg_per_day > 50:
        flags.append(
            f"episodes: {episode_kb_count} in {window_days}d "
            f"(avg {avg_per_day:.1f}/d) — unexpectedly high; cost may be elevated"
        )

    if salience_count > 0 and episode_kb_count == 0:
        flags.append(
            f"episodes: {salience_count} salience events but 0 episodes — "
            "synthesis path is broken"
        )

    return {
        "salience_count": salience_count,
        "episode_kb_count": episode_kb_count,
        "avg_episodes_per_day": round(avg_per_day, 2),
    }


def _check_drift_signals(window_days: int, flags: list[str]) -> dict:
    """Drift distribution; flag if >3 days had severe drift."""
    severe_days: set[str] = set()
    counts: Counter = Counter()
    if _AUDIT_FILE.exists():
        cutoff = _iso_cutoff(window_days)
        try:
            with _AUDIT_FILE.open("r", encoding="utf-8") as f:
                for line in f:
                    row = _parse_jsonl_line(line)
                    if row is None or row.get("ts", "") < cutoff:
                        continue
                    sig = row.get("drift_signal", "ok")
                    counts[sig] += 1
                    if sig == "severe":
                        severe_days.add(row.get("ts", "")[:10])
        except Exception:
            logger.debug("health_check: drift read failed", exc_info=True)

    if len(severe_days) > 3:
        flags.append(
            f"drift: {len(severe_days)} day(s) had severe drift — "
            "calibration is unhealthy and identity-claim updates were suppressed"
        )

    return {
        "counts": dict(counts),
        "severe_days": sorted(severe_days),
    }


# ── Helpers ─────────────────────────────────────────────────────────────────


def _count_kb_entries(entry_type: str, window_days: int) -> int:
    try:
        from app.experiential.vectorstore import get_store
        store = get_store()
        col = store._collection
        if col.count() == 0:
            return 0
        data = col.get(where={"entry_type": entry_type}, include=["metadatas"])
        metas = data.get("metadatas") or []
        cutoff = _iso_cutoff(window_days)
        return sum(1 for m in metas if (m or {}).get("created_at", "") >= cutoff)
    except Exception:
        logger.debug("health_check: KB count for %s failed", entry_type, exc_info=True)
        return 0


def _iso_cutoff(window_days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()


def _parse_jsonl_line(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


# ── Markdown report ─────────────────────────────────────────────────────────


def _write_markdown_report(report: dict) -> Path | None:
    try:
        _HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        logger.error("health_check: cannot create %s", _HEALTH_DIR, exc_info=True)
        return None

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = _HEALTH_DIR / f"{date_str}.md"

    lines: list[str] = [
        "# Narrative-Self pipeline — health check",
        f"_Generated: {report['ts']}, window: {report['window_days']} days_",
        "",
    ]

    flags = report.get("flags") or []
    if flags:
        lines.append("## Flags")
        for f in flags:
            lines.append(f"- {f}")
    else:
        lines.append("## No flags raised")
    lines.append("")

    ch = report["checks"]

    # 1. Chapters
    c = ch.get("chapters", {})
    lines += [
        "## 1. Chapter cadence",
        f"- audit log entries: {c.get('audit_count', 0)}",
        f"- KB chapter entries: {c.get('kb_count', 0)}",
        f"- expected minimum (window_days − 2): {c.get('expected_min', 0)}",
    ]
    sd = c.get("severe_drift_days") or []
    if sd:
        lines.append(f"- severe-drift days ({len(sd)}): {', '.join(sd)}")
    lines.append("")

    # 2. Identity claims
    ic = ch.get("identity_claims", {})
    lines.append("## 2. Identity claims")
    if ic.get("present"):
        lines += [
            f"- current count: {ic.get('current_count', 0)}",
            f"- rotation events: {ic.get('rotation_events', 0)} "
            f"over {ic.get('n_days_with_data', 0)} days",
            "- current claims:",
        ]
        for txt in ic.get("current_claims") or []:
            lines.append(f"  - {txt}")
    else:
        lines.append("- (no identity_claims.json yet)")
    lines.append("")

    # 3. Sampled chapters
    samples = ch.get("sampled_chapters") or []
    lines.append("## 3. Sampled chapters (manual qualitative read)")
    if not samples:
        lines.append("- (no chapters in KB)")
    for i, s in enumerate(samples, 1):
        m = s.get("metadata", {})
        lines += [
            f"### Sample {i} — {(m.get('created_at') or '')[:10]}",
            f"_attractors: {m.get('dominant_attractors', '?')} · "
            f"drift: {m.get('drift_signal', '?')} · n_episodes: {m.get('n_episodes', '?')}_",
            "",
            (s.get("text") or "")[:1500],
            "",
        ]

    # 4. Episodes & salience
    es = ch.get("episodes_salience", {})
    lines += [
        "## 4. Episodes + salience volume",
        f"- salience events: {es.get('salience_count', 0)}",
        f"- KB episode entries: {es.get('episode_kb_count', 0)}",
        f"- avg episodes/day: {es.get('avg_episodes_per_day', 0)}",
        "",
    ]

    # 5. Drift signals
    ds = ch.get("drift_signals", {})
    lines += [
        "## 5. Drift signal distribution",
        f"- counts: {ds.get('counts', {})}",
    ]
    sd2 = ds.get("severe_days") or []
    if sd2:
        lines.append(f"- severe-drift days: {', '.join(sd2)}")
    lines.append("")

    try:
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
    except Exception:
        logger.error("health_check: report write failed", exc_info=True)
        return None

"""Canonical LLM cost reader — single source of truth.

Replaces the broken-since-creation pattern where five separate modules
(:mod:`app.llm_anthropic_budget`, :mod:`app.llm_openrouter_budget`,
:mod:`app.llm_role_spend`, :mod:`app.llm_cost_advisor.analyzer`, and a
parallel reader inside the dashboard) all tried to ``importlib.import_module
("app.audit_log")`` for cost data.  That module does not exist; the
imports failed silently (failure-OPEN posture) and every gate returned
0.0 spend.  The cost subsystem was operationally dormant.

The actual cost ledger is the SQLite ``token_usage`` table in
``/app/workspace/llm_benchmarks.db``, written by :func:`app.llm_benchmarks.record_tokens`
on every observed LLM call.  Schema:

    token_usage(
      id, model, prompt_tokens, completion_tokens, total_tokens,
      cost_usd, ts, project_id, agent_role
    )

This module exposes the queries that all five consumers need.  Same
caching strategy as the prior modules (5-second TTL per query
signature) — keeps the gate cheap on hot paths.

Failure posture
---------------

Every public function returns a safe default on any error (0.0,
empty dict, empty list).  A broken ledger must NEVER block legitimate
calls — the per-provider cap and adaptive back-pressure layers
explicitly rely on this fail-OPEN contract.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.llm_provider_classify import classify_provider

logger = logging.getLogger(__name__)


# ── DB location ─────────────────────────────────────────────────────
#
# The SQLite database is owned by :mod:`app.llm_benchmarks`.  This
# reader opens the SAME file via a fresh connection per process —
# SQLite handles concurrent read-only access via WAL mode (which
# llm_benchmarks already enables).

_DB_PATH = Path("/app/workspace/llm_benchmarks.db")


def _open_readonly() -> Optional[sqlite3.Connection]:
    """Open the cost-ledger DB read-only.  Returns None on any error
    (the file doesn't exist, permissions, lock, …).

    Flushes the writer's pending batch first so reads see the most
    recent rows.  Without this, the buffered writer (in
    :mod:`app.llm_benchmarks`) accumulates writes for up to
    ``_BATCH_INTERVAL`` seconds before committing, and per-provider
    cap checks would see only committed-as-of-last-flush spend.
    That lag let a sustained high-volume workload overshoot the cap
    by ~$1 per flush window before the next read caught up.
    """
    # Flush before read — see docstring.  Cheap (one commit when
    # buffer non-empty, no-op when empty).  Failure-isolated.
    try:
        from app.llm_benchmarks import flush_pending_writes
        flush_pending_writes()
    except Exception:
        pass

    if not _DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(
            f"file:{_DB_PATH}?mode=ro", uri=True,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError:
        return None


# ── 5-second TTL cache ───────────────────────────────────────────
#
# Same pattern as the previous per-module caches.  Keyed by query
# signature so the per-provider 24h read and the per-role 1h read
# get independent cache entries.

_CACHE_TTL_SECONDS = 5.0
_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()


def _cached(key: str, compute):
    """Memoise *compute*'s result under *key* for ``_CACHE_TTL_SECONDS``."""
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if entry is not None and entry.get("expires_at", 0.0) > now:
            return entry["value"]
    value = compute()
    with _cache_lock:
        _cache[key] = {
            "value": value,
            "expires_at": now + _CACHE_TTL_SECONDS,
        }
    return value


def _invalidate_for_tests() -> None:
    """Test helper — wipe all cached query results."""
    with _cache_lock:
        _cache.clear()


# ── Window-cutoff helper ────────────────────────────────────────────


def _cutoff_iso(hours: float) -> str:
    """ISO-8601 timestamp for ``now - hours``.  Used as the
    ``ts >`` predicate in SQL ``WHERE`` clauses.  Matches the format
    written by :func:`app.llm_benchmarks.record_tokens`.
    """
    return (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).isoformat()


# ── Per-provider spend ──────────────────────────────────────────────


def _spend_by_provider_uncached(hours: float) -> dict[str, float]:
    """Sum ``cost_usd`` per provider over the rolling window.

    Provider classification via :func:`app.llm_provider_classify.classify_provider`
    so the rule lives in one place (was previously duplicated across
    three modules).
    """
    conn = _open_readonly()
    if conn is None:
        return {}
    try:
        cur = conn.execute(
            "SELECT model, SUM(cost_usd) AS total FROM token_usage "
            "WHERE ts > ? AND cost_usd > 0 GROUP BY model",
            (_cutoff_iso(hours),),
        )
        totals: dict[str, float] = defaultdict(float)
        for row in cur:
            provider = classify_provider(row["model"])
            if provider is None:
                continue
            totals[provider] += float(row["total"] or 0.0)
        return dict(totals)
    except sqlite3.Error:
        logger.debug(
            "llm_cost_ledger: per-provider query failed", exc_info=True,
        )
        return {}
    finally:
        conn.close()


def spend_by_provider(hours: float = 24.0) -> dict[str, float]:
    """Public: ``{provider: usd}`` over the rolling window.

    Used by :func:`app.llm_anthropic_budget.today_spent_usd` and
    :func:`app.llm_openrouter_budget.today_spent_usd` to enforce
    per-provider daily caps.
    """
    return _cached(
        f"spend_by_provider_{hours}h",
        lambda: _spend_by_provider_uncached(hours),
    )


def spend_for_provider(provider: str, hours: float = 24.0) -> float:
    """Public: USD spend for a single provider over the window."""
    return float(spend_by_provider(hours).get(provider, 0.0))


# ── Calendar-month total (total-cost ceiling) ───────────────────────


def _month_start_iso(now: Optional[float] = None) -> str:
    """First instant of the current UTC calendar month, ISO-8601.
    Matches the ``ts`` format written by ``record_tokens`` so a string
    ``ts >=`` comparison selects the month (ISO-8601 sorts lexically)."""
    dt = (
        datetime.now(timezone.utc)
        if now is None
        else datetime.fromtimestamp(now, tz=timezone.utc)
    )
    return dt.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    ).isoformat()


def _mtd_total_uncached(now: Optional[float]) -> Optional[float]:
    conn = _open_readonly()
    if conn is None:
        return None
    try:
        cur = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM token_usage "
            "WHERE cost_usd IS NOT NULL AND ts >= ?",
            (_month_start_iso(now),),
        )
        row = cur.fetchone()
        return float(row["total"] or 0.0) if row is not None else 0.0
    except sqlite3.Error:
        logger.debug("llm_cost_ledger: month-to-date query failed", exc_info=True)
        return None
    finally:
        conn.close()


def month_to_date_total_usd(now: Optional[float] = None) -> Optional[float]:
    """Public: total ``cost_usd`` across ALL models/providers for the current
    UTC calendar month, or ``None`` if the ledger can't be read.

    Authoritative spend figure for the total-cost ceiling: reads the same
    ``token_usage`` table every observed LLM call writes to, so the ceiling
    sees 100% of spend — not the ~14% ``ticket.completed`` slice that
    ``control_plane.audit_log`` captured."""
    key = f"mtd_total_{int(now) if now is not None else 'live'}"
    return _cached(key, lambda: _mtd_total_uncached(now))


# ── Per-role spend ──────────────────────────────────────────────────


def _spend_by_role_uncached(hours: float) -> dict[str, float]:
    """Sum ``cost_usd`` per ``agent_role`` over the window.

    Rows where ``agent_role`` is NULL are aggregated under
    ``"__unknown__"`` so the caller can distinguish "no role recorded"
    from "role X had no spend".
    """
    conn = _open_readonly()
    if conn is None:
        return {}
    try:
        cur = conn.execute(
            "SELECT COALESCE(agent_role, '__unknown__') AS role, "
            "       SUM(cost_usd) AS total "
            "FROM token_usage "
            "WHERE ts > ? AND cost_usd > 0 "
            "GROUP BY agent_role",
            (_cutoff_iso(hours),),
        )
        return {row["role"]: float(row["total"] or 0.0) for row in cur}
    except sqlite3.Error:
        logger.debug(
            "llm_cost_ledger: per-role query failed", exc_info=True,
        )
        return {}
    finally:
        conn.close()


def spend_by_role(hours: float = 1.0) -> dict[str, float]:
    """Public: ``{role: usd}`` over the rolling window.

    Default 1-hour window matches the adaptive back-pressure cadence
    in :mod:`app.llm_role_spend`.
    """
    return _cached(
        f"spend_by_role_{hours}h",
        lambda: _spend_by_role_uncached(hours),
    )


def spend_for_role(role: str, hours: float = 1.0) -> float:
    """Public: USD spend for a single role over the window."""
    return float(spend_by_role(hours).get(role, 0.0))


# ── Per-day spend (advisor) ─────────────────────────────────────────


def daily_spend_by_provider_for_advisor(
    window_days: int = 7,
) -> dict[str, list[dict]]:
    """Per-(provider, UTC-day) spend for the cost advisor.

    Returns ``{provider: [{day, spend_usd, n_calls}, ...]}`` with one
    entry per UTC day in the window (zero-spend days included as
    explicit zero rows so the advisor's "X of 7 days" rules work).
    """
    conn = _open_readonly()
    if conn is None:
        return {"anthropic": [], "openrouter": [], "ollama": []}
    try:
        # SQLite stores ts as ISO strings — extract the date portion
        # via substring (first 10 chars: YYYY-MM-DD) for grouping.
        cur = conn.execute(
            "SELECT model, SUBSTR(ts, 1, 10) AS day, "
            "       SUM(cost_usd) AS total, COUNT(*) AS n_calls "
            "FROM token_usage "
            "WHERE ts > ? AND cost_usd > 0 "
            "GROUP BY model, day",
            (_cutoff_iso(window_days * 24.0),),
        )
        raw: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {"spend": 0.0, "calls": 0}))
        for row in cur:
            provider = classify_provider(row["model"])
            if provider is None:
                continue
            day = row["day"]
            raw[provider][day]["spend"] += float(row["total"] or 0.0)
            raw[provider][day]["calls"] += int(row["n_calls"] or 0)
    except sqlite3.Error:
        logger.debug(
            "llm_cost_ledger: daily-spend query failed", exc_info=True,
        )
        return {"anthropic": [], "openrouter": [], "ollama": []}
    finally:
        conn.close()

    # Materialise the full UTC-day window with zero-spend days
    # explicit — the advisor's rules count days, not non-zero days.
    today = datetime.now(timezone.utc).date()
    window = [
        (today - timedelta(days=offset)).isoformat()
        for offset in range(window_days - 1, -1, -1)
    ]
    result: dict[str, list[dict]] = {}
    for provider in ("anthropic", "openrouter", "ollama"):
        result[provider] = [
            {
                "day": day,
                "spend_usd": round(raw[provider].get(day, {}).get("spend", 0.0), 6),
                "n_calls": int(raw[provider].get(day, {}).get("calls", 0)),
            }
            for day in window
        ]
    return result


__all__ = [
    "spend_by_provider",
    "spend_for_provider",
    "spend_by_role",
    "spend_for_role",
    "daily_spend_by_provider_for_advisor",
]

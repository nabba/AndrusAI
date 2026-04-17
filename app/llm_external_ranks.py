"""
llm_external_ranks.py — Pull third-party LLM ranking signals.

The selector historically relied entirely on in-house telemetry
(``llm_benchmarks.get_scores``) plus the hand-curated ``strengths``
columns in ``llm_catalog.CATALOG``. Neither signal reacts to the
outside world: a model that degrades in production-wide usage (rising
latency on OpenRouter, dropping quality on HuggingFace's Open LLM
Leaderboard, shifting on Artificial Analysis) will never influence our
selection.

This module introduces a fourth signal — *outside quality*. It pulls
normalised rankings from up to three sources and stores them in
``control_plane.external_ranks`` with a ``fetched_at`` timestamp so
consumers can respect TTLs. ``get_external_score`` aggregates across
sources into a single 0..1 quality score; ``get_combined_scores`` in
``llm_benchmarks`` blends that with the internal success/latency score.

Sources:
  - OpenRouter ``/api/v1/models`` + ``/api/v1/models/{id}/endpoints``
    (public, uses our existing OpenRouter key — no extra plumbing).
  - HuggingFace Open LLM Leaderboard v2 parquet dump (public, no key).
  - Artificial Analysis API (opt-in; activates only when
    ``ARTIFICIAL_ANALYSIS_API_KEY`` / ``AA_API_KEY`` is set).

All fetchers are fail-open: network outages or schema drift cause the
row to be skipped, never raise. A missing source simply contributes
``None`` to the aggregate.

Refresh cadence: weekly via the ``llm-external-ranks`` idle scheduler
job. Manual refresh: ``refresh_all(force=True)``.
"""

from __future__ import annotations

import io
import json
import logging
import os
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

import httpx

logger = logging.getLogger(__name__)

# Default TTL for external rank rows — weekly cadence keeps us close to
# what the upstream leaderboards publish while avoiding hammering.
DEFAULT_TTL_HOURS = 24 * 7

# ── Source definitions ──────────────────────────────────────────────────

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_OPENROUTER_ENDPOINT_URL = "https://openrouter.ai/api/v1/models/{model_id}/endpoints"
_HF_LEADERBOARD_URL = (
    "https://huggingface.co/api/datasets/open-llm-leaderboard/contents/parquet/"
    "default/train/0000.parquet"
)
_AA_API_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"

# AA publishes multiple variants per model (e.g. claude-sonnet-4-6-adaptive,
# claude-sonnet-4-6-xhigh, gpt-5-nano-xhigh). Strip the reasoning-effort /
# adaptive-mode suffixes before catalog-key resolution so the base model
# row wins even when AA emits multiple specialised entries.
_AA_VARIANT_SUFFIXES: tuple[str, ...] = (
    "-adaptive", "-thinking", "-reasoning",
    "-xhigh", "-high", "-medium", "-low",
    "-max-effort", "-max", "-min-effort", "-min",
)


@dataclass(frozen=True, slots=True)
class ExternalRank:
    source: str
    model_id: str      # catalog key
    metric: str        # 'quality'|'speed'|'cost'|'tokens_7d'|'elo'
    value: float
    unit: str
    task_type: str | None = None
    raw: dict | None = None


# ── Catalog key resolution ──────────────────────────────────────────────

def _catalog_key_lookup() -> dict[str, str]:
    """Return a map from various model_id variants to the catalog key.

    External sources report model IDs in several shapes:
      ``"deepseek/deepseek-chat"`` (OpenRouter slug)
      ``"anthropic/claude-sonnet-4.6"`` (HF convention)
      ``"claude-sonnet-4-6"`` (Anthropic bare)
    Normalise everything so rank rows can be joined to CATALOG.
    """
    from app.llm_catalog import CATALOG
    lookup: dict[str, str] = {}
    for name, info in CATALOG.items():
        lookup[name.lower()] = name
        mid = info.get("model_id", "")
        if mid:
            lookup[mid.lower()] = name
            for prefix in ("openrouter/", "anthropic/", "ollama_chat/"):
                if mid.lower().startswith(prefix):
                    lookup[mid.lower()[len(prefix):]] = name
    return lookup


def _resolve_catalog_key(raw_id: str) -> str | None:
    if not raw_id:
        return None
    lookup = _catalog_key_lookup()
    key = raw_id.lower()
    # 1. Direct match
    if key in lookup:
        return lookup[key]
    # 2. Suffix match on bare name (strip provider/slashes)
    bare = key.split("/")[-1]
    if bare in lookup:
        return lookup[bare]
    # 3. Punctuation normalisation — AA emits dash-only slugs
    #    (e.g. "deepseek-v3-2"), our catalog uses dots
    #    (e.g. "deepseek-v3.2"). Try both directions on the bare form.
    dashed_to_dotted = _dash_to_dot_variants(bare)
    for variant in dashed_to_dotted:
        if variant in lookup:
            return lookup[variant]
    return None


def _dash_to_dot_variants(s: str) -> list[str]:
    """Return plausible dot/dash permutations of ``s`` for catalog match.

    AA slugs like ``"deepseek-v3-2"`` correspond to catalog keys like
    ``"deepseek-v3.2"``. The ambiguity is positional — we don't know
    which dashes were originally dots. This helper yields a small
    fixed set of candidates that cover the common version-number
    conventions (``-v3.2``, ``-4.6``, ``-k2.5``) without exploding
    into a combinatorial search.
    """
    out: set[str] = {s}
    # Replace each lone digit-dash-digit with digit-dot-digit.
    # e.g. "claude-sonnet-4-6" → "claude-sonnet-4.6"
    #      "deepseek-v3-2"     → "deepseek-v3.2"
    import re
    out.add(re.sub(r"(\d)-(\d)", r"\1.\2", s))
    # And the reverse: dot → dash (for matching catalog keys that
    # contain dots against dashed slugs).
    out.add(s.replace(".", "-"))
    # Trailing version: "...v3-2" → "...3.2" (drop the v)
    out.add(re.sub(r"-v(\d+)-(\d+)$", r"-\1.\2", s))
    return [x for x in out if x]


# ── Fetchers ────────────────────────────────────────────────────────────

def fetch_openrouter_stats() -> list[ExternalRank]:
    """Pull the public model list + per-endpoint stats from OpenRouter.

    Signals we emit per model:
      - ``cost`` (metric=cost, unit=usd_per_m) from ``pricing.completion``
      - ``speed`` (metric=speed, unit=tok_s) from the best endpoint's
        reported throughput, normalised against the slowest model
      - ``tokens_7d`` (if present) as raw count for popularity diagnostics
    """
    try:
        from app.config import get_settings
        api_key = get_settings().openrouter_api_key.get_secret_value()
    except Exception:
        api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return []

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = httpx.get(_OPENROUTER_MODELS_URL, headers=headers, timeout=20)
        if resp.status_code != 200:
            return []
        models = resp.json().get("data", [])
    except Exception as exc:
        logger.debug(f"external_ranks: openrouter /models failed: {exc}")
        return []

    ranks: list[ExternalRank] = []
    # Collect speed values in a first pass to normalise.
    speeds: dict[str, float] = {}
    for m in models:
        key = _resolve_catalog_key(m.get("id", ""))
        if not key:
            continue
        # Cost metric — completion price in USD per M tokens
        pricing = m.get("pricing", {})
        try:
            cost = float(pricing.get("completion") or 0) * 1_000_000
        except (TypeError, ValueError):
            cost = 0.0
        if cost > 0:
            ranks.append(ExternalRank(
                source="openrouter", model_id=key, metric="cost",
                value=round(cost, 6), unit="usd_per_m",
                raw={"id": m.get("id")},
            ))
        # Speed — pull the top endpoint for this model
        try:
            ep_resp = httpx.get(
                _OPENROUTER_ENDPOINT_URL.format(model_id=m.get("id", "")),
                headers=headers, timeout=10,
            )
            if ep_resp.status_code == 200:
                endpoints = ep_resp.json().get("data", {}).get("endpoints", [])
                if endpoints:
                    best = max(
                        endpoints,
                        key=lambda e: float(e.get("completion_tokens_per_second") or 0),
                    )
                    tok_s = float(best.get("completion_tokens_per_second") or 0)
                    if tok_s > 0:
                        speeds[key] = tok_s
                        ranks.append(ExternalRank(
                            source="openrouter", model_id=key, metric="speed_raw",
                            value=round(tok_s, 2), unit="tok_s",
                            raw={"endpoint": best.get("provider_name")},
                        ))
        except Exception:
            continue  # skip endpoints silently

    # Normalise speed into 0..1 within this batch (simple min-max).
    if len(speeds) >= 2:
        lo, hi = min(speeds.values()), max(speeds.values())
        span = hi - lo if hi > lo else 1.0
        for key, raw in speeds.items():
            ranks.append(ExternalRank(
                source="openrouter", model_id=key, metric="speed",
                value=round((raw - lo) / span, 4), unit="score",
                raw={"raw_tok_s": raw, "batch_min": lo, "batch_max": hi},
            ))

    return ranks


def fetch_hf_leaderboard() -> list[ExternalRank]:
    """Pull the HuggingFace Open LLM Leaderboard quality signal.

    The leaderboard averages six benchmarks (IFEval, BBH, MATH, GPQA,
    MUSR, MMLU-Pro). We emit one ``quality`` row per matched model
    with the Average score rescaled into 0..1.

    Parquet access uses a lazy pandas import so the module stays
    importable on environments without pyarrow.
    """
    try:
        import pandas as pd  # noqa: F401 — required by pyarrow engine
        import pyarrow.parquet as pq
    except Exception:
        logger.debug("external_ranks: pandas/pyarrow not installed, skipping HF fetch")
        return []

    try:
        resp = httpx.get(_HF_LEADERBOARD_URL, timeout=30, follow_redirects=True)
        if resp.status_code != 200:
            logger.debug(f"external_ranks: HF leaderboard {resp.status_code}")
            return []
        table = pq.read_table(io.BytesIO(resp.content))
        df = table.to_pandas()
    except Exception as exc:
        logger.debug(f"external_ranks: HF parquet load failed: {exc}")
        return []

    # Column names follow the v2 schema; gracefully degrade if renamed.
    id_col = next((c for c in ("model_name", "fullname", "model") if c in df.columns), None)
    avg_col = next((c for c in ("average", "Average", "Average ⬆️") if c in df.columns), None)
    if id_col is None or avg_col is None:
        logger.debug("external_ranks: HF schema columns missing")
        return []

    ranks: list[ExternalRank] = []
    for _, row in df.iterrows():
        raw_id = str(row[id_col])
        key = _resolve_catalog_key(raw_id)
        if not key:
            continue
        try:
            avg = float(row[avg_col])
        except (TypeError, ValueError):
            continue
        # Raw averages are in 0..100 on the new leaderboard.
        normalised = max(0.0, min(1.0, avg / 100.0))
        ranks.append(ExternalRank(
            source="huggingface", model_id=key, metric="quality",
            value=round(normalised, 4), unit="score",
            raw={"raw_avg": avg, "id": raw_id},
        ))
    return ranks


def fetch_artificial_analysis() -> list[ExternalRank]:
    """Pull the Artificial Analysis intelligence + speed signals.

    Opt-in: returns [] unless ``artificial_analysis_api_key`` is set in
    Settings (or ``AA_API_KEY`` in the environment). The endpoint
    response schema is respected best-effort — fields we don't find are
    simply not emitted.
    """
    try:
        from app.config import get_settings
        s = get_settings()
        api_key = (
            s.artificial_analysis_api_key.get_secret_value()
            if hasattr(s, "artificial_analysis_api_key") else ""
        ) or os.getenv("AA_API_KEY", "") or os.getenv("ARTIFICIAL_ANALYSIS_API_KEY", "")
    except Exception:
        api_key = os.getenv("AA_API_KEY", "") or os.getenv("ARTIFICIAL_ANALYSIS_API_KEY", "")
    if not api_key:
        return []

    try:
        resp = httpx.get(
            _AA_API_URL,
            headers={"x-api-key": api_key},
            timeout=20,
        )
        if resp.status_code != 200:
            logger.debug(f"external_ranks: AA {resp.status_code}")
            return []
        payload = resp.json()
    except Exception as exc:
        logger.debug(f"external_ranks: AA fetch failed: {exc}")
        return []

    models = payload.get("data") or payload.get("models") or []
    ranks: list[ExternalRank] = []

    # AA emits multiple variants per base model (reasoning modes, effort
    # levels). Take the highest intelligence per catalog key so the base
    # model gets its strongest available signal.
    best_quality: dict[str, tuple[float, float]] = {}  # key -> (raw_index, normalised)
    best_speed:   dict[str, float] = {}                # key -> tok/s
    seen_raw:     dict[str, str] = {}                  # key -> originating slug

    for m in models:
        raw_id = m.get("slug") or m.get("model") or m.get("id") or ""
        # Strip effort/adaptive variant suffixes; fall back to the raw
        # slug if no suffix matches so bare base rows still resolve.
        base_id = raw_id
        for sfx in _AA_VARIANT_SUFFIXES:
            if base_id.endswith(sfx):
                base_id = base_id[: -len(sfx)]
                break
        key = _resolve_catalog_key(base_id) or _resolve_catalog_key(raw_id)
        if not key:
            continue

        evals = m.get("evaluations") or {}
        intel = (
            evals.get("artificial_analysis_intelligence_index")
            or m.get("artificial_analysis_intelligence_index")
            or m.get("intelligence_index")
        )
        if intel is not None:
            try:
                raw_val = float(intel)
                # AA intelligence runs roughly 0..100; clamp & normalise.
                norm = max(0.0, min(1.0, raw_val / 100.0))
                prev = best_quality.get(key)
                if prev is None or raw_val > prev[0]:
                    best_quality[key] = (raw_val, norm)
                    seen_raw[key] = raw_id
            except (TypeError, ValueError):
                pass

        tok_s = (
            m.get("median_output_tokens_per_second")
            or m.get("output_tokens_per_second_median")
            or m.get("tokens_per_second")
        )
        if tok_s is not None:
            try:
                raw_speed = float(tok_s)
                prev = best_speed.get(key)
                if prev is None or raw_speed > prev:
                    best_speed[key] = raw_speed
            except (TypeError, ValueError):
                pass

    for key, (raw_val, norm) in best_quality.items():
        ranks.append(ExternalRank(
            source="artificial_analysis", model_id=key, metric="quality",
            value=round(norm, 4), unit="score",
            raw={"raw_index": raw_val, "from_slug": seen_raw.get(key, "")},
        ))
    for key, raw_speed in best_speed.items():
        ranks.append(ExternalRank(
            source="artificial_analysis", model_id=key, metric="speed_raw",
            value=round(raw_speed, 2), unit="tok_s",
        ))

    # AA speeds can be normalised against OpenRouter batch via a separate
    # reducer pass in refresh_all; we don't double-normalise here.
    return ranks


# ── Persistence ─────────────────────────────────────────────────────────

def _upsert(ranks: Iterable[ExternalRank]) -> int:
    """Upsert a batch of rank rows. Returns number written."""
    try:
        from app.control_plane.db import execute
    except Exception:
        return 0
    written = 0
    for r in ranks:
        try:
            # task_type='' sentinel matches the schema (see migration 017);
            # keeps the ON CONFLICT simple without COALESCE acrobatics.
            tt = r.task_type or ""
            execute(
                """
                INSERT INTO control_plane.external_ranks
                       (source, model_id, metric, value, unit, task_type, raw_payload, fetched_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (source, model_id, metric, task_type)
                DO UPDATE SET value = EXCLUDED.value,
                              unit = EXCLUDED.unit,
                              raw_payload = EXCLUDED.raw_payload,
                              fetched_at = NOW()
                """,
                (r.source, r.model_id, r.metric, r.value, r.unit,
                 tt, json.dumps(r.raw or {})),
            )
            written += 1
        except Exception as exc:
            logger.debug(f"external_ranks: upsert {r.source}/{r.model_id}/{r.metric} failed: {exc}")
    return written


# ── Public API ──────────────────────────────────────────────────────────

def refresh_all(ttl_hours: float = DEFAULT_TTL_HOURS, force: bool = False) -> dict:
    """Fetch + persist ranks from every available source.

    Returns a per-source written count dict. Skips fetching if the most
    recent row is younger than ``ttl_hours`` and ``force`` is False.
    """
    summary = {"openrouter": 0, "huggingface": 0, "artificial_analysis": 0}

    if not force and not _stale(ttl_hours):
        logger.debug("external_ranks: skipping refresh — cache is fresh")
        return summary

    try:
        summary["openrouter"] = _upsert(fetch_openrouter_stats())
    except Exception as exc:
        logger.debug(f"external_ranks: openrouter refresh failed: {exc}")
    try:
        summary["huggingface"] = _upsert(fetch_hf_leaderboard())
    except Exception as exc:
        logger.debug(f"external_ranks: huggingface refresh failed: {exc}")
    try:
        summary["artificial_analysis"] = _upsert(fetch_artificial_analysis())
    except Exception as exc:
        logger.debug(f"external_ranks: artificial_analysis refresh failed: {exc}")

    logger.info(f"external_ranks: refresh complete — {summary}")
    return summary


def _stale(ttl_hours: float) -> bool:
    """Check whether the newest external_ranks row is older than TTL."""
    try:
        from app.control_plane.db import execute_scalar
        ts = execute_scalar(
            "SELECT MAX(fetched_at) FROM control_plane.external_ranks",
            (),
        )
    except Exception:
        return True
    if not ts:
        return True
    if isinstance(ts, datetime):
        age = datetime.now(timezone.utc) - ts.astimezone(timezone.utc)
        return age > timedelta(hours=ttl_hours)
    return True


def get_external_score(
    model_id: str,
    task_type: str | None = None,
    max_age_hours: float = DEFAULT_TTL_HOURS * 2,
) -> float | None:
    """Aggregate external quality signals for a catalog key.

    Returns ``None`` when no source has reported a quality metric for
    the model within ``max_age_hours``. Otherwise returns the mean of
    available quality-axis rows (``quality``, ``elo``, ``speed``) —
    ``cost`` and raw tokens counts are excluded from the blend because
    the selector already considers cost separately.
    """
    try:
        from app.control_plane.db import execute
    except Exception:
        return None

    rows = execute(
        """
        SELECT metric, value
          FROM control_plane.external_ranks
         WHERE model_id = %s
           AND task_type IN ('', %s)
           AND fetched_at >= NOW() - INTERVAL '1 hour' * %s
           AND metric IN ('quality', 'elo', 'speed')
        """,
        (model_id, task_type or "", float(max_age_hours)),
        fetch=True,
    ) or []
    values = [float(r["value"]) for r in rows if r.get("value") is not None]
    if not values:
        return None
    return round(statistics.mean(values), 4)


def get_external_breakdown(model_id: str) -> dict[str, dict[str, float]]:
    """Return per-source metric map for diagnostics / Signal command."""
    try:
        from app.control_plane.db import execute
    except Exception:
        return {}
    rows = execute(
        """
        SELECT source, metric, value, unit, fetched_at
          FROM control_plane.external_ranks
         WHERE model_id = %s
         ORDER BY source, metric
        """,
        (model_id,),
        fetch=True,
    ) or []
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        out.setdefault(r["source"], {})[r["metric"]] = float(r["value"])
    return out


def format_ranks(model_id: str) -> str:
    """Pretty-print external ranks for a model (Signal command output)."""
    breakdown = get_external_breakdown(model_id)
    if not breakdown:
        return f"No external ranks for {model_id}."
    lines = [f"External ranks for {model_id}:"]
    for source, metrics in breakdown.items():
        parts = [f"{m}={v:.3f}" for m, v in sorted(metrics.items())]
        lines.append(f"  {source}: {', '.join(parts)}")
    return "\n".join(lines)

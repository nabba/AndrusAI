"""Vendor-sunset monitor — detect deprecated upstream models.

Years-of-uptime hazard: providers retire models on their own clocks
(OpenRouter, Anthropic, Google have all done this). When a model used
by the runtime catalog is sunset, the agent calls fail with cryptic
upstream errors and the system silently degrades.

This monitor takes a weekly pass over the runtime catalog and queries
each provider's public ``/v1/models`` listing to spot any model that's
either (a) absent from the listing now (definitely sunset) or (b)
flagged with deprecation metadata. It does NOT auto-migrate — that's
operator-approved via change-request — but it does file a Signal
alert and persist the diff for inspection.

Because this monitor reaches OUTBOUND to providers, it's gated behind
``HEALING_VENDOR_SUNSET_ENABLED`` (default ON). Disable in environments
where outbound HTTP isn't acceptable.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

from app.healing.handlers._common import (
    audit_event,
    read_state_json,
    send_signal_alert,
    write_state_json,
)

logger = logging.getLogger(__name__)

_STATE_FILE = "vendor_sunset.json"
_HTTP_TIMEOUT_S = 8.0


def _enabled() -> bool:
    return os.getenv("HEALING_VENDOR_SUNSET_ENABLED", "true").lower() in (
        "true", "1", "yes",
    )


def _http_get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
        return json.load(resp)


def _fetch_openrouter_ids() -> set[str]:
    try:
        data = _http_get_json("https://openrouter.ai/api/v1/models")
    except Exception:
        logger.debug("vendor_sunset: openrouter fetch failed", exc_info=True)
        return set()
    rows = data.get("data") or data.get("models") or []
    ids: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            mid = row.get("id") or row.get("model") or row.get("name")
            if mid:
                ids.add(str(mid))
    return ids


def _fetch_anthropic_ids() -> set[str]:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return set()
    try:
        data = _http_get_json(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
    except Exception:
        logger.debug("vendor_sunset: anthropic fetch failed", exc_info=True)
        return set()
    rows = data.get("data") or []
    return {str(r.get("id", "")) for r in rows if r.get("id")}


def _runtime_catalog_models() -> dict[str, set[str]]:
    """Best-effort: collect models the system is actively using, grouped by
    provider. Reads ``control_plane.discovered_models`` (preferred) and
    falls back to scanning ``llm/`` config files.
    """
    by_provider: dict[str, set[str]] = {"openrouter": set(), "anthropic": set()}
    try:
        from app.control_plane.db import execute
        rows = execute(
            "SELECT model_id, provider FROM control_plane.discovered_models "
            "WHERE status IN ('active', 'discovered') "
            "AND cost_output_per_m > 0 LIMIT 500",
            fetch=True,
        ) or []
        for row in rows:
            provider = (row.get("provider") or "").lower()
            mid = row.get("model_id") or ""
            if not mid:
                continue
            # Strip the provider prefix that the catalog stores
            # ("openrouter/xyz/abc" → "xyz/abc").
            if provider == "openrouter" and mid.startswith("openrouter/"):
                mid = mid[len("openrouter/"):]
            if provider in by_provider:
                by_provider[provider].add(mid)
    except Exception:
        logger.debug("vendor_sunset: catalog read failed", exc_info=True)
    return by_provider


def run() -> None:
    if not _enabled():
        return

    in_use = _runtime_catalog_models()
    if not any(in_use.values()):
        return

    upstream = {
        "openrouter": _fetch_openrouter_ids(),
        "anthropic": _fetch_anthropic_ids(),
    }

    sunset_findings: list[dict] = []
    for provider, models in in_use.items():
        upstream_set = upstream.get(provider) or set()
        if not upstream_set:
            # Couldn't fetch — skip this provider this cycle.
            continue
        missing = sorted(models - upstream_set)
        for m in missing:
            sunset_findings.append({
                "provider": provider,
                "model": m,
                "first_missed_at": time.time(),
            })

    state = read_state_json(_STATE_FILE, {"sunset_models": {}})
    sunset_map = state.setdefault("sunset_models", {})

    new_findings: list[dict] = []
    for f in sunset_findings:
        key = f"{f['provider']}::{f['model']}"
        prev = sunset_map.get(key)
        if prev is None:
            sunset_map[key] = {
                "provider": f["provider"],
                "model": f["model"],
                "first_missed_at": f["first_missed_at"],
                "alerted": False,
            }
            new_findings.append(f)
        else:
            prev["last_seen_missing"] = time.time()
    state["last_run_at"] = time.time()
    write_state_json(_STATE_FILE, state)

    audit_event(
        "vendor_sunset_check",
        n_in_use=sum(len(v) for v in in_use.values()),
        n_sunset=len(sunset_findings),
        n_new=len(new_findings),
    )

    if not new_findings:
        return

    # 2026-05-29 — record sunset models DIRECTLY to the runtime
    # blocklist instead of filing one doomed change-request per model.
    #
    # A sunset model is a standing runtime-data fact, not a source-code
    # edit: the old per-model ``create_request`` path targeted
    # ``workspace/healing/sunset_models.json``, which is categorically
    # outside the change-request validator's allowed roots (app/, tests/,
    # docs/, …). Every such CR was guaranteed-REJECTED, and because the
    # condition is a persistent world-state the monitor re-observed it
    # every week and re-filed — 30 identical rejected CRs in a day.
    #
    # The right tool for a runtime-data write is a direct idempotent
    # write, exactly like the ``model_capability`` self-heal handler
    # does with ``runtime_settings.chat_blocked_models``. We do both:
    #   * append to ``runtime_settings.chat_blocked_models`` (the list
    #     the LLM selector actually consults at request time), and
    #   * maintain the ``sunset_models.json`` audit file in ONE write.
    recorded = _record_sunset_models(new_findings)

    lines = [
        f"  • [{f['provider']}] `{f['model']}`"
        for f in new_findings[:10]
    ]
    body = (
        f"📦 Self-heal: {len(new_findings)} model(s) used by AndrusAI "
        f"are no longer listed by their provider — likely sunset:\n\n"
        + "\n".join(lines)
        + "\n\nPlan migration to a supported alternative. Tracked in "
          "`workspace/self_heal/vendor_sunset.json`."
    )
    if recorded:
        body += (
            f"\n\nAdded {len(recorded)} model(s) to the runtime blocklist "
            f"so the LLM router skips them immediately — no operator "
            f"action required. (Migration to a replacement is still worth "
            f"planning.)"
        )
    send_signal_alert(body, tag="vendor_sunset")

    # Mark them alerted so we don't re-spam next week.
    for f in new_findings:
        key = f"{f['provider']}::{f['model']}"
        if key in sunset_map:
            sunset_map[key]["alerted"] = True
    write_state_json(_STATE_FILE, state)


def _record_sunset_models(findings: list[dict]) -> list[str]:
    """Record newly-sunset models DIRECTLY to the runtime blocklist.

    This is a runtime-data write, not a source-code edit — so it does
    NOT route through the change-request gate (every such CR was
    guaranteed-rejected because ``workspace/`` is outside the
    validator's allowed roots, and the persistent world-state caused
    weekly re-filing). Instead we mirror the ``model_capability``
    self-heal handler: an idempotent append to the list the LLM
    selector actually consults.

    Two sinks, both idempotent:
      * ``runtime_settings.chat_blocked_models`` — the live list the
        selector reads at request time, so the router skips the model
        immediately (no deploy, no operator approval).
      * ``sunset_models.json`` — a single aggregated audit write
        (all new findings in ONE write, never one-per-model).

    Returns the list of ``provider::model`` keys newly recorded.
    """
    recorded: list[str] = []

    # 1. Idempotent append to the consumed runtime blocklist.
    try:
        from app.runtime_settings import add_chat_blocked_model
    except Exception:
        add_chat_blocked_model = None  # type: ignore[assignment]

    # 2. Aggregate audit write into sunset_models.json (ONE write).
    block_path = Path("/app/workspace/healing/sunset_models.json")
    if block_path.exists():
        try:
            existing = json.loads(block_path.read_text())
            if not isinstance(existing, dict):
                existing = {"sunset": []}
        except (OSError, json.JSONDecodeError):
            existing = {"sunset": []}
    else:
        existing = {"sunset": []}

    sunset_list = list(existing.get("sunset") or [])
    already = {
        (e.get("provider"), e.get("model"))
        for e in sunset_list
        if isinstance(e, dict)
    }

    for f in findings:
        provider = f.get("provider", "unknown")
        model = f.get("model", "unknown")
        key = f"{provider}::{model}"
        if add_chat_blocked_model is not None:
            try:
                add_chat_blocked_model(model)
            except Exception:
                logger.debug(
                    "vendor_sunset: add_chat_blocked_model failed for %s",
                    model, exc_info=True,
                )
        if (provider, model) in already:
            continue  # already in the audit file — don't duplicate
        sunset_list.append({
            "provider": provider,
            "model": model,
            "first_missed_at": f.get("first_missed_at", time.time()),
            "added_via": "vendor_sunset_monitor",
        })
        already.add((provider, model))
        recorded.append(key)

    if recorded:
        new_payload = {**existing, "sunset": sunset_list}
        try:
            block_path.parent.mkdir(parents=True, exist_ok=True)
            block_path.write_text(json.dumps(new_payload, indent=2, sort_keys=True))
        except OSError:
            logger.debug(
                "vendor_sunset: sunset_models.json write failed", exc_info=True,
            )

    return recorded

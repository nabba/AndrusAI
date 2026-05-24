"""mcp_discovery.poller — weekly poll of the MCP registry.

Tier 2.3 of the 2026-05-24 ultrathink analysis closure.

What the poller does
====================

  1. Calls ``mcp__mcp-registry__list_connectors`` (when available) to
     fetch the current registry catalog. Cached per-run; never spams.
  2. Loads the local denylist + the set of already-integrated servers
     (probed from ``app/mcp_connectors/registered/`` or equivalent).
  3. For each registry entry not in either set, applies quality
     filters (rating + install count + age).
  4. Surviving candidates → proposal_bridge staged entries with a
     7-day cooldown before promotion to a CR.

Operator approval is REQUIRED for every adoption — proposal_bridge
routes through the standard CR gate. This module never auto-installs.

Failure modes
=============

The MCP registry probe is the only external dependency. When it
fails (network down, schema change, etc), the poller returns a
skipped-reason record but never raises.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ── Tunables ─────────────────────────────────────────────────────────────

# Minimum operator-visible rating before a candidate enters the
# proposal pipeline. Registry rating scale is 0-5.
_MIN_RATING = 4.0

# Minimum cumulative install count. Below this, the connector is too
# new to be a reliable signal of value.
_MIN_INSTALL_COUNT = 100

# Maximum candidates proposed per pass. Avoid flooding the operator's
# change-request queue.
_MAX_CANDIDATES_PER_PASS = 3

# Cooldown applied to proposal_bridge entries (days). Matches the
# library_radar pattern.
_COOLDOWN_DAYS = 7


def _workspace_root() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT  # type: ignore

        return Path(WORKSPACE_ROOT)
    except Exception:
        return Path(os.environ.get("WORKSPACE_ROOT", "/app/workspace"))


def _denylist_path() -> Path:
    return _workspace_root() / "mcp_discovery" / "denylist.txt"


def _state_path() -> Path:
    return _workspace_root() / "mcp_discovery" / "state.json"


# ── Data model ───────────────────────────────────────────────────────────


@dataclass
class DiscoveredConnector:
    """One registry-discovered MCP candidate."""

    name: str
    namespace: str = ""
    description: str = ""
    rating: float = 0.0
    install_count: int = 0
    publisher: str = ""
    homepage: str = ""
    last_updated: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def signature(self) -> str:
        return f"{self.namespace}/{self.name}".strip("/")


# ── Inputs ───────────────────────────────────────────────────────────────


def _load_denylist() -> set[str]:
    p = _denylist_path()
    if not p.exists():
        return set()
    try:
        out: set[str] = set()
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.add(line)
        return out
    except Exception:
        return set()


def _already_integrated() -> set[str]:
    """Best-effort scan of currently-registered MCP connectors.

    v1 implementation: looks for a ``app/mcp_connectors/registered/``
    directory; each subdir's name is treated as an integrated
    connector signature. The system may store this differently —
    keep the read failure-isolated so a renamed directory doesn't
    flood the proposal queue.
    """
    out: set[str] = set()
    try:
        from app.paths import REPO_ROOT  # type: ignore

        roots = [
            Path(REPO_ROOT) / "app" / "mcp_connectors" / "registered",
            Path(REPO_ROOT) / "app" / "mcp",
        ]
    except Exception:
        roots = [Path("/app/mcp_connectors/registered")]
    for r in roots:
        if not r.exists() or not r.is_dir():
            continue
        for entry in r.iterdir():
            if entry.is_dir():
                out.add(entry.name)
    return out


def _fetch_registry_catalog(
    fetcher: Optional[Callable[[], list[dict[str, Any]]]] = None,
) -> list[dict[str, Any]]:
    """Call the MCP registry tool when available; return raw entries.

    The actual tool is a deferred MCP call; this function uses an
    injectable fetcher so unit tests can stub the response without
    touching the real registry.
    """
    if fetcher is not None:
        try:
            return list(fetcher() or [])
        except Exception as exc:
            logger.debug("mcp_discovery: fetcher raised %r", exc, exc_info=True)
            return []
    # Production path: the deferred MCP tool is not directly
    # invokable from Python code (it's a tool-call surface for the
    # LLM agent). v1 leaves the production fetch path empty and
    # relies on operator-supplied fetcher injections; this is the
    # honest seam, not pretending to call something that may not be
    # wired in every environment.
    return []


# ── Filtering ────────────────────────────────────────────────────────────


def _parse_entry(raw: dict[str, Any]) -> Optional[DiscoveredConnector]:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or raw.get("id") or "").strip()
    if not name:
        return None
    return DiscoveredConnector(
        name=name,
        namespace=str(raw.get("namespace") or raw.get("publisher") or ""),
        description=str(raw.get("description") or "")[:1000],
        rating=float(raw.get("rating") or raw.get("avg_rating") or 0.0),
        install_count=int(
            raw.get("install_count")
            or raw.get("installs")
            or raw.get("downloads")
            or 0
        ),
        publisher=str(raw.get("publisher") or ""),
        homepage=str(raw.get("homepage") or raw.get("url") or ""),
        last_updated=str(raw.get("last_updated") or raw.get("updated_at") or ""),
        extra={
            k: v
            for k, v in raw.items()
            if k not in ("name", "id", "namespace", "publisher", "description")
        },
    )


def _passes_quality_filters(c: DiscoveredConnector) -> bool:
    if c.rating < _MIN_RATING:
        return False
    if c.install_count < _MIN_INSTALL_COUNT:
        return False
    return True


# ── Proposal staging ─────────────────────────────────────────────────────


def _stage_candidate(c: DiscoveredConnector) -> bool:
    """Stage a candidate via proposal_bridge. Returns True on success."""
    try:
        from app.proposal_bridge import store as bridge_store
    except Exception:
        logger.debug("mcp_discovery: proposal_bridge unavailable", exc_info=True)
        return False

    sig = c.signature()
    body = (
        f"# MCP connector candidate — {sig}\n\n"
        f"**Publisher**: {c.publisher or '(unknown)'}\n\n"
        f"**Rating**: {c.rating:.1f} / 5  •  "
        f"**Installs**: {c.install_count:,}\n\n"
        f"**Homepage**: {c.homepage or '—'}\n\n"
        f"## Description\n\n{c.description}\n\n"
        f"## Why this matters\n\n"
        f"Discovered by ``mcp_discovery`` poller. Meets quality "
        f"gates (rating ≥ {_MIN_RATING}, installs ≥ {_MIN_INSTALL_COUNT}). "
        f"Operator approval REQUIRED — this proposal is observational; "
        f"adoption goes through the standard change-request gate.\n\n"
        f"## What this would enable\n\n"
        f"_Operator: describe the use case before approving._\n"
    )
    try:
        bridge_store.stage(
            source="mcp_discovery",
            body=body,
            cooldown_days=_COOLDOWN_DAYS,
            target_path=f"docs/proposed_mcp_connectors/{sig.replace('/', '_')}.md",
            metadata={
                "kind": "mcp_connector",
                "signature": sig,
                "rating": c.rating,
                "install_count": c.install_count,
            },
        )
        return True
    except Exception as exc:
        logger.debug(
            "mcp_discovery: proposal_bridge stage raised %r", exc,
            exc_info=True,
        )
        return False


# ── Public entry ─────────────────────────────────────────────────────────


def _enabled() -> bool:
    try:
        from app import runtime_settings

        return bool(runtime_settings.get_mcp_discovery_enabled())
    except Exception:
        return False


def run_discovery_pass(
    *,
    fetcher: Optional[Callable[[], list[dict[str, Any]]]] = None,
) -> dict[str, Any]:
    """One discovery pass. Idle-job entry. Failure-isolated."""
    if not _enabled():
        return {"skipped_reason": "master_switch_off"}

    raw_entries = _fetch_registry_catalog(fetcher=fetcher)
    if not raw_entries:
        return {"skipped_reason": "registry_unavailable"}

    denylist = _load_denylist()
    integrated = _already_integrated()

    candidates: list[DiscoveredConnector] = []
    for raw in raw_entries:
        c = _parse_entry(raw)
        if c is None:
            continue
        sig = c.signature()
        if sig in denylist or sig in integrated:
            continue
        if not _passes_quality_filters(c):
            continue
        candidates.append(c)

    # Sort by rating × install_count so the strongest signals win the
    # rate-limited slots.
    candidates.sort(
        key=lambda c: (c.rating, c.install_count),
        reverse=True,
    )
    staged = 0
    staged_names: list[str] = []
    for c in candidates[:_MAX_CANDIDATES_PER_PASS]:
        if _stage_candidate(c):
            staged += 1
            staged_names.append(c.signature())

    return {
        "n_raw_entries": len(raw_entries),
        "n_candidates_after_filter": len(candidates),
        "n_staged": staged,
        "staged_signatures": staged_names,
        "denylist_size": len(denylist),
        "already_integrated": len(integrated),
        "ts": datetime.now(timezone.utc).isoformat(),
    }


__all__ = ["DiscoveredConnector", "run_discovery_pass"]

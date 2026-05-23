"""P2#a — Multi-source CVE fallback.

PROGRAM §63.9 (P2 hardening, 2026-05-23). The existing
``dependency_radar._gather_cves`` queries OSV.dev only. Two
decade-scale risks:

  * **OSV.dev goes away** — the project gets archived, the URL
    changes, the API rotates. CVE detection silently fails.
  * **OSV.dev silently misses** — a CVE is published to GitHub
    Advisory + NVD but the OSV ingestor hasn't picked it up.

This module wraps the existing OSV call with a fallback chain:

  1. **Primary** — OSV.dev batch query (same code-path as before).
  2. **Secondary** — GitHub Security Advisory REST endpoint
     ``api.github.com/advisories?ecosystem=pip&affects=<package>``.
     No auth required for public advisories; rate-limited to 60 req/hr.

When BOTH succeed and disagree (one finds CVEs the other doesn't),
the radar logs a ``source_divergence`` marker so the operator can
spot OSV drift. When ONE succeeds and the other fails, we union
their results — losing one source doesn't silence CVE detection.

Backward-compatibility: ``query_with_fallback`` returns the same
``{package: [vuln_record]}`` shape ``_gather_cves`` already produces.
Existing callers don't change.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


_GITHUB_ADVISORY_URL = "https://api.github.com/advisories"
_REQUEST_TIMEOUT_S = 20
_USER_AGENT = "AndrusAI-UpgradeLifecycle/1.0"


# ── GitHub Advisory adapter ──────────────────────────────────────────────


def _github_advisory_fetch(
    packages: list[tuple[str, str]],
    *,
    fetcher: Optional[Callable[[str], bytes]] = None,
) -> dict[str, list[dict[str, Any]]]:
    """Query GitHub Security Advisory for each ``(pkg, version)``.

    Returns ``{package: [{id, summary, severity, affected_versions, ...}]}``.
    Returns empty on any failure (graceful degradation).

    ``fetcher`` is injectable for tests; defaults to urllib GET.
    """
    if not packages:
        return {}

    def _default_fetch(url: str) -> bytes:
        req = urllib.request.Request(
            url, headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_S) as resp:
            return resp.read()

    fetch_fn = fetcher or _default_fetch

    out: dict[str, list[dict[str, Any]]] = {}
    for (pkg, ver) in packages:
        try:
            url = (
                f"{_GITHUB_ADVISORY_URL}?ecosystem=pip"
                f"&affects={urllib.parse.quote(pkg)}"
            )
            body = fetch_fn(url)
            advisories = json.loads(body.decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, TimeoutError):
            continue
        except Exception:
            logger.debug(
                "cve_sources: github fetch failed for %s", pkg, exc_info=True,
            )
            continue
        if not isinstance(advisories, list):
            continue
        normalized: list[dict[str, Any]] = []
        for adv in advisories:
            cve_id = (
                adv.get("cve_id")
                or adv.get("ghsa_id")
                or adv.get("id")
            )
            if not cve_id:
                continue
            # Best-effort affected-version match (the API returns the
            # full advisory; we're conservative — if the API found it,
            # we surface it and let the operator narrow).
            normalized.append({
                "id": str(cve_id),
                "summary": str(adv.get("summary") or "")[:200],
                "severity": str(adv.get("severity") or "unknown"),
                "source": "github_advisory",
            })
        if normalized:
            out[pkg] = normalized
    return out


# ── Fallback composition ─────────────────────────────────────────────────


def _emit_divergence_landmark(
    package: str,
    osv_ids: set[str],
    github_ids: set[str],
) -> None:
    """Surface CVE-source divergence to the continuity ledger.

    Failure-isolated — the merge must NEVER fail because the ledger
    emit failed. ``summarise_drift`` aggregates ``ecosystem_snapshot``
    counts year-over-year so the operator sees CVE-source-health drift
    surface in the annual reflection.
    """
    try:
        from app.identity.continuity_ledger import record_event
        osv_sorted = sorted(i for i in osv_ids if i)
        github_sorted = sorted(i for i in github_ids if i)
        only_osv = sorted(set(osv_sorted) - set(github_sorted))
        only_github = sorted(set(github_sorted) - set(osv_sorted))
        record_event(
            kind="ecosystem_snapshot",
            actor="upgrade_lifecycle.cve_sources",
            summary=(
                f"CVE source divergence: {package} — "
                f"OSV-only={len(only_osv)} GitHub-only={len(only_github)}"
            ),
            detail={
                "subkind": "cve_source_divergence",
                "package": package,
                "osv_finding": osv_sorted,
                "github_finding": github_sorted,
                "only_osv": only_osv,
                "only_github": only_github,
            },
        )
    except Exception:
        logger.debug(
            "cve_sources: divergence ledger emit failed for %s",
            package, exc_info=True,
        )


def _merge_results(
    primary: dict[str, list[dict[str, Any]]],
    secondary: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Union primary + secondary per package, dedup by CVE id.

    Returns ``(merged, divergent_packages)`` where ``divergent_packages``
    is the list of packages where ONE source found CVEs the other
    didn't — useful for spotting OSV drift.
    """
    merged: dict[str, list[dict[str, Any]]] = {}
    all_pkgs = set(primary.keys()) | set(secondary.keys())
    divergent: list[str] = []
    for pkg in sorted(all_pkgs):
        prim_ids = {str(v.get("id") or "") for v in primary.get(pkg, [])}
        sec_ids = {str(v.get("id") or "") for v in secondary.get(pkg, [])}
        only_prim = prim_ids - sec_ids
        only_sec = sec_ids - prim_ids
        if only_prim or only_sec:
            divergent.append(pkg)
            _emit_divergence_landmark(pkg, prim_ids, sec_ids)

        # Build merged list with dedup by id.
        seen_ids: set[str] = set()
        rows: list[dict[str, Any]] = []
        for source_rows in (primary.get(pkg, []), secondary.get(pkg, [])):
            for row in source_rows:
                rid = str(row.get("id") or "")
                if rid and rid not in seen_ids:
                    rows.append(row)
                    seen_ids.add(rid)
        if rows:
            merged[pkg] = rows
    return merged, divergent


def query_with_fallback(
    packages: list[tuple[str, str]],
    *,
    primary_runner: Callable[
        [list[tuple[str, str]]], dict[str, list[dict[str, Any]]],
    ],
    secondary_runner: Optional[
        Callable[[list[tuple[str, str]]], dict[str, list[dict[str, Any]]]]
    ] = None,
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Query primary; on empty or failure, also query secondary; merge.

    Returns ``(merged_results, divergent_packages)``.

    The ``primary_runner`` is the existing OSV.dev path (kept for
    backward-compat). The ``secondary_runner`` defaults to GitHub
    Advisory. Both runners follow the same ``packages -> dict``
    contract.

    Failure semantics — each runner is independently failure-isolated.
    If primary raises, secondary still runs. If secondary raises,
    primary's result is returned untouched. If both raise, returns
    empty dict.
    """
    if not packages:
        return {}, []

    primary_result: dict[str, list[dict[str, Any]]] = {}
    try:
        primary_result = dict(primary_runner(packages) or {})
    except Exception:
        logger.debug(
            "cve_sources: primary runner raised; falling back",
            exc_info=True,
        )

    if secondary_runner is None:
        secondary_runner = _github_advisory_fetch

    secondary_result: dict[str, list[dict[str, Any]]] = {}
    try:
        secondary_result = dict(secondary_runner(packages) or {})
    except Exception:
        logger.debug(
            "cve_sources: secondary runner raised; using primary alone",
            exc_info=True,
        )

    # Both empty → no CVEs (the legitimate case + the both-broken case
    # are indistinguishable here; the next pass will catch persistent
    # failure via the upgrade_lifecycle_health monitor).
    if not primary_result and not secondary_result:
        return {}, []

    merged, divergent = _merge_results(primary_result, secondary_result)
    if divergent:
        logger.info(
            "cve_sources: source divergence for %d package(s): %s",
            len(divergent), ", ".join(divergent[:5]),
        )
    return merged, divergent

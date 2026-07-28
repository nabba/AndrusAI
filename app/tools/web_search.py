"""
web_search.py — Cascading web search with three-tier fallback.

Tier 1: Brave Search API (paid, primary; auto-backs-off on 402 quota errors).
Tier 2: Self-hosted SearXNG (free; aggregates Google/Bing/DDG via firecrawl-stack).
Tier 3: DuckDuckGo HTML scrape (free; last resort, no infra dependency).

Public API (preserved for all existing callers):
  - search_brave(query, count=5) -> list[dict]   (programmatic; cascades all tiers)
  - web_search(query) -> str                      (CrewAI @tool wrapper)
  - get_search_status() -> dict                   (for dashboard health surface)
"""
from __future__ import annotations

import contextvars
import logging
import os
import time

import requests
from crewai.tools import tool

from app.config import get_brave_api_key
from app.evidence_capture import record_search_results
from app.tools.search_validation import validate_search_results as _validate_results

logger = logging.getLogger(__name__)

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://searxng:8080/search")
DDG_HTML_URL = "https://html.duckduckgo.com/html/"

# Reusable HTTP session for outbound calls
_session = requests.Session()
_session.headers.update({
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
})

# ── Backend health state (module-local; per-process) ─────────────────────────
# When Brave returns 402 (quota exhausted), don't hammer the API for a day —
# the quota resets monthly so the backoff doesn't need to be precise.
_BRAVE_QUOTA_BACKOFF_S = 24 * 3600

# The backoff is PERSISTED (2026-07-25). It used to live only in this module
# global, so every gateway restart reset it to 0 and the next search hit a
# known-exhausted paid API again — earning another 402 and another 24h "backoff"
# that the next restart would also forget. Observed three times on 2026-07-25
# alone, matching that day's restarts. A quota that resets monthly must not be
# re-probed on a process lifecycle.
#
# ``workspace/`` is a bind mount, so the file survives container recreation.
# Every access is failure-isolated: an unreadable or unwritable state file
# degrades to the old in-memory behaviour rather than breaking search.
_BRAVE_QUOTA_STATE_PATH = os.environ.get(
    "BRAVE_QUOTA_STATE_PATH", "/app/workspace/.brave_quota_block"
)

_brave_quota_blocked_until: float = 0.0  # epoch seconds; 0 = no block
_brave_quota_state_loaded: bool = False
_last_backend_used: str | None = None
_last_failure_chain: list[str] = []
_last_rejection_counts: dict[str, int] = {}
# Why a backend produced nothing this call, keyed by backend name. Populated by
# ``_record_backend_error``; consumed by ``_chain_label``.
_last_backend_errors: dict[str, str] = {}


def _record_backend_error(backend: str, exc: BaseException) -> None:
    """Remember why ``backend`` produced nothing, so the failure chain can name
    a real cause.

    Every backend helper returns ``[]`` both when the search genuinely matched
    nothing and when the request raised. The chain label was inferred from that
    empty list alone, so a timeout, an HTTP error and a real zero-hit result all
    read ``<backend>:no_results`` — which is why the 2026-07-25 investigation
    could not tell whether SearXNG was broken or simply had no matches (see
    reports/GATE_DIAGNOSIS_2026-07-25.md, Addendum 10 "Unresolved").
    """
    _last_backend_errors[backend] = f"{type(exc).__name__}: {exc}"[:200]


def _chain_label(backend: str) -> str:
    """``<backend>:no_results`` only when the backend really returned nothing."""
    reason = _last_backend_errors.get(backend)
    return f"{backend}:error({reason})" if reason else f"{backend}:no_results"


def _load_brave_quota_block() -> None:
    """Read the persisted block deadline once per process."""
    global _brave_quota_blocked_until, _brave_quota_state_loaded
    if _brave_quota_state_loaded:
        return
    _brave_quota_state_loaded = True
    try:
        with open(_BRAVE_QUOTA_STATE_PATH) as handle:
            stored = float((handle.read() or "0").strip() or 0)
    except Exception:
        return
    # Ignore a nonsensically distant deadline so a corrupt file can't disable
    # Brave forever.
    if stored > time.time() + _BRAVE_QUOTA_BACKOFF_S:
        logger.warning(
            "web_search: ignoring implausible persisted Brave block (%s)", stored,
        )
        return
    if stored > _brave_quota_blocked_until:
        _brave_quota_blocked_until = stored
        remaining = stored - time.time()
        if remaining > 0:
            logger.info(
                "web_search: Brave still quota-blocked for %.1fh (persisted)",
                remaining / 3600.0,
            )


def _persist_brave_quota_block(until_epoch: float) -> None:
    try:
        with open(_BRAVE_QUOTA_STATE_PATH, "w") as handle:
            handle.write(str(until_epoch))
    except Exception:
        logger.debug(
            "web_search: could not persist Brave quota block to %s",
            _BRAVE_QUOTA_STATE_PATH, exc_info=True,
        )


# ── Per-task search budget (principled stopping criterion) ───────────────────
# Constitution §Ecological Responsibility + the alignment audit's "no principled
# stopping criterion for web searches" gap. Composes with the researcher agent's
# coarse ReAct ``max_iter`` cap: this is the search-SPECIFIC stop that returns an
# instructive "synthesize now" message so the agent concludes gracefully instead
# of erroring out at the iteration ceiling. Only the agent-facing @tool path is
# bounded, and only inside an active ``search_budget`` context — direct
# ``search_brave`` callers (atlas, fiction, research_orchestrator) are untouched.
_DEFAULT_MAX_SEARCHES = int(os.environ.get("WEB_SEARCH_MAX_CALLS_PER_TASK", "6") or "6")
_search_calls_remaining: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "web_search_calls_remaining", default=None
)


class search_budget:
    """Context manager bounding ``web_search`` tool calls within one research
    task/sub-agent. Re-entrant-safe per thread via ContextVar; resets on exit."""

    def __init__(self, max_calls: int | None = None):
        self._max = max_calls if (max_calls and max_calls > 0) else _DEFAULT_MAX_SEARCHES
        self._token = None

    def __enter__(self) -> "search_budget":
        self._token = _search_calls_remaining.set(self._max)
        return self

    def __exit__(self, *exc) -> bool:
        if self._token is not None:
            try:
                _search_calls_remaining.reset(self._token)
            except (ValueError, LookupError):
                pass
        return False


def _brave_blocked_now() -> bool:
    _load_brave_quota_block()
    return _brave_quota_blocked_until > time.time()


def _search_brave_raw(query: str, count: int) -> list[dict] | None:
    """Returns a result list on success, None on quota-exhaustion (signal to
    skip Brave for the rest of the backoff window), [] on any other failure.
    """
    global _brave_quota_blocked_until
    if _brave_blocked_now():
        return None
    try:
        r = _session.get(
            BRAVE_SEARCH_URL,
            headers={"X-Subscription-Token": get_brave_api_key()},
            params={"q": query, "count": count},
            timeout=10,
        )
        if r.status_code == 402:
            _brave_quota_blocked_until = time.time() + _BRAVE_QUOTA_BACKOFF_S
            _persist_brave_quota_block(_brave_quota_blocked_until)
            logger.warning(
                "web_search: Brave quota exhausted (HTTP 402) — backing off %dh "
                "(persisted, so a restart won't re-probe)",
                _BRAVE_QUOTA_BACKOFF_S // 3600,
            )
            return None
        r.raise_for_status()
        data = r.json()
        return [
            {
                "title": x.get("title", ""),
                "url": x.get("url", ""),
                "description": x.get("description", ""),
            }
            for x in data.get("web", {}).get("results", [])[:count]
        ]
    except Exception as exc:
        logger.debug("web_search: brave failed: %s", exc)
        _record_backend_error("brave", exc)
        return []


def _search_searxng(query: str, count: int) -> list[dict]:
    """Self-hosted SearXNG via the firecrawl stack. Empty list on failure."""
    try:
        r = requests.get(
            SEARXNG_URL,
            params={"q": query, "format": "json", "language": "en"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        return [
            {
                "title": x.get("title", "") or "",
                "url": x.get("url", "") or "",
                "description": x.get("content", "") or "",
            }
            for x in (data.get("results") or [])[:count]
        ]
    except Exception as exc:
        logger.debug("web_search: searxng failed: %s", exc)
        _record_backend_error("searxng", exc)
        return []


def _search_duckduckgo(query: str, count: int) -> list[dict]:
    """Last-resort DDG HTML scrape. Empty list on failure."""
    try:
        from bs4 import BeautifulSoup
        r = requests.post(
            DDG_HTML_URL,
            data={"q": query},
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; AndrusAI-tech-radar)",
                "Accept": "text/html",
            },
            timeout=15,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        out: list[dict] = []
        for div in soup.select("div.result")[: count * 2]:  # over-fetch; some are ads
            link = div.select_one("a.result__a")
            snippet = div.select_one(".result__snippet")
            if not link:
                continue
            url = str(link.get("href", "") or "")
            # DDG redirector — strip wrapper if present
            if "uddg=" in url:
                from urllib.parse import unquote, urlparse, parse_qs
                qs = parse_qs(urlparse(url).query)
                url = unquote(qs.get("uddg", [url])[0])
            out.append({
                "title": link.get_text(strip=True),
                "url": url,
                "description": snippet.get_text(strip=True) if snippet else "",
            })
            if len(out) >= count:
                break
        return out
    except Exception as exc:
        logger.debug("web_search: ddg failed: %s", exc)
        _record_backend_error("ddg", exc)
        return []


def search_brave(query: str, count: int = 5) -> list[dict]:
    """Cascading web search: Brave → SearXNG → DuckDuckGo.

    Function name is preserved for backwards compatibility with all existing
    callers (atlas/api_scout, atlas/learning_planner, fiction_inspiration,
    research_orchestrator, research_adapters/linkedin_data). They get the
    fallback chain transparently — no caller changes needed.

    Always returns a list (possibly empty when every tier failed).
    """
    global _last_backend_used, _last_failure_chain, _last_rejection_counts
    global _last_backend_errors
    chain: list[str] = []
    _last_rejection_counts = {}
    _last_backend_errors = {}

    res = _search_brave_raw(query, count)
    if res:
        valid, rejected = _validate_results(query, res, backend="brave")
        _last_rejection_counts["brave"] = rejected
        if valid:
            _last_backend_used = "brave"
            _last_failure_chain = chain
            record_search_results(query, valid[:count], "brave")
            return valid[:count]
        chain.append("brave:rejected")
    else:
        chain.append("brave:quota" if res is None else _chain_label("brave"))

    res = _search_searxng(query, count)
    if res:
        valid, rejected = _validate_results(query, res, backend="searxng")
        _last_rejection_counts["searxng"] = rejected
        if valid:
            _last_backend_used = "searxng"
            _last_failure_chain = chain
            record_search_results(query, valid[:count], "searxng")
            return valid[:count]
        chain.append("searxng:rejected")
    else:
        chain.append(_chain_label("searxng"))

    res = _search_duckduckgo(query, count)
    if res:
        valid, rejected = _validate_results(query, res, backend="ddg")
        _last_rejection_counts["ddg"] = rejected
        if valid:
            _last_backend_used = "ddg"
            _last_failure_chain = chain
            record_search_results(query, valid[:count], "ddg")
            return valid[:count]
        chain.append("ddg:rejected")
    else:
        chain.append(_chain_label("ddg"))

    _last_backend_used = None
    _last_failure_chain = chain
    logger.warning(
        "web_search: all backends failed for %r: %s",
        query[:60], chain,
    )
    return []


def get_search_status() -> dict:
    """Snapshot of search-backend health for the dashboard.

    Lets the React app tell the user *why* tech radar might be quiet —
    e.g. "Brave quota exhausted, falling back to SearXNG" — instead of
    silently showing an empty list.
    """
    return {
        "last_backend_used": _last_backend_used,
        "last_failure_chain": list(_last_failure_chain),
        "last_rejection_counts": dict(_last_rejection_counts),
        "last_backend_errors": dict(_last_backend_errors),
        "brave_quota_blocked_until": (
            _brave_quota_blocked_until if _brave_blocked_now() else None
        ),
    }


@tool("web_search")
def web_search(query: str) -> str:
    """
    Search the web (Brave → SearXNG → DuckDuckGo fallback chain).
    Returns top 5 results as title + URL + snippet.
    """
    remaining = _search_calls_remaining.get()
    if remaining is not None:
        if remaining <= 0:
            return (
                "SEARCH BUDGET REACHED for this task. Stop searching now and "
                "synthesize an answer from the results you already have. If key "
                "facts are still missing, say so explicitly rather than searching "
                "again."
            )
        _search_calls_remaining.set(remaining - 1)
    results = search_brave(query, 5)
    if not results:
        return "No results found."
    lines = []
    for item in results:
        title = item.get("title") or "No title"
        url = item.get("url") or ""
        snippet = item.get("description") or "No description"
        lines.append(f"**{title}**\n{url}\n{snippet}\n")
    return "\n".join(lines)


# ── Tool registry annotation (Phase 1a, passive) ────────────────────
try:
    from app.tool_registry import Lifecycle, Tier, register_tool

    @register_tool(
        name="web_search",
        capabilities=["searches-web"],
        description=(
            "Search the public web with three-tier fallback: Brave → "
            "SearxNG → DuckDuckGo. Returns top results as a formatted "
            "markdown list (title, URL, snippet). Use this when you "
            "need information beyond your training data — current "
            "events, recent docs, freshly published research."
        ),
        tier=Tier.PRODUCTION,
        lifecycle=Lifecycle.SINGLETON,
    )
    def _web_search_registry_factory():
        return web_search
except ImportError:
    pass

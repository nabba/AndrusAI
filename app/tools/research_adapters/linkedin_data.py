"""
linkedin_data — "Who's the head of sales at <company>, and what's their
LinkedIn URL?" adapter for the research orchestrator.

Registered under two names — ``linkedin_data`` (descriptive) and
``sales_navigator`` (familiar to sales-ops users).  Both resolve to the
same adapter function.

Provider selection
==================
LinkedIn does not offer a public Sales-Navigator-grade API to
non-partner apps.  This adapter picks from three providers in priority
order, using the first one that's available and produces a hit:

1. **Proxycurl** (``PROXYCURL_API_KEY`` env var)
   Paid, ~$0.01–$0.03 per lookup.  Two-step flow:

     a) Resolve company-domain → company LinkedIn URL
        (``/linkedin/company/resolve``)
     b) Search the company's employees filtered by title regex
        (``/linkedin/company/employee/search``)

   Accurate and the canonical production path.

2. **Brave structural search** (``BRAVE_API_KEY`` / no extra config)
   Free fallback: query ``"Head of Sales" site:linkedin.com/in "<company>"``
   and extract the top ``/in/`` URL.  Works surprisingly often for CEE
   because sales leaders there commonly put their title + employer in
   their LinkedIn headline.  Lower accuracy than Proxycurl — may
   confuse "Head of Sales at Monstro Bank" for "Head of Sales at
   Monstro AG" if names collide.

3. **None available** → returns ``None``.  The orchestrator's
   ``known_hard`` path produces the honest "N/A" label.

Fields filled
=============
* ``head_of_sales`` — "Name (headline/title) [provider]"
* ``head_of_sales_linkedin`` / ``linkedin_head_of_sales`` — profile URL

Does **not** fill ``head_of_sales_email`` — neither provider returns
that without a separate email-finder add-on (Apollo handles email
via its own adapter).
"""
from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Per-process cache: (domain_or_name, provider) -> person dict | None.
_CACHE: dict[tuple[str, str], dict | None] = {}

_PROXYCURL_BASE = "https://nubela.co/proxycurl/api"
_TIMEOUT_SECS = 12

# Field keys this adapter can fill.
_SUPPORTED_FIELDS = frozenset({
    "head_of_sales",
    "head_of_sales_linkedin",
    "linkedin_head_of_sales",  # alias
})

# Structural-search title variants for the Brave fallback.
_FALLBACK_TITLE_QUERIES = (
    '"Head of Sales"',
    '"VP Sales"',
    '"Sales Director"',
    '"Chief Revenue Officer"',
)


def _domain_of(subject: dict) -> str:
    """Bare host form suitable for API lookups.  Empty string when
    unknown — caller handles."""
    homepage = subject.get("homepage") or subject.get("url") or ""
    if not homepage:
        return ""
    try:
        url = homepage if "://" in homepage else f"https://{homepage}"
        host = urlparse(url).netloc or urlparse(f"//{homepage}").netloc
        return host.replace("www.", "").strip().lower()
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════════════
# Provider 1: Proxycurl
# ══════════════════════════════════════════════════════════════════════

def _proxycurl_search(domain: str, api_key: str) -> dict | None:
    """Two-step Proxycurl lookup; returns
    ``{"name", "title", "linkedin_url"}`` or ``None``."""
    cache_key = (domain, "proxycurl")
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    try:
        import requests
    except ImportError:
        return None

    headers = {"Authorization": f"Bearer {api_key}"}

    # Step 1 — company-domain → LinkedIn company URL.
    try:
        r = requests.get(
            f"{_PROXYCURL_BASE}/linkedin/company/resolve",
            headers=headers,
            params={"company_domain": domain},
            timeout=_TIMEOUT_SECS,
        )
    except Exception as exc:
        logger.debug("proxycurl resolve raised %s", exc)
        _CACHE[cache_key] = None
        return None
    if r.status_code != 200:
        logger.debug(
            "proxycurl resolve status=%s body=%s",
            r.status_code, (r.text or "")[:200],
        )
        _CACHE[cache_key] = None
        return None
    try:
        company_url = (r.json() or {}).get("url") or ""
    except Exception:
        company_url = ""
    if not company_url:
        _CACHE[cache_key] = None
        return None

    # Step 2 — employee search with sales-title regex.
    try:
        r = requests.get(
            f"{_PROXYCURL_BASE}/linkedin/company/employee/search",
            headers=headers,
            params={
                "url": company_url,
                "keyword_regex": (
                    "(?i)(head.of.sales|vp.of.sales|vp.sales|sales.director|"
                    "director.of.sales|chief.revenue.officer|"
                    "chief.commercial.officer)"
                ),
                "page_size": 1,
            },
            timeout=_TIMEOUT_SECS,
        )
    except Exception as exc:
        logger.debug("proxycurl employee/search raised %s", exc)
        _CACHE[cache_key] = None
        return None
    if r.status_code != 200:
        logger.debug(
            "proxycurl employee/search status=%s body=%s",
            r.status_code, (r.text or "")[:200],
        )
        _CACHE[cache_key] = None
        return None

    try:
        body = r.json() or {}
    except Exception:
        _CACHE[cache_key] = None
        return None
    employees = body.get("employees") or []
    if not employees:
        _CACHE[cache_key] = None
        return None

    emp = employees[0] or {}
    profile = emp.get("profile") or {}
    first = (profile.get("first_name") or emp.get("first_name") or "").strip()
    last = (profile.get("last_name") or emp.get("last_name") or "").strip()
    name = f"{first} {last}".strip()
    title = (
        profile.get("headline") or profile.get("occupation")
        or emp.get("title") or ""
    ).strip()
    url = emp.get("profile_url") or profile.get("public_identifier") or ""
    if url and not url.startswith("http"):
        url = f"https://www.linkedin.com/in/{url}"
    if not (name or url):
        _CACHE[cache_key] = None
        return None
    result = {"name": name, "title": title, "linkedin_url": url}
    _CACHE[cache_key] = result
    return result


# ══════════════════════════════════════════════════════════════════════
# Provider 2: Brave structural search (free fallback)
# ══════════════════════════════════════════════════════════════════════

def _brave_fallback(company_name: str) -> dict | None:
    """Use Brave search to find ``site:linkedin.com/in/ "<title>"
    "<company>"``.  Returns ``{"name", "title", "linkedin_url"}`` or
    ``None`` on no match."""
    if not company_name:
        return None
    cache_key = (company_name, "brave")
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    try:
        from app.tools.web_search import search_brave
    except Exception:
        return None

    for title_q in _FALLBACK_TITLE_QUERIES:
        q = f'{title_q} site:linkedin.com/in "{company_name}"'
        try:
            results = search_brave(q, count=3)
        except Exception:
            results = []
        for r in results or []:
            url = (r or {}).get("url") or ""
            if "linkedin.com/in/" not in url:
                continue
            # Brave titles on LinkedIn results are "Name - Headline - LinkedIn"
            raw_title = (r.get("title") or "").strip()
            # Strip the trailing " - LinkedIn" suffix if present.
            if raw_title.endswith(" - LinkedIn"):
                raw_title = raw_title[:-len(" - LinkedIn")]
            parts = [p.strip() for p in raw_title.split(" - ", 1)]
            name = parts[0] if parts else ""
            headline = parts[1] if len(parts) > 1 else ""
            result = {
                "name": name,
                "title": headline,
                "linkedin_url": url,
            }
            _CACHE[cache_key] = result
            return result

    _CACHE[cache_key] = None
    return None


# ══════════════════════════════════════════════════════════════════════
# Adapter entry point
# ══════════════════════════════════════════════════════════════════════

def linkedin_data_adapter(subject: dict, field_spec: dict) -> str | None:
    """Orchestrator-compatible adapter.

    Tries Proxycurl first (when keyed), falls back to Brave structural
    search if nothing else worked.  Returns ``None`` when no provider
    is available or no match found.
    """
    field_key = field_spec.get("key") or ""
    if field_key not in _SUPPORTED_FIELDS:
        return None

    domain = _domain_of(subject)
    company_name = (subject.get("name") or "").strip()
    if not domain and not company_name:
        return None

    person: dict | None = None
    provider_used = ""

    # Provider 1: Proxycurl (domain-based, accurate).
    proxy_key = os.environ.get("PROXYCURL_API_KEY", "").strip()
    if proxy_key and domain:
        person = _proxycurl_search(domain, proxy_key)
        if person:
            provider_used = "proxycurl"

    # Provider 2: Brave structural search (name-based, free).
    if not person and company_name:
        person = _brave_fallback(company_name)
        if person:
            provider_used = "brave"

    if not person:
        return None

    if field_key == "head_of_sales":
        name = (person.get("name") or "").strip()
        if not name:
            return None
        title = (person.get("title") or "").strip()
        suffix = f" ({title})" if title else ""
        source_tag = f" [{provider_used}]" if provider_used else ""
        return f"{name}{suffix}{source_tag}"

    if field_key in ("head_of_sales_linkedin", "linkedin_head_of_sales"):
        url = person.get("linkedin_url") or ""
        return url or None

    return None


def is_configured() -> bool:
    """True when at least one provider can run.  Brave is treated as
    configured because it uses the same BRAVE_API_KEY the rest of the
    system already needs for ``web_search``; callers can rely on
    ``is_configured()`` as "we can attempt this column" without caring
    which provider wins."""
    if os.environ.get("PROXYCURL_API_KEY", "").strip():
        return True
    # Brave is always-on if the system's main web_search is configured.
    try:
        from app.config import get_brave_api_key
        return bool(get_brave_api_key())
    except Exception:
        return False


def install() -> None:
    """Register under both the descriptive and the familiar name."""
    from app.tools.research_orchestrator import register_adapter
    register_adapter("linkedin_data", linkedin_data_adapter)
    register_adapter("sales_navigator", linkedin_data_adapter)

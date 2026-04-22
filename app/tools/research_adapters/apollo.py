"""
apollo — Adapter for the Apollo.io contact database.

Fills the following field keys for the ``research_orchestrator``:

* ``head_of_sales`` — "FirstName LastName (Title)" string
* ``head_of_sales_linkedin`` / ``linkedin_head_of_sales`` — profile URL
* ``head_of_sales_email`` — work email, only when the tier unlocks it
  (Apollo returns ``email_not_unlocked@domain.com`` for locked rows)

Endpoint: ``POST https://api.apollo.io/v1/mixed_people/search``

Authentication: ``X-Api-Key`` header, key from ``APOLLO_API_KEY`` env var.

Cost model: Apollo is credit-based.  ``people/search`` is cheap
(~1 credit per call).  Email unlocks are expensive (~1 credit each).
We cache results in-process by ``(domain, "head_of_sales")`` so repeat
queries for the same subject within a process don't re-bill.

Failure modes:

* No API key → returns ``None`` (adapter silently unavailable).
* HTTP error / network timeout → returns ``None``, logs debug.
* Empty ``people`` result → caches miss briefly, returns ``None``.
* Locked email → returns ``None`` for ``head_of_sales_email``; other
  fields still resolve from the same response.
"""
from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Per-process cache: (domain, role_class) -> Apollo person dict or None.
# TTL is "until process restart" — fine for a single research task
# which lasts minutes.
_CACHE: dict[tuple[str, str], dict | None] = {}

# Titles we match on.  Apollo does OR-matching across the list.
# Ordered from most- to least-canonical so the first hit is usually
# the most senior sales role.
_APOLLO_TITLES = [
    "Head of Sales",
    "VP of Sales",
    "VP Sales",
    "Vice President of Sales",
    "Sales Director",
    "Director of Sales",
    "Chief Revenue Officer",
    "Chief Commercial Officer",
]

_ENDPOINT = "https://api.apollo.io/v1/mixed_people/search"
_TIMEOUT_SECS = 10


# ── Field keys we can fill ────────────────────────────────────────
_SUPPORTED_FIELDS = frozenset({
    "head_of_sales",
    "head_of_sales_linkedin",
    "linkedin_head_of_sales",  # alias
    "head_of_sales_email",
})


def _get_domain(subject: dict) -> str:
    """Extract a domain suitable for Apollo's
    ``q_organization_domains`` filter.  Accepts either a full URL
    (``https://example.com/foo``) or a bare host (``example.com``)."""
    homepage = subject.get("homepage") or subject.get("url") or ""
    if not homepage:
        return ""
    try:
        url = homepage if "://" in homepage else f"https://{homepage}"
        host = urlparse(url).netloc or urlparse(f"//{homepage}").netloc
        return host.replace("www.", "").strip().lower()
    except Exception:
        return ""


def _search_apollo(domain: str, api_key: str) -> dict | None:
    """Run one Apollo people-search and cache the top hit.

    Returns the ``people[0]`` dict (raw Apollo response) or ``None``.
    Never raises — all failure paths log + return ``None`` so the
    orchestrator can fall through to the next adapter.
    """
    cache_key = (domain, "head_of_sales")
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    try:
        import requests  # lazy import — keep module startup cost low
    except ImportError:
        logger.debug("apollo: requests not installed; adapter disabled")
        return None

    try:
        r = requests.post(
            _ENDPOINT,
            headers={
                "X-Api-Key": api_key,
                "Content-Type": "application/json",
                "Cache-Control": "no-cache",
            },
            json={
                "q_organization_domains": domain,
                "person_titles": _APOLLO_TITLES,
                "page": 1,
                "per_page": 1,
            },
            timeout=_TIMEOUT_SECS,
        )
    except Exception as exc:
        logger.debug("apollo: request raised %s: %s", type(exc).__name__, exc)
        _CACHE[cache_key] = None
        return None

    if r.status_code != 200:
        logger.debug(
            "apollo: search returned status=%s body=%s",
            r.status_code, (r.text or "")[:200],
        )
        # Don't cache hard auth errors (401/403) — the user may fix
        # the key mid-session; cache other misses so we don't thrash.
        if r.status_code not in (401, 403):
            _CACHE[cache_key] = None
        return None

    try:
        data = r.json() or {}
    except Exception:
        _CACHE[cache_key] = None
        return None

    people = data.get("people") or []
    if not people:
        _CACHE[cache_key] = None
        return None
    _CACHE[cache_key] = people[0]
    return people[0]


def apollo_adapter(subject: dict, field_spec: dict) -> str | None:
    """Adapter entry point.  Signature matches the orchestrator's
    ``Adapter`` protocol.

    Returns a non-empty string on a hit, ``None`` otherwise.  Never
    raises — the orchestrator wraps us in a try/except for circuit
    breaking, but cooperating here keeps behaviour predictable.
    """
    api_key = os.environ.get("APOLLO_API_KEY", "").strip()
    if not api_key:
        return None  # gracefully unavailable

    field_key = field_spec.get("key") or ""
    if field_key not in _SUPPORTED_FIELDS:
        return None  # adapter can't fill this column

    domain = _get_domain(subject)
    if not domain:
        return None  # without a domain Apollo can't look up the org

    person = _search_apollo(domain, api_key)
    if not person:
        return None

    if field_key == "head_of_sales":
        name = (
            f"{person.get('first_name', '') or ''} "
            f"{person.get('last_name', '') or ''}"
        ).strip()
        if not name:
            return None
        title = (person.get("title") or "").strip()
        return f"{name} ({title})" if title else name

    if field_key in ("head_of_sales_linkedin", "linkedin_head_of_sales"):
        url = person.get("linkedin_url") or ""
        return url or None

    if field_key == "head_of_sales_email":
        email = (person.get("email") or "").strip()
        # Apollo returns "email_not_unlocked@domain.com" for locked
        # rows.  Treat as miss — caller pays for unlocks separately.
        if not email or "not_unlocked" in email or email.startswith("email_"):
            return None
        return email

    return None  # unreachable given _SUPPORTED_FIELDS


def is_configured() -> bool:
    """True when the adapter has the credentials it needs.  Callers
    use this to decide whether to mark ``head_of_sales*`` fields as
    ``known_hard`` in the spec (when no adapter is configured, the
    fields genuinely cannot be filled)."""
    return bool(os.environ.get("APOLLO_API_KEY", "").strip())


def install() -> None:
    """Register the adapter under the name ``apollo``."""
    from app.tools.research_orchestrator import register_adapter
    register_adapter("apollo", apollo_adapter)

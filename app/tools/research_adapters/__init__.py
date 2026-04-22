"""
research_adapters — Paid/third-party data adapters for the research
orchestrator.

Each submodule exposes an ``Adapter`` function matching the
``research_orchestrator.Adapter`` protocol::

    Adapter = Callable[[subject: dict, field_spec: dict], str | None]

and an ``install()`` function that registers the adapter in the
orchestrator's ``_ADAPTERS`` registry.  Adapters are always registered
(even when their API key is absent) and gracefully return ``None`` so
the orchestrator falls through to the next source in
``source_priority``.

Why not require the key to register?
------------------------------------
The orchestrator's cell output includes which adapter produced the
value (``{"value": ..., "source": "apollo"}``).  Keeping the adapter
registered even when the key is absent means tests and dev
environments hit a clean "no data" result rather than a
"source not found" error path that would mask real bugs.

Currently shipped adapters:

* **apollo** — Apollo.io REST API.  Requires ``APOLLO_API_KEY``.
  Supplies ``head_of_sales`` (name + title), ``head_of_sales_linkedin``,
  ``head_of_sales_email`` (if the tier unlocks personal emails).

* **sales_navigator** / **linkedin_data** — LinkedIn-data adapter.
  Primary provider: **Proxycurl** when ``PROXYCURL_API_KEY`` is set
  (accurate, ~$0.01-$0.03 per lookup).  Fallback: Brave structural
  search (``site:linkedin.com/in "<company>" "head of sales"``) — free
  but lower accuracy.  LinkedIn itself doesn't offer a public
  Sales-Navigator-grade API to non-partners, so Proxycurl is the
  pragmatic canonical provider for this adapter.  Supplies
  ``head_of_sales`` (name) and ``head_of_sales_linkedin`` (profile URL).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def install() -> None:
    """Register all paid-data adapters with the research orchestrator.

    Idempotent — each submodule's ``install()`` just calls
    ``register_adapter(name, fn)`` which overwrites in place.  Called
    lazily from
    :func:`app.tools.research_orchestrator.install_paid_adapters` so
    there's no circular import at orchestrator module load time.

    Intentionally **no** module-level "already-installed" guard: tests
    that manipulate the registry by popping entries must be able to
    re-install without tripping a sticky flag.
    """
    # Lazy inner imports — at this point the orchestrator module is
    # fully loaded so ``register_adapter`` is available.
    from app.tools.research_adapters.apollo import install as _install_apollo
    from app.tools.research_adapters.linkedin_data import (
        install as _install_linkedin,
    )
    _install_apollo()
    _install_linkedin()

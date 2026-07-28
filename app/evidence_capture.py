"""Record which sources the research tools actually returned, per request.

The gap this fills is written down in ``app/crews/output_integrity.py``: that
check establishes a reply is an *answer* rather than internal machinery, but
"groundedness for the non-deep path remains open" because ``ResearchCrew`` is a
plain ``crew.kickoff()`` returning a string — it captures no evidence set, so
nothing can verify that the URLs it cites were ever returned by a tool. The
deep-research fork has that verification (``deep_path._deep_evidence_gate_for``);
the fast fork has nothing, which is why its observed failure mode was a
bibliography padded with plausible org homepages no tool ever returned.

This module is the capture half: a per-request recorder, attached around the
crew dispatch, that the search/fetch tools report into. The checking half is
``app/crews/grounding.py``. Same pattern as ``app/content_clamp.py`` — record
first, decide with numbers later; nothing here changes any behaviour.

What counts as "a tool returned this URL"
-----------------------------------------
Both the structured result fields (a search hit's ``url``) and URLs appearing
*inside* returned content (a link in a fetched page). The research prompt's own
rule is "Every URL you cite must be one a tool actually returned" — a URL inside
fetched text was returned by a tool, so citing it is legitimate.

Threading
---------
The recorder rides a ``ContextVar`` (visible to the thread that runs the crew —
the orchestrator dispatches crews synchronously on its own thread). Sub-agents
spawned via ``run_parallel`` execute on pool threads where the ContextVar is
unset; ``ResearchCrew._run_parallel`` re-attaches the parent's recorder there
via :func:`propagated`. The recorder itself is lock-protected and shared.
"""

from __future__ import annotations

import contextvars
import logging
import re
import threading
from contextlib import nullcontext

logger = logging.getLogger(__name__)

# Mirrors ``deep_path._CITED_URL_RE`` so capture and citation extraction agree
# on what a URL token is.
_URL_RE = re.compile(r"https?://[^\s<>()\]]+", re.IGNORECASE)

#: Hard bound on recorded URLs per request — a crawl of a link farm must not
#: turn the recorder into a memory leak. Generous: a normal research run
#: records a few dozen.
_MAX_URLS = 500

#: Only the head of returned content is scanned for URLs. Tool outputs are
#: already clamped upstream (web_fetch 12k, firecrawl 8k); this is a backstop.
_MAX_SCAN_CHARS = 20_000


class EvidenceRecorder:
    """Thread-safe, bounded set of URLs the tools returned in one request."""

    def __init__(self, max_urls: int = _MAX_URLS):
        self._lock = threading.Lock()
        self._max = max_urls
        self._urls: dict[str, str] = {}  # url -> first origin that produced it
        self.truncated = False

    def add(self, url: str, *, origin: str) -> None:
        value = (url or "").strip().rstrip(".,;:")
        if not value.lower().startswith(("http://", "https://")):
            return
        with self._lock:
            if value in self._urls:
                return
            if len(self._urls) >= self._max:
                self.truncated = True
                return
            self._urls[value] = origin

    def extend_from_text(self, text: str, *, origin: str) -> None:
        for match in _URL_RE.finditer((text or "")[:_MAX_SCAN_CHARS]):
            self.add(match.group(0), origin=origin)

    def urls(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._urls)

    def origins(self) -> dict[str, int]:
        """How many URLs each origin contributed — for the observe-mode logs."""
        counts: dict[str, int] = {}
        with self._lock:
            for origin in self._urls.values():
                counts[origin] = counts.get(origin, 0) + 1
        return counts

    def __len__(self) -> int:
        with self._lock:
            return len(self._urls)


_active: contextvars.ContextVar[EvidenceRecorder | None] = contextvars.ContextVar(
    "evidence_recorder", default=None,
)


def active_recorder() -> EvidenceRecorder | None:
    return _active.get()


class capture_evidence:
    """Attach a recorder for the duration of a ``with`` block.

    Same shape as ``web_search.search_budget``: ContextVar set on enter, token
    reset on exit, reset failures swallowed (a token from another thread's
    context raises ``ValueError`` — re-attachment in pool threads makes that
    reachable, and it must never break the crew).
    """

    def __init__(self, recorder: EvidenceRecorder | None = None):
        # `is not None`, NOT truthiness: an EMPTY recorder is falsy (__len__),
        # and re-attaching a worker thread to a fresh recorder instead of the
        # parent's empty one would silently discard everything the sub-agent
        # records. Caught by test_propagated_reattaches_parent_recorder…
        self.recorder = recorder if recorder is not None else EvidenceRecorder()
        self._token = None

    def __enter__(self) -> EvidenceRecorder:
        self._token = _active.set(self.recorder)
        return self.recorder

    def __exit__(self, *exc) -> bool:
        if self._token is not None:
            try:
                _active.reset(self._token)
            except (ValueError, LookupError):
                pass
        return False


def propagated(recorder: EvidenceRecorder | None):
    """Context manager re-attaching a parent thread's recorder in a worker
    thread; a no-op when the parent had none."""
    return capture_evidence(recorder) if recorder is not None else nullcontext()


def record_search_results(query: str, results, backend: str) -> None:
    """Report structured search hits. No-op without an active recorder;
    never raises — recording must not be able to break a search."""
    recorder = _active.get()
    if recorder is None:
        return
    try:
        origin = f"search:{backend}"
        for row in results or []:
            recorder.add(str(row.get("url") or ""), origin=origin)
            recorder.extend_from_text(str(row.get("description") or ""), origin=origin)
    except Exception:  # pragma: no cover — defensive
        logger.debug("record_search_results failed", exc_info=True)


def record_tool_text(origin: str, text: str, *, urls: tuple = ()) -> None:
    """Report a fetch/scrape result: the fetched URL(s) plus any URLs inside
    the returned content. No-op without an active recorder; never raises."""
    recorder = _active.get()
    if recorder is None:
        return
    try:
        for url in urls:
            recorder.add(str(url or ""), origin=origin)
        recorder.extend_from_text(str(text or ""), origin=origin)
    except Exception:  # pragma: no cover — defensive
        logger.debug("record_tool_text failed", exc_info=True)


__all__ = [
    "EvidenceRecorder",
    "active_recorder",
    "capture_evidence",
    "propagated",
    "record_search_results",
    "record_tool_text",
]

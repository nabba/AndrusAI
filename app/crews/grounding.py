"""grounding.py — were the fast fork's citations actually returned by a tool?

The other half of ``app/evidence_capture.py``. ``output_integrity`` establishes
that a reply is an answer; this establishes that the machine-checkable URLs a
``research``-crew answer cites were returned by a tool in the same request. It
is the fast-fork counterpart of the deep gate's untraced-citation check
(``deep_path._deep_evidence_gate_for``), which found the production failure
mode this targets: bibliographies padded with real-institution homepages
(``https://elfond.ee``, ``https://piimaliit.ee``, …) that no tool ever returned.

Deliberately narrower than the deep gate:

* **URLs only.** The recorder sees URLs; it cannot see a DOI inside a fetched
  PDF, so checking DOI/arXiv citations here would flag legitimately-sourced
  identifiers. Those stay the deep path's job, checked against its structured
  evidence set.
* **Matching mirrors the deep gate**: a cited URL is traced when it equals or
  is a substring of a returned identifier (a cited homepage is covered by a
  retrieved deep link from the same site). Trailing ``/`` is normalised away on
  both sides — a slash difference is not fabrication.
* **URLs present in the task input are allowed.** KB-injected context and
  user-supplied links arrive inside the task string; citing what you were
  handed is not fabrication.

Modes — ``FAST_PATH_GROUNDING`` env var
----------------------------------------
* ``observe`` (default): log + count untraced citations, deliver the reply
  unchanged. This is the measurement that decides enforcement — the checker has
  never seen real fast-fork reply shapes, and the last filter shipped against
  fixtures instead of production shapes was reverted within a day
  (GATE_DIAGNOSIS Addendum 5).
* ``enforce``: additionally convert a violation into the typed no-answer signal
  (``outcome.record_no_answer``), so the orchestrator reports the real cause
  instead of delivering fabricated citations. Enforcement requires a non-empty
  captured evidence set: when no hooked tool returned anything, an untraced
  citation may simply mean the source came from an un-hooked tool (memory, KB
  search, composio), and that ambiguity must not destroy a real answer.
* ``off``: skip entirely.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_MODE_ENV = "FAST_PATH_GROUNDING"
_VALID_MODES = {"off", "observe", "enforce"}

# The forks this check applies to. deep_research has its own, stricter gate;
# other crews' citation semantics are unmeasured. Widening this set is the
# intended follow-up once observe-mode numbers exist per crew.
_CHECKED_CREWS = frozenset({"research"})

# Mirrors ``deep_path._CITED_URL_RE`` — the two checks must agree on what a
# cited URL token is.
_CITED_URL_RE = re.compile(r"https?://[^\s<>()\]]+", re.IGNORECASE)

_lock = threading.Lock()
_counters: dict[str, int] = {}


def _count(key: str, by: int = 1) -> None:
    with _lock:
        _counters[key] = _counters.get(key, 0) + by


def stats() -> dict[str, int]:
    """Counters for telemetry and tests: checked / clean / untraced_replies /
    untraced_urls / enforced / observe_only."""
    with _lock:
        return dict(_counters)


def reset_stats() -> None:
    with _lock:
        _counters.clear()


def mode() -> str:
    value = (os.environ.get(_MODE_ENV) or "observe").strip().lower()
    return value if value in _VALID_MODES else "observe"


def cited_urls(text: str) -> set[str]:
    """URL tokens asserted by a reply, with the deep gate's trailing-punctuation
    trim applied."""
    return {
        match.group(0).rstrip(".,;:")
        for match in _CITED_URL_RE.finditer(text or "")
    }


def _norm(url: str) -> str:
    return (url or "").rstrip("/")


def untraced_citations(reply: str, allowed: set[str]) -> list[str]:
    """Cited URLs not covered by any allowed identifier.

    Coverage semantics match the deep gate (``token == identifier or token in
    identifier``), plus trailing-slash normalisation on both sides.
    """
    allowed_norm = {_norm(a) for a in allowed if a}
    out = []
    for token in sorted(cited_urls(reply)):
        t = _norm(token)
        if not any(t == a or t in a for a in allowed_norm):
            out.append(token)
    return out


@dataclass(frozen=True)
class GroundingReport:
    crew: str
    mode: str
    untraced: tuple[str, ...] = ()
    cited_count: int = 0
    evidence_count: int = 0
    input_url_count: int = 0
    enforced: bool = False
    skipped: str | None = None  # why no verdict was reached, when it wasn't
    origins: dict = field(default_factory=dict)


def enforce_fast_path_grounding(
    *, crew_name: str, reply: str, recorder, task_text: str = "",
) -> GroundingReport | None:
    """Post-crew check. Returns a report, or ``None`` when out of scope.

    Never raises: called from the serving path, where a checker crash must not
    take the answer down with it. In ``enforce`` mode a violation is recorded
    via ``outcome.record_no_answer`` — the orchestrator's existing short
    circuit then reports the cause and skips vetting/critic.
    """
    try:
        current_mode = mode()
        if current_mode == "off" or crew_name not in _CHECKED_CREWS:
            return None

        evidence: set[str] = set(recorder.urls()) if recorder is not None else set()
        input_urls = cited_urls(task_text)
        allowed = evidence | input_urls

        cited = cited_urls(reply or "")
        untraced = untraced_citations(reply or "", allowed)

        _count("checked")
        report_kwargs = dict(
            crew=crew_name,
            mode=current_mode,
            untraced=tuple(untraced),
            cited_count=len(cited),
            evidence_count=len(evidence),
            input_url_count=len(input_urls),
            origins=recorder.origins() if recorder is not None else {},
        )

        if not untraced:
            _count("clean")
            return GroundingReport(**report_kwargs)

        _count("untraced_replies")
        _count("untraced_urls", len(untraced))
        logger.warning(
            "fast-path grounding: %s cited %d url(s) no tool returned "
            "(mode=%s, evidence=%d from %s, input_urls=%d): %s",
            crew_name, len(untraced), current_mode, len(evidence),
            report_kwargs["origins"] or "{}", len(input_urls),
            ", ".join(untraced[:5]),
        )

        if current_mode != "enforce":
            _count("observe_only")
            return GroundingReport(**report_kwargs)

        if not evidence:
            # Nothing captured — cannot distinguish fabrication from a source
            # that came via an un-hooked tool. Observe, don't destroy.
            _count("enforce_skipped_no_evidence")
            return GroundingReport(
                **{**report_kwargs, "skipped": "no captured evidence"},
            )

        from app.crews.outcome import record_no_answer
        shown = ", ".join(untraced[:3])
        record_no_answer(
            crew_name,
            f"the reply cited {len(untraced)} source(s) that no tool in this "
            f"run returned (e.g. {shown}) — refusing to deliver fabricated "
            f"citations",
        )
        _count("enforced")
        return GroundingReport(**{**report_kwargs, "enforced": True})
    except Exception:  # pragma: no cover — defensive
        logger.debug("fast-path grounding check failed", exc_info=True)
        return None


__all__ = [
    "GroundingReport",
    "cited_urls",
    "enforce_fast_path_grounding",
    "mode",
    "reset_stats",
    "stats",
    "untraced_citations",
]

"""PEP/idiom radar — Phase 4 of the elegance plan.

Surfaces newly-published Python PEPs (the language-feature kind, not
informational) as candidate idiom updates: things the codebase could
adopt to be more elegant. Examples: PEP 634 (structural pattern
matching), PEP 695 (type-parameter syntax), PEP 701 (formalised
f-strings), PEP 749 (sub-interpreters).

This is the calendar-driven cousin of :mod:`app.library_radar.proposer`
(which watches third-party libraries on PyPI). PEPs land at a much
lower cadence than libraries — a handful per year reach "Final" —
so the radar runs weekly and stages at most one proposal per PEP,
ever, via the proposal_bridge idempotency layer.

Detection
---------

Reuses the existing PEP feed in :mod:`app.episteme.feed_sources`
(``fetch_python_peps``). For each entry:

  1. Extract the PEP number from the URL/id.
  2. Keep only entries whose title or abstract mention one of the
     idiom-signal keywords below (``match``, ``dataclass``, ``async``,
     ``type``, ``walrus``, ``f-string``, ``structural pattern``,
     ``typing``, ``protocol``, ``slots``).
  3. Stage a markdown proposal at
     ``docs/proposed_pep_idioms/pep_<number>.md`` via the standard
     ``proposal_bridge.stage`` flow.

Each proposal contains:

  * The PEP title + abstract (verbatim, no LLM rewriting — keeps
    the radar deterministic + cheap).
  * Suggested migration patterns the operator can consider.
  * A coding_session_spec scaffold so an agent can pick up the
    proposal and explore concrete migrations.

Discipline
----------

* **Default OFF.** Conservative first ship. The PEP feed is noisy —
  not every "Final" PEP is worth migrating. Operator flips ON after
  seeing one weekly pass.
* **Per-pass cap of 3 proposals.** Backlog spreads over weeks via the
  bridge's 14-day cooldown.
* **Reuses ``source="library_radar"`` for the proposal_bridge.** No
  new source label — PEP idioms ARE library-class adoption proposals.
* **Failure-isolated.** Broken feed / unreachable PEPs.python.org never
  raises; the daemon retries next week.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ── Configuration ────────────────────────────────────────────────────────


# Keywords that signal "this PEP is about a new idiom you can adopt."
# Phrased so a single substring match on a lowercased title-or-abstract
# catches the relevant PEPs without too many false positives.
_IDIOM_KEYWORDS: tuple[str, ...] = (
    "match",
    "dataclass",
    "async",
    "walrus",
    "f-string",
    "structural pattern",
    "typing",
    "protocol",
    "slots",
    "type parameter",
    "type hint",
    "annotation",
    "exception group",
    "sub-interpreter",
    "subinterpreter",
)
_MAX_PER_PASS = 3
_COOLDOWN_DAYS = 14
_POLL_INTERVAL_S = 7 * 24 * 3600
_WARMUP_S = 900  # 15 min — let library_radar warm up first
_LOOKBACK_DAYS = 180  # PEPs are rare; we want a long enough window
_MAX_FEED_ITEMS = 25

_DAEMON_THREAD_NAME = "pep-idiom-radar"


_PEP_NUMBER_RE = re.compile(r"pep[-_\s]*0*(\d+)", re.IGNORECASE)


# ── Data ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class IdiomCandidate:
    """One PEP candidate the radar wants to stage as a proposal."""
    pep_number: int
    title: str
    abstract: str
    published: str
    keywords_matched: tuple[str, ...]


# ── Enable / state ──────────────────────────────────────────────────────


def _enabled() -> bool:
    try:
        from app.runtime_settings import get_pep_idiom_radar_enabled
        return get_pep_idiom_radar_enabled()
    except Exception:
        return os.getenv("PEP_IDIOM_RADAR_ENABLED", "false").lower() in (
            "true", "1", "yes", "on",
        )


# ── Detection ───────────────────────────────────────────────────────────


def _pep_number(entry: dict[str, Any]) -> int | None:
    for field in ("id", "title"):
        text = entry.get(field) or ""
        m = _PEP_NUMBER_RE.search(text)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
    return None


def _idiom_keywords_in(text: str) -> tuple[str, ...]:
    text_lower = text.lower()
    return tuple(kw for kw in _IDIOM_KEYWORDS if kw in text_lower)


def detect_idiom_peps(*, lookback_days: int = _LOOKBACK_DAYS) -> list[IdiomCandidate]:
    """Pull the PEP feed, return candidates whose title or abstract
    mentions at least one idiom keyword.

    Pure read-only. Safe to call from tests with a stubbed fetcher.
    """
    try:
        from app.episteme.feed_sources import fetch_python_peps
    except Exception:
        logger.debug("idiom_radar: feed_sources import failed", exc_info=True)
        return []

    try:
        feed = fetch_python_peps(
            lookback_days=lookback_days, max_items=_MAX_FEED_ITEMS,
        )
    except Exception:
        logger.debug("idiom_radar: PEP fetch raised", exc_info=True)
        return []

    out: list[IdiomCandidate] = []
    for entry in feed:
        title = entry.get("title") or ""
        abstract = entry.get("abstract") or ""
        haystack = f"{title} {abstract}"
        matched = _idiom_keywords_in(haystack)
        if not matched:
            continue
        pep_n = _pep_number(entry)
        if pep_n is None:
            continue
        out.append(IdiomCandidate(
            pep_number=pep_n,
            title=title.strip(),
            abstract=abstract.strip()[:2000],
            published=entry.get("published") or "",
            keywords_matched=matched,
        ))
    # Stable ordering — newest published first, lowest PEP number tiebreaker.
    out.sort(key=lambda c: (c.published, -c.pep_number), reverse=True)
    return out[:_MAX_PER_PASS]


# ── Proposal body + scaffold ────────────────────────────────────────────


def _migration_hints(keywords: tuple[str, ...]) -> list[str]:
    """Per-keyword migration suggestions. Generic but actionable —
    the operator can refine for the specific PEP."""
    hints: list[str] = []
    if "match" in keywords or "structural pattern" in keywords:
        hints.append(
            "Look for `if isinstance(...): elif isinstance(...):` chains "
            "across `app/agents/` and `app/crews/` — strong candidates "
            "for `match` rewriting.",
        )
    if "dataclass" in keywords or "slots" in keywords:
        hints.append(
            "Look for hand-rolled `__init__` + `__eq__` + `__repr__` "
            "classes; `dataclass(frozen=True, slots=True)` is usually "
            "cleaner.",
        )
    if "type" in " ".join(keywords) or "annotation" in keywords or "typing" in keywords:
        hints.append(
            "Run `code_quality.measure_file_at_path` over the modules "
            "with lowest type_coverage; this PEP may simplify the "
            "annotations needed.",
        )
    if "async" in keywords:
        hints.append(
            "Survey blocking I/O call sites; this PEP may unlock new "
            "async patterns in already-async codepaths.",
        )
    if "exception group" in keywords:
        hints.append(
            "Look for `except (X, Y, Z):` blocks that lose per-cause "
            "context — `except*` is a stronger pattern.",
        )
    if not hints:
        hints.append(
            "No keyword-specific hint; read the PEP itself for the "
            "intended use cases, then grep the codebase for the "
            "patterns it replaces.",
        )
    return hints


def _build_body(c: IdiomCandidate) -> str:
    hints = _migration_hints(c.keywords_matched)
    hint_lines = "\n".join(f"- {h}" for h in hints)
    kw_str = ", ".join(f"`{k}`" for k in c.keywords_matched)
    abstract = c.abstract or "_(no abstract in feed)_"
    return (
        f"# PEP {c.pep_number} adoption proposal\n\n"
        f"**Title:** {c.title}\n\n"
        f"**Published:** {c.published or 'unknown'}\n\n"
        f"**Idiom keywords matched:** {kw_str}\n\n"
        f"## Abstract\n\n"
        f"{abstract}\n\n"
        f"## Suggested migration starting points\n\n"
        f"{hint_lines}\n\n"
        f"## Approval rules\n\n"
        f"- Operator gate via Signal 👍 / `/cp/changes`.\n"
        f"- This proposal is a survey, not a code change — approval lands\n"
        f"  the markdown at `docs/proposed_pep_idioms/pep_{c.pep_number}.md`.\n"
        f"- Subsequent refactor CRs to migrate code to the new idiom go\n"
        f"  through `refactor_proposer` (Phase 2) or by hand.\n"
        f"- The PEP itself is upstream — link: "
        f"https://peps.python.org/pep-{c.pep_number:04d}/\n"
    )


def _build_spec(c: IdiomCandidate) -> dict[str, Any]:
    return {
        "intent": (
            f"Survey codebase for patterns that PEP {c.pep_number} "
            f"({c.title}) would simplify."
        ),
        "files": [],  # survey-only: no specific files
        "acceptance": [
            "Survey markdown landed at docs/proposed_pep_idioms/",
            f"At least one concrete code site identified where PEP "
            f"{c.pep_number} would improve elegance (or rationale why none).",
        ],
        "expected_duration_min": 30,
    }


# ── Pass orchestration ──────────────────────────────────────────────────


def _signature(pep_number: int) -> str:
    # Same PEP, same signature — bridge dedup is automatic across passes.
    return f"pep_{pep_number:04d}"


def run_one_pass() -> dict[str, Any]:
    """Find idiom-PEPs, stage at most ``_MAX_PER_PASS`` through the bridge."""
    summary: dict[str, Any] = {
        "checked": False, "staged": 0, "skipped": 0, "errors": 0,
        "n_candidates": 0,
    }
    if not _enabled():
        summary["disabled"] = True
        return summary
    try:
        from app.proposal_bridge import stage
    except Exception:
        logger.debug("idiom_radar: proposal_bridge unavailable", exc_info=True)
        summary["errors"] += 1
        return summary

    candidates = detect_idiom_peps()
    summary["n_candidates"] = len(candidates)
    for c in candidates:
        try:
            target = f"docs/proposed_pep_idioms/pep_{c.pep_number:04d}.md"
            _state, was_new = stage(
                source="library_radar",
                signature=_signature(c.pep_number),
                title=f"PEP {c.pep_number}: {c.title[:80]}",
                body_markdown=_build_body(c),
                target_path=target,
                cooldown_days=_COOLDOWN_DAYS,
                coding_session_spec=_build_spec(c),
            )
            if was_new:
                summary["staged"] += 1
            else:
                summary["skipped"] += 1
        except Exception:
            logger.debug(
                "idiom_radar: stage failed for PEP %s", c.pep_number, exc_info=True,
            )
            summary["errors"] += 1
    summary["checked"] = True
    return summary


# ── Daemon ──────────────────────────────────────────────────────────────


_driver_started = False
_driver_lock = threading.Lock()
_stop_event = threading.Event()


def _is_running() -> bool:
    return any(
        t.name == _DAEMON_THREAD_NAME and t.is_alive()
        for t in threading.enumerate()
    )


def _driver() -> None:
    if _stop_event.wait(_WARMUP_S):
        return
    while not _stop_event.is_set():
        try:
            result = run_one_pass()
            if result.get("checked"):
                logger.info(
                    "idiom_radar: candidates=%d staged=%d skipped=%d errors=%d",
                    result["n_candidates"], result["staged"],
                    result["skipped"], result["errors"],
                )
        except Exception:
            logger.debug("idiom_radar: pass raised", exc_info=True)
        if _stop_event.wait(_POLL_INTERVAL_S):
            return


def start() -> None:
    global _driver_started
    if not _enabled():
        logger.info("idiom_radar: disabled via pep_idiom_radar_enabled")
        return
    with _driver_lock:
        if _is_running():
            return
        if _driver_started:
            logger.warning("idiom_radar: previous thread is dead, re-spawning")
        _stop_event.clear()
        thread = threading.Thread(
            target=_driver, name=_DAEMON_THREAD_NAME, daemon=True,
        )
        thread.start()
        _driver_started = True
        logger.info(
            "idiom_radar: daemon started (warm-up=%ds, poll=%dh)",
            _WARMUP_S, _POLL_INTERVAL_S // 3600,
        )


def stop() -> None:
    _stop_event.set()

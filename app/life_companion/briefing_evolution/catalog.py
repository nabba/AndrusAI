"""catalog — registry of candidate briefing sections.

Each candidate lives as a module under ``app.life_companion.briefing_sections``
exposing four attributes:

  * ``ID``           — stable kebab-case identifier (also used as filename stem)
  * ``DISPLAY_NAME`` — human label shown in the briefing
  * ``DESCRIPTION``  — one-line purpose; used by the LLM proposer
  * ``gather()``     — callable returning a ``list[str]`` of bullet lines
                       (empty list = section auto-hides this briefing)

Candidates are discovered by directory scan — adding a new file is the
only step. The catalog mirrors discovered modules into ``trial_state``
on first load so each new candidate enters as PROPOSED.

Soft-fails throughout: a candidate that raises at import time is logged
and skipped — never blocks the briefing.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from dataclasses import dataclass
from typing import Callable

from app.life_companion.briefing_evolution import trial_state

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Candidate:
    """One candidate section + its gather callable."""
    id: str
    module: str
    display_name: str
    description: str
    gather: Callable[[], list[str]]


_DISCOVERY_PACKAGE = "app.life_companion.briefing_sections"
_cache: dict[str, Candidate] = {}
_discovered = False


def _discover_once() -> None:
    """Lazy one-shot scan. Avoids the cost on every briefing render —
    the briefing fires at most 3× a day, but the same gateway answers
    many Signal commands per minute."""
    global _discovered
    if _discovered:
        return
    try:
        pkg = importlib.import_module(_DISCOVERY_PACKAGE)
    except Exception:
        logger.warning("briefing_sections package import failed", exc_info=True)
        _discovered = True
        return

    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        modname = f"{_DISCOVERY_PACKAGE}.{info.name}"
        try:
            mod = importlib.import_module(modname)
        except Exception:
            logger.warning("briefing_section %s import failed", modname, exc_info=True)
            continue
        try:
            cand = Candidate(
                id=getattr(mod, "ID"),
                module=modname,
                display_name=getattr(mod, "DISPLAY_NAME"),
                description=getattr(mod, "DESCRIPTION", ""),
                gather=getattr(mod, "gather"),
            )
        except AttributeError as exc:
            logger.warning("briefing_section %s missing required attr: %s", modname, exc)
            continue
        _cache[cand.id] = cand
        trial_state.upsert_section(cand.id, modname)
    _discovered = True


def all_candidates() -> list[Candidate]:
    _discover_once()
    return list(_cache.values())


def get(section_id: str) -> Candidate | None:
    _discover_once()
    return _cache.get(section_id)

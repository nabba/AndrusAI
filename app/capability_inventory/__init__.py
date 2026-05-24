"""Capability inventory — what this system can do, in one document.

Gap #5 (2026-05-24): the tool registry tracks tools. The healing-monitor
driver tracks monitors. The idle scheduler tracks idle jobs. The
SignalCommand registry tracks commands. None of these is a unified
"what can this system actually do" answer that future-Andrus (or a
successor operator) can read in 30 seconds.

This package walks all four registries on a weekly cadence + writes
``wiki/self/capability_inventory.md``. Auto-updated; operator can pin
sections via the ``<!-- pin --> ... <!-- /pin -->`` delimiters around
hand-written content (the auto-writer never touches text inside those
markers).
"""
from __future__ import annotations

from app.capability_inventory.builder import (
    Inventory,
    build_inventory,
    render_markdown,
    write_inventory,
    run_once,
)

__all__ = [
    "Inventory",
    "build_inventory",
    "render_markdown",
    "write_inventory",
    "run_once",
]

"""app.research — the auto-research composition layer.

This package holds capabilities that *compose* existing AndrusAI subsystems
into an autonomous-research workflow. It deliberately owns no new
infrastructure: literature search wraps ``app.episteme`` + the arXiv internals
of ``app.episteme.paper_pipeline``; hypothesis generation wraps the headless
brainstorm seed round (``app.brainstorm.headless``) and grounds it in retrieved
literature; the research run (later) is an ``autonomous_executor`` ExecutorRun
whose steps carry research crew-hints.

Why a separate package rather than living inside ``app/episteme``: the
episteme package's ``__init__`` eagerly imports ``chromadb`` and is marked
infrastructure-level (do-not-modify). Anything placed inside it inherits that
import and cannot be exercised on a host without the vector-store stack. The
research layer instead imports episteme internals *lazily, inside functions*,
so the heavy dependency is only pulled when a real KB query actually runs —
and unit tests inject fakes to bypass it entirely.

Nothing here imports at package-load time beyond the stdlib; submodules are
imported on demand by their callers.
"""

from __future__ import annotations

__all__ = [
    "literature",
    "hypothesis",
    "run",
    "experiment",
    "experiment_job",
    # Phase B — literature/citation verification (pure stdlib, host-safe).
    "citation",
    "literature_sources",
    "citation_verifier",
    # Phase C — manuscript composer (pure stdlib, host-safe).
    "manuscript",
]

"""Upgrade lifecycle subsystem — see ``docs/UPGRADE_LIFECYCLE.md``.

PROGRAM §62 (2026-05-23). Layers on top of ``app.dependency_radar``
(producer) and ``app.proposal_bridge`` (staging) to add four stages
the radar alone doesn't cover:

  * **B. Capability extraction** — fetch + LLM-parse a changelog
    into structured ``Capability`` rows. ``changelog_fetcher.py``.
  * **C. Impact analysis** — AST-walk for import + usage sites
    against the upgrading package; match against deprecations +
    breaking_changes. ``impact_analysis.py``.
  * **D. Trial harness** — spin a coding-session worktree with the
    bumped requirement and run the full test suite. ``trial_runner.py``.
  * **E. Capability adoption** — for accepted new_features,
    propose adoption refactors per call site (LLM via factory),
    rate-limited + budget-capped. ``capability_adoption.py``.

Plus the annual ecosystem snapshot at ``ecosystem_snapshot.py`` and
the operator-acceptance flow that drives MAJOR upgrades through the
same change-request / Tier-3 amendment surfaces operators already
trust.

Every LLM decision goes through ``app.llm_factory`` —
no model IDs are hardcoded in this package.
"""
from __future__ import annotations

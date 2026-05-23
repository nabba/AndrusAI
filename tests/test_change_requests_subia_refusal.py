"""Tests for the SubIA boundary refusal in the change-request validator.

Closes the gap audited 2026-05-23: ``app/subia/*`` was reachable through
the standard change-request gate because the validator only refused
TIER_IMMUTABLE via exact-match, and no ``app/subia/*.py`` file was on
that list. The audit recommended a prefix-match refusal mirroring
``architecture_requests/validator._FORBIDDEN_PACKAGE_PREFIXES``.

Pinning here so a future refactor that moves the prefix list or adjusts
the validator's order-of-checks doesn't silently re-open the leak.
"""
from __future__ import annotations

import pytest

from app.change_requests.validator import (
    _FORBIDDEN_INDIVIDUAL_FILES,
    _FORBIDDEN_PATH_PREFIXES,
    validate,
)


class TestSubIABoundary:
    """The standard CR validator must refuse all paths under app/subia/."""

    @pytest.mark.parametrize(
        "subia_path",
        [
            "app/subia/scene/global_workspace.py",
            "app/subia/safety/phenomenal_language_linter.py",
            "app/subia/probes/butlin.py",
            "app/subia/integrity.py",
            "app/subia/kernel.py",
            "app/subia/inquiry/__init__.py",
            "app/subia/probe_proposals/proposer.py",
            # Edge case: a brand-new file in a never-before-seen subia
            # subpackage. Prefix-match must catch it without anybody
            # having to add the file to TIER_IMMUTABLE first.
            "app/subia/new_subpackage_2027/module.py",
        ],
    )
    def test_subia_paths_refused(self, subia_path: str) -> None:
        result = validate(path=subia_path, new_content="x = 1\n")
        assert not result.ok, f"{subia_path!r} should be refused"
        assert result.is_tier_immutable, (
            f"{subia_path!r} refusal must set is_tier_immutable=True so "
            f"the lifecycle records TIER_IMMUTABLE_REFUSED, distinct from "
            f"a plain REJECTED"
        )
        assert "Tier-3 amendment" in (result.reason or ""), (
            "Refusal reason must point the operator at the Tier-3 path"
        )

    def test_goal_emitter_refused(self) -> None:
        """The Tier-3 anchor at app/affect/goal_emitter.py must be
        refused even though it doesn't live under any forbidden prefix
        — it's an individually-listed sentinel."""
        result = validate(
            path="app/affect/goal_emitter.py",
            new_content="x = 1\n",
        )
        assert not result.ok
        assert result.is_tier_immutable

    def test_non_subia_app_paths_still_allowed(self) -> None:
        """Sanity: an ordinary app/ path is still accepted."""
        result = validate(
            path="app/agents/researcher.py",
            new_content="x = 1\n",
        )
        # Either ok=True or rejected for a non-TIER reason; specifically
        # NOT refused with is_tier_immutable=True.
        if not result.ok:
            assert not result.is_tier_immutable

    def test_app_subia_lookalike_not_refused(self) -> None:
        """The prefix is ``app/subia/`` with trailing slash — a path
        like ``app/subia_lookalike.py`` should NOT be caught by the
        consciousness-layer rule. (Auto-deployer's TIER_IMMUTABLE or
        the standard allowed-roots gate may still reject it for other
        reasons, but not as a consciousness-layer refusal.)"""
        result = validate(
            path="app/subia_lookalike.py",
            new_content="x = 1\n",
        )
        if not result.ok:
            assert "consciousness layer" not in (result.reason or "")

    def test_prefix_list_contains_subia(self) -> None:
        """Pin the prefix so a future refactor that empties the tuple
        is caught at the source."""
        assert "app/subia/" in _FORBIDDEN_PATH_PREFIXES

    def test_individual_files_contains_goal_emitter(self) -> None:
        """Pin the individual-file list."""
        assert "app/affect/goal_emitter.py" in _FORBIDDEN_INDIVIDUAL_FILES

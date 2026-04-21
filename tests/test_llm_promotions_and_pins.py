"""
Promotion + Hand-Pin Tests
===========================

Covers the three-layer resolver authority:
    pool (weakest) < promotion < hand-pin (strongest)

Run:
    docker exec crewai-team-gateway-1 python3 -m pytest \
        /app/tests/test_llm_promotions_and_pins.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def clear_caches():
    """Wipe the role-assignment + promotion caches so tests don't see
    stale values from previous cases."""
    import app.llm_role_assignments as ra
    import app.llm_promotions as pr
    with ra._cache_lock:
        ra._cache.clear()
    pr.invalidate_cache()
    yield
    with ra._cache_lock:
        ra._cache.clear()
    pr.invalidate_cache()


@pytest.fixture
def fresh_catalog(monkeypatch):
    """Reset CATALOG to bootstrap between tests."""
    import app.llm_catalog as lc
    snapshot = {n: dict(e) for n, e in lc._BOOTSTRAP_CATALOG.items()}
    monkeypatch.setattr(lc, "CATALOG", snapshot)
    yield snapshot


@pytest.fixture
def no_db(monkeypatch):
    """Default the promotion + pin lookups to empty so tests don't hit Postgres."""
    monkeypatch.setattr("app.llm_promotions._refresh_cache", lambda: set())
    monkeypatch.setattr("app.llm_promotions.invalidate_cache", lambda: None)
    monkeypatch.setattr("app.llm_role_assignments._query_assigned_model", lambda r, m: None)
    monkeypatch.setattr("app.llm_role_assignments.invalidate_cache", lambda *a, **kw: None)


# ── Layer interactions ───────────────────────────────────────────────────

class TestThreeLayerAuthority:
    def test_pool_only_uses_scoring(self, fresh_catalog, no_db):
        """No pins, no promotions — resolver picks by score."""
        from app.llm_catalog import resolve_role_default
        picked = resolve_role_default("coding", "budget")
        # Under budget mode, coding has no tier floor — DeepSeek wins on cost.
        assert picked == "deepseek-v3.2"

    def test_promotion_filter_restricts_candidates(self, fresh_catalog, monkeypatch):
        """When a promoted model fits the role, resolver filters down to
        the promoted set before scoring."""
        # Pretend deepseek-v3.2 is demoted, only claude-sonnet-4.6 promoted.
        monkeypatch.setattr(
            "app.llm_promotions.list_promoted",
            lambda: {"claude-sonnet-4.6"},
        )
        monkeypatch.setattr(
            "app.llm_role_assignments._query_assigned_model",
            lambda r, m: None,
        )
        monkeypatch.setattr(
            "app.llm_role_assignments.invalidate_cache",
            lambda *a, **kw: None,
        )
        from app.llm_catalog import resolve_role_default
        # For coding under budget, Sonnet isn't normally preferred (cost penalty).
        # Promotion filter should force it to win.
        picked = resolve_role_default("coding", "budget")
        assert picked == "claude-sonnet-4.6"

    def test_promotion_ignored_when_no_promoted_model_fits(self, fresh_catalog, monkeypatch):
        """A promoted multimodal-only model shouldn't influence a text-only role."""
        # Promote deepseek-v3.2 (multimodal=False). Role 'media' needs multimodal.
        monkeypatch.setattr(
            "app.llm_promotions.list_promoted",
            lambda: {"deepseek-v3.2"},
        )
        monkeypatch.setattr(
            "app.llm_role_assignments._query_assigned_model",
            lambda r, m: None,
        )
        monkeypatch.setattr(
            "app.llm_role_assignments.invalidate_cache",
            lambda *a, **kw: None,
        )
        from app.llm_catalog import resolve_role_default
        # Media needs multimodal → deepseek filtered out despite being promoted.
        # Sonnet is the only multimodal bootstrap entry.
        picked = resolve_role_default("media", "balanced")
        assert picked == "claude-sonnet-4.6"

    def test_hand_pin_wins_over_promotion(self, fresh_catalog, monkeypatch):
        """Hand pin (layer 3) beats promotion (layer 2)."""
        # Everything points different directions:
        # - hand-pin commander → deepseek-v3.2 (even though not premium, pin is hard)
        # - promotions include claude-sonnet-4.6
        monkeypatch.setattr(
            "app.llm_role_assignments._query_assigned_model",
            lambda r, m: "deepseek-v3.2" if r == "commander" else None,
        )
        monkeypatch.setattr(
            "app.llm_role_assignments.invalidate_cache",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "app.llm_promotions.list_promoted",
            lambda: {"claude-sonnet-4.6"},
        )
        from app.llm_catalog import resolve_role_default
        assert resolve_role_default("commander", "balanced") == "deepseek-v3.2"

    def test_hand_pin_ignored_if_target_not_in_catalog(self, fresh_catalog, monkeypatch):
        """Stale pin pointing at a missing model → resolver takes over."""
        monkeypatch.setattr(
            "app.llm_role_assignments._query_assigned_model",
            lambda r, m: "no-such-model",
        )
        monkeypatch.setattr(
            "app.llm_role_assignments.invalidate_cache",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr("app.llm_promotions.list_promoted", lambda: set())
        from app.llm_catalog import resolve_role_default
        picked = resolve_role_default("commander", "balanced")
        # Bootstrap fallback: claude-sonnet-4.6 (only premium).
        assert picked == "claude-sonnet-4.6"


# ── Promotion module unit tests ──────────────────────────────────────────

class TestPromoteDemote:
    def test_promote_rejects_non_catalog_keys(self, fresh_catalog, no_db):
        from app.llm_promotions import promote
        assert promote("totally-made-up-model") is False

    def test_promote_accepts_bootstrap_keys(self, fresh_catalog, monkeypatch):
        from app.llm_promotions import promote
        called = []
        monkeypatch.setattr(
            "app.control_plane.db.execute",
            lambda *a, **kw: called.append((a, kw)) or [],
        )
        monkeypatch.setattr("app.llm_promotions.invalidate_cache", lambda: None)
        assert promote("deepseek-v3.2", promoted_by="test", reason="unit test") is True
        assert len(called) == 1
        # Second arg is the parameter tuple
        params = called[0][0][1]
        assert params[0] == "deepseek-v3.2"
        assert params[1] == "test"

    def test_demote_always_attempts(self, monkeypatch):
        from app.llm_promotions import demote
        called = []
        monkeypatch.setattr(
            "app.control_plane.db.execute",
            lambda *a, **kw: called.append(a) or [],
        )
        monkeypatch.setattr("app.llm_promotions.invalidate_cache", lambda: None)
        assert demote("anything") is True
        assert called  # DELETE ran


# ── Hand-pin module unit tests ───────────────────────────────────────────

class TestPinUnpin:
    def test_pin_uses_priority_1000(self, fresh_catalog, monkeypatch):
        """pin_role hits set_assignment with priority >= HAND_PIN_PRIORITY."""
        from app.llm_role_assignments import pin_role, HAND_PIN_PRIORITY

        captured = {}
        def _fake_set(role, cost_mode, model, **kw):
            captured.update({"role": role, "cost_mode": cost_mode, "model": model, **kw})
            return True
        monkeypatch.setattr("app.llm_role_assignments.set_assignment", _fake_set)

        ok = pin_role("commander", "balanced", "deepseek-v3.2",
                      assigned_by="user:test", reason="testing")
        assert ok is True
        assert captured["role"] == "commander"
        assert captured["priority"] == HAND_PIN_PRIORITY
        assert captured["source"] == "manual"

    def test_unpin_role_only_retires_hand_pins(self, monkeypatch):
        """unpin_role filters by priority >= HAND_PIN_PRIORITY."""
        from app.llm_role_assignments import unpin_role, HAND_PIN_PRIORITY

        captured_params = []
        def _fake_execute(sql, params=(), fetch=False):
            captured_params.append((sql, params))
            if fetch:
                return [{"role": "commander", "cost_mode": "balanced", "model": "sonnet"}]
            return []
        monkeypatch.setattr("app.control_plane.db.execute", _fake_execute)
        monkeypatch.setattr(
            "app.llm_role_assignments.invalidate_cache",
            lambda *a, **kw: None,
        )

        n = unpin_role("commander", "balanced")
        assert n == 1
        sql = captured_params[0][0]
        params = captured_params[0][1]
        assert "priority >= %s" in sql
        assert HAND_PIN_PRIORITY in params


# ── Canonical role list consistency ─────────────────────────────────────

class TestPublicRoleRegistry:
    """The PUBLIC_ROLES tuple is the single source of truth for
    pinnable roles. These tests lock in the invariants that protect it
    from drifting out of sync with the actual crew registry and
    _ROLE_TO_TASK map."""

    def test_public_roles_no_duplicates(self):
        from app.llm_catalog import PUBLIC_ROLES
        assert len(PUBLIC_ROLES) == len(set(PUBLIC_ROLES))

    def test_crew_registry_entries_are_in_public_roles(self):
        """Every registered crew must appear in CREW_ROLES (and thus
        PUBLIC_ROLES). Prevents a new crew being added to the registry
        but forgotten in the pin dialog's dropdown."""
        from app.llm_catalog import CREW_ROLES
        from app.crews import registry
        registry.install_defaults()
        registered = set(registry._registry.keys())
        missing = registered - set(CREW_ROLES)
        assert not missing, (
            f"crews registered but not in llm_catalog.CREW_ROLES: {missing}. "
            "Add them so the dashboard pin dialog exposes them."
        )

    def test_public_roles_are_valid_role_to_task_entries(self):
        """Every public role must have a canonical task_type so the
        resolver's scoring has something to key on."""
        from app.llm_catalog import PUBLIC_ROLES, _ROLE_TO_TASK
        missing = [r for r in PUBLIC_ROLES if r not in _ROLE_TO_TASK]
        assert not missing, (
            f"PUBLIC_ROLES has {missing} but _ROLE_TO_TASK doesn't. "
            "Add entries so canonical_task_type() resolves them."
        )

    def test_cost_modes_match_weight_keys(self):
        """COST_MODES tuple must match the soft-penalty weight table
        the resolver actually uses."""
        from app.llm_catalog import COST_MODES, _COST_MODE_WEIGHT
        assert set(COST_MODES) == set(_COST_MODE_WEIGHT.keys())

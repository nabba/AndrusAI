"""Tests for SkillRecord.matches_context and related persistence."""
from tests._v2_shim import install_settings_shim

install_settings_shim()

from app.self_improvement.types import SkillRecord  # noqa: E402


class TestMatchesContext:
    def test_no_conditions_always_matches(self):
        r = SkillRecord(id="x", topic="t", content_markdown="c", kb="episteme")
        assert r.matches_context(mode="local", cost_mode="budget")
        assert r.matches_context(mode="cloud", cost_mode="quality")
        assert r.matches_context()  # default empties

    def test_requires_mode_positive(self):
        r = SkillRecord(id="x", topic="t", content_markdown="c", kb="episteme",
                        requires_mode="local")
        assert r.matches_context(mode="local", cost_mode="budget")
        assert not r.matches_context(mode="cloud", cost_mode="budget")

    def test_fallback_for_mode_inverse(self):
        """Skill only active when this mode is NOT the current one."""
        r = SkillRecord(id="x", topic="t", content_markdown="c", kb="episteme",
                        fallback_for_mode="cloud")
        assert r.matches_context(mode="local")
        assert r.matches_context(mode="hybrid")
        assert not r.matches_context(mode="cloud")

    def test_requires_tier_budget_mode(self):
        r = SkillRecord(id="x", topic="t", content_markdown="c", kb="episteme",
                        requires_tier="premium")
        # budget cost_mode only allows local + budget tiers
        assert not r.matches_context(mode="local", cost_mode="budget")
        assert not r.matches_context(mode="local", cost_mode="balanced")  # no mid/premium
        assert r.matches_context(mode="local", cost_mode="quality")

    def test_requires_tier_mid(self):
        r = SkillRecord(id="x", topic="t", content_markdown="c", kb="episteme",
                        requires_tier="mid")
        assert not r.matches_context(mode="local", cost_mode="budget")
        assert r.matches_context(mode="local", cost_mode="balanced")
        assert r.matches_context(mode="local", cost_mode="quality")

    def test_unknown_cost_mode_allows_all_tiers(self):
        r = SkillRecord(id="x", topic="t", content_markdown="c", kb="episteme",
                        requires_tier="premium")
        # Unrecognized cost_mode defaults to allowing all tiers
        assert r.matches_context(mode="hybrid", cost_mode="unknown_mode")

    def test_composite_predicates(self):
        r = SkillRecord(id="x", topic="t", content_markdown="c", kb="episteme",
                        requires_mode="local", requires_tier="budget")
        assert r.matches_context(mode="local", cost_mode="budget")
        assert not r.matches_context(mode="cloud", cost_mode="budget")

    def test_empty_cost_mode_skips_tier_check(self):
        r = SkillRecord(id="x", topic="t", content_markdown="c", kb="episteme",
                        requires_tier="premium")
        # Tier check is only applied when cost_mode is provided
        assert r.matches_context(mode="local", cost_mode="")

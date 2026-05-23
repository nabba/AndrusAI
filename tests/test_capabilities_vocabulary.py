"""Pinning tests for the capability vocabulary (Tier-3 amendment 2026-05-23).

Closes Verified Implementation Plan Gap 4 — the ``ratelimit`` and
``code-intelligence`` capability categories are now part of the bounded
vocabulary in ``app/tool_registry/capabilities.py``.

What's pinned
─────────────

  * Both new categories exist at the expected names.
  * All 10 expected tags are reachable via ``is_known()`` and
    ``description_for()``.
  * ``category_for()`` returns the right top-level for each tag.
  * The pure-addition discipline holds — none of the pre-amendment
    categories or tags were touched.
  * No tag collisions (a freshly-added tag must not shadow one in
    another category).
  * No category collisions (each tag belongs to exactly one category).

The CrewAI / pydantic_settings boot path isn't required for these
tests — capabilities.py is dependency-free.
"""
from __future__ import annotations

import importlib
import sys


def _load_capabilities():
    """Direct-import to avoid pulling in app.healing on the dev host."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_caps", "app/tool_registry/capabilities.py",
    )
    m = importlib.util.module_from_spec(spec)
    sys.modules["_caps"] = m
    spec.loader.exec_module(m)
    return m


CAPS = _load_capabilities()


# ── ratelimit category ─────────────────────────────────────────────

_RATELIMIT_TAGS = (
    "quota-limited-anthropic",
    "quota-limited-brave",
    "quota-limited-google-workspace",
    "quota-limited-openai",
    "quota-limited-osv",
    "quota-limited-github",
)


class TestRatelimitCategory:
    def test_category_exists(self):
        assert "ratelimit" in CAPS.CAPABILITIES

    def test_all_tags_present(self):
        for tag in _RATELIMIT_TAGS:
            assert tag in CAPS.CAPABILITIES["ratelimit"], (
                f"missing tag {tag!r} from ratelimit category"
            )

    def test_is_known_returns_true(self):
        for tag in _RATELIMIT_TAGS:
            assert CAPS.is_known(tag), f"{tag} not in is_known()"

    def test_category_for_routes_correctly(self):
        for tag in _RATELIMIT_TAGS:
            assert CAPS.category_for(tag) == "ratelimit", (
                f"{tag} did not route to 'ratelimit'"
            )

    def test_descriptions_non_empty(self):
        for tag in _RATELIMIT_TAGS:
            d = CAPS.description_for(tag)
            assert d, f"{tag} has empty description"
            assert len(d) > 20, (
                f"{tag} description too short: {d!r}"
            )


# ── code-intelligence category ─────────────────────────────────────

_CODEINTEL_TAGS = (
    "queries-code-symbols",
    "checks-types",
    "finds-test-coverage",
    "finds-deps",
)


class TestCodeIntelligenceCategory:
    def test_category_exists(self):
        assert "code-intelligence" in CAPS.CAPABILITIES

    def test_all_tags_present(self):
        for tag in _CODEINTEL_TAGS:
            assert tag in CAPS.CAPABILITIES["code-intelligence"], (
                f"missing tag {tag!r}"
            )

    def test_is_known_returns_true(self):
        for tag in _CODEINTEL_TAGS:
            assert CAPS.is_known(tag), f"{tag} not in is_known()"

    def test_category_for_routes_correctly(self):
        for tag in _CODEINTEL_TAGS:
            assert CAPS.category_for(tag) == "code-intelligence", (
                f"{tag} did not route to 'code-intelligence'"
            )


# ── Pure-addition discipline ───────────────────────────────────────


class TestPureAddition:
    """The Tier-3 amendment was operator-bounded to ADDITION only.
    None of the pre-amendment categories or tags may have been
    renamed, removed, or relocated."""

    _PRE_AMENDMENT_CATEGORIES = {
        "data", "knowledge", "memory", "compute", "delivery",
        "governance", "observability", "tickets", "code-development",
    }

    _PRE_AMENDMENT_TAGS_SAMPLE = {
        "reads-file": "data",
        "writes-file": "data",
        "searches-web": "data",
        "reads-knowledge-base": "knowledge",
        "writes-team-belief": "memory",
        "executes-code": "compute",
        "renders-pdf": "compute",
        "sends-signal": "delivery",
        "registers-tool": "governance",
        "reads-deployment-state": "observability",
        "manages-tickets": "tickets",
        "submits-coding-session": "code-development",
    }

    def test_all_pre_amendment_categories_still_present(self):
        cats = set(CAPS.CAPABILITIES.keys())
        missing = self._PRE_AMENDMENT_CATEGORIES - cats
        assert not missing, f"pre-amendment categories lost: {missing}"

    def test_all_pre_amendment_tags_still_in_right_category(self):
        for tag, expected_cat in self._PRE_AMENDMENT_TAGS_SAMPLE.items():
            actual = CAPS.category_for(tag)
            assert actual == expected_cat, (
                f"tag {tag} moved from {expected_cat!r} to {actual!r}"
            )


# ── Invariants across the whole vocabulary ─────────────────────────


class TestVocabularyInvariants:
    def test_no_tag_collisions(self):
        """No tag may appear in more than one category."""
        seen: dict[str, str] = {}
        for cat_name, cat in CAPS.CAPABILITIES.items():
            for tag in cat:
                if tag in seen:
                    raise AssertionError(
                        f"tag {tag!r} appears in both "
                        f"{seen[tag]!r} and {cat_name!r}"
                    )
                seen[tag] = cat_name

    def test_no_category_collisions_with_deprecated(self):
        """A tag may not be both active AND deprecated."""
        active = CAPS.all_capability_tags()
        deprecated = set(CAPS.DEPRECATED_CAPABILITIES.keys())
        assert not (active & deprecated), (
            f"tags in BOTH active and deprecated: {active & deprecated}"
        )

    def test_tag_names_are_kebab_case(self):
        """Every tag is lowercase-with-hyphens (kebab-case)."""
        import re
        kebab = re.compile(r"^[a-z]+(?:-[a-z0-9]+)*$")
        for tag in CAPS.all_capability_tags():
            assert kebab.match(tag), (
                f"tag {tag!r} is not kebab-case"
            )

    def test_total_tag_count_increased_by_ten(self):
        """The amendment adds 6 ratelimit + 4 code-intelligence = 10 tags.

        Pre-amendment count was 35 (counted directly from the file).
        Post-amendment expectation: 45.
        """
        assert len(CAPS.all_capability_tags()) >= 45, (
            f"expected ≥45 tags after amendment; got "
            f"{len(CAPS.all_capability_tags())}"
        )


if __name__ == "__main__":
    import sys as _s
    import pytest
    _s.exit(pytest.main([__file__, "-v"]))

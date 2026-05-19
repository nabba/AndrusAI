"""Tests for app.library_radar.idiom_radar — Phase 4 PEP/idiom radar."""
from __future__ import annotations

import pytest


# ── pep_number extraction ───────────────────────────────────────────────


def test_pep_number_extracted_from_url():
    from app.library_radar import idiom_radar
    n = idiom_radar._pep_number({"id": "https://peps.python.org/pep-0734/", "title": ""})
    assert n == 734


def test_pep_number_extracted_from_title():
    from app.library_radar import idiom_radar
    n = idiom_radar._pep_number({"id": "", "title": "PEP 634 -- Structural Pattern Matching"})
    assert n == 634


def test_pep_number_handles_padded_zero():
    from app.library_radar import idiom_radar
    n = idiom_radar._pep_number({"id": "PEP-0008", "title": ""})
    assert n == 8


def test_pep_number_none_when_absent():
    from app.library_radar import idiom_radar
    assert idiom_radar._pep_number({"id": "https://example.com/no-number", "title": "X"}) is None


# ── keyword detection ──────────────────────────────────────────────────


def test_idiom_keywords_match_substring():
    from app.library_radar import idiom_radar
    matched = idiom_radar._idiom_keywords_in("Add Structural Pattern Matching via match statements")
    assert "match" in matched
    assert "structural pattern" in matched


def test_idiom_keywords_empty_when_unrelated():
    from app.library_radar import idiom_radar
    matched = idiom_radar._idiom_keywords_in("New build system for CPython")
    assert matched == ()


# ── detect_idiom_peps ──────────────────────────────────────────────────


def test_detect_idiom_peps_uses_feed(monkeypatch):
    from app.library_radar import idiom_radar
    fake_feed = [
        {
            "id": "https://peps.python.org/pep-0634/",
            "title": "PEP 634 -- Structural Pattern Matching: Specification",
            "abstract": "This PEP introduces match statements for pattern matching.",
            "published": "2026-04-01T00:00:00+00:00",
        },
        {
            "id": "https://peps.python.org/pep-0999/",
            "title": "PEP 999 -- Boring build system update",
            "abstract": "Adjusts build flags. Nothing to migrate.",
            "published": "2026-03-01T00:00:00+00:00",
        },
    ]
    monkeypatch.setattr(
        "app.episteme.feed_sources.fetch_python_peps",
        lambda **kw: fake_feed,
    )
    out = idiom_radar.detect_idiom_peps()
    assert len(out) == 1
    assert out[0].pep_number == 634
    assert "match" in out[0].keywords_matched


def test_detect_idiom_peps_returns_empty_on_fetch_failure(monkeypatch):
    from app.library_radar import idiom_radar
    monkeypatch.setattr(
        "app.episteme.feed_sources.fetch_python_peps",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("network")),
    )
    assert idiom_radar.detect_idiom_peps() == []


def test_detect_idiom_peps_capped_at_max(monkeypatch):
    from app.library_radar import idiom_radar
    # 10 idiom-matching PEPs.
    feed = [
        {
            "id": f"https://peps.python.org/pep-{700+i:04d}/",
            "title": f"PEP {700+i} -- New typing feature {i}",
            "abstract": "Improves typing.",
            "published": f"2026-04-{i+1:02d}T00:00:00+00:00",
        }
        for i in range(10)
    ]
    monkeypatch.setattr(
        "app.episteme.feed_sources.fetch_python_peps",
        lambda **kw: feed,
    )
    out = idiom_radar.detect_idiom_peps()
    assert len(out) <= idiom_radar._MAX_PER_PASS


# ── body / spec ────────────────────────────────────────────────────────


def test_build_body_mentions_pep_number_and_keywords():
    from app.library_radar.idiom_radar import (
        IdiomCandidate,
        _build_body,
    )
    c = IdiomCandidate(
        pep_number=634, title="Structural Pattern Matching",
        abstract="Adds match.", published="2026-01-01",
        keywords_matched=("match",),
    )
    body = _build_body(c)
    assert "PEP 634" in body
    assert "`match`" in body
    assert "https://peps.python.org/pep-0634/" in body


def test_build_spec_has_acceptance_criteria():
    from app.library_radar.idiom_radar import IdiomCandidate, _build_spec
    c = IdiomCandidate(
        pep_number=695, title="Type Parameter Syntax", abstract="",
        published="2026-01-01", keywords_matched=("type",),
    )
    spec = _build_spec(c)
    assert isinstance(spec["acceptance"], list) and spec["acceptance"]
    assert spec["expected_duration_min"] > 0


# ── run_one_pass ───────────────────────────────────────────────────────


def test_run_one_pass_disabled_short_circuits(monkeypatch):
    from app.library_radar import idiom_radar
    monkeypatch.setattr(idiom_radar, "_enabled", lambda: False)
    result = idiom_radar.run_one_pass()
    assert result["disabled"] is True
    assert result["checked"] is False


def test_run_one_pass_stages_via_bridge(monkeypatch, tmp_path):
    """End-to-end through proposal_bridge."""
    monkeypatch.setenv("PROPOSAL_BRIDGE_DIR", str(tmp_path / "bridge"))
    from app.library_radar import idiom_radar

    fake_feed = [{
        "id": "https://peps.python.org/pep-0634/",
        "title": "PEP 634 -- Structural Pattern Matching",
        "abstract": "Introduces match statements.",
        "published": "2026-04-01T00:00:00+00:00",
    }]
    monkeypatch.setattr(
        "app.episteme.feed_sources.fetch_python_peps",
        lambda **kw: fake_feed,
    )
    monkeypatch.setattr(idiom_radar, "_enabled", lambda: True)

    result = idiom_radar.run_one_pass()
    assert result["checked"] is True
    assert result["n_candidates"] == 1
    assert result["staged"] == 1
    body_file = tmp_path / "bridge" / "library_radar" / "pep_0634.md"
    assert body_file.exists()


def test_signature_is_stable_per_pep():
    from app.library_radar.idiom_radar import _signature
    assert _signature(634) == "pep_0634"
    assert _signature(8) == "pep_0008"

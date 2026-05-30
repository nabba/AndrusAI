"""Tests for Gate A — semantic rejection suppression (2026-05-30).

Closes the paraphrase-of-a-rejected-idea flood: the byte-exact CR dedup
only catches identical re-files, so LLM-driven observational producers
(tech_radar → library_radar, …) re-surface the same rejected idea with
fresh wording every pass and slip through. The lessons-learned KB already
clusters rejections semantically; Gate A promotes that signal from an
advisory banner to an actual decision, for observational producers only,
behind an off/advisory/enforcing master switch.

Live failure these pin against: 7 rejected `proposal_bridge:library_radar`
"OpenRouter web search / tool calling" CRs on 2026-05-29, then an 8th
paraphrase (`b1c394dcf290`) filed 2026-05-30 carrying its own
"seen 7× before, similarity 0.67" banner — computed, then ignored.
"""
from __future__ import annotations

import pytest


# ── Policy module: producer allowlist ────────────────────────────────


def test_bridge_observational_producers_are_suppressible():
    """Every observational markdown-doc producer routes through the bridge
    with a ``proposal_bridge:<source>`` requestor — the precise boundary."""
    from app.change_requests import rejection_gate as g
    assert g.is_suppressible_producer("proposal_bridge:library_radar")
    assert g.is_suppressible_producer("proposal_bridge:capability_gap_analyzer")
    assert g.is_suppressible_producer("proposal_bridge:paper_pipeline")
    assert g.is_suppressible_producer("proposal_bridge:dependency_radar")


def test_humans_and_real_fix_producers_are_never_suppressible():
    """Suppression must never silence a bug fix or an operator."""
    from app.change_requests import rejection_gate as g
    for r in ("coder", "operator", "error_diagnosis", "autonomous_executor",
              "self_improver", "researcher", ""):
        assert not g.is_suppressible_producer(r), r


def test_evidence_bearing_adoption_cr_is_never_suppressible():
    """Gate B invariant: the trial-backed adoption CR (library_radar_trial)
    must NEVER be semantically suppressed just for being lexically near a
    rejected unverified doc-proposal for the same package. This is why the
    bare 'library_radar' prefix is intentionally absent from the allowlist."""
    from app.change_requests import rejection_gate as g
    assert not g.is_suppressible_producer("library_radar_trial")
    assert not g.is_suppressible_producer("library_radar")


# ── Policy module: evaluate() thresholds + mode ──────────────────────


@pytest.fixture
def fake_lesson(monkeypatch):
    """Stub the lessons KB so tests don't need ChromaDB. Returns a
    factory that installs a single-cluster match with given sim/count."""
    import app.companion.lessons_learned as ll

    def _install(similarity, count, lesson_id="ca9b"):
        monkeypatch.setattr(
            ll, "check_against",
            lambda text, top_k=1: [{
                "id": lesson_id, "similarity": similarity,
                "sample_reason": "rejected via React", "count": count,
            }],
        )
    return _install


def _set_mode(monkeypatch, mode, sim=0.55, count=3):
    from app.change_requests import rejection_gate as g
    monkeypatch.setattr(g, "config", lambda: (mode, sim, count))


def test_match_requires_both_similarity_and_count(monkeypatch, fake_lesson):
    from app.change_requests import rejection_gate as g
    _set_mode(monkeypatch, "enforcing", sim=0.55, count=3)

    # The real flood: high similarity, seen many times → matched.
    fake_lesson(0.67, 7)
    assert g.evaluate("openrouter web search fetch tooling").should_suppress

    # Similar but never rejected enough (count below floor) → never suppress.
    fake_lesson(0.90, 2)
    v = g.evaluate("brand new but lexically near a once-rejected idea")
    assert not v.matched and not v.should_suppress

    # Rejected often but only loosely similar → below similarity floor.
    fake_lesson(0.40, 20)
    assert not g.evaluate("loosely related").matched


def test_off_mode_never_matches(monkeypatch, fake_lesson):
    from app.change_requests import rejection_gate as g
    _set_mode(monkeypatch, "off")
    fake_lesson(0.99, 99)
    assert not g.evaluate("anything").matched


def test_advisory_matches_but_does_not_suppress(monkeypatch, fake_lesson):
    from app.change_requests import rejection_gate as g
    _set_mode(monkeypatch, "advisory")
    fake_lesson(0.67, 7)
    v = g.evaluate("openrouter web search fetch tooling")
    assert v.matched is True
    assert v.should_suppress is False  # advisory = observe only


def test_evaluate_is_failure_isolated(monkeypatch):
    """A broken KB must never produce a (false) suppression."""
    import app.companion.lessons_learned as ll
    from app.change_requests import rejection_gate as g
    _set_mode(monkeypatch, "enforcing")

    def _boom(*a, **k):
        raise RuntimeError("KB unavailable")
    monkeypatch.setattr(ll, "check_against", _boom)
    assert not g.evaluate("anything").matched


def test_config_failsafe_fallback_is_advisory(monkeypatch):
    """When runtime_settings can't be read at all, config() must fall back to
    the CONSERVATIVE advisory mode — an infra glitch must never cause silent
    suppression. (The shipped runtime_settings *value* is "enforcing" as of
    2026-05-30; this pins the separate fail-safe path.) Forced deterministic
    across host AND CI by replacing runtime_settings with a stub whose
    snapshot() raises."""
    import sys
    import types
    from app.change_requests import rejection_gate as g

    stub = types.ModuleType("app.runtime_settings")

    def _boom():
        raise RuntimeError("settings unreadable")

    stub.snapshot = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.runtime_settings", stub)

    mode, sim, count = g.config()
    assert mode == "advisory"
    assert sim == pytest.approx(0.55)
    assert count == 3


# ── Gate A in create_request ─────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    from app.change_requests import store
    monkeypatch.setattr(store, "_STORE_DIR", tmp_path / "change_requests")
    monkeypatch.setattr(store, "_AUDIT_LOG",
                         tmp_path / "change_requests" / "audit.jsonl")
    store.reset_for_tests()
    yield
    store.reset_for_tests()


@pytest.fixture
def ok_validator(monkeypatch):
    from app.change_requests import lifecycle
    from app.change_requests.validator import ValidationResult
    ok = lambda **kw: ValidationResult(ok=True, reason="", is_tier_immutable=False)
    monkeypatch.setattr(lifecycle, "validate", ok)
    monkeypatch.setattr(lifecycle, "validate_auto_apply", ok)
    yield


def _create(requestor, path, reason):
    from app.change_requests import lifecycle
    return lifecycle.create_request(
        requestor=requestor, path=path,
        new_content="# proposal\n", old_content="", reason=reason,
    )


def test_enforcing_suppresses_observational_producer(monkeypatch, ok_validator, fake_lesson):
    from app.change_requests import lifecycle, store
    from app.change_requests.models import Status
    _set_mode(monkeypatch, "enforcing")
    fake_lesson(0.67, 7)

    cr = _create(
        "proposal_bridge:library_radar",
        "docs/proposed_libraries/abc-openrouter_web_search_fetch.md",
        "OpenRouter Web Search & Fetch Tooling",
    )
    assert cr.status == Status.REJECTED
    assert "semantic-rejection-suppressed" in (cr.decision_reason or "")
    # The promoter sees a non-PENDING CR and terminates the proposal.
    persisted = store.get(cr.id)
    assert persisted is not None and persisted.status == Status.REJECTED


def test_advisory_lets_observational_producer_through(monkeypatch, ok_validator, fake_lesson):
    from app.change_requests.models import Status
    _set_mode(monkeypatch, "advisory")
    fake_lesson(0.67, 7)
    cr = _create(
        "proposal_bridge:library_radar",
        "docs/proposed_libraries/abc-openrouter_web_search_fetch.md",
        "OpenRouter Web Search & Fetch Tooling",
    )
    assert cr.status == Status.PENDING  # observed, not suppressed


def test_human_cr_is_never_suppressed_even_on_strong_match(monkeypatch, ok_validator, fake_lesson):
    from app.change_requests.models import Status
    _set_mode(monkeypatch, "enforcing")
    fake_lesson(0.99, 50)  # would suppress an observational producer
    cr = _create("coder", "app/foo.py", "fix the thing")
    assert cr.status == Status.PENDING


# ── Gate A′ in the library_radar producer ────────────────────────────


def test_library_radar_skips_rejected_pattern_in_enforcing(monkeypatch, fake_lesson):
    """A paraphrase of a repeatedly-rejected idea never gets staged."""
    from app.change_requests import rejection_gate as g
    from app.library_radar import proposer
    _set_mode(monkeypatch, "enforcing")
    fake_lesson(0.67, 7)

    staged = []
    monkeypatch.setattr(proposer, "_load_radar_lines", lambda: [])
    # Force a non-empty requirements read so the pin filter is a no-op.
    monkeypatch.setattr(proposer, "_read_requirements", lambda p: set())
    # If stage() is ever called we record it; it should NOT be.
    import app.proposal_bridge as pb
    monkeypatch.setattr(
        pb, "stage",
        lambda **kw: staged.append(kw) or (None, True),
    )

    line = "[tools] OpenRouter Web Search & Fetch Tooling: native search/fetch for any model. Action: integrate"
    result = proposer.run_one_pass(discoveries=[line], requirements_path="/nonexistent")
    assert result["status"] == "all_rejected_pattern"
    assert result["n_rejected_skipped"] == 1
    assert staged == []  # never reached the bridge

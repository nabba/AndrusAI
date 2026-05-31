"""Host-safe tests for app.epistemic.gate_research_evidence.

The mode switch and zone resolver are monkeypatched everywhere so no
runtime_settings file is touched and no chromadb/LLM is loaded. The
``detect_fn`` seam decouples the escalation logic from the regex detector;
the detector + citation helper are exercised directly with strings.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.epistemic import gate_research_evidence as G

_REPO_ROOT = Path(__file__).resolve().parents[1]

# evaluate() never reads the verdict, so a sentinel is sufficient.
_VERDICT = object()


def _set(monkeypatch, *, mode: str, zone: str):
    monkeypatch.setattr(G, "_mode", lambda: mode)
    monkeypatch.setattr(G, "_zone_for_task", lambda task_id: zone)


def _gap(_text):
    return True, ["95%", "p < 0.01"]


def _no_gap(_text):
    return False, []


# ── activation gating ────────────────────────────────────────────────────


def test_mode_off_is_inert(monkeypatch):
    _set(monkeypatch, mode="off", zone="autonomous")
    assert G.evaluate(
        proposal_text="x" * 200, task_id="t", verdict=_VERDICT, detect_fn=_gap
    ) == (None, "")


def test_too_short_no_op(monkeypatch):
    _set(monkeypatch, mode="enforcing", zone="autonomous")
    # Below _MIN_DRAFT_CHARS the detector must not even be consulted.
    assert G.evaluate(
        proposal_text="too short", task_id="t", verdict=_VERDICT, detect_fn=_gap
    ) == (None, "")


def test_chat_zone_no_op(monkeypatch):
    _set(monkeypatch, mode="enforcing", zone="chat")
    assert G.evaluate(
        proposal_text="x" * 200, task_id="t", verdict=_VERDICT, detect_fn=_gap
    ) == (None, "")


def test_no_gap_returns_empty(monkeypatch):
    _set(monkeypatch, mode="enforcing", zone="autonomous")
    assert G.evaluate(
        proposal_text="x" * 200, task_id="t", verdict=_VERDICT, detect_fn=_no_gap
    ) == (None, "")


# ── advisory mode (zero behaviour change) ─────────────────────────────────


def test_advisory_autonomous_note_no_action(monkeypatch):
    _set(monkeypatch, mode="advisory", zone="autonomous")
    action, note = G.evaluate(
        proposal_text="x" * 200, task_id="t", verdict=_VERDICT, detect_fn=_gap
    )
    assert action is None
    assert note.startswith("advisory:")
    assert "would escalate (verify)" in note
    assert "uncited empirical" in note


def test_advisory_financial_note_mentions_peer_review(monkeypatch):
    _set(monkeypatch, mode="advisory", zone="financial")
    action, note = G.evaluate(
        proposal_text="x" * 200, task_id="t", verdict=_VERDICT, detect_fn=_gap
    )
    assert action is None
    assert "would escalate (peer_review)" in note


# ── enforcing mode (escalation) ───────────────────────────────────────────


def test_enforcing_autonomous_escalates_verify(monkeypatch):
    _set(monkeypatch, mode="enforcing", zone="autonomous")
    action, note = G.evaluate(
        proposal_text="x" * 200, task_id="t", verdict=_VERDICT, detect_fn=_gap
    )
    assert action == "verify"
    assert "uncited empirical" in note
    assert not note.startswith("advisory:")


def test_enforcing_financial_escalates_peer_review(monkeypatch):
    _set(monkeypatch, mode="enforcing", zone="financial")
    action, _ = G.evaluate(
        proposal_text="x" * 200, task_id="t", verdict=_VERDICT, detect_fn=_gap
    )
    assert action == "peer_review"


# ── failure isolation + escalate-only invariant ──────────────────────────


def test_detector_exception_is_isolated(monkeypatch):
    _set(monkeypatch, mode="enforcing", zone="autonomous")

    def boom(_text):
        raise RuntimeError("detector down")

    assert G.evaluate(
        proposal_text="x" * 200, task_id="t", verdict=_VERDICT, detect_fn=boom
    ) == (None, "")


def test_mode_failsafe_off(monkeypatch):
    # A broken settings read must fail safe to "off".
    pytest.importorskip("pydantic_settings")
    import app.runtime_settings as rs

    def boom():
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(rs, "get_research_evidence_gate_mode", boom)
    assert G._mode() == "off"


@pytest.mark.parametrize("mode", ["off", "advisory", "enforcing"])
@pytest.mark.parametrize("zone", ["chat", "autonomous", "financial"])
def test_escalate_only_never_ship_or_hedge(monkeypatch, mode, zone):
    _set(monkeypatch, mode=mode, zone=zone)
    action, _ = G.evaluate(
        proposal_text="x" * 200, task_id="t", verdict=_VERDICT, detect_fn=_gap
    )
    assert action in (None, "verify", "peer_review")


# ── _detect_evidence_gap (regex detector, real strings) ───────────────────


def test_detect_gap_percent_no_citation():
    has_gap, samples = G._detect_evidence_gap(
        "The model reached 95% accuracy on the held-out evaluation set across every run."
    )
    assert has_gap is True
    assert any("95%" in s for s in samples)


def test_detect_gap_pvalue():
    has_gap, _ = G._detect_evidence_gap(
        "The improvement was statistically significant at p < 0.01 over the prior baseline run."
    )
    assert has_gap is True


def test_detect_gap_named_metric():
    has_gap, _ = G._detect_evidence_gap(
        "On the benchmark the system scored BLEU 41.2, well above the previously reported numbers."
    )
    assert has_gap is True


def test_detect_gap_multiplier():
    has_gap, _ = G._detect_evidence_gap(
        "The rewritten kernel delivers a 3x speedup over the previous baseline on identical hardware."
    )
    assert has_gap is True


def test_detect_gap_currency_composition():
    has_gap, samples = G._detect_evidence_gap(
        "Projected annual revenue is EUR 2,400,000 under the new pipeline and growth assumptions."
    )
    assert has_gap is True
    assert any("2,400,000" in s for s in samples)


def test_detect_no_gap_when_author_year_cited():
    has_gap, samples = G._detect_evidence_gap(
        "The model reached 95% accuracy (Smith, 2021) on the held-out evaluation set."
    )
    assert has_gap is False
    assert samples == []


def test_detect_no_gap_when_url_present():
    has_gap, _ = G._detect_evidence_gap(
        "The model reached 95% accuracy; see the report at https://example.org/results for detail."
    )
    assert has_gap is False


def test_detect_no_gap_when_arxiv_id_present():
    has_gap, _ = G._detect_evidence_gap(
        "Following the protocol of arXiv 2401.01234 we observed 95% accuracy on the test split."
    )
    assert has_gap is False


def test_detect_no_gap_when_no_empirical_claims():
    has_gap, samples = G._detect_evidence_gap(
        "This is a purely qualitative summary of the discussion with no figures whatsoever in it."
    )
    assert has_gap is False
    assert samples == []


def test_detect_bare_year_and_integer_not_flagged():
    # Years and plain counts must NOT be treated as empirical claims.
    has_gap, _ = G._detect_evidence_gap(
        "In 2024 the team of 12 engineers met on 3 occasions to discuss the qualitative roadmap."
    )
    assert has_gap is False


def test_detect_samples_capped():
    text = " ".join(f"metric {i} was {i}% in the run" for i in range(1, 12))
    _, samples = G._detect_evidence_gap(text)
    assert len(samples) <= G._MAX_SAMPLES


def test_detect_empty_text():
    assert G._detect_evidence_gap("") == (False, [])


# ── _has_citation_marker ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "see doi:10.1145/1234567.1234568",
        "as shown in [3] earlier",
        "Vaswani et al. introduced it",
        "according to the latest survey",
        "Source: internal benchmark report",
        "consistent with (Smith, 2021)",
        "details at https://example.org",
        "per arXiv 2401.01234 results",
    ],
)
def test_citation_markers_detected(text):
    assert G._has_citation_marker(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "no citation at all in this sentence",
        "",
        "the result was 95% across the board",
    ],
)
def test_no_citation_markers(text):
    assert G._has_citation_marker(text) is False


# ── wiring + settings contract ────────────────────────────────────────────


def test_wired_into_verification_extension():
    src = (_REPO_ROOT / "app" / "epistemic" / "verification_extension.py").read_text(
        encoding="utf-8"
    )
    assert "from app.epistemic.gate_research_evidence import evaluate" in src
    assert "research-evidence: " in src


def test_module_exports_only_evaluate():
    assert G.__all__ == ["evaluate"]


def test_settings_mode_validation_rejects_unknown():
    pytest.importorskip("pydantic_settings")
    import app.runtime_settings as rs

    # Raises before any disk write (validation precedes the _update call).
    with pytest.raises(ValueError):
        rs.set_research_evidence_gate_mode("bogus")


def test_settings_valid_modes_constant():
    pytest.importorskip("pydantic_settings")
    import app.runtime_settings as rs

    assert rs._VALID_RESEARCH_EVIDENCE_GATE_MODES == ("off", "advisory", "enforcing")

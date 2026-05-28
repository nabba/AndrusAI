"""Grounding discipline for the variant archive.

Pins the 2026-05-28 fix for the self-referential alignment-audit drift:
the evolution loop persists free-form hypotheses that embed point-in-time
(and often confabulated) metrics — "response time is 145.5s",
"BadRequestError appears 16x", "50% success rate". Those frozen numbers were
re-read by the TIER_IMMUTABLE alignment auditor and recited as *current measured*
constitutional violations, manufacturing "critical drift" alarms out of stale
guesses.

The fix lives at the one modifiable seam between the immutable generator
(evolution.py / avo_operator.py) and the immutable consumer (alignment_audit.py):
``variant_archive.get_recent_variants`` grounds hypotheses by default — metric
tokens neutralised, provenance age attached — so an LLM consumer cannot launder a
frozen number into a fact. Real numbers come only from the live telemetry
instrument. Operator/forensic surfaces opt out with ``raw=True``.
"""
from __future__ import annotations

from app import variant_archive as va


# ── _neutralize_metrics: unit-tagged numbers go, everything else stays ────────

def test_neutralizes_perf_metric_tokens():
    nm = va._neutralize_metrics
    assert "145.5s" not in nm("The response time is 145.5s average, which is high")
    assert "16x" not in nm("coding:BadRequestError appears 16x - the highest")
    assert "50%" not in nm("the 50% task success rate is partly due to")
    assert "8x" not in nm("research crew has 8x RuntimeError and 3x ConnectionError")
    assert "27%" not in nm("showed +27% improvements on reasoning benchmarks")


def test_preserves_qualitative_text():
    out = va._neutralize_metrics("The response time is 145.5s average, which is high")
    assert "response time" in out and "high" in out


def test_does_not_touch_dates_ids_codes_versions():
    """Conservative by design — only perf-unit-tagged numerics are neutralised."""
    nm = va._neutralize_metrics
    assert "2310.06117" in nm("Step-Back Prompting (arXiv:2310.06117) addresses this")
    assert "402" in nm("APIStatusError: Error code: 402 errors")
    assert "3.11" in nm("Python 3.11 upgrade path")
    assert "2026" in nm("on 2026-05-27 the drift was high")
    assert "3 sources" in nm("stop after 3 sources corroborate")  # 's' word, not seconds


def test_empty_input_safe():
    assert va._neutralize_metrics("") == ""
    assert va._neutralize_metrics(None) is None


# ── get_recent_variants: grounded by default, raw on request ──────────────────

_SAMPLE = [
    {"id": "a", "status": "keep", "delta": 0.0, "timestamp": "2026-05-26T00:39:00+00:00",
     "hypothesis": "The response time is 145.5s average, which is high. "
                   "coding:BadRequestError appears 16x - the highest frequency error.",
     "generation": 49},
    {"id": "b", "status": "keep", "delta": 0.0, "timestamp": "2026-05-27T00:00:00+00:00",
     "hypothesis": "The research crew's 50% task success rate is partly due to "
                   "agents receiving underspecified tasks.",
     "generation": 52},
]


def test_auditor_view_is_grounded(monkeypatch):
    """The auditor calls get_recent_variants(10) with no kwargs — it must not
    see any fabricated number it could restate as a measured fact."""
    monkeypatch.setattr(va, "_load", lambda: list(_SAMPLE))
    text = " ".join(v["hypothesis"] for v in va.get_recent_variants(10))
    for tok in ("145.5", "16x", "50%"):
        assert tok not in text, f"fabricated metric {tok!r} leaked into auditor view"


def test_raw_view_preserves_originals(monkeypatch):
    monkeypatch.setattr(va, "_load", lambda: list(_SAMPLE))
    text = " ".join(v["hypothesis"] for v in va.get_recent_variants(10, raw=True))
    for tok in ("145.5", "16x", "50%"):
        assert tok in text


def test_grounding_attaches_provenance(monkeypatch):
    monkeypatch.setattr(va, "_load", lambda: list(_SAMPLE))
    v = va.get_recent_variants(10)[0]
    assert "age_days" in v and v["age_days"] >= 0
    assert v["as_of"] == _SAMPLE[0]["timestamp"]


def test_grounding_preserves_non_prose_fields(monkeypatch):
    """status/delta/timestamp/generation are untouched — callers that count
    kept variants (evolution.py) must keep working."""
    monkeypatch.setattr(va, "_load", lambda: list(_SAMPLE))
    v = va.get_recent_variants(10)[0]
    assert v["status"] == "keep" and v["delta"] == 0.0 and v["generation"] == 49


def test_stored_record_is_never_mutated(monkeypatch):
    """Grounding is a read-side view; the archive on disk stays verbatim."""
    monkeypatch.setattr(va, "_load", lambda: list(_SAMPLE))
    va.get_recent_variants(10)
    assert "145.5s" in _SAMPLE[0]["hypothesis"]

"""Input-aware creative budget + routing size-guard (2026-06-19).

Regression pins for the 2026-06-18 incident: a 110k-char document attachment
routed to the creative crew aborted in phase 1 ("budget $0.10 exceeded in
phase initiation") before producing any output, because a fixed per-run USD
cap is structurally blind to input size — reading a 28k-token document costs
more than $0.10 by itself.

Two composing fixes:
  1. app.creative_mode.effective_budget_usd — scales the budget with input
     size, capped at a ceiling, never below the operator's explicit value.
  2. routing.maybe_promote_to_creative — large analytical tasks stay in the
     standard `writing` crew (no per-run cap) instead of the ideation crew.

Tests use importorskip so a host without pydantic deps skips cleanly; the
Docker CI image exercises every case.
"""
import pytest


# ── effective_budget_usd ─────────────────────────────────────────────────────

def test_estimate_tokens():
    cm = pytest.importorskip("app.creative_mode")
    assert cm.estimate_tokens("") == 0
    assert cm.estimate_tokens(None) == 0  # type: ignore[arg-type]
    assert cm.estimate_tokens("a" * 400) == 100


def test_small_input_uses_base(monkeypatch):
    cm = pytest.importorskip("app.creative_mode")
    monkeypatch.setattr(cm, "get_budget_usd", lambda: 0.10)
    eb = cm.effective_budget_usd("a short brainstorm prompt")
    assert eb.usd == pytest.approx(0.10)
    assert eb.base_usd == pytest.approx(0.10)
    assert eb.scaled is False
    assert eb.ceiling_hit is False


def test_large_input_scales_up(monkeypatch):
    cm = pytest.importorskip("app.creative_mode")
    monkeypatch.setattr(cm, "get_budget_usd", lambda: 0.10)
    eb = cm.effective_budget_usd("x" * 110_000)  # ~27.5k tokens
    assert eb.scaled is True
    assert eb.usd > 0.10
    assert eb.usd < cm._EFFECTIVE_BUDGET_CEILING_USD
    assert eb.ceiling_hit is False


def test_huge_input_caps_at_ceiling(monkeypatch):
    cm = pytest.importorskip("app.creative_mode")
    monkeypatch.setattr(cm, "get_budget_usd", lambda: 0.10)
    eb = cm.effective_budget_usd("x" * 5_000_000)  # ~1.25M tokens
    assert eb.usd == pytest.approx(cm._EFFECTIVE_BUDGET_CEILING_USD)
    assert eb.ceiling_hit is True


def test_never_below_operator_base(monkeypatch):
    """A high operator-configured budget is never reduced by the ceiling."""
    cm = pytest.importorskip("app.creative_mode")
    monkeypatch.setattr(cm, "get_budget_usd", lambda: 50.0)
    eb = cm.effective_budget_usd("x" * 5_000_000)
    assert eb.usd == pytest.approx(50.0)


def test_incident_110k_doc_would_now_run(monkeypatch):
    """The 2026-06-18 incident: 110k-char doc aborted at $0.10 in phase 1.

    With input-aware budgeting it gets a budget comfortably above $0.10 and
    stays below the creative_crew pre-flight refusal ceiling (60k tokens), so
    the run can actually produce output instead of aborting before phase 1.
    """
    cm = pytest.importorskip("app.creative_mode")
    monkeypatch.setattr(cm, "get_budget_usd", lambda: 0.10)
    eb = cm.effective_budget_usd("x" * 110_000)
    assert eb.input_tokens == 27_500
    assert eb.usd >= 1.0
    assert eb.input_tokens < 60_000  # under creative_crew._MAX_CREATIVE_INPUT_TOKENS


# ── routing.maybe_promote_to_creative size guard ─────────────────────────────

def _decision(task, difficulty=8, crew="writing"):
    return {"crew": crew, "difficulty": difficulty, "task": task}


def test_large_doc_not_promoted_to_creative():
    """A keyword-matching, high-difficulty task that is LARGE stays in writing."""
    routing = pytest.importorskip("app.agents.commander.routing")
    big = "brainstorm novel approaches to this. " + ("x" * 9000)
    out = routing.maybe_promote_to_creative([_decision(big)])
    assert out[0]["crew"] == "writing"
    assert not out[0].get("_auto_promoted")


def test_small_brainstorm_still_promoted():
    """Regression guard: normal short brainstorm tasks are still promoted."""
    routing = pytest.importorskip("app.agents.commander.routing")
    small = "brainstorm novel approaches to user onboarding"
    out = routing.maybe_promote_to_creative([_decision(small)])
    assert out[0]["crew"] == "creative"
    assert out[0].get("_auto_promoted") is True

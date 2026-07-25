"""Regression pins for the 2026-07-25 answer-quality fixes.

Each test corresponds to a finding in ``reports/GATE_DIAGNOSIS_2026-07-25.md``,
recorded from the live 2026-07-24 golden-set run:

  * Finding 0 — the run happened during a 69× HTTP-402 credit outage that the
    harness could not see, so a credit-exhausted system was recorded as a
    quality baseline.
  * Finding 1 — the deep-research evidence gate contradicted itself: its
    precondition accepted only literal URLs while its own per-block check
    accepted ``[Sn]`` labels, and max_tokens truncation removes the source
    list where the literal URLs live.
  * Finding 2 — the creative crew compared whole-request cumulative spend
    against its own per-run budget, so a retry aborted in 0.32 s having made
    zero LLM calls.
  * Finding 3 — crew failure notices reached the critic as reviewable drafts,
    so the user was told *review* had withheld their answer.
  * Finding 4 — "make me a report" was classified as a PDF-artifact request.

Tests use importorskip so a host without the gateway deps skips cleanly; the
Docker image exercises every case.
"""
import json

import pytest


# ── Finding 1: the evidence gate's two halves must agree ────────────────────


def _run_with_web_evidence():
    """A run whose literature step retrieved one fetched web source."""
    from app.autonomous_executor.models import ExecutorStatus
    from app.research.run import HINT_LITERATURE, build_research_run

    run = build_research_run("question")
    literature = next(
        step for step in run.plan if step.crew_hint == HINT_LITERATURE
    )
    literature.status = ExecutorStatus.COMPLETED
    literature.result_text = json.dumps([{
        "source": "web",
        "id": "https://source.example/report",
        "title": "Source",
        "text": (
            "A substantive fetched passage with enough detail to support the "
            "result. A substantive fetched passage with enough detail to "
            "support the result. A substantive fetched passage with detail."
        ),
        "metadata": {
            "url": "https://source.example/report",
            "content_fetched": True,
        },
    }])
    return run


def _run_with_kb_evidence():
    """A run whose only evidence is a KB row — identifier is an internal id.

    No draft can quote a chunk id, so requiring one as proof of citation made
    the gate unpassable by construction for KB-sourced runs.
    """
    from app.autonomous_executor.models import ExecutorStatus
    from app.research.run import HINT_LITERATURE, build_research_run

    run = build_research_run("question")
    literature = next(
        step for step in run.plan if step.crew_hint == HINT_LITERATURE
    )
    literature.status = ExecutorStatus.COMPLETED
    literature.result_text = json.dumps([{
        "source": "kb",
        "id": "kb:chunk:8f3a91c2-not-quotable",
        "title": "Internal knowledge-base chunk",
        "text": (
            "A substantive knowledge-base excerpt with enough detail to "
            "support a finding. A substantive knowledge-base excerpt with "
            "enough detail to support a finding, repeated for length."
        ),
        "score": 0.82,
        "metadata": {},
    }])
    return run


def test_gate_accepts_a_draft_cited_only_with_source_labels():
    """THE headline regression: ``[S1]`` alone must clear the precondition.

    Both live report failures on 2026-07-24 blocked here with the identical
    note "final synthesis cites no identifier retrieved by this run", even
    though evidence had been retrieved and the draft cited it as ``[S1]`` —
    the very form ``_literature_evidence`` teaches the model to use.
    """
    deep_path = pytest.importorskip("app.research.deep_path")

    action, note = deep_path._deep_evidence_gate_for(_run_with_web_evidence())(
        proposal_text="The forest area covers 2.3 million hectares [S1].",
        task_id="task",
    )

    assert action is None, f"a [S1]-cited draft must clear the gate, got: {note}"
    assert "gate clear" in note


def test_gate_accepts_kb_sourced_run_cited_with_source_labels():
    """A KB-only run must be passable at all (it previously was not)."""
    deep_path = pytest.importorskip("app.research.deep_path")

    action, note = deep_path._deep_evidence_gate_for(_run_with_kb_evidence())(
        proposal_text="Dairy herd size fell by 12% over the decade [S1].",
        task_id="task",
    )

    assert action is None, f"KB-sourced runs must be passable, got: {note}"


def test_gate_does_not_accept_an_internal_chunk_id_as_a_citation():
    """A chunk id in the prose is not evidence of citation — it is noise."""
    deep_path = pytest.importorskip("app.research.deep_path")

    action, note = deep_path._deep_evidence_gate_for(_run_with_kb_evidence())(
        proposal_text=(
            "Dairy herd size fell by 12% over the decade. "
            "kb:chunk:8f3a91c2-not-quotable"
        ),
        task_id="task",
    )

    assert action == "verify"


def test_gate_still_blocks_a_draft_with_no_citation_of_any_kind():
    """Loosening the accepted *form* must not loosen the requirement."""
    deep_path = pytest.importorskip("app.research.deep_path")

    action, note = deep_path._deep_evidence_gate_for(_run_with_web_evidence())(
        proposal_text=(
            "The forest area covers 2.3 million hectares and grew by 4%."
        ),
        task_id="task",
    )

    assert action == "verify"
    assert "neither a retrieved identifier nor an" in note


def test_gate_still_blocks_an_out_of_range_source_label():
    """``[S99]`` against a 1-source run is an invented citation, not a label."""
    deep_path = pytest.importorskip("app.research.deep_path")

    action, note = deep_path._deep_evidence_gate_for(_run_with_web_evidence())(
        proposal_text="The forest area covers 2.3 million hectares [S99].",
        task_id="task",
    )

    assert action == "verify"


def test_gate_still_blocks_a_citation_not_retrieved_by_this_run():
    """The anti-laundering check must survive the precondition change."""
    deep_path = pytest.importorskip("app.research.deep_path")

    action, note = deep_path._deep_evidence_gate_for(_run_with_web_evidence())(
        proposal_text=(
            "Supported source: https://source.example/report\n\n"
            "A second assertion cites https://invented.example/report."
        ),
        task_id="task",
    )

    assert action == "verify"
    assert "not retrieved" in note
    assert "invented.example" in note


def test_source_label_numbers_only_counts_in_range_labels():
    deep_path = pytest.importorskip("app.research.deep_path")

    assert deep_path._source_label_numbers("a [S1] b [S3] c [S9]", 3) == {1, 3}
    assert deep_path._source_label_numbers("no labels here", 3) == set()
    assert deep_path._source_label_numbers("[S0] is not 1-based", 3) == set()


# ── Finding 2: the creative budget must measure THIS run ────────────────────


class _FakeTracker:
    """Minimal stand-in for rate_throttle.RequestCostTracker."""

    def __init__(self, cost=0.0, tokens=0):
        self.total_cost_usd = cost
        self.total_tokens = tokens
        self.models_used = set()


def test_creative_budget_ignores_spend_that_predates_the_run(monkeypatch):
    """A request that already spent $0.19 must not shrink the run's budget.

    Live 2026-07-24: the retry run tripped its first budget check having made
    zero LLM calls, because the previous run's $0.198 was still on the shared
    request-level tracker.
    """
    cc = pytest.importorskip("app.crews.creative_crew")

    tracker = _FakeTracker(cost=0.198364, tokens=71095)
    monkeypatch.setattr(cc, "get_active_tracker", lambda: tracker)

    # Baseline the ledger the way run_creative_crew does.
    token = cc._run_baseline_usd.set(tracker.total_cost_usd)
    try:
        # This run has spent nothing yet, so a $0.10 budget is untouched.
        cc._check_budget(0.10, "initiation/researcher")

        # Now this run itself spends $0.04 — still inside budget.
        tracker.total_cost_usd = 0.198364 + 0.04
        cc._check_budget(0.10, "discussion/round-1")

        # And $0.12 of its own spend does trip it.
        tracker.total_cost_usd = 0.198364 + 0.12
        with pytest.raises(cc.BudgetExceeded) as excinfo:
            cc._check_budget(0.10, "convergence")
        assert "this run spent" in str(excinfo.value)
    finally:
        cc._run_baseline_usd.reset(token)


def test_creative_run_spend_is_never_negative(monkeypatch):
    """A tracker swap mid-run must not produce a negative spend."""
    cc = pytest.importorskip("app.crews.creative_crew")

    monkeypatch.setattr(cc, "get_active_tracker", lambda: _FakeTracker(cost=0.01))
    token = cc._run_baseline_usd.set(5.0)
    try:
        assert cc._run_spend_usd() == 0.0
    finally:
        cc._run_baseline_usd.reset(token)


def test_creative_budget_is_a_noop_without_a_tracker(monkeypatch):
    cc = pytest.importorskip("app.crews.creative_crew")

    monkeypatch.setattr(cc, "get_active_tracker", lambda: None)
    assert cc._run_spend_usd() is None
    cc._check_budget(0.0, "initiation/researcher")  # must not raise


# ── Finding 3: no-answer signal replaces critic laundering ──────────────────


def test_no_answer_signal_round_trips_and_clears_on_read():
    outcome = pytest.importorskip("app.crews.outcome")

    outcome.clear_no_answer()
    assert outcome.consume_no_answer() is None

    outcome.record_no_answer("creative", "hit its $0.10 budget before output")
    pending = outcome.consume_no_answer()
    assert pending is not None
    assert pending.crew == "creative"
    assert "0.10" in pending.cause

    # Cleared on read, so a later crew that DID answer isn't suppressed.
    assert outcome.consume_no_answer() is None


def test_no_answer_user_message_names_the_crew_and_the_real_cause():
    outcome = pytest.importorskip("app.crews.outcome")

    message = outcome.NoAnswer(
        crew="creative", cause="hit its $0.10 budget before producing output",
    ).user_message()

    assert "creative" in message
    assert "0.10" in message
    # The old failure mode: blaming adversarial review for an upstream bug.
    assert "withholding" not in message.lower()
    assert "adversarial" not in message.lower()


def test_no_answer_message_survives_an_empty_cause():
    outcome = pytest.importorskip("app.crews.outcome")

    message = outcome.NoAnswer(crew="deep_research", cause="").user_message()
    assert "deep_research" in message
    assert message.strip()


def test_clear_no_answer_drops_a_pending_signal():
    outcome = pytest.importorskip("app.crews.outcome")

    outcome.record_no_answer("deep_research", "gate did not clear")
    outcome.clear_no_answer()
    assert outcome.consume_no_answer() is None


# ── Finding 4: "report" is prose, not a PDF ─────────────────────────────────


@pytest.mark.parametrize("prompt", [
    "please make me a report on estona forest health and deforestation data "
    "over the years. research forestry industry business and practices and "
    "evaluate those from critical point in view",
    "write me a critical report on the Estonian dairy industry's business "
    "practices over the last decade, with sources",
    "make me a report on how Estonian forests have changed over the years",
    "generate a report on Tallinn's housing prices",
])
def test_report_requests_are_text_shape(prompt):
    """The incident prompt must not be put under a PDF contract."""
    ai = pytest.importorskip("app.agents.commander.artifact_intent")

    shape = ai.classify_task(prompt)
    assert not shape.is_artifact, (
        f"classified as artifact ({shape.trigger}) — a report is prose; "
        "the crew has no tool to produce a PDF and the verifier turns a "
        "delivered report into 'could not be delivered as a PDF'"
    )


@pytest.mark.parametrize("prompt,expected_ext", [
    ("make me a pdf report on Estonian forests", ".pdf"),
    ("write the report and save as PDF", ".pdf"),
    ("export the forest report to pdf", ".pdf"),
])
def test_explicit_file_requests_still_classify_as_artifacts(prompt, expected_ext):
    """Asking for a *file* must still be honoured — only the genre word moved."""
    ai = pytest.importorskip("app.agents.commander.artifact_intent")

    shape = ai.classify_task(prompt)
    assert shape.is_artifact, f"{prompt!r} should be artifact-shape"
    assert expected_ext in shape.expected_extensions


def test_chart_and_graphic_requests_are_unaffected():
    ai = pytest.importorskip("app.agents.commander.artifact_intent")

    assert ai.classify_task("make me a chart of forest cover").is_artifact
    assert ai.classify_task("generate a graphic about forest age").is_artifact


# ── Finding 0: the credit circuit breaker ───────────────────────────────────


def test_credit_breaker_absorbs_a_blip_then_trips_on_a_storm(monkeypatch):
    breaker = pytest.importorskip("app.llm_credit_breaker")
    breaker.reset()
    alerts = []
    monkeypatch.setattr(breaker, "_alert_operator", lambda p, c: alerts.append((p, c)))

    model = "openrouter/anthropic/claude-opus-4.7"
    # A blip must still degrade gracefully — failover stays allowed.
    for _ in range(breaker._THRESHOLD - 1):
        breaker.record_credit_error(model)
        assert breaker.should_failover(model)
    assert not alerts

    # The threshold-th error trips it.
    assert breaker.record_credit_error(model) is True
    assert breaker.is_open(model)
    assert not breaker.should_failover(model)
    assert alerts == [("openrouter", breaker._THRESHOLD)]

    # And the operator is told ONCE, not once per error.
    for _ in range(20):
        breaker.record_credit_error(model)
    assert len(alerts) == 1
    breaker.reset()


def test_credit_breaker_is_per_provider(monkeypatch):
    breaker = pytest.importorskip("app.llm_credit_breaker")
    breaker.reset()
    monkeypatch.setattr(breaker, "_alert_operator", lambda p, c: None)

    for _ in range(breaker._THRESHOLD):
        breaker.record_credit_error("openrouter/anthropic/claude-opus-4.7")

    assert not breaker.should_failover("openrouter/z-ai/glm-4.7")
    assert breaker.should_failover("anthropic/claude-sonnet-4.6")
    breaker.reset()


def test_credit_breaker_closes_after_cooldown(monkeypatch):
    breaker = pytest.importorskip("app.llm_credit_breaker")
    breaker.reset()
    monkeypatch.setattr(breaker, "_alert_operator", lambda p, c: None)

    clock = {"t": 1000.0}
    monkeypatch.setattr(breaker.time, "monotonic", lambda: clock["t"])

    model = "openrouter/z-ai/glm-4.7"
    for _ in range(breaker._THRESHOLD):
        breaker.record_credit_error(model)
    assert breaker.is_open(model)

    clock["t"] += breaker._COOLDOWN_S - 1
    assert breaker.is_open(model), "must stay open until the cooldown elapses"

    clock["t"] += 2
    assert not breaker.is_open(model), "must self-heal without operator action"
    assert breaker.should_failover(model)
    breaker.reset()


def test_credit_breaker_window_expires_old_errors(monkeypatch):
    """Errors spread thinly over hours must not accumulate into a trip."""
    breaker = pytest.importorskip("app.llm_credit_breaker")
    breaker.reset()
    monkeypatch.setattr(breaker, "_alert_operator", lambda p, c: None)

    clock = {"t": 1000.0}
    monkeypatch.setattr(breaker.time, "monotonic", lambda: clock["t"])

    model = "openrouter/z-ai/glm-4.7"
    for _ in range(breaker._THRESHOLD * 3):
        breaker.record_credit_error(model)
        clock["t"] += breaker._WINDOW_S  # each one ages out before the next
        assert not breaker.is_open(model)
    breaker.reset()


def test_credit_breaker_snapshot_is_observable(monkeypatch):
    breaker = pytest.importorskip("app.llm_credit_breaker")
    breaker.reset()
    monkeypatch.setattr(breaker, "_alert_operator", lambda p, c: None)

    for _ in range(breaker._THRESHOLD):
        breaker.record_credit_error("openrouter/z-ai/glm-4.7")
    breaker.should_failover("openrouter/z-ai/glm-4.7")

    snap = breaker.snapshot()
    assert snap["openrouter"]["open"] is True
    assert snap["openrouter"]["errors_in_window"] >= breaker._THRESHOLD
    assert snap["openrouter"]["suppressed_failovers"] == 1
    breaker.reset()


def test_failover_path_absorbs_a_blip_then_stops_once_the_breaker_opens(monkeypatch):
    """End-to-end wiring pin: rate_throttle must consult the breaker.

    Without this the breaker module is inert — which is exactly the state the
    system was in on 2026-07-24, when all 69 credit errors failed over.
    """
    rt = pytest.importorskip("app.rate_throttle")
    breaker = pytest.importorskip("app.llm_credit_breaker")
    breaker.reset()
    monkeypatch.setattr(breaker, "_alert_operator", lambda p, c: None)

    model = "openrouter/anthropic/claude-opus-4.7"
    exc = Exception("litellm.APIError: 402 Insufficient credits")
    calls = []

    monkeypatch.setattr(
        rt, "_select_local_failover_model", lambda m: "ollama/llama3.1:8b",
    )

    def fake_completion(**kwargs):
        calls.append(kwargs.get("model"))
        return "local answer"

    # A blip still degrades gracefully.
    for _ in range(breaker._THRESHOLD - 1):
        assert rt._try_credit_failover_sync(
            exc, model, {"model": model}, fake_completion,
        ) == "local answer"
    assert len(calls) == breaker._THRESHOLD - 1

    # The threshold-th call trips the breaker and must NOT reach the fallback.
    before = len(calls)
    assert rt._try_credit_failover_sync(
        exc, model, {"model": model}, fake_completion,
    ) is None
    assert len(calls) == before, (
        "breaker was open but the call still went to the local model"
    )
    breaker.reset()


def test_provider_of_handles_odd_model_strings():
    breaker = pytest.importorskip("app.llm_credit_breaker")

    assert breaker.provider_of("openrouter/anthropic/claude-opus-4.7") == "openrouter"
    assert breaker.provider_of("ollama/llama3.1:8b") == "ollama"
    assert breaker.provider_of("gpt-5") == "unknown"
    assert breaker.provider_of("") == "unknown"
    assert breaker.provider_of(None) == "unknown"  # type: ignore[arg-type]

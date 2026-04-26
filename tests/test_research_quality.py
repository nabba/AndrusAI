"""Tests for the 2026-04-26 research-quality fix bundle (B + C + D).

Background — task ddb451f8 ("please run extensive research and populate
all missing heads of sales and linkedin profile links") delivered an
incomplete result because:

  * The agent ignored its own MATRIX MODE backstory and used
    delegate_work_to_coworker instead of research_orchestrator
    (the orchestrator's known-hard-field policy + paid-adapter chain
    sat unused).
  * Sub-agents spawned by delegate_work_to_coworker landed on a
    budget-tier model (gemma-4-31b-it) for d=8 research because
    force_tier doesn't propagate through CrewAI delegation.
  * The retry hint was generic boilerplate, discarding vetting's
    structured issues list ("rows 7-12 missing Sales Leader names").

Three fixes ship in one PR:
  Fix B: _try_matrix_research_route — pre-builds orchestrator spec at
         the router and injects a literal tool-call template.
  Fix C: _resolve_difficulty_tier_floor — ContextVar-propagated tier
         floor so sub-agents inherit "research d≥7 → mid+, d≥8 → premium".
  Fix D: _build_retry_task — surgical retry hint from vetting's
         structured issues list.
"""
from __future__ import annotations

import json

import pytest

from tests._v2_shim import install_settings_shim
install_settings_shim()

from app.agents.commander.orchestrator import (
    _try_matrix_research_route,
    _build_retry_task,
    _GENERIC_RETRY_HINT,
)
from app.llm_selector import (
    _resolve_difficulty_tier_floor,
    set_active_difficulty,
    reset_active_difficulty,
    get_active_difficulty,
)


# ══════════════════════════════════════════════════════════════════════
# Fix B — matrix-task router pre-resolution
# ══════════════════════════════════════════════════════════════════════

class TestMatrixResearchRoute:
    """The detector should fire on real PSP-style asks and stay quiet
    on conversational / single-shot prompts."""

    def test_psp_enrich_prompt_fires(self):
        """The exact 2026-04-25 task that triggered this work."""
        prompt = (
            "please run extensive research and populate all missing "
            "heads of sales and linkedin profile links"
        )
        out = _try_matrix_research_route(prompt, attachments=[{"contentType": "text/csv"}])
        assert out is not None, "matrix route should fire on the regression prompt"
        assert len(out) == 1
        decision = out[0]
        assert decision["crew"] == "research"
        assert decision["difficulty"] >= 7
        # Pre-built spec embedded in task body
        assert "research_orchestrator(spec_json=" in decision["task"]
        assert "MATRIX TASK — STRICT ROUTER PRE-RESOLUTION" in decision["task"]
        # The known-hard linkedin field is included (canonical Apollo
        # key after 2026-04-26 alignment).
        assert "head_of_sales_linkedin" in decision["task"]
        # Spec is valid JSON when extracted
        # (we don't strictly parse — just sanity-check it's there)
        assert '"fields"' in decision["task"]

    def test_explicit_count_phrase_fires(self):
        """'find LinkedIn for these 20 companies' should fire."""
        prompt = "find linkedin urls for these 20 companies"
        out = _try_matrix_research_route(prompt)
        assert out is not None
        assert out[0]["crew"] == "research"

    def test_no_verb_no_fire(self):
        """A descriptive sentence without a research verb shouldn't fire."""
        prompt = "the linkedin profile of John Doe is at /in/johndoe"
        assert _try_matrix_research_route(prompt) is None

    def test_no_field_no_entity_no_fire(self):
        """'research the topic' with no field/entity hints — too vague."""
        prompt = "research the latest news"
        assert _try_matrix_research_route(prompt) is None

    def test_single_field_with_attachment_fires(self):
        """User attaches a spreadsheet + asks to enrich — entity list
        is implied by the attachment."""
        prompt = "enrich this list with linkedin urls"
        out = _try_matrix_research_route(prompt, attachments=[{"contentType": "text/csv"}])
        assert out is not None

    def test_conversational_prompt_quiet(self):
        """No false positives on chat-shaped messages."""
        for prompt in [
            "ping",
            "what's the time",
            "thanks!",
            "how are you doing?",
            "tell me a joke",
        ]:
            assert _try_matrix_research_route(prompt) is None, (
                f"matrix route falsely fired on conversational prompt: {prompt!r}"
            )

    def test_extracted_spec_includes_all_mentioned_fields(self):
        """When user mentions multiple field types they should ALL
        appear in the orchestrator spec — using canonical adapter-
        compatible keys."""
        prompt = "find ceo, cto, head of sales, and email for these 12 startups"
        out = _try_matrix_research_route(prompt)
        assert out is not None
        body = out[0]["task"]
        assert "ceo_name" in body
        assert "cto_name" in body
        # Canonical Apollo key — was head_of_sales_name in the original
        # implementation, but that didn't match Apollo's _SUPPORTED_FIELDS
        # so the adapter would silently no-op. Fixed 2026-04-26.
        assert '"head_of_sales"' in body
        assert "sales_email" in body

    def test_known_hard_field_marked(self):
        """Personal-LinkedIn field must include the known_hard flag so
        the orchestrator knows to route through paid adapters when
        keyed."""
        prompt = "find linkedin profile for these 5 ceos"
        out = _try_matrix_research_route(prompt)
        assert out is not None
        body = out[0]["task"]
        assert "known_hard" in body
        assert "Apollo" in body or "Sales Navigator" in body or "Proxycurl" in body


# ══════════════════════════════════════════════════════════════════════
# Fix C — difficulty tier floor + ContextVar propagation
# ══════════════════════════════════════════════════════════════════════

class TestResolveDifficultyTierFloor:
    """The (role, difficulty) → minimum tier policy table."""

    def test_research_d8_premium(self):
        """d≥8 research must require premium tier — that's the
        regression class. Without this, gemma-4-31b-it ($0.40/Mo)
        gets picked when the task needs Sonnet-level persistence."""
        assert _resolve_difficulty_tier_floor("research", 8) == "premium"
        assert _resolve_difficulty_tier_floor("research", 10) == "premium"

    def test_research_d7_mid(self):
        """d=7 research bumps to mid tier (between budget and premium)."""
        assert _resolve_difficulty_tier_floor("research", 7) == "mid"

    def test_research_d6_no_floor(self):
        """At d≤6 we trust the cost-mode default — no floor needed."""
        assert _resolve_difficulty_tier_floor("research", 6) is None
        assert _resolve_difficulty_tier_floor("research", 5) is None
        assert _resolve_difficulty_tier_floor("research", 1) is None

    def test_writing_d9_premium(self):
        """High-d writing also gets a floor — but later than research."""
        assert _resolve_difficulty_tier_floor("writing", 9) == "premium"
        assert _resolve_difficulty_tier_floor("writing", 8) is None

    def test_coding_d9_premium_d7_mid(self):
        assert _resolve_difficulty_tier_floor("coding", 9) == "premium"
        assert _resolve_difficulty_tier_floor("coding", 7) == "mid"

    def test_unmapped_role_no_floor(self):
        """Roles not in the policy table return None (no floor)."""
        assert _resolve_difficulty_tier_floor("media", 10) is None
        assert _resolve_difficulty_tier_floor("desktop", 10) is None

    def test_none_difficulty_no_floor(self):
        """Defensive — caller may not know the difficulty."""
        assert _resolve_difficulty_tier_floor("research", None) is None


class TestActiveDifficultyContextVar:
    """ContextVar lifecycle — set/reset/get must behave correctly so
    sub-agents inherit the parent's difficulty even when CrewAI hops
    coroutine contexts."""

    def test_default_is_none(self):
        # Reset any leftover state from prior tests
        reset_active_difficulty(set_active_difficulty(None))
        assert get_active_difficulty() is None

    def test_set_and_read(self):
        token = set_active_difficulty(8)
        try:
            assert get_active_difficulty() == 8
        finally:
            reset_active_difficulty(token)
        assert get_active_difficulty() is None

    def test_nested_set_reset(self):
        outer_token = set_active_difficulty(5)
        try:
            assert get_active_difficulty() == 5
            inner_token = set_active_difficulty(8)
            try:
                assert get_active_difficulty() == 8
            finally:
                reset_active_difficulty(inner_token)
            assert get_active_difficulty() == 5  # restored to outer
        finally:
            reset_active_difficulty(outer_token)
        assert get_active_difficulty() is None

    def test_propagates_through_thread_pool_via_copy_context(self):
        """ContextVar values are inherited by tasks created with
        copy_context() — the path CrewAI takes for sub-agent calls."""
        import contextvars
        from concurrent.futures import ThreadPoolExecutor

        token = set_active_difficulty(8)
        try:
            ctx = contextvars.copy_context()
            with ThreadPoolExecutor(max_workers=1) as pool:
                # Run get_active_difficulty in a separate thread under
                # the captured context — must see 8.
                future = pool.submit(ctx.run, get_active_difficulty)
                assert future.result() == 8
        finally:
            reset_active_difficulty(token)


# ══════════════════════════════════════════════════════════════════════
# Fix D — surgical retry hints
# ══════════════════════════════════════════════════════════════════════

class TestBuildRetryTask:
    """The retry hint should incorporate vetting's specific issues
    instead of throwing the same generic boilerplate every time."""

    def test_empty_issues_falls_back_to_generic(self):
        """No issues list → preserve legacy generic-hint behavior."""
        task = _build_retry_task("find PSP info", issues=[])
        assert _GENERIC_RETRY_HINT in task
        assert "find PSP info" in task

    def test_issues_appear_as_bullets(self):
        """Each issue gets its own bullet so the retry agent can
        target each gap individually."""
        issues = [
            "rows 7-12 missing Sales Leader names",
            "row 5 LinkedIn URL points to a TheOrg page, not LinkedIn",
            "Paynow sales email is wrong",
        ]
        task = _build_retry_task("enrich PSPs", issues=issues)
        for issue in issues:
            assert issue in task
        # bullet markers
        assert task.count("\n  - ") >= len(issues)

    def test_truncates_to_8_issues_with_overflow_marker(self):
        issues = [f"issue number {i}" for i in range(15)]
        task = _build_retry_task("task", issues=issues)
        # First 8 included
        assert "issue number 0" in task
        assert "issue number 7" in task
        # 8th onward NOT included as bullets
        assert "issue number 8" not in task or "truncated" in task
        # Overflow marker mentions the rest
        assert "7 more" in task or "truncated" in task

    def test_long_issue_string_capped(self):
        """A pathologically long issue shouldn't blow up the prompt."""
        big_issue = "x" * 5000
        task = _build_retry_task("task", issues=[big_issue])
        # Was 5000 chars; we cap each at 300
        assert len(task) < 1000  # still small overall

    def test_wrong_crew_framing_present(self):
        """When the retry path knows the crew was wrong, the framing
        signals that to the receiving (re-routed) crew."""
        task = _build_retry_task(
            "task", issues=["something missing"], wrong_crew=True,
        )
        assert "CREW MISMATCH" in task
        assert "fresh routing decision" in task

    def test_data_quality_framing_default(self):
        task = _build_retry_task(
            "task", issues=["something missing"], wrong_crew=False,
        )
        assert "REJECTED BY QUALITY REVIEW" in task
        assert "address these gaps" in task

    def test_includes_research_orchestrator_hint(self):
        """Retry hint should remind the agent of the matrix tool —
        cheap reinforcement of Fix B."""
        task = _build_retry_task("task", issues=["rows missing"])
        assert "research_orchestrator" in task

    def test_honest_partial_coverage_directive(self):
        """The hint must explicitly tell the agent to mark unverified
        gaps as 'not_found' rather than guess. This is the difference
        between an honest partial table and a confidently-wrong one."""
        task = _build_retry_task("task", issues=["rows missing"])
        assert "not_found" in task or "Honest partial coverage" in task

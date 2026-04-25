"""Tests for the 2026-04-25 routing-authority fixes.

Background — task 1bf80ebd ("please run extensive research and populate
all missing heads of sales and linkedin profile links") had its
research routing silently overridden by Theory of Mind to coding
because at d=8 the coding crew had a "better track record". The
coding crew then dumped a 230-line Python script with
"<unavailable in this environment>" as the "execution output", which
the vetting LLM correctly flagged as not fulfilling the request — but
the retry ran the SAME wrong crew, compounding the failure.

These tests pin in:

  Fix A — Theory of Mind only swaps within the same canonical task type.
          research → coding is forbidden (different task types);
          writing → creative is allowed (same task type).

  Fix B — _vetting_signals_wrong_crew detects code-dominated responses
          from the coding crew and "doesn't fulfill" verdicts so the
          orchestrator re-routes via the commander instead of
          retrying the same wrong specialist.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Bring in the test settings shim so app.config can import without env vars.
from tests._v2_shim import install_settings_shim
install_settings_shim()

from app.agents.commander.orchestrator import _vetting_signals_wrong_crew


# ══════════════════════════════════════════════════════════════════════
# Fix B — wrong-crew detector
# ══════════════════════════════════════════════════════════════════════

class TestVettingSignalsWrongCrew:
    """The retry path uses this to decide between same-crew retry and
    re-route-via-commander. False positives → unnecessary re-routes;
    false negatives → user gets the same broken response again."""

    def test_coding_crew_dumping_script_flagged(self):
        """The 2026-04-25 task 1bf80ebd regression — coding crew dumped
        a Python script with no execution output. Must be detected."""
        response = """
```python
import requests

def find_leader():
    return "x"

if __name__ == "__main__":
    find_leader()
```

```
EXECUTION OUTPUT (stdout):
<unavailable in this environment: no connected execution tool / MCP server to run python and capture real stdout>
```
""" * 3  # make sure code_ratio is > 0.55
        assert _vetting_signals_wrong_crew(response, crew_name="coding") is True

    def test_unavailable_marker_flagged(self):
        """Even when code ratio isn't extreme, an unavailable-execution
        marker is a strong wrong-crew signal."""
        response = (
            "Here's a script:\n\n"
            "```python\nprint('hi')\n```\n\n"
            "EXECUTION OUTPUT: unavailable in this environment"
        )
        assert _vetting_signals_wrong_crew(response, crew_name="coding") is True

    def test_normal_coding_response_not_flagged(self):
        """A real coding crew answer with explanation + small snippet
        should NOT trigger re-routing."""
        response = (
            "The bug is in the loop bound. Change line 42 from `i < n` to "
            "`i <= n` and the off-by-one fixes itself. Test added below.\n\n"
            "```python\nfor i in range(n + 1):\n    print(i)\n```\n\n"
            "Verified: prints 0..n inclusive."
        )
        assert _vetting_signals_wrong_crew(response, crew_name="coding") is False

    def test_research_crew_with_factual_errors_not_flagged(self):
        """Data-quality failures from the research crew should NOT
        trigger re-routing — the right path is same-crew retry."""
        response = (
            "PSPs operating in CEE: Adyen, Stripe, PayU, ...\n"
            "(some rows had wrong LinkedIn URLs)\n"
        )
        assert _vetting_signals_wrong_crew(response, crew_name="research") is False

    def test_doesnt_fulfill_phrase_flagged(self):
        """Vetting verdict echoed in corrected response — re-route."""
        response = (
            "Note: the previous response does not fulfill the user's "
            "request — it contained code instead of research findings."
        )
        assert _vetting_signals_wrong_crew(response, crew_name="coding") is True

    def test_empty_response_not_flagged(self):
        assert _vetting_signals_wrong_crew("", crew_name="coding") is False

    @pytest.mark.parametrize("crew", ["research", "writing", "media", "creative"])
    def test_non_coding_crews_safe_with_short_response(self, crew):
        """The code-dominance check is gated on crew_name=='coding' so
        a writing crew producing structured output isn't false-flagged."""
        response = "## Executive Summary\n\nThe market is...\n\nKey findings:\n1. ..."
        assert _vetting_signals_wrong_crew(response, crew_name=crew) is False


# ══════════════════════════════════════════════════════════════════════
# Fix A — Theory of Mind narrowing
# ══════════════════════════════════════════════════════════════════════
#
# The override path is gated by canonical_task_type equality. We test
# the contract directly via canonical_task_type so the test stays
# stable across orchestrator refactors.

class TestTheoryOfMindTaskTypeGuard:
    """The override should never cross task-type boundaries.

    Same-task-type swaps (writing↔creative↔pim, research↔repo_analysis-
    if-they-shared) remain allowed. Cross-task-type swaps (research→
    coding, writing→coding, coding→research) must be blocked.
    """

    def test_research_and_coding_are_different_task_types(self):
        """The bug — research's canonical type must NOT equal coding's."""
        from app.llm_catalog import canonical_task_type
        assert canonical_task_type(role="research") != canonical_task_type(role="coding"), (
            "research and coding must be different task types so Theory "
            "of Mind doesn't swap them"
        )

    def test_writing_and_coding_are_different_task_types(self):
        """Symmetric guard — writing must not get swapped to coding."""
        from app.llm_catalog import canonical_task_type
        assert canonical_task_type(role="writing") != canonical_task_type(role="coding")

    def test_writing_creative_pim_same_task_type(self):
        """Within-group swaps are OK — these all map to ``writing``."""
        from app.llm_catalog import canonical_task_type
        wt = canonical_task_type(role="writing")
        assert canonical_task_type(role="creative") == wt
        assert canonical_task_type(role="pim") == wt

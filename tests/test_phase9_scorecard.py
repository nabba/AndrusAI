"""
Phase 9 regression tests: Butlin + RSM + SK scorecards.

Every indicator evaluator is expected to return a specific status.
These tests pin the current scorecard state so a future commit that
accidentally regresses a STRONG indicator to PARTIAL (or worse)
fails loudly and visibly.

The targets mirror the honest assessment recorded in PROGRAM.md
Phase 9 and the architectural audit:

  Butlin (14): 6 STRONG + 4 PARTIAL + 4 ABSENT + 0 FAIL
  RSM    (5):  4 STRONG + 1 PARTIAL (spontaneous self-correction)
  SK     (6):  6 STRONG

Phase 9 exit criteria from PROGRAM.md are also asserted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.subia.probes import butlin, rsm, sk
from app.subia.probes.indicator_result import IndicatorResult, Status
from app.subia.probes.scorecard import (
    generate_scorecard_markdown,
    meets_exit_criteria,
    run_everything,
    write_scorecard,
)


# ── Butlin indicator statuses ─────────────────────────────────────

class TestButlinIndividual:
    def _find(self, results, indicator: str) -> IndicatorResult:
        for r in results:
            if r.indicator == indicator:
                return r
        raise AssertionError(f"indicator {indicator} not in results")

    def test_rpt1_absent(self):
        results = butlin.run_all()
        assert self._find(results, "RPT-1").status == Status.ABSENT

    def test_rpt2_partial(self):
        results = butlin.run_all()
        assert self._find(results, "RPT-2").status == Status.PARTIAL

    def test_gwt2_strong(self):
        results = butlin.run_all()
        assert self._find(results, "GWT-2").status == Status.STRONG

    def test_gwt3_strong(self):
        results = butlin.run_all()
        assert self._find(results, "GWT-3").status == Status.STRONG

    def test_gwt4_strong(self):
        results = butlin.run_all()
        assert self._find(results, "GWT-4").status == Status.STRONG

    def test_hot1_absent(self):
        results = butlin.run_all()
        assert self._find(results, "HOT-1").status == Status.ABSENT

    def test_hot2_partial(self):
        results = butlin.run_all()
        assert self._find(results, "HOT-2").status == Status.PARTIAL

    def test_hot3_strong(self):
        results = butlin.run_all()
        assert self._find(results, "HOT-3").status == Status.STRONG

    def test_hot4_absent(self):
        results = butlin.run_all()
        assert self._find(results, "HOT-4").status == Status.ABSENT

    def test_ast1_strong(self):
        results = butlin.run_all()
        assert self._find(results, "AST-1").status == Status.STRONG

    def test_pp1_strong(self):
        results = butlin.run_all()
        assert self._find(results, "PP-1").status == Status.STRONG

    def test_ae2_absent(self):
        results = butlin.run_all()
        assert self._find(results, "AE-2").status == Status.ABSENT


class TestButlinAggregate:
    def test_no_fail_indicators(self):
        """Phase 9 requires ≤ 1 FAIL. We aim for 0."""
        counts = butlin.summary()["by_status"]
        assert counts.get(Status.FAIL.value, 0) == 0

    def test_at_least_six_strong(self):
        counts = butlin.summary()["by_status"]
        assert counts.get(Status.STRONG.value, 0) >= 6

    def test_at_least_four_absent_by_declaration(self):
        """ABSENT is architectural honesty, not failure."""
        counts = butlin.summary()["by_status"]
        assert counts.get(Status.ABSENT.value, 0) >= 4

    def test_fourteen_total(self):
        assert butlin.summary()["total"] == 14

    def test_every_strong_has_mechanism_and_test(self):
        for r in butlin.run_all():
            if r.status == Status.STRONG:
                assert r.mechanism, f"{r.indicator} STRONG but no mechanism"
                assert r.test_file, f"{r.indicator} STRONG but no test_file"
                assert r.tier3_protected, (
                    f"{r.indicator} STRONG but mechanism not Tier-3 "
                    f"protected: {r.mechanism}"
                )

    def test_absent_indicators_have_declarative_notes(self):
        """Every ABSENT indicator must explain WHY — architectural
        honesty, not silent skipping.
        """
        for r in butlin.run_all():
            if r.status == Status.ABSENT:
                assert len(r.notes) > 40, (
                    f"{r.indicator} ABSENT with too-brief notes: "
                    f"{r.notes!r}"
                )


# ── RSM signatures ────────────────────────────────────────────────

class TestRSM:
    def test_five_signatures(self):
        assert rsm.summary()["total"] == 5

    def test_four_strong(self):
        counts = rsm.summary()["by_status"]
        assert counts.get(Status.STRONG.value, 0) >= 4

    def test_no_fail(self):
        counts = rsm.summary()["by_status"]
        assert counts.get(Status.FAIL.value, 0) == 0

    def test_at_least_four_present(self):
        """'Present' means STRONG or PARTIAL — the thing exists
        structurally even if not fully closed.
        """
        counts = rsm.summary()["by_status"]
        present = (counts.get(Status.STRONG.value, 0)
                   + counts.get(Status.PARTIAL.value, 0))
        assert present >= 4

    def test_every_signature_cites_mechanism(self):
        for r in rsm.run_all():
            if r.status != Status.ABSENT:
                assert r.mechanism, (
                    f"{r.indicator} has no mechanism cited"
                )


# ── SK evaluation tests ──────────────────────────────────────────

class TestSK:
    def test_six_tests(self):
        assert sk.summary()["total"] == 6

    def test_at_least_five_pass(self):
        counts = sk.summary()["by_status"]
        assert counts.get(Status.STRONG.value, 0) >= 5

    def test_no_fail(self):
        counts = sk.summary()["by_status"]
        assert counts.get(Status.FAIL.value, 0) == 0

    def test_every_sk_test_cites_mechanism_and_test(self):
        for r in sk.run_all():
            assert r.mechanism, f"{r.indicator} has no mechanism"
            assert r.test_file, f"{r.indicator} has no test_file"


# ── Scorecard aggregator ─────────────────────────────────────────

class TestScorecardAggregator:
    def test_run_everything_has_all_three_suites(self):
        data = run_everything()
        assert "butlin" in data
        assert "rsm" in data
        assert "sk" in data
        assert data["butlin"]["total"] == 14
        assert data["rsm"]["total"] == 5
        assert data["sk"]["total"] == 6

    def test_exit_criteria_met(self):
        met, criteria = meets_exit_criteria()
        assert met, f"Phase 9 exit criteria not met: {criteria}"

    def test_markdown_has_required_sections(self):
        md = generate_scorecard_markdown()
        assert "# AndrusAI Consciousness Scorecard" in md
        assert "## Butlin et al. 2023" in md
        assert "## RSM" in md
        assert "## SK" in md
        assert "## Honest caveats" in md
        assert "## Phase 9 exit criteria" in md

    def test_markdown_replaces_legacy_verdict(self):
        md = generate_scorecard_markdown()
        assert (
            "replaces" in md.lower()
            and "andrusai-sentience-verdict" in md.lower()
        )

    def test_markdown_has_disclaimers(self):
        md = generate_scorecard_markdown()
        lower = md.lower()
        assert "phenomenal" in lower
        assert "disclaim" in lower or "not claim" in lower

    def test_markdown_frontmatter_valid(self):
        md = generate_scorecard_markdown()
        assert md.startswith("---\n")
        # must have auto_generated flag so editors know not to edit
        assert "auto_generated: true" in md

    def test_every_strong_row_has_backticked_paths(self):
        """Machine-readability: `path/to/file.py` should appear for
        every STRONG row.
        """
        md = generate_scorecard_markdown()
        # Count STRONG rows in Butlin table
        strong_lines = [
            l for l in md.splitlines()
            if "| STRONG " in l and "| Butlin" not in l
        ]
        for l in strong_lines:
            # A backticked path is present in the mechanism or test
            # columns when the row is STRONG.
            assert "`app/" in l or "`tests/" in l or "`subia" in l, (
                f"STRONG row missing path: {l}"
            )


# ── File system artefacts ────────────────────────────────────────

class TestScorecardFile:
    def test_write_scorecard_to_tmp(self, tmp_path):
        target = tmp_path / "OUT.md"
        result = write_scorecard(path=target)
        assert result == target
        assert target.exists()
        content = target.read_text()
        assert "# AndrusAI Consciousness Scorecard" in content

    def test_live_scorecard_exists_in_repo(self):
        """After Phase 9 commit, the committed SCORECARD.md must exist."""
        path = (
            Path(__file__).resolve().parents[1]
            / "app" / "subia" / "probes" / "SCORECARD.md"
        )
        assert path.exists(), (
            f"SCORECARD.md missing at {path}. Regenerate with "
            f"write_scorecard()."
        )

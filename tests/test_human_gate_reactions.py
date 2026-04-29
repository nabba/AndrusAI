"""Tests for the 👍/👎 reaction → human_gate approval bridge.

The audit found that approving a borderline mutation by reacting 👍 in
Signal silently failed: feedback_pipeline logged the reaction as positive
sentiment but never called human_gate.approve_request. This file verifies:

  1. _send_approval_notification persists the Signal timestamp on the queue entry
  2. find_request_by_signal_timestamp returns the matching pending request
  3. find_request_by_signal_timestamp returns None for stale / missing timestamps
  4. The two new architectural-review hard rejects (path duplication, new-file
     overlap) fire on the kind of mutation that slipped through previously.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.test_metrics import _FakeSettings  # noqa: E402
import app.config as config_mod  # noqa: E402

config_mod.get_settings = lambda: _FakeSettings()
config_mod.get_anthropic_api_key = lambda: "fake-key"
config_mod.get_gateway_secret = lambda: "a" * 64


# ─────────────────────────────────────────────────────────────────────────────
# Reaction → human_gate timestamp lookup
# ─────────────────────────────────────────────────────────────────────────────

class TestHumanGateReactionLookup:
    def test_set_and_find_signal_timestamp(self, tmp_path, monkeypatch):
        """A request whose signal_timestamp is set is findable by that timestamp."""
        import app.human_gate as hg
        monkeypatch.setattr(hg, "APPROVAL_QUEUE_PATH", tmp_path / "queue.json")
        monkeypatch.setattr(hg, "APPROVAL_HISTORY_PATH", tmp_path / "history.json")

        with patch("app.human_gate._send_approval_notification"):
            req_id = hg.request_approval(
                experiment_id="exp_lookup",
                hypothesis="test",
                change_type="code",
                files={"app/foo.py": "code"},
                delta=0.02,
            )

        # Persist a timestamp on the entry — production sets this from
        # send_message_blocking after the notification is delivered.
        hg._set_signal_timestamp(req_id, signal_ts=1700000000123)
        assert hg.find_request_by_signal_timestamp(1700000000123) == req_id

    def test_unknown_timestamp_returns_none(self, tmp_path, monkeypatch):
        import app.human_gate as hg
        monkeypatch.setattr(hg, "APPROVAL_QUEUE_PATH", tmp_path / "queue.json")
        monkeypatch.setattr(hg, "APPROVAL_HISTORY_PATH", tmp_path / "history.json")
        assert hg.find_request_by_signal_timestamp(999) is None

    def test_decided_request_no_longer_found(self, tmp_path, monkeypatch):
        """Once a request is approved or rejected it leaves the pending queue."""
        import app.human_gate as hg
        monkeypatch.setattr(hg, "APPROVAL_QUEUE_PATH", tmp_path / "queue.json")
        monkeypatch.setattr(hg, "APPROVAL_HISTORY_PATH", tmp_path / "history.json")

        with patch("app.human_gate._send_approval_notification"):
            req_id = hg.request_approval(
                experiment_id="exp_decided",
                hypothesis="test",
                change_type="code",
                files={"app/foo.py": "code"},
                delta=0.02,
            )
        hg._set_signal_timestamp(req_id, signal_ts=42)
        assert hg.find_request_by_signal_timestamp(42) == req_id

        hg.reject_request(req_id, approver="test", reason="not now")
        # After rejection the request is in history, not the pending queue.
        assert hg.find_request_by_signal_timestamp(42) is None

    def test_zero_timestamp_returns_none(self, tmp_path, monkeypatch):
        """signal_ts=0 is the sentinel for 'no timestamp' — must not match."""
        import app.human_gate as hg
        monkeypatch.setattr(hg, "APPROVAL_QUEUE_PATH", tmp_path / "queue.json")
        monkeypatch.setattr(hg, "APPROVAL_HISTORY_PATH", tmp_path / "history.json")

        with patch("app.human_gate._send_approval_notification"):
            hg.request_approval(
                experiment_id="exp_zero",
                hypothesis="test",
                change_type="code",
                files={"app/foo.py": "code"},
                delta=0.02,
            )
        # No _set_signal_timestamp call — entry has no signal_timestamp field.
        assert hg.find_request_by_signal_timestamp(0) is None


# ─────────────────────────────────────────────────────────────────────────────
# Architectural review hard rejects (path duplication + new-file overlap)
# ─────────────────────────────────────────────────────────────────────────────

class TestPathDuplicationDetection:
    def test_basename_collides_with_existing_directory(self):
        """The exact case from exp_202604290007_1172 — must HARD reject."""
        from app.architectural_review import _detect_path_duplications

        existing = {
            "app/agents/commander/__init__.py",
            "app/agents/commander/orchestrator.py",
            "app/agents/commander/routing.py",
        }
        # Proposed new file shares its basename with the existing directory.
        proposed = {"app/orch/commander.py": "code"}
        findings = _detect_path_duplications(proposed, existing)
        assert len(findings) == 1
        assert findings[0].new_file == "app/orch/commander.py"
        assert "commander" in findings[0].existing_path

    def test_basename_collides_with_existing_module(self):
        from app.architectural_review import _detect_path_duplications
        existing = {"app/crews/coding.py"}
        proposed = {"app/agents/coding.py": "code"}
        findings = _detect_path_duplications(proposed, existing)
        assert len(findings) == 1
        assert findings[0].existing_path == "app/crews/coding.py"

    def test_modifying_existing_file_does_not_trigger(self):
        """Existing file being modified — not a new duplication."""
        from app.architectural_review import _detect_path_duplications
        existing = {"app/foo/bar.py"}
        proposed = {"app/foo/bar.py": "modified"}
        findings = _detect_path_duplications(proposed, existing)
        assert findings == []

    def test_exempt_basenames_are_allowed(self):
        """Files like utils.py legitimately repeat across packages."""
        from app.architectural_review import _detect_path_duplications
        existing = {"app/agents/utils.py", "app/foo/__init__.py"}
        proposed = {"app/crews/utils.py": "code"}
        findings = _detect_path_duplications(proposed, existing)
        assert findings == []

    def test_unrelated_new_file_is_clean(self):
        from app.architectural_review import _detect_path_duplications
        existing = {"app/agents/commander/__init__.py"}
        proposed = {"app/agents/translator.py": "code"}
        findings = _detect_path_duplications(proposed, existing)
        assert findings == []


class TestHardRejectAggregation:
    def test_path_duplication_is_hard_reject(self):
        from app.architectural_review import (
            ReviewReport, PathDuplicationFinding,
        )
        report = ReviewReport(
            path_duplications=(PathDuplicationFinding(
                new_file="app/orch/commander.py",
                existing_path="commander/",
                reason="basename matches existing directory",
            ),),
        )
        assert report.has_hard_rejects

    def test_new_file_overlap_with_three_owners_is_hard_reject(self):
        from app.architectural_review import ReviewReport, OverlapFinding
        report = ReviewReport(
            overlaps=(OverlapFinding(
                filepath="app/agents/coding.py",
                capability="agent",
                existing_owners=("a.py", "b.py", "c.py", "d.py"),
                is_new_file=True,
            ),),
        )
        assert report.has_hard_rejects
        assert len(report.hard_overlaps) == 1

    def test_existing_file_overlap_stays_soft(self):
        """Modifying an existing file never escalates overlap to hard reject."""
        from app.architectural_review import ReviewReport, OverlapFinding
        report = ReviewReport(
            overlaps=(OverlapFinding(
                filepath="app/agents/researcher.py",
                capability="agent",
                existing_owners=("a.py", "b.py", "c.py"),
                is_new_file=False,
            ),),
        )
        assert not report.has_hard_rejects
        assert report.has_soft_warnings

    def test_two_owners_stays_soft(self):
        """Below the hard threshold → soft warning."""
        from app.architectural_review import ReviewReport, OverlapFinding
        report = ReviewReport(
            overlaps=(OverlapFinding(
                filepath="app/agents/new.py",
                capability="agent",
                existing_owners=("a.py", "b.py"),
                is_new_file=True,
            ),),
        )
        assert not report.has_hard_rejects
        assert report.has_soft_warnings

    def test_summary_lists_path_duplications(self):
        from app.architectural_review import (
            ReviewReport, PathDuplicationFinding,
        )
        report = ReviewReport(
            path_duplications=(PathDuplicationFinding(
                new_file="app/orch/commander.py",
                existing_path="commander/",
                reason="duplicates existing directory",
            ),),
        )
        summary = report.summary()
        assert "duplication" in summary.lower()
        assert "commander" in summary

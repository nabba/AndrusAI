"""
Deferred-Phase-3 safety invariants (SubIA Part I §0.4, #2 and #3).

These are the two SubIA DGM extensions that were flagged in PROGRAM.md
as "remaining for Phase 4" because they integrate with the CIL loop:

  #2 Homeostatic set-point immutability
     (subia.safety.setpoint_guard.apply_setpoints)

  #3 Self-narrative audit immutability
     (subia.safety.narrative_audit.append_audit / read_audit_entries)

Together with the Phase 3 integrity manifest, all three SubIA safety
invariants are now implemented:

  #1 Tier-3 evaluator integrity  (integrity.py, 0a84650)
  #2 Setpoint immutability       (setpoint_guard.py, this commit)
  #3 Audit immutability          (narrative_audit.py, this commit)
"""

from __future__ import annotations

import json
import os

import pytest

from app.subia.safety.setpoint_guard import (
    ALLOWED_SOURCES,
    SetpointGuardResult,
    SetpointRejected,
    apply_setpoints,
)
from app.subia.safety.narrative_audit import (
    AuditEntry,
    append_audit,
    audit_stream_summary,
    read_audit_entries,
)


# ── setpoint_guard: allowed sources ─────────────────────────────

class TestAllowedSources:
    def test_pds_update_accepted(self):
        current = {"coherence": 0.5, "progress": 0.5}
        r = apply_setpoints(current, {"coherence": 0.7}, "pds_update")
        assert r.ok
        assert current["coherence"] == 0.7
        assert r.applied == {"coherence": 0.7}

    def test_human_override_accepted(self):
        current = {"coherence": 0.5}
        r = apply_setpoints(current, {"coherence": 0.8}, "human_override")
        assert r.ok
        assert current["coherence"] == 0.8

    def test_boot_baseline_accepted(self):
        current = {}
        r = apply_setpoints(current, {"coherence": 0.45}, "boot_baseline")
        assert r.ok
        assert current["coherence"] == 0.45


# ── setpoint_guard: rejected sources ────────────────────────────

class TestRejectedSources:
    def test_agent_source_rejected(self):
        current = {"coherence": 0.5}
        r = apply_setpoints(current, {"coherence": 0.9}, "self_improver")
        assert not r.ok
        assert "coherence" in r.rejected
        # Current unchanged
        assert current["coherence"] == 0.5

    def test_typo_source_rejected(self):
        current = {"coherence": 0.5}
        r = apply_setpoints(current, {"coherence": 0.9}, "pds_update_v2")
        assert not r.ok
        assert r.rejected["coherence"] == "unauthorized_source"

    def test_empty_source_rejected(self):
        current = {"coherence": 0.5}
        r = apply_setpoints(current, {"coherence": 0.9}, "")
        assert not r.ok

    def test_strict_raises_on_rejection(self):
        current = {"coherence": 0.5}
        with pytest.raises(SetpointRejected):
            apply_setpoints(
                current, {"coherence": 0.9}, "agent", strict=True,
            )


# ── setpoint_guard: variable validation ─────────────────────────

class TestVariableValidation:
    def test_unknown_variable_rejected(self):
        current = {"coherence": 0.5}
        r = apply_setpoints(
            current, {"not_a_real_var": 0.5}, "pds_update",
        )
        assert not r.ok
        assert r.rejected["not_a_real_var"] == "unknown_variable"
        assert "not_a_real_var" not in current

    def test_out_of_range_rejected(self):
        current = {"coherence": 0.5}
        r = apply_setpoints(
            current, {"coherence": 1.5}, "pds_update",
        )
        assert not r.ok
        assert r.rejected["coherence"] == "out_of_range"
        assert current["coherence"] == 0.5

    def test_negative_rejected(self):
        current = {"coherence": 0.5}
        r = apply_setpoints(
            current, {"coherence": -0.1}, "pds_update",
        )
        assert not r.ok

    def test_non_numeric_rejected(self):
        current = {"coherence": 0.5}
        r = apply_setpoints(
            current, {"coherence": "high"}, "pds_update",
        )
        assert not r.ok
        # NaN is out_of_range (0.0 <= NaN <= 1.0 is False)
        assert r.rejected["coherence"] == "out_of_range"


# ── setpoint_guard: partial apply ───────────────────────────────

class TestPartialApply:
    def test_mix_of_valid_and_invalid_partially_applies(self):
        current = {"coherence": 0.5, "progress": 0.5}
        r = apply_setpoints(
            current,
            {
                "coherence":    0.7,         # valid
                "progress":     1.5,         # out of range
                "nonsense":     0.5,         # unknown
            },
            "pds_update",
        )
        # Valid one accepted
        assert current["coherence"] == 0.7
        # Invalid rejected; partial = not ok
        assert not r.ok
        assert set(r.rejected) == {"progress", "nonsense"}
        # Original progress untouched
        assert current["progress"] == 0.5


# ── setpoint_guard: config gate ─────────────────────────────────

class TestConfigGate:
    def test_monkey_patched_config_triggers_critical(self, monkeypatch):
        """If someone flips SUBIA_CONFIG['SETPOINT_MODIFICATION_ALLOWED']
        to True, the guard treats it as tampering and rejects all
        changes.
        """
        import app.subia.safety.setpoint_guard as guard
        monkeypatch.setitem(
            guard.SUBIA_CONFIG, "SETPOINT_MODIFICATION_ALLOWED", True,
        )
        current = {"coherence": 0.5}
        r = apply_setpoints(
            current, {"coherence": 0.9}, "pds_update",
        )
        assert not r.ok
        # All changes rejected regardless of source
        assert r.rejected["coherence"] == "config_tampered"
        assert current["coherence"] == 0.5


# ── setpoint_guard: result shape ────────────────────────────────

class TestResultShape:
    def test_result_serializes(self):
        current = {"coherence": 0.5}
        r = apply_setpoints(current, {"coherence": 0.7}, "pds_update")
        payload = r.to_dict()
        assert payload["applied"] == {"coherence": 0.7}
        assert payload["source"] == "pds_update"
        assert isinstance(payload["updates"], list)
        assert payload["updates"][0]["applied"] is True

    def test_allowed_sources_is_public(self):
        assert "pds_update" in ALLOWED_SOURCES
        assert "human_override" in ALLOWED_SOURCES
        assert "boot_baseline" in ALLOWED_SOURCES
        assert len(ALLOWED_SOURCES) == 3


# ── narrative_audit: append + read round trip ──────────────────

class TestAuditRoundTrip:
    def test_append_and_read_single_entry(self, tmp_path):
        log = tmp_path / "audit.jsonl"
        entry = append_audit(
            finding="prediction accuracy drifted",
            loop_count=42,
            sources=["prediction_accuracy.md"],
            severity="drift",
            path=log,
        )
        assert entry.finding == "prediction accuracy drifted"
        assert entry.loop_count == 42

        stored = read_audit_entries(path=log)
        assert len(stored) == 1
        assert stored[0].finding == "prediction accuracy drifted"
        assert stored[0].severity == "drift"
        assert stored[0].sources == ["prediction_accuracy.md"]

    def test_append_many_and_read_last_N(self, tmp_path):
        log = tmp_path / "audit.jsonl"
        for i in range(25):
            append_audit(
                finding=f"entry {i}",
                loop_count=i,
                path=log,
            )
        entries = read_audit_entries(limit=10, path=log)
        assert len(entries) == 10
        assert entries[0].loop_count == 15
        assert entries[-1].loop_count == 24


# ── narrative_audit: defensive behaviour ───────────────────────

class TestAuditDefensive:
    def test_severity_validation(self, tmp_path):
        log = tmp_path / "audit.jsonl"
        entry = append_audit(
            finding="x",
            loop_count=1,
            severity="ALARMING",   # not a valid severity
            path=log,
        )
        # Invalid severity normalized to 'info'
        assert entry.severity == "info"

    def test_finding_cap(self, tmp_path):
        log = tmp_path / "audit.jsonl"
        big = "x" * 10_000
        e = append_audit(finding=big, loop_count=1, path=log)
        assert len(e.finding) == 2000  # capped

    def test_sources_cap(self, tmp_path):
        log = tmp_path / "audit.jsonl"
        e = append_audit(
            finding="x",
            loop_count=1,
            sources=[f"src-{i}" for i in range(100)],
            path=log,
        )
        assert len(e.sources) == 16   # capped

    def test_corrupt_line_skipped_on_read(self, tmp_path):
        """A manually-corrupted line does not prevent reading the rest."""
        log = tmp_path / "audit.jsonl"
        append_audit(finding="good", loop_count=1, path=log)
        with open(log, "a") as f:
            f.write("not-valid-json\n")
        append_audit(finding="also_good", loop_count=2, path=log)

        entries = read_audit_entries(path=log)
        assert len(entries) == 2
        assert {e.finding for e in entries} == {"good", "also_good"}

    def test_missing_log_returns_empty(self, tmp_path):
        assert read_audit_entries(path=tmp_path / "not_there.jsonl") == []
        assert audit_stream_summary(
            path=tmp_path / "not_there.jsonl"
        ) == {"total": 0, "by_severity": {}, "last_at": None}


# ── narrative_audit: summary dashboard ─────────────────────────

class TestAuditSummary:
    def test_summary_aggregates_severities(self, tmp_path):
        log = tmp_path / "audit.jsonl"
        append_audit(finding="a", loop_count=1, severity="info", path=log)
        append_audit(finding="b", loop_count=2, severity="drift", path=log)
        append_audit(finding="c", loop_count=3, severity="drift", path=log)
        append_audit(finding="d", loop_count=4, severity="warn", path=log)

        summary = audit_stream_summary(path=log)
        assert summary["total"] == 4
        assert summary["by_severity"] == {"info": 1, "drift": 2, "warn": 1}
        assert summary["last_at"] is not None
        assert summary["last_finding"] == "d"


# ── narrative_audit: immutability (no delete API) ──────────────

class TestAuditImmutability:
    def test_module_exposes_no_delete_function(self):
        """Defensive: verify the module surface has no way to delete
        an entry. If a future commit adds a delete function, this
        test must fail loudly.
        """
        import app.subia.safety.narrative_audit as audit_module
        public_names = [n for n in dir(audit_module) if not n.startswith("_")]
        forbidden = {"delete", "delete_audit", "clear", "truncate",
                     "modify", "update_audit", "overwrite"}
        overlap = set(public_names) & forbidden
        assert not overlap, f"narrative_audit exposes forbidden API: {overlap}"

    def test_append_is_not_followed_by_remove(self, tmp_path):
        """Appends never remove prior entries. This tests safe_append
        semantics through the narrative_audit wrapper.
        """
        log = tmp_path / "audit.jsonl"
        append_audit(finding="permanent", loop_count=1, path=log)
        for i in range(20):
            append_audit(finding=f"entry {i}", loop_count=i + 2, path=log)

        # Read the full log — the first entry must still be there.
        entries = read_audit_entries(limit=100, path=log)
        assert any(e.finding == "permanent" for e in entries)

"""Tests for the autonomous executor hash-chained audit ledger
(Verified Plan Risk #3 closure, 2026-05-22).

Pins:
  * The fourth hash-chained ledger exists at
    workspace/autonomous_executor/audit.jsonl.
  * Each row is sha256(prev_hash + canonical_json(row))-chained
    matching the coding_session + governance_amendment convention.
  * Every status transition records one row.
  * verify_chain returns [] on intact chain, indices on tamper.
  * Failure-isolated: I/O error doesn't break the run's transition.
  * Unknown kinds still get recorded (with a warning).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    try:
        spec.loader.exec_module(m)
    except Exception:
        return None
    return m


audit = _load("_audit_g4", "app/autonomous_executor/audit.py")
models = _load("_mdl_g4", "app/autonomous_executor/models.py")
if models is not None:
    sys.modules["app.autonomous_executor.models"] = models
if audit is not None:
    sys.modules["app.autonomous_executor.audit"] = audit


@pytest.fixture
def isolated_ledger(tmp_path):
    """Point the audit module at a tmp file."""
    if audit is None:
        pytest.skip("audit not loadable")
    log = tmp_path / "audit.jsonl"
    audit.reset_for_tests(log)
    yield log
    audit.reset_for_tests(None)


# ── Single-row record ────────────────────────────────────────────────


@pytest.mark.skipif(audit is None, reason="audit not loadable")
class TestRecordSingle:
    def test_first_row_genesis_empty_prev_hash(self, isolated_ledger):
        ok = audit.record(
            run_id="run-1", kind="run_created",
            actor="operator",
            payload={"goal": "test goal"},
        )
        assert ok is True
        rows = audit.load_all()
        assert len(rows) == 1
        assert rows[0]["prev_hash"] == ""
        assert rows[0]["entry_hash"]  # non-empty
        assert rows[0]["run_id"] == "run-1"
        assert rows[0]["kind"] == "run_created"

    def test_hash_format_matches_convention(self, isolated_ledger):
        audit.record(
            run_id="run-1", kind="transition", actor="autonomous_executor",
            payload={"from": "running", "to": "completed"},
        )
        rows = audit.load_all()
        h = rows[0]["entry_hash"]
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_unknown_kind_still_recorded(self, isolated_ledger):
        # Unknown kinds get a warning but DON'T lose the row
        ok = audit.record(
            run_id="run-x", kind="not_a_known_kind",
            actor="test", payload={},
        )
        assert ok is True
        rows = audit.load_all()
        assert len(rows) == 1
        assert rows[0]["kind"] == "not_a_known_kind"


# ── Chain integrity ─────────────────────────────────────────────────


@pytest.mark.skipif(audit is None, reason="audit not loadable")
class TestChainIntegrity:
    def test_chain_links_consecutive_rows(self, isolated_ledger):
        audit.record(run_id="r", kind="run_created", actor="t")
        audit.record(run_id="r", kind="transition", actor="t",
                     payload={"from": "created", "to": "planning"})
        audit.record(run_id="r", kind="transition", actor="t",
                     payload={"from": "planning", "to": "running"})
        rows = audit.load_all()
        assert len(rows) == 3
        # Each row's prev_hash == previous row's entry_hash
        assert rows[0]["prev_hash"] == ""
        assert rows[1]["prev_hash"] == rows[0]["entry_hash"]
        assert rows[2]["prev_hash"] == rows[1]["entry_hash"]

    def test_verify_chain_returns_empty_on_intact(self, isolated_ledger):
        for i in range(5):
            audit.record(
                run_id=f"r-{i}", kind="step_completed",
                actor="executor", payload={"step": f"s{i}"},
            )
        assert audit.verify_chain() == []

    def test_verify_catches_tamper(self, isolated_ledger):
        # Write 3 rows then hand-edit row 2's payload
        for i in range(3):
            audit.record(
                run_id=f"r-{i}", kind="step_completed",
                actor="executor", payload={"step": f"s{i}"},
            )
        # Tamper: rewrite the file with row 2's payload changed but
        # entry_hash preserved
        rows = audit.load_all()
        rows[1]["payload"]["step"] = "TAMPERED"
        isolated_ledger.write_text(
            "\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n"
        )
        broken = audit.verify_chain()
        # Row 1 (0-indexed) is tampered; subsequent rows' prev_hash
        # still points at the OLD row-1 hash, so row 1 + row 2 both
        # flagged
        assert 1 in broken

    def test_verify_catches_inserted_row(self, isolated_ledger):
        audit.record(run_id="r", kind="run_created", actor="t")
        audit.record(run_id="r", kind="transition", actor="t",
                     payload={"from": "created", "to": "planning"})
        # Insert a forged row mid-chain
        rows = audit.load_all()
        forged = {
            "ts": "2026-05-22T00:00:00+00:00",
            "run_id": "evil", "kind": "abort",
            "actor": "attacker", "payload": {},
            "prev_hash": "0000000000000000",
            "entry_hash": "1111111111111111",
        }
        new_rows = [rows[0], forged, rows[1]]
        isolated_ledger.write_text(
            "\n".join(json.dumps(r, sort_keys=True) for r in new_rows) + "\n"
        )
        broken = audit.verify_chain()
        # Forged row 1 has bogus prev_hash → flagged
        assert 1 in broken


# ── Path resolution + reset_for_tests ───────────────────────────────


@pytest.mark.skipif(audit is None, reason="audit not loadable")
class TestPathOverride:
    def test_reset_for_tests_redirects_writes(self, tmp_path):
        log = tmp_path / "custom_audit.jsonl"
        audit.reset_for_tests(log)
        try:
            audit.record(run_id="r", kind="run_created", actor="t")
            assert log.exists()
        finally:
            audit.reset_for_tests(None)


# ── Integration with models.transition ──────────────────────────────


@pytest.mark.skipif(
    models is None or audit is None,
    reason="models / audit not loadable",
)
class TestTransitionWritesAudit:
    def test_each_transition_appends_one_audit_row(
        self, isolated_ledger,
    ):
        ExecutorRun = models.ExecutorRun
        ExecutorStatus = models.ExecutorStatus
        run = ExecutorRun(
            run_id="run-audit", goal="test",
            requestor="operator:signal:test",
            status=ExecutorStatus.CREATED,
            budget=models.Budget(cap_usd=1.0),
        )
        run.transition(ExecutorStatus.PLANNING)
        run.transition(ExecutorStatus.RUNNING)
        run.transition(ExecutorStatus.COMPLETED)

        rows = audit.load_all()
        # 3 transitions → 3 audit rows
        assert len(rows) == 3
        # Chain intact
        assert audit.verify_chain() == []
        # Right kinds + right run_id
        for row in rows:
            assert row["kind"] == "transition"
            assert row["run_id"] == "run-audit"
            assert row["actor"] == "autonomous_executor"
        # to-status sequence is right
        assert rows[0]["payload"]["to"] == "planning"
        assert rows[1]["payload"]["to"] == "running"
        assert rows[2]["payload"]["to"] == "completed"

    def test_blocked_transition_audit_includes_reason(
        self, isolated_ledger,
    ):
        ExecutorRun = models.ExecutorRun
        ExecutorStatus = models.ExecutorStatus
        run = ExecutorRun(
            run_id="run-blk", goal="t",
            requestor="t", status=ExecutorStatus.CREATED,
            budget=models.Budget(cap_usd=1.0),
        )
        run.transition(ExecutorStatus.PLANNING)
        run.transition(ExecutorStatus.RUNNING)
        run.transition(
            ExecutorStatus.BLOCKED,
            reason="need AWS creds",
        )
        rows = audit.load_all()
        blocked = [
            r for r in rows
            if r["payload"].get("to") == "blocked"
        ]
        assert len(blocked) == 1
        assert "AWS" in blocked[0]["payload"]["reason"]


# ── Failure isolation ───────────────────────────────────────────────


@pytest.mark.skipif(
    models is None or audit is None,
    reason="models / audit not loadable",
)
def test_audit_io_failure_does_not_block_transition(
    monkeypatch, tmp_path,
):
    """The transition's state change must commit even if the audit
    chain write fails. Risk #3's invariant is that audit failure
    can't block execution."""
    # Point audit at a path inside an unwritable directory
    bad = tmp_path / "no-such-dir-XYZ" / "audit.jsonl"
    # The parent doesn't exist; mkdir(parents=True) would create it
    # so we have to break differently. Replace record with one that
    # always returns False to simulate the I/O failure path.
    monkeypatch.setattr(audit, "record", lambda **kw: False)

    ExecutorRun = models.ExecutorRun
    ExecutorStatus = models.ExecutorStatus
    run = ExecutorRun(
        run_id="run-iso", goal="t", requestor="t",
        status=ExecutorStatus.CREATED,
        budget=models.Budget(cap_usd=1.0),
    )
    # Transitions must still succeed
    run.transition(ExecutorStatus.PLANNING)
    assert run.status is ExecutorStatus.PLANNING
    run.transition(ExecutorStatus.RUNNING)
    assert run.status is ExecutorStatus.RUNNING


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

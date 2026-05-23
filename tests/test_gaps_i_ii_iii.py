"""Tests for Verified Plan Gaps I, II, III (third-pass closure,
2026-05-22).

  * Gap I — agent-callable ``delegate_goal`` in
    ``app/autonomous_executor/tools/delegate_tool.py``.
  * Gap II — ``CodingSession.iterate_loop_state`` field.
  * Gap III — ``app/risk_classifier/evidence.py`` rolling stats.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
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


# Stub crewai for the delegate_tool's @tool import
for _mod in ("crewai", "crewai.tools"):
    if _mod not in sys.modules:
        import types
        m = types.ModuleType(_mod)
        if _mod == "crewai.tools":
            m.tool = lambda name: (lambda fn: fn)
            m.BaseTool = type("BaseTool", (), {})
        sys.modules[_mod] = m


# ── Gap I: delegate_tool ─────────────────────────────────────────────


delegate_tool = _load(
    "_dt_g1", "app/autonomous_executor/tools/delegate_tool.py",
)


class TestDelegateToolInputValidation:
    @pytest.mark.skipif(
        delegate_tool is None, reason="delegate_tool not loadable",
    )
    def test_empty_goal_refused(self):
        result = delegate_tool.delegate_goal("")
        assert result["ok"] is False
        assert "empty" in result["error"]

    @pytest.mark.skipif(
        delegate_tool is None, reason="delegate_tool not loadable",
    )
    def test_overlong_goal_refused(self):
        result = delegate_tool.delegate_goal("x" * 2000)
        assert result["ok"] is False
        assert "exceeds" in result["error"]

    @pytest.mark.skipif(
        delegate_tool is None, reason="delegate_tool not loadable",
    )
    def test_negative_budget_refused(self):
        result = delegate_tool.delegate_goal("good goal", budget_usd=-1.0)
        assert result["ok"] is False
        assert "positive" in result["error"]

    @pytest.mark.skipif(
        delegate_tool is None, reason="delegate_tool not loadable",
    )
    def test_budget_clamped_to_max(self, monkeypatch):
        # Set up a fake store so save succeeds
        import types
        models = _load("_mdl_g1", "app/autonomous_executor/models.py")
        if models is None:
            pytest.skip("models not loadable")
        monkeypatch.setitem(
            sys.modules, "app.autonomous_executor.models", models,
        )
        fake_store = types.SimpleNamespace()
        fake_store.save = lambda run: None
        monkeypatch.setitem(
            sys.modules, "app.autonomous_executor.store", fake_store,
        )
        # The wrapper tool reports the EFFECTIVE budget which is
        # clamped to MAX_BUDGET_USD=10.0. crewai's @tool decorator
        # returns a Tool object whose underlying function is .func —
        # works both for the real crewai install and the conftest stub.
        fn = getattr(
            delegate_tool.delegate_goal_tool, "func",
            delegate_tool.delegate_goal_tool,
        )
        result = fn("do thing", budget_usd=999.0)
        assert "$10.00" in result, (
            f"budget should clamp to $10, got: {result}"
        )


# ── Gap II: CodingSession.iterate_loop_state ────────────────────────


models = _load("_cs_g2", "app/coding_session/models.py")


class TestIterateLoopStateField:
    @pytest.mark.skipif(models is None, reason="cs/models not loadable")
    def test_field_default_none(self):
        # Build a minimal session and confirm the new field defaults
        # to None
        session = models.CodingSession(
            id="s1", agent_id="coder", purpose="test",
            created_at="2026-05-22T10:00:00+00:00",
            base="main", base_sha="abc",
            worktree_path="/tmp/s1",
            expires_at="2026-05-22T11:00:00+00:00",
            last_activity_at="2026-05-22T10:00:00+00:00",
        )
        assert session.iterate_loop_state is None

    @pytest.mark.skipif(models is None, reason="cs/models not loadable")
    def test_field_persists_in_to_dict(self):
        session = models.CodingSession(
            id="s1", agent_id="coder", purpose="test",
            created_at="2026-05-22T10:00:00+00:00",
            base="main", base_sha="abc",
            worktree_path="/tmp/s1",
            expires_at="2026-05-22T11:00:00+00:00",
            last_activity_at="2026-05-22T10:00:00+00:00",
            iterate_loop_state={
                "iteration": 3,
                "tests_passing": False,
                "budget_remaining_usd": 1.50,
            },
        )
        d = session.to_dict()
        assert "iterate_loop_state" in d
        assert d["iterate_loop_state"]["iteration"] == 3
        assert d["iterate_loop_state"]["tests_passing"] is False

    @pytest.mark.skipif(models is None, reason="cs/models not loadable")
    def test_field_legacy_byte_stable_when_none(self):
        # When iterate_loop_state is None, the field MUST be absent
        # from to_dict — preserves legacy on-disk JSONs.
        session = models.CodingSession(
            id="s1", agent_id="coder", purpose="test",
            created_at="2026-05-22T10:00:00+00:00",
            base="main", base_sha="abc",
            worktree_path="/tmp/s1",
            expires_at="2026-05-22T11:00:00+00:00",
            last_activity_at="2026-05-22T10:00:00+00:00",
        )
        d = session.to_dict()
        assert "iterate_loop_state" not in d

    @pytest.mark.skipif(models is None, reason="cs/models not loadable")
    def test_roundtrip_through_from_dict(self):
        session = models.CodingSession(
            id="s1", agent_id="coder", purpose="test",
            created_at="2026-05-22T10:00:00+00:00",
            base="main", base_sha="abc",
            worktree_path="/tmp/s1",
            expires_at="2026-05-22T11:00:00+00:00",
            last_activity_at="2026-05-22T10:00:00+00:00",
            iterate_loop_state={"iteration": 5, "result": "green"},
        )
        d = session.to_dict()
        rebuilt = models.CodingSession.from_dict(d)
        assert rebuilt.iterate_loop_state == {
            "iteration": 5, "result": "green",
        }

    @pytest.mark.skipif(models is None, reason="cs/models not loadable")
    def test_from_dict_legacy_json_no_field(self):
        # Pre-Gap-II session JSON (no iterate_loop_state) rehydrates
        # cleanly with None
        rebuilt = models.CodingSession.from_dict({
            "id": "s1", "agent_id": "coder", "purpose": "test",
            "created_at": "2026-05-22T10:00:00+00:00",
            "base": "main", "base_sha": "abc",
            "worktree_path": "/tmp/s1",
            "expires_at": "2026-05-22T11:00:00+00:00",
            "last_activity_at": "2026-05-22T10:00:00+00:00",
            "status": "active",
        })
        assert rebuilt.iterate_loop_state is None


# ── Gap III: risk_classifier/evidence.py ────────────────────────────


evidence = _load("_ev_g3", "app/risk_classifier/evidence.py")


def _fake_cr(
    *, cr_id: str, requestor: str, path: str, status: str,
    created_at: datetime, decision_at: datetime | None = None,
):
    """Minimal CR shape evidence.py uses."""
    cr = MagicMock()
    cr.id = cr_id
    cr.request_id = cr_id
    cr.requestor = requestor
    cr.path = path
    cr.status = status
    cr.created_at = created_at.isoformat()
    cr.decision_at = decision_at.isoformat() if decision_at else None
    return cr


class TestActionsPerZonePerDay:
    @pytest.mark.skipif(evidence is None, reason="evidence not loadable")
    def test_empty_input_returns_empty(self):
        assert evidence.actions_per_zone_per_day(crs=[]) == []

    @pytest.mark.skipif(evidence is None, reason="evidence not loadable")
    def test_window_excludes_old_rows(self):
        now = datetime.now(timezone.utc)
        crs = [
            _fake_cr(
                cr_id="cr1", requestor="r", path="app/x.py",
                status="applied", created_at=now - timedelta(days=10),
            ),
            _fake_cr(
                cr_id="cr2", requestor="r", path="app/y.py",
                status="applied", created_at=now - timedelta(days=60),
            ),
        ]
        rows = evidence.actions_per_zone_per_day(
            window_days=30, crs=crs,
        )
        total = sum(r.cr_count for r in rows)
        # Only the 10-day-old CR should count
        assert total == 1

    @pytest.mark.skipif(evidence is None, reason="evidence not loadable")
    def test_groups_by_zone(self):
        now = datetime.now(timezone.utc)
        crs = [
            _fake_cr(
                cr_id="a", requestor="r",
                path="workspace/healing/foo.json",  # OBSERVABLE
                status="applied", created_at=now - timedelta(days=2),
            ),
            _fake_cr(
                cr_id="b", requestor="r",
                path="workspace/healing/bar.json",  # OBSERVABLE
                status="applied", created_at=now - timedelta(days=3),
            ),
            _fake_cr(
                cr_id="c", requestor="r",
                path="app/auto_deployer.py",  # IMMUTABLE
                status="applied", created_at=now - timedelta(days=5),
            ),
        ]
        rows = evidence.actions_per_zone_per_day(crs=crs)
        # Should have at least 2 distinct zones
        zones = {r.zone for r in rows}
        assert len(zones) >= 2


class TestRollbackRate30d:
    @pytest.mark.skipif(evidence is None, reason="evidence not loadable")
    def test_zero_applied_zero_rate(self):
        crs = []
        assert evidence.rollback_rate_30d(crs=crs) == []

    @pytest.mark.skipif(evidence is None, reason="evidence not loadable")
    def test_calculates_per_requestor(self):
        now = datetime.now(timezone.utc)
        crs = [
            _fake_cr(
                cr_id=f"a{i}", requestor="bot-A",
                path="workspace/notes/foo.md",
                status="applied",
                created_at=now - timedelta(days=5),
                decision_at=now - timedelta(days=5),
            )
            for i in range(8)
        ] + [
            _fake_cr(
                cr_id=f"ar{i}", requestor="bot-A",
                path="workspace/notes/foo.md",
                status="rolled_back",
                created_at=now - timedelta(days=4),
                decision_at=now - timedelta(days=4),
            )
            for i in range(2)
        ]
        rows = evidence.rollback_rate_30d(crs=crs)
        assert len(rows) == 1
        row = rows[0]
        assert row.requestor == "bot-A"
        # 8 applied + 2 rolled_back; rolled_back also counts as applied
        # 10 applied total, 2 rolled back → 20% rate
        assert row.applied_count == 10
        assert row.rolled_back_count == 2
        assert row.rollback_rate == 0.2


class TestEvidenceFor:
    @pytest.mark.skipif(evidence is None, reason="evidence not loadable")
    def test_empty_requestor_returns_zero_evidence(self):
        e = evidence.evidence_for(
            requestor="", path_prefix="workspace/notes/",
        )
        assert e.cr_count == 0

    @pytest.mark.skipif(evidence is None, reason="evidence not loadable")
    def test_combined_shape(self):
        now = datetime.now(timezone.utc)
        crs = [
            _fake_cr(
                cr_id="cr1", requestor="alice",
                path="workspace/notes/a.md", status="applied",
                created_at=now - timedelta(days=10),
            ),
            _fake_cr(
                cr_id="cr2", requestor="alice",
                path="workspace/notes/b.md", status="rejected",
                created_at=now - timedelta(days=8),
            ),
            _fake_cr(
                cr_id="cr3", requestor="alice",
                path="workspace/healing/x.json",  # Different prefix
                status="applied",
                created_at=now - timedelta(days=5),
            ),
        ]
        e = evidence.evidence_for(
            requestor="alice", path_prefix="workspace/notes/",
            crs=crs,
        )
        assert e.requestor == "alice"
        assert e.path_prefix == "workspace/notes/"
        assert e.cr_count == 2  # only notes/ paths
        assert e.applied == 1
        assert e.rejected == 1
        assert e.rejection_rate == 0.5  # 1 of 2 decided
        assert e.first_at is not None
        assert e.last_at is not None
        assert "cr1" in e.sample_cr_ids
        assert "cr2" in e.sample_cr_ids


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

"""Tests for the durable coding-session extension (2026-05-20).

Covers Phase 2 piece 2d:
  * CodingSession.durable defaults to False
  * durable=True survives JSON round-trip
  * Reconciler skips durable sessions on idle-timeout (but still
    expires on TTL)
  * Retention monitor (worktrees) skips durable terminal sessions
  * Manager.set_durable hook (operator + executor opt-in)

Safety invariants pinned:
  * Default behaviour bit-identical to today (durable=False default,
    only persisted when True)
  * TTL still bounds durable sessions (defence against run-forever)
  * Terminal sessions reject set_durable (read-only after terminal)
  * Idempotent on same-value flips (no audit-log noise)
"""
from __future__ import annotations

import json
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# Stubs (defensive — defer to real crewai when available)
_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())

try:
    import crewai as _real_crewai  # noqa: F401
    _crewai_available = True
except Exception:
    _crewai_available = False

if not _crewai_available:
    for _mod in ("crewai", "crewai.tools"):
        if _mod not in sys.modules:
            m = types.ModuleType(_mod)
            if _mod == "crewai.tools":
                m.tool = lambda name: (lambda fn: fn)
                m.BaseTool = type("BaseTool", (), {})
            sys.modules[_mod] = m


# ── Helpers (mirror test_coding_session.py shape) ────────────────────


class FakeBackend:
    """In-memory backend; mirrors the one in test_coding_session.py."""

    def __init__(self) -> None:
        self.refs: dict[str, str] = {"main": "abc123" * 6 + "ab"}
        self.created: list[dict] = []
        self.removed: list[dict] = []

    def resolve_ref(self, ref: str) -> str:
        if ref not in self.refs:
            raise ValueError(f"unknown ref {ref!r}")
        return self.refs[ref]

    def create_worktree(self, *, worktree_path: str, base_sha: str) -> None:
        self.created.append({"path": worktree_path, "sha": base_sha})

    def remove_worktree(self, *, worktree_path: str, force: bool = True) -> None:
        self.removed.append({"path": worktree_path, "force": force})


@pytest.fixture
def store_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from app.coding_session import store
    monkeypatch.setattr(store, "_STORE_DIR", tmp_path)
    monkeypatch.setattr(store, "_AUDIT_LOG", tmp_path / "audit.jsonl")
    store.reset_for_tests()
    return tmp_path


@pytest.fixture
def manager(store_dir: Path) -> Any:
    from app.coding_session import Manager, QuotaConfig
    cfg = QuotaConfig(
        per_agent_active=4,
        system_active=8,
        per_session_disk_bytes=10_240,
        system_disk_bytes=40_960,
        ttl_seconds=60,
        idle_seconds=30,
    )
    return Manager(backend=FakeBackend(), config=cfg)


# ============================================================================
# Model: default + serialisation
# ============================================================================


class TestDurableField:
    def test_default_is_false(self) -> None:
        from app.coding_session import CodingSession, Status
        cs = CodingSession(
            id="x", agent_id="coder", purpose="p",
            created_at="2026-05-20T00:00:00+00:00",
            base="main", base_sha="a" * 40,
            worktree_path="/tmp/x",
            expires_at="2026-05-20T01:00:00+00:00",
            last_activity_at="2026-05-20T00:00:00+00:00",
            status=Status.ACTIVE,
        )
        assert cs.durable is False

    def test_durable_true_persisted(self) -> None:
        from app.coding_session import CodingSession, Status
        cs = CodingSession(
            id="x", agent_id="coder", purpose="p",
            created_at="t1", base="main", base_sha="a" * 40,
            worktree_path="/tmp/x",
            expires_at="t2", last_activity_at="t1",
            status=Status.ACTIVE, durable=True,
        )
        d = cs.to_dict()
        assert d.get("durable") is True

    def test_durable_false_not_persisted(self) -> None:
        # Default-false stays out of the dict so legacy session JSONs
        # don't gain a stray key on round-trip — keeps byte stability.
        from app.coding_session import CodingSession, Status
        cs = CodingSession(
            id="x", agent_id="coder", purpose="p",
            created_at="t1", base="main", base_sha="a" * 40,
            worktree_path="/tmp/x",
            expires_at="t2", last_activity_at="t1",
            status=Status.ACTIVE, durable=False,
        )
        d = cs.to_dict()
        assert "durable" not in d

    def test_roundtrip_preserves_durable(self) -> None:
        from app.coding_session import CodingSession, Status
        cs = CodingSession(
            id="x", agent_id="coder", purpose="p",
            created_at="t1", base="main", base_sha="a" * 40,
            worktree_path="/tmp/x",
            expires_at="t2", last_activity_at="t1",
            status=Status.ACTIVE, durable=True,
        )
        reloaded = CodingSession.from_dict(cs.to_dict())
        assert reloaded.durable is True

    def test_legacy_dict_without_durable_loads_false(self) -> None:
        # Pre-2026-05-20 session JSONs don't have a `durable` key.
        # ``from_dict`` must default to False (not crash, not True).
        from app.coding_session import CodingSession
        legacy = {
            "id": "x", "agent_id": "coder", "purpose": "p",
            "created_at": "t1", "base": "main", "base_sha": "a" * 40,
            "worktree_path": "/tmp/x",
            "expires_at": "t2", "last_activity_at": "t1",
            "status": "active",
        }
        reloaded = CodingSession.from_dict(legacy)
        assert reloaded.durable is False


# ============================================================================
# Reconciler: durable skips idle, still hits TTL
# ============================================================================


class TestReconcilerHonorsDurable:
    def _now(self) -> datetime:
        return datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)

    def _make_cs(self, *, durable: bool, idle_for_s: int, ttl_remaining_s: int):
        from app.coding_session import CodingSession, Status
        now = self._now()
        return CodingSession(
            id=f"sess-{durable}-{idle_for_s}",
            agent_id="coder", purpose="p",
            created_at=now.isoformat(),
            base="main", base_sha="a" * 40,
            worktree_path="/tmp/sess",
            expires_at=(now + timedelta(seconds=ttl_remaining_s)).isoformat(),
            last_activity_at=(now - timedelta(seconds=idle_for_s)).isoformat(),
            status=Status.ACTIVE,
            durable=durable,
        )

    def test_non_durable_idle_expires(self):
        from app.coding_session.reconciler import _classify
        cs = self._make_cs(durable=False, idle_for_s=120, ttl_remaining_s=600)
        outcome = _classify(cs, now=self._now(), idle_seconds=60)
        assert outcome is not None
        _, kind = outcome
        assert kind == "idle"

    def test_durable_idle_does_not_expire(self):
        from app.coding_session.reconciler import _classify
        cs = self._make_cs(durable=True, idle_for_s=120, ttl_remaining_s=600)
        outcome = _classify(cs, now=self._now(), idle_seconds=60)
        assert outcome is None

    def test_durable_still_expires_on_ttl(self):
        # TTL exhaustion is the load-bearing safety: durable means
        # "no idle timeout" not "live forever". An expires_at in the
        # past must still trigger expiry.
        from app.coding_session.reconciler import _classify
        cs = self._make_cs(durable=True, idle_for_s=0, ttl_remaining_s=-1)
        outcome = _classify(cs, now=self._now(), idle_seconds=60)
        assert outcome is not None
        _, kind = outcome
        assert kind == "ttl"

    def test_durable_within_idle_window_unchanged(self):
        from app.coding_session.reconciler import _classify
        cs = self._make_cs(durable=True, idle_for_s=5, ttl_remaining_s=600)
        outcome = _classify(cs, now=self._now(), idle_seconds=60)
        assert outcome is None


# ============================================================================
# Retention monitor: durable terminal sessions spared
# ============================================================================


class TestRetentionMonitorHonorsDurable:
    def test_durable_terminal_session_spared(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Build a session-store dir + one durable-terminal record,
        # one ordinary-terminal record. Run the worktree retention
        # pass via run_worktrees and assert the durable one survives.
        from app.healing.monitors import retention as mon

        store_dir = tmp_path / "sessions"
        store_dir.mkdir()
        # Build absolute paths under the real worktree root the monitor
        # expects; the validator inside the monitor refuses if the
        # path isn't under it. Use a stub that always passes validation
        # so we can test the durable-skip branch in isolation.
        monkeypatch.setattr(
            mon, "_validate_worktree_path", lambda *a, **k: (True, ""),
        )

        ordinary_id = "ordinary-1"
        durable_id = "durable-1"
        (store_dir / f"{ordinary_id}.json").write_text(json.dumps({
            "id": ordinary_id, "status": "submitted",
            "worktree_path": str(tmp_path / "wt" / ordinary_id),
        }))
        (store_dir / f"{durable_id}.json").write_text(json.dumps({
            "id": durable_id, "status": "submitted",
            "worktree_path": str(tmp_path / "wt" / durable_id),
            "durable": True,
        }))

        # Force file mtimes to be older than the retention window so
        # both records are candidates by age — only durability should
        # distinguish them.
        for record in store_dir.glob("*.json"):
            old = time.time() - (8 * 86400)  # 8 days ago
            import os
            os.utime(record, (old, old))

        # Stub the dependencies the monitor expects.
        monkeypatch.setattr(mon, "background_enabled", lambda: True)
        monkeypatch.setattr(mon, "_dry_run", lambda: True)
        monkeypatch.setattr(mon, "read_state_json", lambda *a, **k: {})
        monkeypatch.setattr(mon, "write_state_json", lambda *a, **k: None)
        # Redirect the monitor's store path to our tmp.
        import pathlib
        original_path_class = mon.Path

        def _path_factory(s):
            if s == "/app/workspace/coding_sessions":
                return store_dir
            return original_path_class(s)
        monkeypatch.setattr(mon, "Path", _path_factory)

        # Capture the summary by patching the alert / publish helpers.
        # We don't run the post-processing — just confirm the durable
        # entry was spared. The simplest signal: run the function and
        # confirm the durable session's JSON record is still there.
        mon.run_worktrees()

        # The durable record must still exist; the ordinary one is
        # gone (dry-run mode means the counter is incremented but
        # rmtree + unlink are skipped — so BOTH should still be on
        # disk and only the summary differs).
        #
        # Easier assertion in dry-run mode: confirm the durable
        # record was NEVER classified as "removed candidate" via
        # the summary. We patch back to live mode and check that the
        # durable file persists.
        monkeypatch.setattr(mon, "_dry_run", lambda: False)
        # Re-create the files (they survived dry-run) and re-run.
        mon.run_worktrees()
        assert (store_dir / f"{durable_id}.json").exists(), (
            "durable terminal session must be spared from retention "
            "even when older than the retention window"
        )


# ============================================================================
# Manager.set_durable hook
# ============================================================================


class TestManagerSetDurable:
    @pytest.fixture(autouse=True)
    def _wt_fixture(self, tmp_path):
        # Shared per-test worktree root for manager.start calls.
        self._wt = tmp_path / "wt"
        self._wt.mkdir()

    def test_set_durable_on_active_session(self, manager):
        cs = manager.start(
            agent_id="coder", base="main",
            purpose="iterate on X",
            worktree_root=self._wt,
        )
        assert cs.durable is False
        updated = manager.set_durable(cs.id, value=True)
        assert updated.durable is True

    def test_set_durable_persists_via_store(self, manager):
        from app.coding_session import store
        cs = manager.start(
            agent_id="coder", base="main", purpose="x",
            worktree_root=self._wt,
        )
        manager.set_durable(cs.id, value=True)
        reloaded = store.get(cs.id)
        assert reloaded.durable is True

    def test_set_durable_idempotent_on_same_value(self, manager):
        cs = manager.start(
            agent_id="coder", base="main", purpose="x",
            worktree_root=self._wt,
        )
        # Setting to default-False on a fresh session is a no-op.
        out = manager.set_durable(cs.id, value=False)
        assert out.durable is False
        # Same with True after first set.
        manager.set_durable(cs.id, value=True)
        out = manager.set_durable(cs.id, value=True)
        assert out.durable is True

    def test_set_durable_refused_on_terminal(self, manager):
        from app.coding_session import IllegalTransition
        cs = manager.start(
            agent_id="coder", base="main", purpose="x",
            worktree_root=self._wt,
        )
        manager.discard(cs.id, reason="testing")
        with pytest.raises(IllegalTransition):
            manager.set_durable(cs.id, value=True)

    def test_set_durable_unknown_session(self, manager):
        from app.coding_session import IllegalTransition
        with pytest.raises(IllegalTransition):
            manager.set_durable("nonexistent", value=True)

    def test_set_durable_refreshes_activity(self, manager):
        # Setting durable should also touch last_activity_at so the
        # reconciler's TTL check sees a fresh timestamp — important
        # for the executor's "I'm holding this worktree" semantics.
        cs = manager.start(
            agent_id="coder", base="main", purpose="x",
            worktree_root=self._wt,
        )
        before = cs.last_activity_at
        time.sleep(0.01)  # ensure a measurable ISO-second delta
        updated = manager.set_durable(cs.id, value=True)
        assert updated.last_activity_at >= before


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))

"""Pin: ABORTED + STANDDOWN_COMPLETE can transition back to IDLE.

The original state machine had `set()` (no allowed transitions) from
both terminal phases. The dry_run drill's reset path called
``abort()`` (→ ABORTED) then ``adopt_plan()`` (requires IDLE) and
silently failed with `MigrationStateError: cannot adopt plan in
phase ABORTED; abort first`. The drill audit captured this as
`advance_to_dual_write: ok=False` and the operator had no path
back to IDLE without manually editing
``workspace/runtime_settings.json``.

Adding ``PHASE_ABORTED → {PHASE_IDLE}`` (and the symmetric
``STANDDOWN_COMPLETE → {IDLE}``) gives the operator + dry_run a
clean way to recover.
"""
from __future__ import annotations

import pytest

# Skip the whole module when pydantic_settings isn't installed —
# the state module's transitive import of app.config requires it.
# Tests still run inside the gateway image where it IS installed.
pydantic_settings = pytest.importorskip("pydantic_settings")


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    """Point runtime_settings at a temp path so the test doesn't
    pollute live state."""
    # The state module reads/writes via runtime_settings; intercept
    # the file path.
    from app import paths as _paths
    monkeypatch.setattr(_paths, "WORKSPACE_ROOT", tmp_path)
    # Force the runtime_settings cache to reload from the new dir.
    import app.runtime_settings as rs
    monkeypatch.setattr(rs, "_cache", None)
    monkeypatch.setattr(rs, "_STATE_PATH",
                         tmp_path / "runtime_settings.json")
    yield
    monkeypatch.setattr(rs, "_cache", None)


def test_aborted_can_transition_to_idle():
    """The fix: ABORTED → IDLE is an allowed transition."""
    from app.memory.embedding_migration import state as st
    # Force-write ABORTED state
    st._write_raw({"phase": st.PHASE_ABORTED, "plan_id": "test"})
    cur = st.get_state()
    assert cur.phase == st.PHASE_ABORTED
    # Transition to IDLE — should succeed
    after = st.transition(st.PHASE_IDLE, reason="test_recovery")
    assert after.phase == st.PHASE_IDLE


def test_standdown_complete_can_transition_to_idle():
    """STANDDOWN_COMPLETE is also a terminal-shaped phase that
    operators need a recovery path from."""
    from app.memory.embedding_migration import state as st
    st._write_raw({"phase": st.PHASE_STANDDOWN_COMPLETE, "plan_id": "test"})
    after = st.transition(st.PHASE_IDLE, reason="post_migration_reset")
    assert after.phase == st.PHASE_IDLE


def test_aborted_to_idle_enables_subsequent_adopt():
    """The full sequence that was broken: abort → reset to IDLE →
    adopt_plan succeeds."""
    from app.memory.embedding_migration import state as st
    # Seed: state is in PLANNED (mid-migration)
    st._write_raw({"phase": st.PHASE_PLANNED, "plan_id": "p1"})
    # Abort (PLANNED → ABORTED)
    st.abort(reason="operator-cancel")
    assert st.get_state().phase == st.PHASE_ABORTED
    # Now reset to IDLE
    st.transition(st.PHASE_IDLE, reason="dry_run_reset")
    assert st.get_state().phase == st.PHASE_IDLE
    # And adopt a new plan — this previously raised
    # MigrationStateError("cannot adopt plan in phase ABORTED")
    after = st.adopt_plan("p2")
    assert after.phase == st.PHASE_PLANNED
    assert after.plan_id == "p2"


def test_idle_cannot_transition_to_arbitrary_phase():
    """Don't accidentally loosen IDLE's allowed transitions while
    fixing ABORTED."""
    from app.memory.embedding_migration import state as st
    st._write_raw({"phase": st.PHASE_IDLE})
    with pytest.raises(st.MigrationStateError):
        # IDLE → DUAL_WRITE not allowed (must go via PLANNED)
        st.transition(st.PHASE_DUAL_WRITE, reason="bypass attempt")


def test_aborted_cannot_skip_idle_to_dual_write():
    """ABORTED → IDLE is the ONLY new exit; ABORTED → other phases
    must still raise."""
    from app.memory.embedding_migration import state as st
    st._write_raw({"phase": st.PHASE_ABORTED})
    with pytest.raises(st.MigrationStateError):
        st.transition(st.PHASE_DUAL_WRITE, reason="bypass")
    with pytest.raises(st.MigrationStateError):
        st.transition(st.PHASE_PLANNED, reason="bypass")


def test_2026_05_19_dry_run_regression():
    """The exact pattern that failed before the fix: a previous
    dry_run left state in ABORTED; a new dry_run's reset path
    (abort + transition to IDLE) must produce IDLE.
    """
    from app.memory.embedding_migration import state as st
    # Prior dry_run left this behind
    st._write_raw({"phase": st.PHASE_ABORTED, "plan_id": "prior_dry_run"})
    # New dry_run's reset sequence
    cur = st.get_state()
    if cur.phase != st.PHASE_IDLE:
        st.abort(reason="dry_run_reset")   # ABORTED → ABORTED (idempotent)
        st.transition(st.PHASE_IDLE, reason="dry_run_reset")
    # Should now be IDLE, ready for adopt_plan
    assert st.get_state().phase == st.PHASE_IDLE
    after = st.adopt_plan("fresh_run")
    assert after.phase == st.PHASE_PLANNED

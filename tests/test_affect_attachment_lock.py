"""Regression test for the affect.attachment reentrancy deadlock.

Background — 2026-04-30: every Signal-dispatched task hung silently
inside the ``ON_DELEGATION`` lifecycle hook. py-spy on the live gateway
showed the commander thread stuck here::

    get_peer_model  (app/affect/attachment.py:200)
    update_from_interaction (app/affect/attachment.py:250)
    _affect_on_delegation (app/affect/hooks.py:133)
    execute (app/lifecycle_hooks.py:203)
    _handle_locked (app/agents/commander/orchestrator.py:2865)

Root cause: ``_lock`` was a non-reentrant ``threading.Lock``, but
``update_from_interaction`` holds the lock and then calls
``get_peer_model`` / ``get_user_model``, which both try to re-acquire
it. Any peer-agent delegation deadlocks the calling thread until the
orchestrator's 15-min soft-timeout fires with zero output.

Fix: ``threading.Lock()`` → ``threading.RLock()`` (one-line change).
This test would have caught the bug originally — runs the actual
deadlock path against a wall-clock timeout. It must not hang.
"""
from __future__ import annotations

import threading
import time

import pytest

from app.affect import attachment


def test_lock_is_reentrant():
    """The module-level lock MUST be reentrant — see file docstring."""
    # threading.RLock returns an instance whose type repr varies across
    # Python versions, so we verify behaviour rather than identity.
    assert attachment._lock.acquire(blocking=False)
    try:
        # Re-acquire from the same thread — would deadlock with Lock(),
        # succeeds with RLock().
        assert attachment._lock.acquire(blocking=False)
        attachment._lock.release()
    finally:
        attachment._lock.release()


def test_update_from_interaction_does_not_deadlock(tmp_path, monkeypatch):
    """End-to-end: the actual code path that hung in production must
    return within seconds, not block forever.

    We redirect _PEER_DIR / _ATTACH_DIR to a tmp dir so the test doesn't
    pollute real workspace state.
    """
    monkeypatch.setattr(attachment, "_ATTACH_DIR", tmp_path / "attach")
    monkeypatch.setattr(attachment, "_PEER_DIR", tmp_path / "peer")

    result_holder: dict = {}
    exc_holder: dict = {}

    def _drive():
        try:
            # This is the call site that deadlocked: a peer-agent
            # delegation invokes update_from_interaction with a
            # peer:<role> identity, which re-enters get_peer_model
            # under the same lock.
            m = attachment.update_from_interaction(
                "peer:research",
                observed_valence=0.5,
                note="test",
                interaction_kind="task",
            )
            result_holder["m"] = m
        except BaseException as e:  # noqa: BLE001 — we want to see it
            exc_holder["e"] = e

    t = threading.Thread(target=_drive, daemon=True)
    t.start()
    # 5s is way more than the operation needs (sub-second when working);
    # picked low enough that a deadlock fails the test fast.
    t.join(timeout=5.0)

    assert not t.is_alive(), (
        "update_from_interaction deadlocked — see "
        "app/affect/attachment.py for the RLock fix"
    )
    if "e" in exc_holder:
        raise exc_holder["e"]
    assert "m" in result_holder
    assert result_holder["m"].identity == "peer:research"


def test_get_peer_model_called_under_held_lock(tmp_path, monkeypatch):
    """Direct re-entrancy test: hold the lock, call into a function
    that re-acquires it, verify no hang."""
    monkeypatch.setattr(attachment, "_PEER_DIR", tmp_path / "peer")

    finished = threading.Event()
    err: dict = {}

    def _drive():
        try:
            with attachment._lock:
                # Re-entry — must succeed under RLock
                m = attachment.get_peer_model("research")
                assert m.identity == "peer:research"
            finished.set()
        except BaseException as e:  # noqa: BLE001
            err["e"] = e
            finished.set()

    t = threading.Thread(target=_drive, daemon=True)
    t.start()
    finished.wait(timeout=5.0)
    assert finished.is_set(), "get_peer_model deadlocked under held _lock"
    if "e" in err:
        raise err["e"]

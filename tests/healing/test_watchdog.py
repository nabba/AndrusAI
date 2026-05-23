"""Tests for ``app.healing.watchdog`` — the daemon-thread reaper.

Tests focus on the deterministic ``_check_and_respawn`` core; the
daemon loop itself is just a sleep wrapper around it.
"""
from __future__ import annotations

import time

import pytest


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """Reset crash history + give-up state between tests."""
    from app.healing import watchdog
    monkeypatch.setattr(watchdog, "_crash_history",
                         __import__("collections").defaultdict(
                             lambda: __import__("collections").deque(maxlen=10)))
    monkeypatch.setattr(watchdog, "_given_up", {})
    yield


def _stub_registry(monkeypatch, mapping: dict[str, tuple[str, str]]) -> None:
    """Replace the registered daemons with a minimal mapping for the test."""
    from app.healing import watchdog
    monkeypatch.setattr(watchdog, "_REGISTERED_DAEMONS", dict(mapping))


# ── Liveness check ────────────────────────────────────────────────────────


def test_alive_daemon_not_respawned(monkeypatch):
    """A daemon currently alive must NOT be re-spawned."""
    from app.healing import watchdog

    _stub_registry(monkeypatch, {"healing-monitors": ("does.not.matter", "x")})
    monkeypatch.setattr(watchdog, "_is_alive", lambda name: True)
    started = []
    monkeypatch.setattr(watchdog, "_attempt_start",
                        lambda name: started.append(name) or "started")

    summary = watchdog._check_and_respawn()
    assert summary["alive"] == ["healing-monitors"]
    assert summary["respawned"] == []
    assert started == []


def test_dead_daemon_respawned(monkeypatch):
    """A daemon NOT alive must be re-spawned."""
    from app.healing import watchdog

    _stub_registry(monkeypatch, {"daemon-A": ("mod.a", "start")})
    monkeypatch.setattr(watchdog, "_is_alive", lambda name: False)
    started = []
    monkeypatch.setattr(
        watchdog, "_attempt_start",
        lambda name: started.append(name) or "started",
    )
    monkeypatch.setattr(watchdog, "_audit", lambda *a, **k: None)

    summary = watchdog._check_and_respawn()
    assert summary["respawned"] == ["daemon-A"]
    assert started == ["daemon-A"]


# ── Backoff / give-up ─────────────────────────────────────────────────────


def test_giveup_after_max_crashes(monkeypatch):
    """After 3 crashes within an hour, the watchdog gives up (no further
    re-spawn) and emits a Signal alert.
    """
    from app.healing import watchdog

    _stub_registry(monkeypatch, {"daemon-A": ("mod.a", "start")})
    monkeypatch.setattr(watchdog, "_is_alive", lambda name: False)
    monkeypatch.setattr(watchdog, "_attempt_start", lambda name: "started")
    monkeypatch.setattr(watchdog, "_audit", lambda *a, **k: None)

    alerts = []
    monkeypatch.setattr(
        watchdog, "_send_giveup_alert",
        lambda name, n: alerts.append((name, n)),
    )

    # First 3 passes: respawn each time, populating crash history.
    for _ in range(watchdog._MAX_CRASHES_PER_HOUR):
        s = watchdog._check_and_respawn()
        assert s["respawned"] == ["daemon-A"]

    # 4th pass: history is at cap → give up, alert fires.
    s = watchdog._check_and_respawn()
    assert "daemon-A" in s["given_up"]
    assert alerts and alerts[0][0] == "daemon-A"

    # 5th pass: still in give-up — no respawn, no new alert.
    s = watchdog._check_and_respawn()
    assert "daemon-A" in s["still_in_giveup"]
    assert s["respawned"] == []
    assert len(alerts) == 1


def test_giveup_resets_after_24h_quiet(monkeypatch):
    """After 24 h since give-up, the daemon is allowed to be re-spawned."""
    from app.healing import watchdog

    _stub_registry(monkeypatch, {"daemon-A": ("mod.a", "start")})
    monkeypatch.setattr(watchdog, "_is_alive", lambda name: False)
    monkeypatch.setattr(watchdog, "_attempt_start", lambda name: "started")
    monkeypatch.setattr(watchdog, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(watchdog, "_send_giveup_alert", lambda *a, **k: None)

    # Force into give-up state.
    watchdog._given_up["daemon-A"] = (
        time.time() - (watchdog._GIVEUP_RESET_HOURS + 1) * 3600
    )

    s = watchdog._check_and_respawn()
    # 24h+ passed since giveup → reset and respawn.
    assert "daemon-A" in s["respawned"]
    assert "daemon-A" not in watchdog._given_up


def test_old_crashes_drop_out_of_window(monkeypatch):
    """Crashes older than 1 h shouldn't count toward the cap."""
    from app.healing import watchdog
    from collections import deque

    _stub_registry(monkeypatch, {"daemon-A": ("mod.a", "start")})
    monkeypatch.setattr(watchdog, "_is_alive", lambda name: False)
    monkeypatch.setattr(watchdog, "_attempt_start", lambda name: "started")
    monkeypatch.setattr(watchdog, "_audit", lambda *a, **k: None)

    # Pre-seed crash history with 3 OLD crashes (> 1 h ago).
    old_history = deque(maxlen=10)
    old_ts = time.time() - 3 * 3600
    for _ in range(3):
        old_history.append(old_ts)
    watchdog._crash_history["daemon-A"] = old_history

    # The pass should drop the old crashes from the window and respawn.
    s = watchdog._check_and_respawn()
    assert "daemon-A" in s["respawned"]


# ── Multi-daemon ──────────────────────────────────────────────────────────


def test_one_dead_doesnt_block_others(monkeypatch):
    from app.healing import watchdog

    _stub_registry(monkeypatch, {
        "daemon-A": ("mod.a", "start"),
        "daemon-B": ("mod.b", "start"),
    })
    monkeypatch.setattr(
        watchdog, "_is_alive",
        lambda name: name == "daemon-A",  # A is alive, B is dead
    )
    monkeypatch.setattr(watchdog, "_attempt_start", lambda name: "started")
    monkeypatch.setattr(watchdog, "_audit", lambda *a, **k: None)

    s = watchdog._check_and_respawn()
    assert "daemon-A" in s["alive"]
    assert "daemon-B" in s["respawned"]


def test_failed_respawn_still_counts_as_crash(monkeypatch):
    """If ``_attempt_start`` returns ``"failed"``, the crash counts toward
    the backoff cap — otherwise a daemon that raises on every start
    would be re-tried at the watchdog's full cadence forever.

    Distinct from ``"declined"`` (see
    ``test_declined_start_does_not_count_as_crash``): a daemon that
    raises is a real crash; a daemon whose master switch is off is not.
    """
    from app.healing import watchdog

    _stub_registry(monkeypatch, {"broken": ("mod.x", "start")})
    monkeypatch.setattr(watchdog, "_is_alive", lambda name: False)
    monkeypatch.setattr(watchdog, "_attempt_start", lambda name: "failed")
    monkeypatch.setattr(watchdog, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(watchdog, "_send_giveup_alert", lambda *a, **k: None)

    # 3 failed respawns then giveup.
    for _ in range(watchdog._MAX_CRASHES_PER_HOUR):
        watchdog._check_and_respawn()
    s = watchdog._check_and_respawn()
    assert "broken" in s["given_up"]


# ── Decline-vs-crash distinction (regression for false-positive ─────────
#    "daemon has crashed 3 times" alert when master switch is off) ────────


def test_declined_start_does_not_count_as_crash(monkeypatch):
    """A daemon whose ``start_fn`` returns ``False`` (master switch off,
    prerequisites unmet, etc.) is NOT crashing — it has cleanly chosen
    not to run. The watchdog must not advance the give-up counter and
    must not emit the give-up audit/alert.

    Regression for the daily "💀 Self-heal: daemon `ul-apply-hook` has
    crashed 3 times" false positive: ``apply_hook.start()`` returns
    ``False`` when ``upgrade_lifecycle_apply_hook_enabled`` is OFF
    (its default). The pre-fix watchdog ignored the return value and
    inferred a crash from ``_is_alive=False``, giving up after 3
    minutes every restart.
    """
    from app.healing import watchdog

    _stub_registry(monkeypatch, {"disabled-daemon": ("mod.x", "start")})
    monkeypatch.setattr(watchdog, "_is_alive", lambda name: False)
    monkeypatch.setattr(watchdog, "_attempt_start", lambda name: "declined")

    audit_events: list[tuple] = []
    alerts: list[tuple] = []
    monkeypatch.setattr(
        watchdog, "_audit",
        lambda action, **detail: audit_events.append((action, detail)),
    )
    monkeypatch.setattr(
        watchdog, "_send_giveup_alert",
        lambda name, n: alerts.append((name, n)),
    )

    # Four passes — well past the 3-strikes give-up cap. None should
    # advance crash history, fire an audit, or send an alert.
    for _ in range(4):
        s = watchdog._check_and_respawn()
        assert "disabled-daemon" in s["declined"]
        assert s["respawned"] == []
        assert s["given_up"] == []
        assert s["still_in_giveup"] == []

    # Crash history must stay empty so a daemon that's been off all day
    # doesn't get treated as crash-looping the moment its master switch
    # flips on.
    assert not watchdog._crash_history.get("disabled-daemon")
    assert "disabled-daemon" not in watchdog._given_up
    assert audit_events == []
    assert alerts == []


def test_raising_start_fn_counts_as_crash(monkeypatch):
    """The inverse of the decline path: a ``start_fn`` that RAISES is a
    real crash — the watchdog must still give up after 3 attempts.

    The decline-vs-crash distinction is value-based: ``False`` return
    is decline, exception or ``"failed"`` outcome is crash. Without
    this inverse pin, a future refactor could conflate the two and
    silently disable the give-up backoff.
    """
    from app.healing import watchdog

    _stub_registry(monkeypatch, {"raises": ("mod.y", "start")})
    monkeypatch.setattr(watchdog, "_is_alive", lambda name: False)

    def raising_attempt(name: str) -> str:
        # Simulates _attempt_start's behaviour when the wrapped
        # start_fn raises: it returns "failed" after logging.
        return "failed"

    monkeypatch.setattr(watchdog, "_attempt_start", raising_attempt)

    audit_events: list[tuple] = []
    alerts: list[tuple] = []
    monkeypatch.setattr(
        watchdog, "_audit",
        lambda action, **detail: audit_events.append((action, detail)),
    )
    monkeypatch.setattr(
        watchdog, "_send_giveup_alert",
        lambda name, n: alerts.append((name, n)),
    )

    # 3 failed attempts populate the crash window; 4th pass fires
    # give-up + alert + audit.
    for _ in range(watchdog._MAX_CRASHES_PER_HOUR):
        s = watchdog._check_and_respawn()
        assert "raises" not in s["given_up"]

    s = watchdog._check_and_respawn()
    assert "raises" in s["given_up"]
    assert alerts == [("raises", watchdog._MAX_CRASHES_PER_HOUR)]
    assert any(a[0] == "watchdog_giveup" for a in audit_events)


def test_attempt_start_classifies_false_return_as_declined(monkeypatch):
    """End-to-end pin: ``_attempt_start`` itself must classify a
    ``start_fn`` returning ``False`` as ``"declined"``, not ``"failed"``.

    Without this layer, the contract from ``_check_and_respawn`` is
    moot — the decline classification has to start in
    ``_attempt_start`` where the real return value is captured.
    """
    from app.healing import watchdog
    import sys
    import types

    # Build a stub module whose ``start`` returns False (master-switch-off
    # simulation).
    stub = types.ModuleType("watchdog_test_stub_declined")
    stub.start = lambda: False  # type: ignore[attr-defined]
    sys.modules["watchdog_test_stub_declined"] = stub
    try:
        _stub_registry(monkeypatch, {
            "stub-d": ("watchdog_test_stub_declined", "start"),
        })
        # _is_alive must be False — start_fn didn't spawn anything.
        monkeypatch.setattr(watchdog, "_is_alive", lambda name: False)
        assert watchdog._attempt_start("stub-d") == "declined"
    finally:
        sys.modules.pop("watchdog_test_stub_declined", None)


def test_attempt_start_classifies_exception_as_failed(monkeypatch):
    """Inverse pin: an exception inside ``start_fn`` is classified as
    ``"failed"``, not silently swallowed.
    """
    from app.healing import watchdog
    import sys
    import types

    def boom():
        raise RuntimeError("synthetic crash for test")

    stub = types.ModuleType("watchdog_test_stub_raises")
    stub.start = boom  # type: ignore[attr-defined]
    sys.modules["watchdog_test_stub_raises"] = stub
    try:
        _stub_registry(monkeypatch, {
            "stub-r": ("watchdog_test_stub_raises", "start"),
        })
        monkeypatch.setattr(watchdog, "_is_alive", lambda name: False)
        assert watchdog._attempt_start("stub-r") == "failed"
    finally:
        sys.modules.pop("watchdog_test_stub_raises", None)


def test_attempt_start_classifies_true_return_with_thread_as_started(monkeypatch):
    """``start_fn`` returning True (or None) AND a thread now alive
    classifies as ``"started"``."""
    from app.healing import watchdog
    import sys
    import types

    stub = types.ModuleType("watchdog_test_stub_started")
    stub.start = lambda: True  # type: ignore[attr-defined]
    sys.modules["watchdog_test_stub_started"] = stub
    try:
        _stub_registry(monkeypatch, {
            "stub-s": ("watchdog_test_stub_started", "start"),
        })
        # Pretend the thread is alive after the call.
        monkeypatch.setattr(watchdog, "_is_alive", lambda name: True)
        assert watchdog._attempt_start("stub-s") == "started"
    finally:
        sys.modules.pop("watchdog_test_stub_started", None)


def test_attempt_start_classifies_truthy_return_without_thread_as_failed(monkeypatch):
    """``start_fn`` returning True but no thread alive afterward = failed.

    Catches the bug where a start_fn lies about success.
    """
    from app.healing import watchdog
    import sys
    import types

    stub = types.ModuleType("watchdog_test_stub_lies")
    stub.start = lambda: True  # type: ignore[attr-defined]
    sys.modules["watchdog_test_stub_lies"] = stub
    try:
        _stub_registry(monkeypatch, {
            "stub-l": ("watchdog_test_stub_lies", "start"),
        })
        monkeypatch.setattr(watchdog, "_is_alive", lambda name: False)
        assert watchdog._attempt_start("stub-l") == "failed"
    finally:
        sys.modules.pop("watchdog_test_stub_lies", None)


# ── Heartbeat + start() idempotency ───────────────────────────────────────


def test_heartbeat_touches_file(tmp_path, monkeypatch):
    from app.healing import watchdog

    fp = tmp_path / "watchdog_heartbeat"
    monkeypatch.setattr(watchdog, "_HEARTBEAT_PATH", fp)
    watchdog._touch_heartbeat()
    assert fp.exists()


def test_start_idempotent_when_alive(monkeypatch):
    from app.healing import watchdog

    monkeypatch.setattr(watchdog, "_is_alive", lambda name: True)
    monkeypatch.setattr(watchdog, "_enabled", lambda: True)

    spawned = []

    def fake_thread_start(self):
        spawned.append(self.name)

    # If start() spawned a thread we'd see it here — but with _is_alive
    # returning True it must NOT spawn.
    import threading as _th
    real_start = _th.Thread.start
    monkeypatch.setattr(_th.Thread, "start", fake_thread_start)
    try:
        watchdog.start()
    finally:
        monkeypatch.setattr(_th.Thread, "start", real_start)
    assert spawned == []


def test_start_disabled_short_circuits(monkeypatch):
    from app.healing import watchdog
    monkeypatch.setattr(watchdog, "_enabled", lambda: False)

    spawned = []
    import threading as _th
    real_start = _th.Thread.start
    monkeypatch.setattr(_th.Thread, "start", lambda self: spawned.append(self.name))
    try:
        watchdog.start()
    finally:
        monkeypatch.setattr(_th.Thread, "start", real_start)
    assert spawned == []


# ── Integration with start_fn idempotency ─────────────────────────────────


def test_existing_start_functions_are_thread_liveness_aware():
    """Regression guard: the daemons we watch must use thread-liveness
    detection in their start(), not just a stale ``_started`` flag.
    """
    from app.healing.monitors import _is_running as monitors_is_running
    from app.healing.auditor_bridge import _is_running as bridge_is_running
    # Just confirming the helpers exist and return bools — the watchdog
    # needs them.
    assert isinstance(monitors_is_running(), bool)
    assert isinstance(bridge_is_running(), bool)


def test_registered_daemons_start_return_obeys_contract(monkeypatch):
    """The watchdog's decline-vs-crash distinction depends on every
    registered ``start_fn`` returning ``False`` for clean declines (and
    ``True``/``None`` otherwise). A start_fn that returns ``None``
    where ``False`` is meant would be misclassified as ``"started"``
    when the thread isn't actually alive — and that misclassification
    is what produced the daily false-positive give-up alert.
    """
    import importlib

    from app.healing import watchdog

    for name, (module_path, fn_name) in watchdog._REGISTERED_DAEMONS.items():
        try:
            mod = importlib.import_module(module_path)
        except Exception:
            # Module may legitimately fail to import in test env
            # (e.g., upgrade_lifecycle without psycopg2). Skip those.
            continue
        start_fn = getattr(mod, fn_name)
        # Probe with master switch off and verify False — this is the
        # path the watchdog must classify as "declined".
        if module_path == "app.healing.monitors":
            monkeypatch.setenv("HEALING_MONITORS_ENABLED", "false")
        elif module_path == "app.healing.auditor_bridge":
            monkeypatch.setenv("HEALING_AUDITOR_BRIDGE_ENABLED", "false")
        elif module_path == "app.upgrade_lifecycle.apply_hook":
            # apply_hook reads runtime_settings, not env. Stub _enabled.
            monkeypatch.setattr(mod, "_enabled", lambda: False)
        elif module_path == "app.upgrade_lifecycle.trial_scheduler":
            monkeypatch.setattr(mod, "_enabled", lambda: False)
        else:
            continue
        result = start_fn()
        assert result is False, (
            f"{module_path}.{fn_name}() must return False when "
            f"declining to spawn, got {result!r}. Without this, the "
            f"watchdog cannot distinguish a clean decline from a crash."
        )

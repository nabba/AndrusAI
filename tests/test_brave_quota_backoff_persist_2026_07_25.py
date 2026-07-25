"""The Brave quota backoff must survive a process restart.

`_brave_quota_blocked_until` was a module global only, so every gateway restart
reset it to 0 and the next search re-probed a known-exhausted paid API, earned
another 402, and logged another 24h backoff that the next restart would also
forget. Observed three times on 2026-07-25 alone, matching that day's restarts.
A quota that resets monthly must not be re-probed on a process lifecycle.

See reports/GATE_DIAGNOSIS_2026-07-25.md.
"""
import importlib
import time

import pytest


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    """web_search with a temp state file and clean module state."""
    monkeypatch.setenv(
        "BRAVE_QUOTA_STATE_PATH", str(tmp_path / ".brave_quota_block"),
    )
    module = pytest.importorskip("app.tools.web_search")
    module = importlib.reload(module)
    return module


def _simulate_restart(module):
    """Reload the module the way a fresh gateway process would import it."""
    return importlib.reload(module)


def test_block_persists_across_a_restart(ws):
    deadline = time.time() + 3600
    ws._brave_quota_blocked_until = deadline
    ws._persist_brave_quota_block(deadline)

    fresh = _simulate_restart(ws)

    assert fresh._brave_quota_blocked_until == 0.0, "starts clean before load"
    assert fresh._brave_blocked_now() is True, (
        "a restart must NOT re-probe a quota-exhausted API"
    )


def test_expired_block_does_not_persist_as_blocked(ws):
    ws._persist_brave_quota_block(time.time() - 10)

    fresh = _simulate_restart(ws)

    assert fresh._brave_blocked_now() is False, "an elapsed window must reopen"


def test_no_state_file_means_not_blocked(ws):
    assert ws._brave_blocked_now() is False


def test_corrupt_state_file_fails_open(ws, tmp_path):
    with open(tmp_path / ".brave_quota_block", "w") as handle:
        handle.write("not-a-number")

    fresh = _simulate_restart(ws)

    assert fresh._brave_blocked_now() is False, (
        "a corrupt file must not disable search"
    )


def test_implausible_deadline_is_ignored(ws):
    """A corrupt far-future value must not disable Brave indefinitely."""
    ws._persist_brave_quota_block(time.time() + 400 * 24 * 3600)

    fresh = _simulate_restart(ws)

    assert fresh._brave_blocked_now() is False


def test_unwritable_path_does_not_raise(ws, monkeypatch):
    """Persistence is best-effort — it must never break a search call."""
    monkeypatch.setattr(ws, "_BRAVE_QUOTA_STATE_PATH", "/proc/nope/nope")
    ws._persist_brave_quota_block(time.time() + 3600)  # must not raise


def test_402_persists_the_block(ws, monkeypatch):
    """End-to-end: a 402 response writes the deadline to disk."""
    class _Resp:
        status_code = 402

        def raise_for_status(self):  # pragma: no cover
            raise AssertionError("should not be reached on 402")

    monkeypatch.setattr(ws, "get_brave_api_key", lambda: "dummy")
    monkeypatch.setattr(ws._session, "get", lambda *a, **k: _Resp())

    assert ws._search_brave_raw("anything", 5) is None
    assert ws._brave_blocked_now()

    fresh = _simulate_restart(ws)
    assert fresh._brave_blocked_now(), "the 402 block must outlive the process"


def test_load_is_idempotent_and_cheap(ws, monkeypatch):
    """_brave_blocked_now is called per search; it must read the file once."""
    reads = []
    real_open = open

    def counting_open(path, *a, **k):
        if str(path) == str(ws._BRAVE_QUOTA_STATE_PATH):
            reads.append(1)
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", counting_open)
    for _ in range(10):
        ws._brave_blocked_now()

    assert len(reads) <= 1, f"state file read {len(reads)} times, expected ≤1"

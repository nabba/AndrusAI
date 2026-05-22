"""2026-05-19 incident — chromadb in-process client wedged.

Pins the fix for: source_ledger_daemon alerting "Auto-replayed; upserted 0
rows in 0.1s" when the underlying chromadb client was wedged and every
collection open raised SQLite code 26 ("file is not a database"). The
on-disk file is healthy in this failure mode — the gateway just needs to
be restarted to rebuild its in-process state.

Covers:
  * replay_kb counts client_wedged_errors on get_or_create_collection() failure
  * replay_kb counts client_wedged_errors on upsert() failure
  * _is_client_wedged_exc recognizes the 3 chromadb wedge signatures
  * Daemon routes wedged-client replays to the distinct alert + topic key
  * Daemon's summary distinguishes wedged from genuinely-replayed KBs
"""
from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def ledger_module(tmp_path, monkeypatch):
    import app.paths as paths
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", tmp_path)
    import app.memory.source_ledger as sl
    importlib.reload(sl)
    return sl


def _make_kb(ws: Path, name: str) -> Path:
    kb = ws / name
    kb.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(kb / "chroma.sqlite3"))
    conn.execute("CREATE TABLE IF NOT EXISTS placeholder (id INTEGER)")
    conn.commit()
    conn.close()
    return kb


# Exception types observed in the 2026-05-19 production logs.
_CODE_26_OPEN = (
    "Error getting collection: Database error: error returned from "
    "database: (code: 26) file is not a database"
)
_CODE_26_SEGMENTS = "Error getting collection: Failed to get segments"


class _WedgedClient:
    """Fake chromadb client that raises the production wedge signature on
    every get_or_create_collection() call. Mirrors the 16:20:29 UTC pattern
    where the on-disk file was healthy but the in-process client was stuck.
    """
    def __init__(self, msg: str = _CODE_26_OPEN):
        self._msg = msg

    def get_or_create_collection(self, name):  # noqa: D401
        raise Exception(self._msg)


def _install_wedged_chromadb(monkeypatch, client):
    import sys
    class FakeMgr:
        def get_kb_client(self, name):
            return client
        def embed(self, text):
            return [0.0] * 384
    monkeypatch.setitem(sys.modules, "app.memory.chromadb_manager", FakeMgr())


# ────────────────────────────────────────────────────────────────────
#   _is_client_wedged_exc signature detector
# ────────────────────────────────────────────────────────────────────


def test_is_client_wedged_exc_catches_code_26(ledger_module):
    assert ledger_module._is_client_wedged_exc(Exception(_CODE_26_OPEN))


def test_is_client_wedged_exc_catches_failed_to_get_segments(ledger_module):
    assert ledger_module._is_client_wedged_exc(Exception(_CODE_26_SEGMENTS))


def test_is_client_wedged_exc_catches_bare_file_is_not_a_database(ledger_module):
    assert ledger_module._is_client_wedged_exc(Exception("file is not a database"))


def test_is_client_wedged_exc_negative_for_unrelated_errors(ledger_module):
    assert not ledger_module._is_client_wedged_exc(Exception("dimension mismatch"))
    assert not ledger_module._is_client_wedged_exc(Exception("disk full"))


# ────────────────────────────────────────────────────────────────────
#   replay_kb counts wedge errors
# ────────────────────────────────────────────────────────────────────


def test_replay_counts_wedge_errors_on_collection_open_failure(
    ledger_module, tmp_path, monkeypatch,
):
    kb = "memory"
    _make_kb(tmp_path, kb)
    # 3 distinct collections in the ledger — each will fail at open.
    ledger_module.append_row(kb, "alpha", "d1", "text-alpha", {})
    ledger_module.append_row(kb, "beta", "d2", "text-beta", {})
    ledger_module.append_row(kb, "gamma", "d3", "text-gamma", {})

    _install_wedged_chromadb(monkeypatch, _WedgedClient())
    result = ledger_module.replay_kb(kb)

    # Reproduces the production signature: rows seen, none upserted, every
    # collection open contributed a wedge error.
    assert result.rows_seen == 3
    assert result.rows_upserted == 0
    assert result.client_wedged_errors == 3
    assert "client_wedged_errors" in result.to_dict()


def test_replay_counts_wedge_errors_on_upsert_failure(
    ledger_module, tmp_path, monkeypatch,
):
    """Open succeeds, upsert fails — also a wedge symptom in chromadb 1.5.x."""
    kb = "memory"
    _make_kb(tmp_path, kb)
    ledger_module.append_row(kb, "c", "d1", "text", {})

    class _OpenOkUpsertFails:
        def get_or_create_collection(self, name):
            class _Col:
                def upsert(self, *, ids, documents, metadatas, embeddings):
                    raise Exception(_CODE_26_SEGMENTS)
            return _Col()
    _install_wedged_chromadb(monkeypatch, _OpenOkUpsertFails())

    result = ledger_module.replay_kb(kb)
    assert result.rows_seen == 1
    assert result.rows_upserted == 0
    assert result.client_wedged_errors == 1


def test_replay_does_not_count_wedge_for_dimension_errors(
    ledger_module, tmp_path, monkeypatch,
):
    """A genuine dimension error is NOT a wedged client — it's a real issue
    with the embedding pipeline. We must not collapse these into the
    "restart the gateway" recovery path.
    """
    kb = "memory"
    _make_kb(tmp_path, kb)
    ledger_module.append_row(kb, "c", "d1", "text", {})

    class _DimErr:
        def get_or_create_collection(self, name):
            raise Exception("Collection expecting embedding with dimension of 768, got 384")
    _install_wedged_chromadb(monkeypatch, _DimErr())
    result = ledger_module.replay_kb(kb)
    assert result.client_wedged_errors == 0


# ────────────────────────────────────────────────────────────────────
#   Daemon routing — _looks_client_wedged + alert selection
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def daemon_module(tmp_path, monkeypatch):
    import app.paths as paths
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", tmp_path)
    import app.memory.source_ledger as sl
    importlib.reload(sl)
    import app.memory.source_ledger_daemon as d
    importlib.reload(d)
    return d


def _make_replay_result(sl_mod, **kwargs):
    """Helper to construct ReplayResult with default 'ok'/'kb_name' filled."""
    defaults = {"ok": True, "kb_name": "memory"}
    defaults.update(kwargs)
    return sl_mod.ReplayResult(**defaults)


def test_looks_client_wedged_positive_match(daemon_module):
    import app.memory.source_ledger as sl
    replay = _make_replay_result(sl,
        rows_seen=2068, rows_upserted=0, client_wedged_errors=24,
    )
    assert daemon_module._looks_client_wedged(replay)


def test_looks_client_wedged_negative_when_nothing_attempted(daemon_module):
    """Empty ledger (rows_seen=0) is not a wedged-client signal — it's just
    nothing to replay. Otherwise we'd false-alarm on every empty KB.
    """
    import app.memory.source_ledger as sl
    replay = _make_replay_result(sl,
        rows_seen=0, rows_upserted=0, client_wedged_errors=0,
    )
    assert not daemon_module._looks_client_wedged(replay)


def test_looks_client_wedged_negative_when_some_upserts_landed(daemon_module):
    """Mixed success — replay landed something — is not 'wedged' even if a
    subset failed. The client was at least partially functional.
    """
    import app.memory.source_ledger as sl
    replay = _make_replay_result(sl,
        rows_seen=100, rows_upserted=50, client_wedged_errors=3,
    )
    assert not daemon_module._looks_client_wedged(replay)


def test_looks_client_wedged_negative_for_non_wedge_failures(daemon_module):
    """Replay tried, upserted nothing, but the failures weren't wedge-shaped
    (e.g. embedding errors). Don't tell the operator to restart for this.
    """
    import app.memory.source_ledger as sl
    replay = _make_replay_result(sl,
        rows_seen=100, rows_upserted=0, client_wedged_errors=0,
    )
    assert not daemon_module._looks_client_wedged(replay)


def test_alerts_have_distinct_topic_keys(daemon_module, monkeypatch):
    """Routine drift and client-wedged drift must NOT dedup against each
    other via the arbiter — the wedged variant is actionable and should
    always reach the operator even if a routine drift alert was emitted
    recently.
    """
    sent: list[dict] = []

    def fake_send(body, *, tag):
        sent.append({"body": body, "tag": tag})

    import sys
    class _FakeCommon:
        send_signal_alert = staticmethod(fake_send)
    monkeypatch.setitem(sys.modules, "app.life_companion._common", _FakeCommon())

    import app.memory.source_ledger as sl

    class _Drift:
        ledger_rows = 2068
        kb_rows_total = 0

    replay = _make_replay_result(sl,
        rows_seen=2068, rows_upserted=0, client_wedged_errors=24,
        duration_s=0.1,
    )

    daemon_module._alert_drift_replay("memory", _Drift(), replay)
    daemon_module._alert_drift_replay_client_wedged("memory", _Drift(), replay)

    assert len(sent) == 2
    tags = {row["tag"] for row in sent}
    assert tags == {
        "source_ledger_drift:memory",
        "source_ledger_client_wedged:memory",
    }
    # The wedged alert must NOT claim a successful replay.
    wedged_body = next(r["body"] for r in sent
                       if r["tag"] == "source_ledger_client_wedged:memory")
    assert "Auto-replayed" not in wedged_body
    assert "Restart the gateway" in wedged_body


# ────────────────────────────────────────────────────────────────────
#   2026-05-22 — In-process wedge recovery via PersistentClient recycle
# ────────────────────────────────────────────────────────────────────


def test_recycle_client_drops_memory_default_and_clears_caches():
    """Recycling the default (memory) client must clear _client AND
    _collections + _count_cache — those caches reference the dropped
    client object and would otherwise leak stale handles.
    """
    import app.memory.chromadb_manager as mgr
    mgr._client = "stale-client-sentinel"  # arbitrary truthy non-None — no close()
    mgr._collections["alpha"] = object()
    mgr._collections["beta"] = object()
    mgr._count_cache["alpha"] = 42

    info = mgr.recycle_client("memory")

    assert info == {
        "recycled": "memory",
        "collections_cleared": 2,
        "closed": False,  # sentinel string has no close() — best-effort fails open
    }
    assert mgr._client is None
    assert mgr._collections == {}
    assert mgr._count_cache == {}


def test_recycle_client_drops_non_memory_kb_only():
    """Recycling a KB-rooted client must NOT touch the default _client
    or _collections (which are scoped to the memory KB only).
    """
    import app.memory.chromadb_manager as mgr
    mgr._client = "default-sentinel"
    mgr._collections["x"] = object()
    mgr._kb_clients["philosophy"] = object()
    mgr._kb_clients["episteme"] = object()

    info = mgr.recycle_client("philosophy")

    assert info == {
        "recycled": "philosophy",
        "collections_cleared": 0,
        "closed": False,
    }
    assert mgr._client == "default-sentinel"
    assert "x" in mgr._collections
    assert "philosophy" not in mgr._kb_clients
    assert "episteme" in mgr._kb_clients  # untouched

    # cleanup module state
    mgr._client = None
    mgr._collections.clear()
    mgr._kb_clients.clear()


def test_recycle_client_empty_name_recycles_default():
    """``recycle_client(None)`` and ``recycle_client("")`` must both
    target the default client (matches the ``get_kb_client`` empty-name
    convention).
    """
    import app.memory.chromadb_manager as mgr
    mgr._client = "stale"
    info = mgr.recycle_client(None)
    assert info["recycled"] == "memory"
    mgr._client = "stale"
    info = mgr.recycle_client("")
    assert info["recycled"] == "memory"
    mgr._client = None


def test_recycle_client_calls_close_when_available():
    """When the cached client exposes ``close()`` (chromadb 1.5.x does),
    recycle must call it BEFORE dropping the reference. Per chromadb's
    own docs, this releases the underlying System's SQLite handles —
    "particularly important for PersistentClient to avoid SQLite file
    locking issues", i.e. the wedge symptom this whole path recovers.
    """
    import app.memory.chromadb_manager as mgr

    closed = {"called": False}

    class _ClientWithClose:
        def close(self):
            closed["called"] = True

    mgr._client = _ClientWithClose()
    info = mgr.recycle_client("memory")
    assert closed["called"] is True
    assert info["closed"] is True
    assert mgr._client is None


def test_recycle_client_swallows_close_exception():
    """A close() that raises must not block the recycle — the cached
    reference still gets dropped so the next access creates a fresh
    client. Returns ``closed: False`` so the caller can observe that
    close went sideways.
    """
    import app.memory.chromadb_manager as mgr

    class _ClientWithBadClose:
        def close(self):
            raise RuntimeError("simulated chromadb close failure")

    mgr._client = _ClientWithBadClose()
    info = mgr.recycle_client("memory")
    assert info["closed"] is False
    assert mgr._client is None  # reference dropped regardless


def _install_fake_chromadb_manager_attrs(monkeypatch, *, client, recycle_calls):
    """Patch the SPECIFIC functions on the real ``app.memory.chromadb_manager``
    module. Avoids the sys.modules-vs-attribute-cache issue that bites
    ``monkeypatch.setitem(sys.modules, ...)`` once a parent package has
    already cached the submodule as an attribute.
    """
    import app.memory.chromadb_manager as mgr

    def fake_get_kb_client(name):
        return client["current"]

    def fake_embed(text):
        return [0.0] * 384

    def fake_recycle(name):
        recycle_calls.append(name)
        client["current"] = client["after_recycle"]
        return {"recycled": name or "memory", "collections_cleared": 5}

    monkeypatch.setattr(mgr, "get_kb_client", fake_get_kb_client)
    monkeypatch.setattr(mgr, "embed", fake_embed)
    monkeypatch.setattr(mgr, "recycle_client", fake_recycle, raising=False)


class _OkClient:
    """Mirror of the production happy path — every collection opens
    cleanly, upsert succeeds.
    """
    def get_or_create_collection(self, name):
        class _Col:
            def upsert(self, *, ids, documents, metadatas, embeddings):
                return None
        return _Col()


def test_daemon_helper_returns_recycle_info_on_success(daemon_module, monkeypatch):
    """``_try_recycle_chromadb_client`` proxies to chromadb_manager and
    returns the recycle info dict on success.
    """
    import app.memory.chromadb_manager as mgr

    def fake_recycle(name):
        return {"recycled": name or "memory", "collections_cleared": 3}

    monkeypatch.setattr(mgr, "recycle_client", fake_recycle, raising=False)
    result = daemon_module._try_recycle_chromadb_client("memory")
    assert result == {"recycled": "memory", "collections_cleared": 3}


def test_daemon_helper_returns_none_when_recycle_missing(daemon_module, monkeypatch):
    """If ``chromadb_manager`` exists but lacks ``recycle_client`` (older
    test fakes that pre-date the 2026-05-22 fix), the helper must return
    None — never raise.
    """
    import app.memory.chromadb_manager as mgr
    monkeypatch.delattr(mgr, "recycle_client", raising=False)
    assert daemon_module._try_recycle_chromadb_client("memory") is None


def test_daemon_helper_returns_none_when_recycle_raises(daemon_module, monkeypatch):
    """Exceptions from recycle_client must be swallowed — auto-recovery
    must never break the daemon loop.
    """
    import app.memory.chromadb_manager as mgr

    def boom(name):
        raise RuntimeError("simulated chromadb manager failure")

    monkeypatch.setattr(mgr, "recycle_client", boom, raising=False)
    assert daemon_module._try_recycle_chromadb_client("memory") is None


def _patch_runtime_settings_gate(monkeypatch, *, recycle_enabled=True):
    """Patch the runtime_settings getter the daemon's `_gate` helper calls.
    Other gates are left at their real defaults — they're either Truthy by
    default or unread in the test path.
    """
    import app.runtime_settings as rs
    monkeypatch.setattr(
        rs,
        "get_chromadb_client_recycle_on_wedge_enabled",
        lambda: recycle_enabled,
        raising=False,
    )


def _patch_signal_sink(monkeypatch):
    """Replace the Signal alert sender with an in-memory list. Returns
    the list — every alert during the test appends a dict to it.
    """
    sent: list[dict] = []

    def fake_send(body, *, tag):
        sent.append({"body": body, "tag": tag})

    import app.life_companion._common as common
    monkeypatch.setattr(common, "send_signal_alert", fake_send, raising=False)
    return sent


def test_recycle_path_recovers_when_second_client_is_healthy(
    daemon_module, tmp_path, monkeypatch,
):
    """Full integration of the wedge recovery path: first replay wedges,
    daemon recycles, second replay (with the healthy post-recycle client)
    succeeds. The routine drift alert fires — NOT the operator-restart
    one.
    """
    # First client is wedged on every collection open; after recycle,
    # the next call returns the healthy client.
    state = {"current": _WedgedClient(), "after_recycle": _OkClient()}
    recycle_calls: list[str] = []
    _install_fake_chromadb_manager_attrs(
        monkeypatch, client=state, recycle_calls=recycle_calls,
    )
    _patch_runtime_settings_gate(monkeypatch, recycle_enabled=True)
    sent = _patch_signal_sink(monkeypatch)

    # Build a minimal ledger with rows that will trigger drift on memory.
    import app.paths as paths
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", tmp_path)
    import importlib
    import app.memory.source_ledger as sl
    importlib.reload(sl)
    importlib.reload(daemon_module)
    _make_kb(tmp_path, "memory")
    for i in range(3):
        sl.append_row("memory", "alpha", f"d{i}", f"text-{i}", {})

    # Run one daemon pass.
    summary = daemon_module._run_one_pass()

    # Recycle was attempted exactly once.
    assert recycle_calls == ["memory"]
    drift = summary["drifts"]["memory"]
    assert drift["recycle_attempted"]["recycled"] == "memory"
    assert "replay_retry" in drift
    # The successful retry must NOT be flagged as deferred.
    assert drift.get("replay_deferred") != "client_wedged"
    # The routine drift alert fires, not the operator-restart one.
    tags = [r["tag"] for r in sent]
    assert "source_ledger_drift:memory" in tags
    assert "source_ledger_client_wedged:memory" not in tags


def test_recycle_path_falls_through_when_second_client_also_wedged(
    daemon_module, tmp_path, monkeypatch,
):
    """If even the freshly-recycled client is wedged, the daemon must
    fall through to the existing operator-restart alert — recycle did
    its best, but a fresh client also failing is the signal that
    something deeper is wrong.
    """
    # Both clients are wedged — recycle doesn't help.
    state = {"current": _WedgedClient(), "after_recycle": _WedgedClient()}
    recycle_calls: list[str] = []
    _install_fake_chromadb_manager_attrs(
        monkeypatch, client=state, recycle_calls=recycle_calls,
    )
    _patch_runtime_settings_gate(monkeypatch, recycle_enabled=True)
    sent = _patch_signal_sink(monkeypatch)

    import app.paths as paths
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", tmp_path)
    import importlib
    import app.memory.source_ledger as sl
    importlib.reload(sl)
    importlib.reload(daemon_module)
    _make_kb(tmp_path, "memory")
    for i in range(3):
        sl.append_row("memory", "alpha", f"d{i}", f"text-{i}", {})

    summary = daemon_module._run_one_pass()

    assert recycle_calls == ["memory"]
    drift = summary["drifts"]["memory"]
    assert drift["replay_deferred"] == "client_wedged"
    tags = [r["tag"] for r in sent]
    assert "source_ledger_client_wedged:memory" in tags
    # The routine alert must NOT fire when the recycled client also wedged.
    assert "source_ledger_drift:memory" not in tags


def test_recycle_path_skipped_when_master_switch_off(
    daemon_module, tmp_path, monkeypatch,
):
    """When ``chromadb_client_recycle_on_wedge_enabled=False``, the
    daemon must NOT attempt recycle — operator-restart alert fires
    directly. This is the kill switch for the auto-recovery path.
    """
    state = {"current": _WedgedClient(), "after_recycle": _OkClient()}
    recycle_calls: list[str] = []
    _install_fake_chromadb_manager_attrs(
        monkeypatch, client=state, recycle_calls=recycle_calls,
    )
    _patch_runtime_settings_gate(monkeypatch, recycle_enabled=False)
    sent = _patch_signal_sink(monkeypatch)

    import app.paths as paths
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", tmp_path)
    import importlib
    import app.memory.source_ledger as sl
    importlib.reload(sl)
    importlib.reload(daemon_module)
    _make_kb(tmp_path, "memory")
    for i in range(3):
        sl.append_row("memory", "alpha", f"d{i}", f"text-{i}", {})

    summary = daemon_module._run_one_pass()

    # Even though both clients were prepared, recycle is NEVER attempted.
    assert recycle_calls == []
    drift = summary["drifts"]["memory"]
    assert drift["replay_deferred"] == "client_wedged"
    assert "recycle_attempted" not in drift
    tags = [r["tag"] for r in sent]
    assert "source_ledger_client_wedged:memory" in tags

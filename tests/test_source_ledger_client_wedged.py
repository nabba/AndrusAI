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

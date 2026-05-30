"""Tests for the vendor_sunset blocklist-recording path.

Originally (Wave 0/1 #A4) the monitor filed one change-request per
sunset model against ``workspace/healing/sunset_models.json``. That
path is outside the change-request validator's allowed roots, so every
CR was guaranteed-rejected — and because a sunset model is a persistent
world-state, the monitor re-observed it weekly and re-filed (30
identical rejected CRs in a day).

2026-05-29: the monitor now records sunset models DIRECTLY and
idempotently to the runtime blocklist (one aggregate write, plus an
idempotent append to ``runtime_settings.chat_blocked_models`` — the
list the LLM selector actually consults). No change-request gate.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    from app.healing.monitors import vendor_sunset
    from app.healing.handlers import _common as _h_common

    monkeypatch.setattr(_h_common, "_STATE_DIR", tmp_path / "self_heal")
    monkeypatch.setattr(vendor_sunset, "audit_event", lambda *a, **k: None)

    sent: list[str] = []
    monkeypatch.setattr(vendor_sunset, "send_signal_alert",
                        lambda body, **kw: sent.append(body) or True)

    # Capture chat_blocked_models adds without touching live settings.
    blocked: list[str] = []
    import app.runtime_settings as rt

    def fake_add(model_name):
        name = (model_name or "").strip()
        if not name or name in blocked:
            return False
        blocked.append(name)
        return True

    monkeypatch.setattr(rt, "add_chat_blocked_model", fake_add)

    # Redirect the hardcoded sunset_models.json path into tmp.
    import app.healing.monitors.vendor_sunset as vs_mod
    from pathlib import Path as _Path

    block_file = tmp_path / "workspace" / "healing" / "sunset_models.json"
    real_path = _Path

    def patched_path(s):
        if isinstance(s, str) and s == "/app/workspace/healing/sunset_models.json":
            return block_file
        return real_path(s)

    monkeypatch.setattr(vs_mod, "Path", patched_path)

    yield tmp_path, sent, blocked, block_file


def test_record_sunset_models_writes_blocklist_and_runtime(isolated):
    """A new finding is appended to sunset_models.json AND to the
    runtime chat blocklist the selector consults."""
    tmp_path, sent, blocked, block_file = isolated
    from app.healing.monitors import vendor_sunset

    recorded = vendor_sunset._record_sunset_models([
        {"provider": "openai", "model": "gpt-3.5-turbo-0301",
         "first_missed_at": 1700000000},
    ])
    assert recorded == ["openai::gpt-3.5-turbo-0301"]
    # Runtime blocklist updated (the consumed path).
    assert "gpt-3.5-turbo-0301" in blocked
    # Audit file written once with the finding.
    payload = json.loads(block_file.read_text())
    models = {e["model"] for e in payload["sunset"]}
    assert "gpt-3.5-turbo-0301" in models


def test_record_sunset_models_is_idempotent(isolated):
    """If the model is already recorded, don't duplicate the audit row
    and report nothing newly recorded."""
    tmp_path, sent, blocked, block_file = isolated
    from app.healing.monitors import vendor_sunset

    block_file.parent.mkdir(parents=True, exist_ok=True)
    block_file.write_text(json.dumps({
        "sunset": [
            {"provider": "openai", "model": "gpt-3.5-turbo-0301"},
        ],
    }))

    recorded = vendor_sunset._record_sunset_models([
        {"provider": "openai", "model": "gpt-3.5-turbo-0301"},
    ])
    assert recorded == []  # nothing newly recorded
    payload = json.loads(block_file.read_text())
    # Still exactly one audit row — no duplicate.
    assert len(payload["sunset"]) == 1


def test_record_sunset_models_aggregates_one_write(isolated, monkeypatch):
    """Multiple new findings collapse into a SINGLE file write — not
    one write (or one CR) per model."""
    tmp_path, sent, blocked, block_file = isolated
    from app.healing.monitors import vendor_sunset

    writes = {"n": 0}
    real_write_text = type(block_file).write_text

    def counting_write_text(self, *a, **k):
        writes["n"] += 1
        return real_write_text(self, *a, **k)

    monkeypatch.setattr(type(block_file), "write_text", counting_write_text)

    recorded = vendor_sunset._record_sunset_models([
        {"provider": "openai", "model": "m1"},
        {"provider": "anthropic", "model": "m2"},
        {"provider": "openrouter", "model": "m3"},
    ])
    assert set(recorded) == {"openai::m1", "anthropic::m2", "openrouter::m3"}
    assert writes["n"] == 1  # ONE aggregate write for all three
    assert {"m1", "m2", "m3"}.issubset(set(blocked))


def test_record_sunset_models_does_not_file_change_request(isolated, monkeypatch):
    """Regression: the recording path must NOT go through the
    change-request gate (that was the doomed-CR bug)."""
    tmp_path, sent, blocked, block_file = isolated
    from app.healing.monitors import vendor_sunset
    from app.healing.handlers import _common as _h_common

    called = {"n": 0}
    monkeypatch.setattr(
        _h_common, "file_change_request",
        lambda **kw: called.__setitem__("n", called["n"] + 1) or "cr-x",
    )

    vendor_sunset._record_sunset_models([
        {"provider": "openai", "model": "gpt-x"},
    ])
    assert called["n"] == 0  # no CR filed

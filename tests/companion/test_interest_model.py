"""Tests for ``app.companion.interest_model`` (Phase B #1, 2026-05-09)."""
from __future__ import annotations

import json

import pytest


def test_tokenize_basic():
    from app.companion.interest_model import _tokenize
    out = _tokenize("Forest carbon sequestration in Estonia is a key topic.")
    # stopwords + short tokens dropped
    assert "forest" in out
    assert "carbon" in out
    assert "sequestration" in out
    assert "the" not in out
    assert "is" not in out


def test_bigrams():
    from app.companion.interest_model import _bigrams, _tokenize
    grams = _bigrams(_tokenize("forest carbon flux"))
    assert "forest carbon" in grams
    assert "carbon flux" in grams


def test_recency_weight_halflife():
    from app.companion.interest_model import _recency_weight
    fresh = _recency_weight(0.0)
    week_old = _recency_weight(7.0, halflife=7.0)
    two_weeks = _recency_weight(14.0, halflife=7.0)
    assert fresh == pytest.approx(1.0)
    assert week_old == pytest.approx(0.5)
    assert two_weeks == pytest.approx(0.25)


def test_score_terms_min_freq(monkeypatch):
    from app.companion import interest_model

    streams = {
        "convs": [
            ("forest carbon estonia", 0.0),
            ("forest carbon sequestration", 1.0),
        ],
        "emails": [],
        "events": [],
        "feedback": [],
        "affect": [],
    }
    scores = interest_model._score_terms(streams)
    # "forest carbon" appears in 2 docs → above MIN_FREQ
    assert "forest carbon" in scores
    assert "forest" in scores
    # Each term seen in only one doc — still scored, but counts == 1.
    assert scores["forest carbon"]["sources"]["convs"] == 2


def test_diversity_bonus():
    from app.companion.interest_model import _diversity_bonus
    assert _diversity_bonus({"convs": 1}) == 1.0
    assert _diversity_bonus({"convs": 1, "emails": 1}) == pytest.approx(1.1)
    assert _diversity_bonus({
        "convs": 1, "emails": 1, "events": 1, "affect": 1, "feedback": 1,
    }) == pytest.approx(1.4)


def test_compile_writes_profile(tmp_path, monkeypatch):
    from app.companion import interest_model
    monkeypatch.setattr(interest_model, "_PROFILE_PATH",
                        tmp_path / "interest_profile.json")

    monkeypatch.setattr(
        interest_model, "_conversations_text",
        lambda d: iter([
            ("forest carbon flux", 0.0),
            ("forest carbon flux estonia", 1.0),
            ("estonia winter forest data", 2.0),
        ]),
    )
    monkeypatch.setattr(
        interest_model, "_email_subject_text", lambda d: iter([]),
    )
    monkeypatch.setattr(
        interest_model, "_calendar_titles_text", lambda d: iter([]),
    )
    monkeypatch.setattr(
        interest_model, "_feedback_events_text", lambda d: iter([]),
    )
    monkeypatch.setattr(
        interest_model, "_affect_topics_text", lambda d: iter([]),
    )

    profile = interest_model.compile_interest_profile(lookback_days=14)
    assert (tmp_path / "interest_profile.json").exists()
    on_disk = json.loads((tmp_path / "interest_profile.json").read_text())
    assert on_disk["topics"]
    names = [t["name"] for t in on_disk["topics"]]
    assert any("forest" in n for n in names)


def test_current_profile_missing(tmp_path, monkeypatch):
    from app.companion import interest_model
    monkeypatch.setattr(interest_model, "_PROFILE_PATH",
                        tmp_path / "no-such-file.json")
    p = interest_model.current_profile()
    assert p["topics"] == []


def test_run_respects_cadence(tmp_path, monkeypatch):
    from app.companion import interest_model
    from app.life_companion import _common
    monkeypatch.setattr(_common, "_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(interest_model, "background_enabled", lambda: True)
    monkeypatch.setattr(interest_model, "_PROFILE_PATH",
                        tmp_path / "interest_profile.json")

    calls = []
    def fake_compile(lookback_days=14):
        calls.append(1)
        return {"generated_at": "x", "lookback_days": 14, "topics": []}
    monkeypatch.setattr(interest_model, "compile_interest_profile", fake_compile)

    interest_model.run()
    interest_model.run()  # second within cadence — skipped
    assert len(calls) == 1


# ── Collector regression pins (2026-05-24) ───────────────────────────────
#
# These three tests pin the schemas the collectors depend on. Before
# 2026-05-24 the three silent collectors were quietly mismatched against
# the actual writers — see _conversations_text (created_at vs ts column),
# _email_subject_text (recent_top_subjects vs last_top key), and
# _affect_topics_text (topic/subject vs task_preview key). All three
# failed silently because their except blocks swallow the schema error.
# These tests trip in CI if a future writer drifts again.


def test_conversations_collector_reads_ts_column(tmp_path, monkeypatch):
    """Regression: conversation_store.messages uses ``ts`` not ``created_at``.

    Mirrors the real schema in app/conversation_store.py — keeping this
    test in sync requires whoever changes the messages table to also
    update this fixture, which surfaces the dependency.
    """
    import sqlite3
    from app.companion import interest_model

    db_path = tmp_path / "conversations.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            ts TEXT NOT NULL
        );
    """)
    # 1 row inside the 14-day window, 1 row outside.
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    inside = (now - timedelta(days=2)).isoformat()
    outside = (now - timedelta(days=60)).isoformat()
    conn.execute(
        "INSERT INTO messages (sender_id, role, content, ts) VALUES (?,?,?,?)",
        ("u1", "user", "forest carbon recent", inside),
    )
    conn.execute(
        "INSERT INTO messages (sender_id, role, content, ts) VALUES (?,?,?,?)",
        ("u1", "user", "ancient unrelated content", outside),
    )
    conn.commit()

    class _Stub:
        @staticmethod
        def _get_conn():
            return conn

    monkeypatch.setattr(interest_model, "conversation_store", _Stub, raising=False)
    # The collector imports lazily inside the function, so patch the
    # module-level import path it uses.
    import sys
    sys.modules["app.conversation_store"] = _Stub  # type: ignore[assignment]
    try:
        rows = list(interest_model._conversations_text(14))
    finally:
        sys.modules.pop("app.conversation_store", None)
    assert len(rows) == 1
    text, age = rows[0]
    assert "forest carbon recent" in text
    assert 1.0 < age < 3.0


def test_email_collector_reads_last_top_key(tmp_path, monkeypatch):
    """Regression: email_monitor writes ``last_top``, not ``recent_top_subjects``."""
    import json
    from app.companion import interest_model

    state_path = tmp_path / "email_monitor.json"
    state_path.write_text(json.dumps({
        "last_top": [
            {"subject": "Re: AI certification", "from": "alice@example.com"},
            {"subject": "Quarterly forest data", "from": "bob@example.com"},
        ],
    }), encoding="utf-8")

    monkeypatch.setattr(
        interest_model.Path,
        "exists",
        lambda self: True if str(self).endswith("email_monitor.json") else Path.exists(self),
        raising=False,
    )

    # Simpler: monkeypatch the path constant the collector reads.
    import app.companion.interest_model as im
    orig = im.Path
    def _path(p):
        if str(p).endswith("/email_monitor.json"):
            return state_path
        return orig(p)
    monkeypatch.setattr(im, "Path", _path)

    rows = list(interest_model._email_subject_text(14))
    subjects = [r[0] for r in rows]
    assert "Re: AI certification" in subjects
    assert "Quarterly forest data" in subjects


def test_affect_collector_reads_task_preview_key(tmp_path, monkeypatch):
    """Regression: app/affect/kb_metadata.py writes ``task_preview`` and an
    ISO-8601 ``ts`` string. Both shapes must work — the original
    ``float(row.get("ts", 0))`` aborted on the first ISO row."""
    import json
    import time
    from datetime import datetime, timezone, timedelta
    from app.companion import interest_model

    tags_path = tmp_path / "episode_affect_tags.jsonl"
    now = datetime.now(timezone.utc)
    fresh_iso = (now - timedelta(days=1)).isoformat()       # ISO-8601 (real writer shape)
    fresh_numeric = time.time() - 2 * 86400                  # forward-compat numeric
    stale_iso = (now - timedelta(days=60)).isoformat()
    tags_path.write_text(
        json.dumps({"ts": fresh_iso, "task_preview": "forest carbon flux estonia"}) + "\n"
        + json.dumps({"ts": fresh_numeric, "task_preview": "tallinn vantaa ferry"}) + "\n"
        + json.dumps({"ts": stale_iso, "task_preview": "ancient unrelated content"}) + "\n",
        encoding="utf-8",
    )

    import app.companion.interest_model as im
    orig = im.Path
    def _path(p):
        if str(p).endswith("/episode_affect_tags.jsonl"):
            return tags_path
        return orig(p)
    monkeypatch.setattr(im, "Path", _path)

    rows = list(interest_model._affect_topics_text(14))
    texts = [r[0] for r in rows]
    # Both fresh rows yielded; stale row excluded.
    assert "forest carbon flux estonia" in texts
    assert "tallinn vantaa ferry" in texts
    assert "ancient unrelated content" not in texts

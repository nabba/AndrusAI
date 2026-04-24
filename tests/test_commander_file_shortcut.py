"""Tests for ``Commander._try_answer_file_request`` — the fast-action
path that returns file contents without dispatching a crew.

Contract:
  * Positive patterns ("send me the report", "share the .md", etc.)
    return the file body.
  * Negative guards (requests implying NEW work — "research", "analyze")
    return None so the full router handles them.
  * Missing / empty workspace returns None (fails safe to the router).
  * Keyword filter picks the file matching named topics; falls back to
    newest .md when nothing matches.
"""
from __future__ import annotations

import pathlib
import time
from unittest.mock import patch

import pytest


@pytest.fixture
def commander():
    from app.agents.commander.orchestrator import Commander
    # Minimal bypass: we only exercise _try_answer_file_request, which
    # is pure — no LLM / memory dependencies required.
    return Commander.__new__(Commander)  # skip full __init__


@pytest.fixture
def fake_workspace(tmp_path, monkeypatch):
    """Point the fast-action at a temp workspace with .md fixtures."""
    responses = tmp_path / "output" / "responses"
    responses.mkdir(parents=True)
    # Create 3 files with distinguishing content + staggered mtimes
    files = []
    for i, (name, body) in enumerate([
        ("response_oldest.md", "# Oldest report\n\n- about weather"),
        ("response_middle.md", "# Middle report\n\n- about PSPs and payment methods"),
        ("response_newest.md", "# Newest report\n\n- about something else entirely"),
    ]):
        p = responses / name
        p.write_text(body)
        # Stagger mtimes so sort order is deterministic
        mtime = time.time() - (3 - i) * 10
        import os
        os.utime(p, (mtime, mtime))
        files.append(p)
    # Patch the WORKSPACE constant used inside the fast-action
    monkeypatch.setattr(
        "app.tools.file_manager.WORKSPACE", tmp_path,
    )
    return tmp_path, files


# ══════════════════════════════════════════════════════════════════════
# Positive patterns — should return file body
# ══════════════════════════════════════════════════════════════════════

class TestPositiveMatches:

    @pytest.mark.parametrize("phrase", [
        "send me the report",
        "Send me the PSP report",
        "share the latest .md",
        "get me that file",
        "show me the latest response",
        "fetch the report",
        "deliver the latest output",
        "can you send me the file",
        "resend the md file",
        "attach the report please",
    ])
    def test_matches_return_content(self, commander, fake_workspace, phrase):
        out = commander._try_answer_file_request(phrase)
        assert out is not None, f"phrase {phrase!r} should have matched"
        assert out.startswith("# "), "return value should be file content"

    def test_newest_file_selected_by_default(self, commander, fake_workspace):
        out = commander._try_answer_file_request("send me the latest report")
        assert "Newest report" in out


# ══════════════════════════════════════════════════════════════════════
# Keyword filter — picks the file matching the named topic
# ══════════════════════════════════════════════════════════════════════

class TestKeywordFilter:

    def test_psp_keyword_picks_middle_file(self, commander, fake_workspace):
        """'PSPs' is only in response_middle.md; newest doesn't contain it.
        The keyword scan should prefer the matching file over the newest."""
        out = commander._try_answer_file_request("send me the PSP report")
        assert "Middle report" in out
        assert "PSPs" in out

    def test_weather_keyword_picks_oldest(self, commander, fake_workspace):
        out = commander._try_answer_file_request("send me the weather file")
        assert "Oldest report" in out
        assert "weather" in out

    def test_unmatched_keyword_falls_back_to_newest(
        self, commander, fake_workspace,
    ):
        """A keyword that doesn't appear in any file falls back to the
        newest — the "send me the LATEST" default."""
        out = commander._try_answer_file_request("send me the dinosaur report")
        assert "Newest report" in out


# ══════════════════════════════════════════════════════════════════════
# Negative guards — "new work" requests must NOT shortcut
# ══════════════════════════════════════════════════════════════════════

class TestNegativeGuards:

    @pytest.mark.parametrize("phrase", [
        "please do an extensive research about PSPs",
        "analyze the PSP landscape in CEE",
        "find out which PSPs service Estonia",
        "compile a list of Polish PSPs",
        "generate a report on fintech valuations",
        "create a new document summarizing X",
        "build me a presentation",
        "write me an email about X",
        "look up the current stock price",
    ])
    def test_new_work_requests_return_none(self, commander, fake_workspace, phrase):
        out = commander._try_answer_file_request(phrase)
        assert out is None, f"phrase {phrase!r} should NOT shortcut"

    def test_long_message_returns_none(self, commander, fake_workspace):
        # >120 chars — too complex for shortcut even if patterns match
        phrase = "send me the report " * 20
        out = commander._try_answer_file_request(phrase)
        assert out is None


class TestNoMatchFallsThrough:

    def test_unrelated_question_returns_none(self, commander, fake_workspace):
        assert (
            commander._try_answer_file_request("what's the weather in Helsinki?")
            is None
        )

    def test_empty_input_returns_none(self, commander, fake_workspace):
        assert commander._try_answer_file_request("") is None
        assert commander._try_answer_file_request("   ") is None


# ══════════════════════════════════════════════════════════════════════
# Missing / empty workspace
# ══════════════════════════════════════════════════════════════════════

class TestMissingWorkspace:

    def test_missing_responses_dir_returns_none(
        self, commander, tmp_path, monkeypatch,
    ):
        # tmp_path exists but output/responses/ does not
        monkeypatch.setattr(
            "app.tools.file_manager.WORKSPACE", tmp_path,
        )
        assert (
            commander._try_answer_file_request("send me the report")
            is None
        )

    def test_empty_responses_dir_returns_none(
        self, commander, tmp_path, monkeypatch,
    ):
        (tmp_path / "output" / "responses").mkdir(parents=True)
        monkeypatch.setattr(
            "app.tools.file_manager.WORKSPACE", tmp_path,
        )
        assert (
            commander._try_answer_file_request("send me the report")
            is None
        )

    def test_only_non_md_files_returns_none(
        self, commander, tmp_path, monkeypatch,
    ):
        r = tmp_path / "output" / "responses"
        r.mkdir(parents=True)
        (r / "random.txt").write_text("not md")
        monkeypatch.setattr(
            "app.tools.file_manager.WORKSPACE", tmp_path,
        )
        assert (
            commander._try_answer_file_request("send me the report")
            is None
        )

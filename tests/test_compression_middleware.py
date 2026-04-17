"""Tests for CompressionMiddleware in app/history_compression.py."""
from unittest.mock import MagicMock

import pytest

from tests._v2_shim import install_settings_shim

install_settings_shim()

from app.history_compression import (  # noqa: E402
    CompressionMiddleware, _histories, clear_history, get_history,
)


def _reset():
    _histories.clear()


class _DummyCommander:
    def __init__(self, response="mock reply"):
        self.response = response
        self.handle_calls = []
        self.last_crew_used = "dummy_crew"
        self.last_model_used = "claude-sonnet"
        self._internal_attr = 42

    def handle(self, text, sender, attachments):
        self.handle_calls.append((text, sender, list(attachments)))
        return self.response


class TestCompressionMiddleware:
    def setup_method(self):
        _reset()

    def test_handle_returns_commander_result(self):
        inner = _DummyCommander(response="fancy answer")
        mw = CompressionMiddleware(inner)
        out = mw.handle("hello", "+15551112222", [])
        assert out == "fancy answer"

    def test_handle_records_user_and_assistant_in_compressed_history(self):
        inner = _DummyCommander(response="assistant reply")
        mw = CompressionMiddleware(inner)
        sender = "+15551112222"
        mw.handle("user message here", sender, [])

        from app.security import _sender_hash
        h = get_history(_sender_hash(sender))
        # Current topic should have user + assistant exchange
        contents = [m.content for m in h.current.messages]
        assert "user message here" in contents
        assert "assistant reply" in contents

    def test_handle_defaults_none_attachments_to_empty_list(self):
        inner = _DummyCommander()
        mw = CompressionMiddleware(inner)
        mw.handle("t", "s", None)
        assert inner.handle_calls[0][2] == []

    def test_getattr_proxies_commander_attributes(self):
        inner = _DummyCommander()
        mw = CompressionMiddleware(inner)
        assert mw.last_crew_used == "dummy_crew"
        assert mw.last_model_used == "claude-sonnet"
        assert mw._internal_attr == 42

    def test_getattr_raises_when_missing_on_both(self):
        inner = _DummyCommander()
        mw = CompressionMiddleware(inner)
        with pytest.raises(AttributeError):
            mw.totally_missing_attr

    def test_hasattr_returns_false_for_missing(self):
        inner = _DummyCommander()
        mw = CompressionMiddleware(inner)
        assert hasattr(mw, "last_crew_used")
        assert not hasattr(mw, "nonexistent_attribute_xyz")

    def test_disabled_compression_skips_history(self):
        install_settings_shim(history_compression_enabled=False)
        inner = _DummyCommander()
        mw = CompressionMiddleware(inner)
        mw.handle("user text", "+15559998888", [])
        # History was not touched because shim says compression disabled
        from app.security import _sender_hash
        h = get_history(_sender_hash("+15559998888"))
        # Fresh topic stays empty since middleware skipped when flag off
        assert h.current.messages == []
        # Re-enable for other tests
        install_settings_shim(history_compression_enabled=True)

    def test_commander_failure_surfaces_but_history_updated(self):
        """User message tracked even when commander raises."""
        inner = _DummyCommander()
        inner.handle = MagicMock(side_effect=RuntimeError("commander broke"))
        mw = CompressionMiddleware(inner)
        with pytest.raises(RuntimeError):
            mw.handle("pre-error text", "+15552223333", [])

        from app.security import _sender_hash
        h = get_history(_sender_hash("+15552223333"))
        # User message was recorded before the commander ran
        user_msgs = [m for m in h.current.messages if m.role == "user"]
        assert len(user_msgs) == 1
        assert user_msgs[0].content == "pre-error text"

    def test_pre_hook_failure_does_not_block_handle(self):
        inner = _DummyCommander(response="still works")
        mw = CompressionMiddleware(inner)
        # Sabotage get_history
        import app.history_compression as hc
        original = hc.get_history
        hc.get_history = MagicMock(side_effect=RuntimeError("hist broke"))
        try:
            out = mw.handle("text", "+15554445555", [])
            assert out == "still works"
        finally:
            hc.get_history = original

    def test_truncates_long_messages_to_4000_chars(self):
        inner = _DummyCommander()
        mw = CompressionMiddleware(inner)
        very_long = "a" * 5000
        mw.handle(very_long, "+15556667777", [])
        from app.security import _sender_hash
        h = get_history(_sender_hash("+15556667777"))
        user_msg = h.current.messages[0]
        assert len(user_msg.content) == 4000

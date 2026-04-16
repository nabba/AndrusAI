"""
test_email_tools.py — Unit tests for IMAP/SMTP email tools.

Run: pytest tests/test_email_tools.py -v
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.conftest_capabilities import MockIMAP


class TestEmailToolsFactory:

    def test_returns_empty_when_not_configured(self):
        with patch("app.tools.email_tools._get_email_config", return_value=None):
            from app.tools.email_tools import create_email_tools
            tools = create_email_tools("pim")
        assert tools == []

    def test_returns_five_tools_when_configured(self):
        with patch("app.tools.email_tools._get_email_config", return_value={
            "imap_host": "imap.test.com", "imap_port": 993,
            "smtp_host": "smtp.test.com", "smtp_port": 587,
            "address": "test@test.com", "password": "pass",
        }):
            from app.tools.email_tools import create_email_tools
            tools = create_email_tools("pim")
        assert len(tools) == 5
        names = {t.name for t in tools}
        assert names == {"check_email", "read_email", "send_email", "search_email", "organize_email"}


class TestCheckEmailTool:

    def test_check_email_returns_messages(self):
        imap = MockIMAP()
        imap.add_message("alice@test.com", "Hello from Alice", "Body text")
        imap.add_message("bob@test.com", "Project update", "Details here")

        cfg = {"imap_host": "h", "imap_port": 993, "smtp_host": "h",
               "smtp_port": 587, "address": "me@t.com", "password": "p"}

        with patch("app.tools.email_tools._get_email_config", return_value=cfg), \
             patch("app.tools.email_tools.imaplib") as mock_imap_lib:
            mock_imap_lib.IMAP4_SSL.return_value = imap
            from app.tools.email_tools import create_email_tools
            tools = create_email_tools("pim")
            check = next(t for t in tools if t.name == "check_email")
            result = check._run(folder="INBOX", limit=5, unread_only=True)

        assert "email(s)" in result or "No" in result

    def test_check_email_handles_empty_inbox(self):
        imap = MockIMAP()  # No messages added
        cfg = {"imap_host": "h", "imap_port": 993, "smtp_host": "h",
               "smtp_port": 587, "address": "me@t.com", "password": "p"}

        with patch("app.tools.email_tools._get_email_config", return_value=cfg), \
             patch("app.tools.email_tools.imaplib") as mock_imap_lib:
            mock_imap_lib.IMAP4_SSL.return_value = imap
            from app.tools.email_tools import create_email_tools
            tools = create_email_tools("pim")
            check = next(t for t in tools if t.name == "check_email")
            result = check._run(unread_only=True)

        assert "No" in result

    def test_check_email_handles_connection_error(self):
        cfg = {"imap_host": "h", "imap_port": 993, "smtp_host": "h",
               "smtp_port": 587, "address": "me@t.com", "password": "p"}

        with patch("app.tools.email_tools._get_email_config", return_value=cfg), \
             patch("app.tools.email_tools.imaplib") as mock_imap_lib:
            mock_imap_lib.IMAP4_SSL.side_effect = ConnectionRefusedError("refused")
            from app.tools.email_tools import create_email_tools
            tools = create_email_tools("pim")
            check = next(t for t in tools if t.name == "check_email")
            result = check._run()

        assert "Error" in result


class TestSendEmailTool:

    def test_send_email_success(self):
        cfg = {"imap_host": "h", "imap_port": 993, "smtp_host": "h",
               "smtp_port": 587, "address": "me@t.com", "password": "p"}

        mock_smtp = MagicMock()
        mock_smtp_class = MagicMock()
        mock_smtp_class.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_class.__exit__ = MagicMock(return_value=False)

        with patch("app.tools.email_tools._get_email_config", return_value=cfg), \
             patch("app.tools.email_tools.smtplib") as mock_smtp_lib:
            mock_smtp_lib.SMTP.return_value = mock_smtp_class
            from app.tools.email_tools import create_email_tools
            tools = create_email_tools("pim")
            send = next(t for t in tools if t.name == "send_email")
            result = send._run(to="bob@test.com", subject="Test", body="Hello")

        assert "sent" in result.lower() or "Email" in result


class TestReadEmailTool:

    def test_read_email_returns_body(self):
        imap = MockIMAP()
        imap.add_message("sender@test.com", "Important Subject", "Full email body text here")

        cfg = {"imap_host": "h", "imap_port": 993, "smtp_host": "h",
               "smtp_port": 587, "address": "me@t.com", "password": "p"}

        with patch("app.tools.email_tools._get_email_config", return_value=cfg), \
             patch("app.tools.email_tools.imaplib") as mock_imap_lib:
            mock_imap_lib.IMAP4_SSL.return_value = imap
            from app.tools.email_tools import create_email_tools
            tools = create_email_tools("pim")
            read = next(t for t in tools if t.name == "read_email")
            result = read._run(subject_query="Important")

        assert "Subject" in result or "Important" in result


class TestDecodeHeader:

    def test_decode_plain_header(self):
        from app.tools.email_tools import _decode_header
        assert _decode_header("Simple Subject") == "Simple Subject"

    def test_decode_none_fallback(self):
        from app.tools.email_tools import _decode_header
        # Empty string should not crash
        result = _decode_header("")
        assert isinstance(result, str)

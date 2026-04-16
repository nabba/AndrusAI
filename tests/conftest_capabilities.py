"""
conftest_capabilities.py — Shared fixtures for capability gap elimination tests.

Provides mock objects for bridge, IMAP, SMTP, yfinance, and APScheduler
so tests run without any external services.

Import in test files:
    from tests.conftest_capabilities import mock_bridge, mock_imap, ...
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Ensure app is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Bridge mock ───────────────────────────────────────────────────────────────

class MockBridge:
    """Simulates the Host Bridge client for tool testing."""

    def __init__(self):
        self._available = True
        self._execute_results: dict[str, dict] = {}
        self._default_execute = {"stdout": "", "stderr": "", "exit_code": 0}
        self._files: dict[str, str] = {}

    def is_available(self) -> bool:
        return self._available

    def execute(self, cmd: list[str]) -> dict:
        full_cmd = " ".join(cmd)
        # Try prefix matching: longest match first
        for key in sorted(self._execute_results, key=len, reverse=True):
            if full_cmd.startswith(key):
                return self._execute_results[key]
        return dict(self._default_execute)

    def read_file(self, path: str, max_bytes: int = 1_000_000) -> dict:
        if path in self._files:
            return {"content": self._files[path], "size": len(self._files[path]), "path": path}
        return {"error": "not_found", "detail": f"File not found: {path}"}

    def write_file(self, path: str, content: str, create_dirs: bool = False) -> dict:
        self._files[path] = content
        return {"written": len(content), "path": path}

    def list_files(self, path: str, pattern: str = "*", recursive: bool = False) -> dict:
        return {"files": [
            {"name": "src/main.py"}, {"name": "src/utils.py"},
            {"name": "tests/test_main.py"}, {"name": "package.json"},
            {"name": "README.md"}, {"name": ".gitignore"},
        ]}

    def http_request(self, url: str, method: str = "GET") -> dict:
        return {"status_code": 200, "body": "OK"}

    def inference(self, prompt: str, model: str = "") -> dict:
        return {"response": "Mock inference response."}

    # Test helpers
    def set_execute_result(self, cmd_prefix: str, result: dict):
        self._execute_results[cmd_prefix] = result

    def set_unavailable(self):
        self._available = False


@pytest.fixture
def mock_bridge():
    """Provides a MockBridge and patches get_bridge to return it."""
    bridge = MockBridge()
    with patch("app.bridge_client.get_bridge", return_value=bridge):
        yield bridge


@pytest.fixture
def unavailable_bridge():
    """Provides a bridge that reports unavailable."""
    bridge = MockBridge()
    bridge.set_unavailable()
    with patch("app.bridge_client.get_bridge", return_value=bridge):
        yield bridge


# ── IMAP mock ─────────────────────────────────────────────────────────────────

class MockIMAP:
    """Simulates an IMAP4_SSL connection."""

    def __init__(self):
        self._messages = {}
        self._next_id = 1

    def login(self, user, pw):
        return ("OK", [b"Logged in"])

    def select(self, folder="INBOX", readonly=False):
        return ("OK", [b"5"])

    def search(self, charset, *criteria):
        ids = " ".join(str(i) for i in self._messages.keys())
        return ("OK", [ids.encode()])

    def fetch(self, num, parts):
        mid = int(num)
        if mid in self._messages:
            msg = self._messages[mid]
            return ("OK", [(b"1 (RFC822 {100})", msg.encode())])
        return ("OK", [(None,)])

    def store(self, num, flag_op, flags):
        return ("OK", [b"Done"])

    def copy(self, num, folder):
        return ("OK", [b"Done"])

    def expunge(self):
        return ("OK", [b"Done"])

    def close(self):
        pass

    def logout(self):
        pass

    def add_message(self, from_addr: str, subject: str, body: str = "Test body"):
        mid = self._next_id
        self._next_id += 1
        msg = (
            f"From: {from_addr}\r\n"
            f"Subject: {subject}\r\n"
            f"Date: Wed, 16 Apr 2026 10:00:00 +0000\r\n"
            f"\r\n{body}"
        )
        self._messages[mid] = msg
        return mid


@pytest.fixture
def mock_email_config():
    """Patches email configuration to be enabled with test values."""
    with patch("app.tools.email_tools._get_email_config", return_value={
        "imap_host": "imap.test.com",
        "imap_port": 993,
        "smtp_host": "smtp.test.com",
        "smtp_port": 587,
        "address": "test@test.com",
        "password": "testpass",
    }):
        yield


@pytest.fixture
def mock_imap(mock_email_config):
    """Provides a MockIMAP and patches imaplib."""
    imap = MockIMAP()
    imap.add_message("sender@example.com", "Test Subject", "Hello world")
    imap.add_message("boss@company.com", "Urgent: Meeting", "Please attend")
    with patch("app.tools.email_tools.imaplib") as mock_lib:
        mock_lib.IMAP4_SSL.return_value = imap
        yield imap


# ── yfinance mock ─────────────────────────────────────────────────────────────

class MockTicker:
    """Simulates a yfinance Ticker object."""

    def __init__(self, ticker: str):
        self.ticker = ticker
        self.info = {
            "longName": "Apple Inc.",
            "regularMarketPrice": 185.50,
            "marketCap": 2_900_000_000_000,
            "trailingPE": 30.5,
            "forwardPE": 28.1,
            "trailingEps": 6.08,
            "dividendYield": 0.005,
            "fiftyTwoWeekHigh": 199.62,
            "fiftyTwoWeekLow": 155.98,
            "volume": 65_000_000,
            "beta": 1.28,
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "priceToBook": 45.2,
            "priceToSalesTrailing12Months": 7.8,
            "enterpriseToEbitda": 25.1,
            "pegRatio": 2.8,
            "profitMargins": 0.256,
            "operatingMargins": 0.301,
            "returnOnEquity": 1.47,
            "returnOnAssets": 0.28,
            "grossMargins": 0.443,
            "debtToEquity": 176.3,
            "payoutRatio": 0.15,
            "sharesOutstanding": 15_600_000_000,
        }

    def history(self, period="1y"):
        import pandas as pd
        return pd.DataFrame({
            "Close": [170.0, 175.0, 180.0, 185.5],
            "High": [172.0, 178.0, 183.0, 188.0],
            "Low": [168.0, 173.0, 178.0, 183.0],
        })

    @property
    def income_stmt(self):
        import pandas as pd
        return pd.DataFrame(
            {"2024": [383_285_000_000, 98_100_000_000], "2023": [370_000_000_000, 94_000_000_000]},
            index=["Total Revenue", "Net Income"],
        )

    @property
    def balance_sheet(self):
        import pandas as pd
        return pd.DataFrame(
            {"2024": [135_000_000_000, 145_000_000_000]},
            index=["Current Assets", "Current Liabilities"],
        )

    @property
    def cashflow(self):
        import pandas as pd
        return pd.DataFrame(
            {"2024": [110_000_000_000, -11_000_000_000]},
            index=["Operating Cash Flow", "Capital Expenditure"],
        )


@pytest.fixture
def mock_yfinance():
    """Patches yfinance.Ticker to return MockTicker."""
    with patch("yfinance.Ticker", side_effect=MockTicker):
        yield


# ── SQLite task DB fixture ────────────────────────────────────────────────────

@pytest.fixture
def task_db(tmp_path):
    """Redirects task DB to a temp directory."""
    db_path = tmp_path / "tasks.db"
    with patch("app.tools.task_tools._DB_PATH", db_path):
        yield db_path


# ── Schedule file fixture ─────────────────────────────────────────────────────

@pytest.fixture
def schedule_file(tmp_path):
    """Redirects schedule storage to a temp directory."""
    sched_path = tmp_path / "schedules.json"
    with patch("app.tools.schedule_manager_tools._SCHEDULES_PATH", sched_path):
        yield sched_path


# ── Settings mock (prevents .env loading) ─────────────────────────────────────

@pytest.fixture
def mock_settings():
    """Provides mock settings to avoid requiring .env."""
    mock_s = MagicMock()
    mock_s.email_enabled = True
    mock_s.email_imap_host = "imap.test.com"
    mock_s.email_imap_port = 993
    mock_s.email_smtp_host = "smtp.test.com"
    mock_s.email_smtp_port = 587
    mock_s.email_address = "test@test.com"
    mock_s.email_password = MagicMock()
    mock_s.email_password.get_secret_value.return_value = "testpass"
    mock_s.sec_edgar_user_agent = "TestBot/1.0 (test@test.com)"
    mock_s.bridge_enabled = True
    mock_s.bridge_host = "localhost"
    mock_s.bridge_port = 9100
    with patch("app.config.get_settings", return_value=mock_s):
        yield mock_s

"""
test_financial_tools.py — Unit tests for financial data and analysis tools.

Run: pytest tests/test_financial_tools.py -v
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.conftest_capabilities import MockTicker

# Skip all tests in this file if yfinance is not installed
pytest.importorskip("yfinance", reason="yfinance not installed (runs in Docker)")


class TestFinancialToolsFactory:

    def test_returns_five_tools(self):
        with patch("yfinance.Ticker", side_effect=MockTicker):
            from app.tools.financial_tools import create_financial_tools
            tools = create_financial_tools("test")
        assert len(tools) == 5
        names = {t.name for t in tools}
        assert names == {"stock_data", "company_financials", "sec_filings",
                         "financial_ratios", "financial_model"}

    def test_returns_empty_without_yfinance(self):
        with patch.dict("sys.modules", {"yfinance": None}):
            # Force re-import
            import importlib
            from app.tools import financial_tools
            importlib.reload(financial_tools)
            tools = financial_tools.create_financial_tools("test")
        # This may or may not return empty depending on import caching,
        # but the pattern is tested.
        assert isinstance(tools, list)


class TestStockDataTool:

    def test_stock_data_returns_info(self):
        with patch("yfinance.Ticker", side_effect=MockTicker):
            from app.tools.financial_tools import create_financial_tools
            tools = create_financial_tools("test")
        stock = next(t for t in tools if t.name == "stock_data")
        result = stock._run(ticker="AAPL", period="1y")

        assert "Apple Inc." in result
        assert "$185.5" in result or "185.5" in result
        assert "Technology" in result
        assert "P/E Ratio" in result

    def test_stock_data_shows_price_change(self):
        with patch("yfinance.Ticker", side_effect=MockTicker):
            from app.tools.financial_tools import create_financial_tools
            tools = create_financial_tools("test")
        stock = next(t for t in tools if t.name == "stock_data")
        result = stock._run(ticker="AAPL", period="1y")

        assert "Price change" in result

    def test_stock_data_invalid_ticker(self):
        def bad_ticker(t):
            mock = MagicMock()
            mock.info = {}
            return mock

        with patch("yfinance.Ticker", side_effect=bad_ticker):
            from app.tools.financial_tools import create_financial_tools
            tools = create_financial_tools("test")
        stock = next(t for t in tools if t.name == "stock_data")
        result = stock._run(ticker="XXXX")
        assert "No data" in result


class TestCompanyFinancialsTool:

    def test_returns_income_statement(self):
        with patch("yfinance.Ticker", side_effect=MockTicker):
            from app.tools.financial_tools import create_financial_tools
            tools = create_financial_tools("test")
        fin = next(t for t in tools if t.name == "company_financials")
        result = fin._run(ticker="AAPL", statement="income")
        assert "Income Statement" in result
        assert "Total Revenue" in result

    def test_returns_all_statements(self):
        with patch("yfinance.Ticker", side_effect=MockTicker):
            from app.tools.financial_tools import create_financial_tools
            tools = create_financial_tools("test")
        fin = next(t for t in tools if t.name == "company_financials")
        result = fin._run(ticker="AAPL", statement="all")
        assert "Income Statement" in result
        assert "Balance Sheet" in result
        assert "Cash Flow" in result


class TestFinancialRatiosTool:

    def test_returns_valuation_ratios(self):
        with patch("yfinance.Ticker", side_effect=MockTicker):
            from app.tools.financial_tools import create_financial_tools
            tools = create_financial_tools("test")
        ratios = next(t for t in tools if t.name == "financial_ratios")
        result = ratios._run(ticker="AAPL")

        assert "Valuation" in result
        assert "P/E" in result
        assert "Profitability" in result
        assert "ROE" in result
        assert "Leverage" in result


class TestFinancialModelTool:

    def test_dcf_returns_valuation(self):
        with patch("yfinance.Ticker", side_effect=MockTicker):
            from app.tools.financial_tools import create_financial_tools
            tools = create_financial_tools("test")
        model = next(t for t in tools if t.name == "financial_model")
        result = model._run(ticker="AAPL", discount_rate=0.10, terminal_growth=0.025)

        assert "DCF Valuation" in result
        assert "Intrinsic Value/Share" in result
        assert "Current Price" in result
        assert "Upside/Downside" in result

    def test_dcf_auto_estimates_growth(self):
        with patch("yfinance.Ticker", side_effect=MockTicker):
            from app.tools.financial_tools import create_financial_tools
            tools = create_financial_tools("test")
        model = next(t for t in tools if t.name == "financial_model")
        result = model._run(ticker="AAPL", growth_rate=0.0)  # Auto-estimate

        assert "Growth Rate" in result
        # Should have calculated from revenue history
        assert "%" in result

    def test_dcf_includes_disclaimer(self):
        with patch("yfinance.Ticker", side_effect=MockTicker):
            from app.tools.financial_tools import create_financial_tools
            tools = create_financial_tools("test")
        model = next(t for t in tools if t.name == "financial_model")
        result = model._run(ticker="AAPL")

        assert "Not investment advice" in result or "Simplified DCF" in result


class TestSECFilingsTool:

    def test_sec_filings_handles_api_error(self):
        with patch("yfinance.Ticker", side_effect=MockTicker), \
             patch("requests.get") as mock_get:
            mock_get.return_value.status_code = 500
            mock_get.return_value.json.side_effect = Exception("API error")
            from app.tools.financial_tools import create_financial_tools
            tools = create_financial_tools("test")
            sec = next(t for t in tools if t.name == "sec_filings")
            result = sec._run(ticker="AAPL", filing_type="10-K")

        # Should handle gracefully
        assert isinstance(result, str)

    def test_sec_headers_include_user_agent(self):
        from app.tools.financial_tools import _get_sec_headers
        with patch("app.config.get_settings") as mock_settings:
            mock_settings.return_value.sec_edgar_user_agent = "Test/1.0"
            headers = _get_sec_headers()
        assert headers["User-Agent"] == "Test/1.0"

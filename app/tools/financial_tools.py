"""
financial_tools.py — Financial data and analysis tools.

Data sources:
  - yfinance: market data, fundamentals, financials (MIT, no API key)
  - SEC EDGAR: regulatory filings (free REST API, no API key)
  - Pure Python: financial ratios, DCF modeling

Usage:
    from app.tools.financial_tools import create_financial_tools
    tools = create_financial_tools("financial")
"""

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

_SEC_EDGAR_BASE = "https://efts.sec.gov/LATEST"
_SEC_COMPANY_API = "https://data.sec.gov"


def _get_sec_headers() -> dict:
    """SEC EDGAR requires User-Agent with contact info."""
    try:
        from app.config import get_settings
        ua = get_settings().sec_edgar_user_agent
    except Exception:
        ua = "BotArmy/1.0 (contact@example.com)"
    return {"User-Agent": ua, "Accept": "application/json"}


def create_financial_tools(agent_id: str) -> list:
    """Create financial data tools. Returns [] if yfinance not installed."""
    try:
        import yfinance  # noqa: F401
    except ImportError:
        logger.debug("financial_tools: yfinance not installed")
        return []

    try:
        from crewai.tools import BaseTool
        from pydantic import BaseModel, Field
        from typing import Type
    except ImportError:
        return []

    # ── Tool definitions ──────────────────────────────────────────

    class _StockDataInput(BaseModel):
        ticker: str = Field(description="Stock ticker symbol (e.g. AAPL, MSFT, TSLA)")
        period: str = Field(
            default="1y",
            description="Price history period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, max",
        )

    class StockDataTool(BaseTool):
        name: str = "stock_data"
        description: str = (
            "Get stock market data: current price, price history, market cap, "
            "P/E ratio, dividend yield, and key statistics."
        )
        args_schema: Type[BaseModel] = _StockDataInput

        def _run(self, ticker: str, period: str = "1y") -> str:
            try:
                import yfinance as yf

                stock = yf.Ticker(ticker.upper())
                info = stock.info

                if not info or not info.get("regularMarketPrice"):
                    return f"No data found for ticker: {ticker}"

                # Key stats
                lines = [
                    f"=== {info.get('longName', ticker.upper())} ({ticker.upper()}) ===",
                    f"Price: ${info.get('regularMarketPrice', 'N/A')}",
                    f"Market Cap: ${info.get('marketCap', 0):,.0f}" if info.get("marketCap") else "",
                    f"P/E Ratio: {info.get('trailingPE', 'N/A')}",
                    f"Forward P/E: {info.get('forwardPE', 'N/A')}",
                    f"EPS: ${info.get('trailingEps', 'N/A')}",
                    f"Dividend Yield: {info.get('dividendYield', 0) * 100:.2f}%" if info.get("dividendYield") else "Dividend Yield: N/A",
                    f"52W High: ${info.get('fiftyTwoWeekHigh', 'N/A')}",
                    f"52W Low: ${info.get('fiftyTwoWeekLow', 'N/A')}",
                    f"Volume: {info.get('volume', 0):,.0f}" if info.get("volume") else "",
                    f"Beta: {info.get('beta', 'N/A')}",
                    f"Sector: {info.get('sector', 'N/A')}",
                    f"Industry: {info.get('industry', 'N/A')}",
                ]

                # Price history summary
                hist = stock.history(period=period)
                if not hist.empty:
                    first_price = hist["Close"].iloc[0]
                    last_price = hist["Close"].iloc[-1]
                    change_pct = ((last_price - first_price) / first_price) * 100
                    lines.append(f"\nPrice change ({period}): {change_pct:+.2f}%")
                    lines.append(f"High: ${hist['High'].max():.2f}")
                    lines.append(f"Low: ${hist['Low'].min():.2f}")

                return "\n".join(line for line in lines if line)
            except Exception as e:
                return f"Error fetching stock data: {str(e)[:300]}"

    class _FinancialsInput(BaseModel):
        ticker: str = Field(description="Stock ticker symbol")
        statement: str = Field(
            default="all",
            description="Financial statement: income, balance, cashflow, or all",
        )

    class CompanyFinancialsTool(BaseTool):
        name: str = "company_financials"
        description: str = (
            "Get company financial statements: income statement, balance sheet, "
            "and cash flow statement. Data from most recent annual filings."
        )
        args_schema: Type[BaseModel] = _FinancialsInput

        def _run(self, ticker: str, statement: str = "all") -> str:
            try:
                import yfinance as yf
                import pandas as pd

                stock = yf.Ticker(ticker.upper())
                sections = []

                def _fmt_statement(df: pd.DataFrame, title: str) -> str:
                    if df is None or df.empty:
                        return f"\n--- {title} ---\nNo data available."
                    lines = [f"\n--- {title} ---"]
                    # Show most recent 2 years
                    cols = df.columns[:2]
                    for idx in df.index:
                        vals = []
                        for col in cols:
                            v = df.loc[idx, col]
                            if pd.notna(v):
                                if abs(v) >= 1e9:
                                    vals.append(f"${v/1e9:.2f}B")
                                elif abs(v) >= 1e6:
                                    vals.append(f"${v/1e6:.1f}M")
                                else:
                                    vals.append(f"${v:,.0f}")
                            else:
                                vals.append("N/A")
                        lines.append(f"  {idx}: {' | '.join(vals)}")
                    return "\n".join(lines)

                if statement in ("income", "all"):
                    sections.append(_fmt_statement(stock.income_stmt, "Income Statement"))
                if statement in ("balance", "all"):
                    sections.append(_fmt_statement(stock.balance_sheet, "Balance Sheet"))
                if statement in ("cashflow", "all"):
                    sections.append(_fmt_statement(stock.cashflow, "Cash Flow"))

                header = f"=== {ticker.upper()} Financial Statements ===\n"
                return header + "\n".join(sections)
            except Exception as e:
                return f"Error fetching financials: {str(e)[:300]}"

    class _SECInput(BaseModel):
        ticker: str = Field(description="Company ticker or CIK number")
        filing_type: str = Field(
            default="10-K",
            description="Filing type: 10-K, 10-Q, 8-K, DEF 14A, S-1",
        )
        count: int = Field(default=5, description="Number of filings to return")

    class SECFilingsTool(BaseTool):
        name: str = "sec_filings"
        description: str = (
            "Search SEC EDGAR for company regulatory filings. "
            "Returns filing dates, types, and links to full documents."
        )
        args_schema: Type[BaseModel] = _SECInput

        def _run(self, ticker: str, filing_type: str = "10-K", count: int = 5) -> str:
            try:
                import requests

                # Search EDGAR full-text search
                url = f"{_SEC_EDGAR_BASE}/search-index"
                params = {
                    "q": f'"{ticker}"',
                    "dateRange": "custom",
                    "forms": filing_type,
                    "from": "0",
                    "size": str(count),
                }
                resp = requests.get(url, params=params, headers=_get_sec_headers(), timeout=15)

                if resp.status_code != 200:
                    # Fallback: try company submissions API
                    return self._search_submissions(ticker, filing_type, count)

                data = resp.json()
                hits = data.get("hits", {}).get("hits", [])
                if not hits:
                    return self._search_submissions(ticker, filing_type, count)

                lines = [f"=== SEC Filings for {ticker.upper()} ({filing_type}) ==="]
                for hit in hits[:count]:
                    src = hit.get("_source", {})
                    lines.append(
                        f"  Date: {src.get('file_date', 'N/A')}\n"
                        f"  Form: {src.get('form_type', filing_type)}\n"
                        f"  Description: {src.get('display_names', [''])[0][:100] if src.get('display_names') else 'N/A'}\n"
                        f"  URL: https://www.sec.gov/Archives/{src.get('file_path', '')}"
                    )
                return "\n\n".join(lines)
            except Exception as e:
                return f"Error fetching SEC filings: {str(e)[:300]}"

        def _search_submissions(self, ticker: str, filing_type: str, count: int) -> str:
            """Fallback: search via company submissions API."""
            try:
                import requests

                # First get CIK from ticker
                url = f"{_SEC_COMPANY_API}/submissions/CIK{ticker.upper().zfill(10)}.json"
                resp = requests.get(url, headers=_get_sec_headers(), timeout=15)
                if resp.status_code != 200:
                    return f"No SEC filings found for {ticker}."

                data = resp.json()
                recent = data.get("filings", {}).get("recent", {})
                forms = recent.get("form", [])
                dates = recent.get("filingDate", [])
                accessions = recent.get("accessionNumber", [])
                names = recent.get("primaryDocument", [])

                lines = [f"=== SEC Filings for {data.get('name', ticker.upper())} ==="]
                found = 0
                for i, form in enumerate(forms):
                    if form == filing_type or filing_type == "all":
                        acc = accessions[i].replace("-", "")
                        lines.append(
                            f"  Date: {dates[i]}\n"
                            f"  Form: {form}\n"
                            f"  URL: https://www.sec.gov/Archives/edgar/data/"
                            f"{data.get('cik', '')}/{acc}/{names[i]}"
                        )
                        found += 1
                        if found >= count:
                            break

                if found == 0:
                    return f"No {filing_type} filings found for {ticker}."
                return "\n\n".join(lines)
            except Exception:
                return f"No SEC filings found for {ticker}."

    class _RatiosInput(BaseModel):
        ticker: str = Field(description="Stock ticker symbol")

    class FinancialRatiosTool(BaseTool):
        name: str = "financial_ratios"
        description: str = (
            "Calculate key financial ratios for a company: profitability, "
            "liquidity, leverage, efficiency, and valuation ratios."
        )
        args_schema: Type[BaseModel] = _RatiosInput

        def _run(self, ticker: str) -> str:
            try:
                import yfinance as yf

                stock = yf.Ticker(ticker.upper())
                info = stock.info
                bs = stock.balance_sheet
                inc = stock.income_stmt
                cf = stock.cashflow

                ratios = [f"=== Financial Ratios: {ticker.upper()} ==="]

                # Valuation
                ratios.append("\n-- Valuation --")
                ratios.append(f"  P/E (Trailing): {info.get('trailingPE', 'N/A')}")
                ratios.append(f"  P/E (Forward): {info.get('forwardPE', 'N/A')}")
                ratios.append(f"  P/B: {info.get('priceToBook', 'N/A')}")
                ratios.append(f"  P/S: {info.get('priceToSalesTrailing12Months', 'N/A')}")
                ratios.append(f"  EV/EBITDA: {info.get('enterpriseToEbitda', 'N/A')}")
                ratios.append(f"  PEG: {info.get('pegRatio', 'N/A')}")

                # Profitability
                ratios.append("\n-- Profitability --")
                ratios.append(f"  Profit Margin: {info.get('profitMargins', 'N/A')}")
                ratios.append(f"  Operating Margin: {info.get('operatingMargins', 'N/A')}")
                ratios.append(f"  ROE: {info.get('returnOnEquity', 'N/A')}")
                ratios.append(f"  ROA: {info.get('returnOnAssets', 'N/A')}")
                ratios.append(f"  Gross Margin: {info.get('grossMargins', 'N/A')}")

                # Liquidity (from balance sheet)
                if bs is not None and not bs.empty:
                    try:
                        col = bs.columns[0]
                        ca = bs.loc["Current Assets", col] if "Current Assets" in bs.index else None
                        cl = bs.loc["Current Liabilities", col] if "Current Liabilities" in bs.index else None
                        if ca and cl and cl != 0:
                            ratios.append("\n-- Liquidity --")
                            ratios.append(f"  Current Ratio: {ca/cl:.2f}")
                    except Exception:
                        pass

                # Leverage
                ratios.append("\n-- Leverage --")
                ratios.append(f"  Debt/Equity: {info.get('debtToEquity', 'N/A')}")

                # Dividend
                ratios.append("\n-- Returns --")
                dy = info.get("dividendYield")
                ratios.append(f"  Dividend Yield: {dy*100:.2f}%" if dy else "  Dividend Yield: N/A")
                ratios.append(f"  Payout Ratio: {info.get('payoutRatio', 'N/A')}")

                return "\n".join(ratios)
            except Exception as e:
                return f"Error calculating ratios: {str(e)[:300]}"

    class _DCFInput(BaseModel):
        ticker: str = Field(description="Stock ticker symbol")
        growth_rate: float = Field(
            default=0.0,
            description="Projected annual revenue growth rate (0.0 = auto-estimate from history)",
        )
        discount_rate: float = Field(
            default=0.10,
            description="Discount rate / WACC (default: 10%)",
        )
        terminal_growth: float = Field(
            default=0.025,
            description="Terminal growth rate (default: 2.5%)",
        )
        projection_years: int = Field(default=5, description="Years to project")

    class FinancialModelTool(BaseTool):
        name: str = "financial_model"
        description: str = (
            "Run a simplified DCF (Discounted Cash Flow) valuation model. "
            "Projects free cash flow and calculates intrinsic value per share."
        )
        args_schema: Type[BaseModel] = _DCFInput

        def _run(
            self,
            ticker: str,
            growth_rate: float = 0.0,
            discount_rate: float = 0.10,
            terminal_growth: float = 0.025,
            projection_years: int = 5,
        ) -> str:
            try:
                import yfinance as yf

                stock = yf.Ticker(ticker.upper())
                info = stock.info
                cf = stock.cashflow

                if cf is None or cf.empty:
                    return f"No cash flow data available for {ticker}."

                # Get Free Cash Flow (most recent)
                col = cf.columns[0]
                operating_cf = cf.loc["Operating Cash Flow", col] if "Operating Cash Flow" in cf.index else 0
                capex = cf.loc["Capital Expenditure", col] if "Capital Expenditure" in cf.index else 0
                fcf = operating_cf + capex  # capex is negative

                if fcf <= 0:
                    return f"Negative free cash flow (${fcf/1e9:.2f}B). DCF not applicable."

                # Auto-estimate growth from revenue history
                if growth_rate == 0.0:
                    inc = stock.income_stmt
                    if inc is not None and len(inc.columns) >= 2:
                        try:
                            rev_recent = inc.loc["Total Revenue", inc.columns[0]]
                            rev_prior = inc.loc["Total Revenue", inc.columns[1]]
                            growth_rate = (rev_recent - rev_prior) / abs(rev_prior)
                            growth_rate = max(min(growth_rate, 0.30), -0.10)  # Clamp
                        except Exception:
                            growth_rate = 0.05  # Default 5%
                    else:
                        growth_rate = 0.05

                shares = info.get("sharesOutstanding", 0)
                if not shares:
                    return f"No shares outstanding data for {ticker}."

                current_price = info.get("regularMarketPrice", 0)

                # Project FCF
                projected_fcf = []
                cumulative_fcf = fcf
                for year in range(1, projection_years + 1):
                    cumulative_fcf *= (1 + growth_rate)
                    projected_fcf.append(cumulative_fcf)

                # Discount projected FCF
                pv_fcf = sum(
                    f / (1 + discount_rate) ** (i + 1)
                    for i, f in enumerate(projected_fcf)
                )

                # Terminal value
                terminal_fcf = projected_fcf[-1] * (1 + terminal_growth)
                terminal_value = terminal_fcf / (discount_rate - terminal_growth)
                pv_terminal = terminal_value / (1 + discount_rate) ** projection_years

                # Enterprise value
                ev = pv_fcf + pv_terminal
                equity_value = ev  # Simplified (ignoring net debt for accessibility)
                intrinsic = equity_value / shares

                lines = [
                    f"=== DCF Valuation: {ticker.upper()} ===",
                    f"Current Price: ${current_price:.2f}",
                    f"Base FCF: ${fcf/1e9:.2f}B",
                    f"Growth Rate: {growth_rate*100:.1f}%",
                    f"Discount Rate: {discount_rate*100:.1f}%",
                    f"Terminal Growth: {terminal_growth*100:.1f}%",
                    f"",
                    f"PV of Projected FCF: ${pv_fcf/1e9:.2f}B",
                    f"PV of Terminal Value: ${pv_terminal/1e9:.2f}B",
                    f"Enterprise Value: ${ev/1e9:.2f}B",
                    f"",
                    f"Intrinsic Value/Share: ${intrinsic:.2f}",
                    f"Current Price: ${current_price:.2f}",
                ]

                if current_price > 0:
                    upside = ((intrinsic - current_price) / current_price) * 100
                    lines.append(f"Upside/Downside: {upside:+.1f}%")
                    if upside > 20:
                        lines.append("Signal: UNDERVALUED (>20% upside)")
                    elif upside < -20:
                        lines.append("Signal: OVERVALUED (>20% downside)")
                    else:
                        lines.append("Signal: FAIRLY VALUED")

                lines.append(
                    "\nNote: Simplified DCF model. Not investment advice. "
                    "Does not account for net debt, working capital, or risk factors."
                )
                return "\n".join(lines)
            except Exception as e:
                return f"Error running DCF model: {str(e)[:300]}"

    return [
        StockDataTool(),
        CompanyFinancialsTool(),
        SECFilingsTool(),
        FinancialRatiosTool(),
        FinancialModelTool(),
    ]

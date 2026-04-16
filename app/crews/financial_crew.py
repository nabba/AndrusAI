"""financial_crew.py — Financial analysis and reporting crew."""

from app.agents.financial_analyst import create_financial_analyst
from app.crews.base_crew import run_single_agent_crew

FINANCIAL_TASK_TEMPLATE = """\
Complete this financial analysis task:

{user_input}

You have access to:
- Market data (yfinance): stock prices, fundamentals, dividends, history
- Financial statements: income statement, balance sheet, cash flow
- SEC EDGAR: regulatory filings (10-K, 10-Q, 8-K)
- Financial ratios: profitability, liquidity, leverage, valuation
- DCF valuation model: projections, terminal value, intrinsic value
- Document generation: PDF, DOCX, XLSX for formal reports

Approach:
1. Gather data from all relevant sources.
2. Compute ratios and metrics. Show your calculations.
3. Provide analysis with explicit sourcing.
4. For reports: generate a formatted document (PDF preferred).
5. Always include a "Limitations and Caveats" section.
6. Always include: "This analysis is for informational purposes only
   and does not constitute investment advice."
"""


class FinancialCrew:
    def run(self, task_description: str, parent_task_id: str = None, difficulty: int = 5) -> str:
        return run_single_agent_crew(
            crew_name="financial",
            agent_role="financial_analyst",
            create_agent_fn=create_financial_analyst,
            task_template=FINANCIAL_TASK_TEMPLATE,
            task_description=task_description,
            expected_output="Data-driven financial analysis with sourced data, computed ratios, and clear caveats.",
            parent_task_id=parent_task_id,
            difficulty=difficulty,
        )

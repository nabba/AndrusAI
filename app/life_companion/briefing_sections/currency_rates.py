"""currency_rates — EUR / USD / GBP daily reference rates via ECB.

The operator runs ventures spanning Estonia (EUR), the Baltic states
(EUR), the UK (GBP — Archibal), and US-pricing markets (USD). One line
with the three pairs is enough — the briefing is for awareness, not
trading.

Source: European Central Bank daily reference rates (free, no auth,
XML feed, weekday-only updates). Soft fail on network or parse error.
"""
from __future__ import annotations

import logging
import re
import urllib.request

logger = logging.getLogger(__name__)

ID = "currency-rates"
DISPLAY_NAME = "💱 Currency (vs EUR)"
DESCRIPTION = (
    "Daily ECB reference rates for USD + GBP against EUR. One line, "
    "updates weekdays only. Useful for cross-border invoicing context."
)

_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_CCY_RE = re.compile(
    r'<Cube\s+currency=[\'"](?P<ccy>[A-Z]{3})[\'"]\s+rate=[\'"](?P<rate>[\d.]+)[\'"]'
)
_PAIRS = ("USD", "GBP")


def gather() -> list[str]:
    try:
        req = urllib.request.Request(_URL, headers={"User-Agent": "AndrusAI/1.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            xml = r.read().decode("utf-8")
    except Exception:
        logger.debug("currency_rates.gather: fetch failed", exc_info=True)
        return []
    # Tiny regex scan — XMLpull would be overkill for a flat 30-row feed.
    rates: dict[str, float] = {}
    for m in _CCY_RE.finditer(xml):
        ccy = m.group("ccy")
        try:
            rates[ccy] = float(m.group("rate"))
        except ValueError:
            continue
    if not any(p in rates for p in _PAIRS):
        return []
    parts = [f"1 EUR = {rates[p]:.4f} {p}" for p in _PAIRS if p in rates]
    return [f"  • {' · '.join(parts)}"]

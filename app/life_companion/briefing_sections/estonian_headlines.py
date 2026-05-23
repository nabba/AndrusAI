"""estonian_headlines — top regional headlines via existing web_search.

The operator lives in / spans Estonia + Finland; one workstream (Eesti
mets) is Estonian-language. Surface 3 headlines from Estonian / Baltic
sources so the briefing has a regional pulse not covered by the
workstream news section (which is per-project + LLM-clustered)."""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

ID = "estonian-headlines"
DISPLAY_NAME = "🇪🇪 Estonian headlines"
DESCRIPTION = (
    "Top 3 headlines from Estonian + Baltic news sources. Reuses the "
    "system's existing web_search adapter — no new API key."
)

_QUERY = "Estonia news today site:err.ee OR site:postimees.ee OR site:delfi.ee"
_MAX_LINES = 3


def gather() -> list[str]:
    try:
        from app.tools.web_search import web_search
    except Exception:
        logger.debug("estonian_headlines: web_search import failed", exc_info=True)
        return []
    try:
        raw = web_search(_QUERY) or ""
    except Exception:
        logger.debug("estonian_headlines: web_search call failed", exc_info=True)
        return []
    if not raw.strip():
        return []
    out: list[str] = []
    # Same lightweight parse the workstream_news module uses — the
    # web_search return format is markdown-ish and stable enough.
    for chunk in re.split(r"\n\s*\n", raw):
        title = ""
        url = ""
        for line in chunk.splitlines():
            line = line.strip()
            if not title and len(line) > 6 and not line.startswith("http"):
                title = re.sub(r"^[#*\-\d.)\s]+", "", line).strip("* ").strip()
                continue
            m = re.search(r"(https?://[^\s)]+)", line)
            if not url and m:
                url = m.group(1)
        if not title:
            continue
        url_tail = f"  ({url})" if url else ""
        out.append(f"  • {title[:90]}{url_tail}")
        if len(out) >= _MAX_LINES:
            break
    return out

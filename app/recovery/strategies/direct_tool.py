"""
direct_tool.py — recovery strategy: bypass the LLM and call a known
tool directly.

The cheapest, fastest recovery path. Use when the refusal categorically
maps to a specific tool we have wired up — e.g., the user asked for
emails, the research crew refused because it lacks email tools, but
``email_tools.check_email`` is sitting right there. No need to spin
up another crew + agent + LLM call when we can just call the function
with extracted parameters.

Param extraction is intentionally simple — regex over the user's text
catches the common shapes ("top 25", "today", "from sender X",
"unread"). When extraction can't determine a parameter, we fall back
to the tool's default. If the tool's output isn't useful (empty,
error), we return success=False so the loop moves to the next strategy.
"""
from __future__ import annotations

import logging
import re

from app.recovery.librarian import Alternative
from app.recovery.strategies import StrategyResult

logger = logging.getLogger(__name__)


# ── Param extractors ──────────────────────────────────────────────────
#
# Each extractor reads a natural-language task and returns kwargs
# suitable for the matched tool. Conservative — when uncertain, omit
# the param so the tool uses its own default.

def _extract_int(pattern: str, text: str, default: int | None = None) -> int | None:
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return default
    try:
        return int(m.group(1))
    except (ValueError, IndexError):
        return default


def _email_params(task: str) -> dict:
    """Build a kwargs dict for email_tools.check_email from ``task``."""
    text = task.lower()
    out: dict = {}
    # "top N" / "first N" / "last N" / "give me N"
    n = _extract_int(r"\b(?:top|first|last|give me|show me|list)\s+(\d+)", task)
    if n:
        out["limit"] = max(1, min(n, 100))
    # Time window
    if any(p in text for p in ("today", "today's", "this morning")):
        out["days_back"] = 1
    elif "yesterday" in text:
        out["days_back"] = 2
    elif "this week" in text:
        out["days_back"] = 7
    elif "weekend" in text or "this weekend" in text:
        out["days_back"] = 3
    # Unread
    if any(p in text for p in ("unread", "new emails", "haven't read")):
        out["unread_only"] = True
    return out


def _calendar_params(task: str) -> dict:
    text = task.lower()
    out: dict = {}
    n = _extract_int(r"\b(?:next|upcoming|first)\s+(\d+)", task, default=20)
    if n:
        out["limit"] = max(1, min(n, 50))
    if "today" in text:
        out["days_ahead"] = 1
    elif "tomorrow" in text:
        out["days_ahead"] = 2
    elif "this week" in text:
        out["days_ahead"] = 7
    return out


# ── Tool resolver ─────────────────────────────────────────────────────

def _resolve_tool(tool_name: str):
    """Return a callable wrapping the tool, or None if unavailable.

    Tools require an agent_id for capability gating; we use 'recovery'
    as a synthetic agent which has the same access surface as 'pim'.
    """
    if tool_name == "email_tools.check_email":
        try:
            from app.tools.email_tools import create_email_tools
            tools = create_email_tools("recovery_loop")
            for t in tools:
                if getattr(t, "name", "") == "check_email":
                    return t
        except Exception as exc:
            logger.debug("direct_tool: email tools unavailable: %s", exc)
    if tool_name == "calendar_tools.list_events":
        try:
            from app.tools.calendar_tools import create_calendar_tools
            tools = create_calendar_tools("recovery_loop")
            for t in tools:
                if "list" in getattr(t, "name", "").lower() and "event" in t.name.lower():
                    return t
        except Exception as exc:
            logger.debug("direct_tool: calendar tools unavailable: %s", exc)
    return None


def _format_email_output(raw: str, task: str) -> str:
    """Wrap the raw tool output in a presentable response."""
    body = raw.strip() if raw else "(no output)"
    return (
        f"Pulled this directly from your inbox (no LLM in the loop):\n\n"
        f"{body}"
    )


def _format_generic_output(tool_name: str, raw: str, task: str) -> str:
    return (
        f"Called {tool_name} directly. Result:\n\n{raw.strip()}"
    )


# ── Strategy entry point ──────────────────────────────────────────────

# Maps tool name → param extractor + output formatter
_TOOL_RECIPES: dict[str, dict] = {
    "email_tools.check_email": {
        "extract": _email_params,
        "format": _format_email_output,
        "rationale": "User asked about emails; calling check_email directly skips the agent layer.",
    },
    "calendar_tools.list_events": {
        "extract": _calendar_params,
        "format": lambda raw, task: _format_generic_output("calendar.list_events", raw, task),
        "rationale": "User asked about calendar; calling list_events directly.",
    },
}


def execute(task: str, alt: Alternative, ctx: dict) -> StrategyResult:
    tool_name = alt.tool
    if not tool_name:
        return StrategyResult(success=False, error="direct_tool: no tool specified")

    recipe = _TOOL_RECIPES.get(tool_name)
    if not recipe:
        return StrategyResult(
            success=False,
            error=f"direct_tool: no recipe for {tool_name}",
        )

    tool = _resolve_tool(tool_name)
    if tool is None:
        return StrategyResult(
            success=False,
            error=f"direct_tool: tool {tool_name} not available (env not configured?)",
        )

    try:
        params = recipe["extract"](task)
    except Exception as exc:
        return StrategyResult(success=False, error=f"direct_tool: param extraction failed: {exc}")

    logger.info(
        "direct_tool: invoking %s with %s",
        tool_name, {k: v for k, v in params.items() if k != "credentials"},
    )

    try:
        # CrewAI BaseTool — invoke via _run for the tool's actual logic
        if hasattr(tool, "_run"):
            raw = tool._run(**params)
        elif callable(tool):
            raw = tool(**params)
        else:
            return StrategyResult(success=False, error="direct_tool: tool not callable")
    except Exception as exc:
        return StrategyResult(success=False, error=f"direct_tool: tool raised: {exc}")

    if not raw or (isinstance(raw, str) and len(raw.strip()) < 10):
        return StrategyResult(success=False, error="direct_tool: empty/tiny tool output")

    # Tool error string check (some tools return "Error: ..." instead of raising)
    raw_str = str(raw)
    if raw_str.lower().startswith("error:") or "no email config" in raw_str.lower():
        return StrategyResult(
            success=False,
            error=f"direct_tool: tool returned error: {raw_str[:200]}",
        )

    return StrategyResult(
        success=True,
        text=recipe["format"](raw_str, task),
        note=f"Called {tool_name} directly (no LLM layer).",
        route_changed=True,
    )

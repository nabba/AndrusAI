"""
nl_parser.py — Natural language → cron expression.

Two-stage:
  1. Rule-based parser covers the common cases (daily/weekly/weekdays/every N
     minutes, times like "at 7am") without an LLM call — deterministic + free.
  2. LLM fallback (budget tier) handles anything the rule parser can't.

Output is a 5-field cron expression: "minute hour day-of-month month day-of-week".
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_WEEKDAY_MAP = {
    "sunday": 0, "sun": 0,
    "monday": 1, "mon": 1,
    "tuesday": 2, "tue": 2, "tues": 2,
    "wednesday": 3, "wed": 3,
    "thursday": 4, "thu": 4, "thurs": 4,
    "friday": 5, "fri": 5,
    "saturday": 6, "sat": 6,
}


def _extract_time(text: str) -> tuple[int, int] | None:
    """Parse '7am', '7:30 pm', '07:00', '14:30'. Returns (hour, minute) or None."""
    m = re.search(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b', text, re.IGNORECASE)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or "0")
    period = (m.group(3) or "").lower().replace(".", "")
    if period == "pm" and hour < 12:
        hour += 12
    elif period == "am" and hour == 12:
        hour = 0
    if 0 <= hour < 24 and 0 <= minute < 60:
        return hour, minute
    return None


def _rule_based_parse(text: str) -> str | None:
    """Try to convert simple phrases to cron without hitting an LLM."""
    t = text.lower().strip()

    # every N minutes
    m = re.search(r'every\s+(\d+)\s*min', t)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 59:
            return f"*/{n} * * * *"

    # every N hours
    m = re.search(r'every\s+(\d+)\s*hour', t)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 23:
            return f"0 */{n} * * *"

    # every hour
    if "every hour" in t:
        return "0 * * * *"

    # hourly
    if "hourly" in t:
        return "0 * * * *"

    time_hm = _extract_time(t)

    # daily / every day / each day / each evening / each night
    if re.search(r'\b(daily|every day|each day|each morning|every morning|each evening|every evening|each night|every night)\b', t):
        if time_hm:
            return f"{time_hm[1]} {time_hm[0]} * * *"
        if "morning" in t:
            return "0 8 * * *"
        if "evening" in t:
            return "0 18 * * *"
        if "noon" in t or "midday" in t:
            return "0 12 * * *"
        if "midnight" in t:
            return "0 0 * * *"
        return "0 9 * * *"

    # weekdays
    if re.search(r'\b(weekday|weekdays|mon[- ]fri|monday to friday)\b', t):
        if time_hm:
            return f"{time_hm[1]} {time_hm[0]} * * 1-5"
        return "0 9 * * 1-5"

    # weekends
    if re.search(r'\b(weekend|weekends|sat.?sun)\b', t):
        if time_hm:
            return f"{time_hm[1]} {time_hm[0]} * * 6,0"
        return "0 10 * * 6,0"

    # specific weekday
    for name, dow in _WEEKDAY_MAP.items():
        if re.search(rf'\b(every |each |on )?{name}s?\b', t):
            if time_hm:
                return f"{time_hm[1]} {time_hm[0]} * * {dow}"
            return f"0 9 * * {dow}"

    # at HH:MM (no frequency → default to daily)
    if time_hm and re.search(r'\bat\b', t):
        return f"{time_hm[1]} {time_hm[0]} * * *"

    return None


def _llm_parse(text: str) -> str | None:
    """LLM fallback — uses the budget tier via llm_factory."""
    try:
        from app.llm_factory import create_cheap_vetting_llm
        llm = create_cheap_vetting_llm()
    except Exception:
        return None

    prompt = (
        "Convert the following natural-language schedule into a 5-field cron "
        "expression (minute hour day-of-month month day-of-week). "
        "Output ONLY the cron expression on a single line, nothing else. "
        "Use standard cron syntax (* for any, comma-lists, ranges, and / for step).\n\n"
        f"Input: {text[:300]}"
    )
    try:
        raw = str(llm.call(prompt)).strip()
    except Exception:
        return None

    # Pull out the first line that looks like a 5-field cron expression
    for line in raw.splitlines():
        line = line.strip().strip("`").strip()
        parts = line.split()
        if len(parts) == 5 and all(re.fullmatch(r'[\d*/,\-]+', p) for p in parts):
            return line
    return None


def nl_to_cron(text: str) -> str | None:
    """Convert a natural-language phrase into a cron expression, or None on failure."""
    if not text or not text.strip():
        return None
    result = _rule_based_parse(text)
    if result:
        return result
    return _llm_parse(text)


def describe_cron(cron_expr: str) -> str:
    """Render a cron expression as a short human-readable description.

    Pure-Python; no dependencies. Good-enough summary — not exhaustive.
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return cron_expr
    minute, hour, dom, month, dow = parts

    if cron_expr == "* * * * *":
        return "every minute"
    if cron_expr == "0 * * * *":
        return "every hour"
    m = re.fullmatch(r'\*/(\d+)', minute)
    if m and hour == "*" and dom == "*" and month == "*" and dow == "*":
        return f"every {m.group(1)} minutes"
    m = re.fullmatch(r'\*/(\d+)', hour)
    if m and minute == "0" and dom == "*" and month == "*" and dow == "*":
        return f"every {m.group(1)} hours"

    time_part = ""
    if minute.isdigit() and hour.isdigit():
        time_part = f"at {int(hour):02d}:{int(minute):02d}"

    dow_part = ""
    if dow == "1-5":
        dow_part = "on weekdays"
    elif dow in ("6,0", "0,6"):
        dow_part = "on weekends"
    elif dow.isdigit():
        names = {v: k for k, v in _WEEKDAY_MAP.items() if len(k) > 3}
        day = names.get(int(dow), f"day {dow}")
        dow_part = f"every {day}"
    elif dow == "*" and dom == "*":
        dow_part = "daily"

    segments = [s for s in (dow_part, time_part) if s]
    return " ".join(segments) if segments else cron_expr

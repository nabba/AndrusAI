"""
calendar_tools.py — macOS Calendar automation via AppleScript through bridge.

Integrates with Calendar.app which syncs with iCloud, Google, Exchange.
Zero external dependencies.

IMPORTANT: AppleScript's `date "..."` parser is locale-dependent — an English
date string like "April 19, 2026" fails on a Mac configured for Finnish or
Estonian locale with a cryptic "Invalid date and time" syntax error.  All
date construction here uses numeric component assignment (year/month/day/
hours/minutes/seconds) which is locale-independent.

Usage:
    from app.tools.calendar_tools import create_calendar_tools
    tools = create_calendar_tools("pim")
"""

import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# Calendars to skip when iterating "all calendars" — these are cloud-backed,
# auto-generated, or duplicated calendars that block Calendar.app's
# `every event whose` query. Querying them individually with a timeout
# still works, but they're almost never what the user wants by default.
# Override via CALENDAR_SKIP_LIST env var (comma-separated).
_DEFAULT_CALENDAR_SKIP = [
    "Holidays in Estonia",
    "Holidays in United Kingdom",
    "Holidays in United States",
    "United States holidays",
    "Siri Suggestions",
    "Scheduled Reminders",
    "Birthdays",
    "Polar training results",
    "Polar training targets",
    "Gmail calendar",
    "Coursera Calendar - Andrus Raudsalu - andrus@raudsalu.com",
    "Andrus Raudsalu (TripIt)",
    "The 4 Keys To Indistractable Focus",
    "Reach Your Wildest Goals with Ease",
    "Embrace Your Energy Body",
    "Transferred from admin@raudsalu.com",
    "Transferred from unicorn@raudsalu.com",
    "Transferred from newsletter@raudsalu.com",
    "Incoming",
]
_CALENDAR_SKIP_LIST = [
    s.strip() for s in os.environ.get(
        "CALENDAR_SKIP_LIST",
        ",".join(_DEFAULT_CALENDAR_SKIP),
    ).split(",") if s.strip()
]


def _escape_applescript_string(s: str) -> str:
    """Escape a string for embedding inside AppleScript double-quotes.

    AppleScript accepts `\\\"` for a literal quote and `\\\\` for a literal
    backslash; everything else passes through unchanged. Newlines are stripped
    because they terminate statements.
    """
    return (
        s.replace("\\", "\\\\")
         .replace('"', '\\"')
         .replace("\n", " ")
         .replace("\r", " ")
    )


def _applescript_date_from_components(
    varname: str, dt: datetime, at_midnight: bool = True,
) -> str:
    """Build a locale-independent AppleScript snippet that creates a date.

    Returns a multi-line AppleScript string that assigns a `date` value to
    the given variable name using numeric component assignment.  This avoids
    the locale trap of `date "April 19, 2026"` which fails on non-English
    Macs.

    Args:
        varname: AppleScript variable name (e.g. "startDate").
        dt: Python datetime to convert.
        at_midnight: If True, force time to 00:00:00 regardless of dt's time.
                     If False, use dt's actual hour/minute/second.
    """
    if at_midnight:
        hours, minutes, seconds = 0, 0, 0
    else:
        hours, minutes, seconds = dt.hour, dt.minute, dt.second
    return (
        f"set {varname} to current date\n"
        f"set year of {varname} to {dt.year}\n"
        f"set month of {varname} to {dt.month}\n"
        f"set day of {varname} to {dt.day}\n"
        f"set hours of {varname} to {hours}\n"
        f"set minutes of {varname} to {minutes}\n"
        f"set seconds of {varname} to {seconds}\n"
    )


def _parse_natural_date(text: str) -> datetime | None:
    """Parse a date/time string from the LLM into a Python datetime.

    Handles:
      - ISO 8601: 2026-04-19T14:00:00, 2026-04-19 14:00
      - Common formats: "April 20, 2026 2:00 PM", "20/04/2026 14:00",
        "2026-04-20", etc.
    Returns None if parsing fails.
    """
    if not text:
        return None
    text = text.strip()
    formats = [
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%B %d, %Y %I:%M %p", "%B %d, %Y %I:%M%p",
        "%B %d, %Y %H:%M", "%B %d, %Y",
        "%b %d, %Y %I:%M %p", "%b %d, %Y %I:%M%p",
        "%b %d, %Y %H:%M", "%b %d, %Y",
        "%d %B %Y %H:%M", "%d %B %Y",
        "%d/%m/%Y %H:%M", "%d/%m/%Y",
        "%m/%d/%Y %H:%M", "%m/%d/%Y",
        "%d.%m.%Y %H:%M", "%d.%m.%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    # Last-resort: try dateutil if available
    try:
        from dateutil import parser as _dp
        return _dp.parse(text)
    except Exception:
        return None


def create_calendar_tools(agent_id: str) -> list:
    """Create calendar tools via AppleScript through bridge.

    Returns empty list if bridge is unavailable.
    """
    try:
        from app.bridge_client import get_bridge
        bridge = get_bridge(agent_id)
        if not bridge:
            return []
        if not bridge.is_available():
            logger.debug(f"calendar_tools: bridge unavailable for {agent_id}")
            return []
    except Exception:
        return []

    try:
        from crewai.tools import BaseTool
        from pydantic import BaseModel, Field
        from typing import Type
    except ImportError:
        return []

    def _run_applescript(script: str, timeout: int = 30) -> str:
        """Execute AppleScript via bridge and return output."""
        result = bridge.execute(["osascript", "-e", script], timeout=timeout)
        if "error" in result:
            return f"Error: {result.get('detail', result['error'])}"
        output = result.get("stdout", "").strip()
        stderr = result.get("stderr", "").strip()
        if stderr and not output:
            return f"Calendar error: {stderr[:500]}"
        return output

    # ── Swift/EventKit fast path ──────────────────────────────────────────
    # AppleScript's `whose` predicate is O(total-events-in-history) per
    # calendar.  EventKit queries the indexed Calendar Store in ~100ms
    # regardless of history size.  If the Swift helper has Calendar
    # permission granted, we skip AppleScript entirely.
    #
    # Cached probe result per tool-factory call so we don't retry the
    # permission check on every tool invocation.
    _swift_probe_state: dict = {"available": None}

    def _swift_script_host_path() -> str:
        """Return the host-filesystem path to the Swift helper.

        The script lives in workspace/scripts/ which is volume-mounted,
        so `workspace_host_path` + '/scripts/calendar_events.swift' is the
        host path the bridge can actually read.
        """
        from app.config import get_settings
        s = get_settings()
        host_ws = getattr(s, "workspace_host_path", "") or ""
        if not host_ws:
            return ""
        return f"{host_ws.rstrip('/')}/scripts/calendar_events.swift"

    def _swift_query_events(
        start_dt: datetime, end_dt: datetime, calendar_name: str = "",
    ) -> list[dict] | None:
        """Query events via the Swift EventKit helper.

        Returns a list of event dicts on success, None if Swift is
        unavailable or permissions weren't granted.  Events have keys:
        calendar, title, start, end, location, allDay, notes.
        """
        if _swift_probe_state["available"] is False:
            return None  # already failed once, don't retry every call

        script_path = _swift_script_host_path()
        if not script_path:
            _swift_probe_state["available"] = False
            logger.debug("calendar_tools: workspace_host_path unset, can't locate Swift helper")
            return None

        cmd = [
            "swift", script_path, "list",
            "--start", start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "--end", end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
        ]
        if calendar_name:
            cmd += ["--calendar", calendar_name]

        try:
            result = bridge.execute(cmd, timeout=20)
        except Exception as exc:
            logger.debug(f"calendar_tools: Swift bridge call failed: {exc}")
            _swift_probe_state["available"] = False
            return None

        if "error" in result and "detail" in result:
            logger.debug(f"calendar_tools: Swift bridge error: {result.get('detail')}")
            _swift_probe_state["available"] = False
            return None

        stdout = (result.get("stdout") or "").strip()
        stderr = (result.get("stderr") or "").strip()

        import json as _json
        try:
            parsed = _json.loads(stdout) if stdout else None
        except Exception:
            logger.debug(f"calendar_tools: Swift stdout not JSON: {stdout[:200]}")
            _swift_probe_state["available"] = False
            return None

        # Permission / error payload from Swift
        if isinstance(parsed, dict) and "error" in parsed:
            err = parsed["error"]
            logger.info(f"calendar_tools: Swift helper error: {err}")
            if "access denied" in err.lower() or "grant" in err.lower():
                # Mark unavailable so we don't retry.  The error message
                # is surfaced to the user by the caller so they know to
                # grant permission.
                _swift_probe_state["available"] = False
                _swift_probe_state["permission_msg"] = err
            else:
                _swift_probe_state["available"] = False
            return None

        if not isinstance(parsed, list):
            _swift_probe_state["available"] = False
            return None

        # Success — remember that Swift works for the rest of this session
        _swift_probe_state["available"] = True
        return parsed

    def _format_swift_events(events: list[dict]) -> str:
        """Render Swift EventKit output as a readable text block."""
        if not events:
            return ""
        lines = []
        for evt in events:
            title = evt.get("title", "(no title)")
            start = evt.get("start", "")
            loc = evt.get("location", "")
            cal = evt.get("calendar", "?")
            line = f"[{cal}] {start} | {title}"
            if loc:
                line += f" @ {loc}"
            lines.append(line)
        return "\n".join(lines)

    def _list_calendar_names() -> list[str]:
        """Return names of all enabled calendars (one quick AppleScript call).

        Cached per-tool-factory call — the calendar list rarely changes
        during a single session.
        """
        script = '''
tell application "Calendar"
    set out to ""
    repeat with c in calendars
        set out to out & (name of c) & linefeed
    end repeat
    return out
end tell
'''
        raw = _run_applescript(script, timeout=15)
        if raw.startswith("Error:") or raw.startswith("Calendar error:"):
            return []
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _query_one_calendar_events(
        cal_name: str, start_dt: datetime, end_dt: datetime,
    ) -> tuple[str, str]:
        """Query events from ONE calendar in the date range.

        Returns (calendar_name, formatted_events_block).  Designed to be
        called in parallel across calendars — each call is an independent
        bridge.execute() with its own 15s timeout, so one slow calendar
        doesn't block the others.
        """
        start_script = _applescript_date_from_components("startDate", start_dt, at_midnight=True)
        end_script = _applescript_date_from_components("endDate", end_dt, at_midnight=False)
        safe_name = _escape_applescript_string(cal_name)
        script = f'''
with timeout of 12 seconds
tell application "Calendar"
    {start_script}
    {end_script}
    set output to ""
    try
        set cal to calendar "{safe_name}"
        set evts to (every event of cal whose start date >= startDate and start date <= endDate)
        repeat with evt in evts
            set evtStart to start date of evt
            set evtTitle to summary of evt
            set evtLoc to location of evt
            if evtLoc is missing value then set evtLoc to ""
            set output to output & (evtStart as string) & " | " & evtTitle
            if evtLoc is not "" then
                set output to output & " @ " & evtLoc
            end if
            set output to output & linefeed
        end repeat
    on error errMsg
        return ""
    end try
    return output
end tell
end timeout
'''
        result = _run_applescript(script, timeout=15)
        if result.startswith("Error:") or result.startswith("Calendar error:"):
            return cal_name, ""
        return cal_name, result

    # ── Tool definitions ──────────────────────────────────────────

    class _ListEventsInput(BaseModel):
        days_ahead: int = Field(
            default=1,
            description="Number of days ahead to show events (0=today only, 7=this week)",
        )
        calendar_name: str = Field(
            default="",
            description="Specific calendar name to filter (empty = all calendars)",
        )

    class ListEventsTool(BaseTool):
        name: str = "list_calendar_events"
        description: str = (
            "List upcoming calendar events. Shows events from today through "
            "the specified number of days ahead. Returns time, title, location."
        )
        args_schema: Type[BaseModel] = _ListEventsInput

        def _run(self, days_ahead: int = 1, calendar_name: str = "") -> str:
            # Compute date window (midnight today → end of target day)
            start_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = (start_dt + timedelta(days=max(days_ahead, 1))).replace(
                hour=23, minute=59, second=59
            )

            # Fast path: try Swift/EventKit helper — one bridge call, ~100ms.
            swift_events = _swift_query_events(start_dt, end_dt, calendar_name)
            if swift_events is not None:
                if not swift_events:
                    scope = f"the {max(days_ahead, 1)}-day window"
                    if calendar_name:
                        return f"No events in '{calendar_name}' for {scope}."
                    return f"No events found in {scope}."
                # Sort by start time (ISO strings sort correctly)
                swift_events_sorted = sorted(swift_events, key=lambda e: e.get("start", ""))
                return (
                    f"{len(swift_events_sorted)} event(s) via EventKit:\n\n"
                    + _format_swift_events(swift_events_sorted)
                )

            # Swift unavailable — check if it's a permission issue and give
            # the user actionable instructions.
            if _swift_probe_state.get("permission_msg"):
                return (
                    "⚠️ Calendar access not granted to the BotArmy bridge.\n\n"
                    "To enable fast calendar queries:\n"
                    "1. Open System Settings > Privacy & Security > Calendars\n"
                    "2. Add 'python3' (the process running the bridge) — you may need\n"
                    "   to click the + button and navigate to /usr/bin/python3\n"
                    "3. Restart the bridge: `launchctl kickstart -k gui/$UID/com.crewai.bridge`\n\n"
                    "Falling back to the slow AppleScript path..."
                )

            # Slow fallback: per-calendar serial AppleScript with time budget
            # Single-calendar path — one fast query
            if calendar_name:
                _, result = _query_one_calendar_events(calendar_name, start_dt, end_dt)
                if not result.strip():
                    return f"No events in '{calendar_name}' for this window."
                tagged = "\n".join(
                    f"[{calendar_name}] {line}" for line in result.splitlines() if line.strip()
                )
                return tagged

            # Multi-calendar path — SERIAL per-calendar queries.  Calendar.app
            # serializes AppleScript access internally, so launching multiple
            # concurrent osascript processes queues them up on Calendar.app's
            # side and reliably causes -1712 AppleEvent timeouts.  Serial
            # queries avoid this, and each individual query has its own short
            # Python-level timeout so one stuck calendar doesn't block the
            # whole run.  A total wall-clock budget bounds the worst case.
            import time as _time

            all_cal_names = _list_calendar_names()
            if not all_cal_names:
                return "Calendar error: could not list calendars."

            skip_set = set(_CALENDAR_SKIP_LIST)
            target_cals = [c for c in all_cal_names if c not in skip_set]

            if not target_cals:
                return (
                    f"No calendars to query (all {len(all_cal_names)} are in the "
                    f"skip list). Adjust CALENDAR_SKIP_LIST env var."
                )

            output_lines: list[str] = []
            skipped: list[str] = []
            queried: list[str] = []

            # Total wall-clock budget — return what we have even if some
            # calendars haven't been reached yet.  Prioritises responsiveness
            # over completeness; user gets a "partial results" note.
            TOTAL_BUDGET_S = 45.0
            deadline = _time.monotonic() + TOTAL_BUDGET_S

            for cal in target_cals:
                if _time.monotonic() >= deadline:
                    skipped.append(cal)
                    continue
                try:
                    _, events_block = _query_one_calendar_events(cal, start_dt, end_dt)
                    queried.append(cal)
                    for line in events_block.splitlines():
                        if line.strip():
                            output_lines.append(f"[{cal}] {line}")
                except Exception:
                    skipped.append(cal)

            reached = len(queried)
            total_target = len(target_cals)
            budget_exceeded = len(skipped) > (total_target - reached - 1)

            if not output_lines:
                scope = f"{max(days_ahead, 1)}-day"
                note = ""
                if skipped:
                    note = (
                        f" ({reached}/{total_target} calendars queried; "
                        f"{len(skipped)} deferred due to time budget)"
                    )
                return f"No events found in the {scope} window{note}."

            output_lines.sort()
            header = f"{len(output_lines)} event(s) across {reached}/{total_target} calendars"
            if skipped:
                header += f" — {len(skipped)} deferred (hit {TOTAL_BUDGET_S:.0f}s budget)"
            return header + ":\n\n" + "\n".join(output_lines)

    class _CreateEventInput(BaseModel):
        title: str = Field(description="Event title/summary")
        start_date: str = Field(
            description="Start date and time (e.g. 'April 20, 2026 2:00 PM')"
        )
        end_date: str = Field(
            description="End date and time (e.g. 'April 20, 2026 3:00 PM')"
        )
        calendar_name: str = Field(
            default="",
            description="Calendar to add event to (empty = default calendar)",
        )
        location: str = Field(default="", description="Event location")
        notes: str = Field(default="", description="Event notes/description")

    class CreateEventTool(BaseTool):
        name: str = "create_calendar_event"
        description: str = (
            "Create a new calendar event. Specify title, start/end times. "
            "Optionally set calendar, location, and notes."
        )
        args_schema: Type[BaseModel] = _CreateEventInput

        def _run(
            self,
            title: str,
            start_date: str,
            end_date: str,
            calendar_name: str = "",
            location: str = "",
            notes: str = "",
        ) -> str:
            # Parse dates in Python first (locale-independent)
            start_dt = _parse_natural_date(start_date)
            end_dt = _parse_natural_date(end_date)
            if not start_dt:
                return f"Calendar error: could not parse start_date '{start_date}'. Try ISO format like '2026-04-20 14:00'."
            if not end_dt:
                return f"Calendar error: could not parse end_date '{end_date}'. Try ISO format like '2026-04-20 15:00'."

            # Escape quotes for AppleScript
            title_esc = title.replace('"', '\\"')
            location_esc = location.replace('"', '\\"')
            notes_esc = notes.replace('"', '\\"')

            cal_target = "first calendar"
            if calendar_name:
                cal_target = f'calendar "{calendar_name}"'

            start_script = _applescript_date_from_components("startDate", start_dt, at_midnight=False)
            end_script = _applescript_date_from_components("endDate", end_dt, at_midnight=False)

            props = f'summary:"{title_esc}", start date:startDate, end date:endDate'
            if location:
                props += f', location:"{location_esc}"'
            if notes:
                props += f', description:"{notes_esc}"'

            script = f'''
tell application "Calendar"
    {start_script}
    {end_script}
    tell {cal_target}
        make new event with properties {{{props}}}
    end tell
    return "Event created: {title_esc} at " & (startDate as string)
end tell
'''
            return _run_applescript(script)

    class _SearchEventsInput(BaseModel):
        query: str = Field(description="Search keyword to match in event titles")
        days_ahead: int = Field(
            default=30, description="Search within next N days"
        )

    class SearchEventsTool(BaseTool):
        name: str = "search_calendar_events"
        description: str = (
            "Search calendar events by keyword in title. "
            "Returns matching events within the specified time range."
        )
        args_schema: Type[BaseModel] = _SearchEventsInput

        def _run(self, query: str, days_ahead: int = 30) -> str:
            # Serial per-calendar query + client-side filter (same strategy as
            # list_calendar_events).  See note there re: why not parallel.
            import time as _time

            start_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = (start_dt + timedelta(days=days_ahead)).replace(
                hour=23, minute=59, second=59
            )

            # Fast path: Swift/EventKit
            swift_events = _swift_query_events(start_dt, end_dt)
            if swift_events is not None:
                q = query.lower()
                matches = [
                    e for e in swift_events
                    if q in (e.get("title", "") + " " + e.get("notes", "")
                             + " " + e.get("location", "")).lower()
                ]
                if not matches:
                    return f"No events matching '{query}' in the next {days_ahead} day(s)."
                matches.sort(key=lambda e: e.get("start", ""))
                return (
                    f"{len(matches)} match(es) for '{query}':\n\n"
                    + _format_swift_events(matches)
                )

            all_cal_names = _list_calendar_names()
            if not all_cal_names:
                return "Calendar error: could not list calendars."

            skip_set = set(_CALENDAR_SKIP_LIST)
            target_cals = [c for c in all_cal_names if c not in skip_set]

            matches: list[str] = []
            skipped: list[str] = []
            reached = 0
            query_lower = query.lower()

            # Longer budget for search because it spans more days (default 30d).
            TOTAL_BUDGET_S = 60.0
            deadline = _time.monotonic() + TOTAL_BUDGET_S

            for cal in target_cals:
                if _time.monotonic() >= deadline:
                    skipped.append(cal)
                    continue
                try:
                    _, events_block = _query_one_calendar_events(cal, start_dt, end_dt)
                    reached += 1
                    for line in events_block.splitlines():
                        if line.strip() and query_lower in line.lower():
                            matches.append(f"[{cal}] {line}")
                except Exception:
                    skipped.append(cal)

            if not matches:
                note = (
                    f" ({reached}/{len(target_cals)} calendars searched; "
                    f"{len(skipped)} deferred)" if skipped else ""
                )
                return f"No events matching '{query}' in the next {days_ahead} day(s){note}."

            matches.sort()
            header = f"{len(matches)} match(es) for '{query}' across {reached}/{len(target_cals)} calendars"
            if skipped:
                header += f" ({len(skipped)} deferred)"
            return header + ":\n\n" + "\n".join(matches)

    class _DeleteEventInput(BaseModel):
        title: str = Field(description="Exact title of the event to delete")
        date: str = Field(
            description="Date of the event (e.g. 'April 20, 2026')"
        )

    class DeleteEventTool(BaseTool):
        name: str = "delete_calendar_event"
        description: str = (
            "Delete a calendar event by its exact title and date."
        )
        args_schema: Type[BaseModel] = _DeleteEventInput

        def _run(self, title: str, date: str) -> str:
            target_dt = _parse_natural_date(date)
            if not target_dt:
                return f"Calendar error: could not parse date '{date}'. Try ISO format like '2026-04-20'."

            title_escaped = title.replace('"', '\\"')
            start_of_day = target_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = target_dt.replace(hour=23, minute=59, second=59, microsecond=0)

            start_script = _applescript_date_from_components("targetDate", start_of_day, at_midnight=True)
            end_script = _applescript_date_from_components("endDate", end_of_day, at_midnight=False)

            # Iterate per-calendar to find the event; delete on first hit.
            skip_list_str = ", ".join(f'"{_escape_applescript_string(s)}"' for s in _CALENDAR_SKIP_LIST)
            script = f'''
with timeout of 60 seconds
tell application "Calendar"
    {start_script}
    {end_script}
    set skipList to {{{skip_list_str}}}
    set deletedFrom to ""
    repeat with cal in calendars
        set calName to name of cal
        if calName is in skipList then
            -- skipped
        else
            try
                with timeout of 10 seconds
                    set matched to (every event of cal whose summary is "{title_escaped}" and start date >= targetDate and start date <= endDate)
                    if (count of matched) > 0 then
                        delete item 1 of matched
                        set deletedFrom to calName
                        exit repeat
                    end if
                end timeout
            on error
                -- continue searching other calendars
            end try
        end if
    end repeat
    if deletedFrom is "" then
        return "No event found: {title_escaped} on {date}"
    end if
    return "Deleted event: {title_escaped} on {date} from [" & deletedFrom & "]"
end tell
end timeout
'''
            return _run_applescript(script)

    return [
        ListEventsTool(),
        CreateEventTool(),
        SearchEventsTool(),
        DeleteEventTool(),
    ]

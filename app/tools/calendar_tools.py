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
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


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

    def _run_applescript(script: str) -> str:
        """Execute AppleScript via bridge and return output."""
        result = bridge.execute(["osascript", "-e", script])
        if "error" in result:
            return f"Error: {result.get('detail', result['error'])}"
        output = result.get("stdout", "").strip()
        stderr = result.get("stderr", "").strip()
        if stderr and not output:
            return f"Calendar error: {stderr[:500]}"
        return output

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
            start_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = (start_dt + timedelta(days=max(days_ahead, 1))).replace(
                hour=23, minute=59, second=59
            )

            cal_filter = ""
            if calendar_name:
                cal_filter = f'of calendar "{calendar_name}"'

            start_script = _applescript_date_from_components("startDate", start_dt, at_midnight=True)
            end_script = _applescript_date_from_components("endDate", end_dt, at_midnight=False)

            script = f'''
tell application "Calendar"
    {start_script}
    {end_script}
    set output to ""
    set allEvents to (every event {cal_filter} whose start date >= startDate and start date <= endDate)
    repeat with evt in allEvents
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
    if output is "" then
        return "No events found."
    end if
    return output
end tell
'''
            return _run_applescript(script)

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
            start_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = (start_dt + timedelta(days=days_ahead)).replace(
                hour=23, minute=59, second=59
            )
            query_escaped = query.replace('"', '\\"')

            start_script = _applescript_date_from_components("startDate", start_dt, at_midnight=True)
            end_script = _applescript_date_from_components("endDate", end_dt, at_midnight=False)

            script = f'''
tell application "Calendar"
    {start_script}
    {end_script}
    set output to ""
    set allEvents to (every event whose start date >= startDate and start date <= endDate and summary contains "{query_escaped}")
    repeat with evt in allEvents
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
    if output is "" then
        return "No events matching '{query_escaped}' found."
    end if
    return output
end tell
'''
            return _run_applescript(script)

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

            script = f'''
tell application "Calendar"
    {start_script}
    {end_script}
    set matched to (every event whose summary is "{title_escaped}" and start date >= targetDate and start date <= endDate)
    if (count of matched) is 0 then
        return "No event found: {title_escaped} on {date}"
    end if
    delete item 1 of matched
    return "Deleted event: {title_escaped} on {date}"
end tell
'''
            return _run_applescript(script)

    return [
        ListEventsTool(),
        CreateEventTool(),
        SearchEventsTool(),
        DeleteEventTool(),
    ]

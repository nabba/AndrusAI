"""
calendar_tools.py — macOS Calendar automation via AppleScript through bridge.

Integrates with Calendar.app which syncs with iCloud, Google, Exchange.
Zero external dependencies.

Usage:
    from app.tools.calendar_tools import create_calendar_tools
    tools = create_calendar_tools("pim")
"""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


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
            start = datetime.now().strftime("%B %d, %Y")
            end = (datetime.now() + timedelta(days=max(days_ahead, 1))).strftime(
                "%B %d, %Y"
            )

            cal_filter = ""
            if calendar_name:
                cal_filter = f'of calendar "{calendar_name}"'

            script = f'''
tell application "Calendar"
    set startDate to date "{start} 12:00:00 AM"
    set endDate to date "{end} 11:59:59 PM"
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
            # Escape quotes for AppleScript
            title = title.replace('"', '\\"')
            location = location.replace('"', '\\"')
            notes = notes.replace('"', '\\"')

            cal_target = "first calendar"
            if calendar_name:
                cal_target = f'calendar "{calendar_name}"'

            props = f'summary:"{title}", start date:date "{start_date}", end date:date "{end_date}"'
            if location:
                props += f', location:"{location}"'
            if notes:
                props += f', description:"{notes}"'

            script = f'''
tell application "Calendar"
    tell {cal_target}
        make new event with properties {{{props}}}
    end tell
    return "Event created: {title} on {start_date}"
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
            start = datetime.now().strftime("%B %d, %Y")
            end = (datetime.now() + timedelta(days=days_ahead)).strftime(
                "%B %d, %Y"
            )
            query_escaped = query.replace('"', '\\"')

            script = f'''
tell application "Calendar"
    set startDate to date "{start} 12:00:00 AM"
    set endDate to date "{end} 11:59:59 PM"
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
            title_escaped = title.replace('"', '\\"')

            script = f'''
tell application "Calendar"
    set targetDate to date "{date} 12:00:00 AM"
    set endDate to date "{date} 11:59:59 PM"
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

"""
photos_tools.py — macOS Photos.app automation via AppleScript through bridge.

Integrates with Photos.app which syncs with iCloud Photo Library. Uses the
same host-bridge execute path as calendar_tools.py.

Capabilities:
  - list_albums: enumerate the user's Photos albums
  - count_photos: total photo count (library-wide or per-album)
  - recent_photos: return metadata for the N most recently added photos
  - export_recent: export recent photos to a workspace path for inspection

Privacy note: the first AppleScript call will prompt macOS for Photos
access. Grant it in System Settings → Privacy & Security → Photos → Terminal
(or whichever process runs the host bridge).

Usage:
    from app.tools.photos_tools import create_photos_tools
    tools = create_photos_tools("pim")
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ── AppleScript snippets ─────────────────────────────────────────────────────

_SCRIPT_LIST_ALBUMS = '''
tell application "Photos"
    set albumNames to {}
    repeat with a in albums
        set end of albumNames to (name of a as text)
    end repeat
    set AppleScript's text item delimiters to "\n"
    return albumNames as text
end tell
'''

_SCRIPT_COUNT_PHOTOS = '''
tell application "Photos"
    return (count of every media item) as text
end tell
'''

_SCRIPT_COUNT_IN_ALBUM_TEMPLATE = '''
tell application "Photos"
    try
        set a to album "__ALBUM__"
    on error
        return "ERROR: album not found"
    end try
    return (count of media items of a) as text
end tell
'''

_SCRIPT_RECENT_TEMPLATE = '''
tell application "Photos"
    set allItems to every media item
    set total to count of allItems
    set n to __N__
    if n > total then set n to total
    set lines to {}
    repeat with i from (total - n + 1) to total
        set m to item i of allItems
        set mId to id of m
        try
            set mDate to (date of m) as string
        on error
            set mDate to ""
        end try
        try
            set mName to (name of m) as string
        on error
            set mName to ""
        end try
        set end of lines to mId & "\t" & mDate & "\t" & mName
    end repeat
    set AppleScript's text item delimiters to "\n"
    return lines as text
end tell
'''


def _osascript(bridge, script: str) -> dict:
    """Run AppleScript via the host bridge `execute` endpoint."""
    return bridge.execute(["osascript", "-e", script], timeout=30)


def _quote_applescript_string(s: str) -> str:
    """Minimal AppleScript string escape: backslashes and double quotes."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def create_photos_tools(agent_id: str) -> list:
    """Create Photos tools via AppleScript through bridge.

    Returns an empty list when the bridge is unavailable or when the
    process lacks a capability token for the requesting agent.
    """
    try:
        from app.bridge_client import get_bridge
        bridge = get_bridge(agent_id)
        if not bridge:
            return []
        if not bridge.is_available():
            logger.debug(f"photos_tools: bridge unavailable for {agent_id}")
            return []
    except Exception:
        return []

    try:
        from crewai.tools import BaseTool
        from pydantic import BaseModel, Field
    except ImportError:
        return []

    class _EmptyInput(BaseModel):
        pass

    class _AlbumInput(BaseModel):
        album: str = Field(default="", description="Optional album name; blank = whole library")

    class _RecentInput(BaseModel):
        n: int = Field(default=10, description="How many recent photos to return (1–100)")

    class ListAlbumsTool(BaseTool):
        name: str = "photos_list_albums"
        description: str = "List all Photos.app album names for the signed-in iCloud library."
        args_schema: type = _EmptyInput

        def _run(self) -> str:
            result = _osascript(bridge, _SCRIPT_LIST_ALBUMS)
            if "error" in result:
                return f"Photos error: {result.get('detail', result['error'])}"
            out = (result.get("stdout") or "").strip()
            if not out:
                return "No albums found."
            albums = [a for a in out.splitlines() if a.strip()]
            return f"Albums ({len(albums)}):\n" + "\n".join(f"  - {a}" for a in albums)

    class CountPhotosTool(BaseTool):
        name: str = "photos_count"
        description: str = (
            "Count photos in the library, or in a specific album if `album` is provided."
        )
        args_schema: type = _AlbumInput

        def _run(self, album: str = "") -> str:
            if album.strip():
                script = _SCRIPT_COUNT_IN_ALBUM_TEMPLATE.replace(
                    "__ALBUM__", _quote_applescript_string(album.strip())
                )
            else:
                script = _SCRIPT_COUNT_PHOTOS
            result = _osascript(bridge, script)
            if "error" in result:
                return f"Photos error: {result.get('detail', result['error'])}"
            out = (result.get("stdout") or "").strip()
            if out.startswith("ERROR:"):
                return out
            target = f"album '{album}'" if album else "library"
            return f"{target}: {out} photos"

    class RecentPhotosTool(BaseTool):
        name: str = "photos_recent"
        description: str = (
            "Return metadata (id, date, name) for the N most recently added photos."
        )
        args_schema: type = _RecentInput

        def _run(self, n: int = 10) -> str:
            try:
                n = max(1, min(int(n), 100))
            except (TypeError, ValueError):
                n = 10
            script = _SCRIPT_RECENT_TEMPLATE.replace("__N__", str(n))
            result = _osascript(bridge, script)
            if "error" in result:
                return f"Photos error: {result.get('detail', result['error'])}"
            out = (result.get("stdout") or "").strip()
            if not out:
                return "No photos found."
            lines = [l for l in out.splitlines() if l.strip()]
            formatted = []
            for line in lines:
                parts = line.split("\t")
                if len(parts) >= 2:
                    pid, date = parts[0], parts[1]
                    name = parts[2] if len(parts) > 2 else ""
                    formatted.append(f"  - [{date}] {name or '(unnamed)'} (id: {pid[:20]}…)")
                else:
                    formatted.append(f"  - {line}")
            return f"Last {len(formatted)} photos:\n" + "\n".join(formatted)

    return [ListAlbumsTool(), CountPhotosTool(), RecentPhotosTool()]

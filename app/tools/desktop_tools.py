"""
desktop_tools.py — macOS desktop automation via Host Bridge.

All tools execute on the host via bridge.execute() using macOS built-in
commands: osascript, screencapture, shortcuts, pbcopy/pbpaste, open.

Zero external dependencies.

Usage:
    from app.tools.desktop_tools import create_desktop_tools
    tools = create_desktop_tools("desktop")
"""

import logging

logger = logging.getLogger(__name__)


def create_desktop_tools(agent_id: str) -> list:
    """Create macOS desktop automation tools via bridge.

    Returns empty list if bridge is unavailable.
    """
    try:
        from app.bridge_client import get_bridge
        bridge = get_bridge(agent_id)
        if not bridge:
            return []
        if not bridge.is_available():
            logger.debug(f"desktop_tools: bridge unavailable for {agent_id}")
            return []
    except Exception:
        return []

    try:
        from crewai.tools import BaseTool
        from pydantic import BaseModel, Field
        from typing import Type
    except ImportError:
        return []

    # ── Tool definitions ──────────────────────────────────────────

    class _AppleScriptInput(BaseModel):
        script: str = Field(description="AppleScript source code to execute")

    class RunAppleScriptTool(BaseTool):
        name: str = "run_applescript"
        description: str = (
            "Execute AppleScript code on the macOS host. "
            "Use this to control any scriptable application (Finder, Safari, "
            "Mail, Calendar, System Events, etc.). Returns script output."
        )
        args_schema: Type[BaseModel] = _AppleScriptInput

        def _run(self, script: str) -> str:
            result = bridge.execute(["osascript", "-e", script])
            if "error" in result:
                return f"Error: {result.get('detail', result['error'])}"
            output = result.get("stdout", "").strip()
            stderr = result.get("stderr", "").strip()
            if stderr and not output:
                return f"AppleScript error: {stderr[:500]}"
            return output if output else "Script executed successfully (no output)."

    class _JXAInput(BaseModel):
        script: str = Field(
            description="JavaScript for Automation (JXA) source code to execute"
        )

    class RunJXATool(BaseTool):
        name: str = "run_jxa"
        description: str = (
            "Execute JavaScript for Automation (JXA) on macOS. "
            "JXA is Apple's JavaScript-based alternative to AppleScript. "
            "Use for complex automation with modern syntax."
        )
        args_schema: Type[BaseModel] = _JXAInput

        def _run(self, script: str) -> str:
            result = bridge.execute(["osascript", "-l", "JavaScript", "-e", script])
            if "error" in result:
                return f"Error: {result.get('detail', result['error'])}"
            output = result.get("stdout", "").strip()
            stderr = result.get("stderr", "").strip()
            if stderr and not output:
                return f"JXA error: {stderr[:500]}"
            return output if output else "Script executed successfully (no output)."

    class _ScreenCaptureInput(BaseModel):
        filename: str = Field(
            default="screenshot.png",
            description="Filename for the screenshot (saved to workspace/output/docs/)",
        )
        region: str = Field(
            default="",
            description="Capture region as 'x,y,w,h' (pixels). Empty = full screen.",
        )

    class ScreenCaptureTool(BaseTool):
        name: str = "screen_capture"
        description: str = (
            "Take a screenshot of the macOS desktop. "
            "Saves to workspace output directory. Optionally capture a specific region."
        )
        args_schema: Type[BaseModel] = _ScreenCaptureInput

        def _run(self, filename: str = "screenshot.png", region: str = "") -> str:
            output_dir = "/Users/andrus/BotArmy/crewai-team/workspace/output/docs"
            path = f"{output_dir}/{filename}"
            cmd = ["screencapture", "-x"]  # -x = no sound
            if region:
                cmd.extend(["-R", region])
            cmd.append(path)
            result = bridge.execute(cmd)
            if "error" in result:
                return f"Error: {result.get('detail', result['error'])}"
            return f"Screenshot saved to {path}"

    class _ClipboardInput(BaseModel):
        action: str = Field(
            description="'read' to get clipboard contents, 'write' to set clipboard"
        )
        content: str = Field(
            default="",
            description="Content to write to clipboard (only for 'write' action)",
        )

    class ClipboardTool(BaseTool):
        name: str = "clipboard"
        description: str = (
            "Read from or write to the macOS clipboard. "
            "Use action='read' to get current clipboard, action='write' to set it."
        )
        args_schema: Type[BaseModel] = _ClipboardInput

        def _run(self, action: str, content: str = "") -> str:
            if action == "read":
                result = bridge.execute(["pbpaste"])
                if "error" in result:
                    return f"Error: {result.get('detail', result['error'])}"
                return result.get("stdout", "(clipboard empty)")
            elif action == "write":
                # Use osascript to set clipboard (pbcopy needs stdin)
                script = f'set the clipboard to "{content}"'
                result = bridge.execute(["osascript", "-e", script])
                if "error" in result:
                    return f"Error: {result.get('detail', result['error'])}"
                return f"Clipboard set to: {content[:100]}..."
            return "Invalid action. Use 'read' or 'write'."

    class _ShortcutInput(BaseModel):
        name: str = Field(description="Name of the Apple Shortcut to run")
        input_text: str = Field(
            default="",
            description="Optional text input to pass to the shortcut",
        )

    class RunShortcutTool(BaseTool):
        name: str = "run_shortcut"
        description: str = (
            "Run an Apple Shortcut by name. "
            "Shortcuts are automations created in the macOS Shortcuts app. "
            "Optionally pass text input to the shortcut."
        )
        args_schema: Type[BaseModel] = _ShortcutInput

        def _run(self, name: str, input_text: str = "") -> str:
            cmd = ["shortcuts", "run", name]
            if input_text:
                cmd.extend(["--input-path", "-"])
            result = bridge.execute(cmd)
            if "error" in result:
                return f"Error: {result.get('detail', result['error'])}"
            output = result.get("stdout", "").strip()
            return output if output else f"Shortcut '{name}' executed."

    class _OpenInput(BaseModel):
        target: str = Field(
            description="App name (e.g. 'Safari'), URL, or file path to open"
        )

    class OpenTool(BaseTool):
        name: str = "open_on_mac"
        description: str = (
            "Open an application, URL, or file on macOS. "
            "Examples: 'Safari', 'https://example.com', '/path/to/file.pdf'"
        )
        args_schema: Type[BaseModel] = _OpenInput

        def _run(self, target: str) -> str:
            if target.startswith(("http://", "https://")):
                cmd = ["open", target]
            elif "." not in target and "/" not in target:
                # Looks like an app name
                cmd = ["open", "-a", target]
            else:
                cmd = ["open", target]
            result = bridge.execute(cmd)
            if "error" in result:
                return f"Error: {result.get('detail', result['error'])}"
            return f"Opened: {target}"

    class _WindowInput(BaseModel):
        action: str = Field(
            description="Action: 'list' (list windows), 'focus' (bring to front), 'minimize', 'fullscreen'"
        )
        app_name: str = Field(
            default="",
            description="Application name to target (e.g. 'Safari', 'Finder')",
        )

    class WindowManagerTool(BaseTool):
        name: str = "manage_window"
        description: str = (
            "Manage macOS application windows. "
            "Actions: list (running apps), focus (activate app), minimize, fullscreen."
        )
        args_schema: Type[BaseModel] = _WindowInput

        def _run(self, action: str, app_name: str = "") -> str:
            if action == "list":
                script = (
                    'tell application "System Events" to get name '
                    "of every process whose background only is false"
                )
            elif action == "focus" and app_name:
                script = f'tell application "{app_name}" to activate'
            elif action == "minimize" and app_name:
                script = (
                    f'tell application "System Events" to tell process "{app_name}" '
                    f"to set miniaturized of every window to true"
                )
            elif action == "fullscreen" and app_name:
                script = (
                    f'tell application "System Events" to tell process "{app_name}" '
                    f'to set value of attribute "AXFullScreen" of window 1 to true'
                )
            else:
                return f"Invalid action '{action}' or missing app_name."

            result = bridge.execute(["osascript", "-e", script])
            if "error" in result:
                return f"Error: {result.get('detail', result['error'])}"
            return result.get("stdout", "").strip() or f"{action} completed for {app_name}."

    return [
        RunAppleScriptTool(),
        RunJXATool(),
        ScreenCaptureTool(),
        ClipboardTool(),
        RunShortcutTool(),
        OpenTool(),
        WindowManagerTool(),
    ]

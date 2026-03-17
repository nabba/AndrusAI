from crewai.tools import tool
import pathlib

WORKSPACE = pathlib.Path("/app/workspace/output").resolve()


@tool("file_manager")
def file_manager(action: str, path: str, content: str = "") -> str:
    """
    Read and write files scoped to workspace/output/ only.
    action: 'read' or 'write'
    path: relative path within workspace/output/ (e.g., 'report.md')
    content: text to write (only for 'write' action)
    """
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    # Resolve and validate path — must stay within workspace
    target = (WORKSPACE / path).resolve()
    if not str(target).startswith(str(WORKSPACE)):
        return "Error: Path traversal detected. Access denied."

    if action == "read":
        if not target.exists():
            return f"Error: File not found: {path}"
        return target.read_text()[:32000]
    elif action == "write":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"Written to {path} ({len(content)} chars)"
    else:
        return f"Error: Unknown action '{action}'. Use 'read' or 'write'."

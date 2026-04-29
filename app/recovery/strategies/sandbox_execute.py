"""
sandbox_execute.py — recovery strategy: actually run the code the
coding crew dumped.

The 2026-04-25 regression: coding crew responded with a 230-line
Python script that ended with::

    EXECUTION OUTPUT (stdout):
    <unavailable in this environment: no connected execution tool /
     MCP server to run python and capture real stdout>

The script was correct; we just had no plumbing wired up to actually
run it. This strategy closes that loop by extracting the largest
Python code block from the response and running it in the existing
Docker sandbox (the same one ``app.sandbox_runner`` uses for
evolution-mutation safety checks).

Limitations:
  * Sandbox has NO network — scripts that hit the live web fail.
    For research-shaped scripts that need DuckDuckGo/LinkedIn, this
    strategy is the wrong tool; ``re_route`` to research crew with
    web tools is better.
  * Sandbox is read-only + no host filesystem — scripts that try to
    open files at user-specific paths fail.
  * 60-second wall-clock cap on execution.

When the strategy detects a non-runnable script (matches one of the
above limitations), it returns success=False so the loop tries the
next strategy.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from app.recovery.librarian import Alternative
from app.recovery.strategies import StrategyResult

logger = logging.getLogger(__name__)

_SANDBOX_IMAGE = "crewai-sandbox:latest"
_EXEC_TIMEOUT_S = 60
_OUTPUT_CAP_CHARS = 8000

# Heuristics for detecting "this script needs network/filesystem and
# won't run in our sandbox" — better to skip than spin up a container
# that's guaranteed to fail.
_NETWORK_REQUIRED = re.compile(
    r"\b(?:requests\.|urllib\.|aiohttp|httpx\.|socket\.|smtplib"
    r"|imaplib|paramiko|ftplib|telnetlib|websocket"
    r"|fetch\s*\(|httpx\.)\b",
    re.IGNORECASE,
)
_HOST_FS_REQUIRED = re.compile(
    r"['\"](?:/Users/|/home/|C:\\|~/)[^'\"]+['\"]",
    re.IGNORECASE,
)


def _extract_python_code(response_text: str) -> str | None:
    """Return the largest Python code block in ``response_text``, or None."""
    if not response_text:
        return None
    # Match ```python or ```py or just ``` (assume python)
    blocks = re.findall(
        r"```(?:python|py)?\s*\n(.*?)\n```",
        response_text, re.DOTALL,
    )
    if not blocks:
        return None
    # Pick the largest — usually the main script, not little snippets
    best = max(blocks, key=len)
    return best.strip() if best.strip() else None


def _is_runnable_in_sandbox(code: str) -> tuple[bool, str]:
    """Return (runnable, reason). Quick sanity check before launching docker."""
    if len(code) < 20:
        return False, "code too short"
    if _NETWORK_REQUIRED.search(code):
        return False, "script requires network access (sandbox has --network none)"
    if _HOST_FS_REQUIRED.search(code):
        return False, "script references host-specific paths (sandbox is read-only)"
    return True, ""


def _run_in_sandbox(code: str, timeout_s: int) -> tuple[int, str, str]:
    """Run ``code`` in the sandbox and return (exit_code, stdout, stderr).

    Mirrors app/sandbox_runner.py:run_code_check but returns rich
    output instead of bool. Same isolation flags (--network none,
    --read-only, capability-drop, mem cap).
    """
    tmp_dir = tempfile.mkdtemp(prefix="recovery_exec_")
    container_name = f"recovery-exec-{int(time.time() * 1000) % 1_000_000}"
    try:
        script_path = Path(tmp_dir) / "script.py"
        script_path.write_text(code)

        cmd = [
            "docker", "run", "--rm",
            "--name", container_name,
            "--network", "none",
            "--memory", "512m",
            "--cpus", "1",
            "--security-opt", "no-new-privileges:true",
            "--read-only",
            "--tmpfs", "/tmp:size=256m",
            "-v", f"{tmp_dir}:/work:ro",
            _SANDBOX_IMAGE,
            "python", "/work/script.py",
        ]
        result = subprocess.run(
            cmd, capture_output=True,
            timeout=timeout_s + 10,  # grace for container overhead
        )
        stdout = result.stdout.decode(errors="replace")[:_OUTPUT_CAP_CHARS]
        stderr = result.stderr.decode(errors="replace")[:_OUTPUT_CAP_CHARS]
        return result.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        try:
            subprocess.run(
                ["docker", "kill", container_name],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass
        return -1, "", f"execution exceeded {timeout_s}s timeout"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def execute(task: str, alt: Alternative, ctx: dict) -> StrategyResult:
    """Extract Python code from the original response, run it, return stdout."""
    original_response = ctx.get("original_response", "")
    code = _extract_python_code(original_response)
    if not code:
        return StrategyResult(
            success=False,
            error="sandbox_execute: no Python code block found in response",
        )

    runnable, reason = _is_runnable_in_sandbox(code)
    if not runnable:
        logger.info("sandbox_execute: skipping (%s) — letting next strategy try", reason)
        return StrategyResult(success=False, error=f"sandbox_execute: {reason}")

    logger.info(
        "sandbox_execute: running %d-char script in sandbox (timeout=%ds)",
        len(code), _EXEC_TIMEOUT_S,
    )
    exit_code, stdout, stderr = _run_in_sandbox(code, _EXEC_TIMEOUT_S)

    if exit_code != 0:
        # Run failed — strategy fails, next one tries
        return StrategyResult(
            success=False,
            error=(
                f"sandbox_execute: script exited {exit_code}; "
                f"stderr: {stderr[:300]}"
            ),
        )

    if not stdout.strip():
        return StrategyResult(
            success=False,
            error="sandbox_execute: script ran but produced no stdout",
        )

    # Compose a delivered answer that includes both the code (so user
    # sees what we ran) and its actual output.
    answer = (
        f"Ran the script the coding crew produced. Output:\n\n"
        f"```\n{stdout.strip()}\n```\n\n"
        f"Source script (for reference):\n\n```python\n{code[:2000]}\n```"
    )
    if len(code) > 2000:
        answer += f"\n\n(script truncated to first 2000 chars; full length {len(code)} chars)"

    return StrategyResult(
        success=True,
        text=answer,
        note="Executed the proposed script in a sandboxed Docker container instead of just dumping code.",
        route_changed=True,
    )

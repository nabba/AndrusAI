"""
response_utils.py — Response formatting and file management utilities.

Extracted from main.py to reduce gravity-well coupling.
Handles writing full responses as .md files and pruning old files.
"""

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_WORKSPACE_ROOT = "/app/workspace"
_RESPONSE_OUTPUT_DIR = os.path.join(_WORKSPACE_ROOT, "output", "responses")
_MAX_RESPONSE_FILES = 50


def write_response_md(full_text: str, user_question: str, settings) -> str | None:
    """Write full response as .md file. Returns HOST path for signal-cli or None."""
    try:
        os.makedirs(_RESPONSE_OUTPUT_DIR, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"response_{ts}.md"
        docker_path = os.path.join(_RESPONSE_OUTPUT_DIR, filename)

        question_preview = user_question[:200] if user_question else "N/A"
        content = f"# Response\n\n**Question:** {question_preview}\n\n---\n\n{full_text}\n"
        with open(docker_path, "w", encoding="utf-8") as f:
            f.write(content)

        host_workspace = settings.workspace_host_path
        if not host_workspace:
            logger.warning("WORKSPACE_HOST_PATH not set — cannot attach .md to Signal")
            return None

        host_path = docker_path.replace(_WORKSPACE_ROOT, host_workspace)
        logger.info(f"Response .md written: {docker_path} → {host_path}")
        prune_response_files()
        return host_path
    except Exception:
        logger.warning("Failed to write response .md", exc_info=True)
        return None


def prune_response_files() -> None:
    """Delete old response files beyond _MAX_RESPONSE_FILES."""
    try:
        files = sorted(
            [f for f in os.listdir(_RESPONSE_OUTPUT_DIR) if f.startswith("response_")],
            reverse=True,
        )
        for old_file in files[_MAX_RESPONSE_FILES:]:
            os.unlink(os.path.join(_RESPONSE_OUTPUT_DIR, old_file))
    except Exception:
        pass


def extract_to_mem0(user_text: str, assistant_result: str) -> None:
    """Background: extract facts from conversation into Mem0 persistent memory."""
    if not user_text or not isinstance(user_text, str):
        return
    if not assistant_result or not isinstance(assistant_result, str):
        return
    if len(assistant_result.strip()) < 20:
        return
    try:
        from app.memory.mem0_manager import store_conversation
        store_conversation(
            messages=[
                {"role": "user", "content": user_text[:2000]},
                {"role": "assistant", "content": assistant_result[:4000]},
            ],
        )
    except Exception:
        logger.debug("mem0: conversation extraction failed", exc_info=True)

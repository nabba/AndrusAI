"""dockerfile_pin_staleness — A3-P1 — alert on long-unpinned Dockerfile.

PROGRAM §63.11. ``dockerfile_writer`` (P0#4) deliberately drops the
SHA-256 digest pin on Python bumps + inserts a ``# TODO P0#4: re-pin``
comment. This monitor watches for the case the operator never gets
around to re-pinning — the image stays anchored to the tag (no
digest), which over time means we silently pull a different image
than the one we tested.

Logic:

  * Open the repo-root Dockerfile.
  * Find every ``FROM python:`` line.
  * For each line, check whether the operator has added back an
    ``@sha256:<digest>`` suffix.
  * If ANY line is unpinned AND the surrounding context still has
    the ``# TODO P0#4: re-pin`` marker comment, this is the post-
    bump-window we're watching. Fire a Signal alert.
  * The alert dedup-keys per Dockerfile path so it fires once per
    week, not on every cadence tick.

Cadence: daily probe, internal weekly cadence after first fire.

Master switch: ``dockerfile_pin_staleness_monitor_enabled``
(default ON — operator opted in when they enabled
``dockerfile_writer``; if the writer is OFF this monitor does
nothing useful but it's cheap enough to leave running).
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


NAME = "dockerfile_pin_staleness"
CADENCE_SECONDS = 24 * 3600
INTERNAL_WEEKLY_S = 7 * 24 * 3600
MASTER_SWITCH_KEY = "dockerfile_pin_staleness_monitor_enabled"


_TODO_MARKER = "TODO P0#4: re-pin"
_FROM_PYTHON_RE = re.compile(
    r"^FROM\s+python:[^\s@]+(@sha256:[0-9a-f]{64})?",
    re.MULTILINE,
)


def _state_path() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "healing" / ".dockerfile_pin_state.json"
    except Exception:
        return Path("/app/workspace/healing/.dockerfile_pin_state.json")


def _enabled() -> bool:
    try:
        from app.runtime_settings import (
            get_dockerfile_pin_staleness_monitor_enabled,
        )
        return get_dockerfile_pin_staleness_monitor_enabled()
    except Exception:
        return True


def _dockerfile_path() -> Path:
    override = os.getenv("DOCKERFILE_PATH")
    if override:
        return Path(override)
    try:
        from app.paths import WORKSPACE_ROOT
        # Dockerfile sits at repo root, one level up from workspace
        return Path(WORKSPACE_ROOT).parent / "Dockerfile"
    except Exception:
        return Path("/app/Dockerfile")


def _read_state() -> dict:
    import json
    p = _state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(state: dict) -> None:
    import json
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
        tmp.replace(p)
    except OSError:
        logger.debug("dockerfile_pin: state write failed", exc_info=True)


def check_dockerfile(text: str) -> tuple[bool, int, int]:
    """Pure function — analyses Dockerfile *text* and returns
    ``(should_alert, total_from_lines, unpinned_from_lines)``.

    ``should_alert`` is True when:
      * the file contains the ``# TODO P0#4: re-pin`` marker comment
        (i.e. we ARE in a post-bump-window the writer created), AND
      * at least one ``FROM python:`` line has no ``@sha256:`` suffix.
    """
    has_todo = _TODO_MARKER in text
    matches = list(_FROM_PYTHON_RE.finditer(text))
    total = len(matches)
    unpinned = sum(1 for m in matches if not (m.group(1) or "").startswith("@sha256"))
    return (has_todo and unpinned > 0), total, unpinned


def _notify(body: str) -> None:
    try:
        from app.notify import notify
        notify(
            title="🐳 Dockerfile pin missing",
            body=body,
            url="/cp/changes",
            topic="dockerfile_pin_staleness",
            critical=False, arbitrate=True,
        )
    except Exception:
        logger.debug("dockerfile_pin: notify failed", exc_info=True)


def run() -> None:
    """Driver entry — daily probe + weekly internal cadence."""
    if not _enabled():
        return
    now_ts = time.time()
    state = _read_state()
    last_alert = float(state.get("last_alert_at") or 0.0)

    path = _dockerfile_path()
    if not path.exists():
        return

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return

    should_alert, total, unpinned = check_dockerfile(text)
    if not should_alert:
        # Clear the state so when the next bump happens (and the
        # writer re-introduces the TODO marker), the dedup window
        # restarts fresh.
        if state.get("alerting"):
            state["alerting"] = False
            state["resolved_at"] = datetime.now(timezone.utc).isoformat()
            _write_state(state)
        return

    if last_alert > 0 and (now_ts - last_alert) < INTERNAL_WEEKLY_S:
        return

    days_since_first = 0
    if state.get("first_unpinned_at"):
        try:
            first_iso = state["first_unpinned_at"]
            first_dt = datetime.fromisoformat(str(first_iso))
            if first_dt.tzinfo is None:
                first_dt = first_dt.replace(tzinfo=timezone.utc)
            days_since_first = (datetime.now(timezone.utc) - first_dt).days
        except (ValueError, TypeError):
            pass
    else:
        state["first_unpinned_at"] = datetime.now(timezone.utc).isoformat()

    body = (
        f"`{path.name}` has {unpinned}/{total} ``FROM python:`` line(s) "
        f"without an `@sha256:` digest pin and still carries the "
        f"`# TODO P0#4: re-pin` marker. "
    )
    if days_since_first > 0:
        body += f"Unpinned for ~{days_since_first}d. "
    body += (
        "Operator: pull the image, capture its digest with "
        "`docker inspect --format='{{.RepoDigests}}'`, and replace "
        "the TODO comment with the canonical `@sha256:<digest>` line."
    )
    _notify(body)
    state["last_alert_at"] = now_ts
    state["alerting"] = True
    _write_state(state)

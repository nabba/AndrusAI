"""Capability snapshot capture + persistence.

A snapshot is the union of three sorted lists:

  * tools         — every name in ``ToolRegistry.instance().names()``
  * models        — every llm_catalog key NOT currently in the
                     runtime_settings blocked-models list (the
                     "effective" set agents can actually reach)
  * blocked_models — the runtime_settings list, recorded for
                     transparency so the detector can tell
                     "operator blocked it" apart from "catalog lost it"

Persistence is plain JSON at
``workspace/capability_regression/snapshot.json`` (single most-recent
snapshot — the detector compares prev vs curr in memory) plus an
append-only history JSONL at the same prefix so operators can audit
what the capability surface looked like at any past hour.

Every public call is failure-isolated — a snapshot capture that throws
returns an empty snapshot rather than raising, so the scheduler job
never crashes on a transient registry / catalog hiccup.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass
class CapabilitySnapshot:
    """Frozen view of what tools + models are reachable RIGHT NOW.

    All lists are sorted so equality comparison + diffing are stable.
    """

    schema_version: int = SCHEMA_VERSION
    captured_at: str = ""
    tools: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    blocked_models: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CapabilitySnapshot":
        return cls(
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            captured_at=str(data.get("captured_at", "")),
            tools=sorted(map(str, data.get("tools") or [])),
            models=sorted(map(str, data.get("models") or [])),
            blocked_models=sorted(map(str, data.get("blocked_models") or [])),
        )


def _snapshot_dir() -> Path:
    from app.paths import WORKSPACE_ROOT
    p = WORKSPACE_ROOT / "capability_regression"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _collect_tool_names() -> list[str]:
    try:
        from app.tool_registry.registry import ToolRegistry
        return sorted(ToolRegistry.instance().names())
    except Exception:
        logger.debug(
            "capability_regression: tool registry read failed", exc_info=True,
        )
        return []


def _collect_catalog_keys() -> list[str]:
    try:
        from app.llm_catalog import CATALOG
        return sorted(CATALOG.keys())
    except Exception:
        logger.debug(
            "capability_regression: llm_catalog read failed", exc_info=True,
        )
        return []


def _collect_blocked_models() -> list[str]:
    blocked: set[str] = set()
    try:
        from app import runtime_settings
        blocked.update(runtime_settings.get_chat_blocked_models() or [])
        blocked.update(runtime_settings.get_no_function_calling_models() or [])
    except Exception:
        logger.debug(
            "capability_regression: runtime_settings read failed", exc_info=True,
        )
    return sorted(blocked)


def take_snapshot() -> CapabilitySnapshot:
    """Capture the current capability surface.

    Failure-isolated — each collector independently catches + falls
    back to empty so a single broken subsystem doesn't poison the
    other halves of the snapshot.
    """
    catalog = _collect_catalog_keys()
    blocked = _collect_blocked_models()
    effective = sorted(set(catalog) - set(blocked))
    return CapabilitySnapshot(
        schema_version=SCHEMA_VERSION,
        captured_at=_now_iso(),
        tools=_collect_tool_names(),
        models=effective,
        blocked_models=blocked,
    )


def _snapshot_path() -> Path:
    return _snapshot_dir() / "snapshot.json"


def _history_path() -> Path:
    return _snapshot_dir() / "history.jsonl"


def load_snapshot() -> Optional[CapabilitySnapshot]:
    """Return the most-recent saved snapshot, or None if no prior run."""
    path = _snapshot_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning(
            "capability_regression: corrupted snapshot at %s — treating "
            "as absent", path,
        )
        return None
    return CapabilitySnapshot.from_dict(data)


def save_snapshot(snap: CapabilitySnapshot) -> None:
    """Persist as the new current snapshot + append to history.

    Atomic write via temp-file rename. Failure-isolated — a disk-full
    or perms error logs but never raises out.
    """
    path = _snapshot_path()
    tmp = path.with_suffix(".json.tmp")
    payload = json.dumps(snap.to_dict(), indent=2, sort_keys=True)
    try:
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    except Exception:
        logger.warning(
            "capability_regression: snapshot save failed", exc_info=True,
        )
        return

    try:
        with _history_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(snap.to_dict(), sort_keys=True) + "\n")
    except Exception:
        logger.debug(
            "capability_regression: history append failed", exc_info=True,
        )

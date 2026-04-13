"""
json_store.py — Atomic JSON persistence with bounded retention.

Replaces ~40 ad-hoc load-modify-save blocks across the codebase with
a single, consistent abstraction. Wraps safe_io.safe_write_json so
writes are crash-safe via tempfile + os.replace.

Usage:
    from app.json_store import JsonStore

    # List-typed store with retention cap (keeps last 500 entries)
    journal = JsonStore("workspace/error_journal.json",
                        retention_limit=500, default=[])
    entries = journal.load()
    entries.append({"at": ..., "msg": ...})
    journal.save(entries)

    # Or atomic update via callback
    journal.update(lambda entries: entries + [{"at": ..., "msg": ...}])

    # Dict-typed store
    state = JsonStore("workspace/agent_state.json", default={})
    state.update(lambda s: {**s, "last_seen": ...})
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Optional

from app.safe_io import safe_write_json

logger = logging.getLogger(__name__)


class JsonStore:
    """Atomic JSON persistence with optional retention cap on list values.

    Attributes:
        path:              Destination filesystem location.
        retention_limit:   If set and the stored value is a list, only the
                           last N entries are kept on save.
        default:           Value returned by load() when the file is missing
                           or corrupt. Deep-copied to avoid shared mutation.
    """

    def __init__(
        self,
        path: Path | str,
        retention_limit: Optional[int] = None,
        default: Optional[Any] = None,
    ) -> None:
        self.path = Path(path)
        self.retention_limit = retention_limit
        self._default = default if default is not None else {}

    # ── I/O ─────────────────────────────────────────────────────────

    def load(self) -> Any:
        """Load JSON from disk, returning a deep copy of the default on failure."""
        if not self.path.exists():
            return deepcopy(self._default)
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "json_store: failed to load %s (%s); using default",
                self.path, exc,
            )
            return deepcopy(self._default)

    def save(self, data: Any) -> None:
        """Atomically persist the given data.

        Applies retention_limit when data is a list.
        """
        if self.retention_limit is not None and isinstance(data, list):
            data = data[-self.retention_limit:]
        safe_write_json(self.path, data)

    def update(self, fn: Callable[[Any], Any]) -> Any:
        """Load, transform via fn, save. Returns the stored value.

        If fn returns None the loaded object is saved unchanged —
        useful for in-place mutations.
        """
        data = self.load()
        result = fn(data)
        stored = data if result is None else result
        self.save(stored)
        return stored

    # ── Convenience ─────────────────────────────────────────────────

    def append(self, item: Any) -> Any:
        """Append-one convenience for list stores."""
        def _append(entries: Any) -> list:
            if not isinstance(entries, list):
                entries = []
            entries.append(item)
            return entries
        return self.update(_append)

    def clear(self) -> None:
        """Reset the store to its default value."""
        self.save(deepcopy(self._default))

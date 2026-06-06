"""signal_ts_bridge — one implementation of the Signal-ts → id routing map.

Several subsystems keep a tiny JSON sidecar mapping a Signal-message timestamp
to a domain id, so the ``main.py`` reaction handler can resolve a 👍/👎 back to
the thing it refers to:

  * ``governance_signal_bridge``               ts → governance request id
  * ``interest_goal_signal_bridge``            ts → executor run id
  * ``life_companion.briefing_evolution.feedback_bridge``  ts → trial section id
  * ``epistemic.reaction_bridge``              ts → gate context

These were copy-pasted and had drifted (different TTLs; only one had an entry
cap). This class is the single implementation. Each module instantiates it with
its own filename / TTL / timestamp-field / cap and keeps its own public API and
**value schema** — so existing on-disk files keep working (behaviour-neutral).

Properties (matching the union of the originals):
  * thread-safe (per-instance ``RLock``);
  * atomic write (via ``safe_io.safe_write_json`` when importable, else
    tmp+replace);
  * purge-on-access by a configurable **epoch** timestamp field
    (``ts_field``); the field is stamped by :meth:`put`, owned by the bridge —
    callers supply only domain fields;
  * optional drop-oldest entry cap (``max_entries``);
  * ``persist_on_get`` controls whether a read rewrites the purged form
    (the originals differ: most persist on find, the epistemic one does not).

Every method is failure-isolated: a bridge is a routing AID; losing it just
means the operator falls back to the dashboard / text command — never a crash
or data loss in the real store.

NOTE: the ``autonomous_executor.escalation`` bridge is deliberately NOT built on
this — it stores an ISO-string timestamp and does bidirectional prefix-match
resolution, a different shape; folding it in would add a dual-timestamp/prefix
mode for little gain.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def _safe_pred(predicate: Callable[[dict], bool], value) -> bool:
    try:
        return bool(predicate(value if isinstance(value, dict) else {}))
    except Exception:
        return False


class SignalTsBridge:
    def __init__(
        self,
        path_factory: Callable[[], Path],
        *,
        max_age_seconds: float,
        ts_field: str = "created_at_epoch",
        max_entries: Optional[int] = None,
        persist_on_get: bool = True,
    ) -> None:
        self._path_factory = path_factory
        self._max_age = float(max_age_seconds)
        self._ts_field = ts_field
        self._max_entries = max_entries
        self._persist_on_get = persist_on_get
        self._lock = threading.RLock()

    # ── internal ──────────────────────────────────────────────────────
    def _path(self) -> Path:
        return self._path_factory()

    def _load_raw(self) -> dict:
        p = self._path()
        if not p.exists():
            return {}
        try:
            data = json.loads(p.read_text() or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            logger.debug("signal_ts_bridge[%s]: load failed; fresh", p.name, exc_info=True)
            return {}

    def _purge(self, data: dict) -> dict:
        now = time.time()
        kept: dict = {}
        for k, v in data.items():
            if not isinstance(v, dict):
                continue
            try:
                if (now - float(v.get(self._ts_field) or 0)) <= self._max_age:
                    kept[k] = v
            except Exception:
                continue
        return kept

    def _save(self, data: dict) -> None:
        if self._max_entries is not None and len(data) > self._max_entries:
            # Drop oldest by the timestamp field — keep the newest max_entries.
            items = sorted(
                data.items(),
                key=lambda kv: float((kv[1] or {}).get(self._ts_field) or 0),
                reverse=True,
            )
            data = dict(items[: self._max_entries])
        p = self._path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        try:
            from app.safe_io import safe_write_json
            safe_write_json(p, data)
            return
        except Exception:
            pass
        try:
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
            tmp.replace(p)
        except Exception:
            logger.debug("signal_ts_bridge[%s]: save failed", p.name, exc_info=True)

    # ── public ────────────────────────────────────────────────────────
    def put(self, key, value: dict) -> None:
        """Store ``value`` (a dict of domain fields) under ``str(key)``,
        stamping ``ts_field`` with the current epoch. Purges expired entries +
        applies the cap. Never raises."""
        if not key:
            return
        try:
            v = dict(value or {})
            v[self._ts_field] = time.time()
            with self._lock:
                data = self._purge(self._load_raw())
                data[str(key)] = v
                self._save(data)
        except Exception:
            logger.debug("signal_ts_bridge.put failed", exc_info=True)

    def get(self, key) -> Optional[dict]:
        """Return a copy of the stored dict for ``str(key)`` (post-purge), or
        None. Persists the purged form when ``persist_on_get`` is set."""
        if not key:
            return None
        try:
            with self._lock:
                raw = self._load_raw()
                kept = self._purge(raw)
                if self._persist_on_get and len(kept) != len(raw):
                    self._save(kept)
                entry = kept.get(str(key))
                return dict(entry) if isinstance(entry, dict) else None
        except Exception:
            logger.debug("signal_ts_bridge.get failed", exc_info=True)
            return None

    def remove_where(self, predicate: Callable[[dict], bool]) -> None:
        """Drop every entry whose value dict satisfies ``predicate`` (reverse-key
        cleanup, e.g. unregister by run_id). Never raises."""
        try:
            with self._lock:
                data = self._load_raw()
                kept = {k: v for k, v in data.items() if not _safe_pred(predicate, v)}
                if len(kept) != len(data):
                    self._save(kept)
        except Exception:
            logger.debug("signal_ts_bridge.remove_where failed", exc_info=True)

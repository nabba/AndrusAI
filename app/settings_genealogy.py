"""
settings_genealogy.py — Hash-chained record of every runtime_settings flip.

Why this exists
===============

``runtime_settings.json`` is the canonical store of ~80 master switches the
operator (and assorted self-heal handlers) toggle over time. The existing
``audit.log`` ``runtime_settings_change`` row captures *that* a change
happened, but loses two things future-you will want to know six months later:

  * **The before/after diff** — ``audit.log`` records only the post-change
    snapshot, not the values that were displaced.
  * **The reason** — every operator flip has a motivation; without a place
    to record it, the motivation evaporates the moment the React modal
    closes.

Genealogy is the place. Per-flip rows; one row per key changed; before +
after + actor + reason + hash chain. Sibling pattern to
``app/memory/source_ledger.py`` (decade-scale per-KB ledger),
``app/identity/continuity_ledger.py`` (identity events), and
``app/audit.py`` (security events). Same hash chain shape.

Scope: operator-driven flips only
=================================

The hook lives in the ``POST /api/cp/settings`` dispatcher
(``app/api/config_api.py``), so it captures flips the **operator**
makes via the React surface or a CLI call. System-driven flips
(``runtime_settings._update`` invoked directly by a self-heal handler
adding a model to ``chat_blocked_models``, for example) are NOT
recorded here — their canonical trail is ``audit.log`` with the
handler's actor field. This scope is deliberate: the operator is the
source of *intent*, and intent is what the genealogy preserves.

What this is NOT
================

  * Not a replacement for ``audit.log``. That stays — it records who/when
    for every change including system-driven ones. Genealogy adds the
    operator's *why* on top.
  * Not a tier-3 governance log. Tier-3 amendments have their own
    operator gate + audit ledger (``app/governance_amendment/``). This is
    for the routine flips that don't trigger Tier-3.
  * Not silently filterable. Every flip is recorded; the operator can
    pass an explicit ``redact_keys`` set if a particular value is too
    sensitive to keep in plaintext (the *key* is always recorded —
    redaction only blanks the values).

Storage
=======

One JSONL file at ``workspace/settings_genealogy.jsonl``. Each row::

    {
      "ts":        1747920000.123,                # epoch seconds
      "iso":       "2026-05-22T14:00:00.123+00:00",
      "key":       "tier3_amendment_enabled",
      "old":       false,                          # JSON-safe (str | int | float | bool | None | list | dict)
      "new":       true,
      "actor":     "operator",                     # "operator" | "self_heal:<handler>" | "boot" | ...
      "reason":    "Promoting after Q5 closure",
      "prev_hash": "<64-hex>",
      "hash":      "<64-hex>"                      # sha256(prev_hash + canonical_json(row_without_hash_fields))
    }

Hash chain matches the project's existing pattern: deterministic JSON
encoding (sort_keys=True, no whitespace), sha256 link, genesis = "0"*64.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

logger = logging.getLogger(__name__)


GENESIS_HASH = "0" * 64
_LOCK = threading.Lock()


def _workspace() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT)
    except Exception:
        return Path(os.environ.get("WORKSPACE_ROOT", "/app/workspace"))


def _ledger_path() -> Path:
    return _workspace() / "settings_genealogy.jsonl"


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _link(prev_hash: str, payload: dict) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode("utf-8"))
    h.update(_canonical(payload).encode("utf-8"))
    return h.hexdigest()


def _json_safe(value: Any) -> Any:
    """Coerce ``value`` into a JSON-encodable shape without losing
    structure for the common runtime-settings types.

    Lists/dicts are walked; everything else falls through ``str()`` so a
    pathological value never blocks recording. This is deliberately strict
    against silent data loss: ``set`` / ``tuple`` become lists, numbers
    + bools + None pass through, anything else stringifies.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, set):
        return sorted(_json_safe(v) for v in value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return str(value)


def _read_last_hash(path: Path) -> str:
    """Walk the file backward (small file — full read is fine) and
    return the last row's ``hash`` field. ``GENESIS_HASH`` if empty
    or missing."""
    if not path.exists():
        return GENESIS_HASH
    last = GENESIS_HASH
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                h = row.get("hash")
                if isinstance(h, str) and len(h) == 64:
                    last = h
    except OSError:
        return GENESIS_HASH
    return last


def record_change(
    key: str,
    old_value: Any,
    new_value: Any,
    *,
    actor: str = "operator",
    reason: str = "",
    now: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """Append one genealogy row. Returns the appended row, or None on a
    no-op (old == new) or on best-effort write failure.

    No-op suppression is deliberate: the React settings card POSTs every
    toggle in its payload on save, including unchanged ones. Recording
    those would dilute the ledger with noise.
    """
    if old_value == new_value:
        return None

    cur = float(now) if now is not None else time.time()
    iso = datetime.fromtimestamp(cur, tz=timezone.utc).isoformat()

    payload = {
        "ts": cur,
        "iso": iso,
        "key": str(key),
        "old": _json_safe(old_value),
        "new": _json_safe(new_value),
        "actor": str(actor or "unknown"),
        "reason": str(reason or "").strip(),
    }

    path = _ledger_path()
    with _LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            prev_hash = _read_last_hash(path)
            payload["prev_hash"] = prev_hash
            payload["hash"] = _link(prev_hash, payload)
            with path.open("a", encoding="utf-8") as f:
                f.write(_canonical(payload) + "\n")
        except Exception:
            logger.warning("settings_genealogy: append failed", exc_info=True)
            return None
    return payload


def record_diff(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    actor: str = "operator",
    reason: str = "",
    only_keys: Optional[Iterable[str]] = None,
) -> list[dict[str, Any]]:
    """Compare two snapshots and append one row per changed key.

    When ``only_keys`` is provided, restrict the diff to that set (e.g.,
    the keys that actually appeared in the POST payload). This avoids
    accidental rows when ``snapshot()`` later picks up unrelated boot-time
    defaults.
    """
    keys: Iterable[str]
    if only_keys is not None:
        keys = list(only_keys)
    else:
        keys = sorted(set(before) | set(after))
    rows: list[dict[str, Any]] = []
    for k in keys:
        old = before.get(k)
        new = after.get(k)
        row = record_change(k, old, new, actor=actor, reason=reason)
        if row is not None:
            rows.append(row)
    return rows


def _iter_rows() -> Iterator[dict[str, Any]]:
    path = _ledger_path()
    if not path.exists():
        return
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except OSError:
        return


def recent(limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent ``limit`` rows, newest first.

    Reads the whole file (genealogy is small — even at 1 flip/day for
    10 years it's <4MB) and tail-slices. The chain verification is NOT
    run here; use :func:`verify_chain` when you need that guarantee.
    """
    rows = list(_iter_rows())
    return list(reversed(rows[-max(0, limit):]))


def last_change_for(key: str) -> Optional[dict[str, Any]]:
    """Return the last row touching ``key``, or None if untouched."""
    last: Optional[dict[str, Any]] = None
    for row in _iter_rows():
        if row.get("key") == key:
            last = row
    return last


def index_by_key() -> dict[str, dict[str, Any]]:
    """Return ``{key: last_row}`` for every key ever touched. Used by the
    React surface to render a 'last changed' badge per switch in O(1).
    """
    out: dict[str, dict[str, Any]] = {}
    for row in _iter_rows():
        k = row.get("key")
        if isinstance(k, str):
            out[k] = row
    return out


def verify_chain() -> dict[str, Any]:
    """Walk the file forward, recomputing each link. Returns a summary
    with ``{ok, n_rows, first_bad_row, reason}``. ``ok=True`` when the
    ledger is intact (or empty).

    The hash is ``sha256(prev_hash + canonical(row_without_hash))`` —
    same shape as ``record_change`` produces at write time.
    """
    n = 0
    prev_hash = GENESIS_HASH
    for row in _iter_rows():
        n += 1
        stored = row.get("hash")
        stored_prev = row.get("prev_hash")
        if stored_prev != prev_hash:
            return {
                "ok": False,
                "n_rows": n,
                "first_bad_row": n,
                "reason": "prev_hash_mismatch",
            }
        body = {k: v for k, v in row.items() if k != "hash"}
        recomputed = _link(prev_hash, body)
        if recomputed != stored:
            return {
                "ok": False,
                "n_rows": n,
                "first_bad_row": n,
                "reason": "hash_mismatch",
            }
        prev_hash = stored
    return {
        "ok": True,
        "n_rows": n,
        "first_bad_row": None,
        "reason": None,
    }


__all__ = [
    "GENESIS_HASH",
    "record_change",
    "record_diff",
    "recent",
    "last_change_for",
    "index_by_key",
    "verify_chain",
]

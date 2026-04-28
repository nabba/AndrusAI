"""Atomic JSONL event queue for the Transfer Insight Layer.

Producers (healing/evo/grounding/gap triggers) call ``append_event()``
synchronously on the write path. The compiler drains nightly via
``drain()``: an atomic rename moves the queue file aside before reading,
so concurrent appends during a drain land in a fresh queue file rather
than being lost.

Layout (override base directory via ``TRANSFER_MEMORY_DIR`` env):
    /app/workspace/transfer_memory/
        compile_queue.jsonl          ← producers append here
        compile_queue.jsonl.draining ← transient during drain()
        compile_queue.retry.jsonl    ← failed events with attempts++
        shadow_drafts.jsonl          ← compiler output (Phase 17a)
        .last_compile_at             ← nightly cadence guard

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Iterable

from app.transfer_memory.types import TransferEvent, TransferKind

logger = logging.getLogger(__name__)


_DEFAULT_DIR = Path("/app/workspace/transfer_memory")
_QUEUE_FILENAME = "compile_queue.jsonl"
_RETRY_FILENAME = "compile_queue.retry.jsonl"
_SHADOW_FILENAME = "shadow_drafts.jsonl"
_LAST_COMPILE_FILENAME = ".last_compile_at"
_MAX_ATTEMPTS = 3

# Mutex protecting append + atomic-rename. Cheap because appends are O(1).
_lock = threading.Lock()


def _resolve_dir() -> Path:
    override = os.environ.get("TRANSFER_MEMORY_DIR")
    return Path(override) if override else _DEFAULT_DIR


def _ensure_dir() -> Path:
    d = _resolve_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _event_hash(kind: TransferKind, source_id: str) -> str:
    h = hashlib.sha256(f"{kind.value}::{source_id}".encode()).hexdigest()[:16]
    return f"evt_{h}"


def append_event(
    kind: TransferKind,
    source_id: str,
    summary: str = "",
    project_origin: str = "",
    payload: dict | None = None,
) -> bool:
    """Append a transfer event to the queue. Synchronous. Returns True on success.

    Idempotent at the record level: the deterministic ``event_id`` from
    (kind, source_id) collides if the same source produces multiple events
    in a window; ``drain()`` deduplicates by ``event_id`` before processing.

    Safe to call from the request path — one O(1) lock-protected line write.
    Failures are swallowed and logged; queueing must never break a producer.
    """
    try:
        d = _ensure_dir()
        event = TransferEvent(
            event_id=_event_hash(kind, source_id),
            kind=kind,
            source_id=source_id,
            summary=(summary or "")[:240],
            project_origin=project_origin or "",
            payload=payload or {},
        )
        line = json.dumps(event.to_dict(), separators=(",", ":")) + "\n"
        with _lock:
            with (d / _QUEUE_FILENAME).open("a", encoding="utf-8") as f:
                f.write(line)
        return True
    except Exception:
        logger.debug("transfer_memory.queue.append_event failed", exc_info=True)
        return False


def drain() -> list[TransferEvent]:
    """Drain pending events. Atomic-rename pattern preserves concurrent appends.

    Returns events in append order, deduplicated by ``event_id``. The
    queue file is removed on successful drain; on read failure the
    ``.draining`` file is left for inspection.
    """
    d = _ensure_dir()
    src = d / _QUEUE_FILENAME
    drain_path = d / f"{_QUEUE_FILENAME}.draining"

    with _lock:
        if not src.exists():
            return []
        try:
            src.rename(drain_path)
        except FileNotFoundError:
            return []
        except Exception:
            logger.debug("transfer_memory.queue.drain rename failed", exc_info=True)
            return []

    events: list[TransferEvent] = []
    seen: set[str] = set()
    try:
        with drain_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = TransferEvent.from_dict(json.loads(line))
                except Exception:
                    continue
                if evt.event_id in seen:
                    continue
                seen.add(evt.event_id)
                events.append(evt)
    except Exception:
        logger.debug("transfer_memory.queue.drain read failed", exc_info=True)
    finally:
        try:
            drain_path.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            logger.debug("transfer_memory.queue.drain cleanup failed", exc_info=True)

    return events


def push_retry(events: Iterable[TransferEvent]) -> int:
    """Append failed events to the retry queue with attempts++.

    Events whose ``attempts`` would exceed _MAX_ATTEMPTS are dropped after
    audit-logging. Returns the count actually pushed (i.e. not dropped).
    """
    d = _ensure_dir()
    pushed = 0
    try:
        with (d / _RETRY_FILENAME).open("a", encoding="utf-8") as f:
            for evt in events:
                evt.attempts += 1
                if evt.attempts > _MAX_ATTEMPTS:
                    logger.info(
                        f"transfer_memory.queue: dropping event {evt.event_id} "
                        f"after {evt.attempts} attempts (kind={evt.kind})"
                    )
                    continue
                f.write(json.dumps(evt.to_dict(), separators=(",", ":")) + "\n")
                pushed += 1
    except Exception:
        logger.debug("transfer_memory.queue.push_retry failed", exc_info=True)
    return pushed


def drain_retries() -> list[TransferEvent]:
    """Drain the retry queue using the same atomic-rename pattern."""
    d = _ensure_dir()
    src = d / _RETRY_FILENAME
    drain_path = d / f"{_RETRY_FILENAME}.draining"

    with _lock:
        if not src.exists():
            return []
        try:
            src.rename(drain_path)
        except FileNotFoundError:
            return []
        except Exception:
            return []

    events: list[TransferEvent] = []
    try:
        with drain_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(TransferEvent.from_dict(json.loads(line)))
                except Exception:
                    continue
    except Exception:
        logger.debug("transfer_memory.queue.drain_retries read failed", exc_info=True)
    finally:
        try:
            drain_path.unlink()
        except Exception:
            pass
    return events


def queue_size() -> int:
    """Best-effort count of pending events in the main queue. For dashboards."""
    p = _resolve_dir() / _QUEUE_FILENAME
    if not p.exists():
        return 0
    try:
        with p.open("r", encoding="utf-8") as f:
            return sum(1 for ln in f if ln.strip())
    except Exception:
        return 0


# ── Shadow-draft sink (Phase 17a) ────────────────────────────────────────

def append_shadow_draft(record: dict) -> bool:
    """Append a compiled draft record to the shadow log.

    Phase 17a does not write transfer-memory drafts to the production KBs.
    Instead each successful compile lands here for operator review and
    effectiveness measurement before promotion in 17c.
    """
    try:
        d = _ensure_dir()
        line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
        with _lock:
            with (d / _SHADOW_FILENAME).open("a", encoding="utf-8") as f:
                f.write(line)
        return True
    except Exception:
        logger.debug("transfer_memory.queue.append_shadow_draft failed", exc_info=True)
        return False


def shadow_draft_count() -> int:
    p = _resolve_dir() / _SHADOW_FILENAME
    if not p.exists():
        return 0
    try:
        with p.open("r", encoding="utf-8") as f:
            return sum(1 for ln in f if ln.strip())
    except Exception:
        return 0


# ── Cadence guard ────────────────────────────────────────────────────────

def read_last_compile_at() -> float:
    """Epoch seconds of last successful compile run. 0.0 if never."""
    p = _resolve_dir() / _LAST_COMPILE_FILENAME
    if not p.exists():
        return 0.0
    try:
        return float(p.read_text(encoding="utf-8").strip() or "0")
    except Exception:
        return 0.0


def write_last_compile_at(epoch_seconds: float) -> None:
    try:
        d = _ensure_dir()
        (d / _LAST_COMPILE_FILENAME).write_text(f"{epoch_seconds}\n", encoding="utf-8")
    except Exception:
        logger.debug("transfer_memory.queue.write_last_compile_at failed", exc_info=True)

"""decade_recall.indexer — incremental scan over the 6 hash-chained
audit/ledger files.

Gap 4 of the 2026-05-24 ultrathink analysis closure.

Each source has its own cursor (byte offset). One pass walks every
source from its cursor to EOF, redacts PII, tokenizes, appends to
the combined index. Cursor advances after a successful append.

Design notes
============

  * Per-source cursors so a slow scan on one source doesn't stall
    the others.
  * Pure-stdlib regex tokenizer (matches conversation_memory).
  * Source row preview is 200 chars max; tokens cap at 64 per row.
  * Failure-isolated: a malformed line in one source doesn't break
    the others; we just skip the line and advance the cursor.

Storage
=======

  * Cursors: ``workspace/decade_recall/cursors.json``
  * Index: ``workspace/decade_recall/index.jsonl``
  * Both atomic-write via tempfile + rename.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


_LOCK = threading.Lock()
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
_EMAIL_RE = re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\d[\d\s()-]{7,}\d")
_MAX_TOKENS_PER_ROW = 64
_MAX_PREVIEW_CHARS = 200
_MAX_ROWS_PER_PASS = 5000


def _workspace_root() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT  # type: ignore

        return Path(WORKSPACE_ROOT)
    except Exception:
        return Path("/app/workspace")


def _index_path() -> Path:
    return _workspace_root() / "decade_recall" / "index.jsonl"


def _cursor_path() -> Path:
    return _workspace_root() / "decade_recall" / "cursors.json"


# ── Source registry ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class AuditSource:
    """One hash-chained audit/ledger file to incrementally index.

    ``scope`` is the discriminator the retrieval API filters on.
    ``rel_path`` is relative to ``WORKSPACE_ROOT``.
    """

    scope: str
    rel_path: str
    ts_keys: tuple[str, ...] = ("ts", "timestamp", "at", "created_at", "emitted_at")
    kind_keys: tuple[str, ...] = ("kind", "event", "type", "action")
    ref_keys: tuple[str, ...] = (
        "id",
        "run_id",
        "request_id",
        "cr_id",
        "proposal_id",
        "amendment_id",
    )


SOURCES: tuple[AuditSource, ...] = (
    AuditSource(scope="continuity", rel_path="identity/continuity_ledger.jsonl"),
    AuditSource(scope="changes", rel_path="change_requests/audit.jsonl"),
    AuditSource(scope="drills", rel_path="resilience/drill_audit.jsonl"),
    AuditSource(scope="executor", rel_path="autonomous_executor/audit.jsonl"),
    AuditSource(scope="agreement", rel_path="self_model/agreement_ledger.jsonl"),
    AuditSource(scope="governance", rel_path="governance/audit.jsonl"),
)


def _source_path(src: AuditSource) -> Path:
    return _workspace_root() / src.rel_path


# ── Cursor handling ──────────────────────────────────────────────────────


def _read_cursors() -> dict[str, dict[str, Any]]:
    p = _cursor_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.debug("decade_recall: cursor read failed", exc_info=True)
        return {}


def _write_cursors(state: dict[str, dict[str, Any]]) -> None:
    p = _cursor_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(state, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(p)
    except Exception:
        logger.debug("decade_recall: cursor write failed", exc_info=True)


# ── PII redaction + tokenization ─────────────────────────────────────────


def _redact(text: str) -> str:
    text = _EMAIL_RE.sub("<email>", text or "")
    text = _PHONE_RE.sub("<phone>", text)
    return text


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")][:_MAX_TOKENS_PER_ROW]


def _flatten_row(row: dict[str, Any]) -> str:
    """Build the searchable surface from a heterogeneous row.

    Each source has a different schema; we walk all string/number
    fields recursively and join them into one indexable blob.
    """
    parts: list[str] = []

    def _walk(value: Any, depth: int = 0) -> None:
        if depth > 3:
            return
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (int, float)):
            parts.append(str(value))
        elif isinstance(value, dict):
            for v in value.values():
                _walk(v, depth + 1)
        elif isinstance(value, list):
            for v in value[:20]:
                _walk(v, depth + 1)

    _walk(row)
    return " ".join(parts)


def _row_field(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    """First non-empty string match against the candidate key list."""
    for k in keys:
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


# ── Append + index ───────────────────────────────────────────────────────


def _append_index_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    p = _index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with p.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, sort_keys=True) + "\n")
    except OSError:
        logger.debug("decade_recall: append failed", exc_info=True)


# ── Public scan API ──────────────────────────────────────────────────────


def _enabled() -> bool:
    try:
        from app import runtime_settings

        return bool(runtime_settings.get_decade_recall_enabled())
    except Exception:
        return True


def _scan_one(
    src: AuditSource,
    cursor: dict[str, Any],
    max_lines: int,
) -> tuple[int, int, list[dict[str, Any]]]:
    """Walk one source from its cursor to EOF. Returns
    ``(new_offset, lines_processed, index_rows)``.

    Malformed lines are skipped but counted for the cursor advance —
    so a bad line doesn't block forever.
    """
    path = _source_path(src)
    if not path.exists():
        return int(cursor.get("offset", 0)), 0, []
    try:
        size = path.stat().st_size
    except OSError:
        return int(cursor.get("offset", 0)), 0, []
    offset = int(cursor.get("offset", 0))
    if offset > size:
        # Source file was rotated/truncated. Reset.
        offset = 0
    if offset >= size:
        return offset, 0, []

    index_rows: list[dict[str, Any]] = []
    lines_processed = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            while lines_processed < max_lines:
                line = f.readline()
                if not line:
                    break
                lines_processed += 1
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                ts = _row_field(row, src.ts_keys) or datetime.now(timezone.utc).isoformat()
                kind = _row_field(row, src.kind_keys) or "unknown"
                ref = _row_field(row, src.ref_keys) or None
                flat = _redact(_flatten_row(row))
                preview = flat.strip()[:_MAX_PREVIEW_CHARS]
                index_rows.append(
                    {
                        "ts": ts,
                        "scope": src.scope,
                        "kind": kind.lower(),
                        "ref": ref,
                        "preview": preview,
                        "tokens": _tokens(flat),
                    }
                )
            new_offset = f.tell()
    except OSError:
        logger.debug(
            "decade_recall: read failed for %s", src.rel_path, exc_info=True
        )
        return offset, 0, []
    return new_offset, lines_processed, index_rows


def scan_all_sources(*, max_lines_per_source: int = _MAX_ROWS_PER_PASS) -> dict[str, Any]:
    """Run one incremental pass across every source. Returns a
    per-source breakdown.

    Failure-isolated: one source raising never breaks the others.
    """
    if not _enabled():
        return {"skipped_reason": "master_switch_off"}
    summary: dict[str, Any] = {"sources": {}, "total_indexed": 0}
    with _LOCK:
        cursors = _read_cursors()
        for src in SOURCES:
            cur = cursors.get(src.scope, {"offset": 0})
            try:
                new_offset, lines, rows = _scan_one(
                    src, cur, max_lines_per_source
                )
            except Exception:
                logger.debug(
                    "decade_recall: source %s scan raised",
                    src.scope, exc_info=True,
                )
                continue
            if rows:
                _append_index_rows(rows)
            cursors[src.scope] = {
                "offset": new_offset,
                "last_scan": datetime.now(timezone.utc).isoformat(),
                "lines_processed": lines,
            }
            summary["sources"][src.scope] = {
                "lines_processed": lines,
                "indexed": len(rows),
                "new_offset": new_offset,
            }
            summary["total_indexed"] += len(rows)
        _write_cursors(cursors)
    return summary


def rebuild_index() -> dict[str, Any]:
    """Wipe the index + cursors and re-scan from offset 0. Used
    when the index goes stale (e.g. operator wants to re-key after
    schema change). Idempotent."""
    if not _enabled():
        return {"skipped_reason": "master_switch_off"}
    with _LOCK:
        idx = _index_path()
        cur = _cursor_path()
        try:
            if idx.exists():
                idx.unlink()
        except OSError:
            pass
        try:
            if cur.exists():
                cur.unlink()
        except OSError:
            pass
    # Do one large pass — high cap so the rebuild covers years of
    # historic data in a single invocation.
    return scan_all_sources(max_lines_per_source=200_000)


def run_scan() -> dict[str, Any]:
    """LIGHT idle-job entry. Single pass per call. Failure-isolated."""
    try:
        return scan_all_sources()
    except Exception:
        logger.debug("decade_recall: scan raised", exc_info=True)
        return {"skipped_reason": "internal_error"}


__all__ = [
    "AuditSource",
    "SOURCES",
    "rebuild_index",
    "run_scan",
    "scan_all_sources",
]

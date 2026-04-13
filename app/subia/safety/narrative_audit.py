"""
subia.safety.narrative_audit — append-only self-narrative audit log.

SubIA Part I §0.4 Safety Invariant #3:
    "The system cannot suppress, defer, or modify its own
     self-narrative audit results. Audit findings are logged
     immutably, parallel to the DGM pattern where evaluation
     functions are outside agent-modifiable code."

This module writes audit entries as JSONL lines to a persistent
file under the workspace. Writes are fsync'd (via safe_io.safe_append)
so entries survive crashes. There is NO public delete/modify API.
A caller that wants to rewrite history would have to either
(a) modify the file directly from outside this module — in which
case the integrity manifest catches the mutation — or
(b) monkey-patch this module, which the Tier-3 guard catches.

Public API:
  append_audit(finding, loop_count, sources=()) -> AuditEntry
  read_audit_entries(limit=100) -> list[AuditEntry]
  audit_stream_summary() -> dict   # for dashboards

No delete. No update. By design.

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 3 / 4.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from app.paths import WORKSPACE_ROOT
from app.safe_io import safe_append

logger = logging.getLogger(__name__)


# Canonical path for the audit log. Append-only JSONL. Covered by
# the integrity machinery at its directory level rather than per-
# entry (since entries accumulate continuously).
_AUDIT_DIR = WORKSPACE_ROOT / "subia" / "self"
_AUDIT_LOG = _AUDIT_DIR / "self-narrative-audit.jsonl"


@dataclass
class AuditEntry:
    """One entry in the append-only narrative audit stream."""
    at: str = ""
    loop_count: int = 0
    finding: str = ""
    sources: list = field(default_factory=list)
    severity: str = "info"          # info | warn | drift

    @classmethod
    def from_dict(cls, data: dict) -> "AuditEntry":
        return cls(
            at=str(data.get("at", "")),
            loop_count=int(data.get("loop_count", 0)),
            finding=str(data.get("finding", "")),
            sources=list(data.get("sources", [])),
            severity=str(data.get("severity", "info")),
        )

    def to_dict(self) -> dict:
        return {
            "at": self.at,
            "loop_count": self.loop_count,
            "finding": self.finding,
            "sources": list(self.sources),
            "severity": self.severity,
        }


def append_audit(
    finding: str,
    loop_count: int,
    sources: Iterable[str] = (),
    severity: str = "info",
    *,
    path: Path | None = None,
) -> AuditEntry:
    """Append a single audit entry to the append-only log.

    Returns the constructed AuditEntry. Never raises on I/O error:
    audit logging failure is logged and the caller proceeds. This is
    intentional — a broken log must not crash the consciousness loop,
    because that would be an easy DoS vector.

    The entry timestamp is set from UTC-now unless the caller provided
    it explicitly via a pre-built AuditEntry (not in this signature,
    but available via the module's lower-level writer below).
    """
    target = Path(path) if path else _AUDIT_LOG
    entry = AuditEntry(
        at=datetime.now(timezone.utc).isoformat(),
        loop_count=int(loop_count),
        finding=str(finding)[:2000],   # cap to prevent log bloat
        sources=[str(s)[:200] for s in sources][:16],
        severity=_validate_severity(severity),
    )
    try:
        safe_append(target, json.dumps(entry.to_dict(), default=str))
    except OSError:
        logger.exception("narrative_audit: safe_append failed; entry dropped")
    return entry


def read_audit_entries(
    limit: int = 100,
    *,
    path: Path | None = None,
) -> list[AuditEntry]:
    """Read the most recent `limit` entries from the audit log.

    Entries that fail to parse are skipped (defensive: a corrupted
    single line must not prevent reading the rest).
    """
    target = Path(path) if path else _AUDIT_LOG
    if not target.exists():
        return []
    entries: list[AuditEntry] = []
    try:
        with open(target, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        logger.exception("narrative_audit: failed to read %s", target)
        return []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(AuditEntry.from_dict(json.loads(line)))
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.debug("narrative_audit: dropping malformed line")
            continue
    return entries


def audit_stream_summary(
    *,
    path: Path | None = None,
) -> dict:
    """Structured summary of the audit stream for dashboards."""
    target = Path(path) if path else _AUDIT_LOG
    if not target.exists():
        return {"total": 0, "by_severity": {}, "last_at": None}
    entries = read_audit_entries(limit=10_000, path=target)
    by_sev: dict[str, int] = {}
    for e in entries:
        by_sev[e.severity] = by_sev.get(e.severity, 0) + 1
    return {
        "total":        len(entries),
        "by_severity":  by_sev,
        "last_at":      entries[-1].at if entries else None,
        "last_finding": entries[-1].finding[:120] if entries else None,
    }


# ── Internals ─────────────────────────────────────────────────────

_SEVERITY_VALID = frozenset({"info", "warn", "drift"})


def _validate_severity(sev: str) -> str:
    return sev if sev in _SEVERITY_VALID else "info"

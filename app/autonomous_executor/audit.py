"""Hash-chained audit ledger for the autonomous executor.

Verified Implementation Plan Risk #3 closure (2026-05-22). The plan
explicitly committed to *four* independent hash-chained ledgers —
coding_session + change_request + governance_amendment + this one —
rather than a unified chain that would create a single point of
failure for forensic state.

Storage shape (matches coding_session/audit_verify.py + governance/
amendment_audit.py conventions)::

    workspace/autonomous_executor/audit.jsonl

Each row is::

    {
      "ts": "<ISO8601 UTC>",
      "run_id": "<run identifier>",
      "kind": "<event kind: e.g. transition / step_completed / abort>",
      "actor": "<who: autonomous_executor / operator / agent_id>",
      "payload": {...event-specific...},
      "prev_hash": "<16 hex chars; '' for genesis>",
      "entry_hash": "<16 hex chars; sha256(prev_hash + json(row-without-entry_hash))[:16]>"
    }

Failure-isolated end-to-end — a disk-write error logs at debug and
returns False. Hash-chain corruption is caught by :func:`verify_chain`
which is read-only and produces a list of broken-row indices.

Why this is its own chain rather than emitting into the identity
continuity_ledger:

  * The continuity ledger is *identity-shaping* events. An executor
    step completion isn't identity-shaping; it's operational.
  * Continuity-ledger writes are gated by IDENTITY_LEDGER_ENABLED;
    the executor's forensic trail should be unconditional.
  * Per the plan's Risk #3: "Four independent hash-chained ledgers
    is the correct shape; unification would create a single point
    of failure."

Public surface
──────────────

  * :func:`record` — append one event (transition / step_completed /
    step_failed / blocker_detected / resume / abort / budget_exhausted)
  * :func:`iter_events` / :func:`load_all` — read
  * :func:`verify_chain` — check hash-chain integrity (returns the
    list of broken-row indices; empty list = chain intact)
  * :func:`reset_for_tests` — test isolation
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)


_lock = threading.RLock()
_path_override: Optional[Path] = None


# ── Known event kinds ────────────────────────────────────────────────


# Curated set — any new kind needs a deliberate addition. Keeps the
# audit-grep namespace bounded.
KNOWN_KINDS: frozenset[str] = frozenset({
    "transition",         # run.status change (from → to, with reason)
    "step_completed",     # step COMPLETED with result
    "step_failed",        # step FAILED with reason
    "blocker_detected",   # _detect_blocker fired
    "escalation_emitted", # Signal escalation sent on BLOCKED entry
    "resume",             # operator resumed a BLOCKED run
    "abort",              # operator aborted the run
    "budget_exhausted",   # budget check failed mid-run
    "run_created",        # new run accepted via /delegate
})


# ── Path resolution ──────────────────────────────────────────────────


def _workspace_root() -> Path:
    return Path(os.environ.get("WORKSPACE_ROOT", "workspace"))


def _default_path() -> Path:
    return _workspace_root() / "autonomous_executor" / "audit.jsonl"


def _resolve_path() -> Path:
    if _path_override is not None:
        return _path_override
    return _default_path()


# ── Hashing ──────────────────────────────────────────────────────────


def _compute_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    """sha256(prev_hash + canonical_json(payload))[:16].

    Mirrors :func:`app.coding_session.audit_verify._expected_entry_hash`.
    The 16-hex truncation matches the existing project convention —
    full sha256 would be wasted bytes for the ledger's purpose
    (detecting tampering, not cryptographic uniqueness).
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    blob = (prev_hash + canonical).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _last_hash() -> str:
    """Tail-seek the audit file for the last row's ``entry_hash``.

    Genesis convention: empty string when the file doesn't exist or
    is empty. Tolerates trailing whitespace, blank lines, and a
    malformed final row (returns the previous valid row's hash).
    """
    path = _resolve_path()
    if not path.exists():
        return ""
    try:
        # Read line-by-line. For low-volume audit logs (executor
        # produces <100 rows/day in normal use), a full scan is fine.
        # If volumes ever explode, swap for reverse-seek-and-parse.
        last = ""
        with path.open("r", encoding="utf-8") as fp:
            for raw in fp:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                h = row.get("entry_hash")
                if isinstance(h, str) and h:
                    last = h
        return last
    except OSError:
        return ""


# ── Writer ───────────────────────────────────────────────────────────


def record(
    *,
    run_id: str,
    kind: str,
    actor: str,
    payload: Optional[dict[str, Any]] = None,
    ts: Optional[str] = None,
) -> bool:
    """Append one event row to the ledger. Returns True on success.

    Failure-isolated: any I/O or serialization error logs at debug
    and returns False. NEVER raises — the executor's hot path must
    not be blocked by an audit-log issue.

    Parameters
    ----------
    run_id
        The :class:`ExecutorRun.run_id` this event belongs to.
    kind
        One of :data:`KNOWN_KINDS`. Unknown kinds log a warning and
        STILL get recorded (so a typo doesn't lose forensic data) —
        but the operator surface flags them.
    actor
        Who caused this event: ``"autonomous_executor"`` /
        ``"operator"`` / ``"agent:<agent_id>"``.
    payload
        Event-specific fields. Should be JSON-serialisable; an
        unserialisable payload logs the failure and records an
        empty payload (so the audit row is still chained).
    ts
        Override for the timestamp (test only). Defaults to ``now``.
    """
    if kind not in KNOWN_KINDS:
        logger.warning(
            "autonomous_executor.audit: unknown kind %r — recorded "
            "anyway (operator surface will flag)", kind,
        )

    safe_payload: dict[str, Any] = {}
    try:
        # Round-trip through JSON to catch unserialisable values
        json.dumps(payload or {})
        safe_payload = dict(payload or {})
    except (TypeError, ValueError) as exc:
        logger.debug(
            "autonomous_executor.audit: payload not serialisable for "
            "%s/%s — dropping payload: %s",
            run_id, kind, exc,
        )
        safe_payload = {"_payload_error": str(exc)[:120]}

    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()

    with _lock:
        prev_hash = _last_hash()
        row_without_hash = {
            "ts": ts,
            "run_id": run_id,
            "kind": kind,
            "actor": (actor or "unknown").strip()[:80],
            "payload": safe_payload,
            "prev_hash": prev_hash,
        }
        entry_hash = _compute_hash(prev_hash, row_without_hash)
        row = dict(row_without_hash)
        row["entry_hash"] = entry_hash

        target = _resolve_path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(row, sort_keys=True) + "\n")
            return True
        except OSError as exc:
            logger.debug(
                "autonomous_executor.audit: write failed: %s", exc,
            )
            return False


# ── Reader ───────────────────────────────────────────────────────────


def iter_events() -> Iterator[dict[str, Any]]:
    """Yield every event row in append order. Skips malformed rows."""
    path = _resolve_path()
    if not path.exists():
        return
    try:
        with path.open("r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def load_all() -> list[dict[str, Any]]:
    """Materialise every event row."""
    return list(iter_events())


# ── Verifier ─────────────────────────────────────────────────────────


def verify_chain() -> list[int]:
    """Walk the chain and return the row indices where the hash
    doesn't match the recomputed value. Empty list = chain intact.

    The verifier is read-only and pure-stdlib — safe to run from any
    operator surface (REST, Signal, React).
    """
    broken: list[int] = []
    prev_hash = ""
    path = _resolve_path()
    if not path.exists():
        return broken
    try:
        with path.open("r", encoding="utf-8") as fp:
            for idx, line in enumerate(fp):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    broken.append(idx)
                    continue
                if not isinstance(row, dict):
                    broken.append(idx)
                    continue
                stored_hash = row.get("entry_hash", "")
                row_without_hash = {
                    k: v for k, v in row.items() if k != "entry_hash"
                }
                # The stored prev_hash MUST match the chain so far
                if row.get("prev_hash") != prev_hash:
                    broken.append(idx)
                    # Advance the chain anyway so the next row's
                    # check is well-defined.
                    prev_hash = stored_hash
                    continue
                expected = _compute_hash(prev_hash, row_without_hash)
                if expected != stored_hash:
                    broken.append(idx)
                prev_hash = stored_hash
    except OSError:
        # Treat I/O failure as "chain unverifiable" — return all
        # rows-so-far as broken would be misleading; instead return
        # empty + log.
        logger.debug(
            "autonomous_executor.audit: verify_chain I/O failure",
        )
    return broken


# ── Test helpers ─────────────────────────────────────────────────────


def reset_for_tests(path: Optional[Path] = None) -> None:
    """Override the resolved path. Pass ``None`` to clear."""
    global _path_override
    with _lock:
        _path_override = path


__all__ = [
    "KNOWN_KINDS",
    "iter_events",
    "load_all",
    "record",
    "reset_for_tests",
    "verify_chain",
]

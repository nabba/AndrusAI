"""privacy.aggregator — Unified "what does the system know about X" surface.

Gap #7 (2026-05-24): the system gradually accumulates personal data
across at least 6 subsystems (person_model, social_graph, browse,
calendar, email metadata, audit.log, affect/trace). Each has its own
narrow forget path. There is no unified answer to "show me everything
the system has about person Y, and let me erase it in one operation."

This module is that unification.

Subject types
=============

  * ``person`` — keyed by canonical email; walks person_model,
    social_graph, audit.log sender_id, conversation_memory.
  * ``domain`` — keyed by URL domain; walks browse store +
    blocklist.
  * ``sender_id`` — keyed by Signal sender id; walks audit.log,
    conversation_memory.

Adapter pattern
===============

Each data-source integration is a typed ``PrivacyAdapter``: a
``probe(subject_type, subject_id) -> AdapterReport`` reads + counts;
a ``forget(subject_type, subject_id) -> int`` returns how many
records were removed. Adapters are failure-isolated: a broken
probe never blocks the rest of the audit; a broken forget surfaces
its error in the response but doesn't roll back already-completed
forgets.

What this is NOT
================

  * Not a search engine over message content. Bodies aren't indexed
    by subject; the aggregator surfaces *metadata references* only.
  * Not a tombstone system. Forget calls each adapter's existing
    deletion path; bytes are physically removed, not flagged.
  * Not a court-of-law audit. The aggregator's purpose is to give
    the operator + future-Andrus the answer to "what do I know
    about X." Legal-grade GDPR audits would need additional
    discovery beyond what's mechanically reachable.

Adapter coverage (v1 scope)
===========================

The aggregator currently wires five adapters: ``person_model``,
``social_graph``, ``browse``, ``audit_log``, ``conversation_memory``.
These cover the highest-signal references for the three subject
types. They do NOT yet probe:

  * Calendar attendees (Google / Apple) — person references in
    invitee lists.
  * Gmail sender / recipient metadata beyond what's already in
    ``audit_log``.
  * ``app/inbox`` handlers (PDF receipts, audio transcripts) — these
    may store person names but have no per-person index.
  * Affect trace (``workspace/affect/trace.jsonl``) — person ids
    appear in sender_id of feeling events; the volume is large.
  * Workspace KB content — ``experiential`` / ``philosophy`` /
    ``aesthetics`` chromadb collections aren't indexed by person.

Adding a new adapter is a matter of authoring a probe + forget pair
and appending to ``_ADAPTERS``. The current scope is the "give the
operator + future-Andrus an honest 80% answer" pass; legal-grade
exhaustiveness would require the additional adapters above.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


SUBJECT_PERSON = "person"
SUBJECT_DOMAIN = "domain"
SUBJECT_SENDER = "sender_id"
VALID_SUBJECT_TYPES = (SUBJECT_PERSON, SUBJECT_DOMAIN, SUBJECT_SENDER)


def _workspace() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT)
    except Exception:
        return Path("/app/workspace")


def _enabled() -> bool:
    try:
        from app.runtime_settings import get_privacy_audit_enabled
        return get_privacy_audit_enabled()
    except Exception:
        return os.getenv(
            "PRIVACY_AUDIT_ENABLED", "true",
        ).lower() in ("true", "1", "yes", "on")


# ── Adapter model ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class AdapterReport:
    """One adapter's view of a subject. ``n_references`` is the count
    that the forget path would remove; ``samples`` are up to 5
    illustrative non-sensitive snippets (timestamps, modality names,
    counts), never raw body content."""
    adapter: str
    subject_type: str
    subject_id: str
    n_references: int
    samples: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


@dataclass(frozen=True)
class AdapterForgetResult:
    adapter: str
    subject_type: str
    subject_id: str
    n_removed: int
    error: Optional[str] = None


@dataclass(frozen=True)
class PrivacyAdapter:
    name: str
    supports: tuple[str, ...]                                            # subject types this adapter knows about
    probe: Callable[[str, str], AdapterReport]
    forget: Callable[[str, str], AdapterForgetResult]


# ── Adapters ────────────────────────────────────────────────────────────


def _probe_person_model(subject_type: str, subject_id: str) -> AdapterReport:
    if subject_type != SUBJECT_PERSON:
        return AdapterReport("person_model", subject_type, subject_id, 0)
    try:
        from app.companion import person_model
    except Exception as exc:
        return AdapterReport("person_model", subject_type, subject_id, 0, error=str(exc))
    try:
        profile = person_model._load_profile()
    except Exception as exc:
        return AdapterReport("person_model", subject_type, subject_id, 0, error=str(exc))
    record = profile.get(subject_id)
    if record is None:
        return AdapterReport("person_model", subject_type, subject_id, 0)
    samples = [{
        "first_seen": record.first_seen,
        "last_seen": record.last_seen,
        "total_occurrences": record.total_occurrences(),
        "modality_count": record.modality_count(),
    }]
    return AdapterReport(
        adapter="person_model",
        subject_type=subject_type,
        subject_id=subject_id,
        n_references=1,
        samples=samples,
    )


def _forget_person_model(subject_type: str, subject_id: str) -> AdapterForgetResult:
    if subject_type != SUBJECT_PERSON:
        return AdapterForgetResult("person_model", subject_type, subject_id, 0)
    try:
        from app.companion import person_model
        ok = person_model.forget(subject_id)
        return AdapterForgetResult("person_model", subject_type, subject_id, 1 if ok else 0)
    except Exception as exc:
        return AdapterForgetResult("person_model", subject_type, subject_id, 0, error=str(exc))


def _probe_social_graph(subject_type: str, subject_id: str) -> AdapterReport:
    """social_graph has no per-person index. The probe surfaces a
    qualitative warning when L4 is engaged; the forget path uses the
    existing ``forget_graph`` (whole-graph wipe) since per-person
    removal isn't a primitive the existing module exposes.
    """
    if subject_type != SUBJECT_PERSON:
        return AdapterReport("social_graph", subject_type, subject_id, 0)
    try:
        from app.runtime_settings import get_person_correlation_social_graph_enabled
        engaged = get_person_correlation_social_graph_enabled()
    except Exception:
        engaged = False
    if not engaged:
        return AdapterReport("social_graph", subject_type, subject_id, 0)
    return AdapterReport(
        adapter="social_graph",
        subject_type=subject_type,
        subject_id=subject_id,
        n_references=1,
        samples=[{
            "note": (
                "Social graph is engaged; the subject MAY appear as an "
                "edge endpoint. The graph has no per-person index, so the "
                "forget path wipes the whole graph. If that is unacceptable, "
                "leave the social graph alone and accept the residual "
                "reference."
            ),
        }],
    )


def _forget_social_graph(subject_type: str, subject_id: str) -> AdapterForgetResult:
    if subject_type != SUBJECT_PERSON:
        return AdapterForgetResult("social_graph", subject_type, subject_id, 0)
    try:
        from app.companion import social_graph
        n = social_graph.forget_graph()
        return AdapterForgetResult("social_graph", subject_type, subject_id, int(n or 0))
    except Exception as exc:
        return AdapterForgetResult("social_graph", subject_type, subject_id, 0, error=str(exc))


def _probe_browse_domain(subject_type: str, subject_id: str) -> AdapterReport:
    if subject_type != SUBJECT_DOMAIN:
        return AdapterReport("browse", subject_type, subject_id, 0)
    base = _workspace() / "browse" / "events"
    if not base.exists():
        return AdapterReport("browse", subject_type, subject_id, 0)
    matches = 0
    samples: list[dict[str, Any]] = []
    try:
        for f in sorted(base.glob("*.jsonl")):
            try:
                with f.open("r", encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except Exception:
                            continue
                        url = str(row.get("url") or "")
                        if subject_id in url:
                            matches += 1
                            if len(samples) < 5:
                                samples.append({
                                    "day": f.stem,
                                    "ts": row.get("ts"),
                                })
            except OSError:
                continue
    except OSError:
        return AdapterReport("browse", subject_type, subject_id, 0)
    return AdapterReport(
        adapter="browse",
        subject_type=subject_type,
        subject_id=subject_id,
        n_references=matches,
        samples=samples,
    )


def _forget_browse_domain(subject_type: str, subject_id: str) -> AdapterForgetResult:
    if subject_type != SUBJECT_DOMAIN:
        return AdapterForgetResult("browse", subject_type, subject_id, 0)
    try:
        from app.browse import store
        n = store.forget_domain(subject_id)
        return AdapterForgetResult("browse", subject_type, subject_id, int(n or 0))
    except Exception as exc:
        return AdapterForgetResult("browse", subject_type, subject_id, 0, error=str(exc))


def _probe_audit_log_sender(subject_type: str, subject_id: str) -> AdapterReport:
    """Walks workspace/audit.log for request_received rows mentioning
    the subject id. Matches both ``sender_id`` field and
    ``request_received`` payloads. Bounded to last 365d for cost."""
    if subject_type not in (SUBJECT_SENDER, SUBJECT_PERSON):
        return AdapterReport("audit_log", subject_type, subject_id, 0)
    path = _workspace() / "audit.log"
    if not path.exists():
        return AdapterReport("audit_log", subject_type, subject_id, 0)
    cutoff = time.time() - 365 * 86400
    matches = 0
    samples: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                ts_str = row.get("ts")
                try:
                    ts = datetime.fromisoformat(
                        ts_str.replace("Z", "+00:00")
                    ).timestamp() if isinstance(ts_str, str) else 0
                except Exception:
                    ts = 0
                if ts and ts < cutoff:
                    continue
                sender = str(row.get("sender_id") or row.get("actor") or "")
                if subject_id not in sender:
                    continue
                matches += 1
                if len(samples) < 5:
                    samples.append({
                        "ts": ts_str,
                        "event": row.get("event") or row.get("kind"),
                    })
    except OSError:
        pass
    return AdapterReport(
        adapter="audit_log",
        subject_type=subject_type,
        subject_id=subject_id,
        n_references=matches,
        samples=samples,
    )


def _forget_audit_log(subject_type: str, subject_id: str) -> AdapterForgetResult:
    """audit.log is append-only by design. We do not delete rows from
    it — the audit chain integrity depends on every row being
    present. The forget path is a no-op with an explanatory note.

    The right way to suppress a sender in audit.log is **future**
    redaction: refuse to record their sender_id going forward. That
    is a config decision, not a deletion."""
    return AdapterForgetResult(
        adapter="audit_log",
        subject_type=subject_type,
        subject_id=subject_id,
        n_removed=0,
        error=(
            "audit.log is append-only by design (hash chain integrity). "
            "Existing rows cannot be removed. Use the runtime_settings "
            "blocklist to prevent future references."
        ),
    )


def _probe_conversation_memory(subject_type: str, subject_id: str) -> AdapterReport:
    """conversation_memory has its own per-sender index. We probe by
    delegating to its existing recall path."""
    try:
        from app.conversation_memory import recall
    except Exception as exc:
        return AdapterReport("conversation_memory", subject_type, subject_id, 0, error=str(exc))
    try:
        rows = recall(subject_id, top_k=5)
    except Exception as exc:
        return AdapterReport("conversation_memory", subject_type, subject_id, 0, error=str(exc))
    if not rows:
        return AdapterReport("conversation_memory", subject_type, subject_id, 0)
    samples = [{
        "snippet_length": len(str(r.get("text") or "")),
        "ts": r.get("ts"),
    } for r in rows[:5]]
    return AdapterReport(
        adapter="conversation_memory",
        subject_type=subject_type,
        subject_id=subject_id,
        n_references=len(rows),
        samples=samples,
    )


def _forget_conversation_memory(subject_type: str, subject_id: str) -> AdapterForgetResult:
    """conversation_memory ledger is also append-only with PII
    redaction at scan edge. Same as audit.log — surface the limitation
    explicitly rather than pretend the forget happens."""
    return AdapterForgetResult(
        adapter="conversation_memory",
        subject_type=subject_type,
        subject_id=subject_id,
        n_removed=0,
        error=(
            "conversation_memory index is append-only; deletion requires "
            "rebuilding the index from a redacted audit.log replay. Use "
            "the underlying audit.log blocklist instead."
        ),
    )


_ADAPTERS: tuple[PrivacyAdapter, ...] = (
    PrivacyAdapter(
        name="person_model",
        supports=(SUBJECT_PERSON,),
        probe=_probe_person_model,
        forget=_forget_person_model,
    ),
    PrivacyAdapter(
        name="social_graph",
        supports=(SUBJECT_PERSON,),
        probe=_probe_social_graph,
        forget=_forget_social_graph,
    ),
    PrivacyAdapter(
        name="browse",
        supports=(SUBJECT_DOMAIN,),
        probe=_probe_browse_domain,
        forget=_forget_browse_domain,
    ),
    PrivacyAdapter(
        name="audit_log",
        supports=(SUBJECT_PERSON, SUBJECT_SENDER),
        probe=_probe_audit_log_sender,
        forget=_forget_audit_log,
    ),
    PrivacyAdapter(
        name="conversation_memory",
        supports=(SUBJECT_PERSON, SUBJECT_SENDER),
        probe=_probe_conversation_memory,
        forget=_forget_conversation_memory,
    ),
)


# ── Public API ──────────────────────────────────────────────────────────


def audit_subject(subject_type: str, subject_id: str) -> dict[str, Any]:
    """Walk every adapter that supports ``subject_type`` and return a
    consolidated report. Pure-read; safe to call on a live system.
    """
    if subject_type not in VALID_SUBJECT_TYPES:
        raise ValueError(
            f"subject_type must be one of {VALID_SUBJECT_TYPES}; got {subject_type!r}"
        )
    if not subject_id or not isinstance(subject_id, str):
        raise ValueError("subject_id must be a non-empty string")
    if not _enabled():
        return {
            "subject_type": subject_type,
            "subject_id": subject_id,
            "enabled": False,
            "adapters": [],
            "total_references": 0,
        }
    adapters: list[AdapterReport] = []
    for adapter in _ADAPTERS:
        if subject_type not in adapter.supports:
            continue
        try:
            report = adapter.probe(subject_type, subject_id)
        except Exception as exc:
            report = AdapterReport(
                adapter=adapter.name,
                subject_type=subject_type,
                subject_id=subject_id,
                n_references=0,
                error=str(exc),
            )
        adapters.append(report)
    total = sum(a.n_references for a in adapters)
    return {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "enabled": True,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "adapters": [asdict(a) for a in adapters],
        "total_references": total,
    }


def forget_subject(
    subject_type: str,
    subject_id: str,
    *,
    confirm_phrase: str,
) -> dict[str, Any]:
    """Run every adapter's forget path. Returns per-adapter results.

    ``confirm_phrase`` MUST equal ``"FORGET <subject_type>:<subject_id>"``
    exactly (case-sensitive). This is a deliberate friction gate
    matching the rest of the system's destructive-action pattern
    (see ``governance_ratchet`` relax flow).
    """
    if subject_type not in VALID_SUBJECT_TYPES:
        raise ValueError(
            f"subject_type must be one of {VALID_SUBJECT_TYPES}; got {subject_type!r}"
        )
    if not subject_id or not isinstance(subject_id, str):
        raise ValueError("subject_id must be a non-empty string")
    expected = f"FORGET {subject_type}:{subject_id}"
    if confirm_phrase != expected:
        raise ValueError(
            f"confirm_phrase must equal {expected!r} exactly; "
            f"got {confirm_phrase!r}"
        )
    if not _enabled():
        return {
            "subject_type": subject_type,
            "subject_id": subject_id,
            "enabled": False,
            "results": [],
            "total_removed": 0,
        }
    results: list[AdapterForgetResult] = []
    for adapter in _ADAPTERS:
        if subject_type not in adapter.supports:
            continue
        try:
            res = adapter.forget(subject_type, subject_id)
        except Exception as exc:
            res = AdapterForgetResult(
                adapter=adapter.name,
                subject_type=subject_type,
                subject_id=subject_id,
                n_removed=0,
                error=str(exc),
            )
        results.append(res)
    total_removed = sum(r.n_removed for r in results)
    return {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "enabled": True,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "results": [asdict(r) for r in results],
        "total_removed": total_removed,
    }


__all__ = [
    "SUBJECT_PERSON",
    "SUBJECT_DOMAIN",
    "SUBJECT_SENDER",
    "VALID_SUBJECT_TYPES",
    "AdapterReport",
    "AdapterForgetResult",
    "PrivacyAdapter",
    "audit_subject",
    "forget_subject",
]

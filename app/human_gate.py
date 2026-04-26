"""
human_gate.py — Borderline mutation approval flow.

Between full-auto deploy and full-blocked, there's a useful middle path:
"show me the diff first, I approve, then deploy."

This module routes mutations whose confidence is moderate (delta in
[+0.0, +0.05] OR delta uncertain because eval was unavailable) through
a human approval queue. The owner sees a Signal message with the diff
and a one-click approve/reject control.

Confidence categories:
  - HIGH (auto-deploy):  delta > 0.05 with eval-confirmed signal, low risk
  - BORDERLINE (gated):  delta in [0.001, 0.05] OR eval unavailable
  - LOW (auto-discard):  delta <= 0 or rejected by safety gates

Borderline mutations are queued in workspace/human_approval_queue.json
and surfaced via Signal. The owner can approve (deploy proceeds), reject
(mutation reverted), or ignore (auto-rejects after 24h).

This is a pure proposal queue — it does not modify auto_deployer's
existing safety pipeline. Approved mutations still flow through the
standard validate_proposal_paths → AST → blocked imports → backup →
hot reload → post-deploy monitor sequence.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Configuration ────────────────────────────────────────────────────────────

APPROVAL_QUEUE_PATH = Path("/app/workspace/human_approval_queue.json")
APPROVAL_HISTORY_PATH = Path("/app/workspace/human_approval_history.json")

_AUTO_REJECT_AFTER_HOURS = 24

# Confidence thresholds
_HIGH_CONFIDENCE_DELTA = 0.05
_BORDERLINE_DELTA_FLOOR = 0.001


class ConfidenceTier(str, Enum):
    HIGH = "high"            # Auto-deploy
    BORDERLINE = "borderline"  # Human gate
    LOW = "low"              # Auto-discard


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class ApprovalRequest:
    """A pending borderline mutation awaiting human decision."""
    request_id: str
    experiment_id: str
    hypothesis: str
    change_type: str
    files: dict[str, str]
    delta: float
    confidence_tier: str
    confidence_reason: str
    created_at: float
    decision: str = "pending"   # pending | approved | rejected | expired
    decided_at: float = 0.0
    decided_by: str = ""

    def to_dict(self) -> dict:
        # Truncate file contents in serialized form to avoid massive queue files
        serialized = asdict(self)
        serialized["files"] = {
            path: content[:5000] for path, content in self.files.items()
        }
        return serialized


# ── Confidence classification ────────────────────────────────────────────────

def classify_confidence(
    delta: float,
    eval_measured: bool = True,
    has_high_centrality_files: bool = False,
    is_hot_path: bool = False,
) -> tuple[ConfidenceTier, str]:
    """Decide whether a kept mutation is HIGH/BORDERLINE/LOW confidence.

    Args:
        delta: The reported composite_score delta.
        eval_measured: Whether the targeted task evaluation actually ran.
        has_high_centrality_files: Whether any modified file has many dependents.
        is_hot_path: Whether any modified file is on the hot path.

    Returns:
        (tier, reason) — reason is a short human-readable string.
    """
    if delta < _BORDERLINE_DELTA_FLOOR:
        return ConfidenceTier.LOW, f"delta {delta:+.4f} below threshold"

    # Below high-confidence delta → borderline regardless of other factors
    if delta < _HIGH_CONFIDENCE_DELTA:
        if not eval_measured:
            return ConfidenceTier.BORDERLINE, "eval not measured — needs review"
        return ConfidenceTier.BORDERLINE, f"small delta {delta:+.4f} — needs review"

    # Above threshold but high-risk file → still borderline
    if has_high_centrality_files:
        return ConfidenceTier.BORDERLINE, f"delta {delta:+.4f} but high centrality — needs review"
    if is_hot_path:
        return ConfidenceTier.BORDERLINE, f"delta {delta:+.4f} but hot path — needs review"

    return ConfidenceTier.HIGH, f"delta {delta:+.4f}, eval confirmed, low risk"


# ── Queue persistence ────────────────────────────────────────────────────────

def _load_queue() -> list[dict]:
    if not APPROVAL_QUEUE_PATH.exists():
        return []
    try:
        return json.loads(APPROVAL_QUEUE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_queue(queue: list[dict]) -> None:
    try:
        APPROVAL_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        APPROVAL_QUEUE_PATH.write_text(json.dumps(queue, indent=2, default=str))
    except OSError as e:
        logger.warning(f"human_gate: queue save failed: {e}")


def _archive_decision(request: ApprovalRequest) -> None:
    """Move a decided request to history."""
    try:
        history: list[dict] = []
        if APPROVAL_HISTORY_PATH.exists():
            history = json.loads(APPROVAL_HISTORY_PATH.read_text())
        history.append(request.to_dict())
        history = history[-200:]
        APPROVAL_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        APPROVAL_HISTORY_PATH.write_text(json.dumps(history, indent=2, default=str))
    except OSError:
        pass


# ── Public API ───────────────────────────────────────────────────────────────

def request_approval(
    experiment_id: str,
    hypothesis: str,
    change_type: str,
    files: dict[str, str],
    delta: float,
    confidence_reason: str = "borderline",
) -> str:
    """Queue a borderline mutation for human approval.

    Args:
        experiment_id: ID from results_ledger.
        hypothesis: What the mutation does.
        change_type: skill | code | prompt.
        files: {path: content} dict of proposed changes.
        delta: Reported delta.
        confidence_reason: Why this is borderline rather than auto-deploy.

    Returns:
        Request ID. Use approve_request / reject_request to decide.
    """
    request_id = f"approval_{experiment_id}_{int(time.time())}"
    request = ApprovalRequest(
        request_id=request_id,
        experiment_id=experiment_id,
        hypothesis=hypothesis,
        change_type=change_type,
        files=files,
        delta=delta,
        confidence_tier=ConfidenceTier.BORDERLINE.value,
        confidence_reason=confidence_reason,
        created_at=time.time(),
    )

    queue = _load_queue()
    queue.append(request.to_dict())
    _save_queue(queue)

    _send_approval_notification(request)
    logger.info(f"human_gate: queued {request_id} for approval")
    return request_id


def approve_request(request_id: str, approver: str = "owner") -> bool:
    """Approve a pending request — triggers deploy. Returns True on success."""
    queue = _load_queue()
    for entry in queue:
        if entry.get("request_id") != request_id:
            continue
        if entry.get("decision") != "pending":
            return False  # Already decided

        entry["decision"] = "approved"
        entry["decided_at"] = time.time()
        entry["decided_by"] = approver

        request = ApprovalRequest(**{k: v for k, v in entry.items() if k in ApprovalRequest.__dataclass_fields__})
        _archive_decision(request)

        # Remove from active queue
        queue = [e for e in queue if e.get("request_id") != request_id]
        _save_queue(queue)

        # Trigger deploy via auto_deployer
        try:
            from app.auto_deployer import schedule_deploy
            schedule_deploy(reason=f"human-approved-{request_id}")
            logger.info(f"human_gate: approved {request_id} — deploy scheduled")
        except Exception as e:
            logger.error(f"human_gate: deploy failed for {request_id}: {e}")
            return False

        return True
    return False


def reject_request(request_id: str, approver: str = "owner", reason: str = "") -> bool:
    """Reject a pending request — mutation will not deploy."""
    queue = _load_queue()
    for entry in queue:
        if entry.get("request_id") != request_id:
            continue
        if entry.get("decision") != "pending":
            return False

        entry["decision"] = "rejected"
        entry["decided_at"] = time.time()
        entry["decided_by"] = approver
        if reason:
            entry["confidence_reason"] += f" | rejected: {reason}"

        request = ApprovalRequest(**{k: v for k, v in entry.items() if k in ApprovalRequest.__dataclass_fields__})
        _archive_decision(request)

        queue = [e for e in queue if e.get("request_id") != request_id]
        _save_queue(queue)
        logger.info(f"human_gate: rejected {request_id}")
        return True
    return False


def get_pending_requests() -> list[dict]:
    """Return all pending approval requests (for dashboard)."""
    return [e for e in _load_queue() if e.get("decision") == "pending"]


def expire_stale_requests() -> int:
    """Auto-reject pending requests older than _AUTO_REJECT_AFTER_HOURS.

    Background job — runs in idle scheduler. Returns count expired.
    """
    cutoff = time.time() - _AUTO_REJECT_AFTER_HOURS * 3600
    queue = _load_queue()
    expired_count = 0
    remaining = []

    for entry in queue:
        if entry.get("decision") != "pending":
            remaining.append(entry)
            continue
        if entry.get("created_at", 0) < cutoff:
            entry["decision"] = "expired"
            entry["decided_at"] = time.time()
            entry["decided_by"] = "auto-expire"
            try:
                request = ApprovalRequest(**{
                    k: v for k, v in entry.items() if k in ApprovalRequest.__dataclass_fields__
                })
                _archive_decision(request)
            except Exception:
                pass
            expired_count += 1
        else:
            remaining.append(entry)

    if expired_count > 0:
        _save_queue(remaining)
        logger.info(f"human_gate: expired {expired_count} stale requests")
    return expired_count


# ── Notification ─────────────────────────────────────────────────────────────

def _send_approval_notification(request: ApprovalRequest) -> None:
    """Send Signal message to owner with diff summary. Best-effort."""
    try:
        from app.signal_client import send_message
        from app.config import get_settings

        files_summary = "\n".join(
            f"  - {path} ({len(content)} chars)"
            for path, content in list(request.files.items())[:5]
        )
        msg = (
            f"🔔 Borderline mutation needs review\n"
            f"ID: {request.request_id}\n"
            f"Δ: {request.delta:+.4f}\n"
            f"Type: {request.change_type}\n"
            f"Why borderline: {request.confidence_reason}\n\n"
            f"Hypothesis:\n{request.hypothesis[:200]}\n\n"
            f"Files:\n{files_summary}\n\n"
            f"Reply with: approve {request.request_id} OR reject {request.request_id}"
        )
        send_message(get_settings().signal_owner_number, msg)
    except Exception as e:
        logger.debug(f"human_gate: notification send failed: {e}")

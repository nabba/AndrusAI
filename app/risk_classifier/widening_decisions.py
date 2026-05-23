"""Operator decisions on trust-widening proposals (Phase 4 piece 1b, 2026-05-20).

Append-only JSONL log of operator decisions on `WideningProposal`
records. Sits next to the proposals audit log; decisions reference
proposals by `proposal_id`.

  workspace/risk_classifier/widening_proposals.jsonl   ← propose_widenings emits here
  workspace/risk_classifier/widening_decisions.jsonl   ← mark_approved / mark_rejected emit here

Why two files?
  * Proposals are facts (the analyser observed evidence on date X
    and recommended Y). They never change after emission.
  * Decisions are operator actions (the operator approved or
    rejected a proposal on date Z). They also never change after
    emission.
  * Keeping them separate means the proposals audit stays
    monotonically growing and is safe to compress / archive
    independently of the decision history.

The approve path is the one place the system widens its own
allowlists — it's load-bearing safety. The setters in
`app.runtime_settings.set_auto_apply_allowed_*` are the only path
that mutates the allowlists, so this module's job is to translate
"operator approved proposal X" into the corresponding setter call.

The reject path records the decision but does NOT change settings.
A rejected proposal stays out of `pending_proposals()` so the
operator isn't asked again. Future re-emissions of the same widening
will appear if the evidence strengthens further (different proposal_id).

Idempotency:
  * `mark_approved(id)` on an already-approved proposal: no-op
    (returns prior decision unchanged).
  * `mark_rejected(id)` on an already-rejected proposal: no-op.
  * Approve-then-reject: refused (proposal is already approved;
    operator's intent has been honored).
  * Reject-then-approve: refused (operator already said no; if
    they change their mind, a new proposal will be emitted when
    the evidence accumulates further).

Pinned by `test_approve_then_reject_refused` +
`test_reject_then_approve_refused`.
"""
from __future__ import annotations

import enum
import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class DecisionStatus(str, enum.Enum):
    """Each proposal ends in exactly one decision state."""
    PENDING = "pending"      # no decision recorded yet
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class WideningDecision:
    """One operator decision on a widening proposal."""

    proposal_id: str
    status: DecisionStatus
    decided_at: str       # ISO-8601 UTC
    operator: str = "react-operator"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WideningDecision":
        try:
            status = DecisionStatus(data.get("status", "pending"))
        except ValueError:
            status = DecisionStatus.PENDING
        return cls(
            proposal_id=str(data.get("proposal_id", "")),
            status=status,
            decided_at=str(data.get("decided_at", "")),
            operator=str(data.get("operator", "react-operator")),
            reason=str(data.get("reason", "")),
        )


# ── Storage ─────────────────────────────────────────────────────────


_DEFAULT_BASE_DIR = Path("/app/workspace/risk_classifier")
_base_dir_override: Optional[Path] = None
_LOCK = threading.RLock()
_DECISIONS_CACHE: Optional[dict[str, WideningDecision]] = None


def _base_dir() -> Path:
    return _base_dir_override or _DEFAULT_BASE_DIR


def _decisions_path() -> Path:
    return _base_dir() / "widening_decisions.jsonl"


def _load_decisions() -> dict[str, WideningDecision]:
    """Read the decisions JSONL and build a proposal_id → latest-decision
    index. Defensive on missing or partially-corrupted file."""
    global _DECISIONS_CACHE
    if _DECISIONS_CACHE is not None:
        return _DECISIONS_CACHE
    with _LOCK:
        if _DECISIONS_CACHE is not None:
            return _DECISIONS_CACHE
        idx: dict[str, WideningDecision] = {}
        path = _decisions_path()
        if path.exists():
            try:
                with path.open(encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = WideningDecision.from_dict(
                                json.loads(line),
                            )
                            if not d.proposal_id:
                                continue
                            # Last-write-wins for the same proposal_id.
                            existing = idx.get(d.proposal_id)
                            if (
                                existing is None
                                or d.decided_at > existing.decided_at
                            ):
                                idx[d.proposal_id] = d
                        except (
                            json.JSONDecodeError,
                            ValueError,
                            TypeError,
                        ):
                            continue
            except OSError as exc:
                logger.warning(
                    "widening_decisions: read failed: %s", exc,
                )
        _DECISIONS_CACHE = idx
        return _DECISIONS_CACHE


def _append_decision(decision: WideningDecision) -> None:
    """Atomic append + cache update."""
    with _LOCK:
        path = _decisions_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(
                    decision.to_dict(), separators=(",", ":"),
                ))
                f.write("\n")
        except OSError as exc:
            logger.warning(
                "widening_decisions: append failed: %s", exc,
            )
        # Update the cache in-place so subsequent lookups see it.
        global _DECISIONS_CACHE
        if _DECISIONS_CACHE is not None:
            _DECISIONS_CACHE[decision.proposal_id] = decision


def decision_for(proposal_id: str) -> Optional[WideningDecision]:
    """Look up the latest decision for a proposal. Returns None when
    no decision has been recorded yet."""
    if not proposal_id:
        return None
    return _load_decisions().get(proposal_id)


def reset_for_tests(base_dir: Optional[Path] = None) -> None:
    """Test helper — clear the in-memory cache + redirect base dir."""
    global _base_dir_override, _DECISIONS_CACHE
    with _LOCK:
        _base_dir_override = base_dir
        _DECISIONS_CACHE = None


# ── Public API ─────────────────────────────────────────────────────


def mark_approved(
    proposal_id: str,
    *,
    operator: str = "react-operator",
    reason: str = "",
) -> WideningDecision:
    """Approve a proposal: record the decision + apply the widening
    to runtime_settings.

    The widening is applied via the standard setters
    (``set_auto_apply_allowed_requestors`` /
    ``set_auto_apply_allowed_paths``) so all the existing validation
    (caps, path-traversal guards, type checks) still runs.

    Raises
    ------
    KeyError
        Proposal does not exist (caller passes a bad ID).
    ValueError
        Proposal was already rejected — operator's intent has been
        honored; widening cannot be retroactively approved.

    Returns the decision record. Idempotent: re-approving an
    already-approved proposal returns the existing decision (no new
    setter calls, no new audit row).
    """
    if not proposal_id:
        raise ValueError("proposal_id cannot be empty")

    # Look up the proposal.
    from app.risk_classifier.widening import list_proposals
    matching = [
        p for p in list_proposals(limit=2000)
        if p.proposal_id == proposal_id
    ]
    if not matching:
        raise KeyError(f"proposal {proposal_id!r} not found")
    proposal = matching[0]

    existing = decision_for(proposal_id)
    if existing is not None:
        if existing.status is DecisionStatus.APPROVED:
            return existing
        if existing.status is DecisionStatus.REJECTED:
            raise ValueError(
                f"proposal {proposal_id!r} was already rejected at "
                f"{existing.decided_at}; cannot approve",
            )

    # Apply the widening via the standard setter. Failure here is
    # caller-visible (rate-limit, validation error, etc.).
    from app.runtime_settings import (
        get_auto_apply_allowed_paths,
        get_auto_apply_allowed_requestors,
        set_auto_apply_allowed_paths,
        set_auto_apply_allowed_requestors,
    )

    if proposal.list_name == "auto_apply_allowed_requestors":
        current = list(get_auto_apply_allowed_requestors())
        if proposal.new_entry not in current:
            current.append(proposal.new_entry)
            set_auto_apply_allowed_requestors(current)
    elif proposal.list_name == "auto_apply_allowed_paths":
        current = list(get_auto_apply_allowed_paths())
        if proposal.new_entry not in current:
            current.append(proposal.new_entry)
            set_auto_apply_allowed_paths(current)
    else:
        raise ValueError(
            f"unknown list_name {proposal.list_name!r}"
        )

    decision = WideningDecision(
        proposal_id=proposal_id,
        status=DecisionStatus.APPROVED,
        decided_at=datetime.now(timezone.utc).isoformat(),
        operator=operator,
        reason=reason,
    )
    _append_decision(decision)
    logger.info(
        "widening_decisions: approved %s (list=%s, entry=%r, by=%s)",
        proposal_id, proposal.list_name, proposal.new_entry, operator,
    )

    # Phase A.4 closure (2026-05-22) — emit a governance_ratchet event
    # to the identity continuity ledger. The ledger already has the
    # ``governance_ratchet`` kind registered (PROGRAM §25.2 shipped
    # the kind for governance.py threshold ratcheting); risk-classifier
    # widening is the same shape of event — operator-driven loosening
    # of an automation gate — and belongs in the same trail. Failure-
    # isolated: a sick ledger never blocks the approval.
    try:
        from app.identity.continuity_ledger import record_event
        record_event(
            kind="governance_ratchet",
            actor=operator,
            summary=(
                f"widening approved: {proposal.list_name} += "
                f"{proposal.new_entry!r}"
            ),
            detail={
                "subsystem": "risk_classifier_widening",
                "proposal_id": proposal_id,
                "list_name": proposal.list_name,
                "new_entry": proposal.new_entry,
                "reason": reason,
            },
        )
    except Exception:
        logger.debug(
            "widening_decisions: continuity-ledger emit failed",
            exc_info=True,
        )

    return decision


def mark_rejected(
    proposal_id: str,
    *,
    operator: str = "react-operator",
    reason: str = "",
) -> WideningDecision:
    """Reject a proposal: record the decision; do NOT change settings.

    Raises
    ------
    KeyError
        Proposal does not exist.
    ValueError
        Proposal was already approved — cannot retroactively reject.
    """
    if not proposal_id:
        raise ValueError("proposal_id cannot be empty")

    # Look up the proposal — we need to confirm it exists; details
    # don't matter for rejection.
    from app.risk_classifier.widening import list_proposals
    matching = [
        p for p in list_proposals(limit=2000)
        if p.proposal_id == proposal_id
    ]
    if not matching:
        raise KeyError(f"proposal {proposal_id!r} not found")

    existing = decision_for(proposal_id)
    if existing is not None:
        if existing.status is DecisionStatus.REJECTED:
            return existing
        if existing.status is DecisionStatus.APPROVED:
            raise ValueError(
                f"proposal {proposal_id!r} was already approved at "
                f"{existing.decided_at}; cannot reject",
            )

    decision = WideningDecision(
        proposal_id=proposal_id,
        status=DecisionStatus.REJECTED,
        decided_at=datetime.now(timezone.utc).isoformat(),
        operator=operator,
        reason=reason,
    )
    _append_decision(decision)
    logger.info(
        "widening_decisions: rejected %s (by=%s, reason=%r)",
        proposal_id, operator, reason,
    )
    return decision


def pending_proposals(limit: int = 50) -> list:
    """Return the most-recent proposals that have NOT been decided.

    Caps at ``limit``. The newest-first ordering matches the
    operator's React view.
    """
    from app.risk_classifier.widening import list_proposals
    decisions = _load_decisions()
    out = []
    for p in list_proposals(limit=2000):
        if p.proposal_id in decisions:
            # Already decided (approved or rejected)
            continue
        out.append(p)
        if len(out) >= limit:
            break
    return out

"""Trust-zone widening proposer (Phase 4 piece 1, 2026-05-20).

Observes operator-approval patterns across the change-request log
and proposes additions to the AUTO_APPLY allowlists in
``app.runtime_settings`` for operator confirmation.

The proposer is the system's mechanism for **earning trust over
time**: when a requestor/path combination has demonstrated a clean
approval track record (many approvals, zero rollbacks, low rejection
rate) over a sufficient window, the system says "this looks safe to
auto-apply." The operator confirms or rejects via a normal
change-request flow.

Safety semantics
────────────────

* **The proposer NEVER auto-applies.** Every proposal goes through
  the operator gate. The widening itself is a runtime_settings
  change — the same governance path other dashboard toggles use.
* **Conservative defaults.** ≥10 approvals over ≥30 days, zero
  rollbacks, ≤10% rejection rate. Operators can tune via
  runtime_settings.
* **Pure-function analysis core.** ``propose_widenings(crs, ...)``
  takes a list of ChangeRequest and returns a list of
  WideningProposal. No I/O, no LLMs. Production callers wire the CR
  history; tests inject synthetic data.
* **Default OFF.** ``widening_proposer_enabled`` ships False — the
  module is a pure library with no production callers until the
  operator opts in.

Composition
───────────

* Reads from ``app.change_requests.store`` for the history pass.
* Reads from ``app.runtime_settings.get_auto_apply_allowed_*`` to
  skip already-allowlisted entries.
* Writes to ``workspace/risk_classifier/widening_proposals.jsonl``
  as an append-only audit log.
* Future: emit each proposal via Signal + a confirm-action UI; on
  operator approval, call the runtime_settings setters.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)


# Conservative defaults — match what's in runtime_settings.
DEFAULT_MIN_APPROVALS = 10
DEFAULT_MAX_ROLLBACK_RATE = 0.0      # zero rollbacks tolerated
DEFAULT_MAX_REJECTION_RATE = 0.10    # ≤10% rejection rate
DEFAULT_MIN_HISTORY_DAYS = 30        # need at least 30 days of approval data
DEFAULT_MAX_PROPOSALS_PER_PASS = 5   # don't flood the operator


@dataclass
class WideningEvidence:
    """Per-(requestor, path-prefix) statistics that justify a proposal."""

    requestor: str
    path_prefix: str        # e.g. "workspace/notes/" — trailing slash = prefix match
    approvals: int = 0
    rejections: int = 0
    rollbacks: int = 0
    applied: int = 0
    apply_failed: int = 0
    first_at: str = ""      # earliest ChangeRequest in this group
    last_at: str = ""       # most recent
    sample_cr_ids: list[str] = field(default_factory=list)

    @property
    def total_decided(self) -> int:
        """Decisions that count toward stability — approvals +
        rejections + (implicit applied as a subset of approved)."""
        return self.approvals + self.rejections

    @property
    def rejection_rate(self) -> float:
        if self.total_decided == 0:
            return 0.0
        return self.rejections / self.total_decided

    @property
    def rollback_rate(self) -> float:
        if self.applied == 0:
            return 0.0
        return self.rollbacks / self.applied

    @property
    def history_days(self) -> float:
        """Calendar days between the first + last decision."""
        if not self.first_at or not self.last_at:
            return 0.0
        try:
            first = datetime.fromisoformat(self.first_at)
            last = datetime.fromisoformat(self.last_at)
        except (ValueError, TypeError):
            return 0.0
        return max(0.0, (last - first).total_seconds() / 86400.0)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rejection_rate"] = round(self.rejection_rate, 4)
        d["rollback_rate"] = round(self.rollback_rate, 4)
        d["history_days"] = round(self.history_days, 2)
        return d


@dataclass
class WideningProposal:
    """One proposed widening of the AUTO_APPLY allowlists.

    The proposal carries the suggested change (which allowlist
    + what value to add) AND the evidence supporting it. Operators
    confirm via a normal change-request flow that updates
    runtime_settings.
    """

    proposal_id: str        # uuid hex
    proposed_at: str        # ISO-8601 UTC
    list_name: str          # "auto_apply_allowed_requestors" | "auto_apply_allowed_paths"
    new_entry: str
    evidence: WideningEvidence
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "proposed_at": self.proposed_at,
            "list_name": self.list_name,
            "new_entry": self.new_entry,
            "evidence": self.evidence.to_dict(),
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WideningProposal":
        ev = data.get("evidence") or {}
        return cls(
            proposal_id=str(data.get("proposal_id", "")),
            proposed_at=str(data.get("proposed_at", "")),
            list_name=str(data.get("list_name", "")),
            new_entry=str(data.get("new_entry", "")),
            evidence=WideningEvidence(
                requestor=str(ev.get("requestor", "")),
                path_prefix=str(ev.get("path_prefix", "")),
                approvals=int(ev.get("approvals", 0)),
                rejections=int(ev.get("rejections", 0)),
                rollbacks=int(ev.get("rollbacks", 0)),
                applied=int(ev.get("applied", 0)),
                apply_failed=int(ev.get("apply_failed", 0)),
                first_at=str(ev.get("first_at", "")),
                last_at=str(ev.get("last_at", "")),
                sample_cr_ids=list(ev.get("sample_cr_ids", []) or []),
            ),
            rationale=str(data.get("rationale", "")),
        )


# ── Analysis ────────────────────────────────────────────────────────


def _path_prefix(path: str) -> str:
    """Collapse ``a/b/c/d.py`` to ``a/b/`` for grouping. Top-level
    paths (``foo.py``) become ``""`` and are excluded from grouping —
    too coarse to widen on."""
    if not path or "/" not in path:
        return ""
    parts = path.split("/")
    # First two segments — granular enough to distinguish
    # ``app/agents/`` from ``app/control_plane/`` but coarse enough
    # to collect a meaningful number of CRs.
    return "/".join(parts[:2]) + "/"


def aggregate_evidence(
    crs: Iterable[Any],
    *,
    now: Optional[datetime] = None,
) -> dict[tuple[str, str], WideningEvidence]:
    """Group CRs by (requestor, path_prefix) and aggregate stats.

    Statuses considered:
      * ``APPROVED`` / ``APPLIED`` → ``approvals`` (applied is a
        strict subset of approved; we count it separately too)
      * ``REJECTED`` → ``rejections``
      * ``ROLLED_BACK`` → ``rollbacks`` (and still counted as
        applied for the rate)
      * ``APPLY_FAILED`` → ``apply_failed`` (informational only —
        not counted toward approval stats)
      * ``PENDING`` / ``TIMEOUT`` / ``TIER_IMMUTABLE_REFUSED`` →
        skipped (not a settled decision)
    """
    # Lazy import to avoid heavy module loads when this is unused.
    try:
        from app.change_requests.models import Status
    except Exception:
        # In stripped test envs, Status may not be importable; fall
        # back to string comparison.
        Status = None  # type: ignore[assignment]

    by_key: dict[tuple[str, str], WideningEvidence] = {}
    for cr in crs:
        path = getattr(cr, "path", "")
        requestor = getattr(cr, "requestor", "")
        if not path or not requestor:
            continue
        prefix = _path_prefix(path)
        if not prefix:
            continue
        key = (requestor, prefix)
        ev = by_key.get(key)
        if ev is None:
            ev = WideningEvidence(
                requestor=requestor,
                path_prefix=prefix,
            )
            by_key[key] = ev

        # Pull status either as enum or string
        status_val = getattr(cr, "status", None)
        if hasattr(status_val, "value"):
            status_val = status_val.value
        status_val = str(status_val) if status_val else ""

        if status_val in ("approved", "applied"):
            ev.approvals += 1
            if status_val == "applied":
                ev.applied += 1
        elif status_val == "rejected":
            ev.rejections += 1
        elif status_val == "rolled_back":
            ev.rollbacks += 1
            ev.applied += 1  # rolled_back implies it was applied first
        elif status_val == "apply_failed":
            ev.apply_failed += 1

        # Track temporal extent
        created_at = getattr(cr, "created_at", "") or ""
        decided_at = getattr(cr, "decided_at", None) or created_at
        if decided_at:
            if not ev.first_at or decided_at < ev.first_at:
                ev.first_at = decided_at
            if not ev.last_at or decided_at > ev.last_at:
                ev.last_at = decided_at

        cr_id = getattr(cr, "id", "")
        if cr_id and len(ev.sample_cr_ids) < 5:
            ev.sample_cr_ids.append(cr_id)

    return by_key


def propose_widenings(
    crs: Iterable[Any],
    *,
    current_allowed_requestors: Iterable[str] = (),
    current_allowed_paths: Iterable[str] = (),
    min_approvals: int = DEFAULT_MIN_APPROVALS,
    max_rollback_rate: float = DEFAULT_MAX_ROLLBACK_RATE,
    max_rejection_rate: float = DEFAULT_MAX_REJECTION_RATE,
    min_history_days: int = DEFAULT_MIN_HISTORY_DAYS,
    max_proposals: int = DEFAULT_MAX_PROPOSALS_PER_PASS,
    now: Optional[datetime] = None,
) -> list[WideningProposal]:
    """Pure-function: examine CRs and return widening proposals.

    The decision rule for proposing a widening on ``(requestor,
    path_prefix)``:
      1. ≥ ``min_approvals`` decisions ended APPROVED/APPLIED.
      2. ``rollback_rate <= max_rollback_rate`` (zero rollbacks
         tolerated by default).
      3. ``rejection_rate <= max_rejection_rate`` (≤10% by default).
      4. ``history_days >= min_history_days`` (≥30 by default).

    For each qualifying (requestor, path_prefix), the function emits
    up to two proposals: one to widen the requestor allowlist (if
    not already present), one to widen the path allowlist (if not
    already present). Both go through the operator gate.

    Returns proposals sorted by approval-count desc, capped at
    ``max_proposals``.
    """
    import uuid

    allowed_req = frozenset(current_allowed_requestors)
    allowed_paths = frozenset(current_allowed_paths)

    evidence_map = aggregate_evidence(crs, now=now)
    proposals: list[WideningProposal] = []
    now_iso = (now or datetime.now(timezone.utc)).isoformat()

    # Sort by approvals desc for deterministic, "most-trusted-first"
    # selection.
    for ev in sorted(
        evidence_map.values(),
        key=lambda e: e.approvals,
        reverse=True,
    ):
        # Gate 1: enough approvals?
        if ev.approvals < min_approvals:
            continue
        # Gate 2: clean rollback record?
        if ev.rollback_rate > max_rollback_rate:
            continue
        # Gate 3: rejection rate within bounds?
        if ev.rejection_rate > max_rejection_rate:
            continue
        # Gate 4: enough wall-clock history?
        if ev.history_days < min_history_days:
            continue

        # Propose adding the requestor (if not already)
        if ev.requestor not in allowed_req:
            proposals.append(WideningProposal(
                proposal_id=uuid.uuid4().hex,
                proposed_at=now_iso,
                list_name="auto_apply_allowed_requestors",
                new_entry=ev.requestor,
                evidence=ev,
                rationale=(
                    f"{ev.approvals} approvals, "
                    f"{ev.rollbacks} rollbacks, "
                    f"{ev.rejections} rejections over "
                    f"{ev.history_days:.0f} days. "
                    "Conservative widening — operator confirms."
                ),
            ))
            if len(proposals) >= max_proposals:
                break

        # Propose adding the path prefix (if not already)
        if ev.path_prefix not in allowed_paths:
            proposals.append(WideningProposal(
                proposal_id=uuid.uuid4().hex,
                proposed_at=now_iso,
                list_name="auto_apply_allowed_paths",
                new_entry=ev.path_prefix,
                evidence=ev,
                rationale=(
                    f"{ev.approvals} approvals on this path prefix "
                    f"with clean rollback record over "
                    f"{ev.history_days:.0f} days. Operator confirms."
                ),
            ))
            if len(proposals) >= max_proposals:
                break

    return proposals[:max_proposals]


# ── Audit log (operator-visible record of proposals) ───────────────


_DEFAULT_BASE_DIR = Path("/app/workspace/risk_classifier")
_base_dir_override: Path | None = None
_LOCK = threading.RLock()


def _base_dir() -> Path:
    return _base_dir_override or _DEFAULT_BASE_DIR


def get_base_dir() -> Path:
    return _base_dir()


def _audit_path() -> Path:
    return _base_dir() / "widening_proposals.jsonl"


def append_proposal(proposal: WideningProposal) -> None:
    """Append one proposal to the JSONL audit log. Atomic per-line
    via O_APPEND. Never raises (best-effort)."""
    with _LOCK:
        try:
            path = _audit_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(proposal.to_dict(), separators=(",", ":")))
                f.write("\n")
        except OSError as exc:
            logger.warning(
                "widening: append_proposal failed: %s", exc,
            )


def list_proposals(limit: int = 100) -> list[WideningProposal]:
    """Return the most-recent proposals (newest first). Defensive on
    a missing or partial file."""
    path = _audit_path()
    if not path.exists():
        return []
    proposals: list[WideningProposal] = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    proposals.append(
                        WideningProposal.from_dict(json.loads(line)),
                    )
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
    except OSError as exc:
        logger.warning(
            "widening: list_proposals read failed: %s", exc,
        )
        return []
    # Newest first by proposed_at
    proposals.sort(
        key=lambda p: p.proposed_at,
        reverse=True,
    )
    return proposals[:limit]


def reset_for_tests(base_dir: Optional[Path] = None) -> None:
    """Test helper — redirect the base dir."""
    global _base_dir_override
    with _LOCK:
        _base_dir_override = base_dir


# ── Public top-level: scan and propose ─────────────────────────────


def run_widening_scan(
    crs: Optional[Iterable[Any]] = None,
    *,
    emit_audit: bool = True,
) -> list[WideningProposal]:
    """Top-level scan invoked from the idle scheduler (future) or
    operator-initiated.

    Reads thresholds from runtime_settings. Pulls the CR list from
    ``change_requests.store`` if not supplied. Skips silently when
    the master switch is off — the empty list signals "no
    proposals."
    """
    try:
        from app.runtime_settings import (
            get_auto_apply_allowed_paths,
            get_auto_apply_allowed_requestors,
            get_widening_max_rejection_rate,
            get_widening_max_rollback_rate,
            get_widening_min_approvals,
            get_widening_min_history_days,
            get_widening_proposer_enabled,
        )
    except Exception:
        logger.debug(
            "widening: runtime_settings unavailable; skipping",
            exc_info=True,
        )
        return []

    if not get_widening_proposer_enabled():
        return []

    if crs is None:
        try:
            from app.change_requests import store as cr_store
            crs = cr_store.list_all(limit=2000)
        except Exception:
            logger.debug(
                "widening: change_requests.store unavailable",
                exc_info=True,
            )
            return []

    proposals = propose_widenings(
        crs,
        current_allowed_requestors=get_auto_apply_allowed_requestors(),
        current_allowed_paths=get_auto_apply_allowed_paths(),
        min_approvals=get_widening_min_approvals(),
        max_rollback_rate=get_widening_max_rollback_rate(),
        max_rejection_rate=get_widening_max_rejection_rate(),
        min_history_days=get_widening_min_history_days(),
    )

    if emit_audit:
        for p in proposals:
            append_proposal(p)

    return proposals

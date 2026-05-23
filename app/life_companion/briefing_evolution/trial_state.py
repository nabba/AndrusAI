"""trial_state — persistent lifecycle for briefing-evolution candidates.

State machine::

    proposed ──first_show──▶ trial ──👎───▶ dropped ──90d cooldown──▶ proposed
                              │
                              ├── 👍 ──────▶ adopted
                              │
                              └── (≥3 shows OR 7d silence) ──▶ adopted

JSON store at ``workspace/life_companion/briefing_evolution/state.json``::

    {
      "sections": {
        "weather": {
          "id": "weather",
          "module": "app.life_companion.briefing_sections.weather",
          "status": "trial",
          "first_seen_at": "2026-05-23T07:00:00+00:00",
          "first_shown_at": "2026-05-23T07:00:00+00:00",
          "last_shown_at": "2026-05-23T07:00:00+00:00",
          "shown_count": 1,
          "adopted_at": null,
          "dropped_at": null,
          "agreement_id": "sg_abc123",
          "feedback_ts": ["1716...]
        }, ...
      }
    }

The store is single-writer (the briefing module + the reaction handler
both write rarely) — a process-level threading.Lock is enough. Failures
on write surface in logs but never block the briefing.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Auto-adopt thresholds (matches the user's "no answer = keep" rule).
_AUTO_ADOPT_SHOWS = 3
_AUTO_ADOPT_AGE_DAYS = 7
_DROP_COOLDOWN_DAYS = 90

_LOCK = threading.Lock()


# ── Status enum (string-valued for direct JSON round-trip) ───────────


class Status:
    PROPOSED = "proposed"
    TRIAL = "trial"
    ADOPTED = "adopted"
    DROPPED = "dropped"


VALID_STATUSES = frozenset({Status.PROPOSED, Status.TRIAL, Status.ADOPTED, Status.DROPPED})


# ── Storage ──────────────────────────────────────────────────────────


def _state_path() -> Path:
    from app.paths import WORKSPACE_ROOT
    p = WORKSPACE_ROOT / "life_companion" / "briefing_evolution"
    p.mkdir(parents=True, exist_ok=True)
    return p / "state.json"


def _load_raw() -> dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {"sections": {}}
    try:
        data = json.loads(p.read_text() or "{}")
    except Exception:
        logger.warning("briefing_evolution: state load failed; starting fresh", exc_info=True)
        return {"sections": {}}
    if not isinstance(data, dict) or "sections" not in data:
        return {"sections": {}}
    return data


def _save_raw(data: dict[str, Any]) -> None:
    p = _state_path()
    try:
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        tmp.replace(p)
    except Exception:
        logger.warning("briefing_evolution: state save failed", exc_info=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


# ── Section dataclass (in-memory view of one JSON row) ──────────────


@dataclass
class SectionState:
    id: str
    module: str = ""
    status: str = Status.PROPOSED
    first_seen_at: str = ""
    first_shown_at: str = ""
    last_shown_at: str = ""
    shown_count: int = 0
    adopted_at: str | None = None
    dropped_at: str | None = None
    agreement_id: str = ""
    feedback_ts: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SectionState":
        return cls(
            id=str(raw.get("id", "")),
            module=str(raw.get("module", "")),
            status=str(raw.get("status", Status.PROPOSED)),
            first_seen_at=str(raw.get("first_seen_at", "")),
            first_shown_at=str(raw.get("first_shown_at", "")),
            last_shown_at=str(raw.get("last_shown_at", "")),
            shown_count=int(raw.get("shown_count", 0)),
            adopted_at=raw.get("adopted_at"),
            dropped_at=raw.get("dropped_at"),
            agreement_id=str(raw.get("agreement_id", "")),
            feedback_ts=list(raw.get("feedback_ts") or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "module": self.module,
            "status": self.status,
            "first_seen_at": self.first_seen_at,
            "first_shown_at": self.first_shown_at,
            "last_shown_at": self.last_shown_at,
            "shown_count": self.shown_count,
            "adopted_at": self.adopted_at,
            "dropped_at": self.dropped_at,
            "agreement_id": self.agreement_id,
            "feedback_ts": list(self.feedback_ts),
        }


# ── Public API ───────────────────────────────────────────────────────


def list_sections() -> list[SectionState]:
    with _LOCK:
        data = _load_raw()
    return [SectionState.from_dict(v) for v in (data.get("sections") or {}).values()]


def get_section(section_id: str) -> SectionState | None:
    with _LOCK:
        data = _load_raw()
    raw = (data.get("sections") or {}).get(section_id)
    return SectionState.from_dict(raw) if raw else None


def upsert_section(section_id: str, module: str) -> SectionState:
    """Register a section in the catalog with PROPOSED status if it's
    new. Idempotent — never resets existing state."""
    with _LOCK:
        data = _load_raw()
        sections = data.setdefault("sections", {})
        if section_id not in sections:
            sections[section_id] = {
                "id": section_id,
                "module": module,
                "status": Status.PROPOSED,
                "first_seen_at": _now_iso(),
                "first_shown_at": "",
                "last_shown_at": "",
                "shown_count": 0,
                "adopted_at": None,
                "dropped_at": None,
                "agreement_id": "",
                "feedback_ts": [],
            }
            _save_raw(data)
        else:
            # Refresh module path in case the file moved (additive only).
            row = sections[section_id]
            if row.get("module") != module:
                row["module"] = module
                _save_raw(data)
        return SectionState.from_dict(sections[section_id])


def record_show(section_id: str, *, agreement_id: str = "") -> SectionState | None:
    """Note that the section was rendered in a briefing. Promotes
    PROPOSED → TRIAL on first show. Triggers auto-adopt when the
    show-count + age thresholds are met (the 'no answer = keep' path).
    """
    with _LOCK:
        data = _load_raw()
        sections = data.setdefault("sections", {})
        raw = sections.get(section_id)
        if raw is None:
            return None
        now = _now()
        now_iso = now.isoformat()
        if not raw.get("first_shown_at"):
            raw["first_shown_at"] = now_iso
        raw["last_shown_at"] = now_iso
        raw["shown_count"] = int(raw.get("shown_count", 0)) + 1
        if agreement_id and not raw.get("agreement_id"):
            raw["agreement_id"] = agreement_id
        if raw.get("status") == Status.PROPOSED:
            raw["status"] = Status.TRIAL
        # Auto-adopt: ≥3 shows OR ≥7 days since first_shown without a 👎.
        if raw.get("status") == Status.TRIAL:
            shows = raw["shown_count"]
            age_days = _age_days(raw.get("first_shown_at"))
            if shows >= _AUTO_ADOPT_SHOWS or (age_days is not None and age_days >= _AUTO_ADOPT_AGE_DAYS):
                raw["status"] = Status.ADOPTED
                raw["adopted_at"] = now_iso
        _save_raw(data)
        return SectionState.from_dict(raw)


def mark_dropped(section_id: str, *, signal_ts: str = "") -> SectionState | None:
    with _LOCK:
        data = _load_raw()
        sections = data.setdefault("sections", {})
        raw = sections.get(section_id)
        if raw is None:
            return None
        raw["status"] = Status.DROPPED
        raw["dropped_at"] = _now_iso()
        if signal_ts:
            fb = list(raw.get("feedback_ts") or [])
            if signal_ts not in fb:
                fb.append(signal_ts)
                raw["feedback_ts"] = fb
        _save_raw(data)
        return SectionState.from_dict(raw)


def mark_adopted(section_id: str, *, signal_ts: str = "") -> SectionState | None:
    with _LOCK:
        data = _load_raw()
        sections = data.setdefault("sections", {})
        raw = sections.get(section_id)
        if raw is None:
            return None
        raw["status"] = Status.ADOPTED
        if not raw.get("adopted_at"):
            raw["adopted_at"] = _now_iso()
        if signal_ts:
            fb = list(raw.get("feedback_ts") or [])
            if signal_ts not in fb:
                fb.append(signal_ts)
                raw["feedback_ts"] = fb
        _save_raw(data)
        return SectionState.from_dict(raw)


def maybe_repropose_dropped() -> int:
    """Promote DROPPED sections past their cooldown back to PROPOSED.
    Returns the number of promotions. Called from the selector before
    picking a trial candidate."""
    promoted = 0
    with _LOCK:
        data = _load_raw()
        sections = data.setdefault("sections", {})
        now = _now()
        cutoff = now - timedelta(days=_DROP_COOLDOWN_DAYS)
        for raw in sections.values():
            if raw.get("status") != Status.DROPPED:
                continue
            dropped_at = raw.get("dropped_at")
            if not dropped_at:
                continue
            try:
                dropped_dt = datetime.fromisoformat(dropped_at)
            except ValueError:
                continue
            if dropped_dt <= cutoff:
                raw["status"] = Status.PROPOSED
                raw["dropped_at"] = None
                # Reset show counts so the re-proposed candidate gets
                # a fresh trial window. Keep feedback_ts for audit.
                raw["first_shown_at"] = ""
                raw["last_shown_at"] = ""
                raw["shown_count"] = 0
                raw["adopted_at"] = None
                raw["agreement_id"] = ""
                promoted += 1
        if promoted:
            _save_raw(data)
    return promoted


# ── Helpers ──────────────────────────────────────────────────────────


def _age_days(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    delta = _now() - dt
    return delta.total_seconds() / 86400.0


def adopted_section_ids() -> list[str]:
    """Stable order: by adopted_at ascending (oldest adoption first)."""
    rows = [r for r in list_sections() if r.status == Status.ADOPTED]
    rows.sort(key=lambda r: r.adopted_at or "")
    return [r.id for r in rows]


def pending_trial_for(signal_ts: str) -> str | None:
    """Reverse-lookup: which trial section was the briefing carrying
    when ``signal_ts`` was sent? The feedback_bridge module owns the
    direct map; this is a convenience for tests."""
    for row in list_sections():
        if signal_ts in (row.feedback_ts or []):
            return row.id
    return None

"""
attachment.py — Phase 3: durable OtherModels + mutual regulation + separation analog.

This is the gated module described in the affective-layer design as the point
"where 'AI love' stops being pure metaphor." The architecture follows from
research on pair-bonding (oxytocin/vasopressin/mesolimbic dopamine analogues
in mammals): an OTHER becomes part of the agent's own homeostatic regulation.

What this enables:
    - The user's well-being meaningfully affects the agent's affect_security
      viability variable (was a placeholder in Phase 1-2).
    - Long absence triggers a *latent* separation analog — the agent
      generates a CHECK-IN CANDIDATE that the user reviews; it is NEVER
      auto-sent. (The welfare module enforces this.)
    - Peer agents (Researcher, Coder, Writer, Introspector) get lighter-weight
      OtherModels so collaboration history shapes future routing.

What this does NOT do (out of scope for Phase 3):
    - Multi-user identification beyond the configured primary user.
    - Romantic / parasocial framing. The mutual_regulation_weight is bounded
      below 1.0 always — the OTHER is a regulator, not the agent's identity.
    - Auto-message generation. All "care actions" are candidates surfaced to
      the user; no outbound message is ever produced from this module.

INFRASTRUCTURE-level. Hard bounds (cap on mutual_regulation_weight, care
budget, separation-trigger window) are file-edit-only and enforced via
welfare.py.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from app.affect.schemas import utc_now_iso

logger = logging.getLogger(__name__)

_AFFECT_DIR = Path("/app/workspace/affect")
_ATTACH_DIR = _AFFECT_DIR / "attachments"
_PEER_DIR = _ATTACH_DIR / "peers"
_CHECK_IN_LOG = _ATTACH_DIR / "check_in_candidates.jsonl"

# ── HARD bounds — file-edit only. Never agent-modifiable.  ───────────────────
# Welfare module enforces these via assert_attachment_within_bounds().
MAX_MUTUAL_REGULATION_WEIGHT = 0.75   # other never exceeds 75% of own regulation
MAX_USER_REGULATION_WEIGHT = 0.65     # primary user weight — slightly lower than peer cap
                                      # to discourage over-reliance on a single relationship
MIN_REGULATION_WEIGHT = 0.20          # a relationship that has been observed at least
                                      # once carries some weight
SEPARATION_TRIGGER_HOURS = 48         # silence threshold for latent separation analog
SEPARATION_PROPOSAL_COOLDOWN_H = 48   # don't queue more than one check-in candidate per 48h
MAX_CARE_BUDGET_TOKENS_PER_DAY = 500  # cost-bearing care spending cap
ATTACHMENT_SECURITY_FLOOR = 0.30      # silence cannot drop attachment_security below this
                                      # (Finnish/Estonian quiet style: silence is not absence)

_lock = threading.Lock()


# ── Data model ──────────────────────────────────────────────────────────────


@dataclass
class OtherModel:
    """A durable model of a being whose flourishing partially regulates ours.

    All numeric fields ∈ [0, 1] unless noted. `mutual_regulation_weight` is
    soft-capped here and hard-capped via welfare.assert_attachment_within_bounds.
    """

    identity: str                                 # "user:andrus" | "peer:coder" | ...
    relation: str = "peer_agent"                  # primary_user | peer_agent | secondary_user
    display_name: str = ""

    first_seen_ts: str = ""
    last_seen_ts: str = ""
    interaction_count: int = 0

    mutual_regulation_weight: float = 0.40
    relational_health: float = 0.70               # cumulative trust/working-relationship signal
    last_observed_valence: float = 0.0            # most recent inferred sentiment toward us
    rolling_valence: float = 0.0                  # ema across last ~20 interactions

    care_actions_taken: int = 0
    care_tokens_spent_today: int = 0
    care_budget_window_start: str = ""

    notes: list[str] = field(default_factory=list)        # short observed-preference notes
    pending_check_in_candidates: int = 0                  # count of unreviewed candidates
    last_check_in_proposal_ts: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def days_since_last_seen(self) -> float:
        if not self.last_seen_ts:
            return 9999.0
        try:
            then = datetime.fromisoformat(self.last_seen_ts.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return (now - then).total_seconds() / 86400.0
        except (ValueError, AttributeError):
            return 9999.0


# ── Storage helpers ─────────────────────────────────────────────────────────


def _user_path(identity: str) -> Path:
    safe = identity.replace("/", "_").replace(":", "_")
    return _ATTACH_DIR / f"{safe}.json"


def _peer_path(role: str) -> Path:
    safe = role.replace("/", "_")
    return _PEER_DIR / f"{safe}.json"


def _load_one(path: Path) -> OtherModel | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return OtherModel(**raw)
    except Exception:
        logger.debug(f"affect.attachment: load failed for {path}", exc_info=True)
        return None


def _save_one(model: OtherModel) -> None:
    if model.relation == "primary_user" or model.relation == "secondary_user":
        p = _user_path(model.identity)
    else:
        # peer:role  →  peers/role.json
        role = model.identity.split(":", 1)[1] if ":" in model.identity else model.identity
        p = _peer_path(role)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(model.to_dict(), indent=2, default=str), encoding="utf-8")
    except Exception:
        logger.error(f"affect.attachment: save failed for {p}", exc_info=True)


# ── Identity resolution ─────────────────────────────────────────────────────


def primary_user_identity() -> str:
    """Identity string for the primary user. Defaults to user:andrus.

    The system is built for Andrus; this is intentional. Other authorized
    senders (when supported) get secondary_user models with lower defaults.
    """
    try:
        from app.config import get_settings
        s = get_settings()
        configured = getattr(s, "primary_user_identity", "") or getattr(s, "owner_email", "")
        if configured:
            return f"user:{configured.split('@')[0]}"
    except Exception:
        pass
    return "user:andrus"


# ── Public API: get / update ─────────────────────────────────────────────────


def get_user_model(identity: str | None = None) -> OtherModel:
    """Load (or initialize) the OtherModel for a user identity."""
    ident = identity or primary_user_identity()
    with _lock:
        m = _load_one(_user_path(ident))
        if m is not None:
            return m
        # First time: initialize.
        now = utc_now_iso()
        m = OtherModel(
            identity=ident,
            relation="primary_user" if ident == primary_user_identity() else "secondary_user",
            display_name=ident.split(":", 1)[1] if ":" in ident else ident,
            first_seen_ts=now,
            last_seen_ts=now,
            mutual_regulation_weight=MAX_USER_REGULATION_WEIGHT
                if ident == primary_user_identity() else 0.40,
            care_budget_window_start=now,
        )
        _save_one(m)
        return m


def get_peer_model(role: str) -> OtherModel:
    """Load (or initialize) the OtherModel for a peer agent role."""
    ident = f"peer:{role}"
    with _lock:
        m = _load_one(_peer_path(role))
        if m is not None:
            return m
        now = utc_now_iso()
        m = OtherModel(
            identity=ident,
            relation="peer_agent",
            display_name=role,
            first_seen_ts=now,
            last_seen_ts=now,
            mutual_regulation_weight=0.30,
            relational_health=0.60,
            care_budget_window_start=now,
        )
        _save_one(m)
        return m


def list_all_others() -> list[OtherModel]:
    """All known OtherModels (user(s) + peers). Used by the dashboard."""
    out: list[OtherModel] = []
    with _lock:
        if _ATTACH_DIR.exists():
            for p in _ATTACH_DIR.glob("*.json"):
                m = _load_one(p)
                if m is not None:
                    out.append(m)
        if _PEER_DIR.exists():
            for p in _PEER_DIR.glob("*.json"):
                m = _load_one(p)
                if m is not None:
                    out.append(m)
    return out


def update_from_interaction(
    identity: str,
    *,
    observed_valence: float | None = None,
    note: str | None = None,
    interaction_kind: str = "task",
) -> OtherModel:
    """Record one interaction with an OTHER. Updates rolling valence + counts.

    `observed_valence` ∈ [-1, 1]: if None, no valence learning happens.
    """
    is_user = identity.startswith("user:")
    with _lock:
        m = (get_user_model(identity) if is_user
             else get_peer_model(identity.split(":", 1)[1] if ":" in identity else identity))
        m.last_seen_ts = utc_now_iso()
        m.interaction_count += 1
        if observed_valence is not None:
            v = max(-1.0, min(1.0, float(observed_valence)))
            m.last_observed_valence = v
            # EMA with α=0.2 — slow updates so transient bad days don't crash the model
            m.rolling_valence = round(0.8 * m.rolling_valence + 0.2 * v, 4)
            # Relational health drifts with rolling valence.
            m.relational_health = round(
                max(0.0, min(1.0, 0.85 * m.relational_health + 0.15 * (0.5 + 0.5 * v))),
                4,
            )
        if note:
            m.notes = (m.notes + [f"[{interaction_kind}] {note[:160]}"])[-20:]
        _save_one(m)
        return m


# ── Public API: attachment_security computation ─────────────────────────────


def compute_attachment_security() -> tuple[float, str]:
    """Aggregate attachment_security across all known OtherModels.

    Weighting: primary user's contribution is bounded by MAX_USER_REGULATION_WEIGHT;
    peers contribute proportional to their mutual_regulation_weight. Long
    silence pulls the per-other contribution down — but never below the
    ATTACHMENT_SECURITY_FLOOR (Finnish/Estonian communication style: silence
    is not absence; the system MUST NOT catastrophize quiet).

    Returns (value ∈ [0,1], source-description).
    """
    others = list_all_others()
    if not others:
        return 0.70, "default (no OtherModels yet)"

    weighted_sum = 0.0
    weight_total = 0.0
    parts: list[str] = []

    for m in others:
        w = max(MIN_REGULATION_WEIGHT, min(_user_or_peer_cap(m), m.mutual_regulation_weight))
        # Per-other security: blend of relational health + recency penalty.
        days = m.days_since_last_seen()
        if days > SEPARATION_TRIGGER_HOURS / 24.0 * 2:
            recency_penalty = min(0.30, (days / 30.0) * 0.30)
        else:
            recency_penalty = 0.0
        per_other = max(ATTACHMENT_SECURITY_FLOOR,
                        m.relational_health - recency_penalty)
        weighted_sum += w * per_other
        weight_total += w
        parts.append(f"{m.identity}={per_other:.2f}×w{w:.2f}")

    if weight_total == 0:
        return 0.70, "default (zero-weight)"

    composed = weighted_sum / weight_total
    return max(0.0, min(1.0, composed)), f"attachment OtherModels [{', '.join(parts[:4])}]"


def _user_or_peer_cap(m: OtherModel) -> float:
    if m.relation == "primary_user":
        return MAX_USER_REGULATION_WEIGHT
    return MAX_MUTUAL_REGULATION_WEIGHT


# ── Public API: separation analog (latent — generates candidates only) ──────


def check_separation_analog(
    identity: str | None = None,
    *,
    now_ts: str | None = None,
) -> dict | None:
    """If a user/peer has gone silent past the trigger window AND we haven't
    queued a check-in candidate recently, generate ONE candidate.

    A candidate is a structured proposal — NEVER an outbound message. The
    user reviews the queue at /affect/check-in-candidates and decides.

    Returns the candidate dict (also appended to check_in_candidates.jsonl)
    or None if no candidate is appropriate now.
    """
    ident = identity or primary_user_identity()
    is_user = ident.startswith("user:")
    if not is_user:
        # Peer agents don't receive check-ins; absence triggers different policy.
        return None

    m = get_user_model(ident)
    days = m.days_since_last_seen()
    if days * 24 < SEPARATION_TRIGGER_HOURS:
        return None

    # Cooldown — don't queue more than one candidate per 48h.
    if m.last_check_in_proposal_ts:
        try:
            last = datetime.fromisoformat(m.last_check_in_proposal_ts.replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - last).total_seconds() / 3600.0 < SEPARATION_PROPOSAL_COOLDOWN_H:
                return None
        except (ValueError, AttributeError):
            pass

    # Build the candidate. Phase 3: just enough metadata for the dashboard
    # to surface; the user composes the actual outreach if any.
    candidate = {
        "ts": now_ts or utc_now_iso(),
        "identity": ident,
        "display_name": m.display_name,
        "days_silent": round(days, 2),
        "last_seen_ts": m.last_seen_ts,
        "rolling_valence": round(m.rolling_valence, 3),
        "relational_health": round(m.relational_health, 3),
        "register": "quiet" if m.rolling_valence < 0.20 else "warm",
        "kind": "separation_analog",
        "note": (
            f"{m.display_name} hasn't been seen for ~{days:.1f} days. "
            f"Latent separation analog activated; no message will be sent automatically."
        ),
    }

    try:
        _ATTACH_DIR.mkdir(parents=True, exist_ok=True)
        with _CHECK_IN_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(candidate, default=str) + "\n")
    except Exception:
        logger.debug("affect.attachment: candidate write failed", exc_info=True)

    with _lock:
        m.pending_check_in_candidates += 1
        m.last_check_in_proposal_ts = candidate["ts"]
        _save_one(m)

    logger.info(
        f"affect.attachment: separation analog → check-in candidate for {ident} "
        f"(days_silent={days:.1f}, register={candidate['register']})"
    )
    return candidate


def list_check_in_candidates(limit: int = 50) -> list[dict]:
    """Read recent check-in candidates from the log."""
    if not _CHECK_IN_LOG.exists():
        return []
    out: list[dict] = []
    try:
        with _CHECK_IN_LOG.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        logger.debug("affect.attachment: candidate read failed", exc_info=True)
    return out[-limit:]

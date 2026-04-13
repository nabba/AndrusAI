"""BeliefAdapter — interface that the grounding pipeline talks to.

In production, this delegates to the existing Phase 2 BeliefStore
(`app/subia/belief/store.py`). In tests, the InMemoryBeliefAdapter
provides a clean stub. Decoupling like this lets the grounding
pipeline be unit-tested without ChromaDB / pgvector.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol


@dataclass
class GroundedBelief:
    """Lightweight projection of a Phase 2 Belief, exposed to grounding."""
    belief_id: str
    topic_key: str                      # canonical key, e.g. "share_price:TAL1T:2022-04-14"
    value: str                          # normalized value, e.g. "0.595 EUR"
    evidence_sources: list = field(default_factory=list)
    confidence: float = 0.5
    status: str = "ACTIVE"              # mirrors Belief.belief_status
    formed_at: str = ""

    def is_verified(self, *, min_confidence: float = 0.5) -> bool:
        return (
            self.status == "ACTIVE"
            and self.confidence >= min_confidence
            and bool(self.evidence_sources)
        )


class BeliefAdapter(Protocol):
    def find(self, topic_key: str) -> Optional[GroundedBelief]: ...
    def find_by_prefix(self, topic_prefix: str) -> Optional[GroundedBelief]:
        """Best-effort lookup by topic prefix.

        Useful when the draft response omits a date that the verified
        belief was stored with: ``find_by_prefix("share_price")`` will
        surface ``share_price::april_14_2022`` so the contradiction
        detector can compare values across drift in date specificity.
        """
        ...
    def upsert(
        self,
        topic_key: str,
        value: str,
        evidence_sources: list,
        *,
        confidence: float = 0.7,
    ) -> GroundedBelief: ...
    def retract(self, belief_id: str, reason: str) -> bool: ...
    def supersede_others(self, topic_key: str, surviving_id: str, reason: str) -> int: ...


# ─────────────────────────────────────────────────────────────────────
# In-memory adapter (tests + offline fallback)
# ─────────────────────────────────────────────────────────────────────

class InMemoryBeliefAdapter:
    """Test-grade adapter. Keeps beliefs in a dict by topic_key."""

    def __init__(self) -> None:
        self._by_id: dict[str, GroundedBelief] = {}
        self._by_key: dict[str, list[str]] = {}    # topic_key → [belief_id]

    def find(self, topic_key: str) -> Optional[GroundedBelief]:
        for bid in self._by_key.get(topic_key, []):
            b = self._by_id.get(bid)
            if b and b.status == "ACTIVE":
                return b
        return None

    def find_by_prefix(self, topic_prefix: str) -> Optional[GroundedBelief]:
        if not topic_prefix:
            return None
        # Most-recent ACTIVE belief whose key starts with topic_prefix
        candidates: list[GroundedBelief] = []
        for key, bids in self._by_key.items():
            if not (key == topic_prefix or key.startswith(f"{topic_prefix}::")):
                continue
            for bid in bids:
                b = self._by_id.get(bid)
                if b and b.status == "ACTIVE":
                    candidates.append(b)
        if not candidates:
            return None
        # Newest first
        candidates.sort(key=lambda b: b.formed_at or "", reverse=True)
        return candidates[0]

    def upsert(
        self,
        topic_key: str,
        value: str,
        evidence_sources: list,
        *,
        confidence: float = 0.7,
    ) -> GroundedBelief:
        import uuid
        bid = str(uuid.uuid4())
        b = GroundedBelief(
            belief_id=bid,
            topic_key=topic_key,
            value=value,
            evidence_sources=list(evidence_sources),
            confidence=confidence,
            status="ACTIVE",
            formed_at=datetime.now(timezone.utc).isoformat(),
        )
        self._by_id[bid] = b
        self._by_key.setdefault(topic_key, []).append(bid)
        return b

    def retract(self, belief_id: str, reason: str) -> bool:
        b = self._by_id.get(belief_id)
        if not b:
            return False
        b.status = "RETRACTED"
        return True

    def supersede_others(self, topic_key: str, surviving_id: str, reason: str) -> int:
        n = 0
        for bid in list(self._by_key.get(topic_key, [])):
            if bid == surviving_id:
                continue
            b = self._by_id.get(bid)
            if b and b.status == "ACTIVE":
                b.status = "SUPERSEDED"
                n += 1
        return n


# ─────────────────────────────────────────────────────────────────────
# Production adapter (delegates to Phase 2 BeliefStore)
# ─────────────────────────────────────────────────────────────────────

class Phase2BeliefAdapter:
    """Delegates to `app.subia.belief.store.get_belief_store()`.

    The Phase 2 store is content-addressable by free text; this
    adapter wraps it with a topic-key naming scheme so the grounding
    pipeline can do deterministic lookup/upsert.
    """

    def __init__(self, store=None) -> None:
        if store is None:
            try:
                from app.subia.belief.store import get_belief_store
                store = get_belief_store()
            except Exception:
                store = None
        self._store = store

    def _project(self, b) -> Optional[GroundedBelief]:
        if b is None:
            return None
        return GroundedBelief(
            belief_id=getattr(b, "belief_id", ""),
            topic_key=getattr(b, "domain", "") or "",
            value=(getattr(b, "content", "") or "")[:200],
            evidence_sources=list(getattr(b, "evidence_sources", []) or []),
            confidence=float(getattr(b, "confidence", 0.5) or 0.5),
            status=getattr(b, "belief_status", "ACTIVE"),
            formed_at=str(getattr(b, "formed_at", "") or ""),
        )

    def find(self, topic_key: str) -> Optional[GroundedBelief]:
        if self._store is None:
            return None
        try:
            results = self._store.query_relevant(
                query=topic_key, domain=topic_key, n=1, min_confidence=0.0,
            ) or []
            return self._project(results[0]) if results else None
        except Exception:
            return None

    def find_by_prefix(self, topic_prefix: str) -> Optional[GroundedBelief]:
        if self._store is None or not topic_prefix:
            return None
        try:
            # Phase 2 store domain field carries the topic_key; query
            # by domain prefix (best-effort — Phase 2 query_relevant does
            # substring matching on free text)
            results = self._store.query_relevant(
                query=topic_prefix, domain=None, n=10, min_confidence=0.0,
            ) or []
            for r in results:
                dom = getattr(r, "domain", "") or ""
                if dom == topic_prefix or dom.startswith(f"{topic_prefix}::"):
                    if getattr(r, "belief_status", "") == "ACTIVE":
                        return self._project(r)
            return None
        except Exception:
            return None

    def upsert(
        self,
        topic_key: str,
        value: str,
        evidence_sources: list,
        *,
        confidence: float = 0.7,
    ) -> GroundedBelief:
        if self._store is None:
            # Fallback to in-memory if Phase 2 store unavailable
            tmp = InMemoryBeliefAdapter()
            return tmp.upsert(topic_key, value, evidence_sources,
                              confidence=confidence)
        try:
            b = self._store.add_belief(
                content=f"{topic_key} = {value}",
                domain=topic_key,
                confidence=confidence,
                evidence_sources=evidence_sources,
            )
            return self._project(b)
        except Exception:
            tmp = InMemoryBeliefAdapter()
            return tmp.upsert(topic_key, value, evidence_sources,
                              confidence=confidence)

    def retract(self, belief_id: str, reason: str) -> bool:
        if self._store is None:
            return False
        try:
            return bool(self._store.retract_belief(belief_id, reason))
        except Exception:
            return False

    def supersede_others(self, topic_key: str, surviving_id: str, reason: str) -> int:
        if self._store is None:
            return 0
        try:
            results = self._store.query_relevant(
                query=topic_key, domain=topic_key, n=20, min_confidence=0.0,
            ) or []
            n = 0
            for b in results:
                bid = getattr(b, "belief_id", "")
                if bid and bid != surviving_id and getattr(b, "belief_status", "") == "ACTIVE":
                    if self._store.suspend_belief(bid, reason):
                        n += 1
            return n
        except Exception:
            return 0

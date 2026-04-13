"""Authoritative source registry — discovered, not declared.

When the user says "you can get this from Tallinn Stock Exchange
homepage", correction_capture writes a registered source for the
relevant topic. Subsequent grounding decisions consult the registry
to know WHERE to fetch from instead of leaving the LLM to invent
a source name.

Storage: append-only JSON file. One process should write at a time
(serialised by the chat handler's per-conversation lock). Reads are
atomic (full-file load + cache).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


_DEFAULT_PATH = Path(os.environ.get(
    "SUBIA_SOURCE_REGISTRY_PATH",
    "workspace/source_registry.json",
))


@dataclass
class RegisteredSource:
    topic: str            # e.g. "share_price"
    key: str              # e.g. "TAL1T" or "default"
    url: str              # e.g. "https://nasdaqbaltic.com/..."
    learned_from: str     # 'user_correction' | 'firecrawl' | 'config' | 'manual'
    learned_at: str       # ISO timestamp
    confidence: float = 0.9
    notes: str = ""


class SourceRegistry:
    """Append-only registry of authoritative sources by (topic, key)."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else _DEFAULT_PATH
        self._cache: dict[str, RegisteredSource] = {}
        self._loaded = False

    # ── Internal ────────────────────────────────────────────────────
    def _key(self, topic: str, key: str) -> str:
        return f"{topic}::{key}"

    def _load(self) -> None:
        if self._loaded:
            return
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for entry in data.get("sources", []):
                    rs = RegisteredSource(**entry)
                    self._cache[self._key(rs.topic, rs.key)] = rs
            except Exception as exc:
                logger.warning("source_registry: load failed: %s", exc)
        self._loaded = True

    def _persist(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "sources": [asdict(rs) for rs in self._cache.values()],
            }
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True),
                           encoding="utf-8")
            tmp.replace(self._path)
        except OSError as exc:
            logger.warning("source_registry: persist failed: %s", exc)

    # ── Public API ──────────────────────────────────────────────────
    def register(
        self,
        topic: str,
        key: str,
        url: str,
        *,
        learned_from: str = "user_correction",
        confidence: float = 0.9,
        notes: str = "",
    ) -> RegisteredSource:
        """Idempotent: re-registering the same (topic, key) updates URL."""
        self._load()
        rs = RegisteredSource(
            topic=topic, key=key, url=url,
            learned_from=learned_from,
            learned_at=datetime.now(timezone.utc).isoformat(),
            confidence=confidence,
            notes=notes,
        )
        self._cache[self._key(topic, key)] = rs
        self._persist()
        return rs

    def get(self, topic: str, key: str = "default") -> Optional[RegisteredSource]:
        self._load()
        # Try exact key, then fall back to topic default
        return (
            self._cache.get(self._key(topic, key))
            or self._cache.get(self._key(topic, "default"))
        )

    def all(self) -> list:
        self._load()
        return list(self._cache.values())

    def by_topic(self, topic: str) -> list:
        return [rs for rs in self.all() if rs.topic == topic]


# Process-local default singleton (production use)
_default_registry: Optional[SourceRegistry] = None


def get_default_registry() -> SourceRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = SourceRegistry()
    return _default_registry


def reset_default_registry_for_tests() -> None:
    """Test helper — never call from production code."""
    global _default_registry
    _default_registry = None
